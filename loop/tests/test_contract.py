"""Fixed-output, isolation, and recovery tests; fixture data comes from real RTL."""

from pathlib import Path
import copy
import json
import pytest

from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.artifacts import validate_bundle
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
        # Recorded cycle/activity inputs exercise aggregation; parser validation
        # is covered separately with complete version-1 observations.
        result[name] = dict(row)
    return result


def test_embench_reference_and_weighted_geomean(cfg, records):
    score, vector = aggregate(records, records, cfg, {p: "source" for p in ALLOWLIST})
    assert score["combined_score"] == 1
    assert len([k for k in vector if k.startswith("embench:")]) == len(records) // 2
    faster = copy.deepcopy(records)
    for name, row in faster.items():
        if name.startswith("embench:dma:"):
            row["benchmark_cycles"] /= 4
    score, _ = aggregate(faster, records, cfg, {p: "source" for p in ALLOWLIST})
    assert score["combined_score"] == pytest.approx(4)
    active = [
        r
        for k, r in records.items()
        if k.startswith("embench:dma:") and r["region_offloaded"]
    ]
    assert score["dma_bytes_per_cycle"] == pytest.approx(
        sum(r["region_bytes"] for r in active)
        / sum(r["benchmark_cycles"] / 4 for r in active)
    )
    key = next(
        k
        for k, r in records.items()
        if k.startswith("embench:dma:") and r["region_offloaded"]
    )
    cfg["fitness"]["workload_weights"] = {key: 2}
    faster[key]["benchmark_cycles"] *= 4
    score, _ = aggregate(faster, records, cfg, {p: "source" for p in ALLOWLIST})
    assert score["combined_score"] == pytest.approx(
        4 ** ((len(active) - 1) / (len(active) + 1))
    )


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
            parse(result, cfg)


def test_passive_hardware_metrics_are_required_by_chia(cfg, tmp_path):
    fixture = Path(__file__).parent / "fixtures/embench-observation.txt"
    parser = tmp_path / "tree/software/rocc-dma/scripts/metrics.py"
    parser.parent.mkdir(parents=True)
    parser.write_text(
        (Path(cfg["source"]) / cfg["software"]["metrics_parser"]).read_text()
    )
    text = fixture.read_text()
    result = {"success": True, "returncode": 0, "log": text, "out": ""}
    row = parse(result, cfg, tmp_path / "tree")
    assert row["hardware"]["write_acks"] == 1
    assert row["copy_events"][0]["hw_cycles"] == 34
    with pytest.raises(ValueError):
        parse(
            {**result, "log": text.replace("outstanding=0", "outstanding=1")},
            cfg,
            tmp_path / "tree",
        )


def test_proposal_commit_is_immutable_and_has_exact_allowlist(tmp_path, cfg):
    from chia_dma.state import State
    from skydiscover.search.base_database import Program

    import ray

    ray.init(
        address="local",
        num_cpus=1,
        include_dashboard=False,
        object_store_memory=128 * 1024 * 1024,
    )
    state = State(cfg, tmp_path)
    try:
        bundle = {p: "original\n" for p in ALLOWLIST}
        modified = {**bundle, ALLOWLIST[0]: "original\n// proposal\n"}
        state.reserve(
            Program(
                id="seed",
                solution=json.dumps({"source": bundle}),
                metadata={"island": 0},
            ),
            [],
            cid="candidate",
        )
        saved = state.complete(
            "candidate", modified, {"response": "generated", "backend": "codex"}
        )
        assert saved["source_hash"] == digest(modified) and saved["source"] == modified
        assert saved["generation"]["response"] == "generated"
        assert not (tmp_path / "candidates").exists()
        with pytest.raises(ValueError):
            state.complete("candidate", bundle, {})
        with pytest.raises(ValueError):
            validate_bundle({**bundle, "tests/cheat.c": "bad"})
    finally:
        state.close()
        ray.shutdown()


def test_all_three_real_chia_agent_classes_are_configurable(cfg):
    from chia_dma.adapter import make_agent

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


def test_antigravity_overrides_are_optional_and_only_change_requested_private_fields(
    cfg, tmp_path, monkeypatch
):
    from chia_dma.adapter import override_antigravity

    monkeypatch.setenv("HOME", str(tmp_path / "private-home"))
    folder = Path.home() / ".gemini/antigravity-cli"
    folder.mkdir(parents=True)
    settings = folder / "settings.json"
    token = folder / "antigravity-oauth-token"
    settings.write_text(
        json.dumps(
            {
                "gcp": {"project": "saved-project", "location": "saved-region"},
                "keep": True,
            }
        )
    )
    token.write_text(
        json.dumps(
            {"project_id": "saved-project", "region": "saved-region", "keep": True}
        )
    )
    before = settings.read_bytes(), token.read_bytes()
    override_antigravity(cfg)
    assert (settings.read_bytes(), token.read_bytes()) == before
    cfg["agent"]["backends"]["antigravity"]["gcp_project"] = "explicit-project"
    override_antigravity(cfg)
    assert json.loads(settings.read_text()) == {
        "gcp": {"project": "explicit-project", "location": "saved-region"},
        "keep": True,
    }
    assert json.loads(token.read_text()) == {
        "project_id": "explicit-project",
        "region": "saved-region",
        "keep": True,
    }
