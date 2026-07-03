"""
Fetch ICD -> SNOMED 'Maps to' mappings for all project concept IDs
using the public OHDSI ATLAS demo API.
"""
import json, time, subprocess

BASE = "https://atlas-demo.ohdsi.org/WebAPI/vocabulary/SYNPUF1K/concept/{cid}/related"

def fetch(cid):
    url = BASE.format(cid=cid)
    r = subprocess.run(
        ["curl", "-s", "--compressed", url,
         "-H", "Accept: application/json",
         "-H", "User-Agent: Mozilla/5.0"],
        capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return json.loads(r.stdout)

icd_ref = [
    # (icd_concept_id, icd_code, icd_vocab, condition, icd_english)
    (35207668, "I10",     "ICD10CM", "Hypertension", "Essential (primary) hypertension"),
    (1569120,  "I11",     "ICD10CM", "Hypertension", "Hypertensive heart disease"),
    (1569121,  "I12",     "ICD10CM", "Hypertension", "Hypertensive chronic kidney disease"),
    (1569122,  "I13",     "ICD10CM", "Hypertension", "Hypertensive heart and chronic kidney disease"),
    (1569124,  "I15",     "ICD10CM", "Hypertension", "Secondary hypertension"),
    (44833556, "401",     "ICD9CM",  "Hypertension", "Essential hypertension"),
    (44832366, "402",     "ICD9CM",  "Hypertension", "Hypertensive heart disease"),
    (44832367, "403",     "ICD9CM",  "Hypertension", "Hypertensive chronic kidney disease"),
    (44827780, "404",     "ICD9CM",  "Hypertension", "Hypertensive heart and chronic kidney disease"),
    (44832370, "405",     "ICD9CM",  "Hypertension", "Secondary hypertension"),
    (45533052, "F00",     "ICD10",   "Dementia",     "Dementia in Alzheimer disease"),
    (1568087,  "F01",     "ICD10CM", "Dementia",     "Vascular dementia"),
    (1568088,  "F02",     "ICD10CM", "Dementia",     "Dementia in other diseases classified elsewhere"),
    (35207114, "F03",     "ICD10CM", "Dementia",     "Unspecified dementia"),
    (1568293,  "G30",     "ICD10CM", "Dementia",     "Alzheimer's disease"),
    (1568295,  "G31.0",   "ICD10CM", "Dementia",     "Frontotemporal dementia"),
    (35207360, "G31.1",   "ICD10CM", "Dementia",     "Senile degeneration of brain, NEC"),
    (45547730, "G31.83",  "ICD10CM", "Dementia",     "Neurocognitive disorder with Lewy bodies"),
    (45595932, "G31.84",  "ICD10CM", "Dementia",     "Mild cognitive impairment, uncertain etiology"),
    (45553737, "R41.89",  "ICD10CM", "Dementia",     "Other symptoms involving cognitive functions"),
    (45534454, "R41.841", "ICD10CM", "Dementia",     "Cognitive communication deficit"),
    (45553736, "R41.81",  "ICD10CM", "Dementia",     "Age-related cognitive decline"),
    (44824105, "290",     "ICD9CM",  "Dementia",     "Dementias"),
    (44821814, "294.1",   "ICD9CM",  "Dementia",     "Dementia in conditions classified elsewhere"),
    (44826536, "331",     "ICD9CM",  "Dementia",     "Other cerebral degenerations"),
    (44827645, "294.8",   "ICD9CM",  "Dementia",     "Other persistent mental disorders due to conditions"),
    (44834585, "294.9",   "ICD9CM",  "Dementia",     "Unspecified persistent mental disorders due to conditions"),
    (44832709, "780.93",  "ICD9CM",  "Dementia",     "Memory loss"),
    (1569184,  "I60",     "ICD10CM", "Stroke",       "Nontraumatic subarachnoid hemorrhage"),
    (1569190,  "I61",     "ICD10CM", "Stroke",       "Nontraumatic intracerebral hemorrhage"),
    (1569191,  "I62",     "ICD10CM", "Stroke",       "Other nontraumatic intracranial hemorrhage"),
    (1569193,  "I63",     "ICD10CM", "Stroke/CVA",   "Cerebral infarction"),
    (45548032, "I64",     "ICD10",   "Stroke/CVA",   "Stroke, not specified as haemorrhage or infarction"),
    (1569218,  "I65",     "ICD10CM", "Stroke",       "Occlusion/stenosis of precerebral arteries, no infarction"),
    (1569221,  "I66",     "ICD10CM", "Stroke",       "Occlusion/stenosis of cerebral arteries, no infarction"),
    (1569225,  "I67",     "ICD10CM", "Stroke",       "Other cerebrovascular diseases"),
    (1569227,  "I68",     "ICD10CM", "Stroke",       "Cerebrovascular disorders in diseases classified elsewhere"),
    (1569228,  "I69",     "ICD10CM", "Stroke",       "Sequelae of cerebrovascular disease"),
    (44820872, "430",     "ICD9CM",  "Stroke",       "Subarachnoid hemorrhage"),
    (44835946, "431",     "ICD9CM",  "Stroke",       "Intracerebral hemorrhage"),
    (44835947, "432",     "ICD9CM",  "Stroke",       "Other and unspecified intracranial hemorrhage"),
    (44820873, "433",     "ICD9CM",  "Stroke",       "Occlusion and stenosis of precerebral arteries"),
    (44824253, "434",     "ICD9CM",  "Stroke",       "Occlusion of cerebral arteries"),
    (44820875, "435",     "ICD9CM",  "Stroke/TIA",   "Transient cerebral ischemia"),
    (44835952, "436",     "ICD9CM",  "Stroke",       "Acute, but ill-defined, cerebrovascular disease"),
    (44832388, "437",     "ICD9CM",  "Stroke",       "Other and ill-defined cerebrovascular disease"),
    (44831252, "438",     "ICD9CM",  "Stroke",       "Late effects of cerebrovascular disease"),
    (1567940,  "E10",     "ICD10CM", "Diabetes",     "Type 1 diabetes mellitus"),
    (1567956,  "E11",     "ICD10CM", "Diabetes",     "Type 2 diabetes mellitus"),
    (1567972,  "E13",     "ICD10CM", "Diabetes",     "Other specified diabetes mellitus"),
    (44833365, "250",     "ICD9CM",  "Diabetes",     "Diabetes mellitus"),
    (1571486,  "N18",     "ICD10CM", "CKD",          "Chronic kidney disease"),
    (44830172, "585",     "ICD9CM",  "CKD",          "Chronic kidney disease"),
    (1569178,  "I50",     "ICD10CM", "Heart Failure","Heart failure"),
    (44824250, "428",     "ICD9CM",  "Heart Failure","Heart failure"),
    (1569125,  "I20",     "ICD10CM", "CAD/MI",       "Angina pectoris"),
    (1569126,  "I21",     "ICD10CM", "CAD/MI",       "Acute myocardial infarction"),
    (1569130,  "I22",     "ICD10CM", "CAD/MI",       "Subsequent STEMI/NSTEMI"),
    (1569133,  "I25",     "ICD10CM", "CAD/MI",       "Chronic ischemic heart disease (CAD)"),
    (44832372, "410",     "ICD9CM",  "CAD/MI",       "Acute myocardial infarction"),
    (44834725, "411",     "ICD9CM",  "CAD/MI",       "Other acute/subacute ischemic heart disease"),
    (44835930, "413",     "ICD9CM",  "CAD/MI",       "Angina pectoris"),
    (44827784, "414",     "ICD9CM",  "CAD/MI",       "Other chronic ischemic heart disease (CAD)"),
    (1569170,  "I48",     "ICD10CM", "AFib",         "Atrial fibrillation and flutter"),
    (44824248, "427.3",   "ICD9CM",  "AFib",         "Atrial fibrillation and flutter"),
    (44821957, "427.31",  "ICD9CM",  "AFib",         "Atrial fibrillation"),
    (44820868, "427.32",  "ICD9CM",  "AFib",         "Atrial flutter"),
    (1569271,  "I70",     "ICD10CM", "PAD",          "Atherosclerosis"),
    (1569324,  "I73",     "ICD10CM", "PAD",          "Other peripheral vascular diseases"),
    (44825446, "440",     "ICD9CM",  "PAD",          "Atherosclerosis"),
    (44826654, "443",     "ICD9CM",  "PAD",          "Other peripheral vascular disease"),
    (1568360,  "G45",     "ICD10CM", "TIA",          "Transient cerebral ischemic attacks and related syndromes"),
    (1568361,  "G46",     "ICD10CM", "TIA",          "Vascular syndromes of brain in cerebrovascular diseases"),
]

# Deduplicate by concept ID (some appear in multiple condition groups)
seen = {}
for row in icd_ref:
    cid = row[0]
    if cid not in seen:
        seen[cid] = row

results = []

print(f"Fetching SNOMED mappings for {len(seen)} unique ICD concept IDs...\n")

for i, (cid, icd_code, icd_vocab, condition, icd_english) in enumerate(seen.values()):
    url = BASE.format(cid=cid)
    try:
        data = fetch(cid)
        snomed_hits = [
            c for c in data
            if c.get("VOCABULARY_ID") == "SNOMED"
            and c.get("STANDARD_CONCEPT") == "S"
            and c.get("INVALID_REASON") == "V"
        ]
        if snomed_hits:
            for s in snomed_hits:
                results.append({
                    "condition": condition,
                    "icd_code": icd_code,
                    "icd_vocab": icd_vocab,
                    "icd_concept_id": cid,
                    "icd_english": icd_english,
                    "snomed_concept_id": s["CONCEPT_ID"],
                    "snomed_code": s["CONCEPT_CODE"],
                    "snomed_english": s["CONCEPT_NAME"],
                })
        else:
            results.append({
                "condition": condition,
                "icd_code": icd_code,
                "icd_vocab": icd_vocab,
                "icd_concept_id": cid,
                "icd_english": icd_english,
                "snomed_concept_id": None,
                "snomed_code": None,
                "snomed_english": "NO SNOMED MAPPING",
            })
        print(f"  [{i+1}/{len(seen)}] {icd_code} ({icd_vocab}) -> {len(snomed_hits)} SNOMED hit(s)")
    except Exception as e:
        print(f"  [{i+1}/{len(seen)}] {icd_code} ERROR: {e}")
        results.append({
            "condition": condition, "icd_code": icd_code, "icd_vocab": icd_vocab,
            "icd_concept_id": cid, "icd_english": icd_english,
            "snomed_concept_id": None, "snomed_code": None, "snomed_english": f"ERROR: {e}",
        })
    time.sleep(0.3)

# Save JSON
with open("data/results/icd_omop_snomed_mapping_full.json", "w") as f:
    json.dump(results, f, indent=2)

# Print table
print("\n" + "="*120)
print(f"{'Condition':<14} {'ICD Code':<9} {'ICD Vocab':<9} {'ICD CID':<10} {'ICD English':<50} {'SNOMED CID':<12} {'SNOMED Code':<14} {'SNOMED English'}")
print("="*120)

condition_order = ["Hypertension","Dementia","Stroke","Stroke/CVA","Stroke/TIA","CVA","TIA","Diabetes","CKD","Heart Failure","CAD/MI","AFib","PAD"]
sort_key = {c: i for i, c in enumerate(condition_order)}
results_sorted = sorted(results, key=lambda r: (sort_key.get(r["condition"], 99), r["icd_vocab"], r["icd_code"]))

for r in results_sorted:
    print(f"{r['condition']:<14} {r['icd_code']:<9} {r['icd_vocab']:<9} {r['icd_concept_id']:<10} {r['icd_english']:<50} {str(r['snomed_concept_id']):<12} {str(r['snomed_code']):<14} {r['snomed_english']}")

print(f"\nTotal rows: {len(results)}")
print("Saved to data/results/icd_omop_snomed_mapping_full.json")
