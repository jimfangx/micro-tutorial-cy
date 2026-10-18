# Passive DMA observation contract (version 1)

The accelerator command ABI in README.md is unchanged. No DMA Scala, cache,
interconnect, MMIO, or datapath counters were added. `sim/DMAPassiveMonitor.sv`
is an input-only SystemVerilog `bind` observer. `sims/verilator/Makefile` includes
`sim/Makefrag` for DMARocketConfig and DMAStallRocketConfig. The monitor is compiled
only by Verilator, after hardware elaboration; it has no outputs or control
connections and is absent from synthesis. Host-side observation/logging can cost
simulation wall time, but cannot add accelerator cycles or a hardware critical
path. The software shim maintains a fixed 160-byte RAM statistics structure;
these ordinary software counter updates have a small measured CPU cost.

Keep the observer, binding script, board support, software library, and parser
fixed during evolution. CHIA's candidate allowlist remains the two DMA Scala
files. The CHIA build node uses the ordinary simulator Makefile and automatically
includes the observer. `loop/chia_dma/metrics.py` validates its captured `log` and
`out` through the `scripts/metrics.py` frozen with its seed before accepting an evaluation.
New seeds capture this observer and require every version-1 field and completion
record. Seeds predating the observer/parser must be captured again before use
with the current evaluator. No evolutionary
fitness is calculated in the accelerator or bare-metal software.

## Record format

Records are newline-terminated ASCII: a tag followed by space-separated
`key=integer` fields, including `metrics_version=1`. Integers are decimal, cycle
counts/addresses/counters use 64 bits, and benchmark return values may be signed.
Unknown additional fields can be ignored; missing required fields, duplicate
keys/records, malformed values, unsupported versions, assertion failures,
nonzero simulator exit, timeout, or absent completion are evaluation failures.
Do not substitute zero for missing data. CHIA should pass its process status to
`metrics.parse(text, kind, success=..., returncode=...)` as the current integration
does. The CLI exits nonzero on malformed or incomplete logs. A killed simulator
will usually have no final hardware summary and is rejected as well.

- `DMA_CONFIG`: optional command acceptance event, enabled by `+dma-trace`.
  Contains `accept_cycle`, `src`, `dst`. CONFIG accepts on cmd.valid && cmd.ready.
- `DMA_COPY`: one per response-accepting COPY, emitted by the simulator. Fields:
  `seq`, `bytes`, `src`, `dst`, `config_cycle`, `accept_cycle`, `valid_cycle`,
  `response_cycle`, `hw_cycles`, `status`, `reads`, `writes`, `read_bytes`,
  `write_bytes`. `accept_cycle` is cmd.valid && cmd.ready; `valid_cycle` is the
  first response-valid cycle; `response_cycle` is resp.valid && resp.ready.
  `hw_cycles = response_cycle - accept_cycle`, including response backpressure.
  Status is the unmodified RoCC status (0 success, 1 command, 2 unsupported,
  3 memory failure). Unsupported COPY events may legitimately have no traffic.
- `DMA_HW`: exactly one final summary per reset epoch/run. Contains `result`,
  `cycles`, `configs`, `copies`, `completed`, `failed`, `busy_cycles`,
  `idle_cycles`, `busy_transitions`, `reads`, `writes`, `read_bytes`,
  `write_bytes`, `read_responses`, `write_acks`, `outstanding`,
  `peak_outstanding`, `outstanding_cycles`, `a_stall`, `d_stall`, `cmd_stall`,
  `resp_stall`, `read_latency`, `read_min`, `read_max`, `write_latency`,
  `write_min`, `write_max`, `denied`, `corrupt`, `protocol_errors`, `pending`.
  Cycles count rising edges of the accelerator clock, starting at 1 after reset;
  they are separate from CPU mcycle. Busy is sampled directly each cycle.
  Outstanding is accepted A transactions minus accepted D responses, tracked
  by source ID; its integral and maximum are reported. Request counts/bytes
  count accepted Get/Put transactions and their requested sizes. Latencies run
  from A handshake to D handshake, with sum/min/max separately for reads/writes.
  Stall counts mean valid && !ready on the named channel. Denied/corrupt count
  accepted D responses. `result=0` requires no incomplete command/transaction
  or monitor protocol error at termination. Existing TileLink monitors still
  check full protocol stability, masks, and legality.
- `DMA_PROTOCOL_FAIL`: explicit observer assertion failure, followed by a fatal
  simulation failure; never a successful measurement.
- `EMBENCH`: exactly one software summary after main/verification, with
  `result`, `benchmark_result`, `verification_result`, `main_cycles`,
  `benchmark_cycles`, and the statistics below. All statistics also have a
  `region_` counterpart for the timed execution interval.
- `DMA_STATS`: standalone wrapper statistics with the same field names.
- `MICRO` / `MICRO_ALIGN`: standalone performance/correctness samples containing
  `result`, `bytes`, physical `src`/`dst`, `src_align`/`dst_align` (modulo 64),
  `status`, `copy_seq`, `cpu_cycles`, `dma_cycles`, `op_cycles`, `setup_cycles`.
  `copy_seq` joins a supported sample to its passive DMA_COPY record. Unsupported
  sizes/alignments return status 2 and copy_seq 0, without issuing COPY.
  `DMA microbenchmark PASS` is required after all samples. `DMA_TEST_FAIL`
  identifies data/coherence/ordering, source, guard, or status failure.

Observer records use host stderr (CHIA result.out), separately from HTIF target
stdout (CHIA result.log), with per-COPY flushing. This prevents buffered host
messages from splitting a target summary. Combine the complete streams for
parsing, preserving line boundaries.

Simulator COPY records do not execute on the target hart or add target printf
calls. The shim never prints per-copy diagnostics during a successful timed
region. A fatal shim memory failure retains its detectable exit status 90.
Summary printing is outside the measured intervals and captured statistics.
The hardware summary covers the whole simulation; benchmark software summaries
cover main or its timed region. The verifier cross-checks whole-main software
activity against hardware events, so unexpected offloading during reporting
cannot silently pass.

## Software statistics and identities

Each total and `region_` group explicitly includes zero-valued fields:

| Field | Meaning |
| --- | --- |
| intercepted | Intercepted memcpy calls |
| attempts | DMA API submissions after eligibility validation |
| offloaded | Successfully completed DMA copies |
| errors | Failed DMA attempts, including CONFIG errors |
| fallback | Calls to the original CPU memcpy |
| retry_fallback | Failed DMA attempts safely followed by CPU fallback |
| bytes | Successfully DMA-copied bytes |
| dma_cycles | Initial fence/CONFIG through COPY completion fence |
| setup_cycles | Initial fence through CONFIG response |
| e2e_cycles | API entry through completion fence, including validation/submission |
| min_cycles / max_cycles | Minimum/maximum e2e_cycles of individual attempts |
| hist0 .. hist7 | Counts of successful copies by size bucket |

The histogram has inclusive upper bounds 8, 64, 256, 1024, 4096, 16384, 65536,
and infinity bytes; its sum equals offloaded. Empty latency extrema are zero.
Extrema for the region are measured independently, rather than subtracting
cumulative extrema. The full-main extrema include warmup and verification.

`attempts = offloaded + errors` always holds. Ordinarily
`intercepted = attempts + fallback`. The existing safe fallback after status 1
or 2 is an explicit additional path, so the general identity is
`intercepted = attempts + fallback - retry_fallback`.
`retry_fallback <= errors` and `retry_fallback <= fallback`. Status 3 is fatal
and is never retried. Direct API callers also update DMA statistics but do not
increment intercepted; the memcpy identity applies to shim workloads, as in
Embench and the standalone wrapper tests.

The software e2e interval ends before updating statistics. The MICRO outer
`dma_cycles` brackets the complete API call including counter updates/return;
it is the full software overhead-inclusive latency. Neither is the hardware
COPY latency. The parser/report computes useful bytes divided by each interval
for effective bandwidth; no derived fitness enters hardware or the shim.

## Timing boundaries and passive binding

RV64 M-mode reads the entire 64-bit `mcycle` CSR with `csrr`. The shared Embench
`start_trigger()` snapshots counters, fences, then reads mcycle; `stop_trigger()`
fences, reads mcycle, then snapshots counters. The reported difference includes
the benchmark call/return and the closing fence. No reporting occurs inside.
These fences use the existing Rocket fence/RoCC ordering described in README.md.
CPU-only and DMA builds compile identical board support and benchmark flags.

The fixed binding generator reads the emitted public RoCCDMA ports, not its
controller state or internal scheduling. Firtool removes the initial engine's
constant A-size/source ports; those are bound to the fixed 8-byte/source-0
contract. Variable ports, when present, are used directly. D response size/source
are checked, so an incompatible change fails explicitly instead of silently
misreporting metrics. The current observer measures single-beat Get/Put traffic
on this existing interface. A different interface contract (e.g. multibeat
messages or extra TileLink channels) requires a new frozen evaluation contract;
it must not be admitted under these measurements by suppressing failures.

## Reproduction and extraction

```sh
source env.sh
python software/rocc-dma/scripts/verify_metrics.py
# Audit completed logs without rerunning RTL:
python software/rocc-dma/scripts/verify_metrics.py --check-only
# Extract a CHIA/local simulator transcript (stdout and stderr may be separate):
python software/rocc-dma/scripts/metrics.py embench RUN.log RUN.out --output metrics.json
python software/rocc-dma/scripts/metrics.py microbench MICRO.log --output micro.json
python software/rocc-dma/scripts/test_observer.py
python -m unittest discover -s software/rocc-dma/tests -p 'test_metrics.py'
```

Validation rebuilds both complete Embench variants and runs CPU/DMA wikisort
(actual offloading) and aha-mont64 (zero eligible copies). It also runs raw
command/basic tests, both shim modes, and seven original microbenchmark sizes
plus six alignment/unsupported cases. Monitor tests inject backpressure,
denied/corrupt responses, and early completion. The strict parser rejects broken
records and propagates CHIA timeout/exit failures. `METRICS.md` and
`build/metrics-validation.json` report this incremental validation. `RESULTS.md`
retains the earlier full 19-pair first-cut results; it predates the software
statistics extension. Regenerating that full report now requires current runs
for every binary, and stale records are rejected.

Additional files outside software/rocc-dma are limited to the Verilator Makefile
include and the fixed CHIA parser call/tests. Both are necessary to collect and
validate metrics automatically through the existing CHIA simulator results.
