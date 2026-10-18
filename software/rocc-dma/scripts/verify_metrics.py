#!/usr/bin/env python3
"""Reproduce the additional-metrics validation, or audit its completed RTL logs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from metrics import parse, require, hardware

WORK=Path(__file__).resolve().parents[1]
ROOT=WORK.parents[1]
BUILD=WORK/'build'
LOGS=BUILD/'logs'
KINDS=('basic','command-smoke','wrapper-dma','wrapper-cpu','microbench')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def logged(command,path):
    with path.open('w') as out:
        subprocess.run(list(map(str,command)),cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)


def standalone(kind):
    logged(['make','-C',ROOT/'sims/verilator','CONFIG=DMARocketConfig','run-binary-fast',
            f'BINARY={BUILD}/{kind}.riscv','LOADMEM=1','TIMEOUT_CYCLES=20000000'],
           LOGS/f'metrics-{kind}.log')


def audit():
    for suffix in ('','-denied','-corrupt'):
        text=(LOGS/f'metrics-monitor-test{suffix}.log').read_text()
        require('DMA_MONITOR_TEST PASS' in text,'Missing observer test completion')
        h,_=hardware(text)
        require(h['denied']==int(suffix=='-denied') and h['corrupt']==int(suffix=='-corrupt'),
                'Observer fault counters not verified')
    require('COPY_completed_before_memory_ack' in (LOGS/'metrics-monitor-test-bad-completion.log').read_text(),
            'Missing early-completion rejection test')
    results={k:parse((LOGS/f'metrics-{k}.log').read_text(),k) for k in KINDS}
    for mode in ('cpu','dma'):
        for bench in ('wikisort','aha-mont64'):
            results[f'{mode}:{bench}']=parse((LOGS/f'embench-{mode}-{bench}.log').read_text(),'embench')
    for bench in ('wikisort','aha-mont64'):
        cpu=results[f'cpu:{bench}']['software']; dma=results[f'dma:{bench}']['software']
        require((cpu['benchmark_result'],cpu['verification_result'])==
                (dma['benchmark_result'],dma['verification_result']), 'CPU/DMA result mismatch')
        require(cpu['offloaded']==0,'CPU baseline used DMA')
    require(results['dma:wikisort']['software']['offloaded']>0,'Wikisort must exercise hardware DMA')
    require(results['dma:aha-mont64']['software']['offloaded']==0,'Expected zero-offload compatibility workload')
    expected=json.loads((BUILD/'embench-input-hashes.json').read_text())
    require(all(sha(ROOT/'tests/embench-iot'/p)==h for p,h in expected.items()),'Benchmark/support sources changed')
    # Inspect the actual accelerator source and simulator-only hookup, not just
    # a green software test, to establish that profiling is outside the RTL.
    for name in ('DMAController.scala','RoCCDMA.scala'):
        path=f'generators/chipyard/src/main/scala/dma/{name}'
        original=subprocess.check_output(['git','show',f'707c6075:{path}'],cwd=ROOT)
        require((ROOT/path).read_bytes()==original,'Accelerator RTL changed during instrumentation')
    sv=(WORK/'sim/DMAPassiveMonitor.sv').read_text()
    require('output ' not in sv and 'inout ' not in sv,'Observer must have inputs only')
    manifest=json.loads((BUILD/'embench-manifest.json').read_text())
    saved={(r['mode'],r['benchmark']):r for r in json.loads((BUILD/'embench-results.json').read_text())}
    simulator=ROOT/'sims/verilator/simulator-chipyard.harness-DMARocketConfig'
    provenance={str(simulator.relative_to(ROOT)):sha(simulator)}
    for mode in ('cpu','dma'):
        for bench in ('wikisort','aha-mont64'):
            binary=BUILD/f'embench-{mode}/src/{bench}/{bench}'
            log=LOGS/f'embench-{mode}-{bench}.log'
            row=saved[mode,bench]
            require(row['elf_sha256']==sha(binary)==manifest['inspection'][mode][bench]['elf_sha256'] and
                    row['sim_sha256']==sha(simulator) and row['log_sha256']==sha(log),'Stale benchmark result')
            provenance[str(binary.relative_to(ROOT))]=sha(binary)
            provenance[str(log.relative_to(ROOT))]=sha(log)
    for k in KINDS:
        for p in (BUILD/f'{k}.riscv',LOGS/f'metrics-{k}.log'):
            provenance[str(p.relative_to(ROOT))]=sha(p)
    for p in sorted((WORK/'sim').glob('*')):
        if p.is_file():
            provenance[str(p.relative_to(ROOT))]=sha(p)
    data={'metrics_version':1,'results':results,'sha256':provenance,'benchmark_source_sha256':expected}
    (BUILD/'metrics-validation.json').write_text(json.dumps(data,indent=2)+'\n')
    lines=['# Passive DMA metrics validation','',
           'All additional-metrics validation gates passed in Verilator. The DMA Scala sources are byte-for-byte',
           'unchanged from the verified first cut. The observer has inputs only and is compiled by the simulator',
           'Makefile, outside hardware elaboration/synthesis. No performance-counter registers or control paths',
           'were added to the accelerator. Software statistics have the ordinary cost of RAM counter updates.','',
           'Raw instruction smoke/basic correctness, both memcpy shims, and all microbenchmark size/alignment',
           'cases passed, including source and destination guard checks. Monitor tests covered stalls, response',
           'latency, denied/corrupt responses, and rejection of completion before the final write acknowledgement.','',
           '## Benchmark checks','',
           '| Mode / benchmark | mcycle timed region | Intercepted | Attempts | Completed | Fallback | Bytes | Min / max cycles |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for mode in ('cpu','dma'):
        for bench in ('wikisort','aha-mont64'):
            s=results[f'{mode}:{bench}']['software']
            lines.append(f"| {mode} / {bench} | {s['benchmark_cycles']} | {s['intercepted']} | {s['attempts']} | {s['offloaded']} | {s['fallback']} | {s['bytes']} | {s['min_cycles']} / {s['max_cycles']} |")
    lines += ['', 'CPU/DMA benchmark return values and native verifier results matched. The zero-offload case is a',
              'compatibility check. Both modes use identical board timing/statistics code and benchmark flags.','',
              '## Standalone timing','',
              'Software cycles include the complete blocking API call, including bookkeeping. Hardware cycles',
              'are measured passively from COPY acceptance to response handshake. Bandwidth is calculated here',
              'from useful bytes and raw cycles, outside the accelerator and shim.','',
              '| Bytes | Source / destination alignment mod 64 | Status | CPU cycles | API cycles | Hardware cycles | Hardware bytes/cycle |',
              '| ---: | --- | ---: | ---: | ---: | ---: | ---: |']
    for s in results['microbench']['samples']+results['microbench']['alignment_samples']:
        lines.append(f"| {s['bytes']} | {s['src_align']} / {s['dst_align']} | {s['status']} | {s['cpu_cycles']} | {s['dma_cycles']} | {s.get('hw_cycles','n/a')} | {format(s['hardware_bytes_per_cycle'],'.4f') if 'hardware_bytes_per_cycle' in s else 'n/a'} |")
    lines += ['', 'Unsupported alignments/lengths return status 2 without a hardware COPY or destination modification.',
              'Successful cases include dirty sources, cached destinations, differing alignments, cache-line/page',
              'crossings, and a 64 KiB streaming transfer. The original full 2 MiB correctness suites remain documented',
              'in RESULTS.md; their RTL has not changed. This incremental validation does not repeat the entire',
              '19-program suite. It rebuilds all 19 in both modes and executes the four benchmark runs required here.','',
              'All raw counters, latency extrema, eight size buckets, TileLink metrics, individual COPY timestamps,',
              'and artifact hashes are in build/metrics-validation.json and build/logs. No missing result is accepted.',
              'See OBSERVABILITY.md for the stable format, boundaries, CHIA integration, and reproduction command.','']
    (WORK/'METRICS.md').write_text('\n'.join(lines))
    print('PASS: all ten additional-metrics validation gates; wrote METRICS.md and build/metrics-validation.json')


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--check-only',action='store_true'); args=p.parse_args()
    LOGS.mkdir(parents=True,exist_ok=True)
    if not args.check_only:
        logged(['make','-C',WORK,'libraries',*[BUILD/f'{k}.riscv' for k in KINDS],'-j8'],LOGS/'metrics-software-build.log')
        logged(['make','-C',ROOT/'sims/verilator','CONFIG=DMARocketConfig','-j8'],LOGS/'metrics-simulator-build.log')
        logged([sys.executable,WORK/'scripts/test_observer.py'],LOGS/'metrics-monitor-tests.log')
        logged([sys.executable,WORK/'scripts/embench.py','build'],LOGS/'metrics-embench-build.log')
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures=[pool.submit(standalone,k) for k in KINDS]
            future=pool.submit(logged,[sys.executable,WORK/'scripts/embench.py','run','--bench','wikisort','aha-mont64','--jobs','4'],LOGS/'metrics-embench-run.log')
            for f in futures+[future]: f.result()
    audit()

if __name__=='__main__': main()
