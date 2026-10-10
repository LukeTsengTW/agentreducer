import json
import sys
import pytest

import agentreducer.cli as cli
from agentreducer.cli import main
from cli_support import make_args


@pytest.mark.parametrize('argv',[['--help'],['--version'],['prompt','--help']])
def test_informational_commands_never_prepare_execution(argv,monkeypatch,capsys):
    monkeypatch.setattr(cli,'prepare_execution',lambda c:pytest.fail('execution'))
    assert main(argv)==0
    assert capsys.readouterr().out


def test_invalid_arguments_preserve_json_selection_without_secret(capsys,monkeypatch):
    monkeypatch.setattr(cli,'prepare_execution',lambda c:pytest.fail('execution'))
    assert main(['--format','json','prompt','--PRIVATE_PARSE_CANARY'])==2
    out=capsys.readouterr(); report=json.loads(out.out)
    assert report['status']=='invalid-configuration' and report['result'] is None
    assert 'PRIVATE_PARSE_CANARY' not in out.out+out.err


def test_runtime_system_exit_is_not_parse_error(tmp_path,monkeypatch):
    signal=SystemExit(19)
    def stop(c): raise signal
    monkeypatch.setattr(cli,'prepare_execution',stop)
    with pytest.raises(SystemExit) as got: main(make_args(tmp_path))
    assert got.value is signal


def test_keyboardinterrupt_maps_only_at_outer_ui_boundary(tmp_path,monkeypatch,capsys):
    def stop(c): raise KeyboardInterrupt()
    monkeypatch.setattr(cli,'prepare_execution',stop)
    assert main(['--format','json']+make_args(tmp_path))==130
    assert json.loads(capsys.readouterr().out)['status']=='cancelled'


@pytest.mark.parametrize('text,want',[('BUG',0),('clean',3)])
def test_main_reports_real_reduction_or_initial_pass(tmp_path,capsys,text,want):
    assert main(['--format','json']+make_args(tmp_path,text=text))==want
    report=json.loads(capsys.readouterr().out)
    assert report['exit_code']==want
    assert report['cost']['verifier_launches']['value']>=1
    assert (report['result'] is not None)==(want==0)


def test_config_failure_never_publishes(tmp_path,capsys):
    target=tmp_path/'report.json'
    assert main(['--output',str(target),'--format','json']+make_args(tmp_path)+['--verify-timeout','NaN'])==2
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)['status']=='invalid-configuration'


def test_budget_exhaustion_is_distinct_from_trial_error(tmp_path,capsys):
    assert main(['--format','json']+make_args(tmp_path,stochastic=True)+['--max-total-trials','5'])==5
    r=json.loads(capsys.readouterr().out)
    assert r['error']['code']=='STOCHASTIC_BUDGET_EXHAUSTED' and r['result'] is None
    assert r['cost']['budget']['used']==5


def test_stdout_failure_retains_already_published_artifact(tmp_path,monkeypatch):
    class Broken:
        def write(self,value): raise OSError('PRIVATE_OUTPUT_CANARY')
        def flush(self): pass
    target=tmp_path/'report.json'; values=['--output',str(target)]+make_args(tmp_path)
    monkeypatch.setattr(sys,'stdout',Broken())
    assert main(values)==7
    assert json.loads(target.read_text())['status']=='reduced'


def test_secondary_publication_failure_preserves_established_cancellation(tmp_path,monkeypatch,capsys):
    from agentreducer.reporting.output import OutputFailure
    def stop(c): raise KeyboardInterrupt()
    def publish_failure(*a): raise OutputFailure('OUTPUT_PUBLISH_FAILED')
    monkeypatch.setattr(cli,'prepare_execution',stop); monkeypatch.setattr(cli,'publish',publish_failure)
    assert main(['--format','json','--output',str(tmp_path/'output')]+make_args(tmp_path))==130
    captured=capsys.readouterr(); report=json.loads(captured.out)
    assert report['status']=='cancelled' and report['exit_code']==130
    assert 'delivery' in captured.err.lower()


@pytest.mark.parametrize('code,published,want',[('OUTPUT_EXISTS',False,'OUTPUT_EXISTS'),
    ('OUTPUT_PUBLISH_FAILED',False,'OUTPUT_PUBLISH_FAILED'),
    ('OUTPUT_CLEANUP_FAILED',False,'OUTPUT_CLEANUP_FAILED'),
    ('OUTPUT_CLEANUP_FAILED',True,'OUTPUT_PUBLISHED_CLEANUP_FAILED')])
def test_publication_error_code_and_confirmed_publication_survive_ui(tmp_path,monkeypatch,capsys,code,published,want):
    from agentreducer.reporting.output import OutputFailure
    def fail(*a): raise OutputFailure(code,published=published)
    monkeypatch.setattr(cli,'publish',fail)
    assert main(['--format','json','--output',str(tmp_path/'output')]+make_args(tmp_path))==7
    report=json.loads(capsys.readouterr().out)
    assert report['error']['code']==want and report['result'] is None
    if published: assert 'published and retained' in report['error']['message']
    if code=='OUTPUT_PUBLISH_FAILED': assert 'hardlinks' in report['error']['message']


def test_actual_published_report_retained_and_delivered_error_identifies_cleanup(tmp_path,monkeypatch,capsys):
    from pathlib import Path
    original=Path.unlink
    def unlink(path,*a,**k):
        if path.name.startswith('.agentreducer-output-'): raise OSError('PRIVATE_CLEANUP')
        return original(path,*a,**k)
    target=tmp_path/'output'; values=['--format','json','--output',str(target)]+make_args(tmp_path)
    monkeypatch.setattr(Path,'unlink',unlink)
    assert main(values)==7
    captured=capsys.readouterr(); delivered=json.loads(captured.out)
    assert json.loads(target.read_text())['status']=='reduced'
    assert delivered['error']['code']=='OUTPUT_PUBLISHED_CLEANUP_FAILED'
    assert 'PRIVATE_CLEANUP' not in captured.out+captured.err
