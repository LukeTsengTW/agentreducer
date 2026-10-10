from dataclasses import FrozenInstanceError
from decimal import Decimal
from fractions import Fraction

import pytest

from agentreducer.stochastic import StochasticPolicy


@pytest.mark.parametrize("threshold", ["0.8", "4/5", 0.8, Decimal("0.8"), Fraction(4, 5)])
def test_threshold_is_exact_and_policy_frozen(threshold):
    policy = StochasticPolicy(2, 5, threshold)
    assert policy.failure_threshold == Fraction(4, 5)
    assert policy.target_failures == 4
    with pytest.raises(FrozenInstanceError):
        policy.max_trials = 6


@pytest.mark.parametrize("minimum,maximum,threshold,k", [
    (1, 1, 1, 1), (1, 7, Fraction(1, 3), 3), (1, 3, "1/3", 1),
    (2, 10, "0.000000000000000000001", 1), (1, 9, 1.0, 9),
])
def test_integer_threshold_boundaries(minimum, maximum, threshold, k):
    assert StochasticPolicy(minimum, maximum, threshold).target_failures == k


@pytest.mark.parametrize("threshold", [True, False, None, [], object(), "", " ", "bad", "1/0",
    0, -1, 2, "0", "-0.1", "1.01", float("nan"), float("inf"), float("-inf"),
    Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises((TypeError, ValueError)):
        StochasticPolicy(failure_threshold=threshold)


@pytest.mark.parametrize("minimum,maximum", [(0, 5), (-1, 5), (1, 0), (1, -5),
    (True, 5), (1, False), (1.0, 5), (1, 5.0), (None, 5), (1, None), ("1", 5), (6, 5)])
def test_invalid_trial_counts_rejected(minimum, maximum):
    with pytest.raises((TypeError, ValueError)):
        StochasticPolicy(minimum, maximum)


def test_default_is_fixed_five_with_exact_four_failures():
    assert StochasticPolicy() == StochasticPolicy(5, 5, "0.8")
    assert StochasticPolicy().target_failures == 4
