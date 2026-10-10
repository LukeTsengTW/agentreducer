from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import subprocess
import sys

from cli_support import ROOT,safe_environment


@dataclass(frozen=True)
class Case:
    source: Path
    caller: Path
    input_file: Path
    argv_file: Path
    trace: Path
    original_hashes: dict


def snapshot(source):
    return {p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob('*') if p.is_file()}


def read_trace(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()] if Path(path).exists() else []


def owned_case(tmp_path,domain):
    source=tmp_path/'PRIVATE_SOURCE_ANCHOR with spaces'; source.mkdir()
    caller=tmp_path/'caller'; caller.mkdir()
    (source/'check.py').write_bytes((ROOT/'tests/fixtures/cli_verifier.py').read_bytes())
    (source/'state.txt').write_bytes(b'original')
    for dirname in ('.git','.venv','excluded-private','empty'):
        (source/dirname).mkdir()
    for dirname in ('.git','.venv','excluded-private'):
        (source/dirname/'fake-auth-canary').write_bytes(b'OWNED_FAKE_AUTH_CANARY')
    if domain=='repository':
        for name,data in [('auth',b'AUTH-original'),('cache',b'CACHE-original'),('noise',b'unrelated')]:
            target=source/'src'/f'{name}.py'; target.parent.mkdir(exist_ok=True); target.write_bytes(data)
    text='noise\n\nUSE_CACHE\n\nAUTH_MODULE\n\nnoise'
    if domain=='instructions':
        text='# PRIVATE_INSTRUCTION_TEXT\n\nnoise\n\n## Nested\n\nUSE_CACHE\n\nAUTH_MODULE\n\n```python\nnoise\n```'
    inp=tmp_path/'input with spaces.txt'; inp.write_text(text,encoding='utf-8')
    trace=tmp_path/'verifier.jsonl'; vector=tmp_path/'argv with spaces.json'
    vector.write_text(json.dumps([sys.executable,'check.py','--mode',domain,'--trace',str(trace),
        '--candidate','AGENTS.md' if domain=='instructions' else 'candidate.txt']),encoding='utf-8')
    return Case(source,caller,inp,vector,trace,snapshot(source))


def base_args(case,domain):
    values=[domain,'--source',str(case.source),'--oracle','command','--verify-argv-file',str(case.argv_file),
        '--exclude-basename','excluded-private','--fail-exit-code','1']
    if domain=='repository': values+=['--pin','check.py','--pin','state.txt','--verifier-path','check.py','--verifier-path','state.txt']
    else: values+=['--input-file',str(case.input_file),'--candidate-path','AGENTS.md' if domain=='instructions' else 'candidate.txt']
    return values


def invoke_cli(args,*,cwd,input_bytes=None,console=None):
    prefix=[str(console)] if console else [sys.executable,'-m','agentreducer']
    return subprocess.run(prefix+list(args),cwd=cwd,input=input_bytes,capture_output=True,
        shell=False,timeout=90,env=safe_environment(cwd))


def assert_clean(case,rows):
    assert snapshot(case.source)==case.original_hashes
    assert all(row['pristine'] for row in rows)
    assert all(not Path(row['cwd']).exists() for row in rows)
    assert len({row['cwd'] for row in rows})==len(rows)
    assert all(not any('fake-auth-canary' in x for x in row['files']) for row in rows)
