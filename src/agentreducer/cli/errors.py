"""Finite user-facing errors and deliberately minimal debug frames."""

from dataclasses import dataclass
from pathlib import Path

from agentreducer import InitialCandidateNotFailingError,OracleEvaluationError
from agentreducer.stochastic import StopReason
from agentreducer.reporting.output import OutputFailure
from .inputs import ConfigFailure


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    stage: str | None
    message: str

    def mapping(self):
        return {"code":self.code,"stage":self.stage,"message":self.message}


_CONFIG_CODES=frozenset({"INVALID_CONFIGURATION","INVALID_ARGUMENTS","VERIFY_ARGV_INVALID",
    "INPUT_SELECTION_INVALID","INPUT_READ_FAILED","INTEGER_INVALID","TIMEOUT_INVALID",
    "STOCHASTIC_POLICY_INVALID","STOCHASTIC_OPTIONS_FORBIDDEN","AGENT_OPTIONS_FORBIDDEN",
    "AGENT_OPT_IN_REQUIRED","CONTROLLED_CONTEXT_UNAVAILABLE","MODEL_INVALID","EXCLUSION_INVALID",
    "FORCE_REQUIRES_OUTPUT","TASK_OPTIONS_FORBIDDEN","TASK_INPUT_REQUIRED","STDIN_CONFLICT",
    "CANDIDATE_PATH_INVALID","CANDIDATE_PATH_FORBIDDEN","CANDIDATE_PATH_EXCLUDED",
    "CODEX_EXECUTABLE_INVALID","SOURCE_INVALID","OUTPUT_PLAN_INVALID","CODEX_PREPARATION_FAILED"})

_OUTPUT_MESSAGES={
    "OUTPUT_EXISTS":"The destination already exists; choose another path or explicitly allow replacement with --force.",
    "OUTPUT_PATH_UNSAFE":"Choose a regular destination in an existing directory without links or reparse points.",
    "OUTPUT_PROTECTED":"The destination overlaps a protected source or input; choose another path.",
    "OUTPUT_PLAN_INVALID":"Check the explicit output path and compatible output options.",
    "OUTPUT_PUBLISH_FAILED":"Publication failed. Default no-clobber publication requires hardlinks on the destination filesystem; choose a supported filesystem or explicitly allow replacement with --force.",
    "OUTPUT_CLEANUP_FAILED":"Temporary output cleanup failed before publication was confirmed.",
    "OUTPUT_FAILED":"Output delivery failed; publication was not confirmed.",
}


def stochastic_error_from_public_last_evaluation(session):
    last=session.last_evaluation
    reason=last.stop_reason if last else None
    if reason is StopReason.BUDGET_EXHAUSTED:
        info=ErrorInfo("STOCHASTIC_BUDGET_EXHAUSTED",None,"The global trial budget cannot complete the next classification.")
    elif last is not None:
        info=ErrorInfo("STOCHASTIC_TRIAL_ERROR",None,"A repeated trial returned ERROR.")
    else:
        info=ErrorInfo("STOCHASTIC_ERROR",None,"The repeated oracle returned ERROR.")
    return "stochastic-error",5,info


def classify_error(exc,execution=None):
    if isinstance(exc,ConfigFailure):
        code=exc.code if exc.code in _CONFIG_CODES else "INVALID_CONFIGURATION"
        return "invalid-configuration",2,ErrorInfo(code,None,"Check the explicit inputs and compatible CLI options.")
    if isinstance(exc,InitialCandidateNotFailingError):
        return "initial-not-failing",3,ErrorInfo("INITIAL_NOT_FAILING",None,"The initial classification was PASS.")
    if isinstance(exc,OracleEvaluationError):
        if execution is not None and execution.session is not None:
            return stochastic_error_from_public_last_evaluation(execution.session)
        return "oracle-error",4,ErrorInfo("ORACLE_ERROR",execution.safe_stage() if execution else None,"The configured oracle returned ERROR.")
    if isinstance(exc,OutputFailure):
        code=exc.code if type(exc.code) is str and exc.code in _OUTPUT_MESSAGES else "OUTPUT_FAILED"
        if exc.published is True:
            code="OUTPUT_PUBLISHED_CLEANUP_FAILED" if code=="OUTPUT_CLEANUP_FAILED" else "OUTPUT_PUBLISHED_DELIVERY_FAILED"
            message="Complete output was published and retained, but temporary cleanup failed." if code=="OUTPUT_PUBLISHED_CLEANUP_FAILED" else "Complete output was published and retained, but later delivery failed."
        else:
            message=_OUTPUT_MESSAGES[code]
        return "output-error",7,ErrorInfo(code,None,message)
    if isinstance(exc,KeyboardInterrupt):
        return "cancelled",130,ErrorInfo("CANCELLED",None,"The operation was interrupted.")
    if isinstance(exc,Exception):
        return "internal-error",6,ErrorInfo("INTERNAL_ERROR",None,"Unexpected internal failure.")
    raise TypeError("BaseException outside UI mapping")


def sanitized_stack(exc):
    lines=[type(exc).__name__]
    frame=exc.__traceback__
    while frame is not None:
        code=frame.tb_frame.f_code
        lines.append(f"  {Path(code.co_filename).name}:{frame.tb_lineno} in {code.co_name}")
        frame=frame.tb_next
    return '\n'.join(lines)+'\n'
