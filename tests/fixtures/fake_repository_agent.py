"""Local deterministic process fixture; never invokes a coding model or network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser()
parser.add_argument("--trace",required=True)
parser.add_argument("--behavior",choices=("fixed","agent-error","pattern"),default="fixed")
args,_=parser.parse_known_args()
root=Path.cwd()
paths=sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
auth=Path("src/auth.py").exists()
cache=Path("src/cache.py").exists()
prior=([json.loads(line) for line in Path(args.trace).read_text(encoding="utf-8").splitlines()]
       if args.behavior=="pattern" and Path(args.trace).exists() else [])
ordinal=sum(row["files"]==paths for row in prior)
bad=auth and cache and (args.behavior!="pattern" or ordinal%5!=2)
pristine=(not Path(".trial-result.json").exists() and not Path(".generated.txt").exists()
          and (not auth or Path("src/auth.py").read_bytes()==b"AUTH-original")
          and (not cache or Path("src/cache.py").read_bytes()==b"CACHE-original"))
record={
    "cwd":str(root),"files":paths,
    "digests":{path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths},
    "stdin":sys.stdin.buffer.read().decode("utf-8"),
    "argv":sys.argv[1:],"env":dict(os.environ),"pristine":pristine,
    "agents_text":Path("AGENTS.md").read_text(encoding="utf-8") if Path("AGENTS.md").exists() else None,
    "bad":bad,
}
with Path(args.trace).open("a",encoding="utf-8") as stream:
    stream.write(json.dumps(record)+"\n")
if not pristine or args.behavior=="agent-error":
    raise SystemExit(7)
if auth:Path("src/auth.py").write_bytes(b"mutated")
if cache:Path("src/cache.py").unlink()
Path(".generated.txt").write_bytes(b"created")
Path(".trial-result.json").write_text(
    json.dumps({"files":paths,"auth":auth,"cache":cache,"bad":bad}),encoding="utf-8")
