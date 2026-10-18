"""SkyDiscover's public LLM adapter -> CHIA CLI agent -> sandboxed BashTool."""

import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from skydiscover.llm.base import LLMInterface, LLMResponse
from .artifacts import archive_proposal, read_json, validate_bundle
from .config import ALLOWLIST
from .deployment import chia, expand, render

CONSTRAINTS = """Improve the existing bare-metal RoCC DMA implementation. Preserve the RV64
custom0 ABI, validation/status/error semantics, ordering and coherence. Use only
the existing tlNode. Keep WithRoCCDMA and the disabled io.mem assignments fixed.
Use dma_bash_run_command to read and edit /workspace. Only the two accelerator
Scala files are writable. Overwrite them directly; do not replace bind mounts.
Do not alter evaluator, software, benchmarks, tests, counters or SoC topology.
Make one small concrete change and finish. Do not compile or run benchmarks.
"""


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def generate(cfg, run, seed, task):
    parent = validate_bundle(json.loads(task["parent"]["solution"])["source"])
    tmp = Path(run) / "work"
    tmp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp, prefix=task["id"] + "-") as folder:
        for name in ALLOWLIST:
            (Path(folder) / Path(name).name).write_text(parent[name])
        path, values = render(cfg, run, seed, request=task, work=folder)
        try:
            chia(cfg, "up", path)
            prompt = (
                CONSTRAINTS + "\nSelected parent and inspirations:\n" + json.dumps(task)
            )
            guidance = cfg["search"]["island_guidance"]
            if guidance:
                prompt += (
                    "\nIsland guidance: " + guidance[task["island"] % len(guidance)]
                )
            if task.get("auth"):

                prompt = (
                    "Check that the workspace is ready by using dma_bash_run_command "
                    "to run pwd and sha256sum /workspace/"
                    + ALLOWLIST[0]
                    + ". Report the directory and computed SHA-256."
                )
            command = expand(cfg["generation_exec"], values)
            proc = subprocess.run(
                command,
                input=json.dumps({"cfg": cfg, "prompt": prompt}),
                text=True,
                capture_output=True,
                timeout=cfg["runtime"]["task_timeout_seconds"],
                check=False,
            )
            if proc.returncode:
                raise RuntimeError("Generation worker failed:\n" + proc.stderr[-5000:])
            marker = "CHIA_AGENT_RESULT="
            result = json.loads(
                next(
                    line[len(marker) :]
                    for line in reversed(proc.stdout.splitlines())
                    if line.startswith(marker)
                )
            )
            source = {n: (Path(folder) / Path(n).name).read_text() for n in ALLOWLIST}
            if source != result.pop("source"):
                raise ValueError("Generation source mismatch")
            if task.get("auth"):
                return {**result, "source": source}
            archive_proposal(
                run,
                task["id"],
                source,
                parent,
                {
                    "parent_id": task["parent"]["id"],
                    "island": task["island"],
                    "sequence": task["sequence"],
                    "backend": cfg["agent"]["backend"],
                    "model": result.get("model"),
                    "generation": result,
                },
            )
            return read_json(Path(run) / "candidates" / task["id"] / "proposal.json")
        finally:
            chia(cfg, "down", path)
            path.unlink(missing_ok=True)


class CLIClient(LLMInterface):
    def __init__(self, cfg, state, seed):
        self.cfg, self.state, self.seed = cfg, state, str(seed)

    async def generate(self, system_message, messages, **kwargs):
        cid = json.loads(messages[-1]["content"])["request_id"]
        request = self.state.request(cid)
        handle = generate.chia_remote(
            self.cfg,
            str(self.state.run),
            self.seed,
            request,
            _chia_tag="proposal/" + cid,
        )
        try:
            proposal = await asyncio.to_thread(get, handle)
        except asyncio.CancelledError:
            chia_cancel(getattr(handle, "ref", handle), force=True)
            raise
        payload = {"proposal_id": cid, "source": proposal["source"]}
        return LLMResponse(text="```json\n" + json.dumps(payload) + "\n```")


class ParentContext:
    def build_prompt(self, current_program, context):
        return {
            "system": CONSTRAINTS,
            "user": json.dumps({"request_id": current_program.metadata["request_id"]}),
        }
