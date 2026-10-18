#!/usr/bin/env python3
"""CHIA DMA discovery: initialize, run, resume, replay, and fork."""
from pathlib import Path
import argparse
import asyncio
import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chia_dma.config import ROOT, digest, frozen, load
from chia_dma.artifacts import (RunLock, archive_proposal, git, read_json, snapshot,
                                source_bundle, freeze_loop, write_json)


def bootstrap(cfg):
    for key in ('chia', 'skydiscover', 'evolve_flows'):
        sys.path.insert(0, cfg[key])


def scheduler(cfg, run):
    import ray
    from chia.trace.profiler import start_collector
    from chia.base.cache import start_cache
    from chia.base.bypass import Bypass
    runtime = cfg['runtime']
    options = {'namespace': 'dma-' + digest(str(run))[:24]}
    if runtime['ray_address']:
        options['address'] = runtime['ray_address']
    else:
        options.update(num_cpus=runtime['ray_cpus'], resources={
            'generation': runtime['generation_slots'], 'chipyard': runtime['build_slots'],
            'verilator_run': runtime['simulation_slots']}, _node_ip_address='127.0.0.1')
    # Paths are host-side orchestration paths. Agent containers see none of them.
    options['runtime_env'] = {'env_vars': {'PYTHONPATH': os.pathsep.join([
        cfg.get('_evaluator', str(ROOT)), cfg['chia'], cfg['skydiscover'], cfg['evolve_flows']]),
        'PYTHONHASHSEED': str(cfg['search']['random_seed'])}}
    ray.init(**options)
    available = ray.cluster_resources()
    for resource in ('generation', 'chipyard', 'verilator_run'):
        if available.get(resource, 0) < 1:
            raise RuntimeError(f'CHIA scheduler missing {resource} resource')
    start_collector(log_dir=str(run / 'artifacts' / 'profile'))
    policy = run / 'cache-policy.yaml'
    policy.write_text('cache:\n  generate: true\n  build: true\n  simulate: true\nbypass:\n  generate: true\n  build: true\n  simulate: true\n')
    start_cache(runtime['cache_gb'], str(run / 'cache'), units='GB', yaml_path=str(policy))
    bypass = Bypass(str(policy))
    from chia_dma import tasks
    for name in ('generate', 'build', 'simulate'):
        bypass.set_provider(name, getattr(tasks, 'durable_' + name))
        bypass.set_cond(name, getattr(tasks, 'has_' + name))


def shutdown():
    import ray
    from chia.trace.profiler import stop_collector
    from chia.base.cache import stop_cache
    stop_collector()
    stop_cache()
    ray.shutdown()


def init(cfg, run):
    from chia_dma.setup import preflight, prepare_software
    if (run / 'run.json').exists():
        raise ValueError('Experiment already exists; use resume')
    run.mkdir(parents=True, exist_ok=True)
    freeze_loop(cfg, run)
    proof = preflight(cfg)
    seed = snapshot(cfg)
    manifest = prepare_software(cfg, run, seed)
    metadata = {'run_id': run.name, 'seed': str(seed), 'seed_hash': seed.name,
                'frozen_hash': digest(frozen(cfg)), 'config': cfg, 'preflight': proof,
                'software_hash': manifest['software_hash'],
                'dependencies': {k: git(cfg[k], 'rev-parse', 'HEAD') for k in ('chia', 'skydiscover', 'evolve_flows')}}
    write_json(run / 'run.json', metadata)
    print(f'Initialized {run.name}: seed={seed.name}, {len(manifest["benchmarks"])} Embench binaries per mode', flush=True)
    return metadata


def validate_run(cfg, run):
    meta = read_json(run / 'run.json')
    if meta['frozen_hash'] != digest(frozen(cfg)):
        raise ValueError('Search/evaluation configuration changed since init')
    from chia_dma.setup import preflight, prepare_software
    proof = preflight(cfg)
    if proof['image_id'] != meta['preflight']['image_id']:
        raise ValueError('Container image changed; restore the recorded immutable image')
    for key, revision in meta['dependencies'].items():
        if git(cfg[key], 'rev-parse', 'HEAD') != revision:
            raise ValueError(f'{key} revision differs from the experiment')
    prepare_software(cfg, run, meta['seed'])
    return meta


def fork(cfg, origin, checkpoint, target):
    if target.exists():
        raise ValueError('Fork target already exists')
    cp = origin / 'checkpoints' / checkpoint
    data = read_json(cp / 'state.json')
    if data['frozen_hash'] != digest(frozen(cfg)):
        raise ValueError('Fork configuration differs from historical checkpoint')
    target.mkdir(parents=True)
    if (origin / 'evaluator').exists():
        shutil.copytree(origin / 'evaluator', target / 'evaluator')
    shutil.copytree(origin / 'software', target / 'software')
    shutil.copytree(cp, target / 'checkpoints' / checkpoint)
    # Proposal snapshots are immutable; copy creates a separate writable
    # namespace for all future stage records and build/evaluation artifacts.
    if (origin / 'candidates').exists():
        shutil.copytree(origin / 'candidates', target / 'candidates')
    if (origin / 'reference.json').exists():
        shutil.copyfile(origin / 'reference.json', target / 'reference.json')
    metadata = read_json(origin / 'run.json')
    metadata.update(run_id=target.name, forked_from={'run': origin.name, 'checkpoint': checkpoint})
    write_json(target / 'run.json', metadata)
    write_json(target / 'latest.json', {'checkpoint': checkpoint})
    # Metadata database is independent and is populated from native state/tasks.
    from chia_dma.state import State
    state = State(cfg, target)
    state.sql.execute('UPDATE candidates SET proposal=REPLACE(proposal,?,?)',
                      (str(origin / 'candidates'), str(target / 'candidates')))
    state.checkpoint('forked')
    state.close()
    print(f'Forked {origin.name}/{checkpoint} into {target.name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'config.yaml'))
    parser.add_argument('action', choices=('image', 'doctor', 'init', 'run', 'resume', 'replay', 'fork', 'status'))
    parser.add_argument('--run', default='run-001')
    parser.add_argument('--candidate')
    parser.add_argument('--checkpoint')
    parser.add_argument('--new-run')
    args = parser.parse_args()
    cfg = load(args.config)
    seed = str(cfg['search']['random_seed'])
    if os.path.abspath(sys.executable) != cfg['python'] or os.environ.get('PYTHONHASHSEED') != seed:
        env = {**os.environ, 'PYTHONHASHSEED': seed}
        os.execve(cfg['python'], [cfg['python'], str(Path(__file__).resolve()), *sys.argv[1:]], env)
    bootstrap(cfg)
    if Path(args.run).name != args.run or args.run in ('.', '..'):
        parser.error('--run must be a simple experiment name')
    run = Path(cfg['storage']) / args.run
    archived = run / 'evaluator' / 'loop.py'
    if archived.exists() and Path(__file__).resolve() != archived.resolve() and args.action in ('run', 'resume', 'replay', 'fork'):
        os.execv(cfg['python'], [cfg['python'], str(archived), '--config', cfg['_config_file'], *sys.argv[1:]])
    if archived.exists():
        cfg['_evaluator'] = str(archived.parent)
    if args.action == 'image':
        from chia_dma.containers import ensure_image
        ensure_image(cfg)
        return
    if args.action == 'doctor':
        from chia_dma.setup import preflight
        proof = preflight(cfg)
        print(json.dumps({'image_id': proof['image_id'], 'agent': cfg['agent']['backend'],
                          'authenticated': isinstance(proof['authentication'], dict)}, indent=2))
        return
    if args.action == 'status':
        latest = read_json(run / 'latest.json')
        checkpoint = run / 'checkpoints' / latest['checkpoint']
        state = read_json(checkpoint / 'state.json')
        population = read_json(checkpoint / 'population' / 'metadata.json')
        islands = read_json(checkpoint / 'population' / 'openevolve_native_metadata.json')
        print(json.dumps({'run': run.name, 'checkpoint': latest['checkpoint'],
                          'best': population['best_program_id'],
                          'island_elite_cells': [len(m) for m in islands['island_feature_maps']],
                          'tasks': {k: v['stage'] for k, v in state['tasks'].items()}}, indent=2))
        return
    if args.action == 'fork':
        if not args.checkpoint or not args.new_run or Path(args.new_run).name != args.new_run:
            parser.error('fork requires --checkpoint and a simple --new-run name')
        fork(cfg, run, args.checkpoint, run.parent / args.new_run)
        return
    run.mkdir(parents=True, exist_ok=True)
    with RunLock(run):
        from chia_dma.containers import cleanup_run
        cleanup_run(cfg, run.name)
        if args.action == 'init':
            init(cfg, run)
            return
        if args.action == 'run' and not (run / 'run.json').exists():
            meta = init(cfg, run)
        else:
            meta = validate_run(cfg, run)
        scheduler(cfg, run)
        from chia_dma.state import State
        from chia_dma.search import run_search
        state = State(cfg, run)
        state.sql.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)', (run.name, json.dumps(cfg), meta['seed']))
        try:
            if args.action == 'replay':
                if not args.candidate:
                    parser.error('replay requires --candidate')
                old = read_json(run / 'candidates' / args.candidate / 'proposal.json')
                cid = 'replay-' + uuid.uuid4().hex
                task = {'id': cid, 'parent_id': args.candidate, 'island': old.get('island', 0),
                        'iteration': old.get('iteration', 0), 'backend': old.get('backend'),
                        'model': old.get('model'), 'inspiration_ids': []}
                state.tasks[cid] = task
                archive_proposal(run, cid, old['source'], old['source'], task)
                state.stage(task, 'proposed')
                from chia_dma.evaluator import DMAEvaluator
                result = asyncio.run(DMAEvaluator(cfg, state, meta['seed'], cid).evaluate_program(json.dumps(old['source']), cid))
                state.stage(task, 'updated', result.metrics)
                print(json.dumps(result.metrics, indent=2))
            else:
                asyncio.run(run_search(cfg, state, meta['seed']))
                retries = [t['id'] for t in state.tasks.values() if t['stage'] == 'retry']
                if retries:
                    raise RuntimeError(f'Tasks need resume: {retries}')
                best = state.population.get_best_program()
                print(f'Completed {run.name}; best={best.id} fitness={best.metrics["combined_score"]:.6f}')
        finally:
            state.close()
            shutdown()
            cleanup_run(cfg, run.name)


if __name__ == '__main__':
    main()
