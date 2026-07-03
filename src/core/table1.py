"""Ported from Scripts/Core/03_table1_run01_20260531.py.

Generates Table 1 (baseline characteristics before and after IPTW) for
Cohort B (>=1-year follow-up, PS-trimmed ARB vs DHP-CCB).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from src.config import Config

warnings.filterwarnings("ignore")


def _smd(a, b) -> float:
    pooled_sd = np.sqrt((np.nanstd(a, ddof=1) ** 2 + np.nanstd(b, ddof=1) ** 2) / 2)
    return (np.nanmean(a) - np.nanmean(b)) / pooled_sd if pooled_sd > 0 else np.nan


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)

    survival_path = output_core / "survival_dataset.parquet"
    sv = pd.read_parquet(survival_path)
    print(f"Loaded survival_dataset: {len(sv):,} rows")
    print(f"ARB: {sv['treated'].sum():,}   CCB: {(sv['treated'] == 0).sum():,}")

    def smd_weighted(col, weight_col):
        a = sv.loc[sv["treated"] == 1, col]
        b = sv.loc[sv["treated"] == 0, col]
        wa = sv.loc[sv["treated"] == 1, weight_col]
        wb = sv.loc[sv["treated"] == 0, weight_col]
        pooled_sd = np.sqrt((np.nanstd(a, ddof=1) ** 2 + np.nanstd(b, ddof=1) ** 2) / 2)
        if pooled_sd == 0 or np.isnan(pooled_sd):
            return np.nan
        wa_m = np.average(a.dropna(), weights=wa.loc[a.notna()])
        wb_m = np.average(b.dropna(), weights=wb.loc[b.notna()])
        return (wa_m - wb_m) / pooled_sd

    rows = []

    def add_row(label, arb_val, ccb_val, smd_pre, arb_w, ccb_w, smd_post):
        rows.append(
            {
                "Variable": label,
                "ARB (unweighted)": arb_val,
                "CCB (unweighted)": ccb_val,
                "SMD (unweighted)": round(smd_pre, 3) if not np.isnan(smd_pre) else "",
                "ARB (IPTW)": arb_w,
                "CCB (IPTW)": ccb_w,
                "SMD (IPTW)": round(smd_post, 3) if not np.isnan(smd_post) else "",
            }
        )

    arb = sv[sv["treated"] == 1]
    ccb = sv[sv["treated"] == 0]

    add_row("N", f"{len(arb):,}", f"{len(ccb):,}", np.nan, "", "", np.nan)

    add_row(
        "Age at index (mean ± SD)",
        f"{arb['age_at_index'].mean():.1f} ± {arb['age_at_index'].std():.1f}",
        f"{ccb['age_at_index'].mean():.1f} ± {ccb['age_at_index'].std():.1f}",
        _smd(arb["age_at_index"], ccb["age_at_index"]),
        f"{np.average(arb['age_at_index'].dropna(), weights=arb['iptw'].loc[arb['age_at_index'].notna()]):.1f}",
        f"{np.average(ccb['age_at_index'].dropna(), weights=ccb['iptw'].loc[ccb['age_at_index'].notna()]):.1f}",
        smd_weighted("age_at_index", "iptw"),
    )

    bin_vars = [
        ("Female (%)", "female"),
        ("Black/AA (%)", "race_black_r"),
        ("Asian (%)", "race_asian_r"),
        ("Other race (%)", "race_other_r"),
        ("Hispanic (%)", "hispanic"),
        ("Diabetes (%)", "bl_diabetes"),
        ("CKD (%)", "bl_ckd"),
        ("Heart failure (%)", "bl_heart_failure"),
        ("CAD/MI (%)", "bl_cad_mi"),
        ("AFib (%)", "bl_afib"),
        ("PAD (%)", "bl_pad"),
        ("TIA (%)", "bl_tia"),
    ]
    for label, col in bin_vars:
        if col not in sv.columns:
            continue
        a_pct = 100 * arb[col].mean()
        b_pct = 100 * ccb[col].mean()
        s_pre = _smd(arb[col], ccb[col])
        aw = np.average(arb[col].dropna(), weights=arb["iptw"].loc[arb[col].notna()])
        bw = np.average(ccb[col].dropna(), weights=ccb["iptw"].loc[ccb[col].notna()])
        s_post = smd_weighted(col, "iptw")
        add_row(label, f"{a_pct:.1f}%", f"{b_pct:.1f}%", s_pre, f"{100 * aw:.1f}%", f"{100 * bw:.1f}%", s_post)

    table1 = pd.DataFrame(rows)
    out_csv = output_core / "baseline_characteristics.csv"
    table1.to_csv(out_csv, index=False)
    print(f"Saved: {out_csv}  (ARB N={len(arb):,}, DHP-CCB N={len(ccb):,})")
    print("table1 complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
