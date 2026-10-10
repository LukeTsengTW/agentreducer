import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.agents import (
    AgentContextEvidence, AgentExecutionOracle, AgentRunResult, CodexContextMode,
    CodexRunner, CodexSpec, ContextAssurance, ControlledEnvironment,
    reduce_instructions, reduce_prompt,
)
from agentreducer.execution import CommandResult, CommandSpec, ExitCodePolicy, TextFileMaterializer, WorkspaceTemplate
from agentreducer.instructions import InstructionCandidate, parse_instructions
from agentreducer.prompt import PromptCandidate, PromptSplitMode, split_prompt

FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"
CHECK = """import json, sys
from pathlib import Path
assert Path('state.txt').read_text() == 'mutated'
assert Path('trial-only.txt').read_text() == 'new'
with Path(sys.argv[1]).open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'cwd': str(Path.cwd())}) + '\\n')
raise SystemExit(1 if Path('generated.txt').read_text() == 'bad' else 0)
"""


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def prepare(tmp_path, domain):
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.txt").write_bytes(b"original")
    (source / "AGENTS.md").write_text("original instructions", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    paths = []
    class RecordingTemplate:
        @contextmanager
        def create(self):
            with WorkspaceTemplate(source).create() as workspace:
                paths.append(workspace.path)
                yield workspace
    agent = CodexRunner(CodexSpec("fixture", (sys.executable, str(FAKE.resolve())), 5,
                                 {"AGENTREDUCER_FAKE_MODE": domain, "AGENTREDUCER_FAKE_COUNTER": str(tmp_path / "agents.jsonl")},
                                 CodexContextMode.AMBIENT))
    verifier = CommandSpec((sys.executable, "-c", CHECK, str(tmp_path / "verifiers.jsonl")), 5)
    oracle = AgentExecutionOracle(RecordingTemplate(), agent, verifier,
                                  (lambda candidate: candidate.render()) if domain == "prompt" else (lambda _: "fixed coding task"),
                                  materializer=None if domain == "prompt" else TextFileMaterializer("AGENTS.md", lambda candidate: candidate.render()))
    return oracle, source, before, paths


def assert_integrity(oracle, tmp_path, source, before, paths, count):
    agents = records(tmp_path / "agents.jsonl")
    verifiers = records(tmp_path / "verifiers.jsonl")
    assert len(agents) == len(verifiers) == len(paths) == count
    assert [Path(row["cwd"]) for row in agents] == [Path(row["cwd"]) for row in verifiers] == paths
    assert len(set(paths)) == len(paths)
    assert all(not path.exists() for path in paths)
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert "stable during reduction" in oracle.context.qualification


def test_full_prompt_reduction_with_actual_ambient_adapter_and_fake_processes(tmp_path):
    oracle, source, before, paths = prepare(tmp_path, "prompt")
    candidate = split_prompt("A\nB\nUSE_CACHE\nC\nD\nAUTH_MODULE\nE\nF", PromptSplitMode.LINE)
    qualified = reduce_prompt(candidate, oracle)
    result = qualified.reduction
    assert result.original is candidate
    assert result.reduced.render() == "USE_CACHE\nAUTH_MODULE"
    assert qualified.context is oracle.context
    count = result.evaluations
    assert_integrity(oracle, tmp_path, source, before, paths, count)
    print(f"M4 prompt: logical={count}; agents={count}; verifiers={count}; AMBIENT qualified")
    assert oracle.evaluate(result.reduced) is OracleResult.FAIL
    for index in range(2):
        deletion = PromptCandidate(result.reduced.blocks[:index] + result.reduced.blocks[index + 1:], candidate.mode)
        assert oracle.evaluate(deletion) is OracleResult.PASS
    assert_integrity(oracle, tmp_path, source, before, paths, count + 3)


def test_full_instruction_reduction_retains_headings_and_same_workspace(tmp_path):
    oracle, source, before, paths = prepare(tmp_path, "instructions")
    candidate = parse_instructions("""# Agent Rules

General project instructions.

## Development
- Inspect existing utilities.
- USE_CACHE
- AUTH_MODULE
- Do not modify unrelated files.

## Testing
- Run tests before finishing.
""")
    qualified = reduce_instructions(candidate, oracle)
    result = qualified.reduction
    assert result.reduced.render() == "# Agent Rules\n\n## Development\n\n- USE_CACHE\n\n- AUTH_MODULE"
    assert qualified.context.mode is CodexContextMode.AMBIENT
    count = result.evaluations
    assert_integrity(oracle, tmp_path, source, before, paths, count)
    print(f"M4 instructions: logical={count}; agents={count}; verifiers={count}; AMBIENT qualified")
    assert all(row["prompt"] == "fixed coding task" for row in records(tmp_path / "agents.jsonl"))
    assert oracle.evaluate(result.reduced) is OracleResult.FAIL
    for index in range(2):
        deletion = InstructionCandidate(result.reduced.blocks[:index] + result.reduced.blocks[index + 1:])
        assert oracle.evaluate(deletion) is OracleResult.PASS
    assert_integrity(oracle, tmp_path, source, before, paths, count + 3)


@pytest.mark.parametrize("error_case", ["initial-pass", "intermediate-error"])
def test_full_reduction_preserves_error_counts_without_partial_result(tmp_path, error_case):
    from dataclasses import replace
    oracle, source, before, paths = prepare(tmp_path, "prompt")
    if error_case == "initial-pass":
        candidate = split_prompt("irrelevant")
        exception, count = InitialCandidateNotFailingError, 1
    else:
        candidate = split_prompt("USE_CACHE\nAUTH_MODULE", PromptSplitMode.LINE)
        oracle = replace(oracle, exit_code_policy=ExitCodePolicy({7}, {1}))
        exception, count = OracleEvaluationError, 2
    with pytest.raises(exception):
        reduce_prompt(candidate, oracle)
    assert_integrity(oracle, tmp_path, source, before, paths, count)


def test_generic_fake_controlled_orchestration_uses_owned_homes_and_cleans_them(tmp_path, monkeypatch):
    oracle, source, before, paths = prepare(tmp_path, "instructions")
    from dataclasses import replace
    external = tmp_path / "external"
    external.mkdir()
    (external / "AGENTS.md").write_text("external instructions", encoding="utf-8")
    (external / "auth.json").write_text("dummy auth sentinel", encoding="utf-8")
    monkeypatch.setenv("HOME", str(external))
    monkeypatch.setenv("CODEX_HOME", str(external))
    auxiliary = []
    class ControlledFakeRunner:
        context = AgentContextEvidence(assurance=ContextAssurance.FAKE_FIXTURE,
                                       version="owned-fake-1", unknown_sources=())
        def run(self, *, workspace, prompt):
            environment = {
                "AGENTREDUCER_FAKE_MODE": "instructions",
                "AGENTREDUCER_FAKE_COUNTER": str(tmp_path / "agents.jsonl"),
                "AGENTREDUCER_FAKE_ENV_NAMES": "HOME,USERPROFILE,CODEX_HOME,XDG_CONFIG_HOME",
            }
            with ControlledEnvironment(workspace, env=environment).create() as owned:
                auxiliary.append(owned.root)
                started = time.perf_counter()
                process = subprocess.run((sys.executable, str(FAKE.resolve()), "-"), cwd=workspace,
                                         input=prompt.encode("utf-8"), env=dict(owned.env),
                                         shell=False, capture_output=True, timeout=5)
                command = CommandResult((sys.executable, str(FAKE.resolve()), "-"), process.returncode,
                                        process.stdout.decode("utf-8", "replace"), process.stderr.decode("utf-8", "replace"),
                                        time.perf_counter() - started, False)
                return AgentRunResult(command, None, self.context)
    oracle = replace(oracle, agent=ControlledFakeRunner())
    candidate = parse_instructions("# Rules\n- USE_CACHE\n- AUTH_MODULE\n- irrelevant")
    qualified = reduce_instructions(candidate, oracle)
    count = qualified.reduction.evaluations
    assert len(auxiliary) == len(paths) == count
    assert all(not root.exists() for root in auxiliary)
    assert qualified.context.assurance is ContextAssurance.FAKE_FIXTURE
    assert not qualified.context.native_controlled_verified
    assert "fake-fixture" in qualified.context.qualification
    for row, root, workspace in zip(records(tmp_path / "agents.jsonl"), auxiliary, paths):
        assert not root.is_relative_to(workspace)
        assert all(Path(value).is_relative_to(root) for value in row["env"].values())
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert (external / "auth.json").read_text() == "dummy auth sentinel"
    assert all(not path.exists() for path in paths)
    assert len(records(tmp_path / "verifiers.jsonl")) == count
    print(f"M4 fake CONTROLLED: logical={count}; agents={count}; verifiers={count}; native proof=False")
