# Passive DMA metrics validation

All additional-metrics validation gates passed in Verilator. The DMA Scala sources are byte-for-byte
unchanged from the verified first cut. The observer has inputs only and is compiled by the simulator
Makefile, outside hardware elaboration/synthesis. No performance-counter registers or control paths
were added to the accelerator. Software statistics have the ordinary cost of RAM counter updates.

Raw instruction smoke/basic correctness, both memcpy shims, and all microbenchmark size/alignment
cases passed, including source and destination guard checks. Monitor tests covered stalls, response
latency, denied/corrupt responses, and rejection of completion before the final write acknowledgement.

## Benchmark checks

| Mode / benchmark | mcycle timed region | Intercepted | Attempts | Completed | Fallback | Bytes | Min / max cycles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| cpu / wikisort | 2157902 | 0 | 0 | 0 | 0 | 0 | 0 / 0 |
| cpu / aha-mont64 | 2602200 | 0 | 0 | 0 | 0 | 0 | 0 / 0 |
| dma / wikisort | 1727226 | 456 | 276 | 276 | 180 | 124776 | 439 / 3834 |
| dma / aha-mont64 | 2602204 | 0 | 0 | 0 | 0 | 0 | 0 / 0 |

CPU/DMA benchmark return values and native verifier results matched. The zero-offload case is a
compatibility check. Both modes use identical board timing/statistics code and benchmark flags.

## Standalone timing

Software cycles include the complete blocking API call, including bookkeeping. Hardware cycles
are measured passively from COPY acceptance to response handshake. Bandwidth is calculated here
from useful bytes and raw cycles, outside the accelerator and shim.

| Bytes | Source / destination alignment mod 64 | Status | CPU cycles | API cycles | Hardware cycles | Hardware bytes/cycle |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 8 | 0 / 0 | 0 | 157 | 561 | 34 | 0.2353 |
| 24 | 0 / 0 | 0 | 220 | 220 | 62 | 0.3871 |
| 64 | 0 / 0 | 0 | 536 | 261 | 132 | 0.4848 |
| 256 | 0 / 0 | 0 | 2072 | 663 | 525 | 0.4876 |
| 1024 | 0 / 0 | 0 | 8216 | 2237 | 2098 | 0.4881 |
| 4096 | 0 / 0 | 0 | 33164 | 8499 | 8355 | 0.4902 |
| 65536 | 0 / 0 | 0 | 580815 | 119305 | 119087 | 0.5503 |
| 64 | 8 / 24 | 0 | 536 | 297 | 165 | 0.3879 |
| 256 | 16 / 56 | 0 | 2072 | 698 | 557 | 0.4596 |
| 24 | 56 / 56 | 0 | 216 | 247 | 95 | 0.2526 |
| 64 | 1 / 0 | 2 | 544 | 41 | n/a | n/a |
| 64 | 0 / 3 | 2 | 530 | 38 | n/a | n/a |
| 9 | 0 / 0 | 2 | 86 | 38 | n/a | n/a |

Unsupported alignments/lengths return status 2 without a hardware COPY or destination modification.
Successful cases include dirty sources, cached destinations, differing alignments, cache-line/page
crossings, and a 64 KiB streaming transfer. The original full 2 MiB correctness suites remain documented
in RESULTS.md; their RTL has not changed. This incremental validation does not repeat the entire
19-program suite. It rebuilds all 19 in both modes and executes the four benchmark runs required here.

All raw counters, latency extrema, eight size buckets, TileLink metrics, individual COPY timestamps,
and artifact hashes are in build/metrics-validation.json and build/logs. No missing result is accepted.
See OBSERVABILITY.md for the stable format, boundaries, CHIA integration, and reproduction command.
