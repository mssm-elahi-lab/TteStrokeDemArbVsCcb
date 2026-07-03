"""
03_table1_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Generates Table 1 (baseline characteristics before and after IPTW)
for the run01 primary Cohort B (≥1-year follow-up, PS-trimmed ARB vs CCB).

Mirrors logic in frozen v3: src/may_2026/table1_cohortB_primary.py
Output uses Cohort B definition from run01_survival_dataset.parquet.

Outputs:
  run01_table1_primary_ms_ready.csv
  run01_table1_audit_note.txt

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
from scipy import stats

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

OUT_DIR = cfg.OUT_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# LOAD DATA
# ==============================================================================

sv = pd.read_parquet(cfg.RUN01_SURVIVAL_DATASET)
print(f"Loaded run01_survival_dataset: {len(sv):,} rows")
print(f"ARB: {sv['treated'].sum():,}   CCB: {(sv['treated']==0).sum():,}")

# ==============================================================================
# TABLE 1 HELPER
# ==============================================================================

def smd(a, b):
    pooled_sd = np.sqrt((np.nanstd(a, ddof=1)**2 + np.nanstd(b, ddof=1)**2) / 2)
    return (np.nanmean(a) - np.nanmean(b)) / pooled_sd if pooled_sd > 0 else np.nan

def smd_weighted(col, group, weight_col):
    a = sv.loc[sv["treated"] == 1, col]
    b = sv.loc[sv["treated"] == 0, col]
    wa = sv.loc[sv["treated"] == 1, weight_col]
    wb = sv.loc[sv["treated"] == 0, weight_col]
    pooled_sd = np.sqrt((np.nanstd(a, ddof=1)**2 + np.nanstd(b, ddof=1)**2) / 2)
    if pooled_sd == 0 or np.isnan(pooled_sd):
        return np.nan
    wa_m = np.average(a.dropna(), weights=wa.loc[a.notna()])
    wb_m = np.average(b.dropna(), weights=wb.loc[b.notna()])
    return (wa_m - wb_m) / pooled_sd

rows = []

def add_row(label, arb_val, ccb_val, smd_pre, arb_w, ccb_w, smd_post):
    rows.append({
        "Variable":           label,
        "ARB (unweighted)":   arb_val,
        "CCB (unweighted)":   ccb_val,
        "SMD (unweighted)":   round(smd_pre, 3)  if not np.isnan(smd_pre)  else "",
        "ARB (IPTW)":         arb_w,
        "CCB (IPTW)":         ccb_w,
        "SMD (IPTW)":         round(smd_post, 3) if not np.isnan(smd_post) else "",
    })

arb = sv[sv["treated"] == 1]
ccb = sv[sv["treated"] == 0]

# N
add_row("N", f"{len(arb):,}", f"{len(ccb):,}", np.nan, "", "", np.nan)

# Age
add_row(
    "Age at index (mean ± SD)",
    f"{arb['age_at_index'].mean():.1f} ± {arb['age_at_index'].std():.1f}",
    f"{ccb['age_at_index'].mean():.1f} ± {ccb['age_at_index'].std():.1f}",
    smd(arb["age_at_index"], ccb["age_at_index"]),
    f"{np.average(arb['age_at_index'].dropna(), weights=arb['iptw'].loc[arb['age_at_index'].notna()]):.1f}",
    f"{np.average(ccb['age_at_index'].dropna(), weights=ccb['iptw'].loc[ccb['age_at_index'].notna()]):.1f}",
    smd_weighted("age_at_index", None, "iptw"),
)

# Binary covariates
bin_vars = [
    ("Female (%)",       "female"),
    ("Black/AA (%)",     "race_black_r"),
    ("Asian (%)",        "race_asian_r"),
    ("Other race (%)",   "race_other_r"),
    ("Hispanic (%)",     "hispanic"),
    ("Diabetes (%)",     "bl_diabetes"),
    ("CKD (%)",          "bl_ckd"),
    ("Heart failure (%)", "bl_heart_failure"),
    ("CAD/MI (%)",       "bl_cad_mi"),
    ("AFib (%)",         "bl_afib"),
    ("PAD (%)",          "bl_pad"),
    ("TIA (%)",          "bl_tia"),
]
for label, col in bin_vars:
    if col not in sv.columns:
        continue
    a_pct = 100 * arb[col].mean()
    b_pct = 100 * ccb[col].mean()
    s_pre = smd(arb[col], ccb[col])
    aw = np.average(arb[col].dropna(), weights=arb["iptw"].loc[arb[col].notna()])
    bw = np.average(ccb[col].dropna(), weights=ccb["iptw"].loc[ccb[col].notna()])
    s_post = smd_weighted(col, None, "iptw")
    add_row(label, f"{a_pct:.1f}%", f"{b_pct:.1f}%", s_pre, f"{100*aw:.1f}%", f"{100*bw:.1f}%", s_post)

table1 = pd.DataFrame(rows)
out_csv = OUT_DIR / "run01_table1_primary_ms_ready.csv"
table1.to_csv(out_csv, index=False)
print(f"Saved: {out_csv}")

# Audit note
audit_note = (
    f"run01_table1_primary_ms_ready.csv\n"
    f"Generated: {datetime.today().strftime('%Y-%m-%d')}\n"
    f"Source script: 03_table1_run01_20260531.py\n"
    f"Source data: {cfg.RUN01_SURVIVAL_DATASET}\n"
    f"Cohort: run01 Cohort B — ARB vs DHP-CCB (≥1yr FU, PS 1st–99th pct trim)\n"
    f"N ARB: {len(arb):,}   N CCB: {len(ccb):,}\n"
    f"IPTW: stabilized ATE, winsorized at 1st–99th pct\n"
    f"PS covariates: {cfg.PS_COVARIATES_FIXED} + index_year dummies\n"
    f"bl_cva: EXCLUDED from PS model\n"
    f"Race Black/AA: via CDC concept 38003599\n"
    f"This file is run01 (v4 data + first-line washout + death censoring).\n"
    f"DO NOT replace v3 frozen outputs with this file until run01 is reviewed.\n"
)
audit_path = OUT_DIR / "run01_table1_audit_note.txt"
audit_path.write_text(audit_note)
print(f"Saved: {audit_path}")
print("03_table1_run01 complete.")
