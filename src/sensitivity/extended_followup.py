"""Ported from Scripts/Sensitivity/extended_followup_lt2020_run01_20260623.py.

Extended Potential Follow-Up Sensitivity Analysis.

Restriction: index_date < 2020-01-01 (lt2020 / ~>=6yr potential follow-up
based on admin end date 2025-12-31). Eligibility by index-date only;
observed follow-up duration is NOT used as an inclusion criterion.

Design mirrors the primary analysis:
  - Same PS covariates (+ index_year dummies; ref = earliest year in subgroup)
  - PS refit within the <2020 subgroup
  - Stabilized ATE IPTW; winsorized 1st-99th percentile weights
  - PS 1st-99th percentile overlap trimming applied after PS refit
  - ITT estimand (treatment-initiation)
  - Same lag windows: dementia 180d, stroke/TIA 90d
  - Same censoring rules (censor_date already in survival dataset)
  - Unadjusted, covariate-adjusted, and IPTW-weighted Cox (robust SEs for IPTW)
  - Bonferroni and BH-FDR correction for primary outcomes only
  - Secondary outcomes: nominal p-values only

Low-memory: pyarrow.parquet.read_schema() for schema inspection, usecols
read only, no plots generated.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from lifelines import CoxPHFitter
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.multitest import multipletests

from src.config import Config

warnings.filterwarnings("ignore")

CoxResult = tuple[float | None, float | None, float | None, float | None, str | None]


def _fmt_ci(hr: float, lo: float, hi: float, decimals: int = 2) -> str:
    fmt = f"{{:.{decimals}f}}"
    return f"{fmt.format(hr)} ({fmt.format(lo)}-{fmt.format(hi)})"


def _run_cox_unadjusted(sub: pd.DataFrame, ev_col: str, tm_col: str, warn) -> CoxResult:
    d = sub[[tm_col, ev_col, "treated"]].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    cph = CoxPHFitter()
    try:
        cph.fit(d, duration_col=tm_col, event_col=ev_col, formula="treated")
        hr = float(np.exp(cph.params_["treated"]))
        lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"Unadjusted Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)


def _run_cox_adjusted(sub: pd.DataFrame, ev_col: str, tm_col: str, covariates: list[str], warn) -> CoxResult:
    needed = [tm_col, ev_col, "treated"] + covariates
    avail = [c for c in needed if c in sub.columns]
    d = sub[avail].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    formula = "treated + " + " + ".join(c for c in covariates if c in d.columns)
    cph = CoxPHFitter()
    try:
        cph.fit(d, duration_col=tm_col, event_col=ev_col, formula=formula)
        hr = float(np.exp(cph.params_["treated"]))
        lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"Adjusted Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)


def _run_cox_iptw(sub: pd.DataFrame, ev_col: str, tm_col: str, warn) -> CoxResult:
    d = sub[[tm_col, ev_col, "treated", "iptw_sub"]].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    cph = CoxPHFitter()
    try:
        cph.fit(d, duration_col=tm_col, event_col=ev_col, formula="treated", weights_col="iptw_sub", robust=True)
        hr = float(np.exp(cph.params_["treated"]))
        lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"IPTW Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)


def run(config: Config) -> None:
    analysis = config.analysis
    random_seed = analysis.random_seed
    ps_trim_lower = analysis.propensity_score.trim_lower
    ps_trim_upper = analysis.propensity_score.trim_upper
    ps_covariates_fixed = list(analysis.propensity_score.covariates_fixed)

    cutoff = pd.Timestamp(analysis.sensitivity.extended_followup.index_date_cutoff)

    outcomes = [
        (o.name, o.role, o.label, f"{o.name}_event", f"{o.name}_time_years") for o in analysis.outcomes.order
    ]
    primary_outcomes = [o[0] for o in outcomes if o[1] == "primary"]

    warnings_log: list[str] = []

    def warn(msg: str) -> None:
        print(f"  WARNING: {msg}")
        warnings_log.append(msg)

    out_dir = config.paths.output_sensitivity / "extended_followup"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("SCHEMA INSPECTION")
    print("=" * 70)

    survival_path = config.paths.output_core / "survival_dataset.parquet"
    schema = pq.read_schema(survival_path)
    all_cols = schema.names
    print(f"Columns in survival dataset: {len(all_cols)}")

    outcome_cols = []
    for oname, role, label, ev_col, tm_col in outcomes:
        outcome_cols += [ev_col, tm_col]

    required_cols = (
        ["PERSON_ID", "index_date", "censor_date", "exposure_group", "treated", "index_year"]
        + ps_covariates_fixed
        + outcome_cols
    )

    missing = [c for c in required_cols if c not in all_cols]
    if missing:
        raise RuntimeError(f"ABORT — missing columns in survival dataset: {missing}")

    print(f"All {len(required_cols)} required columns present.")

    print("\n" + "=" * 70)
    print("LOADING DATA")
    print("=" * 70)

    usecols = [c for c in required_cols if c in all_cols]
    df = pd.read_parquet(survival_path, columns=usecols)
    df["index_date"] = pd.to_datetime(df["index_date"])
    df["censor_date"] = pd.to_datetime(df["censor_date"])

    print(f"Loaded {len(df):,} rows, {len(usecols)} columns")

    print("\n" + "=" * 70)
    print("INDEX-DATE RESTRICTION")
    print("=" * 70)

    df = df[df["index_date"] < cutoff].copy()
    n_after_cut = len(df)
    arb_cut = int(df["treated"].sum())
    ccb_cut = int((df["treated"] == 0).sum())
    print(
        f"After index_date < {cutoff.date()} (lt2020 / ~>=6yr potential FU): "
        f"N={n_after_cut:,}  ARB={arb_cut:,}  CCB={ccb_cut:,}"
    )

    if n_after_cut == 0:
        raise RuntimeError("ABORT — zero records after index-date restriction.")

    print("\n" + "=" * 70)
    print("PS REFIT — <2020 SUBGROUP (lt2020 / ~>=6yr potential FU)")
    print("=" * 70)

    df["index_year_sub"] = df["index_date"].dt.year
    year_min_sub = int(df["index_year_sub"].min())
    year_max_sub = int(df["index_year_sub"].max())
    print(f"  Index year range in subgroup: {year_min_sub}-{year_max_sub}")
    print(f"  Reference year (omitted from model): {year_min_sub}")

    year_dummies = pd.get_dummies(df["index_year_sub"].astype(str), prefix="yr_sub", drop_first=False)
    yr_ref_col = f"yr_sub_{year_min_sub}"
    if yr_ref_col in year_dummies.columns:
        year_dummies = year_dummies.drop(columns=[yr_ref_col])
    year_dummy_cols = list(year_dummies.columns)

    for col in year_dummy_cols:
        df[col] = year_dummies[col].values

    ps_features = ps_covariates_fixed + year_dummy_cols
    ps_df = df[ps_features].copy()
    ps_complete = ps_df.notna().all(axis=1)
    n_ps_complete = int(ps_complete.sum())
    n_ps_missing = int((~ps_complete).sum())
    print(f"  Persons with complete PS covariates: {n_ps_complete:,}  (missing: {n_ps_missing:,})")

    if n_ps_missing > 0:
        warn(f"{n_ps_missing} persons excluded from PS fit due to incomplete covariates.")

    n_unknown_in_ps = int(df.loc[ps_complete, "race_unknown_r"].fillna(0).sum())
    print(f"  race_unknown_r in PS fit: {n_unknown_in_ps:,}")

    X_fit = ps_df.loc[ps_complete].values
    y_fit = df.loc[ps_complete, "treated"].values

    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_fit)

    lr = LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=2000, random_state=random_seed)
    lr.fit(X_s, y_fit)

    ps_auc = roc_auc_score(y_fit, lr.predict_proba(X_s)[:, 1])
    print(f"  PS C-statistic (AUC, complete cases): {ps_auc:.4f}")

    if ps_auc > 0.9:
        warn(f"PS AUC = {ps_auc:.4f} — very high; check for separation or near-perfect prediction in small subgroup.")

    ps_full = pd.Series(np.nan, index=df.index)
    ps_full.loc[ps_complete] = lr.predict_proba(X_s)[:, 1]
    df["ps_sub"] = ps_full

    print("\n" + "=" * 70)
    print("PS OVERLAP TRIMMING")
    print("=" * 70)

    ps_arb = df.loc[df["treated"] == 1, "ps_sub"].dropna()
    ps_ccb = df.loc[df["treated"] == 0, "ps_sub"].dropna()
    lo = min(np.nanpercentile(ps_arb, ps_trim_lower * 100), np.nanpercentile(ps_ccb, ps_trim_lower * 100))
    hi = max(np.nanpercentile(ps_arb, ps_trim_upper * 100), np.nanpercentile(ps_ccb, ps_trim_upper * 100))

    n_pre_trim = len(df)
    df = df[df["ps_sub"].notna() & df["ps_sub"].between(lo, hi)].copy()
    n_post_trim = len(df)
    n_removed = n_pre_trim - n_post_trim
    arb_post = int(df["treated"].sum())
    ccb_post = int((df["treated"] == 0).sum())

    print(f"  PS range: [{lo:.4f}, {hi:.4f}]")
    print(f"  Removed by trimming: {n_removed:,}")
    print(f"  Post-trim N: {n_post_trim:,}  ARB={arb_post:,}  CCB={ccb_post:,}")

    print("\n" + "=" * 70)
    print("STABILIZED IPTW")
    print("=" * 70)

    p_treat = df["treated"].mean()
    df["iptw_sub"] = np.where(
        df["treated"] == 1,
        p_treat / df["ps_sub"],
        (1 - p_treat) / (1 - df["ps_sub"]),
    )

    w_lo = df["iptw_sub"].quantile(0.01)
    w_hi = df["iptw_sub"].quantile(0.99)
    df["iptw_sub"] = df["iptw_sub"].clip(lower=w_lo, upper=w_hi)

    w_mean = df["iptw_sub"].mean()
    w_min = df["iptw_sub"].min()
    w_max = df["iptw_sub"].max()
    print(f"  Weights after winsorization: min={w_min:.4f}  max={w_max:.4f}  mean={w_mean:.4f}")

    ess_arb = float(
        df.loc[df["treated"] == 1, "iptw_sub"].sum() ** 2 / (df.loc[df["treated"] == 1, "iptw_sub"] ** 2).sum()
    )
    ess_ccb = float(
        df.loc[df["treated"] == 0, "iptw_sub"].sum() ** 2 / (df.loc[df["treated"] == 0, "iptw_sub"] ** 2).sum()
    )
    ess_total = ess_arb + ess_ccb
    print(f"  ESS: overall={ess_total:.1f}  ARB={ess_arb:.1f}  CCB={ess_ccb:.1f}")

    print("\n" + "=" * 70)
    print("COVARIATE BALANCE")
    print("=" * 70)

    balance_rows = []
    balance_covs = ps_covariates_fixed + year_dummy_cols

    for cov in balance_covs:
        if cov not in df.columns:
            continue
        a = df.loc[df["treated"] == 1, cov].dropna()
        b = df.loc[df["treated"] == 0, cov].dropna()
        s_pool = np.sqrt((a.std() ** 2 + b.std() ** 2) / 2) if (a.std() + b.std()) > 0 else np.nan
        smd_pre = (a.mean() - b.mean()) / s_pool if (s_pool and s_pool > 0) else np.nan

        wa = df.loc[df["treated"] == 1, cov]
        wb = df.loc[df["treated"] == 0, cov]
        wgt_a = df.loc[df["treated"] == 1, "iptw_sub"]
        wgt_b = df.loc[df["treated"] == 0, "iptw_sub"]
        mu_a = np.average(wa.dropna(), weights=wgt_a.loc[wa.notna()])
        mu_b = np.average(wb.dropna(), weights=wgt_b.loc[wb.notna()])
        smd_post = (mu_a - mu_b) / s_pool if (s_pool and s_pool > 0) else np.nan

        balance_rows.append(
            {
                "covariate": cov,
                "mean_arb_unweighted": round(a.mean(), 5),
                "mean_ccb_unweighted": round(b.mean(), 5),
                "smd_before_iptw": round(smd_pre, 5) if not np.isnan(smd_pre) else None,
                "mean_arb_iptw": round(mu_a, 5),
                "mean_ccb_iptw": round(mu_b, 5),
                "smd_after_iptw": round(smd_post, 5) if not np.isnan(smd_post) else None,
            }
        )

    balance_df = pd.DataFrame(balance_rows)

    max_smd_pre = float(balance_df["smd_before_iptw"].abs().max())
    max_smd_post = float(balance_df["smd_after_iptw"].abs().max())
    print(f"  Max |SMD| before IPTW: {max_smd_pre:.4f}")
    print(f"  Max |SMD| after  IPTW: {max_smd_post:.4f}")
    if max_smd_post > 0.10:
        warn(f"Max |SMD| post-IPTW = {max_smd_post:.4f} > 0.10 — residual imbalance.")

    balance_out = out_dir / "balance_summary.csv"
    balance_df.to_csv(balance_out, index=False)
    print(f"  Saved: {balance_out}")

    print("\n" + "=" * 70)
    print("COX MODELS")
    print("=" * 70)

    adj_covariates = [c for c in (ps_covariates_fixed + year_dummy_cols) if c in df.columns]

    results_rows = []
    iptw_pvals_primary = {}

    for oname, role, label, ev_col, tm_col in outcomes:
        print(f"\n--- {oname} ({role}) ---")
        if ev_col not in df.columns or tm_col not in df.columns:
            warn(f"Missing column for {oname}: {ev_col} or {tm_col}")
            continue

        arb_mask = df["treated"] == 1
        ev_arb = int(df.loc[arb_mask, ev_col].sum())
        ev_ccb = int(df.loc[~arb_mask, ev_col].sum())
        ev_total = int(df[ev_col].sum())
        print(f"  Events: ARB={ev_arb}  CCB={ev_ccb}  total={ev_total}")

        if ev_arb < 10 or ev_ccb < 10:
            warn(f"{oname}: per-arm event count below 10 (ARB={ev_arb}, CCB={ev_ccb}) — estimates unreliable.")
        elif ev_arb < 20 or ev_ccb < 20:
            warn(f"{oname}: per-arm event count below 20 (ARB={ev_arb}, CCB={ev_ccb}) — wide CIs expected.")

        obs_fu = df[tm_col].dropna()
        med_fu = float(obs_fu.median())
        q25_fu = float(obs_fu.quantile(0.25))
        q75_fu = float(obs_fu.quantile(0.75))
        print(f"  Follow-up: median={med_fu:.2f}yr  IQR=[{q25_fu:.2f}, {q75_fu:.2f}]")

        hr_u, lo_u, hi_u, p_u, err_u = _run_cox_unadjusted(df, ev_col, tm_col, warn)
        if hr_u is not None:
            print(f"  Unadjusted:  HR={hr_u:.3f} ({lo_u:.3f}-{hi_u:.3f})  p={p_u:.4f}")
        else:
            print(f"  Unadjusted:  FAILED — {err_u}")

        hr_a, lo_a, hi_a, p_a, err_a = _run_cox_adjusted(df, ev_col, tm_col, adj_covariates, warn)
        if hr_a is not None:
            print(f"  Adjusted:    HR={hr_a:.3f} ({lo_a:.3f}-{hi_a:.3f})  p={p_a:.4f}")
        else:
            print(f"  Adjusted:    FAILED — {err_a}")

        hr_w, lo_w, hi_w, p_w, err_w = _run_cox_iptw(df, ev_col, tm_col, warn)
        if hr_w is not None:
            print(f"  IPTW:        HR={hr_w:.3f} ({lo_w:.3f}-{hi_w:.3f})  p={p_w:.4f}")
        else:
            print(f"  IPTW:        FAILED — {err_w}")

        if role == "primary" and p_w is not None:
            iptw_pvals_primary[oname] = p_w

        results_rows.append(
            {
                "outcome": oname,
                "endpoint_type": role,
                "analytic_n": n_post_trim,
                "ARB_n": arb_post,
                "DHPCCB_n": ccb_post,
                "events_ARB": ev_arb,
                "events_DHPCCB": ev_ccb,
                "unadjusted_HR": round(hr_u, 4) if hr_u is not None else None,
                "unadjusted_95CI": _fmt_ci(hr_u, lo_u, hi_u) if hr_u is not None else None,
                "adjusted_HR": round(hr_a, 4) if hr_a is not None else None,
                "adjusted_95CI": _fmt_ci(hr_a, lo_a, hi_a) if hr_a is not None else None,
                "IPTW_HR": round(hr_w, 4) if hr_w is not None else None,
                "IPTW_95CI": _fmt_ci(hr_w, lo_w, hi_w) if hr_w is not None else None,
                "IPTW_p": round(p_w, 6) if p_w is not None else None,
                "Bonferroni_p_primary_only": None,
                "BH_FDR_p_primary_only": None,
                "max_abs_SMD_before_IPTW": round(max_smd_pre, 5),
                "max_abs_SMD_after_IPTW": round(max_smd_post, 5),
            }
        )

    print("\n" + "=" * 70)
    print("MULTIPLE TESTING CORRECTION (primary outcomes only)")
    print("=" * 70)

    primary_order = [oname for oname in primary_outcomes if oname in iptw_pvals_primary]
    if len(primary_order) > 0:
        p_raw = [iptw_pvals_primary[o] for o in primary_order]
        _, p_bonf, _, _ = multipletests(p_raw, method="bonferroni")
        _, p_bh, _, _ = multipletests(p_raw, method="fdr_bh")
        bonf_map = {o: round(float(pb), 6) for o, pb in zip(primary_order, p_bonf)}
        bh_map = {o: round(float(pb), 6) for o, pb in zip(primary_order, p_bh)}
        for o in primary_order:
            print(f"  {o}: raw_p={iptw_pvals_primary[o]:.6f}  Bonf={bonf_map[o]:.6f}  BH-FDR={bh_map[o]:.6f}")
    else:
        bonf_map, bh_map = {}, {}
        warn("No primary outcomes with valid IPTW p-values — multiple testing correction skipped.")

    for row in results_rows:
        oname = row["outcome"]
        if oname in bonf_map:
            row["Bonferroni_p_primary_only"] = bonf_map[oname]
            row["BH_FDR_p_primary_only"] = bh_map[oname]

    results_df = pd.DataFrame(results_rows)
    results_out = out_dir / "results.csv"
    results_df.to_csv(results_out, index=False)
    print(f"\nSaved: {results_out}")

    print("\n" + "=" * 70)
    print("QC REPORT")
    print("=" * 70)

    obs_fu_all = df["b4_time_years"].dropna()
    med_fu_overall = float(obs_fu_all.median())
    q25_fu_overall = float(obs_fu_all.quantile(0.25))
    q75_fu_overall = float(obs_fu_all.quantile(0.75))

    lines = []
    lines.append("# Extended Potential Follow-Up lt2020 (~>=6yr) — QC Report")
    lines.append("")
    lines.append(f"**Date:** {config.analysis.last_modified}")
    lines.append(f"**Admin end date:** {config.analysis.end_date} (fixed)")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Cohort Restriction")
    lines.append(f"- Index-date cutoff: `< {cutoff.date()}` (lt2020 / ~>=6yr potential follow-up)")
    lines.append(
        "- Restriction type: potential follow-up (index-date only; observed follow-up NOT used as inclusion criterion)"
    )
    lines.append(f"- N after index-date restriction: {n_after_cut:,}  (ARB={arb_cut:,}  CCB={ccb_cut:,})")
    lines.append("")

    lines.append("## PS Refit")
    lines.append(f"- N with complete PS covariates: {n_ps_complete:,}  (incomplete excluded: {n_ps_missing:,})")
    lines.append("- PS model: L2 logistic regression (C=1.0, lbfgs, max_iter=2000, seed=42)")
    lines.append(f"- PS C-statistic (AUC): {ps_auc:.4f}")
    lines.append(f"- Index year range in subgroup: {year_min_sub}-{year_max_sub} (ref={year_min_sub})")
    lines.append(f"- race_unknown_r in PS fit: {n_unknown_in_ps:,}")
    lines.append("")

    lines.append("## PS Trimming and Final Analytic Cohort")
    lines.append("- PS trimming: 1st-99th percentile overlap")
    lines.append(f"- PS range after trimming: [{lo:.4f}, {hi:.4f}]")
    lines.append(f"- N removed by PS trimming: {n_removed:,}")
    lines.append(f"- N post-trim: {n_post_trim:,}  (ARB={arb_post:,}  CCB={ccb_post:,})")
    lines.append("")

    lines.append("## Event Counts by Outcome and Arm")
    lines.append("")
    lines.append("| Outcome | Role | Events ARB | Events CCB | Total |")
    lines.append("|---------|------|-----------|-----------|-------|")
    for row in results_rows:
        lines.append(
            f"| {row['outcome']} | {row['endpoint_type']} "
            f"| {row['events_ARB']} | {row['events_DHPCCB']} | {row['events_ARB'] + row['events_DHPCCB']} |"
        )
    lines.append("")

    lines.append("## Follow-Up")
    lines.append(
        f"- Median observed follow-up (b4 time, overall): {med_fu_overall:.2f} yr  "
        f"IQR [{q25_fu_overall:.2f}, {q75_fu_overall:.2f}]"
    )
    lines.append("")

    lines.append("## IPTW")
    lines.append("- Stabilized ATE weights; winsorized 1st-99th percentile")
    lines.append(f"- Weight range: [{w_min:.4f}, {w_max:.4f}]  mean={w_mean:.4f}")
    lines.append(f"- ESS: overall={ess_total:.1f}  ARB={ess_arb:.1f}  CCB={ess_ccb:.1f}")
    lines.append("")

    lines.append("## Covariate Balance")
    lines.append(f"- Max |SMD| before IPTW: {max_smd_pre:.4f}")
    lines.append(f"- Max |SMD| after IPTW:  {max_smd_post:.4f}")
    lines.append("  _(see balance_summary.csv for per-covariate details)_")
    lines.append("")

    lines.append("## Model Results Summary")
    lines.append("")
    lines.append("| Outcome | Role | IPTW HR | IPTW 95% CI | IPTW p | Bonf p | BH-FDR p |")
    lines.append("|---------|------|---------|------------|--------|--------|---------|")
    for row in results_rows:
        bonf_str = str(row["Bonferroni_p_primary_only"]) if row["Bonferroni_p_primary_only"] is not None else "-"
        bh_str = str(row["BH_FDR_p_primary_only"]) if row["BH_FDR_p_primary_only"] is not None else "-"
        p_str = f"{row['IPTW_p']:.4f}" if row["IPTW_p"] is not None else "-"
        ci_str = row["IPTW_95CI"] if row["IPTW_95CI"] else "-"
        hr_str = f"{row['IPTW_HR']:.3f}" if row["IPTW_HR"] is not None else "-"
        lines.append(f"| {row['outcome']} | {row['endpoint_type']} | {hr_str} | {ci_str} | {p_str} | {bonf_str} | {bh_str} |")
    lines.append("")

    if warnings_log:
        lines.append("## Warnings and Convergence Notes")
        lines.append("")
        for w in warnings_log:
            lines.append(f"- {w}")
        lines.append("")
    else:
        lines.append("## Warnings and Convergence Notes")
        lines.append("")
        lines.append("- None.")
        lines.append("")

    qc_out = out_dir / "qc_report.md"
    with open(qc_out, "w") as fh:
        fh.write("\n".join(lines))
    print(f"Saved: {qc_out}")

    print(f"\nSaved: {results_out.name}, {qc_out.name}, {balance_out.name} -> {out_dir}")
    if warnings_log:
        print(f"Warnings ({len(warnings_log)}):")
        for w in warnings_log:
            print(f"  - {w}")
    print("extended_followup complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
