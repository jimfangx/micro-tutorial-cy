# First-cut verification and measurements

All required standalone tests and the CPU/DMA Embench suite completed in Verilator.
Rocket, caches, coherence/interconnect, DRAM implementations, and benchmark sources are unchanged.
The initial command-only bring-up was validated before adding the actual DMA datapath.

Environment: Chipyard base `157dd69e`, Rocket `ece7b9ad5`, Embench `09c2ed8`,
Chisel 6.7.0, Verilator 5.022, RISC-V GCC 16.1.0, SCons 4.11.1.

Standalone verification passed: zero/single/multiple units; cache-line/page boundaries;
64 KiB and 2 MiB transfers exceeding L1/L2; dirty sources and cached destinations;
repeated alternating CPU/DMA accesses; source/guard integrity; invalid alignment, length,
address, overflow, and overlap rejection. The same suite passed with random TileLink delays.
Controller RTL tests additionally covered held requests/responses, delayed read/write replies,
busy commands, reset, unsupported commands, failed reads, and a failed write after earlier progress.
Memory-failure handling was tested by controller-level fault injection. The adapter maps TileLink
denied/corrupt bits to that error input; the full-system DRAM model does not naturally generate
those failures. No FPGA or host-side DMA emulation was used.

The standalone shim test recorded 12 intercepted calls, 5 actual DMA copies, 7 CPU fallbacks,
and 12,936 DMA bytes. The CPU-only shim also passed; `memmove` remained on the CPU.

## Copy microbenchmark

CPU uses the ordinary toolchain memcpy. DMA API cycles include validation, fences, commands,
waiting, and statistics; operation cycles omit API validation/statistics; setup cycles cover
the initial fence and CONFIG response. Reads of the cycle counter add fixed measurement cost.
Samples follow deterministic initialization and verification; cache state evolves between samples.
Threshold remains 64 bytes. No hardware or threshold tuning was performed.

| Bytes | CPU cycles | DMA API cycles | DMA/CPU | Operation cycles | Setup cycles |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 151 | 315 | 2.086 | 51 | 9 |
| 24 | 217 | 137 | 0.631 | 81 | 9 |
| 64 | 534 | 207 | 0.388 | 151 | 9 |
| 256 | 2070 | 609 | 0.294 | 553 | 9 |
| 1024 | 8298 | 2208 | 0.266 | 2152 | 9 |
| 4096 | 33089 | 8616 | 0.260 | 8560 | 9 |
| 65536 | 583923 | 120198 | 0.206 | 120142 | 9 |

## Embench comparison

Both modes use identical GCC architecture/optimization flags, global scale 1, and warmup heat 1.
All runs use the same DMARocketConfig RTL. CPU mode has ordinary memcpy; DMA mode enables wrapping.
Main cycles cover initialization, warmup, timed benchmark, and verification; startup and reporting
are excluded. Benchmark cycles cover start_trigger through stop_trigger, with no printing inside.
Each CPU/DMA pair matched both the benchmark return value and the native verifier result.
Native verification is inherited unchanged; xgboost's scale-1 threshold is weak, so its raw
prediction-count return value is compared explicitly rather than relying only on process exit.
Ratios are DMA cycles / CPU cycles; values above 1 mean more cycles.

| Benchmark | CPU main | DMA main | Ratio | CPU timed | DMA timed | Timed ratio | DMA use |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| aha-mont64 | 2608852 | 2608851 | 1.000 | 2602235 | 2602232 | 1.000 | compatibility only |
| crc32 | 4730729 | 4730714 | 1.000 | 4700959 | 4700919 | 1.000 | compatibility only |
| depthconv | 4455709 | 4455664 | 1.000 | 4451609 | 4451607 | 1.000 | compatibility only |
| edn | 5488983 | 5485800 | 0.999 | 5399354 | 5399358 | 1.000 | compatibility only |
| huffbench | 3654953 | 3656614 | 1.000 | 3334317 | 3334607 | 1.000 | compatibility only |
| matmult-int | 6172333 | 4697298 | 0.761 | 5963048 | 4518712 | 0.758 | hardware DMA |
| md5sum | 3590337 | 3229075 | 0.899 | 3534693 | 3178801 | 0.899 | hardware DMA |
| nettle-aes | 4786578 | 4786404 | 1.000 | 4710248 | 4710199 | 1.000 | compatibility only |
| nettle-sha256 | 5184894 | 5234222 | 1.010 | 5169651 | 5218847 | 1.010 | compatibility only |
| nsichneu | 4146787 | 4146391 | 1.000 | 4130494 | 4130173 | 1.000 | compatibility only |
| picojpeg | 3993656 | 3994424 | 1.000 | 3320897 | 3321773 | 1.000 | compatibility only |
| qrduino | 4487247 | 4489043 | 1.000 | 3732511 | 3733828 | 1.000 | compatibility only |
| sglib-combined | 3640229 | 3571441 | 0.981 | 3513905 | 3446603 | 0.981 | hardware DMA |
| slre | 3343706 | 3342521 | 1.000 | 3311299 | 3310054 | 1.000 | compatibility only |
| statemate | 1828611 | 1828659 | 1.000 | 1825484 | 1825463 | 1.000 | compatibility only |
| tarfind | 3086402 | 3087276 | 1.000 | 3019386 | 3020391 | 1.000 | compatibility only |
| ud | 5412993 | 5413396 | 1.000 | 5406861 | 5406864 | 1.000 | compatibility only |
| wikisort | 3281860 | 2610856 | 0.796 | 2157835 | 1714895 | 0.795 | hardware DMA |
| xgboost | 9091551 | 9100414 | 1.001 | 4527400 | 4533704 | 1.001 | compatibility only |

## Binary memcpy inspection

External-reference flags come from the benchmark objects before linking. Call-site counts
come from the linked ELF disassembly and include libc sites, not just benchmark sites.

| Benchmark | Benchmark object references memcpy | CPU memcpy sites | DMA wrapper sites |
| --- | --- | ---: | ---: |
| aha-mont64 | no | 3 | 3 |
| crc32 | no | 3 | 3 |
| depthconv | no | 3 | 3 |
| edn | no | 3 | 3 |
| huffbench | yes | 4 | 4 |
| matmult-int | yes | 5 | 5 |
| md5sum | yes | 5 | 5 |
| nettle-aes | no | 3 | 3 |
| nettle-sha256 | yes | 10 | 10 |
| nsichneu | no | 3 | 3 |
| picojpeg | yes | 4 | 4 |
| qrduino | yes | 9 | 9 |
| sglib-combined | yes | 4 | 4 |
| slre | no | 3 | 3 |
| statemate | no | 3 | 3 |
| tarfind | no | 3 | 3 |
| ud | no | 3 | 3 |
| wikisort | yes | 10 | 10 |
| xgboost | no | 3 | 3 |

## DMA-enabled operation counts

Counts cover the entire main function, including warmup and verification. Timed-region DMA copies
are also listed. The report's own printf is outside both the counters snapshot and measured intervals.

| Benchmark | Intercepted | Offloaded | CPU fallback | Bytes | DMA op cycles | Setup cycles | Timed DMA copies |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| aha-mont64 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| crc32 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| depthconv | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| edn | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| huffbench | 12 | 0 | 12 | 0 | 0 | 0 | 0 |
| matmult-int | 80 | 80 | 0 | 256000 | 476158 | 720 | 78 |
| md5sum | 134 | 67 | 67 | 67000 | 139306 | 603 | 66 |
| nettle-aes | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| nettle-sha256 | 1689 | 0 | 1689 | 0 | 0 | 0 | 0 |
| nsichneu | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| picojpeg | 18 | 0 | 18 | 0 | 0 | 0 | 0 |
| qrduino | 72 | 0 | 72 | 0 | 0 | 0 | 0 |
| sglib-combined | 32 | 32 | 0 | 12800 | 25158 | 288 | 31 |
| slre | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| statemate | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| tarfind | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ud | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| wikisort | 456 | 276 | 180 | 124776 | 253757 | 2504 | 184 |
| xgboost | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Benchmarks with zero DMA copies validate software compatibility; their timing differences
do not demonstrate accelerator speedup or slowdown. Link wrapping includes eligible external libc
references as well as benchmark references; inlined copies and manual loops remain CPU operations.

Binary call-site inspection, source hashes, shared compilation flags, ELF hashes, raw simulation logs,
and per-run metrics are retained in build/embench-manifest.json, build/embench-results.json, and build/logs.
See README.md for reproducible commands and every change outside the accelerator source directory.
No required verification was skipped. Performance improvement is not a completion requirement.
