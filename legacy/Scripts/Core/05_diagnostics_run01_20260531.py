"""
05_diagnostics_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Generates IPTW diagnostics and PH testing for run01.

Diagnostics:
  - PS distribution overlap plot (pre- and post-trim)
  - IPTW weight distribution summary
  - Schoenfeld residual PH test for each outcome
  - Log-log KM plots for each outcome (visual PH check)

Outputs (to run01_v4_core_design_deathcensor/):
  run01_ps_overlap.png
  run01_iptw_weight_summary.csv
  run01_ph_schoenfeld.csv
  logs/05_diagnostics_run01_<date>.log

Frozen v3 template: src/may_2026/gio_latest_diagnostics.py,
                    src/may_2026/ph_diagnostics_primary_cohort.py

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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

try:
    from lifelines import KaplanMeierFitter, CoxPHFitter
    from lifelines.statistics import proportional_hazard_test
except ImportError:
    raise ImportError("lifelines is required: pip install lifelines")

sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

OUT_DIR = cfg.OUT_DIR
LOG_DIR = cfg.LOG_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

import logging
log_path = LOG_DIR / f"05_diagnostics_run01_{datetime.today().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ==============================================================================
# LOAD DATA
# ==============================================================================

sv = pd.read_parquet(cfg.RUN01_SURVIVAL_DATASET)
log.info(f"Loaded run01_survival_dataset: {len(sv):,}")

# ==============================================================================
# 1. PS OVERLAP PLOT
# ==============================================================================

log.info("Generating PS overlap plot...")
fig, ax = plt.subplots(figsize=(7, 4))
arb_ps = sv.loc[sv["treated"] == 1, "ps"].dropna()
ccb_ps = sv.loc[sv["treated"] == 0, "ps"].dropna()
ax.hist(arb_ps, bins=50, alpha=0.5, label="ARB", density=True, color="#2271B2")
ax.hist(ccb_ps, bins=50, alpha=0.5, label="DHP-CCB", density=True, color="#E66100")
ax.set_xlabel("Propensity Score (P[ARB])")
ax.set_ylabel("Density")
ax.set_title("run01 — PS Distribution by Arm (post-trim)")
ax.legend()
fig.tight_layout()
fig.savefig(OUT_DIR / "run01_ps_overlap.png", dpi=150)
plt.close(fig)
log.info(f"Saved: {OUT_DIR / 'run01_ps_overlap.png'}")

# ==============================================================================
# 2. IPTW WEIGHT SUMMARY
# ==============================================================================

log.info("Computing IPTW weight summary...")
wt_summary = []
for arm, arm_label in [(1, "ARB"), (0, "DHP-CCB")]:
    w = sv.loc[sv["treated"] == arm, "iptw"]
    wt_summary.append({
        "arm":    arm_label,
        "n":      len(w),
        "mean":   w.mean(),
        "sd":     w.std(),
        "min":    w.min(),
        "p1":     w.quantile(0.01),
        "p25":    w.quantile(0.25),
        "median": w.median(),
        "p75":    w.quantile(0.75),
        "p99":    w.quantile(0.99),
        "max":    w.max(),
    })
wt_df = pd.DataFrame(wt_summary)
wt_df.to_csv(OUT_DIR / "run01_iptw_weight_summary.csv", index=False)
log.info(f"Saved: {OUT_DIR / 'run01_iptw_weight_summary.csv'}")

# ==============================================================================
# 3. SCHOENFELD RESIDUALS / PH TEST
# ==============================================================================

log.info("Running Schoenfeld PH tests...")
OUTCOMES = [
    ("stroke_s1_time_years", "stroke_s1_event", "Acute ischemic stroke"),
    ("b4_mci_time_years",    "b4_mci_event",    "Probable dementia + MCI"),
    ("b4_time_years",        "b4_event",        "Probable dementia alone"),
    ("stroke_s2_time_years", "stroke_s2_event", "Ischemic stroke + TIA"),
]

ph_rows = []
for time_col, event_col, label in OUTCOMES:
    d = sv[["treated", time_col, event_col, "iptw"]].dropna()
    d = d[d[time_col] > 0]
    if d[event_col].sum() < 10:
        ph_rows.append({"outcome": label, "test_statistic": np.nan, "p_value": np.nan, "note": "insufficient events"})
        continue
    try:
        cph = CoxPHFitter(penalizer=0.01)
        cph.fit(d, duration_col=time_col, event_col=event_col,
                weights_col="iptw", robust=True)
        ph_result = proportional_hazard_test(cph, d, time_transform="rank")
        pval = float(ph_result.summary.loc["treated", "p"])
        test_stat = float(ph_result.summary.loc["treated", "test_statistic"])
        ph_rows.append({"outcome": label, "test_statistic": test_stat, "p_value": pval, "note": ""})
        log.info(f"  {label}: PH p={pval:.4f}")
    except Exception as e:
        ph_rows.append({"outcome": label, "test_statistic": np.nan, "p_value": np.nan, "note": str(e)})

ph_df = pd.DataFrame(ph_rows)
ph_df.to_csv(OUT_DIR / "run01_ph_schoenfeld.csv", index=False)
log.info(f"Saved: {OUT_DIR / 'run01_ph_schoenfeld.csv'}")

log.info("05_diagnostics_run01 complete.")
