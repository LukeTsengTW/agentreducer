from pathlib import Path
import tomllib
import json
import sys
import sysconfig

from _cli_helpers import owned_case,base_args,invoke_cli

from agentreducer._version import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_version_source_and_console_metadata():
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == "0.1.0"
    assert "version" not in data["project"]
    assert data["project"]["dynamic"] == ["version"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "agentreducer._version.__version__"
    }
    assert data["project"]["scripts"]["agentreducer"] == "agentreducer.cli:main"
    assert data["project"]["dependencies"] == []


def test_real_installed_console_and_module_equivalence(tmp_path):
    case=owned_case(tmp_path,'prompt')
    console=Path(sysconfig.get_path('scripts'))/('agentreducer.exe' if sys.platform=='win32' else 'agentreducer')
    if not console.exists() and (Path(sys.executable).parent/'python311._pth').exists():
        assert invoke_cli(['--version'],cwd=case.caller).stdout.decode().strip()=='agentreducer 0.1.0'
        return  # Explicit floor module proof; active isolated packaging owns console proof.
    assert console.is_file(), 'Required installed console entry is missing'
    for values in (['--help'],['--version'],['prompt','--help'],['instructions','--help'],['repository','--help'],['--format','json','--PRIVATE']):
        a=invoke_cli(values,cwd=case.caller); b=invoke_cli(values,cwd=case.caller,console=console)
        assert (a.returncode,a.stdout,a.stderr)==(b.returncode,b.stdout,b.stderr)
    values=['--format','json']+base_args(case,'prompt')
    a=invoke_cli(values,cwd=case.caller); b=invoke_cli(values,cwd=case.caller,console=console)
    assert a.returncode==b.returncode==0
    left,right=json.loads(a.stdout),json.loads(b.stdout)
    left['cost']['elapsed_seconds']=right['cost']['elapsed_seconds']=0
    assert left==right
