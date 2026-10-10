"""Owned offline fixtures; no authentication/config discovery."""

from pathlib import Path
import json
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
FAKE=ROOT/'tests'/'fixtures'/'cli_fake_agent.py'


def safe_environment(root):
    root=Path(root)
    result={'PYTHONUTF8':'1','PATH':str(Path(sys.executable).parent),
        'HOME':str(root/'home'),'USERPROFILE':str(root/'home'),'CODEX_HOME':str(root/'codex'),
        'APPDATA':str(root/'appdata'),'LOCALAPPDATA':str(root/'localappdata'),
        'TEMP':str(root),'TMP':str(root)}
    for key in ('SYSTEMROOT','WINDIR'):
        value=os.environ.get(key)
        if value is not None: result[key]=value
    for name in ('home','codex','appdata','localappdata'):
        (root/name).mkdir(parents=True,exist_ok=True)
    return result


def make_args(tmp_path,domain='prompt',agent=False,stochastic=False,behavior='fixed',text='noise\n\nBUG'):
    source=tmp_path/'source'; source.mkdir(exist_ok=True)
    checker=source/'check.py'
    checker.write_text("from pathlib import Path\nimport sys\nmode=sys.argv[1]\n"
        "if mode=='agent': bad=Path('generated.txt').read_text()=='bad'\n"
        "elif mode=='repository': bad=Path('bug.txt').exists()\n"
        "else: bad='BUG' in Path('candidate.txt').read_text(encoding='utf-8')\n"
        "print('PRIVATE_CHILD_OUTPUT')\nraise SystemExit(1 if bad else 0)\n",encoding='utf-8')
    if domain=='repository':
        (source/'bug.txt').write_text('BUG'); (source/'noise.txt').write_text('noise')
    vector=tmp_path/'verify argv.json'
    vector.write_text(json.dumps([sys.executable,'check.py','agent' if agent else domain]),encoding='utf-8')
    values=[domain,'--source',str(source),'--oracle','codex' if agent else 'command','--verify-argv-file',str(vector)]
    if domain!='repository':
        values+=['--text',text]
        if domain=='instructions' or not agent: values+=['--candidate-path','candidate.txt']
    else: values+=['--pin','check.py','--verifier-path','check.py']
    if agent:
        prefix=[sys.executable,str(FAKE),'--trace',str(tmp_path/'agent.jsonl'),'--domain',domain,'--behavior',behavior]
        values+=['--allow-agent-execution','--model','offline-fixture','--context-mode','ambient','--codex-argv-json',json.dumps(prefix)]
        if domain!='prompt': values+=['--task-text','fixed task']
    if stochastic:
        values+=['--stochastic','--min-trials','5','--max-trials','5','--failure-threshold','4/5','--max-total-trials','200']
    return values
