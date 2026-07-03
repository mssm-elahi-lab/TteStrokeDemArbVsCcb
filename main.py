from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

# Ensure the repository root (this file's directory) is importable so `src`
# resolves regardless of the working directory the pipeline is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import Config, load_config
from src.core import (
    add_pvalues,
    build_cohort,
    compute_outcomes,
    define_ingredients,
    diagnostics,
    plot_cumulative,
    plot_forest,
    table1,
    table2,
)
from src.reporting import (
    balance_plot,
    cohort_flow,
    concept_definitions,
    followup_timing,
    ps_diagnostics,
)
from src.sensitivity import (
    appendicitis_falsification,
    bp_hierarchy,
    curve_divergence,
    extended_followup,
    monotherapy,
)

Step = tuple[str, str, Callable[[Config], None]]

CORE_STEPS: tuple[Step, ...] = (
    ("define_ingredients", "Define ingredients", define_ingredients.run),
    ("build_cohort", "Build indexed cohort", build_cohort.run),
    ("compute_outcomes", "Compute outcomes & propensity score", compute_outcomes.run),
    ("table1", "Table 1", table1.run),
    ("add_pvalues", "Table 1 P values", add_pvalues.run),
    ("table2", "Table 2", table2.run),
    ("diagnostics", "Diagnostics", diagnostics.run),
    ("plot_forest", "Forest plot", plot_forest.run),
    ("plot_cumulative", "Cumulative event plot", plot_cumulative.run),
)

# Manuscript reporting artifacts built from core outputs (GAP1-4, 6). Run after
# CORE_STEPS so diagnostics' iptw_weight_summary.csv and the persisted PS/flow
# records exist. (GAP5 cox_coefficients is emitted directly by the table2 step.)
REPORTING_STEPS: tuple[Step, ...] = (
    ("cohort_flow", "Cohort flow (Figure 1)", cohort_flow.run),
    ("balance_plot", "Covariate-balance love plot (Supp Fig 2)", balance_plot.run),
    ("ps_diagnostics", "PS/IPTW diagnostics (Supp Table 1)", ps_diagnostics.run),
    ("concept_definitions", "Concept definitions (Supp Table 3)", concept_definitions.run),
    ("followup_timing", "Follow-up / event timing (Supp Table 7)", followup_timing.run),
)

SENSITIVITY_STEPS: tuple[Step, ...] = (
    ("monotherapy", "Monotherapy sensitivity", monotherapy.run),
    ("appendicitis_falsification", "Appendicitis falsification (negative control)", appendicitis_falsification.run),
    ("bp_hierarchy", "BP model hierarchy sensitivity", bp_hierarchy.run),
    ("extended_followup", "Extended follow-up (<2020 index)", extended_followup.run),
    ("curve_divergence", "Curve divergence timing", curve_divergence.run),
)

ALL_STEPS: tuple[Step, ...] = CORE_STEPS + REPORTING_STEPS + SENSITIVITY_STEPS


def _resolve_step(step_arg: str) -> Step:
    if step_arg.isdigit():
        idx = int(step_arg)
        if 0 <= idx < len(CORE_STEPS):
            return CORE_STEPS[idx]
        raise ValueError(f"Step index {idx} out of range (0-{len(CORE_STEPS) - 1})")
    for step in ALL_STEPS:
        if step[0] == step_arg:
            return step
    names = ", ".join(s[0] for s in ALL_STEPS)
    raise ValueError(f"Unknown step {step_arg!r}. Valid steps: 0-{len(CORE_STEPS) - 1}, {names}")


def _setup_run_logger(config: Config) -> logging.Logger:
    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger("main.run")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
    return logger


def _run_step(logger: logging.Logger, label: str, func: Callable[[Config], None], config: Config, dry_run: bool) -> None:
    logger.info(f"{label}...")
    if not dry_run:
        func(config)
    logger.info(f"{label}... done")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="TTE Analysis Pipeline (ARB vs DHP-CCB)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="Run core + sensitivity")
    parser.add_argument("--core", action="store_true", help="Run all core steps")
    parser.add_argument("--sensitivity", action="store_true", help="Run all sensitivity analyses")
    parser.add_argument("--step", metavar="STEP", help="Run a single core step by index or any step by name")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and print plan; no writes")
    parser.add_argument("--show-config", action="store_true", help="Print merged config and exit")
    parser.add_argument(
        "--list-required-files", action="store_true", help="Check required raw data files exist and exit"
    )
    parser.add_argument("--paths-yml", default="config/paths.yml", help="Path to machine-local paths config")
    parser.add_argument("--analysis-yml", default="config/analysis.yml", help="Path to shared analysis config")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        config = load_config(paths_yml=args.paths_yml, analysis_yml=args.analysis_yml)
    except Exception as exc:
        print(f"Config load failed: {exc}", file=sys.stderr)
        return 1

    logger = _setup_run_logger(config)
    logger.info(f"Starting: {config.version_stamp()}")

    if args.show_config:
        logger.info(f"Analysis end date: {config.analysis.end_date}")
        logger.info(f"Random seed: {config.analysis.random_seed}")
        logger.info(f"Primary outcomes: {config.analysis.outcomes.primary}")
        logger.info(f"Secondary outcomes: {config.analysis.outcomes.secondary}")
        logger.info(f"base_dir: {config.paths.base_dir}")
        logger.info(f"extract_v3: {config.paths.extract_v3}")
        logger.info(f"Output root: {config.paths.output_root}")
        return 0

    if args.list_required_files:
        missing = config.paths.missing_required_files()
        if missing:
            logger.info(f"Missing {len(missing)} required file(s):")
            for p in missing:
                logger.info(f" - {p}")
            return 1
        logger.info("All required data files present.")
        return 0

    if args.step is not None:
        try:
            _name, label, func = _resolve_step(args.step)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        _run_step(logger, label, func, config, args.dry_run)
        logger.info(f"Complete: {config.version_stamp()}")
        return 0

    run_core = args.core or args.all or not args.sensitivity
    run_sensitivity = args.sensitivity or args.all

    if run_core:
        logger.info("=== CORE ANALYSIS ===")
        for _name, label, func in CORE_STEPS:
            _run_step(logger, label, func, config, args.dry_run)

        logger.info("=== REPORTING ARTIFACTS ===")
        for _name, label, func in REPORTING_STEPS:
            _run_step(logger, label, func, config, args.dry_run)

    if run_sensitivity:
        logger.info("=== SENSITIVITY ANALYSES ===")
        for _name, label, func in SENSITIVITY_STEPS:
            _run_step(logger, label, func, config, args.dry_run)

    logger.info(f"Complete: {config.version_stamp()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
