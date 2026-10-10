import sys
from pathlib import Path

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.execution import CommandOracle, CommandSpec, ExitCodePolicy, TextFileMaterializer, WorkspaceTemplate
from agentreducer.instructions import InstructionCandidate, InstructionReducer, parse_instructions
from agentreducer.prompt import PromptCandidate, PromptReducer, PromptSplitMode, split_prompt

CHECKER = """from pathlib import Path
import sys
counter = Path(sys.argv[2])
with counter.open('a', encoding='utf-8') as stream:
    stream.write('launch\\n')
assert Path('state.txt').read_text() == 'original'
assert not Path('trial-only.txt').exists()
Path('state.txt').write_text('mutated')
Path('trial-only.txt').write_text('new')
text = Path(sys.argv[1]).read_text(encoding='utf-8')
raise SystemExit(1 if all(marker in text for marker in sys.argv[3:]) else 0)
"""


def checker_oracle(tmp_path: Path, destination: str, markers: tuple[str, str], *, policy=None):
    source = tmp_path / "source"
    source.mkdir()
    (source / "checker.py").write_text(CHECKER, encoding="utf-8")
    (source / "state.txt").write_bytes(b"original")
    (source / destination).write_bytes(b"original input")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    counter = tmp_path / "launches.txt"
    paths = []
    text = TextFileMaterializer(destination, lambda candidate: candidate.render())

    class RecordingMaterializer:
        def materialize(self, candidate, workspace):
            paths.append(workspace)
            text.materialize(candidate, workspace)

    kwargs = {} if policy is None else {"exit_code_policy": policy}
    oracle = CommandOracle(
        WorkspaceTemplate(source),
        CommandSpec((sys.executable, "checker.py", destination, str(counter), *markers), 5),
        RecordingMaterializer(), **kwargs,
    )
    return oracle, source, before, counter, paths


def assert_integrity(source: Path, before: dict[str, bytes], paths: list[Path]) -> None:
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert len(set(paths)) == len(paths)
    assert all(not path.exists() for path in paths)


def test_prompt_reducer_with_real_subprocess_oracle(tmp_path: Path) -> None:
    candidate = split_prompt("A\nB\nUSE_CACHE\nC\nD\nAUTH_MODULE\nE\nF", PromptSplitMode.LINE)
    oracle, source, before, counter, paths = checker_oracle(tmp_path, "prompt.txt", ("USE_CACHE", "AUTH_MODULE"))
    result = PromptReducer().reduce(candidate, oracle)
    launches = len(counter.read_text().splitlines())
    assert result.reduced.render() == "USE_CACHE\nAUTH_MODULE"
    assert result.original is candidate
    assert launches == result.evaluations == len(paths)
    print(f"Prompt integration: logical oracle evaluations = {result.evaluations}; actual subprocess launches = {launches}")
    assert oracle.evaluate(result.reduced) is OracleResult.FAIL
    for index in range(2):
        deletion = PromptCandidate(result.reduced.blocks[:index] + result.reduced.blocks[index + 1:], candidate.mode)
        assert oracle.evaluate(deletion) is OracleResult.PASS
    assert len(counter.read_text().splitlines()) == launches + 3
    assert_integrity(source, before, paths)


def test_instruction_reducer_with_real_subprocess_oracle(tmp_path: Path) -> None:
    candidate = parse_instructions("""# Agent Rules

General project instructions.

## Development
- Inspect existing utilities.
- Prefer functional programming.
- Use the existing cache.
- Do not modify unrelated files.

## Testing
- Run tests before finishing.
""")
    markers = ("Prefer functional programming.", "Use the existing cache.")
    oracle, source, before, counter, paths = checker_oracle(tmp_path, "AGENTS.md", markers)
    result = InstructionReducer().reduce(candidate, oracle)
    launches = len(counter.read_text().splitlines())
    assert result.reduced.render() == "# Agent Rules\n\n## Development\n\n- Prefer functional programming.\n\n- Use the existing cache."
    assert [block.text for block in result.reduced.blocks] == ["- Prefer functional programming.", "- Use the existing cache."]
    assert launches == result.evaluations == len(paths)
    print(f"Instruction integration: logical oracle evaluations = {result.evaluations}; actual subprocess launches = {launches}")
    assert oracle.evaluate(result.reduced) is OracleResult.FAIL
    for index in range(2):
        deletion = InstructionCandidate(result.reduced.blocks[:index] + result.reduced.blocks[index + 1:])
        assert oracle.evaluate(deletion) is OracleResult.PASS
    assert len(counter.read_text().splitlines()) == launches + 3
    assert_integrity(source, before, paths)


def test_command_initial_pass_retains_reducer_error_contract(tmp_path: Path) -> None:
    candidate = split_prompt("irrelevant")
    oracle, source, before, counter, paths = checker_oracle(tmp_path, "prompt.txt", ("USE_CACHE", "AUTH_MODULE"))
    with pytest.raises(InitialCandidateNotFailingError):
        PromptReducer().reduce(candidate, oracle)
    assert counter.read_text().splitlines() == ["launch"]
    assert_integrity(source, before, paths)


def test_command_error_aborts_reduction_without_partial_result(tmp_path: Path) -> None:
    candidate = split_prompt("USE_CACHE\nAUTH_MODULE", PromptSplitMode.LINE)
    oracle, source, before, counter, paths = checker_oracle(
        tmp_path, "prompt.txt", ("USE_CACHE", "AUTH_MODULE"), policy=ExitCodePolicy({7}, {1}),
    )
    with pytest.raises(OracleEvaluationError) as caught:
        PromptReducer().reduce(candidate, oracle)
    assert caught.value.candidate == ()
    assert counter.read_text().splitlines() == ["launch", "launch"]
    assert_integrity(source, before, paths)
