"""Initialize the DMA seed, then let DiscoveryController own the search loop."""

import json
from pathlib import Path
from skydiscover.search.base_database import Program
from .artifacts import archive_proposal, source_bundle
from .controller import create_controller


async def run_search(cfg, state, evaluator, seed):
    if not state.population.programs:
        source = source_bundle(Path(seed) / "tree")
        payload = {"proposal_id": "seed-dma", "source": source}
        initial = Program(
            id="seed-dma",
            solution=json.dumps(payload),
            language="json",
            metadata={"island": 0},
        )
        if not (state.run / "candidates/seed-dma/request.json").exists():
            state.reserve(initial, [], cid="seed-dma", kind="seed")
        archive_proposal(state.run, "seed-dma", source, source, {"backend": None})
        result = await evaluator.evaluate_program(initial.solution, "seed-dma")
        if result.metrics.get("validity") != 1:
            raise RuntimeError("Seed correctness validation failed")
        for island in range(cfg["search"]["num_islands"]):
            state.population.add(
                Program(
                    id=f"seed-{island}",
                    solution=initial.solution,
                    language="json",
                    metrics=result.metrics,
                    metadata={"island": island},
                ),
                iteration=0,
                target_island=island,
            )
        state.checkpoint()
    controller = create_controller(cfg, state, evaluator, seed)
    attempted = sum(r["kind"] == "search" for r in state.requests())
    remaining = len(state.recovery) + max(0, cfg["search"]["iterations"] - attempted)
    try:
        best = await controller.run_discovery(
            state.population.last_iteration + 1,
            remaining,
            checkpoint_callback=state.checkpoint,
            retry_times=1,
        )
        if state.pending():
            raise RuntimeError("Unfinished proposals remain; resume the experiment")
        return best
    finally:
        controller.close()
