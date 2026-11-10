#!/usr/bin/env bash
set -euo pipefail
data="$(cd "$(dirname "$0")" && pwd)"
cd "$data/../openpi-step1"
export LIBERO_CONFIG_PATH="$data/libero-config"
export MUJOCO_GL=egl
export MUJOCO_EGL_DEVICE_ID=0
export PYOPENGL_PLATFORM=egl
export PYTHONPATH="$PWD/examples/libero:$PWD/packages/openpi-client/src:$PWD/third_party/libero"
if [[ -n "${PI05_CLIENT_PYTHON:-}" ]]; then
    python="$PI05_CLIENT_PYTHON"
elif [[ -s "$data/evidence/client-mirror-freeze.txt" ]]; then
    python="$data/client-mirror-venv/bin/python"
else
    python="$data/client-venv/bin/python"
fi
exec "$python"  -u "$@"
