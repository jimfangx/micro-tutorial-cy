#!/usr/bin/env bash
set -euo pipefail

JOB="${1:?Usage: $0 JOB}"

# Use the installation created beside this script by setup.sh.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/discovery-loop"
source .venv/bin/activate

RUN=tutorial-4pct
RUN_DIR="$PWD/experiments/$RUN"
CLUSTER="$RUN_DIR/deployment/cluster.yaml"

chia job stop "$JOB" --chia-cluster "$CLUSTER"
chia job logs --chia-cluster "$CLUSTER" "$JOB" > "$RUN_DIR/$JOB.log"
chia down "$CLUSTER" -y
