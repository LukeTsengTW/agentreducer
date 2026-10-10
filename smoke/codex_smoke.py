"""One explicitly authorized live trial with bounded scalar diagnostics.

--live may consume model usage. Backend model request count remains unknown.
Offline tests supply a local Python fake; no automatic retry is provided.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from agentreducer import OracleResult
from agentreducer.agents import AgentExecutionOracle, CodexContextMode, CodexRunner, CodexSpec
from agentreducer.execution import CommandSpec, WorkspaceTemplate

VERIFIER = """import hashlib, json, runpy, sys
from pathlib import Path
with Path(sys.argv[1]).open('a', encoding='utf-8') as stream:
    stream.write('verifier\\n')
data = Path('arithmetic.py').read_bytes()
changed = hashlib.sha256(data).hexdigest() != sys.argv[2]
correct = runpy.run_path('arithmetic.py')['add'](2, 3) == 5
print(json.dumps({'changed_bytes': changed, 'correct_behavior': correct, 'cwd': str(Path.cwd())}))
raise SystemExit(0 if changed and correct else 1)
"""


class _ArgumentFailure(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentFailure()


def _blank():
    return {
        "status": "invalid-configuration", "qualification": "No agent evaluation completed.",
        "oracle_outcome": None, "agent_stage": "not-started", "error_stage": None,
        "agent_command_result_present": False, "agent_exit_code": None,
        "agent_timed_out": None, "verifier_command_result_present": False,
        "verifier_exit_code": None, "fixture_changed_bytes": None,
        "fixture_correct_behavior": None, "source_unchanged": None,
        "workspace_cleaned": None, "agent_task_processes": 0,
        "verifier_processes": 0, "backend_model_requests": None,
    }


def _emit(report):
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


def _fixture_flags(command):
    # The verifier may have incidental output; only its final JSON booleans
    # enter this projection. No raw output, anchors or arbitrary values escape.
    try:
        value = json.loads(command.stdout.splitlines()[-1]) if command else {}
    except (ValueError, IndexError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    return tuple(value.get(key) if type(value.get(key)) is bool else None
                 for key in ("changed_bytes", "correct_behavior"))


def _project(report, result, paths, counter):
    agent = result.agent_result
    command = agent.command if agent else None
    verifier = result.verifier_result
    stage = result.error_stage.value if result.error_stage else None
    if stage not in ("workspace", "materialization", "prompt", "agent",
                     "verifier", "policy", "cleanup"):
        stage = None
    witnessed = counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []
    verifier_count = 1 if witnessed == ["verifier"] or verifier is not None else (
        0 if stage in ("workspace", "materialization", "prompt", "agent") else None)
    changed, correct = _fixture_flags(verifier)
    report.update(
        oracle_outcome=result.outcome.name,
        agent_stage=("completed" if agent.succeeded else "error") if agent else
                    ("attempted" if paths else "not-started"),
        error_stage=stage, agent_command_result_present=command is not None,
        agent_exit_code=command.exit_code if command and type(command.exit_code) is int else None,
        agent_timed_out=command.timed_out if command and type(command.timed_out) is bool else None,
        verifier_command_result_present=verifier is not None,
        verifier_exit_code=verifier.exit_code if verifier and type(verifier.exit_code) is int else None,
        fixture_changed_bytes=changed, fixture_correct_behavior=correct,
        agent_task_processes=1 if command else (None if paths else 0),
        verifier_processes=verifier_count,
    )


def _execute(args, runner, report):
    with tempfile.TemporaryDirectory(prefix="agentreducer-live-smoke-") as temporary:
        root = Path(temporary)
        capabilities = runner.preflight(cwd=root)
        if capabilities.errors or capabilities.version is None:
            report["status"] = "preflight-failed"
            return 2
        source = root / "source"
        source.mkdir()
        original = b"def add(a, b):\n    return a - b\n"
        (source / "arithmetic.py").write_bytes(original)
        source_hash = hashlib.sha256(original).hexdigest()
        counter = root / "verifiers.txt"
        paths = []

        class RecordingRunner:
            context = runner.context
            sanitize_diagnostic = runner.sanitize_diagnostic

            def run(self, *, workspace, prompt):
                paths.append(workspace)
                return runner.run(workspace=workspace, prompt=prompt)

        oracle = AgentExecutionOracle(
            WorkspaceTemplate(source), RecordingRunner(),
            CommandSpec((sys.executable, "-c", VERIFIER, str(counter), source_hash), 10),
            lambda _: "Fix arithmetic.py so add(a, b) returns the sum. Change only that file. Do not install anything or access the network.",
        )
        report.update(agent_stage="attempted", agent_task_processes=None, verifier_processes=None)
        result = oracle.evaluate_detailed(None)
        _project(report, result, paths, counter)
        report["source_unchanged"] = hashlib.sha256((source / "arithmetic.py").read_bytes()).hexdigest() == source_hash
        report["workspace_cleaned"] = len(paths) == 1 and all(not path.exists() for path in paths)
        verified = (result.outcome is OracleResult.PASS
                    and report["agent_task_processes"] == report["verifier_processes"] == 1
                    and report["source_unchanged"] and report["workspace_cleaned"])
        report["status"] = "verified" if verified else "failed"
        return 0 if verified else 1


def main(argv=None):
    parser = _Parser(prog="codex_smoke.py", description=__doc__, allow_abbrev=False)
    parser.add_argument("--live", action="store_true", help="explicitly authorize this one live trial")
    parser.add_argument("--context-mode", choices=[mode.value for mode in CodexContextMode], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--executable", required=True, help="absolute trusted native executable")
    parser.add_argument("--launcher-arg", action="append", default=[], help="trusted fixed prefix argument")
    report = _blank()
    try:
        args = parser.parse_args(argv)
        if not args.live:
            parser.error("Live opt-in required.")
    except _ArgumentFailure:
        _emit(report)
        return 2
    try:
        mode = CodexContextMode(args.context_mode)
        runner = CodexRunner(CodexSpec(args.model, (args.executable, *args.launcher_arg), context_mode=mode))
    except Exception:
        report.update(status="preparation-failed", error_stage="preparation")
        _emit(report)
        return 2
    report["qualification"] = runner.context.qualification
    if mode is CodexContextMode.CONTROLLED:
        report["status"] = "unavailable"
        _emit(report)
        return 2
    try:
        code = _execute(args, runner, report)
    except KeyboardInterrupt:
        report.update(status="cancelled", error_stage="smoke")
        code = 130
    except Exception:
        report.update(status="smoke-error", error_stage="smoke")
        code = 1
    _emit(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
