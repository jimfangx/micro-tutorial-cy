"""Parse fixed RTL output and calculate reproducible, correctness-gated fitness."""

import math
import importlib.util
from pathlib import Path
from .config import digest


def parse(result, cfg, seed=None):
    """Delegate validation to the existing parser from the immutable seed."""
    root = Path(seed) if seed else Path(cfg["source"])
    path = root / cfg["software"]["metrics_parser"]
    spec = importlib.util.spec_from_file_location("frozen_dma_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.parse(
        result.get("log", "") + "\n" + result.get("out", ""),
        "embench",
        success=result.get("success", False),
        returncode=result.get("returncode"),
    )
    return {
        **data["software"],
        "hardware": data["hardware"],
        "copy_events": data["copies"],
    }


def aggregate(records, reference, cfg, source):
    vector = {}
    values = []
    timed_bytes = timed_cycles = 0
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
            values.append((speedup, weights.get(name, 1.0)))
            timed_bytes += row["region_bytes"]
            timed_cycles += row["benchmark_cycles"]
    if not values:
        raise ValueError("No Embench workload exercises DMA in its timed region")
    if (
        any(w < 0 or not math.isfinite(w) for _, w in values)
        or sum(w for _, w in values) <= 0
    ):
        raise ValueError("Invalid workload weights")
    score = math.exp(
        sum(w * math.log(s) for s, w in values) / sum(w for _, w in values)
    )
    if not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid aggregate score")
    return {
        "combined_score": score,
        "validity": 1,
        "source_bytes": sum(len(s.encode()) for s in source.values()),
        "dma_bytes_per_cycle": timed_bytes / timed_cycles,
        "source_hash": digest(source),
    }, vector
