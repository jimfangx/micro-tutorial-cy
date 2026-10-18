"""Generation-only Docker entry point using CHIA's CLI agent implementations."""

from pathlib import Path
import json
import os
import sys
from .artifacts import source_bundle


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
        **cfg["agent"]["backends"][backend]["options"],
    )
    if cfg["agent"]["model"]:
        args["model"] = cfg["agent"]["model"]
    if backend in ("antigravity", "codex"):
        args["work_dir"] = "/workspace"
    # Container mounts are the hard permission boundary for every provider.
    return classes[backend](**args)


def main():
    """Private generation Ray domain: neither agent nor BashTool can reach evaluator Ray."""
    from chia.base.tools.BashTool import BashTool
    import ray

    req = json.load(sys.stdin)
    cfg = req["cfg"]
    if cfg["agent"]["backend"] == "antigravity":
        options = cfg["agent"]["backends"]["antigravity"]
        folder = Path("/home/ray/.gemini/antigravity-cli")
        for name in ("settings.json", "antigravity-oauth-token"):
            path = folder / name
            data = json.loads(path.read_text()) if path.exists() else {}
            if name == "settings.json":
                data.setdefault("gcp", {}).update(
                    project=options["gcp_project"], location=options["gcp_location"]
                )
            else:
                data.update(
                    project_id=options["gcp_project"], region=options["gcp_location"]
                )
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
    ray.init(**cfg["generation_ray"])
    os.chdir("/workspace")
    tool = BashTool("dma_bash", work_dir="/workspace", timeout_seconds=120)
    try:
        if req.get("action") == "isolation":
            response = {"output": tool.run_command(req["command"])}
        else:
            agent = make_agent(cfg)
            result = agent.prompt(req["prompt"], [tool])
            if not result.success or (
                "print timeout" in result.stderr.lower()
                and "turn in progress" in result.stderr.lower()
            ):
                raise RuntimeError(
                    "Agent failed: "
                    + (result.stderr or result.result or result.stream_result)[-4000:]
                )
            response = {
                "model": cfg["agent"]["model"] or getattr(agent, "model", None),
                "response": result.result,
                "transcript": result.stream_result,
                "source": source_bundle("/workspace"),
            }
        print("CHIA_AGENT_RESULT=" + json.dumps(response), flush=True)
    finally:
        tool.stop()
        ray.shutdown()


if __name__ == "__main__":
    main()
