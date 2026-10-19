"""Prepare inputs, render native deployment, then attach CHIA services."""

from pathlib import Path
from string import Template
import json
import os
import uuid
import ray
import yaml
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector
from .config import ROOT, ALLOWLIST, digest, frozen
from .artifacts import git, read_json, snapshot, source_bundle, write_json
from .tasks import software


def prepare(cfg, run):
    run.mkdir(parents=True, exist_ok=True)
    if (run / "run.json").exists():
        meta = read_json(run / "run.json")
        if meta["frozen_hash"] != digest(frozen(cfg)):
            raise ValueError("Frozen evaluation/search configuration changed")
    else:
        meta = {
            "run_id": run.name,
            "seed": str(snapshot(cfg)),
            "frozen_hash": digest(frozen(cfg)),
            "loop_revision": git(ROOT, "rev-parse", "HEAD"),
            "loop_dirty": bool(git(ROOT, "status", "--porcelain", "--", ".")),
        }
        write_json(run / "run.json", meta)
    render(cfg, run, meta["seed"])
    return meta


def expand(value, bindings):
    if isinstance(value, str):
        return Template(value).safe_substitute(bindings)
    if isinstance(value, list):
        return [expand(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, bindings) for k, v in value.items()}
    return value


def render(cfg, run, seed):
    """Only expand run paths and separate native generation-worker file binds."""
    backend = cfg["agent"]["backends"][cfg["agent"]["backend"]]
    values = dict(
        LOOP=str(ROOT),
        RUN=str(run),
        SEED=str(seed),
        CONDA=cfg["runtime"]["conda"],
        VENV=str(Path(cfg["python"]).parent.parent),
        CHIA=cfg["chia"],
        SKY=cfg["skydiscover"],
        EVOLVE=cfg["evolve_flows"],
        NAME=Path(run).name,
        UID=str(os.getuid()),
        GID=str(os.getgid()),
        AGENT_IMAGE=backend["image"],
        CREDENTIALS=backend["credentials"],
        CREDENTIAL_TARGET=backend["credential_target"],
    )
    data = expand(cfg["cluster"], values)
    template = data["available_node_types"].pop("generation")
    for index in range(template["num_workers"]):
        work = Path(run) / "work" / f"generation-{index}"
        work.mkdir(parents=True, exist_ok=True)
        for name, text in source_bundle(Path(seed) / "tree").items():
            path = work / Path(name).name
            if not path.exists():
                path.write_text(text)
        node = expand(template, {"INDEX": str(index), "WORK": str(work)})
        node["num_workers"] = 1
        node["docker"]["run_options"] += expand(backend.get("mounts", []), values)
        node["worker_setup_commands"] += expand(
            backend.get("setup_commands", []), values
        )
        data["available_node_types"][f"generation-{index}"] = node
    path = Path(run) / "deployment/cluster.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def cache_hit(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def from_cache(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError("Cache entry evicted: " + tag)
    return value


class Runtime:
    """Attach to an existing native cluster; own cache/profiling, not worker lifecycle."""

    def __init__(self, cfg, run):
        self.cfg, self.run = cfg, Path(run)

    def __enter__(self):
        cfg = self.cfg
        ray.init(
            address=cfg["runtime"]["ray_address"],
            namespace="dma-" + digest(str(self.run))[:24],
        )
        missing = {
            "generation",
            "chipyard",
            "verilator_run",
        } - ray.cluster_resources().keys()
        if missing:
            ray.shutdown()
            raise ValueError("Missing native CHIA resources: " + str(sorted(missing)))
        start_collector(log_dir=str(self.run / "artifacts/profile"))
        start_cache(
            cfg["runtime"]["cache_gb"],
            str(self.run / "cache"),
            units="GB",
            yaml_path=cfg["_config_file"],
        )
        bypass = Bypass(cfg["_config_file"])
        for name in ("build", "simulate"):
            bypass.set_cond(name, cache_hit)
            bypass.set_provider(name, from_cache)
        return self

    def __exit__(self, *args):
        try:
            stop_collector()
            stop_cache()
        finally:
            ray.shutdown()


def authenticate(cfg, state, seed):
    from .adapter import generate

    source = source_bundle(Path(seed) / "tree")
    task = {
        "id": "auth-" + uuid.uuid4().hex,
        "auth": True,
        "parent": {"solution": json.dumps({"source": source})},
        "island": 0,
        "sequence": 0,
    }
    result = get(generate.chia_remote(cfg, state.sql, task))
    if (
        digest(source[ALLOWLIST[0]].encode()) not in result["response"]
        or "/workspace" not in result["response"]
    ):
        raise RuntimeError("CLI authentication/BashTool check failed")


def prepare_software(cfg, run, seed):
    path = run / "software/manifest.json"
    manifest = (
        read_json(path)
        if path.exists()
        else get(software.chia_remote(cfg, str(run), str(seed)))
    )
    for row in manifest["workloads"].values():
        if digest((run / "software" / row["file"]).read_bytes()) != row["sha256"]:
            raise ValueError("Frozen ELF changed")
    return manifest
