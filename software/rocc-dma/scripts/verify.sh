#!/usr/bin/env bash
# Run after sourcing env.sh at the Chipyard root.
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
work="$root/software/rocc-dma"
mkdir -p "$work/build/logs"
jobs=${JOBS:-8}
make -C "$work" baseline -j"$jobs" > "$work/build/logs/baseline-run.log" 2>&1
rg -q 'Hello world from core 0, a rocket' "$work/build/logs/baseline-run.log"
echo 'Rocket baseline PASS'
cd "$root"
java -Xmx8G -jar scripts/sbt-launch.jar \
    -Dsbt.ivy.home="$root/.ivy2" -Dsbt.global.base="$root/.sbt" \
    -Dsbt.boot.directory="$root/.sbt/boot" -Dsbt.supershell=false \
    'chipyard/testOnly chipyard.dma.DMAControllerSpec' > "$work/build/logs/protocol.log" 2>&1
rg -q 'All tests passed' "$work/build/logs/protocol.log"
echo 'Controller protocol PASS'
make -C "$work" command-smoke -j"$jobs" > "$work/build/logs/command-smoke.log" 2>&1
rg -q 'DMA command smoke PASS' "$work/build/logs/command-smoke.log"
make -C "$work" correctness -j"$jobs" > "$work/build/logs/correctness.log" 2>&1
rg -q 'DMA correctness/coherence PASS' "$work/build/logs/correctness.log"
echo 'Coherent DMA correctness PASS'
make -C "$root/sims/verilator" CONFIG=DMAStallRocketConfig -j"$jobs" > "$work/build/logs/stall-build.log" 2>&1
make -C "$root/sims/verilator" CONFIG=DMAStallRocketConfig run-binary-fast \
    BINARY="$work/build/correctness.riscv" LOADMEM=1 TIMEOUT_CYCLES=200000000 \
    EXTRA_SIM_FLAGS=+cycle-count > "$work/build/logs/stall-correctness.log" 2>&1
rg -q 'DMA correctness/coherence PASS' "$work/build/logs/stall-correctness.log"
echo 'Coherent DMA with random TileLink delays PASS'
make -C "$work" libraries software-tests -j"$jobs" > "$work/build/logs/software-tests.log" 2>&1
rg -q 'DMA wrapper PASS.*offloaded=5' "$work/build/logs/software-tests.log"
rg -q 'DMA wrapper PASS.*offloaded=0' "$work/build/logs/software-tests.log"
rg -q 'DMA microbenchmark PASS' "$work/build/logs/software-tests.log"
echo 'Library, wrapper, and microbenchmark PASS'
python "$work/scripts/embench.py" build
python "$work/scripts/embench.py" run --bench wikisort
python "$work/scripts/embench.py" run
python "$work/scripts/report.py"
echo 'All required verification PASS'
