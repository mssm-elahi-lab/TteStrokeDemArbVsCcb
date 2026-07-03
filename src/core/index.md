# `src/core/` — Canonical Primary Pipeline

Design `run01` v4: **ARB vs DHP-CCB**, analytic N=87,510. All modules verified **byte-for-byte** against the original run01 scripts. Each exposes `run(config)`. Reads raw parquets from `data/raw/extract_v3/` via `config.paths.*`; writes to `outputs/core/`.

Output names below are the **current** names; §5 of the master plan specifies a rename to semantic names (shown in brackets) — not yet applied.

| Order | Module | Reads | Writes (current) | Manuscript artifact |
|---|---|---|---|---|
| 0 | `define_ingredients.py` | `config` only | log (ingredient/param summary) | — (verification step) |
| 1 | `build_cohort.py` | `antihypertensive_exposures`, `spine`, `conditions`, `icd_map`, `baseline_medications` | `indexed_cohort.parquet`, `race_coding_audit.md` | feeds **Figure 1** (computes the cohort-flow checkpoint counts → GAP1) |
| 2 | `compute_outcomes.py` | `indexed_cohort.parquet`, `conditions`, `icd_map` | `survival_dataset.parquet`, `covariate_balance.csv` | intermediate (PS, IPTW, outcomes, SMD); also computes **PS AUC** (persist for GAP3) |
| 3 | `table1.py` | `survival_dataset.parquet` | `baseline_characteristics.csv`, `baseline_characteristics_audit_note.txt` | **Table 2** (baseline ± IPTW) |
| 3b | `add_pvalues.py` | `survival_dataset.parquet`, `baseline_characteristics.csv` | `baseline_characteristics_pvalues.csv` | **Table 2** (adds P-value column + White row) — run after `table1` |
| 4 | `table2.py` | `survival_dataset.parquet` | `hazard_ratios.csv`, `hazard_ratios_audit_note.txt`, `cox_coefficients.csv` | **Table 3** (crude/adjusted/IPTW HRs; Bonferroni + BH-FDR) + **Supp Table 4** full Cox coefficients (GAP5) |
| 5 | `diagnostics.py` | `survival_dataset.parquet` | `ps_overlap.png`, `iptw_weight_summary.csv`, `ph_schoenfeld.csv` | **Supp Fig 1** (PS overlap), **Supp Table 2** (Schoenfeld PH) |
| 6 | `plot_forest.py` | `hazard_ratios.csv` | `forest_plot.png/.pdf` | **Figure 2** (forest) |
| 7 | `plot_cumulative.py` | `survival_dataset.parquet` | `cumulative_event_*.png/.pdf` | **Supp Fig 3** (IPTW-KM; verify survival-vs-cumulative form) |
| — | `common.py` | — | — | shared-helpers placeholder (currently empty) |

## Dependencies
`build_cohort` → `compute_outcomes` → everything else. `table1`→`add_pvalues`. `table2`→`plot_forest`.

## GAP modules to ADD here (or in a new `src/reporting/`) — see plan §11
`cohort_flow` [Fig 1], `ps_diagnostics` [Supp Table 1], `concept_definitions` [Supp Table 3], `cox_coefficients` [Supp Table 4], `followup_timing` [Supp Table 7], and `balance_plot` [Supp Fig 2].
