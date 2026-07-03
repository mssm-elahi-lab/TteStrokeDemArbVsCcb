# `src/reporting/` — Manuscript reporting artifacts (GAPs)

Pure reporting modules that turn existing core-pipeline outputs into the
manuscript tables/figures the analytical steps did not already emit. None read
raw patient data; each reads `outputs/core/*` and writes back to `outputs/core/`.
All run after `CORE_STEPS` (they depend on `diagnostics` and the persisted
PS / cohort-flow records). Each exposes `run(config)`.

| Module | Reads | Writes | Manuscript artifact | Verified vs manuscript |
|---|---|---|---|---|
| `cohort_flow.py` (GAP1) | `cohort_flow_stages.csv` (build_cohort), `ps_fit_summary.json` (compute_outcomes) | `cohort_flow.csv`, `cohort_flow.png` | **Figure 1** | 375,039→…→87,510; ARB 34,732 / DHP-CCB 52,778 |
| `balance_plot.py` (GAP2) | `covariate_balance.csv` | `covariate_balance_love_plot.png` | **Supp Fig 2** | max |SMD| 0.249 pre / 0.013 post (race_black_r) |
| `ps_diagnostics.py` (GAP3) | `ps_fit_summary.json`, `covariate_balance.csv` | `ps_iptw_diagnostics.csv` | **Supp Table 1** | AUC 0.6075; trimmed 1,237; ESS 84,705 (96.8%); range 0.692–1.645 |
| `concept_definitions.py` (GAP4) | `config/analysis.yml` | `concept_definitions.csv` | **Supp Table 3** | 10 components; concept IDs match |
| `followup_timing.py` (GAP6) | `survival_dataset.parquet` | `followup_duration.csv`, `followup_timing.csv` | **Supp Table 7** | median FU 4.62/4.28/4.87 y; b4_mci timing buckets exact |

**GAP5 (`cox_coefficients.csv`, Supp Table 4)** is emitted directly by the
`table2` core step: `src/core/table2.py` captures the covariate-adjusted Cox
model already fit for the primary outcomes and writes every coefficient
(HR, 95% CI, p). All 15 substantive covariates × 2 primary outcomes match the
manuscript.

## Upstream persistence added for these modules
- `build_cohort` writes `cohort_flow_stages.csv` (the CONSORT checkpoint counts
  it already logs).
- `compute_outcomes` writes `ps_fit_summary.json` (PS AUC, trim count, post-trim
  Ns, IPTW range, effective sample size) — values it otherwise only logs.

These are purely additive; they do not change any existing analytical output.
