#!/usr/bin/env python3
"""Produce the milestone report only from completed RTL test/suite records."""
import json
import re
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
ROOT = WORK.parents[1]
BUILD = WORK / "build"
LOGS = BUILD / "logs"


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def fields(line):
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}


def main():
    gates = {
        "baseline-run.log": "Hello world from core 0, a rocket",
        "command-smoke.log": "DMA command smoke PASS",
        "protocol.log": "All tests passed",
        "correctness.log": "DMA correctness/coherence PASS",
        "stall-correctness.log": "DMA correctness/coherence PASS",
        "software-tests.log": "DMA microbenchmark PASS",
    }
    for name, marker in gates.items():
        require(marker in (LOGS / name).read_text(), f"Incomplete verification: {name}")
    for name in ("correctness.log", "stall-correctness.log"):
        log = (LOGS / name).read_text()
        for size in (0, 8, 24, 64, 192, 8192, 65536, 2097152):
            require(f"DMA correctness bytes={size} PASS" in log,
                    f"Missing {size}-byte result in {name}")
    software = (LOGS / "software-tests.log").read_text()
    wrappers = [fields(line) for line in software.splitlines() if line.startswith("DMA wrapper PASS")]
    require(any(x["offloaded"] == 5 and x["bytes"] == 12936 and x["fallback"] == 7
                for x in wrappers), "DMA wrapper counts not verified")
    require(any(x["offloaded"] == 0 and x["intercepted"] == x["fallback"]
                for x in wrappers), "CPU wrapper not verified")
    micro = [fields(line) for line in software.splitlines() if line.startswith("MICRO ")]
    require([x["bytes"] for x in micro] == [8, 24, 64, 256, 1024, 4096, 65536],
            "Microbenchmark measurements incomplete")
    manifest = json.loads((BUILD / "embench-manifest.json").read_text())
    records = json.loads((BUILD / "embench-results.json").read_text())
    benchmarks = sorted(p.name for p in (ROOT / "tests/embench-iot/src").iterdir() if p.is_dir())
    require(benchmarks == sorted(manifest["benchmarks"]), "Suite inventory changed")
    inspection = manifest["inspection"]
    for mode in ("cpu", "dma"):
        require(set(inspection[mode]) == set(benchmarks), f"Missing binary inspection for {mode}")
    require(inspection["dma"]["wikisort"]["benchmark_memcpy_reference"] and
            inspection["dma"]["wikisort"]["wrapper_call_sites"] > 0,
            "Wikisort memcpy interception not present in generated binaries")
    by_key = {(r["mode"], r["benchmark"]): r for r in records}
    pairs = []
    for bench in benchmarks:
        require(("cpu", bench) in by_key and ("dma", bench) in by_key,
                f"Missing CPU/DMA comparison for {bench}")
        cpu, dma = by_key["cpu", bench], by_key["dma", bench]
        for r in (cpu, dma):
            require(r["result"] == 0 and r["errors"] == 0 and r["main_cycles"] > 0
                    and r["benchmark_cycles"] > 0, f"Failed run: {r['mode']} {bench}")
        require(cpu["offloaded"] == 0, f"CPU baseline used DMA: {bench}")
        require(dma["intercepted"] == dma["offloaded"] + dma["fallback"],
                f"Unaccounted wrapped calls: {bench}")
        pairs.append((bench, cpu, dma))
    lines = [
        "# First-cut verification and measurements", "",
        "All required standalone tests and the CPU/DMA Embench suite completed in Verilator.",
        "Rocket, caches, coherence/interconnect, DRAM implementations, and benchmark sources are unchanged.",
        "The initial command-only bring-up was validated before adding the actual DMA datapath.", "",
        "Environment: Chipyard base `157dd69e`, Rocket `ece7b9ad5`, Embench `09c2ed8`,",
        "Chisel 6.7.0, Verilator 5.022, RISC-V GCC 16.1.0, SCons 4.11.1.", "",
        "Standalone verification passed: zero/single/multiple units; cache-line/page boundaries;",
        "64 KiB and 2 MiB transfers exceeding L1/L2; dirty sources and cached destinations;",
        "repeated alternating CPU/DMA accesses; source/guard integrity; invalid alignment, length,",
        "address, overflow, and overlap rejection. The same suite passed with random TileLink delays.",
        "Controller RTL tests additionally covered held requests/responses, delayed read/write replies,",
        "busy commands, reset, unsupported commands, failed reads, and a failed write after earlier progress.",
        "Denied/corrupt response handling was exercised via controller fault injection; the normal DRAM",
        "model does not naturally generate those errors. No FPGA or host-side DMA emulation was used.", "",
        "The standalone shim test recorded 12 intercepted calls, 5 actual DMA copies, 7 CPU fallbacks,",
        "and 12,936 DMA bytes. The CPU-only shim also passed; `memmove` remained on the CPU.", "",
        "## Copy microbenchmark", "",
        "CPU uses the ordinary toolchain memcpy. DMA API cycles include validation, fences, commands,",
        "waiting, and statistics; operation cycles omit API validation/statistics; setup cycles cover",
        "the initial fence and CONFIG response. Reads of the cycle counter add fixed measurement cost.",
        "Samples follow deterministic initialization and verification; cache state evolves between samples.",
        "Threshold remains 64 bytes. No hardware or threshold tuning was performed.", "",
        "| Bytes | CPU cycles | DMA API cycles | DMA/CPU | Operation cycles | Setup cycles |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m in micro:
        lines.append(f"| {m['bytes']} | {m['cpu_cycles']} | {m['dma_cycles']} | "
                     f"{m['dma_cycles']/m['cpu_cycles']:.3f} | {m['op_cycles']} | {m['setup_cycles']} |")
    lines += ["", "## Embench comparison", "",
              "Both modes use identical GCC architecture/optimization flags, global scale 1, and warmup heat 1.",
              "All runs use the same DMARocketConfig RTL. CPU mode has ordinary memcpy; DMA mode enables wrapping.",
              "Main cycles cover initialization, warmup, timed benchmark, and verification; startup and reporting",
              "are excluded. Benchmark cycles cover start_trigger through stop_trigger, with no printing inside.",
              "Ratios are DMA cycles / CPU cycles; values above 1 mean more cycles.", "",
              "| Benchmark | CPU main | DMA main | Ratio | CPU timed | DMA timed | Timed ratio | DMA use |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for bench, cpu, dma in pairs:
        use = "hardware DMA" if dma["offloaded"] else "compatibility only"
        lines.append(f"| {bench} | {cpu['main_cycles']} | {dma['main_cycles']} | "
                     f"{dma['main_cycles']/cpu['main_cycles']:.3f} | {cpu['benchmark_cycles']} | "
                     f"{dma['benchmark_cycles']} | {dma['benchmark_cycles']/cpu['benchmark_cycles']:.3f} | {use} |")
    lines += ["", "## Binary memcpy inspection", "",
              "External-reference flags come from the benchmark objects before linking. Call-site counts",
              "come from the linked ELF disassembly and include libc sites, not just benchmark sites.", "",
              "| Benchmark | Benchmark object references memcpy | CPU memcpy sites | DMA wrapper sites |",
              "| --- | --- | ---: | ---: |"]
    for bench in benchmarks:
        cpu_i, dma_i = inspection["cpu"][bench], inspection["dma"][bench]
        lines.append(f"| {bench} | {'yes' if dma_i['benchmark_memcpy_reference'] else 'no'} | "
                     f"{cpu_i['memcpy_call_sites']} | {dma_i['wrapper_call_sites']} |")
    lines += ["", "## DMA-enabled operation counts", "",
              "Counts cover the entire main function, including warmup and verification. Timed-region DMA copies",
              "are also listed. The report's own printf is outside both the counters snapshot and measured intervals.", "",
              "| Benchmark | Intercepted | Offloaded | CPU fallback | Bytes | DMA op cycles | Setup cycles | Timed DMA copies |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for bench, _, d in pairs:
        lines.append(f"| {bench} | {d['intercepted']} | {d['offloaded']} | {d['fallback']} | "
                     f"{d['bytes']} | {d['dma_cycles']} | {d['setup_cycles']} | {d['region_offloaded']} |")
    lines += ["", "Benchmarks with zero DMA copies validate software compatibility; their timing differences",
              "do not demonstrate accelerator speedup or slowdown. Link wrapping includes eligible external libc",
              "references as well as benchmark references; inlined copies and manual loops remain CPU operations.", "",
              "Binary call-site inspection, source hashes, shared compilation flags, ELF hashes, raw simulation logs,",
              "and per-run metrics are retained in build/embench-manifest.json, build/embench-results.json, and build/logs.",
              "See README.md for reproducible commands and every change outside the accelerator source directory.",
              "No required verification was skipped. Performance improvement is not a completion requirement.", ""]
    (WORK / "RESULTS.md").write_text("\n".join(lines))
    (BUILD / "measurements.json").write_text(json.dumps({"microbench": micro, "wrappers": wrappers,
                                                          "embench": records}, indent=2) + "\n")
    print(f"Wrote {WORK / 'RESULTS.md'} for {len(pairs)} CPU/DMA comparisons")


if __name__ == "__main__":
    main()
