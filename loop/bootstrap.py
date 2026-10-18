#!/usr/bin/env python3
"""Install loop-only dependencies using the paths/revision in config.yaml."""
from pathlib import Path
import subprocess
import sys
import venv
from chia_dma.config import ROOT, load

cfg=load()
target=Path(cfg['evolve_flows'])
if not (target/'.git').exists():
    subprocess.run(['git','clone','--recurse-submodules',cfg['dependencies']['evolve_flows_url'],str(target)],check=True)
subprocess.run(['git','-C',str(target),'checkout',cfg['dependencies']['evolve_flows_revision']],check=True)
subprocess.run(['git','-C',str(target),'submodule','update','--init','--recursive'],check=True)
venv.EnvBuilder(system_site_packages=True,with_pip=True).create(Path(cfg['python']).parent.parent)
subprocess.run([cfg['python'],'-m','pip','install','openai>=1','pytest>=7'],check=True)
subprocess.run([cfg['python'],'-m','pip','install','--no-deps','-e',cfg['skydiscover'],'-e',cfg['evolve_flows']],check=True)
print('Ready: python loop/loop.py image, then doctor, then run')
