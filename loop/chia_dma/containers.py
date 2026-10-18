"""Per-task mounts using CHIA's Docker lifecycle, with a local SSH transport.

CHIA's static worker mount sets cannot restrict an agent to a new pair of file
mounts on every invocation. This adapter supplies that missing boundary while
leaving container setup/exec/stop to its existing DockerManager.
"""
from pathlib import Path
import contextlib
import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from .config import ROOT


class LocalTransport:
    ip = 'localhost'
    def __init__(self, timeout):
        self.timeout = timeout
    def run(self, command, check=True, timeout=None):
        # CHIA assumes host networking for cluster workers; these task-local
        # sandboxes use bridge networking so they do not share Ray's loopback.
        command = command.replace('--net=host', '--network=bridge')
        return subprocess.run(['bash', '-c', command], capture_output=True, text=True,
                              check=check, timeout=timeout or self.timeout)
    def run_script(self, commands, timeout=None, check=True):
        return self.run('set -e\n' + '\n'.join(commands), check=check, timeout=timeout)


def docker(cfg, args, **kwargs):
    prefix = cfg['runtime']['docker']
    # sg consumes one shell string. Normal deployments may simply set [docker].
    command = [*prefix, shlex.join(['docker', *args])] if prefix[-1:] == ['-c'] else [*prefix, *args]
    return subprocess.run(command, text=True, check=True, **kwargs)


def ensure_image(cfg):
    docker(cfg, ['build', '--build-arg', 'BASE=' + cfg['runtime']['image_base'],
                 '-t', cfg['runtime']['image'], '-f', str(ROOT / 'Dockerfile'), str(ROOT)])


@contextlib.contextmanager
def container(cfg, mounts, *, agent=False):
    from chia.cluster.config import DockerConfig
    from chia.cluster.docker import DockerManager
    name = 'chia-dma-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='container-', dir=Path(cfg['storage']) / 'tmp') as scratch:
        scratch = Path(scratch)
        # CHIA resolves its engine in a login shell. A tiny wrapper handles sg.
        engine = scratch / 'engine'
        prefix = cfg['runtime']['docker']
        body = ('exec ' + shlex.join(prefix) + ' "$(printf \'%q \' docker "$@")"') if prefix[-1:] == ['-c'] else ('exec ' + shlex.join(prefix) + ' "$@"')
        engine.write_text('#!/bin/bash\n' + body + '\n')
        engine.chmod(0o755)
        conda = cfg['runtime']['conda']
        # Do not mount ROOT wholesale: it contains the archive. Only code/deps.
        common = [(conda, conda, 'ro'), (str(ROOT / 'chia_dma'), '/opt/loop/chia_dma', 'ro'),
                  (cfg['chia'], '/opt/chia', 'ro')]
        affinity = sorted(os.sched_getaffinity(0))[:cfg['runtime']['cpus_per_build']]
        options = ['--cpuset-cpus=' + ','.join(map(str, affinity)), '--cpus=' + str(cfg['runtime']['cpus_per_build']), '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                   '--pids-limit=' + str(cfg['runtime']['pids_limit']), '--init', f'--memory={cfg["runtime"]["memory"]}',
                   '--tmpfs /tmp:rw,exec,mode=1777', '--tmpfs /home/ray:rw,exec,mode=1777',
                   f'--user {os.getuid()}:{os.getgid()}',
                   '--env HOME=/home/ray', '--env PYTHONPATH=/opt/chia:/opt/loop',
                   '--env ' + shlex.quote('PATH=' + conda + '/bin:' + conda + '/riscv-tools/bin:/usr/local/bin:/usr/bin:/bin'),
                   '--env ' + shlex.quote('RISCV=' + conda + '/riscv-tools'),
                   '--env ' + shlex.quote('CONDA_PREFIX=' + conda)]
        for key, value in cfg['runtime'].get('environment', {}).items():
            options.append('--env ' + shlex.quote(f'{key}={value}'))
        for source, dest, mode in [*common, *mounts]:
            options.append('--mount ' + shlex.quote(f'type=bind,src={source},dst={dest},readonly' if mode == 'ro' else f'type=bind,src={source},dst={dest}'))
        manager = DockerManager(LocalTransport(cfg['runtime']['task_timeout_seconds']), DockerConfig(
            image=cfg['runtime']['image'], container_name=name, engine=str(engine),
            pull_before_run=False, run_options=options))
        try:
            manager.setup_container()
            yield manager
        finally:
            # stop then remove including on prompt/build failure or interruption.
            try:
                manager.stop_container()
            finally:
                docker(cfg, ['rm', '-f', name], capture_output=True)


def run_worker(cfg, action, request, mounts, *, agent=False):
    from .artifacts import write_json, read_json
    from .config import implementation_hash
    if cfg.get('_implementation_hash') != implementation_hash():
        raise RuntimeError('Loop/evaluator code changed during the experiment')
    Path(cfg['storage'], 'tmp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=action + '-', dir=Path(cfg['storage']) / 'tmp') as folder:
        folder = Path(folder)
        request_file = folder / 'request.json'
        output = folder / 'output'
        output.mkdir()
        write_json(request_file, request)
        with container(cfg, [*mounts, (str(request_file), '/request.json', 'ro'),
                             (str(output), '/output', 'rw')], agent=agent) as manager:
            python = str(Path(cfg['runtime']['conda']) / 'bin/python')
            try:
                result = manager.exec_command(shlex.join([python, '-m', 'chia_dma.worker', action]),
                                              timeout=cfg['runtime']['task_timeout_seconds'])
            except subprocess.CalledProcessError as error:
                raise RuntimeError(f'{action} worker failed: {error.stderr[-6000:]} {error.stdout[-2000:]}') from error
            if result.returncode:
                raise RuntimeError(f'{action} worker failed: {result.stderr[-6000:]} {result.stdout[-3000:]}')
            response = read_json(output / 'result.json')
            # Artifacts travel by value; no container paths escape into run state.
            response['_files'] = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file() and p.name != 'result.json'}
            return response


def agent_mounts(cfg, private_home):
    backend = cfg['agent']['backend']
    settings = cfg['agent']['backends'][backend]
    binary = Path(settings['binary']).resolve(strict=True)
    credentials = Path(settings['credentials'])
    if not credentials.is_dir():
        raise RuntimeError(f'Authenticate {backend} first; missing credentials directory {credentials}')
    # Avoid sharing provider conversations across unrelated branches.
    shutil.copytree(credentials, private_home, symlinks=False,
                    ignore=shutil.ignore_patterns('conversations', 'sessions', 'projects', 'logs', '*.log', 'tmp', 'history.jsonl', 'shell_snapshots', '*.sock', 'app-server-control', 'app-server-daemon'))
    if backend == 'antigravity':
        import json
        settings_file = Path(private_home) / 'antigravity-cli' / 'settings.json'
        data = json.loads(settings_file.read_text()) if settings_file.exists() else {}
        gcp = data.setdefault('gcp', {})
        for key, setting in (('project', 'gcp_project'), ('location', 'gcp_location')):
            if settings.get(setting):
                gcp[key] = settings[setting]
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(data))
        token_file = Path(private_home) / 'antigravity-cli' / 'antigravity-oauth-token'
        if token_file.exists():
            token = json.loads(token_file.read_text())
            # agy also stores routing metadata next to the OAuth token. Keep
            # token contents untouched; override only project/region routing.
            if settings.get('gcp_project'):
                token['project_id'] = settings['gcp_project']
            if settings.get('gcp_location'):
                token['region'] = settings['gcp_location']
            token_file.write_text(json.dumps(token))
    cli = {'antigravity': 'agy', 'claude': 'claude', 'codex': 'codex'}[backend]
    return [(str(binary), '/usr/local/bin/' + cli, 'ro'),
            (str(private_home), settings['credential_target'], 'rw')]
