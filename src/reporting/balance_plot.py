"""GAP2 — Supplementary Figure 2: covariate-balance love plot.

Renders ``covariate_balance_love_plot.png`` — absolute standardized mean
difference (|SMD|) for every propensity-score covariate, before vs after IPTW,
with a reference line at 0.1 (the conventional balance threshold).

Input (existing pipeline output; no raw data):
  - ``outputs/core/covariate_balance.csv``  (from compute_outcomes)
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from src.config import Config  # noqa: E402

# Readable labels for the fixed PS covariates; year dummies fall through as-is.
COVARIATE_LABELS: dict[str, str] = {
    "age_at_index": "Age at index",
    "female": "Female sex",
    "race_black_r": "Race: Black/African American",
    "race_asian_r": "Race: Asian",
    "race_other_r": "Race: Other",
    "race_unknown_r": "Race: Unknown/Unmapped",
    "hispanic": "Hispanic/Latino ethnicity",
    "bl_diabetes": "Diabetes mellitus",
    "bl_ckd": "Chronic kidney disease",
    "bl_heart_failure": "Heart failure",
    "bl_cad_mi": "CAD/MI",
    "bl_afib": "Atrial fibrillation/flutter",
    "bl_pad": "Peripheral artery disease",
    "bl_tia": "Prior TIA",
}


def _label(cov: str) -> str:
    if cov in COVARIATE_LABELS:
        return COVARIATE_LABELS[cov]
    if cov.startswith("yr_"):
        return f"Index year {cov[3:]}"
    return cov


def run(config: Config) -> None:
    output_core = config.paths.output_core
    balance_path = output_core / "covariate_balance.csv"
    if not balance_path.exists():
        raise FileNotFoundError(f"{balance_path} not found — run compute_outcomes first.")

    bal = pd.read_csv(balance_path).copy()
    bal["abs_pre"] = bal["smd_pre"].abs()
    bal["abs_post"] = bal["smd_post"].abs()
    bal = bal.sort_values("abs_pre", ascending=True).reset_index(drop=True)
    bal["label"] = bal["covariate"].map(_label)

    y = range(len(bal))
    fig, ax = plt.subplots(figsize=(8, max(4.0, 0.34 * len(bal))))
    ax.axvline(0.1, color="#888888", linestyle="--", linewidth=1.0, label="|SMD| = 0.1 threshold")
    ax.scatter(bal["abs_pre"], y, facecolors="none", edgecolors="#973b2b", s=48, label="Before IPTW")
    ax.scatter(bal["abs_post"], y, color="#2b5797", s=48, label="After IPTW")
    for yi, (pre, post) in enumerate(zip(bal["abs_pre"], bal["abs_post"])):
        ax.plot([pre, post], [yi, yi], color="#cccccc", linewidth=0.8, zorder=0)

    ax.set_yticks(list(y))
    ax.set_yticklabels(bal["label"], fontsize=8)
    ax.set_xlabel("Absolute standardized mean difference (|SMD|)")
    ax.set_title("Covariate balance before and after IPTW")
    ax.legend(loc="lower right", fontsize=8, frameon=True)
    ax.margins(y=0.02)
    fig.tight_layout()

    out_path = output_core / "covariate_balance_love_plot.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}  ({len(bal)} covariates)")
    print("balance_plot complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
