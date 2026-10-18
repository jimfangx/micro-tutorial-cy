"""Prove the public SkyDiscover controller hooks before replacing the driver."""

import asyncio
import json
from pathlib import Path
from skydiscover.config import (
    Config,
    LLMConfig,
    LLMModelConfig,
    SearchConfig,
    OpenEvolveNativeDatabaseConfig,
)
from skydiscover.search.base_database import Program
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from skydiscover.search.default_discovery_controller import (
    DiscoveryController,
    DiscoveryControllerInput,
)
from skydiscover.llm.base import LLMInterface, LLMResponse
from skydiscover.evaluation.evaluation_result import EvaluationResult


def test_public_controller_concurrency_parent_context_and_invalid_rejection(tmp_path):
    generated, evaluated, checkpoints = [], [], []
    active = peak = 0

    class Context:
        def build_prompt(self, current_program, context):
            return {
                "system": "CLI agent",
                "user": json.dumps(
                    {
                        "parent": current_program.id,
                        "island": current_program.metadata["island"],
                        "source": current_program.solution,
                    }
                ),
            }

    class CLI(LLMInterface):
        async def generate(self, system_message, messages, **kwargs):
            nonlocal active, peak
            task = json.loads(messages[-1]["content"])
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03 if task["island"] == 0 else 0.01)
            generated.append(task)
            active -= 1
            return LLMResponse(text="```json\n" + json.dumps(task) + "\n```")

    class Evaluation:
        async def evaluate_program(self, solution, program_id=""):
            task = json.loads(solution)
            evaluated.append(task)
            return EvaluationResult(
                metrics={
                    "validity": int(task["island"] == 0),
                    "combined_score": 2.0 if task["island"] == 0 else 0.0,
                    "source_bytes": 2.0,
                    "large_copy_bandwidth": 2.0,
                }
            )

        def close(self):
            pass

    database = OpenEvolveNativeDatabase(
        "openevolve_native",
        OpenEvolveNativeDatabaseConfig(
            num_islands=2, feature_dimensions=["source_bytes", "large_copy_bandwidth"]
        ),
    )
    for island in (0, 1):
        database.add(
            Program(
                id=f"seed-{island}",
                solution="{}",
                language="json",
                metrics={
                    "validity": 1,
                    "combined_score": 1.0,
                    "source_bytes": 1.0,
                    "large_copy_bandwidth": 1.0,
                },
            ),
            iteration=0,
            target_island=island,
        )
    model = LLMModelConfig(name="cli", init_client=lambda _: CLI())
    config = Config(
        language="json",
        diff_based_generation=False,
        max_parallel_iterations=2,
        checkpoint_interval=1,
        llm=LLMConfig(models=[model]),
        search=SearchConfig(type="openevolve_native"),
    )
    evaluator_file = tmp_path / "evaluate.py"
    evaluator_file.write_text('def evaluate(path): return {"validity":0}\n')
    controller = DiscoveryController(
        DiscoveryControllerInput(config, str(evaluator_file), database)
    )
    controller.context_builder = Context()
    controller.evaluator.close()
    controller.evaluator = Evaluation()
    try:
        asyncio.run(
            controller.run_discovery(
                1, 2, checkpoint_callback=checkpoints.append, retry_times=1
            )
        )
        children = [p for p in database.programs.values() if p.parent_id]
        assert peak == 2 and len(generated) == len(evaluated) == 2
        assert len(children) == 1 and children[0].parent_id == "seed-0"
        assert children[0].metadata["island"] == 0
        assert all(p.metrics["validity"] == 1 for p in database.programs.values())
        assert checkpoints == [1]
    finally:
        controller.close()
