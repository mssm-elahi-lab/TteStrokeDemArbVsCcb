"""Ported from Scripts/Core/06_plot_forest_run01_20260531.py.

Generates the primary forest plot for IPTW hazard ratios across all four
outcomes. Uses manuscript-friendly outcome labels (no ICD codes).
"""

from __future__ import annotations

import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.config import Config

warnings.filterwarnings("ignore")


def _parse_hr_ci(cell) -> tuple[float, float, float] | tuple[None, None, None]:
    """Parse 'HR (lo-hi)' string into (hr, lo, hi) floats."""
    if pd.isna(cell) or cell == "—":
        return np.nan, np.nan, np.nan
    try:
        hr_str, ci_str = cell.split(" (")
        ci_str = ci_str.rstrip(")")
        lo_str, hi_str = ci_str.split("-")
        return float(hr_str), float(lo_str), float(hi_str)
    except Exception:
        return np.nan, np.nan, np.nan


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)

    table2_path = output_core / "hazard_ratios.csv"
    if not table2_path.exists():
        raise FileNotFoundError(f"Hazard ratios not found at {table2_path}. Run table2 first.")

    t2 = pd.read_csv(table2_path)
    print(f"Loaded hazard ratios: {len(t2)} rows")

    outcomes = list(t2["Outcome"])
    hrs, los, his = zip(*[_parse_hr_ci(v) for v in t2["iptw_hr_ci"]])

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

    ax.text(
        0.02, 0.98, "ARB favored", transform=ax.transAxes, fontsize=7.5, va="top", ha="left",
        color="#2271B2", style="italic",
    )
    ax.text(
        0.98, 0.98, "DHP-CCB favored", transform=ax.transAxes, fontsize=7.5, va="top", ha="right",
        color="#E66100", style="italic",
    )

    ax.set_title("IPTW Hazard Ratios — ARB vs DHP-CCB", fontsize=10, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)

    fig.tight_layout()
    for ext in [".png", ".pdf"]:
        out_path = output_core / f"forest_plot{ext}"
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {out_path}")

    plt.close(fig)
    print("plot_forest complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
