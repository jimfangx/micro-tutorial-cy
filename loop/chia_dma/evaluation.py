"""Build one simulator, run each ELF independently, interpret DMA measurements."""

import asyncio
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import json
import threading
import time
import ray
from chia.base.ChiaFunction import get, chia_cancel
from skydiscover.evaluation.evaluation_result import EvaluationResult
from chia_dma import tasks
from chia_dma.artifacts import read_json, write_json
from chia_dma.config import digest, frozen
from chia_dma.metrics import aggregate, parse


class DMAEvaluator:
    def __init__(self, cfg, state, seed):
        self.cfg, self.state, self.seed = cfg, state, str(seed)
        self.stopped = threading.Event()
        self.tree = Path(seed) / "tree"
        self.inventory = read_json(state.run / "software/manifest.json")["workloads"]
        self.ini = {
            p.name: p.read_bytes()
            for p in (self.tree / cfg["software"]["dram_ini"]).glob("*.ini")
        }
        self.prefix = digest([Path(seed).name, frozen(cfg)])

    async def evaluate_program(self, solution, program_id=""):
        try:
            return await asyncio.to_thread(self.evaluate, solution, program_id)
        except asyncio.CancelledError:
            self.stopped.set()
            raise

    def collect(self, handles):
        """Keep CHIA callback handles for get; wait/cancel their raw Ray refs."""
        pending = {
            getattr(handle, "ref", handle): (name, handle)
            for name, handle in handles.items()
        }
        deadline = time.monotonic() + self.cfg["runtime"]["task_timeout_seconds"]
        try:
            while pending:
                if self.stopped.is_set():
                    raise InterruptedError("Evaluation interrupted")
                if time.monotonic() >= deadline:
                    raise TimeoutError("CHIA task deadline")
                ready, _ = ray.wait(
                    list(pending),
                    num_returns=1,
                    timeout=min(1, max(0, deadline - time.monotonic())),
                )
                for ref in ready:
                    name, handle = pending.pop(ref)
                    yield name, get(handle)
        finally:
            for ref in pending:
                chia_cancel(ref, force=True)

    def evaluate(self, solution, program_id=""):
        payload = json.loads(solution)
        cid = payload["proposal_id"]
        recorded = self.state.result(cid)
        if recorded is not None:
            return EvaluationResult(metrics=recorded)
        saved = self.state.proposal(cid)
        source = saved["source"]
        key = cid if payload.get("replay") else saved["source_hash"]
        tag = f"{self.prefix}/{key}"
        out = self.state.run / "artifacts" / cid
        out.mkdir(parents=True, exist_ok=True)
        records = {}
        try:
            # RTL edits stay in a build worker; simulation receives only artifacts.
            handle = tasks.build.chia_remote(
                self.cfg, self.seed, source, _chia_tag=tag + "/build"
            )
            artifact = dict(self.collect({"build": handle}))["build"]
            (out / "build.log").write_text(artifact.stdout + "\n" + artifact.stderr)
            if not artifact.success:
                raise ValueError("Simulator build failed")
            artifact = ray.put(artifact)
            handles = {}
            try:
                for name, spec in self.inventory.items():
                    if name.startswith("embench:cpu:") and cid != "seed-dma":
                        continue
                    elf = (self.state.run / "software" / spec["file"]).read_bytes()
                    handles[name] = tasks.simulate.chia_remote(
                        self.cfg,
                        artifact,
                        elf,
                        name,
                        self.ini,
                        _chia_tag=tag + "/" + name,
                    )
            except BaseException:
                for handle in handles.values():
                    chia_cancel(getattr(handle, "ref", handle), force=True)
                raise
            with closing(self.collect(handles)) as results:
                for name, result in results:
                    (out / (name.replace(":", "_") + ".log")).write_text(
                        result.log + "\n" + result.out
                    )
                    records[name] = parse(asdict(result), self.cfg, self.tree)
            reference = (
                records
                if cid == "seed-dma"
                else read_json(self.state.run / "reference.json")
            )
            if cid == "seed-dma":
                for name, row in records.items():
                    if name.startswith("embench:cpu:"):
                        dma = records[name.replace(":cpu:", ":dma:")]
                        if row["offloaded"] or any(
                            row[k] != dma[k]
                            for k in ("benchmark_result", "verification_result")
                        ):
                            raise ValueError("CPU/DMA outputs disagree")
            metrics, vector = aggregate(records, reference, self.cfg, source)
            if cid == "seed-dma":
                write_json(self.state.run / "reference.json", records)
        except ValueError as error:
            metrics, vector = {"validity": 0, "combined_score": 0.0}, {
                "error": str(error)
            }
        self.state.record(cid, metrics, {**records, **vector}, program_id)
        return EvaluationResult(metrics=metrics, artifacts={"proposal_id": cid})

    def close(self):
        self.stopped.set()


def evaluate(program_path):
    """File-interface placeholder replaced through the controller's public hook."""
    raise RuntimeError("Initialize the DMA experiment through loop.py")
