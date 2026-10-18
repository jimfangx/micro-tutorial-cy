"""Compose SkyDiscover's full controller using its public component interfaces."""

from pathlib import Path
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


def create_controller(cfg, state, evaluator, seed):
    client = CLIClient(cfg, state, seed)
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
