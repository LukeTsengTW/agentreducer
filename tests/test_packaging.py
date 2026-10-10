"""Mandatory active-runtime gate: isolated builds, installs and real CLI runs."""

from pathlib import Path,PurePosixPath
from email.parser import BytesParser
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
import pytest

from cli_support import ROOT,safe_environment
from _cli_helpers import owned_case,base_args

pytestmark=pytest.mark.packaging

PUBLIC=('pyproject.toml','MANIFEST.in','README.md','CHANGELOG.md','LICENSE','docs/CLI.md',
    'docs/REPORTS.md','docs/SECURITY.md','docs/releases/v0.1.0.md')


def run(argv,cwd,env):
    p=subprocess.run(list(map(str,argv)),cwd=cwd,env=env,capture_output=True,
        shell=False,timeout=180)
    assert p.returncode==0,(argv,p.stdout.decode(errors='replace')[-3000:],p.stderr.decode(errors='replace')[-3000:])
    return p


def test_wheel_sdist_and_from_sdist_wheel_install_and_run_without_checkout(tmp_path):
    stage=tmp_path/'stage'; stage.mkdir()
    files=list(PUBLIC)
    files += [p.relative_to(ROOT).as_posix() for p in (ROOT/'src/agentreducer').rglob('*')
        if p.is_file() and (p.suffix=='.py' or p.name=='py.typed')]
    files += [p.relative_to(ROOT).as_posix() for p in (ROOT/'examples/minimal_failure').rglob('*')
        if p.is_file() and p.suffix in ('.py','.md')]
    before={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    for name in files:
        destination=stage/name; destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,destination)
    env=safe_environment(tmp_path); env.update(PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1',PYTHONNOUSERSITE='1')
    dist=tmp_path/'dist'
    run([sys.executable,'-m','build','--no-isolation','--outdir',dist],stage,env)
    wheels=list(dist.glob('*.whl')); sdists=list(dist.glob('*.tar.gz'))
    assert len(wheels)==len(sdists)==1
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names=archive.namelist()
        assert {'agentreducer/__main__.py','agentreducer/_version.py','agentreducer/py.typed',
            'agentreducer/cli/__init__.py','agentreducer/reporting/json.py'} <= set(wheel_names)
        metadata=BytesParser().parsebytes(archive.read('agentreducer-0.1.0.dist-info/METADATA'))
        assert metadata['Name']=='agentreducer' and metadata['Version']=='0.1.0'
        assert metadata['Requires-Python']=='>=3.11'
        assert all('extra ==' in requirement for requirement in metadata.get_all('Requires-Dist',[]))
        assert metadata['License-Expression']=='MIT' and metadata.get_all('License-File')==['LICENSE']
        assert archive.read('agentreducer-0.1.0.dist-info/licenses/LICENSE')==(ROOT/'LICENSE').read_bytes()
        assert b'agentreducer = agentreducer.cli:main' in archive.read('agentreducer-0.1.0.dist-info/entry_points.txt')
    extracted=tmp_path/'extracted'; extracted.mkdir()
    with tarfile.open(sdists[0]) as archive:
        members=archive.getmembers(); sdist_names=[x.name for x in members]
        for member in members:
            path=PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts and not member.issym() and not member.islnk()
            assert (extracted/member.name).resolve().is_relative_to(extracted.resolve())
        archive.extractall(extracted,filter='data')
    for name in wheel_names+sdist_names:
        parts=PurePosixPath(name).parts
        assert not set(parts)&{'.git','.venv','tests','superpowers','__pycache__','node_modules'}
        assert not any(p=='.env' or p.startswith('.env.') or p in ('auth.json','credentials.json') for p in parts)
        assert not PurePosixPath(name).is_absolute() and '..' not in parts and '\\' not in name
    unpacked=extracted/'agentreducer-0.1.0'
    for name in ('examples/minimal_failure/run_demo.py','examples/minimal_failure/source/checker.py',*PUBLIC):
        assert (unpacked/name).is_file()
    rebuilt=tmp_path/'rebuilt'
    run([sys.executable,'-m','build','--wheel','--no-isolation','--outdir',rebuilt],unpacked,env)
    second=list(rebuilt.glob('*.whl')); assert len(second)==1
    proofs=[]
    for index,wheel in enumerate((wheels[0],second[0])):
        with zipfile.ZipFile(wheel) as archive:
            metadata=BytesParser().parsebytes(archive.read('agentreducer-0.1.0.dist-info/METADATA'))
            assert metadata['License-Expression']=='MIT' and metadata.get_all('License-File')==['LICENSE']
            assert archive.read('agentreducer-0.1.0.dist-info/licenses/LICENSE')==(ROOT/'LICENSE').read_bytes()
        venv=tmp_path/f'venv-{index}'
        run([sys.executable,'-m','venv',venv],tmp_path,env)
        binary=venv/('Scripts' if sys.platform=='win32' else 'bin')
        python=binary/('python.exe' if sys.platform=='win32' else 'python')
        console=binary/('agentreducer.exe' if sys.platform=='win32' else 'agentreducer')
        run([python,'-m','pip','install','--no-index','--no-deps',wheel],tmp_path,env)
        outside=tmp_path/f'outside-{index}'; outside.mkdir()
        assert run([python,'-I','-m','agentreducer','--version'],outside,env).stdout.decode().strip()=='agentreducer 0.1.0'
        assert run([console,'--version'],outside,env).stdout.decode().strip()=='agentreducer 0.1.0'
        for command in ([python,'-I','-m','agentreducer','--help'],[console,'repository','--help']):
            assert b'usage:' in run(command,outside,env).stdout
        origin=run([python,'-I','-c','import agentreducer; print(agentreducer.__file__)'],outside,env).stdout.decode().strip()
        assert Path(origin).is_relative_to(venv) and not Path(origin).is_relative_to(ROOT)
        case_root=tmp_path/f'case-{index}'; case_root.mkdir(); case=owned_case(case_root,'repository')
        argv=['--format','json']+base_args(case,'repository')
        module=json.loads(run([python,'-I','-m','agentreducer',*argv],outside,env).stdout)
        entry=json.loads(run([console,*argv],outside,env).stdout)
        module['cost']['elapsed_seconds']=entry['cost']['elapsed_seconds']=0
        assert module==entry and module['result']['retained_paths']==['src/auth.py','src/cache.py']
        demo=json.loads(run([python,'-I',unpacked/'examples/minimal_failure/run_demo.py','--format','json'],outside,env).stdout)
        assert demo['result']['retained_paths']==['src/auth.py','src/cache.py']
        proofs.append({'wheel':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'logical':module['cost']['logical_evaluations'],
            'demo_logical':demo['cost']['logical_evaluations'],'origin_isolated':True})
    assert before=={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    print('M7 isolated packaging:',json.dumps({'sdist_sha256':hashlib.sha256(sdists[0].read_bytes()).hexdigest(),'proofs':proofs}))
