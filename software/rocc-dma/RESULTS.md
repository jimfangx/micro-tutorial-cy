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
