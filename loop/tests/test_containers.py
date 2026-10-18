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
