#!/usr/bin/env python3
"""Install loop-only dependencies pinned in backend.yaml."""

# Setup steps: (1) clone/check out pinned CHIA; (2) clone/check out evolve-flows
# and initialize its pinned SkyDiscover submodule; (3) create a loop-local venv
# inheriting the activated Chipyard/Ray toolchain; (4) install the OpenAI client
# used by SkyDiscover and pytest; (5) install CHIA, SkyDiscover, and evolve-flows
# editable without replacing toolchain dependencies; (6) prefer this CHIA checkout
# over inherited editable installs. Native `chia up` pulls the pinned upstream
# worker images; there is no custom image build or dependency patch.

from pathlib import Path
import subprocess
import venv
from chia_dma.config import load

cfg = load()
for name in ("chia", "evolve_flows"):
    target = Path(cfg[name])
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "clone", cfg["dependencies"][name + "_url"], str(target)],
            check=True,
        )
    subprocess.run(
        ["git", "-C", str(target), "checkout", cfg["dependencies"][name + "_revision"]],
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
print("Ready: prepare, native chia up, then run")
