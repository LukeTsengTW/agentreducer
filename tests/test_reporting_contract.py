import hashlib
from pathlib import Path

from agentreducer.prompt import PromptBlock,PromptCandidate,PromptReductionResult
from agentreducer.reporting.models import CostSnapshot
from agentreducer.reporting.convert import success_report,reduced_bytes
from agentreducer.repository import build_repository_manifest,RepositoryReductionResult


def test_schema_requires_rendered_identity_and_coded_limitations():
    a=PromptCandidate((PromptBlock('字'),)); r=success_report('prompt',PromptReductionResult(a,a,1),experiment={},cost=CostSnapshot())
    assert r.result['original_sha256']==hashlib.sha256('字'.encode()).hexdigest()
    assert r.result['reduced_sha256']==r.result['original_sha256']
    assert all(set(x)=={'code','message'} for x in r.limitations)
    assert any(x['code']=='OBSERVED_MINIMALITY' for x in r.limitations)


def test_repository_schema_retained_paths_and_pins_are_separate(tmp_path):
    (tmp_path/'check.py').write_bytes(b'pin'); (tmp_path/'bug').write_bytes(b'BUG')
    m=build_repository_manifest(tmp_path,pinned_paths=('check.py',))
    c=m.candidate(); core=RepositoryReductionResult(c,c,1)
    r=success_report('repository',core,experiment={},cost=CostSnapshot())
    assert r.result['retained_paths']==('bug',)
    assert r.result['pinned_paths']==('check.py',)
    assert reduced_bytes('repository',core)==b'bug\n'
    assert reduced_bytes('repository',RepositoryReductionResult(c,m.candidate(()),1))==b''


def test_public_presentation_exports_render_without_oracle():
    from agentreducer.reporting import render_json,render_human,success_report,CostSnapshot
    c=PromptCandidate((PromptBlock('private text'),))
    report=success_report('prompt',PromptReductionResult(c,c,1),experiment={},cost=CostSnapshot())
    assert 'private text' not in render_json(report)+render_human(report)
