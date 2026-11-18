#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
read -r run pid < active-run.txt
while [[ ! -f "$run/finished.txt" ]]; do
    if ! ps -p "$pid" -o stat= | grep -qv '^Z'; then
        echo "Benchmark exited before completing all suites. See $run and /tmp/pi05-full-$run.log."
        exit 1
    fi
    sleep 30
done
python3 summarize.py "$run"
