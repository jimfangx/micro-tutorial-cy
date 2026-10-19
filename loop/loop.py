#!/usr/bin/env python3
"""DMA discovery: prepare, native chia up, run/resume, native chia down."""

from pathlib import Path
import argparse
import asyncio
import json
import os
import sys
import uuid

from chia_dma.config import ROOT, load
from chia_dma.artifacts import RunLock, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "doctor", "run", "resume", "status", "fork", "replay"),
    )
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--run", default="run-001")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--candidate")
    selection.add_argument("--checkpoint")
    parser.add_argument("--new-run", help="New run name or absolute experiment path")
    args = parser.parse_args()
    cfg = load(args.config)
    if os.path.abspath(sys.executable) != cfg["python"]:
        os.execv(
            cfg["python"], [cfg["python"], str(Path(__file__).resolve()), *sys.argv[1:]]
        )
    from chia_dma.runtime import prepare, Runtime, authenticate, prepare_software
    from chia_dma.state import State, fork
    from chia_dma.evaluation import DMAEvaluator
    from chia_dma.search import run_search
    from skydiscover.search.base_database import Program

    for name in (args.run, args.candidate, args.checkpoint):
        if name and (Path(name).name != name or name in (".", "..")):
            parser.error("Use simple run/candidate/checkpoint names")
    run = Path(cfg["storage"]) / args.run
    if args.action == "fork":
        if not args.new_run:
            parser.error("fork requires --new-run")
        fork(
            run,
            args.checkpoint,
            run.parent / Path(args.new_run).expanduser(),
            candidate=args.candidate,
        )
        return
    if args.action == "status":
        cp = run / "checkpoints" / read_json(run / "latest.json")["checkpoint"]
        print(json.dumps(read_json(cp / "state.json"), indent=2))
        return
    if args.action not in ("prepare", "run") and not (run / "run.json").exists():
        parser.error("Prepare a new experiment first")
    if args.action == "replay" and not args.candidate:
        parser.error("replay requires --candidate")
    run.mkdir(parents=True, exist_ok=True)
    with RunLock(run):
        meta = prepare(cfg, run)
        if args.action == "prepare":
            print(run / "deployment/cluster.yaml")
            return
        with Runtime(cfg, run):
            state = State(cfg, run)
            evaluator = None
            try:
                if args.action == "doctor":
                    authenticate(cfg, state, meta["seed"])
                    print("CLI authentication and BashTool passed")
                    return
                requests = [r for r in state.requests() if r["kind"] == "search"]
                needs_llm = len(requests) < cfg["search"]["iterations"] or any(
                    state.proposal(r["id"]) is None for r in state.recovery
                )
                if (
                    args.action != "replay"
                    and needs_llm
                    and cfg["agent"]["authenticate_before_run"]
                ):
                    authenticate(cfg, state, meta["seed"])
                prepare_software(cfg, run, meta["seed"])
                evaluator = DMAEvaluator(cfg, state, meta["seed"])
                if args.action == "replay":
                    old = state.proposal(args.candidate)
                    if old is None:
                        parser.error("No completed proposal for this candidate")
                    parent = Program(
                        id=args.candidate,
                        solution=json.dumps(
                            {"proposal_id": args.candidate, "source": old["source"]}
                        ),
                        metadata={
                            "island": state.request(args.candidate)["island"],
                            "candidate_id": args.candidate,
                        },
                    )
                    request = state.reserve(
                        parent, [], cid="replay-" + uuid.uuid4().hex, kind="replay"
                    )
                    state.complete(
                        request["id"],
                        old["source"],
                        {
                            "backend": old["backend"],
                            "model": old["model"],
                            "replay_of": args.candidate,
                        },
                    )
                    result = asyncio.run(
                        evaluator.evaluate_program(
                            json.dumps({"proposal_id": request["id"], "replay": True})
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
