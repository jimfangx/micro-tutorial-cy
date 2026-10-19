"""Native host-network workers: filesystem boundaries, reuse, and real CLI calls."""

from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import time
import pytest
import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia_dma.config import ALLOWLIST, ROOT
from chia_dma.runtime import Runtime, render
from native_fixture import native_ssh


def chia(cfg, action, path):
    """Tests invoke the upstream CLI, just as the documented procedure does."""
    result = subprocess.run(
        [sys.executable, "-m", "chia.cli.main", action, str(path), "-y"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-5000:] + result.stdout[-3000:]


def cluster(cfg, base, name, *, pools=False):
    cfg = copy.deepcopy(cfg)
    run = base / (name + "-" + base.name[-8:])
    run.mkdir()
    seed = run / "seed"
    for n in (*ALLOWLIST, "build.sbt", "software/benchmark.c"):
        p = seed / "tree" / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("FROZEN")
    (seed / "tree/frozen").write_text("preserved source")
    if not pools:
        node = cfg["cluster"]["available_node_types"]["generation"]
        cfg["cluster"]["available_node_types"] = {"generation": node}
    path = render(cfg, run, seed)
    return cfg, run, seed, path


def attach(cfg):
    ray.init(
        address=cfg["runtime"]["ray_address"],
        namespace="native-proof",
    )


@ray.remote(num_cpus=0, max_concurrency=3)
class Ready:
    def __init__(self):
        self.count = 0

    def arrive(self):
        self.count += 1
        deadline = time.monotonic() + 30
        while self.count < 2:
            if time.monotonic() >= deadline:
                raise TimeoutError("Second generation worker did not reach BashTool")
            time.sleep(0.02)


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def boundary_probe(mark, ready, sql, delay=0.2):
    import os
    import shlex
    import time
    from pathlib import Path
    import ray
    from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
    from chia.base.tools.BashTool import BashTool
    from chia_dma.config import ALLOWLIST

    # Match production's parent initialization before giving the agent its shell.
    for name in ALLOWLIST:
        (Path("/workspace") / name).write_text(mark)
    tool = BashTool(
        "boundary_bash",
        work_dir="/workspace",
        task_options={
            "scheduling_strategy": NodeAffinitySchedulingStrategy(
                ray.get_runtime_context().get_node_id(), soft=False
            )
        },
    )
    ray.get(ready.arrive.remote())
    started = time.time()
    try:
        script = (
            """from pathlib import Path
import errno
p=Path('/workspace/"""
            + ALLOWLIST[0]
            + """')
print('PARENT='+p.read_text())
p.write_text(p.read_text()+' edited')
for name in ['/workspace/build.sbt','/workspace/software/benchmark.c','/opt/loop/chia_dma/evaluation.py']:
 try: Path(name).write_text('FORBIDDEN')
 except OSError as error:
  assert error.errno in [errno.EROFS,errno.EACCES,errno.EPERM], str(error)
  print('DENIED='+name)
 else: raise RuntimeError('Forbidden write succeeded')
assert not Path('/var/run/docker.sock').exists()
assert not Path('/opt/loop/experiments').exists()
"""
        )
        output = tool.run_command("python -c " + shlex.quote(script))
        time.sleep(delay)
        assert (Path("/workspace") / ALLOWLIST[0]).read_text() == mark + " edited"
        assert (Path("/workspace") / ALLOWLIST[1]).read_text() == mark
        from chia_dma.state import complete
        from chia_dma.artifacts import source_bundle

        assert not Path(sql.db_path).exists()
        complete(
            sql,
            mark,
            source_bundle("/workspace"),
            {"response": "BashTool boundary proof"},
            remote=True,
        )
        return {
            "node": ray.get_runtime_context().get_node_id(),
            "started": started,
            "ended": time.time(),
            "output": output,
        }
    finally:
        tool.stop()


def test_native_generation_parent_separation_forbidden_writes_and_serial_reuse(
    native_ssh,
):
    cfg, base = native_ssh
    cfg = copy.deepcopy(cfg)
    credentials = base / "empty-credentials"
    credentials.mkdir()
    cfg["agent"]["backends"][cfg["agent"]["backend"]]["credentials"] = str(credentials)
    cfg["agent"]["backends"][cfg["agent"]["backend"]]["mounts"] = []
    cfg, run, seed, path = cluster(cfg, base, "boundaries")
    try:
        chia(cfg, "up", path)
        attach(cfg)
        from chia_dma.state import State
        from skydiscover.search.base_database import Program

        state = State(cfg, run)
        for mark in ("parent-A", "parent-B", "parent-C"):
            state.reserve(
                Program(
                    id=mark,
                    solution=json.dumps({"source": {n: mark for n in ALLOWLIST}}),
                    metadata={"island": 0},
                ),
                [],
                cid=mark,
            )
        ready = Ready.remote()
        results = [
            get(h)
            for h in [
                boundary_probe.chia_remote(mark, ready, state.sql, 1)
                for mark in ("parent-A", "parent-B", "parent-C")
            ]
        ]
        assert len({r["node"] for r in results}) == 2
        for mark, result in zip(("parent-A", "parent-B", "parent-C"), results):
            assert "PARENT=" + mark in result["output"]
            assert result["output"].count("DENIED=") == 3
            assert state.proposal(mark)["source"][ALLOWLIST[0]] == mark + " edited"
        for a, b in [(results[i], results[j]) for i in range(3) for j in range(i)]:
            if a["node"] == b["node"]:
                assert a["started"] >= b["ended"] or b["started"] >= a["ended"]
        assert any(
            a["started"] < b["ended"] and b["started"] < a["ended"]
            for a, b in [
                (results[0], results[1]),
                (results[0], results[2]),
                (results[1], results[2]),
            ]
        )
        from chia_dma.adapter import generate

        # A completed proposal returns before even reading the missing parent
        # field; reaching the agent path would fail this task.
        recovered = get(generate.chia_remote(cfg, state.sql, {"id": "parent-A"}))
        assert recovered["source"] == state.proposal("parent-A")["source"]
        state.close()
        assert all(
            (seed / "tree" / n).read_text() == "FROZEN"
            for n in (*ALLOWLIST, "build.sbt", "software/benchmark.c")
        )
    finally:
        ray.shutdown()
        chia(cfg, "down", path)


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def simulation_probe(mark):
    from chia.chipyard.state_def import BuildArtifact
    from chia.chipyard.verilator_run_node import VerilatorRunNode

    script = (
        "#!/bin/sh\necho BUILD_"
        + mark
        + '\nsleep 1\nfor arg; do last=$arg; done\ncat "$last"\n'
    ).encode()
    artifact = BuildArtifact(
        mark,
        script,
        "same-simulator",
        "fixture",
        "fixture",
        "verilator",
        True,
        "",
        "",
        0,
    )
    result = VerilatorRunNode().run(
        artifact,
        ("INPUT_" + mark + "\n").encode(),
        "same-input.riscv",
        "/tmp/staging-proof",
        timeout_seconds=10,
    )
    assert result.success
    return result.log


def test_native_build_simulation_worker_placement(native_ssh):
    cfg, base = native_ssh
    cfg, run, seed, path = cluster(cfg, base, "pools", pools=True)
    try:
        chia(cfg, "up", path)
        with Runtime(cfg, run):

            @ChiaFunction()
            def probe():
                import importlib
                import os
                from pathlib import Path

                p = Path("/home/ray/dma-build/frozen")
                return (
                    os.readlink("/proc/self/ns/mnt"),
                    (p.read_text() if p.exists() else None),
                    {
                        name: importlib.import_module(name).__file__
                        for name in (
                            "chia.base.ChiaFunction",
                            "chia_dma.adapter",
                            "skydiscover.config",
                        )
                    },
                )

            build = get(probe.options(resources={"chipyard": 1}).chia_remote())
            simulation = get(
                probe.options(resources={"verilator_run": 1}).chia_remote()
            )
            generation = get(probe.options(resources={"generation": 1}).chia_remote())
            head = get(
                probe.options(resources={"node:__internal_head__": 0.001}).chia_remote()
            )
            assert (
                build[0] != simulation[0]
                and build[1] == "preserved source"
                and simulation[1] is None
            )
            assert head[2]["chia_dma.adapter"] == str(ROOT / "chia_dma/adapter.py")
            assert head[2]["chia.base.ChiaFunction"] == str(
                Path(cfg["chia"]) / "chia/base/ChiaFunction.py"
            )
            for worker in (build, simulation, generation):
                assert worker[2]["chia_dma.adapter"] == "/opt/loop/chia_dma/adapter.py"
                assert (
                    worker[2]["chia.base.ChiaFunction"]
                    == "/opt/chia/chia/base/ChiaFunction.py"
                )
                assert (
                    worker[2]["skydiscover.config"]
                    == "/opt/skydiscover/skydiscover/config.py"
                )
            assert ray.cluster_resources()["generation"] == 2
            handles = [simulation_probe.chia_remote(mark) for mark in ("A", "B")]
            assert [get(h).strip() for h in handles] == [
                "BUILD_A\nINPUT_A",
                "BUILD_B\nINPUT_B",
            ]
    finally:
        ray.shutdown()
        chia(cfg, "down", path)


@pytest.mark.skipif(
    os.environ.get("CHIA_LIVE_AGENTS") != "1",
    reason="Opt-in authenticated provider calls",
)
@pytest.mark.parametrize("backend", ["antigravity", "claude", "codex"])
def test_cli_backend_uses_native_bash_tool(native_ssh, backend):
    from chia_dma.adapter import generate
    from chia_dma.config import digest
    from chia_dma.artifacts import source_bundle, write_json

    original, base = native_ssh
    cfg = copy.deepcopy(original)
    cfg["agent"]["backend"] = backend
    cfg, run, seed, path = cluster(cfg, base, "cli-" + backend)
    # New private content cannot be inferred from the checksum request.
    import uuid

    source = {n: uuid.uuid4().hex for n in ALLOWLIST}
    task = {
        "id": "auth",
        "auth": True,
        "parent": {"solution": json.dumps({"source": source})},
        "island": 0,
    }
    try:
        chia(cfg, "up", path)
        attach(cfg)
        result = get(generate.chia_remote(cfg, None, task))
        write_json(run / "result.json", result)
        assert digest(source[ALLOWLIST[0]].encode()) in result["response"]
        assert result["source"] == source
        if backend != "claude":
            assert "dma_bash" in result["transcript"]
    finally:
        ray.shutdown()
        chia(cfg, "down", path)
