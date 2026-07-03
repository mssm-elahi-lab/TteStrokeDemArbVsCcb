library(dplyr)
library(dbplyr)
library(DBI)

# =========================================================
# DIAGNOSTIC SCRIPT: Verify condition_occurrence field content
#
# Purpose: Before running the full extraction (01_omop_extract.R),
# confirm:
#   (a) What vocabulary/content lives in CONDITION_SOURCE_CONCEPT_ID
#   (b) What vocabulary/content lives in CONDITION_CONCEPT_ID
#   (c) How complete the ICD->SNOMED "Maps to" mapping is for our codes
#   (d) Whether filtering on CONDITION_CONCEPT_ID (SNOMED) could miss
#       records where the ETL failed to map (CONDITION_CONCEPT_ID = 0)
#
# Run this script first. Review the printed output before trusting
# the extraction strategy in 01_omop_extract.R.
# =========================================================

cdm_schema <- "CDMDEID"   # edit to match

# Assumes conn is already open (same as 01_omop_extract.R)
condition_occurrence <- tbl(conn, in_schema(cdm_schema, "CONDITION_OCCURRENCE"))
concept              <- tbl(conn, in_schema(cdm_schema, "CONCEPT"))
concept_relationship <- tbl(conn, in_schema(cdm_schema, "CONCEPT_RELATIONSHIP"))

# ---------------------------------------------------------
# A) What vocabularies are in CONDITION_SOURCE_CONCEPT_ID?
#    If this is Epic EDG local IDs, the vocabulary will NOT be
#    ICD10CM or ICD9CM -- it will be something like "EPIC EDG .1".
#    If this IS ICD OMOP concept IDs, vocabulary will be ICD10CM/ICD9CM.
# ---------------------------------------------------------
cat("\n=== A) Vocabularies in CONDITION_SOURCE_CONCEPT_ID ===\n")
source_concept_vocabs <- condition_occurrence %>%
    filter(!is.na(CONDITION_SOURCE_CONCEPT_ID),
           CONDITION_SOURCE_CONCEPT_ID != 0L) %>%
    inner_join(
        concept %>% select(CONCEPT_ID, VOCABULARY_ID, DOMAIN_ID),
        by = c("CONDITION_SOURCE_CONCEPT_ID" = "CONCEPT_ID")
    ) %>%
    group_by(VOCABULARY_ID, DOMAIN_ID) %>%
    summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID), .groups = "drop") %>%
    arrange(desc(n_rows)) %>%
    collect()

print(source_concept_vocabs, n = 30)

# ---------------------------------------------------------
# B) What vocabularies are in CONDITION_CONCEPT_ID (standard)?
#    Should be almost entirely SNOMED if OMOP ETL is correct.
#    Any rows with CONDITION_CONCEPT_ID = 0 are unmapped records
#    that ONLY the source concept ID approach can catch.
# ---------------------------------------------------------
cat("\n=== B) Vocabularies in CONDITION_CONCEPT_ID (standard field) ===\n")
standard_concept_vocabs <- condition_occurrence %>%
    group_by(CONDITION_CONCEPT_ID) %>%
    summarise(n_rows = n(), .groups = "drop") %>%
    left_join(
        concept %>% select(CONCEPT_ID, VOCABULARY_ID),
        by = c("CONDITION_CONCEPT_ID" = "CONCEPT_ID")
    ) %>%
    mutate(VOCABULARY_ID = coalesce(VOCABULARY_ID, "UNMAPPED (concept_id=0 or missing)")) %>%
    group_by(VOCABULARY_ID) %>%
    summarise(n_rows = sum(n_rows), .groups = "drop") %>%
    arrange(desc(n_rows)) %>%
    collect()

print(standard_concept_vocabs, n = 30)

# ---------------------------------------------------------
# C) How many condition rows have CONDITION_CONCEPT_ID = 0?
#    These are records the ETL FAILED to map to a standard concept.
#    They are completely invisible to SNOMED-based filtering.
#
#    NOTE: Uses two separate counts to avoid boolean-cast SQL that
#    is incompatible with SAP HANA (which rejects CAST(col = 0 AS INT)).
# ---------------------------------------------------------
cat("\n=== C) Rows with CONDITION_CONCEPT_ID = 0 (unmapped by ETL) ===\n")
total_rows     <- condition_occurrence %>% summarise(n = n()) %>% collect() %>% pull(n)
zero_rows      <- condition_occurrence %>% filter(CONDITION_CONCEPT_ID == 0L) %>% summarise(n = n()) %>% collect() %>% pull(n)
nonzero_rows   <- total_rows - zero_rows

unmapped_etl_rows <- tibble::tibble(
    total_rows     = total_rows,
    concept_id_zero    = zero_rows,
    concept_id_nonzero = nonzero_rows
)

print(unmapped_etl_rows)
cat(sprintf(
    "%.1f%% of condition rows have CONDITION_CONCEPT_ID = 0 (ETL-unmapped)\n",
    100 * zero_rows / total_rows
))

# ---------------------------------------------------------
# D) ICD OMOP concept IDs we intend to use -> SNOMED coverage check
#    How many of our ICD concept IDs successfully map via "Maps to"?
# ---------------------------------------------------------
cat("\n=== D) ICD->SNOMED 'Maps to' coverage for our target concept IDs ===\n")

our_icd_concept_ids <- as.integer(c(
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

icd_to_snomed_check <- concept_relationship %>%
    filter(
        CONCEPT_ID_1 %in% our_icd_concept_ids,
        RELATIONSHIP_ID == "Maps to",
        is.na(INVALID_REASON)
    ) %>%
    select(CONCEPT_ID_1, CONCEPT_ID_2) %>%
    collect()

mapped_ids   <- unique(icd_to_snomed_check$CONCEPT_ID_1)
unmapped_ids <- setdiff(our_icd_concept_ids, mapped_ids)

cat(sprintf(
    "%d of %d ICD concept IDs successfully map to a SNOMED concept\n",
    length(mapped_ids), length(our_icd_concept_ids)
))

if (length(unmapped_ids) > 0) {
    cat("\nWARNING — these ICD concept IDs have NO 'Maps to' SNOMED mapping:\n")
    # Look them up by name
    unmapped_detail <- concept %>%
        filter(CONCEPT_ID %in% unmapped_ids) %>%
        select(CONCEPT_ID, CONCEPT_CODE, CONCEPT_NAME, VOCABULARY_ID) %>%
        collect()
    print(unmapped_detail)
} else {
    cat("All ICD concept IDs have at least one SNOMED mapping.\n")
}

# ---------------------------------------------------------
# E) SNOMED concept ID fan-out check
#    How many distinct SNOMED IDs result from our ICD concept IDs?
#    Large fan-out = broad net; verify these are clinically correct.
# ---------------------------------------------------------
cat("\n=== E) Resulting SNOMED concept IDs (fan-out check) ===\n")
snomed_detail <- icd_to_snomed_check %>%
    left_join(
        concept %>%
            select(CONCEPT_ID, CONCEPT_NAME, VOCABULARY_ID) %>%
            collect(),
        by = c("CONCEPT_ID_2" = "CONCEPT_ID")
    ) %>%
    distinct(CONCEPT_ID_2, CONCEPT_NAME, VOCABULARY_ID) %>%
    arrange(VOCABULARY_ID, CONCEPT_NAME)

cat(sprintf(
    "%d distinct SNOMED concept IDs mapped from %d ICD concept IDs\n",
    nrow(snomed_detail), length(our_icd_concept_ids)
))
print(snomed_detail, n = 100)

# ---------------------------------------------------------
# F) If CONDITION_SOURCE_CONCEPT_ID IS ICD-based:
#    Count overlap with our target ICD concept IDs in actual data.
# ---------------------------------------------------------
cat("\n=== F) How many condition rows match our ICD concept IDs via SOURCE field? ===\n")
source_match_count <- condition_occurrence %>%
    filter(CONDITION_SOURCE_CONCEPT_ID %in% our_icd_concept_ids) %>%
    summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID)) %>%
    collect()
print(source_match_count)

cat("\n=== F2) How many condition rows match via STANDARD (SNOMED) field? ===\n")
# Use SNOMED IDs derived above
snomed_ids_from_map <- unique(icd_to_snomed_check$CONCEPT_ID_2)
standard_match_count <- condition_occurrence %>%
    filter(CONDITION_CONCEPT_ID %in% snomed_ids_from_map) %>%
    summarise(n_rows = n(), n_persons = n_distinct(PERSON_ID)) %>%
    collect()
print(standard_match_count)

cat("\n=== SUMMARY ===\n")
cat("Source-field match  : rows =", source_match_count$n_rows,
    "| persons =", source_match_count$n_persons, "\n")
cat("Standard-field match: rows =", standard_match_count$n_rows,
    "| persons =", standard_match_count$n_persons, "\n")
cat(
    "\nIf source-field >> standard-field, the ETL is not mapping ICD->SNOMED reliably\n",
    "and 01_omop_extract.R will MISS those records.\n",
    "In that case, switch the condition filter to CONDITION_SOURCE_CONCEPT_ID.\n"
)
