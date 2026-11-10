#!/usr/bin/env bash
set -euo pipefail
data="$(cd "$(dirname "$0")" && pwd)"
cd "$data"

wait_for() {
    local job="$1" artifact="$2" pid
    while [[ ! -s "$artifact" ]]; do
        pid=$(cat "evidence/$job.pid")
        if ! ps -p "$pid" -o stat= | grep -qv '^Z'; then
            echo "$job exited before producing $artifact"
            exit 1
        fi
        sleep 10
    done
}

while [[ ! -s evidence/server-mirror-freeze.txt ]] &&
      ! grep -q "CudaDevice" evidence/setup-server.log; do
    live=0
    for job in setup-server setup-server-mirror; do
        if ps -p "$(cat "evidence/$job.pid")" -o stat= | grep -qv '^Z'; then
            live=1
        fi
    done
    if [[ "$live" -eq 0 ]]; then
        echo "Both strategy environment installations exited before passing GPU preflight"
        exit 1
    fi
    sleep 10
done
while [[ ! -s libero-config/config.yaml ]]; do
    live=0
    for job in setup-client setup-client-mirror; do
        if ps -p "$(cat "evidence/$job.pid")" -o stat= | grep -qv '^Z'; then
            live=1
        fi
    done
    if [[ "$live" -eq 0 ]]; then
        echo "Both simulator environment installations exited before producing the LIBERO config"
        exit 1
    fi
    sleep 10
done
wait_for download evidence/checkpoint.sha256
sha256sum -c evidence/frozen-batch.sha256
test "$(wc -l < evidence/checkpoint.sha256)" -eq 16
test -s evidence/tokenizer.sha256
run="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$data/runs/$run"
out="$data/runs/$run"

if [[ -s evidence/server-mirror-freeze.txt ]]; then
    export PI05_SERVER_PYTHON="$data/server-mirror-venv/bin/python"
    cp evidence/server-mirror-freeze.txt "$out/server-freeze.txt"
else
    export PI05_SERVER_PYTHON="$data/../openpi-step1/.venv/bin/python"
    cp evidence/server-freeze.txt "$out/server-freeze.txt"
fi
if [[ -s evidence/client-mirror-freeze.txt ]]; then
    export PI05_CLIENT_PYTHON="$data/client-mirror-venv/bin/python"
    cp evidence/client-mirror-freeze.txt "$out/client-freeze.txt"
else
    export PI05_CLIENT_PYTHON="$data/client-venv/bin/python"
    cp evidence/client-freeze.txt "$out/client-freeze.txt"
fi
printf "server=%s\nclient=%s\n" "$PI05_SERVER_PYTHON" "$PI05_CLIENT_PYTHON" >"$out/interpreters.txt"


start_server() {
    local name="$1"
    if curl -fsS --max-time 2 http://127.0.0.1:18005/healthz >/dev/null 2>&1; then
        echo "Port 18005 already has a healthy server"
        exit 1
    fi
    bash server.sh >"$out/$name.log" 2>&1 &
    server_pid=$!
    echo "$server_pid" >"$out/$name.pid"
    for i in $(seq 1 180); do
        if curl -fsS --max-time 2 http://127.0.0.1:18005/healthz >"$out/$name.health" 2>/dev/null; then
            return 0
        fi
        kill -0 "$server_pid" 2>/dev/null || return 1
        sleep 5
    done
    return 1
}

stop_server() {
    if [[ -n "${server_pid:-}" ]]; then
        kill -TERM "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
        server_pid=
    fi
}
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

bash client.sh "$data/preflight.py" "$out/preflight" >"$out/preflight.log" 2>&1
start_server smoke-policy
timeout -k 20s 40m bash client.sh examples/libero/main.py --args.host 127.0.0.1 --args.port 18005 \
    --args.task-suite-name libero_object --args.num-trials-per-task 1 --args.seed 7 \
    --args.video-out-path "$out/official-smoke" >"$out/official-smoke.log" 2>&1
bash client.sh "$data/evaluate.py" --task pick_up_the_alphabet_soup_and_place_it_in_the_basket \
    --task-order-index 0 --init-index 0 --seed 7 --attempt target-qualification --output "$out" \
    >"$out/target-qualification.log" 2>&1
python3 - "$out/target-qualification/result.json" <<'PY'
import json
import pathlib
import sys

result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result["outcome"] == "success" and result["success_signal"], result
PY
stop_server
start_server batch-policy
bash run-batch.sh "$run-batch" >"$out/batch.log" 2>&1
