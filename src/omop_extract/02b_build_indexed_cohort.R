library(dplyr)
library(arrow)

# =========================================================
# LOAD PARQUETS
# =========================================================
export_dir  <- "/Users/akarshsharma/Desktop/tte-project/data/results/v2rstudio-export"
project_dir <- "/Users/akarshsharma/Desktop/tte-project/AIRMS"
dir.create(project_dir, showWarnings = FALSE, recursive = TRUE)

raw_antihypertensive_exposures <- read_parquet(file.path(export_dir, "raw_antihypertensive_exposures.parquet"))
cohort_spine_raw               <- read_parquet(file.path(export_dir, "cohort_spine_raw.parquet"))
raw_conditions                 <- read_parquet(file.path(export_dir, "raw_conditions.parquet"))
raw_measurements               <- read_parquet(file.path(export_dir, "raw_measurements.parquet"))
icd_to_snomed_map              <- read_parquet(file.path(export_dir, "icd_to_snomed_map.parquet"))

cat("raw_antihypertensive_exposures:", nrow(raw_antihypertensive_exposures), "rows\n")
cat("cohort_spine_raw:              ", nrow(cohort_spine_raw), "rows\n")
cat("raw_conditions:                ", nrow(raw_conditions), "rows\n")
cat("raw_measurements:              ", nrow(raw_measurements), "rows\n")
cat("icd_to_snomed_map:             ", nrow(icd_to_snomed_map), "rows\n")

# =========================================================
# PARAMETERS
# =========================================================
min_age           <- 40L
max_age           <- 70L
washout_days      <- 180L
analysis_end_date <- Sys.Date()

# =========================================================
# SNOMED CONCEPT ID SETS
# Derived from the ICD->SNOMED map saved in Script 1.
# CONDITION_SOURCE_VALUE is masked and CONDITION_SOURCE_CONCEPT_ID
# uses Epic local IDs in this DB — CONDITION_CONCEPT_ID (SNOMED) is
# the only usable field.
# =========================================================
get_snomed_ids <- function(icd_ids) {
  unique(icd_to_snomed_map$STANDARD_CONCEPT_ID[
    icd_to_snomed_map$ICD_CONCEPT_ID %in% icd_ids
  ])
}

HYPERTENSION_SNOMED  <- get_snomed_ids(c(35207668L, 1569120L, 1569121L, 1569122L, 1569124L,
                                          44833556L, 44832366L, 44832367L, 44827780L, 44832370L))
DEMENTIA_SNOMED      <- get_snomed_ids(c(45533052L, 1568087L, 1568088L, 35207114L,
                                          1568293L, 1568295L, 35207360L, 45547730L,
                                          45595932L, 45553737L, 45534454L, 45553736L,
                                          44824105L, 44821814L, 44826536L, 44827645L,
                                          44834585L, 44832709L))
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

# =========================================================
# HELPERS
# =========================================================

# First condition date per person using CONDITION_CONCEPT_ID (SNOMED)
first_condition_date <- function(conditions_df, snomed_concept_ids,
                                 date_col = "CONDITION_START_DATE") {
  if (is.null(conditions_df) || nrow(conditions_df) == 0) {
    return(tibble(PERSON_ID = integer(0), condition_first_date = as.Date(NA)))
  }
  conditions_df %>%
    filter(CONDITION_CONCEPT_ID %in% snomed_concept_ids,
           !is.na(.data[[date_col]])) %>%
    group_by(PERSON_ID) %>%
    summarise(condition_first_date = min(.data[[date_col]], na.rm = TRUE),
              .groups = "drop")
}

# Add a pre-index comorbidity flag (TRUE = condition present on/before index_date)
add_comorbidity_flag <- function(df, raw_conditions_df, snomed_ids, flag_col) {
  dates <- first_condition_date(raw_conditions_df, snomed_ids) %>%
    rename(!!paste0(flag_col, "_date") := condition_first_date)
  df %>%
    left_join(dates, by = "PERSON_ID") %>%
    mutate(!!flag_col := !is.na(.data[[paste0(flag_col, "_date")]]) &
                          .data[[paste0(flag_col, "_date")]] <= index_date) %>%
    select(-!!paste0(flag_col, "_date"))
}

# =========================================================
# LABEL EXPOSURES BY ARM
# arm column already assigned in Script 1
# =========================================================
raw_antihypertensive_exposures_labeled <- raw_antihypertensive_exposures %>%
  filter(!is.na(arm))

# =========================================================
# FIND FIRST DATE PER DRUG CLASS PER PERSON
# =========================================================
first_per_arm <- raw_antihypertensive_exposures_labeled %>%
  filter(!is.na(DRUG_EXPOSURE_START_DATE)) %>%
  group_by(PERSON_ID, arm) %>%
  summarise(first_date = min(DRUG_EXPOSURE_START_DATE, na.rm = TRUE), .groups = "drop")

first_arb      <- first_per_arm %>% filter(arm == "ARB")      %>% select(PERSON_ID, arb_index_date      = first_date)
first_ccb      <- first_per_arm %>% filter(arm == "CCB")      %>% select(PERSON_ID, ccb_index_date      = first_date)
first_thiazide <- first_per_arm %>% filter(arm == "THIAZIDE") %>% select(PERSON_ID, thiazide_index_date = first_date)

drug_dates <- full_join(first_arb, first_ccb,  by = "PERSON_ID") %>%
             full_join(first_thiazide,          by = "PERSON_ID")

# =========================================================
# ITT EXPOSURE ASSIGNMENT: FIRST DRUG WINS
# Whoever started first determines the arm.
# Patients who started two drugs on the same day are excluded
# by the same-day multi-drug filter below.
# =========================================================
drug_dates <- drug_dates %>%
  mutate(
    index_date = pmin(arb_index_date, ccb_index_date, thiazide_index_date, na.rm = TRUE),
    exposure_group = case_when(
      !is.na(arb_index_date)      & arb_index_date      == index_date ~ "RAS",
      !is.na(ccb_index_date)      & ccb_index_date      == index_date ~ "NON_RAS",
      !is.na(thiazide_index_date) & thiazide_index_date == index_date ~ "NON_RAS",
      TRUE ~ NA_character_
    )
  ) %>%
  filter(!is.na(exposure_group), !is.na(index_date))

n_raw_treated     <- n_distinct(raw_antihypertensive_exposures$PERSON_ID)
n_assigned        <- nrow(drug_dates)
n_assigned_ras    <- sum(drug_dates$exposure_group == "RAS")
n_assigned_nonras <- sum(drug_dates$exposure_group == "NON_RAS")

cat("\nITT Exposure assignment (first drug wins):\n")
cat("  Raw treated persons:", n_raw_treated, "\n")
cat("  Assigned RAS:", n_assigned_ras, "\n")
cat("  Assigned NON_RAS:", n_assigned_nonras, "\n")

# =========================================================
# BUILD BASE COHORT
# =========================================================
base_cohort <- cohort_spine_raw %>%
  inner_join(drug_dates, by = "PERSON_ID") %>%
  mutate(
    age_at_index            = floor(as.numeric(index_date - XTN_BIRTH_DATE) / 365.25),
    followup_available_days = as.integer(obs_end_date - index_date)
  )

cat("\nBase cohort after spine join:", nrow(base_cohort), "\n")

# =========================================================
# INCLUSION: Age 40-70 at index
# =========================================================
base_cohort <- base_cohort %>%
  filter(!is.na(age_at_index), age_at_index >= min_age, age_at_index <= max_age)

n_after_age        <- nrow(base_cohort)
n_after_age_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_age_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After age filter:", n_after_age,
    "(RAS:", n_after_age_ras, "/ NON_RAS:", n_after_age_nonras, ")\n")

# =========================================================
# INCLUSION: Hypertension (non-circular)
# Route 1: SNOMED dx on or before index date
# Route 2: >=2 elevated BP readings on or before index date
# =========================================================
htn_dx_dates <- first_condition_date(raw_conditions, HYPERTENSION_SNOMED)

base_cohort <- base_cohort %>%
  left_join(htn_dx_dates %>% rename(htn_first_date = condition_first_date), by = "PERSON_ID") %>%
  mutate(has_htn_dx = !is.na(htn_first_date) & htn_first_date <= index_date)

# OMOP standard BP concept IDs: 3004249 = Systolic BP, 3012888 = Diastolic BP
cohort_person_ids <- base_cohort$PERSON_ID
htn_bp_pts <- if (nrow(raw_measurements) > 0) {
  raw_measurements %>%
    filter(PERSON_ID %in% cohort_person_ids,
           MEASUREMENT_CONCEPT_ID %in% c(3004249L, 3012888L),
           !is.na(VALUE_AS_NUMBER), !is.na(MEASUREMENT_DATE)) %>%
    mutate(is_elevated = case_when(
      MEASUREMENT_CONCEPT_ID == 3004249L ~ VALUE_AS_NUMBER >= 140,
      MEASUREMENT_CONCEPT_ID == 3012888L ~ VALUE_AS_NUMBER >= 90,
      TRUE ~ FALSE
    )) %>%
    filter(is_elevated) %>%
    inner_join(base_cohort %>% select(PERSON_ID, index_date), by = "PERSON_ID") %>%
    filter(MEASUREMENT_DATE <= index_date) %>%
    count(PERSON_ID, name = "n_elevated_bp") %>%
    filter(n_elevated_bp >= 2L) %>%
    pull(PERSON_ID)
} else { integer(0) }

base_cohort <- base_cohort %>%
  mutate(has_htn_vitals   = PERSON_ID %in% htn_bp_pts,
         has_hypertension = has_htn_dx | has_htn_vitals) %>%
  filter(has_hypertension)

n_after_htn        <- nrow(base_cohort)
n_after_htn_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_htn_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After HTN inclusion:", n_after_htn,
    "(RAS:", n_after_htn_ras, "/ NON_RAS:", n_after_htn_nonras, ")\n")

# =========================================================
# EXCLUSION: Prior ARB/CCB/Thiazide in washout window (new-user design)
# =========================================================
prior_study_drug_pts <- raw_antihypertensive_exposures_labeled %>%
  filter(!is.na(DRUG_EXPOSURE_START_DATE)) %>%
  inner_join(base_cohort %>% select(PERSON_ID, index_date), by = "PERSON_ID") %>%
  filter(DRUG_EXPOSURE_START_DATE >= (index_date - washout_days),
         DRUG_EXPOSURE_START_DATE <  index_date) %>%
  pull(PERSON_ID) %>%
  unique()

base_cohort <- base_cohort %>%
  filter(!PERSON_ID %in% prior_study_drug_pts)

n_after_washout        <- nrow(base_cohort)
n_after_washout_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_washout_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After washout:", n_after_washout,
    "(RAS:", n_after_washout_ras, "/ NON_RAS:", n_after_washout_nonras, ")\n")

# =========================================================
# EXCLUSION: Prior dementia before index
# =========================================================
dementia_dates <- first_condition_date(raw_conditions, DEMENTIA_SNOMED)

base_cohort <- base_cohort %>%
  left_join(dementia_dates %>% rename(dementia_first_date = condition_first_date), by = "PERSON_ID") %>%
  filter(is.na(dementia_first_date) | dementia_first_date >= index_date)

n_after_dementia        <- nrow(base_cohort)
n_after_dementia_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_dementia_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After dementia exclusion:", n_after_dementia,
    "(RAS:", n_after_dementia_ras, "/ NON_RAS:", n_after_dementia_nonras, ")\n")

# =========================================================
# EXCLUSION: Prior stroke before index
# =========================================================
stroke_dates <- first_condition_date(raw_conditions, STROKE_SNOMED)

base_cohort <- base_cohort %>%
  left_join(stroke_dates %>% rename(stroke_first_date = condition_first_date), by = "PERSON_ID") %>%
  filter(is.na(stroke_first_date) | stroke_first_date >= index_date)

n_after_stroke        <- nrow(base_cohort)
n_after_stroke_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_stroke_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After stroke exclusion:", n_after_stroke,
    "(RAS:", n_after_stroke_ras, "/ NON_RAS:", n_after_stroke_nonras, ")\n")

# =========================================================
# EXCLUSION: Same-day multi-drug initiators
# =========================================================
base_cohort <- base_cohort %>%
  filter(
    !(
      (!is.na(arb_index_date) & !is.na(ccb_index_date)      & arb_index_date == ccb_index_date)      |
      (!is.na(arb_index_date) & !is.na(thiazide_index_date) & arb_index_date == thiazide_index_date) |
      (!is.na(ccb_index_date) & !is.na(thiazide_index_date) & ccb_index_date == thiazide_index_date)
    )
  )

n_after_same_day        <- nrow(base_cohort)
n_after_same_day_ras    <- sum(base_cohort$exposure_group == "RAS")
n_after_same_day_nonras <- sum(base_cohort$exposure_group == "NON_RAS")

cat("After same-day exclusion:", n_after_same_day,
    "(RAS:", n_after_same_day_ras, "/ NON_RAS:", n_after_same_day_nonras, ")\n")

# =========================================================
# DEDUPLICATE AND FINALIZE
# =========================================================
indexed_cohort <- base_cohort %>%
  filter(index_date <= analysis_end_date) %>%
  distinct(PERSON_ID, .keep_all = TRUE)

cat("\nFinal indexed cohort:", nrow(indexed_cohort),
    "(RAS:", sum(indexed_cohort$exposure_group == "RAS"),
    "/ NON_RAS:", sum(indexed_cohort$exposure_group == "NON_RAS"), ")\n")

# =========================================================
# FLAG: Crossover to opposite arm within 30 days (not excluded)
# =========================================================
indexed_cohort <- indexed_cohort %>%
  mutate(
    cross_over_within_30d = case_when(
      exposure_group == "RAS" ~
        ((!is.na(ccb_index_date)      & as.integer(ccb_index_date      - index_date) %in% 1:30) |
         (!is.na(thiazide_index_date) & as.integer(thiazide_index_date - index_date) %in% 1:30)),
      exposure_group == "NON_RAS" ~
         (!is.na(arb_index_date)      & as.integer(arb_index_date      - index_date) %in% 1:30),
      TRUE ~ FALSE
    )
  )

cat("Crossover within 30d — RAS:",
    sum(indexed_cohort$cross_over_within_30d & indexed_cohort$exposure_group == "RAS"),
    "/ NON_RAS:",
    sum(indexed_cohort$cross_over_within_30d & indexed_cohort$exposure_group == "NON_RAS"), "\n")

# =========================================================
# BASELINE COMORBIDITY FLAGS
# TRUE = at least one matching SNOMED dx on or before index_date.
# Descriptive only — no patients excluded.
# =========================================================
indexed_cohort <- indexed_cohort %>%
  add_comorbidity_flag(raw_conditions, DIABETES_SNOMED,      "bl_diabetes") %>%
  add_comorbidity_flag(raw_conditions, CKD_SNOMED,           "bl_ckd") %>%
  add_comorbidity_flag(raw_conditions, HEART_FAILURE_SNOMED, "bl_heart_failure") %>%
  add_comorbidity_flag(raw_conditions, CAD_MI_SNOMED,        "bl_cad_mi") %>%
  add_comorbidity_flag(raw_conditions, AFIB_SNOMED,          "bl_afib") %>%
  add_comorbidity_flag(raw_conditions, PAD_SNOMED,           "bl_pad") %>%
  add_comorbidity_flag(raw_conditions, CVA_SNOMED,           "bl_cva") %>%
  add_comorbidity_flag(raw_conditions, TIA_SNOMED,           "bl_tia")

# Comorbidity prevalence by arm
cat("\nBaseline comorbidity prevalence (%):\n")
print(
  indexed_cohort %>%
    group_by(exposure_group) %>%
    summarise(
      n                 = n(),
      pct_diabetes      = round(mean(bl_diabetes)      * 100, 1),
      pct_ckd           = round(mean(bl_ckd)           * 100, 1),
      pct_heart_failure = round(mean(bl_heart_failure) * 100, 1),
      pct_cad_mi        = round(mean(bl_cad_mi)        * 100, 1),
      pct_afib          = round(mean(bl_afib)          * 100, 1),
      pct_pad           = round(mean(bl_pad)           * 100, 1),
      pct_cva           = round(mean(bl_cva)           * 100, 1),
      pct_tia           = round(mean(bl_tia)           * 100, 1),
      .groups = "drop"
    )
)

# =========================================================
# LAST ACTIVITY DATE  (used as censor proxy in Script 3b)
# Take the latest record date across conditions, measurements,
# and drug exposures for each cohort member.
# Computed here since all three tables are already in memory.
# =========================================================
cat("\nDeriving last_activity_date from conditions + measurements + drug exposures...\n")

cohort_ids_vec <- indexed_cohort$PERSON_ID

last_cond_date <- raw_conditions %>%
  filter(PERSON_ID %in% cohort_ids_vec, !is.na(CONDITION_START_DATE)) %>%
  group_by(PERSON_ID) %>%
  summarise(last_cond = max(CONDITION_START_DATE, na.rm = TRUE), .groups = "drop")

last_meas_date <- raw_measurements %>%
  filter(PERSON_ID %in% cohort_ids_vec, !is.na(MEASUREMENT_DATE)) %>%
  group_by(PERSON_ID) %>%
  summarise(last_meas = max(MEASUREMENT_DATE, na.rm = TRUE), .groups = "drop")

last_drug_date <- raw_antihypertensive_exposures %>%
  filter(PERSON_ID %in% cohort_ids_vec, !is.na(DRUG_EXPOSURE_START_DATE)) %>%
  group_by(PERSON_ID) %>%
  summarise(last_drug = max(DRUG_EXPOSURE_START_DATE, na.rm = TRUE), .groups = "drop")

indexed_cohort <- indexed_cohort %>%
  left_join(last_cond_date, by = "PERSON_ID") %>%
  left_join(last_meas_date, by = "PERSON_ID") %>%
  left_join(last_drug_date, by = "PERSON_ID") %>%
  mutate(
    last_activity_date = pmax(last_cond, last_meas, last_drug, na.rm = TRUE)
  ) %>%
  select(-last_cond, -last_meas, -last_drug)

cat(sprintf("  last_activity_date: %s – %s (median %s)\n",
  format(min(indexed_cohort$last_activity_date, na.rm=TRUE)),
  format(max(indexed_cohort$last_activity_date, na.rm=TRUE)),
  format(median(indexed_cohort$last_activity_date, na.rm=TRUE))))
cat(sprintf("  Patients with no activity date: %d\n",
  sum(is.na(indexed_cohort$last_activity_date))))

# =========================================================
# COHORT FLOW
# =========================================================
cohort_flow <- tibble(
  step = c(
    "Raw treated persons (any study drug)",
    "Assigned to exposure group (ITT: first drug wins)",
    paste0("Age ", min_age, "-", max_age, " at index"),
    "Has hypertension (SNOMED dx or elevated BP, non-circular)",
    paste0("Exclude prior ARB/CCB/Thiazide in ", washout_days, "d washout"),
    "Exclude prior dementia",
    "Exclude prior stroke",
    "Exclude same-day multi-drug initiators",
    "Index date on/before analysis end date",
    "Final indexed cohort"
  ),
  n_total = c(n_raw_treated, n_assigned, n_after_age, n_after_htn,
              n_after_washout, n_after_dementia, n_after_stroke, n_after_same_day,
              nrow(indexed_cohort), nrow(indexed_cohort)),
  n_ras = c(NA_integer_, n_assigned_ras, n_after_age_ras, n_after_htn_ras,
            n_after_washout_ras, n_after_dementia_ras, n_after_stroke_ras, n_after_same_day_ras,
            sum(indexed_cohort$exposure_group == "RAS"),
            sum(indexed_cohort$exposure_group == "RAS")),
  n_non_ras = c(NA_integer_, n_assigned_nonras, n_after_age_nonras, n_after_htn_nonras,
                n_after_washout_nonras, n_after_dementia_nonras, n_after_stroke_nonras,
                n_after_same_day_nonras,
                sum(indexed_cohort$exposure_group == "NON_RAS"),
                sum(indexed_cohort$exposure_group == "NON_RAS"))
)

cat("\nCohort flow:\n")
print(cohort_flow)

# =========================================================
# SAVE
# =========================================================
write_parquet(indexed_cohort, file.path(project_dir, "indexed_cohort.parquet"))
write_parquet(cohort_flow,    file.path(project_dir, "cohort_flow.parquet"))
cat("\nSaved to:", project_dir, "\n")
