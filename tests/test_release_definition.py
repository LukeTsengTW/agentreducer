import json
import subprocess
import sys
import pytest
from cli_support import ROOT,safe_environment


@pytest.mark.parametrize('fmt',['human','json'])
def test_independent_demo_runs_outside_checkout(tmp_path,fmt):
    p=subprocess.run([sys.executable,str(ROOT/'examples/minimal_failure/run_demo.py'),'--format',fmt],
        cwd=tmp_path,env=safe_environment(tmp_path),capture_output=True,shell=False,timeout=60)
    assert p.returncode==0,p.stderr
    if fmt=='json':
        r=json.loads(p.stdout)
        assert r['result']['retained_paths']==['src/auth.py','src/cache.py']
        assert r['result']['pinned_paths']==['checker.py']
        assert r['cost']['agent_launches']['value']==0
        assert r['cost']['verifier_launches']['value']==r['cost']['logical_evaluations']
    else:
        assert b'AgentReducer 0.1.0' in p.stdout and b'Retained: src/auth.py' in p.stdout


def test_test_only_workflow_matrix_and_permissions():
    import yaml
    workflow=yaml.load((ROOT/'.github/workflows/tests.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
    assert set(workflow['on'])=={'push','pull_request'}
    assert workflow['permissions']=={'contents':'read'}
    job=workflow['jobs']['tests']; matrix=job['strategy']['matrix']
    assert set(matrix['os'])=={'ubuntu-latest','windows-latest'}
    assert set(matrix['python-version'])=={'3.11','3.14'}
    assert job['runs-on']=='${{ matrix.os }}'
    steps=job['steps']
    for step in steps:
        if 'uses' in step:
            action,sha=step['uses'].split('@'); assert action in ('actions/checkout','actions/setup-python')
            assert len(sha)==40 and all(c in '0123456789abcdef' for c in sha)
        if step.get('uses','').startswith('actions/checkout@'):
            assert step['with']['persist-credentials']=='false'
    runs='\n'.join(step.get('run','') for step in steps)
    assert '-m packaging tests/test_packaging.py' in runs
    assert '-m "not packaging"' in runs
    assert 'run_demo.py --format json' in runs
    assert 'secrets' not in str(workflow) and '--allow-agent-execution' not in runs


def test_readiness_implemented_state_requires_review_final_gates_and_attribution():
    checklist=json.loads((ROOT/'docs/releases/v0.1.0-readiness.json').read_text(encoding='utf-8'))
    required={'offline-tests','whole-change-review','post-review-validation','final-attribution'}
    gates={item['id']:item for item in checklist['gates']}
    if checklist['state'] in ('M7_IMPLEMENTED_OFFLINE','M7_OFFLINE_ACCEPTED','V0.1.0_RELEASE_READY'):
        assert all(gates[name]['status']=='pass' for name in required)
    else:
        assert checklist['state']=='M7_OFFLINE_REVIEW_PENDING'
        assert any(gates[name]['status']!='pass' for name in required)
    if gates['license']['status']=='pass':
        import tomllib
        project=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']
        assert project['license']=='MIT' and project['license-files']==['LICENSE']
        assert 'Copyright (c) 2026 Luke Tseng' in (ROOT/'LICENSE').read_text(encoding='utf-8')
    else:
        assert gates['license']['reason'] in ('LICENSE_CHOICE_PENDING','LICENSE_PACKAGING_VALIDATION_PENDING')
    if gates['live']['status']=='not-authorized':
        assert gates['live']['reason']=='LIVE_VALIDATION_PENDING'
    else:
        assert gates['live']['status'] in ('authorized','pass','fail')
    waiver=gates['live-release-requirement']
    assert waiver['status']=='waived'
    assert waiver['reason']=='HUMAN_WAIVED_SUCCESSFUL_LIVE_VALIDATION'
    assert checklist['tool_version']==waiver['scope_version']=='0.1.0'
    assert waiver['authorized_by']=='human'
    assert waiver['human_decision']=='豁免 v0.1 成功 live 驗證門檻，將三次失敗列為已知限制'
    assert waiver['evidence']
    assert gates['live']['status']=='fail'
    assert gates['live']['reason']=='LIVE_VALIDATION_FAILED'
    if gates['backend-call-bound']['status']=='not-applicable':
        assert gates['backend-call-bound']['reason']=='HUMAN_ACCEPTED_UNKNOWN_BACKEND_REQUEST_COUNT'
    else:
        assert gates['backend-call-bound']['reason']=='BACKEND_MODEL_CALL_BOUND_UNPROVEN'
    if checklist['state']=='V0.1.0_RELEASE_READY':
        assert gates['license']['status']=='pass'
        assert gates['live']['status']=='pass' or waiver['status']=='waived'
        assert gates['backend-call-bound']['status'] in ('pass','not-applicable')
    publication=gates['publication']
    assert publication['status'] in ('not-authorized','authorized')
    if publication['status']=='authorized':
        assert publication['reason']=='HUMAN_AUTHORIZED_GITHUB_PUBLICATION'
        assert publication['authorized_by']=='human'
        assert publication['scope_version']==checklist['tool_version']=='0.1.0'
        assert publication['target']=='github'
        assert publication['repository']=='LukeTsengTW/agentreducer'
        assert checklist['record_scope']=='pre-publication-readiness'
        assert checklist['published'] is False
