import builtins
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentreducer.agents import ControlledEnvironment
from agentreducer.agents import context

FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"


def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    (path / "AGENTS.md").write_text("candidate sentinel", encoding="utf-8")
    return path


def test_owned_paths_override_external_selectors_without_reading_dummy_auth(tmp_path, monkeypatch):
    workspace = project(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    auth = external / "auth.json"
    auth.write_text("DUMMY_AUTH_MUST_NOT_BE_READ_OR_COPIED", encoding="utf-8")
    (external / "AGENTS.md").write_text("external sentinel", encoding="utf-8")
    (external / ".agents").mkdir()
    original_open = builtins.open
    original_io_open = __import__("io").open
    def guard_open(original):
        def guarded(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and Path(file).absolute() == auth.absolute():
                pytest.fail("Dummy external auth file was accessed")
            return original(file, *args, **kwargs)
        return guarded
    monkeypatch.setattr(builtins, "open", guard_open(original_open))
    monkeypatch.setattr(__import__("io"), "open", guard_open(original_io_open))
    monkeypatch.setattr(shutil, "copyfile", lambda *_args, **_kw: pytest.fail("No copying is needed"))
    monkeypatch.setattr(os, "link", lambda *_args, **_kw: pytest.fail("No hard links are needed"))
    monkeypatch.setattr(os, "symlink", lambda *_args, **_kw: pytest.fail("No symlinks are needed"))
    base = {"HOME": str(external), "CODEX_HOME": str(external), "UNDECLARED": "ambient-value"}
    base.update({key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}})
    names = "HOME,USERPROFILE,CODEX_HOME,APPDATA,LOCALAPPDATA,XDG_CONFIG_HOME,XDG_DATA_HOME,XDG_CACHE_HOME,XDG_STATE_HOME,UNDECLARED"
    counter = tmp_path / "observed.jsonl"
    overrides = {"HOME": str(external), "USERPROFILE": str(external), "CODEX_HOME": str(external),
                 "APPDATA": str(external), "XDG_CONFIG_HOME": str(external),
                 "AGENTREDUCER_FAKE_ENV_NAMES": names, "AGENTREDUCER_FAKE_COUNTER": str(counter)}
    builder = ControlledEnvironment(workspace, env=overrides, base_env=base)
    with builder.create() as owned:
        root = owned.root
        assert not root.is_relative_to(workspace)
        assert (workspace / ".agentreducer-root").is_file()
        config = Path(owned.env["CODEX_HOME"]) / "config.toml"
        assert 'project_root_markers = [".agentreducer-root"]' in config.read_text(encoding="utf-8")
        assert (workspace / "AGENTS.md").read_text() == "candidate sentinel"
        process = subprocess.run((sys.executable, str(FAKE.resolve()), "-"), cwd=workspace,
                                 env=dict(owned.env), input="task", text=True, capture_output=True, timeout=5, shell=False)
        assert process.returncode == 0, process.stderr
        observation = json.loads(counter.read_text(encoding="utf-8").splitlines()[0])
        assert observation["env"]["UNDECLARED"] is None
        for key, value in observation["env"].items():
            if key != "UNDECLARED":
                assert Path(value).is_relative_to(root)
                assert Path(value) != external
        assert not list(root.rglob("auth.json"))
        with pytest.raises(TypeError):
            owned.env["HOME"] = str(external)
    assert not root.exists()
    assert not (workspace / ".agentreducer-root").exists()
    assert auth.exists()
    assert (external / "AGENTS.md").read_text() == "external sentinel"


@pytest.mark.parametrize("relative", [".codex/config.toml", ".agents/skills/test/SKILL.md", "AGENTS.override.md", "nested/AGENTS.md", "nested/AGENTS.override.md", "CLAUDE.md"])
def test_undeclared_context_is_rejected_without_deleting_it(tmp_path, relative):
    workspace = project(tmp_path)
    destination = workspace / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("undeclared sentinel", encoding="utf-8")
    with pytest.raises(ValueError, match="context|instruction"):
        with ControlledEnvironment(workspace).create():
            pytest.fail("Undeclared context accepted")
    assert destination.read_text() == "undeclared sentinel"
    assert not (workspace / ".agentreducer-root").exists()


def test_declared_nested_agents_are_allowed_but_override_cannot_shadow(tmp_path):
    workspace = project(tmp_path)
    nested = workspace / "nested" / "AGENTS.md"
    nested.parent.mkdir()
    nested.write_text("nested candidate", encoding="utf-8")
    with ControlledEnvironment(workspace, declared_instructions={"AGENTS.md", "nested/AGENTS.md"}).create():
        assert nested.read_text() == "nested candidate"
    (workspace / "AGENTS.override.md").write_text("shadow", encoding="utf-8")
    with pytest.raises(ValueError):
        with ControlledEnvironment(workspace, declared_instructions={"AGENTS.md", "AGENTS.override.md"}).create():
            pass


@pytest.mark.parametrize("key", ["CODEX_CONFIG_PATH", "CODEX_API_KEY", "OPENAI_API_KEY", "CUSTOM_INSTRUCTIONS", "PYTHONPATH", "NODE_OPTIONS"])
def test_unexpected_configuration_or_auth_overrides_are_rejected(tmp_path, key):
    with pytest.raises(ValueError):
        with ControlledEnvironment(project(tmp_path), env={key: "dummy-value"}).create():
            pass


@pytest.mark.parametrize("declaration", ["../AGENTS.md", "/AGENTS.md", "other.txt", "AGENTS.override.md"])
def test_instruction_declarations_are_portable_and_cannot_allow_shadow_files(tmp_path, declaration):
    with pytest.raises(ValueError):
        ControlledEnvironment(project(tmp_path), declared_instructions={declaration})


@pytest.mark.parametrize("exception", [ValueError("body failure"), KeyboardInterrupt("cancel"), SystemExit(9)])
def test_auxiliary_cleanup_preserves_original_exceptions(tmp_path, exception):
    workspace = project(tmp_path)
    with pytest.raises(type(exception)) as caught:
        with ControlledEnvironment(workspace).create() as owned:
            root = owned.root
            raise exception
    assert caught.value is exception
    assert not root.exists()
    assert not (workspace / ".agentreducer-root").exists()


@pytest.mark.parametrize("exception", [KeyboardInterrupt("cancel"), SystemExit(9)])
def test_cleanup_failure_keeps_cancellation_and_sanitizes_secondary_note(tmp_path, monkeypatch, exception):
    workspace = project(tmp_path)
    original = context.tempfile.TemporaryDirectory
    class CleanupFailure(original):
        def cleanup(self):
            super().cleanup()
            raise OSError("cleanup dummy-secret")
    monkeypatch.setattr(context.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(type(exception)) as caught:
        with ControlledEnvironment(workspace, base_env={"CUSTOM_TOKEN": "dummy-secret"}, sensitive_env_names={"CUSTOM_TOKEN"}).create() as owned:
            root = owned.root
            raise exception
    assert caught.value is exception
    assert not root.exists()
    assert "[REDACTED]" in " ".join(exception.__notes__)
    assert "dummy-secret" not in " ".join(exception.__notes__)


def test_ordinary_auxiliary_cleanup_failure_is_not_success(tmp_path, monkeypatch):
    original = context.tempfile.TemporaryDirectory
    class CleanupFailure(original):
        def cleanup(self):
            super().cleanup()
            raise OSError("cleanup failed dummy-secret")
    monkeypatch.setattr(context.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(Exception, match="cleanup failed") as caught:
        with ControlledEnvironment(project(tmp_path), base_env={"CUSTOM_TOKEN": "dummy-secret"}, sensitive_env_names={"CUSTOM_TOKEN"}).create():
            pass
    assert "dummy-secret" not in str(caught.value)


def test_owned_marker_never_overwrites_candidate_file(tmp_path):
    workspace = project(tmp_path)
    marker = workspace / ".agentreducer-root"
    marker.write_text("candidate marker", encoding="utf-8")
    with pytest.raises(ValueError, match="marker"):
        with ControlledEnvironment(workspace).create():
            pass
    assert marker.read_text() == "candidate marker"


def test_windows_home_pair_and_inherited_auth_select_owned_profile(tmp_path):
    workspace = project(tmp_path)
    with ControlledEnvironment(workspace, base_env={"CODEX_API_KEY": "dummy-auth", "OPENAI_API_KEY": "dummy-auth"}).create() as owned:
        assert "CODEX_API_KEY" not in owned.env and "OPENAI_API_KEY" not in owned.env
        if os.name == "nt":
            assert Path(owned.env["HOMEDRIVE"] + owned.env["HOMEPATH"]) == Path(owned.env["USERPROFILE"])
