"""Covariate-balance (standardized mean difference) math.

Tests the module-level SMD helpers that back Table 2 / the love plot and the
monotherapy sensitivity balance table. Hand-computed reference values, no data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.core.table1 import _smd as smd_pooled
from src.sensitivity.monotherapy import _smd_binary, _smd_continuous, _smd_weighted

pytestmark = pytest.mark.unit


def test_smd_pooled_zero_when_identical():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert smd_pooled(a, a) == 0.0


def test_smd_pooled_matches_hand_computation():
    a = np.array([1.0, 2.0, 3.0])          # mean 2, var(ddof=1) 1
    b = np.array([4.0, 5.0, 6.0])          # mean 5, var(ddof=1) 1
    pooled_sd = np.sqrt((1.0 + 1.0) / 2)   # = 1
    assert smd_pooled(a, b) == pytest.approx((2.0 - 5.0) / pooled_sd)


def test_smd_pooled_nan_when_no_spread():
    a = np.array([2.0, 2.0])
    b = np.array([2.0, 2.0])
    assert np.isnan(smd_pooled(a, b))


def test_smd_binary_zero_when_equal_prevalence():
    a = pd.Series([1, 0, 1, 0])
    b = pd.Series([1, 0, 1, 0])
    assert _smd_binary(a, b) == 0.0


def test_smd_binary_sign_and_magnitude():
    a = pd.Series([1, 1, 1, 0])   # p1 = 0.75
    b = pd.Series([1, 0, 0, 0])   # p2 = 0.25
    denom = np.sqrt((0.75 * 0.25 + 0.25 * 0.75) / 2)
    assert _smd_binary(a, b) == pytest.approx((0.75 - 0.25) / denom)


def test_smd_binary_nan_when_no_variance():
    a = pd.Series([1, 1, 1])
    b = pd.Series([1, 1, 1])
    assert np.isnan(_smd_binary(a, b))


def test_smd_continuous_matches_pooled_formula():
    a = pd.Series([10.0, 12.0, 14.0])
    b = pd.Series([11.0, 13.0, 15.0])
    s = np.sqrt((a.std() ** 2 + b.std() ** 2) / 2)
    assert _smd_continuous(a, b) == pytest.approx((a.mean() - b.mean()) / s)


def test_smd_weighted_uniform_weights_equal_unweighted_binary():
    # With all weights = 1, the weighted binary SMD equals the unweighted one.
    df = pd.DataFrame(
        {
            "treated": [1, 1, 1, 1, 0, 0, 0, 0],
            "bl_diabetes": [1, 1, 1, 0, 1, 0, 0, 0],
            "iptw": [1.0] * 8,
        }
    )
    arb = df[df.treated == 1]["bl_diabetes"]
    ccb = df[df.treated == 0]["bl_diabetes"]
    assert _smd_weighted("bl_diabetes", df, "iptw") == pytest.approx(_smd_binary(arb, ccb))


def test_smd_weighted_age_population_variance_hand_value():
    # age branch uses weighted (population, ddof=0) variance, not sample variance.
    # ARB=[60,70] mean 65 var 25; CCB=[62,72] mean 67 var 25; pooled sd = 5.
    df = pd.DataFrame(
        {
            "treated": [1, 1, 0, 0],
            "age_at_index": [60.0, 70.0, 62.0, 72.0],
            "iptw": [1.0, 1.0, 1.0, 1.0],
        }
    )
    assert _smd_weighted("age_at_index", df, "iptw") == pytest.approx((65.0 - 67.0) / 5.0)
