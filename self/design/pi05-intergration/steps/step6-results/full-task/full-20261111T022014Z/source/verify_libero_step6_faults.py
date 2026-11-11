"""Exercise invalid output, late observations and cancellation on the task policy lease."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
from pathlib import Path
import time
from urllib.error import HTTPError

from steward.world.panda_control import LIBERO_TASK_POLICY, POLICY_OBSERVATION_WALL_S
from steward.world.server import StewardHost
from verify_libero_step3 import PANDA_ID, Robot, call, credentials
from verify_libero_step6 import (SCENARIO, assert_no_action, capture, fault_checks, policy_lease,
                                rejected, save, submit, infer, policy_preview)


def run(host, out):
    robot = Robot(host.robot_servers[PANDA_ID].url)
    robot.advance(0)

    def reset():
        previous = robot.status()["episodeId"]
        call(host.wca.url, "/wca/episode", {"action": "reset"})
        robot.advance(0)
        assert robot.status()["episodeId"] > previous

    action = [0.0] * 6 + [-1.0]
    fault_checks(robot, reset, action, out, profile=LIBERO_TASK_POLICY)
    checks = {}
    for name, invalid in (("nan", [float("nan")] + [0.0] * 6), ("wrong_dimension", [0.0] * 6),
                          ("out_of_range", [2.0] + [0.0] * 6),
                          ("out_of_workspace", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0])):
        lease = policy_lease(robot, LIBERO_TASK_POLICY)
        observed = capture(robot)
        if name == "out_of_workspace":
            # At the initial pose the ceiling is more than one action away.
            # Reach a valid high pose through the same leased HTTP control path.
            sequence = 0
            while robot.status()["sitePosition"][2] + .05 <= .43:
                assert sequence < 25
                observed = capture(robot)
                submit(robot, lease, sequence, [0, 0, .8, 0, 0, 0, -1], observed["observationId"])
                sequence += 1
                robot.advance(sequence * 25)
            observed = capture(robot)
        else:
            sequence = 0
        before = robot.status()
        try:
            submit(robot, lease, sequence, invalid, observed["observationId"])
        except HTTPError as exc:
            assert exc.code == 400
            error = json.loads(exc.read())["error"]
        else:
            raise AssertionError(f"Invalid {name} output was accepted")
        robot.advance(before["step"] + 25)
        after = robot.status()
        assert after["lastApplied"] == before["lastApplied"] and not after["pending"]
        checks[name] = {"error": error, "before": before, "after": after}
        robot.stop(lease)
        reset()
    lease = policy_lease(robot, LIBERO_TASK_POLICY)
    observed = capture(robot)
    # Represent an inference reply arriving after the observation deadline.
    time.sleep(POLICY_OBSERVATION_WALL_S + .1)
    error = rejected(lambda: submit(robot, lease, 0, action, observed["observationId"]), "observation expired")
    robot.advance(25)
    checks["late_inference_reply"] = {"rejection": error, **assert_no_action(robot)}
    robot.stop(lease)
    reset()
    lease = policy_lease(robot, LIBERO_TASK_POLICY)
    observed = capture(robot)
    submit(robot, lease, 0, action, observed["observationId"])
    robot.advance(10)
    before = robot.status()
    assert before["lastApplied"]["sequence"] == 0 and before["lastCompleted"] is None
    robot.stop(lease)
    error = rejected(lambda: submit(robot, lease, 1, action, observed["observationId"]), "control generation")
    robot.advance(510)
    after = robot.status()
    assert after["lastApplied"] == before["lastApplied"] and after["lastCompleted"] is None
    assert not after["pending"] and after["stopState"] == "stopped"
    checks["stop_during_action"] = {"before": before, "after": after, "late_rejection": error}
    reset()
    lease = policy_lease(robot, LIBERO_TASK_POLICY)
    observed = capture(robot)
    timeout_out = out / "timeout-request"
    timeout_out.mkdir()
    try:
        infer(policy_preview(observed), 18066, timeout_out, timeout_s=.001)
    except subprocess.TimeoutExpired as exc:
        timeout_error = str(exc)
    else:
        raise AssertionError("Injected inference process timeout did not fire")
    robot.stop(lease)
    robot.advance(500)
    state = assert_no_action(robot)
    assert robot.status()["stopState"] == "stopped"
    error = rejected(lambda: submit(robot, lease, 0, action, observed["observationId"]), "control generation")
    checks["inference_process_timeout"] = {"timeout_seconds": .001, "error": timeout_error,
                                            "late_rejection": error, **state}
    save(out / "output-and-stop-gates.json", checks)
    print(json.dumps({"pass": True, "base_gates": 4, "additional_gates": list(checks)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    host = StewardHost(args.root, scenario=SCENARIO, freeze_idle=True, dispatch_task=False,
                       speed=float("inf")).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(run, host, args.out)
            while not work.done():
                host.pump(timeout=.002)
            work.result()
    finally:
        host.stop()


if __name__ == "__main__":
    main()
