library(dplyr)
library(dbplyr)
library(DBI)

# Produces a full 3-way mapping table:
#   ICD code | ICD English name | ICD OMOP concept ID
#   -> SNOMED concept ID | SNOMED English name
#
# Assumes conn is already open.

cdm_schema       <- "CDMDEID"
concept          <- tbl(conn, in_schema(cdm_schema, "CONCEPT"))
concept_relation <- tbl(conn, in_schema(cdm_schema, "CONCEPT_RELATIONSHIP"))

# ----------------------------------------------------------
# 1) ICD concept ID reference table (fully verified against DB)
# ----------------------------------------------------------
icd_ref <- tibble::tribble(
    ~icd_concept_id, ~icd_code,   ~icd_vocab,  ~condition,       ~icd_english,

    # Hypertension
    35207668L, "I10",     "ICD10CM", "Hypertension", "Essential (primary) hypertension",
    1569120L,  "I11",     "ICD10CM", "Hypertension", "Hypertensive heart disease",
    1569121L,  "I12",     "ICD10CM", "Hypertension", "Hypertensive chronic kidney disease",
    1569122L,  "I13",     "ICD10CM", "Hypertension", "Hypertensive heart and chronic kidney disease",
    1569124L,  "I15",     "ICD10CM", "Hypertension", "Secondary hypertension",
    44833556L, "401",     "ICD9CM",  "Hypertension", "Essential hypertension",
    44832366L, "402",     "ICD9CM",  "Hypertension", "Hypertensive heart disease",
    44832367L, "403",     "ICD9CM",  "Hypertension", "Hypertensive chronic kidney disease",
    44827780L, "404",     "ICD9CM",  "Hypertension", "Hypertensive heart and chronic kidney disease",
    44832370L, "405",     "ICD9CM",  "Hypertension", "Secondary hypertension",

    # Dementia
    45533052L, "F00",     "ICD10",   "Dementia", "Dementia in Alzheimer disease",
    1568087L,  "F01",     "ICD10CM", "Dementia", "Vascular dementia",
    1568088L,  "F02",     "ICD10CM", "Dementia", "Dementia in other diseases classified elsewhere",
    35207114L, "F03",     "ICD10CM", "Dementia", "Unspecified dementia",
    1568293L,  "G30",     "ICD10CM", "Dementia", "Alzheimer's disease",
    1568295L,  "G31.0",   "ICD10CM", "Dementia", "Frontotemporal dementia",
    35207360L, "G31.1",   "ICD10CM", "Dementia", "Senile degeneration of brain, NEC",
    45547730L, "G31.83",  "ICD10CM", "Dementia", "Neurocognitive disorder with Lewy bodies",
    45595932L, "G31.84",  "ICD10CM", "Dementia", "Mild cognitive impairment, uncertain etiology",
    45553737L, "R41.89",  "ICD10CM", "Dementia", "Other symptoms involving cognitive functions",
    45534454L, "R41.841", "ICD10CM", "Dementia", "Cognitive communication deficit",
    45553736L, "R41.81",  "ICD10CM", "Dementia", "Age-related cognitive decline",
    44824105L, "290",     "ICD9CM",  "Dementia", "Dementias",
    44821814L, "294.1",   "ICD9CM",  "Dementia", "Dementia in conditions classified elsewhere",
    44826536L, "331",     "ICD9CM",  "Dementia", "Other cerebral degenerations",
    44827645L, "294.8",   "ICD9CM",  "Dementia", "Other persistent mental disorders due to conditions",
    44834585L, "294.9",   "ICD9CM",  "Dementia", "Unspecified persistent mental disorders due to conditions",
    44832709L, "780.93",  "ICD9CM",  "Dementia", "Memory loss",

    # Stroke
    1569184L,  "I60",     "ICD10CM", "Stroke", "Nontraumatic subarachnoid hemorrhage",
    1569190L,  "I61",     "ICD10CM", "Stroke", "Nontraumatic intracerebral hemorrhage",
    1569191L,  "I62",     "ICD10CM", "Stroke", "Other nontraumatic intracranial hemorrhage",
    1569193L,  "I63",     "ICD10CM", "Stroke", "Cerebral infarction",
    45548032L, "I64",     "ICD10",   "Stroke", "Stroke, not specified as haemorrhage or infarction",
    1569218L,  "I65",     "ICD10CM", "Stroke", "Occlusion/stenosis of precerebral arteries, no infarction",
    1569221L,  "I66",     "ICD10CM", "Stroke", "Occlusion/stenosis of cerebral arteries, no infarction",
    1569225L,  "I67",     "ICD10CM", "Stroke", "Other cerebrovascular diseases",
    1569227L,  "I68",     "ICD10CM", "Stroke", "Cerebrovascular disorders in diseases classified elsewhere",
    1569228L,  "I69",     "ICD10CM", "Stroke", "Sequelae of cerebrovascular disease",
    44820872L, "430",     "ICD9CM",  "Stroke", "Subarachnoid hemorrhage",
    44835946L, "431",     "ICD9CM",  "Stroke", "Intracerebral hemorrhage",
    44835947L, "432",     "ICD9CM",  "Stroke", "Other and unspecified intracranial hemorrhage",
    44820873L, "433",     "ICD9CM",  "Stroke", "Occlusion and stenosis of precerebral arteries",
    44824253L, "434",     "ICD9CM",  "Stroke", "Occlusion of cerebral arteries",
    44820875L, "435",     "ICD9CM",  "Stroke", "Transient cerebral ischemia",
    44835952L, "436",     "ICD9CM",  "Stroke", "Acute, but ill-defined, cerebrovascular disease",
    44832388L, "437",     "ICD9CM",  "Stroke", "Other and ill-defined cerebrovascular disease",
    44831252L, "438",     "ICD9CM",  "Stroke", "Late effects of cerebrovascular disease",

    # Diabetes
    1567940L,  "E10",     "ICD10CM", "Diabetes",     "Type 1 diabetes mellitus",
    1567956L,  "E11",     "ICD10CM", "Diabetes",     "Type 2 diabetes mellitus",
    1567972L,  "E13",     "ICD10CM", "Diabetes",     "Other specified diabetes mellitus",
    44833365L, "250",     "ICD9CM",  "Diabetes",     "Diabetes mellitus",

    # CKD
    1571486L,  "N18",     "ICD10CM", "CKD",          "Chronic kidney disease (CKD)",
    44830172L, "585",     "ICD9CM",  "CKD",          "Chronic kidney disease (CKD)",

    # Heart Failure
    1569178L,  "I50",     "ICD10CM", "Heart Failure","Heart failure",
    44824250L, "428",     "ICD9CM",  "Heart Failure","Heart failure",

    # CAD/MI
    1569125L,  "I20",     "ICD10CM", "CAD/MI",       "Angina pectoris",
    1569126L,  "I21",     "ICD10CM", "CAD/MI",       "Acute myocardial infarction",
    1569130L,  "I22",     "ICD10CM", "CAD/MI",       "Subsequent STEMI/NSTEMI",
    1569133L,  "I25",     "ICD10CM", "CAD/MI",       "Chronic ischemic heart disease (CAD)",
    44832372L, "410",     "ICD9CM",  "CAD/MI",       "Acute myocardial infarction",
    44834725L, "411",     "ICD9CM",  "CAD/MI",       "Other acute/subacute ischemic heart disease",
    44835930L, "413",     "ICD9CM",  "CAD/MI",       "Angina pectoris",
    44827784L, "414",     "ICD9CM",  "CAD/MI",       "Other chronic ischemic heart disease (CAD)",

    # AFib
    1569170L,  "I48",     "ICD10CM", "AFib",         "Atrial fibrillation and flutter",
    44824248L, "427.3",   "ICD9CM",  "AFib",         "Atrial fibrillation and flutter",
    44821957L, "427.31",  "ICD9CM",  "AFib",         "Atrial fibrillation",
    44820868L, "427.32",  "ICD9CM",  "AFib",         "Atrial flutter",

    # PAD
    1569271L,  "I70",     "ICD10CM", "PAD",          "Atherosclerosis",
    1569324L,  "I73",     "ICD10CM", "PAD",          "Other peripheral vascular diseases",
    44825446L, "440",     "ICD9CM",  "PAD",          "Atherosclerosis",
    44826654L, "443",     "ICD9CM",  "PAD",          "Other peripheral vascular disease",

    # CVA
    1569193L,  "I63",     "ICD10CM", "CVA",          "Cerebral infarction",
    45548032L, "I64",     "ICD10",   "CVA",          "Stroke NOS",

    # TIA
    1568360L,  "G45",     "ICD10CM", "TIA",          "Transient cerebral ischemic attacks and related syndromes",
    1568361L,  "G46",     "ICD10CM", "TIA",          "Vascular syndromes of brain in cerebrovascular diseases",
    44820875L, "435",     "ICD9CM",  "TIA",          "Transient cerebral ischemia"
)

# ----------------------------------------------------------
# 2) Pull ICD -> SNOMED mappings from concept_relationship
# ----------------------------------------------------------
all_icd_ids <- unique(icd_ref$icd_concept_id)

icd_to_snomed <- concept_relation %>%
    filter(
        CONCEPT_ID_1 %in% all_icd_ids,
        RELATIONSHIP_ID == "Maps to",
        is.na(INVALID_REASON)
    ) %>%
    select(icd_concept_id = CONCEPT_ID_1, snomed_concept_id = CONCEPT_ID_2) %>%
    collect()

# ----------------------------------------------------------
# 3) Pull SNOMED concept names
# ----------------------------------------------------------
snomed_ids <- unique(icd_to_snomed$snomed_concept_id)

snomed_names <- concept %>%
    filter(CONCEPT_ID %in% snomed_ids) %>%
    select(snomed_concept_id = CONCEPT_ID,
           snomed_code        = CONCEPT_CODE,
           snomed_english     = CONCEPT_NAME,
           snomed_vocab       = VOCABULARY_ID) %>%
    collect()

# ----------------------------------------------------------
# 4) Join everything into one flat table
# ----------------------------------------------------------
mapping_table <- icd_ref %>%
    distinct(icd_concept_id, icd_code, icd_vocab, condition, icd_english) %>%
    left_join(icd_to_snomed, by = "icd_concept_id") %>%
    left_join(snomed_names,  by = "snomed_concept_id") %>%
    arrange(condition, icd_vocab, icd_code) %>%
    select(
        condition,
        icd_code, icd_vocab, icd_concept_id, icd_english,
        snomed_concept_id, snomed_code, snomed_english
    )

# ----------------------------------------------------------
# 5) Print
# ----------------------------------------------------------
cat("\n=== Full ICD -> OMOP Concept ID -> SNOMED Mapping Table ===\n\n")
print(mapping_table, n = Inf, width = Inf)

# Flag any ICD codes with no SNOMED mapping
no_snomed <- mapping_table %>% filter(is.na(snomed_concept_id))
if (nrow(no_snomed) > 0) {
    cat("\nWARNING — ICD codes with no SNOMED mapping (will be missed in extraction):\n")
    print(no_snomed %>% select(condition, icd_code, icd_vocab, icd_english), n = Inf)
}

# ----------------------------------------------------------
# 6) Save as CSV
# ----------------------------------------------------------
out_path <- "data/results/icd_omop_snomed_mapping_table.csv"
write.csv(mapping_table, out_path, row.names = FALSE)
cat("\nSaved to", out_path, "\n")
