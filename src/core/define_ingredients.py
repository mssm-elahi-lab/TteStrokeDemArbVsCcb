"""Ported from Scripts/Core/00_define_ingredients_run01_20260531.py.

Verification/summary script — logs the approved ingredient lists and design
constants from config/analysis.yml. Does not build the cohort or run models;
run this first to confirm ingredient lists before the rest of the pipeline.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

from src.config import Config


def run(config: Config) -> None:
    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"define_ingredients_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(f"{__name__}.run")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))

    analysis = config.analysis
    drugs = analysis.drug_classes

    logger.info("=" * 70)
    logger.info("TTE Analysis — INGREDIENT SUMMARY")
    logger.info("=" * 70)
    logger.info(f"ANALYSIS_END_DATE: {analysis.end_date}")
    logger.info(f"WASHOUT_DAYS:      {analysis.cohort.washout_days}")
    logger.info(f"MIN_AGE / MAX_AGE: {analysis.cohort.min_age} / {analysis.cohort.max_age}")
    logger.info(f"MIN_FOLLOWUP_DAYS: {analysis.cohort.min_followup_days}")
    logger.info(f"RANDOM_SEED:       {analysis.random_seed}")
    logger.info("")
    logger.info(f"INCLUDE_ADDITIONAL_DHP_INDEX: {drugs.include_additional_dhp_index}")
    logger.info("")
    logger.info("--- ARB index + washout ingredients ---")
    for d in drugs.arb_ingredients:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- DHP-CCB primary index comparator ---")
    for d in drugs.dhp_ccb_primary_index:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- DHP-CCB additional (washout always; index only if toggle=True) ---")
    for d in drugs.dhp_ccb_additional:
        logger.info(f"  {d}")
    logger.info("")
    logger.info(f"--- DHP-CCB INDEX (effective, toggle={drugs.include_additional_dhp_index}) ---")
    for d in drugs.dhp_ccb_index:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- DHP-CCB WASHOUT (all 4 always) ---")
    for d in drugs.dhp_ccb_washout:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- DHP/non-DHP CCBs EXCLUDED from index and washout ---")
    for d in drugs.dhp_ccb_excluded_iv_nondhp:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- THIAZIDE washout ingredients ---")
    for d in drugs.thiazide_washout:
        logger.info(f"  {d}")
    logger.info(f"  [PENDING AUTHOR REVIEW: {list(drugs.thiazide_review_pending)}]")
    logger.info("")
    logger.info("--- ACEi washout ingredients (source: raw_baseline_medications) ---")
    for d in drugs.acei_washout:
        logger.info(f"  {d}")
    logger.info("")
    logger.info("--- PS covariates ---")
    for c in analysis.propensity_score.covariates_fixed:
        logger.info(f"  {c}")
    logger.info("  + index_year categorical dummies (added dynamically)")
    logger.info("")
    logger.info("--- Outcome labels ---")
    for k, v in analysis.outcomes.labels.items():
        logger.info(f"  {k}: {v}")
    logger.info("")
    logger.info(f"Output dir: {config.paths.output_core}")
    logger.info(f"Log:        {log_path}")
    logger.info("=" * 70)
    logger.info("Ingredient summary complete. Review above before proceeding to build_cohort.")

    for handler in logger.handlers:
        handler.close()


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
