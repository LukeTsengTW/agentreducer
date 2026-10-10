from collections import defaultdict
import json
import sys
import pytest
from cli_support import ROOT
from _cli_helpers import owned_case,base_args,invoke_cli,read_trace,assert_clean


def agent_args(c,behavior='pattern'):
    args=base_args(c,'repository'); args[args.index('command')]='codex'
    vector=json.loads(c.argv_file.read_text()); vector[vector.index('repository')]='agent-repository'; c.argv_file.write_text(json.dumps(vector))
    prefix=[sys.executable,str(ROOT/'tests/fixtures/fake_repository_agent.py'),'--trace',str(c.caller/'agent.jsonl'),'--behavior',behavior]
    return args+['--allow-agent-execution','--model','offline-fixture','--context-mode','ambient','--task-text','fixed task',
        '--codex-argv-json',json.dumps(prefix),'--stochastic','--min-trials','5','--max-trials','5','--failure-threshold','4/5','--max-total-trials','500']


def test_repository_m5_real_cli_trial_counts_and_policy_witnesses(tmp_path):
    c=owned_case(tmp_path,'repository')
    p=invoke_cli(['--format','json']+agent_args(c),cwd=c.caller)
    assert p.returncode==0,p.stderr
    r=json.loads(p.stdout); agents=read_trace(c.caller/'agent.jsonl'); verifiers=read_trace(c.trace)
    assert r['context']['stochastic']['policy']['failure_threshold']=={'numerator':4,'denominator':5}
    assert r['context']['agent']['mode']=='ambient'
    assert r['cost']['underlying_trial_attempts']==r['cost']['budget']['used']==len(agents)==len(verifiers)
    assert r['cost']['agent_launches']['value']==len(agents) and r['cost']['verifier_launches']['value']==len(verifiers)
    assert r['cost']['projection_attempts']==r['cost']['completed_projection_checks']==len(agents)
    counts=r['cost']['stochastic_counters']
    for name in ('pass','fail','error'): assert counts[f'observed_{name}_trials']==counts[f'committed_{name}_trials']
    groups=defaultdict(list)
    for row in agents: groups[tuple(row['files'])].append(row['bad'])
    retained=('check.py','src/auth.py','src/cache.py','state.txt')
    assert sum(groups[retained][:5])==4
    for omitted in ('src/auth.py','src/cache.py'):
        assert groups[tuple(x for x in retained if x!=omitted)] and not any(groups[tuple(x for x in retained if x!=omitted)])
    assert_clean(c,agents)
    assert all(not __import__('pathlib').Path(x['cwd']).exists() for x in verifiers)


@pytest.mark.parametrize('mode',['partial-budget','trial-error'])
def test_m5_failures_publish_no_partial_reduced_artifact(tmp_path,mode):
    c=owned_case(tmp_path,'repository'); target=tmp_path/'paths'
    args=agent_args(c,'agent-error' if mode=='trial-error' else 'pattern')
    if mode=='partial-budget': args+=['--min-trials','1','--max-total-trials','2']
    p=invoke_cli(['--format','json','--reduced-output',str(target)]+args,cwd=c.caller)
    r=json.loads(p.stdout); agents=read_trace(c.caller/'agent.jsonl'); verifiers=read_trace(c.trace)
    assert p.returncode==5 and r['result'] is None and not target.exists()
    assert len(agents)==(2 if mode=='partial-budget' else 1)
    assert len(verifiers)==(2 if mode=='partial-budget' else 0)
    assert r['error']['code']==('STOCHASTIC_BUDGET_EXHAUSTED' if mode=='partial-budget' else 'STOCHASTIC_TRIAL_ERROR')
