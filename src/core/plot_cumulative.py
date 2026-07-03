"""Ported from Scripts/Core/07_plot_cumulative_event_run01_20260531.py.

Generates cumulative event plots (1 - Kaplan-Meier survival curves) for
stroke S1 (primary) and dementia B4_MCI (primary cognitive), plus an
unweighted supplemental check for stroke S1.

Uses IPTW-weighted Nelson-Aalen estimator for the final manuscript figure
and unweighted KM curves as a supplemental check.
"""

from __future__ import annotations

import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from lifelines import KaplanMeierFitter

from src.config import Config

warnings.filterwarnings("ignore")

COLORS = {
    "ARB": "#2271B2",
    "DHP-CCB": "#E66100",
}


def _plot_cumulative_event(
    sv_df: pd.DataFrame,
    time_col: str,
    event_col: str,
    title: str,
    output_core,
    out_stem: str,
    weighted: bool = True,
) -> None:
    """Plot 1 - KM for ARB vs DHP-CCB, optionally IPTW-weighted."""
    fig, ax = plt.subplots(figsize=(7.5, 4.5))

    for arm, arm_label in [(1, "ARB"), (0, "DHP-CCB")]:
        d = sv_df[sv_df["treated"] == arm][[time_col, event_col, "iptw"]].dropna()
        d = d[d[time_col] > 0]

        kmf = KaplanMeierFitter()
        if weighted:
            kmf.fit(d[time_col], event_observed=d[event_col], weights=d["iptw"], label=arm_label)
        else:
            kmf.fit(d[time_col], event_observed=d[event_col], label=arm_label)

        t = kmf.survival_function_.index
        cum_event = 1.0 - kmf.survival_function_[arm_label]
        ci_lower = 1.0 - kmf.confidence_interval_[f"{arm_label}_upper_0.95"]
        ci_upper = 1.0 - kmf.confidence_interval_[f"{arm_label}_lower_0.95"]

        color = COLORS[arm_label]
        ax.step(t, cum_event * 100, where="post", color=color, linewidth=2, label=arm_label)
        ax.fill_between(t, ci_lower * 100, ci_upper * 100, step="post", alpha=0.15, color=color)

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
        p = output_core / f"{out_stem}{ext}"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        print(f"Saved: {p}")
    plt.close(fig)


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)

    sv = pd.read_parquet(output_core / "survival_dataset.parquet")
    print(f"Loaded survival_dataset: {len(sv):,}")

    _plot_cumulative_event(
        sv,
        "stroke_s1_time_years",
        "stroke_s1_event",
        title="Acute Ischemic Stroke\nARB vs DHP-CCB",
        output_core=output_core,
        out_stem="cumulative_event_stroke_s1",
        weighted=True,
    )

    _plot_cumulative_event(
        sv,
        "b4_mci_time_years",
        "b4_mci_event",
        title="Probable Dementia + Mild Cognitive Impairment\nARB vs DHP-CCB",
        output_core=output_core,
        out_stem="cumulative_event_b4_mci",
        weighted=True,
    )

    _plot_cumulative_event(
        sv,
        "stroke_s1_time_years",
        "stroke_s1_event",
        title="Acute Ischemic Stroke — Unweighted KM\nARB vs DHP-CCB (supplemental)",
        output_core=output_core,
        out_stem="cumulative_event_stroke_s1_supplemental_unweighted",
        weighted=False,
    )

    print("plot_cumulative complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
