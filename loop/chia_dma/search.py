"""Minimal asynchronous CLI-agent adapter around OpenEvolve's sample/add APIs.

No evolutionary algorithm or population serializer is implemented here.
"""
from pathlib import Path
import asyncio
import json
import uuid
from skydiscover.search.base_database import Program
from chia.base.ChiaFunction import get
from .artifacts import archive_proposal, read_json, source_bundle
from .config import ALLOWLIST, digest
from .evaluator import DMAEvaluator
from .tasks import generate

CONSTRAINTS = '''Improve the existing bare-metal RoCC DMA implementation. Preserve the RV64
custom0 command ABI, validation/status/error semantics, memory ordering and
coherence. Use only the existing tlNode connection. Do not add L1 access, SoC
ports, other bus topology, profiling counters, or alter Rocket/cache/SoC or
software/testbench/evaluation behavior. The shim, threshold and benchmark flags
are frozen. Changes must perform real RTL DMA transfers and pass every test.
Keep WithRoCCDMA and the disabled io.mem assignments unchanged.
Only the two supplied accelerator Scala files are writable. Directly overwrite
file contents (e.g. Python open(path, "w")); atomic rename editors cannot replace
read-only bind-mount entries. Do not attempt to change permissions or mount
points. Generation is edit-only: do not compile or run tests in this workspace.
Read at most six files, make one small concrete change, then finish immediately.
The separate evaluator owns compilation and testing; do not tune or iterate here.
'''


def prompt(cfg, parent, inspirations, island):
    guidance = cfg['search']['island_guidance']
    context = {'parent_id': parent.id, 'parent_metrics': parent.metrics,
               'parent_source': json.loads(parent.solution),
               'island': island, 'guidance': guidance[island % len(guidance)] if guidance else '',
               'inspirations': [{'id': p.id, 'metrics': p.metrics, 'source': json.loads(p.solution)} for p in inspirations]}
    return CONSTRAINTS + '\nSearch context:\n' + json.dumps(context, indent=2)


def select(cfg, state):
    population = state.population
    island = population.current_island
    parent, inspirations = population.sample(num_context_programs=cfg['search']['inspirations'])
    iteration = state.next_iteration
    state.next_iteration += 1
    task = {'id': 'candidate-' + uuid.uuid4().hex, 'iteration': iteration,
            'island': island, 'parent_id': parent.id, 'parent_source': json.loads(parent.solution),
            'inspiration_ids': [p.id for p in inspirations], 'prompt': prompt(cfg, parent, inspirations, island),
            'backend': cfg['agent']['backend'], 'model': cfg['agent']['model']}
    state.tasks[task['id']] = task
    state.stage(task, 'selected', {'parent': parent.id, 'inspirations': task['inspiration_ids']})
    return task


async def process(cfg, state, seed, task):
    cid = task['id']
    proposal_file = state.run / 'candidates' / cid / 'proposal.json'
    try:
        if not proposal_file.exists():
            state.stage(task, 'dispatching')
            proposal = await asyncio.to_thread(get, generate.chia_remote(
                cfg, str(state.run), str(seed), task,
                _chia_tag=f'{state.run.name}/{cid}/generation/{digest(task["prompt"])}'))
        else:
            proposal = read_json(proposal_file)
        if task['stage'] not in ('evaluated', 'built'):
            state.stage(task, 'proposed', {'source_hash': proposal['source_hash']})
        evaluator = DMAEvaluator(cfg, state, seed, cid)
        result = await evaluator.evaluate_program(json.dumps(proposal['source']), cid)
        if result.metrics.get('validity') == 1:
            # Explicit island captured at selection prevents out-of-order
            # completion from moving candidates into the current sampling island.
            state.population.add(Program(
                id=cid, solution=json.dumps(proposal['source'], sort_keys=True), language='scala',
                metrics=result.metrics, parent_id=task['parent_id'],
                other_context_ids=task['inspiration_ids'], iteration_found=task['iteration'],
                metadata={'island': task['island'], 'source_hash': proposal['source_hash'],
                          'backend': proposal['backend'], 'model': proposal['model']},
                prompts={'generation': task['prompt']}),
                iteration=task['iteration'], target_island=task['island'])
        # Invalid candidates remain diagnostic records; they never enter sample()
        # or add(), even if their forged/missing metrics would suggest high speed.
        state.stage(task, 'updated', {'validity': result.metrics.get('validity'),
                                     'combined_score': result.metrics.get('combined_score', 0.0)})
        print(f'{cid}: validity={result.metrics.get("validity")} fitness={result.metrics.get("combined_score", 0):.6f}', flush=True)
    except Exception as error:
        # Infrastructure/agent failures are retryable on resume, with any
        # completed proposal/build/sim reused from durable artifacts.
        task['error'] = str(error)
        state.stage(task, 'retry', {'error': str(error)})
        print(f'{cid}: retry required: {error}', flush=True)


async def run_search(cfg, state, seed):
    if not state.population.programs:
        bundle = source_bundle(Path(seed) / 'tree')
        cid = 'seed-dma'
        task = {'id': cid, 'iteration': 0, 'island': 0, 'parent_id': None,
                'backend': None, 'model': None, 'inspiration_ids': []}
        state.tasks[cid] = task
        archive_proposal(state.run, cid, bundle, bundle, task)
        state.stage(task, 'proposed')
        evaluator = DMAEvaluator(cfg, state, seed, cid, baseline=True)
        result = await evaluator.evaluate_program(json.dumps(bundle), cid)
        if result.metrics.get('validity') != 1:
            raise RuntimeError(f'Seed RTL/software validation failed: {result.artifacts}')
        # Seed all islands with the same measured implementation via native add.
        for island in range(cfg['search']['num_islands']):
            pid = cid if island == 0 else f'seed-island-{island}'
            state.population.add(Program(id=pid, solution=json.dumps(bundle, sort_keys=True),
                                        language='scala', metrics=result.metrics,
                                        parent_id=cid if island else None,
                                        metadata={'island': island, 'source_hash': digest(bundle)}),
                                 iteration=0, target_island=island)
        state.stage(task, 'updated')
    pending = [t for t in state.tasks.values() if t['stage'] != 'updated' and t['id'] != 'seed-dma']
    limit = cfg['runtime']['generation_slots'] + cfg['runtime']['build_slots']
    active = set()
    while pending or active or state.next_iteration <= cfg['search']['iterations']:
        while len(active) < limit and (pending or state.next_iteration <= cfg['search']['iterations']):
            task = pending.pop(0) if pending else select(cfg, state)
            active.add(asyncio.create_task(process(cfg, state, seed, task)))
        if active:
            _, active = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
    state.checkpoint('finished')
