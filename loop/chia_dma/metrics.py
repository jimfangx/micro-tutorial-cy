"""Parse fixed RTL output and calculate reproducible, correctness-gated fitness."""

import math
import importlib.util
from pathlib import Path
from .config import digest


def parse(name, result, cfg, seed=None):
    """Delegate validation to the existing parser from the immutable seed."""
    if not result.get("success") or result.get("returncode") != 0:
        raise ValueError(f"{name}: simulator failed or timed out")
    root = Path(seed) if seed else Path(cfg["source"])
    path = root / cfg["software"]["metrics_parser"]
    if not path.is_file():
        raise ValueError(
            "Seed predates the required DMA observer/parser; capture a new seed"
        )
    spec = importlib.util.spec_from_file_location("frozen_dma_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.parse(
        result["log"] + "\n" + result["out"],
        "embench" if name.startswith("embench:") else name,
        success=result["success"],
        returncode=result["returncode"],
    )
    extra = {"hardware": data["hardware"], "copy_events": data["copies"]}
    if name == "microbench":
        for row in data["samples"]:
            row["bandwidth_bytes_per_cycle"] = row["bytes"] / row["dma_cycles"]
            row["operation_bandwidth_bytes_per_cycle"] = row["bytes"] / row["op_cycles"]
        return {
            "copies": data["samples"],
            "alignment_samples": data["alignment_samples"],
            **extra,
        }
    return {**data.get("software", {"correct": True}), **extra}


def aggregate(records, reference, cfg, source, hardware=None):
    vector = {}
    groups = {"embench": [], "microbench": []}
    weights = cfg["fitness"]["workload_weights"]
    embench_keys = {k for k in reference if k.startswith("embench:dma:")}
    if not embench_keys or not embench_keys.issubset(records):
        raise ValueError("Full Embench suite results are required")
    for name in sorted(embench_keys):
        row, seed = records[name], reference[name]
        if (row["benchmark_result"], row["verification_result"]) != (
            seed["benchmark_result"],
            seed["verification_result"],
        ):
            raise ValueError(f"{name}: deterministic benchmark output changed")
        activity = (
            "intercepted",
            "offloaded",
            "fallback",
            "bytes",
            "region_offloaded",
            "region_bytes",
        )
        if cfg["fitness"]["require_activity_match"] and any(
            row[k] != seed[k] for k in activity
        ):
            raise ValueError(
                f"{name}: DMA activity changed; investigate before admission"
            )
        speedup = seed["benchmark_cycles"] / row["benchmark_cycles"]
        vector[name] = {
            **row,
            "speedup": speedup,
            "main_speedup": seed["main_cycles"] / row["main_cycles"],
            "correct": True,
            "exercises_dma": bool(seed["region_offloaded"]),
        }
        if seed["region_offloaded"]:
            groups["embench"].append((speedup, weights.get(name, 1.0)))
    seed_copies = {r["bytes"]: r for r in reference["microbench"]["copies"]}
    copies = {r["bytes"]: r for r in records["microbench"]["copies"]}
    if set(copies) != set(seed_copies):
        raise ValueError("Microbenchmark coverage changed")
    for size, row in sorted(copies.items()):
        name = f"microbench:{size}"
        speedup = seed_copies[size]["dma_cycles"] / row["dma_cycles"]
        vector[name] = {**row, "speedup": speedup, "correct": True}
        groups["microbench"].append((speedup, weights.get(name, 1.0)))
    logs, group_weights = [], []
    for group, values in groups.items():
        weight = cfg["fitness"][group + "_weight"]
        if weight < 0:
            raise ValueError("Fitness weights must be nonnegative")
        if not values or weight == 0:
            continue
        if (
            any(w < 0 or not math.isfinite(w) for _, w in values)
            or sum(w for _, w in values) <= 0
        ):
            raise ValueError("Invalid workload weights")
        logs.append(
            weight * sum(w * math.log(s) for s, w in values) / sum(w for _, w in values)
        )
        group_weights.append(weight)
    if not group_weights:
        raise ValueError("No measured workloads have positive fitness weight")
    for metric, limit in (("area", "max_area"), ("delay", "max_delay")):
        maximum = cfg["fitness"][limit]
        if maximum is not None:
            if hardware is None or metric not in hardware or hardware[metric] > maximum:
                raise ValueError(
                    f"Reliable {metric} estimate required and must satisfy {limit}"
                )
    score = math.exp(sum(logs) / sum(group_weights))
    if not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid aggregate score")
    return {
        "combined_score": score,
        "validity": 1,
        "source_bytes": sum(len(s.encode()) for s in source.values()),
        "large_copy_bandwidth": copies[max(copies)]["bandwidth_bytes_per_cycle"],
        "source_hash": digest(source),
    }, vector
