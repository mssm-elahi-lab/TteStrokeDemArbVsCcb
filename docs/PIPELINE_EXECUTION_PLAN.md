# TTE Reproducibility Pipeline — Master Execution Plan

**Status:** Ready to execute. Self-contained handoff — assumes **no prior conversation context**.
**Project:** `C:\Users\riccig01\Downloads\TteAnalysis`
**Author of plan:** Claude (organizing agent). **Domain owner:** the user (organizing someone else's code for publication; not the original author).
**Companion files:** `src/index.md` and each `src/*/index.md` (the script map — read those for per-script disposition).

---

## 0. HOW TO USE THIS DOCUMENT

Read in order: §1 End Goal → §2 Ubiquitous Language → §3 Canonical Design → §4–6 (structure/naming/config) → §7 Artifact Inventory → §8 Scripts → §9 Decisions → §10 Phases → §11 Gaps → §12 Verification → §13 Definition of Done.

**One-line summary:** Turn this folder into a clean, `git`-tracked, config-driven repository where someone with the raw AIRMS extracts runs `python src/main.py --all` and regenerates **every computed table and figure in the manuscript**, with the legacy/dead code removed and the data provenance documented.

---

## 1. END GOAL (the definition of success)

A journal reviewer (or the author, or a future collaborator) can:

1. Clone the repository.
2. `pip install -r requirements.txt`.
3. Copy `config/paths.example.yml` → `config/paths.yml` and set **one** value (`base_dir`).
4. Run **`python src/main.py --all`**.
5. Receive **100% of the manuscript's computed tables and figures**, regenerated from the raw data extracts, identical to what the paper reports — with a timestamped, config-version-stamped audit log.

Non-goals (explicitly out of scope):
- Re-pulling raw data from the OMOP/BioMe **database** (needs protected institutional access; the extraction R code is kept only as readable **provenance**).
- Re-analysing or "fixing" the science. **We reproduce the published `run01` analysis exactly.** Documented statistical caveats become *limitations text*, not code changes.
- The descriptive **Main Table 1** (target-trial specification) — it is design prose, not computed; it stays a static document.

---

## 2. UBIQUITOUS LANGUAGE (shared, precise glossary)

Use these exact terms everywhere (code, comments, docs, commits). This is the shared domain language.

### Study domain
| Term | Precise meaning |
|---|---|
| **TTE** | Target Trial Emulation — the causal-inference framework emulating a randomized trial from observational EHR data. |
| **ARB** | Angiotensin Receptor Blocker — the **treatment** arm (exposure=1). Ingredients: losartan, valsartan, olmesartan, telmisartan, candesartan, azilsartan. |
| **DHP-CCB** | Dihydropyridine Calcium Channel Blocker — the **active comparator** arm (exposure=0). Index ingredients: amlodipine, nifedipine. |
| **Index date** | Date of first qualifying ARB or DHP-CCB dispensing ("first-drug-wins" intention-to-treat assignment). |
| **Washout** | 180-day pre-index window; any prior ARB/DHP-CCB/thiazide/ACEi dispensing disqualifies (new-user design). |
| **IPTW** | Inverse Probability of Treatment Weighting — stabilized ATE weights, winsorized at the 1st–99th percentile. |
| **PS** | Propensity Score — P(ARB \| covariates), L2 logistic regression (C=1.0, seed=42). |
| **PS trim** | Restrict to the propensity-score overlap region, 1st–99th percentile of each arm. |
| **SMD** | Standardized Mean Difference — covariate balance metric, computed before and after IPTW. |
| **Primary outcomes** | `stroke_s1` (acute ischemic stroke, harmonized AIS) and `b4_mci` (probable dementia + mild cognitive impairment). |
| **Secondary outcomes** | `b4` (probable dementia alone) and `stroke_s2` (stroke + TIA). |
| **Death-censoring** | Censoring follow-up at death (`XTN_DEATH_DATE`); the defining feature of the `run01` v4 design (addresses informative-censoring concern C1). |
| **Falsification / negative control** | The appendicitis analysis — an outcome with no plausible ARB-vs-DHP-CCB effect; a null result supports adequate confounding control. |

### Project / engineering
| Term | Precise meaning |
|---|---|
| **run01** | The FINAL, canonical analysis design = `run01_v4_core_design_deathcensor` (2026-05-31, v4 extract). **This is what the manuscript reports.** Synonym for "canonical pipeline." |
| **v3 / v4 extract** | AIRMS data-pull generations. **v4 = the "May 18 AIRMS export"** = `data/raw/extract_v3/` (folder name kept for continuity; see `data/EXTRACTS.md`). The canonical analysis uses this. |
| **AIRMS export** | The May 18 2026 multi-file parquet download (`tte_raw_separate_parquets_20260518_180330`), indexed by `DOWNLOAD_MANIFEST.csv`. **The data source of truth.** |
| **Canonical pipeline** | `src/config.py`, `src/main.py`, `src/core/*`, `src/sensitivity/*` — the verified run01 code. |
| **Legacy code** | `src/analysis/*` — ~50 older scripts (mostly the obsolete "RAS vs NON_RAS" v3 design). Almost all REMOVED; a few are DONORS. See `src/analysis/index.md`. |
| **Provenance** | `src/omop_extract/*` — R code that produced the raw extracts from the database. **Kept but not runnable by reviewers.** |
| **Artifact** | A manuscript-reportable output (a table or figure). See §7. |
| **Reachable** | An artifact/script is *reachable* if it produces a manuscript artifact, or a file consumed by another reachable script. **Unreachable ⇒ removed.** |
| **Donor** | A legacy script whose logic is ported into a canonical module to build a GAP. Reference only — it is not run; it is not kept. |
| **GAP** | A manuscript artifact not yet produced by the canonical pipeline. There are 6 (see §11). |
| **Computed artifact** | A table/figure derived from data (vs. descriptive/static, e.g., Main Table 1). |

### Ubiquitous naming rule for the two "Table" numbering systems (CRITICAL — avoids confusion)
The manuscript and the code number things differently. **Always disambiguate:**
- **Manuscript Table 1** = target-trial specification = **descriptive/static** (no script).
- **Manuscript Table 2** = baseline characteristics ± IPTW = code module `table1` → output `baseline_characteristics.csv`.
- **Manuscript Table 3** = hazard ratios = code module `table2` → output `hazard_ratios.csv`.
- When speaking of a table, **always say "Manuscript Table N" or "the `table1`/`table2` module,"** never a bare "Table 2."

---

## 3. THE CANONICAL DESIGN — why `run01`/v4, proven empirically

The repository historically contains **two analysis lineages**. Only one is the manuscript.

| | **Canonical (USE THIS)** | **Legacy (REMOVE)** |
|---|---|---|
| Name | `run01_v4_core_design_deathcensor` | RAS-vs-NON_RAS, v3 |
| Exposure | ARB vs **DHP-CCB** (thiazides excluded) | ARB vs **NON_RAS** (CCB **+ thiazide**) |
| Extract | v4 (May 18 export) | v3 |
| Analytic N | **87,510** (ARB=34,732; DHP-CCB=52,778) | 77,321 |
| Location | `src/core/` + `src/sensitivity/` | `src/analysis/` |
| Death-censoring | Yes | No |

**Proof the manuscript = run01/v4 (empirical, not inferred):** the manuscript text states *"the analytic cohort included 87,510 adults, including 34,732 ARB initiators [and] 52,778 DHP-CCB"*; the sensitivity Ns (appendicitis 87,357; monotherapy 78,180; extended-follow-up 32,647) all match run01 exactly; the legacy Ns (77,321/30,374/46,947) appear **zero** times; and Supp Table 5 uses baseline BP, which exists only in v4. There is also an author GitHub repo (`akarshsharma3/tte-project`) that is an **earlier v3 snapshot** — useful for provenance/known-issues **only**; its "canonical" v3 pipeline is superseded.

---

## 4. TARGET REPOSITORY STRUCTURE

```
TteAnalysis/                          ← repo root (git-tracked)
├── README.md                         ← run instructions + manuscript-artifact map + limitations
├── requirements.txt                  ← pinned Python deps
├── .python-version                   ← 3.13
├── .gitignore                        ← excludes data/, outputs/, config/paths.yml
├── config/
│   ├── analysis.yml                  ← shared clinical/study params (git-tracked, VERIFIED against source)
│   ├── paths.example.yml             ← template (git-tracked)
│   └── paths.yml                     ← machine-local paths (GITIGNORED)
├── data/                             ← GITIGNORED (large, protected)
│   ├── raw/
│   │   ├── extract_v3/               ← the May 18 AIRMS export (32 parquets + DOWNLOAD_MANIFEST.csv)
│   │   └── appendicitis/             ← appendicitis_conditions_raw_NARROW.parquet (falsification source)
│   ├── EXTRACTS.md                   ← data provenance; references DOWNLOAD_MANIFEST.csv
│   └── DOWNLOAD_MANIFEST.csv         ← (copy) authoritative index of the export
├── src/
│   ├── index.md                      ← src overview + pipeline DAG
│   ├── __init__.py
│   ├── config.py                     ← typed config loader (dataclasses; ${var} path resolution)
│   ├── main.py                       ← single orchestrator / CLI
│   ├── core/                         ← canonical primary pipeline (+ index.md)
│   ├── sensitivity/                  ← 5 sensitivity analyses (+ index.md)
│   └── omop_extract/                 ← R extraction PROVENANCE (not runnable; + index.md + README)
├── outputs/                          ← GITIGNORED, regenerable
│   ├── core/                         ← manuscript main + core supplement artifacts
│   ├── sensitivity/<name>/           ← one folder per sensitivity analysis
│   └── logs/                         ← timestamped, config-version-stamped run logs
└── manuscript/                       ← main/ supplemental/ cover_letter/ (docx)
```

**Removed at cleanup:** `src/analysis/` (after donors are ported), and the pre-existing old top-level folders `Scripts/`, `Analysis Datasets/`, `Version N of AIRMS data pull/` (these are the un-refactored originals; the user previously chose to keep them until the new pipeline was proven — they may now be deleted or moved out of the shared tree; **confirm before deleting** since data is large).

---

## 5. NAMING CONVENTIONS

- **No dates, no `run01` prefix, no version suffixes** in filenames. (`table1_primary_ms_ready_CORRECTED_20260615v2.csv` → `baseline_characteristics.csv`.)
- **Semantic output names**, not manuscript numbers (avoids the Table-2-vs-Table-3 confusion). Canonical output names:
  | Module | Output file | Manuscript artifact |
  |---|---|---|
  | `build_cohort` | `indexed_cohort.parquet` | (intermediate) |
  | `compute_outcomes` | `survival_dataset.parquet`, `covariate_balance.csv` | (intermediate) |
  | `cohort_flow` (GAP1) | `cohort_flow.csv` + `cohort_flow.png` | **Figure 1** |
  | `table1` | `baseline_characteristics.csv` (+ `_pvalues.csv`) | **Table 2** |
  | `table2` | `hazard_ratios.csv` | **Table 3** |
  | `plot_forest` | `forest_plot.png/.pdf` | **Figure 2** |
  | `diagnostics` | `ps_overlap.png`, `iptw_weight_summary.csv`, `ph_schoenfeld.csv` | **Supp Fig 1, Supp Table 2** |
  | `balance_plot` (GAP2) | `covariate_balance_love_plot.png` | **Supp Fig 2** |
  | `plot_cumulative` | `cumulative_event_*.png/.pdf` | **Supp Fig 3** |
  | `ps_diagnostics` (GAP3) | `ps_iptw_diagnostics.csv` | **Supp Table 1** |
  | `concept_definitions` (GAP4) | `concept_definitions.csv` | **Supp Table 3** |
  | `cox_coefficients` (GAP5) | `cox_coefficients.csv` | **Supp Table 4** |
  | `bp_hierarchy` | `bp_model_hierarchy.csv` | **Supp Table 5** |
  | `monotherapy` | `monotherapy_*.csv` | **Supp Table 6** |
  | `followup_timing` (GAP6) | `followup_timing.csv` | **Supp Table 7** |
  | `extended_followup` | `results.csv`, `qc_report.md` | **Supp Table 8** |
  | `appendicitis_falsification` | `appendicitis_falsification_results.csv` | **Supp Table 9** |
- **Modules**: `snake_case.py`, one analysis step each. Each exposes `def run(config: Config) -> None:`.
- **Config keys**: `snake_case`; nested by domain (`cohort.washout_days`, `propensity_score.trim_lower`).
- **Commits**: conventional (`feat:`, `refactor:`, `docs:`, `chore:`). No dates in messages.

---

## 6. CONFIGURATION MODEL

**Two-tier YAML, loaded/merged by `src/config.py` into frozen dataclasses.**

- **`config/analysis.yml`** (git-tracked, shared): all clinical definitions, study parameters, drug lists, SNOMED/ICD concept IDs, outcome roles, multiple-testing settings. **Already created and VERIFIED** — all 47 fields were diffed programmatically against the original `run01_config.py`; identical. Do not change values (that would break manuscript reproduction).
- **`config/paths.yml`** (gitignored, machine-local): `base_dir` + specific parquet references. **Per-analysis specific references — never a merged mega-dataset.** Each script reads only the files it needs. Includes: `antihypertensive_exposures`, `spine`, `conditions`, `icd_map`, `baseline_medications`, `baseline_covariates_augmented`, `appendicitis_narrow`, and `outputs.*`.
- **`DOWNLOAD_MANIFEST.csv`** is the **data source of truth**: it lists every file in the May 18 export (name, size, rows, unique persons, original HPC path). Reference it in `data/EXTRACTS.md`. Note the export has `_partial` variants — the pipeline uses the **full** (non-partial) files.
- **Precedence:** CLI flag > env var > `paths.yml` > `analysis.yml` > code default.
- Every run prints/logs a **version stamp**: `ConfigV1.0 (2026-05-31) @ <timestamp>` to `outputs/logs/`.

---

## 7. MANUSCRIPT ARTIFACT INVENTORY (the reachability root)

17 computed artifacts + 1 descriptive. **Status legend:** ✅ generates today (verified) · 🔨 GAP to build · 📄 static.

| # | Manuscript artifact | Status | Module |
|---|---|---|---|
| 1 | Main Table 1 — target-trial specification | 📄 static | — |
| 2 | **Main Figure 1 — cohort flow** | 🔨 GAP1 | `cohort_flow` |
| 3 | Main Table 2 — baseline ± IPTW | ✅ | `table1` + `add_pvalues` |
| 4 | Main Figure 2 — forest plot | ✅ | `plot_forest` |
| 5 | Main Table 3 — hazard ratios | ✅ | `table2` |
| 6 | Supp Fig 1 — PS overlap | ✅ | `diagnostics` |
| 7 | **Supp Fig 2 — covariate-balance love plot** | 🔨 GAP2 | `balance_plot` |
| 8 | Supp Fig 3 — IPTW-KM primary outcomes | ✅ (verify form) | `plot_cumulative` |
| 9 | **Supp Table 1 — PS/IPTW diagnostics** | 🔨 GAP3 | `ps_diagnostics` |
| 10 | Supp Table 2 — PH / Schoenfeld | ✅ | `diagnostics` |
| 11 | **Supp Table 3 — concept definitions** | 🔨 GAP4 | `concept_definitions` |
| 12 | **Supp Table 4 — full Cox coefficients** | 🔨 GAP5 | `cox_coefficients` |
| 13 | Supp Table 5 — BP-adjusted sensitivity | ✅ (N reconciled) | `bp_hierarchy` |
| 14 | Supp Table 6 — monotherapy | ✅ | `monotherapy` |
| 15 | **Supp Table 7 — follow-up / timing** | 🔨 GAP6 (partial) | `followup_timing` (+ `curve_divergence`) |
| 16 | Supp Table 8 — extended follow-up | ✅ | `extended_followup` |
| 17 | Supp Table 9 — appendicitis falsification | ✅ | `appendicitis_falsification` |

**Today: 10 computed artifacts generate end-to-end from raw data, byte-verified.** 6 gaps remain (all buildable from existing pipeline outputs — no missing data, no missing methods).

---

## 8. WHICH SCRIPTS TO CONSIDER, AND WHY

**Do not reverse-engineer the legacy pile blindly. Use the index.md files.**
- `src/core/index.md`, `src/sensitivity/index.md` — the canonical modules (KEEP; already verified).
- `src/analysis/index.md` — every legacy script classified **KEEP / DONOR (for GAP-n) / REMOVE (reason)** with its I/O. The large majority are REMOVE (obsolete RAS-vs-NON_RAS v3 design or one-off diagnostics/audits). Only a handful are DONORS (their logic is ported into the GAP modules, then they are removed).
- `src/omop_extract/index.md` — R extraction files, all PROVENANCE-KEEP (documented, not runnable).

**Rule (repeat):** keep a legacy script only if it generates a manuscript artifact or a file consumed by a kept script. Everything else is removed. Donors are ported, then removed.

---

## 9. DECISIONS LOG (resolved with the domain owner — do not relitigate)

1. **Source of truth = `run01`/v4** (ARB vs DHP-CCB, N=87,510). The manuscript reports this. Legacy v3/RAS-NONRAS is removed.
2. **Reproduction scope = 100% of computed artifacts.** Build the 6 gaps. Descriptive Main Table 1 stays static.
3. **Language = Python analytical pipeline** + R OMOP extraction kept as **non-runnable provenance** (no reachable R analysis exists — legacy R was hand-typed tables / SuperLearner, all dead).
4. **Destination = this `TteAnalysis` folder**, fresh `git` initialized here. Author's GitHub repo left untouched.
5. **Dead-code disposal = messy-but-complete first commit, then delete in follow-up commits** (clean HEAD, fully recoverable via history).
6. **Stance = reproduce `run01` exactly.** Do not "fix" documented bugs; surface them as *limitations* in the README.

---

## 10. EXECUTION PHASES (intent + steps + acceptance checks)

Each phase has an explicit **Intent** and **Done-check**. Do not advance until the Done-check passes.

### Phase A — Map & Index  *(non-destructive; do first)*
**Intent:** produce the authoritative script map so disposal and porting are grounded, not guessed.
**Steps:** create `src/index.md` (overview + pipeline DAG), `src/core/index.md`, `src/sensitivity/index.md`, `src/analysis/index.md` (full KEEP/DONOR/REMOVE classification with I/O), `src/omop_extract/index.md`.
**Done-check:** every file in `src/analysis` and `src/omop_extract` appears in an index with a disposition; every GAP has an identified donor (or "build fresh from <existing output>").

### Phase B — Safety Net  *(git)*
**Intent:** capture the complete current state before any change; nothing lost.
**Steps:** `git init`; confirm `.gitignore` excludes `data/`, `outputs/`, `config/paths.yml`; `git add -A`; commit `chore: initial import (complete pre-cleanup state)`.
**Done-check:** `git status` clean; `git log` shows the import commit; `data/` and `outputs/` NOT tracked.

### Phase C — Wire data + prove end-to-end
**Intent:** confirm the pipeline runs raw→artifacts in one command before building gaps.
**Steps:** place the appendicitis parquet at `data/raw/appendicitis/appendicitis_conditions_raw_NARROW.parquet` (source: `Analysis Datasets/appendicitis_falsification_run01/appendicitis_conditions_run01_raw_NARROW.parquet`); point `config/paths.yml:appendicitis_narrow` at it; run `python src/main.py --all`.
**Done-check:** all 10 existing artifacts regenerate; appendicitis completes (N=87,357, 156 events, HR≈1.18); logs carry the version stamp.

### Phase D — Build the 6 GAPS  (see §11 for specs)
**Intent:** reach 100% computed-artifact reproduction.
**Steps:** implement each GAP module under `src/core/` (or a new `src/reporting/`), port donor logic to the run01 design, wire into `main.py`, verify each output against the manuscript's reported numbers.
**Done-check:** each of the 17 computed artifacts has a generating module; each GAP output matches the manuscript (or a documented, understood reconciliation).

### Phase E — Prune
**Intent:** clean HEAD for the reviewer.
**Steps:** delete `src/analysis/` (donors already ported); keep `src/omop_extract/` as provenance with its README; commit `refactor: remove superseded legacy analysis lineage`. Confirm with the owner before deleting the large old top-level folders (`Scripts/`, `Analysis Datasets/`, `Version N ...`).
**Done-check:** `src/` contains only `config.py`, `main.py`, `core/`, `sensitivity/`, `omop_extract/`, and index.md files; `python src/main.py --all` still passes.

### Phase F — Docs, Provenance, Final Smoke Test
**Intent:** make it publication-grade and self-explanatory.
**Steps:** finish `README.md` (setup, run, the manuscript-artifact map from §5, limitations from §12); update `data/EXTRACTS.md` referencing `DOWNLOAD_MANIFEST.csv`; add `src/omop_extract/README` marking it non-runnable provenance; final `python src/main.py --all` from a clean state.
**Done-check:** the §13 Definition of Done is fully satisfied.

---

## 11. THE 6 GAPS — build specs

All target the **run01/DHP-CCB design**, verified against **manuscript-reported values** (extract them from `manuscript/`). All inputs already exist as pipeline outputs — no new data.

- **GAP1 — Figure 1, cohort flow** (`cohort_flow`). Input: the checkpoint counts `build_cohort.py` already computes and logs (375,039 raw → 211,931 age 40–70 → 122,158 hypertension → 118,925 prevalent-exclusion → 112,122 same-day-exclusion → 100,320 washout → 88,747 ≥365d follow-up; then PS-trim → 87,510). Emit `cohort_flow.csv` and a CONSORT-style `cohort_flow.png`. Donor: `generate_cohort_flow.py` (reference for layout only — its numbers are v3). **Best approach:** have `build_cohort.py` return/persist a structured flow record; `cohort_flow` renders it.
- **GAP2 — Supp Fig 2, love plot** (`balance_plot`). Input: `covariate_balance.csv` (has `smd_pre`, `smd_post` per covariate). Emit `covariate_balance_love_plot.png` (dot plot, |SMD| before vs after IPTW, reference line at 0.1). Donors: `bbb_balance_assessment.py` (DHP-CCB SMD computation) + `04b_analysis_tables.py` (lollipop love-plot rendering).
- **GAP3 — Supp Table 1, PS/IPTW diagnostics** (`ps_diagnostics`). Inputs: PS AUC/C-statistic (computed in `compute_outcomes` — **persist it**, currently only logged), effective sample size (from `iptw` in `survival_dataset`), weight distribution (`iptw_weight_summary.csv`). Emit `ps_iptw_diagnostics.csv`. Donor: `bbb_ps_iptw_diagnostics.py` / `ps_calibration_cohortB.py`.
- **GAP4 — Supp Table 3, concept definitions** (`concept_definitions`). Input: `config/analysis.yml` already holds all the IDs (drug lists, SNOMED/ICD concept IDs, washout classes). Emit `concept_definitions.csv` (class → ingredients/concept IDs → readable role). Human-readable **names** come from: outcome codebook in `table2_corrected_strict_s1.py`, comorbidity names via `lookup_comorbidity_omop_ids.py` (public Athena API, no DB needed), and the `00c_full_mapping_table.R` **output CSV** (provenance — reuse the CSV, don't re-run). Prefer a static config+names table over hitting an external API at run time. (Borderline descriptive; generating from config is cheap and maximally reproducible.)
- **GAP5 — Supp Table 4, full Cox coefficients** (`cox_coefficients`). `table2.py` already fits the covariate-adjusted Cox model but extracts only the treatment term. Extend to emit **all** coefficients (HR, 95% CI, p) per covariate for the two primary outcomes → `cox_coefficients.csv`. Donors: the adjusted-model fit already in `table2.py`; the `full_covariate_hr` block in `outcome_analyses_outputs.py` shows the extraction pattern.
- **GAP6 — Supp Table 7, follow-up / timing** (`followup_timing`). Inputs: `survival_dataset` follow-up columns (median/IQR follow-up, event timing for `b4_mci`) + the interval/landmark/lagged results already produced by `curve_divergence`. Emit `followup_timing.csv`. Donors: `curve_divergence` (already ported); `14_denominator_reconciliation_and_exposure_time.py` (person-time / follow-up-duration computation); `11_dementia_subgroup_analyses.py` (dementia/MCI event-timing by bucket).

**Reconciliation item (RESOLVED 2026-07-02):** The manuscript's Supp Table 5 BP-adjusted sensitivity (N=58,269; stroke IPTW HR 0.87 [0.77–0.97]; cognitive 0.84 [0.66–1.07]) adds **only baseline SBP + DBP** to the PS — that is `bp_hierarchy` **Model `A_bp_only`**, which reproduces N=58,269 and the HRs exactly. The earlier "58,272" was **Model `B_bp_meds`** (a different, more-adjusted model that also adds ACEi/beta-blocker prior-med flags), not the manuscript's headline BP analysis. No filter bug; the original note mis-attributed the manuscript number to the wrong model.

---

## 12. VERIFICATION METHODOLOGY

- **Existing modules** (`core`, `sensitivity`): already verified **byte-for-byte** against the original `run01` scripts run on the same data (row-by-row parquet/CSV diffs; all numeric fields identical; only cosmetic label/dash differences). Do not regress this.
- **GAP modules:** verify each output against the **manuscript's reported values** (Ns, HRs, CIs, counts extracted from `manuscript/`). A GAP is "done" only when it matches, or the difference is understood and documented.
- **End-to-end:** `python src/main.py --all` from a clean checkout must reproduce every computed artifact and write version-stamped logs.
- **Principle:** reproduce, don't re-analyse. If a computed value disagrees with the manuscript, first suspect a filter/definition difference in the port — not a "bug to fix."

---

## 13. KNOWN CAVEATS / LIMITATIONS (for the README's limitations section)

1. **Data access boundary:** raw extraction (`src/omop_extract/`) needs OMOP/BioMe DB access; reviewers cannot run it. The pipeline starts from the shared raw extracts. This is expected for protected-health-data reproducibility.
2. **Informative censoring (author issue C1):** addressed by the run01 death-censoring design; still disclose as a limitation (death linkage incomplete).
3. ~~**Supp Table 5 BP-N off by 3**~~ — RESOLVED: the manuscript reports `bp_hierarchy` Model `A_bp_only` (N=58,269, exact match); 58,272 was the different Model `B_bp_meds`. See §11.
4. **Main Table 1** is descriptive (target-trial spec), intentionally not script-generated.
5. Documented author issues that are **already resolved in run01** (record as resolved): race imbalance → `race_unknown_r` covariate included; no multiple-testing → Bonferroni + BH-FDR applied across the 2 primary outcomes.

---

## 14. DEFINITION OF DONE

- [ ] `git`-initialized; clean HEAD; `data/`+`outputs/` untracked; history preserves the pre-cleanup state.
- [ ] `python src/main.py --all` regenerates **all 17 computed artifacts** from raw data, with version-stamped logs.
- [ ] Each GAP output matches the manuscript (or has a documented reconciliation).
- [ ] `src/` contains only the canonical pipeline + `omop_extract` provenance + index.md files; legacy `src/analysis/` removed.
- [ ] `README.md` has setup, run command, the manuscript-artifact map, and the limitations list.
- [ ] `config/analysis.yml` unchanged from verified values; `config/paths.yml` gitignored with a working example.
- [ ] `data/EXTRACTS.md` documents provenance and references `DOWNLOAD_MANIFEST.csv`.
- [ ] A reviewer can clone, set `base_dir`, and reproduce the paper's computed results.

---

*End of master plan. The per-script disposition lives in `src/analysis/index.md` (see Phase A).*
