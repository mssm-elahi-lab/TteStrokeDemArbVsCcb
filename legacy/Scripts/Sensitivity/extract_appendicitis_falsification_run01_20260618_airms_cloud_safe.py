"""
extract_appendicitis_falsification_run01_20260618_airms_cloud_safe.py

Appendicitis falsification (negative control) endpoint analysis
TTE ARB vs DHP-CCB project — BioMe Biobank (Mount Sinai)

PURPOSE:
    Build patient-level appendicitis endpoint for the run01 denominator cohort
    and run falsification Cox models (crude, covariate-adjusted, IPTW-weighted)
    using the same modeling conventions as the primary Table 2 analysis.

    Appendicitis is not biologically plausible as an ARB vs CCB treatment effect;
    a null hazard ratio is expected under proper confounding control.

INPUTS:
    1. run01_survival_dataset.parquet  — final run01 analytic cohort
    2. appendicitis_conditions_airms_raw_NARROW.parquet — NARROW concept set only

FIXED CONSTANTS:
    ANALYSIS_END_DATE: 2025-12-31  (v3 AIRMS analysis)
    RANDOM_SEED: 42

MODELING CONVENTIONS (matching 04_table2_run01_20260531.py):
    - Crude:     CoxPH with "treated" only
    - Adjusted:  CoxPH with "treated" + PS_COVARIATES_FIXED + year dummies
    - IPTW:      CoxPH with "treated", weights=iptw, robust=True (sandwich SE)
    - penalizer=0 by default; retry with 0.01 on convergence failure (logged)

OUTPUTS (all in AIRMS/results/appendicitis_falsification_run01/):
    appendicitis_conditions_run01_raw_NARROW.parquet
    appendicitis_falsification_endpoint_run01.parquet
    appendicitis_falsification_analysis_dataset_run01.parquet
    appendicitis_falsification_results_run01.csv
    appendicitis_falsification_audit_note.md

Run ID: run01_v4_core_design_deathcensor
Author: (initials)
Date:   2026-06-18
"""

# =============================================================================
# IMPORTS
# =============================================================================

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

try:
    from lifelines import CoxPHFitter
except ImportError:
    raise ImportError("lifelines is required: pip install lifelines")

# =============================================================================
# FIXED CONSTANTS
# =============================================================================

ANALYSIS_END_DATE = pd.Timestamp("2025-12-31")  # v3 AIRMS analysis — do not use date.today()
RANDOM_SEED = 42

# =============================================================================
# PATHS
# =============================================================================

BASE_DIR = Path("/Users/akarshsharma/Desktop/tte-project")

RUN01_SURVIVAL = (
    BASE_DIR
    / "AIRMS/results/final_candidate_runs_20260531/run01_v4_core_design_deathcensor"
    / "run01_survival_dataset.parquet"
)

APPENDICITIS_NARROW = (
    BASE_DIR
    / "AIRMS/most recent extract/appendicitis-export"
    / "appendicitis_conditions_airms_raw_NARROW.parquet"
)

OUT_DIR = BASE_DIR / "AIRMS/results/appendicitis_falsification_run01"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# PS MODEL COVARIATES (matching run01_config.PS_COVARIATES_FIXED)
# =============================================================================

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

# =============================================================================
# LOAD DATA
# =============================================================================

print("=" * 70)
print("APPENDICITIS FALSIFICATION ANALYSIS — run01")
print(f"Analysis end date (fixed): {ANALYSIS_END_DATE.date()}")
print("=" * 70)

# Guard: verify input files exist
for p in [RUN01_SURVIVAL, APPENDICITIS_NARROW]:
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {p}")

surv = pd.read_parquet(RUN01_SURVIVAL)
print(f"\n[1] Loaded run01_survival_dataset: {len(surv):,} rows")

# Guard: PERSON_ID must exist
assert "PERSON_ID" in surv.columns, "STOP: PERSON_ID missing from survival dataset"
# Guard: index_date must exist
assert "index_date" in surv.columns, "STOP: index_date missing from survival dataset"

app_raw = pd.read_parquet(APPENDICITIS_NARROW)
print(f"[2] Loaded appendicitis NARROW: {len(app_raw):,} rows")

# Guard: PERSON_ID must exist in appendicitis
assert "PERSON_ID" in app_raw.columns, "STOP: PERSON_ID missing from appendicitis file"

# =============================================================================
# COERCE PERSON_ID TO STRING IN BOTH FILES
# =============================================================================

surv["PERSON_ID"] = surv["PERSON_ID"].astype(str).str.strip()
app_raw["PERSON_ID"] = app_raw["PERSON_ID"].astype(str).str.strip()
print(f"[3] PERSON_ID coerced to string in both files")

# =============================================================================
# PARSE DATE FIELDS
# =============================================================================

surv["index_date"] = pd.to_datetime(surv["index_date"])
surv["censor_date"] = pd.to_datetime(surv["censor_date"])
if "clinical_end_date" in surv.columns:
    surv["clinical_end_date"] = pd.to_datetime(surv["clinical_end_date"])
else:
    print("  WARN: clinical_end_date not found in survival dataset; using censor_date only")

# Use CONDITION_START_DATE as primary date field (string → datetime)
app_raw["CONDITION_START_DATE"] = pd.to_datetime(app_raw["CONDITION_START_DATE"], errors="coerce")
# CONDITION_START_DATETIME already datetime64 from parquet; use as fallback
app_raw["CONDITION_START_DATETIME"] = pd.to_datetime(app_raw["CONDITION_START_DATETIME"], errors="coerce")

# Fill missing CONDITION_START_DATE from CONDITION_START_DATETIME
missing_date_mask = app_raw["CONDITION_START_DATE"].isna()
app_raw.loc[missing_date_mask, "CONDITION_START_DATE"] = app_raw.loc[
    missing_date_mask, "CONDITION_START_DATETIME"
].dt.normalize()

n_missing_dates = app_raw["CONDITION_START_DATE"].isna().sum()
print(f"[4] Parsed date fields. Appendicitis rows with no date: {n_missing_dates:,}")

# =============================================================================
# CONFIRM DENOMINATOR N
# =============================================================================

DENOMINATOR_N = len(surv)
print(f"\n[5] Denominator N (run01 survival dataset): {DENOMINATOR_N:,}")
print(f"    ARB (treated=1): {(surv['treated']==1).sum():,}")
print(f"    CCB (treated=0): {(surv['treated']==0).sum():,}")

# =============================================================================
# FILTER APPENDICITIS TO DENOMINATOR PERSONS ONLY
# =============================================================================

denominator_ids = set(surv["PERSON_ID"].unique())
app_in_denom = app_raw[app_raw["PERSON_ID"].isin(denominator_ids)].copy()

n_app_persons_raw = app_raw["PERSON_ID"].nunique()
n_app_persons_denom = app_in_denom["PERSON_ID"].nunique()

print(f"\n[6] Appendicitis records (raw): {len(app_raw):,} across {n_app_persons_raw:,} unique persons")
print(f"    Appendicitis records in denominator: {len(app_in_denom):,} across {n_app_persons_denom:,} persons")

# Drop rows with no usable condition date
app_in_denom = app_in_denom.dropna(subset=["CONDITION_START_DATE"])
print(f"    After dropping records with no date: {len(app_in_denom):,} rows")

# Save raw narrow appendicitis filtered to denominator
out_raw = OUT_DIR / "appendicitis_conditions_run01_raw_NARROW.parquet"
app_in_denom.to_parquet(out_raw, index=False)
print(f"    Saved: {out_raw}")

# =============================================================================
# BUILD PATIENT-LEVEL ENDPOINT
# =============================================================================

print(f"\n[7] Building patient-level endpoint...")

# Get first appendicitis date per person
first_app = (
    app_in_denom
    .groupby("PERSON_ID")["CONDITION_START_DATE"]
    .min()
    .reset_index()
    .rename(columns={"CONDITION_START_DATE": "first_appendicitis_date"})
)

# Merge onto denominator
ep = surv[["PERSON_ID", "index_date", "censor_date", "clinical_end_date", "treated", "iptw"] + 
          [c for c in PS_COVARIATES_FIXED if c in surv.columns] +
          [c for c in surv.columns if c.startswith("yr_")]].copy()
ep = ep.merge(first_app, on="PERSON_ID", how="left")

# Guard: row count must equal denominator N before prevalent exclusion
assert len(ep) == DENOMINATOR_N, (
    f"STOP: endpoint merge changed row count: {len(ep)} != {DENOMINATOR_N}"
)
print(f"    Endpoint rows = {len(ep):,} (matches denominator N ✓)")

# ----- Flags ---------------------------------------------------------------

# any_appendicitis_ever: has at least one appendicitis record in denom
ep["any_appendicitis_ever"] = ep["first_appendicitis_date"].notna().astype(int)

# prevalent_appendicitis_on_or_before_index: first_date <= index_date
ep["prevalent_appendicitis_on_or_before_index"] = (
    ep["first_appendicitis_date"].notna() &
    (ep["first_appendicitis_date"] <= ep["index_date"])
).astype(int)

# first_appendicitis_date_after_index: strictly after index
ep["first_appendicitis_date_after_index"] = ep.apply(
    lambda r: r["first_appendicitis_date"]
    if pd.notna(r["first_appendicitis_date"]) and r["first_appendicitis_date"] > r["index_date"]
    else pd.NaT,
    axis=1,
)

# incident_appendicitis_after_index: has first date strictly after index
ep["incident_appendicitis_after_index"] = (
    ep["first_appendicitis_date_after_index"].notna()
).astype(int)

# appendicitis_before_or_on_censor_date: incident AND within follow-up
ep["appendicitis_before_or_on_censor_date"] = (
    ep["incident_appendicitis_after_index"].eq(1) &
    ep["first_appendicitis_date_after_index"].notna() &
    (ep["first_appendicitis_date_after_index"] <= ep["censor_date"])
).astype(int)

# days_to_appendicitis_after_index (raw, may be after censor)
ep["days_to_appendicitis_after_index"] = (
    ep["first_appendicitis_date_after_index"] - ep["index_date"]
).dt.days

# days_to_appendicitis_censored: days to event if event within follow-up, else to censor
ep["days_to_appendicitis_censored"] = np.where(
    ep["appendicitis_before_or_on_censor_date"] == 1,
    (ep["first_appendicitis_date_after_index"] - ep["index_date"]).dt.days,
    (ep["censor_date"] - ep["index_date"]).dt.days,
)

# Guard: verify binary flags are only 0/1
for flag_col in [
    "any_appendicitis_ever",
    "prevalent_appendicitis_on_or_before_index",
    "incident_appendicitis_after_index",
    "appendicitis_before_or_on_censor_date",
]:
    vals = set(ep[flag_col].unique())
    assert vals.issubset({0, 1}), f"STOP: {flag_col} contains non-binary values: {vals}"
print("    Binary flag checks passed ✓")

# Guard: verify incident events are not counted before/on index
incident_rows = ep[ep["incident_appendicitis_after_index"] == 1]
if len(incident_rows) > 0:
    bad = incident_rows[
        incident_rows["first_appendicitis_date_after_index"] <= incident_rows["index_date"]
    ]
    assert len(bad) == 0, f"STOP: {len(bad)} incident events on or before index_date"
print("    Incident event timing check passed ✓")

# Guard: verify events after censor are not counted as events within follow-up
bad_censor = ep[
    (ep["appendicitis_before_or_on_censor_date"] == 1) &
    (ep["first_appendicitis_date_after_index"] > ep["censor_date"])
]
assert len(bad_censor) == 0, f"STOP: {len(bad_censor)} events counted after censor date"
print("    Censor date boundary check passed ✓")

# Save full endpoint file
out_ep = OUT_DIR / "appendicitis_falsification_endpoint_run01.parquet"
ep.to_parquet(out_ep, index=False)
print(f"    Saved: {out_ep}")

# =============================================================================
# COUNTS SUMMARY
# =============================================================================

n_any = ep["any_appendicitis_ever"].sum()
n_prevalent = ep["prevalent_appendicitis_on_or_before_index"].sum()
n_incident_raw = ep["incident_appendicitis_after_index"].sum()
n_incident_censored = ep["appendicitis_before_or_on_censor_date"].sum()

print("\n" + "=" * 70)
print("COUNTS SUMMARY")
print("=" * 70)
print(f"  Denominator N:                           {DENOMINATOR_N:>8,}")
print(f"  Patients with any appendicitis record:   {n_any:>8,}")
print(f"  Prevalent appendicitis (on/before index):{n_prevalent:>8,}")
print(f"  Incident appendicitis (after index):     {n_incident_raw:>8,}")
print(f"  Incident within follow-up (≤censor):     {n_incident_censored:>8,}")

# By exposure group
n_arb = len(ep[ep["treated"] == 1])
n_ccb = len(ep[ep["treated"] == 0])
n_arb_event = ep.loc[ep["treated"] == 1, "appendicitis_before_or_on_censor_date"].sum()
n_ccb_event = ep.loc[ep["treated"] == 0, "appendicitis_before_or_on_censor_date"].sum()

# Crude incidence rates: events per 100 person-years
# Person-time = days_to_appendicitis_censored / 365.25
ep_analysis_all = ep[ep["prevalent_appendicitis_on_or_before_index"] == 0].copy()
n_analysis = len(ep_analysis_all)
ep_analysis_all["person_years"] = ep_analysis_all["days_to_appendicitis_censored"] / 365.25

py_arb = ep_analysis_all.loc[ep_analysis_all["treated"] == 1, "person_years"].sum()
py_ccb = ep_analysis_all.loc[ep_analysis_all["treated"] == 0, "person_years"].sum()
ev_arb = ep_analysis_all.loc[ep_analysis_all["treated"] == 1, "appendicitis_before_or_on_censor_date"].sum()
ev_ccb = ep_analysis_all.loc[ep_analysis_all["treated"] == 0, "appendicitis_before_or_on_censor_date"].sum()

ir_arb = (ev_arb / py_arb * 1000) if py_arb > 0 else np.nan
ir_ccb = (ev_ccb / py_ccb * 1000) if py_ccb > 0 else np.nan

print(f"\n  --- After prevalent exclusion (analysis N = {n_analysis:,}) ---")
print(f"  ARB arm:  N={ep_analysis_all[ep_analysis_all['treated']==1].shape[0]:,} | events={int(ev_arb)} | person-years={py_arb:,.1f} | IR={ir_arb:.2f}/1000 PY")
print(f"  CCB arm:  N={ep_analysis_all[ep_analysis_all['treated']==0].shape[0]:,} | events={int(ev_ccb)} | person-years={py_ccb:,.1f} | IR={ir_ccb:.2f}/1000 PY")

# =============================================================================
# BUILD ANALYSIS DATASET (EXCLUDE PREVALENT; SURVIVAL FORMAT)
# =============================================================================

# Exclude prevalent appendicitis
ana = ep[ep["prevalent_appendicitis_on_or_before_index"] == 0].copy()
ana["app_event"] = ana["appendicitis_before_or_on_censor_date"].astype(int)
ana["app_time_years"] = ana["days_to_appendicitis_censored"] / 365.25

# Guard: all time values > 0
bad_time = (ana["app_time_years"] <= 0).sum()
if bad_time > 0:
    print(f"  WARN: {bad_time} rows with app_time_years <= 0; clamping to 0.001 for Cox")
    ana.loc[ana["app_time_years"] <= 0, "app_time_years"] = 0.001

# Save analysis dataset
out_ana = OUT_DIR / "appendicitis_falsification_analysis_dataset_run01.parquet"
ana.to_parquet(out_ana, index=False)
print(f"\n  Saved analysis dataset: {out_ana}")
print(f"  Analysis N (after prevalent exclusion): {len(ana):,}")
print(f"  Event N: {int(ana['app_event'].sum())}")

# =============================================================================
# COX MODELS (matching 04_table2_run01_20260531.py conventions)
# =============================================================================

def fit_cox_model(data, time_col, event_col, covariates, weight_col=None, label=""):
    """Fit CoxPH; return dict of results. penalizer=0; retry 0.01 on failure."""
    d = data[[time_col, event_col] + covariates].copy()
    if weight_col:
        d["_wt"] = data[weight_col].values
    d = d.dropna()
    d = d[d[time_col] > 0]
    n_total = len(d)
    n_events = int(d[event_col].sum())

    if n_events < 5:
        print(f"  WARN [{label}]: only {n_events} events — skipping Cox model")
        return dict(
            model=label, hr=np.nan, ci_lo=np.nan, ci_hi=np.nan,
            p=np.nan, n_total=n_total, n_events=n_events,
            converged=False, note="Insufficient events (<5)"
        )

    cph = CoxPHFitter(penalizer=0)
    fit_kwargs = {"duration_col": time_col, "event_col": event_col}
    if weight_col:
        fit_kwargs["weights_col"] = "_wt"
        fit_kwargs["robust"] = True  # sandwich SE for IPTW

    converged = True
    note = ""
    try:
        cph.fit(d, **fit_kwargs)
    except Exception as e1:
        print(f"  [{label}] penalizer=0 failed ({e1}); retrying with penalizer=0.01")
        try:
            cph = CoxPHFitter(penalizer=0.01)
            cph.fit(d, **fit_kwargs)
            converged = True
            note = "penalizer=0.01 used (convergence fallback)"
        except Exception as e2:
            print(f"  [{label}] WARN: Cox fit failed: {e2}")
            return dict(
                model=label, hr=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                p=np.nan, n_total=n_total, n_events=n_events,
                converged=False, note=str(e2)
            )

    hr    = float(np.exp(cph.params_["treated"]))
    ci_lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
    ci_hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
    p_val = float(cph.summary.loc["treated", "p"])

    print(f"  [{label}] HR={hr:.3f} (95%CI {ci_lo:.3f}-{ci_hi:.3f}) p={p_val:.4f} | N={n_total:,} events={n_events}")
    return dict(
        model=label, hr=hr, ci_lo=ci_lo, ci_hi=ci_hi, p=p_val,
        n_total=n_total, n_events=n_events, converged=converged, note=note
    )


print("\n" + "=" * 70)
print("COX MODEL RESULTS — Falsification endpoint: Appendicitis")
print("=" * 70)

year_cols = [c for c in ana.columns if c.startswith("yr_")]
adj_cols = ["treated"] + [c for c in PS_COVARIATES_FIXED if c in ana.columns] + year_cols

# 1. Crude
res_crude = fit_cox_model(ana, "app_time_years", "app_event", ["treated"], label="Crude")

# 2. Covariate-adjusted
res_adj = fit_cox_model(ana, "app_time_years", "app_event", adj_cols, label="Adjusted")

# 3. IPTW Cox (robust SE)
res_iptw = fit_cox_model(
    ana, "app_time_years", "app_event",
    covariates=["treated"], weight_col="iptw", label="IPTW"
)

results = [res_crude, res_adj, res_iptw]

# =============================================================================
# INTERPRET FALSIFICATION RESULT
# =============================================================================

iptw_hr  = res_iptw["hr"]
iptw_lo  = res_iptw["ci_lo"]
iptw_hi  = res_iptw["ci_hi"]
iptw_p   = res_iptw["p"]

if not np.isnan(iptw_p):
    if iptw_p >= 0.05 and not np.isnan(iptw_hr):
        interpretation = (
            f"REASSURINGLY NULL: IPTW HR={iptw_hr:.2f} (95%CI {iptw_lo:.2f}-{iptw_hi:.2f}) p={iptw_p:.4f}. "
            "No significant association between ARB vs CCB initiation and appendicitis. "
            "This supports adequate confounding control in the primary analysis."
        )
    else:
        interpretation = (
            f"CONCERNING: IPTW HR={iptw_hr:.2f} (95%CI {iptw_lo:.2f}-{iptw_hi:.2f}) p={iptw_p:.4f}. "
            "A statistically significant association with appendicitis was detected. "
            "This may indicate residual confounding or chance. Requires further investigation."
        )
else:
    interpretation = "INDETERMINATE: IPTW model did not converge or had insufficient events."

print(f"\nFALSIFICATION INTERPRETATION: {interpretation}")

# =============================================================================
# SAVE RESULTS CSV
# =============================================================================

rows = []
for res in results:
    rows.append({
        "model":        res["model"],
        "endpoint":     "appendicitis",
        "endpoint_role": "falsification_negative_control",
        "n_total":      res["n_total"],
        "n_events":     res["n_events"],
        "hr":           round(res["hr"], 4) if not np.isnan(res["hr"]) else np.nan,
        "ci_lo":        round(res["ci_lo"], 4) if not np.isnan(res["ci_lo"]) else np.nan,
        "ci_hi":        round(res["ci_hi"], 4) if not np.isnan(res["ci_hi"]) else np.nan,
        "p_value":      round(res["p"], 6) if not np.isnan(res["p"]) else np.nan,
        "hr_ci_formatted": (
            f"{res['hr']:.2f} ({res['ci_lo']:.2f}-{res['ci_hi']:.2f})"
            if not np.isnan(res["hr"]) else "n/a"
        ),
        "converged":    res["converged"],
        "note":         res["note"],
    })

df_results = pd.DataFrame(rows)
out_csv = OUT_DIR / "appendicitis_falsification_results_run01.csv"
df_results.to_csv(out_csv, index=False)
print(f"\nSaved results: {out_csv}")
print(df_results.to_string(index=False))

# =============================================================================
# SAVE AUDIT NOTE
# =============================================================================

audit_text = f"""# appendicitis_falsification_audit_note.md

**Generated:** {datetime.today().strftime('%Y-%m-%d')}
**Script:** src/extract_appendicitis_falsification_run01_20260618_airms_cloud_safe.py
**Run ID:** run01_v4_core_design_deathcensor

## Inputs
- Survival dataset: {RUN01_SURVIVAL}
- Appendicitis (NARROW): {APPENDICITIS_NARROW}

## Cohort
- Denominator N (run01): {DENOMINATOR_N:,}
  - ARB (treated=1): {(surv['treated']==1).sum():,}
  - CCB (treated=0): {(surv['treated']==0).sum():,}
- Analysis end date (fixed): {ANALYSIS_END_DATE.date()}

## Appendicitis Endpoint Counts
- Any appendicitis record in denominator: {n_any:,}
- Prevalent (on or before index date): {n_prevalent:,}
- Incident (after index date, any): {n_incident_raw:,}
- Incident within follow-up (≤ censor date): {n_incident_censored:,}
- Analysis N (after prevalent exclusion): {len(ana):,}
- Event N (analysis): {int(ana['app_event'].sum())}

## Incidence Rates (after prevalent exclusion)
- ARB: {int(ev_arb)} events / {py_arb:,.1f} person-years = {ir_arb:.2f} per 1,000 PY
- CCB: {int(ev_ccb)} events / {py_ccb:,.1f} person-years = {ir_ccb:.2f} per 1,000 PY

## Model Specifications
- Crude Cox: "treated" only
- Adjusted Cox: "treated" + PS_COVARIATES_FIXED + year dummies
- IPTW Cox: "treated", weights=iptw (from run01 survival dataset), robust=True (sandwich SE)
- penalizer=0 default; 0.01 fallback on convergence failure

## Results
| Model     | HR   | 95% CI           | p-value  | N      | Events |
|-----------|------|------------------|----------|--------|--------|
| Crude     | {res_crude['hr']:.3f} | {res_crude['ci_lo']:.3f}–{res_crude['ci_hi']:.3f} | {res_crude['p']:.4f} | {res_crude['n_total']:,} | {res_crude['n_events']} |
| Adjusted  | {res_adj['hr']:.3f} | {res_adj['ci_lo']:.3f}–{res_adj['ci_hi']:.3f} | {res_adj['p']:.4f} | {res_adj['n_total']:,} | {res_adj['n_events']} |
| IPTW      | {res_iptw['hr']:.3f} | {res_iptw['ci_lo']:.3f}–{res_iptw['ci_hi']:.3f} | {res_iptw['p']:.4f} | {res_iptw['n_total']:,} | {res_iptw['n_events']} |

## Interpretation
{interpretation}

## Checks Passed
- PERSON_ID present in both files: ✓
- index_date present: ✓
- Endpoint row count = denominator N before prevalent exclusion: ✓
- Binary flags are 0/1 only: ✓
- Incident events are strictly after index_date: ✓
- Events after censor date are not counted as events: ✓
- Model N and event N printed: ✓

## Output Files
- appendicitis_conditions_run01_raw_NARROW.parquet
- appendicitis_falsification_endpoint_run01.parquet
- appendicitis_falsification_analysis_dataset_run01.parquet
- appendicitis_falsification_results_run01.csv
- appendicitis_falsification_audit_note.md
"""

out_audit = OUT_DIR / "appendicitis_falsification_audit_note.md"
out_audit.write_text(audit_text)
print(f"Saved audit note: {out_audit}")

print("\n" + "=" * 70)
print("DONE — Appendicitis falsification analysis complete")
print("=" * 70)
