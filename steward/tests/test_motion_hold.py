"""S0 protection and control handoff against the MuJoCo robot port."""

import time

import pytest

from steward.assets.generators import steward_bot
from steward.ego.runtime import EgoRuntime, RobotLink
from steward.ego.server import ego_api
from steward.net import HttpError, call
from steward.world.server import StewardHost

ROBOT = "w:robot-1"


def _wait_for(read, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        result = read()
        if predicate(result):
            return result
        if time.monotonic() >= deadline:
            pytest.fail(f"condition not reached, last result: {result}")
        time.sleep(0.02)


def test_s0_holds_the_arm_and_rejects_old_commands_without_an_ego_runtime(kitchen_root):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen").start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    try:
        sim = host.sim
        initial = link.motion_status()
        initial_reach = link.proprioception()["joints"]["reach"]
        target = initial_reach + 0.22
        link.command({"reach": target, "gripper": "close"}, initial["epoch"])
        moving = _wait_for(link.proprioception, lambda frame: frame["joints"]["reach"] >= initial_reach + 0.03)
        assert moving["joints"]["reach"] < target - 0.03

        # A paused physics clock cannot provide evidence of standstill after this request.
        with sim.lock:
            sim.paused = True
        hold = link.hold_motion()
        assert hold["state"] == "unknown"
        assert hold["epoch"] != initial["epoch"]
        with pytest.raises(HttpError, match="standstill"):
            link.resume_motion(hold["epoch"])
        with pytest.raises(HttpError, match="on hold"):
            link.command({"gripper": "open"}, initial["epoch"])

        with sim.lock:
            sim.paused = False
        settled = _wait_for(link.motion_status, lambda status: status["state"] == "held")
        assert settled["t"] > hold["t"]
        held = link.proprioception()
        assert held["gripper"]["state"] == "closed"
        assert held["joints"]["reach"] < target - 0.03
        resumed = link.resume_motion(settled["epoch"])
        assert resumed["state"] == "ready"
        with pytest.raises(HttpError, match="stale"):
            link.command({"reach": 0.3}, initial["epoch"])
        with pytest.raises(HttpError, match="missing"):
            link.command({"reach": 0.3})
        link.command({"gripper": "open"}, resumed["epoch"])
        assert link.proprioception()["gripper"]["state"] == "open"
    finally:
        host.stop()


def test_runtime_cancels_a_running_job_and_requires_s0_resume(kitchen_root):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    runtime = EgoRuntime(link, steward_bot().to_dict())
    runtime.start()
    api = ego_api(runtime).start()
    try:
        job = runtime.explore()
        assert job["motionEpoch"] == link.motion_status()["epoch"]
        stop = call("POST", f"{api.url}/skill/stop", {})
        assert stop["stopped"] == job["id"]
        assert stop["motion"]["epoch"] != job["motionEpoch"]
        assert runtime.wait(job["id"], timeout=5)["state"] == "stopped"
        with pytest.raises(PermissionError, match="on hold"):
            runtime.explore()
        settled = _wait_for(link.motion_status, lambda status: status["state"] == "held")
        resumed = call("POST", f"{api.url}/ego/motion/resume", {"epoch": settled["epoch"]})
        new_job = runtime.explore()
        assert new_job["id"] != job["id"]
        assert new_job["motionEpoch"] == resumed["epoch"]
        runtime.stop_motion()
        assert runtime.wait(new_job["id"], timeout=5)["state"] == "stopped"
    finally:
        api.stop()
        runtime.stop()
        host.stop()


def test_protective_stop_invalidates_an_intent_recovery_authorization(kitchen_root):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    try:
        revision = link.hold_motion("intent-revision")
        assert revision["reason"] == "intent-revision"
        _wait_for(link.motion_status, lambda status: status["state"] == "held")
        protective = link.hold_motion()
        assert protective["reason"] == "protection"
        assert protective["epoch"] != revision["epoch"]
        assert link.hold_motion("intent-revision")["epoch"] == protective["epoch"]
        _wait_for(link.motion_status, lambda status: status["state"] == "held")
        with pytest.raises(HttpError, match="current hold"):
            link.resume_motion(revision["epoch"])
    finally:
        host.stop()


def test_a_lost_dispatch_can_be_looked_up_by_receipt_without_starting_a_second_job(kitchen_root):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    runtime = EgoRuntime(link, steward_bot().to_dict())
    runtime.start()
    api = ego_api(runtime).start()
    try:
        pose = link.sensors()["odometry"]
        target = {key: pose[key] for key in ("x", "y", "yaw")}
        receipt = call("POST", f"{api.url}/ego/predict", {"skill": "navigate", "params": target})
        assert receipt["withinRiskLimit"], receipt["issues"]
        status_url = f"{api.url}/skill/status?receipt={receipt['id']}"
        assert call("GET", status_url)["state"] == "unknown"
        sent = call("POST", f"{api.url}/skill/run", {"receipt": receipt["id"]})
        recovered = call("GET", status_url)
        assert recovered["id"] == sent["id"]
        runtime.wait(recovered["id"], timeout=10)
        with pytest.raises(HttpError, match="already executed"):
            call("POST", f"{api.url}/skill/run", {"receipt": receipt["id"]})
        assert len(runtime.executor.jobs) == 1
    finally:
        api.stop()
        runtime.stop()
        host.stop()
