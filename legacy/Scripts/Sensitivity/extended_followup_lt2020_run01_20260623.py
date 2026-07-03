"""
extended_followup_lt2020_run01_20260623.py
run01_v4_core_design_deathcensor — Extended Potential Follow-Up Sensitivity Analysis

Restriction: index_date < 2020-01-01  (lt2020 / ~≥6yr potential follow-up based on
             admin end date 2025-12-31). Eligibility by index-date only;
             observed follow-up duration is NOT used as an inclusion criterion.

Design mirrors final run01 primary analysis:
  - Same PS covariates (PS_COVARIATES_FIXED + index_year dummies; ref = earliest year in subgroup)
  - PS refit within the <2020 subgroup
  - Stabilized ATE IPTW; winsorized 1st–99th percentile weights
  - PS 1st–99th percentile overlap trimming applied after PS refit
  - ITT estimand (treatment-initiation)
  - Same lag windows: dementia 180d, stroke/TIA 90d
  - Same censoring rules (censor_date already in survival dataset)
  - Unadjusted, covariate-adjusted, and IPTW-weighted Cox (robust SEs for IPTW)
  - Bonferroni and BH-FDR correction for primary outcomes only (stroke_s1, b4_mci)
  - Secondary outcomes (b4, stroke_s2): nominal p-values only

Low-memory:
  - pyarrow.parquet.read_schema() for schema inspection
  - usecols read only
  - No plots generated

Administrative end date: 2025-12-31 (run01 fixed — never use date.today())
Random seed: 42
Author: (initials)
Date: 2026-06-23
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from lifelines import CoxPHFitter
from statsmodels.stats.multitest import multipletests

warnings.filterwarnings("ignore")

# ==============================================================================
# PATHS (from run01_config.py; standalone hardcoded)
# ==============================================================================

BASE_DIR = Path("/Users/akarshsharma/Desktop/tte-project")
OUT_DIR  = (
    BASE_DIR
    / "AIRMS" / "results" / "final_candidate_runs_20260531"
    / "run01_v4_core_design_deathcensor"
)
SURVIVAL_DATASET = OUT_DIR / "run01_survival_dataset.parquet"
RESULTS_DIR = OUT_DIR / "supplemental_extended_potential_followup_lt2020"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ANALYSIS_END_DATE  = pd.Timestamp("2025-12-31")  # run01 fixed — DO NOT change
INDEX_DATE_CUTOFF  = pd.Timestamp("2020-01-01")   # <2020 => ~≥6yr potential FU (lt2020)
RANDOM_SEED        = 42
PS_TRIM_LOWER      = 0.01
PS_TRIM_UPPER      = 0.99

# PS covariates (from run01_config.py PS_COVARIATES_FIXED)
PS_COVARIATES_FIXED = [
    "age_at_index",
    "female",
    "race_black_r",
    "race_asian_r",
    "race_other_r",
    "race_unknown_r",
    "hispanic",
    "bl_diabetes",
    "bl_ckd",
    "bl_heart_failure",
    "bl_cad_mi",
    "bl_afib",
    "bl_pad",
    "bl_tia",
]

# Outcomes: (internal_name, role, label, event_col, time_col)
OUTCOMES = [
    ("stroke_s1", "primary",   "Acute ischemic stroke",
     "stroke_s1_event", "stroke_s1_time_years"),
    ("b4_mci",    "primary",   "Probable dementia + MCI",
     "b4_mci_event",    "b4_mci_time_years"),
    ("b4",        "secondary", "Probable dementia alone",
     "b4_event",         "b4_time_years"),
    ("stroke_s2", "secondary", "Ischemic stroke + TIA",
     "stroke_s2_event", "stroke_s2_time_years"),
]

PRIMARY_OUTCOMES   = [o[0] for o in OUTCOMES if o[1] == "primary"]
SECONDARY_OUTCOMES = [o[0] for o in OUTCOMES if o[1] == "secondary"]

WARNINGS_LOG = []

def warn(msg):
    print(f"  WARNING: {msg}")
    WARNINGS_LOG.append(msg)

# ==============================================================================
# ENDPOINT AUDIT (from run01_config.py; confirmed)
# ==============================================================================

print("=" * 70)
print("ENDPOINT AUDIT")
print("=" * 70)

ENDPOINT_AUDIT_ROWS = [
    {
        "outcome":               "stroke_s1",
        "role":                  "primary",
        "snomed_ids":            "443454; 372924",
        "4009705_in_b4_mci":     "N/A",
        "4164092_NOT_in_s1":     True,
        "381591_NOT_in_s2":      "N/A",
        "tia_concept_in_s2":     "N/A",
        "lag_days":              90,
        "note": "Harmonized AIS. 4164092 sensitivity only. 381591 excluded.",
    },
    {
        "outcome":               "b4_mci",
        "role":                  "primary",
        "snomed_ids":            "378419; 443605; 4182210; 439795; 4009705",
        "4009705_in_b4_mci":     True,
        "4164092_NOT_in_s1":     "N/A",
        "381591_NOT_in_s2":      "N/A",
        "tia_concept_in_s2":     "N/A",
        "lag_days":              180,
        "note": "4009705 (R41.81) confirmed included.",
    },
    {
        "outcome":               "b4",
        "role":                  "secondary",
        "snomed_ids":            "378419; 443605; 4182210",
        "4009705_in_b4_mci":     "N/A",
        "4164092_NOT_in_s1":     "N/A",
        "381591_NOT_in_s2":      "N/A",
        "tia_concept_in_s2":     "N/A",
        "lag_days":              180,
        "note": "Probable dementia alone (subset of b4_mci SNOMEDs).",
    },
    {
        "outcome":               "stroke_s2",
        "role":                  "secondary",
        "snomed_ids":            "443454; 372924; 373503",
        "4009705_in_b4_mci":     "N/A",
        "4164092_NOT_in_s1":     "N/A",
        "381591_NOT_in_s2":      True,
        "tia_concept_in_s2":     373503,
        "lag_days":              90,
        "note": "TIA=373503 only. 381591 excluded. 4164092 excluded.",
    },
]

audit_df = pd.DataFrame(ENDPOINT_AUDIT_ROWS)
audit_out = RESULTS_DIR / "endpoint_audit_minimal.csv"
audit_df.to_csv(audit_out, index=False)
print(f"Saved: {audit_out}")

# ==============================================================================
# SCHEMA INSPECTION (low-memory)
# ==============================================================================

print("\n" + "=" * 70)
print("SCHEMA INSPECTION")
print("=" * 70)

schema = pq.read_schema(SURVIVAL_DATASET)
all_cols = schema.names
print(f"Columns in survival dataset: {len(all_cols)}")

# Required columns
OUTCOME_COLS = []
for oname, role, label, ev_col, tm_col in OUTCOMES:
    OUTCOME_COLS += [ev_col, tm_col]

REQUIRED_COLS = (
    ["PERSON_ID", "index_date", "censor_date", "exposure_group", "treated",
     "index_year"]
    + PS_COVARIATES_FIXED
    + OUTCOME_COLS
)

missing = [c for c in REQUIRED_COLS if c not in all_cols]
if missing:
    sys.exit(f"ABORT — missing columns in survival dataset: {missing}")

print(f"All {len(REQUIRED_COLS)} required columns present.")

# ==============================================================================
# LOAD DATA (required columns only)
# ==============================================================================

print("\n" + "=" * 70)
print("LOADING DATA")
print("=" * 70)

USECOLS = [c for c in REQUIRED_COLS if c in all_cols]
df = pd.read_parquet(SURVIVAL_DATASET, columns=USECOLS)
df["index_date"]  = pd.to_datetime(df["index_date"])
df["censor_date"] = pd.to_datetime(df["censor_date"])

print(f"Loaded {len(df):,} rows, {len(USECOLS)} columns")

# ==============================================================================
# INDEX-DATE RESTRICTION  (potential FU only; no observed FU restriction)
# ==============================================================================

print("\n" + "=" * 70)
print("INDEX-DATE RESTRICTION")
print("=" * 70)

df = df[df["index_date"] < INDEX_DATE_CUTOFF].copy()
n_after_cut = len(df)
arb_cut     = int(df["treated"].sum())
ccb_cut     = int((df["treated"] == 0).sum())
print(f"After index_date < {INDEX_DATE_CUTOFF.date()} (lt2020 / ~≥6yr potential FU): N={n_after_cut:,}  ARB={arb_cut:,}  CCB={ccb_cut:,}")

if n_after_cut == 0:
    sys.exit("ABORT — zero records after index-date restriction.")

# ==============================================================================
# PS REFIT WITHIN SUBGROUP
# index_year dummies rebuilt within subgroup; ref = earliest year present
# ==============================================================================

print("\n" + "=" * 70)
print("PS REFIT — <2020 SUBGROUP (lt2020 / ~≥6yr potential FU)")
print("=" * 70)

df["index_year_sub"] = df["index_date"].dt.year
year_min_sub = int(df["index_year_sub"].min())
year_max_sub = int(df["index_year_sub"].max())
print(f"  Index year range in subgroup: {year_min_sub}–{year_max_sub}")
print(f"  Reference year (omitted from model): {year_min_sub}")

year_dummies = pd.get_dummies(
    df["index_year_sub"].astype(str), prefix="yr_sub", drop_first=False
)
yr_ref_col = f"yr_sub_{year_min_sub}"
if yr_ref_col in year_dummies.columns:
    year_dummies = year_dummies.drop(columns=[yr_ref_col])
year_dummy_cols = list(year_dummies.columns)

# Attach year dummies to df
for col in year_dummy_cols:
    df[col] = year_dummies[col].values

ps_features = PS_COVARIATES_FIXED + year_dummy_cols
ps_df       = df[ps_features].copy()
ps_complete = ps_df.notna().all(axis=1)
n_ps_complete = int(ps_complete.sum())
n_ps_missing  = int((~ps_complete).sum())
print(f"  Persons with complete PS covariates: {n_ps_complete:,}  (missing: {n_ps_missing:,})")

if n_ps_missing > 0:
    warn(f"{n_ps_missing} persons excluded from PS fit due to incomplete covariates.")

# Persons with unknown race included as race_unknown_r covariate (run01 design C7 corrected)
n_unknown_in_ps = int(df.loc[ps_complete, "race_unknown_r"].fillna(0).sum())
print(f"  race_unknown_r in PS fit: {n_unknown_in_ps:,}")

X_fit = ps_df.loc[ps_complete].values
y_fit = df.loc[ps_complete, "treated"].values

scaler = StandardScaler()
X_s    = scaler.fit_transform(X_fit)

lr = LogisticRegression(
    penalty="l2", C=1.0, solver="lbfgs", max_iter=2000, random_state=RANDOM_SEED
)
lr.fit(X_s, y_fit)

ps_auc = roc_auc_score(y_fit, lr.predict_proba(X_s)[:, 1])
print(f"  PS C-statistic (AUC, complete cases): {ps_auc:.4f}")

if ps_auc > 0.9:
    warn(f"PS AUC = {ps_auc:.4f} — very high; check for separation or near-perfect prediction in small subgroup.")

ps_full = pd.Series(np.nan, index=df.index)
ps_full.loc[ps_complete] = lr.predict_proba(X_s)[:, 1]
df["ps_sub"] = ps_full

# ==============================================================================
# PS OVERLAP TRIMMING (1st–99th percentile within subgroup)
# ==============================================================================

print("\n" + "=" * 70)
print("PS OVERLAP TRIMMING")
print("=" * 70)

ps_arb = df.loc[df["treated"] == 1, "ps_sub"].dropna()
ps_ccb = df.loc[df["treated"] == 0, "ps_sub"].dropna()
lo = min(np.nanpercentile(ps_arb, PS_TRIM_LOWER * 100),
         np.nanpercentile(ps_ccb, PS_TRIM_LOWER * 100))
hi = max(np.nanpercentile(ps_arb, PS_TRIM_UPPER * 100),
         np.nanpercentile(ps_ccb, PS_TRIM_UPPER * 100))

n_pre_trim = len(df)
df = df[df["ps_sub"].notna() & df["ps_sub"].between(lo, hi)].copy()
n_post_trim = len(df)
n_removed   = n_pre_trim - n_post_trim
arb_post    = int(df["treated"].sum())
ccb_post    = int((df["treated"] == 0).sum())

print(f"  PS range: [{lo:.4f}, {hi:.4f}]")
print(f"  Removed by trimming: {n_removed:,}")
print(f"  Post-trim N: {n_post_trim:,}  ARB={arb_post:,}  CCB={ccb_post:,}")

# ==============================================================================
# STABILIZED IPTW (winsorized 1st–99th percentile)
# ==============================================================================

print("\n" + "=" * 70)
print("STABILIZED IPTW")
print("=" * 70)

p_treat = df["treated"].mean()
df["iptw_sub"] = np.where(
    df["treated"] == 1,
    p_treat       / df["ps_sub"],
    (1 - p_treat) / (1 - df["ps_sub"]),
)

w_lo = df["iptw_sub"].quantile(0.01)
w_hi = df["iptw_sub"].quantile(0.99)
df["iptw_sub"] = df["iptw_sub"].clip(lower=w_lo, upper=w_hi)

w_mean = df["iptw_sub"].mean()
w_min  = df["iptw_sub"].min()
w_max  = df["iptw_sub"].max()
print(f"  Weights after winsorization: min={w_min:.4f}  max={w_max:.4f}  mean={w_mean:.4f}")

# Effective sample size
ess_arb   = float(df.loc[df["treated"]==1, "iptw_sub"].sum()**2 /
                  (df.loc[df["treated"]==1, "iptw_sub"]**2).sum())
ess_ccb   = float(df.loc[df["treated"]==0, "iptw_sub"].sum()**2 /
                  (df.loc[df["treated"]==0, "iptw_sub"]**2).sum())
ess_total = ess_arb + ess_ccb
print(f"  ESS: overall={ess_total:.1f}  ARB={ess_arb:.1f}  CCB={ess_ccb:.1f}")

# ==============================================================================
# COVARIATE BALANCE (SMD before/after IPTW)
# ==============================================================================

print("\n" + "=" * 70)
print("COVARIATE BALANCE")
print("=" * 70)

balance_rows = []
balance_covs = PS_COVARIATES_FIXED + year_dummy_cols

for cov in balance_covs:
    if cov not in df.columns:
        continue
    a = df.loc[df["treated"] == 1, cov].dropna()
    b = df.loc[df["treated"] == 0, cov].dropna()
    s_pool = np.sqrt((a.std()**2 + b.std()**2) / 2) if (a.std() + b.std()) > 0 else np.nan
    smd_pre = (a.mean() - b.mean()) / s_pool if (s_pool and s_pool > 0) else np.nan

    wa  = df.loc[df["treated"] == 1, cov]
    wb  = df.loc[df["treated"] == 0, cov]
    wgt_a = df.loc[df["treated"] == 1, "iptw_sub"]
    wgt_b = df.loc[df["treated"] == 0, "iptw_sub"]
    mu_a  = np.average(wa.dropna(), weights=wgt_a.loc[wa.notna()])
    mu_b  = np.average(wb.dropna(), weights=wgt_b.loc[wb.notna()])
    smd_post = (mu_a - mu_b) / s_pool if (s_pool and s_pool > 0) else np.nan

    balance_rows.append({
        "covariate":  cov,
        "mean_arb_unweighted":   round(a.mean(), 5),
        "mean_ccb_unweighted":   round(b.mean(), 5),
        "smd_before_iptw":       round(smd_pre, 5) if not np.isnan(smd_pre) else None,
        "mean_arb_iptw":         round(mu_a, 5),
        "mean_ccb_iptw":         round(mu_b, 5),
        "smd_after_iptw":        round(smd_post, 5) if not np.isnan(smd_post) else None,
    })

balance_df = pd.DataFrame(balance_rows)

max_smd_pre  = float(balance_df["smd_before_iptw"].abs().max())
max_smd_post = float(balance_df["smd_after_iptw"].abs().max())
print(f"  Max |SMD| before IPTW: {max_smd_pre:.4f}")
print(f"  Max |SMD| after  IPTW: {max_smd_post:.4f}")
if max_smd_post > 0.10:
    warn(f"Max |SMD| post-IPTW = {max_smd_post:.4f} > 0.10 — residual imbalance.")

balance_out = RESULTS_DIR / "extended_followup_lt2020_balance_summary.csv"
balance_df.to_csv(balance_out, index=False)
print(f"  Saved: {balance_out}")

# ==============================================================================
# COX MODELS
# Helper: format HR and 95% CI string
# ==============================================================================

def fmt_ci(hr, lo, hi, decimals=2):
    """Format HR (lo–hi) as string."""
    fmt = f"{{:.{decimals}f}}"
    return f"{fmt.format(hr)} ({fmt.format(lo)}–{fmt.format(hi)})"

def run_cox_unadjusted(sub, ev_col, tm_col):
    """Unadjusted Cox: treatment only."""
    d = sub[[tm_col, ev_col, "treated"]].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    cph = CoxPHFitter()
    try:
        cph.fit(d, duration_col=tm_col, event_col=ev_col, formula="treated")
        hr  = float(np.exp(cph.params_["treated"]))
        lo  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv  = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"Unadjusted Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)

def run_cox_adjusted(sub, ev_col, tm_col, covariates):
    """Covariate-adjusted Cox: treatment + PS covariates."""
    needed = [tm_col, ev_col, "treated"] + covariates
    avail  = [c for c in needed if c in sub.columns]
    d = sub[avail].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    formula = "treated + " + " + ".join(c for c in covariates if c in d.columns)
    cph = CoxPHFitter()
    try:
        cph.fit(d, duration_col=tm_col, event_col=ev_col, formula=formula)
        hr  = float(np.exp(cph.params_["treated"]))
        lo  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv  = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"Adjusted Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)

def run_cox_iptw(sub, ev_col, tm_col):
    """IPTW-weighted Cox (robust SEs): treatment only with weights."""
    d = sub[[tm_col, ev_col, "treated", "iptw_sub"]].dropna().copy()
    d = d[d[tm_col] > 0]
    if d[ev_col].sum() == 0:
        return None, None, None, None, None
    cph = CoxPHFitter()
    try:
        cph.fit(
            d,
            duration_col=tm_col,
            event_col=ev_col,
            formula="treated",
            weights_col="iptw_sub",
            robust=True,
        )
        hr  = float(np.exp(cph.params_["treated"]))
        lo  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        hi  = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        pv  = float(cph.summary.loc["treated", "p"])
        return hr, lo, hi, pv, None
    except Exception as e:
        warn(f"IPTW Cox failed ({ev_col}): {e}")
        return None, None, None, None, str(e)

# ==============================================================================
# RUN MODELS FOR ALL 4 OUTCOMES
# ==============================================================================

print("\n" + "=" * 70)
print("COX MODELS")
print("=" * 70)

# Covariate-adjusted formula columns: PS fixed + year dummies
ADJ_COVARIATES = [c for c in (PS_COVARIATES_FIXED + year_dummy_cols) if c in df.columns]

results_rows = []
iptw_pvals_primary = {}   # for Bonferroni + BH-FDR

for oname, role, label, ev_col, tm_col in OUTCOMES:
    print(f"\n--- {oname} ({role}) ---")
    if ev_col not in df.columns or tm_col not in df.columns:
        warn(f"Missing column for {oname}: {ev_col} or {tm_col}")
        continue

    arb_mask   = df["treated"] == 1
    ev_arb     = int(df.loc[arb_mask, ev_col].sum())
    ev_ccb     = int(df.loc[~arb_mask, ev_col].sum())
    ev_total   = int(df[ev_col].sum())
    print(f"  Events: ARB={ev_arb}  CCB={ev_ccb}  total={ev_total}")

    if ev_arb < 10 or ev_ccb < 10:
        warn(f"{oname}: per-arm event count below 10 (ARB={ev_arb}, CCB={ev_ccb}) — estimates unreliable.")
    elif ev_arb < 20 or ev_ccb < 20:
        warn(f"{oname}: per-arm event count below 20 (ARB={ev_arb}, CCB={ev_ccb}) — wide CIs expected.")

    # Follow-up for this outcome (using time col as proxy)
    obs_fu = df[tm_col].dropna()
    med_fu = float(obs_fu.median())
    q25_fu = float(obs_fu.quantile(0.25))
    q75_fu = float(obs_fu.quantile(0.75))
    print(f"  Follow-up: median={med_fu:.2f}yr  IQR=[{q25_fu:.2f}, {q75_fu:.2f}]")

    # Unadjusted
    hr_u, lo_u, hi_u, p_u, err_u = run_cox_unadjusted(df, ev_col, tm_col)
    if hr_u is not None:
        print(f"  Unadjusted:  HR={hr_u:.3f} ({lo_u:.3f}–{hi_u:.3f})  p={p_u:.4f}")
    else:
        print(f"  Unadjusted:  FAILED — {err_u}")

    # Covariate-adjusted
    hr_a, lo_a, hi_a, p_a, err_a = run_cox_adjusted(df, ev_col, tm_col, ADJ_COVARIATES)
    if hr_a is not None:
        print(f"  Adjusted:    HR={hr_a:.3f} ({lo_a:.3f}–{hi_a:.3f})  p={p_a:.4f}")
    else:
        print(f"  Adjusted:    FAILED — {err_a}")

    # IPTW-weighted (robust SEs)
    hr_w, lo_w, hi_w, p_w, err_w = run_cox_iptw(df, ev_col, tm_col)
    if hr_w is not None:
        print(f"  IPTW:        HR={hr_w:.3f} ({lo_w:.3f}–{hi_w:.3f})  p={p_w:.4f}")
    else:
        print(f"  IPTW:        FAILED — {err_w}")

    if role == "primary" and p_w is not None:
        iptw_pvals_primary[oname] = p_w

    results_rows.append({
        "outcome":               oname,
        "endpoint_type":         role,
        "analytic_n":            n_post_trim,
        "ARB_n":                 arb_post,
        "DHPCCB_n":              ccb_post,
        "events_ARB":            ev_arb,
        "events_DHPCCB":         ev_ccb,
        "unadjusted_HR":         round(hr_u, 4) if hr_u is not None else None,
        "unadjusted_95CI":       fmt_ci(hr_u, lo_u, hi_u) if hr_u is not None else None,
        "adjusted_HR":           round(hr_a, 4) if hr_a is not None else None,
        "adjusted_95CI":         fmt_ci(hr_a, lo_a, hi_a) if hr_a is not None else None,
        "IPTW_HR":               round(hr_w, 4) if hr_w is not None else None,
        "IPTW_95CI":             fmt_ci(hr_w, lo_w, hi_w) if hr_w is not None else None,
        "IPTW_p":                round(p_w, 6) if p_w is not None else None,
        "Bonferroni_p_primary_only": None,   # filled below
        "BH_FDR_p_primary_only":    None,   # filled below
        "max_abs_SMD_before_IPTW":  round(max_smd_pre, 5),
        "max_abs_SMD_after_IPTW":   round(max_smd_post, 5),
    })

# ==============================================================================
# MULTIPLE TESTING CORRECTION — primary outcomes only
# Bonferroni (K=2) and BH-FDR (K=2) across stroke_s1 and b4_mci
# ==============================================================================

print("\n" + "=" * 70)
print("MULTIPLE TESTING CORRECTION (primary outcomes only)")
print("=" * 70)

primary_order = [oname for oname in PRIMARY_OUTCOMES if oname in iptw_pvals_primary]
if len(primary_order) > 0:
    p_raw   = [iptw_pvals_primary[o] for o in primary_order]
    _, p_bonf, _, _ = multipletests(p_raw, method="bonferroni")
    _, p_bh,   _, _ = multipletests(p_raw, method="fdr_bh")
    bonf_map = {o: round(float(pb), 6) for o, pb in zip(primary_order, p_bonf)}
    bh_map   = {o: round(float(pb), 6) for o, pb in zip(primary_order, p_bh)}
    for o in primary_order:
        print(f"  {o}: raw_p={iptw_pvals_primary[o]:.6f}  Bonf={bonf_map[o]:.6f}  BH-FDR={bh_map[o]:.6f}")
else:
    bonf_map, bh_map = {}, {}
    warn("No primary outcomes with valid IPTW p-values — multiple testing correction skipped.")

# Backfill into results rows
for row in results_rows:
    oname = row["outcome"]
    if oname in bonf_map:
        row["Bonferroni_p_primary_only"] = bonf_map[oname]
        row["BH_FDR_p_primary_only"]     = bh_map[oname]

# ==============================================================================
# SAVE RESULTS CSV
# ==============================================================================

results_df = pd.DataFrame(results_rows)
results_out = RESULTS_DIR / "extended_followup_lt2020_results.csv"
results_df.to_csv(results_out, index=False)
print(f"\nSaved: {results_out}")

# ==============================================================================
# QC REPORT MARKDOWN
# ==============================================================================

print("\n" + "=" * 70)
print("QC REPORT")
print("=" * 70)

obs_fu_all = df["b4_time_years"].dropna()
med_fu_overall  = float(obs_fu_all.median())
q25_fu_overall  = float(obs_fu_all.quantile(0.25))
q75_fu_overall  = float(obs_fu_all.quantile(0.75))

lines = []
lines.append("# Extended Potential Follow-Up lt2020 (~≥6yr) — QC Report")
lines.append("")
lines.append("**Run:** run01_v4_core_design_deathcensor")
lines.append("**Date:** 2026-06-23")
lines.append("**Script:** extended_followup_lt2020_run01_20260623.py")
lines.append("**Admin end date:** 2025-12-31 (run01 fixed)")
lines.append("")
lines.append("---")
lines.append("")

lines.append("## Cohort Restriction")
lines.append(f"- Index-date cutoff: `< {INDEX_DATE_CUTOFF.date()}` (lt2020 / ~≥6yr potential follow-up)")
lines.append("- Restriction type: potential follow-up (index-date only; observed follow-up NOT used as inclusion criterion)")
lines.append(f"- N after index-date restriction: {n_after_cut:,}  (ARB={arb_cut:,}  CCB={ccb_cut:,})")
lines.append("")

lines.append("## PS Refit")
lines.append(f"- N with complete PS covariates: {n_ps_complete:,}  (incomplete excluded: {n_ps_missing:,})")
lines.append(f"- PS model: L2 logistic regression (C=1.0, lbfgs, max_iter=2000, seed=42)")
lines.append(f"- PS C-statistic (AUC): {ps_auc:.4f}")
lines.append(f"- Index year range in subgroup: {year_min_sub}–{year_max_sub} (ref={year_min_sub})")
lines.append(f"- race_unknown_r in PS fit: {n_unknown_in_ps:,}")
lines.append("")

lines.append("## PS Trimming and Final Analytic Cohort")
lines.append(f"- PS trimming: 1st–99th percentile overlap")
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
        f"| {row['events_ARB']} | {row['events_DHPCCB']} | {row['events_ARB']+row['events_DHPCCB']} |"
    )
lines.append("")

lines.append("## Follow-Up")
lines.append(f"- Median observed follow-up (b4 time, overall): {med_fu_overall:.2f} yr  IQR [{q25_fu_overall:.2f}, {q75_fu_overall:.2f}]")
lines.append("")

lines.append("## IPTW")
lines.append(f"- Stabilized ATE weights; winsorized 1st–99th percentile")
lines.append(f"- Weight range: [{w_min:.4f}, {w_max:.4f}]  mean={w_mean:.4f}")
lines.append(f"- ESS: overall={ess_total:.1f}  ARB={ess_arb:.1f}  CCB={ess_ccb:.1f}")
lines.append("")

lines.append("## Covariate Balance")
lines.append(f"- Max |SMD| before IPTW: {max_smd_pre:.4f}")
lines.append(f"- Max |SMD| after IPTW:  {max_smd_post:.4f}")
lines.append(f"  _(see extended_followup_10yr_balance_summary.csv for per-covariate details)_")
lines.append("")

lines.append("## Model Results Summary")
lines.append("")
lines.append("| Outcome | Role | IPTW HR | IPTW 95% CI | IPTW p | Bonf p | BH-FDR p |")
lines.append("|---------|------|---------|------------|--------|--------|---------|")
for row in results_rows:
    bonf_str = str(row["Bonferroni_p_primary_only"]) if row["Bonferroni_p_primary_only"] is not None else "—"
    bh_str   = str(row["BH_FDR_p_primary_only"]) if row["BH_FDR_p_primary_only"] is not None else "—"
    p_str    = f"{row['IPTW_p']:.4f}" if row["IPTW_p"] is not None else "—"
    ci_str   = row["IPTW_95CI"] if row["IPTW_95CI"] else "—"
    hr_str   = f"{row['IPTW_HR']:.3f}" if row["IPTW_HR"] is not None else "—"
    lines.append(
        f"| {row['outcome']} | {row['endpoint_type']} | {hr_str} | {ci_str} "
        f"| {p_str} | {bonf_str} | {bh_str} |"
    )
lines.append("")

if WARNINGS_LOG:
    lines.append("## Warnings and Convergence Notes")
    lines.append("")
    for w in WARNINGS_LOG:
        lines.append(f"- {w}")
    lines.append("")
else:
    lines.append("## Warnings and Convergence Notes")
    lines.append("")
    lines.append("- None.")
    lines.append("")

qc_out = RESULTS_DIR / "extended_followup_lt2020_qc_report.md"
with open(qc_out, "w") as fh:
    fh.write("\n".join(lines))
print(f"Saved: {qc_out}")

# ==============================================================================
# FINAL SUMMARY
# ==============================================================================

print("\n" + "=" * 70)
print("COMPLETED")
print("=" * 70)
print(f"Output directory: {RESULTS_DIR}")
print(f"  1. {results_out.name}")
print(f"  2. {qc_out.name}")
print(f"  3. {balance_out.name}")
print(f"  4. {audit_out.name}")
if WARNINGS_LOG:
    print(f"\nWarnings ({len(WARNINGS_LOG)}):")
    for w in WARNINGS_LOG:
        print(f"  - {w}")
