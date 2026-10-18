"""CHIA-scheduled tasks. Independent generation/build/simulation resources."""
from pathlib import Path
import json
import base64
import shutil
import tempfile
import time
from chia.base.ChiaFunction import ChiaFunction
from chia.trace.profiler import get_profiler
from .artifacts import (archive_proposal, copy_tree, files, read_json, source_bundle,
                        validate_bundle, validate_integration, write_json)
from .config import ALLOWLIST, digest
from .containers import agent_mounts, run_worker


def event(operation, **metadata):
    profiler = get_profiler()
    import ray
    metadata['task_id'] = str(ray.get_runtime_context().get_task_id())
    profiler.add_info({'operation': operation, **metadata})
    profiler.log_event('dma_' + operation, **metadata)


def save_files(dest, response):
    for name, data in response.pop('_files', {}).items():
        p = Path(dest) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@ChiaFunction(resources={'generation': 1}, max_retries=0)
def generate(cfg, run, seed, task):
    cid = task['id']
    proposed = Path(run) / 'candidates' / cid / 'proposal.json'
    if proposed.exists():
        return read_json(proposed)
    Path(cfg['storage'], 'tmp').mkdir(parents=True, exist_ok=True)
    parent = validate_bundle(task['parent_source'])
    with tempfile.TemporaryDirectory(dir=Path(cfg['storage']) / 'tmp', prefix='proposal-') as folder:
        folder = Path(folder)
        rw = folder / 'rtl'
        rw.mkdir()
        mounts = [(str(Path(seed) / 'tree'), '/workspace', 'ro')]
        for name in ALLOWLIST:
            p = rw / Path(name).name
            p.write_text(parent[name])
            mounts.append((str(p), '/workspace/' + name, 'rw'))
        mounts += agent_mounts(cfg, folder / 'credentials')
        event('generation', run_id=Path(run).name, candidate_id=cid, island=task['island'],
              parent=task['parent_id'], iteration=task['iteration'], inspirations=task['inspiration_ids'], agent=cfg['agent']['backend'], model=cfg['agent']['model'])
        response = run_worker(cfg, 'generate', {'cfg': cfg, 'run_id': Path(run).name, 'prompt': task['prompt']}, mounts, agent=True)
        # Only direct writes to the mounted implementation files are accepted.
        bundle = {name: (rw / Path(name).name).read_text() for name in ALLOWLIST}
        if bundle != response['source']:
            raise ValueError('Worker source differs from its assigned writable files')
        result = {**task, 'parent_hash': digest(parent), 'backend': cfg['agent']['backend'],
                  'model': response.get('model', cfg['agent']['model']), 'generation': {k: v for k, v in response.items() if k not in ('source', '_files')}}
        archive_proposal(run, cid, bundle, parent, result)
        event('generated', run_id=Path(run).name, candidate_id=cid, source_hash=digest(bundle))
        return read_json(proposed)


@ChiaFunction(resources={'chipyard': 1}, max_retries=0)
def build(cfg, run, seed, candidate):
    dest = Path(run) / 'artifacts' / candidate / 'build'
    result_file = dest / 'result.json'
    if result_file.exists():
        return read_json(result_file)
    proposal = read_json(Path(run) / 'candidates' / candidate / 'proposal.json')
    bundle = validate_bundle(proposal['source'])
    validate_integration(bundle, source_bundle(Path(seed) / 'tree'))
    if digest(bundle) != proposal['source_hash']:
        raise ValueError('Candidate source hash mismatch')
    with tempfile.TemporaryDirectory(dir=Path(cfg['storage']) / 'tmp', prefix='build-') as folder:
        tree = Path(folder) / 'tree'
        # Equivalent to a worktree, but also includes uncommitted/submodule source.
        copy_tree(Path(seed) / 'tree', tree)
        for name, source in bundle.items():
            p = tree / name
            p.chmod(0o644)
            p.write_text(source)
        before = files(tree)
        expected = read_json(Path(seed) / 'manifest.json')['files']
        changed = {k for k in set(before) | set(expected) if before.get(k) != expected.get(k)}
        if not changed.issubset(ALLOWLIST):
            raise ValueError(f'Candidate modified frozen files: {sorted(changed)}')
        # Make owns the entire private build tree; the immutable seed stays ro.
        for path in tree.rglob('*'):
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o200 | (0o100 if path.is_dir() else 0))
        tree.chmod(0o755)
        event('build', run_id=Path(run).name, candidate_id=candidate)
        response = run_worker(cfg, 'build', {'cfg': cfg, 'run_id': Path(run).name}, [(str(tree), '/workspace', 'rw')])
        save_files(dest, response)
        # Persist the build (including a failed build) before any simulator tasks.
        write_json(result_file, response)
        return response


@ChiaFunction(resources={'verilator_run': 1}, max_retries=0)
def simulate(cfg, run, seed, candidate, workload, artifact):
    dest = Path(run) / 'artifacts' / candidate / 'runs' / workload.replace(':', '_')
    path = dest / 'result.json'
    if path.exists():
        return read_json(path)
    software = Path(run) / 'software'
    inventory = read_json(software / 'manifest.json')
    spec = inventory['workloads'][workload]
    elf = software / spec['file']
    if digest(elf.read_bytes()) != spec['sha256']:
        raise ValueError('Frozen benchmark ELF hash mismatch')
    ini = Path(seed) / 'tree' / cfg['software']['dram_ini']
    event('simulation', run_id=Path(run).name, candidate_id=candidate, benchmark=workload)
    response = run_worker(cfg, 'run', {'cfg': cfg, 'run_id': Path(run).name, 'artifact': artifact, 'name': workload.replace(':', '_')},
                          [(str(elf), '/test.elf', 'ro'), (str(ini), '/dram', 'ro')])
    save_files(dest, response)
    response.update(workload=workload, elf_sha256=spec['sha256'],
                    simulator_sha256=digest(base64.b64decode(artifact['simulator_binary_content']['_bytes'])))
    write_json(path, response)
    return response


def proposal_path(cfg, run, seed, task):
    return Path(run) / 'candidates' / task['id'] / 'proposal.json'


def build_path(cfg, run, seed, candidate):
    return Path(run) / 'artifacts' / candidate / 'build' / 'result.json'


def simulation_path(cfg, run, seed, candidate, workload, artifact):
    return Path(run) / 'artifacts' / candidate / 'runs' / workload.replace(':', '_') / 'result.json'


def durable_generate(tag, data_path, *args, **kwargs):
    return read_json(proposal_path(*args, **kwargs))


def durable_build(tag, data_path, *args, **kwargs):
    return read_json(build_path(*args, **kwargs))


def durable_simulate(tag, data_path, *args, **kwargs):
    return read_json(simulation_path(*args, **kwargs))


def has_generate(tag, data_path, *args, **kwargs):
    return proposal_path(*args, **kwargs).exists()


def has_build(tag, data_path, *args, **kwargs):
    return build_path(*args, **kwargs).exists()


def has_simulate(tag, data_path, *args, **kwargs):
    return simulation_path(*args, **kwargs).exists()
