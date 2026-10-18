"""One configuration entry point, shared by the CLI and every worker."""

from pathlib import Path
import copy
import hashlib
import json
import os
import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = (
    "generators/chipyard/src/main/scala/dma/DMAController.scala",
    "generators/chipyard/src/main/scala/dma/RoCCDMA.scala",
)


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True).encode()
    )
    return hashlib.sha256(data).hexdigest()


def load(path=ROOT / "config.yaml"):
    path = Path(path).resolve()
    cfg = yaml.safe_load(os.path.expandvars(path.read_text()))
    for key in ("source", "storage", "chia", "skydiscover", "evolve_flows"):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    cfg["python"] = os.path.abspath(path.parent / cfg["python"])
    cfg["runtime"]["conda"] = str((path.parent / cfg["runtime"]["conda"]).resolve())
    for backend in cfg["agent"]["backends"].values():
        for key in ("binary", "credentials"):
            backend[key] = str((path.parent / backend[key]).resolve())
        backend["extra_binaries"] = {
            name: str((path.parent / binary).resolve())
            for name, binary in backend.get("extra_binaries", {}).items()
        }
    if tuple(cfg["hardware"]["allowlist"]) != ALLOWLIST:
        raise ValueError(
            "The DMA implementation allowlist is fixed; establish a new integration to expand it"
        )
    if cfg["agent"]["backend"] not in cfg["agent"]["backends"]:
        raise ValueError("Unknown agent backend")
    if cfg["search"]["num_islands"] < 2:
        raise ValueError("At least two islands are required")
    if cfg["search"]["type"] != "openevolve_native":
        raise ValueError("This tutorial uses SkyDiscover openevolve_native")
    cfg["_config_file"] = str(path)
    return cfg


def frozen(cfg):
    """Backend/model may change on resume; evaluation and search rules may not."""
    data = copy.deepcopy(cfg)
    for key in (
        "agent",
        "cache",
        "bypass",
        "_config_file",
        "python",
        "chia",
        "skydiscover",
        "evolve_flows",
        "storage",
        "source",
        "cluster",
        "generation_cluster",
        "generation_ray",
        "generation_exec",
        "image_build",
        "image_inspect",
    ):
        data.pop(key, None)
    data["runtime"] = {
        k: v for k, v in data["runtime"].items() if k in ("image", "environment")
    }
    data["worker_contract"] = {
        name: {
            "image": node["docker"]["image"],
            "mounts_environment": node["docker"]["run_options"],
            "environment": node.get("worker_env_commands", []),
            "setup": node.get("worker_setup_commands", []),
        }
        for name, node in cfg["cluster"]["available_node_types"].items()
    }
    data["search"].pop("iterations", None)
    data["search"].pop("max_in_flight", None)
    return data
