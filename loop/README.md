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

This section creates a new experiment. For an existing checkpoint, skip to
[restore without Chipyard source setup](#restore-in-a-fresh-github-clone).

The host needs Bash, Docker access, and passwordless SSH to the configured
hosts with Docker available to that SSH user. No host Python, CHIA, Conda,
Java, sbt, Verilator, RISC-V compiler, or agent CLI installation is used.
The example is single-host: source/state mounts and Docker volumes must be
available on that host. Authenticate your selected provider using its container
CLI and retain its login files at the credential paths in `backend.yaml`.

`container.sh` starts a controller from the published CHIA base image. Bootstrap
installs the pinned Python integrations and Docker/SSH client tools **inside
that container**. Workers use published CHIA images and a read-only package
volume. No derived image is built.

```sh
RUN=run-001
./loop/container.sh "$RUN" bootstrap
# A NEW experiment needs initialized generator/benchmark source inputs.
# For a fresh full checkout, initialize source submodules inside the container:
./loop/container.sh "$RUN" bash -c '
  git config --global url.https://github.com/.insteadOf git@github.com:
  bash scripts/init-submodules-no-riscv-tools.sh
  git submodule update --init tests/embench-iot
'
```

Skip source initialization when the required submodules are already populated.
That Git URL setting lives in the controller's private home.
Do not run Chipyard `build-setup.sh` or source a host `env.sh`: CHIA's build image
supplies the toolchain. The controller and workers use image Python 3.10.19 and
Ray 2.54.0; build tools come from the build image's own Chipyard environment.
Native YAML supplies head/worker import paths. SkyDiscover is provided by the
pinned evolve-flows submodule, installed by bootstrap inside the container.

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

./loop/container.sh "$RUN" prepare
./loop/container.sh "$RUN" chia up "$CLUSTER" -y 2>&1 | tee -a "$RUN_DIR/cluster-up.log"
./loop/container.sh "$RUN" doctor
./loop/container.sh "$RUN" run 2>&1 | tee -a "$RUN_DIR/driver.log"
```

`prepare` freezes current local implementation inputs, including relevant
uncommitted/untracked files, without resetting the checkout. `chia up` pulls the
pinned upstream worker images. Builds use the preserved source, not the image's
bundled Chipyard checkout. `doctor` makes a real provider/BashTool checksum call;
`run` also probes authentication when new LLM work is needed.

Antigravity/Claude binaries come from their upstream images. Codex is installed
with pinned npm inside its generation container, using the upstream Claude
image's Node runtime. SCons is installed inside the build container. Provider logins are copied
from read-only mounts into a private worker home. Existing login files can be
reused. Refresh expired credential files through the provider's container CLI
and redeploy workers to refresh their private copies.

## Stop and resume

Press **Ctrl-C once in the driver terminal** and wait for it to exit. This stops
search/evaluation and releases the run's writer lock. Then tear down that run's
native workers and Ray head:

```sh
./loop/container.sh "$RUN" chia down "$CLUSTER" -y
```

Use the deployment YAML that actually started those workers. Keep it until
teardown; do not regenerate it with changed ports/providers first. A completed
driver also leaves workers running until `chia down`. Teardown retains experiment
files, SQL history, checkpoints, and the preserved seed. Other live runs should
use their own deployment YAML and distinct names/ports.

To continue the latest state later, set `RUN`, `RUN_DIR`, and
`CLUSTER` as above, then:

```sh
./loop/container.sh "$RUN" prepare
./loop/container.sh "$RUN" chia up "$CLUSTER" -y
./loop/container.sh "$RUN" resume 2>&1 | tee -a "$RUN_DIR/driver.log"
```

If the cluster is still up with the same configuration, just run `resume`.
For a driver started without an interactive terminal, send SIGINT from another
terminal with `./loop/container.sh "$RUN" bash -c "pkill -INT -f 'loop.py (run|resume)'"`
and wait for its exit before `chia down`. After teardown, optional
`./loop/container.sh "$RUN" remove` removes the controller; state and its Docker
package volume remain. Recreating that controller requires bootstrap again.
There is no explicit save command or checkpoint YAML field: checkpoints commit
automatically after seed initialization and controller callbacks. After an
interruption, the latest native checkpoint and SQL completions recover pending
work. Completed SQL proposals do not invoke the generation LLM again; unfinished
generations can. The optional authentication probe is a separate provider call.
Build/simulation reuse depends on CHIA cache contents; evicted or unfinished
tasks may run again.

This container-only setup changes the frozen worker contract. Earlier
host-toolchain experiments require their original matching code/settings;
start a new run for this setup. Existing experiment files are retained.

To switch providers: stop the driver, use `chia down` on its existing deployment,
edit `agent.backend`, then `prepare`, `chia up`, and `resume`. Saved proposals
retain their original provider and sources.

## Find a candidate or checkpoint

These commands are read-only and work while the driver runs:

```sh
./loop/container.sh "$RUN" status        # latest checkpoint's recovery envelope
cat "$RUN_DIR/latest.json"               # checkpoint directory name
ls "$RUN_DIR/checkpoints"                # increasing checkpoint sequence
```

`status` shows checkpointed admitted candidate IDs and generated/evaluated flags;
SQL shows newer work that may not have reached a checkpoint yet. To list committed
checkpoints and their admitted candidates without loading the search engine:

```sh
./loop/container.sh "$RUN" python - "$RUN_DIR" <<'PY_CHECKPOINTS'
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
./loop/container.sh "$RUN" fork --checkpoint "$CHECKPOINT" --new-run branch-001
# Alternative: run this instead of the command above.
CANDIDATE=candidate-xxxxxxxx
./loop/container.sh "$RUN" fork --candidate "$CANDIDATE" --new-run branch-001
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

**Restore-only installation needs no Chipyard source setup or host toolchain.**
Check out only `loop/`; the saved seed supplies generator/benchmark/parser inputs,
and published CHIA images supply build/runtime tools. Docker, SSH/Docker access,
and provider credentials are still needed.

A GitHub clone supplies code, not experiment state. Keep the matching loop code,
selected checkpoint, SQL, frozen ELFs, and the entire seed referenced by `run.json`.
The following same-host example uses a new GitHub clone beneath the source
checkout's mounted directory so its controller can write the destination:

```sh
SOURCE="$PWD"
DEST="$SOURCE/loop/experiments/branch-checkout"
RUN=run-001
# Replace these with your tutorial repository URL and matching published commit.
REPO_URL=https://github.com/YOUR-ORG/cy-chia-tutorial.git
CODE_REVISION=YOUR_MATCHING_COMMIT
./loop/container.sh "$RUN" bash -s -- "$REPO_URL" "$DEST" "$CODE_REVISION" <<'SH_CLONE'
git clone --filter=blob:none --sparse "$1" "$2"
git -C "$2" sparse-checkout set loop
git -C "$2" checkout "$3"
SH_CLONE
```

This creates an independent Git clone. No source submodules or Chipyard setup
are needed in it. For a matching loop with uncommitted changes, copy them too
using container tools; the seed excludes `loop/`:

```sh
./loop/container.sh "$RUN" bash -c 'rsync -a --exclude .deps --exclude .venv --exclude experiments --exclude __pycache__ --exclude .pytest_cache loop/ "$1/loop/"' bash "$DEST"
CHECKPOINT=00000003-xxxxxxxx
./loop/container.sh "$RUN" fork --checkpoint "$CHECKPOINT" \
  --new-run "$DEST/loop/experiments/branch-001"
# --candidate "$CANDIDATE" may replace --checkpoint "$CHECKPOINT".
cd "$DEST"
RUN=branch-001
./loop/container.sh "$RUN" bootstrap
```

The destination must not already contain `branch-001`. Bootstrap can run in the
new clone while the source driver continues. Its controller mounts the run's
preserved seed read-only, even when that seed is outside the new checkout.
Keep restored state in place **before starting its controller/prepare** so it
reuses the seed instead of capturing the sparse checkout. A new experiment
requires initialized source inputs; a restored experiment does not.

For a destination outside the controller's project mount, fork locally first,
then use a published CHIA container with source/destination bind mounts and
`rsync -a` to transfer that stopped child directory to the other clone. Do not
copy a live SQL main file directly. The seed can remain shared by absolute path
on the same machine.

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
./loop/container.sh "$RUN" prepare
./loop/container.sh "$RUN" chia up "$CLUSTER" -y
./loop/container.sh "$RUN" resume 2>&1 | tee -a "$RUN_DIR/driver.log"
```

`prepare` regenerates absolute mounts for the new clone. Do not reuse the
source's rendered cluster YAML. Fork copies frozen benchmark ELFs and the
selected native checkpoint plus filtered SQL; expensive cache entries, raw logs,
and transient workspaces stay in the source experiment. They are not needed for
search recovery, but keep the source experiment if you need its raw evidence.

For a restore on another machine, transfer a completed fork directory and its
**entire** seed directory (`manifest.json` and `tree/`, preserving symlinks/modes),
bootstrap the matching container dependencies, and update only the copied
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
./loop/container.sh "$RUN" chia status --chia-cluster "$CLUSTER"
# Task durations/cache callback profiling; emits a consolidated CSV table.
./loop/container.sh "$RUN" chia viz-profile "$RUN_DIR/artifacts/profile/ChiaProfileCollector.log" --format table
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
./loop/container.sh "$RUN" python - "$RUN_DIR" <<'PY_METRICS'
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
./loop/container.sh "$RUN" python - "$RUN_DIR" "$CANDIDATE" <<'PY_CANDIDATE'
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
./loop/container.sh "$RUN" replay --candidate "$CANDIDATE"
```

A replay has a new `replay-*` SQL row and fresh cache tags. If the original driver
is live, fork first and replay in the child instead. One writer is allowed per
run. Completed evaluation records repair interruption before population admission.
Native checkpoints do not save RNG state: already selected work retains its
parent/island context, while future stochastic choices can differ. Runs require
the current matching checkpoint format; there are no old-format migrations.

```sh
./loop/container.sh "$RUN" python -m pytest -c loop/pytest.ini loop/tests -q
./loop/container.sh "$RUN" bash -c 'CHIA_LIVE_AGENTS=1 python -m pytest -c loop/pytest.ini loop/tests/test_native_cluster.py -q'
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
| `container.sh` | Launch the published controller container and execute tutorial/CHIA commands. |
| `bootstrap.py` | Prepare pinned CHIA/evolve-flows sources and install Python packages inside the controller. |
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
