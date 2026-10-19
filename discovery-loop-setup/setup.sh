#!/usr/bin/env bash
set -euo pipefail

# Keep downloads and the installation beside this script, regardless of cwd.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

for command in wget tar unzip uv; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

wget -O loop-tutorial-4pct.tar.gz \
    https://pub-e8de871931e7477ba67db373fb7f148b.r2.dev/loop-tutorial-4pct.tar.gz
mkdir -p discovery-loop
tar -xzf loop-tutorial-4pct.tar.gz -C discovery-loop

# Install the metric visualization and SVG generation code in discovery-loop.
wget -O benchmark-trends.zip \
    https://pub-e8de871931e7477ba67db373fb7f148b.r2.dev/benchmark-trends.zip
unzip -o benchmark-trends.zip -d discovery-loop

cd discovery-loop
uv venv --python 3.10.19 --managed-python --seed .venv
source .venv/bin/activate
python -m pip install pyyaml==6.0.3
python bootstrap.py
