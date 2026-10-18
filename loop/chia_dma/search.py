"""Initialize the DMA seed, then let DiscoveryController own the search loop."""

import json
from pathlib import Path
from skydiscover.search.base_database import Program
from .artifacts import source_bundle
from skydiscover.config import (
    Config,
    LLMConfig,
    LLMModelConfig,
    SearchConfig,
    EvaluatorConfig,
)
from skydiscover.search.default_discovery_controller import (
    DiscoveryController,
    DiscoveryControllerInput,
)
from .adapter import CLIClient, ParentContext


def create_controller(cfg, state, evaluator):
    client = CLIClient(cfg, state)
    model = LLMModelConfig(name="chia-cli", init_client=lambda _: client)
    config = Config(
        language="json",
        file_suffix=".json",
        diff_based_generation=False,
        max_solution_length=2_000_000,
        max_parallel_iterations=cfg["search"]["max_in_flight"],
        checkpoint_interval=1,
        evaluator=EvaluatorConfig(cascade_evaluation=False),
        llm=LLMConfig(models=[model]),
        search=SearchConfig(
            type=cfg["search"]["type"],
            num_context_programs=cfg["search"]["inspirations"],
        ),
    )
    controller = DiscoveryController(
        DiscoveryControllerInput(
            config,
            str(Path(__file__).with_name("evaluation.py")),
            state.population,
            output_dir=str(state.run),
        )
    )
    controller.evaluator.close()
    controller.evaluator = evaluator
    controller.context_builder = ParentContext()
    return controller


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
        if not state.sql.query_value(
            "SELECT COUNT(*) FROM proposals WHERE id='seed-dma'"
        ):
            state.reserve(initial, [], cid="seed-dma", kind="seed")
        state.complete("seed-dma", source, {"backend": None})
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
    controller = create_controller(cfg, state, evaluator)
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
