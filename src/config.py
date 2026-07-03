from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ANALYSIS_YML = "config/analysis.yml"
DEFAULT_PATHS_YML = "config/paths.yml"


@dataclass(frozen=True)
class OutcomeDef:
    name: str
    role: str
    label: str


@dataclass(frozen=True)
class CohortConfig:
    washout_days: int
    min_age: int
    max_age: int
    min_followup_days: int


@dataclass(frozen=True)
class PropensityScoreConfig:
    trim_lower: float
    trim_upper: float
    covariates_fixed: tuple[str, ...]


@dataclass(frozen=True)
class OutcomesConfig:
    dementia_lag_days: int
    stroke_lag_days: int
    order: tuple[OutcomeDef, ...]

    @property
    def labels(self) -> dict[str, str]:
        return {o.name: o.label for o in self.order}

    @property
    def roles(self) -> dict[str, str]:
        return {o.name: o.role for o in self.order}

    @property
    def primary(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.order if o.role == "primary")

    @property
    def secondary(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.order if o.role == "secondary")


@dataclass(frozen=True)
class MultipleTestingConfig:
    primary_alpha: float

    def bonferroni_k(self, outcomes: OutcomesConfig) -> int:
        return len(outcomes.primary)


@dataclass(frozen=True)
class BpHierarchyModel:
    id: str
    complete_case: tuple[str, ...]
    add_covariates: tuple[str, ...]


@dataclass(frozen=True)
class BpHierarchyConfig:
    source_columns: tuple[tuple[str, str], ...]  # (short_name, raw_column) pairs
    binary_flag_covariates: tuple[str, ...]
    models: tuple[BpHierarchyModel, ...]

    @property
    def rename_map(self) -> dict[str, str]:
        """Raw parquet column -> short name used by the models."""
        return {raw: short for short, raw in self.source_columns}

    @property
    def short_names(self) -> tuple[str, ...]:
        return tuple(short for short, _ in self.source_columns)


@dataclass(frozen=True)
class CurveDivergenceConfig:
    landmarks_years: tuple[int, ...]
    intervals_years: tuple[tuple[int, int | None], ...]


@dataclass(frozen=True)
class ExtendedFollowupConfig:
    index_date_cutoff: str


@dataclass(frozen=True)
class FollowupTimingConfig:
    followup_column: str
    bucket_edges_months: tuple[float, ...]  # final edge is +inf
    bucket_labels: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityConfig:
    bp_hierarchy: BpHierarchyConfig
    curve_divergence: CurveDivergenceConfig
    extended_followup: ExtendedFollowupConfig
    followup_timing: FollowupTimingConfig


@dataclass(frozen=True)
class DrugClassesConfig:
    arb_ingredients: tuple[str, ...]
    dhp_ccb_primary_index: tuple[str, ...]
    dhp_ccb_additional: tuple[str, ...]
    include_additional_dhp_index: bool
    thiazide_washout: tuple[str, ...]
    thiazide_review_pending: tuple[str, ...]
    acei_washout: tuple[str, ...]
    acei_washout_window_days: int
    dhp_ccb_excluded_iv_nondhp: tuple[str, ...]

    @property
    def dhp_ccb_index(self) -> tuple[str, ...]:
        if self.include_additional_dhp_index:
            return self.dhp_ccb_primary_index + self.dhp_ccb_additional
        return self.dhp_ccb_primary_index

    @property
    def dhp_ccb_washout(self) -> tuple[str, ...]:
        return self.dhp_ccb_primary_index + self.dhp_ccb_additional


@dataclass(frozen=True)
class CognitiveDefs:
    b4_snomed_ids: tuple[int, ...]
    b4_mci_snomed_ids: tuple[int, ...]
    b4_icd_ids: tuple[int, ...]
    mci_icd_ids: tuple[int, ...]

    @property
    def b4_mci_icd_ids(self) -> tuple[int, ...]:
        return self.b4_icd_ids + self.mci_icd_ids


@dataclass(frozen=True)
class VascularDefs:
    stroke_s1_snomed_ids: tuple[int, ...]
    stroke_broad_snomed_ids: tuple[int, ...]
    tia_snomed_ids: tuple[int, ...]
    stroke_s1_icd_ids: tuple[int, ...]
    stroke_broad_icd_ids_extra: tuple[int, ...]
    tia_icd_ids: tuple[int, ...]

    @property
    def stroke_broad_icd_ids(self) -> tuple[int, ...]:
        return self.stroke_s1_icd_ids + self.stroke_broad_icd_ids_extra


@dataclass(frozen=True)
class ComorbidityDefs:
    hypertension_icd_ids: tuple[int, ...]
    diabetes_icd_ids: tuple[int, ...]
    ckd_icd_ids: tuple[int, ...]
    heart_failure_icd_ids: tuple[int, ...]
    cad_mi_icd_ids: tuple[int, ...]
    afib_icd_ids: tuple[int, ...]
    pad_icd_ids: tuple[int, ...]


@dataclass(frozen=True)
class RaceCodingDefs:
    white_concept_ids: frozenset[int]
    black_concept_ids: frozenset[int]
    asian_concept_ids: frozenset[int]
    unknown_concept_ids: frozenset[int]


@dataclass(frozen=True)
class LegacyAliases:
    mci_omop_concept_id: int
    stroke_s1_omop: int
    tia_omop: int


@dataclass(frozen=True)
class ClinicalDefinitions:
    cognitive: CognitiveDefs
    vascular: VascularDefs
    comorbidities: ComorbidityDefs
    race_coding: RaceCodingDefs
    legacy_aliases: LegacyAliases

    @property
    def bl_tia_icd_ids(self) -> tuple[int, ...]:
        return self.vascular.tia_icd_ids

    @property
    def prevalent_cognitive_excl_snomeds(self) -> tuple[int, ...]:
        return self.cognitive.b4_mci_snomed_ids

    @property
    def prevalent_vascular_excl_snomeds(self) -> tuple[int, ...]:
        return self.vascular.stroke_s1_snomed_ids


@dataclass(frozen=True)
class Analysis:
    end_date: str
    random_seed: int
    cohort: CohortConfig
    propensity_score: PropensityScoreConfig
    outcomes: OutcomesConfig
    multiple_testing: MultipleTestingConfig
    sensitivity: SensitivityConfig
    drug_classes: DrugClassesConfig
    clinical: ClinicalDefinitions
    version: str
    last_modified: str

    def validate(self) -> None:
        assert self.cohort.min_age < self.cohort.max_age, "cohort.min_age >= cohort.max_age"
        assert self.random_seed >= 0, "analysis.random_seed < 0"
        assert 0 <= self.propensity_score.trim_lower < self.propensity_score.trim_upper <= 1, (
            "propensity_score trim bounds must satisfy 0 <= trim_lower < trim_upper <= 1"
        )
        assert len(self.outcomes.primary) == 2, "expected exactly 2 primary outcomes"


@dataclass(frozen=True)
class Paths:
    base_dir: Path
    extract_v1: Path
    extract_v2: Path
    extract_v3: Path
    antihypertensive_exposures: Path
    spine: Path
    conditions: Path
    icd_map: Path
    baseline_medications: Path
    baseline_covariates_augmented: Path
    appendicitis_narrow: Path
    output_root: Path
    output_core: Path
    output_sensitivity: Path
    log_dir: Path

    def validate(self) -> None:
        """Cheap, always-safe checks. Does NOT require raw data to be present yet
        (see missing_required_files) so --dry-run/--show-config work pre-Phase 7."""
        assert self.base_dir.exists(), f"paths.base_dir does not exist: {self.base_dir}"

    def missing_required_files(self) -> list[Path]:
        required = [
            self.antihypertensive_exposures,
            self.spine,
            self.conditions,
            self.icd_map,
            self.baseline_medications,
        ]
        return [p for p in required if not p.exists()]


@dataclass(frozen=True)
class Config:
    analysis: Analysis
    paths: Paths
    config_version: str = "ConfigV1.0"

    def version_stamp(self) -> str:
        now = datetime.now().isoformat(timespec="microseconds")
        return f"{self.config_version} ({self.analysis.last_modified}) @ {now}"


def _resolve_placeholder(value: str, context: dict[str, Any]) -> str:
    def lookup(path: str) -> str:
        node: Any = context
        for part in path.split("."):
            node = node[part]
        return str(node)

    return re.sub(r"\$\{([^}]+)\}", lambda m: lookup(m.group(1)), value)


def _resolve_all(node: Any, context: dict[str, Any]) -> Any:
    if isinstance(node, str):
        return _resolve_placeholder(node, context)
    if isinstance(node, dict):
        return {k: _resolve_all(v, context) for k, v in node.items()}
    return node


def _load_analysis(analysis_yml: Path) -> Analysis:
    raw = yaml.safe_load(analysis_yml.read_text())

    cohort = CohortConfig(**raw["cohort"])
    ps = PropensityScoreConfig(
        trim_lower=raw["propensity_score"]["trim_lower"],
        trim_upper=raw["propensity_score"]["trim_upper"],
        covariates_fixed=tuple(raw["propensity_score"]["covariates_fixed"]),
    )
    outcomes = OutcomesConfig(
        dementia_lag_days=raw["outcomes"]["dementia_lag_days"],
        stroke_lag_days=raw["outcomes"]["stroke_lag_days"],
        order=tuple(OutcomeDef(**o) for o in raw["outcomes"]["order"]),
    )
    mt = MultipleTestingConfig(primary_alpha=raw["multiple_testing"]["primary_alpha"])
    sensitivity = _load_sensitivity(raw["sensitivity"])

    dc = raw["drug_classes"]
    drug_classes = DrugClassesConfig(
        arb_ingredients=tuple(dc["arb_ingredients"]),
        dhp_ccb_primary_index=tuple(dc["dhp_ccb_primary_index"]),
        dhp_ccb_additional=tuple(dc["dhp_ccb_additional"]),
        include_additional_dhp_index=dc["include_additional_dhp_index"],
        thiazide_washout=tuple(dc["thiazide_washout"]),
        thiazide_review_pending=tuple(dc["thiazide_review_pending"]),
        acei_washout=tuple(dc["acei_washout"]),
        acei_washout_window_days=dc["acei_washout_window_days"],
        dhp_ccb_excluded_iv_nondhp=tuple(dc["dhp_ccb_excluded_iv_nondhp"]),
    )

    cd = raw["clinical_definitions"]
    clinical = ClinicalDefinitions(
        cognitive=CognitiveDefs(
            b4_snomed_ids=tuple(cd["cognitive"]["b4_snomed_ids"]),
            b4_mci_snomed_ids=tuple(cd["cognitive"]["b4_mci_snomed_ids"]),
            b4_icd_ids=tuple(cd["cognitive"]["b4_icd_ids"]),
            mci_icd_ids=tuple(cd["cognitive"]["mci_icd_ids"]),
        ),
        vascular=VascularDefs(
            stroke_s1_snomed_ids=tuple(cd["vascular"]["stroke_s1_snomed_ids"]),
            stroke_broad_snomed_ids=tuple(cd["vascular"]["stroke_broad_snomed_ids"]),
            tia_snomed_ids=tuple(cd["vascular"]["tia_snomed_ids"]),
            stroke_s1_icd_ids=tuple(cd["vascular"]["stroke_s1_icd_ids"]),
            stroke_broad_icd_ids_extra=tuple(cd["vascular"]["stroke_broad_icd_ids_extra"]),
            tia_icd_ids=tuple(cd["vascular"]["tia_icd_ids"]),
        ),
        comorbidities=ComorbidityDefs(
            hypertension_icd_ids=tuple(cd["comorbidities"]["hypertension_icd_ids"]),
            diabetes_icd_ids=tuple(cd["comorbidities"]["diabetes_icd_ids"]),
            ckd_icd_ids=tuple(cd["comorbidities"]["ckd_icd_ids"]),
            heart_failure_icd_ids=tuple(cd["comorbidities"]["heart_failure_icd_ids"]),
            cad_mi_icd_ids=tuple(cd["comorbidities"]["cad_mi_icd_ids"]),
            afib_icd_ids=tuple(cd["comorbidities"]["afib_icd_ids"]),
            pad_icd_ids=tuple(cd["comorbidities"]["pad_icd_ids"]),
        ),
        race_coding=RaceCodingDefs(
            white_concept_ids=frozenset(cd["race_coding"]["white_concept_ids"]),
            black_concept_ids=frozenset(cd["race_coding"]["black_concept_ids"]),
            asian_concept_ids=frozenset(cd["race_coding"]["asian_concept_ids"]),
            unknown_concept_ids=frozenset(cd["race_coding"]["unknown_concept_ids"]),
        ),
        legacy_aliases=LegacyAliases(**cd["legacy_aliases"]),
    )

    return Analysis(
        end_date=raw["analysis"]["end_date"],
        random_seed=raw["analysis"]["random_seed"],
        cohort=cohort,
        propensity_score=ps,
        outcomes=outcomes,
        multiple_testing=mt,
        sensitivity=sensitivity,
        drug_classes=drug_classes,
        clinical=clinical,
        version=raw["version"],
        last_modified=raw["last_modified"],
    )


def _load_sensitivity(raw: dict[str, Any]) -> SensitivityConfig:
    bp = raw["bp_hierarchy"]
    bp_cfg = BpHierarchyConfig(
        source_columns=tuple((short, col) for short, col in bp["source_columns"].items()),
        binary_flag_covariates=tuple(bp["binary_flag_covariates"]),
        models=tuple(
            BpHierarchyModel(
                id=m["id"],
                complete_case=tuple(m["complete_case"]),
                add_covariates=tuple(m["add_covariates"]),
            )
            for m in bp["models"]
        ),
    )

    cd = raw["curve_divergence"]
    cd_cfg = CurveDivergenceConfig(
        landmarks_years=tuple(cd["landmarks_years"]),
        intervals_years=tuple((lo, hi) for lo, hi in cd["intervals_years"]),
    )

    ext_cfg = ExtendedFollowupConfig(index_date_cutoff=raw["extended_followup"]["index_date_cutoff"])

    ft = raw["followup_timing"]
    edges = tuple(float("inf") if e is None else float(e) for e in ft["bucket_edges_months"])
    ft_cfg = FollowupTimingConfig(
        followup_column=ft["followup_column"],
        bucket_edges_months=edges,
        bucket_labels=tuple(ft["bucket_labels"]),
    )

    return SensitivityConfig(
        bp_hierarchy=bp_cfg,
        curve_divergence=cd_cfg,
        extended_followup=ext_cfg,
        followup_timing=ft_cfg,
    )


def _load_paths(paths_yml: Path) -> Paths:
    raw: dict[str, Any] = yaml.safe_load(paths_yml.read_text())

    # ${a.b.c} placeholders can reference siblings resolved in an earlier pass
    # (e.g. data.extract_v3 -> ${data.raw_root} -> ${base_dir}); a few fixed-point
    # passes over the whole tree converges without hardcoding key order.
    resolved = raw
    for _ in range(5):
        resolved = _resolve_all(resolved, resolved)

    d = resolved
    return Paths(
        base_dir=Path(d["base_dir"]),
        extract_v1=Path(d["data"]["extract_v1"]),
        extract_v2=Path(d["data"]["extract_v2"]),
        extract_v3=Path(d["data"]["extract_v3"]),
        antihypertensive_exposures=Path(d["data"]["antihypertensive_exposures"]),
        spine=Path(d["data"]["spine"]),
        conditions=Path(d["data"]["conditions"]),
        icd_map=Path(d["data"]["icd_map"]),
        baseline_medications=Path(d["data"]["baseline_medications"]),
        baseline_covariates_augmented=Path(d["data"]["baseline_covariates_augmented"]),
        appendicitis_narrow=Path(d["data"]["appendicitis_narrow"]),
        output_root=Path(d["outputs"]["root"]),
        output_core=Path(d["outputs"]["core"]),
        output_sensitivity=Path(d["outputs"]["sensitivity"]),
        log_dir=Path(d["outputs"]["logs"]),
    )


def load_config(
    paths_yml: str | Path = DEFAULT_PATHS_YML,
    analysis_yml: str | Path = DEFAULT_ANALYSIS_YML,
) -> Config:
    analysis = _load_analysis(Path(analysis_yml))
    paths = _load_paths(Path(paths_yml))

    analysis.validate()
    paths.validate()

    return Config(analysis=analysis, paths=paths)
