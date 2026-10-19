#!/usr/bin/env bash
set -euo pipefail

# Use the installation created beside this script by setup.sh.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/discovery-loop"
source .venv/bin/activate

RUN=tutorial-4pct
RUN_DIR="$PWD/experiments/$RUN"
CLUSTER="$RUN_DIR/deployment/cluster.yaml"

JOB="${1:-}"
if [[ -z "$JOB" ]]; then
    if [[ ! -s "$RUN_DIR/job-id" ]]; then
        printf 'No saved job ID. Run launch.sh first or pass a job ID: %s JOB\n' "$0" >&2
        exit 1
    fi
    JOB="$(<"$RUN_DIR/job-id")"
fi

chia job stop "$JOB" --chia-cluster "$CLUSTER"
chia job logs --chia-cluster "$CLUSTER" "$JOB" > "$RUN_DIR/$JOB.log"
chia down "$CLUSTER" -y
