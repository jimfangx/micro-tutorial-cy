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

- Ten contract/container tests passed (`experiments/tests.log`).
- Actual Antigravity worker authentication passed (`experiments/doctor.log`).
- The unchanged software flow built/froze 19 full-suite Embench binaries per
  CPU/DMA mode plus five standalone test binaries (43 ELF artifacts), with source
  hashes checked before and after compilation (`experiments/validation/software`).
- Fresh RTL build and full-suite runtime validation are being run under
  `experiments/validation3`; completion is not yet claimed.
