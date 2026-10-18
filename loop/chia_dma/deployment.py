"""Render native CHIA YAML from the one user configuration; invoke its CLI."""

from pathlib import Path
from string import Template
import os
import subprocess
import yaml
from .config import ROOT, digest


def expand(value, bindings):
    if isinstance(value, str):
        return Template(value).safe_substitute(bindings)
    if isinstance(value, list):
        return [expand(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, bindings) for k, v in value.items()}
    return value


def bindings(cfg, run, seed, **extra):
    backend = cfg["agent"]["backends"][cfg["agent"]["backend"]]
    return dict(
        LOOP=str(ROOT),
        SOURCE=cfg["source"],
        RUN=str(run),
        SEED=str(seed),
        CONDA=cfg["runtime"]["conda"],
        CHIA=cfg["chia"],
        NAME=Path(run).name,
        AGENT=cfg["agent"]["backend"],
        AGENT_BINARY=backend["binary"],
        CREDENTIALS=backend["credentials"],
        CREDENTIAL_TARGET=backend["credential_target"],
        UID=str(os.getuid()),
        GID=str(os.getgid()),
        **extra,
    )


def render(cfg, run, seed, *, request=None, work=None):
    values = bindings(
        cfg,
        run,
        seed,
        CID=request["id"] if request else "",
        WORK=str(work or ""),
        NETWORK="dma-" + digest([str(run), request["id"] if request else ""])[:12],
        BRIDGE="dg" + digest([str(run), request["id"] if request else ""])[:12],
    )
    template = cfg["generation_cluster"] if request else cfg["cluster"]
    data = expand(template, values)
    if request:
        node = data["available_node_types"]["generation"]
        node["docker"]["run_options"] += expand(
            cfg["agent"]["backends"][cfg["agent"]["backend"]]["mounts"], values
        )
        node["worker_setup_commands"] += expand(
            cfg["agent"]["backends"][cfg["agent"]["backend"]].get("setup_commands", []),
            values,
        )
        values["CONTAINER"] = node["docker"]["container_name"] + "-0"
    path = (
        Path(run)
        / "deployment"
        / (request["id"] + ".yaml" if request else "cluster.yaml")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path, values


def chia(cfg, action, path):
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT), cfg["chia"], os.environ.get("PYTHONPATH", "")]
        ),
    }
    try:
        return subprocess.run(
            [cfg["python"], "-m", "chia.cli.main", action, str(path), "-y"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"chia {action} failed: {error.stderr[-5000:]}\n{error.stdout[-2000:]}"
        ) from error
