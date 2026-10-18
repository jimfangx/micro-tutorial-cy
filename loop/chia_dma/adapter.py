"""SkyDiscover LLM interface -> native CHIA CLI agent -> restricted BashTool."""

import asyncio
import json
import os
from pathlib import Path
import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.base.tools.BashTool import BashTool
from skydiscover.llm.base import LLMInterface, LLMResponse
from .artifacts import source_bundle, validate_bundle
from .config import ALLOWLIST
from .state import complete, proposal

CONSTRAINTS = """Improve the existing bare-metal RoCC DMA implementation. Preserve the RV64
custom0 ABI, validation/status/error semantics, ordering and coherence. Use only
the existing tlNode. Keep WithRoCCDMA and the disabled io.mem assignments fixed.
Use dma_bash_run_command to read and edit /workspace. Only the two accelerator
Scala files are writable. Overwrite them directly; do not replace bind mounts.
Do not alter evaluator, software, benchmarks, tests, counters or SoC topology.
Make one small concrete change and finish. Do not compile or run benchmarks.
"""


def make_agent(cfg):
    from chia.models.antigravity import AntigravityLLM
    from chia.models.claude import ClaudeCodeLLM
    from chia.models.codex import CodexLLM

    backend = cfg["agent"]["backend"]
    classes = {
        "antigravity": AntigravityLLM,
        "claude": ClaudeCodeLLM,
        "codex": CodexLLM,
    }
    args = dict(
        timeout_seconds=cfg["agent"]["timeout_seconds"],
        retries=cfg["agent"]["retries"],
        resume_session=False,
        **cfg["agent"]["backends"][backend]["options"]
    )
    if cfg["agent"]["model"]:
        args["model"] = cfg["agent"]["model"]
    if backend in ("antigravity", "codex"):
        args["work_dir"] = "/workspace"
    return classes[backend](**args)


def override_antigravity(cfg):
    """Apply only explicit project/location overrides to the private credential copy."""
    options = cfg["agent"]["backends"]["antigravity"]
    values = {key: options.get(key) for key in ("gcp_project", "gcp_location")}
    if not any(v is not None for v in values.values()):
        return
    folder = Path.home() / ".gemini/antigravity-cli"
    for name, keys in [
        ("settings.json", ("project", "location")),
        ("antigravity-oauth-token", ("project_id", "region")),
    ]:
        path = folder / name
        data = json.loads(path.read_text()) if path.exists() else {}
        target = data.setdefault("gcp", {}) if name == "settings.json" else data
        target.update(
            {
                key: value
                for key, value in zip(keys, values.values())
                if value is not None
            }
        )
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def generate(cfg, sql, task):
    if not task.get("auth"):
        saved = proposal(sql, task["id"], remote=True)
        if saved:
            return saved
    parent = validate_bundle(json.loads(task["parent"]["solution"])["source"])
    # Each native generation worker has its own two writable file binds and one
    # generation slot. Reuse is serialized for this entire task, including MCP.
    for name, text in parent.items():
        (Path("/workspace") / name).write_text(text)
    if cfg["agent"]["backend"] == "antigravity":
        override_antigravity(cfg)
    os.chdir("/workspace")
    prompt = CONSTRAINTS + "\nSelected parent and inspirations:\n" + json.dumps(task)
    guidance = cfg["search"]["island_guidance"]
    if guidance:
        prompt += "\nIsland guidance: " + guidance[task["island"] % len(guidance)]
    if task.get("auth"):
        prompt = (
            "Please use dma_bash_run_command to run pwd and sha256sum /workspace/"
            + ALLOWLIST[0]
            + ". Report the directory and checksum."
        )
    tool = BashTool(
        "dma_bash",
        work_dir="/workspace",
        timeout_seconds=120,
        task_options={
            "scheduling_strategy": NodeAffinitySchedulingStrategy(
                ray.get_runtime_context().get_node_id(), soft=False
            )
        },
    )
    try:
        agent = make_agent(cfg)
        result = agent.prompt(prompt, [tool])
        if not result.success or (
            "print timeout" in result.stderr.lower()
            and "turn in progress" in result.stderr.lower()
        ):
            raise RuntimeError(
                "Agent failed: "
                + (result.stderr or result.result or result.stream_result)[-4000:]
            )
        generation = {
            "backend": cfg["agent"]["backend"],
            "model": cfg["agent"]["model"] or getattr(agent, "model", None),
            "response": result.result,
            "transcript": result.stream_result,
        }
        source = source_bundle("/workspace")
        if task.get("auth"):
            return {**generation, "source": source}
        return complete(sql, task["id"], source, generation, remote=True)
    finally:
        tool.stop()


class CLIClient(LLMInterface):
    def __init__(self, cfg, state):
        self.cfg, self.state = cfg, state

    async def generate(self, system_message, messages, **kwargs):
        cid = json.loads(messages[-1]["content"])["request_id"]
        handle = generate.chia_remote(
            self.cfg,
            self.state.sql,
            self.state.request(cid),
            _chia_tag="proposal/" + cid,
        )
        try:
            saved = await asyncio.to_thread(get, handle)
        except asyncio.CancelledError:
            chia_cancel(getattr(handle, "ref", handle), force=True)
            raise
        return LLMResponse(
            text="```json\n"
            + json.dumps({"proposal_id": cid, "source": saved["source"]})
            + "\n```"
        )


class ParentContext:
    def build_prompt(self, current_program, context):
        return {
            "system": CONSTRAINTS,
            "user": json.dumps({"request_id": current_program.metadata["request_id"]}),
        }
