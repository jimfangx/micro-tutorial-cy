#!/usr/bin/env bash
# Only Docker and a POSIX host shell are needed; all Python/tools run in containers.
set -euo pipefail
LOOP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(dirname -- "$LOOP")
RUN=${1:?Usage: loop/container.sh RUN bootstrap|prepare|run|resume|chia ...}
shift
case "$RUN" in ''|.|..|*[!a-zA-Z0-9_.-]*) echo 'Use a simple run name' >&2; exit 2;; esac
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then DOCKER=(sudo -n docker); fi
# A published CHIA image reads YAML so no host Python/YAML package is required.
READER=ghcr.io/ucb-bar/chia@sha256:8196c9c1f2790989f06383f2455c888cd7472bd6237cbb7256a080d4c5977b11
mapfile -t SETTINGS < <("${DOCKER[@]}" run --rm -i --mount "type=bind,src=$LOOP,dst=/loop,readonly" "$READER" python - "$RUN" <<'PY'
import json, pathlib, sys, yaml
from string import Template
cfg=yaml.safe_load(open('/loop/config.yaml'))['docker']
print(cfg['image'])
print(Template(cfg['container_name']).substitute(NAME=sys.argv[1]))
meta=pathlib.Path('/loop/experiments')/sys.argv[1]/'run.json'
print(json.loads(meta.read_text())['seed'] if meta.exists() else '')
PY
)
IMAGE=${SETTINGS[0]}
NAME=${SETTINGS[1]}
EXEC=(-i)
if [[ -t 0 ]]; then EXEC+=(-t); fi
if [[ ${1:-} == remove ]]; then exec "${DOCKER[@]}" rm -f "$NAME"; fi
if "${DOCKER[@]}" inspect "$NAME" >/dev/null 2>&1; then
    "${DOCKER[@]}" start "$NAME" >/dev/null
else
    MOUNTS=()
    if [[ -n ${SETTINGS[2]} ]]; then MOUNTS+=(--mount "type=bind,src=${SETTINGS[2]},dst=${SETTINGS[2]},readonly"); fi
    for path in "$HOME/.ssh" "$HOME/.gemini" "$HOME/.claude" "$HOME/.claude.json" "$HOME/.codex"; do
        [[ ! -e "$path" ]] || MOUNTS+=(--mount "type=bind,src=$path,dst=$path,readonly")
    done
    "${DOCKER[@]}" run -d --init --network host --shm-size=2g --name "$NAME" \
        --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \
        --env HOME="$HOME" --env USER="$USER" --env CHIA_CONTROLLER="$NAME" \
        --env "PYTHONPATH=/opt/dma-python:$LOOP:$LOOP/.deps/chia:$LOOP/.deps/evolve-flows:$LOOP/.deps/evolve-flows/skydiscover" \
        --mount "type=bind,src=$PROJECT,dst=$PROJECT" \
        --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
        --mount "type=volume,src=$NAME-python,dst=/opt/dma-python" \
        "${MOUNTS[@]}" --workdir "$PROJECT" "$IMAGE" sleep infinity >/dev/null
fi
if [[ ${1:-} == bootstrap ]]; then
    "${DOCKER[@]}" exec --user root "$NAME" bash -c \
        'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends docker.io openssh-server netcat-openbsd && mkdir -p /run/sshd "$HOME" && chown "$1:$2" /opt/dma-python "$HOME"' bash "$(id -u)" "$(id -g)"
    exec "${DOCKER[@]}" exec "${EXEC[@]}" "$NAME" python "$LOOP/bootstrap.py"
elif [[ ${1:-} == chia || ${1:-} == python || ${1:-} == bash ]]; then
    exec "${DOCKER[@]}" exec "${EXEC[@]}" "$NAME" "$@"
else
    exec "${DOCKER[@]}" exec "${EXEC[@]}" "$NAME" python -u "$LOOP/loop.py" "$@" --run "$RUN"
fi
