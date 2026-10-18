"""Normal CHIA tasks: one worker checkout, one build, many independent runs."""

from pathlib import Path
import shutil
import subprocess
from chia.base.ChiaFunction import ChiaFunction
from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.trace.profiler import get_profiler
from .artifacts import (
    source_bundle,
    validate_bundle,
    validate_integration,
    write_json,
)
from .config import digest


def event(operation, **info):
    get_profiler().add_info(info)
    get_profiler().log_event("dma_" + operation, **info)


def workspace(cfg, seed):
    # Native cluster worker_setup_commands initialize this private checkout once.
    return Path("/workspace/chipyard")


def apply_source(tree, source, baseline):
    validate_bundle(source)
    validate_integration(source, baseline)
    for name, text in source.items():
        (tree / name).write_text(text)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def software(cfg, run, seed):
    tree = workspace(cfg, seed)
    apply_source(
        tree,
        source_bundle(Path(seed) / "tree"),
        source_bundle(Path(seed) / "tree"),
    )
    for i, command in enumerate(cfg["software"]["build_commands"]):
        with open(Path(run) / f"software-build-{i}.log", "w") as log:
            subprocess.run(
                [p.replace("{workspace}", str(tree)) for p in command],
                cwd=tree,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    target = Path(run) / "software"
    target.mkdir(exist_ok=True)
    benchmarks = sorted(
        p.name
        for p in (tree / cfg["software"]["benchmark_inventory"]).iterdir()
        if p.is_dir()
    )
    paths = {
        f"embench:{mode}:{name}": cfg["software"]["embench_elf"].format(
            mode=mode, benchmark=name
        )
        for mode in ("cpu", "dma")
        for name in benchmarks
    }
    paths.update({n: s["elf"] for n, s in cfg["software"]["standalone"].items()})
    workloads = {}
    for name, rel in paths.items():
        p = target / (name.replace(":", "_") + ".elf")
        shutil.copyfile(tree / rel, p)
        workloads[name] = {"file": p.name, "sha256": digest(p.read_bytes())}
    versions = {
        name: subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        for name, cmd in {
            "gcc": ["riscv64-unknown-elf-gcc", "--version"],
            "verilator": ["verilator", "--version"],
        }.items()
    }
    manifest = {"benchmarks": benchmarks, "workloads": workloads, "toolchain": versions}
    write_json(target / "manifest.json", manifest)
    return manifest


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def build(cfg, seed, source):
    tree = workspace(cfg, seed)
    apply_source(tree, source, source_bundle(Path(seed) / "tree"))
    hw = cfg["hardware"]
    event("build", source_hash=digest(source))
    artifact = ChiselBuildNode(
        str(tree),
        hw["config"],
        config_package=hw["config_package"],
        make_jobs=hw["make_jobs"],
        timeout_seconds=hw["build_timeout_seconds"],
        extra_make_args=hw["extra_make_args"],
        clean=hw["clean"],
        bundle_runtime_libs=True,
    ).build()
    if artifact.success and hw["protocol_command"]:
        result = subprocess.run(
            [a.replace("{workspace}", str(tree)) for a in hw["protocol_command"]],
            cwd=tree,
            capture_output=True,
            text=True,
            timeout=hw["build_timeout_seconds"],
        )
        artifact.stdout += "\nProtocol tests:\n" + result.stdout
        artifact.stderr += result.stderr
        if result.returncode or "All tests passed" not in result.stdout:
            artifact.success = False
            artifact.returncode = result.returncode or 1
    return artifact


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def simulate(cfg, artifact, content, name, ini):
    event("simulation", benchmark=name)
    return VerilatorRunNode().run(
        artifact,
        content,
        name.replace(":", "_") + ".riscv",
        "/tmp/dma-sim",
        plusargs={
            **cfg["software"]["plusargs"],
            f"+verilator+seed+{cfg['software']['random_seed']}": "",
            "+loadmem": name.replace(":", "_") + ".riscv",
        },
        timeout_cycles=cfg["software"]["timeout_cycles"],
        timeout_seconds=cfg["software"]["timeout_seconds"],
        dramsim_ini_files=ini,
        verbose=False,
    )
