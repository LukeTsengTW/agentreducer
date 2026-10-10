"""Validate all user choices before process discovery or workspace creation."""

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from agentreducer.execution import CommandSpec, ExitCodePolicy, TextFileMaterializer, WorkspaceTemplate
from agentreducer.execution.workspace import DEFAULT_EXCLUSIONS, _reject_link
from agentreducer.prompt import PromptSplitMode, split_prompt
from agentreducer.instructions import parse_instructions
from agentreducer.repository import build_repository_manifest, RepositoryProjectionMaterializer
from agentreducer.stochastic import StochasticPolicy
from agentreducer.reporting.output import OutputFailure, prepare_output
from .inputs import (ConfigFailure, read_argv, read_text_input, exit_code,
                     finite_seconds, positive_int, exact_fraction)


@dataclass(frozen=True)
class RunConfig:
    domain: str
    oracle: str
    format: str
    debug: bool
    source: Path = field(repr=False)
    candidate: object = field(repr=False)
    workspace: object = field(repr=False)
    materializer: object = field(repr=False)
    projection: object = field(repr=False)
    verifier: CommandSpec = field(repr=False)
    exit_policy: ExitCodePolicy = field(repr=False)
    render_prompt: object = field(repr=False)
    stochastic_policy: StochasticPolicy | None = field(repr=False)
    max_total_trials: int | None
    model: str | None = field(repr=False)
    agent_timeout: float
    codex_argv: tuple[str, ...] | None = field(repr=False)
    output: object = field(repr=False)
    safe_experiment: object


def _candidate_materializer(path):
    if not isinstance(path, str) or any(ord(c) < 32 for c in path) or "\\" in path:
        raise ConfigFailure("CANDIDATE_PATH_INVALID")
    materializer = TextFileMaterializer(path, lambda candidate: candidate.render())
    if materializer.relative_path.as_posix() != path:
        raise ConfigFailure("CANDIDATE_PATH_INVALID")
    return materializer


def load_config(ns, *, read_stdin):
    try:
        return _load_config(ns, read_stdin=read_stdin)
    except ConfigFailure:
        raise
    except OutputFailure:
        raise ConfigFailure("OUTPUT_PLAN_INVALID") from None
    except (OSError, TypeError, ValueError):
        raise ConfigFailure("INVALID_CONFIGURATION") from None


def _load_config(ns, *, read_stdin):
    # No workspace/discovery/process calls are permitted anywhere in this module.
    agent_fields = (ns.allow_agent_execution, ns.model, ns.context_mode,
        ns.codex_executable, ns.codex_argv_json, ns.codex_argv_file, ns.agent_timeout)
    if ns.oracle == "command" and any(x is not None and x is not False for x in agent_fields):
        raise ConfigFailure("AGENT_OPTIONS_FORBIDDEN")
    if ns.oracle == "codex":
        if not ns.allow_agent_execution or not ns.model or ns.context_mode is None:
            raise ConfigFailure("AGENT_OPT_IN_REQUIRED")
        if ns.context_mode != "ambient":
            raise ConfigFailure("CONTROLLED_CONTEXT_UNAVAILABLE")
        ns.model.encode("utf-8")
        if any(ord(c) < 32 or c.isspace() for c in ns.model):
            raise ConfigFailure("MODEL_INVALID")
    stochastic_values = (ns.min_trials, ns.max_trials, ns.failure_threshold, ns.max_total_trials)
    policy = None
    budget = None
    if ns.stochastic:
        if any(x is None for x in stochastic_values):
            raise ConfigFailure("STOCHASTIC_POLICY_INVALID")
        policy = StochasticPolicy(positive_int(ns.min_trials), positive_int(ns.max_trials),
                                  exact_fraction(ns.failure_threshold))
        budget = positive_int(ns.max_total_trials)
        if budget < policy.min_trials:
            raise ConfigFailure("STOCHASTIC_POLICY_INVALID")
    elif any(x is not None for x in stochastic_values):
        raise ConfigFailure("STOCHASTIC_OPTIONS_FORBIDDEN")
    timeout = finite_seconds(ns.verify_timeout)
    agent_timeout = finite_seconds(ns.agent_timeout if ns.agent_timeout is not None else "120")
    passes = frozenset(map(exit_code, ns.pass_exit_code)) if ns.pass_exit_code is not None else frozenset({0})
    fails = frozenset(map(exit_code, ns.fail_exit_code)) if ns.fail_exit_code is not None else None
    exit_policy = ExitCodePolicy(passes, fails)
    for name in ns.exclude_basename:
        if not name or name in (".", "..") or any(c in name for c in "/\\") or any(ord(c)<32 for c in name):
            raise ConfigFailure("EXCLUSION_INVALID")
    exclusions = DEFAULT_EXCLUSIONS | frozenset(ns.exclude_basename)
    if ns.force and ns.output is None and ns.reduced_output is None:
        raise ConfigFailure("FORCE_REQUIRES_OUTPUT")
    task_fields = (getattr(ns,"task_file",None), getattr(ns,"task_text",None), bool(getattr(ns,"task_stdin",False)))
    has_task = sum((task_fields[0] is not None, task_fields[1] is not None, task_fields[2]))
    if (ns.oracle == "command" or ns.domain == "prompt") and has_task:
        raise ConfigFailure("TASK_OPTIONS_FORBIDDEN")
    if ns.oracle == "codex" and ns.domain != "prompt" and has_task != 1:
        raise ConfigFailure("TASK_INPUT_REQUIRED")
    if getattr(ns,"stdin",False) and task_fields[2]:
        raise ConfigFailure("STDIN_CONFLICT")
    materializer = projection = None
    if ns.domain != "repository":
        if ns.domain == "prompt" and ns.oracle == "codex":
            if ns.candidate_path is not None:
                raise ConfigFailure("CANDIDATE_PATH_FORBIDDEN")
        else:
            materializer = _candidate_materializer(ns.candidate_path)
            if set(materializer.relative_path.parts) & exclusions:
                raise ConfigFailure("CANDIDATE_PATH_EXCLUDED")
    argv = read_argv(json_text=ns.verify_argv_json, file_path=ns.verify_argv_file)
    verifier = CommandSpec(argv, timeout)
    codex_argv = None
    if ns.oracle == "codex":
        if ns.codex_executable is not None:
            executable = Path(ns.codex_executable)
            if not executable.is_absolute():
                raise ConfigFailure("CODEX_EXECUTABLE_INVALID")
            codex_argv = (str(executable),)
        elif ns.codex_argv_json is not None or ns.codex_argv_file is not None:
            codex_argv = read_argv(json_text=ns.codex_argv_json, file_path=ns.codex_argv_file)
    source = Path(ns.source).absolute()
    for part in (source, *source.parents):
        _reject_link(part)
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ConfigFailure("SOURCE_INVALID")
    protected = tuple(p for p in (getattr(ns,"input_file",None),task_fields[0],
        ns.verify_argv_file,ns.codex_argv_file) if p is not None)
    destination = ns.output if ns.output is not None else ns.reduced_output
    output = prepare_output(destination,force=ns.force,kind="report" if ns.output is not None else "reduced",
        source=source,protected_inputs=protected) if destination is not None else None
    fixed_task = read_text_input(file_path=task_fields[0],text=task_fields[1],
        use_stdin=task_fields[2],read_stdin=read_stdin) if has_task else None
    if ns.domain == "repository":
        manifest = build_repository_manifest(source,pinned_paths=ns.pin,exclusions=exclusions)
        candidate = manifest.candidate()
        projection = RepositoryProjectionMaterializer(manifest,verifier_paths=ns.verifier_path)
        workspace, materializer = manifest.workspace_template(), projection
    else:
        text = read_text_input(file_path=ns.input_file,text=ns.text,use_stdin=ns.stdin,read_stdin=read_stdin)
        candidate = split_prompt(text,PromptSplitMode(ns.split)) if ns.domain == "prompt" else parse_instructions(text)
        workspace = WorkspaceTemplate(source,exclusions)
    render_prompt = (lambda c: c.render()) if ns.domain == "prompt" else (lambda c: fixed_task)
    experiment = {"domain":ns.domain,"oracle":ns.oracle,"verify_timeout_seconds":timeout,
        "pass_exit_codes":sorted(passes),"fail_exit_codes":sorted(fails) if fails is not None else None,
        "candidate_path":getattr(ns,"candidate_path",None),"split":getattr(ns,"split",None),
        "context_mode":ns.context_mode,"model":ns.model,"agent_timeout_seconds":agent_timeout if ns.oracle=="codex" else None,
        "stochastic":ns.stochastic,"max_total_trials":budget,"exclusions":sorted(exclusions)}
    return RunConfig(ns.domain,ns.oracle,ns.format,ns.debug,source,candidate,workspace,
        materializer,projection,verifier,exit_policy,render_prompt,policy,budget,
        ns.model,agent_timeout,codex_argv,output,MappingProxyType(experiment))
