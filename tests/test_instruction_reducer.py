from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.instructions import (
    InstructionBlock,
    InstructionBlockKind,
    InstructionCandidate,
    InstructionEvaluator,
    InstructionOracle,
    InstructionReducer,
    parse_instructions,
)


class RecordingOracle:
    def __init__(self, evaluate: Callable[[InstructionCandidate], OracleResult]) -> None:
        self._evaluate = evaluate
        self.calls: list[InstructionCandidate] = []

    def evaluate(self, candidate: InstructionCandidate) -> OracleResult:
        self.calls.append(candidate)
        return self._evaluate(candidate)


def assert_one_minimal(candidate: InstructionCandidate, oracle: InstructionEvaluator) -> None:
    assert oracle.evaluate(candidate) is OracleResult.FAIL
    for index in range(len(candidate.blocks)):
        deletion = InstructionCandidate(candidate.blocks[:index] + candidate.blocks[index + 1:])
        assert oracle.evaluate(deletion) is OracleResult.PASS, deletion


def test_synthetic_failure_retains_only_two_rules_and_their_section() -> None:
    source = (
        "# Agent Rules\n\nGeneral project instructions.\n\n## Development\n\n"
        "- Inspect existing utilities.\n- Prefer functional programming.\n"
        "- Use the existing cache.\n- Do not modify unrelated files.\n\n"
        "## Testing\n\n- Run tests before finishing."
    )
    calls: list[str] = []

    def evaluate(text: str) -> OracleResult:
        calls.append(text)
        if "Prefer functional programming." in text and "Use the existing cache." in text:
            return OracleResult.FAIL
        return OracleResult.PASS

    candidate = parse_instructions(source)
    oracle = InstructionOracle(evaluate)
    result = InstructionReducer().reduce(candidate, oracle)

    assert result.original is candidate
    assert result.original_block_count == 6
    assert [block.text for block in result.reduced.blocks] == [
        "- Prefer functional programming.", "- Use the existing cache."
    ]
    for block in result.reduced.blocks:
        assert block.kind is InstructionBlockKind.UNORDERED_LIST_ITEM
        assert [h.title for h in block.heading_path] == ["Agent Rules", "Development"]
    assert "## Testing" not in result.reduced.render()
    assert "General project instructions." not in result.reduced.render()
    assert calls[:2] == [candidate.render(), ""]
    assert result.evaluations == len(calls)
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize(
    ("source", "required", "expected"),
    [
        ("# Rules\n- A\n- KEEP\n- B", {"KEEP"}, ["- KEEP"]),
        ("# Rules\n- A\n- B\n- C", {"- A", "- B", "- C"}, ["- A", "- B", "- C"]),
        ("# Rules\nA paragraph.\n\nKEEP this prose.\n\nAnother paragraph.",
         {"KEEP"}, ["KEEP this prose."]),
        ("# Rules\n## First\n- CACHE\n- noise\n## Second\n- AUTH\n- more noise",
         {"CACHE", "AUTH"}, ["- CACHE", "- AUTH"]),
        ("# Rules\n## Same\n1. CACHE\n2. noise\n3. AUTH",
         {"CACHE", "AUTH"}, ["1. CACHE", "3. AUTH"]),
        ("Intro before headings.\n\n# Rules\nKeep after heading.\n\nNoise.",
         {"Intro", "Keep"}, ["Intro before headings.", "Keep after heading."]),
        ("# Rules\n" + "\n".join(f"- noise-{i}" for i in range(256)) + "\n- KEEP\n- tail",
         {"KEEP"}, ["- KEEP"]),
    ],
)
def test_reduction_cases(source: str, required: set[str], expected: list[str]) -> None:
    candidate = parse_instructions(source)
    original_blocks = candidate.blocks
    oracle = InstructionOracle(
        lambda text: OracleResult.FAIL if all(term in text for term in required) else OracleResult.PASS
    )

    result = InstructionReducer().reduce(candidate, oracle)

    assert [block.text for block in result.reduced.blocks] == expected
    assert candidate.blocks is original_blocks
    positions = [
        next(i for i, original in enumerate(candidate.blocks) if original is block)
        for block in result.reduced.blocks
    ]
    assert positions == sorted(set(positions))
    assert_one_minimal(result.reduced, oracle)


def test_duplicate_rules_are_not_deduplicated() -> None:
    candidate = parse_instructions("# Rules\n- repeat-rule\n- noise\n- repeat-rule\n- repeat-rule")
    oracle = InstructionOracle(
        lambda text: OracleResult.FAIL if text.count("repeat-rule") >= 2 else OracleResult.PASS
    )

    result = InstructionReducer().reduce(candidate, oracle)

    assert [block.text for block in result.reduced.blocks] == ["- repeat-rule", "- repeat-rule"]
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize(
    "atomic",
    [
        "- Before finishing:\n  - run pytest\n  - run mypy\n  - run ruff",
        "1. Before finishing:\n   1. run pytest\n   2. run mypy",
        "```sh\n# Example\nrun pytest\nrun mypy\n```",
        "- Example:\n  ```sh\n  run pytest\n  run mypy\n  ```",
    ],
)
def test_nested_and_code_content_is_not_reduced_inside_its_parent(atomic: str) -> None:
    candidate = parse_instructions("# Rules\n\nIrrelevant prose.\n\n" + atomic + "\n\nOther prose.")
    oracle = InstructionOracle(
        lambda text: OracleResult.FAIL if "run pytest" in text else OracleResult.PASS
    )

    result = InstructionReducer().reduce(candidate, oracle)

    assert len(result.reduced.blocks) == 1
    assert result.reduced.blocks[0].text == atomic
    assert "run mypy" in result.reduced.render()
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("outcome", list(OracleResult))
def test_text_oracle_adapter_forwards_every_outcome(outcome: OracleResult) -> None:
    calls: list[str] = []

    def evaluate(text: str) -> OracleResult:
        calls.append(text)
        return outcome

    candidate = parse_instructions("# Rules\n- A\n- B")
    oracle: InstructionEvaluator = InstructionOracle(evaluate)

    assert oracle.evaluate(candidate) is outcome
    assert calls == [candidate.render()]


@pytest.mark.parametrize("source", ["", "# Heading only", "# Rules\n- Does not fail."])
def test_initial_pass_raises_without_testing_other_candidates(source: str) -> None:
    candidate = parse_instructions(source)
    oracle = RecordingOracle(lambda value: OracleResult.PASS)

    with pytest.raises(InitialCandidateNotFailingError):
        InstructionReducer().reduce(candidate, oracle)

    assert oracle.calls == [candidate]


@pytest.mark.parametrize("error_units", [("A", "B", "C", "D"), (), ("A", "B"), ("B", "C", "D"), ("B", "D")])
def test_error_propagates_during_initial_empty_partition_and_complement_checks(
    error_units: tuple[str, ...],
) -> None:
    def evaluate(candidate: InstructionCandidate) -> OracleResult:
        units = tuple(block.text[2:] for block in candidate.blocks)
        if units == error_units:
            return OracleResult.ERROR
        return OracleResult.FAIL if "B" in units and "D" in units else OracleResult.PASS

    candidate = parse_instructions("# Rules\n- A\n- B\n- C\n- D")
    oracle = RecordingOracle(evaluate)

    with pytest.raises(OracleEvaluationError) as caught:
        InstructionReducer().reduce(candidate, oracle)

    assert caught.value.candidate == oracle.calls[-1].blocks
    assert tuple(block.text[2:] for block in oracle.calls[-1].blocks) == error_units
    assert sum(call.blocks == caught.value.candidate for call in oracle.calls) == 1


def test_empty_initial_error_cannot_be_a_successful_reduction() -> None:
    with pytest.raises(OracleEvaluationError):
        InstructionReducer().reduce(InstructionCandidate(), InstructionOracle(lambda text: OracleResult.ERROR))


def test_text_evaluator_exception_propagates_unchanged() -> None:
    failure = OSError("Synthetic evaluator unavailable")
    calls: list[str] = []

    def evaluate(text: str) -> OracleResult:
        calls.append(text)
        if text == "":
            raise failure
        return OracleResult.FAIL

    candidate = parse_instructions("# Rules\n- A")
    with pytest.raises(OSError) as caught:
        InstructionReducer().reduce(candidate, InstructionOracle(evaluate))

    assert caught.value is failure
    assert calls == [candidate.render(), ""]


@pytest.mark.parametrize("invalid", [None, True, "FAIL"])
def test_invalid_outcomes_still_raise_type_error(invalid: object) -> None:
    with pytest.raises(TypeError, match="must return an OracleResult"):
        InstructionReducer().reduce(
            parse_instructions("- Rule."), InstructionOracle(lambda text: cast(OracleResult, invalid))
        )


def test_result_and_evaluation_counts_are_deterministic_and_local_to_each_run() -> None:
    def evaluate(candidate: InstructionCandidate) -> OracleResult:
        texts = {block.text for block in candidate.blocks}
        return (OracleResult.FAIL if {"- A", "- B"} <= texts or {"- C", "- D"} <= texts
                else OracleResult.PASS)

    candidate = parse_instructions("# Rules\n- A\n- B\n- C\n- D")
    oracle = RecordingOracle(evaluate)
    reducer = InstructionReducer()
    first = reducer.reduce(candidate, oracle)
    trace = oracle.calls.copy()
    second = reducer.reduce(candidate, oracle)

    assert first == second
    assert [block.text for block in first.reduced.blocks] == ["- A", "- B"]
    assert trace == oracle.calls[len(trace):]
    assert first.evaluations == second.evaluations == len(trace)
    assert_one_minimal(first.reduced, oracle)
    assert_one_minimal(second.reduced, oracle)


def test_statistics_include_heading_and_separator_characters_and_are_immutable() -> None:
    candidate = parse_instructions("# Root\r\n\r\n- KEEP\r\n- noise\r\n")
    oracle = RecordingOracle(
        lambda value: OracleResult.FAIL if "KEEP" in value.render() else OracleResult.PASS
    )
    result = InstructionReducer().reduce(candidate, oracle)
    evaluations = len(oracle.calls)

    assert result.original is candidate
    assert result.original_block_count == 2
    assert result.reduced_block_count == 1
    assert result.original_character_count == 23
    assert result.reduced_character_count == 14
    assert result.block_reduction_percentage == 50.0
    assert result.character_reduction_percentage == pytest.approx(900 / 23)
    assert result.evaluations == evaluations
    assert len(oracle.calls) == evaluations
    with pytest.raises(FrozenInstanceError):
        setattr(result, "evaluations", 0)
    assert_one_minimal(result.reduced, oracle)
    assert result.evaluations == evaluations


@pytest.mark.parametrize(
    ("candidate", "block_percentage", "character_percentage", "evaluations"),
    [
        (InstructionCandidate(), 0.0, 0.0, 1),
        (parse_instructions("# Heading only\n## Empty child"), 0.0, 0.0, 1),
        (InstructionCandidate((InstructionBlock(""),)), 100.0, 0.0, 2),
        (parse_instructions("# Rules\n- Real content."), 100.0, 100.0, 2),
    ],
)
def test_failing_empty_candidates_and_zero_denominators(
    candidate: InstructionCandidate, block_percentage: float,
    character_percentage: float, evaluations: int,
) -> None:
    oracle = RecordingOracle(lambda value: OracleResult.FAIL)
    result = InstructionReducer().reduce(candidate, oracle)

    assert result.reduced == InstructionCandidate()
    assert result.reduced_block_count == result.reduced_character_count == 0
    assert result.block_reduction_percentage == block_percentage
    assert result.character_reduction_percentage == character_percentage
    assert result.evaluations == evaluations == len(oracle.calls)
    assert_one_minimal(result.reduced, oracle)


def test_unreduced_candidate_has_zero_percent_reduction_and_exact_evaluations() -> None:
    candidate = parse_instructions("# 規則\n- Keep 😀")
    oracle = RecordingOracle(
        lambda value: OracleResult.FAIL if value.blocks else OracleResult.PASS
    )
    result = InstructionReducer().reduce(candidate, oracle)

    assert result.original == result.reduced
    assert result.original_character_count == result.reduced_character_count == 14
    assert result.block_reduction_percentage == result.character_reduction_percentage == 0.0
    assert result.evaluations == 2
    assert oracle.calls == [candidate, InstructionCandidate()]
    assert_one_minimal(result.reduced, oracle)


def test_one_minimality_for_all_three_rule_boolean_text_oracles() -> None:
    candidate = parse_instructions("# Root\n## First\n- unit-A\n- unit-B\n## Second\n- unit-C")
    bits = {"unit-A": 1, "unit-B": 2, "unit-C": 4}
    for other_outcomes in range(128):
        table = other_outcomes | (1 << 7)

        def evaluate(text: str, outcomes: int = table) -> OracleResult:
            mask = sum(bit for term, bit in bits.items() if term in text)
            return OracleResult.FAIL if outcomes & (1 << mask) else OracleResult.PASS

        oracle = InstructionOracle(evaluate)
        result = InstructionReducer().reduce(candidate, oracle)

        assert list(result.reduced.blocks) == [
            block for block in candidate.blocks if block in result.reduced.blocks
        ]
        assert_one_minimal(result.reduced, oracle)
