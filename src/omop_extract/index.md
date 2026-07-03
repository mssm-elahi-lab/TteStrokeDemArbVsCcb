# `src/omop_extract/` — OMOP Extraction (PROVENANCE, not runnable)

**Disposition: KEEP ALL as provenance.** This R code produced the raw parquet extracts from the Mount Sinai BioMe **OMOP database** (SAP HANA / `CDMDEID` schema). A reviewer **cannot run it** — it needs live institutional DB access (or, for two files, an internet concept API). It is kept for **transparency**: it documents exactly how the raw data in `data/raw/extract_v3/` was created. The runnable pipeline starts *downstream* of this, from the extracts.

Add a short `README` here stating: "Provenance only — requires OMOP/BioMe DB access; not executable by reviewers; produced the parquets indexed by `data/DOWNLOAD_MANIFEST.csv`."

| File | Purpose | Needs | Produces |
|---|---|---|---|
| `__init__.py` | package marker | — | — |
| `00_verify_condition_fields.R` | DB diagnostic: inspect CONDITION_OCCURRENCE vocabularies / unmapped records | live DB | stdout |
| `00b_verify_concept_ids.R` | DB diagnostic: verify ICD→OMOP concept IDs vs local CONCEPT table | live DB | stdout |
| `00c_full_mapping_table.R` | Build **ICD→OMOP→SNOMED** 3-way concept mapping (all conditions) | live DB | `icd_omop_snomed_mapping_table.csv` — *its output CSV is the richest reference for GAP4 concept definitions; reuse the CSV, don't re-run* |
| `01_omop_extract.R` | Main extract: antihypertensive drugs (EPIC ERX codes), conditions, SNOMED map | live DB | `raw_antihypertensive_exposures`, `cohort_spine_raw`, `raw_conditions`, `icd_to_snomed_map`, `source_code_map` `.parquet` |
| `01_full_extract_final.R` | Comprehensive single-script extract (drugs + conditions + labs/smoking/visits/meds) | live DB (10 tables) | 24 parquets |
| `01b_baseline_covariates_7b.R` | Baseline covariates from parquets (no DB) | extracted parquets | 26 covariate parquets |
| `01c_extract_missing_covariates.R` | Augmented covariates (BP/A1c/LDL/BMI, smoking, utilization) | live DB (chunked) | 19 parquets (incl. `baseline_covariates_patient_augmented`) |
| `02b_build_indexed_cohort.R` | Build indexed cohort (age 40–70, washout) + flow, from parquets | extracted parquets | `indexed_cohort.parquet`, `cohort_flow.parquet` — **superseded** by `core/build_cohort.py` for the runnable pipeline; kept only as provenance |
| `fetch_snomed_mappings.py` | Fetch ICD→SNOMED from public OHDSI ATLAS demo API (fallback mapper) | internet | JSON mappings |

**Note:** `updated_omop_extract_expanded_antihypertensives.R` (if present at the project/GitHub root) belongs to this provenance set as well.
