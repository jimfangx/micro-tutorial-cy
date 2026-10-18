# Reused infrastructure and validation

The tutorial pins CHIA `a4cedf24`, evolve-flows `62d27130`, and SkyDiscover
`78833ea6`. All code changes stay under `loop/`. Upstream CHIA is unpatched.

| Responsibility | Owner |
| --- | --- |
| Worker deployment/networking | Native CHIA YAML and `chia up/down` |
| Search concurrency/iteration processing | SkyDiscover `DiscoveryController` |
| Sampling, islands, MAP-Elites, migration, population save/load | Native `openevolve_native` |
| Build and simulation artifacts | CHIA nodes and cache/bypass |
| Numerical history and pending/completed proposals | CHIA `SQLiteNode` |
| Materialized accelerator sources | Separate native worker workspaces |

## Images and environment

`config.yaml` pins Linux amd64 upstream image manifests by digest: CHIA's
chisel-build, verilator-run, Antigravity, Claude Code, and base images. The base
image hosts Codex through read-only binary mounts because the inspected CHIA
checkout has no separate Codex image. There is no derived image or image-building
application command.

Image inspection and container checks verify Python/Ray, build tools, Java,
RISC-V GCC, Verilator, rsync, user permissions, and provider binaries. The local
Conda Python/JVM/Verilator tools and loop venv are mounted read-only to keep
Python/Ray versions aligned with the head. The pinned build-image RISC-V
toolchain provides the compiler and simulator libraries; the matching runtime
image provides spike-dasm. The host RISC-V libraries/disassembler require
GLIBC_2.38 and cannot be mixed into the upstream Ubuntu 22.04 runtime. The local
Verilator configuration also embeds its original host C++ compiler path, so
`hardware.extra_make_args.CXX` explicitly selects the image's C++ compiler.
The preceding SoC build validation verified this toolchain combination.
Native Ray commands run through the venv interpreter.
Builds use a fresh `/home/ray/dma-build` initialized from the preserved local
seed, rather than the image's bundled Chipyard checkout. Generation mounts the
same seed read-only at `/workspace`, with only its two worker-specific Scala
files writable. Each generation worker has one serial scheduling slot.

Native BashTool uses public node affinity to stay inside its caller's restricted
worker. CLI credentials/scratch live in a private temporary home. Ordinary host
networking follows the CHIA examples' trusted-agent model; no private network,
firewall rules, private Ray cluster, or compatibility patches are installed.

## Search and evaluation

Controller construction is at the top of `search.py`. Its public LLM initializer
provides the CHIA CLI adapter; its evaluator/context-builder hooks supply DMA
semantics. The database subclass wraps only public sample/add to preserve a
pending selection and its target island. No controller methods are copied.
At the pinned revision, EvolverNode's YAML-only entrypoint does not expose all
of these initializer/population/context/checkpoint hooks, so the tutorial
constructs the full DiscoveryController directly.

The inspected upstream `ChiaEvaluator` uses `ray.get` on build/run handles,
including lists. CHIA cache-enabled handles carry callbacks that require
CHIA `get`; that function does not accept lists of callback handles. The upstream
evaluator also discards the build result before invoking `run_fn(workload=...)`,
requiring additional state or per-evaluation wrappers to transport our immutable
artifact safely across concurrent candidates. Replacing the small DMA adapter
would add code and lose callback/cancellation behavior.

The retained collector uses standard Ray wait, CHIA get, and CHIA cancellation.
It holds the callback handle until collection and passes only raw refs to
wait/cancel. There is no second scheduler. The pipeline loads a SQL proposal,
builds once, submits Embench ELF tasks, parses captured results through
the frozen existing DMA parser, and calculates DMA-specific fitness. Simulator
assertions and Embench verification remain enabled. Compilation
failure and invalid measurements reject a candidate; infrastructure timeout or
interruption cancels unfinished tasks and leaves completed proposals recoverable.

## SQL recovery and checkpoint boundary

A `proposals` row records a selection before generation. A conditional SQL update
commits complete sources/hash/response once, before generation returns. SQLite
writes are pinned to the head through CHIA; the database file is not mounted in
generation workers. No proposal/source/diff file archive remains.

Final numerical metrics repair the evaluation-complete/admission-pending window.
Native checkpoints own programs, best/iteration, islands, elite archives, feature
maps/statistics, and migration counters. Their small companion envelope records
admitted IDs and generated/evaluated flags from one SQL snapshot. It supplies
only the information missing from native checkpoints, not another population
serializer or per-stage task journal.

Fork copies the selected native checkpoint and a filtered SQLite backup. Later
proposals, generations, and evaluations are removed from the fork; pending
requests retain their original context. Replay materializes the same source
from SQL with fresh expensive-task tags and makes no LLM call or population
admission. Cache eviction never deletes SQL proposals. There are no checkpoint
migrations or format-version branches; create new runs after changing the loop.
Native RNG state is not checkpointed, so only already selected work is promised
exact context recovery.

## Evidence

Fresh validation logs and native SSH fixtures are retained under
`experiments/refactor-checks/`. Controller/cache/recovery checks use cheap tasks
and recorded RTL observations; they do not establish new hardware performance.
Native checks exercise concurrent workspace separation, serialized reuse,
forbidden writes, SQL completion from restricted workers, pool placement, and
concurrent staging with identical simulator/ELF filenames.

[The Embench-only suite](experiments/refactor-checks/embench-only-suite.log)
passed 17 tests (three opt-in provider tests skipped). It covers the current
evaluator, scoring, controller, cache/profiler callbacks,
recovery, resume, fork/replay, backend switching, and native filesystem boundary.
Its Embench software counters are synthetic; its hardware fixture comes from
recorded RTL observations. These tests do not establish hardware performance.

[The focused real Embench check](experiments/refactor-checks/embench-only-real.log)
built exactly 38 ELFs (19 benchmarks in CPU/DMA modes), then ran CPU and DMA
Wikisort concurrently. Both passed the frozen parser and agreed on benchmark
and verification results; the DMA run exercised timed offloads. The check
reused the unchanged seed's previously built simulator. It did not rebuild RTL
or simulate the full Embench suite. Its [parsed results](experiments/refactor-checks/ssh-a13cab8fefac4bcabb8ec80fb56adc04/embench-only-real/proof.json)
record that reuse explicitly.

[Earlier live provider checks](experiments/refactor-checks/native-host-cli3.log)
passed for Antigravity, Claude, and Codex. Provider integration is unchanged by
the Embench-only simplification; these calls were not repeated for this change.
