import json
import pytest

import agentreducer.cli as cli
from agentreducer.cli.errors import classify_error
from _cli_helpers import owned_case,base_args,invoke_cli,read_trace


@pytest.mark.parametrize('fmt',['json','human'])
@pytest.mark.parametrize('failure',['unknown','choice','argv','utf8','source','verifier-error'])
def test_process_failures_never_leak_owned_canaries_or_private_anchor(tmp_path,fmt,failure):
    c=owned_case(tmp_path,'prompt'); args=base_args(c,'prompt'); output=tmp_path/'report.json'
    if failure=='unknown': args+=['--PRIVATE_REPORT_CANARY']
    elif failure=='choice': args[args.index('command')]='PRIVATE_REPORT_CANARY'
    elif failure=='argv': c.argv_file.write_text('PRIVATE_REPORT_CANARY')
    elif failure=='utf8': c.input_file.write_bytes(b'PRIVATE_REPORT_CANARY\xff')
    elif failure=='source': args[args.index(str(c.source))]=str(c.source/'missing')
    elif failure=='verifier-error':
        vector=json.loads(c.argv_file.read_text()); vector+=['--error']; c.argv_file.write_text(json.dumps(vector))
    p=invoke_cli(['--format',fmt,'--debug','--output',str(output)]+args,cwd=c.caller)
    persisted=output.read_text() if output.exists() else ''
    visible=p.stdout.decode()+p.stderr.decode()+persisted
    assert 'PRIVATE_REPORT_CANARY' not in visible and str(c.source) not in visible
    if failure=='verifier-error':
        assert p.returncode==4 and json.loads(persisted)['result'] is None
    else:
        assert p.returncode==2 and not output.exists() and not read_trace(c.trace)


def test_exception_str_and_repr_are_never_called():
    class Malicious(RuntimeError):
        def __str__(self): pytest.fail('str called')
        def __repr__(self): pytest.fail('repr called')
    assert classify_error(Malicious())[1]==6


def test_publication_failure_emits_safe_result_null_and_retains_cost(tmp_path,monkeypatch,capsys):
    from agentreducer.reporting.output import OutputFailure
    from cli_support import make_args
    def fail(*a): raise OutputFailure('PRIVATE_REPORT_CANARY')
    monkeypatch.setattr(cli,'publish',fail)
    assert cli.main(['--format','json','--output',str(tmp_path/'report')]+make_args(tmp_path))==7
    got=capsys.readouterr(); r=json.loads(got.out)
    assert r['status']=='output-error' and r['result'] is None and r['cost']['logical_evaluations']>0
    assert 'PRIVATE_REPORT_CANARY' not in got.out+got.err


def test_invalid_model_rejected_before_stdin_or_discovery(tmp_path,monkeypatch):
    from agentreducer.cli.parser import parse_cli
    from agentreducer.cli.config import load_config,ConfigFailure
    from cli_support import make_args
    args=make_args(tmp_path,agent=True); i=args.index('--text'); del args[i:i+2]; args+=['--stdin','--model','invalid model']
    with pytest.raises(ConfigFailure):
        load_config(parse_cli(args),read_stdin=lambda:pytest.fail('stdin read before invalid model check'))
