"""Config layer — the reproducibility contract.

These tests exercise the two-tier YAML load/merge, the ${a.b.c} placeholder
resolver, the validation guards, and the derived properties that the pipeline
relies on. No raw data / PHI is required: the real `config/analysis.yml` is
loaded, and a throwaway `paths.yml` is pointed at a temp directory.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import (
    Analysis,
    CohortConfig,
    MultipleTestingConfig,
    OutcomeDef,
    OutcomesConfig,
    PropensityScoreConfig,
    _load_analysis,
    _resolve_all,
    _resolve_placeholder,
    load_config,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_YML = REPO_ROOT / "config" / "analysis.yml"


def _write_paths_yml(tmp_path: Path) -> Path:
    """A minimal, self-referential paths.yml exercising placeholder resolution."""
    yml = tmp_path / "paths.yml"
    yml.write_text(
        f"""
base_dir: {tmp_path.as_posix()}
data:
  raw_root: ${{base_dir}}/data/raw
  extract_v1: ${{data.raw_root}}/extract_v1
  extract_v2: ${{data.raw_root}}/extract_v2
  extract_v3: ${{data.raw_root}}/extract_v3
  antihypertensive_exposures: ${{data.extract_v3}}/exposures.parquet
  spine: ${{data.extract_v3}}/spine.parquet
  conditions: ${{data.extract_v3}}/conditions.parquet
  icd_map: ${{data.extract_v3}}/icd_map.parquet
  baseline_medications: ${{data.extract_v3}}/meds.parquet
  baseline_covariates_augmented: ${{data.extract_v3}}/cov.parquet
  appendicitis_narrow: ${{data.raw_root}}/appendicitis/narrow.parquet
outputs:
  root: ${{base_dir}}/outputs
  core: ${{outputs.root}}/core
  sensitivity: ${{outputs.root}}/sensitivity
  logs: ${{outputs.root}}/logs
"""
    )
    return yml


# --------------------------------------------------------------------------
# Placeholder resolution
# --------------------------------------------------------------------------


def test_resolve_placeholder_simple():
    ctx = {"base_dir": "/root", "data": {"raw_root": "/root/raw"}}
    assert _resolve_placeholder("${base_dir}/x", ctx) == "/root/x"
    assert _resolve_placeholder("${data.raw_root}/y", ctx) == "/root/raw/y"


def test_resolve_placeholder_no_placeholder_is_identity():
    assert _resolve_placeholder("plain/path", {}) == "plain/path"


def test_resolve_all_recurses_dicts():
    tree = {"base_dir": "/r", "data": {"x": "${base_dir}/x"}}
    out = _resolve_all(tree, tree)
    assert out["data"]["x"] == "/r/x"


# --------------------------------------------------------------------------
# Full load + merge of the real analysis.yml with a temp paths.yml
# --------------------------------------------------------------------------


def test_load_config_resolves_nested_placeholders(tmp_path):
    paths_yml = _write_paths_yml(tmp_path)
    cfg = load_config(paths_yml=paths_yml, analysis_yml=ANALYSIS_YML)

    # Multi-hop placeholder: extract_v3 -> raw_root -> base_dir
    assert cfg.paths.extract_v3 == tmp_path / "data" / "raw" / "extract_v3"
    assert cfg.paths.spine.name == "spine.parquet"
    assert cfg.paths.output_core == tmp_path / "outputs" / "core"


def test_load_config_stamps_version(tmp_path):
    cfg = load_config(paths_yml=_write_paths_yml(tmp_path), analysis_yml=ANALYSIS_YML)
    stamp = cfg.version_stamp()
    assert stamp.startswith("ConfigV1.0")
    assert cfg.analysis.last_modified in stamp


def test_missing_required_files_reports_absent_extracts(tmp_path):
    cfg = load_config(paths_yml=_write_paths_yml(tmp_path), analysis_yml=ANALYSIS_YML)
    # The temp dir has no parquet files -> all required inputs are missing.
    missing = cfg.paths.missing_required_files()
    assert len(missing) == 5
    assert cfg.paths.spine in missing


# --------------------------------------------------------------------------
# analysis.yml semantics: exactly two primary outcomes, frozen config
# --------------------------------------------------------------------------


def test_real_analysis_has_two_primary_outcomes():
    analysis = _load_analysis(ANALYSIS_YML)
    analysis.validate()  # must not raise
    assert len(analysis.outcomes.primary) == 2
    assert analysis.multiple_testing.bonferroni_k(analysis.outcomes) == 2


def test_config_is_frozen():
    analysis = _load_analysis(ANALYSIS_YML)
    with pytest.raises(dataclasses.FrozenInstanceError):
        analysis.random_seed = 999  # type: ignore[misc]


# --------------------------------------------------------------------------
# Validation guards (synthetic Analysis objects)
# --------------------------------------------------------------------------


def _analysis(**overrides) -> Analysis:
    outcomes = OutcomesConfig(
        dementia_lag_days=0,
        stroke_lag_days=0,
        order=(
            OutcomeDef("dementia", "primary", "Dementia"),
            OutcomeDef("stroke", "primary", "Stroke"),
        ),
    )
    base = dict(
        end_date="2024-12-31",
        random_seed=42,
        cohort=CohortConfig(washout_days=365, min_age=50, max_age=90, min_followup_days=1),
        propensity_score=PropensityScoreConfig(0.01, 0.99, ("age_at_index",)),
        outcomes=outcomes,
        multiple_testing=MultipleTestingConfig(primary_alpha=0.05),
        sensitivity=None,
        drug_classes=None,
        clinical=None,
        version="1.0",
        last_modified="2026-05-31",
    )
    base.update(overrides)
    return Analysis(**base)  # type: ignore[arg-type]


def test_validate_rejects_bad_age_bounds():
    a = _analysis(cohort=CohortConfig(washout_days=1, min_age=90, max_age=50, min_followup_days=1))
    with pytest.raises(AssertionError, match="min_age"):
        a.validate()


def test_validate_rejects_negative_seed():
    with pytest.raises(AssertionError, match="random_seed"):
        _analysis(random_seed=-1).validate()


@pytest.mark.parametrize("lo,hi", [(0.5, 0.5), (-0.1, 0.9), (0.1, 1.5), (0.9, 0.1)])
def test_validate_rejects_bad_trim_bounds(lo, hi):
    a = _analysis(propensity_score=PropensityScoreConfig(lo, hi, ("age_at_index",)))
    with pytest.raises(AssertionError, match="trim"):
        a.validate()


def test_validate_rejects_wrong_primary_count():
    one = OutcomesConfig(0, 0, (OutcomeDef("dementia", "primary", "Dementia"),))
    with pytest.raises(AssertionError, match="primary"):
        _analysis(outcomes=one).validate()


def test_outcomes_role_partition():
    outcomes = OutcomesConfig(
        0,
        0,
        (
            OutcomeDef("a", "primary", "A"),
            OutcomeDef("b", "primary", "B"),
            OutcomeDef("c", "secondary", "C"),
        ),
    )
    assert outcomes.primary == ("a", "b")
    assert outcomes.secondary == ("c",)
    assert outcomes.labels == {"a": "A", "b": "B", "c": "C"}
