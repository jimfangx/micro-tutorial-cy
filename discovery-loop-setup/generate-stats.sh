#!/usr/bin/env bash
set -euo pipefail

# Use the installation created beside this script by setup.sh.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/discovery-loop"
source .venv/bin/activate

RUN=tutorial-4pct
RUN_DIR="$PWD/experiments/$RUN"

python benchmark-trends/generate.py --run "$RUN_DIR" --output "$PWD/benchmark-trends"
