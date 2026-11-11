#!/usr/bin/env bash
set -euo pipefail
data="$(cd "$(dirname "$0")" && pwd)"
cd "$data/../openpi-step1"
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export JAX_PLATFORMS=cuda
export OPENPI_DATA_HOME="$data/openpi-cache"
export PYTHONPATH="$PWD/src"
if [[ -n "${PI05_SERVER_PYTHON:-}" ]]; then
    python="$PI05_SERVER_PYTHON"
elif [[ -s "$data/evidence/server-mirror-freeze.txt" ]]; then
    python="$data/server-mirror-venv/bin/python"
else
    python="$PWD/.venv/bin/python"
fi
printf 'Policy interpreter: %s\n' "$python"
exec "$python"  -u "$data/serve.py" "$data/checkpoints/pi05_libero" "$@"
