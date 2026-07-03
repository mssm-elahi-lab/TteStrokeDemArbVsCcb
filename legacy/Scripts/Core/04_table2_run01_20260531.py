"""
04_table2_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Generates Table 2 (IPTW-Cox hazard ratios) for the four outcomes.

CORRECTIONS (2026-05-31):
  [C2/C3] Outcome column names now match corrected 02 script:
            b4_time_years/b4_event             <- probable dementia alone (SECONDARY)
            b4_mci_time_years/b4_mci_event     <- prob. dementia + MCI (PRIMARY)
            stroke_s1_time_years/stroke_s1_event <- AIS (PRIMARY)
            stroke_s2_time_years/stroke_s2_event <- AIS+TIA (SECONDARY)
  [C8]  Table 2 includes outcome_role, outcome_order, crude/adj/IPTW HR,
        Bonferroni and BH-FDR p-values across 2 PRIMARY outcomes only.
        Secondary outcomes show raw p-values labeled secondary.
  [C9]  penalizer=0 by default; increases to penalizer=0.01 only on convergence
        failure, with logged warning.

Outputs:
  run01_table2_primary_ms_ready.csv
  run01_table2_audit_note.txt

Frozen v3 template: src/may_2026/table2_corrected_outcome_buckets_20260513.py

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
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

try:
    from lifelines import CoxPHFitter
    from lifelines.utils import concordance_index
except ImportError:
    raise ImportError("lifelines is required: pip install lifelines")

sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

OUT_DIR = cfg.OUT_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# LOAD DATA
# ==============================================================================

sv = pd.read_parquet(cfg.RUN01_SURVIVAL_DATASET)
print(f"Loaded run01_survival_dataset: {len(sv):,} rows")

# ==============================================================================
# OUTCOME DEFINITIONS
# ==============================================================================

# [C2/C3 CORRECTED] outcome columns use run01 naming convention from 02 script
# Primary outcomes first; secondary second (order matches cfg.OUTCOME_ORDER)
OUTCOMES = [
    # (internal_name, time_col,               event_col,           role,       order, label)
    ("stroke_s1", "stroke_s1_time_years", "stroke_s1_event",  "primary",   1, "Acute ischemic stroke"),
    ("b4_mci",    "b4_mci_time_years",    "b4_mci_event",     "primary",   2, "Probable dementia + mild cognitive impairment"),
    ("b4",        "b4_time_years",         "b4_event",         "secondary", 3, "Probable dementia alone"),
    ("stroke_s2", "stroke_s2_time_years", "stroke_s2_event",  "secondary", 4, "Ischemic stroke + transient ischemic attack"),
]

PS_ADJ_COLS = [c for c in cfg.PS_COVARIATES_FIXED if c in sv.columns]
year_cols = [c for c in sv.columns if c.startswith("yr_")]

# ==============================================================================
# MODEL FITTING
# ==============================================================================

def fit_cox(data, time_col, event_col, covariates=None, weight_col=None):
    """Fit Cox model; return (hr, ci_lo, ci_hi, p, n_events, n_total)."""
    d = data[[time_col, event_col] + (covariates or [])].copy()
    if weight_col:
        d["_wt"] = data[weight_col].values
    d = d.dropna()
    d = d[d[time_col] > 0]
    n_total  = len(d)
    n_events = int(d[event_col].sum())
    if n_events < 5:
        return np.nan, np.nan, np.nan, np.nan, n_events, n_total

    # [C9] No penalizer by default; add only if convergence fails.
    cph = CoxPHFitter(penalizer=0)
    fit_kwargs = {"duration_col": time_col, "event_col": event_col}
    if weight_col:
        fit_kwargs["weights_col"] = "_wt"
        fit_kwargs["robust"] = True  # sandwich SE for IPTW

    try:
        cph.fit(d, **fit_kwargs)
        hr    = float(np.exp(cph.params_["treated"]))
        ci_lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        ci_hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        p_val = float(cph.summary.loc["treated", "p"])
        return hr, ci_lo, ci_hi, p_val, n_events, n_total
    except Exception:
        # Retry once with small penalizer if convergence failed
        try:
            print(f"  Retrying {event_col} with penalizer=0.01 (convergence fallback)")
            cph2 = CoxPHFitter(penalizer=0.01)
            cph2.fit(d, **fit_kwargs)
            hr    = float(np.exp(cph2.params_["treated"]))
            ci_lo = float(np.exp(cph2.confidence_intervals_.loc["treated", "95% lower-bound"]))
            ci_hi = float(np.exp(cph2.confidence_intervals_.loc["treated", "95% upper-bound"]))
            p_val = float(cph2.summary.loc["treated", "p"])
            return hr, ci_lo, ci_hi, p_val, n_events, n_total
        except Exception as e:
            print(f"  Warning: Cox fit failed for {event_col}: {e}")
            return np.nan, np.nan, np.nan, np.nan, n_events, n_total


rows = []
for name, time_col, event_col, role, order, label in OUTCOMES:
    if time_col not in sv.columns or event_col not in sv.columns:
        print(f"  Skipping {label} -- columns not found in survival dataset")
        continue

    n_events_arb = int(sv.loc[sv["treated"] == 1, event_col].sum())
    n_events_ccb = int(sv.loc[sv["treated"] == 0, event_col].sum())

    # Crude
    hr_cr, lo_cr, hi_cr, p_cr, _, _ = fit_cox(sv, time_col, event_col, covariates=["treated"])

    # Adjusted
    adj_cols = ["treated"] + PS_ADJ_COLS + year_cols
    adj_cols = [c for c in adj_cols if c in sv.columns]
    hr_adj, lo_adj, hi_adj, p_adj, _, _ = fit_cox(sv, time_col, event_col, covariates=adj_cols)

    # IPTW
    hr_iptw, lo_iptw, hi_iptw, p_iptw, _, _ = fit_cox(
        sv, time_col, event_col, covariates=["treated"], weight_col="iptw"
    )

    def fmt_hr(hr, lo, hi):
        if any(np.isnan(x) for x in [hr, lo, hi]):
            return "n/a"
        return f"{hr:.2f} ({lo:.2f}-{hi:.2f})"

    rows.append({
        "outcome":          name,
        "outcome_role":     role,
        "outcome_order":    order,
        "Outcome":          label,
        "N_ARB_events":     n_events_arb,
        "N_CCB_events":     n_events_ccb,
        "crude_hr_ci":      fmt_hr(hr_cr,   lo_cr,   hi_cr),
        "crude_p_raw":      p_cr   if not np.isnan(p_cr)   else np.nan,
        "crude_p":          round(p_cr,   4) if not np.isnan(p_cr)   else np.nan,
        "adj_hr_ci":        fmt_hr(hr_adj,  lo_adj,  hi_adj),
        "adj_p_raw":        p_adj  if not np.isnan(p_adj)  else np.nan,
        "adj_p":            round(p_adj,  4) if not np.isnan(p_adj)  else np.nan,
        "iptw_hr_ci":       fmt_hr(hr_iptw, lo_iptw, hi_iptw),
        "iptw_p_raw":       p_iptw if not np.isnan(p_iptw) else np.nan,
        "iptw_p":           round(p_iptw, 4) if not np.isnan(p_iptw) else np.nan,
    })
    print(f"  [{role}] {label}: ARB={n_events_arb} CCB={n_events_ccb} | IPTW HR={fmt_hr(hr_iptw, lo_iptw, hi_iptw)} p={p_iptw:.4f}")

table2 = pd.DataFrame(rows)

# Constants required for multiple testing block — defined here before use
PRIMARY_OUTCOMES   = cfg.PRIMARY_OUTCOMES
SECONDARY_OUTCOMES = cfg.SECONDARY_OUTCOMES
MT_BONFERRONI_K    = cfg.MT_BONFERRONI_K

# [C8] Multiple testing correction across PRIMARY outcomes only
# Use raw (unrounded) IPTW p-values to avoid rounding artifacts
primary_mask = table2["outcome_role"] == "primary"
primary_ps = table2.loc[primary_mask, "iptw_p_raw"].values.astype(float)

# Bonferroni
table2["primary_family_p_bonferroni"] = np.nan
if not any(np.isnan(primary_ps)):
    bonf = np.minimum(primary_ps * MT_BONFERRONI_K, 1.0)
    table2.loc[primary_mask, "primary_family_p_bonferroni"] = bonf

# BH-FDR — robust to scipy version
table2["primary_family_p_bh_fdr"] = np.nan
if not any(np.isnan(primary_ps)) and len(primary_ps) > 0:
    try:
        from scipy.stats import false_discovery_control
        bh = false_discovery_control(primary_ps, method="bh")
        table2.loc[primary_mask, "primary_family_p_bh_fdr"] = bh
    except (ImportError, AttributeError, TypeError):
        # Correct monotonic step-up BH adjustment (Benjamini & Hochberg 1995)
        n_tests = len(primary_ps)
        order   = np.argsort(primary_ps)
        bh_adj  = np.empty(n_tests)
        # Step 1: p_i * n / rank_i (rank 1 = smallest p)
        bh_adj[order] = primary_ps[order] * n_tests / np.arange(1, n_tests + 1)
        # Step 2: enforce monotone non-decreasing from the smallest p upward
        for i in range(n_tests - 2, -1, -1):
            bh_adj[order[i]] = min(bh_adj[order[i]], bh_adj[order[i + 1]])
        bh_adj = np.minimum(bh_adj, 1.0)
        table2.loc[primary_mask, "primary_family_p_bh_fdr"] = bh_adj

# Significance flags
table2["sig_bonferroni"] = np.where(
    primary_mask,
    table2["primary_family_p_bonferroni"].lt(0.05).map({True: "*", False: ""}),
    "[secondary]"
)
table2["sig_bh_fdr"] = np.where(
    primary_mask,
    table2["primary_family_p_bh_fdr"].lt(0.05).map({True: "*", False: ""}),
    "[secondary]"
)

out_csv = OUT_DIR / "run01_table2_primary_ms_ready.csv"
table2.to_csv(out_csv, index=False)
print(f"Saved: {out_csv}")

print("\n=== TABLE 2 ===")
print(table2.to_string(index=False))

# Audit note
from datetime import datetime
audit_note = (
    f"run01_table2_primary_ms_ready.csv\n"
    f"Generated: {datetime.today().strftime('%Y-%m-%d')}\n"
    f"Source script: 04_table2_run01_20260531.py\n"
    f"Source data: {cfg.RUN01_SURVIVAL_DATASET}\n"
    f"Model: IPTW Cox (stabilized ATE, 1st-99th pct trim, robust SE)\n"
    f"Multiple testing: Bonferroni and BH-FDR across 2 PRIMARY outcomes only.\n"
    f"Primary outcomes: {cfg.PRIMARY_OUTCOMES}\n"
    f"Secondary outcomes: {cfg.SECONDARY_OUTCOMES}\n"
    f"Cox penalizer: 0 (default); see logs if convergence fallback used.\n"
    f"DO NOT replace v3 frozen Table 2 with this file until run01 is reviewed.\n"
)
(OUT_DIR / "run01_table2_audit_note.txt").write_text(audit_note)
print("04_table2_run01 complete.")
