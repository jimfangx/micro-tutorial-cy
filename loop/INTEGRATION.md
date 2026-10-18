# Integration decisions and validation

The tutorial pins CHIA `a4cedf24`, evolve-flows `62d27130`, and its SkyDiscover
submodule `78833ea6`. The inspected upstream
[memcpy helpers](https://github.com/ucb-bar/chia/blob/a4cedf24b131c459d7f176453d8e0cea97dda796/examples/memcpy/helpers.py)
reset a worker checkout and collect diffs; this tutorial instead preserves one
complete source seed. The
[gem5 alignment example](https://github.com/ucb-bar/chia/tree/a4cedf24b131c459d7f176453d8e0cea97dda796/examples/gem5_align)
provides SQLite and source-state examples, while CHIA cache/bypass supplies task
output reuse. Neither replaces evolutionary checkpoints or pending proposals.

| Responsibility | Reused API |
| --- | --- |
| Deployment | Native cluster YAML, `chia up`, `chia down` |
| Tasks | `ChiaFunction`, `TrackedRef`, `chia_wait`, `get`, `chia_cancel` |
| RTL | `ChiselBuildNode`, `BuildArtifact`, `VerilatorRunNode`, `RunResult` |
| CLI agents/tools | `AntigravityLLM`, `ClaudeCodeLLM`, `CodexLLM`, native `BashTool` |
| Search | Full `DiscoveryController.run_discovery`, public `LLMModelConfig.init_client` |
| Population | `OpenEvolveNativeDatabase.sample/add/save/load` |
| Evaluation | Async `evaluate_program` returning native `EvaluationResult` |
| Cache | `start_cache`, callbacks on `get`, `Bypass.set_cond/set_provider` |
| History/profiling | `SQLiteNode`, `start_collector`, native profiler events |

## Controller integration

The small [controller probe](tests/test_controller_probe.py) demonstrates two
concurrent generations with different parents/islands and an invalid result.
The [integration tests](tests/test_controller_integration.py) then exercise the
production adapter, evaluator, and native checkpoints with real CHIA tasks,
cache callbacks, profiling, and inexpensive benchmark fixtures. These checks
preceded removal of the custom Driver.

[controller.py](chia_dma/controller.py) constructs the native controller and
supplies its public LLM initializer, evaluator, and context builder. It uses the
same evaluator-component assignment that evolve-flows' bridge uses. There are
no copied controller methods, private monkeypatches, custom iteration scheduler,
or alternate controller implementation. The database subclass only records and
recovers selections, rejects invalid admissions, and supplies their target island.

At the pinned revision, `EvolverNode.run_search` accepts YAML configuration and
an evaluator, but does not expose the callable LLM initializer, context builder,
existing population, or proposal-aware checkpoint callback needed here. Direct
`DiscoveryController` composition keeps those public hooks without copying the
EvolverNode bridge or adding a local API proxy for CLI agents.

CHIA cache-enabled calls return callback handles. The evaluator retains those
handles for `get`, while passing raw refs to `chia_wait`/retry/cancel. After a
build, all benchmark tasks are submitted together with one shared object-store
build artifact. DMA-specific coverage, observation, output, and fitness checks
remain in the evaluator/parser; the controller processes their results.

## Native deployment and enforcement

`config.yaml` contains native main-cluster and per-proposal generation-cluster
YAML templates. Python only expands paths/identifiers and invokes the native
CLI. Generation uses a container-only native cluster: the main cluster schedules
its proxy task, and a private Ray instance inside the container hosts BashTool.
This keeps the agent off the evaluator's trusted Ray domain. Dedicated generation
containers use native `scoped_teardown: false`, so recovery removes a worker
even while its private Ray/BashTool is still running. The empty head-start
guard below prevents that teardown from stopping the host cluster.

[chia-native.patch](patches/chia-native.patch), applied by bootstrap only inside
`loop/.deps/chia`, contains two small compatibility fixes for the pinned CHIA:

- Honor an explicit YAML Docker network instead of also forcing `--net=host`.
- Skip stopping a host Ray head when a container-only cluster has no head-start
  commands. Teardown hooks still run; native worker teardown removes containers.

The original external CHIA checkout is untouched. These are source dependency
patches, not runtime monkeypatches or replacement deployment implementations.

Each generation has a separate Docker network, read-only root/source/toolchain,
two writable source-file binds initialized from its selected parent, and private
temporary home/credentials. Native YAML setup/teardown commands install/remove
interface-specific host and private-network rejection rules. Ray binds wildcard
sockets even when given localhost, so bridge networking alone is insufficient.
The test starts a reachable host listener and verifies denial through both the
Docker gateway and host IP, alongside forbidden file writes. Another check tears down an active private
BashTool/Ray worker and verifies its container/network are removed while the
trusted build cluster keeps working. Concurrent
workspace tests verify the frozen tree and the other parent's files stay intact.

Build workers copy the fixed seed once into a private writable checkout; builds
overwrite the two implementation files. The original checkout is not mounted.
The retained seed includes dependency/classpath caches, so restoration does not
refer back to mutable caches in the original source tree. Git operations on the
input checkout are read-only provenance queries.

## Checkpoint and recovery boundaries

Native checkpoint files include program solutions/metrics/lineage, the best
program and iteration, plus `openevolve_native_metadata.json`: islands, archive,
feature maps/statistics, island bests/generations, current island, and last
migration generation. Native save/load remains authoritative for all of these.
The native format has no in-flight CLI proposals or RNG-state snapshot.

The additional records are deliberately small:

- `candidates/<id>/request.json`: original parent, inspirations, island, sequence,
  and backend selection, persisted before generation.
- `proposal.json`, two complete Scala files, and a provenance diff: committed
  before returning generation, then made read-only.
- `state.json` beside each native checkpoint: frozen-config hash, admitted IDs,
  and generated/evaluated flags for requests known at that checkpoint.
- `metrics.db`: numerical results and lineage through CHIA SQLite, including
  terminal invalid results and evaluation-complete/admission-pending results.

A checkpoint writes native population and the small envelope to a new directory,
then atomically updates `latest.json`. Completed proposals are independent of
cache eviction and checkpoint timing. Resume repairs admission from SQLite
without rebuilding completed evaluations; unfinished expensive work uses CHIA
cache/bypass. New stochastic selections can differ after restart, while pending
requests retain their exact original context.

Fork uses the selected native checkpoint, copies only proposals present then,
and filters a SQLite backup to evaluations completed then. A request unfinished
at that checkpoint remains an unfinished request even if its original run later
completed it. Replay uses the fixed seed plus saved complete files with fresh
build/simulation tags and no population admission. Existing experiments are not
rewritten. Format 3 deliberately rejects older formats; use their matching
previous loop implementation to resume them.

## Validation evidence

The standard suite passed **17 tests**, with the three provider calls opt-in.
All **three live backend checks passed** separately. A fresh self-contained seed
also built **43 ELF inputs**, the SoC simulator, and passing controller protocol
tests. No full RTL simulation/performance rerun was started for this refactor.

Evidence: [standard suite](experiments/refactor-checks/suite-final-pass.log),
[live backends](experiments/refactor-checks/live-backends-final-pass.log),
[interrupted native worker cleanup](experiments/refactor-checks/native-recovery-final.log),
and [fresh build proof](experiments/refactor-checks/ssh-d8ffe8fec543478d842a7a95f2c3799b/seed-build/proof.json).
All validation containers, networks, and their firewall rules were removed.

Fresh checks cover concurrent parent/island context, attempted forbidden edits,
invalid archive rejection, benchmark fan-out, warm/cold/evicted cache behavior,
restart after generation, evaluation-before-admission recovery, resume after
admission, native migration, historical fork/replay, cancellation, and backend
switching. `experiments/refactor-checks/` retains logs and native deployment
fixtures. The controller/cache tests use cheap CHIA fixture tasks and recorded
RTL observation data; they do not simulate RTL or establish fresh performance.

Native `chia up/down` checks use real separate build/simulation containers and
concurrent generation containers. Opt-in live CLI checks require a backend to
compute a hash available only inside its workspace through BashTool; Claude's
built-in tools are disabled for this check and normal generation. Provider
credentials must be valid when running these checks.

Earlier full live validation, before this controller/deployment replacement,
passed 43 seed workloads, 24 candidate workloads, and 24 independent replay
workloads, including full 2 MiB correctness. Candidate and replay fitness were
1.0; an invalid generated design was rejected by controller tests. Evidence in
`experiments/live-validation`, `replay-validation`, and `invalid-validation` is
preserved. A later pre-replacement check passed software/SoC builds, protocol
tests, and command-smoke, then stopped the long simulations at the user's request.
Those results are historical evidence, distinct from this refactor's checks.
