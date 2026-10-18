# RoCC DMA discovery with CHIA and SkyDiscover

Edit [config.yaml](config.yaml), then follow a candidate through
[search.py](chia_dma/search.py). SkyDiscover's `DiscoveryController` runs the
search with `search.type: openevolve_native`; CHIA runs the CLI agents, builds,
and simulations. All tutorial changes and experiment writes stay under `loop/`.

The local example requires the initialized Chipyard toolchain, Docker, passwordless
SSH to the configured worker hosts, and permission to run the configured Docker
and firewall commands. Set `cluster.auth` in the central configuration; the
same YAML anchor supplies generation-worker authentication. The bootstrap does
not change host SSH configuration or credentials.

```sh
source env.sh
python loop/bootstrap.py
python loop/loop.py image
python loop/loop.py prepare --run run-001
loop/.venv/bin/chia up loop/experiments/run-001/deployment/cluster.yaml -y
python loop/loop.py doctor --run run-001
python loop/loop.py run --run run-001
loop/.venv/bin/chia down loop/experiments/run-001/deployment/cluster.yaml -y
```

`prepare` freezes the current local source and renders ordinary CHIA YAML.
`up` and `down` actions on `loop.py` are conveniences for the same native CLI.
Build and simulation pools remain up until `chia down`; each generation worker
is brought up and down around its own proposal. Images, mounts, credentials,
setup commands, environment, networks, and worker counts live in `config.yaml`.
Do not edit generated deployment files. Bootstrap applies the two small pinned
CHIA fixes described in [INTEGRATION.md](INTEGRATION.md).

Configure `agent.backend` as `antigravity`, `claude`, or `codex`, with its binary,
credentials, and options under `agent.backends`. `doctor` makes a real BashTool
request through that backend. Antigravity requires an authenticated project with
model access and the Vertex AI API enabled. Claude also mounts its separate
`.claude.json` configuration. Credentials are copied into a private temporary
home; project/location overrides never change the host credential files.

## Follow a candidate

1. Native `sample()` selects the parent and inspirations. A small durable request
   records that selection and its island before the CLI call.
2. The [CLI adapter](chia_dma/adapter.py) initializes two writable files from
   that parent, starts a native CHIA generation worker, and calls the selected
   CHIA agent with native `BashTool`. Each concurrent proposal has its own worker.
3. The completed proposal is archived before evaluation. Only the two complete
   Scala files, lineage, source hash, response, and optional diff are needed.
4. `ChiselBuildNode` builds the implementation and runs `DMAControllerSpec`.
   `VerilatorRunNode` fans out all correctness and performance workloads using
   the shared native build artifact and frozen ELF files.
5. The frozen DMA parser validates observations and outputs. Native `add()`
   admits only fully valid candidates, followed by a native population checkpoint.

The controller owns iteration concurrency, result processing, and checkpoint
callbacks. `search.max_in_flight` bounds candidate pipelines; native CHIA resource
slots independently bound generation, compilation, and simulation. The small
population adapter preserves concurrent selections and pins admission to the
selected island. Native sampling, MAP-Elites, migration, and save/load remain in
SkyDiscover.

Generation mounts the fixed source tree read-only, then binds exactly
`DMAController.scala` and `RoCCDMA.scala` as writable project files. The evaluator,
benchmarks, historical proposals, other candidates, and Docker socket have no
writable mount. A separate network and firewall rules deny host services and
private networks while allowing provider access. BashTool and its private Ray
service run inside this boundary. Denying a forbidden BashTool write is tested,
including when two different parents generate concurrently.

## Source, state, and recovery

A seed is one self-contained directory at `experiments/seeds/<hash>/tree`, with
file hashes, Git revision, and a provenance diff alongside it. It includes local
uncommitted and untracked implementation inputs and retained build dependencies.
Git object stores and generated outputs are excluded. Inert `.git` marker files
preserve Chipyard's initialized-generator discovery; they are not repositories.
Restoration copies this fixed tree and overwrites the two saved Scala files.
It needs no original checkout, recursive cloning, commits, or patch application.
The original checkout is never reset or cleaned.

| State | Owner |
| --- | --- |
| Population, islands, archives, feature maps, migration | SkyDiscover native checkpoints |
| Expensive build and simulation results | CHIA cache and bypass |
| Numerical metrics, workload results, lineage queries | CHIA `SQLiteNode` |
| Selected requests and completed generations | Small durable candidate archive |

CHIA `get` performs cache callbacks; bypass reads completed build/simulation
outputs. A cold or evicted cache reruns those tasks. Generation bypass reads the
immutable proposal archive, so a cache miss cannot repeat a completed LLM call.
Profiler output and simulator logs remain diagnostics. Software is built once
and its 43 ELF inputs are frozen and hash checked.

```sh
python loop/loop.py status --run run-001
python loop/loop.py resume --run run-001
python loop/loop.py replay --run run-001 --candidate candidate-<id>
python loop/loop.py fork --run run-001 \
  --checkpoint <name-from-checkpoints-directory> --new-run run-001-fork
python loop/loop.py up --run run-001-fork
python loop/loop.py resume --run run-001-fork
python loop/loop.py down --run run-001-fork
```

Resume recovers the original parent/island selection. A completed generation
requires no provider login or new LLM call. A completed evaluation interrupted
before admission is recovered from its numerical record. Invalid candidates
remain terminal diagnostic records outside valid archives. Native migration
copies retain the original candidate identity.

Each checkpoint adds only admitted candidate IDs and flags describing which
requests had completed generation/evaluation at that point. A historical fork
filters out later proposals and numerical history, including later completions
of requests that were still pending at the selected checkpoint. Replay creates
fresh task tags, evaluates exactly the archived source without an LLM call, and
does not change the population. Backend/model changes apply to new generations;
evaluation/search settings, worker image identity, parser, and ELF inputs stay frozen.
Use the matching tutorial implementation when resuming; a seed preserves hardware
and software inputs rather than a separate executable copy of the search driver.

New runs use **format 3**. Legacy experiment directories are preserved; resuming
or forking their older checkpoint formats requires their matching previous loop
implementation. Native checkpoints do not contain pending CLI work or random
number generator state. Saved pending selections are recovered exactly; future
stochastic selections are not promised to match an uninterrupted run bit for bit.

## Evaluation and validation

Fitness remains the weighted geometric mean of measured DMA speedups relative
to the seed, with separately normalized Embench and microbenchmark groups.
Full workload coverage, deterministic outputs, passive hardware observations,
protocol tests, and unchanged DMA activity gate admission. CPU-only seed runs
provide an independent comparison. There are no estimated cycle counts.

Defaults allow two candidate pipelines, two generations, one build, and 24
simulations. Workloads fan out together; resource availability and contention
still affect elapsed time. The full 2 MiB correctness workload previously took
about 80 minutes on this host. The refactor's recovery/cache tests use small
fixture tasks rather than that long simulation.

```sh
loop/.venv/bin/python -m pytest -c loop/pytest.ini loop/tests -q
CHIA_LIVE_AGENTS=1 loop/.venv/bin/python -m pytest -c loop/pytest.ini \
  loop/tests/test_native_cluster.py -q
```

The native cluster tests use real Docker/SSH and create temporary SSH fixtures
under `loop/`. Live provider calls are opt-in. See
[INTEGRATION.md](INTEGRATION.md) for checkpoint contents, API choices, and the
scope of validation evidence.
