library(dplyr)
library(dbplyr)
library(DBI)

# =========================================================
# DIAGNOSTIC: Verify ICD OMOP concept IDs against the local
# CONCEPT vocabulary table.
#
# The CONCEPT table contains NO patient data — it is the OMOP
# vocabulary reference (codes, names, vocabularies). It is
# present and unmasked even in maximally de-identified OMOP
# exports. Querying it does not require any patient-level access.
#
# Purpose: Confirm that every concept ID we looked up via the
# Athena web API (athena.ohdsi.org) actually exists in this
# database's CONCEPT table with the expected code and name.
# A mismatch would mean the local OMOP vocabulary snapshot is
# older or newer than what Athena returned.
# =========================================================

cdm_schema <- "CDMDEID"   # edit to match your schema
concept    <- tbl(conn, in_schema(cdm_schema, "CONCEPT"))

# ---------------------------------------------------------
# All ICD concept IDs we looked up, grouped by condition
# ---------------------------------------------------------
icd_concept_lookup <- tibble::tribble(
    ~concept_id, ~expected_code,  ~expected_vocab, ~condition,

    # Hypertension — ICD-10-CM
    35207668L,  "I10",    "ICD10CM", "Hypertension",
    1569120L,   "I11",    "ICD10CM", "Hypertension",
    1569121L,   "I12",    "ICD10CM", "Hypertension",
    1569122L,   "I13",    "ICD10CM", "Hypertension",
    1569124L,   "I15",    "ICD10CM", "Hypertension",
    # Hypertension — ICD-9-CM
    44833556L,  "401",    "ICD9CM",  "Hypertension",
    44832366L,  "402",    "ICD9CM",  "Hypertension",
    44832367L,  "403",    "ICD9CM",  "Hypertension",
    44827780L,  "404",    "ICD9CM",  "Hypertension",
    44832370L,  "405",    "ICD9CM",  "Hypertension",

    # Dementia — ICD-10-CM
    45533052L,  "F00",    "ICD10",   "Dementia",
    1568087L,   "F01",    "ICD10CM", "Dementia",
    1568088L,   "F02",    "ICD10CM", "Dementia",
    35207114L,  "F03",    "ICD10CM", "Dementia",
    1568293L,   "G30",    "ICD10CM", "Dementia",
    1568295L,   "G31.0",  "ICD10CM", "Dementia",
    35207360L,  "G31.1",  "ICD10CM", "Dementia",
    45547730L,  "G31.83", "ICD10CM", "Dementia",
    45595932L,  "G31.84", "ICD10CM", "Dementia",
    45553737L,  "R41.89", "ICD10CM", "Dementia",
    45534454L,  "R41.841","ICD10CM", "Dementia",
    45553736L,  "R41.81", "ICD10CM", "Dementia",
    # Dementia — ICD-9-CM
    44824105L,  "290",    "ICD9CM",  "Dementia",
    44821814L,  "294.1",  "ICD9CM",  "Dementia",
    44826536L,  "331",    "ICD9CM",  "Dementia",
    44827645L,  "294.8",  "ICD9CM",  "Dementia",
    44834585L,  "294.9",  "ICD9CM",  "Dementia",
    44832709L,  "780.93", "ICD9CM",  "Dementia",

    # Stroke — ICD-10-CM
    1569184L,   "I60",    "ICD10CM", "Stroke",
    1569190L,   "I61",    "ICD10CM", "Stroke",
    1569191L,   "I62",    "ICD10CM", "Stroke",
    1569193L,   "I63",    "ICD10CM", "Stroke",
    45548032L,  "I64",    "ICD10",   "Stroke",
    1569218L,   "I65",    "ICD10CM", "Stroke",
    1569221L,   "I66",    "ICD10CM", "Stroke",
    1569225L,   "I67",    "ICD10CM", "Stroke",
    1569227L,   "I68",    "ICD10CM", "Stroke",
    1569228L,   "I69",    "ICD10CM", "Stroke",
    # Stroke — ICD-9-CM
    44820872L,  "430",    "ICD9CM",  "Stroke",
    44835946L,  "431",    "ICD9CM",  "Stroke",
    44835947L,  "432",    "ICD9CM",  "Stroke",
    44820873L,  "433",    "ICD9CM",  "Stroke",
    44824253L,  "434",    "ICD9CM",  "Stroke",
    44820875L,  "435",    "ICD9CM",  "Stroke",
    44835952L,  "436",    "ICD9CM",  "Stroke",
    44832388L,  "437",    "ICD9CM",  "Stroke",
    44831252L,  "438",    "ICD9CM",  "Stroke",

    # Diabetes — ICD-10-CM
    1567940L,   "E10",    "ICD10CM", "Diabetes",
    1567956L,   "E11",    "ICD10CM", "Diabetes",
    1567972L,   "E13",    "ICD10CM", "Diabetes",
    # Diabetes — ICD-9-CM
    44833365L,  "250",    "ICD9CM",  "Diabetes",

    # CKD — ICD-10-CM
    1571486L,   "N18",    "ICD10CM", "CKD",
    # CKD — ICD-9-CM
    44830172L,  "585",    "ICD9CM",  "CKD",

    # Heart Failure — ICD-10-CM
    1569178L,   "I50",    "ICD10CM", "Heart Failure",
    # Heart Failure — ICD-9-CM
    44824250L,  "428",    "ICD9CM",  "Heart Failure",

    # CAD/MI — ICD-10-CM
    1569125L,   "I20",    "ICD10CM", "CAD/MI",
    1569126L,   "I21",    "ICD10CM", "CAD/MI",
    1569130L,   "I22",    "ICD10CM", "CAD/MI",
    1569133L,   "I25",    "ICD10CM", "CAD/MI",
    # CAD/MI — ICD-9-CM
    44832372L,  "410",    "ICD9CM",  "CAD/MI",
    44834725L,  "411",    "ICD9CM",  "CAD/MI",
    44835930L,  "413",    "ICD9CM",  "CAD/MI",
    44827784L,  "414",    "ICD9CM",  "CAD/MI",

    # AFib — ICD-10-CM
    1569170L,   "I48",    "ICD10CM", "AFib",
    # AFib — ICD-9-CM
    44824248L,  "427.3",  "ICD9CM",  "AFib",
    44821957L,  "427.31", "ICD9CM",  "AFib",
    44820868L,  "427.32", "ICD9CM",  "AFib",

    # PAD — ICD-10-CM
    1569271L,   "I70",    "ICD10CM", "PAD",
    1569324L,   "I73",    "ICD10CM", "PAD",
    # PAD — ICD-9-CM
    44825446L,  "440",    "ICD9CM",  "PAD",
    44826654L,  "443",    "ICD9CM",  "PAD",

    # CVA — ICD-10-CM
    1569193L,   "I63",    "ICD10CM", "CVA",
    45548032L,  "I64",    "ICD10",   "CVA",

    # TIA — ICD-10-CM
    1568360L,   "G45",    "ICD10CM", "TIA",
    1568361L,   "G46",    "ICD10CM", "TIA",
    # TIA — ICD-9-CM
    44820875L,  "435",    "ICD9CM",  "TIA"
)

all_ids <- unique(icd_concept_lookup$concept_id)
cat("Querying CONCEPT table for", length(all_ids), "concept IDs...\n")

# Pull matching rows from the local CONCEPT table
found_in_db <- concept %>%
    filter(CONCEPT_ID %in% all_ids) %>%
    select(CONCEPT_ID, CONCEPT_CODE, CONCEPT_NAME, VOCABULARY_ID, INVALID_REASON) %>%
    collect()

cat("Found in local CONCEPT table:", nrow(found_in_db), "of", length(all_ids), "\n\n")

# ---------------------------------------------------------
# Join expected vs actual and flag mismatches
# ---------------------------------------------------------
verification <- icd_concept_lookup %>%
    distinct(concept_id, expected_code, expected_vocab, condition) %>%
    left_join(
        found_in_db %>%
            rename(concept_id = CONCEPT_ID,
                   actual_code  = CONCEPT_CODE,
                   actual_name  = CONCEPT_NAME,
                   actual_vocab = VOCABULARY_ID,
                   invalid      = INVALID_REASON),
        by = "concept_id"
    ) %>%
    mutate(
        status = case_when(
            is.na(actual_code)                          ~ "MISSING from DB",
            actual_vocab != expected_vocab              ~ "VOCAB MISMATCH",
            toupper(actual_code) != toupper(expected_code) ~ "CODE MISMATCH",
            !is.na(invalid)                             ~ "INVALID/DEPRECATED",
            TRUE                                        ~ "OK"
        )
    )

# ---------------------------------------------------------
# Summary
# ---------------------------------------------------------
cat("=== Verification Summary by Status ===\n")
print(table(verification$status))

cat("\n=== Problems (non-OK rows) ===\n")
problems <- verification %>% filter(status != "OK")
if (nrow(problems) == 0) {
    cat("None. All concept IDs verified against local CONCEPT table.\n")
} else {
    print(problems %>%
        select(concept_id, condition, expected_code, expected_vocab,
               actual_code, actual_vocab, actual_name, status),
        n = Inf)
}

cat("\n=== Full Verification Table ===\n")
print(
    verification %>%
        arrange(condition, expected_vocab, expected_code) %>%
        select(condition, concept_id, expected_code, expected_vocab,
               actual_name, status),
    n = Inf
)
