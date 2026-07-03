# Legacy (archival)

Original `run01` analysis scripts and internal planning documents, kept for
**provenance only**. These are **superseded by the canonical pipeline in
`src/`** and are not maintained. Do not run them to reproduce the manuscript —
use `python main.py --all` instead (see the top-level `README.md`).

- `Scripts/` — the original per-step analysis scripts (`Core/`, `Sensitivity/`).
  Each `src/` module carries a `"""Ported from Scripts/...` docstring pointing
  back to its origin here.
- `docs/` — early organization/planning documents from the refactor
  (`COMPREHENSIVE_ORGANIZATION_PLAN.md`, `PROJECT_ORGANIZATION_PLAN.md`). The
  design decisions that survived into the shipped pipeline are documented in
  `docs/PIPELINE_EXECUTION_PLAN.md` and `docs/CONFIG_DESIGN.md`.
