"""Trusted container entry point. Coding agents only run in the generate action."""
from dataclasses import asdict
from pathlib import Path
import base64
import json
import os
import subprocess
import sys
import time
from .artifacts import source_bundle, write_json


def encode(value):
    if isinstance(value, bytes):
        return {'_bytes': base64.b64encode(value).decode()}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'_bytes'}:
            return base64.b64decode(value['_bytes'])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def make_agent(cfg):
    from chia.models.antigravity import AntigravityLLM
    from chia.models.claude import ClaudeCodeLLM
    from chia.models.codex import CodexLLM
    backend = cfg['agent']['backend']
    classes = {'antigravity': AntigravityLLM, 'claude': ClaudeCodeLLM, 'codex': CodexLLM}
    args = dict(timeout_seconds=cfg['agent']['timeout_seconds'], retries=cfg['agent']['retries'],
                resume_session=False, **cfg['agent']['backends'][backend]['options'])
    if cfg['agent']['model']:
        args['model'] = cfg['agent']['model']
    if backend in ('antigravity', 'codex'):
        args['work_dir'] = '/workspace'
    # Container mounts are the hard permission boundary for every provider.
    return classes[backend](**args)


def main(action):
    req = json.loads(Path('/request.json').read_text())
    cfg = req['cfg']
    start = time.monotonic()
    # CHIA's local decorators query Ray for a profiler collector. Avoid implicit
    # startup of a machine-sized cluster inside each sandbox.
    import ray
    ray.init(num_cpus=0, include_dashboard=False, object_store_memory=80 * 1024 * 1024,
             _node_ip_address='127.0.0.1', logging_level='ERROR')
    os.chdir('/workspace')
    if action in ('generate', 'auth'):
        prompt = req.get('prompt', 'Reply exactly CHIA_AUTH_OK. Do not use tools or modify any files.')
        agent = make_agent(cfg)
        result = agent.prompt(prompt, [])
        if not result.success:
            raise RuntimeError(f'Agent failed: {result.stderr[-2000:]}')
        if action == 'auth' and 'CHIA_AUTH_OK' not in result.result:
            raise RuntimeError('Agent authentication probe did not return CHIA_AUTH_OK')
        response = {'response': result.result, 'transcript': result.stream_result,
                    'returncode': result.returncode, 'success': result.success}
        cli = {'antigravity': 'agy', 'claude': 'claude', 'codex': 'codex'}[cfg['agent']['backend']]
        response['agent_version'] = subprocess.check_output([cli, '--version'], text=True).strip()
        if action == 'generate':
            response['source'] = source_bundle('/workspace')
    elif action == 'software':
        for i, command in enumerate(cfg['software']['build_commands']):
            command = [part.replace('{workspace}', '/workspace') for part in command]
            with open(f'/output/software-{i}.log', 'w') as out:
                subprocess.run(command, check=True, stdout=out, stderr=subprocess.STDOUT)
        versions = {}
        for name, command in {'gcc': ['riscv64-unknown-elf-gcc', '--version'],
                              'verilator': ['verilator', '--version'],
                              'java': ['java', '-version']}.items():
            version = subprocess.run(command, capture_output=True, text=True, check=True)
            versions[name] = (version.stdout + version.stderr).strip()
        response = {'success': True, 'toolchain': versions}
    elif action == 'build':
        from chia.chipyard.chisel_build_node import ChiselBuildNode
        hw = cfg['hardware']
        node = ChiselBuildNode('/workspace', hw['config'], config_package=hw['config_package'],
                               make_jobs=hw['make_jobs'], timeout_seconds=hw['build_timeout_seconds'],
                               extra_make_args=hw['extra_make_args'], clean=True, bundle_runtime_libs=True)
        artifact = node.build()
        if artifact.success and hw.get('protocol_command'):
            protocol = subprocess.run(hw['protocol_command'], capture_output=True, text=True,
                                      timeout=hw['build_timeout_seconds'])
            artifact.stdout += '\nProtocol tests:\n' + protocol.stdout
            artifact.stderr += protocol.stderr
            if protocol.returncode or 'All tests passed' not in protocol.stdout:
                artifact.success = False
                artifact.returncode = protocol.returncode or 1
        response = {'artifact': encode(asdict(artifact))}
    elif action == 'run':
        from chia.chipyard.verilator_run_node import VerilatorRunNode
        from chia.chipyard.state_def import BuildArtifact
        artifact = BuildArtifact(**decode(req['artifact']))
        # VerilatorRunNode creates a private task directory, stages dependencies,
        # applies simulation timeouts, and collects stdout/stderr itself.
        result = VerilatorRunNode().run(
            artifact, Path('/test.elf').read_bytes(), req['name'] + '.riscv', '/tmp/sim',
            plusargs={'+cycle-count': ''}, timeout_cycles=cfg['software']['timeout_cycles'],
            timeout_seconds=cfg['software']['timeout_seconds'],
            dramsim_ini_files={p.name: p.read_bytes() for p in Path('/dram').glob('*.ini')},
            verbose=False)
        response = encode(asdict(result))
    elif action == 'isolation':
        denied = []
        for name in req['frozen_paths']:
            try:
                with open('/workspace/' + name, 'a') as out:
                    out.write('ILLEGAL')
            except OSError:
                denied.append(name)
        for name in req.get('allowed_paths', []):
            with open('/workspace/' + name, 'a') as out:
                out.write('\n// writable candidate\n')
        response = {'denied': denied, 'original_visible': Path(cfg['source'], 'build.sbt').exists(),
                    'socket_visible': Path('/var/run/docker.sock').exists()}
    else:
        raise ValueError(action)
    response['wall_seconds'] = time.monotonic() - start
    write_json('/output/result.json', response)


if __name__ == '__main__':
    main(sys.argv[1])
