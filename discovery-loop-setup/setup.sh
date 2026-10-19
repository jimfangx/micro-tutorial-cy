#!/usr/bin/env bash
set -euo pipefail

# Keep downloads and the installation beside this script, regardless of cwd.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

for command in curl tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

curl -fL -o loop-tutorial-4pct.tar.gz \
    https://pub-e8de871931e7477ba67db373fb7f148b.r2.dev/loop-tutorial-4pct.tar.gz
mkdir -p discovery-loop
tar -xzf loop-tutorial-4pct.tar.gz -C discovery-loop

# Download the metric visualization and SVG generation code.
curl -fL -o benchmark-trends.zip \
    https://pub-e8de871931e7477ba67db373fb7f148b.r2.dev/benchmark-trends.zip

cd discovery-loop
uv venv --python 3.10.19 --managed-python --seed .venv
source .venv/bin/activate
python -m zipfile -e ../benchmark-trends.zip .
python -m pip install pyyaml==6.0.3
python bootstrap.py
