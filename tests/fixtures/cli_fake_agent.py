"""Local Python protocol fixture; never invokes a model or native CLI."""

from pathlib import Path
import argparse
import json
import sys

p=argparse.ArgumentParser()
p.add_argument('--trace',required=True)
p.add_argument('--domain',required=True)
p.add_argument('--behavior',choices=('fixed','agent-error','pattern'),default='fixed')
args,extra=p.parse_known_args()
prompt=sys.stdin.buffer.read().decode('utf-8')
trace=Path(args.trace)
index=len(trace.read_text().splitlines()) if trace.exists() else 0
with trace.open('a',encoding='utf-8') as stream:
    stream.write(json.dumps({'cwd':str(Path.cwd()),'prompt':prompt,'argv':extra,'trial':index})+'\n')
if args.behavior=='agent-error':
    print('PRIVATE_AGENT_ERROR',file=sys.stderr)
    raise SystemExit(9)
if args.domain=='prompt': bad='BUG' in prompt
elif args.domain=='instructions': bad='BUG' in Path('candidate.txt').read_text(encoding='utf-8')
else: bad=Path('bug.txt').exists()
if args.behavior=='pattern' and index%5==4: bad=False
Path('generated.txt').write_text('bad' if bad else 'good',encoding='utf-8')
print('PRIVATE_AGENT_OUTPUT')
