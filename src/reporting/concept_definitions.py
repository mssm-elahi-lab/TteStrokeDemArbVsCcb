"""GAP4 — Supplementary Table 3: concept / phenotype definitions.

Emits ``concept_definitions.csv`` (component -> operational definition ->
included drugs/concepts) so the manuscript's design table regenerates from the
canonical config rather than being hand-maintained.

Sources:
  - Drug ingredient lists and outcome/exclusion SNOMED IDs: ``config/analysis.yml``
    (the single source of truth for the study definitions).
  - Human-readable SNOMED concept names: ``CONCEPT_NAMES`` below — the outcome
    codebook used by the manuscript (Athena standard-concept names). Config
    stores the numeric IDs; the names are attached here for the readable table.
  - The appendicitis falsification concept set is applied upstream in the raw
    NARROW extract (see ``src/sensitivity/appendicitis_falsification.py`` and
    ``data/EXTRACTS.md``); it is carried here as a documented constant because
    it is not part of ``analysis.yml``.

This module reads no raw patient data — it is a pure config -> table renderer.
"""

from __future__ import annotations

from src.config import Config

# Standard-concept names for every SNOMED/OMOP concept ID referenced by the
# study definitions (outcome codebook). Kept alongside the numeric IDs held in
# config/analysis.yml so the readable table can be regenerated deterministically.
CONCEPT_NAMES: dict[int, str] = {
    # Vascular
    443454: "acute ischemic stroke",
    372924: "cerebral arterial occlusion with infarction",
    373503: "transient ischemic attack",
    # Cognitive
    378419: "Alzheimer's disease",
    443605: "vascular dementia",
    4182210: "unspecified dementia",
    439795: "mild cognitive impairment, uncertain etiology",
    4009705: "age-related cognitive decline",
}

# Falsification / negative-control concept set (acute appendicitis and variants).
# Applied during the raw NARROW appendicitis extract, not in analysis.yml.
APPENDICITIS_CONCEPTS: tuple[tuple[int, str], ...] = (
    (4310400, "acute appendicitis"),
    (196149, "acute appendicitis with generalized peritonitis"),
    (44784251, "acute appendicitis with localized peritonitis"),
    (193238, "acute appendicitis with peritoneal abscess"),
    (4178300, "acute gangrenous appendicitis"),
    (4117866, "acute perforated appendicitis"),
    (4268754, "abscess of appendix"),
    (4141626, "acute appendicitis with appendix abscess"),
    (4057524, "acute appendicitis with peritonitis"),
    (441604, "acute appendicitis without peritonitis"),
    (4340802, "acute focal appendicitis"),
    (4222930, "acute fulminating appendicitis"),
    (4277609, "acute fulminating appendicitis with perforation and peritonitis"),
    (4275886, "acute gangrenous appendicitis with perforation and peritonitis"),
    (4177979, "acute obstructive appendicitis"),
    (4151696, "acute obstructive appendicitis with perforation and peritonitis"),
    (42536650, "acute phlegmonous appendicitis"),
    (4340803, "acute suppurative appendicitis"),
)


def _fmt_concepts(ids: object) -> str:
    """Format an iterable of SNOMED IDs as 'id - name; id - name' using the codebook."""
    parts = []
    for cid in ids:
        name = CONCEPT_NAMES.get(int(cid))
        parts.append(f"{cid} - {name}" if name else str(cid))
    return "; ".join(parts)


def _fmt_appendicitis() -> str:
    return "; ".join(f"{cid} - {name}" for cid, name in APPENDICITIS_CONCEPTS)


def run(config: Config) -> None:
    import pandas as pd

    analysis = config.analysis
    dc = analysis.drug_classes
    clin = analysis.clinical
    cohort = analysis.cohort

    arb = "; ".join(dc.arb_ingredients)
    dhp_index = "; ".join(dc.dhp_ccb_index)
    acei = "; ".join(dc.acei_washout)
    dhp_wash = "; ".join(dc.dhp_ccb_washout)
    thiazide = "; ".join(dc.thiazide_washout)

    washout_concepts = (
        f"ACE inhibitors: {acei}. "
        f"ARBs: {arb}. "
        f"DHP-CCBs: {dhp_wash}. "
        f"Thiazide-type diuretics: {thiazide}"
    )

    stroke_primary = clin.vascular.stroke_s1_snomed_ids
    stroke_secondary = tuple(stroke_primary) + tuple(clin.vascular.tia_snomed_ids)
    cog_primary = clin.cognitive.b4_mci_snomed_ids
    cog_secondary = clin.cognitive.b4_snomed_ids

    rows = [
        (
            "ARB index strategy",
            "Initiation of an ARB-based treatment strategy",
            arb,
        ),
        (
            "DHP-CCB index strategy",
            "Initiation of an index-eligible chronic outpatient DHP-CCB strategy",
            dhp_index,
        ),
        (
            f"{cohort.washout_days}-day first-line antihypertensive washout",
            (
                "Absence of medication records for major first-line chronic "
                f"antihypertensive classes during the {cohort.washout_days} days before index"
            ),
            washout_concepts,
        ),
        (
            "Primary vascular outcome",
            "Incident acute ischemic stroke",
            _fmt_concepts(stroke_primary),
        ),
        (
            "Secondary vascular outcome",
            "Incident ischemic stroke plus transient ischemic attack",
            "Primary vascular outcome concepts + " + _fmt_concepts(clin.vascular.tia_snomed_ids),
        ),
        (
            "Primary cognitive outcome",
            (
                "Incident probable dementia (Alzheimer's disease, vascular dementia, "
                "or unspecified dementia) plus mild cognitive impairment / cognitive decline"
            ),
            _fmt_concepts(cog_primary),
        ),
        (
            "Secondary cognitive outcome",
            (
                "Incident probable dementia alone (Alzheimer's disease, vascular "
                "dementia, or unspecified dementia)"
            ),
            _fmt_concepts(cog_secondary),
        ),
        (
            "Falsification outcome",
            (
                "Incident recorded acute appendicitis after index date; excluded if "
                "appendicitis was recorded on or before index date"
            ),
            _fmt_appendicitis(),
        ),
        (
            "Prevalent cognitive exclusion",
            "Excluded if the primary cognitive outcome definition was present on or before index date",
            "Same concepts as primary cognitive outcome",
        ),
        (
            "Prevalent vascular exclusion",
            "Excluded if the primary vascular outcome definition was present on or before index date",
            "Same concepts as primary vascular outcome",
        ),
    ]

    df = pd.DataFrame(rows, columns=["Component", "Operational definition", "Included drugs/concepts"])

    out_dir = config.paths.output_core
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "concept_definitions.csv"
    df.to_csv(out_path, index=False)
    print(f"Saved: {out_path}  ({len(df)} components)")
    print("concept_definitions complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
