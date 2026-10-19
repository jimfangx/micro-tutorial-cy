"""Tutorial controls layered over fixed backend settings."""

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


# Computes stable SHA-256 identifiers for source/configuration and cache keys.
def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True).encode()
    )
    return hashlib.sha256(data).hexdigest()


# Loads YAML, expands environment variables, resolves paths, and validates settings.
def load(path=ROOT / "config.yaml"):
    path = Path(path).resolve()
    cpus = str(len(os.sched_getaffinity(0)))
    cfg, cluster = [
        yaml.safe_load(os.path.expandvars(p.read_text().replace("${CPUS}", cpus)))
        for p in (ROOT / "backend.yaml", path)
    ]
    settings = {key: cluster.pop(key) for key in ("agent", "search", "runtime")}
    merge(cfg, dict(**settings, cluster=cluster))
    for key in ("source", "storage", "chia", "skydiscover", "evolve_flows"):
        cfg[key] = str((ROOT / cfg[key]).resolve())
    cfg["python"] = os.path.abspath(ROOT / cfg["python"])
    cfg["runtime"]["conda"] = str((ROOT / cfg["runtime"]["conda"]).resolve())
    for backend in cfg["agent"]["backends"].values():
        backend["credentials"] = str((ROOT / backend["credentials"]).resolve())
    generation = cfg["cluster"]["available_node_types"]["generation"]
    if generation["resources"] != {"generation": 1} or generation["num_workers"] < 1:
        raise ValueError(
            "Each generation workspace needs one serial generation slot; increase num_workers for concurrency"
        )
    if (
        cfg["cluster"]["available_node_types"]["build"]["resources"].get("chipyard")
        != 1
    ):
        raise ValueError(
            "Each private build checkout requires one serial chipyard slot; increase num_workers for parallel builds"
        )
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
    cfg["_config_file"] = str(ROOT / "backend.yaml")
    return cfg


def merge(base, updates):
    for key, value in updates.items():
        if isinstance(value, dict):
            merge(base.setdefault(key, {}), value)
        else:
            base[key] = value


# Selects settings that must remain unchanged when resuming an experiment.
def frozen(cfg):
    """Backend/model may change on resume; evaluation and search rules may not."""
    data = {
        key: copy.deepcopy(cfg[key])
        for key in (
            "dependencies",
            "hardware",
            "software",
            "fitness",
            "search",
            "preserve",
        )
    }
    data["worker_contract"] = {
        name: {
            "image": node["docker"]["image"],
            "mounts_environment": node["docker"]["run_options"],
            "environment": node.get("worker_env_commands", []),
            "setup": node.get("worker_setup_commands", []),
        }
        for name, node in cfg["cluster"]["available_node_types"].items()
        if name != "generation"
    }
    data["search"].pop("iterations", None)
    data["search"].pop("max_in_flight", None)
    return data
