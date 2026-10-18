#!/usr/bin/env python3
"""Version-1 DMA records, shared by local scripts and CHIA RunResult consumers."""
import argparse
import json
from pathlib import Path
import re


class MetricsError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise MetricsError(message)


def fields(line):
    tokens=line.split()[1:]
    require(tokens and all(re.fullmatch(r'\w+=-?\d+', t) for t in tokens), 'Malformed metric record')
    result={}
    for token in tokens:
        k,v=token.split('=')
        require(k not in result, f'Duplicate metric: {k}')
        result[k]=int(v)
    require(result.get('metrics_version')==1, 'Missing/unsupported metrics version')
    return result


def records(text, tag):
    return [fields(line) for line in text.splitlines() if line.startswith(tag+' ')]


def single(text, tag):
    rows=records(text,tag)
    require(len(rows)==1, f'Expected one {tag} completion record')
    return rows[0]


def required(row, names):
    require(set(names)<=row.keys(), 'Incomplete metrics: '+','.join(sorted(set(names)-row.keys())))


def statistics(row, prefix=''):
    names=('intercepted','attempts','offloaded','errors','fallback','retry_fallback','bytes',
           'dma_cycles','setup_cycles','e2e_cycles','min_cycles','max_cycles',
           *(f'hist{i}' for i in range(8)))
    required(row,[prefix+n for n in names])
    s={k:row[prefix+k] for k in names}
    require(all(v>=0 for v in s.values()), 'Negative software counter')
    require(s['attempts']==s['offloaded']+s['errors'], 'Unaccounted DMA attempts')
    require(s['intercepted']==s['attempts']+s['fallback']-s['retry_fallback'], 'Unaccounted memcpy calls')
    require(s['retry_fallback']<=min(s['fallback'],s['errors']), 'Invalid error fallback count')
    require(sum(s[f'hist{i}'] for i in range(8))==s['offloaded'], 'Invalid copy-size histogram')
    if s['attempts']:
        require(0<s['min_cycles']<=s['max_cycles']<=s['e2e_cycles'], 'Invalid software latency extrema')
        require(s['attempts']*s['min_cycles']<=s['e2e_cycles']<=s['attempts']*s['max_cycles'], 'Invalid latency total')
        require(s['setup_cycles']<=s['dma_cycles']<=s['e2e_cycles'], 'Invalid software timing boundaries')
    else:
        require(all(s[k]==0 for k in ('bytes','dma_cycles','setup_cycles','e2e_cycles','min_cycles','max_cycles')), 'Nonzero inactive DMA statistics')
    require((s['offloaded']==0)==(s['bytes']==0), 'Invalid successful byte count')


def hardware(text):
    h=single(text,'DMA_HW')
    names=('result','cycles','configs','copies','completed','failed','busy_cycles','idle_cycles',
           'busy_transitions','reads','writes','read_bytes','write_bytes','read_responses','write_acks',
           'outstanding','peak_outstanding','outstanding_cycles','a_stall','d_stall','cmd_stall','resp_stall',
           'read_latency','read_min','read_max','write_latency','write_min','write_max',
           'denied','corrupt','protocol_errors','pending')
    required(h,names)
    require(all(v>=0 for v in h.values()), 'Negative hardware metric')
    require(h['result']==h['outstanding']==h['pending']==h['protocol_errors']==0, 'Incomplete/failed hardware observation')
    require(h['cycles']>0 and h['busy_cycles']+h['idle_cycles']==h['cycles'], 'Invalid observation interval')
    require(h['reads']==h['read_responses'] and h['writes']==h['write_acks'], 'Unacknowledged TileLink requests')
    for prefix,count in (('read',h['read_responses']),('write',h['write_acks'])):
        total,lo,hi=(h[prefix+'_'+s] for s in ('latency','min','max'))
        require((0<=lo<=hi and count*lo<=total<=count*hi) if count else total==lo==hi==0,
                'Invalid TileLink latency statistics')
    copies=records(text,'DMA_COPY')
    require(len(copies)==h['copies']==h['completed']+h['failed'], 'Missing COPY timestamps')
    for seq,c in enumerate(copies,1):
        required(c,('seq','bytes','src','dst','config_cycle','accept_cycle','valid_cycle','response_cycle',
                    'hw_cycles','status','reads','writes','read_bytes','write_bytes'))
        require(c['seq']==seq and 0<=c['config_cycle']<=c['accept_cycle']<=c['valid_cycle']<=c['response_cycle'], 'Invalid COPY event ordering')
        require(all(v>=0 for v in c.values()), 'Negative COPY metric')
        require(c['hw_cycles']==c['response_cycle']-c['accept_cycle'] and c['hw_cycles']>0, 'Invalid hardware COPY latency')
        require(c['status'] in (0,1,2,3), 'Unknown completion status')
        if c['status']==0:
            require(c['read_bytes']==c['write_bytes']==c['bytes'], 'Incomplete successful COPY traffic')
    for key in ('reads','writes','read_bytes','write_bytes'):
        require(sum(c[key] for c in copies)==h[key], 'Unaccounted TileLink traffic')
    require(sum(c['status']==0 for c in copies)==h['completed'], 'Completion/status mismatch')
    return h,copies


def check_histogram(row, copies):
    expected=[0]*8
    for c in copies:
        if c['status']==0:
            bucket=next((i for i,limit in enumerate((8,64,256,1024,4096,16384,65536))
                         if c['bytes']<=limit),7)
            expected[bucket]+=1
    require([row[f'hist{i}'] for i in range(8)]==expected, 'Software/hardware copy-size histogram mismatch')


def parse(text, kind, *, success=True, returncode=0):
    """Accept CHIA's result.log + '\\n' + result.out and its exit/timeout status."""
    require(success and returncode==0, 'Simulator failed or timed out')
    require(not re.search(r'Assertion failed|%Error|\*\*\* FAILED|DMA_TEST_FAIL|DMA_PROTOCOL_FAIL|\bFAIL\b',text), 'Correctness/protocol failure')
    h,copies=hardware(text)
    require(h['denied']==h['corrupt']==0, 'TileLink denied/corrupt response during workload')
    result={'hardware':h,'copies':copies}
    if kind=='embench':
        row=single(text,'EMBENCH')
        required(row,('result','benchmark_result','verification_result','main_cycles','benchmark_cycles'))
        require(row['result']==0 and row['verification_result']!=0, 'Benchmark verification failed')
        require(row['main_cycles']>=row['benchmark_cycles']>0, 'Invalid mcycle measurement')
        statistics(row); statistics(row,'region_')
        require(row['errors']==row['region_errors']==0, 'DMA software failure')
        require(h['failed']==0 and row['attempts']==h['copies'], 'Unexpected hardware COPY failure/attempt')
        require(row['offloaded']==h['completed'] and row['bytes']==h['write_bytes'], 'Software/hardware DMA activity mismatch')
        check_histogram(row,copies)
        result['software']=row
    elif kind=='microbench':
        require(h['failed']==0, 'Hardware performance-copy failure')
        require(text.count('DMA microbenchmark PASS')==1, 'Missing microbenchmark completion')
        rows=records(text,'MICRO'); align=records(text,'MICRO_ALIGN')
        require([r.get('bytes') for r in rows]==[8,24,64,256,1024,4096,65536], 'Incomplete size coverage')
        require(len(align)==6, 'Incomplete alignment coverage')
        seen=[]
        for r in rows+align:
            required(r,('result','bytes','src','dst','src_align','dst_align','status','copy_seq',
                        'cpu_cycles','dma_cycles','op_cycles','setup_cycles'))
            require(r['result']==0 and r['cpu_cycles']>0 and r['dma_cycles']>0, 'Invalid standalone result')
            require(r['src']%64==r['src_align'] and r['dst']%64==r['dst_align'], 'Incorrect alignment metadata')
            if r['status']==0:
                seq=r['copy_seq']; seen.append(seq)
                require(0<seq<=len(copies), 'Missing standalone COPY event')
                c=copies[seq-1]
                require(c['status']==0 and (c['bytes'],c['src'],c['dst'])==(r['bytes'],r['src'],r['dst']), 'Wrong standalone COPY event')
                require(r['dma_cycles']>=r['op_cycles']>=c['hw_cycles']>0, 'Hardware/software latency boundaries overlap incorrectly')
                r['hw_cycles']=c['hw_cycles']
                r['hardware_bytes_per_cycle']=r['bytes']/c['hw_cycles']
                r['e2e_bytes_per_cycle']=r['bytes']/r['dma_cycles']
            else:
                require(r['status']==2 and r['copy_seq']==r['op_cycles']==r['setup_cycles']==0, 'Invalid unsupported alignment result')
        require(sorted(seen)==list(range(1,len(copies)+1)), 'Missing/duplicate performance copy')
        result['samples']=rows; result['alignment_samples']=align
    elif kind in ('wrapper-dma','wrapper-cpu'):
        require(text.count('DMA wrapper PASS ')==1, 'Missing wrapper completion')
        s=single(text,'DMA_STATS'); statistics(s)
        require(s['errors']==0 and s['offloaded']==h['completed'] and s['bytes']==h['write_bytes'], 'Wrapper/hardware count mismatch')
        require(h['failed']==0 and s['attempts']==h['copies'], 'Unexpected wrapper hardware failure/attempt')
        check_histogram(s,copies)
        require(s['intercepted']==12 and s['offloaded']==(5 if kind=='wrapper-dma' else 0), 'Wrapper fixed workload changed')
        result['software']=s
    else:
        markers={'basic':'DMA basic PASS','command-smoke':'DMA command smoke PASS',
                 'correctness':'DMA correctness/coherence PASS'}
        require(kind in markers and text.count(markers[kind])==1, 'Missing correctness completion')
        if kind=='correctness':
            for size in (0,8,24,64,192,8192,65536,2097152):
                require(f'DMA correctness bytes={size} PASS' in text, 'Incomplete correctness coverage')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind',choices=('embench','microbench','wrapper-dma','wrapper-cpu','basic','command-smoke','correctness'))
    p.add_argument('logs',nargs='+',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    data=parse('\n'.join(f.read_text() for f in args.logs),args.kind)
    output=json.dumps(data,indent=2)+'\n'
    if args.output: args.output.write_text(output)
    else: print(output,end='')

if __name__=='__main__':
    main()
