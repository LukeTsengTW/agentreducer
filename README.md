# AgentReducer

## v0.1.0 CLI and offline usage

The current implementation includes prompt, instruction and repository
file-presence reduction, explicit command/Codex oracles, finite-horizon repeated
trials, private-safe reports and atomic opt-in exports. Python >=3.11 is required
and runtime dependencies are empty. Install with `python -m pip install -e .`,
then run `python examples/minimal_failure/run_demo.py --format json` for an
independent local-Python-only demonstration. `agentreducer --help` and
`python -m agentreducer --help` share the same entry point.

See [CLI usage](docs/CLI.md), [report schema](docs/REPORTS.md),
[execution/security boundaries](docs/SECURITY.md),
[offline demo](examples/minimal_failure/README.md) and
[prepared release notes](docs/releases/v0.1.0.md). Native CONTROLLED remains
unavailable; AMBIENT is conditional on stable uncontrolled context. Trial and
agent process budgets do not establish a backend model request bound. Global
agent-readable filesystem isolation is NOT PROVEN.

M7 offline implementation, required review and final engineering gates are complete.
Licensed under [MIT](LICENSE), copyright 2026 Luke Tseng.

AgentReducer v0.1.0 is release-ready under an explicit human-approved v0.1-only
waiver of successful live Codex validation. Three real AMBIENT validation
attempts failed to modify the smoke fixture; root cause remains unresolved.
Two trials used gpt-6-luna; one used gpt-6.1-sol plus a changed native executable.
Offline Codex composition is validated; successful live Codex behavior has not
been demonstrated. `LIVE_VALIDATION` remains `FAILED`. The supervisor accepted
the agreed development/readiness scope as `PROJECT_COMPLETE`.
Download the wheel or source archive from the
[v0.1.0 GitHub Release](https://github.com/LukeTsengTW/agentreducer/releases/tag/v0.1.0).

The milestone introduction and examples below are preserved as
historical material; this section and the linked CLI guide describe current
capabilities.


AgentReducer aims to reduce a large failing AI coding-agent task, including its
repository context, prompt, and instructions, to a smaller input that reproduces
the same failure.

**M0 implements a deterministic reduction engine.** It provides a typed
`Oracle` protocol, explicit `PASS` / `FAIL` / `ERROR` outcomes, and a generic
`ddmin` sequence reducer. **M1 adds structured prompt reduction** and **M2 adds
structured Markdown agent-instruction reduction**, using that same engine.
**M3 adds deterministic command-based failure evaluation inside fresh disposable
copies of a workspace.** The M0–M2 examples use synthetic evaluators; M3 can run
local checker commands. **M4 adds a generic agent→verifier oracle and native
Codex CLI adapter, verified with offline fake processes.** Native CONTROLLED
isolation remains unavailable/fail-closed; explicit AMBIENT results carry a
conditional context qualification. Live Codex smoke has not been executed.
There is no end-user CLI,
API client, Docker integration, stochastic repeated trials, repository context
reduction, or general repository-level `AGENTS.md` discovery yet.

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

## M2: agent instruction reduction

M2 accepts the **content of one document**. It does not open files, discover
`AGENTS.md`, walk repositories, or implement parent/child instruction inheritance.

```python
from agentreducer import OracleResult
from agentreducer.instructions import InstructionOracle, InstructionReducer, parse_instructions


def synthetic_instruction_failure(text: str) -> OracleResult:
    if "Prefer functional programming." in text and "Use the existing cache." in text:
        return OracleResult.FAIL
    return OracleResult.PASS


instructions = parse_instructions("""# Agent Rules

General project instructions.

## Development
- Inspect existing utilities.
- Prefer functional programming.
- Use the existing cache.
- Do not modify unrelated files.

## Testing
- Run tests before finishing.
""")
result = InstructionReducer().reduce(
    instructions, InstructionOracle(synthetic_instruction_failure)
)
print(result.reduced.render())
assert [block.text for block in result.reduced.blocks] == [
    "- Prefer functional programming.", "- Use the existing cache."
]
assert "## Testing" not in result.reduced.render()
```

The result retains `# Agent Rules` and `## Development`, followed by the two
required rules. The renderer uses one blank line between headings and blocks,
including between consecutive list items (a loose Markdown list).

`InstructionCandidate` stores a tuple of frozen `InstructionBlock` objects. Each
block contains its text, an `InstructionBlockKind`, and a tuple of
`InstructionHeading` objects. Headings hold a level, title, and parse-local
occurrence number; two separate sections with the same title stay distinct.
These models are immutable and hashable. Candidate and heading-path collections
are snapshotted as tuples; ordering and duplicate occurrences are preserved.

Parsing rules are intentionally smaller than CommonMark:

- **Headings:** ATX levels 1–6, with zero to three leading spaces and ASCII spaces,
  tabs, or end-of-line after the opening hashes. Heading levels define the hierarchy;
  skipped levels are retained. Surrounding ASCII spaces/tabs and optional closing
  hashes are normalized. Headings are context, never reducible instruction units.
- **Prose:** contiguous nonblank lines form one block, ending before a recognized
  heading, list item, or fence. Text before the first heading has an empty path.
- **List items:** `-`, `*`, `+`, or 1–9 ASCII digits followed by `.` or `)`, with
  ASCII spaces/tabs or end-of-line after the marker. Top-level markers may have zero to
  three leading spaces. Original markers, numbers, and text are preserved;
  ordered lists are not renumbered when items disappear.
- **Nested content:** more-indented lines belong to their current top-level item,
  including nested lists, headings, and paragraphs. Blank lines are retained
  inside an item when followed by more-indented content. Unindented plain text
  without a blank line is a lazy continuation; a same-or-less-indented recognized
  heading, list marker, fence, or thematic break ends the item. Indentation tabs
  count at four-column stops for this classification; their text is unchanged.
- **Fences:** runs of at least three backticks or tildes are supported. A closer
  uses the same character, at least the opening run length, and only trailing
  spaces/tabs. Standalone opening and closing fences allow up to three leading
  spaces. Backtick info strings cannot contain backticks. Markdown-looking body
  text is opaque, including blank lines. A fence inside an item, or immediately
  after its marker (including nested markers), stays in that atomic item. For
  contained fences, closing-delimiter indentation is ignored. An unterminated
  fence conservatively consumes through EOF; no closing delimiter is invented.
- **Whitespace:** CRLF and CR become LF. Outside atomic content, repeated blank
  lines (empty or ASCII spaces/tabs only) create no blocks. Whitespace on content
  lines and blank lines inside fences or nested content are retained. Other
  Unicode whitespace is retained as text. Rendering adds no terminal newline.

Rendering emits only the heading paths needed by retained blocks. Required
ancestors stay, empty sections disappear, and instruction order is unchanged.
When a block starts with one to three ASCII spaces, rendering removes that
container margin, and up to the same number of leading spaces from each following
line. This prevents an independent retained block from becoming nested under a
preceding list after reduction. Original `block.text` is unchanged; relative
nested indentation is preserved for space-indented content. Tabs remain verbatim;
mixed space/tab container alignment is outside the supported round-trip subset.
Empty and heading-only documents have zero instruction blocks and render as
`""`; their structural headings are intentionally omitted. Heading occurrence
numbers can change after rendering and reparsing; block content (after the
documented margin normalization), kinds, hierarchy, and distinct section
boundaries are preserved for the supported syntax.
Manually constructed candidates must put unsectioned text before sectioned text;
the renderer rejects an order that cannot preserve that scope without inventing
a heading. Returning to a parent path re-emits the parent heading.

`InstructionOracle` renders candidates and calls a `Callable[[str], OracleResult]`.
Objects implementing `InstructionEvaluator.evaluate(InstructionCandidate)` may
also be supplied directly. `InstructionReducer` passes the blocks to M0's `ddmin`
and returns an immutable `InstructionReductionResult`. Headings are reconstructed
for every oracle call; they are never tested as independent removable blocks.
The result is block-level 1-minimal, not necessarily globally smallest. Initial
PASS, ERROR, raised exceptions, and invalid outcomes have M0's existing behavior.
ERROR aborts with no partial result; its exception contains the candidate blocks.
Failing empty candidates are supported.

The result exposes `original`, `reduced`, and exact `evaluations`, plus calculated
`original_block_count`, `reduced_block_count`, `original_character_count`, and
`reduced_character_count`. Character counts use `len(render())`, including
required headings and normalized separators. `block_reduction_percentage` and
`character_reduction_percentage` each use `100 * (original - reduced) / original`,
and each returns `0.0` when its own denominator is zero. M1 and M2 share a private
per-run counting wrapper: initial checks, empty checks, and repeated evaluations
all count; rendering and reading statistics make no additional oracle calls.

Blockquotes, tables, HTML, setext headings, and indented code are not interpreted
as special containers; their text is retained as ordinary prose, while recognized
ATX/list/fence boundaries still apply. The parser does not implement arbitrary
CommonMark edge cases, Markdown reference resolution, sentence splitting, or
recursive reduction inside a list item or fence. Malformed input is preserved
conservatively rather than repaired; valid Markdown output is not promised for
malformed input. M2 itself does not count tokens, execute subprocesses, or
integrate agents; M3 provides command evaluation through a separate package.

## M3: command oracle and isolated workspace copies

This example uses a real local Python checker with the unchanged prompt reducer:

```python
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from agentreducer.execution import CommandOracle, CommandSpec, TextFileMaterializer, WorkspaceTemplate
from agentreducer.prompt import PromptReducer, PromptSplitMode, split_prompt


with TemporaryDirectory() as directory:
    source = Path(directory)
    (source / "check.py").write_text(
        "from pathlib import Path\n"
        "text = Path('prompt.txt').read_text(encoding='utf-8')\n"
        "raise SystemExit(1 if 'USE_CACHE' in text and 'AUTH_MODULE' in text else 0)\n",
        encoding="utf-8",
    )
    oracle = CommandOracle(
        workspace=WorkspaceTemplate(source),
        command=CommandSpec((sys.executable, "check.py"), timeout_seconds=5.0),
        materializer=TextFileMaterializer("prompt.txt", lambda candidate: candidate.render()),
    )
    prompt = split_prompt(
        "Inspect authentication.\nUSE_CACHE\nRead README.\nAUTH_MODULE\nRun tests.",
        PromptSplitMode.LINE,
    )
    result = PromptReducer().reduce(prompt, oracle)
    assert result.reduced.render() == "USE_CACHE\nAUTH_MODULE"
    assert not (source / "prompt.txt").exists()
    print(result.reduced.render())
```

The same generic `CommandOracle` works with `InstructionReducer`: materialize
`candidate.render()` into `AGENTS.md` and configure a command that checks those
instructions. The execution package does not import either candidate domain.

### Execution and outcomes

`CommandSpec` snapshots argv and optional `env` overrides, with a positive finite
`timeout_seconds` (default 60). It rejects command strings. `run_command(spec,
cwd=path)` runs one argv-based process with a copied child environment,
`shell=False`, closed stdin, and captured stdout/stderr; it does not print output
or classify exit codes. Use an absolute executable path such as `sys.executable`
for predictable behavior across platforms. On Windows, `cwd` and child `env`
overrides do not control executable lookup the same way they do on POSIX.
Direct `.bat`/`.cmd` executables, including uppercase suffixes, are rejected on
Windows because the OS can invoke a shell for them. No shell fallback is provided.
See the [Python subprocess documentation](https://docs.python.org/3.11/library/subprocess.html#security-considerations).

`CommandOracle.evaluate(candidate)` returns the existing `OracleResult`:

- Default exit code 0 is PASS; every other integer exit code is FAIL.
- `ExitCodePolicy(pass_exit_codes={0}, fail_exit_codes={1})` uses explicit disjoint
  sets; any other exit code is ERROR. The sets are immutable snapshots.
- Missing executables, invalid sources, copy/materialization/launch errors,
  timeouts, policy errors, and cleanup errors are ERROR. They never count as a
  reproduced test failure. Configuration errors raise when configuration is built.
- Cancellation exceptions such as `KeyboardInterrupt` propagate after workspace
  cleanup is attempted. If cleanup also fails, the original cancellation is
  preserved with a cleanup-error exception note. M0's initial-PASS and ERROR-abort
  contracts remain unchanged.

`evaluate_detailed(candidate)` performs one evaluation and returns a frozen
`CommandEvaluation(outcome, command, error)`. Its optional `CommandResult` includes
argv, exit code, stdout, stderr, monotonic duration, and a timeout flag. Launch or
setup failure can have no command result; later errors retain collected results.
Calling `evaluate()` and then `evaluate_detailed()` performs two separate trials.
Neither rendering nor reading diagnostics runs another process.

Output is decoded as UTF-8 with invalid bytes replaced. Timeout diagnostics retain
available partial output as strings with normalized line endings; absent output
becomes an empty string. A timeout stops and reaps the direct child. **Terminating
all descendant processes is outside M3.** Output is buffered in memory, so callers
should use commands with bounded output.

### Workspace and materialization contracts

`WorkspaceTemplate(source).create()` validates the source at use time and yields
a fresh `Workspace.path` in a temporary directory. It cleans up after normal or
exceptional exits. Source must be a directory and remain stable throughout a
reduction; copying is not a transactional snapshot of concurrent source edits.
Temporary destinations inside the source tree are rejected to prevent recursive
self-copy. No command is run in the original source directory.

`DEFAULT_EXCLUSIONS` contains `.git`, `.venv`, `__pycache__`, `.pytest_cache`,
`.mypy_cache`, `.ruff_cache`, and `node_modules`. Exclusions match entry basenames
at every depth, for files and directories. Passing `exclusions` replaces the
defaults; use `DEFAULT_EXCLUSIONS | {"another-entry"}` to extend them. `.gitignore`
is not interpreted. Other dotfiles, `.env.example`, `.github`, and `AGENTS.md`
are retained. Nonexcluded symbolic links and Windows reparse points, including
junctions, are rejected rather than followed; excluded entries are not traversed.

`CandidateMaterializer[T]` defines `materialize(candidate, workspace) -> None`.
The built-in `TextFileMaterializer(relative_path, render)` writes UTF-8/LF text,
creates necessary parents, and atomically replaces the target from a staging file
in the same directory. It cleans staging files after failure; overwriting a hard
link does not edit its other names. Both slash styles are supported, but absolute,
drive/UNC/rooted, traversal, stream, Windows device/alias, and existing
link/reparse paths are rejected. Containment is checked with resolved path
components. Custom materializers are trusted code and must obey the same
workspace-only write contract.

### Security and limits

**Commands execute locally with the current user's permissions. A temporary
workspace provides repeatability and source protection; it is not a security
sandbox.** Malicious commands can still access network, user files, credentials,
and other resources available to the process, including the original source via
an explicit path. Only run trusted commands and materializers with trusted,
stable source trees. Path checks are not a defense against a malicious process
concurrently altering the workspace.

M3 has zero runtime dependencies and uses only the Python standard library. It
does not integrate Codex or another coding agent, retry stochastic failures,
reduce repository context, provide Docker isolation, or manage process trees.

## M4: qualified agent execution

`AgentExecutionOracle(workspace, agent, verifier, render_prompt, materializer=None)`
creates one fresh project copy per evaluation. It optionally materializes the
candidate, renders a task, runs `agent.run(workspace=path, prompt=text)` once,
then runs the M3 verifier in that **same actual directory** only after agent
success. The verifier sees the agent's edits. Only verifier exit policy determines
PASS/FAIL. Agent nonzero exit, timeout, launch/result/context errors and workspace,
rendering, materialization, policy or cleanup failures are ERROR. Cancellation
propagates after cleanup, retaining the original cancellation object.

`evaluate_detailed` returns frozen `AgentEvaluation` with separate agent and
verifier diagnostics, immutable context qualification and an error stage.
Earlier completed results survive later failures, including cleanup errors.
Calling `evaluate` and `evaluate_detailed` performs two trials. The generic runner
protocol requires `context` and `run`; it does not create workspaces or classify
test failures. Runners with private diagnostic values may provide an optional
`sanitize_diagnostic(text)` hook for later oracle errors, without exposing env maps.

`reduce_prompt` and `reduce_instructions` delegate to the existing reducer once
and return `AgentReductionResult(reduction, context)`. The nested result and its
statistics are unchanged. Metadata reads add zero evaluations. Initial PASS and
intermediate ERROR retain their existing exceptions and return no partial wrapper.

### Assurance and context modes

| Evidence | Availability | Meaning |
| --- | --- | --- |
| Native CONTROLLED | Unavailable; zero task launches | Default mode fails closed; no verified native isolation provider exists in M4 |
| Native AMBIENT | Explicit opt-in adapter path, offline tested | Context is not controlled or frozen; correctness is conditional on ambient context remaining stable during reduction |
| Offline fake CONTROLLED | Verified fixture contract | Owned environment and orchestration tests; never native Codex proof |
| Live Codex smoke | Not executed; human authorization pending | No model/account/authentication compatibility claim |

Caller-built evidence, a supplied API key, fake help output or successful preflight
cannot upgrade native CONTROLLED. No automatic AMBIENT fallback exists.
`--ignore-user-config` concerns user configuration, and is not evidence that global
AGENTS, skills, plugins, hooks or managed context are absent. Native preflight
reports these sources as unknown. AMBIENT qualification also survives setup and
execution errors. Stable context alone does not make a live agent deterministic:
M4 takes one trial per candidate; 1-minimality requires a deterministic oracle.
Repeated-trial handling belongs to a later milestone.

`ControlledEnvironment` is an auxiliary tool for trusted runners. It creates
empty owned homes outside the disposable project, filters inherited environment,
applies protected HOME/USERPROFILE/CODEX_HOME/platform roots last, and bounds the
project root with an owned marker. Undeclared `.codex`, `.agents`, instruction and
override sources are rejected. Declared relative `AGENTS.md` files stay in the
project; an override file cannot shadow them. No credential file is read, copied
or linked; credential/config-selector overrides are rejected. Auxiliary homes
are cleaned on success, failure and cancellation. This tool is not a native
isolation provider or a security sandbox.

### Codex configuration and invocation

This Python example only creates configuration and defines a preparation
function. Executing the example performs **no native discovery, preflight or
model request**. Call the function and evaluate its oracle only after separately
authorizing live use and selecting a trusted, stable source tree.

```python
from dataclasses import replace

from agentreducer.agents import (
    AgentExecutionOracle, CodexContextMode, CodexRunner, CodexSpec,
)
from agentreducer.execution import WorkspaceTemplate


safe_default = CodexSpec(model="your-model")
assert safe_default.context_mode is CodexContextMode.CONTROLLED
ambient = replace(safe_default, context_mode=CodexContextMode.AMBIENT)
assert ambient.context_mode is CodexContextMode.AMBIENT


def prepare_authorized_live_oracle(source, native_prefix, verifier):
    runner = CodexRunner(replace(ambient, argv_prefix=tuple(native_prefix)))
    return AgentExecutionOracle(
        WorkspaceTemplate(source), runner, verifier,
        render_prompt=lambda candidate: candidate.render(),
    )
```

`CodexSpec` requires a caller-selected model, snapshots env/prefix/secret-name
collections and uses a finite timeout (default 120 seconds). Environment maps
are hidden from repr. `CodexRunner` resolves its trusted native executable once
at preparation. An explicit prefix begins with an absolute executable and may
contain trusted wrapper arguments. Windows default discovery prefers `codex.exe`;
batch-only installations receive an error. `.cmd`/`.bat` and known shell launchers
are rejected both before and after path resolution; there is no shell/npm fallback
or binary installation.

The fixed invocation is `--ask-for-approval never exec --model MODEL --sandbox
workspace-write --skip-git-repo-check --ephemeral --ignore-user-config --color
never -`. The prompt goes only to stdin as exact UTF-8 bytes, preserving CRLF and
Unicode. It is not placed in argv, env or an adapter-created prompt file.
Captured output uses UTF-8 replacement; available timeout output is retained.
Exit zero means agent execution success, regardless of its prose. It does not
mean the test passed. Each run starts one process with `shell=False`, without
retry, model/provider fallback or automatic preflight.

`runner.preflight(cwd=path)` explicitly performs bounded `--version`, `--help`
and `exec --help` probes with closed stdin. Its immutable report identifies the
resolved invocation/version and required flags. It makes no model request,
performs no login and does not prove account/model access or controlled isolation.

Known nonempty authentication env values (inherited and stage overrides) are
redacted from exposed diagnostics, argv and generated cleanup notes, longest
first, including equivalents produced by the adapter's newline normalization.
Extra secret env names are configurable. Process input/env and raw buffers
are not rewritten before execution; agent-only overrides are not forwarded to
the verifier. This is bounded exact-value redaction, not general secret detection:
transformed values, unknown secrets and file-backed credentials are outside it.
No credential files are scanned to extend the mask. Existing Codex authentication
may be reused only by separately authorized AMBIENT live execution; the library
does not log in, move auth files or implement a controlled authentication bridge.

The developer artifact `smoke/codex_smoke.py` is outside normal pytest discovery.
Its `--help` and missing-`--live` paths stop before native discovery or preflight.
An explicitly authorized live invocation first requires one successful model-free
capability preflight, then attempts one tiny fix and one verifier,
checks changed bytes/behavior and original-source integrity, and confirms cleanup.
The live path remains **NOT EXECUTED; human authorization pending**.

M3's trusted-code, stable-source, bounded-output and direct-child process limits
also apply here. Workspace copies are not an OS sandbox. Descendant-process
termination, Linux/macOS runtime verification, native skill/plugin/hook isolation,
stochastic robustness and release readiness are not established by offline tests.

## Empirical repeated trials

`agentreducer.stochastic` repeats a trusted single-trial oracle with a required
finite session budget. It adds no model/provider, retry, cache or runtime
dependency. Each requested sample calls its source afresh. A caller-supplied
caching oracle must bypass its cache; the library cannot inspect hidden caches.

```python
from agentreducer import OracleResult
from agentreducer.stochastic import RepeatedOracle, StochasticPolicy


class ScriptedOracle:
    def __init__(self):
        self.outcomes = iter([
            OracleResult.FAIL, OracleResult.FAIL, OracleResult.PASS,
            OracleResult.FAIL, OracleResult.FAIL,
        ])

    def evaluate(self, candidate):
        return next(self.outcomes)


session = RepeatedOracle(ScriptedOracle(), StochasticPolicy(5, 5, "0.8"),
                         max_total_trials=5)
detail = session.evaluate_detailed("example")
assert detail.outcome is OracleResult.FAIL
assert detail.trials_executed == 5
assert detail.observed_failure_rate.numerator == 4
assert session.budget.remaining == 0
```

The frozen policy accepts positive integer `min_trials <= max_trials` and an
exact threshold in `(0,1]`. Decimal/ratio strings, `Fraction`, finite `Decimal`,
integer1 and finite floats are supported; float0.8 normalizes from its decimal
string to4/5. Bool, invalid/nonfinite values and unlimited budgets are rejected.

For horizon `N=max_trials` and threshold `a/b`, failure requires
`K=(a*N+b-1)//b` FAIL observations. At or after the minimum, stop early only
when `F>=K` (FAIL) or `F+N-n<K` (PASS). Every hypothetical remaining PASS/FAIL
completion would then produce the same full-horizon decision. The last normal
trial always uses `FULL_HORIZON`; early stop reasons apply only before `N`.
`min_trials=max_trials` selects fixed-count mode.

`observed_failure_rate` separately reports `Fraction(F,F+P)`, or `None` with no
valid observations. Its denominator can differ from the decision horizon after
early stopping. This is empirical evidence, without a confidence interval,
true-probability estimate, iid or unbiasedness guarantee. Correlation, shared
caches, backend/account changes, external effects and ambient context can change
observations. Unexecuted future ERRORs are unknown.

The first underlying ERROR aborts immediately, even before the minimum. It is
never PASS/FAIL and never enters the empirical denominator. Raised exceptions,
invalid results and cancellation propagate; a charged trial is never refunded.
If the remaining session budget is below the minimum, the next candidate is
ERROR with zero trials. Exhaustion during an undecided candidate is also ERROR,
with partial observations retained. A budget ERROR adds no underlying ERROR
trial. The hard budget counts attempted source calls, not dollars, tokens or
opaque source-internal/backend requests. One session rejects concurrent and
reentrant evaluation. Start a new explicit session for a new budget.

Detailed results, records and snapshots are frozen. `snapshot()` distinguishes
logical candidate classifications, charged underlying attempts, observed raw
PASS/FAIL/ERROR, committed observations, unreturned attempts and aborted
classifications. If raw PASS returns but summary processing fails, accounting
is attempt1/observedPASS1/committedPASS0/postprocessingFailure1, with propagation;
it is not an ERROR trial. `last_evaluation` clears at candidate entry and stays
`None` after propagation. Metadata reads draw zero samples; calling both
`evaluate` and `evaluate_detailed` performs two independent classifications.

The session keeps aggregate counts and at most its last bounded result. Default
records retain enum/stage/counts only. Trusted custom summaries must already be
sanitized and fit2048 characters, references256, stages128. No raw detailed
result, environment or candidate history is retained in result records.

### Compose with the existing reducers

This example uses an in-memory predicate and consumes no model usage:

```python
from agentreducer import OracleResult
from agentreducer.prompt import PromptSplitMode, split_prompt
from agentreducer.stochastic import (
    RepeatedOracle, StochasticPolicy, reduce_prompt_stochastic,
)


class ContainsBoth:
    def evaluate(self, candidate):
        text = candidate.render()
        return (OracleResult.FAIL if "USE_CACHE" in text and "AUTH_MODULE" in text
                else OracleResult.PASS)


candidate = split_prompt("noise\nUSE_CACHE\nAUTH_MODULE", PromptSplitMode.LINE)
session = RepeatedOracle(ContainsBoth(), StochasticPolicy(2, 5, "0.8"),
                         max_total_trials=200)
qualified = reduce_prompt_stochastic(candidate, session)
assert qualified.reduction.reduced.render() == "USE_CACHE\nAUTH_MODULE"
assert qualified.reduction.evaluations == qualified.counters.logical_candidate_evaluations
assert qualified.budget.used == qualified.counters.underlying_trial_attempts
```

`reduce_instructions_stochastic` similarly preserves M2 headings and statistics.
Each wrapper requires a fresh session, delegates once to the unchanged reducer,
and adds no validation trials. Existing initial-PASS/ERROR exceptions are
preserved; failures return no partial reduction wrapper. The caller can inspect
its session afterward. Stochastic 1-minimality means the retained candidate was
observed FAIL and each one-block deletion was observed PASS under this policy
during that reduction. It is relative to those observations, without a guarantee
about resampling, deterministic failure or a global minimum. Offline tests check
actual in-run witnesses, including duplicate block occurrences.

For an existing M3 command oracle, explicitly use `CommandTrials`; for an M4
agent oracle, use `AgentTrials` with `RepeatedOracle.from_trials`. Each adapter
calls the underlying `evaluate_detailed` exactly once per trial. The core creates
no workspace: every M4 call still creates, materializes, runs, verifies and
cleans its own fresh M3 copy. Repeating only a verifier in a previously modified
workspace is outside the fresh-trial contract.

Agent context evidence propagates unchanged. AMBIENT remains uncontrolled and
unfrozen, with stability required across all repeated trials and the entire
reduction. Fake controlled evidence stays fake. Native CONTROLLED's first
fail-closed ERROR aborts repetition; sampling cannot upgrade it to proof.

Process counts are separate from trial calls. Present M4 command records can
prove a launch; ambiguous absent records remain `None`, with known lower bounds.
Unknown contributions keep aggregate counts unknown. An optional trusted
observation-only `launch_observer` returns monotonic exact `LaunchCounts` before
and after each call; it must perform no subprocesses or oracle evaluations.
Contradictory evidence aborts. The offline integrations use child-written
counters to prove exact agent/verifier costs, including zero-launch errors.
`AgentTrials(include_summary=True)` permits bounded already-redacted M4
diagnostics. `CommandTrials` does not copy potentially unsanitized M3 output.

This milestone adds no live smoke or stochastic model experiment. M4's genuine
one-agent smoke remains pending direct human authorization. File-set/repository
context reduction, confidence procedures, provider isolation and process-tree
termination are outside this layer.

## Repository workspace file-presence reduction

M6 reduces managed regular-file presence in an initial disposable workspace.
Retained source bytes stay unchanged; explicit pinned infrastructure remains
outside ddmin. This local example uses a Python checker and creates its own tiny
source tree:

```python
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from agentreducer.execution import CommandOracle, CommandSpec, ExitCodePolicy
from agentreducer.repository import (
    build_repository_manifest, RepositoryProjectionMaterializer, RepositoryReducer,
    ExternalReadIsolation,
)

with TemporaryDirectory(prefix="agentreducer-repository-example-") as directory:
    source = Path(directory) / "source"
    source.mkdir()
    for name in ("auth.py", "cache.py", "noise.txt"):
        (source / name).write_bytes(b"original")
    (source / "checker.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(1 if Path('auth.py').exists() and "
        "Path('cache.py').exists() else 0)\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(source, pinned_paths=("checker.py",))
    projection = RepositoryProjectionMaterializer(
        manifest, verifier_paths=("checker.py",)
    )
    oracle = CommandOracle(
        manifest.workspace_template(),
        CommandSpec((sys.executable, "checker.py"), timeout_seconds=5),
        projection, ExitCodePolicy({0}, {1}),
    )
    result = RepositoryReducer().reduce(
        manifest.candidate(), oracle, projection=projection
    )
    assert result.reduced.files == ("auth.py", "cache.py")
    assert result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED
    assert (source / "noise.txt").read_bytes() == b"original"
    print(result.reduced.files, result.evaluations)
```

The manifest includes ordinary dotfiles and opaque binary files, hashes raw
bytes, and rejects included links/reparse points and nonportable path aliases.
Default exclusions match M3 at every depth: `.git`, `.venv`,
`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` and `node_modules`.
Custom exclusions replace defaults. Pins are exact explicit file paths; config
or checker filenames are never automatically pinned. `verifier_paths` explicitly
declares pinned checker dependencies; an empty tuple declares an external or
inline checker. Undeclared source files cannot remain in the projected copy.

Every trial first validates the anchored source and complete fresh copy, deletes
omitted managed files in that copy, applies declared fixed text overlays, then
checks the final namespace/bytes. Source changes, unsafe destinations and
projection failures abort with ERROR before agent/verifier execution. Originally
empty directories are removed; only parents of retained/pinned/overlay files
remain. This is a trusted stable-source contract, not an atomic snapshot.

The result is a **1-minimal retained set of managed files in the projected
initial workspace under the configured oracle**. It minimizes exactly the failure
identity the checker recognizes. A checker that calls every nonzero exit FAIL
can accept a different import/build failure; M6 does not infer causal root-bug
equivalence. File/byte statistics describe original source bytes before overlays
and agent edits; pinned bytes are reported separately.

**Workspace projection does not establish agent-readable filesystem isolation.**
Omitted files are absent from the initial trial workspace; an agent/runtime may
still read elsewhere on the host. Global agent-readable filesystem isolation:
**NOT PROVEN**. `NOT_GUARANTEED` means the execution contract does not guarantee
external read isolation, not that external reads were observed. `UNKNOWN` means
no applicable assurance was established. `VERIFIED_RESTRICTED` is reserved and
rejected in M6 v0.1. `projection=` records matching completed materializations
within each actual classification; stale or unused projectors cannot certify it.

Metadata-only path oracles remain useful and explicitly have no physical
projection evidence:

```python
from agentreducer import OracleResult
from agentreducer.repository import RepositoryFile, RepositoryManifest, RepositoryReducer

manifest = RepositoryManifest(tuple(
    RepositoryFile(name, "0" * 64, 1, 0o644)
    for name in ("auth", "cache", "noise")
))

class FileNamesOracle:
    def evaluate(self, candidate):
        return (OracleResult.FAIL if {"auth", "cache"} <= set(candidate.files)
                else OracleResult.PASS)

result = RepositoryReducer().reduce(manifest.candidate(), FileNamesOracle())
assert result.reduced.files == ("auth", "cache")
assert not result.visibility.workspace_projection_verified
assert result.visibility.external_read_isolation.value == "unknown"
```

Use `reduce_repository_agent(candidate, oracle, projection=projection)` with an
existing M4 oracle, or `reduce_repository_stochastic(candidate, session, projection=projection)`
with a fresh M5 session. They add no trials. The nested repository result carries
visibility; preserve both its qualification and M4 context evidence. The M5 wrapper
also preserves the session qualification verbatim, policy, budget and counters.
Repeated-trial minimality remains policy-relative and observation-relative.

Hold prompt text fixed. For fixed M2 instructions, render once into
`FixedTextOverlay("AGENTS.md", fixed_text)`; pin an existing AGENTS file and
explicitly list its replacement in `replace_pinned_paths`, or declare an absent
fixed overlay path. An overlay on a reducible file is rejected. Repository-owned
AGENTS may instead be reduced without an overlay. Verifier dependencies cannot
be overlaid. Agent-generated files/edits belong to that trial; every next trial
starts with a fresh projection. No joint prompt/instructions/files optimizer.

M6 does not append the private original source anchor to agent prompts, argv,
environment, cwd, generated metadata or public infrastructure diagnostics.
Caller-supplied context can intentionally contain that path. Source provenance
stays private and outside candidate identity; this non-disclosure behavior is
not a claim that an agent cannot discover other host files.

## Development

Requires Python 3.11+. In a virtual environment, install and run:

```shell
python -m pip install -e ".[test]"
python -m pytest
```

There are no runtime dependencies. `pytest` is the sole direct test dependency;
`setuptools` is used only to build the package. The `src/agentreducer` package
contains the outcome/error models, oracle protocol, and reducer. Its `prompt`
subpackage contains M1's models, parser, oracle adapter, and reducer, and
`instructions` contains M2's equivalents. `execution` contains M3's command
models, workspace copying, materializer, runner, exit policy, and generic oracle.
`agents` contains M4's context models, owned environment, native Codex adapter,
generic agent oracle and qualified reduction entry points.
`tests` covers contracts, source preservation and cleanup, real subprocess
integrations, edge cases, and 1-minimality for deterministic predicates.
