"""P-value display formatting and the multiple-testing family size.

Covers the importable, pure pieces of the multiple-testing machinery. The
Bonferroni/BH-FDR *application* is computed inline inside `table2.run()` over
the survival dataset and is not separately importable, so it is exercised by
the pipeline rather than here; `bonferroni_k` (the family size those
corrections use) is unit-tested below.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import MultipleTestingConfig, OutcomeDef, OutcomesConfig
from src.core.add_pvalues import _pval_format

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "p,expected",
    [
        (0.0004, "<0.001"),
        (0.0009999, "<0.001"),
        (0.001, "0.001"),
        (0.04321, "0.043"),
        (0.5, "0.500"),
        (1.0, "1.000"),
    ],
)
def test_pval_format_values(p, expected):
    assert _pval_format(p) == expected


def test_pval_format_nan_is_blank():
    assert _pval_format(np.nan) == ""


def test_pval_format_just_below_threshold_is_capped():
    # 0.0005 < 0.001, so it renders as the "<0.001" floor, not "0.001".
    assert _pval_format(0.0005) == "<0.001"


def test_bonferroni_k_counts_primary_outcomes():
    outcomes = OutcomesConfig(
        0,
        0,
        (
            OutcomeDef("dementia", "primary", "Dementia"),
            OutcomeDef("stroke", "primary", "Stroke"),
            OutcomeDef("tia", "secondary", "TIA"),
        ),
    )
    mt = MultipleTestingConfig(primary_alpha=0.05)
    # Family = the 2 primary outcomes only; the secondary is excluded.
    assert mt.bonferroni_k(outcomes) == 2
