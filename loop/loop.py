#!/usr/bin/env python3
"""DMA tutorial: native CHIA workers and SkyDiscover's full discovery controller."""

from pathlib import Path
import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chia_dma.config import ROOT, load
from chia_dma.artifacts import RunLock, archive_proposal, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "image",
            "prepare",
            "init",
            "up",
            "down",
            "doctor",
            "run",
            "resume",
            "status",
            "fork",
            "replay",
        ),
    )
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--run", default="run-001")
    parser.add_argument("--candidate")
    parser.add_argument("--checkpoint")
    parser.add_argument("--new-run")
    args = parser.parse_args()
    cfg = load(args.config)
    if os.path.abspath(sys.executable) != cfg["python"]:
        os.execv(
            cfg["python"], [cfg["python"], str(Path(__file__).resolve()), *sys.argv[1:]]
        )
    for key in ("chia", "skydiscover", "evolve_flows"):
        sys.path.insert(0, cfg[key])
    from chia_dma.deployment import bindings, expand, chia
    from chia_dma.setup import prepare, authenticate, prepare_software
    from chia_dma.state import State, fork
    from chia_dma.runtime import Runtime
    from chia_dma.evaluation import DMAEvaluator
    from chia_dma.search import run_search

    for name in (args.run, args.new_run, args.candidate, args.checkpoint):
        if name and (Path(name).name != name or name in (".", "..")):
            parser.error("Use simple run/candidate/checkpoint names")
    run = Path(cfg["storage"]) / args.run
    if args.action == "image":
        subprocess.run(expand(cfg["image_build"], bindings(cfg, run, "")), check=True)
        return
    if args.action == "fork":
        if not args.new_run:
            parser.error("fork requires --new-run")
        fork(
            run,
            args.checkpoint or read_json(run / "latest.json")["checkpoint"],
            run.parent / args.new_run,
        )
        return
    if args.action == "status":
        print(
            json.dumps(
                read_json(
                    run
                    / "checkpoints"
                    / read_json(run / "latest.json")["checkpoint"]
                    / "state.json"
                ),
                indent=2,
            )
        )
        return
    if args.action == "down":
        Runtime(cfg, run).cleanup_generation()
        result = chia(cfg, "down", run / "deployment/cluster.yaml")
        print(result.stdout, result.stderr)
        return
    if (
        args.action not in ("prepare", "init", "run", "up")
        and not (run / "run.json").exists()
    ):
        parser.error("Experiment does not exist; prepare a new run first")
    run.mkdir(parents=True, exist_ok=True)
    with RunLock(run):
        meta = prepare(cfg, run)
        if args.action in ("prepare", "init"):
            print(run / "deployment/cluster.yaml")
            return
        if args.action in ("up", "down"):
            result = chia(cfg, args.action, run / "deployment/cluster.yaml")
            print(result.stdout, result.stderr)
            return
        with Runtime(cfg, run):
            if args.action == "doctor":
                authenticate(cfg, run, meta["seed"])
                print("CLI authentication and BashTool passed")
                return
            state = State(cfg, run)
            evaluator = None
            try:
                requests = [r for r in state.requests() if r["kind"] == "search"]
                needs_llm = len(requests) < cfg["search"]["iterations"] or any(
                    not (run / "candidates" / r["id"] / "proposal.json").exists()
                    for r in state.recovery
                )
                if (
                    args.action != "replay"
                    and needs_llm
                    and cfg["agent"]["authenticate_before_run"]
                ):
                    authenticate(cfg, run, meta["seed"])
                prepare_software(cfg, run, meta["seed"])
                evaluator = DMAEvaluator(cfg, state, meta["seed"])
                if args.action == "replay":
                    if not args.candidate:
                        parser.error("replay requires --candidate")
                    old = read_json(
                        run / "candidates" / args.candidate / "proposal.json"
                    )
                    parent = state.request(args.candidate)["parent"]
                    from skydiscover.search.base_database import Program

                    request = state.reserve(
                        Program(**parent),
                        [],
                        cid="replay-" + uuid.uuid4().hex,
                        kind="replay",
                    )
                    archive_proposal(
                        run,
                        request["id"],
                        old["source"],
                        old["source"],
                        {"backend": old.get("backend"), "replay_of": args.candidate},
                    )
                    result = asyncio.run(
                        evaluator.evaluate_program(
                            json.dumps(
                                {
                                    "proposal_id": request["id"],
                                    "source": old["source"],
                                    "replay": True,
                                }
                            )
                        )
                    )
                    print(result.metrics)
                else:
                    best = asyncio.run(run_search(cfg, state, evaluator, meta["seed"]))
                    print("Completed; best=" + str(best.id if best else None))
            finally:
                if evaluator:
                    evaluator.close()
                state.close()


if __name__ == "__main__":
    main()
