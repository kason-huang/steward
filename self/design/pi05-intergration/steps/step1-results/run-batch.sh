#!/usr/bin/env bash
set -euo pipefail
data="$(cd "$(dirname "$0")" && pwd)"
cd "$data"
sha256sum -c evidence/frozen-batch.sha256
run="$data/runs/${1:?run identifier required}"
mkdir -p "$data/runs"
mkdir "$run"
cp evidence/frozen-batch.json "$run/"
python3 - "$run" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

run = Path(sys.argv[1])
root = Path.cwd()
plan = json.loads((run / "frozen-batch.json").read_text())
for index, init in enumerate(plan["init_indices"]):
    name = f"attempt-{index:02d}-init-{init:03d}"
    command = ["bash", "client.sh", str(root / "evaluate.py"), "--task", plan["task"],
               "--task-order-index", str(plan["task_order_index"]), "--init-index", str(init),
               "--seed", str(plan["seed"]), "--timeout", str(plan["wall_timeout_seconds_per_episode"]),
               "--attempt", name, "--output", str(run)]
    with (run / (name + ".log")).open("w") as log:
        print(command, file=log, flush=True)
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    result_path = run / name / "result.json"
    if not result_path.exists():
        result_path.parent.mkdir(exist_ok=True)
        result_path.write_text(json.dumps({
            "attempt": name, "init_index": init, "outcome": "exception",
            "termination": "runner_exit_before_result", "exit_code": completed.returncode,
            "human_intervention": False, "success_signal": False,
        }, indent=2) + "\n")
    print(result_path.read_text(), flush=True)
results = [json.loads(path.read_text()) for path in sorted(run.glob("attempt-*/result.json"))]
successes = sum(r["outcome"] == "success" and r["success_signal"] and not r["human_intervention"] for r in results)
summary = {"successes": successes, "total": len(results), "success_rate": successes / len(results), "attempts": results}
assert len(results) == plan["num_episodes"]
(run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(f"{successes}/{len(results)} successful", flush=True)
PY
