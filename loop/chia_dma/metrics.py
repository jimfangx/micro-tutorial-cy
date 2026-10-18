"""Parse fixed RTL output and calculate reproducible, correctness-gated fitness."""
import math
import re
from .config import digest


def fields(line):
    return {k: int(v) for k, v in re.findall(r'(\w+)=(-?\d+)', line)}


def parse(name, result, cfg):
    if not result.get('success') or result.get('returncode') != 0:
        raise ValueError(f'{name}: simulator failed or timed out')
    text = result['log'] + '\n' + result['out']
    if re.search(r'Assertion failed|%Error|\*\*\* FAILED|\bFAIL\b', text):
        raise ValueError(f'{name}: RTL assertion or test failure')
    if name.startswith('embench:'):
        records = re.findall(r'^EMBENCH .*$', text, re.M)
        if len(records) != 1:
            raise ValueError(f'{name}: expected one Embench completion record')
        row = fields(records[0])
        required = ('result', 'benchmark_result', 'verification_result', 'errors', 'main_cycles',
                    'benchmark_cycles', 'intercepted', 'offloaded', 'fallback', 'bytes',
                    'dma_cycles', 'setup_cycles', 'region_offloaded', 'region_bytes')
        if any(k not in row for k in required):
            raise ValueError(f'{name}: incomplete performance counters')
        if row['result'] or not row['verification_result'] or row['errors']:
            raise ValueError(f'{name}: benchmark correctness failure')
        if row['benchmark_cycles'] <= 0 or row['main_cycles'] <= 0:
            raise ValueError(f'{name}: invalid cycle measurement')
        if row['intercepted'] != row['offloaded'] + row['fallback']:
            raise ValueError(f'{name}: unaccounted intercepted copies')
        if min(row[k] for k in ('intercepted', 'offloaded', 'fallback', 'bytes', 'dma_cycles', 'setup_cycles')) < 0:
            raise ValueError(f'{name}: negative DMA measurement')
        if row['offloaded'] and (row['bytes'] <= 0 or row['dma_cycles'] <= 0):
            raise ValueError(f'{name}: DMA copies without DMA bytes or duration')
        return row
    settings = cfg['software']['standalone'][name]
    if settings['marker'] not in text:
        raise ValueError(f'{name}: missing correctness completion marker')
    if name == 'microbench':
        rows = [fields(line) for line in re.findall(r'^MICRO .*$', text, re.M)]
        if not rows or len({r['bytes'] for r in rows}) != len(rows):
            raise ValueError('Missing or duplicate microbenchmark samples')
        for row in rows:
            if any(row.get(k, 0) <= 0 for k in ('bytes', 'cpu_cycles', 'dma_cycles', 'op_cycles', 'setup_cycles')):
                raise ValueError('Incomplete DMA latency measurement')
            row['bandwidth_bytes_per_cycle'] = row['bytes'] / row['dma_cycles']
            row['active_bandwidth_bytes_per_cycle'] = row['bytes'] / row['op_cycles']
        return {'copies': rows}
    if name.startswith('wrapper-'):
        records = re.findall(r'^DMA wrapper PASS .*$', text, re.M)
        if len(records) != 1:
            raise ValueError('Missing wrapper operation counts')
        row = fields(records[0])
        if name == 'wrapper-dma' and (row.get('offloaded'), row.get('bytes')) != (5, 12936):
            raise ValueError('Wrapper failed to perform the fixed DMA workload')
        if name == 'wrapper-cpu' and row.get('offloaded') != 0:
            raise ValueError('CPU-only wrapper used DMA')
        return row
    if name == 'correctness':
        for size in (0, 8, 24, 64, 192, 8192, 65536, 2097152):
            if f'DMA correctness bytes={size} PASS' not in text:
                raise ValueError(f'Missing {size}-byte correctness case')
    return {'correct': True}


def aggregate(records, reference, cfg, source, hardware=None):
    vector = {}
    groups = {'embench': [], 'microbench': []}
    weights = cfg['fitness']['workload_weights']
    embench_keys = {k for k in reference if k.startswith('embench:dma:')}
    if not embench_keys or not embench_keys.issubset(records):
        raise ValueError('Full Embench suite results are required')
    for name in sorted(embench_keys):
        row, seed = records[name], reference[name]
        if (row['benchmark_result'], row['verification_result']) != (seed['benchmark_result'], seed['verification_result']):
            raise ValueError(f'{name}: deterministic benchmark output changed')
        activity = ('intercepted', 'offloaded', 'fallback', 'bytes', 'region_offloaded', 'region_bytes')
        if cfg['fitness']['require_activity_match'] and any(row[k] != seed[k] for k in activity):
            raise ValueError(f'{name}: DMA activity changed; investigate before admission')
        speedup = seed['benchmark_cycles'] / row['benchmark_cycles']
        vector[name] = {**row, 'speedup': speedup, 'main_speedup': seed['main_cycles'] / row['main_cycles'],
                        'correct': True, 'exercises_dma': bool(seed['region_offloaded'])}
        if seed['region_offloaded']:
            groups['embench'].append((speedup, weights.get(name, 1.0)))
    seed_copies = {r['bytes']: r for r in reference['microbench']['copies']}
    copies = {r['bytes']: r for r in records['microbench']['copies']}
    if set(copies) != set(seed_copies):
        raise ValueError('Microbenchmark coverage changed')
    for size, row in sorted(copies.items()):
        name = f'microbench:{size}'
        speedup = seed_copies[size]['dma_cycles'] / row['dma_cycles']
        vector[name] = {**row, 'speedup': speedup, 'correct': True}
        groups['microbench'].append((speedup, weights.get(name, 1.0)))
    logs, group_weights = [], []
    for group, values in groups.items():
        weight = cfg['fitness'][group + '_weight']
        if weight < 0:
            raise ValueError('Fitness weights must be nonnegative')
        if not values or weight == 0:
            continue
        if any(w < 0 or not math.isfinite(w) for _, w in values) or sum(w for _, w in values) <= 0:
            raise ValueError('Invalid workload weights')
        logs.append(weight * sum(w * math.log(s) for s, w in values) / sum(w for _, w in values))
        group_weights.append(weight)
    if not group_weights:
        raise ValueError('No measured workloads have positive fitness weight')
    for metric, limit in (('area', 'max_area'), ('delay', 'max_delay')):
        maximum = cfg['fitness'][limit]
        if maximum is not None:
            if hardware is None or metric not in hardware or hardware[metric] > maximum:
                raise ValueError(f'Reliable {metric} estimate required and must satisfy {limit}')
    score = math.exp(sum(logs) / sum(group_weights))
    if not math.isfinite(score) or score <= 0:
        raise ValueError('Invalid aggregate score')
    return {'combined_score': score, 'validity': 1,
            'source_bytes': sum(len(s.encode()) for s in source.values()),
            'large_copy_bandwidth': copies[max(copies)]['bandwidth_bytes_per_cycle'],
            'source_hash': digest(source)}, vector
