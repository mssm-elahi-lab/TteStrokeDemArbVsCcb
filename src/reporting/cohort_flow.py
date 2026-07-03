"""GAP1 — Figure 1: cohort flow (CONSORT-style) diagram.

Emits ``cohort_flow.csv`` (one row per inclusion checkpoint, with the number
excluded at each step) and ``cohort_flow.png`` (a vertical CONSORT diagram).

Inputs (existing pipeline outputs; no raw data):
  - ``outputs/core/cohort_flow_stages.csv``  (from build_cohort)
  - ``outputs/core/ps_fit_summary.json``     (from compute_outcomes; PS-trim stage)
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from src.config import Config  # noqa: E402


def _load_stages(config: Config) -> pd.DataFrame:
    output_core = config.paths.output_core
    stages_path = output_core / "cohort_flow_stages.csv"
    summary_path = output_core / "ps_fit_summary.json"
    if not stages_path.exists():
        raise FileNotFoundError(f"{stages_path} not found — run build_cohort first.")
    if not summary_path.exists():
        raise FileNotFoundError(f"{summary_path} not found — run compute_outcomes first.")

    stages = pd.read_csv(stages_path)
    ps = json.loads(summary_path.read_text())

    # Append the final PS-overlap-trim stage (produced by compute_outcomes).
    ps_row = pd.DataFrame(
        [
            {
                "stage": "ps_trim",
                "description": f"PS overlap trim ({ps['ps_trim_lower']:.2f}-{ps['ps_trim_upper']:.2f}) — analytic cohort",
                "n_total": ps["n_post_trim"],
                "n_arb": ps["n_post_trim_arb"],
                "n_ccb": ps["n_post_trim_ccb"],
            }
        ]
    )
    return pd.concat([stages, ps_row], ignore_index=True)


def run(config: Config) -> None:
    flow = _load_stages(config)

    # Number excluded at each step = drop in total from the previous checkpoint.
    flow["n_excluded"] = (flow["n_total"].shift(1) - flow["n_total"]).astype("Int64")

    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)
    csv_path = output_core / "cohort_flow.csv"
    flow.to_csv(csv_path, index=False)
    print(f"Saved: {csv_path}")

    _render_png(flow, output_core / "cohort_flow.png")
    print("cohort_flow complete.")


def _render_png(flow: pd.DataFrame, path) -> None:
    n = len(flow)
    fig, ax = plt.subplots(figsize=(9, 2.1 * n))
    ax.axis("off")

    box_w, box_h = 0.60, 0.62
    x_center = 0.34
    y_step = 1.0

    for i, row in flow.iterrows():
        y = (n - 1 - i) * y_step
        label = f"{row['description']}\nN = {int(row['n_total']):,}"
        if pd.notna(row["n_arb"]) and pd.notna(row["n_ccb"]):
            label += f"\n(ARB {int(row['n_arb']):,} / DHP-CCB {int(row['n_ccb']):,})"
        ax.add_patch(
            plt.Rectangle(
                (x_center - box_w / 2, y - box_h / 2),
                box_w,
                box_h,
                fill=True,
                facecolor="#eef3fb",
                edgecolor="#2b5797",
                linewidth=1.3,
            )
        )
        ax.text(x_center, y, label, ha="center", va="center", fontsize=9)

        if i > 0:
            ax.annotate(
                "",
                xy=(x_center, y + box_h / 2),
                xytext=(x_center, y + y_step - box_h / 2),
                arrowprops=dict(arrowstyle="-|>", color="#2b5797", lw=1.2),
            )
            excl = row["n_excluded"]
            if pd.notna(excl):
                y_mid = y + y_step / 2
                ax.add_patch(
                    plt.Rectangle(
                        (x_center + box_w / 2 + 0.04, y_mid - box_h / 3),
                        0.34,
                        0.5,
                        fill=True,
                        facecolor="#fbeeee",
                        edgecolor="#973b2b",
                        linewidth=1.0,
                    )
                )
                ax.text(
                    x_center + box_w / 2 + 0.21,
                    y_mid,
                    f"Excluded\nn = {int(excl):,}",
                    ha="center",
                    va="center",
                    fontsize=8,
                )

    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.7, (n - 1) * y_step + 0.7)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
