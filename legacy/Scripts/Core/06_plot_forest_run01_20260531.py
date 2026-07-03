"""
06_plot_forest_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Generates the primary forest plot for IPTW hazard ratios across all four outcomes.
Uses manuscript-friendly outcome labels (no ICD codes).

Output:
  run01_forest_plot_ms_ready.png
  run01_forest_plot_ms_ready.pdf  (vector for journal submission)

Frozen v3 template: src/may_2026/plot_updated_main_forest_plot.py

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
import matplotlib.patches as mpatches

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

OUT_DIR = cfg.OUT_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# LOAD TABLE 2
# ==============================================================================

table2_path = OUT_DIR / "run01_table2_primary_ms_ready.csv"
if not table2_path.exists():
    raise FileNotFoundError(f"Table 2 not found at {table2_path}. Run 04_table2_run01 first.")

t2 = pd.read_csv(table2_path)
print(f"Loaded Table 2: {len(t2)} rows")

# ==============================================================================
# PARSE HRs FROM TABLE 2
# ==============================================================================

def parse_hr_ci(cell):
    """Parse 'HR (lo–hi)' string into (hr, lo, hi) floats."""
    if pd.isna(cell) or cell == "—":
        return np.nan, np.nan, np.nan
    try:
        hr_str, ci_str = cell.split(" (")
        ci_str = ci_str.rstrip(")")
        lo_str, hi_str = ci_str.split("-")
        return float(hr_str), float(lo_str), float(hi_str)
    except Exception:
        return np.nan, np.nan, np.nan

outcomes  = list(t2["Outcome"])
hrs, los, his = zip(*[parse_hr_ci(v) for v in t2["iptw_hr_ci"]])

# ==============================================================================
# FOREST PLOT
# ==============================================================================

fig, ax = plt.subplots(figsize=(8, max(4, len(outcomes) * 1.2 + 1.5)))

y_positions = list(range(len(outcomes), 0, -1))
colors = ["#2271B2"] * len(outcomes)

for i, (y, hr, lo, hi, label) in enumerate(zip(y_positions, hrs, los, his, outcomes)):
    if np.isnan(hr):
        ax.text(0.5, y, "—", va="center", ha="center", fontsize=9, color="gray")
        ax.text(-0.5, y, label, va="center", ha="right", fontsize=9)
        continue
    ax.plot([lo, hi], [y, y], color=colors[i], linewidth=2, solid_capstyle="round")
    ax.plot(hr, y, "o", color=colors[i], markersize=8, zorder=5)
    ax.text(-0.5, y, label, va="center", ha="right", fontsize=9)
    ax.text(hi + 0.08, y, f"{hr:.2f} ({lo:.2f}–{hi:.2f})", va="center", ha="left", fontsize=8)

ax.axvline(x=1.0, color="black", linestyle="--", linewidth=1, alpha=0.7)
ax.set_yticks(y_positions)
ax.set_yticklabels([""] * len(outcomes))
ax.set_xlabel("Hazard Ratio (ARB vs DHP-CCB)\nIPTW Cox, stabilized ATE weights", fontsize=10)
ax.set_xlim(-0.5, max([h for h in his if not np.isnan(h)] + [2.0]) + 1.0)
ax.set_ylim(0.2, len(outcomes) + 0.8)

ax.text(0.02, 0.98, "ARB favored", transform=ax.transAxes, fontsize=7.5,
        va="top", ha="left", color="#2271B2", style="italic")
ax.text(0.98, 0.98, "DHP-CCB favored", transform=ax.transAxes, fontsize=7.5,
        va="top", ha="right", color="#E66100", style="italic")

ax.set_title("run01_v4_core_design_deathcensor\nIPTW Hazard Ratios — ARB vs DHP-CCB",
             fontsize=10, fontweight="bold")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_visible(False)

fig.tight_layout()
for ext in [".png", ".pdf"]:
    out_path = OUT_DIR / f"run01_forest_plot_ms_ready{ext}"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

plt.close(fig)
print("06_plot_forest_run01 complete.")
