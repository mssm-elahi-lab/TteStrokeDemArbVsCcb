"""Ported from Scripts/Core/02_outcomes_and_ps_deathcensor_run01_20260531.py.

Outcome ascertainment, time-to-event construction, propensity score
estimation, and IPTW computation.

  [C2] Cognitive outcomes use v4 harmonized B4/B4_MCI bucket definitions:
         b4_mci (PRIMARY) = B4_MCI_SNOMED_IDS; b4 (SECONDARY) = B4_SNOMED_IDS
  [C3] Vascular outcomes: stroke_s1 (PRIMARY); stroke_s2 (SECONDARY) = stroke_s1 + TIA
  [C5] censor_date uses clinical_end_date from indexed cohort (extended obs_end)
  [C7] PS fit includes race_unknown_r as a separate EHR-missingness indicator;
       all persons fit in PS model (no race-based exclusion from PS fit).
  [C9] No penalizer by default; document if convergence fails.
"""

from __future__ import annotations

import json
import logging
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from src.config import Config

warnings.filterwarnings("ignore")


def _first_postlag_date(
    conditions_df: pd.DataFrame, snomed_ids, cohort_df: pd.DataFrame, lag_days: int, col_name: str
) -> pd.DataFrame:
    """For each person in cohort_df, return the first condition date strictly
    after index_date + lag_days. Returns DataFrame(PERSON_ID, col_name)."""
    lag = pd.Timedelta(days=lag_days)
    sub = conditions_df[conditions_df["CONDITION_CONCEPT_ID"].isin(snomed_ids)].dropna(
        subset=["CONDITION_START_DATE"]
    ).copy()
    idx = cohort_df[["PERSON_ID", "index_date"]].copy()
    sub = sub.merge(idx, on="PERSON_ID", how="inner")
    sub = sub[sub["CONDITION_START_DATE"] > sub["index_date"] + lag]
    if len(sub) == 0:
        return pd.DataFrame(columns=["PERSON_ID", col_name])
    return (
        sub.groupby("PERSON_ID")["CONDITION_START_DATE"]
        .min()
        .reset_index()
        .rename(columns={"CONDITION_START_DATE": col_name})
    )


def _apply_tte(df: pd.DataFrame, qlfd_date_col: str, outcome_name: str) -> pd.DataFrame:
    """Apply TTE using pre-computed lag-qualified first event date."""
    event_mask = df[qlfd_date_col].notna() & (df[qlfd_date_col] <= df["censor_date"])
    event_time = np.where(event_mask, df[qlfd_date_col], df["censor_date"])
    time_days = (pd.to_datetime(event_time) - df["index_date"]).dt.days
    df[f"{outcome_name}_event"] = event_mask.astype(int)
    df[f"{outcome_name}_time_years"] = np.maximum(time_days / 365.25, 0.0)
    return df


def run(config: Config) -> None:
    analysis = config.analysis
    clinical = analysis.clinical

    analysis_end_date = pd.Timestamp(analysis.end_date)
    dementia_lag_days = analysis.outcomes.dementia_lag_days
    stroke_lag_days = analysis.outcomes.stroke_lag_days
    ps_trim_lower = analysis.propensity_score.trim_lower
    ps_trim_upper = analysis.propensity_score.trim_upper
    random_seed = analysis.random_seed
    ps_covariates_fixed = list(analysis.propensity_score.covariates_fixed)

    # [C2] v4 harmonized B4/B4_MCI SNOMED IDs (direct; pre-verified)
    b4_snomed_ids = clinical.cognitive.b4_snomed_ids
    b4_mci_snomed_ids = clinical.cognitive.b4_mci_snomed_ids

    # [C3] Stroke SNOMED IDs (direct; pre-verified)
    stroke_s1_snomed_ids = clinical.vascular.stroke_s1_snomed_ids
    tia_snomed_ids = clinical.vascular.tia_snomed_ids

    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"compute_outcomes_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(f"{__name__}.run")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))

    logger.info("=" * 70)
    logger.info("TTE Analysis — compute_outcomes")
    logger.info(f"ANALYSIS_END_DATE: {analysis.end_date}")
    logger.info("=" * 70)

    # ==========================================================================
    # LOAD DATA
    # ==========================================================================

    logger.info("Loading data...")
    output_core = config.paths.output_core
    cohort = pd.read_parquet(output_core / "indexed_cohort.parquet")
    raw_cond = pd.read_parquet(config.paths.conditions)
    icd_map = pd.read_parquet(config.paths.icd_map)  # noqa: F841 (read for parity; unused downstream)

    cohort["index_date"] = pd.to_datetime(cohort["index_date"])
    raw_cond["CONDITION_START_DATE"] = pd.to_datetime(raw_cond["CONDITION_START_DATE"], errors="coerce")
    logger.info(f"  indexed cohort: {len(cohort):,}")
    logger.info(f"  raw_conditions: {len(raw_cond):,}")

    # ==========================================================================
    # SNOMED SETS [C2/C3]
    # Direct SNOMED IDs from config (pre-verified via v4 codebook + icd_map).
    # ==========================================================================

    dem_b4_snomed = list(b4_snomed_ids)  # probable dementia alone (SECONDARY)
    dem_b4mci_snomed = list(b4_mci_snomed_ids)  # probable dementia + MCI (PRIMARY)
    stroke_s1_snomed = list(stroke_s1_snomed_ids)  # harmonized AIS (PRIMARY)
    tia_snomed = list(tia_snomed_ids)  # [373503]

    logger.info(f"  B4 SNOMED IDs:     {dem_b4_snomed}")
    logger.info(f"  B4_MCI SNOMED IDs: {dem_b4mci_snomed}")
    logger.info(f"  Stroke S1 SNOMED:  {stroke_s1_snomed}")
    logger.info(f"  TIA SNOMED:        {tia_snomed}")

    # ==========================================================================
    # OUTCOME ASCERTAINMENT — LAG-QUALIFIED FIRST EVENT DATE
    # For each outcome, find the first condition date STRICTLY AFTER
    # index_date + lag. Prevents within-lag codes from blocking later true
    # incident events.
    # ==========================================================================

    logger.info("Ascertaining lag-qualified outcomes...")

    b4_qlfd = _first_postlag_date(raw_cond, dem_b4_snomed, cohort, dementia_lag_days, "b4_qlfd_date")
    b4mci_qlfd = _first_postlag_date(raw_cond, dem_b4mci_snomed, cohort, dementia_lag_days, "b4mci_qlfd_date")
    stroke_s1_qlfd = _first_postlag_date(raw_cond, stroke_s1_snomed, cohort, stroke_lag_days, "stroke_s1_qlfd_date")
    stroke_s2_qlfd = _first_postlag_date(
        raw_cond, stroke_s1_snomed + tia_snomed, cohort, stroke_lag_days, "stroke_s2_qlfd_date"
    )

    cohort = (
        cohort.merge(b4_qlfd, on="PERSON_ID", how="left")
        .merge(b4mci_qlfd, on="PERSON_ID", how="left")
        .merge(stroke_s1_qlfd, on="PERSON_ID", how="left")
        .merge(stroke_s2_qlfd, on="PERSON_ID", how="left")
    )
    for col in ["b4_qlfd_date", "b4mci_qlfd_date", "stroke_s1_qlfd_date", "stroke_s2_qlfd_date"]:
        cohort[col] = pd.to_datetime(cohort[col], errors="coerce")

    # ==========================================================================
    # EXPLICIT DEATH CENSORING
    # [C5] censor_date uses clinical_end_date from indexed cohort (extended obs_end).
    # Falls back to obs_end_date if clinical_end_date absent (pre-correction cohort).
    # ==========================================================================

    logger.info("Applying censor_date = min(clinical_end_date, death) [C5]...")
    cohort["XTN_DEATH_DATE"] = pd.to_datetime(cohort.get("XTN_DEATH_DATE"), errors="coerce")
    cohort["obs_end_date"] = pd.to_datetime(cohort["obs_end_date"], errors="coerce")

    if "clinical_end_date" in cohort.columns:
        cohort["clinical_end_date"] = pd.to_datetime(cohort["clinical_end_date"], errors="coerce")
        logger.info("  Using clinical_end_date from indexed cohort (extended obs_end)")
    else:
        cohort["clinical_end_date"] = cohort["obs_end_date"]
        logger.warning("  clinical_end_date not found in indexed cohort; falling back to obs_end_date")

    cohort["_death_filled"] = cohort["XTN_DEATH_DATE"].fillna(cohort["clinical_end_date"])
    cohort["censor_date"] = cohort[["clinical_end_date", "_death_filled"]].min(axis=1).clip(upper=analysis_end_date)
    cohort.drop(columns=["_death_filled"], inplace=True)

    n_death_shortens = (
        cohort["XTN_DEATH_DATE"].notna() & (cohort["XTN_DEATH_DATE"] < cohort["clinical_end_date"])
    ).sum()
    n_death_after_clin = (
        cohort["XTN_DEATH_DATE"].notna() & (cohort["XTN_DEATH_DATE"] > cohort["clinical_end_date"])
    ).sum()
    n_censor_clinical = (cohort["censor_date"] == cohort["clinical_end_date"]).sum()
    n_censor_death = (cohort["XTN_DEATH_DATE"].notna() & (cohort["censor_date"] == cohort["XTN_DEATH_DATE"])).sum()
    logger.info(f"  N death shortens clinical_end_date:  {n_death_shortens:,}")
    logger.info(f"  N death after clinical_end_date:     {n_death_after_clin:,}")
    logger.info(f"  N censor_date == clinical_end_date:  {n_censor_clinical:,}")
    logger.info(f"  N censor_date == XTN_DEATH_DATE:     {n_censor_death:,}")

    # ==========================================================================
    # TIME-TO-EVENT CONSTRUCTION — using lag-qualified first event dates
    # Event = 1 if first qualifying post-lag date occurs on/before censor_date.
    # Time = years from index_date to lag-qualified event date or censor_date.
    # ==========================================================================

    logger.info("Building time-to-event variables (lag-qualified)...")

    cohort = _apply_tte(cohort, "b4_qlfd_date", "b4")
    cohort = _apply_tte(cohort, "b4mci_qlfd_date", "b4_mci")
    cohort = _apply_tte(cohort, "stroke_s1_qlfd_date", "stroke_s1")
    cohort = _apply_tte(cohort, "stroke_s2_qlfd_date", "stroke_s2")

    # Binary treatment: 1 = ARB, 0 = CCB
    cohort["treated"] = (cohort["exposure_group"] == "ARB").astype(int)
    logger.info(f"  Treated (ARB): {cohort['treated'].sum():,}")
    logger.info(f"  Control (CCB): {(cohort['treated'] == 0).sum():,}")

    for outcome in ["b4", "b4_mci", "stroke_s1", "stroke_s2"]:
        n_ev = cohort[f"{outcome}_event"].sum()
        logger.info(f"  {outcome}: {n_ev:,} events")

    # ==========================================================================
    # FILTER: POSITIVE FOLLOW-UP (uses lag-qualified b4 time as reference)
    # ==========================================================================

    cohort = cohort[cohort["b4_time_years"] > 0].copy()
    logger.info(f"After positive follow-up filter: {len(cohort):,}")

    # ==========================================================================
    # PROPENSITY SCORE
    # Logistic regression; same covariates as v3 primary analysis
    # ==========================================================================

    logger.info("Fitting propensity score model...")

    cohort["index_year"] = pd.to_datetime(cohort["index_date"]).dt.year
    year_min = cohort["index_year"].min()
    cohort["index_year_cat"] = cohort["index_year"].astype(str)
    year_dummies = pd.get_dummies(cohort["index_year_cat"], prefix="yr", drop_first=False)
    yr_ref = f"yr_{year_min}"
    if yr_ref in year_dummies.columns:
        year_dummies = year_dummies.drop(columns=[yr_ref])

    ps_df = cohort[ps_covariates_fixed].copy()
    ps_df = pd.concat([ps_df, year_dummies], axis=1)

    # Save year dummy columns into cohort so Table 2 adjusted Cox + diagnostics
    # can access the same calendar-time covariates from the survival dataset.
    year_dummy_cols = list(year_dummies.columns)
    for col in year_dummy_cols:
        cohort[col] = year_dummies[col].values

    # All persons with complete PS covariates; race_unknown_r is now a model covariate
    # (EHR missingness/coding category) — not excluded from PS fit.
    ps_complete = ps_df.notna().all(axis=1)
    logger.info(f"  Persons with complete PS covariates (all races included): {ps_complete.sum():,}")
    n_unknown_in_ps = int(cohort.loc[ps_complete, "race_unknown_r"].fillna(0).sum())
    logger.info(f"  Of those, race_unknown_r in PS fit: {n_unknown_in_ps:,}")

    X = ps_df.loc[ps_complete].values
    y = cohort.loc[ps_complete, "treated"].values

    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)

    # v3-comparable sklearn logistic regression (L2, C=1.0)
    lr = LogisticRegression(
        penalty="l2",
        C=1.0,
        solver="lbfgs",
        max_iter=2000,
        random_state=random_seed,
    )
    lr.fit(X_s, y)
    ps_auc = roc_auc_score(y, lr.predict_proba(X_s)[:, 1])
    logger.info(f"  PS model AUC/C-statistic (complete cases): {ps_auc:.4f}")

    ps_full = pd.Series(np.nan, index=cohort.index)
    ps_full.loc[ps_complete] = lr.predict_proba(X_s)[:, 1]
    cohort["ps"] = ps_full

    for race_col in ["race_white_r", "race_black_r", "race_asian_r", "race_other_r", "race_unknown_r"]:
        if race_col in cohort.columns:
            n_race = int(cohort[race_col].fillna(0).sum())
            n_race_ps = int(cohort.loc[cohort["ps"].notna(), race_col].fillna(0).sum())
            logger.info(f"  {race_col}: N={n_race:,} in cohort, N={n_race_ps:,} with PS computed")

    # ==========================================================================
    # PS TRIMMING (percentile of each arm)
    # ==========================================================================

    ps_arb = cohort.loc[cohort["treated"] == 1, "ps"].dropna()
    ps_ccb = cohort.loc[cohort["treated"] == 0, "ps"].dropna()
    lo = min(np.nanpercentile(ps_arb, ps_trim_lower * 100), np.nanpercentile(ps_ccb, ps_trim_lower * 100))
    hi = max(np.nanpercentile(ps_arb, ps_trim_upper * 100), np.nanpercentile(ps_ccb, ps_trim_upper * 100))

    n_pre = len(cohort)
    cohort = cohort[cohort["ps"].notna() & cohort["ps"].between(lo, hi)].copy()
    logger.info(f"PS trimming ({ps_trim_lower}–{ps_trim_upper}): removed {n_pre - len(cohort):,}")
    logger.info(
        f"Post-PS-trim N: {len(cohort):,}  (ARB={cohort['treated'].sum():,}, CCB={(cohort['treated'] == 0).sum():,})"
    )

    # ==========================================================================
    # STABILIZED ATE IPTW
    # ==========================================================================

    p_treat = cohort["treated"].mean()
    cohort["iptw"] = np.where(
        cohort["treated"] == 1,
        p_treat / cohort["ps"],
        (1 - p_treat) / (1 - cohort["ps"]),
    )

    w_lo = cohort["iptw"].quantile(0.01)
    w_hi = cohort["iptw"].quantile(0.99)
    cohort["iptw"] = cohort["iptw"].clip(lower=w_lo, upper=w_hi)
    logger.info(f"IPTW range after winsorizing: [{cohort['iptw'].min():.3f}, {cohort['iptw'].max():.3f}]")

    # ==========================================================================
    # PERSIST PS / IPTW DIAGNOSTIC SCALARS (for Supp Table 1 / ps_diagnostics and
    # the PS-trim stage of Figure 1 / cohort_flow). These values are otherwise
    # only logged; persisting them keeps the reporting modules off the raw fit.
    # Effective sample size uses the (winsorized) stabilized weights:
    #   ESS = (sum w)^2 / sum(w^2).
    # ==========================================================================

    def _ess(weights: pd.Series) -> float:
        w = weights.dropna().to_numpy(dtype=float)
        return float(w.sum() ** 2 / np.square(w).sum()) if w.size else float("nan")

    n_post_trim = int(len(cohort))
    n_post_arb = int((cohort["treated"] == 1).sum())
    n_post_ccb = int((cohort["treated"] == 0).sum())
    ess_overall = _ess(cohort["iptw"])
    ps_summary = {
        "ps_model": "L2 logistic regression (C=1.0, seed=%d)" % random_seed,
        "ps_auc": float(ps_auc),
        "n_pre_trim": int(n_pre),
        "n_trimmed": int(n_pre - n_post_trim),
        "n_post_trim": n_post_trim,
        "n_post_trim_arb": n_post_arb,
        "n_post_trim_ccb": n_post_ccb,
        "ps_trim_lower": float(ps_trim_lower),
        "ps_trim_upper": float(ps_trim_upper),
        "iptw_min": float(cohort["iptw"].min()),
        "iptw_max": float(cohort["iptw"].max()),
        "ess_overall": ess_overall,
        "ess_arb": _ess(cohort.loc[cohort["treated"] == 1, "iptw"]),
        "ess_ccb": _ess(cohort.loc[cohort["treated"] == 0, "iptw"]),
        "ess_overall_pct": float(100.0 * ess_overall / n_post_trim) if n_post_trim else float("nan"),
    }
    ps_summary_path = output_core / "ps_fit_summary.json"
    ps_summary_path.write_text(json.dumps(ps_summary, indent=2))
    logger.info(f"  Effective sample size (overall): {ess_overall:,.0f} ({ps_summary['ess_overall_pct']:.1f}%)")
    logger.info(f"Saved PS/IPTW diagnostic summary: {ps_summary_path}")

    # ==========================================================================
    # COVARIATE BALANCE
    # ==========================================================================

    logger.info("Computing covariate balance (SMD before/after IPTW)...")
    balance_rows = []
    covs = ps_covariates_fixed + [c for c in cohort.columns if c.startswith("yr_")]
    for cov in covs:
        if cov not in cohort.columns:
            continue
        a = cohort.loc[cohort["treated"] == 1, cov].dropna()
        b = cohort.loc[cohort["treated"] == 0, cov].dropna()
        pooled_sd = np.sqrt((a.std() ** 2 + b.std() ** 2) / 2) if (a.std() + b.std()) > 0 else np.nan
        smd_pre = (a.mean() - b.mean()) / pooled_sd if pooled_sd and pooled_sd > 0 else np.nan

        wa = cohort.loc[cohort["treated"] == 1, cov]
        wb = cohort.loc[cohort["treated"] == 0, cov]
        wa_wt = cohort.loc[cohort["treated"] == 1, "iptw"]
        wb_wt = cohort.loc[cohort["treated"] == 0, "iptw"]
        mean_a_w = np.average(wa.dropna(), weights=wa_wt.loc[wa.notna()])
        mean_b_w = np.average(wb.dropna(), weights=wb_wt.loc[wb.notna()])
        smd_post = (mean_a_w - mean_b_w) / pooled_sd if pooled_sd and pooled_sd > 0 else np.nan

        balance_rows.append(
            {
                "covariate": cov,
                "mean_arb": a.mean(),
                "mean_ccb": b.mean(),
                "smd_pre": smd_pre,
                "mean_arb_w": mean_a_w,
                "mean_ccb_w": mean_b_w,
                "smd_post": smd_post,
            }
        )

    balance_df = pd.DataFrame(balance_rows)
    bal_out = output_core / "covariate_balance.csv"
    balance_df.to_csv(bal_out, index=False)
    logger.info(f"Saved: {bal_out}")

    # ==========================================================================
    # SAVE SURVIVAL DATASET
    # ==========================================================================

    survival_out = output_core / "survival_dataset.parquet"
    cohort.to_parquet(survival_out, index=False)
    logger.info(f"Saved: {survival_out}  ({len(cohort):,} rows)")
    logger.info("compute_outcomes complete.")

    for handler in logger.handlers:
        handler.close()


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
