"""GAP6 — Supplementary Table 7: follow-up duration and event timing.

Emits two CSVs from the survival dataset (no raw data, no model refit):
  - ``followup_duration.csv`` — observed follow-up (years) by arm: N, median,
    IQR, min, max. Observed follow-up uses the primary cognitive-outcome
    timeline ``b4_mci_time_years`` (time to first qualifying event or censor),
    matching the manuscript.
  - ``followup_timing.csv`` — distribution of probable dementia + MCI (b4_mci)
    events across follow-up-month buckets, overall and by arm.

The interval / landmark / lagged hazard-ratio panels that also belong to this
supplement are produced separately by ``src/sensitivity/curve_divergence.py``.
"""

from __future__ import annotations

import pandas as pd

from src.config import Config

# Pooled-group display label differs between the two panels: the duration table
# labels it "Overall", the event-timing table "All". (Presentation only.)
DURATION_GROUPS = [("Overall", None), ("ARB", 1), ("DHP-CCB", 0)]
TIMING_GROUPS = [("All", None), ("ARB", 1), ("DHP-CCB", 0)]


def _duration_row(name: str, years: pd.Series) -> dict:
    return {
        "group": name,
        "n": int(years.notna().sum()),
        "median_years": round(float(years.median()), 2),
        "iqr_lower_years": round(float(years.quantile(0.25)), 2),
        "iqr_upper_years": round(float(years.quantile(0.75)), 2),
        "min_years": round(float(years.min()), 2),
        "max_years": round(float(years.max()), 2),
    }


def run(config: Config) -> None:
    output_core = config.paths.output_core
    survival_path = output_core / "survival_dataset.parquet"
    if not survival_path.exists():
        raise FileNotFoundError(f"{survival_path} not found — run compute_outcomes first.")

    cfg = config.analysis.sensitivity.followup_timing
    followup_col = cfg.followup_column
    edges = list(cfg.bucket_edges_months)
    labels = list(cfg.bucket_labels)

    sv = pd.read_parquet(survival_path, columns=["treated", "b4_mci_event", followup_col])

    # ---- Follow-up duration by arm ------------------------------------------
    dur_rows = [
        _duration_row(name, (sv if treated is None else sv[sv["treated"] == treated])[followup_col])
        for name, treated in DURATION_GROUPS
    ]
    dur_path = output_core / "followup_duration.csv"
    pd.DataFrame(dur_rows).to_csv(dur_path, index=False)
    print(f"Saved: {dur_path}")

    # ---- b4_mci event timing distribution -----------------------------------
    ev = sv[sv["b4_mci_event"] == 1].copy()
    ev["months"] = ev[followup_col] * 12.0
    ev["bucket"] = pd.cut(ev["months"], bins=edges, labels=labels, right=True, include_lowest=True)

    timing = pd.DataFrame({"event_timing_months": labels})
    for name, treated in TIMING_GROUPS:
        sub = ev if treated is None else ev[ev["treated"] == treated]
        n = len(sub)
        counts = sub["bucket"].value_counts().reindex(labels, fill_value=0)
        prefix = {"All": "all", "ARB": "arb", "DHP-CCB": "dhp_ccb"}[name]
        timing[f"{prefix}_n"] = [int(counts[b]) for b in labels]
        timing[f"{prefix}_pct"] = [round(100 * counts[b] / n, 1) if n else 0.0 for b in labels]

    timing_path = output_core / "followup_timing.csv"
    timing.to_csv(timing_path, index=False)
    print(f"Saved: {timing_path}")
    print(f"  b4_mci events: {len(ev):,} (ARB {int((ev.treated == 1).sum())}, DHP-CCB {int((ev.treated == 0).sum())})")
    print("followup_timing complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
