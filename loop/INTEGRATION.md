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
| Fixed base Chipyard/evaluator inputs | One immutable, self-contained source seed |
| Materialized accelerator sources | Separate native worker workspaces |

## Operating the tutorial

[README.md](README.md) contains the runnable procedures:

- [Setup and run](README.md#set-up-and-run): initialize the Chipyard environment,
  bootstrap loop dependencies, `prepare`, native `chia up`, `doctor`, then `run`.
- [Stop and resume](README.md#stop-and-resume): interrupt the foreground driver,
  wait for cleanup, then `chia down` with the original rendered deployment YAML.
  `resume --run NAME` reads `latest.json`; increase the YAML proposal total to
  extend the search.
- [Choose a checkpoint](README.md#find-a-candidate-or-checkpoint): inspect committed
  `state.json` envelopes, then `fork --checkpoint NAME --new-run OTHER` or
  `fork --candidate ID --new-run OTHER`. No checkpoint selector lives in YAML.
- [Restore in a fresh clone](README.md#restore-in-a-fresh-github-clone): obtain
  matching loop code, fork into the clone's experiment directory, retain access
  to the preserved seed, bootstrap that clone, regenerate its deployment, then
  `chia up` and `resume`. Separate live clusters require distinct run names,
  head/worker ports, dashboard ports, and matching `runtime.ray_address` values.
- [Inspect logs and metrics](README.md#logs-and-consolidated-metrics): driver log,
  raw candidate logs, native CHIA profiling, and read-only consolidated SQL queries.

`run` and `resume` use the same recovery path. There is no special restore action:
selecting an older snapshot creates an independent fork, whose `latest.json`
points at that snapshot. `replay` is a separate evaluation command and is not
needed for a historical continuation.

## Images and environment

`config.yaml` shows container names and pins Linux amd64 upstream image manifests
by digest: CHIA's
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
Bootstrap owns the driver's dependency imports; native `head_env_commands` and
`worker_env_commands` own task import paths. The application adds no job-level
`PYTHONPATH` override or per-command dependency path injection.
The tutorial-facing `config.yaml` shows native cluster placement, worker resources
and counts, Ray startup commands, and provider/search controls. A small recursive
merge overlays these on the fixed backend settings. Cache/bypass read their fixed settings directly from
the backend YAML. Host CPU affinity supplies compilation jobs and simulation
slots automatically; both build heap settings are fixed at 32 GiB.
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
The original seed's benchmark rows are also the sole fitness reference. Generation
recovers through its initial SQL lookup; the duplicate CHIA generation bypass
callbacks have been removed. Build/simulation cache and bypass remain native.
Native checkpoints own programs, best/iteration, islands, elite archives, feature
maps/statistics, and migration counters. Their small companion envelope records
admitted IDs and generated/evaluated flags from one SQL snapshot. It supplies
only the information missing from native checkpoints, not another population
serializer or per-stage task journal.

Fork copies the selected native checkpoint and a filtered SQLite backup. Later
proposals, generations, and evaluations are removed from the fork; pending
requests retain their original context. Consequently, a proposal completed
*after* a historical snapshot is not a completed proposal in that fork, even if
it exists in the source run now. The no-regeneration guarantee applies to
completed proposals retained at the selected recovery boundary. Replay
materializes the same source from SQL with fresh expensive-task tags and makes no LLM call or population
admission. Cache eviction never deletes SQL proposals. There are no checkpoint
migrations or format-version branches; create new runs after changing the loop.
Native RNG state is not checkpointed, so only already selected work is promised
exact context recovery.

Committed checkpoint directories use a monotonically increasing sequence plus a
unique suffix. A hidden temporary directory becomes visible through atomic
rename; only then is `latest.json` replaced. Sequence order, not filesystem
modification time, determines history. Candidate selection chooses the earliest
committed checkpoint whose admission set contains that candidate. This supports
full historical continuation while the source is live: checkpoint directories are immutable after atomic rename,
and SQLite's online backup captures history before the fork filters later work.
An absolute destination permits another loop checkout's experiment directory.
Separate live native clusters need distinct head/dashboard ports and a matching
`runtime.ray_address`; no custom deployment or networking controller is added.

## Experiment files and queries

Paths below are relative to `loop/experiments/NAME/`, except for the shared seed.

| Path | Contents and use |
| --- | --- |
| `run.json` | Absolute preserved-seed path, frozen settings hash, loop Git revision/dirty flag, and fork provenance. |
| `metrics.db` | CHIA SQLite proposal/source archive and consolidated numerical history. Keep WAL/SHM companions while a writer is live. |
| `latest.json` | Name of the latest committed checkpoint, used by resume. |
| `checkpoints/SEQUENCE-SUFFIX/state.json` | Frozen settings hash, admitted candidate IDs, and generated/evaluated flags for SQL requests at that boundary. |
| `checkpoints/SEQUENCE-SUFFIX/population/` | SkyDiscover's native program and population checkpoint files. |
| `software/manifest.json` and `software/*.elf` | Frozen Embench inventory, ELF hashes, and toolchain provenance. Verified before evaluation/resume. |
| `deployment/cluster.yaml` | Rendered native CHIA configuration with absolute paths. Regenerate after relocation; retain the old copy until its cluster is down. |
| `work/generation-N/` | Transient two-file writable workspace for one serial generation worker. Reinitialized from the chosen parent before each invocation. |
| `cache/` | CHIA's evictable expensive-task outputs. Not the authoritative proposal or population archive. |
| `artifacts/ID/build.log` | Collected build stdout/stderr for a candidate. |
| `artifacts/ID/embench_dma_BENCHMARK.log` | Collected simulator output; seed evaluation also has CPU logs. |
| `artifacts/profile/ChiaProfileCollector.log` | Native CHIA JSONL task profiling; `chia viz-profile ... --format table` aggregates task durations. |
| `software-build-0.log` | Embench compiler output. |
| `driver.log`, `cluster-up.log` | Optional terminal captures created by the README's `tee` commands. |
| `../seeds/HASH/manifest.json` and `tree/` | Complete preserved implementation/evaluation source, including relevant uncommitted/untracked files. Shared read-only across forks. |

The `proposals` table is keyed by candidate `id`. It holds `parent`, `island`,
`sequence`, `kind` (`seed`, `search`, or `replay`), selected request context, the
two complete sources in JSON, `source_hash`, generation response/transcript,
provider/model, final numerical `metrics`, and native `program_id` linkage.
A missing `source` means generation is unfinished; a present source with missing
`metrics` means evaluation is unfinished. `metrics.validity = 0` means rejection.
`combined_score`, `source_bytes`, and `dma_bytes_per_cycle` live in that metrics
JSON. A program ID by itself does not prove committed admission: check the
checkpoint's `admitted` set.

The `benchmarks` table is keyed by `(candidate, name)`. Its JSON metrics retain
per-workload software cycles/results, activity, hardware observer counters, and
copy events. Valid scored DMA rows additionally include speedup and correctness
fields. A row named `error` contains the rejection reason. Seed benchmark rows
are the sole fitness reference; no `reference.json` is used. The README provides
queries using Python's standard-library SQLite client, so no separate database
CLI or metrics service is required. These queries can run alongside a live
writer. Raw logs are written when task results are collected; Ray logs/dashboard
provide worker diagnostics while tasks are still running.

## Restore contract and compatibility

A standalone native population directory is insufficient for this tutorial:
its program payloads refer to proposal IDs, and evaluation also needs SQL, the
fixed source seed, frozen ELFs, and matching settings. Preserve the following:

1. The selected committed checkpoint, including its `population/` and `state.json`.
2. Consistent `metrics.db` history, `run.json`, `latest.json`, and the frozen
   `software/` directory. Use `fork` to collect/filter this set while the source
   is running; do not copy a live SQLite main file without its WAL or a backup.
3. The entire referenced seed directory, including `manifest.json`, the full
   tree, executable permissions, and symlinks. The seed is fixed and self-contained;
   restoring sources overwrites its two Scala files in a private worker checkout.
4. The matching loop implementation/configuration and pinned dependencies, plus
   a compatible initialized Chipyard toolchain, credentials, Docker, and SSH.

`fork` copies the selected native checkpoint and an online SQL backup, retains
frozen software files, and updates the child run's metadata/latest pointer. It
excludes old `artifacts/`, `cache/`, `deployment/`, `work/`, and writer locks. The
source's raw logs remain there. The child can rebuild expensive outputs without
another generation call for retained completed proposals. It shares the source
seed by its absolute path; copying that seed is unnecessary on the same filesystem.
The child does not need to retain the source experiment once its state is copied,
but it must retain access to the seed and any shared toolchain it still uses.

On another machine/filesystem, transfer the completed child directory and its
seed while the child is stopped. For example, with shell variables pointing at
the actual source/destination locations, `rsync -a "$SEED/" "$NEW_SEED/"` preserves
the seed tree's links/modes. Edit **only the copied run** to point at the new seed:

```sh
python - "$NEW_RUN_DIR" "$NEW_SEED" <<'PY_RELOCATE'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1]) / "run.json"
meta = json.loads(path.read_text())
meta["seed"] = str(pathlib.Path(sys.argv[2]).resolve())
path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
PY_RELOCATE
```

Keep the seed contents unchanged and use matching loop/evaluation settings;
`prepare` is a frozen-settings check and deployment renderer, not an importer or
an automatic seed manifest audit. Install dependencies in the new toolchain,
configure local SSH/provider paths, then follow the README's `prepare`, `chia up`,
and `resume` procedure. Rendered YAML, venvs, and old absolute deployment paths
are not portable and must be recreated.

Compatibility is deliberately limited:

- No old checkpoint-format migrations or compatibility branches are supported.
  Use matching loop code, native dependency pins, and evaluation/search rules.
  `run.json` records a Git revision and dirty flag, not an archive of loop code.
  The seed excludes `loop/`; preserve uncommitted loop changes separately.
- `prepare`/State reject a changed frozen configuration. Fixed hardware/software,
  fitness, native search policy, dependency pins, and build/simulation worker
  image/mount/environment/setup contracts are part of that hash. It is not a
  complete toolchain binary fingerprint.
- Provider/model, total proposal count, in-flight limit, placement, resource
  counts, and Ray connection ports can change between resumes. Redeploy changed
  workers with native `chia down/up`. Generation workspace serialization remains
  enforced.
- Automatic host CPU count is included as `hardware.make_jobs` in frozen settings.
  A host with a different CPU-affinity count therefore fails the current restore
  check. This is not an automatic cross-machine portability workflow; the
  tutorial's normal policy remains all available cores and a fixed 32 GiB heap.
- Native checkpoints restore full populations, islands, valid archives, feature
  maps/statistics, best/iteration metadata, and migration counters. They do not
  save RNG state. Pending selections preserve their original parent/island and
  inspirations, but future sampling and asynchronous completion order can differ.
- A fork preserves work visible at its selected boundary. A normal latest-state
  resume additionally uses durable SQL completions made since that checkpoint to
  repair interruption before admission. Replay uses a new ID/fresh task tags,
  records new metrics, and never adds a population member.

## Evidence

The [import-environment suite](experiments/refactor-checks/native-import-environment-final-suite.log)
passed 17 tests with three provider checks skipped; one recovery fixture timed
out starting its local Ray head. The [focused recovery/migration rerun](experiments/refactor-checks/native-import-recovery-final-check.log)
passed both checks. Native tests attach through the production `Runtime` without
a job-level import override and verify pinned imports on the head and build,
simulation, and generation workers. CLI imports also passed with `PYTHONPATH`
unset while launching from outside the checkout.

The operating instructions were checked against the current CLI. All 17 shell
examples pass `bash -n`; checkpoint listing and both read-only SQL examples ran
against the recovery suite's completed experiment. The relocation example ran
against a temporary manifest, internal documentation links resolved, and native
`chia viz-profile --format table` produced an aggregate from the suite's profiler
log. These documentation checks did not launch new LLM calls, builds, or simulations.

[The four-reduction suite](experiments/refactor-checks/four-trims-final-suite.log)
passed 18 tests with three opt-in provider tests skipped. It includes SQL-only
reference recovery, completed-generation reuse through the production SQL guard,
and selecting a candidate checkpoint while the source controller is running.
The branch restores the full native population/history and then continues
independently; the source continues without being paused or modified by the fork.

The [configuration split checks](experiments/refactor-checks/config-split-check/proof.json)
validated all three providers against CHIA's native cluster schema, including
three isolated generation workers, 32 compile jobs/simulation slots on this
host, and the fixed 32 GiB heap. The [standard suite](experiments/refactor-checks/split-config-suite.log)
passed 17 tests, with three live-provider tests skipped. These configuration
changes did not trigger another RTL build or long simulation run.

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
