#!/usr/bin/env bash
# One independent init-0 case, a fresh dedicated policy server and the installed ego profile.
set -euo pipefail
if [[ $# != 2 || ! "$1" =~ ^(N1|N2|C1|F1)$ || ! "$2" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "Usage: bash scripts/run_libero_step7.sh N1|N2|C1|F1 unique-attempt-name" >&2
  exit 2
fi
case_name=$1
attempt=$2
cd "$(dirname "$0")/.."
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1
test ! -e "var/step7/$attempt"
test -f var/step7/policy.toml
test -d var/step7/dsh/robot-1
port=$(.venv/bin/python -c 'import tomllib; print(tomllib.load(open("var/step7/policy.toml", "rb"))["policy"]["port"])')
# Refuse to replace an unrelated process; the shared showcase uses a different port.
.venv/bin/python - "$port" <<'PY'
from pathlib import Path
import os,signal,sys,time
p=Path("var/step7/policy-active.pid")
if p.exists():
    pid=int(p.read_text())
    try: cmd=Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0",b" ").decode()
    except FileNotFoundError: cmd=""
    if cmd:
        assert "/pi05-step1/serve.py " in cmd and f"--port {sys.argv[1]} " in cmd, cmd
        os.kill(pid,signal.SIGTERM)
        for _ in range(100):
            if not Path(f"/proc/{pid}").exists(): break
            time.sleep(.05)
PY
bash /home/huawei/hqk/workspace/pi05-step1/server.sh --port "$port" >"var/step7/policy-$attempt.log" 2>&1 &
policy_pid=$!
echo "$policy_pid" >var/step7/policy-active.pid
echo "$policy_pid" >"var/step7/policy-$attempt.pid"
trap 'kill "$policy_pid" 2>/dev/null || true; wait "$policy_pid" 2>/dev/null || true' EXIT
for i in $(seq 1 90); do
  if .venv/bin/python - "$port" <<'PY' 2>/dev/null
import socket,sys
with socket.create_connection(("127.0.0.1",int(sys.argv[1])),.5): pass
PY
  then break; fi
  kill -0 "$policy_pid"
  sleep 1
done
bash /home/huawei/hqk/workspace/pi05-step1/client.sh "$PWD/scripts/probe_pi05_step6.py" \
  /home/huawei/hqk/workspace/pi05-step1/runs/20261110T021656Z/target-qualification/policy/000.npz \
  "$PWD/var/step7/$attempt-preflight.json" --port "$port"
.venv/bin/python -u scripts/verify_libero_step7.py var/step4/wsim-root "var/step7/$attempt" \
  --policy-config var/step7/policy.toml --mode chat --case "$case_name" \
  --harness-home var/step7/dsh/robot-1 --policy-pid "$policy_pid"
