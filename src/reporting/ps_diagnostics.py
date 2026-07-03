"""GAP3 — Supplementary Table 1: propensity-score / IPTW diagnostics.

Emits ``ps_iptw_diagnostics.csv`` (a Diagnostic -> Value summary): PS model,
C-statistic/AUC, trimming, patients trimmed, IPTW winsorization + weight range,
effective sample size, and the maximum absolute SMD before/after IPTW.

Inputs (all existing pipeline outputs; no raw data, no model refit):
  - ``outputs/core/ps_fit_summary.json``      (from compute_outcomes)
  - ``outputs/core/iptw_weight_summary.csv``  (from diagnostics)
  - ``outputs/core/covariate_balance.csv``    (from compute_outcomes)
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.config import Config


def _max_abs_smd(balance: pd.DataFrame, col: str) -> tuple[float, str]:
    s = balance[col].abs()
    idx = s.idxmax()
    return float(balance.loc[idx, col]), str(balance.loc[idx, "covariate"])


def run(config: Config) -> None:
    output_core = config.paths.output_core

    summary_path = output_core / "ps_fit_summary.json"
    balance_path = output_core / "covariate_balance.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"{summary_path} not found — run compute_outcomes first.")
    if not balance_path.exists():
        raise FileNotFoundError(f"{balance_path} not found — run compute_outcomes first.")

    ps = json.loads(summary_path.read_text())
    balance = pd.read_csv(balance_path)

    smd_pre, smd_pre_cov = _max_abs_smd(balance, "smd_pre")
    smd_post, smd_post_cov = _max_abs_smd(balance, "smd_post")

    lo_pct = f"{ps['ps_trim_lower'] * 100:.0f}st"
    hi_pct = f"{ps['ps_trim_upper'] * 100:.0f}th"
    trim_desc = f"{lo_pct}-{hi_pct} percentile"

    rows = [
        ("Primary cohort", "ARB vs DHP-CCB, >=1-year follow-up"),
        ("Analytic N", f"{ps['n_post_trim']:,}"),
        ("ARB / DHP-CCB N", f"{ps['n_post_trim_arb']:,} / {ps['n_post_trim_ccb']:,}"),
        ("PS model", "Logistic regression"),
        ("PS model C-statistic/AUC", f"{ps['ps_auc']:.4f}"),
        ("PS trimming", trim_desc),
        ("Patients trimmed", f"{ps['n_trimmed']:,}"),
        ("Stabilized ATE IPTW", "Yes"),
        ("IPTW winsorization", trim_desc),
        ("IPTW weight range", f"{ps['iptw_min']:.3f}-{ps['iptw_max']:.3f}"),
        (
            "Approximate stabilized IPTW ESS",
            f"{ps['ess_overall']:,.0f} / {ps['n_post_trim']:,}, {ps['ess_overall_pct']:.1f}%",
        ),
        ("Max pre-IPTW |SMD|", f"{abs(smd_pre):.3f}, {smd_pre_cov}"),
        ("Max post-IPTW |SMD|", f"{abs(smd_post):.3f}, {smd_post_cov}"),
    ]

    df = pd.DataFrame(rows, columns=["Diagnostic", "Value"])
    out_path = output_core / "ps_iptw_diagnostics.csv"
    df.to_csv(out_path, index=False)
    print(f"Saved: {out_path}")
    print(df.to_string(index=False))
    print("ps_diagnostics complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
