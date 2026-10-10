from pathlib import Path
import os
import pytest

from agentreducer.reporting.output import prepare_output, publish, OutputFailure


def plan(tmp_path,force=False):
    source=tmp_path/'source'; source.mkdir(exist_ok=True)
    return prepare_output(tmp_path/'report.json',force=force,kind='report',source=source,protected_inputs=())


def test_atomic_no_clobber_and_explicit_force(tmp_path):
    p=plan(tmp_path); publish(p,b'complete\n')
    assert p.path.read_bytes()==b'complete\n'
    with pytest.raises(OutputFailure): publish(p,b'overwrite')
    assert p.path.read_bytes()==b'complete\n'
    publish(plan(tmp_path,True),b'replacement')
    assert p.path.read_bytes()==b'replacement'
    assert not list(tmp_path.glob('.agentreducer-output-*'))


def test_late_destination_race_never_overwrites(tmp_path,monkeypatch):
    p=plan(tmp_path); real=os.link
    def race(src,dst,**kwargs):
        Path(dst).write_bytes(b'concurrent')
        return real(src,dst,**kwargs)
    monkeypatch.setattr(os,'link',race)
    with pytest.raises(OutputFailure) as got: publish(p,b'complete\n')
    assert not got.value.published and p.path.read_bytes()==b'concurrent'
    assert not list(tmp_path.glob('.agentreducer-output-*'))


def test_unsupported_link_fails_closed_without_replace(tmp_path,monkeypatch):
    p=plan(tmp_path)
    def unsupported(*a,**k): raise OSError('PRIVATE')
    monkeypatch.setattr(os,'link',unsupported)
    monkeypatch.setattr(os,'replace',lambda *a:pytest.fail('replace fallback'))
    with pytest.raises(OutputFailure) as got: publish(p,b'complete')
    assert not p.path.exists() and 'PRIVATE' not in str(got.value)


def test_fsync_failure_does_not_publish(tmp_path,monkeypatch):
    p=plan(tmp_path)
    def fail(*a): raise OSError('PRIVATE')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(OutputFailure): publish(p,b'complete')
    assert not p.path.exists() and not list(tmp_path.glob('.agentreducer-output-*'))


@pytest.mark.parametrize('exception',[SystemExit('PRIVATE'),KeyboardInterrupt()])
def test_primary_cancellation_survives_cleanup_failure(tmp_path,monkeypatch,exception):
    p=plan(tmp_path)
    def fail(*a): raise exception
    real_unlink=Path.unlink
    def unlink(path,*a,**k):
        if path.name.startswith('.agentreducer-output-'): raise OSError('cleanup')
        return real_unlink(path,*a,**k)
    monkeypatch.setattr(os,'fsync',fail); monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(type(exception)) as got: publish(p,b'complete')
    assert got.value is exception and not p.path.exists()


def test_published_output_survives_owned_temp_cleanup_failure(tmp_path,monkeypatch):
    p=plan(tmp_path); real=Path.unlink
    def unlink(path,*a,**k):
        if path.name.startswith('.agentreducer-output-'): raise OSError('cleanup')
        return real(path,*a,**k)
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(OutputFailure) as got: publish(p,b'complete')
    assert got.value.published and p.path.read_bytes()==b'complete'


def test_force_revalidates_hardlink_input_alias(tmp_path):
    p=plan(tmp_path,True); inp=tmp_path/'input'; inp.write_text('private')
    p=prepare_output(p.path,force=True,kind='report',source=p.source,protected_inputs=(inp,))
    p.path.hardlink_to(inp)
    with pytest.raises(OutputFailure): publish(p,b'bad')
    assert inp.read_text()=='private'


@pytest.mark.parametrize('failure',['write','flush','close'])
def test_stream_failure_never_exposes_incomplete_destination(tmp_path,monkeypatch,failure):
    p=plan(tmp_path); real=os.fdopen
    class Stream:
        def __init__(self,fd,mode): self.stream=real(fd,mode)
        def __enter__(self): return self
        def __exit__(self,*a):
            self.close()
        def close(self):
            self.stream.close()
            if failure=='close': raise OSError('PRIVATE')
        def write(self,value):
            if failure=='write': raise OSError('PRIVATE')
            return self.stream.write(value)
        def flush(self):
            if failure=='flush': raise OSError('PRIVATE')
            self.stream.flush()
        def fileno(self): return self.stream.fileno()
    monkeypatch.setattr(os,'fdopen',Stream)
    with pytest.raises(OutputFailure): publish(p,b'complete')
    assert not p.path.exists() and not list(tmp_path.glob('.agentreducer-output-*'))


def test_reparse_destination_is_rejected_before_staging(tmp_path,monkeypatch):
    from types import SimpleNamespace
    p=plan(tmp_path); original=Path.lstat
    def lstat(path):
        if path==p.path: return SimpleNamespace(st_mode=0o100600,st_file_attributes=0x400)
        return original(path)
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(OutputFailure): publish(p,b'bad')
    assert not list(tmp_path.glob('.agentreducer-output-*'))


@pytest.mark.parametrize('signal',[KeyboardInterrupt(),SystemExit(19)])
def test_primary_cancel_survives_stream_close_failure(tmp_path,monkeypatch,signal):
    p=plan(tmp_path); original=os.fdopen
    class Stream:
        def __init__(self,fd,mode): self.stream=original(fd,mode)
        def __enter__(self): return self
        def __exit__(self,*a): self.close()
        def write(self,value): raise signal
        def close(self):
            self.stream.close()
            raise OSError('secondary private close failure')
    monkeypatch.setattr(os,'fdopen',Stream)
    with pytest.raises(type(signal)) as got: publish(p,b'complete')
    assert got.value is signal
    assert not p.path.exists() and not list(tmp_path.glob('.agentreducer-output-*'))


@pytest.mark.parametrize('signal',[KeyboardInterrupt(),SystemExit(19)])
def test_cancellation_originating_in_post_publication_cleanup_escapes_unchanged(tmp_path,monkeypatch,signal):
    p=plan(tmp_path); original=Path.unlink
    def unlink(path,*a,**k):
        if path.name.startswith('.agentreducer-output-'): raise signal
        return original(path,*a,**k)
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(type(signal)) as got: publish(p,b'complete')
    assert got.value is signal and p.path.read_bytes()==b'complete'
