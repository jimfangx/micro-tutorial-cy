#!/usr/bin/env python3
"""Install loop-only dependencies using the paths/revision in config.yaml."""

from pathlib import Path
import subprocess
import sys
import venv
from chia_dma.config import ROOT, load

cfg = load()
chia = Path(cfg["chia"])
if not (chia / ".git").exists():
    subprocess.run(
        ["git", "clone", cfg["dependencies"]["chia_url"], str(chia)], check=True
    )
subprocess.run(
    ["git", "-C", str(chia), "checkout", cfg["dependencies"]["chia_revision"]],
    check=True,
)
patch = ROOT / "patches/chia-native.patch"
if subprocess.run(
    ["git", "-C", str(chia), "apply", "--reverse", "--check", str(patch)],
    capture_output=True,
).returncode:
    subprocess.run(["git", "-C", str(chia), "apply", str(patch)], check=True)
target = Path(cfg["evolve_flows"])
if not (target / ".git").exists():
    subprocess.run(
        [
            "git",
            "clone",
            "--recurse-submodules",
            cfg["dependencies"]["evolve_flows_url"],
            str(target),
        ],
        check=True,
    )
subprocess.run(
    [
        "git",
        "-C",
        str(target),
        "checkout",
        cfg["dependencies"]["evolve_flows_revision"],
    ],
    check=True,
)
subprocess.run(
    ["git", "-C", str(target), "submodule", "update", "--init", "--recursive"],
    check=True,
)
venv.EnvBuilder(system_site_packages=True, with_pip=True).create(
    Path(cfg["python"]).parent.parent
)
subprocess.run(
    [cfg["python"], "-m", "pip", "install", "openai>=1", "pytest>=7"], check=True
)
subprocess.run(
    [
        cfg["python"],
        "-m",
        "pip",
        "install",
        "--no-deps",
        "-e",
        cfg["chia"],
        "-e",
        cfg["skydiscover"],
        "-e",
        cfg["evolve_flows"],
    ],
    check=True,
)
site = subprocess.check_output(
    [cfg["python"], "-c", 'import sysconfig; print(sysconfig.get_path("purelib"))'],
    text=True,
).strip()
(Path(site) / "chia_tutorial.pth").write_text(
    "import sys; sys.path.insert(0, " + repr(cfg["chia"]) + ")\n"
)
print("Ready: image, prepare, native chia up, then run")
