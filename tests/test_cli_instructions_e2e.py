import json
from _cli_helpers import owned_case,base_args,invoke_cli,read_trace,assert_clean


def test_instruction_headings_and_atomic_raw_renderer_across_process(tmp_path):
    c=owned_case(tmp_path,'instructions'); artifact=tmp_path/'reduced.md'
    p=invoke_cli(['--format','json','--reduced-output',str(artifact)]+base_args(c,'instructions'),cwd=c.caller)
    assert p.returncode==0,p.stderr
    r=json.loads(p.stdout); rows=read_trace(c.trace)
    expected='# PRIVATE_INSTRUCTION_TEXT\n\n## Nested\n\nUSE_CACHE\n\nAUTH_MODULE'
    assert artifact.read_bytes()==expected.encode()
    assert r['result']['reduced_block_count']==2
    assert r['cost']['logical_evaluations']==len(rows)==r['cost']['verifier_launches']['value']
    assert 'PRIVATE_INSTRUCTION_TEXT' not in p.stdout.decode()
    assert any(x['text']==expected and x['bad'] for x in rows)
    assert any('USE_CACHE' in x['text'] and 'AUTH_MODULE' not in x['text'] and not x['bad'] for x in rows)
    assert any('AUTH_MODULE' in x['text'] and 'USE_CACHE' not in x['text'] and not x['bad'] for x in rows)
    assert_clean(c,rows)
