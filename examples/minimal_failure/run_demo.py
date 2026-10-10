"""Run the installed CLI on an owned copy of a tiny offline repository."""

from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import tempfile


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format',choices=('human','json'),default='human')
    args=parser.parse_args(argv)
    example=Path(__file__).resolve().parent/'source'
    names=('checker.py','src/auth.py','src/cache.py','src/noise.py')
    original={name:(example/name).read_bytes() for name in names}
    with tempfile.TemporaryDirectory(prefix='agentreducer-demo-') as owned:
        root=Path(owned); source=root/'source'; source.mkdir()
        for name in names:
            target=source/name; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(example/name,target)
        vector=root/'verify argv.json'
        vector.write_text(json.dumps([sys.executable,'checker.py']),encoding='utf-8')
        completed=subprocess.run([sys.executable,'-m','agentreducer','--format',args.format,
            'repository','--source',str(source),'--oracle','command','--verify-argv-file',str(vector),
            '--pin','checker.py','--verifier-path','checker.py','--fail-exit-code','1'],shell=False,check=False)
        if any((example/name).read_bytes()!=data or (source/name).read_bytes()!=data for name,data in original.items()):
            raise RuntimeError('Offline demo source changed unexpectedly.')
        return completed.returncode


if __name__=='__main__':
    raise SystemExit(main())
