# ===========================================================
# FULL TTE EXTRACTION — SINGLE SCRIPT
#
# Part 1  (Sections 1-8): Core extract — drugs, cohort, conditions.
# Part 2  (Section 7b):   Condition baseline covariates (no DB queries).
# Part 3  (Section 7c):   Augmented covariates — labs, smoking,
#                         visits, baseline meds.
#                         → candidate → validate → _augmented.parquet
#
# Design rules (all parts):
#   - No temp tables, no copy_to()
#   - No grepl() pushed to SQL (all pattern matching on local R data)
#   - Local PERSON_ID always character; DB-side IDs numeric when possible
#   - Chunked PERSON_ID queries with per-chunk date bounds
#   - CHUNK_SIZE 500; all STOP_IF flags TRUE
#   - Candidate file saved before validation; original parquet never overwritten
# ===========================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(dbplyr)
  library(DBI)
  library(arrow)
  library(tidyr)
  library(purrr)
})

# ===========================================================
# 1) PATHS
# ===========================================================
project_dir <- path.expand("~/tte_arb_project")
dir.create(project_dir, showWarnings = FALSE, recursive = TRUE)
VISIBLE_DIR <- "/tmp/240710138"   # edit to match your RStudio Files pane path

# ===========================================================
# 1b) DATE CONTROLS
# ===========================================================
RX_MIN_VALID_DATE    <- as.Date("1990-01-01")
ANALYSIS_DATA_CUTOFF <- as.Date("2025-12-31")

ENFORCE_OLDER_INDEX  <- FALSE
TARGET_MIN_FUP_YEARS <- 10

# ===========================================================
# 1c) AUGMENTED COVARIATE EXTRACTION CONFIG
# ===========================================================
BASELINE_LOOKBACK_DAYS      <- 365L
CHUNK_SIZE                  <- 500L

STOP_IF_MEASUREMENTS_EMPTY  <- TRUE
STOP_IF_SMOKING_EMPTY       <- TRUE
STOP_IF_VISITS_EMPTY        <- TRUE
STOP_IF_BASELINE_MEDS_EMPTY <- TRUE

# ===========================================================
# HELPER
# ===========================================================
msg <- function(...) cat(sprintf(...), "\n")

# ===========================================================
# 2) TABLE HANDLES
# ===========================================================
cdm_schema <- "CDMDEID"

person               <- tbl(conn, in_schema(cdm_schema, "PERSON"))
drug_exposure        <- tbl(conn, in_schema(cdm_schema, "DRUG_EXPOSURE"))
condition_occurrence <- tbl(conn, in_schema(cdm_schema, "CONDITION_OCCURRENCE"))
observation_period   <- tbl(conn, in_schema(cdm_schema, "OBSERVATION_PERIOD"))
concept              <- tbl(conn, in_schema(cdm_schema, "CONCEPT"))
concept_relationship <- tbl(conn, in_schema(cdm_schema, "CONCEPT_RELATIONSHIP"))

# Additional handles for augmented covariate extraction.
# required = FALSE → NULL if table unavailable; no crash.
get_tbl <- function(table_name, required = FALSE) {
  tryCatch(
    tbl(conn, in_schema(cdm_schema, table_name)),
    error = function(e) {
      if (required) stop("Could not access ", cdm_schema, ".", table_name, ": ", e$message)
      message("WARNING: ", cdm_schema, ".", table_name, " unavailable — skipping (", e$message, ")")
      NULL
    }
  )
}

measurement_tbl      <- get_tbl("MEASUREMENT")
observation_tbl      <- get_tbl("OBSERVATION")
visit_tbl            <- get_tbl("VISIT_OCCURRENCE")
concept_ancestor_tbl <- get_tbl("CONCEPT_ANCESTOR")

# JDBC fetch size note:
# For SAP HANA via RJDBC, the most reliable way to set fetch size is in the
# connection URL parameter: ...?fetchSize=5000
# The rJava JNI approach (setFetchSize on connection object) is driver-specific
# and may silently do nothing. It is not included here.

# ===========================================================
# 3) EPIC ERX SOURCE CODES BY DRUG CLASS
#    Add real institution-specific source codes to any placeholder
#    vectors (marked integer(0)) before running.
# ===========================================================
losartan_source_codes    <- c(104L, 13711L, 13926L, 16357L, 18790L, 20387L, 46198L, 53237L, 126819L, 300083L)
valsartan_source_codes   <- c(482L, 6337L, 6656L, 7435L, 7856L, 13499L, 14000L, 14992L, 19157L, 20668L, 26068L, 26071L, 47424L, 63626L, 88017L, 88018L, 88022L, 88023L, 88246L, 88369L, 114730L, 114731L, 114732L, 114734L, 114735L, 114736L, 117552L, 117577L, 129612L, 135156L, 135168L, 135170L, 300171L, 300684L)
candesartan_source_codes <- c(3344L, 3626L, 11173L, 12435L, 12579L, 13531L, 14796L, 19295L, 42440L, 44325L)
telmisartan_source_codes <- c(11200L, 12392L, 13478L, 14908L, 16625L, 17096L, 54259L, 62012L)
olmesartan_source_codes  <- c(23779L, 23780L, 23781L, 23782L, 23783L, 23784L, 43183L, 56266L)
azilsartan_source_codes  <- c(97834L, 97836L, 97844L, 97845L, 97905L, 97928L)
irbesartan_source_codes  <- integer(0)  # placeholder — add EPIC ERX source codes
eprosartan_source_codes  <- integer(0)  # placeholder — add EPIC ERX source codes

arb_source_codes <- unique(c(
  losartan_source_codes, valsartan_source_codes, candesartan_source_codes,
  telmisartan_source_codes, olmesartan_source_codes, azilsartan_source_codes,
  irbesartan_source_codes, eprosartan_source_codes
))

amlodipine_source_codes <- c(208L, 1887L, 2389L, 2919L, 5593L, 6839L, 7307L, 7863L, 11125L, 11656L, 11751L, 12738L, 24771L, 24939L, 31796L, 31797L, 31798L, 31799L, 31800L, 31801L, 31802L, 31803L, 34662L, 34663L, 34664L, 34665L, 34666L, 34667L, 34668L, 34669L, 37476L, 37477L, 37478L, 37481L, 37482L, 37483L, 41700L, 41701L, 41702L, 44004L, 53244L, 55912L, 66086L, 66087L, 66088L, 66089L, 93318L, 93548L, 95795L, 95796L, 95797L, 95798L, 95800L, 95801L, 95802L, 95803L, 96104L, 96379L, 114627L, 114628L, 114629L, 114790L, 114791L, 114792L, 123520L, 123531L, 124199L, 124200L, 124201L, 124405L, 129525L, 129615L, 300001L, 300159L, 402445L)
nifedipine_source_codes <- c(130L, 2917L, 3365L, 3391L, 3408L, 5303L, 6957L, 7005L, 8385L, 9161L, 9939L, 10020L, 10731L, 11344L, 11581L, 13239L, 16733L, 18498L, 19312L, 26713L, 28276L, 28277L, 31393L, 31394L, 34742L, 40846L, 41027L, 55660L, 55661L, 55662L, 55663L, 55664L, 58597L, 58598L, 82242L, 300039L)

felodipine_source_codes  <- c(10856L, 17495L, 7677L, 52877L, 48284L, 13750L, 15699L, 4781L, 2772L, 57864L, 49025L, 13996L, 14428L, 19914L)
isradipine_source_codes  <- c(14678L, 17358L, 10861L, 17247L, 47926L, 19956L, 52000L, 6668L, 15204L, 8871L, 47925L)
nicardipine_source_codes <- c(402490L, 400062L, 402304L, 402641L, 410012L, 7511L, 300604L, 116652L, 7622L, 15032L, 14384L, 55632L, 3978L, 3752L, 3119L, 44472L, 44473L, 5936L, 17555L, 120832L, 84268L, 84267L, 82995L)
clevidipine_source_codes <- c(83375L, 83380L, 700775L, 83612L)
nimodipine_source_codes  <- integer(0)  # placeholder — add EPIC ERX source codes
nisoldipine_source_codes <- integer(0)  # placeholder — add EPIC ERX source codes

dhp_ccb_source_codes <- unique(c(
  amlodipine_source_codes, nifedipine_source_codes,
  felodipine_source_codes, isradipine_source_codes,
  nicardipine_source_codes, clevidipine_source_codes,
  nimodipine_source_codes, nisoldipine_source_codes
))

# ── Non-DHP CCBs ─────────────────────────────────────────
diltiazem_source_codes   <- c(402025L, 400016L, 402279L, 13543L, 47384L, 82240L, 700160L, 1038L, 6238L, 44489L, 47383L, 17971L, 15761L, 18946L, 3317L, 4421L, 11663L, 27724L, 6838L, 27726L, 10164L, 27725L, 11352L, 20712L, 27728L, 2793L, 2397L, 13896L, 6650L, 19116L, 6320L, 27727L, 47385L, 9237L, 12036L, 44491L, 16184L, 7018L, 20466L, 16654L, 14036L, 300532L, 36418L, 10186L, 27729L, 17214L, 36420L, 36419L, 7376L, 2727L, 44569L, 124608L, 2678L, 44488L, 27730L, 98801L, 3170L, 124607L, 98803L, 124606L, 124318L, 10305L, 98798L, 27732L, 10240L, 124612L, 7263L, 27734L, 28076L, 62382L, 27731L, 18063L, 98800L, 47382L, 19324L, 1799L, 18628L, 36061L, 36062L, 28073L, 7849L, 9433L, 27733L, 44490L, 36063L, 97352L, 18012L, 27735L, 28075L, 61966L, 47380L, 44492L, 47363L, 47381L, 28072L, 98797L, 36064L, 3273L, 124605L, 12885L, 85862L, 16820L, 28074L, 2478L, 7422L, 11042L, 5956L, 97118L, 119909L, 37679L, 37678L, 37680L, 47387L, 2307L, 3911L, 4486L, 48285L, 16223L)
verapamil_source_codes   <- c(14226L, 18331L, 16073L, 61944L, 26229L, 8722L, 26384L, 13462L, 62592L, 15653L, 25682L, 63799L, 15861L, 59L, 5643L, 17741L, 9301L, 20173L, 2426L, 20503L, 9500L, 2463L, 63800L, 645L, 13902L, 551L, 15781L, 12775L, 2134L, 44067L, 11434L, 63803L, 51968L, 14218L, 3193L, 20680L, 5762L, 11534L, 17004L, 44068L, 15694L, 10717L, 11338L, 1853L, 4056L, 46193L, 63804L, 857L, 216L, 18991L, 4423L, 4600L)

nondhp_ccb_source_codes <- unique(c(diltiazem_source_codes, verapamil_source_codes))

ccb_source_codes <- unique(c(dhp_ccb_source_codes, nondhp_ccb_source_codes))

hydrochlorothiazide_source_codes <- c(31L, 336L, 378L, 541L, 1184L, 1593L, 1723L, 1762L, 2054L, 2070L, 2296L, 2505L, 2710L, 3104L, 3190L, 3345L, 3384L, 3631L, 4479L, 4509L, 4557L, 5017L, 5037L, 5270L, 5350L, 5813L, 5854L, 5947L, 5969L, 6045L, 6108L, 6269L, 7030L, 7529L, 7708L, 7783L, 7796L, 7843L, 7896L, 8086L, 8217L, 8220L, 9132L, 9947L, 9969L, 10140L, 10903L, 10927L, 11137L, 12133L, 12262L, 12433L, 12570L, 13074L, 13238L, 13338L, 13373L, 14275L, 14388L, 14938L, 15151L, 15339L, 15707L, 15992L, 16047L, 16526L, 16832L, 17389L, 17606L, 17854L, 18040L, 18106L, 18313L, 18448L, 18870L, 18933L, 19393L, 19774L, 20212L, 20355L, 20688L, 20767L, 20810L, 20816L, 27345L, 27346L, 27347L, 30520L, 30521L, 30522L, 30523L, 30524L, 35239L, 35240L, 35241L, 38578L, 38582L, 38916L, 38930L, 40590L, 41179L, 41188L, 41573L, 42576L, 43169L, 43409L, 43523L, 44348L, 44384L, 47910L, 48286L, 48480L, 49681L, 51056L, 51057L, 51084L, 51085L, 51429L, 51806L, 51981L, 52552L, 53095L, 53210L, 53240L, 53713L, 53714L, 54169L, 54207L, 54337L, 54551L, 54553L, 54682L, 58550L, 58755L, 59175L, 59176L, 61037L, 62168L, 62408L, 62413L, 62805L, 63427L, 63699L, 64702L, 64709L, 74854L, 77791L, 80538L, 80539L, 80563L, 80564L, 80638L, 80722L, 81882L, 97645L, 97646L, 97647L, 98631L, 98632L, 98633L, 101353L, 101629L, 101630L, 136486L, 300015L)
chlorthalidone_source_codes <- c(1551L, 2099L, 3822L, 5842L, 7069L, 7336L, 7398L, 9767L, 11445L, 16118L, 19543L, 20415L, 42446L, 45217L, 45535L, 45540L, 62065L, 62066L, 62172L, 136663L)

indapamide_source_codes  <- c(14505L, 3149L, 51425L, 53267L, 1130L, 19192L)
metolazone_source_codes  <- c(5836L, 525L, 300034L, 17722L, 54201L, 64676L, 20549L, 7207L, 6194L, 1102L)

thiazide_source_codes <- unique(c(
  hydrochlorothiazide_source_codes, chlorthalidone_source_codes,
  indapamide_source_codes, metolazone_source_codes
))

combo_or_overlap_source_codes <- c(43L, 1558L, 1692L, 5222L, 8863L, 9540L, 11481L, 12592L, 13633L, 13909L, 14359L, 14466L, 16359L, 17995L, 18013L, 20349L, 21627L, 21628L, 29080L, 29081L, 29082L, 29083L, 29084L, 29085L, 36084L, 36090L, 39723L, 39724L, 42439L, 43182L, 44326L, 47423L, 51274L, 53238L, 54258L, 56267L, 62013L, 63627L, 66167L, 66168L, 66171L, 66172L, 76564L, 76565L, 76566L, 76567L, 76590L, 76591L, 76592L, 76593L, 77491L, 77721L, 78895L, 78896L, 78897L, 78898L, 78901L, 78902L, 78903L, 78904L, 79020L, 79025L, 80540L, 80541L, 80565L, 80566L, 82954L, 82960L, 86335L, 86336L, 86337L, 86338L, 86339L, 86344L, 86345L, 86346L, 86347L, 86348L, 87007L, 87096L, 88434L, 88441L, 88442L, 88443L, 88460L, 89218L, 89228L, 94658L, 94659L, 94660L, 94661L, 94662L, 94668L, 94669L, 94670L, 94671L, 94673L, 95186L, 95320L, 97213L, 97214L, 97215L, 97216L, 97217L, 97277L, 97281L, 97660L, 97661L, 98637L, 98645L, 101121L, 101365L, 108736L, 108740L, 108815L)

# ── Per-drug metadata lookup (used in Sections 4 & 5) ──────────────────────
# Maps each integer source code to drug_name, drug_subclass, arm.
# Rows from integer(0) placeholders produce no rows — harmless.
drug_class_spec <- bind_rows(
  tibble(source_code = losartan_source_codes,            drug_name = "losartan",            drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = valsartan_source_codes,           drug_name = "valsartan",           drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = candesartan_source_codes,         drug_name = "candesartan",         drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = telmisartan_source_codes,         drug_name = "telmisartan",         drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = olmesartan_source_codes,          drug_name = "olmesartan",          drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = azilsartan_source_codes,          drug_name = "azilsartan",          drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = irbesartan_source_codes,          drug_name = "irbesartan",          drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = eprosartan_source_codes,          drug_name = "eprosartan",          drug_subclass = "ARB",           arm = "ARB"),
  tibble(source_code = amlodipine_source_codes,          drug_name = "amlodipine",          drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = nifedipine_source_codes,          drug_name = "nifedipine",          drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = felodipine_source_codes,          drug_name = "felodipine",          drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = isradipine_source_codes,          drug_name = "isradipine",          drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = nicardipine_source_codes,         drug_name = "nicardipine",         drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = nimodipine_source_codes,          drug_name = "nimodipine",          drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = nisoldipine_source_codes,         drug_name = "nisoldipine",         drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = clevidipine_source_codes,         drug_name = "clevidipine",         drug_subclass = "DHP_CCB",       arm = "CCB"),
  tibble(source_code = diltiazem_source_codes,           drug_name = "diltiazem",           drug_subclass = "NONDHP_CCB",    arm = "CCB"),
  tibble(source_code = verapamil_source_codes,           drug_name = "verapamil",           drug_subclass = "NONDHP_CCB",    arm = "CCB"),
  tibble(source_code = hydrochlorothiazide_source_codes, drug_name = "hydrochlorothiazide", drug_subclass = "THIAZIDE",      arm = "THIAZIDE"),
  tibble(source_code = chlorthalidone_source_codes,      drug_name = "chlorthalidone",      drug_subclass = "THIAZIDE_LIKE", arm = "THIAZIDE"),
  tibble(source_code = indapamide_source_codes,          drug_name = "indapamide",          drug_subclass = "THIAZIDE_LIKE", arm = "THIAZIDE"),
  tibble(source_code = metolazone_source_codes,          drug_name = "metolazone",          drug_subclass = "THIAZIDE_LIKE", arm = "THIAZIDE")
) %>%
  distinct(source_code, .keep_all = TRUE)

# ===========================================================
# 4) MAP EPIC ERX SOURCE CODES -> OMOP CONCEPT_IDs
# ===========================================================
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

# Enrich source_code_map with drug_name, drug_subclass, arm from drug_class_spec.
# CONCEPT_ID is the OMOP drug concept ID; drug_class_spec is keyed on the raw
# integer source code which matches CONCEPT_CODE (character) in source_code_map.
drug_concept_lookup <- source_code_map %>%
  mutate(source_code = as.integer(CONCEPT_CODE)) %>%
  left_join(drug_class_spec, by = "source_code") %>%
  select(-source_code)

get_omop_ids <- function(source_codes) {
  source_code_map %>%
    filter(CONCEPT_CODE %in% as.character(source_codes)) %>%
    pull(CONCEPT_ID) %>% unique() %>% as.integer()
}

arb_omop_ids      <- get_omop_ids(arb_source_codes)
ccb_omop_ids      <- get_omop_ids(ccb_source_codes)
thiazide_omop_ids <- get_omop_ids(thiazide_source_codes)
all_target_source_ids <- unique(c(arb_omop_ids, ccb_omop_ids, thiazide_omop_ids))

# Build a per-OMOP-ID lookup for the collect()-side join
omop_id_to_class <- drug_concept_lookup %>%
  select(CONCEPT_ID, drug_name, drug_subclass, arm) %>%
  distinct(CONCEPT_ID, .keep_all = TRUE)

cat("Mapped source codes:", nrow(source_code_map), "\n")
cat("ARB OMOP IDs:", length(arb_omop_ids),
    "/ CCB:", length(ccb_omop_ids),
    "/ Thiazide:", length(thiazide_omop_ids), "\n")
cat("drug_concept_lookup rows:", nrow(drug_concept_lookup), "\n")
print(drug_concept_lookup %>% count(arm, drug_subclass, drug_name, name = "n_omop_ids") %>% arrange(arm, drug_subclass, drug_name))

# ===========================================================
# 5) EXTRACT DRUG EXPOSURES
# ===========================================================
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
  # Join class metadata locally after collect() — no SQL pattern matching
  left_join(omop_id_to_class, by = c("DRUG_SOURCE_CONCEPT_ID" = "CONCEPT_ID"))

cat("raw_antihypertensive_exposures rows:", nrow(raw_antihypertensive_exposures), "\n")
cat("\nBy arm:\n"); print(table(raw_antihypertensive_exposures$arm, useNA = "always"))
cat("\nBy drug_subclass:\n"); print(table(raw_antihypertensive_exposures$drug_subclass, useNA = "always"))
cat("\nBy drug_name:\n"); print(table(raw_antihypertensive_exposures$drug_name, useNA = "always"))

cat("\nDrug date range:\n")
print(range(raw_antihypertensive_exposures$DRUG_EXPOSURE_START_DATE, na.rm = TRUE))

cat("\nYearly drug record density:\n")
print(
  raw_antihypertensive_exposures %>%
    mutate(yr = as.integer(format(as.Date(DRUG_EXPOSURE_START_DATE), "%Y"))) %>%
    group_by(yr) %>%
    summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID), .groups = "drop") %>%
    arrange(yr),
  n = Inf
)

# ===========================================================
# 5c) TREATED PERSON IDs
# ===========================================================
first_index <- raw_antihypertensive_exposures %>%
  group_by(PERSON_ID) %>%
  summarise(index_date = min(as.Date(DRUG_EXPOSURE_START_DATE), na.rm = TRUE),
            .groups = "drop")

if (ENFORCE_OLDER_INDEX) {
  index_cutoff <- ANALYSIS_DATA_CUTOFF - round(365.25 * TARGET_MIN_FUP_YEARS)
  eligible_ids <- first_index %>% filter(index_date <= index_cutoff) %>% pull(PERSON_ID)
  treated_person_ids <- intersect(unique(raw_antihypertensive_exposures$PERSON_ID), eligible_ids)
  cat("\nENFORCE_OLDER_INDEX = TRUE; cutoff:", as.character(index_cutoff),
      "; retained:", length(treated_person_ids), "\n")
} else {
  treated_person_ids <- unique(raw_antihypertensive_exposures$PERSON_ID)
  cat("\nAll treated persons included:", length(treated_person_ids), "\n")
}

# ===========================================================
# 6) COHORT SPINE
# ===========================================================
obs_period_summary <- observation_period %>%
  filter(PERSON_ID %in% treated_person_ids) %>%
  collect() %>%
  mutate(
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
  mutate(obs_end_date = coalesce(as.Date(obs_end_date), ANALYSIS_DATA_CUTOFF))

cat("cohort_spine_raw rows:", nrow(cohort_spine_raw), "\n")
cat("Persons with no observation_period record:",
    sum(is.na(cohort_spine_raw$obs_start_date)), "\n")

fup_check <- cohort_spine_raw %>%
  inner_join(first_index, by = "PERSON_ID") %>%
  mutate(potential_fu_years = as.numeric(as.Date(obs_end_date) -
                                         as.Date(index_date)) / 365.25)
cat("\nPotential follow-up years summary:\n")
print(summary(fup_check$potential_fu_years))

# ===========================================================
# 7) MAP ICD CONCEPT IDs -> STANDARD SNOMED CONCEPT IDs
# ===========================================================
all_icd_concept_ids <- unique(c(
  35207668L, 1569120L, 1569121L, 1569122L, 1569124L,
  44833556L, 44832366L, 44832367L, 44827780L, 44832370L,   # Hypertension
  45533052L, 1568087L, 1568088L, 35207114L,
  1568293L, 1568295L, 35207360L, 45547730L,
  45595932L, 45553737L, 45534454L, 45553736L,
  44824105L, 44821814L, 44826536L, 44827645L, 44834585L, 44832709L,   # Dementia
  1569184L, 1569190L, 1569191L, 1569193L, 45548032L,
  1569218L, 1569221L, 1569225L, 1569227L, 1569228L,
  44820872L, 44835946L, 44835947L, 44820873L, 44824253L,
  44820875L, 44835952L, 44832388L, 44831252L,               # Stroke
  1567940L, 1567956L, 1567972L, 44833365L,                  # Diabetes
  1571486L, 44830172L,                                      # CKD
  1569178L, 44824250L,                                      # Heart failure
  1569125L, 1569126L, 1569130L, 1569133L,
  44832372L, 44834725L, 44835930L, 44827784L,               # CAD/MI
  1569170L, 44824248L, 44821957L, 44820868L,                # AFib
  1569271L, 1569324L, 44825446L, 44826654L,                 # PAD
  1568360L, 1568361L                                        # TIA
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
if (length(unmapped) > 0)
  cat("WARNING:", length(unmapped), "ICD concept IDs had no 'Maps to' mapping\n")

get_snomed_ids <- function(icd_ids) {
  unique(icd_to_snomed_map$STANDARD_CONCEPT_ID[icd_to_snomed_map$ICD_CONCEPT_ID %in% icd_ids])
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

# ===========================================================
# 7a) EXTRACT CONDITIONS
# ===========================================================
cat("Starting condition_occurrence pull...\n")
cat(format(Sys.time()), "\n")

# No copy_to() or temp tables anywhere in this script.
# Filter by CONDITION_CONCEPT_ID (small integer set — indexable) on the DB,
# then filter PERSON_ID locally after collect(). This is the only path used.
raw_conditions <- condition_occurrence %>%
  filter(CONDITION_CONCEPT_ID %in% all_standard_condition_ids) %>%
  select(PERSON_ID, CONDITION_OCCURRENCE_ID, CONDITION_CONCEPT_ID,
         CONDITION_SOURCE_CONCEPT_ID, CONDITION_SOURCE_VALUE,
         CONDITION_START_DATE, CONDITION_END_DATE,
         CONDITION_TYPE_CONCEPT_ID, VISIT_OCCURRENCE_ID) %>%
  collect() %>%
  filter(PERSON_ID %in% treated_person_ids)

cat("raw_conditions rows:", nrow(raw_conditions), "\n")
cat("raw_conditions distinct persons:", n_distinct(raw_conditions$PERSON_ID), "\n")
cat(format(Sys.time()), "\n")

# ===========================================================
# 8) SAVE CORE OUTPUTS
# ===========================================================
save_pq <- function(df, fname) {
  path <- file.path(project_dir, fname)
  write_parquet(df, path)
  msg("[OK] %s (%s rows)", fname, format(nrow(df), big.mark = ","))
}

save_pq(raw_antihypertensive_exposures, "raw_antihypertensive_exposures.parquet")
save_pq(cohort_spine_raw,               "cohort_spine_raw.parquet")
save_pq(raw_conditions,                 "raw_conditions.parquet")
save_pq(icd_to_snomed_map,              "icd_to_snomed_map.parquet")
save_pq(source_code_map,                "source_code_map.parquet")
save_pq(drug_concept_lookup,            "drug_concept_lookup.parquet")

cat("\nCore outputs saved.\n")

# ===========================================================
# SECTION 7b: CONDITION BASELINE COVARIATES
# Zero DB queries. Uses raw_conditions and first_index in memory.
# Produces baseline_covariates_patient.parquet with condition flags.
# Labs/smoking/visits/meds are zero-filled — filled in Section 7c.
# ===========================================================
cat("\n========================================\n")
cat("  SECTION 7b: CONDITION BASELINE COVARIATES\n")
cat("========================================\n")
cat(format(Sys.time()), "\n\n")

# Standardise PERSON_ID to character everywhere
first_index      <- first_index      %>% mutate(PERSON_ID = as.character(PERSON_ID))
cohort_spine_raw <- cohort_spine_raw %>% mutate(PERSON_ID = as.character(PERSON_ID))
raw_conditions   <- raw_conditions   %>% mutate(PERSON_ID = as.character(PERSON_ID))
raw_antihypertensive_exposures <- raw_antihypertensive_exposures %>%
  mutate(PERSON_ID = as.character(PERSON_ID))
treated_person_ids <- as.character(unique(treated_person_ids))

index_spine <- first_index %>%
  filter(PERSON_ID %in% treated_person_ids) %>%
  mutate(index_date = as.Date(index_date))

cat("index_spine rows:", nrow(index_spine), "\n")

pre_index_conditions <- raw_conditions %>%
  inner_join(index_spine %>% select(PERSON_ID, index_date), by = "PERSON_ID") %>%
  mutate(CONDITION_START_DATE = as.Date(CONDITION_START_DATE)) %>%
  filter(!is.na(CONDITION_START_DATE), CONDITION_START_DATE < index_date)

cat("Pre-index condition rows:", nrow(pre_index_conditions), "\n")

make_cond_flag <- function(snomed_ids, flag_name) {
  if (length(snomed_ids) == 0) {
    warning(paste0("No SNOMED IDs for ", flag_name))
    return(tibble(PERSON_ID = character(), !!flag_name := integer()))
  }
  pre_index_conditions %>%
    filter(CONDITION_CONCEPT_ID %in% snomed_ids) %>%
    distinct(PERSON_ID) %>%
    mutate(!!flag_name := 1L)
}

condition_covariates <- index_spine %>%
  select(PERSON_ID) %>%
  left_join(make_cond_flag(DIABETES_SNOMED,      "diabetes_baseline"),      by = "PERSON_ID") %>%
  left_join(make_cond_flag(CKD_SNOMED,           "ckd_baseline"),           by = "PERSON_ID") %>%
  left_join(make_cond_flag(HEART_FAILURE_SNOMED, "hf_baseline"),            by = "PERSON_ID") %>%
  left_join(make_cond_flag(CAD_MI_SNOMED,        "cad_mi_baseline"),        by = "PERSON_ID") %>%
  left_join(make_cond_flag(AFIB_SNOMED,          "afib_baseline"),          by = "PERSON_ID") %>%
  left_join(make_cond_flag(PAD_SNOMED,           "pad_baseline"),           by = "PERSON_ID") %>%
  left_join(make_cond_flag(CVA_SNOMED,           "cva_baseline"),           by = "PERSON_ID") %>%
  left_join(make_cond_flag(TIA_SNOMED,           "tia_baseline"),           by = "PERSON_ID") %>%
  left_join(make_cond_flag(HYPERTENSION_SNOMED,  "hypertension_baseline"),  by = "PERSON_ID") %>%
  left_join(make_cond_flag(DEMENTIA_SNOMED,      "dementia_baseline"),      by = "PERSON_ID") %>%
  mutate(across(where(is.integer), ~ replace_na(.x, 0L)))

cat("Condition flag sums:\n")
print(colSums(condition_covariates %>% select(-PERSON_ID)))

# Assemble baseline covariate file with condition flags + zero-filled placeholders.
# This is the "conditions-only" version; augmented values are added in Section 7c.
baseline_covariates_patient <- index_spine %>%
  select(PERSON_ID, index_date) %>%
  left_join(condition_covariates, by = "PERSON_ID") %>%
  mutate(across(where(is.integer), ~ replace_na(.x, 0L)))

save_pq(baseline_covariates_patient, "baseline_covariates_patient.parquet")
cat("\nSection 7b complete.\n")

# ===========================================================
# SECTION 7c: AUGMENTED COVARIATE EXTRACTION
# Labs / smoking / visits / baseline meds.
# Uses in-memory index_spine and treated_person_ids from above —
# no parquet reads. DB handles already open from Section 2.
# ===========================================================
cat("\n========================================\n")
cat("  SECTION 7c: AUGMENTED COVARIATES\n")
cat("========================================\n")
cat(format(Sys.time()), "\n\n")

# baseline_covariates_existing is the conditions-only version from 7b.
# Section 6 (assembly) will join augmented data onto this.
baseline_covariates_existing <- baseline_covariates_patient

treated_chunks <- chunk_vector <- function(x, chunk_size = CHUNK_SIZE) {
  x <- unique(as.character(x))
  split(x, ceiling(seq_along(x) / chunk_size))
}
# Reassign properly after defining the function
chunk_vector_fn <- chunk_vector

# Sort patients by index_date before chunking so each chunk covers a narrow
# date window. Without this, a chunk of 500 random patients can span the full
# study period (e.g. 2005–2025), forcing SAP HANA to scan 20 years of
# MEASUREMENT/OBSERVATION rows. Sorted, each chunk spans ~2–4 weeks, collapsing
# the per-chunk date window to ~13 months and preventing query-plan hangs.
index_spine <- index_spine %>%
  arrange(index_date, PERSON_ID)
treated_person_ids <- index_spine$PERSON_ID   # re-derive in sorted order
treated_chunks  <- chunk_vector_fn(treated_person_ids, CHUNK_SIZE)

msg("Treated persons: %s", format(length(treated_person_ids), big.mark = ","))
msg("Chunks: %d of size ~%d", length(treated_chunks), CHUNK_SIZE)

# Write chunked DB queries to per-chunk parquet files.
# is_core = TRUE: stop() if >5% chunks fail, or if all return 0 rows.
# Run query_fun(ids) with a hard elapsed-time limit.
# Returns the result on success, or NULL on error/timeout.
.run_with_timeout <- function(ids, query_fun, timeout_sec) {
  setTimeLimit(elapsed = timeout_sec, transient = TRUE)
  result <- tryCatch(
    query_fun(ids),
    error = function(e) { warning(e$message); NULL }
  )
  setTimeLimit(elapsed = Inf, transient = FALSE)
  result
}

query_chunks_to_parquet <- function(chunks, query_fun, out_dir, label, is_core = FALSE,
                                    query_timeout_sec = 180L, retry_sub_n = 5L) {
  if (dir.exists(out_dir)) unlink(out_dir, recursive = TRUE)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  written <- 0L
  failed  <- 0L
  n_total <- length(chunks)

  for (i in seq_along(chunks)) {
    msg("[%s] chunk %d/%d at %s", label, i, n_total, format(Sys.time()))

    dat <- .run_with_timeout(chunks[[i]], query_fun, query_timeout_sec)

    if (is.null(dat)) {
      # First attempt failed — split into sub-chunks and retry each one
      ids      <- chunks[[i]]
      sub_size <- max(1L, ceiling(length(ids) / retry_sub_n))
      sub_list <- split(ids, ceiling(seq_along(ids) / sub_size))
      msg("[%s] chunk %d timed out — retrying as %d sub-chunks (~%d IDs each)",
          label, i, length(sub_list), sub_size)

      parts <- vector("list", length(sub_list))
      all_failed <- TRUE
      for (j in seq_along(sub_list)) {
        part <- .run_with_timeout(sub_list[[j]], query_fun, query_timeout_sec)
        if (!is.null(part)) {
          parts[[j]] <- part
          all_failed <- FALSE
        }
      }

      dat <- if (!all_failed) bind_rows(Filter(Negate(is.null), parts)) else NULL

      if (is.null(dat)) {
        warning(sprintf("[%s] chunk %d: all %d sub-chunks failed — data for these %d IDs not recovered.",
                        label, i, length(sub_list), length(ids)))
        failed <- failed + 1L
      }
    }

    if (!is.null(dat) && nrow(dat) > 0) {
      write_parquet(dat, file.path(out_dir, sprintf("part-%05d.parquet", i)))
      written <- written + 1L
    }
    rm(dat); gc(verbose = FALSE)
  }

  fail_pct <- if (n_total > 0) failed / n_total else 0

  if (is_core) {
    if (fail_pct > 0.05)
      stop(sprintf("[%s] %d of %d chunks failed (%.1f%% > 5%% threshold). Stopping.",
                   label, failed, n_total, 100 * fail_pct))
    if (written == 0)
      stop(sprintf("[%s] All chunks returned 0 rows. Stopping.", label))
  } else if (failed > 0) {
    warning(sprintf("[%s] %d of %d chunks failed (%.1f%%).", label, failed, n_total, 100 * fail_pct))
  }

  msg("[%s] Complete: %d chunks written, %d failed.", label, written, failed)
  written
}

read_chunk_dir <- function(out_dir, empty_tbl) {
  files <- list.files(out_dir, pattern = "\\.parquet$", full.names = TRUE)
  if (length(files) == 0) return(empty_tbl)
  open_dataset(out_dir) %>% collect()
}

# ─────────────────────────────────────────────────────────
# 7c-1) MEASUREMENT CONCEPT SETS (grepl on local data only)
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-1) Building measurement concept sets ===")

empty_lab_concept_map <- tibble(
  CONCEPT_ID = integer(), CONCEPT_NAME = character(),
  VOCABULARY_ID = character(), STANDARD_CONCEPT = character(),
  CONCEPT_CLASS_ID = character(), covariate = character()
)

lab_concept_map <- tryCatch({
  measurement_concepts_all <- concept %>%
    filter(DOMAIN_ID == "Measurement", is.na(INVALID_REASON)) %>%
    select(CONCEPT_ID, CONCEPT_NAME, VOCABULARY_ID, STANDARD_CONCEPT, CONCEPT_CLASS_ID) %>%
    collect()

  match_measurement <- function(covariate, include_pats, exclude_pats = character()) {
    nm  <- tolower(measurement_concepts_all$CONCEPT_NAME)
    inc <- Reduce(`|`, lapply(include_pats, function(p) grepl(p, nm, perl = TRUE)))
    exc <- if (length(exclude_pats) == 0) rep(FALSE, length(nm)) else
      Reduce(`|`, lapply(exclude_pats, function(p) grepl(p, nm, perl = TRUE)))
    measurement_concepts_all %>% filter(inc & !exc) %>% mutate(covariate = covariate)
  }

  base_map <- bind_rows(
    match_measurement("hba1c",            c("hemoglobin a1c", "hba1c", "glycated hemoglobin"), c("estimated")),
    match_measurement("ldl",              c("cholesterol in ldl", "ldl cholesterol", "low density lipoprotein"), c("ratio", "calculated/hdl")),
    match_measurement("hdl",              c("cholesterol in hdl", "hdl cholesterol", "high density lipoprotein"), c("ratio")),
    match_measurement("total_cholesterol",c("^cholesterol \\[", "cholesterol.total", "total cholesterol"), c("ldl", "hdl", "vldl", "ratio")),
    match_measurement("triglycerides",    c("triglyceride"), c("ratio")),
    match_measurement("bmi",              c("body mass index", "\\bbmi\\b")),
    match_measurement("sbp",              c("systolic blood pressure")),
    match_measurement("dbp",              c("diastolic blood pressure")),
    match_measurement("creatinine",       c("creatinine \\[mass/volume\\] in serum", "creatinine.*serum", "creatinine.*plasma"), c("urine", "clearance", "ratio")),
    match_measurement("egfr",             c("glomerular filtration rate", "\\begfr\\b"), c("cystatin")),
    match_measurement("albuminuria",      c("albumin/creatinine", "microalbumin", "protein/creatinine"))
  ) %>% distinct(covariate, CONCEPT_ID, .keep_all = TRUE)

  known_ids     <- tibble(CONCEPT_ID = c(3004249L, 3012888L), covariate = c("sbp", "dbp"))
  known_details <- concept %>%
    filter(CONCEPT_ID %in% known_ids$CONCEPT_ID) %>%
    select(CONCEPT_ID, CONCEPT_NAME, VOCABULARY_ID, STANDARD_CONCEPT, CONCEPT_CLASS_ID) %>%
    collect() %>%
    inner_join(known_ids, by = "CONCEPT_ID")

  bind_rows(base_map, known_details) %>% distinct(covariate, CONCEPT_ID, .keep_all = TRUE)
}, error = function(e) {
  warning("lab_concept_map build failed: ", e$message)
  empty_lab_concept_map
})

save_pq(lab_concept_map, "lab_concept_map.parquet")
msg("Matched measurement concepts by covariate:")
print(lab_concept_map %>% count(covariate, name = "n_concepts") %>% arrange(covariate), n = Inf)

measurement_ids <- unique(lab_concept_map$CONCEPT_ID)

# ─────────────────────────────────────────────────────────
# 7c-2) EXTRACT BASELINE MEASUREMENTS
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-2) Extracting baseline measurements ===")

empty_measurements <- tibble(
  PERSON_ID = character(), MEASUREMENT_ID = integer(),
  MEASUREMENT_CONCEPT_ID = integer(), MEASUREMENT_DATE = as.Date(character()),
  VALUE_AS_NUMBER = numeric(), UNIT_CONCEPT_ID = integer(),
  VALUE_SOURCE_VALUE = character(), covariate = character()
)

raw_baseline_measurements <- empty_measurements

raw_meas_pq <- file.path(project_dir, "raw_baseline_measurements.parquet")

if (file.exists(raw_meas_pq)) {
  # ── SKIP-GUARD: parquet from a prior run already exists — load it directly ──
  msg("raw_baseline_measurements.parquet found on disk — skipping DB extraction.")
  raw_baseline_measurements <- read_parquet(raw_meas_pq) %>%
    mutate(
      PERSON_ID              = as.character(PERSON_ID),
      MEASUREMENT_CONCEPT_ID = as.integer(MEASUREMENT_CONCEPT_ID),
      MEASUREMENT_DATE       = as.Date(MEASUREMENT_DATE),
      VALUE_AS_NUMBER        = as.numeric(VALUE_AS_NUMBER),
      UNIT_CONCEPT_ID        = as.integer(UNIT_CONCEPT_ID)
    )
  msg("raw_baseline_measurements rows: %s", format(nrow(raw_baseline_measurements), big.mark = ","))
} else if (is.null(measurement_tbl)) {
  warning("MEASUREMENT table unavailable — raw_baseline_measurements is empty.")
} else if (length(measurement_ids) == 0) {
  warning("No measurement concept IDs resolved — raw_baseline_measurements is empty.")
} else {
  raw_meas_dir <- file.path(project_dir, "raw_baseline_measurements_chunks")

  query_chunks_to_parquet(
    treated_chunks,
    function(ids) {
      ids_chr    <- as.character(ids)
      ids_db_try <- suppressWarnings(as.numeric(ids_chr))
      ids_db     <- if (anyNA(ids_db_try)) ids_chr else ids_db_try

      chunk_idx   <- index_spine %>% filter(PERSON_ID %in% ids_chr)
      chunk_min_d <- min(chunk_idx$index_date, na.rm = TRUE) - BASELINE_LOOKBACK_DAYS
      chunk_max_d <- max(chunk_idx$index_date, na.rm = TRUE)

      measurement_tbl %>%
        filter(PERSON_ID %in% ids_db,
               MEASUREMENT_CONCEPT_ID %in% measurement_ids,
               !is.na(MEASUREMENT_DATE),
               MEASUREMENT_DATE >= chunk_min_d,
               MEASUREMENT_DATE <= chunk_max_d) %>%
        select(PERSON_ID, MEASUREMENT_ID, MEASUREMENT_CONCEPT_ID,
               MEASUREMENT_DATE, VALUE_AS_NUMBER, UNIT_CONCEPT_ID,
               VALUE_SOURCE_VALUE) %>%
        collect() %>%
        mutate(PERSON_ID        = as.character(PERSON_ID),
               MEASUREMENT_DATE = as.Date(MEASUREMENT_DATE)) %>%
        filter(PERSON_ID %in% ids_chr) %>%
        inner_join(chunk_idx, by = "PERSON_ID") %>%
        filter(MEASUREMENT_DATE < index_date,
               MEASUREMENT_DATE >= index_date - BASELINE_LOOKBACK_DAYS) %>%
        select(-index_date)
    },
    raw_meas_dir, "measurements", is_core = TRUE
  )

  raw_baseline_measurements <- read_chunk_dir(raw_meas_dir, empty_measurements) %>%
    mutate(MEASUREMENT_CONCEPT_ID = as.integer(MEASUREMENT_CONCEPT_ID)) %>%
    left_join(lab_concept_map %>%
                transmute(MEASUREMENT_CONCEPT_ID = as.integer(CONCEPT_ID), covariate),
              by = "MEASUREMENT_CONCEPT_ID")

  save_pq(raw_baseline_measurements, "raw_baseline_measurements.parquet")
  msg("raw_baseline_measurements rows: %s", format(nrow(raw_baseline_measurements), big.mark = ","))
}

if (STOP_IF_MEASUREMENTS_EMPTY && nrow(raw_baseline_measurements) == 0)
  stop("Measurement extraction returned 0 rows and STOP_IF_MEASUREMENTS_EMPTY = TRUE.")

# Unit diagnostics
raw_baseline_measurements <- tryCatch({
  if (nrow(raw_baseline_measurements) == 0) {
    raw_baseline_measurements %>% mutate(UNIT_NAME = character())
  } else {
    unit_ids    <- unique(na.omit(raw_baseline_measurements$UNIT_CONCEPT_ID))
    unit_lookup <- if (length(unit_ids) > 0) {
      concept %>%
        filter(CONCEPT_ID %in% unit_ids) %>%
        select(CONCEPT_ID, CONCEPT_NAME) %>%
        collect() %>%
        transmute(UNIT_CONCEPT_ID = as.integer(CONCEPT_ID), UNIT_NAME = CONCEPT_NAME)
    } else tibble(UNIT_CONCEPT_ID = integer(), UNIT_NAME = character())
    raw_baseline_measurements %>% left_join(unit_lookup, by = "UNIT_CONCEPT_ID")
  }
}, error = function(e) {
  warning("Unit lookup failed: ", e$message)
  raw_baseline_measurements %>% mutate(UNIT_NAME = NA_character_)
})

measurement_unit_diagnostics <- if (nrow(raw_baseline_measurements) > 0)
  raw_baseline_measurements %>%
    count(covariate, MEASUREMENT_CONCEPT_ID, UNIT_CONCEPT_ID, UNIT_NAME, sort = TRUE)
else
  tibble(covariate = character(), MEASUREMENT_CONCEPT_ID = integer(),
         UNIT_CONCEPT_ID = integer(), UNIT_NAME = character(), n = integer())
save_pq(measurement_unit_diagnostics, "measurement_unit_diagnostics.parquet")

# Plausible-range cleaning (local only)
plausible_ranges <- tibble::tribble(
  ~covariate,         ~min_val, ~max_val,
  "hba1c",                 3,       20,
  "ldl",                  10,      400,
  "hdl",                   5,      150,
  "total_cholesterol",    50,      500,
  "triglycerides",        20,     2000,
  "bmi",                  10,      100,
  "sbp",                  60,      300,
  "dbp",                  30,      200,
  "creatinine",          0.2,       20,
  "egfr",                  1,      200,
  "albuminuria",           0,    10000
)

cleaned_baseline_measurements <- if (nrow(raw_baseline_measurements) > 0) {
  raw_baseline_measurements %>%
    left_join(plausible_ranges, by = "covariate") %>%
    mutate(
      value_num      = as.numeric(VALUE_AS_NUMBER),
      plausible_flag = as.integer(!is.na(VALUE_AS_NUMBER) & !is.na(min_val) & !is.na(max_val) &
                                    VALUE_AS_NUMBER >= min_val & VALUE_AS_NUMBER <= max_val),
      cleaned_value  = if_else(plausible_flag == 1L, as.numeric(VALUE_AS_NUMBER), NA_real_)
    )
} else {
  raw_baseline_measurements %>%
    mutate(value_num = numeric(), plausible_flag = integer(),
           cleaned_value = numeric(), min_val = numeric(), max_val = numeric())
}
save_pq(cleaned_baseline_measurements, "cleaned_baseline_measurements.parquet")

# Patient-level measurement summary
empty_meas_summary <- index_spine %>% select(PERSON_ID)

baseline_measurement_summary <- if (nrow(cleaned_baseline_measurements) == 0) {
  empty_meas_summary
} else {
  tryCatch({
    cleaned_baseline_measurements %>%
      left_join(index_spine, by = "PERSON_ID") %>%
      mutate(days_before_index = as.numeric(index_date - MEASUREMENT_DATE)) %>%
      group_by(PERSON_ID, covariate) %>%
      arrange(days_before_index, .by_group = TRUE) %>%
      summarise(
        closest_raw_value     = first(value_num),
        closest_raw_date      = first(MEASUREMENT_DATE),
        closest_cleaned_value = { idx <- which(plausible_flag == 1L)[1]
          if (is.na(idx)) NA_real_ else value_num[idx] },
        closest_cleaned_date  = { idx <- which(plausible_flag == 1L)[1]
          if (is.na(idx)) as.Date(NA) else MEASUREMENT_DATE[idx] },
        mean_cleaned_value = mean(cleaned_value, na.rm = TRUE),
        max_cleaned_value  = suppressWarnings(max(cleaned_value, na.rm = TRUE)),
        min_cleaned_value  = suppressWarnings(min(cleaned_value, na.rm = TRUE)),
        n_measurements = n(),
        n_plausible    = sum(plausible_flag, na.rm = TRUE),
        .groups = "drop"
      ) %>%
      mutate(
        mean_cleaned_value = if_else(is.nan(mean_cleaned_value), NA_real_, mean_cleaned_value),
        max_cleaned_value  = if_else(is.infinite(max_cleaned_value), NA_real_, max_cleaned_value),
        min_cleaned_value  = if_else(is.infinite(min_cleaned_value), NA_real_, min_cleaned_value)
      ) %>%
      pivot_wider(
        names_from  = covariate,
        values_from = c(closest_raw_value, closest_raw_date, closest_cleaned_value,
                        closest_cleaned_date, mean_cleaned_value, max_cleaned_value,
                        min_cleaned_value, n_measurements, n_plausible),
        names_sep = "__"
      ) %>%
      right_join(index_spine %>% select(PERSON_ID), by = "PERSON_ID")
  }, error = function(e) {
    warning("baseline_measurement_summary build failed: ", e$message)
    empty_meas_summary
  })
}
save_pq(baseline_measurement_summary, "baseline_measurement_summary.parquet")

# ─────────────────────────────────────────────────────────
# 7c-3) SMOKING FROM OBSERVATION
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-3) Extracting smoking status from OBSERVATION ===")

empty_smoking <- tibble(
  PERSON_ID = character(), OBSERVATION_ID = integer(),
  OBSERVATION_CONCEPT_ID = integer(), VALUE_AS_CONCEPT_ID = integer(),
  OBSERVATION_DATE = as.Date(character()), OBSERVATION_SOURCE_VALUE = character(),
  VALUE_AS_STRING = character()
)

raw_baseline_smoking <- empty_smoking

smoking_status_patient <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(smoking_status = NA_character_, smoking_current = 0L,
         smoking_former = 0L, smoking_never = 0L, smoking_unknown = 1L)

if (!is.null(observation_tbl)) {
  tryCatch({
    obs_concepts_all <- concept %>%
      filter(DOMAIN_ID %in% c("Observation", "Meas Value"), is.na(INVALID_REASON)) %>%
      select(CONCEPT_ID, CONCEPT_NAME, DOMAIN_ID) %>%
      collect()

    smoking_concepts    <- obs_concepts_all %>%
      filter(grepl("smok|tobacco|nicotine|cigarette", tolower(CONCEPT_NAME), perl = TRUE))
    smoking_concept_ids <- unique(smoking_concepts$CONCEPT_ID)
    save_pq(smoking_concepts, "smoking_concept_map.parquet")
    msg("Smoking concept IDs found: %d", length(smoking_concept_ids))

    if (length(smoking_concept_ids) > 0) {
      raw_smoking_dir <- file.path(project_dir, "raw_baseline_smoking_chunks")

      query_chunks_to_parquet(
        treated_chunks,
        function(ids) {
          ids_chr    <- as.character(ids)
          ids_db_try <- suppressWarnings(as.numeric(ids_chr))
          ids_db     <- if (anyNA(ids_db_try)) ids_chr else ids_db_try

          chunk_idx   <- index_spine %>% filter(PERSON_ID %in% ids_chr)
          chunk_max_d <- max(chunk_idx$index_date, na.rm = TRUE)

          observation_tbl %>%
            filter(PERSON_ID %in% ids_db,
                   !is.na(OBSERVATION_DATE),
                   OBSERVATION_DATE <= chunk_max_d,
                   OBSERVATION_CONCEPT_ID %in% smoking_concept_ids |
                     VALUE_AS_CONCEPT_ID  %in% smoking_concept_ids) %>%
            select(PERSON_ID, OBSERVATION_ID, OBSERVATION_CONCEPT_ID,
                   VALUE_AS_CONCEPT_ID, OBSERVATION_DATE,
                   OBSERVATION_SOURCE_VALUE, VALUE_AS_STRING) %>%
            collect() %>%
            mutate(PERSON_ID        = as.character(PERSON_ID),
                   OBSERVATION_DATE = as.Date(OBSERVATION_DATE)) %>%
            filter(PERSON_ID %in% ids_chr) %>%
            inner_join(chunk_idx, by = "PERSON_ID") %>%
            filter(OBSERVATION_DATE < index_date) %>%
            select(-index_date)
        },
        raw_smoking_dir, "smoking", is_core = TRUE
      )

      raw_baseline_smoking <- read_chunk_dir(raw_smoking_dir, empty_smoking) %>%
        mutate(
          PERSON_ID              = as.character(PERSON_ID),
          OBSERVATION_CONCEPT_ID = as.character(OBSERVATION_CONCEPT_ID),
          VALUE_AS_CONCEPT_ID    = as.character(VALUE_AS_CONCEPT_ID),
          OBSERVATION_DATE       = as.Date(OBSERVATION_DATE)
        )

      if (nrow(raw_baseline_smoking) > 0) {
        obs_name_lkp <- smoking_concepts %>%
          transmute(OBSERVATION_CONCEPT_ID = as.character(CONCEPT_ID),
                    observation_concept_name = CONCEPT_NAME)
        val_name_lkp <- smoking_concepts %>%
          transmute(VALUE_AS_CONCEPT_ID = as.character(CONCEPT_ID),
                    value_concept_name = CONCEPT_NAME)

        raw_baseline_smoking <- raw_baseline_smoking %>%
          left_join(obs_name_lkp, by = "OBSERVATION_CONCEPT_ID") %>%
          left_join(val_name_lkp, by = "VALUE_AS_CONCEPT_ID")

        smoking_status_patient <- raw_baseline_smoking %>%
          mutate(
            smoke_text = tolower(paste(
              coalesce(observation_concept_name, ""),
              coalesce(value_concept_name, ""),
              coalesce(OBSERVATION_SOURCE_VALUE, ""),
              coalesce(VALUE_AS_STRING, "")
            )),
            smoking_status = case_when(
              grepl("current|every day|some day",      smoke_text, perl = TRUE) ~ "current",
              grepl("former|quit|ex-smoker|past smoker", smoke_text, perl = TRUE) ~ "former",
              grepl("never",                           smoke_text, perl = TRUE) ~ "never",
              grepl("smoker|smoking|tobacco",          smoke_text, perl = TRUE) ~ "smoker_unknown_currentness",
              TRUE ~ "unknown"
            ),
            priority = case_when(
              smoking_status == "current"                    ~ 1L,
              smoking_status == "former"                     ~ 2L,
              smoking_status == "never"                      ~ 3L,
              smoking_status == "smoker_unknown_currentness" ~ 4L,
              TRUE                                           ~ 5L
            )
          ) %>%
          arrange(PERSON_ID, desc(OBSERVATION_DATE), priority) %>%
          group_by(PERSON_ID) %>%
          slice_head(n = 1) %>%
          ungroup() %>%
          transmute(
            PERSON_ID,
            smoking_status,
            smoking_date    = OBSERVATION_DATE,
            smoking_current = as.integer(smoking_status == "current"),
            smoking_former  = as.integer(smoking_status == "former"),
            smoking_never   = as.integer(smoking_status == "never"),
            smoking_unknown = as.integer(smoking_status %in% c("unknown", "smoker_unknown_currentness"))
          ) %>%
          right_join(index_spine %>% select(PERSON_ID), by = "PERSON_ID") %>%
          mutate(
            smoking_status  = coalesce(smoking_status, "unknown"),
            smoking_current = coalesce(smoking_current, 0L),
            smoking_former  = coalesce(smoking_former,  0L),
            smoking_never   = coalesce(smoking_never,   0L),
            smoking_unknown = coalesce(smoking_unknown,  1L)
          )
      }
    }
  }, error = function(e) {
    warning("Smoking extraction failed: ", e$message)
  })
}

save_pq(raw_baseline_smoking,   "raw_baseline_smoking.parquet")
save_pq(smoking_status_patient, "smoking_status_patient.parquet")

if (STOP_IF_SMOKING_EMPTY && nrow(raw_baseline_smoking) == 0)
  stop("Smoking extraction returned 0 rows and STOP_IF_SMOKING_EMPTY = TRUE.")

# ─────────────────────────────────────────────────────────
# 7c-4) VISIT / UTILIZATION DENSITY
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-4) Extracting baseline visit/utilization density ===")

empty_visits <- tibble(
  PERSON_ID = character(), VISIT_OCCURRENCE_ID = integer(),
  VISIT_CONCEPT_ID = integer(), VISIT_START_DATE = as.Date(character()),
  VISIT_END_DATE = as.Date(character())
)

raw_baseline_visits <- empty_visits

baseline_util_summary <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(n_visits_baseline = 0L, n_outpatient_visits = 0L,
         n_ed_visits = 0L, n_inpatient_visits = 0L)

if (!is.null(visit_tbl)) {
  tryCatch({
    raw_visit_dir <- file.path(project_dir, "raw_baseline_visits_chunks")

    query_chunks_to_parquet(
      treated_chunks,
      function(ids) {
        ids_chr    <- as.character(ids)
        ids_db_try <- suppressWarnings(as.numeric(ids_chr))
        ids_db     <- if (anyNA(ids_db_try)) ids_chr else ids_db_try

        chunk_idx   <- index_spine %>% filter(PERSON_ID %in% ids_chr)
        chunk_min_d <- min(chunk_idx$index_date, na.rm = TRUE) - BASELINE_LOOKBACK_DAYS
        chunk_max_d <- max(chunk_idx$index_date, na.rm = TRUE)

        visit_tbl %>%
          filter(PERSON_ID %in% ids_db,
                 !is.na(VISIT_START_DATE),
                 VISIT_START_DATE >= chunk_min_d,
                 VISIT_START_DATE <= chunk_max_d) %>%
          select(PERSON_ID, VISIT_OCCURRENCE_ID, VISIT_CONCEPT_ID,
                 VISIT_START_DATE, VISIT_END_DATE) %>%
          collect() %>%
          mutate(PERSON_ID        = as.character(PERSON_ID),
                 VISIT_START_DATE = as.Date(VISIT_START_DATE),
                 VISIT_END_DATE   = as.Date(VISIT_END_DATE)) %>%
          filter(PERSON_ID %in% ids_chr) %>%
          inner_join(chunk_idx, by = "PERSON_ID") %>%
          filter(VISIT_START_DATE < index_date,
                 VISIT_START_DATE >= index_date - BASELINE_LOOKBACK_DAYS) %>%
          select(-index_date)
      },
      raw_visit_dir, "visits", is_core = TRUE
    )

    raw_baseline_visits <- read_chunk_dir(raw_visit_dir, empty_visits) %>%
      mutate(
        PERSON_ID        = as.character(PERSON_ID),
        VISIT_CONCEPT_ID = as.character(VISIT_CONCEPT_ID),
        VISIT_START_DATE = as.Date(VISIT_START_DATE),
        VISIT_END_DATE   = as.Date(VISIT_END_DATE)
      )

    if (nrow(raw_baseline_visits) > 0) {
      visit_ids      <- unique(na.omit(raw_baseline_visits$VISIT_CONCEPT_ID))
      visit_concepts <- concept %>%
        filter(CONCEPT_ID %in% as.integer(visit_ids)) %>%
        select(CONCEPT_ID, CONCEPT_NAME) %>%
        collect() %>%
        transmute(VISIT_CONCEPT_ID = as.character(CONCEPT_ID), VISIT_CONCEPT_NAME = CONCEPT_NAME)

      baseline_util_summary <- raw_baseline_visits %>%
        left_join(visit_concepts, by = "VISIT_CONCEPT_ID") %>%
        mutate(
          vt    = tolower(coalesce(VISIT_CONCEPT_NAME, "")),
          is_op = grepl("outpatient|office|ambulatory",     vt, perl = TRUE),
          is_ed = grepl("emergency|\\ber\\b|\\bed\\b",      vt, perl = TRUE),
          is_ip = grepl("inpatient|hospital",               vt, perl = TRUE)
        ) %>%
        group_by(PERSON_ID) %>%
        summarise(
          n_visits_baseline   = n(),
          n_outpatient_visits = sum(is_op, na.rm = TRUE),
          n_ed_visits         = sum(is_ed, na.rm = TRUE),
          n_inpatient_visits  = sum(is_ip, na.rm = TRUE),
          .groups = "drop"
        ) %>%
        right_join(index_spine %>% select(PERSON_ID), by = "PERSON_ID") %>%
        mutate(across(starts_with("n_"), ~ coalesce(as.integer(.), 0L)))
    }
  }, error = function(e) {
    warning("Visit extraction failed: ", e$message)
  })
}

save_pq(raw_baseline_visits,   "raw_baseline_visits.parquet")
save_pq(baseline_util_summary, "baseline_util_summary.parquet")

if (STOP_IF_VISITS_EMPTY && nrow(raw_baseline_visits) == 0)
  stop("Visit extraction returned 0 rows and STOP_IF_VISITS_EMPTY = TRUE.")

# ─────────────────────────────────────────────────────────
# 7c-5) BASELINE MEDICATION HISTORY
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-5) Extracting baseline medication history ===")

med_ingredient_spec <- tibble::tribble(
  ~med_class,      ~ingredient_name,
  "statin",        "atorvastatin",
  "statin",        "rosuvastatin",
  "statin",        "simvastatin",
  "statin",        "pravastatin",
  "statin",        "lovastatin",
  "statin",        "pitavastatin",
  "statin",        "fluvastatin",
  "antiplatelet",  "aspirin",
  "antiplatelet",  "clopidogrel",
  "antiplatelet",  "ticagrelor",
  "antiplatelet",  "prasugrel",
  "anticoagulant", "apixaban",
  "anticoagulant", "rivaroxaban",
  "anticoagulant", "dabigatran",
  "anticoagulant", "edoxaban",
  "anticoagulant", "warfarin",
  "ace_inhibitor", "lisinopril",
  "ace_inhibitor", "enalapril",
  "ace_inhibitor", "benazepril",
  "ace_inhibitor", "ramipril",
  "ace_inhibitor", "captopril",
  "beta_blocker",  "metoprolol",
  "beta_blocker",  "carvedilol",
  "beta_blocker",  "atenolol",
  "beta_blocker",  "bisoprolol",
  "beta_blocker",  "nebivolol",
  "beta_blocker",  "labetalol",
  "beta_blocker",  "propranolol",
  "loop_diuretic", "furosemide",
  "loop_diuretic", "torsemide",
  "loop_diuretic", "bumetanide",
  "mra",           "spironolactone",
  "mra",           "eplerenone",
  "metformin",     "metformin"
)

ingredient_concepts <- tryCatch({
  drug_concepts_all <- concept %>%
    filter(DOMAIN_ID == "Drug", is.na(INVALID_REASON)) %>%
    select(CONCEPT_ID, CONCEPT_NAME, CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT) %>%
    collect()
  drug_concepts_all %>%
    filter(CONCEPT_CLASS_ID == "Ingredient") %>%
    mutate(ingredient_name = tolower(CONCEPT_NAME)) %>%
    inner_join(med_ingredient_spec, by = "ingredient_name")
}, error = function(e) {
  warning("ingredient_concepts build failed: ", e$message)
  tibble(CONCEPT_ID = integer(), CONCEPT_NAME = character(),
         CONCEPT_CLASS_ID = character(), VOCABULARY_ID = character(),
         STANDARD_CONCEPT = character(), ingredient_name = character(),
         med_class = character())
})

save_pq(ingredient_concepts, "med_ingredient_concepts.parquet")
msg("Ingredient concepts found: %d", nrow(ingredient_concepts))

empty_descendants <- tibble(
  DESCENDANT_CONCEPT_ID = integer(), DESCENDANT_CONCEPT_NAME = character(),
  CONCEPT_CLASS_ID = character(), VOCABULARY_ID = character(),
  STANDARD_CONCEPT = character(), med_class = character(), ingredient_name = character()
)

med_descendant_concepts <- if (!is.null(concept_ancestor_tbl) && nrow(ingredient_concepts) > 0) {
  tryCatch({
    med_descendants <- concept_ancestor_tbl %>%
      filter(ANCESTOR_CONCEPT_ID %in% ingredient_concepts$CONCEPT_ID) %>%
      select(ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID) %>%
      collect() %>%
      inner_join(ingredient_concepts %>% select(ANCESTOR_CONCEPT_ID = CONCEPT_ID,
                                                med_class, ingredient_name),
                 by = "ANCESTOR_CONCEPT_ID")

    desc_names <- concept %>%
      filter(CONCEPT_ID %in% unique(med_descendants$DESCENDANT_CONCEPT_ID)) %>%
      select(CONCEPT_ID, CONCEPT_NAME, CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT) %>%
      collect()

    desc_names %>%
      transmute(DESCENDANT_CONCEPT_ID = CONCEPT_ID, DESCENDANT_CONCEPT_NAME = CONCEPT_NAME,
                CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT) %>%
      inner_join(med_descendants, by = "DESCENDANT_CONCEPT_ID") %>%
      distinct(DESCENDANT_CONCEPT_ID, med_class, ingredient_name, .keep_all = TRUE)
  }, error = function(e) {
    warning("CONCEPT_ANCESTOR expansion failed (", e$message, ") — falling back to ingredient IDs only.")
    ingredient_concepts %>%
      transmute(DESCENDANT_CONCEPT_ID = CONCEPT_ID, DESCENDANT_CONCEPT_NAME = CONCEPT_NAME,
                CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT, med_class, ingredient_name)
  })
} else {
  if (nrow(ingredient_concepts) > 0) {
    warning("CONCEPT_ANCESTOR unavailable — using ingredient concept IDs only.")
    ingredient_concepts %>%
      transmute(DESCENDANT_CONCEPT_ID = CONCEPT_ID, DESCENDANT_CONCEPT_NAME = CONCEPT_NAME,
                CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT, med_class, ingredient_name)
  } else {
    empty_descendants
  }
}
save_pq(med_descendant_concepts, "med_descendant_concepts.parquet")

med_ids <- unique(med_descendant_concepts$DESCENDANT_CONCEPT_ID)

empty_meds <- tibble(
  PERSON_ID = character(), DRUG_EXPOSURE_ID = integer(),
  DRUG_CONCEPT_ID = integer(), DRUG_SOURCE_CONCEPT_ID = integer(),
  DRUG_EXPOSURE_START_DATE = as.Date(character()),
  DRUG_EXPOSURE_END_DATE = as.Date(character()),
  DAYS_SUPPLY = numeric(), DRUG_SOURCE_VALUE = character(),
  med_class = character(), ingredient_name = character()
)

raw_baseline_medications <- empty_meds

if (length(med_ids) > 0) {
  tryCatch({
    raw_med_dir <- file.path(project_dir, "raw_baseline_medications_chunks")

    query_chunks_to_parquet(
      treated_chunks,
      function(ids) {
        ids_chr    <- as.character(ids)
        ids_db_try <- suppressWarnings(as.numeric(ids_chr))
        ids_db     <- if (anyNA(ids_db_try)) ids_chr else ids_db_try

        chunk_idx   <- index_spine %>% filter(PERSON_ID %in% ids_chr)
        chunk_max_d <- max(chunk_idx$index_date, na.rm = TRUE)

        drug_exposure %>%
          filter(PERSON_ID %in% ids_db,
                 DRUG_CONCEPT_ID %in% med_ids,
                 !is.na(DRUG_EXPOSURE_START_DATE),
                 DRUG_EXPOSURE_START_DATE <= chunk_max_d) %>%
          select(PERSON_ID, DRUG_EXPOSURE_ID, DRUG_CONCEPT_ID, DRUG_SOURCE_CONCEPT_ID,
                 DRUG_EXPOSURE_START_DATE, DRUG_EXPOSURE_END_DATE,
                 DAYS_SUPPLY, DRUG_SOURCE_VALUE) %>%
          collect() %>%
          mutate(PERSON_ID                = as.character(PERSON_ID),
                 DRUG_EXPOSURE_START_DATE = as.Date(DRUG_EXPOSURE_START_DATE),
                 DRUG_EXPOSURE_END_DATE   = as.Date(DRUG_EXPOSURE_END_DATE)) %>%
          filter(PERSON_ID %in% ids_chr) %>%
          inner_join(chunk_idx, by = "PERSON_ID") %>%
          filter(DRUG_EXPOSURE_START_DATE < index_date) %>%
          select(-index_date)
      },
      raw_med_dir, "baseline_meds", is_core = TRUE
    )

    raw_baseline_medications <- read_chunk_dir(raw_med_dir, empty_meds) %>%
      mutate(
        PERSON_ID                = as.character(PERSON_ID),
        DRUG_CONCEPT_ID          = as.character(DRUG_CONCEPT_ID),
        DRUG_SOURCE_CONCEPT_ID   = as.character(DRUG_SOURCE_CONCEPT_ID),
        DRUG_EXPOSURE_START_DATE = as.Date(DRUG_EXPOSURE_START_DATE),
        DRUG_EXPOSURE_END_DATE   = as.Date(DRUG_EXPOSURE_END_DATE)
      ) %>%
      inner_join(
        med_descendant_concepts %>%
          transmute(DRUG_CONCEPT_ID = as.character(DESCENDANT_CONCEPT_ID),
                    med_class, ingredient_name),
        by = "DRUG_CONCEPT_ID"
      )
  }, error = function(e) {
    warning("Baseline medication extraction failed: ", e$message)
  })
}

if (nrow(raw_baseline_medications) < 100 && length(treated_person_ids) > 1000) {
  warning("raw_baseline_medications suspiciously small (",
          nrow(raw_baseline_medications), " rows for ",
          length(treated_person_ids), " persons) — replacing with empty tibble.")
  raw_baseline_medications <- empty_meds
}

save_pq(raw_baseline_medications, "raw_baseline_medications.parquet")
msg("raw_baseline_medications rows: %s", format(nrow(raw_baseline_medications), big.mark = ","))

if (STOP_IF_BASELINE_MEDS_EMPTY && nrow(raw_baseline_medications) == 0)
  stop("Baseline medication extraction returned 0 rows and STOP_IF_BASELINE_MEDS_EMPTY = TRUE.")

med_class_diagnostics <- raw_baseline_medications %>%
  count(med_class, ingredient_name, name = "n_rows", sort = TRUE)
top_drug_source_values_by_class <- raw_baseline_medications %>%
  count(med_class, DRUG_SOURCE_VALUE, sort = TRUE) %>%
  group_by(med_class) %>% slice_head(n = 25) %>% ungroup()

save_pq(med_class_diagnostics,           "med_class_diagnostics.parquet")
save_pq(top_drug_source_values_by_class, "top_drug_source_values_by_class.parquet")

baseline_medication_summary_long <- if (nrow(raw_baseline_medications) > 0) {
  raw_baseline_medications %>%
    left_join(index_spine, by = "PERSON_ID") %>%
    mutate(days_before = as.numeric(index_date - DRUG_EXPOSURE_START_DATE)) %>%
    group_by(PERSON_ID, med_class) %>%
    summarise(
      has_365d      = as.integer(any(days_before >= 0 & days_before <= 365, na.rm = TRUE)),
      has_730d      = as.integer(any(days_before >= 0 & days_before <= 730, na.rm = TRUE)),
      ever_preindex = as.integer(any(days_before >= 0, na.rm = TRUE)),
      n_rx          = n(),
      .groups = "drop"
    )
} else {
  tibble(PERSON_ID = character(), med_class = character(),
         has_365d = integer(), has_730d = integer(),
         ever_preindex = integer(), n_rx = integer())
}

baseline_medication_summary <- if (nrow(baseline_medication_summary_long) > 0) {
  baseline_medication_summary_long %>%
    pivot_wider(names_from = med_class,
                values_from = c(has_365d, has_730d, ever_preindex, n_rx),
                names_glue = "{.value}_{med_class}", values_fill = 0L)
} else {
  index_spine %>% select(PERSON_ID)
}
save_pq(baseline_medication_summary, "baseline_medication_summary.parquet")

# ─────────────────────────────────────────────────────────
# 7c-6) ASSEMBLE CANDIDATE — validate before writing final output
# ─────────────────────────────────────────────────────────
msg("\n=== 7c-6) Assembling candidate augmented covariate file ===")

baseline_base <- baseline_covariates_existing %>%
  select(PERSON_ID, index_date,
         diabetes_baseline, ckd_baseline, hf_baseline, cad_mi_baseline,
         afib_baseline, pad_baseline, cva_baseline, tia_baseline,
         hypertension_baseline, dementia_baseline)

baseline_covariates_candidate <- baseline_base %>%
  left_join(baseline_measurement_summary, by = "PERSON_ID") %>%
  left_join(smoking_status_patient,       by = "PERSON_ID") %>%
  left_join(baseline_util_summary,        by = "PERSON_ID") %>%
  left_join(baseline_medication_summary,  by = "PERSON_ID") %>%
  mutate(
    smoking_status  = coalesce(smoking_status, "unknown"),
    smoking_current = coalesce(smoking_current, 0L),
    smoking_former  = coalesce(smoking_former,  0L),
    smoking_never   = coalesce(smoking_never,   0L),
    smoking_unknown = coalesce(smoking_unknown,  1L),
    across(starts_with("n_"),    ~ coalesce(as.integer(.), 0L)),
    across(starts_with("has_"),  ~ coalesce(as.integer(.), 0L)),
    across(starts_with("ever_"), ~ coalesce(as.integer(.), 0L))
  )

# Save candidate first — available for inspection regardless of validation outcome.
# baseline_covariates_patient.parquet is NEVER overwritten from this point.
save_pq(baseline_covariates_candidate, "baseline_covariates_patient_augmented_candidate.parquet")
msg("Candidate saved. Running validation...")

# Coverage table helpers
n_total <- nrow(baseline_covariates_candidate)
cov_n   <- function(df, col) {
  if (!col %in% names(df)) return(0L)
  sum(!is.na(df[[col]]) & df[[col]] != 0, na.rm = TRUE)
}
cov_pct <- function(df, col, n) round(100 * cov_n(df, col) / n, 1)

meas_covs     <- c("sbp", "dbp", "hba1c", "ldl", "hdl", "total_cholesterol",
                   "triglycerides", "bmi", "creatinine", "egfr", "albuminuria")
coverage_rows <- list()

for (cv in meas_covs) {
  col   <- paste0("closest_cleaned_value__", cv)
  n_col <- paste0("n_measurements__", cv)
  n_meas <- if (n_col %in% names(baseline_covariates_candidate))
    sum(baseline_covariates_candidate[[n_col]], na.rm = TRUE) else 0L
  coverage_rows[[cv]] <- tibble(
    domain = "measurement", covariate = cv,
    n_persons_with_value = cov_n(baseline_covariates_candidate, col),
    pct_coverage         = cov_pct(baseline_covariates_candidate, col, n_total),
    total_records        = n_meas
  )
}

n_known_smoking <- sum(
  baseline_covariates_candidate$smoking_current +
  baseline_covariates_candidate$smoking_former  +
  baseline_covariates_candidate$smoking_never   > 0, na.rm = TRUE
)
coverage_rows[["smoking_known"]] <- tibble(
  domain = "smoking", covariate = "smoking_known",
  n_persons_with_value = n_known_smoking,
  pct_coverage         = round(100 * n_known_smoking / n_total, 1),
  total_records        = nrow(raw_baseline_smoking)
)

n_any_visit <- if ("n_visits_baseline" %in% names(baseline_covariates_candidate))
  sum(baseline_covariates_candidate$n_visits_baseline > 0, na.rm = TRUE) else 0L
coverage_rows[["any_visit"]] <- tibble(
  domain = "visits", covariate = "any_baseline_visit",
  n_persons_with_value = n_any_visit,
  pct_coverage         = round(100 * n_any_visit / n_total, 1),
  total_records        = nrow(raw_baseline_visits)
)

all_med_classes <- unique(med_ingredient_spec$med_class)
for (mc in all_med_classes) {
  col <- paste0("has_365d_", mc)
  coverage_rows[[paste0("med_", mc)]] <- tibble(
    domain = "medication", covariate = mc,
    n_persons_with_value = cov_n(baseline_covariates_candidate, col),
    pct_coverage         = cov_pct(baseline_covariates_candidate, col, n_total),
    total_records        = if (nrow(raw_baseline_medications) > 0)
      sum(raw_baseline_medications$med_class == mc, na.rm = TRUE) else 0L
  )
}

covariate_coverage_detailed <- bind_rows(coverage_rows) %>%
  mutate(n_treated = n_total) %>%
  arrange(domain, covariate)
save_pq(covariate_coverage_detailed, "covariate_coverage.parquet")

cat("\n=== FINAL COVERAGE TABLE ===\n")
print(covariate_coverage_detailed %>%
        select(domain, covariate, n_persons_with_value, pct_coverage, total_records),
      n = Inf)

# Validation
msg("\n=== Validation ===")
validation_errors <- character()

check_meas <- function(cv, label = cv) {
  col <- paste0("closest_cleaned_value__", cv)
  n   <- if (col %in% names(baseline_covariates_candidate))
    sum(!is.na(baseline_covariates_candidate[[col]])) else 0L
  if (n == 0)
    validation_errors <<- c(validation_errors,
      sprintf("FAIL: %s — 0 persons have a plausible baseline value", label))
  else
    msg("  PASS: %s — %d persons", label, n)
}

check_meas("sbp"); check_meas("dbp"); check_meas("hba1c", "HbA1c")
check_meas("ldl", "LDL"); check_meas("bmi", "BMI")

n_creat <- { col <- "closest_cleaned_value__creatinine"
  if (col %in% names(baseline_covariates_candidate)) sum(!is.na(baseline_covariates_candidate[[col]])) else 0L }
n_egfr  <- { col <- "closest_cleaned_value__egfr"
  if (col %in% names(baseline_covariates_candidate)) sum(!is.na(baseline_covariates_candidate[[col]])) else 0L }
if (n_creat == 0 && n_egfr == 0)
  validation_errors <- c(validation_errors,
    "FAIL: creatinine/eGFR — 0 persons have any plausible value for either")
else
  msg("  PASS: creatinine/eGFR — creatinine %d persons, eGFR %d persons", n_creat, n_egfr)

if (n_known_smoking == 0)
  validation_errors <- c(validation_errors,
    "FAIL: smoking — 0 persons have a known (current/former/never) status")
else
  msg("  PASS: smoking — %d persons with known status", n_known_smoking)

if (n_any_visit == 0)
  validation_errors <- c(validation_errors,
    "FAIL: visits — 0 persons have any baseline visit record")
else
  msg("  PASS: visits — %d persons with >= 1 visit", n_any_visit)

n_nonzero_med_classes <- sum(sapply(all_med_classes, function(mc)
  cov_n(baseline_covariates_candidate, paste0("has_365d_", mc)) > 0))
if (n_nonzero_med_classes < 2)
  validation_errors <- c(validation_errors,
    sprintf("FAIL: baseline medications — only %d/%d classes have any persons (need >= 2)",
            n_nonzero_med_classes, length(all_med_classes)))
else
  msg("  PASS: baseline medications — %d/%d classes have >= 1 person",
      n_nonzero_med_classes, length(all_med_classes))

col_alb <- "closest_cleaned_value__albuminuria"
if (!col_alb %in% names(baseline_covariates_candidate) ||
    sum(!is.na(baseline_covariates_candidate[[col_alb]])) == 0)
  warning("albuminuria: 0 persons have a value — optional covariate, continuing.")

# Write final augmented output only if validation passes.
# baseline_covariates_patient.parquet is NEVER overwritten.
if (length(validation_errors) > 0) {
  cat("\n=== VALIDATION FAILED ===\n")
  for (e in validation_errors) cat(" ", e, "\n")
  cat("\nbaseline_covariates_patient.parquet has NOT been overwritten.\n")
  cat("Candidate is at: baseline_covariates_patient_augmented_candidate.parquet\n")
  stop(sprintf("%d validation check(s) failed. Inspect the candidate file and re-run.",
               length(validation_errors)))
} else {
  msg("\n=== ALL VALIDATION CHECKS PASSED ===")
  save_pq(baseline_covariates_candidate, "baseline_covariates_patient_augmented.parquet")
  msg("Augmented file written: baseline_covariates_patient_augmented.parquet")
  msg("NOTE: baseline_covariates_patient.parquet was NOT overwritten.")
}

# ===========================================================
# FINAL INVENTORY + ZIP + NAVIGATE
# ===========================================================
cat("\nFinal parquet inventory:\n")
pf <- list.files(project_dir, pattern = "\\.parquet$", full.names = TRUE)
fi <- file.info(pf)
print(data.frame(file = basename(pf), size_mb = round(fi$size / 1e6, 2), row.names = NULL))

zip_output_dir <- if (dir.exists(VISIBLE_DIR)) VISIBLE_DIR else project_dir
zip_path <- file.path(zip_output_dir, "tte_extracts_full.zip")
tryCatch({
  zip(zipfile = zip_path, files = pf)
  msg("Zip created: %s", zip_path)
}, error = function(e) {
  warning("Zip failed: ", e$message)
})

output_dir_to_open <- if (exists("zip_path") && file.exists(zip_path)) dirname(zip_path) else project_dir
cat("\nOutput directory for download:\n")
cat(normalizePath(output_dir_to_open, mustWork = FALSE), "\n")

try({
  if (requireNamespace("rstudioapi", quietly = TRUE) && rstudioapi::isAvailable())
    rstudioapi::filesPaneNavigate(output_dir_to_open)
}, silent = TRUE)

msg("\nDONE: full extraction completed at %s", format(Sys.time()))
