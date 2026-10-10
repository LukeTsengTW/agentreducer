from pathlib import Path
import pytest

from agentreducer.cli.parser import parse_cli
from agentreducer.cli.config import load_config, ConfigFailure
from agentreducer.reporting.output import prepare_output, OutputFailure


def args(source, *extra, domain='prompt', oracle='command'):
    values = [domain, '--source', str(source), '--oracle', oracle,
        '--verify-argv-json', '["python", "check.py"]']
    if domain != 'repository': values += ['--text', 'noise\n\nBUG']
    if domain == 'prompt' and oracle == 'command': values += ['--candidate-path', 'input.txt']
    return values + list(extra)


@pytest.mark.parametrize('extra', [ ['--model','x'], ['--context-mode','ambient'],
    ['--allow-agent-execution'], ['--agent-timeout','1'], ['--codex-argv-json','["x"]'],
    ['--min-trials','1'], ['--stochastic'], ['--verify-timeout','NaN'],
    ['--candidate-path','../x'], ['--candidate-path','a\\b'], ['--candidate-path','a//b'],
    ['--candidate-path','a\nfile'], ['--exclude-basename','../x']])
def test_configuration_rejects_before_workspace_or_discovery(tmp_path, monkeypatch, extra):
    source=tmp_path/'source'; source.mkdir()
    import shutil
    from agentreducer.execution import WorkspaceTemplate
    monkeypatch.setattr(shutil,'which',lambda *a: pytest.fail('discovery'))
    monkeypatch.setattr(WorkspaceTemplate,'create',lambda *a: pytest.fail('workspace'))
    with pytest.raises(ConfigFailure): load_config(parse_cli(args(source,*extra)),read_stdin=lambda:pytest.fail('stdin'))


def test_command_defaults_and_explicit_pass_replacement(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    c=load_config(parse_cli(args(source,'--pass-exit-code','7','--fail-exit-code','9')),read_stdin=lambda:b'')
    assert c.exit_policy.pass_exit_codes == frozenset({7})
    assert c.exit_policy.fail_exit_codes == frozenset({9})
    assert c.candidate.render() == 'noise\n\nBUG'
    assert c.safe_experiment['oracle'] == 'command'
    assert str(source) not in repr(c)
    assert c.workspace.source == source


@pytest.mark.parametrize('extra', [[], ['--allow-agent-execution'],
    ['--allow-agent-execution','--model','x'],
    ['--allow-agent-execution','--model','x','--context-mode','controlled']])
def test_codex_requires_all_explicit_consent_and_ambient(tmp_path,extra):
    source=tmp_path/'source'; source.mkdir()
    with pytest.raises(ConfigFailure): load_config(parse_cli(args(source,*extra,oracle='codex')),read_stdin=lambda:b'')


def test_codex_prompt_has_no_candidate_path(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    base=args(source,'--allow-agent-execution','--model','fake','--context-mode','ambient',oracle='codex')
    assert load_config(parse_cli(base),read_stdin=lambda:b'').materializer is None
    with pytest.raises(ConfigFailure):
        load_config(parse_cli(base+['--candidate-path','input.txt']),read_stdin=lambda:b'')


def test_instruction_tasks_are_required_only_for_codex_and_no_double_stdin(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    with pytest.raises(ConfigFailure): load_config(parse_cli(args(source,'--task-text','do it',domain='instructions')),read_stdin=lambda:b'')
    base=args(source,'--allow-agent-execution','--model','fake','--context-mode','ambient',domain='instructions',oracle='codex')
    with pytest.raises(ConfigFailure): load_config(parse_cli(base),read_stdin=lambda:b'')
    c=load_config(parse_cli(base+['--task-text','do it']),read_stdin=lambda:b'')
    assert c.render_prompt(c.candidate) == 'do it'
    base[base.index('--text')] = '--stdin'; base.remove('noise\n\nBUG')
    with pytest.raises(ConfigFailure): load_config(parse_cli(base+['--task-stdin']),read_stdin=lambda:pytest.fail('read'))


def test_stochastic_policy_is_complete_and_exact(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    good=['--stochastic','--min-trials','2','--max-trials','5','--failure-threshold','0.80','--max-total-trials','10']
    c=load_config(parse_cli(args(source,*good)),read_stdin=lambda:b'')
    assert c.stochastic_policy.failure_threshold.numerator == 4
    assert c.stochastic_policy.failure_threshold.denominator == 5
    for extra in [['--max-total-trials','1'], ['--min-trials','6']]:
        with pytest.raises(ConfigFailure): load_config(parse_cli(args(source,*good,*extra)),read_stdin=lambda:b'')


def test_repository_verifier_dependencies_must_be_pinned(tmp_path):
    source=tmp_path/'source'; source.mkdir(); (source/'check.py').write_text('x'); (source/'bug.txt').write_text('BUG')
    base=args(source,'--verifier-path','check.py',domain='repository')
    with pytest.raises(ConfigFailure): load_config(parse_cli(base),read_stdin=lambda:b'')
    c=load_config(parse_cli(base+['--pin','check.py']),read_stdin=lambda:b'')
    assert c.candidate.files == ('bug.txt',)
    assert c.projection.projection_attempts == 0


@pytest.mark.parametrize('kind',['report','reduced'])
def test_output_preflight_protects_source_inputs_and_missing_parents(tmp_path,kind):
    source=tmp_path/'source'; source.mkdir()
    inp=tmp_path/'input.txt'; inp.write_text('private')
    targets=[inp,source/'output',tmp_path/'missing'/'x',source]
    for target in targets:
        with pytest.raises(OutputFailure): prepare_output(target,force=True,kind=kind,source=source,protected_inputs=(inp,))
    assert inp.read_text() == 'private'
    hard=tmp_path/'hard'; hard.hardlink_to(inp)
    with pytest.raises(OutputFailure): prepare_output(hard,force=True,kind=kind,source=source,protected_inputs=(inp,))
    with pytest.raises(OutputFailure): prepare_output(inp,force=False,kind=kind,source=source,protected_inputs=())


def test_output_preflight_error_is_configuration_exit_class(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    ns=parse_cli(['--output',str(source/'bad')]+args(source))
    with pytest.raises(ConfigFailure) as got: load_config(ns,read_stdin=lambda:b'')
    assert got.value.code == 'OUTPUT_PLAN_INVALID'


def test_force_requires_root_destination(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    with pytest.raises(ConfigFailure):
        load_config(parse_cli(['--force']+args(source)),read_stdin=lambda:b'')
