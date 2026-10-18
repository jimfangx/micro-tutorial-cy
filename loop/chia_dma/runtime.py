"""CHIA owns the long-lived build/simulation pools, cache, bypass and profiler."""

from pathlib import Path
import os
import ray
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector
from .config import ROOT, digest
from .artifacts import read_json
from .deployment import chia


def cache_hit(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def from_cache(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError("Cache entry evicted: " + tag)
    return value


def has_proposal(tag, data_path, cfg, run, seed, task):
    return (Path(run) / "candidates" / task["id"] / "proposal.json").exists()


def from_proposal(tag, data_path, cfg, run, seed, task):
    return read_json(Path(run) / "candidates" / task["id"] / "proposal.json")


class Runtime:
    """Attach to the native cluster; own only this experiment's CHIA services."""

    def __init__(self, cfg, run):
        self.cfg, self.run = cfg, Path(run)

    def __enter__(self):
        cfg = self.cfg
        ray.init(
            address=cfg["runtime"]["ray_address"],
            namespace="dma-" + digest(str(self.run))[:24],
            runtime_env={
                "env_vars": {
                    "PYTHONPATH": os.pathsep.join(
                        [
                            "/opt/loop",
                            "/opt/loop/.deps/chia",
                            str(ROOT),
                            cfg["chia"],
                            cfg["skydiscover"],
                            cfg["evolve_flows"],
                        ]
                    )
                }
            },
        )
        missing = {
            "generation",
            "chipyard",
            "verilator_run",
        } - ray.cluster_resources().keys()
        if missing:
            ray.shutdown()
            raise ValueError("Missing native CHIA resources: " + str(sorted(missing)))
        self.cleanup_generation()
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
        bypass.set_cond("generate", has_proposal)
        bypass.set_provider("generate", from_proposal)
        return self

    def cleanup_generation(self):
        for path in (self.run / "deployment").glob("*.yaml"):
            if path.name != "cluster.yaml":
                chia(self.cfg, "down", path)
                path.unlink()

    def __exit__(self, *args):
        stop_collector()
        stop_cache()
        self.cleanup_generation()
        ray.shutdown()
