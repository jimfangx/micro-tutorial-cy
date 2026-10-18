#!/usr/bin/env python3
"""Build unmodified Embench with SCons and run it on Chipyard's real RTL."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from metrics import parse as parse_metrics

WORK = Path(__file__).resolve().parents[1]
ROOT = WORK.parents[1]
SUITE = ROOT / "tests/embench-iot"
BUILD = WORK / "build"
LOGS = BUILD / "logs"
BENCHMARKS = sorted(p.name for p in (SUITE / "src").iterdir() if p.is_dir())
CC = "riscv64-unknown-elf-gcc"
ARCH = "-march=rv64gc -mabi=lp64d -mcmodel=medany"
CFLAGS = f"-O2 -g -std=gnu11 {ARCH} -fno-common -fno-builtin-memcpy -fno-builtin-printf -specs=htif_nano.specs -I{WORK}"
SIM = ROOT / "sims/verilator/simulator-chipyard.harness-DMARocketConfig"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs():
    return {str(p.relative_to(SUITE)): sha(p) for folder in ("src", "support")
            for p in sorted((SUITE / folder).rglob("*")) if p.is_file()}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def run_logged(command, log, cwd=ROOT):
    with log.open("w") as out:
        subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, check=True)


def elf(mode, bench):
    return BUILD / f"embench-{mode}" / "src" / bench / bench


def inspect(mode, bench):
    binary = elf(mode, bench)
    dump = subprocess.check_output(["riscv64-unknown-elf-objdump", "-d", str(binary)], text=True)
    binary.with_suffix(".dump").write_text(dump)
    objects = sorted(binary.parent.glob("*.o"))
    undefined = subprocess.check_output(["riscv64-unknown-elf-nm", "-u", *map(str, objects)], text=True)
    def sites(symbol):
        return sum(bool(re.search(r"\b(?:jal|jalr|j|call|tail)\b.*<" + symbol + r">", line))
                   for line in dump.splitlines())
    return {"benchmark_memcpy_reference": bool(re.search(r"\bU\s+memcpy\s*$", undefined, re.M)),
            "memcpy_call_sites": sites("memcpy"), "wrapper_call_sites": sites("__wrap_memcpy"),
            "elf_sha256": sha(binary)}


def build(modes):
    before = inputs()
    snapshot = BUILD / "embench-input-hashes.json"
    if snapshot.exists() and json.loads(snapshot.read_text()) != before:
        raise SystemExit("Embench inputs differ from the original source snapshot")
    write_json(snapshot, before)
    run_logged(["make", "-C", str(WORK), "libraries"], LOGS / "embench-libraries.log")
    runtime = BUILD / "atexit-workaround.o"
    subprocess.run([CC, *CFLAGS.split(), "-c", str(ROOT / "tests/atexit-workaround.c"),
                    "-o", str(runtime)], check=True)
    manifest_path = BUILD / "embench-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update(benchmarks=BENCHMARKS, cflags=CFLAGS, global_scale_factor=1, warmup_heat=1,
                    source_hashes=before, simulator_config="DMARocketConfig")
    manifest.setdefault("inspection", {})
    manifest.setdefault("ldflags", {})
    for mode in modes:
        flags = (f"{ARCH} -static -specs=htif_nano.specs -L{BUILD / mode} {runtime} "
                 "-Wl,--wrap=atexit,--wrap=main,--wrap=verify_benchmark")
        if mode != "cpu":
            # libc/HTIF may introduce memcpy references after our archive is
            # searched, even when the benchmark object has no memcpy calls.
            flags += " -Wl,--wrap=memcpy,--undefined=__wrap_memcpy"
        command = ["scons", "-f", "sconstruct.py", f"--config-dir={WORK / 'embench'}",
                   f"--build-dir={BUILD / ('embench-' + mode)}", f"cc={CC}", f"ld={CC}",
                   f"cflags={CFLAGS}", f"ldflags={flags}", "user_libs=rocc_dma m",
                   "gsf=1", "warmup_heat=1"]
        print(f"Building Embench {mode}", flush=True)
        run_logged(command, LOGS / f"embench-build-{mode}.log", SUITE)
        manifest["ldflags"][mode] = flags
        manifest["inspection"][mode] = {b: inspect(mode, b) for b in BENCHMARKS}
    if inputs() != before:
        raise SystemExit("Embench benchmark/support sources changed during the build")
    write_json(manifest_path, manifest)
    print(f"Built and inspected {len(BENCHMARKS)} benchmarks per mode", flush=True)


def run_one(mode, bench, sim_hash):
    binary = elf(mode, bench)
    log = LOGS / f"embench-{mode}-{bench}.log"
    command = ["make", "-C", str(ROOT / "sims/verilator"), "CONFIG=DMARocketConfig",
               "run-binary-fast", f"BINARY={binary}", "LOADMEM=1", "TIMEOUT_CYCLES=1000000000",
               "EXTRA_SIM_FLAGS=+cycle-count", f"EXTRA_SIM_OUT_NAME={mode}"]
    run_logged(command, log)
    data = log.read_text()
    measured = parse_metrics(data, "embench")
    result = measured["software"]
    result["hardware"] = measured["hardware"]
    result["copy_events"] = measured["copies"]
    result.update(mode=mode, benchmark=bench, elf_sha256=sha(binary), sim_sha256=sim_hash,
                  log_sha256=sha(log))
    return result


def run(modes, benches, jobs):
    manifest = json.loads((BUILD / "embench-manifest.json").read_text())
    if manifest["source_hashes"] != inputs():
        raise SystemExit("Embench inputs changed since build")
    # Build once, before concurrent run-binary processes inspect the same target.
    run_logged(["make", "-C", str(ROOT / "sims/verilator"), "CONFIG=DMARocketConfig", "-j8"],
               LOGS / "embench-simulator-build.log")
    sim_hash = sha(SIM)
    path = BUILD / "embench-results.json"
    saved = json.loads(path.read_text()) if path.exists() else []
    records = {(r["mode"], r["benchmark"]): r for r in saved}
    def complete(mode, bench):
        r = records.get((mode, bench))
        log = LOGS / f"embench-{mode}-{bench}.log"
        return (r and r["elf_sha256"] == sha(elf(mode, bench)) and r["sim_sha256"] == sim_hash
                and log.exists() and r["log_sha256"] == sha(log) and r["result"] == 0)
    def save(result):
        records[result["mode"], result["benchmark"]] = result
        write_json(path, [records[k] for k in sorted(records)])
        print(f"PASS {result['mode']} {result['benchmark']}: main={result['main_cycles']} "
              f"timed={result['benchmark_cycles']} DMA={result['offloaded']}", flush=True)
    # Wikisort is the required first integration run; remaining programs follow.
    first = [mode for mode in modes if not complete(mode, "wikisort")]
    with ThreadPoolExecutor(max_workers=max(1, min(jobs, len(first)))) as pool:
        futures = []
        for mode in first:
            print(f"Running {mode} wikisort first", flush=True)
            futures.append(pool.submit(run_one, mode, "wikisort", sim_hash))
        for future in as_completed(futures):
            save(future.result())
    todo = [(mode, b) for b in benches for mode in modes if not complete(mode, b)]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_one, m, b, sim_hash) for m, b in todo]
        for future in as_completed(futures):
            save(future.result())
    if inputs() != manifest["source_hashes"]:
        raise SystemExit("Embench inputs changed during execution")
    print("Requested Embench runs complete", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "run"))
    parser.add_argument("--modes", nargs="+", choices=("cpu", "dma", "wrapper"), default=["cpu", "dma"])
    parser.add_argument("--bench", nargs="+", choices=BENCHMARKS)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    BUILD.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    if args.action == "build":
        build(args.modes)
    else:
        run(args.modes, args.bench or BENCHMARKS, max(1, args.jobs))


if __name__ == "__main__":
    main()
