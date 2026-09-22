"""Deterministic partition/complement delta debugging."""

from collections.abc import Sequence
from typing import TypeVar

from ..models import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from ..oracle import Oracle

T = TypeVar("T")


def ddmin(candidate: Sequence[T], oracle: Oracle[T]) -> list[T]:
    """Return a 1-minimal failing subsequence as a new list.

    The initial candidate must FAIL. Every ERROR aborts with
    OracleEvaluationError; exceptions raised by the oracle propagate unchanged.
    A failing empty candidate yields an empty list. Otherwise, balanced
    contiguous partitions and then their complements are tried left to right,
    accepting the first FAIL. The oracle must be deterministic. The result need
    not be the globally smallest failing subsequence.

    Evaluations receive tuple snapshots. Item identities, duplicates, and order
    are preserved; item objects are not copied and must not be mutated by the
    oracle.
    """
    current = tuple(candidate)
    if _evaluate(current, oracle) is OracleResult.PASS:
        raise InitialCandidateNotFailingError("The initial candidate returned PASS.")

    if not current or _evaluate((), oracle) is OracleResult.FAIL:
        return []

    granularity = 2
    while len(current) >= 2:
        bounds = [
            (
                index * len(current) // granularity,
                (index + 1) * len(current) // granularity,
            )
            for index in range(granularity)
        ]

        for start, end in bounds:
            subset = current[start:end]
            if _evaluate(subset, oracle) is OracleResult.FAIL:
                current = subset
                granularity = 2
                break
        else:
            for start, end in bounds:
                complement = current[:start] + current[end:]
                if _evaluate(complement, oracle) is OracleResult.FAIL:
                    current = complement
                    granularity = max(2, granularity - 1)
                    break
            else:
                # At singleton granularity every one-item deletion has passed.
                if granularity == len(current):
                    break
                granularity = min(len(current), granularity * 2)

    return list(current)


def _evaluate(candidate: tuple[T, ...], oracle: Oracle[T]) -> OracleResult:
    result = oracle.evaluate(candidate)
    if result is OracleResult.ERROR:
        raise OracleEvaluationError(candidate)
    if not isinstance(result, OracleResult):
        raise TypeError("Oracle.evaluate() must return an OracleResult.")
    return result
