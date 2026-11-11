"""Replay an actual Step 6 action log and compare all world-side physics samples."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path

import numpy as np

from rollout_libero_step6 import PhysicalEvidence
from steward.world.panda_control import LIBERO_TASK_POLICY
from steward.world.server import StewardHost
from verify_libero_step3 import PANDA_ID, Robot, call, credentials
from verify_libero_step6 import SCENARIO, capture, save, submit


def replay(host, args):
    source_summary = json.loads((args.source / "summary.json").read_text())
    actions = [json.loads(line) for line in (args.source / "actions.jsonl").read_text().splitlines()]
    robot = Robot(host.robot_servers[PANDA_ID].url)
    robot.advance(0)
    lease = credentials(call(robot.url, "/control/acquire", credentials(robot.status())
                             | {"ttlS": 30, "policy": LIBERO_TASK_POLICY}))
    errors = {key: 0.0 for key in ("can", "tool", "basket", "can_velocity", "fingers")}
    count = 0
    with gzip.open(args.source / "wsim-physics.jsonl.gz", "rt") as reference:
        with gzip.open(args.out / "wsim-physics.jsonl.gz", "wt") as trace:
            evidence = PhysicalEvidence(host.sim, trace)

            def measure():
                nonlocal count
                evidence.capture()
                expected = json.loads(next(reference))
                actual = evidence.row
                assert actual["step"] == expected["step"]
                assert actual["inside"] == expected["inside"]
                for key in errors:
                    errors[key] = max(errors[key], float(np.max(np.abs(np.subtract(actual[key], expected[key])))))
                count += 1

            unsubscribe = host.sim.subscribe_steps(measure)
            try:
                for row in actions:
                    if row["sequence"] % 5 == 0:
                        call(robot.url, "/control/renew", lease | {"ttlS": 30})
                        frame = capture(robot)
                    submit(robot, lease, row["sequence"], row["applied_action"], frame["observationId"])
                    robot.advance(row["receipt"]["endStep"])
                    receipt = robot.status()["lastCompleted"]
                    assert receipt == row["receipt"]
                before_stop = evidence.summary()
                robot.stop(lease)
                robot.advance(source_summary["stop_status"]["step"])
                after_stop = evidence.summary()
                assert reference.readline() == ""
            finally:
                unsubscribe()
                if robot.status()["leased"]:
                    robot.stop(lease)
    stopped = robot.status()
    passed = (before_stop["success"] and after_stop["success"] and max(errors.values()) <= 1e-9
              and stopped["stopState"] == "stopped" and not stopped["pending"])
    result = {"pass": passed, "actions": len(actions), "physical_samples": count,
              "maximum_absolute_error": errors, "before_stop": before_stop,
              "after_stop": after_stop, "stop": stopped}
    save(args.out / "replay.json", result)
    print(json.dumps({key: result[key] for key in ("pass", "actions", "physical_samples", "maximum_absolute_error")}))
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    host = StewardHost(args.root, scenario=SCENARIO, freeze_idle=True, dispatch_task=False,
                       speed=float("inf")).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(replay, host, args)
            while not work.done():
                host.pump(timeout=.002)
            return 0 if work.result() else 1
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
