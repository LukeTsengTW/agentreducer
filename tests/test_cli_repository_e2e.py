import json
from _cli_helpers import owned_case,base_args,invoke_cli,read_trace,assert_clean


def test_repository_process_scoped_visibility_pins_and_witnesses(tmp_path):
    c=owned_case(tmp_path,'repository'); target=tmp_path/'paths.txt'
    p=invoke_cli(['--format','json','--reduced-output',str(target)]+base_args(c,'repository'),cwd=c.caller)
    assert p.returncode==0,p.stderr
    r=json.loads(p.stdout); rows=read_trace(c.trace)
    assert r['result']['retained_paths']==['src/auth.py','src/cache.py']
    assert r['result']['pinned_paths']==['check.py','state.txt']
    assert r['context']['repository_visibility']['workspace_projection_verified'] is True
    assert r['context']['repository_visibility']['external_read_isolation']=='not-guaranteed'
    assert r['cost']['projection_attempts']==r['cost']['completed_projection_checks']==len(rows)
    assert r['cost']['logical_evaluations']==r['cost']['verifier_launches']['value']==len(rows)
    assert target.read_bytes()==b'src/auth.py\nsrc/cache.py\n'
    for omit in ('src/auth.py','src/cache.py'):
        assert any(set(x['files'])==({'check.py','state.txt','src/auth.py','src/cache.py'}-{omit}) and not x['bad'] for x in rows)
    assert str(c.source) not in p.stdout.decode()+p.stderr.decode()
    assert_clean(c,rows)


def test_source_drift_blocks_later_child_and_claims_no_visibility(tmp_path):
    c=owned_case(tmp_path,'repository')
    argv=json.loads(c.argv_file.read_text()); argv+=['--drift-source',str(c.source/'src/noise.py')]; c.argv_file.write_text(json.dumps(argv))
    p=invoke_cli(['--format','json']+base_args(c,'repository'),cwd=c.caller)
    r=json.loads(p.stdout)
    assert p.returncode==4 and len(read_trace(c.trace))==1
    assert r['result'] is None and r['context']['repository_visibility']['workspace_projection_verified'] is False
    assert r['cost']['verifier_launches']['value'] is None and r['cost']['verifier_launches']['lower_bound']==1


def test_invalid_verifier_pin_fails_with_zero_children(tmp_path):
    c=owned_case(tmp_path,'repository'); args=base_args(c,'repository')
    i=args.index('--pin'); del args[i:i+2]
    p=invoke_cli(['--format','json']+args,cwd=c.caller)
    assert p.returncode==2 and not read_trace(c.trace)
