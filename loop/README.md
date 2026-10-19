# DMA discovery with CHIA and SkyDiscover

This tutorial evolves the two DMA accelerator Scala files and evaluates them
against Embench. CHIA deploys the workers and runs/caches builds and simulations;
SkyDiscover's `DiscoveryController` owns the search through `openevolve_native`.

```mermaid
flowchart LR
    parent[SkyDiscover selects parent] --> agent[Generate candidate]
    agent --> build[Build simulator]
    build --> benchmarks[Run Embench in parallel]
    benchmarks --> score[Verify and score]
    score --> checkpoint[Admit valid candidate and checkpoint]
    checkpoint --> parent
```

Read `run_experiment()` and `run_search()` in [loop.py](loop.py) first.
The native controller calls `CLIClient.generate()` in [agent.py](chia_dma/agent.py),
then `DMAEvaluator.evaluate()` in [evaluation.py](chia_dma/evaluation.py). The
evaluator shows the CHIA build, benchmark fan-out, parsing, scoring, and SQL save
in order. `experiment.py` uses `save_selection`, `save_proposal`,
`save_evaluation`, and `save_checkpoint` for the corresponding durable steps.
Those three files show the flow; [experiment.py](chia_dma/experiment.py) holds
source preservation, SQL history, and checkpoint recovery.

Edit [config.yaml](config.yaml) for native CHIA placement, resource/container
counts, container names/images, Ray connection, LLM provider, and proposal counts.
[backend.yaml](backend.yaml) specifies the fixed mounts, credentials, dependency
pins, isolation, and evaluation settings. `prepare` combines them into a native
`deployment/cluster.yaml`; pass that generated file to `chia up/down`.

**Checkpoint selection is a CLI option, not a YAML setting.** `resume --run NAME`
loads the run's latest checkpoint. To continue from an older checkpoint, use
`fork --checkpoint NAME --new-run OTHER`, then resume the new run. A fork restores
full historical search state, including the population and islands.

The current machine runs the Ray head, search controller, SQLite, and cache.
Generation, compilation, and simulation run in Docker workers. Run commands on
the host, from the directory containing `loop/`, with `loop/.venv` activated. A sparse clone
containing only `loop/` supports both new runs and checkpoint restores. Replace
example run, candidate, checkpoint, and destination names with your own.

## Set up and run

This section creates a new experiment without a host Chipyard checkout. For an
existing checkpoint, see [restore in a fresh clone](#restore-in-a-fresh-github-clone).

The host needs Python **3.10.19**, Git, Bash, Docker, and passwordless SSH to the
configured host. Docker must work without sudo for that SSH user. No host
Chipyard, Java, sbt, Verilator, RISC-V compiler, or agent CLI is needed.
The example is single-host: source/state bind mounts must be available to its
Docker daemon. Authenticate your selected provider using its container
CLI and retain its login files at the credential paths in `backend.yaml`.

Create the host head's Python environment once. Bootstrap installs pinned CHIA
and SkyDiscover from evolve-flows; it installs the OpenEvolve dependencies and
omits SkyDiscover's optional AlphaEvolve backend. Workers use published CHIA
images and install missing packages inside their own containers. No derived image
or head container is created:

```sh
python3.10 -m venv loop/.venv
source loop/.venv/bin/activate
python -m pip install pyyaml==6.0.3
python loop/bootstrap.py
```

The commands below run **on the host**, with that environment activated. Native
CHIA commands manage workers and jobs. The tutorial's
`loop.py` handles source preparation and search-specific checkpoint operations;
these are not upstream CHIA subcommands.

The base implementation is [base/chipyard.patch](base/chipyard.patch), applied to
the initialized Chipyard source in the published CHIA build image. It contains
the DMA hardware, observer, software/parser, and required Embench sources. See
[base/README.md](base/README.md) for source revisions and baseline updates.
No Chipyard submodule initialization or `build-setup.sh` is needed.

The host head uses Python 3.10.19 and Ray 2.54.0, matching the workers exactly;
Ray rejects even a Python patch-version mismatch. Native YAML activates `loop/.venv` and
supplies worker import paths. Build tools come from the build image's own
Chipyard environment; the host environment is never mounted into workers.

If that Python version is unavailable locally, use
`uv venv --python 3.10.19 --seed loop/.venv` instead of `python3.10 -m venv`.

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
JOB="$RUN-$(date +%s)"
set -o pipefail

python loop/loop.py prepare --run "$RUN"
chia up "$CLUSTER" -y 2>&1 | tee -a "$RUN_DIR/cluster-up.log"
chia job submit --chia-cluster "$CLUSTER" --submission-id "$JOB" --no-wait -- \
  python -u "$PWD/loop/loop.py" run --run "$RUN"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
```

`prepare` launches the pinned CHIA build image, applies the bundled patch, and
preserves a complete read-only source seed. Its identity includes the image and
patch/script hashes. Repeated preparation reuses that seed; checkpoint restores
use the run's recorded seed. Native build workers copy it into a private checkout
for `ChiselBuildNode`; generation sees it read-only. The original development
checkout is never read or modified. `chia up` pulls the pinned worker images.
The absolute entrypoint uses the head's local checkout; omit `--working-dir` to
avoid uploading experiment artifacts. Head/worker imports come from native YAML.
`--chia-cluster` selects the correct job server from the rendered YAML, including
for a live fork.

Antigravity/Claude binaries come from their upstream images. Codex is installed
with pinned npm inside its generation container, using the upstream Claude
image's Node runtime. SCons is installed inside the build container. Provider logins are copied
from read-only mounts into a private worker home. Existing login files can be
reused. Refresh expired credential files through the provider's container CLI
and redeploy workers to refresh their private copies.

## Stop and resume

Pressing **Ctrl-C while following logs only stops the log viewer**; the submitted
job continues. In an activated host terminal, stop that job, then tear down its workers
and Ray head:

```sh
chia job stop "$JOB" --chia-cluster "$CLUSTER"
chia job logs --chia-cluster "$CLUSTER" "$JOB" > "$RUN_DIR/$JOB.log"
chia down "$CLUSTER" -y
```

Use `chia job list --chia-cluster "$CLUSTER"` to recover a job ID from another
terminal. Wait for the stop command to finish before teardown. Saving its logs
retains them after the job server stops.

Use the deployment YAML that actually started those workers. Keep it until
teardown; do not regenerate it with changed ports/providers first. A completed
driver also leaves workers running until `chia down`. Teardown retains experiment
files, SQL history, checkpoints, and the preserved seed. Other live runs should
use their own deployment YAML and distinct names/ports.

To continue the latest state later, activate `loop/.venv` on the host and set
`RUN`, `RUN_DIR`, and `CLUSTER` as above. Each submission needs a new
job ID; the experiment run name stays the same:

```sh
python loop/loop.py prepare --run "$RUN"
chia up "$CLUSTER" -y
JOB="$RUN-$(date +%s)"
chia job submit --chia-cluster "$CLUSTER" --submission-id "$JOB" --no-wait -- \
  python -u "$PWD/loop/loop.py" resume --run "$RUN"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
```

If the cluster is still up with the same configuration, just submit the resume
job. `chia down` stops this cluster's native head and container workers;
the checkout, Python environment, and experiment state remain available.

There is no explicit save command or checkpoint YAML field: checkpoints commit
automatically after seed initialization and controller callbacks. After an
interruption, the latest native checkpoint and SQL completions recover pending
work. Completed SQL proposals do not invoke the generation LLM again; unfinished
generations can. Build/simulation reuse depends on CHIA cache contents; evicted
or unfinished tasks may run again.

The native-head worker setup and DMA-only baseline change the frozen contract. Earlier
experiments require their original matching loop code/settings; start a new run
for this setup. Existing experiment files are retained; no checkpoint migration
is provided.

To switch providers: stop the driver, use `chia down` on its existing deployment,
edit `agent.backend`, then `prepare`, `chia up`, and `resume`. Saved proposals
retain their original provider and sources.

## Find a candidate or checkpoint

These commands are read-only and work while the driver runs:

```sh
python loop/loop.py status --run "$RUN"        # latest checkpoint's recovery envelope
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

**New runs and restores need no host Chipyard source setup or toolchain.**
Check out only `loop/`; the saved seed supplies generator/benchmark/parser inputs,
and published CHIA images supply build/runtime tools. The host still needs the
Python head environment, Docker, SSH/Docker access, and provider credentials.

A GitHub clone supplies code, not experiment state. Keep the matching loop code,
selected checkpoint, SQL, frozen ELFs, and the entire seed referenced by `run.json`.
The following same-host example creates a separate GitHub clone:

```sh
SOURCE="$PWD"
DEST="$(dirname "$SOURCE")/branch-checkout"
RUN=run-001
# Replace these with your tutorial repository URL and matching published commit.
REPO_URL=https://github.com/YOUR-ORG/cy-chia-tutorial.git
CODE_REVISION=YOUR_MATCHING_COMMIT
git clone --filter=blob:none --sparse "$REPO_URL" "$DEST"
git -C "$DEST" sparse-checkout set loop
git -C "$DEST" checkout "$CODE_REVISION"
```

This creates an independent Git clone. No source submodules or Chipyard setup
are needed in it. For a matching loop with uncommitted changes, copy them too
using `rsync`; the seed excludes `loop/`:

```sh
rsync -a --exclude .deps --exclude .venv --exclude experiments \
  --exclude __pycache__ --exclude .pytest_cache loop/ "$DEST/loop/"
CHECKPOINT=00000003-xxxxxxxx
python loop/loop.py fork --run "$RUN" --checkpoint "$CHECKPOINT" \
  --new-run "$DEST/loop/experiments/branch-001"
# --candidate "$CANDIDATE" may replace --checkpoint "$CHECKPOINT".
cd "$DEST"
RUN=branch-001
python3.10 -m venv loop/.venv
source loop/.venv/bin/activate
python -m pip install pyyaml==6.0.3
python loop/bootstrap.py
```

The destination must not already contain `branch-001`. Bootstrap can run in the
new clone while the source driver continues. Workers mount the run's preserved
seed read-only, even when that seed is outside the new checkout.
Keep restored state in place **before prepare** so it
reuses the recorded seed. Without restored metadata, `prepare` starts a new
experiment from the bundled patch and pinned build image.

Do not copy a live SQL main file directly; `fork` takes an online backup. The seed
can remain shared by absolute path on the same machine.

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
chia up "$CLUSTER" -y
JOB="$RUN-$(date +%s)"
chia job submit --chia-cluster "$CLUSTER" --submission-id "$JOB" --no-wait -- \
  python -u "$PWD/loop/loop.py" resume --run "$RUN"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
```

`prepare` regenerates absolute mounts for the new clone. Do not reuse the
source's rendered cluster YAML. Fork copies frozen benchmark ELFs and the
selected native checkpoint plus filtered SQL; expensive cache entries, raw logs,
and transient workspaces stay in the source experiment. They are not needed for
search recovery, but keep the source experiment if you need its raw evidence.

For a restore on another machine, transfer a completed fork directory and its
**entire** seed directory (`manifest.json` and `tree/`, preserving symlinks/modes),
bootstrap the matching host Python dependencies, and update only the copied
run's `run.json` `seed` path to the transferred seed. Regenerate the deployment
with `prepare`. There is no portable export/import or checkpoint migration
command; [INTEGRATION.md](INTEGRATION.md#restore-contract-and-compatibility)
details the required files and compatibility limits, including CPU-count changes.

## Logs and consolidated metrics

Set `RUN_DIR`, `CLUSTER`, and `JOB` to the experiment/submission you want to
inspect. CHIA exposes driver logs through its job commands while the head is
running; teardown saves them as `$RUN_DIR/$JOB.log`. Build/simulation logs appear
as tasks finish, not as live streams:

```sh
chia job list --chia-cluster "$CLUSTER"
chia job status --chia-cluster "$CLUSTER" "$JOB"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
ls "$RUN_DIR/artifacts"
CANDIDATE=candidate-xxxxxxxx
tail -n 80 "$RUN_DIR/artifacts/$CANDIDATE/build.log"
tail -n 80 "$RUN_DIR/artifacts/$CANDIDATE/wikisort.log"
tail -n 80 "$RUN_DIR/software-build.log"
chia status --chia-cluster "$CLUSTER"
# Task durations/cache callback profiling; emits a consolidated CSV table.
chia viz-profile "$RUN_DIR/artifacts/profile/ChiaProfileCollector.log" --format table
```

The configured Ray dashboard is at `http://127.0.0.1:8287` (branch example:
`8288`). Worker Ray logs are under `/tmp/ray/session_latest/logs` **inside each
container**; use `docker ps` to find its rendered name, then `docker exec` to
inspect those files. Head Ray logs are on the host under
`/tmp/ray/dma-$RUN/session_latest/logs`.
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
        FROM benchmarks WHERE candidate=? AND name!='error'
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

One simulator build is shared by 19 independent Embench ELF tasks. Both the seed
and candidates run DMA mode. Embench’s existing SCons build runs through one
Makefile target in the build container. The evaluator checks simulator assertions,
Embench verification, candidate output agreement, full coverage,
and DMA activity consistency. Fitness is a weighted
geometric mean of seed/candidate timed cycles on workloads with timed DMA
activity. Remaining workloads are correctness checks. Invalid candidates cannot
enter valid native archives. MAP-Elites uses source size and timed DMA bytes per
benchmark cycle.

Replay is an optional fresh evaluation of saved source, independent of restoring
search state. It makes no generation call and does not change the population:

```sh
# With this run's native cluster up, and no other writer for this same run:
JOB="$RUN-replay-$(date +%s)"
chia job submit --chia-cluster "$CLUSTER" --submission-id "$JOB" --no-wait -- \
  python -u "$PWD/loop/loop.py" replay --run "$RUN" --candidate "$CANDIDATE"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
```

A replay has a new `replay-*` SQL row and fresh cache tags. If the original driver
is live, fork first and replay in the child instead. One writer is allowed per
run. Completed evaluation records repair interruption before population admission.
Native checkpoints do not save RNG state: already selected work retains its
parent/island context, while future stochastic choices can differ. Runs require
the current matching checkpoint format; there are no old-format migrations.

```sh
python -m pip install pytest==9.1.1
python -m pytest -c loop/pytest.ini loop/tests -q
CHIA_LIVE_AGENTS=1 python -m pytest -c loop/pytest.ini loop/tests/test_native_cluster.py -q
```

See [INTEGRATION.md](INTEGRATION.md) for ownership, checkpoint contents, restore
requirements, and validation evidence.

## File roles

| File | Role |
| --- | --- |
| `config.yaml` | CHIA cluster resources, container names/images, startup, and tutorial search controls. |
| `backend.yaml` | Fixed implementation, deployment, and evaluation settings. |
| `base/chipyard.patch` | DMA/Embench changes applied to the pinned build image’s Chipyard source. |
| `base/prepare.sh` | Materialize that image-plus-patch source seed inside Docker. |
| `base/README.md` | Base source revisions and baseline update procedure. |
| `bootstrap.py` | Prepare pinned CHIA/evolve-flows sources and install the host head's Python packages. |
| `loop.py` | Experiment flow, seed evaluation, native controller setup, replay, and CLI commands. |
| `chia_dma/config.py` | Merge settings, resolve paths/CPU count, and select frozen settings. |
| `chia_dma/runtime.py` | Preserve inputs, render deployment, attach CHIA cache/profiler. |
| `chia_dma/agent.py` | Initialize the selected parent, invoke a native CLI agent with BashTool, and save its proposal. |
| `chia_dma/evaluation.py` | CHIA worker tasks and the build → benchmark → verify → score pipeline. |
| `chia_dma/experiment.py` | Preserved source, SQL proposals/metrics, native population hooks, checkpoints, forks, and the writer lock. |
| `chia_dma/__init__.py` | Python package marker. |
| `tests/test_contract.py` | Source, provider, parser, and fitness contracts. |
| `tests/test_controller_integration.py` | Controller concurrency, cache/profiler, and recovery integration. |
| `tests/test_native_cluster.py` | Real native worker placement, mount boundaries, staging, and optional provider checks. |
| `tests/native_fixture.py` | Temporary localhost SSH fixture for native cluster tests. |
| `tests/test_preservation.py` | Immutable image-plus-patch seed reuse and candidate restoration. |
| `tests/conftest.py` | Load test source inputs from the bundled patch. |
| `tests/fixtures/rtl-measurements.json` | Recorded Embench inputs for aggregation tests. |
| `tests/fixtures/embench-observation.txt` | Recorded hardware plus synthetic software counters for parser integration. |
| `pytest.ini` | Test import paths. |
| `README.md` | Tutorial procedure and file map. |
| `INTEGRATION.md` | Infrastructure choices and validation evidence. |
