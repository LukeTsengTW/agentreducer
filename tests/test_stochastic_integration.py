import json
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.agents import (
    AgentContextEvidence, AgentExecutionOracle, AgentRunResult, CodexContextMode,
    CodexRunner, CodexSpec, ContextAssurance, ControlledEnvironment,
)
from agentreducer.execution import CommandResult, CommandSpec, ExitCodePolicy, TextFileMaterializer, WorkspaceTemplate
from agentreducer.instructions import InstructionCandidate, parse_instructions
from agentreducer.prompt import PromptCandidate, PromptSplitMode, split_prompt
from agentreducer.stochastic import (
    AgentTrials, LaunchCounts, RepeatedOracle, StochasticPolicy, StopReason,
    reduce_instructions_stochastic, reduce_prompt_stochastic,
)

P,F,E = OracleResult.PASS, OracleResult.FAIL, OracleResult.ERROR
FIXTURE = Path(__file__).parent / "fixtures" / "fake_stochastic_agent.py"
CHECK = """import json,sys
from pathlib import Path
with Path(sys.argv[1]).open('a',encoding='utf-8') as stream:
    stream.write(json.dumps({'cwd':str(Path.cwd())})+'\\n')
assert Path('state.txt').read_bytes() == b'mutated'
assert Path('trial-only.txt').read_bytes() == b'new'
if sys.argv[2] == 'verifier-error': raise SystemExit(7)
raise SystemExit(1 if Path('generated.txt').read_text() == 'bad' else 0)
"""


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class RecordingSession(RepeatedOracle):
    def evaluate(self, candidate):
        outcome = super().evaluate(candidate)
        self.trace.append((candidate,outcome))
        return outcome


def prepare(tmp_path, domain="prompt", mode="pattern", context_mode=CodexContextMode.AMBIENT):
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.txt").write_bytes(b"original")
    (source / "AGENTS.md").write_bytes(b"original instructions")
    before = {p.name:p.read_bytes() for p in source.iterdir()}
    paths = []
    class RecordingTemplate:
        @contextmanager
        def create(self):
            with WorkspaceTemplate(source).create() as workspace:
                paths.append(workspace.path)
                yield workspace
    env = {"AGENTREDUCER_STOCH_DOMAIN":domain, "AGENTREDUCER_STOCH_MODE":mode,
           "AGENTREDUCER_STOCH_COUNTER":str(tmp_path / "agents.jsonl")}
    agent = CodexRunner(CodexSpec("fixture", (sys.executable,str(FIXTURE.resolve())), 5, env, context_mode))
    verifier = CommandSpec((sys.executable,"-c",CHECK,str(tmp_path / "verifiers.jsonl"),mode),5)
    oracle = AgentExecutionOracle(RecordingTemplate(),agent,verifier,
        (lambda candidate:candidate.render()) if domain == "prompt" else (lambda _:"fixed coding task"),
        materializer=None if domain == "prompt" else TextFileMaterializer("AGENTS.md",lambda candidate:candidate.render()),
        exit_code_policy=ExitCodePolicy({0},{1}))
    def observe():
        return LaunchCounts(len(rows(tmp_path / "agents.jsonl")),len(rows(tmp_path / "verifiers.jsonl")))
    return oracle,source,before,paths,observe,env


def verify_lifecycle(tmp_path, source, before, paths, agent_count, verifier_count):
    agents,verifiers = rows(tmp_path / "agents.jsonl"),rows(tmp_path / "verifiers.jsonl")
    assert len(agents) == agent_count and len(verifiers) == verifier_count
    assert len(set(paths)) == len(paths) and all(not path.exists() for path in paths)
    assert {p.name:p.read_bytes() for p in source.iterdir()} == before
    assert [Path(row["cwd"]) for row in agents] == paths[:agent_count]
    assert [Path(row["cwd"]) for row in verifiers] == paths[:verifier_count]
    assert all(row["pristine"] for row in agents)


def test_fixed_five_is_five_real_agents_and_verifiers_in_fresh_copies(tmp_path):
    oracle,source,before,paths,observe,_ = prepare(tmp_path)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=5,launch_observer=observe)
    result = session.evaluate_detailed(split_prompt("USE_CACHE\nAUTH_MODULE",PromptSplitMode.LINE))
    assert result.outcome is F and result.stop_reason is StopReason.FULL_HORIZON
    assert tuple(record.outcome for record in result.records) == (F,F,P,F,F)
    assert (result.fail_trials,result.pass_trials,result.error_trials) == (4,1,0)
    assert result.local_launch_counts == session.snapshot().launch_counts == LaunchCounts(5,5)
    assert result.counters.logical_candidate_evaluations == 1
    assert result.counters.underlying_trial_attempts == result.budget_after.used == len(paths) == 5
    assert result.counters.observed_fail_trials == result.counters.committed_fail_trials == 4
    verify_lifecycle(tmp_path,source,before,paths,5,5)
    print("M5 fixed5: logical1/trials5/agent5/verifier5; F4/P1/E0; 5fresh copies; AMBIENT")


@pytest.mark.parametrize("mode,expected,count", [("all-pass",P,2),("all-fail",F,4)])
def test_real_early_stopping_does_not_launch_unused_trials(tmp_path,mode,expected,count):
    oracle,source,before,paths,observe,_ = prepare(tmp_path,mode=mode)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(2,5,"0.8"),max_total_trials=5,launch_observer=observe)
    result = session.evaluate_detailed(split_prompt("USE_CACHE AUTH_MODULE"))
    assert result.outcome is expected and result.trials_executed == count
    assert result.stopped_early and result.decision_complete
    assert result.local_launch_counts == LaunchCounts(count,count)
    assert session.budget.used == count
    verify_lifecycle(tmp_path,source,before,paths,count,count)
    print(f"M5 early {expected.name}: trials={count}; agent={count}; verifier={count}")


@pytest.mark.parametrize("case,agents,verifiers", [("agent-error",1,0),("verifier-error",1,1),("controlled",0,0)])
def test_first_infrastructure_error_aborts_before_minimum(tmp_path,case,agents,verifiers):
    oracle,source,before,paths,observe,_ = prepare(tmp_path,mode=case,
        context_mode=CodexContextMode.CONTROLLED if case == "controlled" else CodexContextMode.AMBIENT)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=5,launch_observer=observe)
    result = session.evaluate_detailed(split_prompt("USE_CACHE AUTH_MODULE"))
    assert result.outcome is E and result.stop_reason is StopReason.TRIAL_ERROR
    assert result.trials_executed == result.error_trials == session.budget.used == len(paths) == 1
    assert result.local_launch_counts == LaunchCounts(agents,verifiers)
    assert result.observed_failure_rate is None and not result.decision_complete
    assert not result.context.native_controlled_verified
    verify_lifecycle(tmp_path,source,before,paths,agents,verifiers)
    print(f"M5 {case}: first ERROR; trial1/agent{agents}/verifier{verifiers}; no retry")


def test_budget_partial_then_precheck_adds_no_process(tmp_path):
    oracle,source,before,paths,observe,_ = prepare(tmp_path)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(2,5,"0.8"),max_total_trials=2,launch_observer=observe)
    candidate = split_prompt("USE_CACHE AUTH_MODULE")
    first = session.evaluate_detailed(candidate)
    assert first.outcome is E and first.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert (first.fail_trials,first.error_trials) == (2,0)
    second = session.evaluate_detailed(candidate)
    assert second.outcome is E and second.trials_executed == 0
    assert second.local_launch_counts == LaunchCounts(0,0)
    assert len(paths) == session.budget.used == 2
    verify_lifecycle(tmp_path,source,before,paths,2,2)
    print("M5 real budget: partial F2/P0/E0 trial2/agent2/verifier2; next0/0/0")


@pytest.mark.parametrize("domain,budget", [("prompt",200),("instructions",100)])
def test_full_repeated_reduction_has_exact_cost_and_in_run_witnesses(tmp_path,domain,budget):
    oracle,source,before,paths,observe,_ = prepare(tmp_path,domain)
    session = RecordingSession.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=budget,launch_observer=observe)
    session.trace = []
    if domain == "prompt":
        candidate = split_prompt("A\nB\nUSE_CACHE\nC\nD\nAUTH_MODULE\nE\nF",PromptSplitMode.LINE)
        qualified = reduce_prompt_stochastic(candidate,session)
        expected = "USE_CACHE\nAUTH_MODULE"
    else:
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
        qualified = reduce_instructions_stochastic(candidate,session)
        expected = "# Agent Rules\n\n## Development\n\n- USE_CACHE\n\n- AUTH_MODULE"
    result,c = qualified.reduction,qualified.counters
    assert result.original is candidate and result.reduced.render() == expected
    count = result.evaluations
    assert len(session.trace) == c.logical_candidate_evaluations == count
    assert c.underlying_trial_attempts == c.budget.used == qualified.budget.used == len(paths) == 5*count
    assert c.launch_counts == LaunchCounts(5*count,5*count)
    assert c.unreturned_attempts == c.observed_error_trials == c.committed_error_trials == 0
    assert c.observed_pass_trials == c.committed_pass_trials
    assert c.observed_fail_trials == c.committed_fail_trials
    assert any(current == result.reduced and outcome is F for current,outcome in session.trace)
    for index in range(len(result.reduced.blocks)):
        blocks = result.reduced.blocks[:index]+result.reduced.blocks[index+1:]
        deletion = PromptCandidate(blocks,result.reduced.mode) if domain == "prompt" else InstructionCandidate(blocks)
        assert any(current == deletion and outcome is P for current,outcome in session.trace)
    assert qualified.context is oracle.context
    assert "all repeated trials and the entire reduction" in qualified.qualification
    assert "not controlled and not frozen" in qualified.qualification
    verify_lifecycle(tmp_path,source,before,paths,5*count,5*count)
    if domain == "instructions":
        assert all(row["prompt"] == "fixed coding task" for row in rows(tmp_path / "agents.jsonl"))
        assert any(row["instructions"] == expected for row in rows(tmp_path / "agents.jsonl"))
    assert len(session.trace) == count
    assert observe() == c.launch_counts
    print(f"M5 {domain}: logical={count}; trials={5*count}; agents={5*count}; verifiers={5*count}; budget={c.budget.used}/{budget}; observed P/F/E={c.observed_pass_trials}/{c.observed_fail_trials}/{c.observed_error_trials}; committed identical; retained FAIL +2deletion PASS in-run; extra0")


@pytest.mark.parametrize("case,error,count", [("all-pass",InitialCandidateNotFailingError,5),("agent-error",OracleEvaluationError,1)])
def test_real_reduction_preserves_initial_error_contract(tmp_path,case,error,count):
    oracle,source,before,paths,observe,_ = prepare(tmp_path,mode=case)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=10,launch_observer=observe)
    with pytest.raises(error): reduce_prompt_stochastic(split_prompt("USE_CACHE AUTH_MODULE"),session)
    assert session.budget.used == count
    verify_lifecycle(tmp_path,source,before,paths,count,0 if case == "agent-error" else count)


def test_intermediate_budget_error_does_not_return_partial_wrapper(tmp_path):
    oracle,source,before,paths,observe,_ = prepare(tmp_path)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=7,launch_observer=observe)
    with pytest.raises(OracleEvaluationError):
        reduce_prompt_stochastic(split_prompt("USE_CACHE\nAUTH_MODULE",PromptSplitMode.LINE),session)
    assert session.budget.used == 5 and session.last_evaluation.trials_executed == 0
    assert session.snapshot().logical_candidate_evaluations == 2
    verify_lifecycle(tmp_path,source,before,paths,5,5)


def test_duplicate_candidates_still_run_fresh_processes(tmp_path):
    oracle,source,before,paths,observe,_ = prepare(tmp_path)
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(),max_total_trials=10,launch_observer=observe)
    candidate = split_prompt("USE_CACHE AUTH_MODULE")
    assert session.evaluate(candidate) is F
    assert session.evaluate_detailed(candidate).outcome is F
    assert session.budget.used == 10
    for _ in range(3): session.snapshot(),session.budget,session.context,session.last_evaluation
    assert observe() == LaunchCounts(10,10)
    verify_lifecycle(tmp_path,source,before,paths,10,10)


def test_fake_controlled_repeated_trials_clean_distinct_owned_auxiliary_homes(tmp_path):
    oracle,source,before,paths,observe,env = prepare(tmp_path,mode="all-fail")
    auxiliary = []
    class ControlledFake:
        context = AgentContextEvidence(assurance=ContextAssurance.FAKE_FIXTURE,version="m5-owned-fake",unknown_sources=())
        def run(self, *, workspace, prompt):
            with ControlledEnvironment(workspace,env=env).create() as owned:
                auxiliary.append(owned.root)
                started = time.perf_counter()
                process = subprocess.run((sys.executable,str(FIXTURE.resolve())),cwd=workspace,
                    input=prompt.encode("utf-8"),env=dict(owned.env),capture_output=True,shell=False,timeout=5)
                command = CommandResult((sys.executable,str(FIXTURE.resolve())),process.returncode,
                    process.stdout.decode("utf-8","replace"),process.stderr.decode("utf-8","replace"),time.perf_counter()-started,False)
                return AgentRunResult(command,None,self.context)
    oracle = replace(oracle,agent=ControlledFake())
    session = RepeatedOracle.from_trials(AgentTrials(oracle),StochasticPolicy(2,2,1),max_total_trials=2,launch_observer=observe)
    result = session.evaluate_detailed(split_prompt("USE_CACHE AUTH_MODULE"))
    assert result.outcome is F and result.local_launch_counts == LaunchCounts(2,2)
    assert len(set(auxiliary)) == len(auxiliary) == 2
    assert all(not root.exists() for root in auxiliary)
    assert all(not root.is_relative_to(workspace) for root,workspace in zip(auxiliary,paths))
    assert result.context.assurance is ContextAssurance.FAKE_FIXTURE
    assert not result.context.native_controlled_verified and "fake-fixture" in result.qualification
    verify_lifecycle(tmp_path,source,before,paths,2,2)
