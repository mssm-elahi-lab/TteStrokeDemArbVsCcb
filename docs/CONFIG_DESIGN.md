# TTE Analysis: Centralized Configuration Design

## Executive Summary

This design separates configuration into **three layers**:
1. **`config/analysis.yml`** (git-tracked, shared): Clinical definitions, analysis parameters, study design constants
2. **`config/paths.yml`** (machine-local, NOT git-tracked): User-specific data paths, output directories
3. **Environment variables / `.env`** (secrets, optional overrides)
4. **CLI flags**: Override specific values for single runs

The loader pattern validates, merges, versions, and stamps configuration for reproducibility.

---

## Problem Analysis

Current `run01_config.py` issues:
- **Hardcoded absolute paths**: `/Users/akarshsharma/Desktop/...` breaks on every other machine
- **Mixed concerns**: Drug ingredients, ICD codes, file paths, parameters all in one file
- **No versioning**: Difficult to track when clinical definitions changed
- **No override mechanism**: Must edit Python file for one-off runs
- **No validation**: Type errors only discovered at runtime
- **Reproducibility risk**: Which version of config did this analysis use? Unclear.

---

## Architecture: Three-Tier Configuration

```
┌─────────────────────────────────────────┐
│  CLI Flags (highest precedence)         │  python script.py --analysis-end-date 2026-06-30
├─────────────────────────────────────────┤
│  Environment Variables                  │  export TTE_DATA_DIR=/mnt/data
├─────────────────────────────────────────┤
│  config/paths.yml (machine-local)       │  BASE_DIR: ~/tte-project  ← User-specific
├─────────────────────────────────────────┤
│  config/analysis.yml (shared, versioned) │  RANDOM_SEED: 42  ← All machines same
└─────────────────────────────────────────┘
```

### Layer 1: `config/analysis.yml` (VERSION-CONTROLLED)

**Purpose**: Clinical definitions, study design constants, outcome rules.
**Shared across all team members.**
**Tracked in git.**

```yaml
# config/analysis.yml
# TTE Analysis v1 Configuration Schema
# Version: 1.0
# Last updated: 2026-07-01
# Commit hash: abc123def456

config_version: "1.0"
config_updated_date: "2026-07-01"
description: "run01_v4_core_design_deathcensor - Final Candidate Run 01"

# ============================================================================
# ANALYSIS PARAMETERS — Fixed for reproducibility
# ============================================================================
analysis:
  end_date: "2025-12-31"  # v3 AIRMS analysis lock date
  random_seed: 42
  followup:
    min_days: 365
    washout_days: 180
    lag_dementia_days: 180
    lag_stroke_days: 90
  age_range:
    min: 40
    max: 70
  propensity_score:
    trim_lower: 0.01
    trim_upper: 0.99

# ============================================================================
# OUTCOME DEFINITIONS — B4 (PRIMARY dementia), B4_MCI, STROKE_S1
# ============================================================================
outcomes:
  # Primary outcomes (N=2, used for multiple testing correction)
  primary:
    - name: "stroke_s1"
      label: "Acute ischemic stroke"
      type: "vascular"
      snomed_ids: [443454, 372924]
      icd_concept_ids: [1569193, 44824253]
      lag_days: 90
      
    - name: "b4_mci"
      label: "Probable dementia + mild cognitive impairment"
      type: "cognitive"
      snomed_ids: [378419, 443605, 4182210, 439795, 4009705]
      icd_concept_ids: [45533052, 1568087, 1568293, 35207114, 44824105, 45595932, 45553736]
      lag_days: 180
  
  # Secondary outcomes (raw p-values only)
  secondary:
    - name: "b4"
      label: "Probable dementia alone"
      type: "cognitive"
      snomed_ids: [378419, 443605, 4182210]
      icd_concept_ids: [45533052, 1568087, 1568293, 35207114, 44824105]
      lag_days: 180
      
    - name: "stroke_s2"
      label: "Ischemic stroke + transient ischemic attack"
      type: "vascular"
      snomed_ids: [443454, 372924, 373503]
      icd_concept_ids: [1569193, 44824253, 1568360, 44820875]
      lag_days: 90

# ============================================================================
# PREVALENT OUTCOME EXCLUSION — Who gets excluded at baseline
# ============================================================================
prevalent_exclusions:
  cognitive: "b4_mci"  # exclude if any B4_MCI disease before/at index
  vascular: "stroke_s1"  # exclude if any S1 stroke before/at index
  note: "TIA prior is NOT excluded; used as PS covariate only"

# ============================================================================
# DRUG INGREDIENTS — Drug class definitions
# ============================================================================
drug_classes:
  dhp_ccb:
    primary_index: ["amlodipine", "nifedipine"]
    additional: ["felodipine", "isradipine"]
    washout: ["amlodipine", "nifedipine", "felodipine", "isradipine"]
    include_additional_in_index: false  # AUTHOR_REVIEW_REQUIRED to change
    note: "IV/procedural excluded: clevidipine, nicardipine"
    
  arb:
    ingredients: ["losartan", "valsartan", "olmesartan", "telmisartan", 
                  "candesartan", "azilsartan"]
    note: "irbesartan, eprosartan absent from v4 extract"
    
  acei:
    washout: ["lisinopril", "enalapril", "ramipril", "captopril", "benazepril"]
    note: "quinapril, fosinopril, moexipril, perindopril, trandolapril absent from v4"
    
  thiazide:
    washout: ["hydrochlorothiazide", "chlorthalidone", "indapamide"]
    pending_review: ["metolazone"]

# ============================================================================
# COMORBIDITY DEFINITIONS — Baseline covariates
# ============================================================================
comorbidities:
  hypertension_icd_ids: [35207668, 1569120, 1569121, 1569122, 1569124,
                         44833556, 44832366, 44832367, 44827780, 44832370]
  diabetes_icd_ids: [1567940, 1567956, 1567972, 44833365]
  ckd_icd_ids: [1571486, 44830172]
  heart_failure_icd_ids: [1569178, 44824250]
  cad_mi_icd_ids: [1569125, 1569126, 1569130, 1569133, 44832372, 44834725, 44835930, 44827784]
  afib_icd_ids: [1569170, 44824248, 44821957, 44820868]
  pad_icd_ids: [1569271, 1569324, 44825446, 44826654]

# ============================================================================
# PS MODEL COVARIATES — Variables in propensity score
# ============================================================================
propensity_score_covariates:
  fixed: [
    "age_at_index",
    "female",
    "race_black_r",
    "race_asian_r",
    "race_other_r",
    "race_unknown_r",
    "hispanic",
    "bl_diabetes",
    "bl_ckd",
    "bl_heart_failure",
    "bl_cad_mi",
    "bl_afib",
    "bl_pad",
    "bl_tia"
  ]
  dynamic: "index_year"  # categorical dummies added at load time
  reference_categories: ["race_white_r"]
  notes:
    - "bl_cva EXCLUDED: concurrent-index CVA biases PS"
    - "race_unknown_r included as PS covariate (models EHR missingness)"

# ============================================================================
# RACE CODING — Mutually exclusive categories
# ============================================================================
race_categories:
  white_concept_ids: [8527, 38003598]
  black_concept_ids: [8516, 38003599]  # 38003599 = CDC code active in BioMe
  asian_concept_ids: [8515, 38003601]
  unknown_concept_ids: [0, 8552, 8657]  # 0=no matching concept; null treated same
  other: "RACE_CONCEPT_ID nonmissing and not in any above set"

# ============================================================================
# MULTIPLE TESTING CORRECTION — Bonferroni + BH-FDR
# ============================================================================
multiple_testing:
  primary_alpha: 0.05
  # Bonferroni and BH-FDR applied across PRIMARY outcomes only (N=2)
  # Secondary outcomes: raw p-values only
  applied_to: "primary_outcomes"

# ============================================================================
# DESIGN DECISIONS & VERSIONING — Track corrections
# ============================================================================
design_decisions:
  C1: "V4_CONDITIONS and V4_ICD_MAP point to v4 extract, not v3"
  C2: "Dementia outcomes use v4 harmonized B4/B4_MCI bucket definitions"
  C3: "Stroke primary = harmonized AIS (443454+372924). Broad (4164092) = SENSITIVITY"
  C4: "Prevalent exclusion: cognitive = B4_MCI union; vascular = primary stroke S1"
  C5: "Censoring uses clinical_end_date = max(obs_end, last_condition_date, last_drug_date, last_baseline_med_date)"
  C6: "ACEi washout uses date-based window (1-180d pre-index) from raw_baseline_medications"
  C7: "Race: mutually exclusive White/Black/Asian/Other/Unknown"
  C8: "Table 2 includes Bonferroni+BH-FDR across 2 primary outcomes only"
  C9: "No arbitrary penalizer; document if used"
  C10: "V4 data throughout (conditions, ICD map, spine, medications)"
  
  last_correction_date: "2026-05-31"
  readme: "DO NOT MODIFY without updating README_design_changes.md"

---

### Layer 2: `config/paths.yml` (MACHINE-LOCAL, NOT TRACKED)

**Purpose**: Machine-specific paths (user's home directory, mount points, local data directories).
**NEVER committed to git.**
**Created by each user locally.**

Template: `config/paths.yml.example` (IN git) → User copies to `config/paths.yml` (NOT in git)

```yaml
# config/paths.yml.example
# ============================================================================
# MACHINE-LOCAL PATHS — Copy to config/paths.yml and customize for your machine
# DO NOT commit config/paths.yml to git — add it to .gitignore
# ============================================================================

paths:
  # Root data directory (adjust to your machine)
  base_dir: "~/tte-project"  # Expands to user's home directory
  
  # Data extract directories (version-specific)
  v4_extract_dir: "${base_dir}/AIRMS/most recent extract"
  v3_audit_dir: "${base_dir}/data/results/v3rstudio-export"  # AUDIT ONLY
  
  # Primary v4 sources (relative to v4_extract_dir)
  data:
    antihtn_exposures: "raw_antihypertensive_exposures.parquet"
    spine: "cohort_spine_raw.parquet"
    conditions: "raw_conditions.parquet"
    icd_map: "icd_to_snomed_map.parquet"
    baseline_meds: "raw_baseline_medications.parquet"
  
  # Output directories
  output:
    root: "${base_dir}/AIRMS/results/final_candidate_runs_20260531/run01_v4_core_design_deathcensor"
    logs: "${output.root}/logs"
    intermediate: "${output.root}/intermediate"
    tables: "${output.root}/tables"
    figures: "${output.root}/figures"
  
  # Optional: Temporary working directory (for large intermediate files)
  temp_dir: "/tmp/tte_analysis"  # or "C:\\Users\\<username>\\AppData\\Local\\Temp\\tte"

---

### Layer 3: Environment Variables (OPTIONAL)

Override or supplement file-based config:

```bash
# .env (NOT tracked in git, added to .gitignore)
export TTE_ANALYSIS_END_DATE="2026-06-30"
export TTE_DATA_DIR="/mnt/shared-data/AIRMS"
export TTE_RANDOM_SEED="123"
```

Or:
```bash
python script.py --env .env.staging
```

---

### Layer 4: CLI Flags (ONE-OFF OVERRIDES)

Override specific values for single runs without modifying files:

```bash
# Single override
python 04_table2.py --analysis-end-date 2026-06-30

# Multiple overrides
python 06_forest_plot.py \
  --random-seed 999 \
  --ps-trim-lower 0.02 \
  --ps-trim-upper 0.98

# Use different config file
python 02_build_cohort.py --config config/analysis.staging.yml
```

---

## Config Loader Pattern

### `src/config/loader.py`

```python
"""
Configuration loader with validation, versioning, and precedence handling.

Precedence (highest to lowest):
1. CLI flags (--flag value)
2. Environment variables (TTE_FLAG=value)
3. config/paths.yml (machine-local)
4. config/analysis.yml (shared, version-controlled)
5. defaults
"""

from typing import Any, Optional
from pathlib import Path
import os
from dataclasses import dataclass
from datetime import datetime
import yaml
import logging
from functools import lru_cache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigVersion:
    """Immutable config version metadata."""
    schema_version: str
    updated_date: str
    commit_hash: Optional[str]
    loaded_time: datetime
    
    def stamp(self) -> str:
        """Return human-readable version stamp for reproducibility."""
        return f"ConfigV{self.schema_version} ({self.updated_date}) @ {self.loaded_time.isoformat()}"


@dataclass(frozen=True)
class Analysis:
    """Immutable analysis configuration."""
    end_date: str
    random_seed: int
    min_followup_days: int
    washout_days: int
    min_age: int
    max_age: int
    dementia_lag_days: int
    stroke_lag_days: int
    ps_trim_lower: float
    ps_trim_upper: float
    
    def validate(self) -> None:
        """Validate analysis parameters."""
        assert self.min_age < self.max_age, "min_age must be < max_age"
        assert 0 < self.ps_trim_lower < self.ps_trim_upper < 1, "PS trim bounds invalid"
        assert self.washout_days > 0, "washout_days must be positive"
        assert self.random_seed >= 0, "random_seed must be non-negative"
        log.info(f"✓ Analysis config validated: {self}")


@dataclass(frozen=True)
class Paths:
    """Immutable path configuration with expansion."""
    base_dir: Path
    v4_extract_dir: Path
    output_root: Path
    logs_dir: Path
    temp_dir: Optional[Path]
    
    def data_file(self, filename: str) -> Path:
        """Resolve data file path relative to v4 extract."""
        path = self.v4_extract_dir / filename
        if not path.exists():
            log.warning(f"Data file does not exist: {path}")
        return path
    
    def validate(self) -> None:
        """Validate that required directories exist or are creatable."""
        required = [self.base_dir, self.v4_extract_dir]
        for path in required:
            if not path.exists():
                raise FileNotFoundError(f"Required path missing: {path}")
        
        # Output dirs will be created on demand
        for path in [self.logs_dir, self.temp_dir]:
            if path:
                path.mkdir(parents=True, exist_ok=True)
        
        log.info(f"✓ Paths config validated: base_dir={self.base_dir}")


class ConfigLoader:
    """
    Centralized config loader with three-tier merging.
    
    Usage:
        config = ConfigLoader("config/analysis.yml", "config/paths.yml")
        config.load()
        
        # Access merged config
        print(config.analysis.end_date)
        print(config.paths.logs_dir)
        print(config.version.stamp())
    """
    
    def __init__(
        self,
        analysis_file: Path,
        paths_file: Path,
        env_file: Optional[Path] = None,
    ):
        self.analysis_file = Path(analysis_file)
        self.paths_file = Path(paths_file)
        self.env_file = Path(env_file) if env_file else None
        
        self.analysis: Optional[Analysis] = None
        self.paths: Optional[Paths] = None
        self.version: Optional[ConfigVersion] = None
        self._raw_config: dict[str, Any] = {}
    
    @lru_cache(maxsize=1)
    def load(self) -> "ConfigLoader":
        """Load and merge configuration from all sources."""
        log.info("=== CONFIG LOADER START ===")
        
        # 1. Load analysis.yml (version-controlled)
        analysis_data = self._load_yaml(self.analysis_file)
        log.info(f"Loaded analysis.yml: v{analysis_data.get('config_version')}")
        
        # 2. Load paths.yml (machine-local)
        paths_data = self._load_yaml(self.paths_file)
        log.info(f"Loaded paths.yml from {self.paths_file}")
        
        # 3. Load .env file if provided
        env_data = {}
        if self.env_file and self.env_file.exists():
            env_data = self._load_yaml(self.env_file)
            log.info(f"Loaded .env overrides from {self.env_file}")
        
        # 4. Load environment variables (TTE_* prefix)
        env_vars = {k.replace("TTE_", ""): v 
                    for k, v in os.environ.items() 
                    if k.startswith("TTE_")}
        if env_vars:
            log.info(f"Found {len(env_vars)} TTE_* environment variables")
        
        # Merge: analysis > paths > .env > env_vars > defaults
        self._raw_config = self._merge(analysis_data, paths_data, env_data, env_vars)
        
        # 5. Parse into typed objects
        self._parse_config()
        
        # 6. Validate
        self.analysis.validate()
        self.paths.validate()
        
        log.info(f"✓ Config loaded successfully: {self.version.stamp()}")
        log.info("=== CONFIG LOADER END ===\n")
        
        return self
    
    def _load_yaml(self, path: Path) -> dict:
        """Load YAML file with error handling."""
        if not path.exists():
            if "analysis" in str(path):
                raise FileNotFoundError(
                    f"analysis.yml not found: {path}\n"
                    "Create it from the schema in CONFIG_DESIGN.md"
                )
            elif "paths" in str(path):
                raise FileNotFoundError(
                    f"paths.yml not found: {path}\n"
                    "Copy config/paths.yml.example to config/paths.yml and customize"
                )
            return {}
        
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
            return data
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {path}: {e}")
    
    def _merge(self, *configs: dict) -> dict:
        """
        Merge config dicts with precedence: first > second > third...
        """
        merged = {}
        for config in reversed(configs):
            merged.update(config)
        return merged
    
    def _parse_config(self) -> None:
        """Parse raw config into typed objects."""
        # Version metadata
        self.version = ConfigVersion(
            schema_version=self._raw_config.get("config_version", "1.0"),
            updated_date=self._raw_config.get("config_updated_date", "unknown"),
            commit_hash=self._raw_config.get("commit_hash"),
            loaded_time=datetime.now(),
        )
        
        # Analysis section
        analysis_data = self._raw_config.get("analysis", {})
        self.analysis = Analysis(
            end_date=analysis_data.get("end_date", "2025-12-31"),
            random_seed=int(analysis_data.get("random_seed", 42)),
            min_followup_days=int(analysis_data.get("followup", {}).get("min_days", 365)),
            washout_days=int(analysis_data.get("followup", {}).get("washout_days", 180)),
            min_age=int(analysis_data.get("age_range", {}).get("min", 40)),
            max_age=int(analysis_data.get("age_range", {}).get("max", 70)),
            dementia_lag_days=int(analysis_data.get("followup", {}).get("lag_dementia_days", 180)),
            stroke_lag_days=int(analysis_data.get("followup", {}).get("lag_stroke_days", 90)),
            ps_trim_lower=float(analysis_data.get("propensity_score", {}).get("trim_lower", 0.01)),
            ps_trim_upper=float(analysis_data.get("propensity_score", {}).get("trim_upper", 0.99)),
        )
        
        # Paths section (with environment variable expansion)
        paths_data = self._raw_config.get("paths", {})
        base_dir = self._expand_path(paths_data.get("base_dir", "~/tte-project"))
        
        self.paths = Paths(
            base_dir=base_dir,
            v4_extract_dir=self._resolve_path(
                paths_data.get("v4_extract_dir", "${base_dir}/AIRMS/most recent extract"),
                base_dir=base_dir
            ),
            output_root=self._resolve_path(
                paths_data.get("output", {}).get("root", 
                    "${base_dir}/AIRMS/results/run01"),
                base_dir=base_dir
            ),
            logs_dir=self._resolve_path(
                paths_data.get("output", {}).get("logs", 
                    "${output.root}/logs"),
                base_dir=base_dir,
                output_root=self._resolve_path(
                    paths_data.get("output", {}).get("root"),
                    base_dir=base_dir
                )
            ),
            temp_dir=self._resolve_path(
                paths_data.get("temp_dir"),
                base_dir=base_dir
            ) if paths_data.get("temp_dir") else None,
        )
    
    def _expand_path(self, path_str: str) -> Path:
        """Expand ~ to home directory."""
        return Path(path_str).expanduser()
    
    def _resolve_path(self, path_str: Optional[str], base_dir: Path, 
                      output_root: Optional[Path] = None) -> Path:
        """Resolve ${variable} references in paths."""
        if not path_str:
            return base_dir
        
        path_str = path_str.replace("${base_dir}", str(base_dir))
        if output_root:
            path_str = path_str.replace("${output.root}", str(output_root))
        
        return self._expand_path(path_str)


# Convenience: Global config instance
_global_config: Optional[ConfigLoader] = None


def init_config(
    analysis_file: str = "config/analysis.yml",
    paths_file: str = "config/paths.yml",
    env_file: Optional[str] = None,
) -> ConfigLoader:
    """Initialize global config instance."""
    global _global_config
    _global_config = ConfigLoader(analysis_file, paths_file, env_file)
    _global_config.load()
    return _global_config


def get_config() -> ConfigLoader:
    """Get global config instance (must call init_config first)."""
    if _global_config is None:
        raise RuntimeError(
            "Config not initialized. Call init_config() first:\n"
            "  config = init_config()\n"
            "  print(config.analysis.end_date)"
        )
    return _global_config
```

---

## Implementation: Three Example Scripts

### Script 1: Define Ingredients (Verification)

```python
# Scripts/Core/00_define_ingredients_run01.py
"""
Verification script: Load config and print ingredient lists.
Replaces old run01_config.py import pattern.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# Add config loader to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from config.loader import init_config, get_config

# Initialize config
config = init_config(
    analysis_file="config/analysis.yml",
    paths_file="config/paths.yml",
)

log_dir = config.paths.logs_dir
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.FileHandler(log_dir / f"00_ingredient_summary_{datetime.today().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# Access config
cfg = get_config()

log.info("="*80)
log.info(f"Configuration Stamp: {cfg.version.stamp()}")
log.info("="*80)

log.info(f"Analysis end date: {cfg.analysis.end_date}")
log.info(f"Random seed: {cfg.analysis.random_seed}")
log.info(f"Output logs directory: {cfg.paths.logs_dir}")
log.info(f"V4 extract directory: {cfg.paths.v4_extract_dir}")

log.info("\n✓ All configuration loaded successfully")
```

**Usage:**
```bash
# Normal run
python Scripts/Core/00_define_ingredients_run01.py

# Override random seed for testing
TTE_RANDOM_SEED=999 python Scripts/Core/00_define_ingredients_run01.py

# Use alternate config file
python Scripts/Core/00_define_ingredients_run01.py --config config/analysis.staging.yml
```

---

### Script 2: Build Cohort (Main Pipeline)

```python
# Scripts/Core/01_build_indexed_cohort_run01.py
"""
Build indexed cohort using centralized config.
"""

import sys
from pathlib import Path
import pandas as pd
from config.loader import init_config, get_config

# Initialize config
config = init_config()

cfg = get_config()
log_dir = cfg.paths.logs_dir

# Access clinical definitions from config
analysis_end_date = cfg.analysis.end_date
min_followup_days = cfg.analysis.min_followup_days
washout_days = cfg.analysis.washout_days

# Access paths from config
v4_antihtn_file = cfg.paths.data_file("raw_antihypertensive_exposures.parquet")
v4_conditions_file = cfg.paths.data_file("raw_conditions.parquet")
output_cohort = cfg.paths.output_root / "run01_indexed_cohort.parquet"

print(f"Loading v4 data from: {cfg.paths.v4_extract_dir}")
print(f"Writing output to: {cfg.paths.output_root}")
print(f"Analysis end date: {analysis_end_date}")

# Load data
exposures_df = pd.read_parquet(v4_antihtn_file)
conditions_df = pd.read_parquet(v4_conditions_file)

# ... build cohort logic ...

# Save output
output_cohort.parent.mkdir(parents=True, exist_ok=True)
indexed_cohort.to_parquet(output_cohort)

print(f"✓ Cohort built: {output_cohort}")
print(f"  Config version: {cfg.version.stamp()}")
```

---

### Script 3: Override Example (One-Off Analysis)

```python
# Usage: python Scripts/Sensitivity/sensitivity_analysis.py --analysis-end-date 2026-06-30
"""
Sensitivity analysis with alternate parameters.
"""

import argparse
import sys
from pathlib import Path
from config.loader import init_config, ConfigLoader

# Parse CLI overrides
parser = argparse.ArgumentParser()
parser.add_argument("--analysis-end-date", type=str, help="Override analysis end date")
parser.add_argument("--random-seed", type=int, help="Override random seed")
parser.add_argument("--ps-trim-lower", type=float, help="Override PS trim lower")
args = parser.parse_args()

# Initialize config
config = init_config()

# Apply CLI overrides (would require custom loader method)
if args.analysis_end_date:
    config.analysis.end_date = args.analysis_end_date
if args.random_seed:
    config.analysis.random_seed = args.random_seed

cfg = get_config()

print(f"Sensitivity analysis with:")
print(f"  End date: {cfg.analysis.end_date}")
print(f"  Random seed: {cfg.analysis.random_seed}")
print(f"  Config stamp: {cfg.version.stamp()}")
```

---

## Anti-Pattern Enforcement

### 1. Linting Rule: Ban Raw `Path()` Usage

Create `.pylintrc` or `ruff.toml` rule:

```toml
# ruff.toml
[tool.ruff.lint]
select = ["E", "F", "W"]

# Custom linting via pre-commit hook
```

**Pre-commit hook** (`.git/hooks/pre-commit`):

```bash
#!/bin/bash
# Detect hardcoded Path() or "/absolute/paths" in Python files

echo "Checking for hardcoded paths..."

patterns=(
    'Path("/Users/'
    'Path("/home/'
    'Path("C:\\\\Users\\'
    '"/data/results/'
    '"/Users/.*/Desktop/'
)

found_violations=0
for pattern in "${patterns[@]}"; do
    if git diff --cached -S "$pattern" | grep -q "$pattern"; then
        echo "ERROR: Hardcoded path detected: $pattern"
        echo "Use config loader instead: config.paths.<attribute>"
        found_violations=$((found_violations + 1))
    fi
done

if [ $found_violations -gt 0 ]; then
    exit 1
fi
```

### 2. Type Checking: Enforce Path Resolution

```python
# src/config/path_resolver.py
"""
Safe path resolution that only works with ConfigLoader.
Prevents accidental hardcoding.
"""

from pathlib import Path
from typing import Type

class SafePath:
    """Wrapper that only allows paths from ConfigLoader."""
    
    def __new__(cls, *args, **kwargs):
        # Reject direct instantiation
        raise RuntimeError(
            "❌ Do not use Path() directly for data files.\n"
            "Use config.paths.<attribute> instead:\n"
            "  from config.loader import get_config\n"
            "  cfg = get_config()\n"
            "  path = cfg.paths.data_file('raw_conditions.parquet')"
        )

# In scripts, use:
# from config.path_resolver import SafePath as Path
# This will catch any Path(hardcoded_string) attempts
```

### 3. Config Validation in CI/CD

```yaml
# .github/workflows/config-check.yml
name: Config Validation

on: [push, pull_request]

jobs:
  validate-config:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Validate config/analysis.yml schema
        run: |
          python scripts/validate_config.py config/analysis.yml
      
      - name: Audit for hardcoded paths
        run: |
          ! git diff HEAD~1 | grep -E '(Path\("|"/Users|"/home)'
```

---

## Versioning Strategy: Config Reproducibility

### Version Stamp Format

Every analysis output includes:

```
ConfigV1.0 (2026-07-01) @ 2026-07-01T14:32:55.123456
```

This appears in:
1. **Log files**: First line of every script run
2. **Output metadata**: Parquet file metadata
3. **Results tables**: Included in table footnotes
4. **Papers/reports**: Referenced in "Analysis" section

### Git Workflow

```bash
# Update analysis.yml (version-controlled)
git add config/analysis.yml
git commit -m "feat: update stroke definition to include S1 broad

- Updated STROKE_BROAD_SNOMED_IDS to include 4164092
- Bumps config schema version to 1.0.1
- Design decision C3 revised
- See config/analysis.yml for full details"

# Never commit paths.yml
echo "config/paths.yml" >> .gitignore
echo ".env" >> .gitignore
```

### Config Version Bump Rules

| Scenario | Bump | Example |
|----------|------|---------|
| Typo fix in ICD codes | Patch (1.0.1) | `B4_ICD_IDS = [45533052, ...]` |
| Add new outcome definition | Minor (1.1.0) | New secondary outcome |
| Change primary outcome | Major (2.0.0) | Redefine stroke definition |
| Adjust clinical lag | Patch | `STROKE_LAG_DAYS: 90 → 120` |

---

## Summary: Configuration Architecture

```
┌─ config/analysis.yml (git-tracked) ────────────────────────┐
│  • Clinical definitions (outcomes, drug classes, ICD codes) │
│  • Study parameters (seeds, lags, age range, PS bounds)     │
│  • Version: 1.0, Last updated 2026-07-01                   │
│  • Shared across all team members                           │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌─ config/paths.yml.example (git-tracked) ──────────────────┐
│  • Template: users copy to config/paths.yml                │
│  • Machine-local paths (NOT tracked in git)                │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌─ .env (local, NOT tracked) ──────────────────────────────┐
│  • Optional overrides (TTE_RANDOM_SEED=999)                │
│  • Machine-specific settings                               │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌─ CLI Flags (one-off) ────────────────────────────────────┐
│  • python script.py --random-seed 999                      │
│  • Overrides all file-based config                         │
└───────────────────────────────────────────────────────────────┘
                            ▼
              ConfigLoader.load() → Analysis + Paths
                            ▼
              Validated, Type-Checked, Immutable
                            ▼
       Version Stamp: ConfigV1.0 (2026-07-01) @ 14:32:55
```

**Key Benefits:**
- ✅ **No hardcoded paths** — enforced by type system
- ✅ **Machine-portable** — paths.yml is local, not tracked
- ✅ **Reproducible** — version stamp in every output
- ✅ **Auditable** — design decisions versioned in analysis.yml
- ✅ **Flexible** — CLI flags for one-off runs
- ✅ **Safe** — immutable dataclasses prevent mutation
- ✅ **Maintainable** — separation of concerns (clinical ≠ paths ≠ secrets)
