#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
data="$HOME/hqk/workspace/pi05-step1"
upstream="$HOME/hqk/workspace/openpi-step1"
run_id="${1:?pass a unique run ID}"
out="$here/$run_id"
media="$data/full-benchmark-videos/$run_id"
mkdir "$out"
mkdir -p "$media"
[[ "$(git -C "$upstream" rev-parse HEAD)" == 215abfb217dbac7d5f1273282331b9b1866c0479 ]]
[[ "$(git -C "$upstream/third_party/libero" rev-parse HEAD)" == f78abd68ee283de9f9be3c8f7e2a9ad60246e95c ]]
[[ -z "$(git -C "$upstream" status --porcelain --untracked-files=no)" ]]
cp "$data"/evidence/{revisions.txt,checkpoint.sha256,tokenizer.sha256,server-mirror-freeze.txt,client-mirror-freeze.txt} "$out/"
cp "$upstream/examples/libero/main.py" "$out/upstream-main.py"
printf '%s\n' "$media" >"$out/video-root.txt"
nvidia-smi >"$out/nvidia-smi-start.txt"
date -u -Iseconds >"$out/started.txt"

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

for suite in libero_spatial libero_object libero_goal libero_10; do
    port=18016
    if [[ -n "$(ss -H -ltn "( sport = :$port )")" ]]; then
        echo "Port $port is already in use"
        exit 1
    fi
    printf '%s %s\n' "$(date -u -Iseconds)" "$suite"
    bash "$data/server.sh" --port "$port" >"$out/$suite.policy.log" 2>&1 &
    server_pid=$!
    ready=0
    for ((i=0; i<180; i++)); do
        if curl -fsS --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ready=1; break; fi
        kill -0 "$server_pid" || exit 1
        sleep 5
    done
    [[ "$ready" -eq 1 ]]
    export PI05_EPISODES_JSONL="$out/$suite.jsonl"
    timeout -k 30s 3h bash "$data/client.sh" "$here/record_official.py" --args.host 127.0.0.1 --args.port "$port" \
        --args.task-suite-name "$suite" --args.num-trials-per-task 50 --args.seed 7 \
        --args.video-out-path "$media/$suite" >"$out/$suite.client.log" 2>&1
    [[ "$(wc -l < "$PI05_EPISODES_JSONL")" -eq 500 ]]
    stop_server
    printf '%s\n' "$suite" >>"$out/completed-suites.txt"
done
date -u -Iseconds >"$out/finished.txt"
