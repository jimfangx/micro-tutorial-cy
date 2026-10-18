"""Actual Docker boundary checks, separate from paid provider generation."""
from pathlib import Path
import tempfile
import pytest
from chia_dma.config import ALLOWLIST, load
from chia_dma.containers import run_worker


def test_agent_mounts_reject_frozen_source_archive_and_original():
    cfg=load()
    Path(cfg['storage'],'tmp').mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=Path(cfg['storage'])/'tmp') as folder:
        folder=Path(folder)
        tree=folder/'seed'
        for name in ('build.sbt','software/rocc-dma/memcpy_wrap.c',*ALLOWLIST):
            p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('frozen')
        writable=folder/'candidate.scala';writable.write_text('candidate')
        mounts=[(str(tree),'/workspace','ro'),(str(writable),'/workspace/'+ALLOWLIST[0],'rw')]
        result=run_worker(cfg,'isolation',{'cfg':cfg,'frozen_paths':['build.sbt','software/rocc-dma/memcpy_wrap.c',ALLOWLIST[1]],'allowed_paths':[ALLOWLIST[0]]},mounts,agent=True)
        assert set(result['denied'])=={'build.sbt','software/rocc-dma/memcpy_wrap.c',ALLOWLIST[1]}
        assert not result['original_visible']
        assert not result['socket_visible']
        assert writable.read_text()=='candidate\n// writable candidate\n'
        assert (tree/ALLOWLIST[0]).read_text()=='frozen'


def test_restart_cleanup_only_reaps_its_own_run():
    from chia_dma.config import digest
    from chia_dma.containers import cleanup_run, docker
    import uuid
    cfg=load()
    names=['recovery-'+uuid.uuid4().hex,'unrelated-'+uuid.uuid4().hex]
    ids=[]
    try:
        for name in names:
            token=digest([cfg['storage'],name])[:24]
            ids.append(docker(cfg,['run','-d','--label','chia.dma.run='+token,
                                   cfg['runtime']['image'],'sleep','infinity'],capture_output=True).stdout.strip())
        cleanup_run(cfg,names[0])
        assert not docker(cfg,['ps','-aq','--filter','id='+ids[0]],capture_output=True).stdout.strip()
        assert docker(cfg,['ps','-q','--filter','id='+ids[1]],capture_output=True).stdout.strip()==ids[1][:12]
    finally:
        for name in names:cleanup_run(cfg,name)
