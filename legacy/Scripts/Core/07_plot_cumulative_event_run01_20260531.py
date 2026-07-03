"""
07_plot_cumulative_event_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Generates cumulative event plots (1 - Kaplan-Meier survival curves)
for the four primary outcomes: stroke S1 (primary), dementia B4_MCI
(primary cognitive), dementia B4 alone (secondary cognitive), stroke S2 (supplemental).

Uses IPTW-weighted Nelson-Aalen estimator for the final manuscript figure
and unweighted KM curves as a supplemental check.

Outputs:
  run01_cumulative_event_stroke_s1_ms_ready.png
  run01_cumulative_event_b4_mci_ms_ready.png
  run01_cumulative_event_stroke_s1_supplemental_unweighted.png
  (plus PDF versions for journal submission)

Frozen v3 template: src/may_2026/cumulative_event_plots_20260525.py

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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

try:
    from lifelines import KaplanMeierFitter
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
print(f"Loaded run01_survival_dataset: {len(sv):,}")

# ==============================================================================
# HELPER: CUMULATIVE EVENT PLOT
# ==============================================================================

COLORS = {
    "ARB":     "#2271B2",
    "DHP-CCB": "#E66100",
}

def plot_cumulative_event(sv_df, time_col, event_col, title, out_stem, weighted=True):
    """Plot 1 - KM for ARB vs DHP-CCB, optionally IPTW-weighted."""
    fig, ax = plt.subplots(figsize=(7.5, 4.5))

    for arm, arm_label in [(1, "ARB"), (0, "DHP-CCB")]:
        d = sv_df[sv_df["treated"] == arm][[time_col, event_col, "iptw"]].dropna()
        d = d[d[time_col] > 0]

        kmf = KaplanMeierFitter()
        if weighted:
            kmf.fit(
                d[time_col],
                event_observed=d[event_col],
                weights=d["iptw"],
                label=arm_label,
            )
        else:
            kmf.fit(d[time_col], event_observed=d[event_col], label=arm_label)

        t = kmf.survival_function_.index
        cum_event = 1.0 - kmf.survival_function_[arm_label]
        ci_lower = 1.0 - kmf.confidence_interval_[f"{arm_label}_upper_0.95"]
        ci_upper = 1.0 - kmf.confidence_interval_[f"{arm_label}_lower_0.95"]

        color = COLORS[arm_label]
        ax.step(t, cum_event * 100, where="post", color=color, linewidth=2, label=arm_label)
        ax.fill_between(t, ci_lower * 100, ci_upper * 100,
                        step="post", alpha=0.15, color=color)

    ax.set_xlabel("Years from index date", fontsize=10)
    ax.set_ylabel("Cumulative event probability (%)", fontsize=10)
    wt_label = "IPTW-weighted" if weighted else "Unweighted (KM)"
    ax.set_title(f"{title}\n({wt_label})", fontsize=10, fontweight="bold")
    ax.legend(title="Treatment", frameon=False)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    for ext in [".png", ".pdf"]:
        p = OUT_DIR / f"{out_stem}{ext}"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        print(f"Saved: {p}")
    plt.close(fig)


# ==============================================================================
# GENERATE PLOTS
# ==============================================================================

plot_cumulative_event(
    sv, "stroke_s1_time_years", "stroke_s1_event",
    title="Acute Ischemic Stroke\nARB vs DHP-CCB (run01)",
    out_stem="run01_cumulative_event_stroke_s1_ms_ready",
    weighted=True,
)

plot_cumulative_event(
    sv, "b4_mci_time_years", "b4_mci_event",
    title="Probable Dementia + Mild Cognitive Impairment\nARB vs DHP-CCB (run01)",
    out_stem="run01_cumulative_event_b4_mci_ms_ready",
    weighted=True,
)

# Supplemental: unweighted
plot_cumulative_event(
    sv, "stroke_s1_time_years", "stroke_s1_event",
    title="Acute Ischemic Stroke — Unweighted KM\nARB vs DHP-CCB (run01 supplemental)",
    out_stem="run01_cumulative_event_stroke_s1_supplemental_unweighted",
    weighted=False,
)

print("07_plot_cumulative_event_run01 complete.")
