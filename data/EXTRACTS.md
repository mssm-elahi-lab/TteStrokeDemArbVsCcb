# Data Extract Versions

**Source of truth:** `DOWNLOAD_MANIFEST.csv` (a tracked copy lives at
`data/DOWNLOAD_MANIFEST.csv`; the original ships inside the export at
`data/raw/extract_v3/DOWNLOAD_MANIFEST.csv`). It indexes every file in the
**May 18 2026 AIRMS export** (`tte_raw_separate_parquets_20260518_180330`) with
its size, row count, unique-person count, and original HPC path. The export has
some `_partial` variants; the pipeline uses the **full** (non-partial) files.


| Folder | Code Refers To | Notes |
|--------|-----------------|-------|
| `extract_v1` | v1 | Oldest AIRMS pull. 5 parquet files (spine, icd map, antihtn exposures, conditions, measurements). |
| `extract_v2` | v2 | Second AIRMS pull. Same 5 files as v1, refreshed. |
| `extract_v3` | v4 / "most recent extract" | Current pull used by the pipeline. 34 parquet files; the 5 required by the core pipeline are `raw_antihypertensive_exposures.parquet`, `cohort_spine_raw.parquet`, `raw_conditions.parquet`, `icd_to_snomed_map.parquet`, `raw_baseline_medications.parquet`. |

**Why v3 = v4 in code?** The original scripts numbered extracts v3 and v4 before these
folders were named "Version N of AIRMS data pull". `extract_v3` here is what the code
calls `V4_DIR` / "most recent extract". The mapping is frozen for reproducibility —
do not renumber.

**Audit-only, not present:** `run01_config.py` also defines `V3_EXPORT` /
`V3_CONDITIONS_AUDIT` / `V3_ICD_MAP_AUDIT`, pointing at a `v3rstudio-export` folder
used only for historical comparison, never read by the pipeline. That folder was not
found under the original project root and is not part of `data/raw/`.

## `appendicitis/` — falsification / negative-control source

`data/raw/appendicitis/appendicitis_conditions_raw_NARROW.parquet` (1,461 rows,
313 persons) is the raw condition-occurrence pull for the appendicitis
falsification analysis (Supp Table 9). It was produced by an external/cloud
AIRMS query and is not part of any versioned extract above. The canonical copy
lives at the path `config/paths.yml:appendicitis_narrow` points to; the original
was relocated from the legacy `Analysis Datasets/appendicitis_falsification_run01/`
folder. Only `src/sensitivity/appendicitis_falsification.py` reads it.
