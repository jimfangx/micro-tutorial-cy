# First-cut verification and measurements

Environment: Chipyard base `157dd69e`, Rocket `ece7b9ad5`, Embench `09c2ed8`,
Chisel 6.7.0, Verilator 5.022, RISC-V GCC 16.1.0, SCons 4.11.1.

Verified during implementation:

- Unmodified `RocketConfig`: existing bare-metal `hello.c` passed.
- Command-only bring-up: CONFIG, zero-length COPY, unsupported command passed.
- Controller RTL protocol suite: passed in Verilator, including backpressure,
  delayed read/write responses, response stalls, busy commands, invalid ranges,
  reset, failed read, and failure after an earlier successful write.

Reproduction commands and remaining full-system results are recorded below as
those runs complete. Build logs and simulation transcripts are under `build/logs`.
No performance improvement is required or assumed.

Additional direct full-system checks passed on the final DMA hardware: an
8-byte dirty-source copy and a 1 KiB copy (128 read/write units). Both compared
CPU reads against the source after the COPY response and synchronization.
The larger normal and randomly stalled correctness runs, and the C wrapper
suite, are still running; their completion is not yet claimed.

The DMA-enabled standalone wrapper test passed: 12 intercepted calls,
5 hardware-offloaded copies, 7 CPU fallbacks, 12,936 DMA bytes, and 25,347
measured DMA operation cycles (including submission/fences). It also checked
return values, unsupported-transfer fallback, guards/source integrity, and that
`memmove` remains on the CPU. CPU-wrapper and microbenchmark runs are pending.

The first large byte-loop test runs were intentionally stopped before completion
and replaced with equivalent aligned 64-bit initialization/comparison loops.
The transfer sizes, DMA transactions, guards, and full byte/source coverage are
unchanged. This reduces test-only CPU work in RTL simulation; it does not change
the accelerator design. These interrupted runs are not counted as passing.

The CPU-only wrapper test also passed: 12 intercepted calls, all 12 handled by
CPU fallback, zero offloaded copies, and zero DMA bytes/cycles. The standalone
microbenchmark has now started in the same live test sequence.

## Completed standalone copy microbenchmark

Cycles are raw `rdcycle` deltas. DMA API includes validation and statistics;
operation cycles include fences and command execution; setup covers initial
fence through CONFIG response. No threshold or hardware tuning was performed.

| Bytes | CPU cycles | DMA API cycles | DMA/CPU | Operation cycles | Setup cycles |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 151 | 315 | 2.086 | 51 | 9 |
| 24 | 217 | 137 | 0.631 | 81 | 9 |
| 64 | 534 | 207 | 0.388 | 151 | 9 |
| 256 | 2070 | 609 | 0.294 | 553 | 9 |
| 1024 | 8298 | 2208 | 0.266 | 2152 | 9 |
| 4096 | 33089 | 8616 | 0.260 | 8560 | 9 |
| 65536 | 583923 | 120198 | 0.206 | 120142 | 9 |

Both wrapper variants and the microbenchmark completed successfully. The
normal and randomly stalled large correctness suites remain pending.

Both normal and random-delay full-system correctness runs have now passed all
cases through 64 KiB: 0, 8, 24, 64, 192, 8,192, and 65,536 bytes. These include
cache-line/page crossings and transfers exceeding the configured L1 capacity.
The 2 MiB case and subsequent repeated/error checks remain in progress; neither
full suite is counted as complete yet.

The complete normal and random-delay standalone correctness suites both passed,
including the 2 MiB transfers and final repeated/error checks. Embench board
support and SCons integration are now being built. GNU C11 is selected for this
Embench version's pre-C23 `bool` typedef; benchmark sources remain unchanged.

DMA-enabled Embench wikisort passed native verification and returned benchmark
result 0. Total main execution: 2,610,856 cycles; timed region: 1,714,895 cycles.
It intercepted 456 memcpy calls, offloaded 276, used 180 CPU fallbacks, and
transferred 124,776 bytes with 253,757 DMA operation cycles and 2,504 setup cycles.
The timed region contained 184 DMA copies totaling 83,184 bytes. DMA errors: 0.
The CPU wikisort run and remaining suite are still pending.

CPU-baseline wikisort also passed with matching raw/verifier results. CPU main
cycles: 3281860; CPU timed cycles: 2157835. DMA/CPU
main ratio: 0.796; timed ratio: 0.795. Baseline DMA operations: 0.
The remaining 18 CPU/DMA benchmark pairs are now running automatically.
