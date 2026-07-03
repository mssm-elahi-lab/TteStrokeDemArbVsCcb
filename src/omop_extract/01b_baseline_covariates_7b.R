# =========================================================
# SECTION 7b — BASELINE COVARIATES (CRASH-PROOF VERSION)
#
# Zero database queries. Zero collect() calls.
# Uses ONLY objects already in memory from Sections 1–7a:
#   raw_antihypertensive_exposures, cohort_spine_raw,
#   raw_conditions, first_index, treated_person_ids,
#   icd_to_snomed_map, source_code_map, drug_concept_lookup,
#   project_dir, VISIBLE_DIR (if available)
#
# Labs / smoking / visits / baseline meds → empty typed
# placeholders. Handle those in separate scripts later.
# =========================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
  library(arrow)
})

cat("\n========================================\n")
cat("  SECTION 7b: BASELINE COVARIATES\n")
cat("========================================\n")
cat(format(Sys.time()), "\n\n")

# ─────────────────────────────────────────────────────────
# 7b-0)  STANDARDISE PERSON_ID → character everywhere
#         Prevents join failures from integer/character mismatch.
# ─────────────────────────────────────────────────────────
cat("Standardising PERSON_ID types...\n")

first_index                    <- first_index                    %>% mutate(PERSON_ID = as.character(PERSON_ID))
cohort_spine_raw               <- cohort_spine_raw               %>% mutate(PERSON_ID = as.character(PERSON_ID))
raw_conditions                 <- raw_conditions                 %>% mutate(PERSON_ID = as.character(PERSON_ID))
raw_antihypertensive_exposures <- raw_antihypertensive_exposures %>% mutate(PERSON_ID = as.character(PERSON_ID))
treated_person_ids             <- as.character(unique(treated_person_ids))

cat("  treated_person_ids:", length(treated_person_ids), "\n")
cat("  first_index rows:  ", nrow(first_index), "\n")
cat("  raw_conditions rows:", nrow(raw_conditions), "\n")

# ─────────────────────────────────────────────────────────
# Diagnostic warnings collector
# ─────────────────────────────────────────────────────────
diagnostic_warnings <- tibble(section = character(), warning_msg = character())

add_warning <- function(section, msg) {
  cat("  WARNING [", section, "]:", msg, "\n")
  diagnostic_warnings <<- bind_rows(
    diagnostic_warnings,
    tibble(section = section, warning_msg = msg)
  )
}

# ─────────────────────────────────────────────────────────
# 7b-1)  BUILD INDEX SPINE
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-1: Building index_spine ---\n")

index_spine <- first_index %>%
  filter(PERSON_ID %in% treated_person_ids) %>%
  mutate(index_date = as.Date(index_date))

cat("  index_spine rows:", nrow(index_spine), "\n")

# ─────────────────────────────────────────────────────────
# 7b-2)  CONDITION COVARIATES FROM raw_conditions (all local)
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-2: Building condition covariates ---\n")

# Confirm SNOMED vectors exist; rebuild if missing (should exist from 7a)
if (!exists("HYPERTENSION_SNOMED")) {
  add_warning("7b-2", "SNOMED vectors not found in session — rebuilding from icd_to_snomed_map")

  get_snomed_ids <- function(icd_ids) {
    unique(icd_to_snomed_map$STANDARD_CONCEPT_ID[
      icd_to_snomed_map$ICD_CONCEPT_ID %in% icd_ids
    ])
  }

  HYPERTENSION_SNOMED  <- get_snomed_ids(c(35207668L, 1569120L, 1569121L, 1569122L, 1569124L,
                                           44833556L, 44832366L, 44832367L, 44827780L, 44832370L))
  DEMENTIA_SNOMED      <- get_snomed_ids(c(45533052L, 1568087L, 1568088L, 35207114L, 1568293L,
                                           1568295L, 35207360L, 45547730L, 45595932L, 45553737L,
                                           45534454L, 45553736L, 44824105L, 44821814L, 44826536L,
                                           44827645L, 44834585L, 44832709L))
  STROKE_SNOMED        <- get_snomed_ids(c(1569184L, 1569190L, 1569191L, 1569193L, 45548032L,
                                           1569218L, 1569221L, 1569225L, 1569227L, 1569228L,
                                           44820872L, 44835946L, 44835947L, 44820873L, 44824253L,
                                           44820875L, 44835952L, 44832388L, 44831252L))
  DIABETES_SNOMED      <- get_snomed_ids(c(1567940L, 1567956L, 1567972L, 44833365L))
  CKD_SNOMED           <- get_snomed_ids(c(1571486L, 44830172L))
  HEART_FAILURE_SNOMED <- get_snomed_ids(c(1569178L, 44824250L))
  CAD_MI_SNOMED        <- get_snomed_ids(c(1569125L, 1569126L, 1569130L, 1569133L,
                                           44832372L, 44834725L, 44835930L, 44827784L))
  AFIB_SNOMED          <- get_snomed_ids(c(1569170L, 44824248L, 44821957L, 44820868L))
  PAD_SNOMED           <- get_snomed_ids(c(1569271L, 1569324L, 44825446L, 44826654L))
  CVA_SNOMED           <- get_snomed_ids(c(1569193L, 45548032L))
  TIA_SNOMED           <- get_snomed_ids(c(1568360L, 1568361L, 44820875L))
}

# Pre-index conditions only
pre_index_conditions <- raw_conditions %>%
  inner_join(index_spine %>% select(PERSON_ID, index_date), by = "PERSON_ID") %>%
  mutate(CONDITION_START_DATE = as.Date(CONDITION_START_DATE)) %>%
  filter(!is.na(CONDITION_START_DATE), CONDITION_START_DATE < index_date)

cat("  Pre-index condition rows:     ", nrow(pre_index_conditions), "\n")
cat("  Pre-index condition persons:  ", n_distinct(pre_index_conditions$PERSON_ID), "\n")

# Helper: binary flag from SNOMED concept IDs
make_cond_flag <- function(snomed_ids, flag_name) {
  if (length(snomed_ids) == 0) {
    add_warning("7b-2", paste0("No SNOMED IDs resolved for ", flag_name, " — all zeros"))
    return(tibble(PERSON_ID = character(), !!flag_name := integer()))
  }
  pre_index_conditions %>%
    filter(CONDITION_CONCEPT_ID %in% snomed_ids) %>%
    distinct(PERSON_ID) %>%
    mutate(!!flag_name := 1L)
}

flag_diabetes <- make_cond_flag(DIABETES_SNOMED,      "diabetes_baseline")
flag_ckd      <- make_cond_flag(CKD_SNOMED,           "ckd_baseline")
flag_hf       <- make_cond_flag(HEART_FAILURE_SNOMED, "hf_baseline")
flag_cad_mi   <- make_cond_flag(CAD_MI_SNOMED,        "cad_mi_baseline")
flag_afib     <- make_cond_flag(AFIB_SNOMED,          "afib_baseline")
flag_pad      <- make_cond_flag(PAD_SNOMED,           "pad_baseline")
flag_cva      <- make_cond_flag(CVA_SNOMED,           "cva_baseline")
flag_tia      <- make_cond_flag(TIA_SNOMED,           "tia_baseline")
flag_htn      <- make_cond_flag(HYPERTENSION_SNOMED,  "hypertension_baseline")
flag_dementia <- make_cond_flag(DEMENTIA_SNOMED,      "dementia_baseline")

condition_covariates <- index_spine %>%
  select(PERSON_ID) %>%
  left_join(flag_diabetes, by = "PERSON_ID") %>%
  left_join(flag_ckd,      by = "PERSON_ID") %>%
  left_join(flag_hf,       by = "PERSON_ID") %>%
  left_join(flag_cad_mi,   by = "PERSON_ID") %>%
  left_join(flag_afib,     by = "PERSON_ID") %>%
  left_join(flag_pad,      by = "PERSON_ID") %>%
  left_join(flag_cva,      by = "PERSON_ID") %>%
  left_join(flag_tia,      by = "PERSON_ID") %>%
  left_join(flag_htn,      by = "PERSON_ID") %>%
  left_join(flag_dementia, by = "PERSON_ID") %>%
  mutate(across(where(is.integer), ~ replace_na(.x, 0L)))

cat("  condition_covariates rows:", nrow(condition_covariates), "\n")
cat("  Condition flag sums:\n")
print(colSums(condition_covariates %>% select(-PERSON_ID)))

# ─────────────────────────────────────────────────────────
# 7b-3 through 7b-6)  EMPTY TYPED PLACEHOLDERS
#   No DB queries. No collect() calls.
#   Labs, smoking, visits, and baseline meds will be extracted
#   in separate smaller scripts once this baseline file exists.
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-3 to 7b-6: Creating empty placeholders (no DB queries) ---\n")

add_warning("7b-3", "MEASUREMENT not queried — raw_baseline_measurements is empty placeholder")
add_warning("7b-4", "OBSERVATION not queried — raw_baseline_smoking is empty placeholder")
add_warning("7b-5", "VISIT_OCCURRENCE not queried — raw_baseline_visits is empty placeholder")
add_warning("7b-6", "DRUG_EXPOSURE not re-queried — raw_baseline_medications is empty placeholder")

# Measurements
raw_baseline_measurements <- tibble(
  PERSON_ID = character(), MEASUREMENT_CONCEPT_ID = integer(),
  MEASUREMENT_DATE = as.Date(character()), VALUE_AS_NUMBER = numeric(),
  UNIT_CONCEPT_ID = integer(), RANGE_LOW = numeric(), RANGE_HIGH = numeric()
)
cleaned_baseline_measurements <- raw_baseline_measurements %>%
  mutate(lab_class = character())
lab_concept_map <- tibble(
  MEASUREMENT_CONCEPT_ID = integer(), CONCEPT_NAME = character(), lab_class = character()
)
measurement_unit_diagnostics <- tibble(
  MEASUREMENT_CONCEPT_ID = integer(), UNIT_CONCEPT_ID = integer(), n = integer()
)

# Smoking
raw_baseline_smoking <- tibble(
  PERSON_ID = character(), OBSERVATION_CONCEPT_ID = integer(),
  OBSERVATION_DATE = as.Date(character())
)
smoking_status_patient <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(smoking_status = NA_character_)

# Visits
raw_baseline_visits <- tibble(
  PERSON_ID = character(), VISIT_OCCURRENCE_ID = integer(),
  VISIT_CONCEPT_ID = integer(), VISIT_START_DATE = as.Date(character()),
  VISIT_END_DATE = as.Date(character())
)

# Medications
raw_baseline_medications <- tibble(
  PERSON_ID = character(), DRUG_CONCEPT_ID = integer(),
  DRUG_SOURCE_CONCEPT_ID = integer(),
  DRUG_EXPOSURE_START_DATE = as.Date(character()), DAYS_SUPPLY = integer()
)
med_ingredient_concepts <- tibble(
  CONCEPT_ID = integer(), CONCEPT_NAME = character(),
  VOCABULARY_ID = character(), CONCEPT_CLASS_ID = character(), med_class = character()
)
med_descendant_concepts <- tibble(
  ancestor_concept_id = integer(), descendant_concept_id = integer()
)
med_class_diagnostics <- tibble(
  med_class = character(), n_persons = integer(), n_records = integer()
)
top_drug_source_values_by_class <- tibble(
  DRUG_SOURCE_CONCEPT_ID = integer(), med_class = character(), n = integer()
)

# Medication covariate columns — all zeros, one per person
medication_covariates <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(
    statin_baseline     = 0L,
    ace_baseline        = 0L,
    metformin_baseline  = 0L,
    betablocker_baseline = 0L,
    anticoag_baseline   = 0L,
    aspirin_baseline    = 0L
  )

cat("  All placeholder objects created.\n")

# ─────────────────────────────────────────────────────────
# 7b-7)  APOE — not available in standard OMOP
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-7: APOE (empty placeholder) ---\n")

add_warning("7b-7", "APOE genotype not extractable from standard OMOP — empty outputs created")

raw_apoe_genotype <- tibble(
  PERSON_ID   = character(),
  APOE_STATUS = character()
)
apoe_patient_summary <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(apoe_e4_carrier = NA_integer_, apoe_status = NA_character_)

# ─────────────────────────────────────────────────────────
# 7b-8)  ASSEMBLE baseline_covariates_patient
#         One row per treated person. Condition flags from raw_conditions;
#         everything else zero-filled or NA.
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-8: Assembling baseline_covariates_patient ---\n")

baseline_covariates_patient <- index_spine %>%
  select(PERSON_ID, index_date) %>%
  left_join(condition_covariates,  by = "PERSON_ID") %>%
  left_join(medication_covariates, by = "PERSON_ID") %>%
  left_join(smoking_status_patient %>% select(PERSON_ID, smoking_status),
            by = "PERSON_ID") %>%
  mutate(across(where(is.integer), ~ replace_na(.x, 0L)))

cat("  baseline_covariates_patient rows:", nrow(baseline_covariates_patient), "\n")
cat("  baseline_covariates_patient cols:", ncol(baseline_covariates_patient), "\n")
cat("  Column names:\n")
print(names(baseline_covariates_patient))

# ─────────────────────────────────────────────────────────
# 7b-9)  COVARIATE COVERAGE REPORT
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-9: Covariate coverage ---\n")

binary_cols <- baseline_covariates_patient %>%
  select(where(is.integer)) %>%
  names()

covariate_coverage <- tibble(
  covariate     = binary_cols,
  n_with_flag   = sapply(binary_cols, function(col)
    sum(baseline_covariates_patient[[col]] == 1L, na.rm = TRUE)),
  pct_of_cohort = sapply(binary_cols, function(col)
    round(100 * mean(baseline_covariates_patient[[col]] == 1L, na.rm = TRUE), 2))
)

cat("\n  Covariate coverage summary:\n")
print(covariate_coverage, n = Inf)

# ─────────────────────────────────────────────────────────
# 7b-10) SAVE ALL OUTPUTS
# ─────────────────────────────────────────────────────────
cat("\n--- 7b-10: Saving parquet files ---\n")

save_pq <- function(df, fname) {
  path <- file.path(project_dir, fname)
  tryCatch({
    write_parquet(df, path)
    cat("  [OK]", fname, "(", nrow(df), "rows )\n")
  }, error = function(e) {
    cat("  [FAIL]", fname, "—", e$message, "\n")
  })
}

# New 7b outputs
save_pq(raw_baseline_measurements,      "raw_baseline_measurements.parquet")
save_pq(cleaned_baseline_measurements,  "cleaned_baseline_measurements.parquet")
save_pq(baseline_covariates_patient,    "baseline_covariates_patient.parquet")
save_pq(raw_baseline_medications,       "raw_baseline_medications.parquet")
save_pq(raw_baseline_visits,            "raw_baseline_visits.parquet")
save_pq(covariate_coverage,             "covariate_coverage.parquet")
save_pq(lab_concept_map,                "lab_concept_map.parquet")
save_pq(measurement_unit_diagnostics,   "measurement_unit_diagnostics.parquet")
save_pq(med_ingredient_concepts,        "med_ingredient_concepts.parquet")
save_pq(med_descendant_concepts,        "med_descendant_concepts.parquet")
save_pq(med_class_diagnostics,          "med_class_diagnostics.parquet")
save_pq(top_drug_source_values_by_class,"top_drug_source_values_by_class.parquet")
save_pq(raw_baseline_smoking,           "raw_baseline_smoking.parquet")
save_pq(smoking_status_patient,         "smoking_status_patient.parquet")
save_pq(diagnostic_warnings,            "diagnostic_warnings.parquet")
save_pq(raw_apoe_genotype,              "raw_apoe_genotype.parquet")
save_pq(apoe_patient_summary,           "apoe_patient_summary.parquet")

# Re-save previously successful objects (guards against session loss)
save_pq(raw_antihypertensive_exposures, "raw_antihypertensive_exposures.parquet")
save_pq(cohort_spine_raw,               "cohort_spine_raw.parquet")
save_pq(raw_conditions,                 "raw_conditions.parquet")
save_pq(icd_to_snomed_map,              "icd_to_snomed_map.parquet")
save_pq(source_code_map,                "source_code_map.parquet")
save_pq(drug_concept_lookup,            "drug_concept_lookup.parquet")

# File inventory
cat("\nFinal parquet inventory:\n")
pf <- list.files(project_dir, pattern = "\\.parquet$", full.names = TRUE)
fi <- file.info(pf)
print(data.frame(
  file    = basename(pf),
  size_mb = round(fi$size / 1e6, 2),
  row.names = NULL
))

# ─────────────────────────────────────────────────────────
# 7b-11) ZIP (best-effort — failure does not abort)
# ─────────────────────────────────────────────────────────
zip_output_dir <- if (exists("VISIBLE_DIR") && nchar(VISIBLE_DIR) > 0 &&
                       dir.exists(VISIBLE_DIR)) VISIBLE_DIR else project_dir
zip_path <- file.path(zip_output_dir, "tte_extracts_7b.zip")

tryCatch({
  zip(zipfile = zip_path, files = pf)
  cat("\nZip created at:", zip_path, "\n")
}, error = function(e) {
  cat("\nWARNING: ZIP creation failed:", e$message, "\n")
  cat("Parquet files are in:", project_dir, "\n")
})

cat("\n========================================\n")
cat("  SECTION 7b COMPLETE\n")
cat(format(Sys.time()), "\n")
cat("  Warnings logged:", nrow(diagnostic_warnings), "\n")
cat("========================================\n")

if (exists("rstudioapi") && rstudioapi::isAvailable()) {
  rstudioapi::filesPaneNavigate(project_dir)
}
