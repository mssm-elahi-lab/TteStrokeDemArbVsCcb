library(dplyr)
library(dbplyr)
library(DBI)
library(arrow)

# =========================================================
# 1) PATHS
# =========================================================
project_dir <- path.expand("~/tte_arb_project")
dir.create(project_dir, showWarnings = FALSE, recursive = TRUE)
VISIBLE_DIR <- "/tmp/240710138"   # edit to match your RStudio Files pane path

# =========================================================
# 1b) DATE CONTROLS
# =========================================================
# Safety floor: removes sentinel/bad dates (1800, pre-EHR era).
# NOTE: The actual earliest antihypertensive drug record in this DB
# is 2006-07-12 for these specific EPIC ERX source codes — the 1990
# floor is just a guard rail; it does not artificially restrict data.
RX_MIN_VALID_DATE <- as.Date("1990-01-01")

# Avoid partial 2026 data and placeholder future obs_end dates (e.g. 2098).
ANALYSIS_DATA_CUTOFF <- as.Date("2025-12-31")

# Data-driven index restriction:
#   FALSE = include ALL treated persons regardless of when they initiated.
#           Follow-up will vary; median depends on when patients started.
#   TRUE  = restrict to persons whose first Rx was on or before
#           (ANALYSIS_DATA_CUTOFF - TARGET_MIN_FUP_YEARS), guaranteeing
#           every person has *potential* follow-up >= that many years.
#           Use only if you have a clinical reason to require a minimum.
#
# Based on the yearly density check, data volume ramps up sharply in 2011
# (Epic EHR adoption). Pre-2011 data (2006-2010) is sparse but not excluded
# here — it is valid data and will be retained. Set to FALSE to maximise
# the number of persons and follow-up time available.
ENFORCE_OLDER_INDEX    <- FALSE
TARGET_MIN_FUP_YEARS   <- 10   # only used when ENFORCE_OLDER_INDEX = TRUE

# =========================================================
# 2) TABLE HANDLES
# =========================================================
cdm_schema <- "CDMDEID"

person               <- tbl(conn, in_schema(cdm_schema, "PERSON"))
drug_exposure        <- tbl(conn, in_schema(cdm_schema, "DRUG_EXPOSURE"))
condition_occurrence <- tbl(conn, in_schema(cdm_schema, "CONDITION_OCCURRENCE"))
observation_period   <- tbl(conn, in_schema(cdm_schema, "OBSERVATION_PERIOD"))
concept              <- tbl(conn, in_schema(cdm_schema, "CONCEPT"))
concept_relationship <- tbl(conn, in_schema(cdm_schema, "CONCEPT_RELATIONSHIP"))
# NOTE: measurement table intentionally skipped — BP pull times out.
# HTN inclusion will use diagnosis route only (348K/422K persons already covered).

# =========================================================
# 3) EPIC ERX SOURCE CODES BY DRUG CLASS
# =========================================================
losartan_source_codes    <- c(104L, 13711L, 13926L, 16357L, 18790L, 20387L, 46198L, 53237L, 126819L, 300083L)
valsartan_source_codes   <- c(482L, 6337L, 6656L, 7435L, 7856L, 13499L, 14000L, 14992L, 19157L, 20668L, 26068L, 26071L, 47424L, 63626L, 88017L, 88018L, 88022L, 88023L, 88246L, 88369L, 114730L, 114731L, 114732L, 114734L, 114735L, 114736L, 117552L, 117577L, 129612L, 135156L, 135168L, 135170L, 300171L, 300684L)
candesartan_source_codes <- c(3344L, 3626L, 11173L, 12435L, 12579L, 13531L, 14796L, 19295L, 42440L, 44325L)
telmisartan_source_codes <- c(11200L, 12392L, 13478L, 14908L, 16625L, 17096L, 54259L, 62012L)
olmesartan_source_codes  <- c(23779L, 23780L, 23781L, 23782L, 23783L, 23784L, 43183L, 56266L)
azilsartan_source_codes  <- c(97834L, 97836L, 97844L, 97845L, 97905L, 97928L)

arb_source_codes <- unique(c(
    losartan_source_codes, valsartan_source_codes, candesartan_source_codes,
    telmisartan_source_codes, olmesartan_source_codes, azilsartan_source_codes
))

amlodipine_source_codes <- c(208L, 1887L, 2389L, 2919L, 5593L, 6839L, 7307L, 7863L, 11125L, 11656L, 11751L, 12738L, 24771L, 24939L, 31796L, 31797L, 31798L, 31799L, 31800L, 31801L, 31802L, 31803L, 34662L, 34663L, 34664L, 34665L, 34666L, 34667L, 34668L, 34669L, 37476L, 37477L, 37478L, 37481L, 37482L, 37483L, 41700L, 41701L, 41702L, 44004L, 53244L, 55912L, 66086L, 66087L, 66088L, 66089L, 93318L, 93548L, 95795L, 95796L, 95797L, 95798L, 95800L, 95801L, 95802L, 95803L, 96104L, 96379L, 114627L, 114628L, 114629L, 114790L, 114791L, 114792L, 123520L, 123531L, 124199L, 124200L, 124201L, 124405L, 129525L, 129615L, 300001L, 300159L, 402445L)
nifedipine_source_codes <- c(130L, 2917L, 3365L, 3391L, 3408L, 5303L, 6957L, 7005L, 8385L, 9161L, 9939L, 10020L, 10731L, 11344L, 11581L, 13239L, 16733L, 18498L, 19312L, 26713L, 28276L, 28277L, 31393L, 31394L, 34742L, 40846L, 41027L, 55660L, 55661L, 55662L, 55663L, 55664L, 58597L, 58598L, 82242L, 300039L)

ccb_source_codes <- unique(c(amlodipine_source_codes, nifedipine_source_codes))

hydrochlorothiazide_source_codes <- c(31L, 336L, 378L, 541L, 1184L, 1593L, 1723L, 1762L, 2054L, 2070L, 2296L, 2505L, 2710L, 3104L, 3190L, 3345L, 3384L, 3631L, 4479L, 4509L, 4557L, 5017L, 5037L, 5270L, 5350L, 5813L, 5854L, 5947L, 5969L, 6045L, 6108L, 6269L, 7030L, 7529L, 7708L, 7783L, 7796L, 7843L, 7896L, 8086L, 8217L, 8220L, 9132L, 9947L, 9969L, 10140L, 10903L, 10927L, 11137L, 12133L, 12262L, 12433L, 12570L, 13074L, 13238L, 13338L, 13373L, 14275L, 14388L, 14938L, 15151L, 15339L, 15707L, 15992L, 16047L, 16526L, 16832L, 17389L, 17606L, 17854L, 18040L, 18106L, 18313L, 18448L, 18870L, 18933L, 19393L, 19774L, 20212L, 20355L, 20688L, 20767L, 20810L, 20816L, 27345L, 27346L, 27347L, 30520L, 30521L, 30522L, 30523L, 30524L, 35239L, 35240L, 35241L, 38578L, 38582L, 38916L, 38930L, 40590L, 41179L, 41188L, 41573L, 42576L, 43169L, 43409L, 43523L, 44348L, 44384L, 47910L, 48286L, 48480L, 49681L, 51056L, 51057L, 51084L, 51085L, 51429L, 51806L, 51981L, 52552L, 53095L, 53210L, 53240L, 53713L, 53714L, 54169L, 54207L, 54337L, 54551L, 54553L, 54682L, 58550L, 58755L, 59175L, 59176L, 61037L, 62168L, 62408L, 62413L, 62805L, 63427L, 63699L, 64702L, 64709L, 74854L, 77791L, 80538L, 80539L, 80563L, 80564L, 80638L, 80722L, 81882L, 97645L, 97646L, 97647L, 98631L, 98632L, 98633L, 101353L, 101629L, 101630L, 136486L, 300015L)
chlorthalidone_source_codes <- c(1551L, 2099L, 3822L, 5842L, 7069L, 7336L, 7398L, 9767L, 11445L, 16118L, 19543L, 20415L, 42446L, 45217L, 45535L, 45540L, 62065L, 62066L, 62172L, 136663L)

thiazide_source_codes <- unique(c(hydrochlorothiazide_source_codes, chlorthalidone_source_codes))

combo_or_overlap_source_codes <- c(43L, 1558L, 1692L, 5222L, 8863L, 9540L, 11481L, 12592L, 13633L, 13909L, 14359L, 14466L, 16359L, 17995L, 18013L, 20349L, 21627L, 21628L, 29080L, 29081L, 29082L, 29083L, 29084L, 29085L, 36084L, 36090L, 39723L, 39724L, 42439L, 43182L, 44326L, 47423L, 51274L, 53238L, 54258L, 56267L, 62013L, 63627L, 66167L, 66168L, 66171L, 66172L, 76564L, 76565L, 76566L, 76567L, 76590L, 76591L, 76592L, 76593L, 77491L, 77721L, 78895L, 78896L, 78897L, 78898L, 78901L, 78902L, 78903L, 78904L, 79020L, 79025L, 80540L, 80541L, 80565L, 80566L, 82954L, 82960L, 86335L, 86336L, 86337L, 86338L, 86339L, 86344L, 86345L, 86346L, 86347L, 86348L, 87007L, 87096L, 88434L, 88441L, 88442L, 88443L, 88460L, 89218L, 89228L, 94658L, 94659L, 94660L, 94661L, 94662L, 94668L, 94669L, 94670L, 94671L, 94673L, 95186L, 95320L, 97213L, 97214L, 97215L, 97216L, 97217L, 97277L, 97281L, 97660L, 97661L, 98637L, 98645L, 101121L, 101365L, 108736L, 108740L, 108815L)

# =========================================================
# 4) MAP EPIC ERX SOURCE CODES -> OMOP CONCEPT_IDs
# =========================================================
all_target_source_codes <- unique(as.character(c(
    arb_source_codes, ccb_source_codes, thiazide_source_codes
)))

source_code_map <- concept %>%
    filter(CONCEPT_CODE %in% all_target_source_codes,
           VOCABULARY_ID == "EPIC ERX .1",
           DOMAIN_ID == "Drug",
           is.na(INVALID_REASON)) %>%
    select(CONCEPT_ID, CONCEPT_CODE, VOCABULARY_ID, DOMAIN_ID, CONCEPT_CLASS_ID) %>%
    collect()

get_omop_ids <- function(source_codes) {
    source_code_map %>%
        filter(CONCEPT_CODE %in% as.character(source_codes)) %>%
        pull(CONCEPT_ID) %>% unique() %>% as.integer()
}

arb_omop_ids      <- get_omop_ids(arb_source_codes)
ccb_omop_ids      <- get_omop_ids(ccb_source_codes)
thiazide_omop_ids <- get_omop_ids(thiazide_source_codes)
all_target_source_ids <- unique(c(arb_omop_ids, ccb_omop_ids, thiazide_omop_ids))

cat("Mapped source codes:", nrow(source_code_map), "\n")
cat("ARB OMOP IDs:", length(arb_omop_ids),
    "/ CCB:", length(ccb_omop_ids),
    "/ Thiazide:", length(thiazide_omop_ids), "\n")

# =========================================================
# 5) EXTRACT DRUG EXPOSURES
#    Date bounds applied on-server to reduce transfer volume.
#    RX_MIN_VALID_DATE removes sentinel/historical noise (<1990).
#    ANALYSIS_DATA_CUTOFF avoids partial 2026 and placeholder future dates.
# =========================================================
raw_antihypertensive_exposures <- drug_exposure %>%
    filter(
        DRUG_SOURCE_CONCEPT_ID %in% all_target_source_ids,
        !is.na(DRUG_EXPOSURE_START_DATE),
        DRUG_EXPOSURE_START_DATE >= RX_MIN_VALID_DATE,
        DRUG_EXPOSURE_START_DATE <= ANALYSIS_DATA_CUTOFF
    ) %>%
    select(PERSON_ID, DRUG_EXPOSURE_ID, DRUG_CONCEPT_ID, DRUG_SOURCE_CONCEPT_ID,
           DRUG_EXPOSURE_START_DATE, DRUG_EXPOSURE_END_DATE, VERBATIM_END_DATE,
           DAYS_SUPPLY, QUANTITY, REFILLS, DRUG_TYPE_CONCEPT_ID,
           ROUTE_CONCEPT_ID, VISIT_OCCURRENCE_ID, DRUG_SOURCE_VALUE) %>%
    collect() %>%
    mutate(arm = case_when(
        DRUG_SOURCE_CONCEPT_ID %in% arb_omop_ids      ~ "ARB",
        DRUG_SOURCE_CONCEPT_ID %in% ccb_omop_ids      ~ "CCB",
        DRUG_SOURCE_CONCEPT_ID %in% thiazide_omop_ids ~ "THIAZIDE",
        TRUE ~ NA_character_
    ))

cat("raw_antihypertensive_exposures rows:", nrow(raw_antihypertensive_exposures), "\n")
print(table(raw_antihypertensive_exposures$arm, useNA = "always"))

# =========================================================
# 5b) HISTORICAL-DEPTH DIAGNOSTIC
#     Confirm extracted data spans the years we need.
# =========================================================
cat("\nDrug date range in extracted cohort:\n")
print(range(raw_antihypertensive_exposures$DRUG_EXPOSURE_START_DATE, na.rm = TRUE))

cat("\nYearly drug record density (extracted cohort):\n")
print(
    raw_antihypertensive_exposures %>%
        mutate(yr = as.integer(format(as.Date(DRUG_EXPOSURE_START_DATE), "%Y"))) %>%
        group_by(yr) %>%
        summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID), .groups = "drop") %>%
        arrange(yr),
    n = Inf
)

# =========================================================
# 5c) DETERMINE TREATED PERSON IDs
#     By default (ENFORCE_OLDER_INDEX = FALSE) all treated persons are
#     included — no data is discarded based on an arbitrary follow-up
#     target.  The follow-up check in 6b will tell you the actual
#     distribution so you can decide post-hoc whether to restrict.
#
#     If ENFORCE_OLDER_INDEX = TRUE, only persons whose first Rx was on
#     or before (ANALYSIS_DATA_CUTOFF - TARGET_MIN_FUP_YEARS) are kept,
#     ensuring every included person had *potential* follow-up of at
#     least TARGET_MIN_FUP_YEARS years.
# =========================================================
first_index <- raw_antihypertensive_exposures %>%
    group_by(PERSON_ID) %>%
    summarise(index_date = min(as.Date(DRUG_EXPOSURE_START_DATE), na.rm = TRUE),
              .groups = "drop")

if (ENFORCE_OLDER_INDEX) {
    index_cutoff <- ANALYSIS_DATA_CUTOFF - round(365.25 * TARGET_MIN_FUP_YEARS)
    eligible_ids <- first_index %>%
        filter(index_date <= index_cutoff) %>%
        pull(PERSON_ID)
    treated_person_ids <- intersect(unique(raw_antihypertensive_exposures$PERSON_ID), eligible_ids)
    cat("\nENFORCE_OLDER_INDEX = TRUE\n")
    cat("Index cutoff (", TARGET_MIN_FUP_YEARS, "yr potential FU):", as.character(index_cutoff), "\n")
    cat("Persons retained:", length(treated_person_ids), "of",
        n_distinct(raw_antihypertensive_exposures$PERSON_ID), "total treated\n")
} else {
    treated_person_ids <- unique(raw_antihypertensive_exposures$PERSON_ID)
    cat("\nAll treated persons included (ENFORCE_OLDER_INDEX = FALSE):",
        length(treated_person_ids), "\n")
}

# =========================================================
# 6) BUILD COHORT SPINE
#    Observation period dates are clamped to the same valid window
#    used for drugs so downstream censoring is consistent.
# =========================================================
obs_period_summary <- observation_period %>%
    filter(PERSON_ID %in% treated_person_ids) %>%
    collect() %>%
    mutate(
        # coalesce fills NA dates BEFORE pmax/pmin so that groups with all-NA
        # end dates never produce -Inf in the subsequent summarise().
        OBSERVATION_PERIOD_START_DATE = pmax(
            coalesce(as.Date(OBSERVATION_PERIOD_START_DATE), RX_MIN_VALID_DATE),
            RX_MIN_VALID_DATE
        ),
        OBSERVATION_PERIOD_END_DATE = pmin(
            coalesce(as.Date(OBSERVATION_PERIOD_END_DATE), ANALYSIS_DATA_CUTOFF),
            ANALYSIS_DATA_CUTOFF
        )
    ) %>%
    group_by(PERSON_ID) %>%
    summarise(obs_start_date = min(OBSERVATION_PERIOD_START_DATE, na.rm = TRUE),
              obs_end_date   = max(OBSERVATION_PERIOD_END_DATE,   na.rm = TRUE),
              .groups = "drop")

cohort_spine_raw <- person %>%
    filter(PERSON_ID %in% treated_person_ids) %>%
    select(PERSON_ID, YEAR_OF_BIRTH, MONTH_OF_BIRTH, DAY_OF_BIRTH,
           XTN_BIRTH_DATE, GENDER_CONCEPT_ID, RACE_CONCEPT_ID,
           ETHNICITY_CONCEPT_ID, XTN_DEATH_DATE) %>%
    collect() %>%
    left_join(obs_period_summary, by = "PERSON_ID") %>%
    mutate(
        # Persons absent from observation_period entirely get ANALYSIS_DATA_CUTOFF
        # as their obs_end_date so downstream censoring never produces NA/Inf.
        obs_end_date = coalesce(as.Date(obs_end_date), ANALYSIS_DATA_CUTOFF)
    )

n_missing_obs <- sum(is.na(cohort_spine_raw$obs_start_date))
cat("cohort_spine_raw rows:", nrow(cohort_spine_raw), "\n")
cat("Persons with no observation_period record (obs_end capped at cutoff):",
    n_missing_obs, "\n")

# =========================================================
# 6b) FOLLOW-UP SANITY CHECK
#     Inspect potential follow-up years before the expensive condition pull.
#     If median is still too low, increase TARGET_MEDIAN_FUP_YEARS or
#     decrease RX_MIN_VALID_DATE, then re-run from section 5c.
# =========================================================
fup_check <- cohort_spine_raw %>%
    inner_join(first_index, by = "PERSON_ID") %>%
    mutate(potential_fu_years = as.numeric(as.Date(obs_end_date) -
                                           as.Date(index_date)) / 365.25)

cat("\nPotential follow-up years (index date -> obs_end_date):\n")
print(summary(fup_check$potential_fu_years))
cat("Percentiles:\n")
print(quantile(fup_check$potential_fu_years, probs = c(0.10, 0.25, 0.50, 0.75, 0.90),
               na.rm = TRUE))

# =========================================================
# 7) MAP ATHENA ICD CONCEPT IDs -> STANDARD SNOMED CONCEPT IDs
#    CONDITION_SOURCE_VALUE is masked; CONDITION_SOURCE_CONCEPT_ID
#    uses EPIC EDG .1 local IDs. CONDITION_CONCEPT_ID (SNOMED) is
#    the only usable field in this Epic OMOP export.
# =========================================================
all_icd_concept_ids <- unique(c(
    # Hypertension
    35207668L, 1569120L, 1569121L, 1569122L, 1569124L,
    44833556L, 44832366L, 44832367L, 44827780L, 44832370L,
    # Dementia
    45533052L, 1568087L, 1568088L, 35207114L,
    1568293L, 1568295L, 35207360L, 45547730L,
    45595932L, 45553737L, 45534454L, 45553736L,
    44824105L, 44821814L, 44826536L, 44827645L, 44834585L, 44832709L,
    # Stroke
    1569184L, 1569190L, 1569191L, 1569193L, 45548032L,
    1569218L, 1569221L, 1569225L, 1569227L, 1569228L,
    44820872L, 44835946L, 44835947L, 44820873L, 44824253L,
    44820875L, 44835952L, 44832388L, 44831252L,
    # Diabetes
    1567940L, 1567956L, 1567972L, 44833365L,
    # CKD
    1571486L, 44830172L,
    # Heart failure
    1569178L, 44824250L,
    # CAD/MI
    1569125L, 1569126L, 1569130L, 1569133L,
    44832372L, 44834725L, 44835930L, 44827784L,
    # AFib
    1569170L, 44824248L, 44821957L, 44820868L,
    # PAD
    1569271L, 1569324L, 44825446L, 44826654L,
    # TIA
    1568360L, 1568361L, 44820875L
))

cat("Mapping", length(all_icd_concept_ids), "ICD concept IDs to SNOMED...\n")

icd_to_snomed_map <- concept_relationship %>%
    filter(
        CONCEPT_ID_1 %in% all_icd_concept_ids,
        RELATIONSHIP_ID == "Maps to",
        is.na(INVALID_REASON)
    ) %>%
    select(ICD_CONCEPT_ID = CONCEPT_ID_1, STANDARD_CONCEPT_ID = CONCEPT_ID_2) %>%
    collect()

cat("Mappings found:", nrow(icd_to_snomed_map), "\n")

unmapped <- setdiff(all_icd_concept_ids, icd_to_snomed_map$ICD_CONCEPT_ID)
if (length(unmapped) > 0) {
    cat("WARNING:", length(unmapped), "ICD concept IDs had no 'Maps to' mapping\n")
}

get_snomed_ids <- function(icd_ids) {
    unique(icd_to_snomed_map$STANDARD_CONCEPT_ID[
        icd_to_snomed_map$ICD_CONCEPT_ID %in% icd_ids
    ])
}

HYPERTENSION_SNOMED  <- get_snomed_ids(c(35207668L, 1569120L, 1569121L, 1569122L, 1569124L, 44833556L, 44832366L, 44832367L, 44827780L, 44832370L))
DEMENTIA_SNOMED      <- get_snomed_ids(c(45533052L, 1568087L, 1568088L, 35207114L, 1568293L, 1568295L, 35207360L, 45547730L, 45595932L, 45553737L, 45534454L, 45553736L, 44824105L, 44821814L, 44826536L, 44827645L, 44834585L, 44832709L))
STROKE_SNOMED        <- get_snomed_ids(c(1569184L, 1569190L, 1569191L, 1569193L, 45548032L, 1569218L, 1569221L, 1569225L, 1569227L, 1569228L, 44820872L, 44835946L, 44835947L, 44820873L, 44824253L, 44820875L, 44835952L, 44832388L, 44831252L))
DIABETES_SNOMED      <- get_snomed_ids(c(1567940L, 1567956L, 1567972L, 44833365L))
CKD_SNOMED           <- get_snomed_ids(c(1571486L, 44830172L))
HEART_FAILURE_SNOMED <- get_snomed_ids(c(1569178L, 44824250L))
CAD_MI_SNOMED        <- get_snomed_ids(c(1569125L, 1569126L, 1569130L, 1569133L, 44832372L, 44834725L, 44835930L, 44827784L))
AFIB_SNOMED          <- get_snomed_ids(c(1569170L, 44824248L, 44821957L, 44820868L))
PAD_SNOMED           <- get_snomed_ids(c(1569271L, 1569324L, 44825446L, 44826654L))
CVA_SNOMED           <- get_snomed_ids(c(1569193L, 45548032L))
TIA_SNOMED           <- get_snomed_ids(c(1568360L, 1568361L, 44820875L))

all_standard_condition_ids <- unique(c(
    HYPERTENSION_SNOMED, DEMENTIA_SNOMED, STROKE_SNOMED,
    DIABETES_SNOMED, CKD_SNOMED, HEART_FAILURE_SNOMED,
    CAD_MI_SNOMED, AFIB_SNOMED, PAD_SNOMED, CVA_SNOMED, TIA_SNOMED
))
cat("Distinct SNOMED concept IDs to filter on:", length(all_standard_condition_ids), "\n")

# =========================================================
# 7a) EXTRACT CONDITIONS
#     Filtering by ~50 SNOMED concept IDs on DB (fast/indexable).
#     IMPORTANT: push PERSON_ID filter into DB to avoid collecting
#     the full global condition table slice into local memory.
#     This can still take hours, but is usually much faster and safer
#     than filtering person_id after collect().
# =========================================================
cat("Starting condition_occurrence pull — this runs overnight...\n")
cat(format(Sys.time()), "\n")

# Upload treated IDs as a temporary DB table, then join server-side.
# This avoids a huge IN (...) clause and avoids global collect() first.
treated_ids_local <- tibble(PERSON_ID = unique(treated_person_ids))

raw_conditions <- tryCatch({
    copy_to(conn,
            treated_ids_local,
            name = "tmp_treated_person_ids",
            temporary = TRUE,
            overwrite = TRUE)

    treated_ids_db <- tbl(conn, "tmp_treated_person_ids")

    cond_query <- condition_occurrence %>%
        inner_join(treated_ids_db, by = "PERSON_ID") %>%
        filter(CONDITION_CONCEPT_ID %in% all_standard_condition_ids) %>%
        select(PERSON_ID, CONDITION_OCCURRENCE_ID, CONDITION_CONCEPT_ID,
               CONDITION_SOURCE_CONCEPT_ID, CONDITION_SOURCE_VALUE,
               CONDITION_START_DATE, CONDITION_END_DATE,
               CONDITION_TYPE_CONCEPT_ID, VISIT_OCCURRENCE_ID)

    # Preflight row count to set expectations before collecting.
    cond_counts <- cond_query %>%
        summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID)) %>%
        collect()
    cat("Condition rows to collect after treated-person filter:", cond_counts$n_rows[[1]], "\n")
    cat("Distinct treated persons with >=1 condition row:", cond_counts$n_persons[[1]], "\n")

    cond_query %>% collect()
}, error = function(e) {
    cat("WARNING: temp-table join path failed; falling back to slower local PERSON_ID filter.\n")
    cat("Reason:", as.character(e$message), "\n")

    condition_occurrence %>%
        filter(CONDITION_CONCEPT_ID %in% all_standard_condition_ids) %>%
        select(PERSON_ID, CONDITION_OCCURRENCE_ID, CONDITION_CONCEPT_ID,
               CONDITION_SOURCE_CONCEPT_ID, CONDITION_SOURCE_VALUE,
               CONDITION_START_DATE, CONDITION_END_DATE,
               CONDITION_TYPE_CONCEPT_ID, VISIT_OCCURRENCE_ID) %>%
        collect() %>%
        filter(PERSON_ID %in% treated_person_ids)
})

cat("raw_conditions rows:", nrow(raw_conditions), "\n")
cat("raw_conditions distinct persons:", n_distinct(raw_conditions$PERSON_ID), "\n")
cat(format(Sys.time()), "\n")

# =========================================================
# 8) SAVE — runs immediately after collect() returns
# =========================================================
write_parquet(raw_antihypertensive_exposures,
              file.path(project_dir, "raw_antihypertensive_exposures.parquet"))
write_parquet(cohort_spine_raw,
              file.path(project_dir, "cohort_spine_raw.parquet"))
write_parquet(raw_conditions,
              file.path(project_dir, "raw_conditions.parquet"))
write_parquet(icd_to_snomed_map,
              file.path(project_dir, "icd_to_snomed_map.parquet"))
write_parquet(source_code_map,
              file.path(project_dir, "source_code_map.parquet"))

cat("\nAll files saved:\n")
print(file.info(list.files(project_dir, full.names = TRUE))[, "size", drop = FALSE])

# =========================================================
# 9) ZIP FOR DOWNLOAD
# =========================================================
zip_output_dir <- if (dir.exists(VISIBLE_DIR)) VISIBLE_DIR else project_dir
zip_path <- file.path(zip_output_dir, "tte_extracts.zip")

zip_status <- tryCatch({
    zip(zipfile = zip_path,
        files   = list.files(project_dir, full.names = TRUE))
    TRUE
}, error = function(e) {
    cat("WARNING: ZIP creation failed.\n")
    cat("Reason:", as.character(e$message), "\n")
    FALSE
})

if (zip_status && file.exists(zip_path)) {
    cat("Zip created at:", zip_path, "\n")
} else {
    cat("ZIP not created. Parquet files are still available in:", project_dir, "\n")
}

rstudioapi::filesPaneNavigate(project_dir)
