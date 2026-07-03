"""
00_define_ingredients_run01_20260531.py
run01_v4_core_design_deathcensor — Final Candidate Run 01

VERIFICATION / SUMMARY SCRIPT — prints the approved ingredient lists and
design constants from run01_config.py and writes a confirmation log to:
  AIRMS/results/final_candidate_runs_20260531/run01_v4_core_design_deathcensor/
    logs/00_ingredient_summary_run01_<date>.log

This script does NOT build the cohort or run any models.
Run this first to confirm the ingredient lists before executing scripts 01–08.

Source of truth: run01_config.py (in this same directory)
DO NOT modify run01_config.py without updating README_design_changes.md
and preflight_ingredient_inventory.md.

Author: (initials)
Date:   2026-05-31
Data:   v4 expanded extract (AIRMS/most recent extract/)
Frozen v3 reference: src/may_2026/02b_build_indexed_cohort.py
"""

# ==============================================================================
# DRY-RUN GUARD
# ==============================================================================
# This script is safe to run anytime — it only reads config and prints.
# No guard required.

import sys
import logging
from pathlib import Path
from datetime import datetime

# run01_config.py is in the same directory as this script
sys.path.insert(0, str(Path(__file__).parent))
import run01_config as cfg

LOG_DIR = cfg.LOG_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_path = LOG_DIR / f"00_ingredient_summary_run01_{datetime.today().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

log.info("=" * 70)
log.info("run01_v4_core_design_deathcensor — INGREDIENT SUMMARY")
log.info("=" * 70)
log.info(f"ANALYSIS_END_DATE: {cfg.ANALYSIS_END_DATE_STR}")
log.info(f"WASHOUT_DAYS:      {cfg.WASHOUT_DAYS}")
log.info(f"MIN_AGE / MAX_AGE: {cfg.MIN_AGE} / {cfg.MAX_AGE}")
log.info(f"MIN_FOLLOWUP_DAYS: {cfg.MIN_FOLLOWUP_DAYS}")
log.info(f"RANDOM_SEED:       {cfg.RANDOM_SEED}")
log.info("")
log.info(f"INCLUDE_ADDITIONAL_DHP_INDEX: {cfg.INCLUDE_ADDITIONAL_DHP_INDEX}")
log.info("")
log.info("--- ARB index + washout ingredients ---")
for d in cfg.ARB_INGREDIENTS:
    log.info(f"  {d}")
log.info("")
log.info("--- DHP-CCB primary index comparator ---")
for d in cfg.DHP_CCB_PRIMARY_INDEX:
    log.info(f"  {d}")
log.info("")
log.info("--- DHP-CCB additional (washout always; index only if toggle=True) ---")
for d in cfg.DHP_CCB_ADDITIONAL:
    log.info(f"  {d}")
log.info("")
log.info(f"--- DHP-CCB INDEX (effective, toggle={cfg.INCLUDE_ADDITIONAL_DHP_INDEX}) ---")
for d in cfg.DHP_CCB_INDEX:
    log.info(f"  {d}")
log.info("")
log.info("--- DHP-CCB WASHOUT (all 4 always) ---")
for d in cfg.DHP_CCB_WASHOUT:
    log.info(f"  {d}")
log.info("")
log.info("--- DHP/non-DHP CCBs EXCLUDED from index and washout ---")
for d in cfg.DHP_CCB_EXCLUDED_IV_NONDPHP:
    log.info(f"  {d}")
log.info("")
log.info("--- THIAZIDE washout ingredients ---")
for d in cfg.THIAZIDE_WASHOUT:
    log.info(f"  {d}")
log.info(f"  [PENDING AUTHOR REVIEW: {cfg.THIAZIDE_REVIEW_PENDING}]")
log.info("")
log.info("--- ACEi washout ingredients (source: raw_baseline_medications) ---")
for d in cfg.ACEI_WASHOUT:
    log.info(f"  {d}")
log.info("")
log.info("--- PS covariates ---")
for c in cfg.PS_COVARIATES_FIXED:
    log.info(f"  {c}")
log.info("  + index_year categorical dummies (added dynamically)")
log.info("")
log.info("--- Outcome labels ---")
for k, v in cfg.OUTCOME_LABELS.items():
    log.info(f"  {k}: {v}")
log.info("")
log.info(f"Output dir: {cfg.OUT_DIR}")
log.info(f"Log:        {log_path}")
log.info("=" * 70)
log.info("Ingredient summary complete. Review above and run01_config.py before proceeding to script 01.")
TIA_OMOP       = 373503
# 381591 excluded
