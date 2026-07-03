"""
02_outcomes_and_ps_deathcensor_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Outcome ascertainment, time-to-event construction, propensity score
estimation, and IPTW computation for run01.

CORRECTIONS (2026-05-31):
  [C1/C10] Uses V4_CONDITIONS and V4_ICD_MAP from v4 extract.
  [C2]  Cognitive outcomes use v4 harmonized B4/B4_MCI bucket definitions:
          b4_mci (PRIMARY) = B4_MCI_SNOMED_IDS = [378419,443605,4182210,439795,4009705]
          b4     (SECONDARY) = B4_SNOMED_IDS = [378419,443605,4182210]
  [C3]  Vascular outcomes:
          stroke_s1 (PRIMARY)  = STROKE_S1_SNOMED_IDS = [443454, 372924]
          stroke_s2 (SECONDARY) = stroke_s1 + TIA [373503]
  [C5]  censor_date uses clinical_end_date from indexed cohort (extended obs_end)
  [C7]  PS fit includes race_unknown_r as a separate EHR-missingness indicator;
        all persons fit in PS model (no race-based exclusion from PS fit).
  [C9]  No penalizer by default; document if convergence fails.

Output:
  run01_survival_dataset.parquet
  run01_covariate_balance.csv
  logs/02_outcomes_ps_run01_<date>.log

Frozen v3 template: src/may_2026/03b_outcomes_and_ps.py

Author: (initials)
Date:   2026-05-31
"""

# ==============================================================================
# DRY-RUN GUARD
# ==============================================================================

RUN_FULL_ANALYSIS: bool = False

if not RUN_FULL_ANALYSIS:
    raise RuntimeError(
        "Dry-run protected script. Review preflight outputs and set "
        "RUN_FULL_ANALYSIS = True before execution."
    )

# ==============================================================================
# IMPORTS
# ==============================================================================

import sys
import logging
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

ANALYSIS_END_DATE  = pd.Timestamp(cfg.ANALYSIS_END_DATE_STR)
DEMENTIA_LAG_DAYS  = cfg.DEMENTIA_LAG_DAYS
STROKE_LAG_DAYS    = cfg.STROKE_LAG_DAYS
PS_TRIM_LOWER      = cfg.PS_TRIM_LOWER
PS_TRIM_UPPER      = cfg.PS_TRIM_UPPER
RANDOM_SEED        = cfg.RANDOM_SEED

# [C2] v4 harmonized B4/B4_MCI SNOMED IDs (direct; pre-verified)
B4_SNOMED_IDS     = cfg.B4_SNOMED_IDS        # probable dementia alone: [378419,443605,4182210]
B4_MCI_SNOMED_IDS = cfg.B4_MCI_SNOMED_IDS    # +MCI: adds 439795, 4009705

# [C3] Stroke SNOMED IDs (direct; pre-verified)
STROKE_S1_SNOMED_IDS = cfg.STROKE_S1_SNOMED_IDS  # [443454, 372924]
TIA_SNOMED_IDS       = cfg.TIA_SNOMED_IDS          # used for ICD map lookup; fallback

LOG_DIR = cfg.LOG_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_path = LOG_DIR / f"02_outcomes_ps_run01_{datetime.today().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

log.info("=" * 70)
log.info("run01 — 02_outcomes_and_ps_deathcensor_run01")
log.info(f"ANALYSIS_END_DATE: {cfg.ANALYSIS_END_DATE_STR}")
log.info("=" * 70)

# ==============================================================================
# HELPER
# ==============================================================================

def get_snomed_ids(icd_ids, icd_map):
    return (
        icd_map.loc[icd_map["ICD_CONCEPT_ID"].isin(icd_ids), "STANDARD_CONCEPT_ID"]
        .dropna().astype(int).unique().tolist()
    )

# ==============================================================================
# LOAD DATA
# ==============================================================================

log.info("Loading data...")
cohort   = pd.read_parquet(cfg.RUN01_INDEXED_COHORT)
raw_cond = pd.read_parquet(cfg.V4_CONDITIONS)
icd_map  = pd.read_parquet(cfg.V4_ICD_MAP)

cohort["index_date"] = pd.to_datetime(cohort["index_date"])
raw_cond["CONDITION_START_DATE"] = pd.to_datetime(raw_cond["CONDITION_START_DATE"], errors="coerce")
log.info(f"  indexed cohort: {len(cohort):,}")
log.info(f"  raw_conditions: {len(raw_cond):,}")

# ==============================================================================
# SNOMED SETS [C2/C3 CORRECTED]
# Use direct SNOMED IDs from config (pre-verified via v4 codebook + icd_map).
# No ICD->SNOMED resolution needed for primary outcomes.
# Comorbidity-only SNOMEDs (bl_tia for covariate) resolved via ICD map.
# ==============================================================================

# [C2] Cognitive outcomes — v4 harmonized bucket definitions
DEM_B4_SNOMED     = list(B4_SNOMED_IDS)       # probable dementia alone (SECONDARY)
DEM_B4MCI_SNOMED  = list(B4_MCI_SNOMED_IDS)   # probable dementia + MCI (PRIMARY)

# [C3] Vascular outcomes
STROKE_S1_SNOMED = list(STROKE_S1_SNOMED_IDS)  # harmonized AIS (PRIMARY)
# TIA: direct SNOMED ID — do NOT pass TIA_SNOMED_IDS through get_snomed_ids();
# TIA_SNOMED_IDS = [373503] is already the STANDARD_CONCEPT_ID, not an ICD concept ID.
TIA_SNOMED = list(cfg.TIA_SNOMED_IDS)   # [373503]

log.info(f"  B4 SNOMED IDs:     {DEM_B4_SNOMED}")
log.info(f"  B4_MCI SNOMED IDs: {DEM_B4MCI_SNOMED}")
log.info(f"  Stroke S1 SNOMED:  {STROKE_S1_SNOMED}")
log.info(f"  TIA SNOMED:        {TIA_SNOMED}")

# ==============================================================================
# OUTCOME ASCERTAINMENT — LAG-QUALIFIED FIRST EVENT DATE
# For each outcome, find the first condition date STRICTLY AFTER index_date + lag.
# This prevents early within-lag codes from blocking later true incident events.
# ==============================================================================

log.info("Ascertaining lag-qualified outcomes...")

def first_postlag_date(conditions_df, snomed_ids, cohort_df, lag_days, col_name):
    """
    For each person in cohort_df, return the first condition date
    strictly after index_date + lag_days.  Returns DataFrame(PERSON_ID, col_name).
    """
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
        .min().reset_index()
        .rename(columns={"CONDITION_START_DATE": col_name})
    )

# Cognitive: first post-lag qualifying date
b4_qlfd    = first_postlag_date(raw_cond, DEM_B4_SNOMED,    cohort, DEMENTIA_LAG_DAYS, "b4_qlfd_date")
b4mci_qlfd = first_postlag_date(raw_cond, DEM_B4MCI_SNOMED, cohort, DEMENTIA_LAG_DAYS, "b4mci_qlfd_date")

# Vascular: first post-lag qualifying date for S1 and composite S2
stroke_s1_qlfd = first_postlag_date(raw_cond, STROKE_S1_SNOMED,             cohort, STROKE_LAG_DAYS, "stroke_s1_qlfd_date")
stroke_s2_qlfd = first_postlag_date(raw_cond, STROKE_S1_SNOMED + TIA_SNOMED, cohort, STROKE_LAG_DAYS, "stroke_s2_qlfd_date")

cohort = (
    cohort
    .merge(b4_qlfd,        on="PERSON_ID", how="left")
    .merge(b4mci_qlfd,     on="PERSON_ID", how="left")
    .merge(stroke_s1_qlfd, on="PERSON_ID", how="left")
    .merge(stroke_s2_qlfd, on="PERSON_ID", how="left")
)
for col in ["b4_qlfd_date", "b4mci_qlfd_date", "stroke_s1_qlfd_date", "stroke_s2_qlfd_date"]:
    cohort[col] = pd.to_datetime(cohort[col], errors="coerce")

# ==============================================================================
# EXPLICIT DEATH CENSORING
# censor = min(XTN_DEATH_DATE, obs_end_date, ANALYSIS_END_DATE)
# ==============================================================================

# [C5 CORRECTED]: censor_date uses clinical_end_date from indexed cohort
# clinical_end_date was computed in 01 STEP 7B (before the >=365d filter).
# Fall back to obs_end_date if clinical_end_date absent (pre-correction cohort).
log.info("Applying censor_date = min(clinical_end_date, death) [C5]...")
cohort["XTN_DEATH_DATE"]    = pd.to_datetime(cohort.get("XTN_DEATH_DATE"), errors="coerce")
cohort["obs_end_date"]      = pd.to_datetime(cohort["obs_end_date"],      errors="coerce")

if "clinical_end_date" in cohort.columns:
    cohort["clinical_end_date"] = pd.to_datetime(cohort["clinical_end_date"], errors="coerce")
    log.info("  Using clinical_end_date from indexed cohort (extended obs_end)")
else:
    cohort["clinical_end_date"] = cohort["obs_end_date"]
    log.warning("  clinical_end_date not found in indexed cohort; falling back to obs_end_date")

# censor_date = min(clinical_end_date, XTN_DEATH_DATE if present, ANALYSIS_END_DATE)
# death_filled: fillna so min() over two columns is well-defined
cohort["_death_filled"] = cohort["XTN_DEATH_DATE"].fillna(cohort["clinical_end_date"])
cohort["censor_date"] = (
    cohort[["clinical_end_date", "_death_filled"]].min(axis=1).clip(upper=ANALYSIS_END_DATE)
)
cohort.drop(columns=["_death_filled"], inplace=True)

# Censor date audit
n_death_shortens   = (cohort["XTN_DEATH_DATE"].notna() & (cohort["XTN_DEATH_DATE"] < cohort["clinical_end_date"])).sum()
n_death_after_clin = (cohort["XTN_DEATH_DATE"].notna() & (cohort["XTN_DEATH_DATE"] > cohort["clinical_end_date"])).sum()
n_censor_clinical  = (cohort["censor_date"] == cohort["clinical_end_date"]).sum()
n_censor_death     = (cohort["XTN_DEATH_DATE"].notna() & (cohort["censor_date"] == cohort["XTN_DEATH_DATE"])).sum()
log.info(f"  N death shortens clinical_end_date:  {n_death_shortens:,}")
log.info(f"  N death after clinical_end_date:     {n_death_after_clin:,}")
log.info(f"  N censor_date == clinical_end_date:  {n_censor_clinical:,}")
log.info(f"  N censor_date == XTN_DEATH_DATE:     {n_censor_death:,}")

# ==============================================================================
# TIME-TO-EVENT CONSTRUCTION — using lag-qualified first event dates
# Event = 1 if first qualifying post-lag date occurs on/before censor_date.
# Time = years from index_date to lag-qualified event date or censor_date.
# Prevents within-lag codes from masking later incident events.
# ==============================================================================

log.info("Building time-to-event variables (lag-qualified)...")

def apply_tte(df, qlfd_date_col, outcome_name):
    """Apply TTE using pre-computed lag-qualified first event date."""
    event_mask = (
        df[qlfd_date_col].notna() &
        (df[qlfd_date_col] <= df["censor_date"])
    )
    event_time = np.where(event_mask, df[qlfd_date_col], df["censor_date"])
    time_days  = (pd.to_datetime(event_time) - df["index_date"]).dt.days
    df[f"{outcome_name}_event"]      = event_mask.astype(int)
    df[f"{outcome_name}_time_years"] = np.maximum(time_days / 365.25, 0.0)
    return df

# [C2/C3]: outcomes use lag-qualified dates
# stroke_s1 = PRIMARY vascular; b4_mci = PRIMARY cognitive
# b4 = SECONDARY cognitive; stroke_s2 = SECONDARY vascular
cohort = apply_tte(cohort, "b4_qlfd_date",        "b4")
cohort = apply_tte(cohort, "b4mci_qlfd_date",     "b4_mci")
cohort = apply_tte(cohort, "stroke_s1_qlfd_date", "stroke_s1")
cohort = apply_tte(cohort, "stroke_s2_qlfd_date", "stroke_s2")

# Binary treatment: 1 = ARB, 0 = CCB
cohort["treated"] = (cohort["exposure_group"] == "ARB").astype(int)
log.info(f"  Treated (ARB): {cohort['treated'].sum():,}")
log.info(f"  Control (CCB): {(cohort['treated']==0).sum():,}")

for outcome in ["b4", "b4_mci", "stroke_s1", "stroke_s2"]:
    n_ev = cohort[f"{outcome}_event"].sum()
    log.info(f"  {outcome}: {n_ev:,} events")

# ==============================================================================
# FILTER: POSITIVE FOLLOW-UP (uses lag-qualified b4 time as reference)
# ==============================================================================

cohort = cohort[cohort["b4_time_years"] > 0].copy()
log.info(f"After positive follow-up filter: {len(cohort):,}")

# ==============================================================================
# PROPENSITY SCORE
# Logistic regression; same covariates as v3 primary analysis
# ==============================================================================

log.info("Fitting propensity score model...")

cohort["index_year"] = pd.to_datetime(cohort["index_date"]).dt.year
year_min = cohort["index_year"].min()
cohort["index_year_cat"] = cohort["index_year"].astype(str)
year_dummies = pd.get_dummies(cohort["index_year_cat"], prefix="yr", drop_first=False)
yr_ref = f"yr_{year_min}"
if yr_ref in year_dummies.columns:
    year_dummies = year_dummies.drop(columns=[yr_ref])

ps_df = cohort[cfg.PS_COVARIATES_FIXED].copy()
ps_df = pd.concat([ps_df, year_dummies], axis=1)

# Fix 2: save year dummy columns into cohort so Table 2 adjusted Cox + diagnostics
# can access the same calendar-time covariates from the survival dataset.
year_dummy_cols = list(year_dummies.columns)
for col in year_dummy_cols:
    cohort[col] = year_dummies[col].values

# All persons with complete PS covariates; race_unknown_r is now a model covariate
# (EHR missingness/coding category) — not excluded from PS fit.
ps_complete = ps_df.notna().all(axis=1)
log.info(f"  Persons with complete PS covariates (all races included): {ps_complete.sum():,}")
n_unknown_in_ps = int(cohort.loc[ps_complete, "race_unknown_r"].fillna(0).sum())
log.info(f"  Of those, race_unknown_r in PS fit: {n_unknown_in_ps:,}")

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
    random_state=RANDOM_SEED,
)
lr.fit(X_s, y)
# Fix 4: log true PS AUC/C-statistic (not classification accuracy)
ps_auc = roc_auc_score(y, lr.predict_proba(X_s)[:, 1])
log.info(f"  PS model AUC/C-statistic (complete cases): {ps_auc:.4f}")

ps_full = pd.Series(np.nan, index=cohort.index)
ps_full.loc[ps_complete] = lr.predict_proba(X_s)[:, 1]
cohort["ps"] = ps_full

# Log post-PS-fit N by race category
for race_col in ["race_white_r", "race_black_r", "race_asian_r", "race_other_r", "race_unknown_r"]:
    if race_col in cohort.columns:
        n_race = int(cohort[race_col].fillna(0).sum())
        n_race_ps = int(cohort.loc[cohort["ps"].notna(), race_col].fillna(0).sum())
        log.info(f"  {race_col}: N={n_race:,} in cohort, N={n_race_ps:,} with PS computed")

# ==============================================================================
# PS TRIMMING (1st–99th percentile of each arm)
# ==============================================================================

ps_arb = cohort.loc[cohort["treated"] == 1, "ps"].dropna()
ps_ccb = cohort.loc[cohort["treated"] == 0, "ps"].dropna()
lo = min(np.nanpercentile(ps_arb, PS_TRIM_LOWER * 100),
         np.nanpercentile(ps_ccb, PS_TRIM_LOWER * 100))
hi = max(np.nanpercentile(ps_arb, PS_TRIM_UPPER * 100),
         np.nanpercentile(ps_ccb, PS_TRIM_UPPER * 100))

n_pre = len(cohort)
cohort = cohort[cohort["ps"].notna() & cohort["ps"].between(lo, hi)].copy()
log.info(f"PS trimming ({PS_TRIM_LOWER}–{PS_TRIM_UPPER}): removed {n_pre - len(cohort):,}")
log.info(f"Post-PS-trim N: {len(cohort):,}  (ARB={cohort['treated'].sum():,}, CCB={(cohort['treated']==0).sum():,})")

# ==============================================================================
# STABILIZED ATE IPTW
# ==============================================================================

p_treat = cohort["treated"].mean()
cohort["iptw"] = np.where(
    cohort["treated"] == 1,
    p_treat       / cohort["ps"],
    (1 - p_treat) / (1 - cohort["ps"]),
)

# Winsorize weights
w_lo = cohort["iptw"].quantile(0.01)
w_hi = cohort["iptw"].quantile(0.99)
cohort["iptw"] = cohort["iptw"].clip(lower=w_lo, upper=w_hi)
log.info(f"IPTW range after winsorizing: [{cohort['iptw'].min():.3f}, {cohort['iptw'].max():.3f}]")

# ==============================================================================
# COVARIATE BALANCE
# ==============================================================================

log.info("Computing covariate balance (SMD before/after IPTW)...")
balance_rows = []
covs = cfg.PS_COVARIATES_FIXED + [c for c in cohort.columns if c.startswith("yr_")]
for cov in covs:
    if cov not in cohort.columns:
        continue
    a = cohort.loc[cohort["treated"] == 1, cov].dropna()
    b = cohort.loc[cohort["treated"] == 0, cov].dropna()
    pooled_sd = np.sqrt((a.std()**2 + b.std()**2) / 2) if (a.std() + b.std()) > 0 else np.nan
    smd_pre = (a.mean() - b.mean()) / pooled_sd if pooled_sd and pooled_sd > 0 else np.nan

    wa = cohort.loc[cohort["treated"] == 1, cov]
    wb = cohort.loc[cohort["treated"] == 0, cov]
    wa_wt = cohort.loc[cohort["treated"] == 1, "iptw"]
    wb_wt = cohort.loc[cohort["treated"] == 0, "iptw"]
    mean_a_w = np.average(wa.dropna(), weights=wa_wt.loc[wa.notna()])
    mean_b_w = np.average(wb.dropna(), weights=wb_wt.loc[wb.notna()])
    smd_post = (mean_a_w - mean_b_w) / pooled_sd if pooled_sd and pooled_sd > 0 else np.nan

    balance_rows.append({
        "covariate":  cov,
        "mean_arb":   a.mean(),
        "mean_ccb":   b.mean(),
        "smd_pre":    smd_pre,
        "mean_arb_w": mean_a_w,
        "mean_ccb_w": mean_b_w,
        "smd_post":   smd_post,
    })

balance_df = pd.DataFrame(balance_rows)
bal_out = cfg.OUT_DIR / "run01_covariate_balance.csv"
balance_df.to_csv(bal_out, index=False)
log.info(f"Saved: {bal_out}")

# ==============================================================================
# SAVE SURVIVAL DATASET
# ==============================================================================

cohort.to_parquet(cfg.RUN01_SURVIVAL_DATASET, index=False)
log.info(f"Saved: {cfg.RUN01_SURVIVAL_DATASET}  ({len(cohort):,} rows)")
log.info("02_outcomes_and_ps_deathcensor_run01 complete.")
