import json
import pytest
from _cli_helpers import owned_case,base_args,invoke_cli,read_trace,assert_clean


@pytest.mark.parametrize('source_kind',['file','stdin','text'])
@pytest.mark.parametrize('split',['paragraph','line'])
def test_prompt_actual_process_has_retained_fail_and_deletion_pass_witnesses(tmp_path,source_kind,split):
    c=owned_case(tmp_path,'prompt'); args=base_args(c,'prompt'); inp=None
    if source_kind!='file':
        i=args.index('--input-file'); del args[i:i+2]
        if source_kind=='stdin': args+=['--stdin']; inp=c.input_file.read_bytes()
        else: args+=['--text',c.input_file.read_text()]
    artifact=tmp_path/'reduced.txt'
    completed=invoke_cli(['--format','json','--reduced-output',str(artifact)]+args+['--split',split],cwd=c.caller,input_bytes=inp)
    assert completed.returncode==0,completed.stderr
    r=json.loads(completed.stdout); rows=read_trace(c.trace)
    assert r['cost']['logical_evaluations']==r['cost']['verifier_launches']['value']==len(rows)
    expected='USE_CACHE\n\nAUTH_MODULE' if split=='paragraph' else 'USE_CACHE\nAUTH_MODULE'
    assert artifact.read_bytes()==expected.encode()
    assert any(x['text']==expected and x['bad'] for x in rows)
    assert all(any(x['text']==deletion and not x['bad'] for x in rows) for deletion in ('USE_CACHE','AUTH_MODULE'))
    assert 'PRIVATE_REPORT_CANARY' not in completed.stdout.decode()+completed.stderr.decode()
    assert_clean(c,rows)


@pytest.mark.parametrize('failure,want',[('pass',3),('error',4)])
def test_prompt_failure_has_no_partial_artifact(tmp_path,failure,want):
    c=owned_case(tmp_path,'prompt'); target=tmp_path/'reduced'
    if failure=='pass': c.input_file.write_text('clean')
    else:
        argv=json.loads(c.argv_file.read_text()); argv+=['--error']; c.argv_file.write_text(json.dumps(argv))
    p=invoke_cli(['--format','json','--reduced-output',str(target)]+base_args(c,'prompt'),cwd=c.caller)
    assert p.returncode==want and json.loads(p.stdout)['result'] is None and not target.exists()
    assert_clean(c,read_trace(c.trace))
