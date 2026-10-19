#!/usr/bin/env bash
set -euo pipefail

# Use the installation created beside this script by setup.sh.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/discovery-loop"
source .venv/bin/activate

RUN=tutorial-4pct
RUN_DIR="$PWD/experiments/$RUN"
CLUSTER="$RUN_DIR/deployment/cluster.yaml"
python loop.py status --run "$RUN"
python loop.py prepare --run "$RUN"
chia up "$CLUSTER" -y
JOB="$RUN-$(date +%s)"
chia job submit --chia-cluster "$CLUSTER" --submission-id "$JOB" --no-wait -- \
  python -u "$PWD/loop.py" resume --run "$RUN"
printf '%s\n' "$JOB" > "$RUN_DIR/job-id"
chia job logs --chia-cluster "$CLUSTER" "$JOB" --follow
