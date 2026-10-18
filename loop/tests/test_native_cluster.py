"""Real native up/down, separate parents, and forbidden BashTool writes."""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import uuid
import os
import pytest
import hashlib
import socket
import time
from chia_dma.config import ALLOWLIST
from chia_dma.artifacts import write_json
from chia_dma.deployment import render, chia, expand
from native_fixture import native_ssh


def test_native_generation_clusters_enforce_concurrent_parent_boundaries(native_ssh):
    original, base = native_ssh
    cfg = copy.deepcopy(original)
    credentials = base / "empty-credentials"
    credentials.mkdir()
    cfg["agent"]["backends"][cfg["agent"]["backend"]]["credentials"] = str(credentials)
    cfg["agent"]["backends"][cfg["agent"]["backend"]]["mounts"] = []
    run = base / "boundary"
    run.mkdir()
    seed = run / "seed"
    tree = seed / "tree"
    for name in (*ALLOWLIST, "build.sbt", "software/benchmark.c"):
        p = tree / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("FROZEN")
    listener = socket.socket()
    listener.bind(("0.0.0.0", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    with socket.create_connection(("127.0.0.1", port)):
        pass
    host = socket.gethostbyname(socket.gethostname())
    script = """from pathlib import Path
import socket, struct, errno
p=Path('/workspace/generators/chipyard/src/main/scala/dma/DMAController.scala')
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
try: socket.create_connection(('127.0.0.1',6387),timeout=0.2)
except OSError: print('RAY_ISOLATED')
else: raise RuntimeError('Trusted Ray reachable')
gateway = next(socket.inet_ntoa(struct.pack('<L',int(line.split()[2],16))) for line in Path('/proc/net/route').read_text().splitlines()[1:] if line.split()[1] == '00000000')
for host in [gateway, 'HOST_IP']:
 try: socket.create_connection((host, HOST_PORT),timeout=0.2)
 except OSError: print('HOST_DENIED='+host)
 else: raise RuntimeError('Host services reachable: '+host)
""".replace("HOST_IP", host).replace("HOST_PORT", str(port))

    def one(mark):
        work = run / mark
        work.mkdir()
        for name in ALLOWLIST:
            (work / Path(name).name).write_text(mark)
        task = {"id": "boundary-" + mark + "-" + uuid.uuid4().hex}
        path, values = render(cfg, run, seed, request=task, work=work)
        try:
            chia(cfg, "up", path)
            import shlex

            request = {
                "cfg": cfg,
                "action": "isolation",
                "command": "python -c " + shlex.quote(script),
            }
            result = subprocess.run(
                expand(cfg["generation_exec"], values),
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=120,
                check=True,
            )
            marker = "CHIA_AGENT_RESULT="
            data = json.loads(
                next(
                    line[len(marker) :]
                    for line in result.stdout.splitlines()
                    if line.startswith(marker)
                )
            )
            assert "PARENT=" + mark in data["output"]
            assert (
                sum(line.startswith("DENIED=") for line in data["output"].splitlines())
                == 3
                and "RAY_ISOLATED" in data["output"]
                and data["output"].count("HOST_DENIED=") == 2
            )
            assert (work / "DMAController.scala").read_text() == mark + " edited"
            assert all(
                (tree / n).read_text() == "FROZEN"
                for n in (*ALLOWLIST, "build.sbt", "software/benchmark.c")
            )
        finally:
            chia(cfg, "down", path)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(one, ["parent-A", "parent-B"]))
    finally:
        listener.close()


def test_native_build_simulation_pools_and_scoped_down(native_ssh):
    from chia.base.ChiaFunction import ChiaFunction, get
    import ray

    cfg, base = native_ssh
    run = base / "native-pools"
    run.mkdir()
    seed = run / "seed"
    (seed / "tree").mkdir(parents=True)
    (seed / "tree/frozen").write_text("preserved source")
    work = run / "generation-work"
    work.mkdir()
    for name in ALLOWLIST:
        p = seed / "tree" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("preserved source")
        (work / Path(name).name).write_text("selected parent")
    credentials = run / "empty-credentials"
    credentials.mkdir()
    backend = cfg["agent"]["backends"][cfg["agent"]["backend"]]
    backend["credentials"], backend["mounts"] = str(credentials), []
    path, _ = render(cfg, run, seed)
    try:
        chia(cfg, "up", path)
        ray.init(address=cfg["runtime"]["ray_address"], namespace="native-pool-proof")

        @ChiaFunction()
        def probe():
            import os
            from pathlib import Path

            return {
                "namespace": os.readlink("/proc/self/ns/mnt"),
                "tree": (
                    Path("/workspace/chipyard/frozen").read_text()
                    if Path("/workspace/chipyard/frozen").exists()
                    else None
                ),
            }

        b = get(probe.options(resources={"chipyard": 1}).chia_remote())
        s = get(probe.options(resources={"verilator_run": 1}).chia_remote())
        assert b["namespace"] != s["namespace"]
        assert b["tree"] == "preserved source" and s["tree"] is None
        assert ray.cluster_resources()["generation"] == 2
        # Simulate interruption while the agent's private Ray/BashTool is live.
        generation, values = render(
            cfg, run, seed, request={"id": "interrupted-" + uuid.uuid4().hex}, work=work
        )
        proc = None
        try:
            chia(cfg, "up", generation)
            proc = subprocess.Popen(
                expand(cfg["generation_exec"], values),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            proc.stdin.write(
                json.dumps(
                    {
                        "cfg": cfg,
                        "action": "isolation",
                        "command": "touch /tmp/tool-running; sleep 120",
                    }
                )
            )
            proc.stdin.close()
            for _ in range(100):
                ready = subprocess.run(
                    [
                        "sudo",
                        "docker",
                        "exec",
                        values["CONTAINER"],
                        "test",
                        "-f",
                        "/tmp/tool-running",
                    ],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                assert (
                    proc.poll() is None
                ), "Private generation exited before interruption"
                time.sleep(0.1)
            else:
                pytest.fail("Private BashTool did not start")
        finally:
            chia(cfg, "down", generation)
            if proc:
                proc.wait(timeout=20)
        assert (
            subprocess.run(
                ["sudo", "docker", "inspect", values["CONTAINER"]], capture_output=True
            ).returncode
            != 0
        )
        assert (
            subprocess.run(
                ["sudo", "docker", "network", "inspect", values["NETWORK"]],
                capture_output=True,
            ).returncode
            != 0
        )
        # Dedicated generation teardown must leave the trusted cluster alive.
        assert (
            get(probe.options(resources={"chipyard": 1}).chia_remote())["tree"]
            == "preserved source"
        )
    finally:
        ray.shutdown()
        chia(cfg, "down", path)


@pytest.mark.skipif(
    os.environ.get("CHIA_LIVE_AGENTS") != "1",
    reason="Opt-in calls to authenticated CLI providers",
)
@pytest.mark.parametrize("backend", ["antigravity", "claude", "codex"])
def test_cli_backends_invoke_native_bash_tool(native_ssh, backend):
    original, base = native_ssh
    cfg = copy.deepcopy(original)
    cfg["agent"]["backend"] = backend
    run = base / ("cli-" + backend)
    run.mkdir()
    seed = run / "seed"
    tree = seed / "tree"
    work = run / "work"
    work.mkdir()
    content = uuid.uuid4().hex
    for name in ALLOWLIST:
        p = tree / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        (work / Path(name).name).write_text(content)
    request = {"id": "cli-" + uuid.uuid4().hex}
    path, values = render(cfg, run, seed, request=request, work=work)
    prompt = (
        "Please use dma_bash_run_command to calculate the SHA-256 of /workspace/"
        + ALLOWLIST[0]
        + " and report the computed checksum."
    )
    try:
        chia(cfg, "up", path)
        process = subprocess.run(
            expand(cfg["generation_exec"], values),
            input=json.dumps({"cfg": cfg, "prompt": prompt}),
            capture_output=True,
            text=True,
            timeout=cfg["agent"]["timeout_seconds"] + 120,
            check=False,
        )
        assert process.returncode == 0, process.stderr[-16000:] + process.stdout[-3000:]
        marker = "CHIA_AGENT_RESULT="
        result = json.loads(
            next(
                line[len(marker) :]
                for line in process.stdout.splitlines()
                if line.startswith(marker)
            )
        )
        write_json(run / "result.json", result)
        assert hashlib.sha256(content.encode()).hexdigest() in result["response"]
        if backend != "claude":
            assert "[Tool Call:" in result["transcript"]
            calls = result["transcript"][result["transcript"].index("[Tool Call:") :]
            assert "dma_bash" in calls
        assert (work / "DMAController.scala").read_text() == content
        write_json(
            run / "tool-proof.json",
            {"backend": backend, "tool_called": True, "source_unchanged": True},
        )
    finally:
        chia(cfg, "down", path)
