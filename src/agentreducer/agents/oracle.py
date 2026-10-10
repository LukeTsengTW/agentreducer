"""Exactly one disposable project, followed by its verifier after agent success."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar

from ..execution.materializer import CandidateMaterializer
from ..execution.models import CommandSpec, ExitCodePolicy
from ..execution.runner import run_command
from ..execution.workspace import WorkspaceTemplate
from ..models import OracleResult
from ._diagnostics import DiagnosticRedactor
from .context import AgentContextEvidence, _strings
from .models import AgentEvaluation, AgentRunResult, AgentStage
from .protocol import AgentRunner

T = TypeVar("T")
_SECRET_NAMES = frozenset({"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"})


@dataclass(frozen=True)
class AgentExecutionOracle(Generic[T]):
    workspace: WorkspaceTemplate
    agent: AgentRunner
    verifier: CommandSpec = field(repr=False)
    render_prompt: Callable[[T], str]
    materializer: CandidateMaterializer[T] | None = None
    exit_code_policy: ExitCodePolicy = field(default_factory=ExitCodePolicy)
    sensitive_env_names: frozenset[str] = _SECRET_NAMES
    _context: AgentContextEvidence = field(init=False, repr=False)

    def __post_init__(self):
        context = self.agent.context
        if not isinstance(context, AgentContextEvidence):
            raise TypeError("Agent context must be AgentContextEvidence.")
        if not isinstance(self.verifier, CommandSpec) or not callable(self.render_prompt):
            raise TypeError("A CommandSpec verifier and callable prompt renderer are required.")
        object.__setattr__(self, "_context", context)
        object.__setattr__(self, "sensitive_env_names", frozenset(_strings(self.sensitive_env_names, "sensitive_env_names")))
        hook = getattr(self.agent, "sanitize_diagnostic", None)
        if hook is not None and not callable(hook):
            raise TypeError("Optional sanitize_diagnostic hook must be callable.")

    @property
    def context(self):
        return self._context

    def evaluate(self, candidate: T) -> OracleResult:
        return self.evaluate_detailed(candidate).outcome

    def evaluate_detailed(self, candidate: T) -> AgentEvaluation:
        redact = DiagnosticRedactor.from_environments(
            _SECRET_NAMES | self.sensitive_env_names, os.environ, self.verifier.env,
        )
        hook = getattr(self.agent, "sanitize_diagnostic", None)

        def clean(text):
            text = redact(text)
            return hook(text) if hook is not None else text

        def clean_command(command):
            if command is None:
                return None
            return replace(command, argv=tuple(clean(arg) for arg in command.argv),
                           stdout=clean(command.stdout), stderr=clean(command.stderr))

        def expose(evaluation):
            run = evaluation.agent_result
            if run is not None:
                run = replace(run, command=clean_command(run.command), error=clean(run.error) if run.error else None)
            return replace(evaluation, agent_result=run, verifier_result=clean_command(evaluation.verifier_result),
                           error=clean(evaluation.error) if evaluation.error else None)

        agent_result = None
        verifier_result = None
        evaluation = None
        stage = AgentStage.WORKSPACE
        body_finished = False
        entered = False

        def failure(message, error_stage):
            return AgentEvaluation(OracleResult.ERROR, agent_result, verifier_result, self.context, error_stage, message)

        try:
            with self.workspace.create() as workspace:
                entered = True
                try:
                    if self.materializer is not None:
                        stage = AgentStage.MATERIALIZATION
                        self.materializer.materialize(candidate, workspace.path)
                    stage = AgentStage.PROMPT
                    prompt = self.render_prompt(candidate)
                    if not isinstance(prompt, str):
                        raise TypeError("Prompt renderer must return a string.")
                    stage = AgentStage.AGENT
                    if self.agent.context != self.context:
                        raise ValueError("Agent context changed since oracle preparation.")
                    run = self.agent.run(workspace=workspace.path, prompt=prompt)
                    if not isinstance(run, AgentRunResult):
                        raise TypeError("Agent must return AgentRunResult.")
                    agent_result = run
                    if run.context != self.context:
                        raise ValueError("Agent result context does not match declared context.")
                    if not run.succeeded:
                        evaluation = failure(run.error or "Agent execution did not succeed.", stage)
                    else:
                        stage = AgentStage.VERIFIER
                        verifier_result = run_command(self.verifier, cwd=workspace.path)
                        if verifier_result.timed_out:
                            evaluation = failure(f"Verifier timeout after {self.verifier.timeout_seconds} seconds.", stage)
                        else:
                            stage = AgentStage.POLICY
                            outcome = self.exit_code_policy.classify(verifier_result.exit_code)
                            if not isinstance(outcome, OracleResult):
                                raise TypeError("Exit-code policy must return an OracleResult.")
                            evaluation = (failure(f"Unclassified verifier exit code: {verifier_result.exit_code}", stage)
                                          if outcome is OracleResult.ERROR else
                                          AgentEvaluation(outcome, agent_result, verifier_result, self.context))
                except Exception as exc:
                    evaluation = failure(f"{type(exc).__name__}: {exc}", stage)
                body_finished = True
            return expose(evaluation)
        except Exception as exc:
            if entered and body_finished:
                error = f"Workspace cleanup failed: {type(exc).__name__}: {exc}"
                if evaluation is not None and evaluation.error:
                    error = f"Earlier {evaluation.error_stage.value} error: {evaluation.error}; {error}"
                return expose(failure(error, AgentStage.CLEANUP))
            return expose(failure(f"{type(exc).__name__}: {exc}", stage))
        except BaseException as exc:
            if hasattr(exc, "__notes__"):
                exc.__notes__[:] = [clean(note) for note in exc.__notes__]
            raise
