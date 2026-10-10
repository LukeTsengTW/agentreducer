"""Owned offline subprocess checker with independent in-run witnesses."""

from pathlib import Path
import argparse
import hashlib
import json
import sys

p=argparse.ArgumentParser()
p.add_argument('--mode',required=True)
p.add_argument('--trace',required=True)
p.add_argument('--candidate',default='candidate.txt')
p.add_argument('--error',action='store_true')
p.add_argument('--drift-source')
args=p.parse_args()
root=Path.cwd()
files=sorted(x.relative_to(root).as_posix() for x in root.rglob('*') if x.is_file())
text=Path(args.candidate).read_text(encoding='utf-8') if args.mode in ('prompt','instructions') else None
pristine=Path('state.txt').read_bytes()==b'original' and not Path('trial-only.txt').exists()
if args.mode=='agent-repository':
    trial=json.loads(Path('.trial-result.json').read_text(encoding='utf-8'))
    bad=trial['bad']; managed=trial['files']
elif args.mode=='repository':
    bad=Path('src/auth.py').exists() and Path('src/cache.py').exists(); managed=files
else:
    bad=all(marker in text for marker in ('USE_CACHE','AUTH_MODULE')); managed=files
record={'cwd':str(root),'files':managed,'pristine':pristine,'text':text,'bad':bad,
    'digests':{x:hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in files}}
with Path(args.trace).open('a',encoding='utf-8') as stream:
    stream.write(json.dumps(record)+'\n')
if args.drift_source:
    Path(args.drift_source).write_bytes(b'changed-owned-source')
Path('state.txt').write_bytes(b'mutated')
Path('trial-only.txt').write_bytes(b'new')
print('PRIVATE_REPORT_CANARY')
print('PRIVATE_REPORT_CANARY',file=sys.stderr)
raise SystemExit(7 if args.error or not pristine else 1 if bad else 0)
