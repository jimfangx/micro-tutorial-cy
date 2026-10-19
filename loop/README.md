# DMA discovery with CHIA and SkyDiscover

This tutorial evolves the two DMA accelerator Scala files and evaluates them
against Embench. CHIA deploys the workers and runs/caches builds and simulations;
SkyDiscover's `DiscoveryController` owns the search through `openevolve_native`.

Edit [config.yaml](config.yaml) for native CHIA placement, resource/container
counts, container names/images, Ray connection, LLM provider, and proposal counts.
[backend.yaml](backend.yaml) specifies the fixed mounts, credentials, dependency
pins, isolation, and evaluation settings. `prepare` combines them into a native
`deployment/cluster.yaml`; pass that generated file to `chia up/down`.

**Checkpoint selection is a CLI option, not a YAML setting.** `resume --run NAME`
loads the run's latest checkpoint. To continue from an older checkpoint, use
`fork --checkpoint NAME --new-run OTHER`, then resume the new run. A fork restores
full historical search state, including the population and islands.

All commands below run from the Chipyard repository root. Replace example run,
candidate, checkpoint, and destination names with your own.

## Set up and run

Use an initialized Chipyard checkout with its RISC-V/Java/Verilator environment,
Docker access, passwordless SSH to the configured hosts, and a valid host login
for the selected provider. For a new machine, follow the repository's
[Chipyard setup instructions](../docs/Chipyard-Basics/Initial-Repo-Setup.rst)
(`./build-setup.sh riscv-tools`) first. `bootstrap.py` installs loop dependencies;
it does not initialize Chipyard or configure SSH/provider logins.

```sh
source env.sh
# PyYAML is needed to read the configuration before bootstrapping.
python -m pip install 'PyYAML==6.0.3'
python loop/bootstrap.py
# Once per fresh Chipyard environment: CHIA's host dependencies, plus the
# lightweight SkyDiscover dependencies used by this tutorial. The loop venv
# inherits these; installing Ray here also provides .conda-env/bin/ray.
python -m pip install -e loop/.deps/chia numpy tqdm python-dotenv
```

The validated host uses Python 3.10 and Ray 2.54.0. The loop venv inherits the
activated Chipyard environment, which must be at `.conda-env` (or the fixed
`backend.yaml` toolchain path). Workers mount that environment read-only, so the
head and workers use matching Python/Ray versions.
Bootstrap supplies the driver's installed dependencies; native CHIA
`head_env_commands` and `worker_env_commands` supply task import paths.

Choose `agent.backend: antigravity`, `codex`, or `claude` in `config.yaml`.
`search.iterations` is the **total** number of search proposals across all
resumes, excluding the seed and replay evaluations. To extend a run, increase
that total. `search.max_in_flight` bounds concurrent candidate iterations;
generation `num_workers` sets the number of isolated generation workspaces.
Build and simulation pools have independent limits. Compilation uses all CPUs
available to the host process, simulations fan out one task per available CPU,
and the JVM heap is fixed at 32 GiB.

```sh
RUN=run-001
RUN_DIR="$PWD/loop/experiments/$RUN"
CLUSTER="$RUN_DIR/deployment/cluster.yaml"
set -o pipefail

python loop/loop.py prepare --run "$RUN"
loop/.venv/bin/chia up "$CLUSTER" -y 2>&1 | tee -a "$RUN_DIR/cluster-up.log"
python loop/loop.py doctor --run "$RUN"
python -u loop/loop.py run --run "$RUN" 2>&1 | tee -a "$RUN_DIR/driver.log"
```

`prepare` freezes current local implementation inputs, including relevant
uncommitted/untracked files, without resetting the checkout. `chia up` pulls the
pinned upstream worker images. Builds use the preserved source, not the image's
bundled Chipyard checkout. `doctor` makes a real provider/BashTool checksum call;
`run` also probes authentication when new LLM work is needed.

Antigravity/Claude binaries come from their upstream images. Codex uses
read-only host binary mounts on the CHIA base image. Provider logins are copied
from read-only mounts into a private worker home. Refresh a host login and
redeploy workers if the private copy has expired.

## Stop and resume

Press **Ctrl-C once in the driver terminal** and wait for it to exit. This stops
search/evaluation and releases the run's writer lock. Then tear down that run's
native workers and Ray head:

```sh
loop/.venv/bin/chia down "$CLUSTER" -y
```

Use the deployment YAML that actually started those workers. Keep it until
teardown; do not regenerate it with changed ports/providers first. A completed
driver also leaves workers running until `chia down`. Teardown retains experiment
files, SQL history, checkpoints, and the preserved seed. Other live runs should
use their own deployment YAML and distinct names/ports.

To continue the latest state later, source `env.sh`, set `RUN`, `RUN_DIR`, and
`CLUSTER` as above, then:

```sh
python loop/loop.py prepare --run "$RUN"
loop/.venv/bin/chia up "$CLUSTER" -y
python -u loop/loop.py resume --run "$RUN" 2>&1 | tee -a "$RUN_DIR/driver.log"
```

If the cluster is still up with the same configuration, just run `resume`.
There is no explicit save command or checkpoint YAML field: checkpoints commit
automatically after seed initialization and controller callbacks. After an
interruption, the latest native checkpoint and SQL completions recover pending
work. Completed SQL proposals do not invoke the generation LLM again; unfinished
generations can. The optional authentication probe is a separate provider call.
Build/simulation reuse depends on CHIA cache contents; evicted or unfinished
tasks may run again.

To switch providers: stop the driver, use `chia down` on its existing deployment,
edit `agent.backend`, then `prepare`, `chia up`, and `resume`. Saved proposals
retain their original provider and sources.

## Find a candidate or checkpoint

These commands are read-only and work while the driver runs:

```sh
python loop/loop.py status --run "$RUN"   # latest checkpoint's recovery envelope
cat "$RUN_DIR/latest.json"               # checkpoint directory name
ls "$RUN_DIR/checkpoints"                # increasing checkpoint sequence
```

`status` shows checkpointed admitted candidate IDs and generated/evaluated flags;
SQL shows newer work that may not have reached a checkpoint yet. To list committed
checkpoints and their admitted candidates without loading the search engine:

```sh
python - "$RUN_DIR" <<'PY_CHECKPOINTS'
import json, pathlib, sys
run = pathlib.Path(sys.argv[1])
latest = json.loads((run / "latest.json").read_text())["checkpoint"]
for path in sorted((run / "checkpoints").glob("[!.]*/state.json")):
    state = json.loads(path.read_text())
    print(path.parent.name, "(latest)" if path.parent.name == latest else "")
    print("  admitted:", ", ".join(state["admitted"]))
PY_CHECKPOINTS
```

Choose an exact snapshot, or let `--candidate` find the earliest committed
checkpoint containing an admitted candidate:

```sh
CHECKPOINT=00000003-xxxxxxxx
python loop/loop.py fork --run "$RUN" --checkpoint "$CHECKPOINT" --new-run branch-001
# Alternative: run this instead of the command above.
CANDIDATE=candidate-xxxxxxxx
python loop/loop.py fork --run "$RUN" --candidate "$CANDIDATE" --new-run branch-001
```

Omit both selectors to fork the latest checkpoint. The destination must not
exist. Checkpoint names are directory basenames, not paths. A candidate must
already appear in a committed admission set. A concurrent snapshot can contain
other candidates too: the fork restores the entire snapshot, not just that
candidate, and subsequent parents are selected by native SkyDiscover sampling.

Forking works while the source driver is running: it reads immutable checkpoints
and takes an online SQLite backup, then removes history later than the selected
snapshot. The source run continues independently. To run the child here, use its
run name in `prepare`, `chia up`, and `resume`; use separate Ray ports if the
source cluster remains up, as described next.

## Restore in a fresh GitHub clone

A GitHub clone supplies code, not experiment state. You need the matching loop
implementation, the source experiment, and the preserved seed referenced by its
`run.json`. The seed contains the fixed base Chipyard/generators, benchmark
sources, parser, and relevant local changes. SQL contains each proposal's two
complete Scala sources, lineage, and generation response.

The following same-host workflow keeps the original running and shares only its
immutable seed/toolchain. Start in the **source checkout**, whose loop code must
match the run being restored:

```sh
SOURCE="$PWD"
DEST=/absolute/path/cy-chia-branch
REPO_URL="$(git remote get-url origin)"
CODE_REVISION="$(git rev-parse HEAD)"
git clone "$REPO_URL" "$DEST"
git -C "$DEST" checkout "$CODE_REVISION"
```

The repository is the tutorial's Chipyard fork containing `loop/` and the DMA
integration; cloning vanilla upstream Chipyard alone does not supply them.
The selected code commit must be available from GitHub. If the matching loop
has uncommitted changes, also copy those files; a Git revision cannot restore
them. For example, copy the current loop implementation without runtime state:

```sh
rsync -a --exclude .deps --exclude .venv --exclude experiments \
  --exclude __pycache__ --exclude .pytest_cache "$SOURCE/loop/" "$DEST/loop/"
```

Restore the selected full checkpoint directly into the new clone. Use a
**different run name** to avoid container/head directory collisions:

```sh
RUN=run-001
CHECKPOINT=00000003-xxxxxxxx
python "$SOURCE/loop/loop.py" fork --run "$RUN" --checkpoint "$CHECKPOINT" \
  --new-run "$DEST/loop/experiments/branch-001"
# --candidate "$CANDIDATE" may replace --checkpoint "$CHECKPOINT".

# Reuse this host's initialized toolchain; create a new loop-local venv.
ln -s "$SOURCE/.conda-env" "$DEST/.conda-env"
source "$SOURCE/env.sh"
cd "$DEST"
python loop/bootstrap.py
```

The restored run builds from its self-contained seed, so a second submodule or
Chipyard toolchain initialization is unnecessary for this same-host restore.
To create an unrelated new seed from the new checkout later, initialize that
checkout with the normal Chipyard setup first.

In the **new clone's** `loop/config.yaml`, choose unused ports in all four places:

| Setting | Source example | Branch example |
| --- | --- | --- |
| Head Ray `--port` | `6387` | `6388` |
| Worker Ray `--address=$RAY_HEAD_IP:PORT` | `6387` | `6388` |
| `runtime.ray_address` | `127.0.0.1:6387` | `127.0.0.1:6388` |
| Head `--dashboard-port` | `8287` | `8288` |

Increase `search.iterations` beyond the retained search proposal count if you
want additional proposals. Keep fixed evaluation/search rules and image pins
unchanged. Each build still uses all available CPUs and 32 GiB of heap; two
simultaneous clusters share the host's physical resources.

```sh
RUN=branch-001
RUN_DIR="$PWD/loop/experiments/$RUN"
CLUSTER="$RUN_DIR/deployment/cluster.yaml"
python loop/loop.py prepare --run "$RUN"
loop/.venv/bin/chia up "$CLUSTER" -y
python -u loop/loop.py resume --run "$RUN" 2>&1 | tee -a "$RUN_DIR/driver.log"
```

`prepare` regenerates absolute mounts for the new clone. Do not reuse the
source's rendered cluster YAML. Fork copies frozen benchmark ELFs and the
selected native checkpoint plus filtered SQL; expensive cache entries, raw logs,
and transient workspaces stay in the source experiment. They are not needed for
search recovery, but keep the source experiment if you need its raw evidence.

For a restore on another machine, transfer a completed fork directory and its
**entire** seed directory (`manifest.json` and `tree/`, preserving symlinks/modes),
install the matching toolchain/loop dependencies, and update only the copied
run's `run.json` `seed` path to the transferred seed. Regenerate the deployment
with `prepare`. There is no portable export/import or checkpoint migration
command; [INTEGRATION.md](INTEGRATION.md#restore-contract-and-compatibility)
details the required files and compatibility limits, including CPU-count changes.

## Logs and consolidated metrics

Set `RUN_DIR` to the experiment you want to inspect. The driver log exists if you
used `tee` above. Build/simulation logs appear as tasks finish, not as live streams:

```sh
tail -f "$RUN_DIR/driver.log"
ls "$RUN_DIR/artifacts"
CANDIDATE=candidate-xxxxxxxx
tail -n 80 "$RUN_DIR/artifacts/$CANDIDATE/build.log"
tail -n 80 "$RUN_DIR/artifacts/$CANDIDATE/embench_dma_wikisort.log"
tail -n 80 "$RUN_DIR/software-build-0.log"
loop/.venv/bin/chia status --chia-cluster "$CLUSTER"
# Task durations/cache callback profiling; emits a consolidated CSV table.
loop/.venv/bin/chia viz-profile "$RUN_DIR/artifacts/profile/ChiaProfileCollector.log" --format table
```

The configured Ray dashboard is at `http://127.0.0.1:8287` (branch example:
`8288`). Worker Ray logs are under `/tmp/ray/session_latest/logs` **inside each
container**; use `docker ps` to find its rendered name, then `docker exec` to
inspect those files. Head Ray logs are under `/tmp/ray/dma-$RUN/session_latest/logs`.
`cluster-up.log` captures native deployment output when started with the command
above. Generation response/transcript data lives in SQL, not a separate log file.

`metrics.db` is the consolidated numerical and lineage store. This read-only
query lists all proposals, their stage, score, source hash, and parent/island:

```sh
python - "$RUN_DIR" <<'PY_METRICS'
import pathlib, sqlite3, sys
path = (pathlib.Path(sys.argv[1]) / "metrics.db").resolve()
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
    rows = db.execute("""
        SELECT id, parent, island, kind, backend,
          CASE WHEN source IS NULL THEN 'generation pending'
               WHEN metrics IS NULL THEN 'evaluation pending'
               WHEN json_extract(metrics, '$.validity') = 0 THEN 'rejected'
               ELSE 'valid; see checkpoint for admission' END AS stage,
          round(json_extract(metrics, '$.combined_score'), 5) AS score,
          source_hash
        FROM proposals ORDER BY sequence
    """)
    print("\t".join(column[0] for column in rows.description))
    for row in rows:
        print("\t".join("-" if value is None else str(value) for value in row))
PY_METRICS
```

A score above `1` means faster than the seed on scored workloads; validity must
be `1`. Admission is confirmed by the checkpoint's `admitted` set. To inspect
per-benchmark cycles/speedups and the generation response for one proposal:

```sh
CANDIDATE=candidate-xxxxxxxx
python - "$RUN_DIR" "$CANDIDATE" <<'PY_CANDIDATE'
import pathlib, sqlite3, sys
path = (pathlib.Path(sys.argv[1]) / "metrics.db").resolve()
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
    for row in db.execute("""
        SELECT name, json_extract(metrics, '$.benchmark_cycles'),
          json_extract(metrics, '$.speedup'),
          json_extract(metrics, '$.region_offloaded'),
          json_extract(metrics, '$.region_bytes')
        FROM benchmarks WHERE candidate=? AND name LIKE 'embench:dma:%'
        ORDER BY name
    """, (sys.argv[2],)):
        print(row)  # workload, timed cycles, speedup, timed offloads, timed bytes
    error = db.execute("SELECT metrics FROM benchmarks WHERE candidate=? AND name='error'",
                       (sys.argv[2],)).fetchone()
    if error:
        print("rejection:", error[0])
    response = db.execute("SELECT json_extract(generation, '$.response') FROM proposals WHERE id=?",
                          (sys.argv[2],)).fetchone()
    if response:
        print("generation response:", response[0])
PY_CANDIDATE
```

Use `$.transcript` instead of `$.response` for the saved agent transcript. The
seed's benchmark rows provide the fitness reference; there is no `reference.json`.
Live read-only queries are safe. To copy a live database, use `fork` or SQLite's
backup API rather than copying `metrics.db` alone while its WAL is active.

## Evaluation, replay, and validation

Each generation initializes a separate workspace from its selected parent. The
frozen tree is read-only and only the two accelerator files are writable; other
candidates, evaluator inputs, and historical SQL proposals are not writable or
mounted as candidate archives. BashTool runs on that same restricted worker.
The native CHIA trusted-agent/host-network model applies.

One simulator build is shared by independent Embench ELF tasks. The seed runs
CPU and DMA modes; candidates run DMA mode. The evaluator uses the frozen parser and checks simulator assertions, Embench
verification, CPU/DMA output agreement, candidate output agreement, full coverage,
and DMA activity consistency. Fitness is a weighted
geometric mean of seed/candidate timed cycles on workloads with timed DMA
activity. Remaining workloads are correctness checks. Invalid candidates cannot
enter valid native archives. MAP-Elites uses source size and timed DMA bytes per
benchmark cycle.

Replay is an optional fresh evaluation of saved source, independent of restoring
search state. It makes no generation call and does not change the population:

```sh
# With this run's native cluster up, and no other writer for this same run:
python loop/loop.py replay --run "$RUN" --candidate "$CANDIDATE"
```

A replay has a new `replay-*` SQL row and fresh cache tags. If the original driver
is live, fork first and replay in the child instead. One writer is allowed per
run. Completed evaluation records repair interruption before population admission.
Native checkpoints do not save RNG state: already selected work retains its
parent/island context, while future stochastic choices can differ. Runs require
the current matching checkpoint format; there are no old-format migrations.

```sh
loop/.venv/bin/python -m pytest -c loop/pytest.ini loop/tests -q
CHIA_LIVE_AGENTS=1 loop/.venv/bin/python -m pytest -c loop/pytest.ini \
  loop/tests/test_native_cluster.py -q
```

See [INTEGRATION.md](INTEGRATION.md) for ownership, checkpoint contents, restore
requirements, and validation evidence. Start reading the implementation at
[search.py](chia_dma/search.py), then [adapter.py](chia_dma/adapter.py),
[evaluation.py](chia_dma/evaluation.py), and [state.py](chia_dma/state.py).

## File roles

| File | Role |
| --- | --- |
| `config.yaml` | CHIA cluster resources, container names/images, startup, and tutorial search controls. |
| `backend.yaml` | Fixed implementation, deployment, and evaluation settings. |
| `bootstrap.py` | Install pinned Python dependencies in the loop environment. |
| `loop.py` | Application commands and experiment lifecycle. |
| `chia_dma/config.py` | Merge settings, resolve paths/CPU count, and select frozen settings. |
| `chia_dma/runtime.py` | Preserve inputs, render deployment, attach CHIA cache/profiler, and authenticate. |
| `chia_dma/search.py` | Initialize the seed and SkyDiscover controller. |
| `chia_dma/adapter.py` | CLI-agent generation with restricted native BashTool workspaces. |
| `chia_dma/tasks.py` | CHIA tasks to compile Embench, build a simulator, and run an ELF. |
| `chia_dma/evaluation.py` | Build once, fan out ELFs, collect/cancel tasks, and record evaluation. |
| `chia_dma/metrics.py` | Frozen-parser adapter, Embench validation, and fitness. |
| `chia_dma/state.py` | SQL proposals/history and native population checkpoint/recovery hooks. |
| `chia_dma/artifacts.py` | Self-contained seed, source constraints, atomic metadata, and run lock. |
| `chia_dma/__init__.py` | Python package marker. |
| `tests/test_contract.py` | Source, provider, parser, and fitness contracts. |
| `tests/test_controller_integration.py` | Controller concurrency, cache/profiler, and recovery integration. |
| `tests/test_native_cluster.py` | Real native worker placement, mount boundaries, staging, and optional provider checks. |
| `tests/native_fixture.py` | Temporary localhost SSH fixture for native cluster tests. |
| `tests/test_preservation.py` | Dirty/untracked source preservation and independent restoration. |
| `tests/fixtures/rtl-measurements.json` | Recorded Embench inputs for aggregation tests. |
| `tests/fixtures/embench-observation.txt` | Recorded hardware plus synthetic software counters for parser integration. |
| `pytest.ini` | Test import paths. |
| `README.md` | Tutorial procedure and file map. |
| `INTEGRATION.md` | Infrastructure choices and validation evidence. |
