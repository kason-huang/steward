"""Run fixed LIBERO controls through the Steward HTTP ports and record the physics."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from unittest.mock import patch

import numpy as np

from probe_libero_step3_osc import max_errors
from verify_libero_step2 import call, dump, request
from steward.scene.libero import PANDA_ID, scenario_name
from steward.world.panda_control import (ARM_SPEED_LIMIT, FINGER_SPEED_LIMIT, SITE_ANGULAR_LIMIT,
                                         SITE_SPEED_LIMIT, STOP_WINDOW, ControlConflict)
from steward.world.panda_osc import ARM_JOINTS
from steward.world.server import StewardHost

LIMITS = {"site_m": 1e-5, "site_rad": 1e-4, "goal_m": 1e-5, "goal_rad": 1e-4,
          "arm_torque": 1e-3, "finger_ctrl": 1e-10, "arm_rad": 1e-5, "arm_rad_s": 1e-4}
CREDENTIAL_KEYS = ("episodeId", "controlGeneration", "leaseId")


def expect_error(code, base, path, body):
    try:
        call(base, path, body)
    except HTTPError as exc:
        assert exc.code == code, (path, exc.code, exc.read())
        return
    raise AssertionError(f"{path} did not return {code}")


def credentials(status):
    return {key: status[key] for key in CREDENTIAL_KEYS if key in status}


class Robot:
    def __init__(self, url):
        self.url = url

    def status(self):
        return call(self.url, "/control/status")

    def advance(self, step):
        return call(self.url, "/clock/wait", {"hold": "validation", "until": step * 0.002})

    def acquire(self, ttl=30):
        return credentials(call(self.url, "/control/acquire", credentials(self.status()) | {"ttlS": ttl}))

    def action(self, lease, sequence, values):
        return call(self.url, "/control/action", lease | {"sequence": sequence, "action": values})

    def stop(self, lease):
        return call(self.url, "/control/stop", lease)


def pulse_measurements(trial, trace):
    pulses = []
    for index, (source, observed) in enumerate(zip(trial["cycles"], trace, strict=True)):
        if not source["label"].startswith("axis-"):
            continue
        axis = int(source["label"].split("-")[1])
        sign = np.sign(source["action"][axis])
        assert trace[index + 1]["label"] == "hold"
        start = trace[index - 1] if index else trial["initial"]
        end = trace[index + 1]
        if axis < 3:
            movement = end["site_position"][axis] - start["site_position"][axis]
        else:
            # sin of the net rotation angle times its world-axis projection.
            relative = np.asarray(end["site_rotation"]) @ np.asarray(start["site_rotation"]).T
            a, b = (axis - 1) % 3, (axis - 2) % 3
            movement = (relative[a, b] - relative[b, a]) / 2
        assert movement * sign > 0, (source["label"], movement)
        pulses.append({"label": observed["label"], "signedProjection": movement})
    assert len(pulses) == 12
    return pulses


def grip_measurements(sim, trial, trace):
    names = ("gripper0_finger_joint1", "gripper0_finger_joint2")
    samples = []
    for index, (source, observed) in enumerate(zip(trial["cycles"], trace, strict=True)):
        if not source["label"].startswith("grip-"):
            continue
        state = observed["state"]
        expected = [source["state"]["joints"][name]["qpos"][0] for name in names]
        np.testing.assert_allclose(state["fingerPosition"], expected, atol=1e-6, rtol=0)
        for name, value in zip(names, state["fingerPosition"], strict=True):
            low, high = sim.model.joint(name).range
            assert low - 0.001 <= value <= high + 0.001
        samples.append({"label": observed["label"], "step": state["step"],
                        "widthM": abs(np.diff(state["fingerPosition"])[0])})
        assert state["gripTarget"] == source["state"]["grip_target"]
    assert len(samples) == 16
    assert samples[0]["widthM"] - samples[7]["widthM"] > 0.03
    assert samples[-1]["widthM"] - samples[7]["widthM"] > 0.03
    assert not sim.robots[PANDA_ID].grippers
    return samples


def capture_replay(host, robot, trial, out, index):
    trace = []
    frames = out / "frames"
    wrist_frames = out / "wrist"
    if index == 0:
        frames.mkdir()
        wrist_frames.mkdir()
    sim = host.sim
    controls = []
    dispose = sim.subscribe_steps(lambda: controls.append(sim.data.ctrl.tolist()))
    lease = robot.acquire()
    try:
        for sequence, expected in enumerate(trial["cycles"]):
            controls.clear()
            accepted = robot.action(lease, sequence, expected["action"])
            assert accepted["pending"] and accepted["acceptedSequence"] == sequence
            assert accepted["lastApplied"] is None or accepted["lastApplied"]["sequence"] < sequence
            robot.advance((sequence + 1) * 25)
            state = robot.status()
            assert len(controls) == 25 and state["lastCompleted"] == {
                "sequence": sequence, "controlGeneration": lease["controlGeneration"],
                "startStep": sequence * 25, "plannedEndStep": (sequence + 1) * 25, "endStep": (sequence + 1) * 25,
            }
            trace.append({
                "label": expected["label"], "action": expected["action"], "state": state, "controls": controls.copy(),
                "site_position": state["sitePosition"], "site_rotation": state["siteRotation"],
                "goal_position": state["goalPosition"], "goal_rotation": state["goalRotation"],
            })
            if index == 0:
                frame = request(host.wca.url, "/wca/render?camera=overview&width=640&height=480")
                (frames / f"{sequence:03}.png").write_bytes(frame)
                wrist = request(robot.url, "/camera/rgb?camera=wrist&width=480&height=480")
                (wrist_frames / f"{sequence:03}.png").write_bytes(wrist)
    finally:
        dispose()
    errors = max_errors(trial, trace)
    for field, output, key in (("qpos", "armPosition", "arm_rad"),
                               ("qvel", "armVelocity", "arm_rad_s")):
        errors["max"][key] = max(
            float(np.max(np.abs(np.subtract(actual["state"][output],
                   [reference["state"]["joints"][name][field][0] for name in ARM_JOINTS]))))
            for actual, reference in zip(trace, trial["cycles"], strict=True)
        )
    assert all(value <= LIMITS[key] for key, value in errors["max"].items()), errors
    with gzip.open(out / f"wsim-init-{index}.json.gz", "wt") as stream:
        json.dump(trace, stream, separators=(",", ":"))
    return {"errors": errors, "axisPulses": pulse_measurements(trial, trace),
            "gripper": grip_measurements(sim, trial, trace)}


def lifecycle(host, robot):
    base = host.wca.url
    url = robot.url
    action = [0.8, 0, 0, 0, 0, 0, 1]
    call(base, "/wca/episode", {"action": "reset"})
    robot.advance(0)
    lease = robot.acquire()
    expect_error(409, url, "/control/acquire", credentials(robot.status()) | {"ttlS": 1})
    expect_error(409, url, "/control/renew", credentials(robot.status()) | {"ttlS": 1})
    expect_error(400, url, "/control/action", lease | {"sequence": 0, "action": [0]})
    expect_error(400, url, "/control/action", lease | {"sequence": 0, "action": [float("nan")] * 7})
    expect_error(400, url, "/command", {"gripper": "close"})
    assert robot.status()["acceptedSequence"] == -1
    robot.action(lease, 0, action)
    robot.advance(10)
    expect_error(409, url, "/control/action", lease | {"sequence": 0, "action": action})
    robot.action(lease, 1, action)
    stopped = robot.stop(lease)
    assert not stopped["pending"] and stopped["stopState"] == "unknown"
    expect_error(409, url, "/control/stop", lease)
    repeated = robot.stop(credentials(stopped))
    assert repeated["controlGeneration"] == stopped["controlGeneration"]
    robot.advance(510)
    held = robot.status()
    assert held["lastApplied"]["sequence"] == 0 and held["lastCompleted"] is None
    assert held["stopState"] == "stopped" and held["gripTarget"] == stopped["gripTarget"]
    assert held["stillSteps"] >= STOP_WINDOW
    assert max(map(abs, held["armVelocity"])) <= ARM_SPEED_LIMIT
    assert max(map(abs, held["fingerVelocity"])) <= FINGER_SPEED_LIMIT
    assert held["siteLinearSpeed"] <= SITE_SPEED_LIMIT
    assert held["siteAngularSpeed"] <= SITE_ANGULAR_LIMIT

    lease = robot.acquire(ttl=0.1)
    robot.action(lease, 0, action)
    time.sleep(0.15)
    expired = robot.status()
    assert expired["reason"] == "expired" and not expired["pending"] and expired["step"] == held["step"]
    expect_error(409, url, "/control/renew", lease | {"ttlS": 1})
    expect_error(409, url, "/control/action", lease | {"sequence": 1, "action": action})
    robot.advance(560)
    assert robot.status()["lastApplied"] == held["lastApplied"]

    lease = robot.acquire()
    robot.action(lease, 0, action)
    call(base, "/wca/episode", {"action": "reset"})
    reset = robot.status()
    assert reset["episodeId"] != lease["episodeId"] and reset["lastApplied"] is None
    expect_error(409, url, "/control/action", lease | {"sequence": 1, "action": action})
    replacement = robot.acquire()
    robot.action(replacement, 0, action)
    robot.advance(25)
    final = robot.status()
    assert final["lastCompleted"]["sequence"] == 0
    call(base, "/wca/clock", {"action": "pause"})
    call(url, "/control/renew", replacement | {"ttlS": 0.1})
    robot.action(replacement, 1, action)
    time.sleep(0.15)
    call(base, "/wca/clock", {"action": "resume"})
    robot.advance(50)
    resumed = robot.status()
    assert resumed["reason"] == "expired" and resumed["lastApplied"] == final["lastApplied"]
    assert resumed["gripTarget"] == final["gripTarget"]

    replacement = robot.acquire()
    clipped = [100, -100, 0, 0, 0, 0, 0]
    robot.action(replacement, 0, clipped)
    robot.advance(51)
    limited = robot.status()
    # At the first substep both position goals use the same site position as the reported state.
    np.testing.assert_allclose(np.subtract(limited["goalPosition"], limited["sitePosition"]),
                               [0.05, -0.05, 0], atol=1e-12, rtol=0)
    old = host.sim
    robot.action(replacement, 1, action)
    switched = call(base, "/wca/episode", {"action": "load", "scenario": scenario_name(0, panda=True)})
    next_robot = Robot(switched["robots"][PANDA_ID])
    switched_status = next_robot.status()
    assert switched_status["episodeId"] != replacement["episodeId"] and switched_status["lastApplied"] is None
    expect_error(409, next_robot.url, "/control/action", replacement | {"sequence": 2, "action": action})
    expect_error(404, next_robot.url, "/sensors/rgbd", None)
    assert old.panda.retired and old.panda.pending is None and old.panda.running is None
    for attempt in (lambda: old.panda_request(PANDA_ID, "status", {}), lambda: old.wait_until(0.2)):
        try:
            attempt()
        except ControlConflict:
            pass
        else:
            raise AssertionError("Retired Panda episode still accepted a request")
    fault_lease = next_robot.acquire()
    next_robot.action(fault_lease, 0, action)
    with patch.object(host.sim.panda.osc, "apply", side_effect=np.linalg.LinAlgError("diagnostic fault")):
        host.sim.step()
    fault = next_robot.status()
    assert fault["reason"] == "controller-fault" and fault["stopState"] == "unknown"
    assert not fault["leased"] and not fault["pending"] and host.sim.paused
    assert fault["lastApplied"] is None
    expect_error(409, next_robot.url, "/control/acquire", credentials(fault) | {"ttlS": 1})
    call(base, "/wca/episode", {"action": "reset"})
    recovered = next_robot.status()
    assert recovered["fault"] is None and recovered["episodeId"] != fault["episodeId"]
    return {"scenarioSwitch": switched_status, "retiredStep": old.step_count, "fault": fault,
            "faultReset": recovered,
            "pausedExpiry": resumed, "clippedAction": limited, "stopped": stopped, "repeatStop": repeated, "settled": held, "expired": expired,
            "reset": reset, "newOwner": final}


def regressions(host):
    base = host.wca.url
    summary = {}
    for name in ("phase0-kitchen", "cobot-table"):
        loaded = call(base, "/wca/episode", {"action": "load", "scenario": name})
        robot, url = next(iter(loaded["robots"].items()))
        before = call(url, "/sensors/proprioception")
        call(url, "/command", {"base": [0.01, 0, 0]})
        sim = host.sim
        ctrl = sim.data.actuator(sim.build.names[robot].actuators["root_x"]).ctrl[0]
        assert ctrl == 0.01
        assert call(base, "/wca/episode", {"action": "reset"})["reset"]
        after = call(url, "/sensors/proprioception")
        assert before["episode"] != after["episode"] and before["joints"] == after["joints"]
        summary[name] = {"robot": robot, "oldEpisode": before["episode"], "resetEpisode": after["episode"],
                         "ordinaryBaseCommandX": ctrl}
    return summary


def validate(host, baseline, out):
    protocol = json.loads((baseline / "protocol.json").read_text())
    model_reference = json.loads((baseline / "model.json").read_text())
    summary = {}
    for index in protocol["indices"]:
        name = scenario_name(index, panda=True)
        loaded = call(host.wca.url, "/wca/episode", {"action": "load", "scenario": name})
        robot = Robot(loaded["robots"][PANDA_ID])
        assert call(host.wca.url, "/console/state")["scenario"] == name
        with gzip.open(baseline / f"init-{index}-repeat-0.json.gz", "rt") as stream:
            record = json.load(stream)
        warm = protocol["warmup_cycles"]
        trial = {"initial": record["cycles"][warm - 1]["state"], "cycles": record["cycles"][warm:]}
        sim = host.sim
        assert sim.timestep == model_reference["physics_step"]
        assert model_reference["control_period"] == 25 * sim.timestep
        assert [sim.model.actuator(i).name for i in range(sim.model.nu)] == model_reference["actuators"]
        assert {sim.model.joint(i).name for i in range(sim.model.njnt)} == set(model_reference["joints"])
        assert sim.model.site("gripper0_grip_site").id == sim.panda.osc.site
        assert sim.model.camera("overview").id >= 0
        assert sim.model.camera("robot0_eye_in_hand").id >= 0
        for kind, fields_by_name in model_reference["effective"].items():
            getter = getattr(sim.model, kind)
            for element, fields in fields_by_name.items():
                compiled = getter(element)
                for field, expected in fields.items():
                    if field == "joint":
                        assert sim.model.joint(int(compiled.trnid[0])).name == expected
                    else:
                        np.testing.assert_allclose(getattr(compiled, field), expected, rtol=1e-7, atol=1e-9,
                                                   err_msg=f"{kind} {element} {field}")
        for name, fields in model_reference["bodies"].items():
            for field, value in fields.items():
                if field == "iquat":
                    q = sim.model.body(name).iquat
                    assert 1 - abs(np.dot(q, value)) < 1e-10
                else:
                    np.testing.assert_allclose(getattr(sim.model.body(name), field), value, rtol=1e-6, atol=1e-10)
        snapshots = []
        for _ in range(2):
            call(host.wca.url, "/wca/episode", {"action": "reset"})
            robot.advance(0)
            assert robot.status()["step"] == 0
            sim = host.sim
            snapshot = {joint: {"qpos": sim.data.joint(joint).qpos.tolist(),
                                "qvel": sim.data.joint(joint).qvel.tolist()}
                        for joint in trial["initial"]["joints"]}
            assert snapshot == trial["initial"]["joints"]
            snapshots.append(snapshot)
        summary[index] = {"trajectory": capture_replay(host, robot, trial, out, index),
                          "resetSnapshots": snapshots}
        print(f"verified Panda initial state {index}", flush=True)
    dump(out / "lifecycle.json", lifecycle(host, robot))
    dump(out / "summary.json", {
        "limits": LIMITS,
        "stopLimits": {"armRadS": ARM_SPEED_LIMIT, "fingerMS": FINGER_SPEED_LIMIT,
                       "siteMS": SITE_SPEED_LIMIT, "siteRadS": SITE_ANGULAR_LIMIT, "steps": STOP_WINDOW},
        "initialStates": summary, "legacy": regressions(host),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    host = StewardHost(args.root, freeze_idle=True, dispatch_task=False).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(validate, host, args.root / "libero-step3", args.out)
            while not future.done():
                host.pump(timeout=0.002)
            future.result()
    finally:
        host.stop()
    subprocess.run([args.ffmpeg, "-loglevel", "error", "-framerate", "20", "-i",
                    str(args.out / "frames/%03d.png"), "-framerate", "20", "-i",
                    str(args.out / "wrist/%03d.png"), "-filter_complex", "hstack",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(args.out / "wsim-init0.mp4")], check=True)


if __name__ == "__main__":
    main()
