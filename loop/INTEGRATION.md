# Inspected APIs and integration decisions

Inspected local sources, rather than assuming CLI agents work through an
OpenAI-compatible API:

| Existing component | Reused API / finding |
| --- | --- |
| CHIA `a4cedf24` (local checkout) | `AntigravityLLM.prompt(message, tools)`, `ClaudeCodeLLM.prompt`, `CodexLLM.prompt`; local methods run inside the per-proposal sandbox. |
| CHIA container lifecycle | `chia.cluster.docker.DockerManager.setup_container/exec_command/stop_container`; its SSH-shaped transport is adapted to local subprocesses. Task containers use bridge networking and restricted mounts. |
| CHIA scheduling | `ChiaFunction` resource labels independently bound generation, build, and simulation. |
| CHIA RTL nodes | `ChiselBuildNode.build()` returns a serializable `BuildArtifact`; `VerilatorRunNode.run()` stages one simulator and ELF in a private task directory and captures real RTL output. |
| CHIA database | `SQLiteNode.init_schema/transaction/execute/wal_checkpoint`; FULL synchronous writes and checkpoint database copies. |
| CHIA diagnostics/cache | `get_profiler().add_info/log_event`, `start_collector`, `start_cache`, `Bypass.set_provider/set_cond`, stable `_chia_tag`; `chia.base.ChiaFunction.get()` unwraps profiler envelopes. |
| evolve-flows `62d27130` | `EvolverNode.run_search` and `run_skydiscover` inject `ChiaEvaluator`, but construct the default API `LLMPool` controller and do not expose proposal-stage CLI checkpoint hooks. |
| SkyDiscover `78833ea6` | `OpenEvolveNativeDatabase.sample/add/save/load` supplies islands, MAP-Elites, archive/best tracking, inspirations, population limits and migration. `add(target_island=...)` fixes the destination when asynchronous results arrive out of order. |
| SkyDiscover evaluator | `ChiaEvaluator` builds once, dispatches all workloads before collecting results, then invokes the mapper. One evaluator instance per candidate binds its private build artifact; no shared build/run actor state. |

Therefore `search.py` composes the existing native database with CHIA's three
CLI agents and evaluator. It changes no CHIA, SkyDiscover, or OpenEvolve internals.
The upstream controller is not reused because that would require replacing its
API LLM pool and iteration internals to persist isolated CLI proposals. Native
population serialization remains authoritative; the checkpoint envelope adds
RNG, pending stages, and the SQLite snapshot. Invalid candidates are persisted
as diagnostics and are never passed to native `add()`, which otherwise does not
itself filter invalid candidates from unoccupied feature cells.

Upstream references:
[CHIA memcpy](https://docs.chialoops.ai/en/main/user_guides/memcpy_example.html),
[architectural discovery](https://docs.chialoops.ai/en/main/case-studies/architectural-discovery-basic.html),
[evolve-flows](https://github.com/ucb-bar/evolve-flows),
[SkyDiscover](https://github.com/ucb-bar/skydiscover).

## Validation evidence

- All 13 contract/container tests passed (`experiments/tests.log`), including
  native island migration, MAP-Elites persistence, invalid-result exclusion,
  historical forks, backend switching, cache callbacks, and Docker permissions.
- All three CHIA CLI backends authenticated inside real task containers and
  produced allowlisted edits. Two Antigravity proposals completed concurrently
  in `experiments/generation-fast`. Codex's companion code-mode host is mounted
  alongside its executable. Partial provider timeouts are rejected rather than
  being mistaken for completed proposals; the default generation budget is ten
  minutes.
- The existing software flow built and froze 43 ELF artifacts: 19 Embench
  programs per CPU/DMA mode and five standalone tests. No original software,
  benchmark, or accelerator source was modified by the loop.
- `experiments/live-validation` passed the fresh SoC build, DMAControllerSpec,
  all 43 seed workloads, passive metrics validation, and all 19 CPU/DMA output
  comparisons. This includes the full 2 MiB correctness/coherence test. The
  measured seed fitness is 1.0.
- Public historical fork/replay in `experiments/replay-validation` passed all
  24 workloads after a fresh independent build. Every DMA Embench cycle,
  activity/output record and microbenchmark sample matched the baseline;
  replay fitness is exactly 1.0 (`replay-proof.json`). No LLM call generated
  the replayed RTL, and the original checkpoint remained unchanged.
- `experiments/invalid-validation` forked the complete two-island population,
  rejected a real integration-violating source through the CHIA evaluator,
  preserved both elite maps/global best, and passed public CLI resume. Its
  SQLite database and writable namespace are independent of the original.
- The main cluster was stopped and restarted from `00000010-retry` with Codex
  selected. The completed Antigravity proposal's source/metadata hashes were
  preserved; only the unfinished generation was retried. The Antigravity design
  subsequently failed the frozen controller test and was retained with validity
  0. Codex's proposal passed compilation/controller tests, all 19 DMA Embench
  programs, the microbenchmarks, and all standalone tests, including full 2 MiB
  correctness. It was admitted with fitness 1.0. Both islands have two elite
  cells, and native migration occurred.
- A final public CLI resume with default Antigravity selected authenticated and
  preserved the complete native population, tasks, RNG state, iteration count,
  source hashes, and proposal metadata. All 43 seed workloads, 24 candidate
  workloads and 24 replay workloads passed. The invalid proposals remain
  diagnostic records outside the population. The original accelerator sources
  still match the seed (`live-validation/final-acceptance.json`).

The full correctness test took about 71 minutes on this host. Simulation wall
limits are three hours and CHIA collection permits four hours. Correctness and
performance workloads fan out together; every correctness gate must pass before
admission. The central configuration allows up to 24 simulator tasks. During the
independent replay/recovery test, the host budget was split between clusters.

Each experiment archives its evaluator code separately from the source seed.
The existing DMA parser is loaded from that seed. Resume, replay and fork use
the archived evaluator, so edits in another thread cannot change in-flight
measurement or search rules.
