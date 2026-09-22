from collections.abc import Callable, Sequence
from typing import cast

import pytest

from agentreducer import (
    InitialCandidateNotFailingError,
    Oracle,
    OracleEvaluationError,
    OracleResult,
    ddmin,
)


class RecordingOracle:
    def __init__(self, evaluate: Callable[[Sequence[int]], OracleResult]) -> None:
        self._evaluate = evaluate
        self.calls: list[tuple[int, ...]] = []

    def evaluate(self, candidate: Sequence[int]) -> OracleResult:
        self.calls.append(tuple(candidate))
        return self._evaluate(candidate)


def test_oracle_can_implement_protocol_without_inheritance() -> None:
    oracle: Oracle[int] = RecordingOracle(
        lambda candidate: OracleResult.FAIL if 42 in candidate else OracleResult.PASS
    )

    assert oracle.evaluate([42]) is OracleResult.FAIL
    assert oracle.evaluate([]) is OracleResult.PASS
    assert ddmin([0, 42, 1], oracle) == [42]
    assert oracle.evaluate([42]) is OracleResult.FAIL
    assert oracle.evaluate([]) is OracleResult.PASS


@pytest.mark.parametrize("items", [[], [1], [1, 2, 3]])
def test_initial_pass_is_rejected_without_further_evaluations(items: list[int]) -> None:
    oracle = RecordingOracle(lambda candidate: OracleResult.PASS)
    original = items.copy()

    with pytest.raises(InitialCandidateNotFailingError, match="initial candidate"):
        ddmin(items, oracle)

    assert oracle.calls == [tuple(items)]
    assert items == original


@pytest.mark.parametrize(
    "error_candidate",
    [
        pytest.param((0, 1, 2, 3), id="initial"),
        pytest.param((), id="empty-check"),
        pytest.param((0, 1), id="partition"),
        pytest.param((1, 2, 3), id="complement"),
        pytest.param((1, 3), id="after-successful-reduction"),
    ],
)
def test_error_always_aborts_without_returning_a_result(
    error_candidate: tuple[int, ...],
) -> None:
    def evaluate(candidate: Sequence[int]) -> OracleResult:
        if tuple(candidate) == error_candidate:
            return OracleResult.ERROR
        if 1 in candidate and 3 in candidate:
            return OracleResult.FAIL
        return OracleResult.PASS

    items = [0, 1, 2, 3]
    oracle = RecordingOracle(evaluate)

    with pytest.raises(OracleEvaluationError, match="Oracle returned ERROR") as caught:
        ddmin(items, oracle)

    assert caught.value.candidate == error_candidate
    assert oracle.calls[-1] == error_candidate
    assert oracle.calls.count(error_candidate) == 1
    assert items == [0, 1, 2, 3]


@pytest.mark.parametrize("items", [[], [7]])
def test_empty_evaluation_error_cannot_be_skipped(items: list[int]) -> None:
    oracle = RecordingOracle(
        lambda candidate: OracleResult.FAIL if candidate else OracleResult.ERROR
    )

    with pytest.raises(OracleEvaluationError) as caught:
        ddmin(items, oracle)

    assert caught.value.candidate == ()
    assert oracle.calls == ([tuple(items), ()] if items else [()])


@pytest.mark.parametrize("error_candidate", [(0, 1, 2, 3), (0, 1), (1, 3)])
def test_exceptions_raised_by_oracle_propagate_unchanged(
    error_candidate: tuple[int, ...],
) -> None:
    failure = OSError("Evaluation environment unavailable")

    def evaluate(candidate: Sequence[int]) -> OracleResult:
        if tuple(candidate) == error_candidate:
            raise failure
        if 1 in candidate and 3 in candidate:
            return OracleResult.FAIL
        return OracleResult.PASS

    oracle = RecordingOracle(evaluate)

    with pytest.raises(OSError) as caught:
        ddmin([0, 1, 2, 3], oracle)

    assert caught.value is failure
    assert oracle.calls[-1] == error_candidate


@pytest.mark.parametrize("invalid_result", [None, True, False, "FAIL", "ERROR", 1, []])
@pytest.mark.parametrize("invalid_candidate", [(0, 1, 2, 3), (0, 1)])
def test_invalid_return_values_are_not_interpreted_as_outcomes(
    invalid_result: object, invalid_candidate: tuple[int, ...]
) -> None:
    def evaluate(candidate: Sequence[int]) -> OracleResult:
        if tuple(candidate) == invalid_candidate:
            # Deliberately violate the protocol to verify runtime validation.
            return cast(OracleResult, invalid_result)
        return OracleResult.FAIL if len(candidate) == 4 else OracleResult.PASS

    oracle = RecordingOracle(evaluate)

    with pytest.raises(TypeError, match="must return an OracleResult"):
        ddmin([0, 1, 2, 3], oracle)

    assert oracle.calls[-1] == invalid_candidate
