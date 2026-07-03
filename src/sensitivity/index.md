# `src/sensitivity/` — Sensitivity Analyses

Design `run01` v4: **ARB vs DHP-CCB**. All modules verified **byte-for-byte** against the original run01 scripts. Each exposes `run(config)`; all read `outputs/core/survival_dataset.parquet` (from `core.compute_outcomes`) plus, where noted, specific raw parquets. Each writes to `outputs/sensitivity/<name>/`.

| Module | Extra inputs (beyond survival_dataset) | Key output | Manuscript artifact | Verified result |
|---|---|---|---|---|
| `monotherapy.py` | `antihypertensive_exposures` (same-day thiazide flag) | `monotherapy_sensitivity_table2.csv` | **Supp Table 6** | N=78,180; stroke IPTW HR 0.85 (bonf p=0.006) |
| `bp_hierarchy.py` | `baseline_covariates_augmented` (SBP/DBP/BMI + prior-med flags) | `bp_model_hierarchy.csv` | **Supp Table 5** | Model `A_bp_only` = manuscript's BP sensitivity: N=58,269, stroke 0.87 (0.77–0.97), cognitive 0.84 (0.66–1.07) — exact match. `B_bp_meds` (N=58,272) / `C_m1a` are additional, more-adjusted models. |
| `extended_followup.py` | — (restricts index date < 2020-01-01) | `results.csv`, `qc_report.md` | **Supp Table 8** | N=32,647; stroke IPTW HR 0.85 (bonf p=0.038) |
| `curve_divergence.py` | — (interval/landmark/lagged timing) | `overall_hr`, `interval_hr`, `landmark_riskdiff`, `lagged_b4_mci`, `time_interaction` | **Supp Table 7** (partial — pair with GAP6 `followup_timing`) | matches run01 originals |
| `appendicitis_falsification.py` | `appendicitis_narrow` (`data/raw/appendicitis/…NARROW.parquet`) | `appendicitis_falsification_results.csv` | **Supp Table 9** | analysis N=87,357; 156 events; IPTW HR 1.18 (reassuring null) |

## Notes
- `appendicitis_falsification` requires `config.paths.appendicitis_narrow`. The source file is `Analysis Datasets/appendicitis_falsification_run01/appendicitis_conditions_run01_raw_NARROW.parquet` (1,461 rows, 313 persons) — relocate to `data/raw/appendicitis/` and point config at it (plan Phase C). It raises `FileNotFoundError` if absent.
- `curve_divergence` is compute-heavy (many Cox/KM fits across intervals/landmarks) — minutes, not seconds.
- Run all via `python main.py --sensitivity`, or one via `python main.py --step <name>`.
