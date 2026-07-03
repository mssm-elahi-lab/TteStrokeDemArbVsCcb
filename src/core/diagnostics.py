"""Ported from Scripts/Core/05_diagnostics_run01_20260531.py.

Generates IPTW diagnostics and PH testing:
  - PS distribution overlap plot (post-trim)
  - IPTW weight distribution summary
  - Schoenfeld residual PH test for each outcome
"""

from __future__ import annotations

import logging
import sys
import warnings
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.statistics import proportional_hazard_test

from src.config import Config

warnings.filterwarnings("ignore")


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)
    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"diagnostics_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = logging.getLogger(f"{__name__}.run")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))

    sv = pd.read_parquet(output_core / "survival_dataset.parquet")
    logger.info(f"Loaded survival_dataset: {len(sv):,}")

    # ==========================================================================
    # 1. PS OVERLAP PLOT
    # ==========================================================================

    logger.info("Generating PS overlap plot...")
    fig, ax = plt.subplots(figsize=(7, 4))
    arb_ps = sv.loc[sv["treated"] == 1, "ps"].dropna()
    ccb_ps = sv.loc[sv["treated"] == 0, "ps"].dropna()
    ax.hist(arb_ps, bins=50, alpha=0.5, label="ARB", density=True, color="#2271B2")
    ax.hist(ccb_ps, bins=50, alpha=0.5, label="DHP-CCB", density=True, color="#E66100")
    ax.set_xlabel("Propensity Score (P[ARB])")
    ax.set_ylabel("Density")
    ax.set_title("PS Distribution by Arm (post-trim)")
    ax.legend()
    fig.tight_layout()
    ps_overlap_path = output_core / "ps_overlap.png"
    fig.savefig(ps_overlap_path, dpi=150)
    plt.close(fig)
    logger.info(f"Saved: {ps_overlap_path}")

    # ==========================================================================
    # 2. IPTW WEIGHT SUMMARY
    # ==========================================================================

    logger.info("Computing IPTW weight summary...")
    wt_summary = []
    for arm, arm_label in [(1, "ARB"), (0, "DHP-CCB")]:
        w = sv.loc[sv["treated"] == arm, "iptw"]
        wt_summary.append(
            {
                "arm": arm_label,
                "n": len(w),
                "mean": w.mean(),
                "sd": w.std(),
                "min": w.min(),
                "p1": w.quantile(0.01),
                "p25": w.quantile(0.25),
                "median": w.median(),
                "p75": w.quantile(0.75),
                "p99": w.quantile(0.99),
                "max": w.max(),
            }
        )
    wt_df = pd.DataFrame(wt_summary)
    wt_summary_path = output_core / "iptw_weight_summary.csv"
    wt_df.to_csv(wt_summary_path, index=False)
    logger.info(f"Saved: {wt_summary_path}")

    # ==========================================================================
    # 3. SCHOENFELD RESIDUALS / PH TEST
    # ==========================================================================

    logger.info("Running Schoenfeld PH tests...")
    outcomes = [
        ("stroke_s1_time_years", "stroke_s1_event", "Acute ischemic stroke"),
        ("b4_mci_time_years", "b4_mci_event", "Probable dementia + MCI"),
        ("b4_time_years", "b4_event", "Probable dementia alone"),
        ("stroke_s2_time_years", "stroke_s2_event", "Ischemic stroke + TIA"),
    ]

    ph_rows = []
    for time_col, event_col, label in outcomes:
        d = sv[["treated", time_col, event_col, "iptw"]].dropna()
        d = d[d[time_col] > 0]
        if d[event_col].sum() < 10:
            ph_rows.append(
                {"outcome": label, "test_statistic": np.nan, "p_value": np.nan, "note": "insufficient events"}
            )
            continue
        try:
            cph = CoxPHFitter(penalizer=0.01)
            cph.fit(d, duration_col=time_col, event_col=event_col, weights_col="iptw", robust=True)
            ph_result = proportional_hazard_test(cph, d, time_transform="rank")
            pval = float(ph_result.summary.loc["treated", "p"])
            test_stat = float(ph_result.summary.loc["treated", "test_statistic"])
            ph_rows.append({"outcome": label, "test_statistic": test_stat, "p_value": pval, "note": ""})
            logger.info(f"  {label}: PH p={pval:.4f}")
        except Exception as e:
            ph_rows.append({"outcome": label, "test_statistic": np.nan, "p_value": np.nan, "note": str(e)})

    ph_df = pd.DataFrame(ph_rows)
    ph_path = output_core / "ph_schoenfeld.csv"
    ph_df.to_csv(ph_path, index=False)
    logger.info(f"Saved: {ph_path}")

    logger.info("diagnostics complete.")
    for handler in logger.handlers:
        handler.close()


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
