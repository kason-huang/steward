#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export GIT_LFS_SKIP_SMUDGE=1
export UV_HTTP_TIMEOUT=180
export UV_CONCURRENT_DOWNLOADS=16
data="$(cd "$(dirname "$0")" && pwd)"
source_repo="$data/../openpi-step1"
mirror_repo="$data/mirror-project"
mkdir -p "$mirror_repo"
git -C "$source_repo" archive HEAD | tar -xf - -C "$mirror_repo"
cd "$mirror_repo"
sed -i -e 's#https://pypi.org/simple#https://mirrors.ustc.edu.cn/pypi/simple#g' \
       -e 's#https://files.pythonhosted.org/packages/#https://mirrors.ustc.edu.cn/pypi/packages/#g' uv.lock
cp uv.lock "$data/evidence/server-mirror.uv.lock"
sha256sum "$source_repo/uv.lock" "$data/evidence/server-mirror.uv.lock" > "$data/evidence/server-locks.sha256"
export UV_DEFAULT_INDEX=https://mirrors.ustc.edu.cn/pypi/simple
export UV_PROJECT_ENVIRONMENT="$data/server-mirror-venv"
uv sync --frozen --no-dev --no-install-project
PYTHONPATH="$source_repo/src" XLA_PYTHON_CLIENT_PREALLOCATE=false "$UV_PROJECT_ENVIRONMENT/bin/python" -c "import jax; devices=jax.devices(); print(devices); assert any(d.platform == 'gpu' for d in devices)"
uv pip freeze --python "$UV_PROJECT_ENVIRONMENT/bin/python" > "$data/evidence/server-mirror-freeze.txt"
