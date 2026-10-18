"""Reject corrupt or incomplete observations; fixture captured from real RTL."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metrics import MetricsError, fields, parse, statistics

FIXTURE=(Path(__file__).parent/'fixtures/observed-basic.txt').read_text()


class MetricsTests(unittest.TestCase):
    def test_actual_hardware_completion_and_latency(self):
        r=parse(FIXTURE,'basic')
        self.assertEqual(r['copies'][0]['hw_cycles'],34)
        self.assertEqual(r['hardware']['write_acks'],1)
        self.assertEqual(r['hardware']['write_bytes'],8)

    def test_missing_or_duplicate_results(self):
        for broken in ('', FIXTURE.replace('DMA basic PASS',''),
                       '\n'.join(l for l in FIXTURE.splitlines() if not l.startswith('DMA_HW ')),
                       '\n'.join(l for l in FIXTURE.splitlines() if not l.startswith('DMA_COPY ')),
                       FIXTURE+FIXTURE):
            with self.subTest(broken=broken[:60]),self.assertRaises(MetricsError): parse(broken,'basic')

    def test_timestamp_status_traffic_and_protocol_corruption(self):
        for old,new in [('hw_cycles=34','hw_cycles=33'),('write_bytes=8','write_bytes=0'),
                        ('outstanding=0','outstanding=1'),('protocol_errors=0','protocol_errors=1'),
                        ('write_acks=1','write_acks=0'),('status=0','status=7'),
                        ('read_latency=16','read_latency=malformed'),('metrics_version=1','metrics_version=2')]:
            with self.subTest(field=old),self.assertRaises(MetricsError): parse(FIXTURE.replace(old,new),'basic')
        with self.assertRaises(MetricsError): parse(FIXTURE+'\nAssertion failed\n','basic')

    def test_process_errors_timeouts_and_missing_benchmarks(self):
        for success,code in ((False,0),(False,-9),(True,1)):
            with self.assertRaises(MetricsError): parse(FIXTURE,'basic',success=success,returncode=code)
        with self.assertRaises(MetricsError): parse(FIXTURE,'embench')
        with self.assertRaises(MetricsError): parse(FIXTURE,'microbench')

    def test_duplicate_keys_and_noninteger_fields(self):
        for line in ('DMA_HW metrics_version=1 cycles=1 cycles=2',
                     'DMA_HW metrics_version=1 cycles=0.5',
                     'DMA_HW cycles=1','DMA_HW metrics_version=1 garbage'):
            with self.assertRaises(MetricsError): fields(line)

    def test_software_identities_histogram_and_extrema(self):
        # Unit input, not a claimed RTL result. Includes the safe error/fallback path.
        s=dict(intercepted=3,attempts=2,offloaded=1,errors=1,fallback=2,retry_fallback=1,
               bytes=64,dma_cycles=30,setup_cycles=5,e2e_cycles=40,min_cycles=10,max_cycles=30,
               **{f'hist{i}':int(i==1) for i in range(8)})
        statistics(s)
        for field,value in [('attempts',3),('intercepted',2),('hist1',0),('min_cycles',31),
                            ('max_cycles',9),('bytes',0),('e2e_cycles',100),('retry_fallback',2)]:
            with self.subTest(field=field),self.assertRaises(MetricsError): statistics({**s,field:value})
        with self.assertRaises(MetricsError): statistics({k:v for k,v in s.items() if k!='attempts'})

if __name__=='__main__': unittest.main()
