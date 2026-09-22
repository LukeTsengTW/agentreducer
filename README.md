# AgentReducer

AgentReducer aims to reduce a large failing AI coding-agent task, including its
repository context, prompt, and instructions, to a smaller input that reproduces
the same failure.

**M0 implements a deterministic reduction engine.** It provides a typed
`Oracle` protocol, explicit `PASS` / `FAIL` / `ERROR` outcomes, and a generic
`ddmin` sequence reducer. **M1 adds structured prompt reduction** using that same
engine. The examples below use deterministic, synthetic oracles. There is no
real coding-agent integration, CLI, API client, or Docker integration yet.
Codex integration is planned for a later milestone; M2 is not implemented.

## Example

M0 can reduce any sequence with a caller-supplied oracle:

```python
from collections.abc import Sequence

from agentreducer import OracleResult, ddmin


class CacheAuthOracle:
    def evaluate(self, candidate: Sequence[str]) -> OracleResult:
        if "USE_CACHE" in candidate and "AUTH_MODULE" in candidate:
            return OracleResult.FAIL
        return OracleResult.PASS


items = ["A", "B", "USE_CACHE", "C", "D", "AUTH_MODULE", "E", "F"]
print(ddmin(items, CacheAuthOracle()))
# ['USE_CACHE', 'AUTH_MODULE']
```

## Behavior

- The initial candidate is evaluated first. `PASS` raises
  `InitialCandidateNotFailingError`; no reduction is attempted.
- `FAIL` means the failure of interest was reproduced. Only `FAIL` permits
  accepting a candidate.
- Any `ERROR`, including one for an empty or intermediate candidate, immediately
  raises `OracleEvaluationError`. Its `candidate` attribute is a tuple of the
  input that could not be evaluated. No partial result is returned and no ERROR
  is counted as PASS or FAIL. Exceptions raised by the oracle propagate unchanged;
  a return value other than `OracleResult` raises `TypeError`.
- Empty input is evaluated once: `FAIL` returns `[]`, `PASS` raises
  `InitialCandidateNotFailingError`, and `ERROR` raises `OracleEvaluationError`.
  After verifying any nonempty input fails, the reducer tests the empty candidate
  before partitioning. If it fails, the result is `[]`.
- Reduction starts with two balanced contiguous partitions, using boundaries
  `i * len(candidate) // granularity`. Partitions are tested left to right,
  followed by their complements in the same order. The first failing candidate
  is accepted. After accepting a partition, granularity resets to two; after
  accepting a complement, it decreases by one (to at least two). If neither
  works, granularity doubles, capped at the number of remaining items.
- With a deterministic oracle, fixed input order produces the same evaluation
  order and result. When several minimal subsets exist, this traversal chooses
  one; it does not search for all of them or guarantee a global size minimum.
- Every successful result is **1-minimal**: it fails, and removing any single
  remaining item passes. At singleton granularity all such deletions are checked;
  for a one-item result the empty candidate has already passed. The empty result
  satisfies the deletion condition vacuously. Monotonicity is not required.
- Input collections are not modified. The oracle receives tuple snapshots and
  the result is a new list, retaining original order, duplicate occurrences, and
  item identities. Items need not be hashable or comparable. Snapshots are
  shallow: oracles must not mutate the item objects or depend on changing state.

This is partition/complement delta debugging, not a powerset search. The worst
case uses a quadratic number of oracle calls. M0 has no retries, caching,
parallel evaluations, or support for flaky oracles. Reproduction and failure
identity are the caller's oracle's responsibility.

## M1: prompt reduction

```python
from agentreducer import OracleResult
from agentreducer.prompt import PromptOracle, PromptReducer, PromptSplitMode, split_prompt


def synthetic_failure(text: str) -> OracleResult:
    if "USE_CACHE" in text and "AUTH_MODULE" in text:
        return OracleResult.FAIL
    return OracleResult.PASS


prompt = split_prompt(
    "Inspect authentication.\nRead README.\nUSE_CACHE\n"
    "Do not modify unrelated files.\nAUTH_MODULE\nRun tests.",
    mode=PromptSplitMode.LINE,
)
result = PromptReducer().reduce(prompt, PromptOracle(synthetic_failure))
print(result.reduced.render())
# USE_CACHE
# AUTH_MODULE
assert result.original_block_count == 6
assert result.reduced_block_count == 2
```

`split_prompt(text, mode=PromptSplitMode.PARAGRAPH)` returns a frozen
`PromptCandidate` containing a tuple of frozen `PromptBlock(text)` objects and
the mode used for rendering. Blocks retain their order and duplicate occurrences;
the candidate is hashable. Manually constructed blocks keep their text verbatim,
including empty strings. Candidates snapshot supplied block collections as tuples.
Python's hash values are process-local; `render()` is deterministic across runs.

Segmentation treats LF, CRLF, and CR as line endings, normalizing them to LF.
A blank line is empty or contains only ASCII spaces and tabs. All characters on
nonblank lines are retained, including indentation, trailing spaces, tabs, and
Unicode whitespace. Other Unicode line-separator characters are retained as text.

- **Paragraph mode** (the default): one block per run of nonblank lines. Internal
  line breaks remain LF; rendering joins paragraphs with `\n\n`.
- **Line mode:** one block per nonblank logical line; rendering joins with `\n`.
- Leading, trailing, and repeated blank lines create no empty blocks. Rendering
  adds no terminal newline. Empty or blank-only input produces zero blocks and
  renders as `""`. These rules normalize formatting; the oracle evaluates the
  rendered candidate, which need not be byte-identical to the source text.

`PromptOracle` accepts a `Callable[[str], OracleResult]`, including an existing
text evaluator's bound `evaluate` method. It renders the candidate and forwards
the outcome unchanged. Alternatively, pass an object implementing
`PromptEvaluator.evaluate(PromptCandidate) -> OracleResult` directly to the
reducer. M0's `Oracle[T]` continues to evaluate sequences of items; a private
adapter bridges these contracts without changing the generic engine.

`PromptReducer.reduce` returns an immutable `PromptReductionResult` with
`original`, `reduced`, and `evaluations`. The original candidate is unchanged.
The reduced candidate retains its mode and is 1-minimal with respect to whole
blocks, with the same traversal and error semantics as M0. Initial PASS raises
`InitialCandidateNotFailingError`; any ERROR raises `OracleEvaluationError`,
whose `candidate` is the tuple of prompt blocks that could not be evaluated.
Oracle exceptions propagate unchanged. An initially failing empty prompt is
supported. No partial result or statistics are returned on an error.

Statistics are calculated from the stored candidates:

- `original_block_count` and `reduced_block_count` count block occurrences.
- `original_character_count` and `reduced_character_count` use Python `len()` on
  rendered text, including separators and retained whitespace, after segmentation
  normalization. These are Unicode code-point counts, not bytes or tokens.
- `reduction_percentage` is the percentage of rendered characters removed:
  `100 * (original_character_count - reduced_character_count) / original_character_count`.
  It is `0.0` when the original has zero characters, including zero-block input.
- `evaluations` counts actual oracle calls during this reduction, including
  initial and empty-candidate checks and repeated evaluations. Each reduction has
  its own counter; reading statistics or rendering does not invoke the oracle.

M1 reduces whole lines or paragraphs. It does not parse sentences, understand
Markdown/code structure, count tokens, or guarantee a globally smallest prompt.
Use the mode appropriate to the prompt; the oracle must remain deterministic.

## Development

Requires Python 3.11+. In a virtual environment, install and run:

```shell
python -m pip install -e ".[test]"
python -m pytest
```

There are no runtime dependencies. `pytest` is the sole direct test dependency;
`setuptools` is used only to build the package. The `src/agentreducer` package
contains the outcome/error models, oracle protocol, and reducer. Its `prompt`
subpackage contains M1's models, parser, oracle adapter, and reducer. `tests`
covers the contracts, edge cases, and 1-minimality for deterministic predicates.
