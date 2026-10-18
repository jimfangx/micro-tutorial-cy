"""One ChiaEvaluator per candidate, keeping its build/run binding private."""
from pathlib import Path
import json
from chia.base.ChiaFunction import get
from skydiscover.evaluation.chia_evaluator import ChiaEvaluator
from skydiscover.evaluation.evaluation_result import EvaluationResult
from .artifacts import read_json, write_json
from .metrics import aggregate, parse
from .tasks import build, simulate, event


def resolve(refs, *, timeout=None):
    """CHIA supports cache/session callbacks on a single ref, not a ref list.

    Tasks are already dispatched concurrently. Collect individually to unwrap
    both callbacks and profiler envelopes, retaining one overall deadline.
    """
    if not isinstance(refs, list):
        return get(refs, timeout=timeout)
    import time
    deadline = time.monotonic() + timeout if timeout is not None else None
    return [get(ref, timeout=max(0, deadline - time.monotonic()) if deadline else None) for ref in refs]


class DMAEvaluator(ChiaEvaluator):
    def __init__(self, cfg, state, seed, candidate, *, baseline=False, replay=None):
        self.cfg, self.state, self.seed, self.candidate = cfg, state, seed, candidate
        self.run = state.run
        self.baseline = baseline
        self.task = state.tasks[candidate]
        self.artifact = None
        self.replay = replay
        inventory = read_json(self.run / 'software' / 'manifest.json')['workloads']
        self.correctness = [n for n in cfg['software']['standalone'] if n != 'microbench']
        workloads = [n for n in inventory if n.startswith('embench:dma:')] + ['microbench']
        if baseline:
            workloads += [n for n in inventory if n.startswith('embench:cpu:')]
        super().__init__(self.dispatch_build, self.dispatch_run, self.map_results, workloads,
                         str(self.run / 'artifacts' / candidate / 'evaluation'),
                         timeout=cfg['runtime']['task_timeout_seconds'], max_retries=0)
        # CHIA get() unwraps profiler envelopes and cache/session callbacks.
        from types import SimpleNamespace
        import ray
        self._ray = SimpleNamespace(get=resolve, cancel=ray.cancel)

    def tag(self, operation):
        from .config import digest, frozen
        return f'{self.run.name}/{self.candidate}/{operation}/{digest(frozen(self.cfg))[:16]}'

    def dispatch_build(self, solution):
        return build.chia_remote(self.cfg, str(self.run), str(self.seed), self.candidate,
                                 _chia_tag=self.tag('build'))

    def dispatch_run(self, workload):
        return simulate.chia_remote(self.cfg, str(self.run), str(self.seed), self.candidate,
                                    workload, self.artifact, _chia_tag=self.tag('run/' + workload))

    async def _dispatch_build(self, solution, label):
        result = await super()._dispatch_build(solution, label)
        if isinstance(result, EvaluationResult):
            return result
        artifact = result['artifact']
        if not artifact['success']:
            return EvaluationResult({'combined_score': 0.0, 'validity': 0},
                                    {'error': artifact['stderr'] + '\n' + artifact['stdout'][-6000:], 'failure_stage': 'build'})
        self.artifact = artifact
        self.state.stage(self.task, 'built', {'simulator': artifact['simulator_binary_name']})
        # Correctness gates run before any performance tasks are dispatched.
        import asyncio
        import ray
        refs = [self.dispatch_run(name) for name in self.correctness]
        try:
            results = await asyncio.to_thread(resolve, refs, timeout=self.timeout)
            self.gate_records = {name: parse(name, result, self.cfg) for name, result in zip(self.correctness, results)}
        except Exception as error:
            return EvaluationResult({'combined_score': 0.0, 'validity': 0}, {'error': str(error), 'failure_stage': 'correctness'})
        return result

    def map_results(self, runs):
        try:
            records = {**self.gate_records, **{name: parse(name, result, self.cfg) for name, result in zip(self.workloads, runs)}}
            proposal = read_json(self.run / 'candidates' / self.candidate / 'proposal.json')
            reference_file = self.run / 'reference.json'
            reference = records if self.baseline else read_json(reference_file)
            if self.baseline:
                for name, row in records.items():
                    if name.startswith('embench:cpu:'):
                        dma = records[name.replace(':cpu:', ':dma:')]
                        if row['offloaded'] != 0 or (row['benchmark_result'], row['verification_result']) != (dma['benchmark_result'], dma['verification_result']):
                            raise ValueError('CPU-only and DMA-enabled results disagree')
            metrics, vector = aggregate(records, reference, self.cfg, proposal['source'])
            vector = {**records, **vector}
            # Full raw metrics plus the scalar and measured features persist.
            dest = self.run / 'artifacts' / self.candidate / 'evaluation'
            write_json(dest / 'metrics.json', {'metrics': metrics, 'vector': vector})
            if self.baseline:
                write_json(reference_file, records)
            self.state.evaluated(self.task, metrics, vector)
            event('fitness', candidate_id=self.candidate, fitness=metrics['combined_score'], correct=True)
            return EvaluationResult(metrics)
        except Exception as error:
            return EvaluationResult({'combined_score': 0.0, 'validity': 0},
                                    {'error': str(error), 'failure_stage': 'metrics'})

    async def evaluate_program(self, program_solution, program_id=''):
        saved = self.run / 'artifacts' / self.candidate / 'evaluation' / 'result.json'
        if saved.exists():
            data = read_json(saved)
            result = EvaluationResult(**data)
            # Repair the tiny interruption window between artifact and checkpoint.
            if result.metrics.get('validity') == 1 and 'metrics' not in self.task:
                complete = read_json(saved.parent / 'metrics.json')
                self.state.evaluated(self.task, complete['metrics'], complete['vector'])
            return result
        result = await super().evaluate_program(program_solution, program_id or self.candidate)
        if result.metrics.get('validity') != 1:
            result.metrics.update(combined_score=0.0, validity=0)
            self.state.evaluated(self.task, result.metrics, {'failure': result.artifacts})
        write_json(saved, {'metrics': result.metrics, 'artifacts': result.artifacts})
        return result
