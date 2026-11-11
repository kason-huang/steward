#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$script_dir/../../../../../.." && pwd)
package="$repo/steward"
data="$repo/../pi05-step1"
port="${POLICY_PORT:-18066}"
out="${1:-$package/var/step6/repro-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$out"
out=$(cd "$out" && pwd)
ffmpeg=$(find "$data/server-mirror-venv" -type f -name 'ffmpeg-linux*' -print -quit)
test -x "$ffmpeg"
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1
cd "$package"
bash "$data/client.sh" "$package/scripts/probe_pi05_step6.py" \
  "$script_dir/policy-preflight.npz" "$out/preflight.json" --port "$port"
"$package/.venv/bin/python" scripts/rollout_libero_step6.py \
  var/step4/wsim-root "$out/rollout" --port "$port" --prefix-size 5 \
  --max-actions 280 --max-seconds 420 --ffmpeg "$ffmpeg"
"$package/.venv/bin/python" scripts/audit_libero_step6.py "$out/rollout"
"$package/.venv/bin/python" scripts/replay_libero_step6.py \
  var/step4/wsim-root "$out/rollout" "$out/replay"
printf '%s\n' "$out"
