"""Ephemeral localhost SSH for testing the real CHIA CLI; all files stay in loop/."""

from pathlib import Path
import copy
import os
import socket
import subprocess
import time
import uuid
import pytest
from chia_dma.config import ROOT, load


@pytest.fixture(scope="module")
def native_ssh():
    folder = ROOT / "experiments/refactor-checks" / ("ssh-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    for key in ("host", "client"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(folder / key)],
            check=True,
        )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = folder / "sshd_config"
    config.write_text(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {folder/'host'}
PidFile {folder/'sshd.pid'}
AuthorizedKeysFile {folder/'client.pub'}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
AllowUsers {os.environ['USER']}
LogLevel ERROR
""")
    log = (folder / "sshd.log").open("w")
    proc = subprocess.Popen(
        ["sudo", "-n", "/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError((folder / "sshd.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        cfg = load()
        with socket.socket() as ray_socket, socket.socket() as dashboard_socket:
            ray_socket.bind(("127.0.0.1", 0))
            dashboard_socket.bind(("127.0.0.1", 0))
            ray_port = ray_socket.getsockname()[1]
            dashboard_port = dashboard_socket.getsockname()[1]
        cfg["runtime"]["ray_address"] = f"127.0.0.1:{ray_port}"
        for key in ("head_start_ray_commands", "worker_start_ray_commands"):
            cfg["cluster"][key] = [
                command.replace("6387", str(ray_port)).replace(
                    "8287", str(dashboard_port)
                )
                for command in cfg["cluster"][key]
            ]
        for name in ("cluster", "generation_cluster"):
            cfg[name]["auth"] = {
                "ssh_user": os.environ["USER"],
                "ssh_private_key": str(folder / "client"),
                "ssh_proxy_command": f"nc 127.0.0.1 {port}",
            }
        yield cfg, folder
    finally:
        subprocess.run(
            ["sudo", "kill", "-TERM", str(proc.pid)], check=False, capture_output=True
        )
        proc.wait(timeout=10)
        log.close()
