from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.prompt import (
    PromptBlock,
    PromptCandidate,
    PromptEvaluator,
    PromptOracle,
    PromptReducer,
    PromptSplitMode,
    split_prompt,
)


class RecordingPromptOracle:
    def __init__(self, evaluate: Callable[[PromptCandidate], OracleResult]) -> None:
        self._evaluate = evaluate
        self.calls: list[PromptCandidate] = []

    def evaluate(self, candidate: PromptCandidate) -> OracleResult:
        self.calls.append(candidate)
        return self._evaluate(candidate)


def assert_one_minimal(candidate: PromptCandidate, oracle: PromptEvaluator) -> None:
    assert oracle.evaluate(candidate) is OracleResult.FAIL
    for index in range(len(candidate.blocks)):
        deletion = PromptCandidate(
            candidate.blocks[:index] + candidate.blocks[index + 1:], candidate.mode
        )
        assert oracle.evaluate(deletion) is OracleResult.PASS, deletion


@pytest.mark.parametrize(
    ("mode", "separator"),
    [(PromptSplitMode.LINE, "\n"), (PromptSplitMode.PARAGRAPH, "\n\n")],
)
def test_two_failure_inducing_blocks_with_a_synthetic_text_oracle(
    mode: PromptSplitMode, separator: str
) -> None:
    calls: list[str] = []

    def evaluate(text: str) -> OracleResult:
        calls.append(text)
        if "USE_CACHE" in text and "AUTH_MODULE" in text:
            return OracleResult.FAIL
        return OracleResult.PASS

    source = separator.join([
        "Inspect authentication.", "Read README.", "USE_CACHE",
        "Do not modify unrelated files.", "AUTH_MODULE", "Run tests.",
    ])
    candidate = split_prompt(source, mode)
    oracle = PromptOracle(evaluate)

    result = PromptReducer().reduce(candidate, oracle)

    assert result.original is candidate
    assert candidate.render() == source
    assert result.reduced.render() == separator.join(["USE_CACHE", "AUTH_MODULE"])
    assert result.reduced.blocks[0] is candidate.blocks[2]
    assert result.reduced.blocks[1] is candidate.blocks[4]
    assert result.reduced.mode is mode
    assert calls[:2] == [candidate.render(), ""]
    assert result.evaluations == len(calls)
    assert result.original_block_count == 6
    assert result.reduced_block_count == 2
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("mode", list(PromptSplitMode))
@pytest.mark.parametrize(
    ("texts", "required"),
    [
        pytest.param(["A", "BUG", "B"], {"BUG"}, id="one-required-block"),
        pytest.param(["A", "B", "C"], {"A", "B", "C"}, id="all-blocks-required"),
        pytest.param(["BUG"], {"BUG"}, id="already-minimal-singleton"),
        pytest.param(
            [f"noise-{index}" for index in range(128)] + ["BUG", "tail"],
            {"BUG"}, id="many-irrelevant-blocks",
        ),
    ],
)
def test_reduction_with_a_structured_oracle(
    mode: PromptSplitMode, texts: list[str], required: set[str]
) -> None:
    candidate = PromptCandidate(tuple(PromptBlock(text) for text in texts), mode)
    oracle = RecordingPromptOracle(
        lambda prompt: (
            OracleResult.FAIL
            if required.issubset(block.text for block in prompt.blocks)
            else OracleResult.PASS
        )
    )

    result = PromptReducer().reduce(candidate, oracle)

    assert [block.text for block in result.reduced.blocks] == [
        text for text in texts if text in required
    ]
    assert result.evaluations == len(oracle.calls)
    assert all(call.mode is mode for call in oracle.calls)
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("mode", list(PromptSplitMode))
def test_duplicate_text_blocks_and_original_order_are_preserved(
    mode: PromptSplitMode,
) -> None:
    candidate = PromptCandidate(
        tuple(PromptBlock(text) for text in ["BUG", "noise", "BUG", "tail", "BUG"]),
        mode,
    )
    oracle = PromptOracle(
        lambda text: OracleResult.FAIL if text.count("BUG") >= 2 else OracleResult.PASS
    )

    result = PromptReducer().reduce(candidate, oracle)

    assert result.reduced.blocks == (PromptBlock("BUG"), PromptBlock("BUG"))
    positions = [
        next(index for index, original in enumerate(candidate.blocks) if original is block)
        for block in result.reduced.blocks
    ]
    assert positions == sorted(set(positions))
    assert len(candidate.blocks) == 5
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("outcome", list(OracleResult))
def test_text_adapter_renders_once_and_forwards_every_outcome(outcome: OracleResult) -> None:
    class TextEvaluator:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def evaluate(self, text: str) -> OracleResult:
            self.calls.append(text)
            return outcome

    candidate = split_prompt("  A  \n\nB\n\n  A  ")
    underlying = TextEvaluator()
    oracle: PromptEvaluator = PromptOracle(underlying.evaluate)

    assert oracle.evaluate(candidate) is outcome
    assert underlying.calls == ["  A  \n\nB\n\n  A  "]


@pytest.mark.parametrize("source", ["", "A\nB"])
def test_initial_nonfailing_prompt_is_rejected(source: str) -> None:
    candidate = split_prompt(source, PromptSplitMode.LINE)
    oracle = RecordingPromptOracle(lambda prompt: OracleResult.PASS)

    with pytest.raises(InitialCandidateNotFailingError):
        PromptReducer().reduce(candidate, oracle)

    assert oracle.calls == [candidate]


@pytest.mark.parametrize("mode", list(PromptSplitMode))
@pytest.mark.parametrize(
    ("texts", "percentage", "evaluations"),
    [
        pytest.param((), 0.0, 1, id="zero-blocks-and-characters"),
        pytest.param(("",), 0.0, 2, id="one-block-zero-characters"),
        pytest.param(("some text",), 100.0, 2, id="nonempty-to-empty"),
    ],
)
def test_failing_empty_prompt_and_zero_length_statistics(
    mode: PromptSplitMode, texts: tuple[str, ...], percentage: float, evaluations: int
) -> None:
    candidate = PromptCandidate(tuple(PromptBlock(text) for text in texts), mode)
    oracle = RecordingPromptOracle(lambda prompt: OracleResult.FAIL)

    result = PromptReducer().reduce(candidate, oracle)

    assert result.original_block_count == len(texts)
    assert result.reduced_block_count == 0
    assert result.original_character_count == len(candidate.render())
    assert result.reduced_character_count == 0
    assert result.reduction_percentage == percentage
    assert result.evaluations == evaluations == len(oracle.calls)
    assert result.reduced == PromptCandidate((), mode)
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("mode", list(PromptSplitMode))
@pytest.mark.parametrize(
    "error_texts",
    [("A", "B", "C", "D"), (), ("A", "B"), ("B", "C", "D"), ("B", "D")],
    ids=["initial", "empty", "partition", "complement", "after-reduction"],
)
def test_oracle_error_aborts_at_every_reduction_stage(
    mode: PromptSplitMode, error_texts: tuple[str, ...]
) -> None:
    def evaluate(prompt: PromptCandidate) -> OracleResult:
        texts = tuple(block.text for block in prompt.blocks)
        if texts == error_texts:
            return OracleResult.ERROR
        if "B" in texts and "D" in texts:
            return OracleResult.FAIL
        return OracleResult.PASS

    candidate = PromptCandidate(tuple(PromptBlock(text) for text in "ABCD"), mode)
    oracle = RecordingPromptOracle(evaluate)

    with pytest.raises(OracleEvaluationError) as caught:
        PromptReducer().reduce(candidate, oracle)

    assert caught.value.candidate == tuple(PromptBlock(text) for text in error_texts)
    assert oracle.calls[-1].blocks == caught.value.candidate
    assert sum(call.blocks == caught.value.candidate for call in oracle.calls) == 1


def test_empty_initial_error_is_not_treated_as_a_valid_empty_result() -> None:
    oracle = PromptOracle(lambda text: OracleResult.ERROR)

    with pytest.raises(OracleEvaluationError) as caught:
        PromptReducer().reduce(split_prompt(""), oracle)

    assert caught.value.candidate == ()


def test_exceptions_from_text_evaluator_propagate_unchanged() -> None:
    failure = OSError("Synthetic evaluation unavailable")
    calls: list[str] = []

    def evaluate(text: str) -> OracleResult:
        calls.append(text)
        if text == "A\nB":
            raise failure
        return OracleResult.FAIL if text == "A\nB\nC\nD" else OracleResult.PASS

    with pytest.raises(OSError) as caught:
        PromptReducer().reduce(
            split_prompt("A\nB\nC\nD", PromptSplitMode.LINE), PromptOracle(evaluate)
        )

    assert caught.value is failure
    assert calls == ["A\nB\nC\nD", "", "A\nB"]


@pytest.mark.parametrize("invalid", [None, True, "FAIL"])
def test_invalid_text_oracle_outcomes_are_rejected(invalid: object) -> None:
    oracle = PromptOracle(lambda text: cast(OracleResult, invalid))

    with pytest.raises(TypeError, match="must return an OracleResult"):
        PromptReducer().reduce(split_prompt("A"), oracle)


def test_multiple_minima_and_counts_are_deterministic_across_reused_reducer() -> None:
    def evaluate(prompt: PromptCandidate) -> OracleResult:
        texts = {block.text for block in prompt.blocks}
        return (
            OracleResult.FAIL
            if {"A", "B"}.issubset(texts) or {"C", "D"}.issubset(texts)
            else OracleResult.PASS
        )

    reducer = PromptReducer()
    candidate = split_prompt("A\nB\nC\nD", PromptSplitMode.LINE)
    oracle = RecordingPromptOracle(evaluate)
    first = reducer.reduce(candidate, oracle)
    first_trace = oracle.calls.copy()
    second = reducer.reduce(candidate, oracle)

    assert first == second
    assert first.reduced.render() == "A\nB"
    assert first.evaluations == second.evaluations == len(first_trace)
    assert first_trace == oracle.calls[len(first_trace):]
    assert_one_minimal(first.reduced, oracle)
    assert_one_minimal(second.reduced, oracle)


@pytest.mark.parametrize(
    ("mode", "original_characters"),
    [(PromptSplitMode.LINE, 11), (PromptSplitMode.PARAGRAPH, 13)],
)
def test_statistics_include_separators_and_do_not_call_the_oracle(
    mode: PromptSplitMode, original_characters: int
) -> None:
    candidate = PromptCandidate((PromptBlock("abc"), PromptBlock("KEEP"), PromptBlock("xy")), mode)
    oracle = RecordingPromptOracle(
        lambda prompt: OracleResult.FAIL if "KEEP" in prompt.render() else OracleResult.PASS
    )
    result = PromptReducer().reduce(candidate, oracle)
    evaluations = len(oracle.calls)

    assert result.original_block_count == 3
    assert result.reduced_block_count == 1
    assert result.original_character_count == original_characters
    assert result.reduced_character_count == 4
    assert result.reduction_percentage == pytest.approx(
        100 * (original_characters - 4) / original_characters
    )
    assert result.evaluations == evaluations
    assert result.reduced.render() == "KEEP"
    assert len(oracle.calls) == evaluations
    with pytest.raises(FrozenInstanceError):
        setattr(result, "evaluations", 0)
    assert_one_minimal(result.reduced, oracle)
    assert result.evaluations == evaluations  # Later validation is outside this run.


def test_statistics_count_normalized_unicode_text_not_bytes_or_raw_source() -> None:
    source = "\r\n  嗨😀 \r\nX\r\n"
    candidate = split_prompt(source, PromptSplitMode.LINE)
    oracle = PromptOracle(
        lambda text: OracleResult.FAIL if "嗨😀" in text else OracleResult.PASS
    )

    result = PromptReducer().reduce(candidate, oracle)

    assert candidate.render() == "  嗨😀 \nX"
    assert result.original_character_count == 7
    assert result.reduced_character_count == 5
    assert result.reduction_percentage == pytest.approx(200 / 7)
    assert_one_minimal(result.reduced, oracle)


def test_unreduced_prompt_has_zero_percent_reduction_and_exact_call_count() -> None:
    candidate = split_prompt("KEEP", PromptSplitMode.LINE)
    oracle = RecordingPromptOracle(
        lambda prompt: OracleResult.FAIL if prompt.blocks else OracleResult.PASS
    )

    result = PromptReducer().reduce(candidate, oracle)

    assert result.original == result.reduced
    assert result.reduction_percentage == 0.0
    assert result.evaluations == 2
    assert oracle.calls == [candidate, PromptCandidate((), PromptSplitMode.LINE)]
    assert_one_minimal(result.reduced, oracle)


@pytest.mark.parametrize("mode", list(PromptSplitMode))
def test_one_minimality_for_all_three_block_boolean_oracles(mode: PromptSplitMode) -> None:
    candidate = PromptCandidate(tuple(PromptBlock(text) for text in "ABC"), mode)
    bits = {"A": 1, "B": 2, "C": 4}
    # All 128 truth tables with a failing full prompt, including non-monotonic ones.
    for other_outcomes in range(128):
        table = other_outcomes | (1 << 7)

        def evaluate(prompt: PromptCandidate, outcomes: int = table) -> OracleResult:
            mask = sum(bits[block.text] for block in prompt.blocks)
            return OracleResult.FAIL if outcomes & (1 << mask) else OracleResult.PASS

        oracle = RecordingPromptOracle(evaluate)
        result = PromptReducer().reduce(candidate, oracle)

        assert result.evaluations == len(oracle.calls)
        assert result.reduced.mode is mode
        assert list(result.reduced.blocks) == [
            block for block in candidate.blocks if block in result.reduced.blocks
        ]
        assert_one_minimal(result.reduced, oracle)
