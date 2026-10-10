import pytest

from agentreducer import InitialCandidateNotFailingError,OracleEvaluationError
from agentreducer.cli.inputs import ConfigFailure
from agentreducer.reporting.output import OutputFailure
from agentreducer.cli.errors import classify_error,sanitized_stack


@pytest.mark.parametrize('exc,status,code',[(ConfigFailure('VERIFY_ARGV_INVALID'),'invalid-configuration',2),
    (InitialCandidateNotFailingError('PRIVATE'),'initial-not-failing',3),
    (OracleEvaluationError(('PRIVATE',)),'oracle-error',4),
    (RuntimeError('PRIVATE'),'internal-error',6),(OutputFailure(),'output-error',7),
    (KeyboardInterrupt('PRIVATE'),'cancelled',130)])
def test_fixed_error_mapping_never_exposes_exception(exc,status,code):
    got_status,got_code,info=classify_error(exc)
    assert (got_status,got_code)==(status,code)
    assert 'PRIVATE' not in repr(info)


def test_debug_excludes_exception_values_source_locals_chains_and_notes():
    try:
        private_value='PRIVATE_DEBUG_CANARY'
        raise RuntimeError(private_value)
    except RuntimeError as exc:
        exc.add_note(private_value)
        rendered=sanitized_stack(exc)
    assert 'RuntimeError' in rendered and 'PRIVATE_DEBUG_CANARY' not in rendered
    assert 'private_value=' not in rendered and 'raise RuntimeError' not in rendered


def test_runtime_systemexit_is_not_an_error_classification():
    with pytest.raises(TypeError): classify_error(SystemExit(19))
