#!/usr/bin/env python3
"""Small Verilator tests of the fixed passive monitor, including failure detection."""
import subprocess
from pathlib import Path
from metrics import hardware, records

WORK=Path(__file__).resolve().parents[1]
BUILD=WORK/'build'
LOGS=BUILD/'logs'


def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    with (LOGS/'metrics-monitor-test-build.log').open('w') as out:
        subprocess.run(['verilator','--binary','--timing','--assert','--top-module','DMAMonitorTest',
                        '--Mdir',str(BUILD/'monitor-test'),'-Wno-fatal',
                        str(WORK/'sim/DMAPassiveMonitor.sv'),str(WORK/'tests/DMAMonitorTest.sv')],
                       check=True,stdout=out,stderr=subprocess.STDOUT)
    for arg in ('','+denied','+corrupt','+bad-completion'):
        r=subprocess.run([str(BUILD/'monitor-test/VDMAMonitorTest'),'+dma-trace']+([arg] if arg else []),
                         text=True,capture_output=True)
        text=r.stdout+r.stderr
        (LOGS/('metrics-monitor-test'+arg.replace('+','-')+'.log')).write_text(text)
        if arg=='+bad-completion':
            assert r.returncode!=0 and 'COPY_completed_before_memory_ack' in text
        else:
            assert r.returncode==0 and 'DMA_MONITOR_TEST PASS' in text,text
            h,_=hardware(text)
            assert (h['denied'],h['corrupt'])==(int(arg=='+denied'),int(arg=='+corrupt'))
            config=records(text,'DMA_CONFIG')
            assert len(config)==1 and config[0]['accept_cycle']>0
        print('PASS passive observer:',arg or 'stalls/latencies')

if __name__=='__main__':
    main()
