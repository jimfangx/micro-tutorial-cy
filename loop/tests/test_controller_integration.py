"""Real CHIA cache/profile and native controller, using inexpensive fixture tasks."""

import asyncio
import json
from pathlib import Path
import time
import pytest
import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.cache import start_cache, stop_cache
from chia.base.bypass import Bypass
from chia.trace.profiler import start_collector, stop_collector, reset_profiler
from chia.chipyard.state_def import BuildArtifact, RunResult
from skydiscover.search.base_database import Program
from chia_dma.config import load, ALLOWLIST, digest
from chia_dma.artifacts import (
    read_json,
    write_json,
    validate_integration,
)
from chia_dma.state import State, complete, proposal
from chia_dma.search import create_controller
from chia_dma.adapter import CLIClient, ParentContext
from chia_dma.evaluation import DMAEvaluator
from chia_dma.runtime import cache_hit, from_cache


@ray.remote(num_cpus=0)
class Counts:
    def __init__(self):
        self.counts = {}
        self.active = 0
        self.peak = 0
        self.barrier = False

    def add(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1

    def parallel(self):
        self.barrier = True

    def enter(self):
        self.active += 1
        self.peak = max(self.peak, self.active)
        return self.barrier

    def leave(self):
        self.active -= 1

    def read(self):
        return self.counts, self.peak


@pytest.fixture
def flow(tmp_path, monkeypatch, request):
    from chia_dma import adapter, tasks, evaluation

    cfg = load()
    cfg["storage"] = str(tmp_path)
    root = Path(__file__).resolve().parents[1]
    ray.init(
        address="local",
        num_cpus=6,
        resources={"generation": 2, "chipyard": 1, "verilator_run": 2},
        include_dashboard=False,
        namespace="controller-proof",
        _node_ip_address="127.0.0.1",
        runtime_env={
            "env_vars": {
                "PYTHONPATH": ":".join(
                    [
                        str(root),
                        str(root / "tests"),
                        cfg["chia"],
                        cfg["skydiscover"],
                        cfg["evolve_flows"],
                    ]
                )
            }
        },
    )
    request.addfinalizer(ray.shutdown)
    run = tmp_path / "run"
    run.mkdir()
    seed = tmp_path / "seed"
    tree = seed / "tree"
    source = {
        ALLOWLIST[0]: "class Controller {}\n",
        ALLOWLIST[
            1
        ]: "class Adapter {\n io.mem.req.valid := false.B\n}\nclass WithRoCCDMA() extends Config(fixed)\n",
    }
    for name, text in source.items():
        p = tree / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    fixture = root / "tests/fixtures/embench-observation.txt"
    parser = tree / cfg["software"]["metrics_parser"]
    parser.parent.mkdir(parents=True)
    parser.write_text(
        (Path(cfg["source"]) / cfg["software"]["metrics_parser"]).read_text()
    )
    output = fixture.read_text()
    elf = b"fixture ELF"
    write_json(
        run / "software/manifest.json",
        {
            "workloads": {
                n: {"file": n + ".elf", "sha256": digest(elf)}
                for n in ("embench:dma:aha-mont64", "embench:dma:crc32")
            }
        },
    )
    for name in ("embench:dma:aha-mont64", "embench:dma:crc32"):
        (run / "software" / (name + ".elf")).write_bytes(elf)
    counts = Counts.remote()

    @ChiaFunction(resources={"generation": 1})
    def generate(cfg, sql, task):
        saved = proposal(sql, task["id"], remote=True)
        if saved:
            return saved
        ray.get(counts.add.remote("generate"))
        if ray.get(counts.enter.remote()):
            for _ in range(250):
                if ray.get(counts.read.remote())[1] >= 2:
                    break
                time.sleep(0.02)
        time.sleep(0.1)
        parent = json.loads(task["parent"]["solution"])["source"]
        child = dict(parent)
        child[ALLOWLIST[0]] += "// " + task["id"] + " " + cfg["agent"]["backend"] + "\n"
        if cfg["agent"]["backend"] == "antigravity" and task["island"] == 1:
            child[ALLOWLIST[1]] += "// forbidden integration change\n"
        saved = complete(
            sql,
            task["id"],
            child,
            {
                "backend": cfg["agent"]["backend"],
                "model": "fixture",
                "response": "fixture",
            },
            remote=True,
        )
        ray.get(counts.leave.remote())
        return saved

    @ChiaFunction(resources={"chipyard": 1})
    def build(cfg, seed, child):
        ray.get(counts.add.remote("build"))
        validate_integration(child, source)
        return BuildArtifact(
            "fixture",
            b"ELF",
            "fixture",
            "DMARocketConfig",
            "chipyard",
            "verilator",
            True,
            "",
            "",
            0,
        )

    @ChiaFunction(resources={"verilator_run": 1})
    def simulate(cfg, artifact, content, name, ini):
        ray.get(counts.add.remote("simulate"))
        return RunResult(name, output, "", 0, True)

    monkeypatch.setattr(adapter, "generate", generate)
    monkeypatch.setattr(tasks, "build", build)
    monkeypatch.setattr(tasks, "simulate", simulate)

    def aggregate(records, reference, cfg, source):
        assert set(reference) == set(records)
        return (
            {
                "validity": 1,
                "combined_score": 1.0,
                "source_bytes": sum(map(len, source.values())),
                "dma_bytes_per_cycle": 1.0,
            },
            {},
        )

    monkeypatch.setattr(evaluation, "aggregate", aggregate)
    start_collector(log_dir=str(tmp_path / "profile"))
    reset_profiler()
    cache = start_cache(
        2, str(tmp_path / "cache"), units="MB", yaml_path=cfg["_config_file"]
    )
    bypass = Bypass(cfg["_config_file"])
    for name in ("build", "simulate"):
        bypass.set_cond(name, cache_hit)
        bypass.set_provider(name, from_cache)
    state = State(cfg, run)
    parent = Program(
        id="seed-dma",
        solution=json.dumps({"proposal_id": "seed-dma", "source": source}),
        language="json",
        metadata={"island": 0},
    )
    state.reserve(parent, [], cid="seed-dma", kind="seed")
    state.complete("seed-dma", source, {"backend": None})
    evaluator = DMAEvaluator(cfg, state, seed)
    result = evaluator.evaluate(parent.solution)
    assert result.metrics["validity"] == 1, state.sql.query("SELECT * FROM benchmarks")
    assert not (run / "reference.json").exists()
    parent.metrics = result.metrics
    for island in (0, 1):
        p = Program(**parent.to_dict())
        p.id = f"seed-{island}"
        state.population.add(p, iteration=0, target_island=island)
    state.checkpoint()
    yield cfg, state, seed, evaluator, counts, cache, tmp_path
    state.close()
    stop_cache()
    stop_collector()
    reset_profiler()
    ray.shutdown()


def test_full_controller_concurrent_context_invalid_cache_profile_and_resume(flow):
    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    get(counts.parallel.remote())
    controller = create_controller(cfg, state, evaluator)
    asyncio.run(
        controller.run_discovery(
            1, 2, checkpoint_callback=state.checkpoint, retry_times=1
        )
    )
    values, peak = get(counts.read.remote())
    assert peak == 2
    assert values == {"build": 3, "simulate": 4, "generate": 2}
    requests = [r for r in state.requests() if r["kind"] == "search"]
    assert {r["island"] for r in requests} == {0, 1}
    valid = next(r for r in requests if r["island"] == 0)
    invalid = next(r for r in requests if r["island"] == 1)
    assert valid["id"] in state.admitted and invalid["id"] not in state.admitted
    assert state.result(invalid["id"])["validity"] == 0
    assert all(p.metrics["validity"] == 1 for p in state.population.programs.values())
    saved = read_json(state.run / "latest.json")["checkpoint"]
    native = read_json(
        state.run / "checkpoints" / saved / "population/openevolve_native_metadata.json"
    )
    assert len(native["islands"]) == 2
    p = state.proposal(valid["id"])
    # New query identity, identical source: numerical history is cold, CHIA is warm.
    for flush in (False, True):
        if flush:
            get(cache.flush.chia_remote())
        r = state.reserve(Program(**valid["parent"]), [], kind="check")
        state.complete(r["id"], p["source"], {})
        result = evaluator.evaluate(
            json.dumps({"proposal_id": r["id"], "source": p["source"]})
        )
        assert result.metrics["validity"] == 1
        current, _ = get(counts.read.remote())
        assert current["build"] == values["build"] + int(flush)
        assert current["simulate"] == values["simulate"] + 2 * int(flush)
        assert current["generate"] == 2
    state.close()
    restored = State(cfg, state.run)
    try:
        assert restored.admitted == state.admitted and restored.pending() == []
        assert (
            restored.population.island_feature_maps
            == state.population.island_feature_maps
        )
    finally:
        restored.close()
    stop_collector()
    logs = list((tmp_path / "profile").glob("**/*log"))
    assert logs and any(
        "build" in p.read_text() and "simulate" in p.read_text() for p in logs
    )


def test_generation_survives_interruption_without_another_llm_call(flow):
    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    parent, _ = state.population.sample()
    prompt = ParentContext().build_prompt(parent, {})
    client = CLIClient(cfg, state)
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    before, _ = get(counts.read.remote())
    assert before["generate"] == 1
    request = state.request(parent.metadata["request_id"])
    state.close()
    cfg["agent"]["backend"] = "codex"
    restored = State(cfg, state.run)
    resumed = DMAEvaluator(cfg, restored, seed)
    controller = create_controller(cfg, restored, resumed)
    try:
        asyncio.run(
            controller.run_discovery(
                1, 1, checkpoint_callback=restored.checkpoint, retry_times=1
            )
        )
        after, _ = get(counts.read.remote())
        assert after["generate"] == 1
        assert request["id"] in restored.admitted
        child = next(
            p
            for p in restored.population.programs.values()
            if p.metadata["candidate_id"] == request["id"]
        )
        assert (
            child.parent_id == request["parent"]["id"]
            and child.metadata["island"] == request["island"]
        )
        original = restored.proposal(request["id"])
        assert original["backend"] == "antigravity"
        # A newly sampled proposal uses the changed backend.
        asyncio.run(
            controller.run_discovery(
                2, 1, checkpoint_callback=restored.checkpoint, retry_times=1
            )
        )
        latest = max(restored.requests(), key=lambda r: r["sequence"])
        assert restored.proposal(latest["id"])["backend"] == "codex"
    finally:
        restored.close()


def test_historical_fork_retains_pending_context_excludes_future_and_replay_is_independent(
    flow,
):
    from chia_dma.state import fork

    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    write_json(
        state.run / "run.json",
        {"run_id": state.run.name, "seed": str(seed)},
    )
    parent, _ = state.population.sample()
    selected = state.request(parent.metadata["request_id"])
    # Checkpoint a selection whose generation has not yet completed.
    cp = state.checkpoint()
    cfg["agent"]["backend"] = "codex"
    client = CLIClient(cfg, state)
    prompt = ParentContext().build_prompt(parent, {})
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    controller = create_controller(cfg, state, evaluator)
    state.recovery = state.pending()
    asyncio.run(
        controller.run_discovery(
            1, 1, checkpoint_callback=state.checkpoint, retry_times=1
        )
    )
    assert selected["id"] in state.admitted
    original_latest = (state.run / "latest.json").read_bytes()
    child_path = tmp_path / "fork"
    fork(state.run, cp, child_path)
    child = State(cfg, child_path)
    try:
        assert child.admitted == {"seed-dma"}
        assert [r["id"] for r in child.pending()] == [selected["id"]]
        assert child.request(selected["id"]) == selected
        assert child.proposal(selected["id"]) is None
        assert (
            child.sql.query_value(
                "SELECT backend FROM proposals WHERE id=?", (selected["id"],)
            )
            == selected["backend"]
        )
        assert child.result(selected["id"]) is None
        before_resume, _ = get(counts.read.remote())
        branch_evaluator = DMAEvaluator(cfg, child, seed)
        branch_controller = create_controller(cfg, child, branch_evaluator)
        asyncio.run(
            branch_controller.run_discovery(
                1, 1, checkpoint_callback=child.checkpoint, retry_times=1
            )
        )
        assert get(counts.read.remote())[0]["generate"] == before_resume["generate"] + 1
        assert selected["id"] in child.admitted
        # A historical replay uses a new tag and never calls the generator/add.
        p = state.proposal(selected["id"])
        request = child.reserve(Program(**selected["parent"]), [], kind="replay")
        child.complete(request["id"], p["source"], {})
        before, _ = get(counts.read.remote())
        replay = DMAEvaluator(cfg, child, seed)
        result = replay.evaluate(
            json.dumps(
                {"proposal_id": request["id"], "source": p["source"], "replay": True}
            )
        )
        after, _ = get(counts.read.remote())
        assert (
            result.metrics["validity"] == 1 and after["generate"] == before["generate"]
        )
        assert (
            after["build"] == before["build"] + 1
            and after["simulate"] == before["simulate"] + 2
        )
        assert child.admitted == {"seed-dma", selected["id"]}
        assert (state.run / "latest.json").read_bytes() == original_latest
    finally:
        child.close()


def test_completed_evaluation_before_admission_recovers_from_sql_without_generation(
    flow,
):
    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    parent, _ = state.population.sample()
    prompt = ParentContext().build_prompt(parent, {})
    client = CLIClient(cfg, state)
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    from skydiscover.utils.code_utils import parse_full_rewrite

    payload = parse_full_rewrite(response.text, "json")
    result = evaluator.evaluate(payload, "lost-program-id")
    assert result.metrics["validity"] == 1
    before, _ = get(counts.read.remote())
    state.close()
    restored = State(cfg, state.run)
    try:
        resumed = DMAEvaluator(cfg, restored, seed)
        controller = create_controller(cfg, restored, resumed)
        asyncio.run(
            controller.run_discovery(
                1, 1, checkpoint_callback=restored.checkpoint, retry_times=1
            )
        )
        assert get(counts.read.remote())[0] == before
        cid = json.loads(payload)["proposal_id"]
        assert cid in restored.admitted
        actual = restored.sql.query_value(
            "SELECT program_id FROM proposals WHERE id=?", (cid,)
        )
        assert actual != "lost-program-id" and actual in restored.population.programs
    finally:
        restored.close()


def test_online_candidate_fork_restores_full_state_and_continues_independently(flow):
    from chia_dma.artifacts import RunLock
    from chia_dma.state import fork

    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    cfg["agent"]["backend"] = "codex"
    controller = create_controller(cfg, state, evaluator)
    target = tmp_path / "other-copy/loop/experiments/branch"
    write_json(state.run / "run.json", {"run_id": state.run.name, "seed": str(seed)})

    async def branch_while_running():
        await controller.run_discovery(
            1, 1, checkpoint_callback=state.checkpoint, retry_times=1
        )
        selected = state.requests()[-1]["id"]
        checkpoint = read_json(state.run / "latest.json")["checkpoint"]
        continuing = asyncio.create_task(
            controller.run_discovery(
                2, 3, checkpoint_callback=state.checkpoint, retry_times=1
            )
        )
        for _ in range(500):
            if get(counts.read.remote())[0]["generate"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert not continuing.done()
        await asyncio.to_thread(fork, state.run, None, target, candidate=selected)
        await continuing
        return selected, checkpoint

    with RunLock(state.run):
        selected, checkpoint = asyncio.run(branch_while_running())
    parent_requests = state.requests()
    parent_latest = (state.run / "latest.json").read_bytes()
    child = State(cfg, target)
    try:
        assert read_json(target / "latest.json")["checkpoint"] == checkpoint
        assert child.admitted == {"seed-dma", selected} and not child.pending()
        assert child.benchmarks("seed-dma") == state.benchmarks("seed-dma")
        assert set(child.population.programs) == {
            p.stem
            for p in (
                state.run / "checkpoints" / checkpoint / "population/programs"
            ).glob("*.json")
        }
        assert len(child.requests()) == 2 and len(parent_requests) == 5
        native = Path("population/openevolve_native_metadata.json")
        assert (target / "checkpoints" / checkpoint / native).read_bytes() == (
            state.run / "checkpoints" / checkpoint / native
        ).read_bytes()
        resumed = create_controller(cfg, child, DMAEvaluator(cfg, child, seed))
        asyncio.run(
            resumed.run_discovery(
                child.population.last_iteration + 1,
                1,
                checkpoint_callback=child.checkpoint,
                retry_times=1,
            )
        )
        assert len(child.requests()) == 3
        assert state.requests() == parent_requests
        assert (state.run / "latest.json").read_bytes() == parent_latest
    finally:
        child.close()


def test_native_migration_preserves_original_candidate_ids(flow):
    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    cfg["agent"]["backend"] = "codex"
    # Prefer unique children over the seed already present on both islands.
    for program in state.population.programs.values():
        program.metrics["combined_score"] = 0.5
    controller = create_controller(cfg, state, evaluator)
    asyncio.run(
        controller.run_discovery(
            1, 8, checkpoint_callback=state.checkpoint, retry_times=1
        )
    )
    assert state.population.last_migration_generation > 0
    assert len(state.requests()) == 9 and len(state.admitted) == 9
    assert all(
        p.metadata["candidate_id"] in state.admitted
        for p in state.population.programs.values()
    )
    assert any(p.metadata.get("migrant") for p in state.population.programs.values())


def test_cached_evaluation_timeout_cancels_raw_ref_and_preserves_completed_proposal(
    flow, monkeypatch
):
    from chia_dma import tasks, evaluation

    cfg, state, seed, evaluator, counts, cache, tmp_path = flow
    parent = next(iter(state.population.programs.values()))
    request = state.reserve(parent, [], kind="search")
    source = json.loads(parent.solution)["source"]
    source[ALLOWLIST[0]] += "// timeout case\n"
    state.complete(request["id"], source, {})

    @ChiaFunction(resources={"chipyard": 1}, max_retries=0)
    def build(cfg, seed, source):
        time.sleep(60)

    monkeypatch.setattr(tasks, "build", build)
    cfg["runtime"]["task_timeout_seconds"] = 0
    original_cancel = evaluation.chia_cancel
    cancelled = []

    def cancel(ref, **kwargs):
        assert isinstance(ref, ray.ObjectRef)
        cancelled.append(ref)
        return original_cancel(ref, **kwargs)

    monkeypatch.setattr(evaluation, "chia_cancel", cancel)
    with pytest.raises(TimeoutError, match="CHIA task deadline"):
        evaluator.evaluate(json.dumps({"proposal_id": request["id"], "source": source}))
    assert len(cancelled) == 1 and state.result(request["id"]) is None
    assert state.proposal(request["id"])["source_hash"] == digest(source)
