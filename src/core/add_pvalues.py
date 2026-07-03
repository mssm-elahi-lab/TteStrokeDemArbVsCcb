"""Ported from Scripts/Core/add_pvalues_table1_run01_20260615.py.

Reads baseline_characteristics.csv (output of table1.run()) and
survival_dataset.parquet, computes unweighted baseline P values, inserts a
derived White (%) row, and writes baseline_characteristics_pvalues.csv with a
"P value" column added.

This is a minimal, accuracy-preserving pass:
  - No models are rerun.
  - No PS/IPTW weights are recomputed.
  - Existing SMD and weighted columns are preserved exactly.
  - Only the White row and P value column are added.

STATISTICAL METHODS:
  Age at index:  Two-sample Welch t-test (scipy.stats.ttest_ind, equal_var=False)
  Binary vars:   Pearson chi-square on 2x2 unweighted count table
                 (scipy.stats.chi2_contingency, correction=False); Fisher exact
                 if any expected cell count < 5.
  Race rows:     Separate 2x2 chi-square for each displayed indicator row
                 (race_white_r, race_black_r, race_asian_r, race_other_r,
                 race_unknown_r). The omnibus chi-square across all 5
                 mutually exclusive race categories is reported in the audit
                 note only; it is NOT inserted as a table row.

NOTE: originally chained off a manually hand-edited "_CORRECTED" table1 file
that is not reproducible by any script. This pipeline instead chains directly
off baseline_characteristics.csv (table1.run()'s output), which has the same
column structure.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import stats

from src.config import Config

warnings.filterwarnings("ignore")


def _pval_format(p: float) -> str:
    """Format P value for display: 3 decimal places, or <0.001."""
    if np.isnan(p):
        return ""
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)

    survival_ds = output_core / "survival_dataset.parquet"
    table1_source = output_core / "baseline_characteristics.csv"
    out_csv = output_core / "baseline_characteristics_pvalues.csv"

    assert table1_source.exists(), f"Table source not found: {table1_source}"
    assert survival_ds.exists(), f"Survival dataset not found: {survival_ds}"

    needed_cols = [
        "treated",
        "age_at_index",
        "female",
        "race_black_r",
        "race_asian_r",
        "race_other_r",
        "race_unknown_r",
        "hispanic",
        "bl_diabetes",
        "bl_ckd",
        "bl_heart_failure",
        "bl_cad_mi",
        "bl_afib",
        "bl_pad",
        "bl_tia",
        "iptw",
    ]

    sv = pd.read_parquet(survival_ds, columns=needed_cols)

    n_arb = int((sv["treated"] == 1).sum())
    n_ccb = int((sv["treated"] == 0).sum())
    n_total = len(sv)

    print(f"\nLoaded survival_dataset: {n_total:,} rows")
    print(f"  ARB : {n_arb:,}")
    print(f"  CCB : {n_ccb:,}")

    assert n_arb == 34_732, f"ARB N mismatch: expected 34,732, got {n_arb:,}"
    assert n_ccb == 52_778, f"CCB N mismatch: expected 52,778, got {n_ccb:,}"
    print("  OK: N confirmed against manuscript table (ARB=34,732, CCB=52,778)")

    # Derive race_white_r as complement of the four other mutually exclusive categories
    sv["race_white_r"] = (
        (sv["race_black_r"] == 0) & (sv["race_asian_r"] == 0) & (sv["race_other_r"] == 0) & (sv["race_unknown_r"] == 0)
    ).astype(int)

    arb = sv[sv["treated"] == 1]
    ccb = sv[sv["treated"] == 0]

    def pval_age(col: str = "age_at_index") -> float:
        """Welch two-sample t-test for a continuous variable."""
        a_vals = arb[col].dropna()
        b_vals = ccb[col].dropna()
        _, p = stats.ttest_ind(a_vals, b_vals, equal_var=False)
        return float(p)

    def pval_binary(col: str) -> float:
        """Pearson chi-square on 2x2 unweighted count table."""
        a_pos = int(arb[col].sum())
        a_neg = n_arb - a_pos
        b_pos = int(ccb[col].sum())
        b_neg = n_ccb - b_pos
        contingency = np.array([[a_pos, a_neg], [b_pos, b_neg]])
        chi2, p, dof, expected = stats.chi2_contingency(contingency, correction=False)
        if expected.min() < 5:
            _, p = stats.fisher_exact(contingency)
        return float(p)

    # Map table Variable label -> computation function
    pval_map = {
        "N": None,  # no test for header row
        "Age at index (mean ± SD)": ("continuous", "age_at_index"),
        "Female (%)": ("binary", "female"),
        "White (%)": ("binary", "race_white_r"),
        "Black/AA (%)": ("binary", "race_black_r"),
        "Asian (%)": ("binary", "race_asian_r"),
        "Other race (%)": ("binary", "race_other_r"),
        "Unknown/Unmapped race (%)": ("binary", "race_unknown_r"),
        "Hispanic (%)": ("binary", "hispanic"),
        "Diabetes (%)": ("binary", "bl_diabetes"),
        "CKD (%)": ("binary", "bl_ckd"),
        "Heart failure (%)": ("binary", "bl_heart_failure"),
        "CAD/MI (%)": ("binary", "bl_cad_mi"),
        "AFib (%)": ("binary", "bl_afib"),
        "PAD (%)": ("binary", "bl_pad"),
        "TIA (%)": ("binary", "bl_tia"),
    }

    pvals_computed = {}
    print("\nComputing P values:")
    for label, spec in pval_map.items():
        if spec is None:
            pvals_computed[label] = np.nan
            continue
        kind, col = spec
        p = pval_age(col) if kind == "continuous" else pval_binary(col)
        pvals_computed[label] = p
        print(f"  {label:<40s}  p = {_pval_format(p)}")

    # Omnibus race chi-square (audit note only — not inserted in table)
    race_cats = ["race_white_r", "race_black_r", "race_asian_r", "race_other_r", "race_unknown_r"]
    race_ct = np.array(
        [
            [int(arb[c].sum()) for c in race_cats],
            [int(ccb[c].sum()) for c in race_cats],
        ]
    )
    chi2_race, p_race_omnibus, dof_race, _ = stats.chi2_contingency(race_ct, correction=False)
    print(
        f"\n  [Omnibus race chi-square (audit only): "
        f"chi2={chi2_race:.2f}, df={dof_race}, p={_pval_format(p_race_omnibus)}]"
    )
    print("  (Not inserted in table — race displayed as binary indicator rows)")

    # ==========================================================================
    # LOAD AND UPDATE TABLE
    # ==========================================================================

    tbl = pd.read_csv(table1_source)
    tbl.columns = [c.strip() for c in tbl.columns]
    print(f"\nLoaded table: {len(tbl)} rows x {len(tbl.columns)} cols")
    print(f"Columns: {list(tbl.columns)}")

    def _pct(series):
        return f"{100 * series.mean():.1f}%"

    def _smd(a_col, b_col):
        pooled_sd = np.sqrt((a_col.std(ddof=1) ** 2 + b_col.std(ddof=1) ** 2) / 2)
        return round((a_col.mean() - b_col.mean()) / pooled_sd, 3) if pooled_sd > 0 else np.nan

    def _iptw_pct(grp_df, col):
        return f"{100 * np.average(grp_df[col].dropna(), weights=grp_df['iptw'].loc[grp_df[col].notna()]):.1f}%"

    def _smd_w(col):
        a_col = arb[col]
        b_col = ccb[col]
        pooled_sd = np.sqrt((a_col.std(ddof=1) ** 2 + b_col.std(ddof=1) ** 2) / 2)
        if pooled_sd == 0 or np.isnan(pooled_sd):
            return np.nan
        wa_m = np.average(a_col.dropna(), weights=arb["iptw"].loc[a_col.notna()])
        wb_m = np.average(b_col.dropna(), weights=ccb["iptw"].loc[b_col.notna()])
        return round((wa_m - wb_m) / pooled_sd, 3)

    white_row = pd.DataFrame(
        [
            {
                "Variable": "White (%)",
                "ARB (unweighted)": _pct(arb["race_white_r"]),
                "CCB (unweighted)": _pct(ccb["race_white_r"]),
                "SMD (unweighted)": _smd(arb["race_white_r"], ccb["race_white_r"]),
                "ARB (IPTW)": _iptw_pct(arb, "race_white_r"),
                "CCB (IPTW)": _iptw_pct(ccb, "race_white_r"),
                "SMD (IPTW)": _smd_w("race_white_r"),
            }
        ]
    )

    black_idx = tbl.index[tbl["Variable"] == "Black/AA (%)"].tolist()
    if not black_idx:
        raise ValueError("Could not locate 'Black/AA (%)' row for White row insertion.")
    black_pos = black_idx[0]
    tbl = pd.concat([tbl.iloc[:black_pos], white_row, tbl.iloc[black_pos:]], ignore_index=True)
    print(f"Inserted White (%) row before Black/AA at position {black_pos}")
    print(f"Table now: {len(tbl)} rows")

    ccb_col_idx = tbl.columns.get_loc("CCB (unweighted)")
    insert_at = ccb_col_idx + 1
    tbl.insert(insert_at, "P value", tbl["Variable"].map(lambda v: _pval_format(pvals_computed.get(v, np.nan))))

    tbl.to_csv(out_csv, index=False)
    print(f"Saved: {out_csv}")
    print("add_pvalues complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
