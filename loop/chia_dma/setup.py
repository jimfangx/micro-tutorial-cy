"""Freeze software once and check authenticated workers before search starts."""
from pathlib import Path
import shutil
import subprocess
import tempfile
from .artifacts import copy_tree, digest, files, read_json, readonly, sync_directory, sync_tree, write_json
from .containers import agent_mounts, docker, run_worker
from .tasks import save_files


def preflight(cfg):
    docker(cfg, ['info', '--format', '{{.ServerVersion}}'], capture_output=True)
    image = docker(cfg, ['image', 'inspect', cfg['runtime']['image'], '--format', '{{.Id}}'], capture_output=True).stdout.strip()
    Path(cfg['storage'], 'tmp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=Path(cfg['storage']) / 'tmp') as folder:
        folder = Path(folder)
        mounts = agent_mounts(cfg, folder / 'credentials')
        if cfg['agent']['authenticate_before_run']:
            response = run_worker(cfg, 'auth', {'cfg': cfg}, mounts, agent=True)
            response.pop('_files', None)
            return {'image_id': image, 'authentication': response}
    return {'image_id': image, 'authentication': 'disabled by configuration'}


def prepare_software(cfg, run, seed):
    target = Path(run) / 'software'
    if (target / 'manifest.json').exists():
        manifest = read_json(target / 'manifest.json')
        for record in manifest['workloads'].values():
            if digest((target / record['file']).read_bytes()) != record['sha256']:
                raise ValueError('Frozen software artifact changed')
        return manifest
    with tempfile.TemporaryDirectory(dir=Path(cfg['storage']) / 'tmp', prefix='software-') as folder:
        tree = Path(folder) / 'tree'
        copy_tree(Path(seed) / 'tree', tree)
        # Source copy is private; existing tool/build flow can write its outputs.
        for p in [tree, *tree.rglob('*')]:
            if not p.is_symlink():
                p.chmod(p.stat().st_mode | 0o200 | (0o100 if p.is_dir() else 0))
        before = files(tree)
        response = run_worker(cfg, 'software', {'cfg': cfg, 'run_id': Path(run).name}, [(str(tree), '/workspace', 'rw')])
        save_files(Path(run) / 'artifacts' / 'software', response)
        after = files(tree)
        # All originally captured files, including benchmark sources, shim,
        # runtime and build flags, must remain identical during compilation.
        if any(after.get(name) != record for name, record in before.items()):
            raise ValueError('Software build altered a frozen input')
        temp = Path(folder) / 'frozen'
        temp.mkdir()
        inventory = sorted(p.name for p in (tree / cfg['software']['benchmark_inventory']).iterdir() if p.is_dir())
        workloads = {}
        for mode in ('cpu', 'dma'):
            for benchmark in inventory:
                name = f'embench:{mode}:{benchmark}'
                path = tree / cfg['software']['embench_elf'].format(mode=mode, benchmark=benchmark)
                dest = name.replace(':', '_') + '.elf'
                shutil.copyfile(path, temp / dest)
                workloads[name] = {'file': dest, 'sha256': digest(path.read_bytes())}
        for name, settings in cfg['software']['standalone'].items():
            path = tree / settings['elf']
            dest = name + '.elf'
            shutil.copyfile(path, temp / dest)
            workloads[name] = {'file': dest, 'sha256': digest(path.read_bytes())}
        build_manifest = tree / 'software/rocc-dma/build/embench-manifest.json'
        shutil.copyfile(build_manifest, temp / 'embench-build.json')
        manifest = {'benchmarks': inventory, 'workloads': workloads,
                    'software_hash': digest({k: v for k, v in before.items() if k.startswith(('software/', 'tests/'))}),
                    'toolchain': response['toolchain']}
        write_json(temp / 'manifest.json', manifest)
        sync_tree(temp)
        temp.rename(target)
        sync_directory(target.parent)
        readonly(target)
        return manifest
