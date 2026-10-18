"""Preserve a seed and configure an experiment before native chia up."""

from pathlib import Path
import json
import subprocess
import uuid
from chia.base.ChiaFunction import get
from .artifacts import git, read_json, snapshot, source_bundle, write_json
from .config import ROOT, ALLOWLIST, digest, frozen
from .deployment import render
from .tasks import software


def image_ids(cfg):
    nodes = list(cfg["cluster"]["available_node_types"].values()) + list(
        cfg["generation_cluster"]["available_node_types"].values()
    )
    images = {n["docker"]["image"] for n in nodes}
    return {
        image: subprocess.check_output(
            [*cfg["image_inspect"], image], text=True
        ).strip()
        for image in images
    }


def prepare(cfg, run):
    run.mkdir(parents=True, exist_ok=True)
    images = image_ids(cfg)
    if (run / "run.json").exists():
        meta = read_json(run / "run.json")
        if meta.get("format") != 3:
            raise ValueError(
                "Checkpoint format predates the native controller; use its previous loop revision or start a new run"
            )
        if meta["frozen_hash"] != digest(frozen(cfg)):
            raise ValueError("Frozen evaluation/search configuration changed")
        if meta["image_ids"] != images:
            raise ValueError("Worker image identity changed")
    else:
        seed = snapshot(cfg)
        meta = {
            "image_ids": images,
            "format": 3,
            "run_id": run.name,
            "seed": str(seed),
            "frozen_hash": digest(frozen(cfg)),
            "loop_revision": git(ROOT, "rev-parse", "HEAD"),
            "loop_dirty": bool(git(ROOT, "status", "--porcelain", "--", ".")),
        }
        write_json(run / "run.json", meta)
    render(cfg, run, meta["seed"])
    return meta


def authenticate(cfg, run, seed):
    from .adapter import generate

    task = {
        "id": "auth-" + uuid.uuid4().hex,
        "auth": True,
        "parent": {
            "solution": json.dumps({"source": source_bundle(Path(seed) / "tree")})
        },
        "island": 0,
        "sequence": 0,
    }
    result = get(generate.chia_remote(cfg, str(run), str(seed), task))
    expected = digest(source_bundle(Path(seed) / "tree")[ALLOWLIST[0]].encode())
    if expected not in result["response"] or "/workspace" not in result["response"]:
        raise RuntimeError("CLI authentication/tool probe failed")


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
