"""Fixed-output, isolation, and recovery tests; fixture data comes from real RTL."""

from pathlib import Path
import copy
import json
import random
import pytest

from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.artifacts import (
    archive_proposal,
    read_json,
    source_bundle,
    validate_bundle,
)
from chia_dma.metrics import aggregate, parse


@pytest.fixture
def cfg(tmp_path):
    c = load()
    c["storage"] = str(tmp_path / "storage")
    return c


@pytest.fixture
def records(cfg):
    data = json.loads(
        (Path(__file__).parent / "fixtures/rtl-measurements.json").read_text()
    )
    result = {}
    for row in data["embench"]:
        name = f'embench:{row["mode"]}:{row["benchmark"]}'
        # Historical full-suite fixture predates metrics version 1. It is input
        # to aggregation tests only; the runtime parser must reject it now.
        result[name] = dict(row)
    copies = copy.deepcopy(data["microbench"])
    for row in copies:
        row["bandwidth_bytes_per_cycle"] = row["bytes"] / row["dma_cycles"]
    result["microbench"] = {"copies": copies}
    return result


def test_real_rtl_reference_and_group_weighted_geomean(cfg, records):
    score, vector = aggregate(records, records, cfg, {p: "source" for p in ALLOWLIST})
    assert score["combined_score"] == 1
    assert len([k for k in vector if k.startswith("embench:")]) == len(records) // 2
    faster = copy.deepcopy(records)
    for name, row in faster.items():
        if name.startswith("embench:dma:"):
            row["benchmark_cycles"] /= 4
    cfg["fitness"].update(embench_weight=1, microbench_weight=1)
    score, _ = aggregate(faster, records, cfg, {p: "source" for p in ALLOWLIST})
    assert score["combined_score"] == pytest.approx(2)


def test_activity_change_missing_workload_and_wrong_output_rejected(cfg, records):
    key = next(
        k for k, r in records.items() if k.startswith("embench:dma:") and r["offloaded"]
    )
    for field in ("bytes", "offloaded", "benchmark_result"):
        bad = copy.deepcopy(records)
        bad[key][field] += 1
        with pytest.raises(ValueError):
            aggregate(bad, records, cfg, {p: "source" for p in ALLOWLIST})
    bad = copy.deepcopy(records)
    del bad[key]
    with pytest.raises(ValueError, match="Full Embench"):
        aggregate(bad, records, cfg, {p: "source" for p in ALLOWLIST})


def test_failed_or_incomplete_simulation_never_yields_score(cfg):
    for result in (
        {"success": False, "returncode": -1},
        {"success": True, "returncode": 0, "log": "EMBENCH result=0", "out": ""},
    ):
        with pytest.raises(ValueError):
            parse("embench:dma:wikisort", result, cfg)
    with pytest.raises(ValueError):
        parse(
            "microbench",
            {
                "success": True,
                "returncode": 0,
                "log": "DMA microbenchmark PASS",
                "out": "",
            },
            cfg,
        )


def test_passive_hardware_metrics_are_required_by_chia(cfg, tmp_path):
    cfg["software"]["standalone"]["basic"] = {"marker": "DMA basic PASS"}
    fixture = (
        Path(__file__).resolve().parents[2]
        / "software/rocc-dma/tests/fixtures/observed-basic.txt"
    )
    parser = tmp_path / "tree/software/rocc-dma/scripts/metrics.py"
    parser.parent.mkdir(parents=True)
    parser.write_text((fixture.parents[2] / "scripts/metrics.py").read_text())
    text = fixture.read_text()
    result = {"success": True, "returncode": 0, "log": text, "out": ""}
    row = parse("basic", result, cfg, tmp_path / "tree")
    assert row["hardware"]["write_acks"] == 1
    assert row["copy_events"][0]["hw_cycles"] == 34
    with pytest.raises(ValueError):
        parse(
            "basic",
            {**result, "log": text.replace("outstanding=0", "outstanding=1")},
            cfg,
            tmp_path / "tree",
        )


def test_proposal_commit_is_immutable_and_has_exact_allowlist(tmp_path):
    bundle = {p: "original\n" for p in ALLOWLIST}
    modified = dict(bundle)
    modified[ALLOWLIST[0]] += "// proposal\n"
    archive_proposal(
        tmp_path, "candidate", modified, bundle, {"parent_id": "seed", "iteration": 1}
    )
    saved = read_json(tmp_path / "candidates/candidate/proposal.json")
    assert saved["source_hash"] == digest(modified)
    assert source_bundle(tmp_path / "candidates/candidate/rtl") == modified
    with pytest.raises(ValueError):
        archive_proposal(tmp_path, "candidate", bundle, bundle, {})
    with pytest.raises(ValueError):
        validate_bundle({**bundle, "tests/cheat.c": "bad"})


def test_all_three_real_chia_agent_classes_are_configurable(cfg):
    from chia_dma.agents import make_agent

    names = {
        "antigravity": "AntigravityLLM",
        "claude": "ClaudeCodeLLM",
        "codex": "CodexLLM",
    }
    for backend, classname in names.items():
        cfg["agent"]["backend"] = backend
        agent = make_agent(cfg)
        assert type(agent).__name__ == classname
        assert agent.timeout_seconds == cfg["agent"]["timeout_seconds"]


def test_allowlisted_file_cannot_change_fixed_soc_mixin_or_l1():
    from chia_dma.artifacts import validate_integration

    source = {p: "source" for p in ALLOWLIST}
    source[ALLOWLIST[1]] = (
        "class Accelerator {\n  io.mem.req.valid := false.B\n}\nclass WithRoCCDMA(x: Int) extends Config(fixed)\n"
    )
    candidate = dict(source)
    candidate[ALLOWLIST[1]] = candidate[ALLOWLIST[1]].replace(
        "Config(fixed)", "Config(cheating)"
    )
    with pytest.raises(ValueError, match="integration"):
        validate_integration(candidate, source)
    candidate[ALLOWLIST[1]] = source[ALLOWLIST[1]].replace("false.B", "true.B")
    with pytest.raises(ValueError, match="L1"):
        validate_integration(candidate, source)
    validate_integration(source, source)
