from dataclasses import FrozenInstanceError

import pytest

from agentreducer import OracleResult


def test_command_configuration_snapshots_mutable_inputs() -> None:
    from agentreducer.execution import CommandSpec

    argv = ["python", "input with spaces"]
    env = {"MODE": "original"}
    spec = CommandSpec(argv, timeout_seconds=2, env=env)
    argv.append("extra")
    env["MODE"] = "changed"
    assert spec.argv == ("python", "input with spaces")
    assert dict(spec.env) == {"MODE": "original"}
    assert spec.timeout_seconds == 2
    with pytest.raises(TypeError):
        spec.env["MODE"] = "changed"
    with pytest.raises(FrozenInstanceError):
        spec.argv = ("another",)


@pytest.mark.parametrize("argv", ["python --version", b"python", [], [""], [1], ["python", "\0"]])
def test_invalid_argv_is_rejected_before_launch(argv: object) -> None:
    from agentreducer.execution import CommandSpec

    with pytest.raises((TypeError, ValueError)):
        CommandSpec(argv)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), None, "1", True])
def test_invalid_timeout_is_rejected_before_launch(timeout: object) -> None:
    from agentreducer.execution import CommandSpec

    with pytest.raises((TypeError, ValueError)):
        CommandSpec(("python",), timeout_seconds=timeout)


@pytest.mark.parametrize("env", [{"": "x"}, {"A=B": "x"}, {"A\0": "x"}, {"A": "\0"}, {1: "x"}, {"A": 1}, []])
def test_invalid_environment_is_rejected_before_launch(env: object) -> None:
    from agentreducer.execution import CommandSpec

    with pytest.raises((TypeError, ValueError)):
        CommandSpec(("python",), env=env)


@pytest.mark.parametrize("code,expected", [(0, OracleResult.PASS), (1, OracleResult.FAIL), (7, OracleResult.FAIL), (-9, OracleResult.FAIL), (None, OracleResult.ERROR)])
def test_default_exit_code_policy(code: int | None, expected: OracleResult) -> None:
    from agentreducer.execution import ExitCodePolicy

    assert ExitCodePolicy().classify(code) is expected


def test_explicit_exit_code_sets_are_snapshots_and_unknown_codes_are_errors() -> None:
    from agentreducer.execution import ExitCodePolicy

    passes, failures = {2}, {3}
    policy = ExitCodePolicy(passes, failures)
    passes.add(0)
    failures.add(4)
    assert policy.classify(2) is OracleResult.PASS
    assert policy.classify(3) is OracleResult.FAIL
    assert policy.classify(0) is OracleResult.ERROR
    assert policy.classify(4) is OracleResult.ERROR
    assert policy.classify(None) is OracleResult.ERROR
    assert ExitCodePolicy({2}, set()).classify(0) is OracleResult.ERROR
    with pytest.raises(FrozenInstanceError):
        policy.pass_exit_codes = frozenset({9})


def test_invalid_exit_code_policies_are_rejected() -> None:
    from agentreducer.execution import ExitCodePolicy

    with pytest.raises(ValueError):
        ExitCodePolicy({0}, {0})
    with pytest.raises(TypeError):
        ExitCodePolicy({"0"}, {1})
    with pytest.raises(TypeError):
        ExitCodePolicy({True}, {1})


def test_diagnostics_are_immutable_and_keep_command_details() -> None:
    from agentreducer.execution import CommandEvaluation, CommandResult

    argv = ["python"]
    command = CommandResult(argv, 1, "out", "err", 0.1, False)
    argv.append("changed")
    evaluation = CommandEvaluation(OracleResult.FAIL, command)
    assert evaluation.command.stdout == "out"
    assert evaluation.command.stderr == "err"
    assert evaluation.command.argv == ("python",)
    assert evaluation.outcome is OracleResult.FAIL
    with pytest.raises(FrozenInstanceError):
        command.exit_code = 0
    with pytest.raises(FrozenInstanceError):
        evaluation.outcome = OracleResult.PASS
