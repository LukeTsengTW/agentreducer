"""Exact finite-horizon empirical failure policy; no probability estimate."""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import math


@dataclass(frozen=True)
class StochasticPolicy:
    min_trials: int = 5
    max_trials: int = 5
    failure_threshold: Fraction = Fraction(4, 5)

    def __post_init__(self):
        if type(self.min_trials) is not int or type(self.max_trials) is not int:
            raise TypeError("Trial counts must be strict integers.")
        if not 1 <= self.min_trials <= self.max_trials:
            raise ValueError("Require 1 <= min_trials <= max_trials.")
        value = self.failure_threshold
        if isinstance(value, bool) or not isinstance(value, (Fraction, int, Decimal, float, str)):
            raise TypeError("Unsupported threshold type.")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Threshold must be finite.")
            value = str(value)
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("Threshold must be finite.")
        try:
            threshold = Fraction(value)
        except (ValueError, ZeroDivisionError, OverflowError):
            raise ValueError("Invalid threshold.") from None
        if not 0 < threshold <= 1:
            raise ValueError("Require 0 < failure_threshold <= 1.")
        object.__setattr__(self, "failure_threshold", threshold)

    @property
    def target_failures(self) -> int:
        a, b = self.failure_threshold.numerator, self.failure_threshold.denominator
        return (a * self.max_trials + b - 1) // b
