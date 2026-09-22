# AgentReducer

AgentReducer aims to reduce a large failing AI coding-agent task, including its
repository context, prompt, and instructions, to a smaller input that reproduces
the same failure.

**M0 implements only a deterministic reduction engine.** It provides a typed
`Oracle` protocol, explicit `PASS` / `FAIL` / `ERROR` outcomes, and a generic
`ddmin` sequence reducer. There is no real coding-agent integration, CLI, API
client, or Docker integration yet.

## Example

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

## Development

Requires Python 3.11+. In a virtual environment, install and run:

```shell
python -m pip install -e ".[test]"
python -m pytest
```

There are no runtime dependencies. `pytest` is the sole direct test dependency;
`setuptools` is used only to build the package. The `src/agentreducer` package
contains the outcome/error models, oracle protocol, and reducer; `tests` covers
the contracts, edge cases, and 1-minimality for deterministic predicates.
