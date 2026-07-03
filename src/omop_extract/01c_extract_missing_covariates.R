# =========================================================
# EXTRACT MISSING BASELINE COVARIATES FOR TTE
# BP / A1c / LDL / BMI / smoking / utilization / baseline meds
#
# Run AFTER 01b_baseline_covariates_7b.R has produced:
#   raw_antihypertensive_exposures.parquet
#   cohort_spine_raw.parquet
#   raw_conditions.parquet
#   baseline_covariates_patient.parquet
#
# Design:
#   - No temp tables
#   - No grepl() pushed to SQL (all grepl on local data only)
#   - Chunked PERSON_ID queries (IN clause of 5000 IDs at a time)
#   - Each section saves its output before moving on
#   - Unavailable tables → clean empty outputs + warnings, no stop()
# =========================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(dbplyr)
  library(DBI)
  library(arrow)
  library(tidyr)
  library(purrr)
})

# ----------------------------
# Config — edit if needed
# ----------------------------
project_dir  <- path.expand("~/tte_arb_project")
cdm_schema   <- "CDMDEID"

ANALYSIS_DATA_CUTOFF   <- as.Date("2025-12-31")
BASELINE_LOOKBACK_DAYS <- 365L
CHUNK_SIZE             <- 250L

# FALSE = skip loudly but continue; TRUE = stop() if section returns 0 rows
STOP_IF_MEASUREMENTS_EMPTY  <- TRUE
STOP_IF_SMOKING_EMPTY       <- TRUE
STOP_IF_VISITS_EMPTY        <- TRUE
STOP_IF_BASELINE_MEDS_EMPTY <- TRUE

# ----------------------------
# Helpers
# ----------------------------
msg <- function(...) cat(sprintf(...), "\n")

chunk_vector <- function(x, chunk_size = CHUNK_SIZE) {
  x <- unique(as.character(x))
  split(x, ceiling(seq_along(x) / chunk_size))
}

# Write chunked DB queries to per-chunk parquet files; returns # files written.
# is_core = TRUE: stop() if >5% of chunks fail, or if all chunks produce 0 rows.
query_chunks_to_parquet <- function(chunks, query_fun, out_dir, label, is_core = FALSE) {
  if (dir.exists(out_dir)) unlink(out_dir, recursive = TRUE)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  written <- 0L
  failed  <- 0L
  n_total <- length(chunks)

  for (i in seq_along(chunks)) {
    msg("[%s] chunk %d/%d at %s", label, i, n_total, format(Sys.time()))
    dat <- tryCatch(
      query_fun(chunks[[i]]),
      error = function(e) {
        warning(sprintf("[%s] chunk %d failed: %s", label, i, e$message))
        NULL
      }
    )
    if (is.null(dat)) {
      failed <- failed + 1L
    } else if (nrow(dat) > 0) {
      write_parquet(dat, file.path(out_dir, sprintf("part-%05d.parquet", i)))
      written <- written + 1L
    }
    rm(dat); gc(verbose = FALSE)
  }

  fail_pct <- if (n_total > 0) failed / n_total else 0

  if (is_core) {
    if (fail_pct > 0.05) {
      stop(sprintf(
        "[%s] %d of %d chunks failed (%.1f%% > 5%% threshold). Stopping.",
        label, failed, n_total, 100 * fail_pct
      ))
    }
    if (written == 0) {
      stop(sprintf("[%s] All chunks returned 0 rows. Stopping.", label))
    }
  } else if (failed > 0) {
    warning(sprintf("[%s] %d of %d chunks failed (%.1f%%).", label, failed, n_total, 100 * fail_pct))
  }

  msg("[%s] Complete: %d chunks written, %d failed.", label, written, failed)
  written
}

# Read a directory of parquet chunk files; return empty_tbl if directory is empty.
read_chunk_dir <- function(out_dir, empty_tbl) {
  files <- list.files(out_dir, pattern = "\\.parquet$", full.names = TRUE)
  if (length(files) == 0) return(empty_tbl)
  open_dataset(out_dir) %>% collect()
}

# Try to get a DB table handle; return NULL (not stop) if unavailable.
# required = TRUE stops the script (use only for tables that absolutely must exist).
get_tbl <- function(table_name, required = FALSE) {
  out <- tryCatch(
    tbl(conn, in_schema(cdm_schema, table_name)),
    error = function(e) {
      if (required) stop("Could not access ", cdm_schema, ".", table_name, ": ", e$message)
      message("WARNING: ", cdm_schema, ".", table_name, " unavailable — skipping (", e$message, ")")
      NULL
    }
  )
  out
}

save_pq <- function(df, fname) {
  path <- file.path(project_dir, fname)
  write_parquet(df, path)
  msg("[OK] %s (%s rows)", fname, format(nrow(df), big.mark = ","))
}

# ----------------------------
# Read existing core parquets
# ----------------------------
msg("Reading existing core parquet files from %s", project_dir)

baseline_covariates_existing <- read_parquet(
  file.path(project_dir, "baseline_covariates_patient.parquet")
) %>%
  mutate(PERSON_ID = as.character(PERSON_ID),
         index_date = as.Date(index_date))

index_spine <- baseline_covariates_existing %>%
  select(PERSON_ID, index_date) %>%
  distinct(PERSON_ID, .keep_all = TRUE)

treated_person_ids <- unique(index_spine$PERSON_ID)
treated_chunks     <- chunk_vector(treated_person_ids, CHUNK_SIZE)

msg("Treated persons: %s", format(length(treated_person_ids), big.mark = ","))
msg("Chunks: %d of size ~%d", length(treated_chunks), CHUNK_SIZE)

# DB table handles — all required=FALSE except concept/drug_exposure which
# are known to work from Sections 1–7a.

# Set JDBC fetch size on the connection to prevent full result-set materialisation
# in the JVM before rows are returned to R. This is the primary defence against
# the rJava JNI null-pointer crash (SIGSEGV in rsession during large collects).
tryCatch({
  RJDBC::.jcall(conn@jc, "V", "setFetchSize", 5000L)
  msg("JDBC fetch size set to 5000.")
}, error = function(e) {
  warning("Could not set JDBC fetch size (non-fatal): ", e$message)
})

concept              <- get_tbl("CONCEPT",          required = TRUE)
drug_exposure        <- get_tbl("DRUG_EXPOSURE",    required = TRUE)
measurement_tbl      <- get_tbl("MEASUREMENT",      required = FALSE)  # was required=TRUE — FIXED
observation_tbl      <- get_tbl("OBSERVATION",      required = FALSE)
visit_tbl            <- get_tbl("VISIT_OCCURRENCE", required = FALSE)
concept_ancestor_tbl <- get_tbl("CONCEPT_ANCESTOR", required = FALSE)

# =========================================================
# 1) MEASUREMENT CONCEPT SETS (local grepl on collected data)
# =========================================================
msg("\n=== 1) Building measurement concept sets ===")

# Empty fallback used if concept table fails
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

  # All grepl() runs on local R data — never pushed to SQL
  match_measurement <- function(covariate, include_pats, exclude_pats = character()) {
    nm  <- tolower(measurement_concepts_all$CONCEPT_NAME)
    inc <- Reduce(`|`, lapply(include_pats, function(p) grepl(p, nm, perl = TRUE)))
    exc <- if (length(exclude_pats) == 0) rep(FALSE, length(nm)) else
      Reduce(`|`, lapply(exclude_pats, function(p) grepl(p, nm, perl = TRUE)))
    measurement_concepts_all %>% filter(inc & !exc) %>% mutate(covariate = covariate)
  }

  base_map <- bind_rows(
    match_measurement("hba1c",           c("hemoglobin a1c", "hba1c", "glycated hemoglobin"), c("estimated")),
    match_measurement("ldl",             c("cholesterol in ldl", "ldl cholesterol", "low density lipoprotein"), c("ratio", "calculated/hdl")),
    match_measurement("hdl",             c("cholesterol in hdl", "hdl cholesterol", "high density lipoprotein"), c("ratio")),
    match_measurement("total_cholesterol", c("^cholesterol \\[", "cholesterol.total", "total cholesterol"), c("ldl", "hdl", "vldl", "ratio")),
    match_measurement("triglycerides",   c("triglyceride"), c("ratio")),
    match_measurement("bmi",             c("body mass index", "\\bbmi\\b")),
    match_measurement("sbp",             c("systolic blood pressure")),
    match_measurement("dbp",             c("diastolic blood pressure")),
    match_measurement("creatinine",      c("creatinine \\[mass/volume\\] in serum", "creatinine.*serum", "creatinine.*plasma"), c("urine", "clearance", "ratio")),
    match_measurement("egfr",            c("glomerular filtration rate", "\\begfr\\b"), c("cystatin")),
    match_measurement("albuminuria",     c("albumin/creatinine", "microalbumin", "protein/creatinine"))
  ) %>% distinct(covariate, CONCEPT_ID, .keep_all = TRUE)

  # Hard-coded backstop IDs in case name-matching misses them
  known_ids <- tibble(CONCEPT_ID = c(3004249L, 3012888L), covariate = c("sbp", "dbp"))
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

# =========================================================
# 2) EXTRACT BASELINE MEASUREMENTS
#    Entire section skipped cleanly if measurement_tbl is NULL.
# =========================================================
msg("\n=== 2) Extracting baseline measurements ===")

empty_measurements <- tibble(
  PERSON_ID = character(), MEASUREMENT_ID = integer(),
  MEASUREMENT_CONCEPT_ID = integer(), MEASUREMENT_DATE = as.Date(character()),
  VALUE_AS_NUMBER = numeric(), UNIT_CONCEPT_ID = integer(),
  VALUE_SOURCE_VALUE = character(), covariate = character()
)

raw_baseline_measurements <- empty_measurements

if (is.null(measurement_tbl)) {
  warning("MEASUREMENT table unavailable — raw_baseline_measurements is empty.")
} else if (length(measurement_ids) == 0) {
  warning("No measurement concept IDs resolved — raw_baseline_measurements is empty.")
} else {
  raw_meas_dir <- file.path(project_dir, "raw_baseline_measurements_chunks")

  n_written_meas <- query_chunks_to_parquet(
    treated_chunks,
    function(ids) {
      ids_chr     <- as.character(ids)
      ids_db_try  <- suppressWarnings(as.numeric(ids_chr))
      ids_db      <- if (anyNA(ids_db_try)) ids_chr else ids_db_try

      chunk_idx   <- index_spine %>% filter(PERSON_ID %in% ids_chr)
      chunk_min_d <- min(chunk_idx$index_date, na.rm = TRUE) - BASELINE_LOOKBACK_DAYS
      chunk_max_d <- max(chunk_idx$index_date, na.rm = TRUE)

      measurement_tbl %>%
        filter(
          PERSON_ID %in% ids_db,
          MEASUREMENT_CONCEPT_ID %in% measurement_ids,
          !is.na(MEASUREMENT_DATE),
          MEASUREMENT_DATE >= chunk_min_d,
          MEASUREMENT_DATE <= chunk_max_d
        ) %>%
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
    raw_meas_dir,
    "measurements",
    is_core = TRUE
  )

  raw_baseline_measurements <- read_chunk_dir(raw_meas_dir, empty_measurements) %>%
    left_join(
      lab_concept_map %>% select(MEASUREMENT_CONCEPT_ID = CONCEPT_ID, covariate),
      by = "MEASUREMENT_CONCEPT_ID"
    )
}

save_pq(raw_baseline_measurements, "raw_baseline_measurements.parquet")
msg("raw_baseline_measurements rows: %s", format(nrow(raw_baseline_measurements), big.mark = ","))

if (STOP_IF_MEASUREMENTS_EMPTY && nrow(raw_baseline_measurements) == 0) {
  stop("Measurement extraction returned 0 rows and STOP_IF_MEASUREMENTS_EMPTY = TRUE.")
}

# Unit diagnostics
empty_unit_diag <- tibble(covariate = character(), MEASUREMENT_CONCEPT_ID = integer(),
                          UNIT_CONCEPT_ID = integer(), UNIT_NAME = character(), n = integer())

measurement_unit_diagnostics <- empty_unit_diag

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
        transmute(UNIT_CONCEPT_ID = CONCEPT_ID, UNIT_NAME = CONCEPT_NAME)
    } else {
      tibble(UNIT_CONCEPT_ID = integer(), UNIT_NAME = character())
    }
    raw_baseline_measurements %>% left_join(unit_lookup, by = "UNIT_CONCEPT_ID")
  }
}, error = function(e) {
  warning("Unit lookup failed: ", e$message)
  raw_baseline_measurements %>% mutate(UNIT_NAME = NA_character_)
})

if (nrow(raw_baseline_measurements) > 0 && "UNIT_NAME" %in% names(raw_baseline_measurements)) {
  measurement_unit_diagnostics <- raw_baseline_measurements %>%
    count(covariate, MEASUREMENT_CONCEPT_ID, UNIT_CONCEPT_ID, UNIT_NAME, sort = TRUE)
}
save_pq(measurement_unit_diagnostics, "measurement_unit_diagnostics.parquet")

# Plausible ranges (local filtering only)
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
      plausible_flag = as.integer(
        !is.na(VALUE_AS_NUMBER) & !is.na(min_val) & !is.na(max_val) &
          VALUE_AS_NUMBER >= min_val & VALUE_AS_NUMBER <= max_val
      ),
      cleaned_value = if_else(plausible_flag == 1L, as.numeric(VALUE_AS_NUMBER), NA_real_)
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
        closest_cleaned_value = {
          idx <- which(plausible_flag == 1L)[1]
          if (is.na(idx)) NA_real_ else value_num[idx]
        },
        closest_cleaned_date  = {
          idx <- which(plausible_flag == 1L)[1]
          if (is.na(idx)) as.Date(NA) else MEASUREMENT_DATE[idx]
        },
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
        names_sep   = "__"
      ) %>%
      right_join(index_spine %>% select(PERSON_ID), by = "PERSON_ID")
  }, error = function(e) {
    warning("baseline_measurement_summary build failed: ", e$message)
    empty_meas_summary
  })
}

save_pq(baseline_measurement_summary, "baseline_measurement_summary.parquet")

# =========================================================
# 3) SMOKING FROM OBSERVATION
# =========================================================
msg("\n=== 3) Extracting smoking status from OBSERVATION ===")

empty_smoking <- tibble(
  PERSON_ID = character(), OBSERVATION_ID = integer(),
  OBSERVATION_CONCEPT_ID = integer(), VALUE_AS_CONCEPT_ID = integer(),
  OBSERVATION_DATE = as.Date(character()), OBSERVATION_SOURCE_VALUE = character(),
  VALUE_AS_STRING = character()
)

raw_baseline_smoking <- empty_smoking

smoking_status_patient <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(smoking_status   = NA_character_,
         smoking_current  = 0L,
         smoking_former   = 0L,
         smoking_never    = 0L,
         smoking_unknown  = 1L)

if (!is.null(observation_tbl)) {
  tryCatch({
    # collect concept names locally, grepl on local data
    obs_concepts_all <- concept %>%
      filter(DOMAIN_ID %in% c("Observation", "Meas Value"), is.na(INVALID_REASON)) %>%
      select(CONCEPT_ID, CONCEPT_NAME, DOMAIN_ID) %>%
      collect()

    smoking_concepts <- obs_concepts_all %>%
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
            filter(
              PERSON_ID %in% ids_db,
              !is.na(OBSERVATION_DATE),
              OBSERVATION_DATE <= chunk_max_d,
              OBSERVATION_CONCEPT_ID %in% smoking_concept_ids |
                VALUE_AS_CONCEPT_ID  %in% smoking_concept_ids
            ) %>%
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
        raw_smoking_dir,
        "smoking",
        is_core = TRUE
      )

      raw_baseline_smoking <- read_chunk_dir(raw_smoking_dir, empty_smoking)

      if (nrow(raw_baseline_smoking) > 0) {
        obs_name_lkp <- smoking_concepts %>%
          transmute(OBSERVATION_CONCEPT_ID = CONCEPT_ID, observation_concept_name = CONCEPT_NAME)
        val_name_lkp <- smoking_concepts %>%
          transmute(VALUE_AS_CONCEPT_ID = CONCEPT_ID, value_concept_name = CONCEPT_NAME)

        raw_baseline_smoking <- raw_baseline_smoking %>%
          left_join(obs_name_lkp, by = "OBSERVATION_CONCEPT_ID") %>%
          left_join(val_name_lkp, by = "VALUE_AS_CONCEPT_ID")

        smoking_status_patient <- raw_baseline_smoking %>%
          mutate(
            smoke_text     = tolower(paste(
              coalesce(observation_concept_name, ""),
              coalesce(value_concept_name, ""),
              coalesce(OBSERVATION_SOURCE_VALUE, ""),
              coalesce(VALUE_AS_STRING, "")
            )),
            smoking_status = case_when(
              grepl("current|every day|some day", smoke_text, perl = TRUE) ~ "current",
              grepl("former|quit|ex-smoker|past smoker", smoke_text, perl = TRUE) ~ "former",
              grepl("never", smoke_text, perl = TRUE) ~ "never",
              grepl("smoker|smoking|tobacco", smoke_text, perl = TRUE) ~ "smoker_unknown_currentness",
              TRUE ~ "unknown"
            ),
            priority = case_when(
              smoking_status == "current"                   ~ 1L,
              smoking_status == "former"                    ~ 2L,
              smoking_status == "never"                     ~ 3L,
              smoking_status == "smoker_unknown_currentness"~ 4L,
              TRUE                                          ~ 5L
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

if (STOP_IF_SMOKING_EMPTY && nrow(raw_baseline_smoking) == 0) {
  stop("Smoking extraction returned 0 rows and STOP_IF_SMOKING_EMPTY = TRUE.")
}

# =========================================================
# 4) VISIT / UTILIZATION DENSITY
# =========================================================
msg("\n=== 4) Extracting baseline visit/utilization density ===")

empty_visits <- tibble(
  PERSON_ID = character(), VISIT_OCCURRENCE_ID = integer(),
  VISIT_CONCEPT_ID = integer(), VISIT_START_DATE = as.Date(character()),
  VISIT_END_DATE = as.Date(character())
)

raw_baseline_visits <- empty_visits

baseline_util_summary <- index_spine %>%
  select(PERSON_ID) %>%
  mutate(n_visits_baseline  = 0L, n_outpatient_visits = 0L,
         n_ed_visits        = 0L, n_inpatient_visits  = 0L)

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
          filter(
            PERSON_ID %in% ids_db,
            !is.na(VISIT_START_DATE),
            VISIT_START_DATE >= chunk_min_d,
            VISIT_START_DATE <= chunk_max_d
          ) %>%
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
      raw_visit_dir,
      "visits",
      is_core = TRUE
    )

    raw_baseline_visits <- read_chunk_dir(raw_visit_dir, empty_visits)

    if (nrow(raw_baseline_visits) > 0) {
      visit_ids      <- unique(na.omit(raw_baseline_visits$VISIT_CONCEPT_ID))
      visit_concepts <- concept %>%
        filter(CONCEPT_ID %in% visit_ids) %>%
        select(CONCEPT_ID, CONCEPT_NAME) %>%
        collect() %>%
        transmute(VISIT_CONCEPT_ID = CONCEPT_ID, VISIT_CONCEPT_NAME = CONCEPT_NAME)

      baseline_util_summary <- raw_baseline_visits %>%
        left_join(visit_concepts, by = "VISIT_CONCEPT_ID") %>%
        mutate(
          vt         = tolower(coalesce(VISIT_CONCEPT_NAME, "")),
          is_op      = grepl("outpatient|office|ambulatory", vt, perl = TRUE),
          is_ed      = grepl("emergency|\\ber\\b|\\bed\\b", vt, perl = TRUE),
          is_ip      = grepl("inpatient|hospital", vt, perl = TRUE)
        ) %>%
        group_by(PERSON_ID) %>%
        summarise(
          n_visits_baseline  = n(),
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

save_pq(raw_baseline_visits,    "raw_baseline_visits.parquet")
save_pq(baseline_util_summary,  "baseline_util_summary.parquet")

if (STOP_IF_VISITS_EMPTY && nrow(raw_baseline_visits) == 0) {
  stop("Visit extraction returned 0 rows and STOP_IF_VISITS_EMPTY = TRUE.")
}

# =========================================================
# 5) BASELINE MEDICATION HISTORY
# =========================================================
msg("\n=== 5) Extracting baseline medication history ===")

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
      inner_join(
        ingredient_concepts %>% select(ANCESTOR_CONCEPT_ID = CONCEPT_ID, med_class, ingredient_name),
        by = "ANCESTOR_CONCEPT_ID"
      )

    # Re-collect drug names for descendants (already have drug_concepts_all in this scope if above worked)
    desc_names <- concept %>%
      filter(CONCEPT_ID %in% unique(med_descendants$DESCENDANT_CONCEPT_ID)) %>%
      select(CONCEPT_ID, CONCEPT_NAME, CONCEPT_CLASS_ID, VOCABULARY_ID, STANDARD_CONCEPT) %>%
      collect()

    desc_names %>%
      transmute(DESCENDANT_CONCEPT_ID = CONCEPT_ID,
                DESCENDANT_CONCEPT_NAME = CONCEPT_NAME,
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
    warning("CONCEPT_ANCESTOR unavailable — using ingredient concept IDs only for baseline meds.")
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
          filter(
            PERSON_ID %in% ids_db,
            DRUG_CONCEPT_ID %in% med_ids,
            !is.na(DRUG_EXPOSURE_START_DATE),
            DRUG_EXPOSURE_START_DATE <= chunk_max_d
          ) %>%
          select(PERSON_ID, DRUG_EXPOSURE_ID, DRUG_CONCEPT_ID, DRUG_SOURCE_CONCEPT_ID,
                 DRUG_EXPOSURE_START_DATE, DRUG_EXPOSURE_END_DATE,
                 DAYS_SUPPLY, DRUG_SOURCE_VALUE) %>%
          collect() %>%
          mutate(
            PERSON_ID                = as.character(PERSON_ID),
            DRUG_EXPOSURE_START_DATE = as.Date(DRUG_EXPOSURE_START_DATE),
            DRUG_EXPOSURE_END_DATE   = as.Date(DRUG_EXPOSURE_END_DATE)
          ) %>%
          filter(PERSON_ID %in% ids_chr) %>%
          inner_join(chunk_idx, by = "PERSON_ID") %>%
          filter(DRUG_EXPOSURE_START_DATE < index_date) %>%
          select(-index_date)
      },
      raw_med_dir,
      "baseline_meds",
      is_core = TRUE
    )

    raw_baseline_medications <- read_chunk_dir(raw_med_dir, empty_meds) %>%
      inner_join(
        med_descendant_concepts %>%
          select(DRUG_CONCEPT_ID = DESCENDANT_CONCEPT_ID, med_class, ingredient_name),
        by = "DRUG_CONCEPT_ID"
      )
  }, error = function(e) {
    warning("Baseline medication extraction failed: ", e$message)
  })
}

# Quality check: discard suspiciously small result
if (nrow(raw_baseline_medications) < 100 && length(treated_person_ids) > 1000) {
  warning(
    "raw_baseline_medications only has ", nrow(raw_baseline_medications), " rows for ",
    length(treated_person_ids), " persons. DRUG_CONCEPT_ID may not be at ingredient level. ",
    "Replacing with empty tibble."
  )
  raw_baseline_medications <- empty_meds
}

save_pq(raw_baseline_medications, "raw_baseline_medications.parquet")
msg("raw_baseline_medications rows: %s", format(nrow(raw_baseline_medications), big.mark = ","))

if (STOP_IF_BASELINE_MEDS_EMPTY && nrow(raw_baseline_medications) == 0) {
  stop("Baseline medication extraction returned 0 rows and STOP_IF_BASELINE_MEDS_EMPTY = TRUE.")
}

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
    pivot_wider(
      names_from  = med_class,
      values_from = c(has_365d, has_730d, ever_preindex, n_rx),
      names_glue  = "{.value}_{med_class}",
      values_fill = 0L
    )
} else {
  index_spine %>% select(PERSON_ID)
}

save_pq(baseline_medication_summary, "baseline_medication_summary.parquet")

# =========================================================
# 6) ASSEMBLE CANDIDATE — validate before writing final output
# =========================================================
msg("\n=== 6) Assembling candidate augmented covariate file ===")

baseline_base <- baseline_covariates_existing %>%
  select(
    PERSON_ID, index_date,
    diabetes_baseline, ckd_baseline, hf_baseline, cad_mi_baseline,
    afib_baseline, pad_baseline, cva_baseline, tia_baseline,
    hypertension_baseline, dementia_baseline
  )

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
save_pq(baseline_covariates_candidate, "baseline_covariates_patient_augmented_candidate.parquet")
msg("Candidate saved. Running validation before writing final augmented file.")

# ─────────────────────────────────────────────────────────
# 6a) DETAILED COVERAGE TABLE (always printed)
# ─────────────────────────────────────────────────────────
n_total <- nrow(baseline_covariates_candidate)

# Returns count of non-NA, non-zero values for a column (0 if column absent)
cov_n <- function(df, col) {
  if (!col %in% names(df)) return(0L)
  sum(!is.na(df[[col]]) & df[[col]] != 0, na.rm = TRUE)
}
cov_pct <- function(df, col, n) round(100 * cov_n(df, col) / n, 1)

meas_covs <- c("sbp", "dbp", "hba1c", "ldl", "hdl", "total_cholesterol",
               "triglycerides", "bmi", "creatinine", "egfr", "albuminuria")

coverage_rows <- list()

for (cv in meas_covs) {
  col   <- paste0("closest_cleaned_value__", cv)
  n_col <- paste0("n_measurements__", cv)
  n_meas <- if (n_col %in% names(baseline_covariates_candidate))
    sum(baseline_covariates_candidate[[n_col]], na.rm = TRUE) else 0L
  coverage_rows[[cv]] <- tibble(
    domain               = "measurement",
    covariate            = cv,
    n_persons_with_value = cov_n(baseline_covariates_candidate, col),
    pct_coverage         = cov_pct(baseline_covariates_candidate, col, n_total),
    total_records        = n_meas
  )
}

n_known_smoking <- sum(
  baseline_covariates_candidate$smoking_current +
  baseline_covariates_candidate$smoking_former  +
  baseline_covariates_candidate$smoking_never   > 0,
  na.rm = TRUE
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
    domain               = "medication",
    covariate            = mc,
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

# ─────────────────────────────────────────────────────────
# 6b) VALIDATION
# ─────────────────────────────────────────────────────────
msg("\n=== 6b) Validation ===")

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

check_meas("sbp")
check_meas("dbp")
check_meas("hba1c",  "HbA1c")
check_meas("ldl",    "LDL")
check_meas("bmi",    "BMI")

n_creat <- { col <- "closest_cleaned_value__creatinine"
  if (col %in% names(baseline_covariates_candidate)) sum(!is.na(baseline_covariates_candidate[[col]])) else 0L }
n_egfr  <- { col <- "closest_cleaned_value__egfr"
  if (col %in% names(baseline_covariates_candidate)) sum(!is.na(baseline_covariates_candidate[[col]])) else 0L }
if (n_creat == 0 && n_egfr == 0) {
  validation_errors <- c(validation_errors,
    "FAIL: creatinine/eGFR — 0 persons have any plausible value for either")
} else {
  msg("  PASS: creatinine/eGFR — creatinine %d persons, eGFR %d persons", n_creat, n_egfr)
}

if (n_known_smoking == 0) {
  validation_errors <- c(validation_errors,
    "FAIL: smoking — 0 persons have a known (current/former/never) status")
} else {
  msg("  PASS: smoking — %d persons with known status", n_known_smoking)
}

if (n_any_visit == 0) {
  validation_errors <- c(validation_errors,
    "FAIL: visits — 0 persons have any baseline visit record")
} else {
  msg("  PASS: visits — %d persons with >= 1 visit", n_any_visit)
}

n_nonzero_med_classes <- sum(sapply(all_med_classes, function(mc) {
  cov_n(baseline_covariates_candidate, paste0("has_365d_", mc)) > 0
}))
if (n_nonzero_med_classes < 2) {
  validation_errors <- c(validation_errors,
    sprintf("FAIL: baseline medications — only %d/%d classes have any persons (need >= 2)",
            n_nonzero_med_classes, length(all_med_classes)))
} else {
  msg("  PASS: baseline medications — %d/%d classes have >= 1 person",
      n_nonzero_med_classes, length(all_med_classes))
}

# albuminuria is optional — warn, do not fail
col_alb <- "closest_cleaned_value__albuminuria"
if (!col_alb %in% names(baseline_covariates_candidate) ||
    sum(!is.na(baseline_covariates_candidate[[col_alb]])) == 0) {
  warning("albuminuria: 0 persons have a value — optional covariate, continuing.")
}

# ─────────────────────────────────────────────────────────
# 6c) WRITE FINAL OUTPUT OR STOP
#     baseline_covariates_patient.parquet is NEVER overwritten.
# ─────────────────────────────────────────────────────────
if (length(validation_errors) > 0) {
  cat("\n=== VALIDATION FAILED ===\n")
  for (e in validation_errors) cat(" ", e, "\n")
  cat("\nbaseline_covariates_patient.parquet has NOT been overwritten.\n")
  cat("Candidate is at: baseline_covariates_patient_augmented_candidate.parquet\n")
  stop(sprintf(
    "%d validation check(s) failed. Inspect the candidate file and re-run.",
    length(validation_errors)
  ))
} else {
  msg("\n=== ALL VALIDATION CHECKS PASSED ===")
  save_pq(baseline_covariates_candidate, "baseline_covariates_patient_augmented.parquet")
  msg("Augmented file written: baseline_covariates_patient_augmented.parquet")
  msg("NOTE: baseline_covariates_patient.parquet was NOT overwritten.")
  msg("Update downstream scripts to read _augmented.parquet.")
}

# File inventory
cat("\nFinal parquet inventory:\n")
pf <- list.files(project_dir, pattern = "\\.parquet$", full.names = TRUE)
fi <- file.info(pf)
print(data.frame(file = basename(pf), size_mb = round(fi$size / 1e6, 2), row.names = NULL))

# Zip (best-effort)
zip_path <- file.path(project_dir, "tte_extracts_with_covariates.zip")
tryCatch({
  zip(zipfile = zip_path, files = pf)
  msg("Zip created: %s", zip_path)
}, error = function(e) {
  warning("Zip failed: ", e$message)
})

msg("\nDONE: missing covariate extraction completed at %s", format(Sys.time()))

# Best-effort RStudio Files pane navigation for easy download
output_dir_to_open <- if (exists("zip_path") && file.exists(zip_path)) dirname(zip_path) else project_dir

cat("\nOutput directory for download:\n")
cat(normalizePath(output_dir_to_open, mustWork = FALSE), "\n")

try({
  if (requireNamespace("rstudioapi", quietly = TRUE) && rstudioapi::isAvailable()) {
    rstudioapi::filesPaneNavigate(output_dir_to_open)
  }
}, silent = TRUE)
