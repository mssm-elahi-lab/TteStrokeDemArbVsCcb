"""
01_build_indexed_cohort_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

Build the indexed cohort for the run01 design:
  - ARB initiation vs chronic outpatient DHP-CCB initiation
  - First-line chronic antihypertensive 180-day washout:
      ACE inhibitors, ARBs, DHP-CCBs, thiazide/thiazide-like diuretics
  - Hypertensive adults aged 40-70
  - Prevalent neurovascular/cognitive exclusion [C4 CORRECTED]:
      Cognitive: B4_MCI (probable dementia + MCI) on/before index
      Vascular:  stroke S1 (harmonized AIS) on/before index
      TIA prior: NOT excluded (covariate only)
  - >=365 days potential post-index follow-up using clinical_end_date [C5]
  - Race coding: mutually exclusive 5-category [C7]

CORRECTIONS (2026-05-31):
  [C1]  V4_CONDITIONS and V4_ICD_MAP from v4 extract (run01_config.py)
  [C4]  Prevalent exclusion uses B4_MCI_SNOMED_IDS (cognitive) and
        STROKE_S1_SNOMED_IDS (vascular); prior TIA NOT excluded.
  [C5]  clinical_end_date = max(obs_end, last_condition_date, last_drug_date,
        last_baseline_med_date); censor = min(death, clinical_end, 2025-12-31)
  [C6]  ACEi washout uses DRUG_EXPOSURE_START_DATE (date-based, 1-180d pre-index)
  [C7]  Race: White/Black/Asian/Other/Unknown mutually exclusive;
        Unknown/Unmapped retained and modeled as separate race_unknown_r
        covariate in PS; race audit written.

Frozen v3 template: src/may_2026/02b_build_indexed_cohort.py
Data source: v4 expanded extract (AIRMS/most recent extract/)

Output:
  AIRMS/results/final_candidate_runs_20260531/run01_v4_core_design_deathcensor/
    run01_indexed_cohort.parquet
    logs/01_build_indexed_cohort_run01.log

Author: (initials)
Date:   2026-05-31
"""

# ==============================================================================
# DRY-RUN GUARD — must be set to True before production execution
# ==============================================================================

RUN_FULL_ANALYSIS: bool = False

if not RUN_FULL_ANALYSIS:
    raise RuntimeError(
        "Dry-run protected script. Review preflight outputs and set "
        "RUN_FULL_ANALYSIS = True before execution."
    )

# ==============================================================================
# IMPORTS
# ==============================================================================

import sys
import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Import run01 ingredient definitions
sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg  # noqa: E402

ANALYSIS_END_DATE_STR        = cfg.ANALYSIS_END_DATE_STR
WASHOUT_DAYS                 = cfg.WASHOUT_DAYS
MIN_AGE                      = cfg.MIN_AGE
MAX_AGE                      = cfg.MAX_AGE
MIN_FOLLOWUP_DAYS            = cfg.MIN_FOLLOWUP_DAYS
ARB_INGREDIENTS              = cfg.ARB_INGREDIENTS
DHP_CCB_INDEX                = cfg.DHP_CCB_INDEX
DHP_CCB_WASHOUT              = cfg.DHP_CCB_WASHOUT
ACEI_WASHOUT                 = cfg.ACEI_WASHOUT
THIAZIDE_WASHOUT             = cfg.THIAZIDE_WASHOUT
INCLUDE_ADDITIONAL_DHP_INDEX = cfg.INCLUDE_ADDITIONAL_DHP_INDEX
V4_ANTIHTN_EXPOSURES         = cfg.V4_ANTIHTN_EXPOSURES
V4_SPINE                     = cfg.V4_SPINE
V4_CONDITIONS                = cfg.V4_CONDITIONS      # v4 [C1]
V4_ICD_MAP                   = cfg.V4_ICD_MAP          # v4 [C1]
V4_BASELINE_MEDS             = cfg.V4_BASELINE_MEDS
RUN01_INDEXED_COHORT         = cfg.RUN01_INDEXED_COHORT
LOG_DIR                      = cfg.LOG_DIR

# Cognitive outcome SNOMED IDs (direct; no ICD join needed) [C4]
PREVALENT_COGNITIVE_EXCL_SNOMEDS = cfg.PREVALENT_COGNITIVE_EXCL_SNOMEDS  # B4_MCI
B4_MCI_ICD_IDS               = cfg.B4_MCI_ICD_IDS
B4_ICD_IDS                   = cfg.B4_ICD_IDS

# Vascular outcome SNOMED IDs (direct; no ICD join needed) [C4]
PREVALENT_VASCULAR_EXCL_SNOMEDS = cfg.PREVALENT_VASCULAR_EXCL_SNOMEDS  # stroke S1
STROKE_S1_ICD_IDS            = cfg.STROKE_S1_ICD_IDS
TIA_ICD_IDS                  = cfg.TIA_ICD_IDS
BL_TIA_ICD_IDS               = cfg.BL_TIA_ICD_IDS

# Comorbidity ICD concept IDs
HYPERTENSION_ICD_IDS         = cfg.HYPERTENSION_ICD_IDS
DIABETES_ICD_IDS             = cfg.DIABETES_ICD_IDS
CKD_ICD_IDS                  = cfg.CKD_ICD_IDS
HEART_FAILURE_ICD_IDS        = cfg.HEART_FAILURE_ICD_IDS
CAD_MI_ICD_IDS               = cfg.CAD_MI_ICD_IDS
AFIB_ICD_IDS                 = cfg.AFIB_ICD_IDS
PAD_ICD_IDS                  = cfg.PAD_ICD_IDS

# Race coding concept ID sets [C7]
WHITE_CONCEPT_IDS            = cfg.WHITE_CONCEPT_IDS
BLACK_CONCEPT_IDS            = cfg.BLACK_CONCEPT_IDS
ASIAN_CONCEPT_IDS            = cfg.ASIAN_CONCEPT_IDS
UNKNOWN_CONCEPT_IDS          = cfg.UNKNOWN_CONCEPT_IDS

# ==============================================================================
# SETUP
# ==============================================================================

LOG_DIR.mkdir(parents=True, exist_ok=True)
log_path = LOG_DIR / f"01_build_indexed_cohort_run01_{datetime.today().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

ANALYSIS_END_DATE = pd.Timestamp(ANALYSIS_END_DATE_STR)

log.info("=" * 70)
log.info("run01_v4_core_design_deathcensor — 01_build_indexed_cohort_run01")
log.info(f"ANALYSIS_END_DATE: {ANALYSIS_END_DATE_STR}")
log.info(f"WASHOUT_DAYS: {WASHOUT_DAYS}")
log.info(f"MIN_FOLLOWUP_DAYS: {MIN_FOLLOWUP_DAYS}")
log.info(f"INCLUDE_ADDITIONAL_DHP_INDEX: {INCLUDE_ADDITIONAL_DHP_INDEX}")
log.info(f"ARB ingredients: {ARB_INGREDIENTS}")
log.info(f"DHP_CCB_INDEX: {DHP_CCB_INDEX}")
log.info(f"DHP_CCB_WASHOUT: {DHP_CCB_WASHOUT}")
log.info(f"ACEI_WASHOUT: {ACEI_WASHOUT}")
log.info(f"THIAZIDE_WASHOUT: {THIAZIDE_WASHOUT}")
log.info("=" * 70)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def get_snomed_ids(icd_ids, icd_map):
    return (
        icd_map.loc[icd_map["ICD_CONCEPT_ID"].isin(icd_ids), "STANDARD_CONCEPT_ID"]
        .dropna()
        .astype(int)
        .unique()
        .tolist()
    )


def first_condition_date(conditions_df, snomed_ids):
    sub = conditions_df[conditions_df["CONDITION_CONCEPT_ID"].isin(snomed_ids)].dropna(
        subset=["CONDITION_START_DATE"]
    )
    if len(sub) == 0:
        return pd.DataFrame(columns=["PERSON_ID", "condition_first_date"])
    return (
        sub.groupby("PERSON_ID")["CONDITION_START_DATE"]
        .min()
        .reset_index()
        .rename(columns={"CONDITION_START_DATE": "condition_first_date"})
    )


def add_comorbidity_flag(df, conditions_df, snomed_ids, flag_col):
    dates = first_condition_date(conditions_df, snomed_ids).rename(
        columns={"condition_first_date": f"{flag_col}_date"}
    )
    df = df.merge(dates, on="PERSON_ID", how="left")
    df[flag_col] = df[f"{flag_col}_date"].notna() & (
        df[f"{flag_col}_date"] <= df["index_date"]
    )
    return df.drop(columns=[f"{flag_col}_date"])


# ==============================================================================
# LOAD DATA
# ==============================================================================

log.info("Loading parquets...")
drugs   = pd.read_parquet(V4_ANTIHTN_EXPOSURES)
spine   = pd.read_parquet(V4_SPINE)
cond    = pd.read_parquet(V4_CONDITIONS)
icd_map = pd.read_parquet(V4_ICD_MAP)
meds    = pd.read_parquet(V4_BASELINE_MEDS)   # for ACEi washout

log.info(f"  raw_antihypertensive_exposures (v4): {len(drugs):,} rows")
log.info(f"  cohort_spine_raw (v4):               {len(spine):,} rows")
log.info(f"  raw_conditions:                      {len(cond):,} rows")
log.info(f"  icd_to_snomed_map:                   {len(icd_map):,} rows")
log.info(f"  raw_baseline_medications (v4):       {len(meds):,} rows")

# Parse dates
drugs["DRUG_EXPOSURE_START_DATE"] = pd.to_datetime(drugs["DRUG_EXPOSURE_START_DATE"], errors="coerce")
cond["CONDITION_START_DATE"]      = pd.to_datetime(cond["CONDITION_START_DATE"],      errors="coerce")
spine["XTN_BIRTH_DATE"]           = pd.to_datetime(spine["XTN_BIRTH_DATE"],           errors="coerce")
spine["obs_start_date"]           = pd.to_datetime(spine["obs_start_date"],           errors="coerce")
spine["obs_end_date"]             = pd.to_datetime(spine["obs_end_date"],             errors="coerce")
spine["XTN_DEATH_DATE"]           = pd.to_datetime(spine["XTN_DEATH_DATE"],           errors="coerce")
meds["DRUG_EXPOSURE_START_DATE"]  = pd.to_datetime(meds["DRUG_EXPOSURE_START_DATE"],  errors="coerce")

# ==============================================================================
# RESOLVE SNOMED CONCEPT ID SETS
# ==============================================================================

log.info("Resolving SNOMED concept ID sets from ICD map...")
# Comorbidities resolved via ICD map
HYPERTENSION_SNOMED  = get_snomed_ids(HYPERTENSION_ICD_IDS,  icd_map)
DIABETES_SNOMED      = get_snomed_ids(DIABETES_ICD_IDS,       icd_map)
CKD_SNOMED           = get_snomed_ids(CKD_ICD_IDS,            icd_map)
HEART_FAILURE_SNOMED = get_snomed_ids(HEART_FAILURE_ICD_IDS,  icd_map)
CAD_MI_SNOMED        = get_snomed_ids(CAD_MI_ICD_IDS,         icd_map)
AFIB_SNOMED          = get_snomed_ids(AFIB_ICD_IDS,           icd_map)
PAD_SNOMED           = get_snomed_ids(PAD_ICD_IDS,            icd_map)
TIA_SNOMED           = get_snomed_ids(TIA_ICD_IDS,            icd_map)  # covariate

# [C4] Prevalent exclusion uses direct SNOMED IDs from config (pre-verified)
# These are not resolved via ICD map — they are the confirmed SNOMED concept IDs.
DEM_EXCL_SNOMED   = list(PREVALENT_COGNITIVE_EXCL_SNOMEDS)   # B4_MCI: 378419,443605,4182210,439795,4009705
STROKE_EXCL_SNOMED = list(PREVALENT_VASCULAR_EXCL_SNOMEDS)   # S1: 443454, 372924
log.info(f"  Cognitive prevalent exclusion SNOMEDs (B4_MCI): {DEM_EXCL_SNOMED}")
log.info(f"  Vascular prevalent exclusion SNOMEDs (stroke S1): {STROKE_EXCL_SNOMED}")

# ==============================================================================
# STEP 1: IDENTIFY INDEX DATES — FIRST ARB OR DHP-CCB DISPENSING
# First-drug-wins ITT design: exposure group determined by the first dispensing
# of any approved ARB or DHP-CCB ingredient.
# ==============================================================================

log.info("Identifying index dates (first ARB or DHP-CCB dispensing)...")

arb_exp = drugs[drugs["drug_name"].isin(ARB_INGREDIENTS)].copy()
ccb_exp = drugs[drugs["drug_name"].isin(DHP_CCB_INDEX)].copy()

first_arb = (
    arb_exp.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"]
    .min()
    .reset_index()
    .rename(columns={"DRUG_EXPOSURE_START_DATE": "arb_index_date"})
)
first_ccb = (
    ccb_exp.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"]
    .min()
    .reset_index()
    .rename(columns={"DRUG_EXPOSURE_START_DATE": "ccb_index_date"})
)

drug_dates = first_arb.merge(first_ccb, on="PERSON_ID", how="outer")
drug_dates["index_date"] = drug_dates[["arb_index_date", "ccb_index_date"]].min(axis=1)
drug_dates = drug_dates.dropna(subset=["index_date"])

def assign_group(row):
    if pd.notna(row["arb_index_date"]) and row["arb_index_date"] == row["index_date"]:
        return "ARB"
    if pd.notna(row["ccb_index_date"]) and row["ccb_index_date"] == row["index_date"]:
        return "CCB"
    return None

drug_dates["exposure_group"] = drug_dates.apply(assign_group, axis=1)
drug_dates = drug_dates.dropna(subset=["exposure_group", "index_date"])

log.info(f"  ARB first dispensing: {first_arb['PERSON_ID'].nunique():,} persons")
log.info(f"  CCB first dispensing: {first_ccb['PERSON_ID'].nunique():,} persons")
log.info(f"  ARB index (first-drug-wins): {(drug_dates['exposure_group']=='ARB').sum():,}")
log.info(f"  CCB index (first-drug-wins): {(drug_dates['exposure_group']=='CCB').sum():,}")

# Cohort flow checkpoint 1
n_cf1_raw = len(drug_dates)
n_cf1_arb = (drug_dates["exposure_group"] == "ARB").sum()
n_cf1_ccb = (drug_dates["exposure_group"] == "CCB").sum()

# ==============================================================================
# STEP 2: BUILD BASE COHORT (join with spine)
# ==============================================================================

log.info("Joining with spine...")
base = spine.merge(drug_dates, on="PERSON_ID", how="inner")
base["age_at_index"] = (
    (base["index_date"] - base["XTN_BIRTH_DATE"]).dt.days / 365.25
).apply(np.floor)

# follow-up days computed after clinical_end_date extension in STEP 7B (before >=365d filter)

# ==============================================================================
# STEP 3: AGE FILTER (40–70)
# ==============================================================================

base = base[base["age_at_index"].notna() & base["age_at_index"].between(MIN_AGE, MAX_AGE)].copy()
n_cf2_age = len(base)
log.info(f"After age {MIN_AGE}–{MAX_AGE} filter: {n_cf2_age:,}")

# ==============================================================================
# STEP 4: HYPERTENSION REQUIREMENT
# ==============================================================================

htn_dates = first_condition_date(cond, HYPERTENSION_SNOMED).rename(
    columns={"condition_first_date": "htn_first_date"}
)
base = base.merge(htn_dates, on="PERSON_ID", how="left")
base["has_htn_dx"] = base["htn_first_date"].notna() & (base["htn_first_date"] <= base["index_date"])
base = base[base["has_htn_dx"]].copy()
n_cf3_htn = len(base)
log.info(f"After hypertension requirement: {n_cf3_htn:,}")

# ==============================================================================
# STEP 5: PREVALENT OUTCOME EXCLUSION (global, mirrors v3)
# Dementia: first_date strictly BEFORE index_date (exclusive — v3 bug C6 fixed)
# Stroke:   first_date <= index_date (inclusive)
# ==============================================================================

# [C4] CORRECTED: use B4_MCI SNOMEDs for cognitive, stroke S1 SNOMEDs for vascular
# Prior TIA NOT excluded from cohort — TIA is a PS covariate only.
dem_excl_dates = first_condition_date(cond, DEM_EXCL_SNOMED).rename(
    columns={"condition_first_date": "b4mci_first_date"}
)
str_excl_dates = first_condition_date(cond, STROKE_EXCL_SNOMED).rename(
    columns={"condition_first_date": "stroke_s1_first_date"}
)
base = base.merge(dem_excl_dates, on="PERSON_ID", how="left")
base = base.merge(str_excl_dates, on="PERSON_ID", how="left")
base["b4mci_first_date"]     = pd.to_datetime(base["b4mci_first_date"],     errors="coerce")
base["stroke_s1_first_date"] = pd.to_datetime(base["stroke_s1_first_date"], errors="coerce")

# Cognitive (B4_MCI): first date on or before index_date (inclusive)
excl_cognitive = base["b4mci_first_date"].notna() & (base["b4mci_first_date"] <= base["index_date"])
# Vascular (stroke S1): first date on or before index_date (inclusive)
excl_vascular  = base["stroke_s1_first_date"].notna() & (base["stroke_s1_first_date"] <= base["index_date"])

n_excl_cognitive = excl_cognitive.sum()
n_excl_vascular  = excl_vascular.sum()
n_excl_either    = (excl_cognitive | excl_vascular).sum()
log.info(f"  Prevalent B4_MCI exclusion: {n_excl_cognitive:,}")
log.info(f"  Prevalent stroke S1 exclusion: {n_excl_vascular:,}")
log.info(f"  Total prevalent exclusion (any): {n_excl_either:,}")
base = base[~excl_cognitive & ~excl_vascular].copy()
n_cf4_prev = len(base)
log.info(f"After prevalent cognitive/vascular exclusion: {n_cf4_prev:,}")

# ==============================================================================
# STEP 6: SAME-DAY MULTI-CLASS EXCLUSION
# Exclude persons who initiated both ARB and DHP-CCB on the same day
# ==============================================================================

excl_sameday = (
    base["arb_index_date"].notna() &
    base["ccb_index_date"].notna() &
    (base["arb_index_date"] == base["ccb_index_date"])
)
base = base[~excl_sameday].copy()
n_cf5_sameday = len(base)
log.info(f"After same-day dual initiator exclusion: {n_cf5_sameday:,}")

# ==============================================================================
# STEP 7: FIRST-LINE CHRONIC ANTIHYPERTENSIVE 180-DAY WASHOUT
# Exclude any dispensing of ACEi, ARB, DHP-CCB, or thiazide in the 180 days
# prior to (but not including) the index date.
#
# ACEi source: raw_baseline_medications.parquet (med_class = 'ace_inhibitor')
# Other classes: raw_antihypertensive_exposures.parquet
# ==============================================================================

log.info(f"Applying {WASHOUT_DAYS}-day first-line antihypertensive washout...")

washout_arb     = set(ARB_INGREDIENTS)
washout_ccb     = set(DHP_CCB_WASHOUT)
washout_thiaz   = set(THIAZIDE_WASHOUT)
washout_acei    = set(ACEI_WASHOUT)

# From antihypertensive exposures: ARBs, CCBs, thiazides
washout_drugs = drugs[drugs["drug_name"].isin(washout_arb | washout_ccb | washout_thiaz)].copy()

base_index = base[["PERSON_ID", "index_date"]].copy()

# Join index dates to washout drug records
washout_check = washout_drugs.merge(base_index, on="PERSON_ID", how="inner")
washout_check["days_before_index"] = (
    washout_check["index_date"] - washout_check["DRUG_EXPOSURE_START_DATE"]
).dt.days

# Prior exposure: 1 to WASHOUT_DAYS days before index (exclude index day itself)
prior_exposure = washout_check[
    (washout_check["days_before_index"] > 0) &
    (washout_check["days_before_index"] <= WASHOUT_DAYS)
]["PERSON_ID"].unique()

# From baseline medications: ACEi
washout_acei_meds = meds[meds["ingredient_name"].isin(washout_acei)].copy()
washout_acei_check = washout_acei_meds.merge(base_index, on="PERSON_ID", how="inner")
washout_acei_check["days_before_index"] = (
    washout_acei_check["index_date"] - washout_acei_check["DRUG_EXPOSURE_START_DATE"]
).dt.days
prior_acei = washout_acei_check[
    (washout_acei_check["days_before_index"] > 0) &
    (washout_acei_check["days_before_index"] <= WASHOUT_DAYS)
]["PERSON_ID"].unique()

all_washout_excl = set(prior_exposure) | set(prior_acei)
base = base[~base["PERSON_ID"].isin(all_washout_excl)].copy()
n_cf6_washout = len(base)
log.info(f"  Excluded by ARB/CCB/thiazide washout: {len(prior_exposure):,}")
log.info(f"  Excluded by ACEi washout: {len(prior_acei):,}")
log.info(f"  Total excluded by washout (union): {len(all_washout_excl):,}")
log.info(f"After washout: {n_cf6_washout:,}")

# ==============================================================================
# STEP 7B: CLINICAL END DATE AND CENSOR DATE [C5 CORRECTED]
# Computed BEFORE the >=365d filter so the filter uses the correct follow-up.
#
# clinical_end_date = max(obs_end_date, last_condition_date, last_drug_date,
#                         last_baseline_med_date), capped at ANALYSIS_END_DATE
# censor_date = min(clinical_end_date, XTN_DEATH_DATE if present, ANALYSIS_END_DATE)
# followup_available_days = (censor_date - index_date).dt.days
# ==============================================================================

log.info("Computing clinical_end_date [C5] before >=365d filter...")

last_cond = (
    cond.groupby("PERSON_ID")["CONDITION_START_DATE"].max()
    .reset_index()
    .rename(columns={"CONDITION_START_DATE": "last_condition_date"})
)
last_cond["last_condition_date"] = pd.to_datetime(last_cond["last_condition_date"], errors="coerce")

last_drug = (
    drugs.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"].max()
    .reset_index()
    .rename(columns={"DRUG_EXPOSURE_START_DATE": "last_drug_date"})
)

last_med = (
    meds.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"].max()
    .reset_index()
    .rename(columns={"DRUG_EXPOSURE_START_DATE": "last_baseline_med_date"})
)

base = base.merge(last_cond, on="PERSON_ID", how="left")
base = base.merge(last_drug, on="PERSON_ID", how="left")
base = base.merge(last_med,  on="PERSON_ID", how="left")

# clinical_end_date = max of activity dates, vectorized (skipna=True by default)
base["clinical_end_date"] = (
    base[["obs_end_date", "last_condition_date", "last_drug_date", "last_baseline_med_date"]]
    .max(axis=1)
    .clip(upper=ANALYSIS_END_DATE)
)

# censor_date = min(clinical_end_date, XTN_DEATH_DATE if not null, ANALYSIS_END_DATE)
# death_filled: use death if present, else fall back to clinical_end (so min() is well-defined)
base["_death_filled"] = base["XTN_DEATH_DATE"].fillna(base["clinical_end_date"])
base["censor_date"] = (
    base[["clinical_end_date", "_death_filled"]].min(axis=1).clip(upper=ANALYSIS_END_DATE)
)
base.drop(columns=["_death_filled"], inplace=True)
base["followup_available_days"] = (base["censor_date"] - base["index_date"]).dt.days

# Audit counts
n_extended         = (base["clinical_end_date"] > base["obs_end_date"]).sum()
n_death_shortens   = (base["XTN_DEATH_DATE"].notna() & (base["XTN_DEATH_DATE"] < base["clinical_end_date"])).sum()
n_death_after_clin = (base["XTN_DEATH_DATE"].notna() & (base["XTN_DEATH_DATE"] > base["clinical_end_date"])).sum()
n_censor_clinical  = (base["censor_date"] == base["clinical_end_date"]).sum()
n_censor_death     = (base["XTN_DEATH_DATE"].notna() & (base["censor_date"] == base["XTN_DEATH_DATE"])).sum()
n_obs_short_ok     = (
    ((base["obs_end_date"] - base["index_date"]).dt.days < MIN_FOLLOWUP_DAYS) &
    (base["followup_available_days"] >= MIN_FOLLOWUP_DAYS)
).sum()
log.info(f"  N clinical_end_date > obs_end_date:     {n_extended:,}")
log.info(f"  N death shortens clinical_end_date:     {n_death_shortens:,}")
log.info(f"  N death after clinical_end_date:        {n_death_after_clin:,}")
log.info(f"  N censor_date == clinical_end_date:     {n_censor_clinical:,}")
log.info(f"  N censor_date == XTN_DEATH_DATE:        {n_censor_death:,}")
log.info(f"  N obs_end<{MIN_FOLLOWUP_DAYS}d but censor_date>={MIN_FOLLOWUP_DAYS}d: {n_obs_short_ok:,}")
log.info(f"  Median followup_available_days:         {base['followup_available_days'].median():.0f}")

# ==============================================================================
# STEP 8: >=365 DAYS POTENTIAL POST-INDEX FOLLOW-UP (uses censor_date-based days)
# ==============================================================================

base = base[base["followup_available_days"] >= MIN_FOLLOWUP_DAYS].copy()
n_cf7_followup = len(base)
log.info(f"After >={MIN_FOLLOWUP_DAYS}d follow-up requirement: {n_cf7_followup:,}")

# ==============================================================================
# STEP 9: ADD COMORBIDITY FLAGS (baseline, mirrors v3)
# ==============================================================================

log.info("Adding comorbidity flags...")
for col, snomed in [
    ("bl_diabetes",      DIABETES_SNOMED),
    ("bl_ckd",           CKD_SNOMED),
    ("bl_heart_failure", HEART_FAILURE_SNOMED),
    ("bl_cad_mi",        CAD_MI_SNOMED),
    ("bl_afib",          AFIB_SNOMED),
    ("bl_pad",           PAD_SNOMED),
    ("bl_tia",           TIA_SNOMED),
]:
    base = add_comorbidity_flag(base, cond, snomed, col)

# ==============================================================================
# STEP 10: DEMOGRAPHICS
# ==============================================================================

# Female flag (OMOP: 8532 = Female, 8507 = Male)
base["female"] = np.where(
    base["GENDER_CONCEPT_ID"] == 8532, 1.0,
    np.where(base["GENDER_CONCEPT_ID"] == 8507, 0.0, np.nan),
)

# Race indicators
# NOTE: Black/AA via CDC concept 38003599 (OMOP standard 8516 is all-zero in this extract)
#       Verified in v3 race audit. Do not use RACE_CONCEPT_ID == 8516 for Black.
# White reference; unknown excluded from model
race_data = spine[["PERSON_ID", "RACE_CONCEPT_ID"]].copy()

# Merge CDC race supplement if available
# [C7] Use RACE_CONCEPT_ID directly from spine (confirmed in v4 cohort_spine_raw)
base = base.merge(race_data, on="PERSON_ID", how="left", suffixes=("", "_spine"))
# If RACE_CONCEPT_ID already in base from spine join, use it directly

# [C7] CORRECTED: mutually exclusive race categories. Unknown != Other.
# Race concept IDs from run01_config (WHITE_CONCEPT_IDS etc.)
rc = base["RACE_CONCEPT_ID"].fillna(0).astype(int)
base["race_white_r"]   = rc.isin(WHITE_CONCEPT_IDS).astype(float)
base["race_black_r"]   = rc.isin(BLACK_CONCEPT_IDS).astype(float)
base["race_asian_r"]   = rc.isin(ASIAN_CONCEPT_IDS).astype(float)
base["race_unknown_r"] = (
    rc.isin(UNKNOWN_CONCEPT_IDS) | (base["RACE_CONCEPT_ID"].isna())
).astype(float)
# Other: nonmissing race concept not in White/Black/Asian/Unknown (mutually exclusive)
base["race_other_r"] = (
    ~rc.isin(WHITE_CONCEPT_IDS | BLACK_CONCEPT_IDS | ASIAN_CONCEPT_IDS | UNKNOWN_CONCEPT_IDS) &
    base["RACE_CONCEPT_ID"].notna()
).astype(float)

# Sanity check: categories must sum to 1 per person
assert (base[["race_white_r","race_black_r","race_asian_r","race_other_r","race_unknown_r"]].sum(axis=1) == 1).all(), \
    "Race categories are not mutually exclusive — check concept ID sets"

# Unknown/Unmapped race: retained in cohort AND included in PS fit as a separate
# covariate (race_unknown_r). PS_COVARIATES_FIXED in run01_config.py includes it.
n_unknown_race = int(base["race_unknown_r"].sum())
log.info(
    f"  race_unknown_r: {n_unknown_race:,} persons "
    "(retained in cohort; included in PS fit as a covariate)"
)

# Race audit output (written when script runs)
_race_audit = base.groupby(["exposure_group", "race_white_r","race_black_r",
                             "race_asian_r","race_other_r","race_unknown_r"]).size()
race_audit_path = cfg.OUT_DIR / "run01_race_coding_audit.md"
with open(race_audit_path, "w") as _f:
    _f.write("# run01 Race Coding Audit\n")
    _f.write(f"Generated: {datetime.today().strftime('%Y-%m-%d')}\n\n")
    _f.write("## Race Category Counts by Arm (mutually exclusive; White=reference)\n\n")
    for arm in ["ARB", "CCB"]:
        sub = base[base["exposure_group"] == arm]
        _f.write(f"### {arm}\n")
        _f.write(f"- White:           {int(sub['race_white_r'].sum()):,}\n")
        _f.write(f"- Black/Afr.Am.:   {int(sub['race_black_r'].sum()):,}\n")
        _f.write(f"- Asian:           {int(sub['race_asian_r'].sum()):,}\n")
        _f.write(f"- Other:           {int(sub['race_other_r'].sum()):,}\n")
        _f.write(f"- Unknown/Unmapped:{int(sub['race_unknown_r'].sum()):,}\n")
        _f.write(f"- TOTAL:           {len(sub):,}\n\n")
    _f.write("## Notes\n")
    _f.write(f"- White concept IDs: {sorted(WHITE_CONCEPT_IDS)}\n")
    _f.write(f"- Black concept IDs: {sorted(BLACK_CONCEPT_IDS)}\n")
    _f.write(f"- Asian concept IDs: {sorted(ASIAN_CONCEPT_IDS)}\n")
    _f.write(f"- Unknown concept IDs: {sorted(UNKNOWN_CONCEPT_IDS)} + null\n")
    _f.write(f"- Other: nonmissing RACE_CONCEPT_ID not in any above set\n")
    _f.write(f"- Unknown persons included in PS fit as race_unknown_r covariate: N={n_unknown_race:,}\n")
log.info(f"Race coding audit written: {race_audit_path}")

# Hispanic (OMOP: 38003563 = Hispanic)
base["hispanic"] = (base["ETHNICITY_CONCEPT_ID"] == 38003563).astype(float)

# Index year (for PS model categorical dummies)
base["index_year"] = base["index_date"].dt.year

# ==============================================================================
# STEP 11: LOG COHORT FLOW SUMMARY
# ==============================================================================

log.info("")
log.info("=== COHORT FLOW SUMMARY ===")
log.info(f"  Raw ARB/CCB index identified:             {n_cf1_raw:,}  (ARB={n_cf1_arb:,}, CCB={n_cf1_ccb:,})")
log.info(f"  After age {MIN_AGE}–{MAX_AGE}:                       {n_cf2_age:,}")
log.info(f"  After hypertension requirement:           {n_cf3_htn:,}")
log.info(f"  After prevalent dementia/stroke exclusion:{n_cf4_prev:,}")
log.info(f"  After same-day dual initiator exclusion:  {n_cf5_sameday:,}")
log.info(f"  After 180d first-line washout:            {n_cf6_washout:,}")
log.info(f"  After >={MIN_FOLLOWUP_DAYS}d follow-up (censor_date-based): {n_cf7_followup:,}")
log.info(f"  ARB (pre-PS trim): {(base['exposure_group']=='ARB').sum():,}")
log.info(f"  CCB (pre-PS trim): {(base['exposure_group']=='CCB').sum():,}")
log.info("")

# ==============================================================================
# SAVE OUTPUT
# ==============================================================================

out_path = RUN01_INDEXED_COHORT
out_path.parent.mkdir(parents=True, exist_ok=True)
base.to_parquet(out_path, index=False)
log.info(f"Saved: {out_path}  ({len(base):,} rows)")
log.info("01_build_indexed_cohort_run01 complete.")
