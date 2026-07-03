# `src/` — Source Index

Canonical, config-driven TTE pipeline (design `run01` v4: **ARB vs DHP-CCB**, N=87,510).
The entry point / CLI is `main.py` at the repository root; `src/` is an importable package.
Read `PIPELINE_EXECUTION_PLAN.md` (repo root) first for the full plan and Ubiquitous Language.

## Layout
| Path | Role | Disposition |
|---|---|---|
| `config.py` | Typed config loader (dataclasses; resolves `${...}` paths from `config/*.yml`). | KEEP |
| `core/` | Canonical primary pipeline (cohort → outcomes/PS/IPTW → tables/figures/diagnostics). See `core/index.md`. | KEEP (verified) |
| `reporting/` | Manuscript reporting artifacts built from core outputs (the 6 gap tables/figures). See `reporting/index.md`. | KEEP |
| `sensitivity/` | 5 sensitivity analyses. See `sensitivity/index.md`. | KEEP (verified) |
| `omop_extract/` | R database-extraction code. See `omop_extract/index.md`. | PROVENANCE (kept, not runnable) |

> The legacy `analysis/` lineage (obsolete v3 / RAS-vs-NON_RAS scripts) was removed
> once its donor logic was ported to the canonical design; it is recoverable via
> git history (initial-import commit).

## Pipeline DAG (what runs, in order)

```
data/raw/extract_v3/*.parquet  (May 18 AIRMS export; per-analysis specific files)
        │
        ▼
core.build_cohort ─────────────► outputs/core/indexed_cohort.parquet  (+ cohort-flow counts → GAP1 Figure 1)
        │
        ▼
core.compute_outcomes ─────────► outputs/core/survival_dataset.parquet, covariate_balance.csv, ps_fit_summary.json
        │
        ├─► core.table1 (+ add_pvalues) ─► baseline_characteristics.csv (+ _pvalues)  [Manuscript Table 2]
        ├─► core.table2 ────────────────► hazard_ratios.csv, cox_coefficients.csv     [Manuscript Table 3, Supp Table 4]
        │        └─► core.plot_forest ──► forest_plot                [Manuscript Figure 2]
        ├─► core.diagnostics ───────────► ps_overlap [Supp Fig 1], ph_schoenfeld [Supp Table 2], iptw_weight_summary
        ├─► core.plot_cumulative ───────► cumulative_event_*         [Supp Fig 3]
        │
        ├─► reporting.{cohort_flow, balance_plot, ps_diagnostics, concept_definitions, followup_timing}
        │                                 [Fig 1, Supp Fig 2, Supp Tables 1, 3, 7]
        │
        └─► sensitivity.{monotherapy, appendicitis_falsification, bp_hierarchy, extended_followup, curve_divergence}
                                          [Supp Tables 6, 9, 5, 8, 7]
```

All 17 computed manuscript artifacts now regenerate from raw data via
`python main.py --all`. See the artifact map in `README.md`.

## Entry points
- `python main.py --all` — full pipeline (core + sensitivity).
- `python main.py --core` — primary analysis only.
- `python main.py --step <name>` — a single module (e.g. `--step table2`).

All modules expose `def run(config: Config) -> None:` and read/write via `config.paths.*`.
