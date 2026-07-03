"""
run01_config.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Importable configuration module (CORRECTED 2026-05-31).
Defines all ingredient lists, drug class mappings, data paths, and design
constants. Imported by all pipeline scripts in this candidate run.

CORRECTION PASS (2026-05-31):
  [C1] V4_CONDITIONS and V4_ICD_MAP now point to v4 extract, not v3.
  [C2] Dementia outcomes use v4 harmonized B4/B4_MCI bucket definitions
       (from cognitive_bucket_rerun_20260526 codebook).
       B4_MCI is PRIMARY cognitive outcome; B4 is SECONDARY.
  [C3] Stroke primary = harmonized AIS (443454+372924). Broad (4164092) = SENSITIVITY.
  [C4] Prevalent exclusion: cognitive = B4_MCI union; vascular = primary stroke S1.
       Prior TIA NOT excluded from cohort (covariate only).
  [C5] Censoring uses clinical_end_date = max(obs_end, last_condition_date,
       last_drug_date, last_baseline_med_date), then min with death/analysis cap.
  [C6] ACEi washout uses date-based window (1-180d pre-index) from
       raw_baseline_medications (DRUG_EXPOSURE_START_DATE confirmed present).
  [C7] Race: mutually exclusive White/Black/Asian/Other/Unknown.
       Unknown is NOT combined with Other. Unknown excluded from PS fit (logged).
  [C8] Table 2 includes Bonferroni+BH-FDR across 2 primary outcomes only.
  [C9] No arbitrary penalizer; document if used.
  [C10] V4 data throughout (conditions, ICD map, spine, medications).

DO NOT MODIFY without updating README_design_changes.md.

Author: (initials)
Date:   2026-05-31 (correction pass)
"""

from pathlib import Path

# ==============================================================================
# FIXED ANALYSIS CONSTANTS
# ==============================================================================

ANALYSIS_END_DATE_STR = "2025-12-31"   # v3 AIRMS analysis — fixed for reproducibility
WASHOUT_DAYS          = 180
MIN_AGE               = 40
MAX_AGE               = 70
MIN_FOLLOWUP_DAYS     = 365
PS_TRIM_LOWER         = 0.01
PS_TRIM_UPPER         = 0.99
DEMENTIA_LAG_DAYS     = 180
STROKE_LAG_DAYS       = 90
RANDOM_SEED           = 42

# ==============================================================================
# DHP-CCB INDEX TOGGLE
# ==============================================================================
# False (default): index = amlodipine + nifedipine only
# True (AUTHOR_REVIEW_REQUIRED before changing): adds felodipine + isradipine
# Washout always includes all 4.

INCLUDE_ADDITIONAL_DHP_INDEX: bool = False

# ==============================================================================
# INGREDIENT LISTS
# ==============================================================================

ARB_INGREDIENTS = [
    "losartan",
    "valsartan",
    "olmesartan",
    "telmisartan",
    "candesartan",
    "azilsartan",
    # irbesartan, eprosartan: absent from v4 extract
]

DHP_CCB_PRIMARY_INDEX = [
    "amlodipine",
    "nifedipine",
]

DHP_CCB_ADDITIONAL = [
    "felodipine",
    "isradipine",
    # nisoldipine: absent from v4 extract
]

# Derived lists — do not modify directly; change INCLUDE_ADDITIONAL_DHP_INDEX above
DHP_CCB_INDEX   = DHP_CCB_PRIMARY_INDEX + (DHP_CCB_ADDITIONAL if INCLUDE_ADDITIONAL_DHP_INDEX else [])
DHP_CCB_WASHOUT = DHP_CCB_PRIMARY_INDEX + DHP_CCB_ADDITIONAL  # always all 4

THIAZIDE_WASHOUT = [
    "hydrochlorothiazide",
    "chlorthalidone",
    "indapamide",
    # metolazone: excluded pending author review
]

THIAZIDE_REVIEW_PENDING = ["metolazone"]

ACEI_WASHOUT = [
    "lisinopril",
    "enalapril",
    "ramipril",
    "captopril",
    "benazepril",
    # quinapril, fosinopril, moexipril, perindopril, trandolapril: absent from v4
]

DHP_CCB_EXCLUDED_IV_NONDPHP = [
    "clevidipine",   # IV/procedural — AUTHOR_REVIEW_REQUIRED if disputed
    "nicardipine",   # IV/procedural — AUTHOR_REVIEW_REQUIRED if disputed
    "nimodipine",    # neurological; absent from v4 extract
    "diltiazem",     # non-DHP CCB
    "verapamil",     # non-DHP CCB
]

# ==============================================================================
# DATA PATHS — v4 EXPANDED EXTRACT [C1/C10 CORRECTED]
# All conditions/outcomes/ICD mapping data use v4 extract, NOT v3rstudio-export.
# V3 paths preserved as V3_*_AUDIT for comparison only; never use in pipeline.
# ==============================================================================

BASE_DIR  = Path("/Users/akarshsharma/Desktop/tte-project")
V4_DIR    = BASE_DIR / "AIRMS" / "most recent extract"
V3_EXPORT = BASE_DIR / "data" / "results" / "v3rstudio-export"  # AUDIT ONLY
OUT_DIR   = BASE_DIR / "AIRMS" / "results" / "final_candidate_runs_20260531" / "run01_v4_core_design_deathcensor"
LOG_DIR   = OUT_DIR / "logs"

# Primary v4 sources
V4_ANTIHTN_EXPOSURES = V4_DIR / "raw_antihypertensive_exposures.parquet"
V4_SPINE             = V4_DIR / "cohort_spine_raw.parquet"
V4_CONDITIONS        = V4_DIR / "raw_conditions.parquet"        # [C1] CORRECTED: was v3
V4_ICD_MAP           = V4_DIR / "icd_to_snomed_map.parquet"     # [C1] CORRECTED: was v3; 76 rows confirmed
V4_BASELINE_MEDS     = V4_DIR / "raw_baseline_medications.parquet"
# DRUG_EXPOSURE_START_DATE confirmed present in V4_BASELINE_MEDS

# Audit-only (READ ONLY — do not use in pipeline)
V3_CONDITIONS_AUDIT  = V3_EXPORT / "raw_conditions.parquet"
V3_ICD_MAP_AUDIT     = V3_EXPORT / "icd_to_snomed_map.parquet"

# Intermediate outputs
RUN01_INDEXED_COHORT   = OUT_DIR / "run01_indexed_cohort.parquet"
RUN01_SURVIVAL_DATASET = OUT_DIR / "run01_survival_dataset.parquet"

# ==============================================================================
# COGNITIVE OUTCOME DEFINITIONS — B4 / B4_MCI [C2 CORRECTED]
#
# Source: cognitive_bucket_rerun_20260526/codebook_cognitive_buckets.csv
#   analysis_freezes/current_analysis_2026_05_26/cognitive_bucket_rerun_20260526/
#
# B4 (probable dementia alone):
#   SNOMED IDs = [378419, 443605, 4182210]
#     378419  = Alzheimer's disease (F00/G30)
#     443605  = Vascular dementia (F01)
#     4182210 = Unspecified dementia (F03/ICD9-290)
#
# B4_MCI (probable dementia + MCI):
#   SNOMED IDs = [378419, 443605, 4182210, 439795, 4009705]
#     439795  = MCI, uncertain etiology (G31.84)    <- ICD_CONCEPT_ID 45595932
#     4009705 = Age-related cognitive decline (R41.81) <- ICD_CONCEPT_ID 45553736
#
# Assignment:
#   B4_MCI = PRIMARY cognitive outcome
#   B4     = SECONDARY cognitive outcome
# ==============================================================================

B4_SNOMED_IDS     = [378419, 443605, 4182210]
B4_MCI_SNOMED_IDS = [378419, 443605, 4182210, 439795, 4009705]

# ICD concept IDs for B4 (for prevalent exclusion and ascertainment joins)
B4_ICD_IDS = [
    45533052,   # F00 Dementia in Alzheimer disease -> 378419
    1568087,    # F01 Vascular dementia -> 443605
    1568293,    # G30 Alzheimer's disease -> 378419
    35207114,   # F03 Unspecified dementia -> 4182210
    44824105,   # ICD9-290 Dementias -> 4182210
]

# ICD concept IDs for MCI add-on
MCI_ICD_IDS = [
    45595932,   # G31.84 MCI, uncertain etiology -> 439795
    45553736,   # R41.81 Age-related cognitive decline -> 4009705
]

# Full B4_MCI ICD concept IDs (B4 + MCI; used for conservative prevalent exclusion)
B4_MCI_ICD_IDS = B4_ICD_IDS + MCI_ICD_IDS

# ==============================================================================
# VASCULAR OUTCOME DEFINITIONS — STROKE [C3 CORRECTED]
#
# Three candidates compared in preflight_stroke_definition_counts.md:
#   A. Strict AIS:     443454 only
#   B. Harmonized AIS: 443454 + 372924  <- DEFAULT PRIMARY
#   C. Broad AIS:      443454 + 372924 + 4164092  <- SENSITIVITY ONLY
#
# 372924 (ICD-9 434.x cerebral arterial occlusion w/ infarct) is included in
# the primary definition for adequate ICD-9 era coverage. Source ICD_CONCEPT_ID:
# 44824253 in v4 icd_to_snomed_map.
#
# 4164092 (ICD-9 436 acute ill-defined CVD) is sensitivity only.
# 381591 EXCLUDED: maps from multiple ICD sources including stenosis codes;
#         not a clean ischemic stroke concept.
# ==============================================================================

STROKE_S1_SNOMED_IDS     = [443454, 372924]            # primary: harmonized AIS
STROKE_BROAD_SNOMED_IDS  = [443454, 372924, 4164092]   # sensitivity only
TIA_SNOMED_IDS           = [373503]                    # 381591 excluded

# ICD concept IDs for stroke S1 (for prevalent exclusion + outcome joins)
STROKE_S1_ICD_IDS = [
    1569193,    # ICD10 I63.x acute ischemic stroke -> 443454
    44824253,   # ICD9 434.x cerebral arterial occlusion -> 372924
]

# ICD concept IDs for broad stroke (sensitivity only)
STROKE_BROAD_ICD_IDS = STROKE_S1_ICD_IDS + [
    44835952,   # ICD9 436 acute ill-defined CVD -> 4164092  (SENSITIVITY ONLY)
]

# TIA ICD concept IDs
TIA_ICD_IDS = [
    1568360,    # G45.9 TIA unspecified -> 373503
    44820875,   # ICD9 435.x TIA -> 373503
]

# ==============================================================================
# PREVALENT OUTCOME EXCLUSION [C4 CORRECTED]
#
# Cognitive: exclude if B4_MCI first_date <= index_date (conservative global union)
# Vascular:  exclude if stroke S1 first_date <= index_date
# TIA prior: NOT excluded; is a PS model covariate; stroke+TIA is secondary outcome
#
# Rationale for using B4_MCI as global exclusion:
#   Mirrors cognitive_bucket_rerun_20260526 design (lines 22-41 of that script).
#   Global exclusion >= any single-bucket exclusion, making B4-alone analyses
#   slightly conservative but harmonized across a single cohort build.
#   AUTHOR_REVIEW_REQUIRED to change to per-outcome risk sets.
# ==============================================================================

PREVALENT_COGNITIVE_EXCL_SNOMEDS = B4_MCI_SNOMED_IDS  # conservative global
PREVALENT_VASCULAR_EXCL_SNOMEDS  = STROKE_S1_SNOMED_IDS

# ==============================================================================
# COMORBIDITY ICD CONCEPT IDS (unchanged from v3; use v4 icd_map for resolution)
# ==============================================================================

HYPERTENSION_ICD_IDS = [
    35207668, 1569120, 1569121, 1569122, 1569124,
    44833556, 44832366, 44832367, 44827780, 44832370,
]
DIABETES_ICD_IDS      = [1567940, 1567956, 1567972, 44833365]
CKD_ICD_IDS           = [1571486, 44830172]
HEART_FAILURE_ICD_IDS = [1569178, 44824250]
CAD_MI_ICD_IDS        = [1569125, 1569126, 1569130, 1569133, 44832372, 44834725, 44835930, 44827784]
AFIB_ICD_IDS          = [1569170, 44824248, 44821957, 44820868]
PAD_ICD_IDS           = [1569271, 1569324, 44825446, 44826654]
BL_TIA_ICD_IDS        = TIA_ICD_IDS[:]   # prior TIA = covariate; same ICD list

# ==============================================================================
# CENSORING [C5 CORRECTED]
# clinical_end_date = max(obs_end_date, last_condition_date, last_drug_date,
#                         last_baseline_med_date)  -- clipped at ANALYSIS_END_DATE
# censor_date = min(XTN_DEATH_DATE if present, clinical_end_date, 2025-12-31)
# See 01_build_indexed_cohort for full implementation and audit counts.
# ==============================================================================

# ACEi WASHOUT DATE-BASED WINDOW [C6 CONFIRMED]
# raw_baseline_medications has DRUG_EXPOSURE_START_DATE (138k+ ACEi rows confirmed)
ACEI_WASHOUT_WINDOW_DAYS = 180  # must match WASHOUT_DAYS; explicit for clarity

# ==============================================================================
# RACE CODING [C7 CORRECTED] — mutually exclusive categories
# Unknown/Unmapped is NOT combined with Other.
# Unknown/Unmapped retained and modeled as separate race_unknown_r covariate in PS.
# Persons with unknown race are included in PS fit when other covariates are complete.
# NOTE: original C7 comment said "excluded from PS fit"; corrected 2026-06-04.
# ==============================================================================

WHITE_CONCEPT_IDS   = {8527, 38003598}
BLACK_CONCEPT_IDS   = {8516, 38003599}   # 38003599 = CDC code active in BioMe
ASIAN_CONCEPT_IDS   = {8515, 38003601}
UNKNOWN_CONCEPT_IDS = {0, 8552, 8657}    # 0=no matching concept; null treated same
# Other = RACE_CONCEPT_ID nonmissing and not in any above set

# ==============================================================================
# PS MODEL COVARIATES [unchanged from v3; bl_cva excluded]
# ==============================================================================

PS_COVARIATES_FIXED = [
    "age_at_index",
    "female",
    "race_black_r",
    "race_asian_r",
    "race_other_r",
    "race_unknown_r",  # EHR missingness/coding category; retained as separate indicator
    "hispanic",
    "bl_diabetes",
    "bl_ckd",
    "bl_heart_failure",
    "bl_cad_mi",
    "bl_afib",
    "bl_pad",
    "bl_tia",
]
# index_year categorical dummies added dynamically (ref = min year)
# bl_cva EXCLUDED: concurrent-index CVA biases PS; see README_design_changes.md
# race_white_r: reference category (omitted from model)
# race_unknown_r: included as PS covariate — models EHR missingness/coding;
#   do NOT exclude these persons from PS fit.

# ==============================================================================
# OUTCOME LABELS AND ROLES [C2/C3 CORRECTED]
# Primary outcomes: stroke_s1, b4_mci
# Secondary outcomes: b4, stroke_s2
# ==============================================================================

OUTCOME_ORDER = [
    # (internal_name, role,        label_for_table)
    ("stroke_s1",  "primary",   "Acute ischemic stroke"),
    ("b4_mci",     "primary",   "Probable dementia + mild cognitive impairment"),
    ("b4",         "secondary", "Probable dementia alone"),
    ("stroke_s2",  "secondary", "Ischemic stroke + transient ischemic attack"),
]

OUTCOME_LABELS    = {r[0]: r[2] for r in OUTCOME_ORDER}
OUTCOME_ROLES     = {r[0]: r[1] for r in OUTCOME_ORDER}
PRIMARY_OUTCOMES  = [r[0] for r in OUTCOME_ORDER if r[1] == "primary"]
SECONDARY_OUTCOMES = [r[0] for r in OUTCOME_ORDER if r[1] == "secondary"]

# ==============================================================================
# MULTIPLE TESTING [C8]
# Bonferroni and BH-FDR applied across PRIMARY outcomes only (N=2).
# Secondary outcomes: raw p-values only.
# ==============================================================================

MT_PRIMARY_ALPHA = 0.05
MT_BONFERRONI_K  = len(PRIMARY_OUTCOMES)   # = 2

# ==============================================================================
# LEGACY SINGLE-CONCEPT ALIASES (kept for backward compatibility; use
# B4_MCI_SNOMED_IDS, STROKE_S1_SNOMED_IDS, TIA_SNOMED_IDS above instead)
# ==============================================================================
MCI_OMOP_CONCEPT_ID = 439795   # G31.84 — part of B4_MCI_SNOMED_IDS[3]
STROKE_S1_OMOP      = 443454   # I63.x  — part of STROKE_S1_SNOMED_IDS[0]
TIA_OMOP            = 373503   # G45.9/ICD9-435 — TIA_SNOMED_IDS[0]
# 381591 EXCLUDED from all definitions: stenosis/ill-defined CVD

