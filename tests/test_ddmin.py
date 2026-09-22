from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

import pytest

from agentreducer import InitialCandidateNotFailingError, Oracle, OracleResult, ddmin

T = TypeVar("T")


class PredicateOracle(Generic[T]):
    def __init__(self, fails: Callable[[Sequence[T]], bool]) -> None:
        self.fails = fails
        self.calls: list[tuple[T, ...]] = []

    def evaluate(self, candidate: Sequence[T]) -> OracleResult:
        assert isinstance(candidate, tuple)
        self.calls.append(candidate)
        return OracleResult.FAIL if self.fails(candidate) else OracleResult.PASS


def assert_one_minimal(result: Sequence[T], oracle: Oracle[T]) -> None:
    snapshot = tuple(result)
    assert oracle.evaluate(snapshot) is OracleResult.FAIL
    for index in range(len(snapshot)):
        deletion = snapshot[:index] + snapshot[index + 1:]
        assert oracle.evaluate(deletion) is OracleResult.PASS, (snapshot, deletion)


@pytest.mark.parametrize(
    ("items", "required", "expected"),
    [
        pytest.param(
            ["A", "B", "USE_CACHE", "C", "D", "AUTH_MODULE", "E", "F"],
            {"USE_CACHE", "AUTH_MODULE"},
            ["USE_CACHE", "AUTH_MODULE"],
            id="cache-and-auth",
        ),
        pytest.param(
            ["A", "B", "BUG", "C", "D"], {"BUG"}, ["BUG"],
            id="single-failure-item",
        ),
        pytest.param(
            ["A", "B", "C", "D", "E"],
            {"A", "B", "C", "D", "E"},
            ["A", "B", "C", "D", "E"],
            id="no-removable-items",
        ),
        pytest.param(["BUG"], {"BUG"}, ["BUG"], id="already-minimal-singleton"),
        pytest.param(["A", "BUG"], {"BUG"}, ["BUG"], id="two-items"),
    ],
)
def test_basic_reductions(
    items: list[str], required: set[str], expected: list[str]
) -> None:
    original = items.copy()
    oracle = PredicateOracle[str](lambda candidate: required.issubset(candidate))

    result = ddmin(items, oracle)

    assert result == expected
    assert items == original
    assert result is not items
    assert_one_minimal(result, oracle)


def test_many_irrelevant_items() -> None:
    items = list(range(1024))
    oracle = PredicateOracle[int](lambda candidate: 17 in candidate and 1001 in candidate)

    result = ddmin(items, oracle)

    assert result == [17, 1001]
    # A broad regression guard against enumerating candidate combinations.
    assert len(oracle.calls) < len(items)
    assert_one_minimal(result, oracle)


@pytest.mark.parametrize("size", [2, 3, 5, 7, 9, 17])
def test_uneven_partitions_do_not_drop_necessary_items(size: int) -> None:
    items = list(range(size))
    oracle = PredicateOracle[int](lambda candidate: len(candidate) == size)

    result = ddmin(items, oracle)

    assert result == items
    assert_one_minimal(result, oracle)


def test_complement_reduction_and_evaluation_order() -> None:
    oracle = PredicateOracle[int](lambda candidate: len(candidate) >= 3)

    result = ddmin([0, 1, 2, 3], oracle)

    assert result == [1, 2, 3]
    assert oracle.calls[:11] == [
        (0, 1, 2, 3), (),
        (0, 1), (2, 3),  # Partitions at granularity two.
        (2, 3), (0, 1),  # Their complements.
        (0,), (1,), (2,), (3,),  # Partitions at singleton granularity.
        (1, 2, 3),  # The first failing complement.
    ]
    assert_one_minimal(result, oracle)


def test_multiple_minima_are_resolved_deterministically() -> None:
    def fails(candidate: Sequence[str]) -> bool:
        return {"A", "B"}.issubset(candidate) or {"C", "D"}.issubset(candidate)

    first = PredicateOracle(fails)
    second = PredicateOracle(fails)

    first_result = ddmin(["A", "B", "C", "D"], first)
    second_result = ddmin(["A", "B", "C", "D"], second)

    assert first_result == second_result == ["A", "B"]
    assert first.calls == second.calls
    assert_one_minimal(first_result, first)
    assert_one_minimal(second_result, second)


def test_one_minimal_is_not_necessarily_globally_smallest() -> None:
    oracle = PredicateOracle[int](
        lambda candidate: {1, 2, 3}.issubset(candidate) or 5 in candidate
    )

    result = ddmin([1, 2, 3, 4, 5, 6], oracle)

    assert result == [1, 2, 3]
    assert oracle.evaluate((5,)) is OracleResult.FAIL
    assert_one_minimal(result, oracle)


def test_duplicate_occurrences_are_preserved() -> None:
    oracle = PredicateOracle[str](lambda candidate: candidate.count("BUG") >= 2)

    result = ddmin(["BUG", "noise", "BUG", "noise", "BUG"], oracle)

    assert result == ["BUG", "BUG"]
    assert_one_minimal(result, oracle)


def test_order_sensitive_failure() -> None:
    oracle = PredicateOracle[int](
        lambda candidate: (
            3 in candidate and 1 in candidate and candidate.index(3) < candidate.index(1)
        )
    )

    result = ddmin((2, 0, 3, 1, 4), oracle)

    assert result == [3, 1]
    assert_one_minimal(result, oracle)


def test_unhashable_items_retain_identity_and_are_not_mutated() -> None:
    items = [{"needed": False}, {"needed": True}, {"needed": False}]
    original = [item.copy() for item in items]
    oracle = PredicateOracle[dict[str, bool]](
        lambda candidate: any(item["needed"] for item in candidate)
    )

    result = ddmin(items, oracle)

    assert len(result) == 1
    assert result[0] is items[1]
    assert items == original
    assert_one_minimal(result, oracle)
    result.clear()
    assert items == original


def test_items_need_not_support_equality() -> None:
    class Token:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("The reducer must not compare item values.")

    required = Token()
    oracle = PredicateOracle[Token](
        lambda candidate: any(item is required for item in candidate)
    )

    result = ddmin([Token(), required, Token()], oracle)

    assert len(result) == 1
    assert result[0] is required
    assert_one_minimal(result, oracle)


@pytest.mark.parametrize("items", [[], [1], [0, 1, 2]])
def test_failing_empty_candidate_is_returned(items: list[int]) -> None:
    oracle = PredicateOracle[int](lambda candidate: True)

    result = ddmin(items, oracle)

    assert result == []
    assert result is not items
    assert oracle.calls == ([tuple(items), ()] if items else [()])
    assert_one_minimal(result, oracle)


def test_initial_failure_is_checked_before_failing_empty_candidate() -> None:
    oracle = PredicateOracle[int](lambda candidate: not candidate)

    with pytest.raises(InitialCandidateNotFailingError):
        ddmin([1, 2], oracle)

    assert oracle.calls == [(1, 2)]


def test_non_monotonic_failure() -> None:
    # Adding an item can remove the failure, while the full input still fails.
    failing = {(0, 1, 2, 3, 4), (0, 2, 3, 4), (0, 2, 4), (0, 4)}
    oracle = PredicateOracle[int](lambda candidate: tuple(candidate) in failing)

    result = ddmin([0, 1, 2, 3, 4], oracle)

    assert result == [0, 4]
    assert_one_minimal(result, oracle)


def test_one_minimality_for_all_four_item_boolean_oracles() -> None:
    # Enumerate oracle truth tables only in this bounded test, not in the reducer.
    # All 32,768 PASS/FAIL mappings with a failing full input are checked, including
    # non-monotonic failures and failing empty candidates.
    items = [0, 1, 2, 3]
    full_mask = (1 << len(items)) - 1
    for other_outcomes in range(1 << full_mask):
        table = other_outcomes | (1 << full_mask)

        def fails(candidate: Sequence[int], outcomes: int = table) -> bool:
            mask = sum(1 << item for item in candidate)
            return bool(outcomes & (1 << mask))

        oracle = PredicateOracle(fails)
        result = ddmin(items, oracle)

        assert result == sorted(set(result))
        assert set(result).issubset(items)
        assert_one_minimal(result, oracle)
