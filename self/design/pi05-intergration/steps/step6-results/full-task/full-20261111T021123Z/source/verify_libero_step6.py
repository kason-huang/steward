"""Run the Panda initial-state-0 policy validation in a dedicated WSim and ER."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError

from steward.ego.panda_observation import PandaObservationRuntime, policy_inputs
from steward.ego.runtime import RobotLink
from steward.glrender import encode_image
from steward.world.panda_control import POLICY_OBSERVATION_WALL_S, POLICY_PREFIX_STEPS, policy_hover_target

from verify_libero_step3 import Robot, call, credentials

SCENARIO = "libero-object-alphabet-soup-panda"
PROMPT = "pick up the alphabet soup and place it in the basket"


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def infer(preview, port, out):
    observation = out / "observation.json"
    save(observation, preview | {"prompt": PROMPT})
    folder = Path(__file__).resolve().parent
    wrapper = folder.parents[2] / "pi05-step1/client.sh"
    subprocess.run(["bash", str(wrapper), str(folder / "openpi_step6_infer.py"), str(port),
                    str(observation), str(out / "inference.json")], check=True, timeout=60)
    return json.loads((out / "inference.json").read_text())


def policy_lease(robot):
    return credentials(call(robot.url, "/control/acquire", credentials(robot.status())
                            | {"ttlS": 30, "policy": True}))


def capture(robot):
    return PandaObservationRuntime(RobotLink(robot.url)).read()


def policy_preview(frame):
    inputs = policy_inputs(frame)
    return {"episodeId": frame["episodeId"], "step": frame["step"],
            "observation/state": inputs["observation/state"].tolist(),
            **{key: base64.b64encode(encode_image(inputs[key])).decode("ascii")
               for key in ("observation/image", "observation/wrist_image")}}


def submit(robot, lease, sequence, action, observation_id):
    return call(robot.url, "/control/action", lease | {
        "sequence": sequence, "action": action, "observationId": observation_id})


def rejected(action, message):
    try:
        action()
    except HTTPError as exc:
        error = json.loads(exc.read())["error"]
        assert exc.code == 409 and message in error, (exc.code, error)
        return error
    raise AssertionError(f"Expected rejection: {message}")


def assert_no_action(robot):
    state = robot.status()
    assert state["lastApplied"] is None and state["lastCompleted"] is None and not state["pending"]
    return {"step": state["step"], "episodeId": state["episodeId"],
            "controlGeneration": state["controlGeneration"], "lastApplied": state["lastApplied"],
            "lastCompleted": state["lastCompleted"], "pending": state["pending"], "reason": state["reason"]}


def fault_checks(robot, reset, action, out):
    evidence = {}

    lease = policy_lease(robot)
    observed = capture(robot)
    missing = observed | {"images": {"external": observed["images"]["external"]}}
    try:
        policy_preview(missing)
    except ValueError as exc:
        client_rejection = str(exc)
    else:
        raise AssertionError("Missing wrist frame reached the policy")
    error = rejected(lambda: submit(robot, lease, 0, action, None), "synchronized observation")
    robot.advance(25)
    evidence["missing_wrist"] = {"client_rejection": client_rejection, "wsim_rejection": error,
                                 **assert_no_action(robot)}
    reset()

    lease = policy_lease(robot)
    observed = capture(robot)
    robot.advance(POLICY_PREFIX_STEPS + 25)
    error = rejected(lambda: submit(robot, lease, 0, action, observed["observationId"]), "observation expired")
    robot.advance(POLICY_PREFIX_STEPS + 50)
    evidence["stale_step"] = {"source_step": observed["step"], "wsim_rejection": error,
                              **assert_no_action(robot)}
    reset()

    lease = policy_lease(robot)
    observed = capture(robot)
    submit(robot, lease, 0, action, observed["observationId"])
    assert robot.status()["pending"] and robot.status()["lastApplied"] is None
    reset()
    error = rejected(lambda: submit(robot, lease, 1, action, observed["observationId"]), "Stale Panda episode")
    robot.advance(25)
    evidence["episode_reset"] = {"old_episode": observed["episodeId"], "queued_before_reset": True,
                                 "wsim_rejection": error, **assert_no_action(robot)}
    reset()

    lease = policy_lease(robot)
    observed = capture(robot)
    submit(robot, lease, 0, action, observed["observationId"])
    assert robot.status()["pending"] and robot.status()["lastApplied"] is None
    time.sleep(POLICY_OBSERVATION_WALL_S + .1)
    robot.advance(25)
    state = assert_no_action(robot)
    assert state["reason"] == "observation_expired"
    evidence["queued_wall_expiration"] = {"queued_before_expiry": True, **state}
    reset()
    save(out / "fault-gates.json", evidence)


def short_prefix(robot, port, out):
    lease = policy_lease(robot)
    try:
        results = []
        sequence = 0
        for index in range(2):
            round_out = out / f"prefix-{index}"
            round_out.mkdir()
            frame = capture(robot)
            before = robot.status()
            assert frame["observationId"] and frame["step"] == before["step"]
            prediction = infer(policy_preview(frame), port, round_out)
            receipts = []
            prefix = prediction["actions"][:3]
            target = before["sitePosition"]
            for action in prefix:
                target = policy_hover_target(action, target)
            for action in prefix:
                accepted = submit(robot, lease, sequence, action, frame["observationId"])
                start = accepted["step"]
                robot.advance(start + 25)
                status = robot.status()
                receipt = status["lastCompleted"]
                assert receipt == {"sequence": sequence, "controlGeneration": lease["controlGeneration"],
                                   "startStep": start, "plannedEndStep": start + 25, "endStep": start + 25}
                receipts.append({"action": action, "receipt": receipt, "site_position": status["sitePosition"]})
                sequence += 1
            summary = {"observation_step": frame["step"], "episodeId": frame["episodeId"],
                       "control_generation": lease["controlGeneration"], "before_site": before["sitePosition"],
                       "receipts": receipts, "discarded": len(prediction["actions"]) - len(receipts)}
            save(round_out / "applied.json", summary)
            results.append(summary)
        save(out / "prefixes.json", results)
    finally:
        robot.stop(lease)
    robot.advance(robot.status()["step"] + 500)
    stopped = robot.status()
    assert stopped["lastApplied"]["sequence"] == sequence - 1 and not stopped["pending"]
    assert stopped["stopState"] == "stopped"
    save(out / "stop.json", stopped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=18065)
    parser.add_argument("--short-prefix", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True)
    env = os.environ | {"MUJOCO_GL": "egl", "OPENBLAS_NUM_THREADS": "1"}
    with (args.out / "steward.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "steward.cli", "--root", str(args.root), "up", "--scenario", SCENARIO,
             "--wca-port", "0", "--robot-port", "0", "--api-port", "0", "--speed", "max",
             "--freeze-idle", "--no-dispatch-task"], stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        try:
            addresses = json.loads(server.stdout.readline())
            panda = addresses["robots"]["w:panda"]
            robot = Robot(panda["url"])
            before = robot.status()
            assert before["step"] == 0 and before["lastApplied"] is None and not before["leased"]
            state = call(panda["egoApi"], "/ego/observe", {})
            frame = call(panda["egoApi"], "/ego/observation")
            assert (state["episodeId"], state["step"]) == (frame["episodeId"], frame["step"])
            result = infer(frame, args.port, args.out)
            after = robot.status()
            assert frame["episodeId"] == before["episodeId"] and frame["step"] == 0
            assert after == before
            save(args.out / "inference-only.json", {"before": before, "after": after, "pass": True})
            if args.short_prefix:
                def reset():
                    previous = robot.status()["episodeId"]
                    call(addresses["wca"], "/wca/episode", {"action": "reset"})
                    robot.advance(0)
                    assert robot.status()["episodeId"] > previous
                robot.advance(0)
                fault_checks(robot, reset, result["actions"][0], args.out)
                short_prefix(robot, args.port, args.out)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
