"""Ported from Scripts/Core/01_build_indexed_cohort_run01_20260531.py.

Build the indexed cohort:
  - ARB initiation vs chronic outpatient DHP-CCB initiation
  - First-line chronic antihypertensive washout (ACEi, ARB, DHP-CCB, thiazide)
  - Hypertensive adults aged min_age-max_age
  - Prevalent neurovascular/cognitive exclusion [C4]:
      Cognitive: B4_MCI (probable dementia + MCI) on/before index
      Vascular:  stroke S1 (harmonized AIS) on/before index
      TIA prior: NOT excluded (covariate only)
  - >=min_followup_days potential post-index follow-up using clinical_end_date [C5]
  - Race coding: mutually exclusive 5-category [C7]
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from src.config import Config


def get_snomed_ids(icd_ids, icd_map: pd.DataFrame) -> list[int]:
    return (
        icd_map.loc[icd_map["ICD_CONCEPT_ID"].isin(icd_ids), "STANDARD_CONCEPT_ID"]
        .dropna()
        .astype(int)
        .unique()
        .tolist()
    )


def first_condition_date(conditions_df: pd.DataFrame, snomed_ids) -> pd.DataFrame:
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


def add_comorbidity_flag(df: pd.DataFrame, conditions_df: pd.DataFrame, snomed_ids, flag_col: str) -> pd.DataFrame:
    dates = first_condition_date(conditions_df, snomed_ids).rename(
        columns={"condition_first_date": f"{flag_col}_date"}
    )
    df = df.merge(dates, on="PERSON_ID", how="left")
    df[flag_col] = df[f"{flag_col}_date"].notna() & (df[f"{flag_col}_date"] <= df["index_date"])
    return df.drop(columns=[f"{flag_col}_date"])


def _assign_group(row) -> str | None:
    if pd.notna(row["arb_index_date"]) and row["arb_index_date"] == row["index_date"]:
        return "ARB"
    if pd.notna(row["ccb_index_date"]) and row["ccb_index_date"] == row["index_date"]:
        return "CCB"
    return None


def run(config: Config) -> None:
    analysis = config.analysis
    cohort_cfg = analysis.cohort
    drug_classes = analysis.drug_classes
    clinical = analysis.clinical

    washout_days = cohort_cfg.washout_days
    min_age = cohort_cfg.min_age
    max_age = cohort_cfg.max_age
    min_followup_days = cohort_cfg.min_followup_days
    arb_ingredients = drug_classes.arb_ingredients
    dhp_ccb_index = drug_classes.dhp_ccb_index
    dhp_ccb_washout = drug_classes.dhp_ccb_washout
    acei_washout = drug_classes.acei_washout
    thiazide_washout = drug_classes.thiazide_washout

    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"build_cohort_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(f"{__name__}.run")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))

    analysis_end_date = pd.Timestamp(analysis.end_date)

    logger.info("=" * 70)
    logger.info("TTE Analysis — build_cohort")
    logger.info(f"ANALYSIS_END_DATE: {analysis.end_date}")
    logger.info(f"WASHOUT_DAYS: {washout_days}")
    logger.info(f"MIN_FOLLOWUP_DAYS: {min_followup_days}")
    logger.info(f"INCLUDE_ADDITIONAL_DHP_INDEX: {drug_classes.include_additional_dhp_index}")
    logger.info(f"ARB ingredients: {list(arb_ingredients)}")
    logger.info(f"DHP_CCB_INDEX: {list(dhp_ccb_index)}")
    logger.info(f"DHP_CCB_WASHOUT: {list(dhp_ccb_washout)}")
    logger.info(f"ACEI_WASHOUT: {list(acei_washout)}")
    logger.info(f"THIAZIDE_WASHOUT: {list(thiazide_washout)}")
    logger.info("=" * 70)

    # ==========================================================================
    # LOAD DATA
    # ==========================================================================

    logger.info("Loading parquets...")
    drugs = pd.read_parquet(config.paths.antihypertensive_exposures)
    spine = pd.read_parquet(config.paths.spine)
    cond = pd.read_parquet(config.paths.conditions)
    icd_map = pd.read_parquet(config.paths.icd_map)
    meds = pd.read_parquet(config.paths.baseline_medications)  # for ACEi washout

    logger.info(f"  raw_antihypertensive_exposures (v4): {len(drugs):,} rows")
    logger.info(f"  cohort_spine_raw (v4):               {len(spine):,} rows")
    logger.info(f"  raw_conditions:                      {len(cond):,} rows")
    logger.info(f"  icd_to_snomed_map:                   {len(icd_map):,} rows")
    logger.info(f"  raw_baseline_medications (v4):       {len(meds):,} rows")

    drugs["DRUG_EXPOSURE_START_DATE"] = pd.to_datetime(drugs["DRUG_EXPOSURE_START_DATE"], errors="coerce")
    cond["CONDITION_START_DATE"] = pd.to_datetime(cond["CONDITION_START_DATE"], errors="coerce")
    spine["XTN_BIRTH_DATE"] = pd.to_datetime(spine["XTN_BIRTH_DATE"], errors="coerce")
    spine["obs_start_date"] = pd.to_datetime(spine["obs_start_date"], errors="coerce")
    spine["obs_end_date"] = pd.to_datetime(spine["obs_end_date"], errors="coerce")
    spine["XTN_DEATH_DATE"] = pd.to_datetime(spine["XTN_DEATH_DATE"], errors="coerce")
    meds["DRUG_EXPOSURE_START_DATE"] = pd.to_datetime(meds["DRUG_EXPOSURE_START_DATE"], errors="coerce")

    # ==========================================================================
    # RESOLVE SNOMED CONCEPT ID SETS
    # ==========================================================================

    logger.info("Resolving SNOMED concept ID sets from ICD map...")
    hypertension_snomed = get_snomed_ids(clinical.comorbidities.hypertension_icd_ids, icd_map)
    diabetes_snomed = get_snomed_ids(clinical.comorbidities.diabetes_icd_ids, icd_map)
    ckd_snomed = get_snomed_ids(clinical.comorbidities.ckd_icd_ids, icd_map)
    heart_failure_snomed = get_snomed_ids(clinical.comorbidities.heart_failure_icd_ids, icd_map)
    cad_mi_snomed = get_snomed_ids(clinical.comorbidities.cad_mi_icd_ids, icd_map)
    afib_snomed = get_snomed_ids(clinical.comorbidities.afib_icd_ids, icd_map)
    pad_snomed = get_snomed_ids(clinical.comorbidities.pad_icd_ids, icd_map)
    tia_snomed = get_snomed_ids(clinical.vascular.tia_icd_ids, icd_map)  # covariate

    # [C4] Prevalent exclusion uses direct SNOMED IDs from config (pre-verified)
    dem_excl_snomed = list(clinical.prevalent_cognitive_excl_snomeds)  # B4_MCI
    stroke_excl_snomed = list(clinical.prevalent_vascular_excl_snomeds)  # S1
    logger.info(f"  Cognitive prevalent exclusion SNOMEDs (B4_MCI): {dem_excl_snomed}")
    logger.info(f"  Vascular prevalent exclusion SNOMEDs (stroke S1): {stroke_excl_snomed}")

    # ==========================================================================
    # STEP 1: IDENTIFY INDEX DATES — FIRST ARB OR DHP-CCB DISPENSING
    # First-drug-wins ITT design: exposure group determined by the first dispensing
    # of any approved ARB or DHP-CCB ingredient.
    # ==========================================================================

    logger.info("Identifying index dates (first ARB or DHP-CCB dispensing)...")

    arb_exp = drugs[drugs["drug_name"].isin(arb_ingredients)].copy()
    ccb_exp = drugs[drugs["drug_name"].isin(dhp_ccb_index)].copy()

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

    drug_dates["exposure_group"] = drug_dates.apply(_assign_group, axis=1)
    drug_dates = drug_dates.dropna(subset=["exposure_group", "index_date"])

    logger.info(f"  ARB first dispensing: {first_arb['PERSON_ID'].nunique():,} persons")
    logger.info(f"  CCB first dispensing: {first_ccb['PERSON_ID'].nunique():,} persons")
    logger.info(f"  ARB index (first-drug-wins): {(drug_dates['exposure_group'] == 'ARB').sum():,}")
    logger.info(f"  CCB index (first-drug-wins): {(drug_dates['exposure_group'] == 'CCB').sum():,}")

    # Cohort flow checkpoint 1
    n_cf1_raw = len(drug_dates)
    n_cf1_arb = (drug_dates["exposure_group"] == "ARB").sum()
    n_cf1_ccb = (drug_dates["exposure_group"] == "CCB").sum()

    # ==========================================================================
    # STEP 2: BUILD BASE COHORT (join with spine)
    # ==========================================================================

    logger.info("Joining with spine...")
    base = spine.merge(drug_dates, on="PERSON_ID", how="inner")
    base["age_at_index"] = ((base["index_date"] - base["XTN_BIRTH_DATE"]).dt.days / 365.25).apply(np.floor)

    # follow-up days computed after clinical_end_date extension in STEP 7B (before >=365d filter)

    # ==========================================================================
    # STEP 3: AGE FILTER
    # ==========================================================================

    base = base[base["age_at_index"].notna() & base["age_at_index"].between(min_age, max_age)].copy()
    n_cf2_age = len(base)
    logger.info(f"After age {min_age}–{max_age} filter: {n_cf2_age:,}")

    # ==========================================================================
    # STEP 4: HYPERTENSION REQUIREMENT
    # ==========================================================================

    htn_dates = first_condition_date(cond, hypertension_snomed).rename(
        columns={"condition_first_date": "htn_first_date"}
    )
    base = base.merge(htn_dates, on="PERSON_ID", how="left")
    base["has_htn_dx"] = base["htn_first_date"].notna() & (base["htn_first_date"] <= base["index_date"])
    base = base[base["has_htn_dx"]].copy()
    n_cf3_htn = len(base)
    logger.info(f"After hypertension requirement: {n_cf3_htn:,}")

    # ==========================================================================
    # STEP 5: PREVALENT OUTCOME EXCLUSION (global, mirrors v3)
    # Dementia/stroke: first_date on or before index_date (inclusive)
    # ==========================================================================

    # [C4] CORRECTED: use B4_MCI SNOMEDs for cognitive, stroke S1 SNOMEDs for vascular
    # Prior TIA NOT excluded from cohort — TIA is a PS covariate only.
    dem_excl_dates = first_condition_date(cond, dem_excl_snomed).rename(
        columns={"condition_first_date": "b4mci_first_date"}
    )
    str_excl_dates = first_condition_date(cond, stroke_excl_snomed).rename(
        columns={"condition_first_date": "stroke_s1_first_date"}
    )
    base = base.merge(dem_excl_dates, on="PERSON_ID", how="left")
    base = base.merge(str_excl_dates, on="PERSON_ID", how="left")
    base["b4mci_first_date"] = pd.to_datetime(base["b4mci_first_date"], errors="coerce")
    base["stroke_s1_first_date"] = pd.to_datetime(base["stroke_s1_first_date"], errors="coerce")

    excl_cognitive = base["b4mci_first_date"].notna() & (base["b4mci_first_date"] <= base["index_date"])
    excl_vascular = base["stroke_s1_first_date"].notna() & (base["stroke_s1_first_date"] <= base["index_date"])

    n_excl_cognitive = excl_cognitive.sum()
    n_excl_vascular = excl_vascular.sum()
    n_excl_either = (excl_cognitive | excl_vascular).sum()
    logger.info(f"  Prevalent B4_MCI exclusion: {n_excl_cognitive:,}")
    logger.info(f"  Prevalent stroke S1 exclusion: {n_excl_vascular:,}")
    logger.info(f"  Total prevalent exclusion (any): {n_excl_either:,}")
    base = base[~excl_cognitive & ~excl_vascular].copy()
    n_cf4_prev = len(base)
    logger.info(f"After prevalent cognitive/vascular exclusion: {n_cf4_prev:,}")

    # ==========================================================================
    # STEP 6: SAME-DAY MULTI-CLASS EXCLUSION
    # ==========================================================================

    excl_sameday = (
        base["arb_index_date"].notna()
        & base["ccb_index_date"].notna()
        & (base["arb_index_date"] == base["ccb_index_date"])
    )
    base = base[~excl_sameday].copy()
    n_cf5_sameday = len(base)
    logger.info(f"After same-day dual initiator exclusion: {n_cf5_sameday:,}")

    # ==========================================================================
    # STEP 7: FIRST-LINE CHRONIC ANTIHYPERTENSIVE WASHOUT
    # Exclude any dispensing of ACEi, ARB, DHP-CCB, or thiazide in the washout
    # window prior to (but not including) the index date.
    # ==========================================================================

    logger.info(f"Applying {washout_days}-day first-line antihypertensive washout...")

    washout_arb = set(arb_ingredients)
    washout_ccb = set(dhp_ccb_washout)
    washout_thiaz = set(thiazide_washout)

    washout_drugs = drugs[drugs["drug_name"].isin(washout_arb | washout_ccb | washout_thiaz)].copy()

    base_index = base[["PERSON_ID", "index_date"]].copy()

    washout_check = washout_drugs.merge(base_index, on="PERSON_ID", how="inner")
    washout_check["days_before_index"] = (
        washout_check["index_date"] - washout_check["DRUG_EXPOSURE_START_DATE"]
    ).dt.days

    prior_exposure = washout_check[
        (washout_check["days_before_index"] > 0) & (washout_check["days_before_index"] <= washout_days)
    ]["PERSON_ID"].unique()

    washout_acei_meds = meds[meds["ingredient_name"].isin(set(acei_washout))].copy()
    washout_acei_check = washout_acei_meds.merge(base_index, on="PERSON_ID", how="inner")
    washout_acei_check["days_before_index"] = (
        washout_acei_check["index_date"] - washout_acei_check["DRUG_EXPOSURE_START_DATE"]
    ).dt.days
    prior_acei = washout_acei_check[
        (washout_acei_check["days_before_index"] > 0) & (washout_acei_check["days_before_index"] <= washout_days)
    ]["PERSON_ID"].unique()

    all_washout_excl = set(prior_exposure) | set(prior_acei)
    base = base[~base["PERSON_ID"].isin(all_washout_excl)].copy()
    n_cf6_washout = len(base)
    logger.info(f"  Excluded by ARB/CCB/thiazide washout: {len(prior_exposure):,}")
    logger.info(f"  Excluded by ACEi washout: {len(prior_acei):,}")
    logger.info(f"  Total excluded by washout (union): {len(all_washout_excl):,}")
    logger.info(f"After washout: {n_cf6_washout:,}")

    # ==========================================================================
    # STEP 7B: CLINICAL END DATE AND CENSOR DATE [C5]
    # Computed BEFORE the follow-up filter so the filter uses correct follow-up.
    #
    # clinical_end_date = max(obs_end_date, last_condition_date, last_drug_date,
    #                         last_baseline_med_date), capped at ANALYSIS_END_DATE
    # censor_date = min(clinical_end_date, XTN_DEATH_DATE if present, ANALYSIS_END_DATE)
    # ==========================================================================

    logger.info("Computing clinical_end_date [C5] before follow-up filter...")

    last_cond = (
        cond.groupby("PERSON_ID")["CONDITION_START_DATE"]
        .max()
        .reset_index()
        .rename(columns={"CONDITION_START_DATE": "last_condition_date"})
    )
    last_cond["last_condition_date"] = pd.to_datetime(last_cond["last_condition_date"], errors="coerce")

    last_drug = (
        drugs.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"]
        .max()
        .reset_index()
        .rename(columns={"DRUG_EXPOSURE_START_DATE": "last_drug_date"})
    )

    last_med = (
        meds.groupby("PERSON_ID")["DRUG_EXPOSURE_START_DATE"]
        .max()
        .reset_index()
        .rename(columns={"DRUG_EXPOSURE_START_DATE": "last_baseline_med_date"})
    )

    base = base.merge(last_cond, on="PERSON_ID", how="left")
    base = base.merge(last_drug, on="PERSON_ID", how="left")
    base = base.merge(last_med, on="PERSON_ID", how="left")

    base["clinical_end_date"] = (
        base[["obs_end_date", "last_condition_date", "last_drug_date", "last_baseline_med_date"]]
        .max(axis=1)
        .clip(upper=analysis_end_date)
    )

    base["_death_filled"] = base["XTN_DEATH_DATE"].fillna(base["clinical_end_date"])
    base["censor_date"] = base[["clinical_end_date", "_death_filled"]].min(axis=1).clip(upper=analysis_end_date)
    base.drop(columns=["_death_filled"], inplace=True)
    base["followup_available_days"] = (base["censor_date"] - base["index_date"]).dt.days

    n_extended = (base["clinical_end_date"] > base["obs_end_date"]).sum()
    n_death_shortens = (base["XTN_DEATH_DATE"].notna() & (base["XTN_DEATH_DATE"] < base["clinical_end_date"])).sum()
    n_death_after_clin = (
        base["XTN_DEATH_DATE"].notna() & (base["XTN_DEATH_DATE"] > base["clinical_end_date"])
    ).sum()
    n_censor_clinical = (base["censor_date"] == base["clinical_end_date"]).sum()
    n_censor_death = (base["XTN_DEATH_DATE"].notna() & (base["censor_date"] == base["XTN_DEATH_DATE"])).sum()
    n_obs_short_ok = (
        ((base["obs_end_date"] - base["index_date"]).dt.days < min_followup_days)
        & (base["followup_available_days"] >= min_followup_days)
    ).sum()
    logger.info(f"  N clinical_end_date > obs_end_date:     {n_extended:,}")
    logger.info(f"  N death shortens clinical_end_date:     {n_death_shortens:,}")
    logger.info(f"  N death after clinical_end_date:        {n_death_after_clin:,}")
    logger.info(f"  N censor_date == clinical_end_date:     {n_censor_clinical:,}")
    logger.info(f"  N censor_date == XTN_DEATH_DATE:        {n_censor_death:,}")
    logger.info(f"  N obs_end<{min_followup_days}d but censor_date>={min_followup_days}d: {n_obs_short_ok:,}")
    logger.info(f"  Median followup_available_days:         {base['followup_available_days'].median():.0f}")

    # ==========================================================================
    # STEP 8: MINIMUM POTENTIAL POST-INDEX FOLLOW-UP (censor_date-based)
    # ==========================================================================

    base = base[base["followup_available_days"] >= min_followup_days].copy()
    n_cf7_followup = len(base)
    logger.info(f"After >={min_followup_days}d follow-up requirement: {n_cf7_followup:,}")

    # ==========================================================================
    # STEP 9: ADD COMORBIDITY FLAGS (baseline, mirrors v3)
    # ==========================================================================

    logger.info("Adding comorbidity flags...")
    for col, snomed in [
        ("bl_diabetes", diabetes_snomed),
        ("bl_ckd", ckd_snomed),
        ("bl_heart_failure", heart_failure_snomed),
        ("bl_cad_mi", cad_mi_snomed),
        ("bl_afib", afib_snomed),
        ("bl_pad", pad_snomed),
        ("bl_tia", tia_snomed),
    ]:
        base = add_comorbidity_flag(base, cond, snomed, col)

    # ==========================================================================
    # STEP 10: DEMOGRAPHICS
    # ==========================================================================

    # Female flag (OMOP: 8532 = Female, 8507 = Male)
    base["female"] = np.where(
        base["GENDER_CONCEPT_ID"] == 8532,
        1.0,
        np.where(base["GENDER_CONCEPT_ID"] == 8507, 0.0, np.nan),
    )

    # Race indicators
    # NOTE: Black/AA via CDC concept 38003599 (OMOP standard 8516 is all-zero in this extract)
    #       Verified in v3 race audit. Do not use RACE_CONCEPT_ID == 8516 for Black.
    race_data = spine[["PERSON_ID", "RACE_CONCEPT_ID"]].copy()

    # [C7] Use RACE_CONCEPT_ID directly from spine (confirmed in v4 cohort_spine_raw)
    base = base.merge(race_data, on="PERSON_ID", how="left", suffixes=("", "_spine"))

    # [C7] CORRECTED: mutually exclusive race categories. Unknown != Other.
    white_ids = clinical.race_coding.white_concept_ids
    black_ids = clinical.race_coding.black_concept_ids
    asian_ids = clinical.race_coding.asian_concept_ids
    unknown_ids = clinical.race_coding.unknown_concept_ids

    rc = base["RACE_CONCEPT_ID"].fillna(0).astype(int)
    base["race_white_r"] = rc.isin(white_ids).astype(float)
    base["race_black_r"] = rc.isin(black_ids).astype(float)
    base["race_asian_r"] = rc.isin(asian_ids).astype(float)
    base["race_unknown_r"] = (rc.isin(unknown_ids) | (base["RACE_CONCEPT_ID"].isna())).astype(float)
    base["race_other_r"] = (
        ~rc.isin(white_ids | black_ids | asian_ids | unknown_ids) & base["RACE_CONCEPT_ID"].notna()
    ).astype(float)

    assert (
        base[["race_white_r", "race_black_r", "race_asian_r", "race_other_r", "race_unknown_r"]].sum(axis=1) == 1
    ).all(), "Race categories are not mutually exclusive — check concept ID sets"

    n_unknown_race = int(base["race_unknown_r"].sum())
    logger.info(
        f"  race_unknown_r: {n_unknown_race:,} persons (retained in cohort; included in PS fit as a covariate)"
    )

    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)
    race_audit_path = output_core / "race_coding_audit.md"
    with open(race_audit_path, "w") as f:
        f.write("# Race Coding Audit\n")
        f.write(f"Generated: {datetime.today().strftime('%Y-%m-%d')}\n\n")
        f.write("## Race Category Counts by Arm (mutually exclusive; White=reference)\n\n")
        for arm in ["ARB", "CCB"]:
            sub = base[base["exposure_group"] == arm]
            f.write(f"### {arm}\n")
            f.write(f"- White:           {int(sub['race_white_r'].sum()):,}\n")
            f.write(f"- Black/Afr.Am.:   {int(sub['race_black_r'].sum()):,}\n")
            f.write(f"- Asian:           {int(sub['race_asian_r'].sum()):,}\n")
            f.write(f"- Other:           {int(sub['race_other_r'].sum()):,}\n")
            f.write(f"- Unknown/Unmapped:{int(sub['race_unknown_r'].sum()):,}\n")
            f.write(f"- TOTAL:           {len(sub):,}\n\n")
        f.write("## Notes\n")
        f.write(f"- White concept IDs: {sorted(white_ids)}\n")
        f.write(f"- Black concept IDs: {sorted(black_ids)}\n")
        f.write(f"- Asian concept IDs: {sorted(asian_ids)}\n")
        f.write(f"- Unknown concept IDs: {sorted(unknown_ids)} + null\n")
        f.write("- Other: nonmissing RACE_CONCEPT_ID not in any above set\n")
        f.write(f"- Unknown persons included in PS fit as race_unknown_r covariate: N={n_unknown_race:,}\n")
    logger.info(f"Race coding audit written: {race_audit_path}")

    # Hispanic (OMOP: 38003563 = Hispanic)
    base["hispanic"] = (base["ETHNICITY_CONCEPT_ID"] == 38003563).astype(float)

    # Index year (for PS model categorical dummies)
    base["index_year"] = base["index_date"].dt.year

    # ==========================================================================
    # STEP 11: LOG COHORT FLOW SUMMARY
    # ==========================================================================

    logger.info("")
    logger.info("=== COHORT FLOW SUMMARY ===")
    logger.info(f"  Raw ARB/CCB index identified:             {n_cf1_raw:,}  (ARB={n_cf1_arb:,}, CCB={n_cf1_ccb:,})")
    logger.info(f"  After age {min_age}–{max_age}:                       {n_cf2_age:,}")
    logger.info(f"  After hypertension requirement:           {n_cf3_htn:,}")
    logger.info(f"  After prevalent dementia/stroke exclusion:{n_cf4_prev:,}")
    logger.info(f"  After same-day dual initiator exclusion:  {n_cf5_sameday:,}")
    logger.info(f"  After 180d first-line washout:            {n_cf6_washout:,}")
    logger.info(f"  After >={min_followup_days}d follow-up (censor_date-based): {n_cf7_followup:,}")
    n_pre_ps_arb = int((base["exposure_group"] == "ARB").sum())
    n_pre_ps_ccb = int((base["exposure_group"] == "CCB").sum())
    logger.info(f"  ARB (pre-PS trim): {n_pre_ps_arb:,}")
    logger.info(f"  CCB (pre-PS trim): {n_pre_ps_ccb:,}")
    logger.info("")

    # ==========================================================================
    # STEP 11B: PERSIST COHORT-FLOW CHECKPOINTS (for Figure 1 / cohort_flow)
    # Structured record of the pre-PS-trim CONSORT stages; the PS-trim step is
    # appended by compute_outcomes. Numbers are exactly those logged above.
    # ==========================================================================

    flow_stages = pd.DataFrame(
        [
            ("raw_index", "First ARB or DHP-CCB dispensing identified", n_cf1_raw, n_cf1_arb, n_cf1_ccb),
            ("age", f"Aged {min_age}-{max_age} at index", n_cf2_age, None, None),
            ("hypertension", "Hypertension diagnosis on/before index", n_cf3_htn, None, None),
            ("prevalent_excl", "No prevalent dementia/MCI or stroke", n_cf4_prev, None, None),
            ("sameday_excl", "No same-day dual initiation", n_cf5_sameday, None, None),
            ("washout", f"{washout_days}-day first-line antihypertensive washout", n_cf6_washout, None, None),
            (
                "followup",
                f">={min_followup_days}d potential follow-up (pre-PS-trim)",
                n_cf7_followup,
                n_pre_ps_arb,
                n_pre_ps_ccb,
            ),
        ],
        columns=["stage", "description", "n_total", "n_arb", "n_ccb"],
    )
    flow_path = output_core / "cohort_flow_stages.csv"
    flow_stages.to_csv(flow_path, index=False)
    logger.info(f"Saved cohort-flow checkpoints: {flow_path}")

    # ==========================================================================
    # SAVE OUTPUT
    # ==========================================================================

    out_path = output_core / "indexed_cohort.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.to_parquet(out_path, index=False)
    logger.info(f"Saved: {out_path}  ({len(base):,} rows)")
    logger.info("build_cohort complete.")

    for handler in logger.handlers:
        handler.close()


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
