# DMA discovery with CHIA and SkyDiscover

Edit only [config.yaml](config.yaml). Its comments explain the application,
worker, and provider settings. Start with [search.py](chia_dma/search.py), then
[the CLI adapter](chia_dma/adapter.py), [evaluation](chia_dma/evaluation.py), and
[SQL state](chia_dma/state.py).

Use the initialized local Chipyard/RISC-V environment, Docker, and existing
passwordless SSH to the configured worker hosts. Bootstrap installs the pinned
Python projects under `loop/`; its opening comment lists every setup step.
It does not initialize Chipyard, change SSH keys, or modify host credentials.

```sh
source env.sh
python loop/bootstrap.py
python loop/loop.py prepare --run run-001
loop/.venv/bin/chia up loop/experiments/run-001/deployment/cluster.yaml -y
python loop/loop.py doctor --run run-001
python loop/loop.py run --run run-001
loop/.venv/bin/chia down loop/experiments/run-001/deployment/cluster.yaml -y
```

`prepare` preserves current local inputs and renders ordinary native CHIA YAML.
`chia up` pulls pinned upstream build, simulation, and selected CLI images.
There is no custom image build or CHIA compatibility patch. Read-only mounts
supply the local Python/JVM/Verilator packages and preserved implementation;
the image's bundled Chipyard source is not used for compilation. Build workers
use the image's RISC-V toolchain/libraries and an explicit image C++ compiler;
simulation uses the matching image disassembler. The C++ override is necessary
because the local Verilator configuration embeds its original compiler path.
These settings keep the resulting simulator compatible with the runtime image.

Build, simulation, and generation have independent native resource limits.
The generation template expands into separate workers with separate two-file
bind mounts. Each worker has one generation slot, serializing workspace reuse.
Before invoking the agent, the adapter overwrites both files from the selected
parent. Native BashTool is pinned to the same worker. Project inputs and
original source are read-only; evaluator/benchmark files cannot be edited, and
the proposal database is not mounted in generation. The examples' trusted-agent
host-network model is used throughout.

Choose `agent.backend` as `antigravity`, `claude`, or `codex`. Upstream images
provide Antigravity/Claude binaries; Codex uses two read-only host binary mounts
on the upstream CHIA base image. Credentials are copied from read-only mounts
into a private worker home. Antigravity project/location overrides apply only
when explicitly configured, and only to that copy. `doctor` verifies login and
BashTool by calculating a checksum inside a generation workspace.

To switch providers, first use `chia down` with the existing deployment, edit
`agent.backend`, run `prepare` again, then use native `chia up` before resuming.
Saved completed proposals retain their original provider and never require a
new LLM call.

## Follow one iteration

1. SkyDiscover's native `sample()` selects a parent and inspirations; SQL records
   that context before generation.
2. A native CHIA generation task initializes its workspace, calls the selected
   CLI with BashTool, and commits both complete Scala sources and the generation
   response through CHIA SQLite before returning.
3. The evaluator loads that SQL proposal and builds one SoC simulator using
   `ChiselBuildNode`. RTL edits stay in the private build checkout.
4. Each configured benchmark ELF is submitted independently to
   `VerilatorRunNode`, sharing the compiled artifact.
5. The frozen DMA parser validates captured simulator output. DMA-specific
   workload coverage, activity consistency, measured cycles, and geometric-mean
   fitness determine the result. Invalid candidates never enter valid archives.
6. `DiscoveryController` processes results and invokes the checkpoint callback;
   native `openevolve_native` owns populations, islands, MAP-Elites, and migration.

The loop evaluates only Embench, with benchmark verification and simulator/RTL
assertions enabled. The seed runs both CPU and DMA modes for an independent
output comparison; candidates run DMA mode. Fitness is the weighted geometric
mean of reference/candidate timed-cycle ratios for workloads with timed DMA
activity. Other Embench workloads remain correctness checks. MAP-Elites uses
source size and timed DMA bytes per benchmark cycle as its descriptors.

## Recovery, replay, and forks

```sh
python loop/loop.py status --run run-001
python loop/loop.py resume --run run-001
python loop/loop.py replay --run run-001 --candidate candidate-<id>
python loop/loop.py fork --run run-001 \
  --checkpoint <checkpoint-directory-name> --new-run run-001-fork
python loop/loop.py prepare --run run-001-fork
# Use native chia up for the fork's generated deployment, then resume it.
```

One self-contained `seeds/<hash>/tree` preserves local implementation inputs,
including relevant uncommitted/untracked files. Git supplies provenance only;
restoration copies that fixed seed and overwrites the two saved sources. The
original checkout is never reset or cleaned. No complete checkout is copied per
candidate, and no recursive Git reconstruction is needed.

`metrics.db` has one authoritative `proposals` row per selection: parent/island,
source/hash, generation response, and completion/admission linkage. Workload
measurements live in `benchmarks`. There are no parallel proposal JSON, Scala,
or patch archives. Files are materialized only in worker workspaces. Numerical
history repairs evaluations completed just before admission was interrupted.

CHIA cache/bypass owns expensive build/simulation reuse; eviction may rerun
those tasks but cannot regenerate a completed SQL proposal. Native population
checkpoints carry search state. A small envelope records admitted IDs and which
SQL completions existed at each checkpoint, allowing historical forks to exclude
future work. Replay uses fresh task tags and never changes the population.

Start a new experiment after changing this loop; there are no checkpoint
migrations or format-version branches. Resume/fork/replay apply to runs created
by the current implementation. Native checkpoints do not save RNG state:
pending selections recover exactly, while future stochastic choices may differ.

## Validation

```sh
loop/.venv/bin/python -m pytest -c loop/pytest.ini loop/tests -q
CHIA_LIVE_AGENTS=1 loop/.venv/bin/python -m pytest -c loop/pytest.ini \
  loop/tests/test_native_cluster.py -q
```

Orchestration/recovery tests use inexpensive native CHIA tasks and recorded RTL
observations. Docker/SSH tests exercise real mount permissions and placement;
provider calls are opt-in. Embench ELFs fan out independently after one simulator
build. See [INTEGRATION.md](INTEGRATION.md) for validation evidence.
