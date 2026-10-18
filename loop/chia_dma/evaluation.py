"""DMA evaluation adapter for SkyDiscover, using ordinary CHIA collection."""

import asyncio
from dataclasses import asdict
from pathlib import Path
import json
import threading
import time
import ray
from chia.base.ChiaFunction import get, chia_wait, chia_cancel, TrackedRef
from skydiscover.evaluation.evaluation_result import EvaluationResult
from chia_dma import tasks
from chia_dma.artifacts import read_json, write_json
from chia_dma.config import digest, frozen
from chia_dma.metrics import aggregate, parse


class DMAEvaluator:
    def __init__(self, cfg, state, seed):
        self.cfg, self.state, self.seed = cfg, state, str(seed)
        self.stopped = threading.Event()
        self.tree = str(Path(seed) / "tree")
        self.inventory = read_json(state.run / "software/manifest.json")["workloads"]
        self.ini = {
            p.name: p.read_bytes()
            for p in (Path(self.tree) / cfg["software"]["dram_ini"]).glob("*.ini")
        }
        self.prefix = digest([Path(seed).name, frozen(cfg)])

    async def evaluate_program(self, solution, program_id=""):
        try:
            return await asyncio.to_thread(self.evaluate, solution, program_id)
        except asyncio.CancelledError:
            self.stopped.set()
            raise

    def evaluate(self, solution, program_id=""):
        payload = json.loads(solution)
        cid = payload["proposal_id"]
        # Numerical history repairs the evaluation-complete/admission-interrupted
        # window. It does not replace CHIA's build/simulation output cache.
        recorded = self.state.result(cid)
        if recorded is not None:
            return EvaluationResult(metrics=recorded)
        refs = []
        handles = {}

        def submit(key, fn, *args):
            tag = f'{self.prefix}/{cid if payload.get("replay") else digest(payload["source"])}/{key}'
            slot = {}

            def send():
                slot["handle"] = fn.chia_remote(*args, _chia_tag=tag)
                return getattr(slot["handle"], "ref", slot["handle"])

            ref = TrackedRef(send(), submit_fn=send, label=cid + "/" + key)
            refs.append(ref)
            handles[id(ref)] = (key, slot)

        records = {}
        artifact = None
        try:
            submit("build", tasks.build, self.cfg, self.seed, payload["source"])
            while refs:
                if self.stopped.is_set():
                    raise InterruptedError("Evaluation interrupted")
                ready, refs = chia_wait(
                    refs,
                    num_returns=1,
                    timeout=30,
                    pending_timeout=self.cfg["runtime"]["pending_timeout_seconds"],
                    retry=True,
                )
                if any(
                    time.monotonic() - r.submitted_at
                    > self.cfg["runtime"]["task_timeout_seconds"]
                    for r in refs
                ):
                    raise TimeoutError("CHIA task deadline")
                for ref in ready:
                    key, slot = handles.pop(id(ref))
                    value = get(slot["handle"])
                    out = self.state.run / "artifacts" / cid
                    out.mkdir(parents=True, exist_ok=True)
                    if key == "build":
                        (out / "build.log").write_text(
                            value.stdout + "\n" + value.stderr
                        )
                        if not value.success:
                            raise ValueError("Build/controller tests failed")
                        artifact = ray.put(value)
                        for name, spec in self.inventory.items():
                            if name.startswith("embench:cpu:") and cid != "seed-dma":
                                continue
                            elf = (
                                self.state.run / "software" / spec["file"]
                            ).read_bytes()
                            if digest(elf) != spec["sha256"]:
                                raise RuntimeError("Frozen ELF changed")
                            submit(
                                name,
                                tasks.simulate,
                                self.cfg,
                                artifact,
                                elf,
                                name,
                                self.ini,
                            )
                    else:
                        (out / (key.replace(":", "_") + ".log")).write_text(
                            value.log + "\n" + value.out
                        )
                        records[key] = parse(key, asdict(value), self.cfg, self.tree)
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
            metrics, vector = aggregate(records, reference, self.cfg, payload["source"])
            if cid == "seed-dma":
                write_json(self.state.run / "reference.json", records)
        except ValueError as error:
            metrics, vector = {"validity": 0, "combined_score": 0.0}, {
                "error": str(error)
            }
        finally:
            for ref in refs:
                chia_cancel(ref.ref, force=True)
        self.state.record(cid, metrics, {**records, **vector}, program_id)
        return EvaluationResult(metrics=metrics, artifacts={"proposal_id": cid})

    def close(self):
        self.stopped.set()


def evaluate(program_path):
    """The controller replaces this file-interface evaluator with DMAEvaluator."""
    raise RuntimeError("Initialize the DMA experiment through loop.py")
