#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
read -r id benchmark_pid <active-run.txt
out="$PWD/$id/upstream-probe"
data="$HOME/hqk/workspace/pi05-step1"
videos="$data/full-benchmark-videos/$id/upstream-probe"
port=18016
mkdir "$out"
if [[ -n "$(ss -H -ltn "( sport = :$port )")" ]]; then
    echo "Probe port is in use"
    exit 1
fi
bash "$data/server.sh" --port "$port" >"$out/policy.log" 2>&1 &
server=$!
trap 'kill -TERM "$server" 2>/dev/null || true; wait "$server" 2>/dev/null || true' EXIT
ready=0
for ((i=0; i<60; i++)); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$server" || exit 1
    sleep 3
done
[[ "$ready" -eq 1 ]]
printf '%s\n' "$videos" >"$out/video-root.txt"
timeout -k 15s 5m bash "$data/client.sh" examples/libero/main.py --args.host 127.0.0.1 --args.port "$port" \
    --args.task-suite-name libero_spatial --args.num-trials-per-task 1 --args.seed 7 \
    --args.video-out-path "$videos" >"$out/client.log" 2>&1
