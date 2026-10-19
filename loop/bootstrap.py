#!/usr/bin/env python3
"""Install pinned Python integrations inside the published controller container."""

from pathlib import Path
import subprocess
import sys
from chia_dma.config import load

cfg = load()
git = ["git", "-c", "url.https://github.com/.insteadOf=git@github.com:"]
for name in ("chia", "evolve_flows"):
    target = Path(cfg[name])
    if not (target / ".git").exists():
        subprocess.run(
            git + ["clone", cfg["dependencies"][name + "_url"], str(target)],
            check=True,
        )
    subprocess.run(
        git + ["-C", str(target), "checkout", cfg["dependencies"][name + "_revision"]],
        check=True,
    )
subprocess.run(
    git + ["-C", str(target), "submodule", "update", "--init", "--recursive"],
    check=True,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        "/opt/dma-python",
        *cfg["dependencies"]["python_packages"],
    ],
    check=True,
)
print("Ready: prepare, native chia up, then run/resume inside this container")
