# Bare-metal coherent RoCC memcpy DMA

First-cut tutorial implementation for this Chipyard checkout. Correctness is the
milestone; there is no throughput, cache-policy, or threshold tuning.

## Environment and baseline

Repository: Chipyard `157dd69e`; Rocket Chip `ece7b9ad544b07df39cd13b6fd7236562a26355d`;
Embench-IoT `09c2ed8`. Chisel 6.7.0, Verilator 5.022, RISC-V GCC 16.1.0.
Source `env.sh` from the repository root before building. SCons is needed for
Embench (`python -m pip install scons`, verified with 4.11.1).

The unmodified `RocketConfig` runs `tests/hello.c` with the repository's
`htif_nano.specs` startup/linker runtime. The existing
`tests/atexit-workaround.c` is linked with `--wrap=atexit` for this toolchain.
The DMA build uses the same runtime. No new startup assembly or linker script.
The emitted device tree reports main memory `[0x80000000,0x90000000)`, 64-byte
cache lines, a 32 KiB L1D, and a 512 KiB inclusive L2.

## Instruction contract

RV64 M-mode, physical addresses, one hart, synchronous non-reentrant API.
Custom-0 opcode `0x0b`; funct7 selects the operation:

| funct7 | funct3 | Operation | rs1 | rs2 | rd |
| --- | --- | --- | --- | --- | --- |
| 0 | 7 | CONFIG | source physical address | destination physical address | status |
| 1 | 6 | COPY | byte length | x0 | status after completion |

CONFIG stores addresses without accessing memory. COPY validates the entire
range before starting. Transfers use aligned 8-byte units; lengths must be
multiples of 8. Zero length succeeds without any memory transaction. Source and
destination ranges must be disjoint and entirely within cacheable main memory.
Commands are accepted only when idle; later commands are backpressured while
busy or while the prior response is pending. Completion is held until accepted.
CONFIG and COPY should always request a response (xd=1).

Statuses: 0 success; 1 unsupported command/operand flags; 2 invalid or
unsupported transfer (no writes issued); 3 memory transaction failure (destination
may be partially written). Software must not silently retry status 3 with CPU
copying. The caller must treat that error as fatal or recover explicitly.

## Ordering and coherence

`LazyRoCC.tlNode` attaches through RocketTile's `tlOtherMastersNode` to the
existing coherent hierarchy. Ordinary Get/Put requests are serviced by that
hierarchy, including probes of dirty CPU cache lines and invalidation of cached
destinations. There is no L1 `io.mem` access, cache maintenance, DRAM bypass,
or separate DRAM/L2 path.

The blocking API uses `fence rw,rw` with a compiler memory clobber before CONFIG
and after the COPY response. The inline custom instructions also clobber memory.
In this Rocket checkout, `RocketCore.scala` defines `id_mem_busy` from
`!io.dmem.ordered || io.dmem.req.valid`, remembers a preceding fence in
`id_reg_fence`, and stalls subsequent RoCC instructions while memory is busy.
`id_do_fence` also stalls fences while RoCC is active or in the pipeline.
COPY's response occurs only after the final TileLink write acknowledgement.
These source checks establish why the fences order these custom instructions;
the dirty-source/cached-destination tests verify the behavior in RTL.

## Implementation boundaries

The command contract is separate from the one-unit read/write datapath.
Future buffering, scheduling, and L1-access experiments can retain this ABI.
Version 1 does not support virtual addresses, MMIO, overlap, unaligned copies,
byte tails, concurrent callers, interrupts, or multiple outstanding requests.
`memmove` remains the ordinary CPU operation. Link wrapping affects external
`memcpy` references, not inlined copies or explicit load/store loops.

## Verification stages

The initial command-only bring-up passed `command-smoke` before the DMA datapath
was added. The final hardware contains a real datapath and has no placeholder
COPY operation. `DMAControllerSpec` runs the synthesized controller in Verilator
with held-off requests and replies, busy command presentation, response
backpressure, reset, invalid ranges, read failure, and a failed write after an
earlier write has committed. The `DMAStallRocketConfig` adds the repository's
`TLDelayer(0.5)` only on the accelerator path for full-system backpressure tests.
It retains TileLink monitors and the standard caches and interconnect.

The standalone `correctness` binary uses raw instructions directly, before the
C library or link interception. It checks deterministic bytes, source integrity,
destination guards, repeated dirty/cached CPU–DMA accesses, unsupported alignment
and sizes, MMIO, range overflow, and overlap rejection. The 2 MiB case exceeds
both cache capacities; a transfer starting 8 bytes before a 4 KiB boundary also
crosses cache-line and page boundaries. Unsupported-transfer responses must not
modify memory. RTL protocol tests inject failures because the normal DRAM model
does not naturally return denied/corrupt transactions.

## Build and simulation

From the repository root:

```sh
source env.sh
make -C software/rocc-dma baseline -j8
java -Xmx8G -jar scripts/sbt-launch.jar \
  -Dsbt.ivy.home="$PWD/.ivy2" -Dsbt.global.base="$PWD/.sbt" \
  -Dsbt.boot.directory="$PWD/.sbt/boot" \
  'chipyard/testOnly chipyard.dma.DMAControllerSpec'
make -C software/rocc-dma command-smoke correctness -j8
make -C sims/verilator CONFIG=DMAStallRocketConfig -j8
make -C sims/verilator CONFIG=DMAStallRocketConfig run-binary-fast \
  BINARY="$PWD/software/rocc-dma/build/correctness.riscv" \
  LOADMEM=1 TIMEOUT_CYCLES=200000000
make -C software/rocc-dma libraries software-tests -j8
```

`run-binary` is also supported and adds instruction logging. Use it or
`run-binary-debug` selectively for failures. Use `TIMEOUT_CYCLES` to set the
harness limit; appending a second `+max-cycles` argument does not override the
first value seen by the HDL harness. Large capacity tests can take substantial
host time in cycle-accurate simulation. All simulator runs exercise real RTL.

The `microbench` binary reports CPU copy cycles, full DMA API cycles, DMA
operation cycles including fences and command setup, and separately measured
setup cycles (initial fence through CONFIG response). Cycle reads add a small
fixed measurement cost; no attempt is made to subtract that cost or tune the
threshold. The CPU implementation remains the toolchain's ordinary `memcpy`.

## Software behavior

`rocc_dma_copy()` validates sizes, disjoint ranges, and physical main-memory
bounds; it is blocking and returns the hardware status. Zero bytes returns
success immediately. The standalone raw-instruction test separately exercises
hardware zero-length behavior. `DMA_RAM_BASE` and `DMA_RAM_END` default to this
configuration's emitted memory map; adjust them together with any changed
hardware memory map. Hardware independently validates against `ExtMem`.

`memcpy_wrap.c` supplies `__wrap_memcpy`, with GNU ld `--wrap=memcpy`. Its fallback
uses `__real_memcpy`, retaining the libc implementation and avoiding recursion.
The fixed `DMA_THRESHOLD` defaults to 64 bytes and can be overridden at compile
time; it has not been tuned. `DMA_DISABLED` builds a CPU-only wrapper. Hardware
memory errors terminate the shim's process with status 90, because the target
may already be partially modified. Direct API callers receive status 3 and own
error recovery. Validation and unsupported-operation failures can safely fall
back because they precede writes. The wrapper leaves `memmove` on the CPU.

The wrapper test checks zero and small transfers, alignment/length fallback,
return values, destination guards, source integrity, unwrapped `memmove`, and
nonzero actual offload counts. The emitted binary contains both `memcpy` and
`__wrap_memcpy`; disassembly confirms calls to the wrapper and its fallback to
libc. Compile benchmark units consistently with `-fno-builtin-memcpy` when
comparing CPU and DMA builds, so compiler builtins do not hide external calls.

## Files outside the accelerator source directory

- `generators/chipyard/src/main/scala/config/DMAConfigs.scala`: instantiate the
  standard Rocket tile with DMA; a second test configuration adds TL delays.
- `generators/chipyard/src/test/scala/dma/DMAControllerSpec.scala`: adversarial
  protocol and failure tests of the actual synthesized controller.
- `build.sbt`: add ChiselTest to the Chipyard test configuration only.
- `.gitignore`: ignore generated `test_run_dir` simulation artifacts.
- `software/rocc-dma/`: C API, link shim, standalone tests, build/run tooling,
  board support, and documentation, kept out of benchmark sources.

The existing Rocket execution pipeline, decoder, LSU, caches, TileLink
interconnect/coherence, and DRAM implementations are unchanged. Existing
`tests/atexit-workaround.c` is reused without modification. The pre-existing
Embench checkout is used as input; its benchmark and support sources remain
unchanged.

## Embench build and execution

The existing `tests/embench-iot/sconstruct.py` builds the unmodified benchmarks.
Board support is supplied by `embench/boardsupport.c`, with compiler/linker flags
and `user_libs` supplied through SCons's existing configuration interface.
GCC 16 defaults to C23, which conflicts with this Embench version's `bool`
typedef; both modes therefore use `-std=gnu11` along with identical `-O2`,
`rv64gc`/`lp64d`/`medany`, and `-fno-builtin-memcpy` flags. Global scale and
warmup heat are both 1. No benchmark calibration constants are changed.

```sh
# At the Chipyard root, after sourcing env.sh:
python software/rocc-dma/scripts/embench.py build
python software/rocc-dma/scripts/embench.py run --jobs 8
python software/rocc-dma/scripts/report.py
```

CPU and DMA builds live in separate `build/embench-cpu` and
`build/embench-dma` directories. CPU mode uses ordinary libc memcpy with DMA
submission disabled. DMA mode adds `--wrap=memcpy` and the DMA shim archive via
`user_libs=rocc_dma m`. `--undefined=__wrap_memcpy` retains the shim even for
programs whose memcpy references arise only later from libc or HTIF libraries.
This fixes archive search order without altering those libraries. The original
CPU memcpy is retained for fallback. Optional CPU-only interception is available:

```sh
python software/rocc-dma/scripts/embench.py build --modes wrapper
python software/rocc-dma/scripts/embench.py run --modes wrapper --bench wikisort
```

The runner executes CPU and DMA wikisort first, waits for both, then runs the
remaining 18 benchmarks in independent RTL processes. `--jobs` controls host
concurrency; each simulated system still has one hart and one DMA transfer at a
time. All modes use the same `DMARocketConfig` and Chipyard `run-binary-fast`
infrastructure with `LOADMEM=1`. `EXTRA_SIM_OUT_NAME` keeps per-mode harness logs
separate. A billion-cycle timeout bounds each run; it does not shorten workloads.
Use `--bench NAME` to select programs (wikisort remains the initial gate).

Board support wraps `main` to measure initialization, warmup, benchmark, and
verification cycles together. Startup and final reporting are excluded. The
standard `start_trigger`/`stop_trigger` functions capture the timed region and
counter snapshots without printing there. Volatile field loads prevent snapshot
operations from becoming intercepted memcpy calls. `--wrap=verify_benchmark`
records the benchmark return value and the original verifier result, preserving
the verifier's behavior. Both CPU/DMA values must match in the final report.
This matters for inherited weak verifiers, including xgboost's scale-1 check;
its actual inference loop still executes every sample. Nonzero program exit is
reported as failure by the existing HTIF simulator, and the runner also requires
an explicit successful `EMBENCH` result record with no DMA errors.

`build/embench-manifest.json` records shared flags, original source hashes,
per-mode linker flags, ELF hashes, benchmark-object memcpy references, and linked
call-site counts. Disassemblies sit next to their ELF files. Completed run
metrics, ELF/simulator/log hashes, and raw signed return values are recorded in
`build/embench-results.json`; transcripts are in `build/logs`. A rerun reuses only
completed records whose binary, simulator, and transcript hashes still match.
Do not start a duplicate runner while an earlier run is live. After all required
runs complete, `report.py` checks every standalone gate and every CPU/DMA pair
before producing `RESULTS.md` and `build/measurements.json`.

The complete reproducible sequence is `software/rocc-dma/scripts/verify.sh`.
It runs the baseline, command smoke, controller protocol tests, both coherent
correctness configurations, wrapper tests, microbenchmark, Embench builds and
runs, and final reporting. The optional Embench wrapper mode is supported but
is outside the required CPU/DMA comparison; its library is tested standalone.

The supplied benchmark input is recorded as the `tests/embench-iot` submodule in
`.gitmodules`, pinned to `09c2ed8`. That entry makes the benchmark dependency
reproducible; initialize the submodule before running these commands in a fresh
checkout. Its upstream benchmark and support files are used without edits.
The Rocket, inclusive-cache, TestChipIP, and DRAMSim2 submodule commits match the
original Chipyard base, with clean worktrees. Existing conda/environment changes
in this workspace belong to the supplied toolchain setup.

## Passive metrics for CHIA

See [OBSERVABILITY.md](OBSERVABILITY.md) for the versioned RoCC/TileLink records,
shim counters and histogram, precise mcycle boundaries, strict CHIA parser, and
reproduction commands. [METRICS.md](METRICS.md) records the incremental validation.
The accelerator RTL and its command interface are unchanged. The original
[RESULTS.md](RESULTS.md) retains the first-cut full-suite measurements from before
the software statistics extension.
