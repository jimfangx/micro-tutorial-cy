# RoCC DMA discovery tutorial

All editable settings are in **[config.yaml](config.yaml)**. The original
Chipyard tree is an input. Only `loop/` is written by this implementation.
Experiment storage defaults to `loop/experiments/`; set `storage` to a durable
local disk with room for seed trees, build artifacts, and checkpoints.

From the Chipyard root:

```sh
source env.sh
python loop/bootstrap.py
python loop/loop.py image
python loop/loop.py doctor
python loop/loop.py run --run tutorial
```

`doctor` makes a short real request through the selected CHIA agent inside its
Docker sandbox. **Working authentication and project/model access are startup
invariants**, checked before a new search or resume. Antigravity uses OAuth;
having an `agy` executable or token file is insufficient. Its GCP project also
needs the relevant API enabled and permission to use the selected model.
`agent.backends.antigravity.gcp_project` and `gcp_location` override routing in a
private credential copy; host settings/tokens are never edited. Credentials
are not stored in experiments. Each proposal starts a fresh provider session,
reconstructing context from durable parent/inspiration sources and metrics.

The defaults run two proposals, two islands, up to two concurrent generations,
one build, and four simulator processes. A first build is expensive. This is a
real Chisel/Verilator flow, using the current local Chipyard implementation,
not a host DMA model. Every configured benchmark runs independently through
CHIA; each candidate builds **one** SoC simulator, shared by its benchmark tasks.
Standalone correctness gates finish before performance fan-out. The seed also
runs all CPU-only Embench binaries for a separate comparison.

## Read the loop

Start with [search.py](chia_dma/search.py). It is the small orchestration adapter:

1. Call native `OpenEvolveNativeDatabase.sample()` for a parent and inspirations.
2. Checkpoint selection; dispatch the configured CHIA CLI model in a sandbox.
3. Save the exact proposal **before** evaluating it.
4. `DMAEvaluator` uses `ChiaEvaluator` to build once, gate correctness, and fan out.
5. Calculate fitness and call native `add()` **only** for a valid candidate.

OpenEvolve owns island populations, MAP-Elites cells, sampling, migration, and
population serialization. The loop adds filesystem isolation and checkpoints
at generation/build/evaluation transitions. It does not implement another search
algorithm, database service, profiler, or logger.

[containers.py](chia_dma/containers.py) uses CHIA's `DockerManager` with a local
transport. Each generation receives a read-only parent tree and writable file
mounts for exactly the two DMA implementation files. It receives no original
checkout, experiment archive, other candidate workspace, or Docker socket.
Builds copy the immutable seed into independent writable trees. Simulation
containers receive only the simulator artifact, a frozen ELF, and DRAM settings.
The integration mixin and disabled L1 interface are checked before compilation.

[metrics.py](chia_dma/metrics.py) parses the existing Embench and DMA microbench
records. Correctness, complete coverage, deterministic benchmark outputs, and
unchanged DMA activity gate admission. Fitness is a weighted geometric mean of
speedups relative to the DMA-enabled seed, with separately normalized Embench
and microbenchmark groups. Embench programs with no timed DMA activity remain
correctness checks. All raw metrics and speedups are retained. Missing counters
cause a clear invalid result; there are no synthetic cycle estimates.

## Recovery

```sh
python loop/loop.py status --run tutorial
python loop/loop.py resume --run tutorial
python loop/loop.py replay --run tutorial --candidate candidate-<id>
python loop/loop.py fork --run tutorial \
  --checkpoint 00000012-updated --new-run tutorial-fork
python loop/loop.py resume --run tutorial-fork
```

Use an actual checkpoint name from `experiments/tutorial/checkpoints/`.
`replay` reevaluates the historical source under a new candidate ID without an
LLM call or a population update. `resume` restores the native population,
Python RNG, in-flight tasks, and CHIA SQLite database. A completed proposal,
build, or simulation is recovered from durable artifacts through CHIA bypass;
CHIA's bounded output cache is an additional optimization. Deleting/evicting the
cache does not delete proposals or checkpoints. `fork` copies the selected
checkpoint into a separate database/run namespace and preserves its origin.

Change `agent.backend` to `claude` or `codex` to switch agents on resume. Set its
binary/credentials/options in the same configuration file. This preserves the
population/history and starts fresh conversational context. You may increase
`search.iterations` or change resource limits on resume. Hardware/software,
fitness/search rules, evaluator code, dependency revisions, and container image
are checked for drift; changing them requires a new experiment and baseline.

The driver runs CHIA/Ray on this host by default and shuts it down on exit.
For an existing scheduler, set `runtime.ray_address`; its trusted orchestration
workers need the configured resource labels, Docker access, and identical
persistent filesystem/toolchain paths. SQLite remains on the driver's local
storage. Agent processes themselves never run on those trusted workers.

## Artifacts and checks

Each run contains `run.json`, `software/manifest.json`, `reference.json`,
`metrics.db`, immutable `candidates/<id>/{rtl,changes.patch,proposal.json}`,
`artifacts/<id>/` (simulator, logs, per-workload records and metrics), native
`checkpoints/`, and CHIA profiler output. Source snapshots include uncommitted
files and submodule revisions. Original external Git pointers are replaced by
inert revision markers needed by Chipyard's initialization checks. Exact source
hashes and the original Git provenance remain in the seed manifest.

```sh
loop/.venv/bin/python -m pytest -c loop/pytest.ini loop/tests -q
```

The tests cover real RTL measurement fixtures, invalid-result rejection,
proposal immutability, fixed integration boundaries, actual Docker permissions,
native islands/migration/checkpoint recovery, historical forks, and backend
selection. These contract tests do not substitute for a live tutorial run.
See [INTEGRATION.md](INTEGRATION.md) for the inspected upstream APIs and current
live-validation evidence.
