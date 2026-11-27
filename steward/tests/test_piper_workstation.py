"""Integration checks using the PhyAgentOS robot-control-sim asset."""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from steward.assets.piper import ARM_JOINTS, WORKSPACE_CAMERA, WRIST_CAMERA
from steward.assets.generators import printed_board
from steward.ego.bodies import robot_model, runtime_config
from steward.ego.monitor import MonitorConfig
from steward.ego.perception import decode_rgbd
from steward.ego.runtime import EgoRuntime, RobotLink, RuntimeConfig
from steward.ego.server import ego_api
from steward.net import HttpError, call
from steward.world.server import StewardHost

ROBOT = "w:robot-1"

pytestmark = pytest.mark.skipif(not os.environ.get("STEWARD_PIPER_MJCF"),
                                reason="requires the PhyAgentOS robot-control-sim PiPER MJCF")


def test_native_piper_motion_hold_and_camera_ports(tmp_path):
    host = StewardHost(tmp_path, scenario="piper-workstation", speed=1.0)
    host.sim.paused = True
    host.start()
    sim = host.sim
    link = RobotLink(host.robot_servers[ROBOT].url)
    try:
        assert host.embodiments[ROBOT]["id"] == "phyagent-piper"
        assert call("GET", f"{host.wca.url}/console/state")["holding"][ROBOT]["graspMode"] == "contact"
        assert sim.model.nu == 7 and sim.model.neq == 1
        assert set(link.proprioception()["joints"]) == {*ARM_JOINTS, "joint7", "joint8"}
        sensors = link.sensors()
        assert sensors["odometry"]["z"] == pytest.approx(0.751)
        assert {c["id"] for c in sensors["cameras"]} == {WORKSPACE_CAMERA, WRIST_CAMERA}
        assert link.camera_calibration()["reference_camera"] == WORKSPACE_CAMERA
        for camera in (WORKSPACE_CAMERA, WRIST_CAMERA):
            frame = decode_rgbd(sim.rgbd(ROBOT, camera))
            assert frame["camera"] == camera
            assert frame["rgb"].shape[:2] == frame["depth"].shape
            assert frame["depth"].max() > 0.1
            assert "detections" not in frame["header"]

        original = link.motion_status()["epoch"]
        link.command({"gripper": "close"}, original)
        sim.step_seconds(0.5)
        assert link.proprioception()["gripper"]["state"] == "closed"
        assert not link.proprioception()["gripper"]["holding"]
        link.command({"joints": {"joint1": 1.0}, "gripper": "open"}, original)
        assert sim.data.ctrl[sim.model.actuator(f"{ROBOT}/gripper").id] == pytest.approx(0.105)
        sim.step_seconds(0.03)
        mid = link.proprioception()["joints"]["joint1"]
        assert 0.02 < mid < 0.6
        hold = link.hold_motion()
        assert hold["state"] == "unknown"
        assert hold["epoch"] != original
        with pytest.raises(HttpError, match="standstill"):
            link.resume_motion(hold["epoch"])
        with sim.lock:
            sim.paused = False
        deadline = time.monotonic() + 4.0
        while link.motion_status()["state"] != "held" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert link.motion_status()["state"] == "held"
        stopped = link.proprioception()
        assert stopped["joints"]["joint1"] == pytest.approx(mid, abs=0.04)
        assert stopped["gripper"]["state"] == "open"
        assert stopped["joints"]["joint8"] == pytest.approx(-stopped["joints"]["joint7"], abs=0.002)
        resumed = link.resume_motion(hold["epoch"])
        with pytest.raises(HttpError, match="stale"):
            link.command({"joints": {"joint1": 1.0}}, original)
        link.command({"joints": {"joint1": -0.1}}, resumed["epoch"])
    finally:
        host.stop()


def _pump_until(host, read, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host.pump(0.02)
        result = read()
        if result:
            return result
    pytest.fail("PiPER perception or World update did not arrive")


def test_piper_direct_s0_commands_wait_for_encoders_and_honor_task_stop(tmp_path):
    host = StewardHost(tmp_path, scenario="piper-workstation", speed=1.0).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    body_id = host.embodiments[ROBOT]["id"]
    runtime = EgoRuntime(link, robot_model(body_id), runtime_config(body_id))
    api = ego_api(runtime).start()

    def command(body):
        return call("POST", f"{api.url}/ego/body/command", body, timeout=10)

    try:
        s0_tools = call("GET", f"{api.url}/ego/plan")["availableS0Tools"]
        assert s0_tools["body_set_joints"] == s0_tools["body_set_gripper"] == "control"
        initial = link.proprioception()["joints"]
        with pytest.raises(HttpError, match="active, controlled"):
            command({"joints": {"joint1": 0.2}})
        runtime.acquire_control("operator")
        scope = {"owner": "operator", "taskId": "test-direct-s0"}
        for invalid in ({"joint1": 0.2, "joint2": 20}, {"joint7": 0.01}, {"joint1": True}):
            with pytest.raises(HttpError):
                command({**scope, "joints": invalid})
        assert link.proprioception()["joints"]["joint1"] == pytest.approx(initial["joint1"], abs=0.001)

        result = command({**scope, "joints": {"joint1": 0.2}})
        assert result["state"] == "reached"
        assert result["joints"] == {"joint1": pytest.approx(0.2, abs=0.01)}
        assert link.proprioception()["joints"]["joint2"] == pytest.approx(initial["joint2"], abs=0.01)
        result = command({**scope, "gripper": "close"})
        assert result["state"] == "reached" and result["gripper"]["state"] == "closed"
        assert result["joints"] == {"joint7": pytest.approx(0, abs=0.002), "joint8": pytest.approx(0, abs=0.002)}
        result = command({**scope, "gripper": "open"})
        assert result["state"] == "reached" and result["gripper"]["state"] == "open"
        assert result["joints"] == {"joint7": pytest.approx(0.0525, abs=0.002),
                                    "joint8": pytest.approx(-0.0525, abs=0.002)}

        revision = runtime.revise_intent("operator", "Test revised S0 motion")["intentRevision"]
        with pytest.raises(HttpError, match="intent is stale"):
            command({**scope, "joints": {"joint1": 0.0}})
        scope["intentRevision"] = revision
        with host.sim.lock:
            host.sim.paused = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            moving = pool.submit(command, {**scope, "joints": {"joint1": 0.0}})
            actuator = host.sim.model.actuator(f"{ROBOT}/joint1").id
            _pump_until(host, lambda: host.sim.data.ctrl[actuator] == 0.0)
            with pytest.raises(HttpError, match="current motion"):
                command({**scope, "gripper": "close"})
            with pytest.raises(PermissionError, match="active S0 command"):
                runtime.run("unused-receipt", "operator", scope["intentRevision"], task_id=scope["taskId"])
            assert runtime.stop_motion(task_id="other-task") == {"stopped": None}
            stopped = runtime.stop_motion(task_id=scope["taskId"])
            assert stopped["stopped"] and stopped["direct"] and stopped["motion"]["state"] == "unknown"
            with pytest.raises(HttpError, match="interrupted"):
                moving.result(timeout=3)
        with pytest.raises(HttpError, match="task has ended"):
            command({**scope, "gripper": "close"})
        with pytest.raises(HttpError, match="on hold"):
            command({**scope, "taskId": "new-task", "gripper": "close"})
        assert runtime.executor.current is None
    finally:
        api.stop()
        host.stop()


def test_world_edit_reaches_piper_through_robot_rgbd_and_observer(tmp_path):
    host = StewardHost(tmp_path, scenario="piper-workstation", speed=1.0).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    runtime = EgoRuntime(link, robot_model(body), runtime_config(body))
    runtime.start()
    try:
        plan = runtime.plan_view()
        assert plan["body"] == "phyagent-piper"
        assert plan["availableSkills"] == ["pick", "place"]
        def seen_box(expected_x):
            return next((obj for obj in runtime.summary()["objects"]
                         if obj["category"] == "box" and obj["color"] == "black"
                         and abs(obj["position"][0] - expected_x) < 0.02
                         and obj["lastSeenAgoS"] < 0.3), None)

        initial = _pump_until(host, lambda: seen_box(0.31))
        assert initial["id"].startswith("e:")
        assert runtime.gripper_pose()["gripper_link"] == "link6"
        with ThreadPoolExecutor(max_workers=1) as http:
            edit = http.submit(call, "POST", f"{host.wca.url}/wca/edit",
                               {"action": "move", "entity": "w:black-box", "on": "w:table", "offset": [0.12, 0]})
            _pump_until(host, edit.done)
            assert edit.result()["ok"]
        moved = _pump_until(host, lambda: seen_box(0.39))
        assert moved["position"][0] - initial["position"][0] > 0.05

        def score():
            result = call("GET", f"{host.wca.url}/observer/latest").get(ROBOT, {})
            return result if result.get("matches", {}).get(moved["id"]) == "w:black-box" else None

        evidence = _pump_until(host, score)
        assert evidence["maxPositionErrorM"] < 0.035
        assert evidence["selfPositionErrorM"] == 0.0
        assert all(obj["id"].startswith("e:") for obj in runtime.summary()["objects"])
        _pump_until(host, lambda: all(obj["category"] != "box" or abs(obj["position"][0] - 0.31) > 0.03
                                     for obj in runtime.summary()["objects"]))
        if initial["id"] != moved["id"]:
            drop = next(event for event in runtime.dropped() if event["id"] == initial["id"])
            assert "seen through" in drop["reason"]
    finally:
        runtime.stop()
        host.stop()


def test_piper_monitor_holds_the_arm_when_a_pick_target_moves(tmp_path):
    host = StewardHost(tmp_path, scenario="piper-workstation", speed=1.0).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    config = runtime_config(body, RuntimeConfig(monitor=MonitorConfig(poll_s=0.05)))
    runtime = EgoRuntime(link, robot_model(body), config)
    runtime.start()
    try:
        def box_track():
            return next((obj for obj in runtime.summary()["objects"]
                         if obj["category"] == "box" and obj["color"] == "black" and obj["existence"] > 0.8), None)

        box = _pump_until(host, box_track)
        receipt = runtime.predict("pick", {"target": box["id"]})
        assert receipt["withinRiskLimit"], receipt["issues"]
        job = runtime.run(receipt["id"])
        _pump_until(host, lambda: link.proprioception()["joints"]["joint2"] > 0.05)
        with ThreadPoolExecutor(max_workers=1) as http:
            edit = http.submit(call, "POST", f"{host.wca.url}/wca/edit",
                               {"action": "move", "entity": "w:black-box", "on": "w:table", "offset": [0.12, 0]})
            _pump_until(host, edit.done)
            assert edit.result()["ok"]

        def protected():
            return next((event for event in runtime.monitor.events(0)["events"]
                         if event["kind"] == "target-position-changed"), None)

        event = _pump_until(host, protected)
        assert event["holdAccepted"] and event["job"] == job["id"] and event["displacementM"] > 0.05
        assert event["target"] == box["id"]
        assert runtime.wait(job["id"], timeout=3)["state"] == "stopped"
        assert _pump_until(host, lambda: link.motion_status()["state"] == "held")
        assert not link.proprioception()["gripper"]["holding"]
    finally:
        runtime.stop()
        host.stop()


def test_piper_camera_failure_preserves_last_seen_and_updates_joint_feedback(tmp_path, monkeypatch):
    host = StewardHost(tmp_path, scenario="piper-workstation").start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    runtime = EgoRuntime(link, robot_model(body), runtime_config(body))
    runtime.start()
    try:
        def box():
            return next((obj for obj in runtime.summary()["objects"] if obj["category"] == "box"), None)

        target = _pump_until(host, box)["id"]

        def disconnected(_camera=None):
            raise HttpError(503, "RGB-D source unavailable")

        monkeypatch.setattr(link, "rgbd", disconnected)
        _pump_until(host, lambda: runtime.summary().get("cameraErrors", {}).get(WORKSPACE_CAMERA))
        # An already pending image may complete during the switch. Measure once the failure is visible.
        with runtime._lock:
            observed_at = runtime.estimator.tracks[target].last_seen
        link.command({"joints": {"joint1": 0.2}}, link.motion_status()["epoch"])
        result = _pump_until(host, lambda: s if (s := runtime.summary())["self"]["joints"]["joint1"] > 0.1
                             and s["t"] - observed_at > 0.3 else None)
        assert result["cameraErrors"] == {WORKSPACE_CAMERA: "RGB-D source unavailable"}
        with runtime._lock:
            assert runtime.estimator.tracks[target].last_seen == observed_at
    finally:
        runtime.stop()
        host.stop()


def test_a_hidden_piper_target_expires_until_seen_again(tmp_path):
    host = StewardHost(tmp_path, scenario="piper-workstation").start()
    screen = printed_board(0.16, 0.04, 0.33, back=[0.58, 0.60, 0.65, 1.0], category="screen")
    host.authoring.publish_asset("piper-occluder", screen.to_dict())
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    runtime = EgoRuntime(link, robot_model(body), runtime_config(body))
    runtime.start()
    try:
        def fresh_box():
            return next((obj for obj in runtime.summary()["objects"]
                         if obj["category"] == "box" and obj["color"] == "black" and obj["lastSeenAgoS"] < 0.3), None)

        box = _pump_until(host, fresh_box)
        host.edit({"action": "spawn", "entity": "w:screen", "asset": "piper-occluder",
                   "on": "w:table", "offset": [0.19, -0.255]})
        covered = host.sim.time
        _pump_until(host, lambda: runtime.summary()["t"] - covered > 1.0)
        assert fresh_box() is None
        stale = runtime.predict("pick", {"target": box["id"]})
        assert not stale["feasible"] and "observe a current robot-side target" in stale["issues"][0]
        host.edit({"action": "remove", "entity": "w:screen"})
        visible = _pump_until(host, fresh_box)
        assert runtime.predict("pick", {"target": visible["id"]})["withinRiskLimit"]
    finally:
        runtime.stop()
        host.stop()


@pytest.mark.parametrize("interruption", ["normal", "revise-during-transfer", "drop-during-transfer",
                                          "re-pick-from-tray"])
def test_piper_pick_and_place_has_independent_world_and_robot_evidence(tmp_path, interruption):
    speed = 1.0 if interruption == "drop-during-transfer" else 2.0
    host = StewardHost(tmp_path, scenario="piper-workstation", speed=speed).start()
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    runtime = EgoRuntime(link, robot_model(body), runtime_config(body))
    runtime.start()
    try:
        def tracks():
            objects = runtime.summary()["objects"]
            selected = {
                obj["color"]: obj for obj in objects if obj["category"] in ("box", "tray")
                and obj["existence"] >= 0.8 and obj["lastSeenAgoS"] < 0.3
            }
            return selected if {"black", "blue", "brown"} <= selected.keys() else None

        objects = _pump_until(host, tracks)
        box = objects["black"]["id"]
        left = objects["blue"]["id"]
        right = objects["brown"]["id"]
        owner = "piper-test"
        runtime.acquire_control(owner)
        revision = runtime.revise_intent(owner, "把黑盒放到左盘")["intentRevision"]

        def start(skill, params):
            receipt = runtime.predict(skill, params, revision)
            assert receipt["withinRiskLimit"], receipt["issues"]
            return runtime.run(receipt["id"], owner, revision)

        def finish(job):
            return _pump_until(host, lambda: result if (result := runtime.job(job["id"]))["state"] != "running"
                               else None, timeout=12.0)

        score_url = f"{host.wca.url}/wca/verify-placement"

        def verify(place, support, world_support):
            assert place["state"] == "succeeded", place
            assert not link.proprioception()["gripper"]["holding"]
            goal = {"entity": "w:black-box", "support": world_support, "offset": [0, 0], "toleranceM": 0.035}
            world = call("POST", score_url, goal)
            assert world["complete"], world
            verdict = runtime.verify_placement(place["id"], goal | {"entity": box, "support": support},
                                               owner, revision)
            assert verdict["intent"] == "satisfied", (
                f"{verdict['reason']}, {verdict['evidence']}; "
                f"tracked {box}, drop events {[event for event in runtime.dropped() if event['id'] == box]}"
            )
            assert verdict["observedSupport"] == support

        def verify_contact_lift(source):
            assert link.proprioception()["gripper"]["holding"]
            with host.sim.lock:
                scene = host.sim.world_state()
                grip = host.sim.robots[ROBOT].grippers["link6"]
                assert grip.held == "w:black-box"
                assert grip.attached is None and not grip.saved_contacts
            relations = [r for r in scene["relations"] if r["a"]["entity"] == "w:black-box"]
            assert any(r["type"] == "attached" and r["mode"] == "contact" and r["derived"]
                       and r["b"]["entity"] == ROBOT for r in relations)
            assert not any(r["type"] == "supported-by" and r["b"]["entity"] == source for r in relations)

        pick = finish(start("pick", {"target": box}))
        assert pick["state"] == "succeeded", pick
        verify_contact_lift("w:table")
        place = start("place", {"on": left})
        support, world_support = left, "w:left-tray"
        if interruption in ("revise-during-transfer", "drop-during-transfer"):
            initial = link.proprioception()["joints"]["joint1"]
            _pump_until(host, lambda: link.proprioception()["joints"]["joint1"] - initial > 0.06)
        if interruption == "drop-during-transfer":
            with host.sim.lock:
                sim = host.sim
                finger_bodies = {sim.model.body(f"{ROBOT}/{finger}").id for finger in ("link7", "link8")}
                contact_bodies = finger_bodies | {sim._root_body("w:black-box")}
                for geom_id, body_id in enumerate(sim.model.geom_bodyid):
                    if body_id in contact_bodies:
                        sim.model.geom_friction[geom_id] = 0
            event = _pump_until(host, lambda: next(
                (event for event in runtime.monitor.events(0)["events"] if event["kind"] == "carried-object-lost"),
                None))
            assert event["holdAccepted"] and event["job"] == place["id"]
            assert finish(place)["state"] == "stopped"
            assert _pump_until(host, lambda: link.motion_status()["state"] == "held")
            assert not link.proprioception()["gripper"]["holding"]
            assert link.proprioception()["gripper"]["state"] == "closed"
            return
        if interruption == "revise-during-transfer":
            update = runtime.revise_intent(owner, "改放到右盘")
            assert update["interrupted"]["stopped"] == place["id"]
            assert finish(place)["state"] == "stopped"
            assert link.proprioception()["gripper"]["holding"]
            with pytest.raises(PermissionError, match="intent"):
                runtime.run(place["receipt"], owner, revision)
            held = _pump_until(host, lambda: status if (status := link.motion_status())["state"] == "held" else None)
            runtime.resume_motion(held["epoch"], owner)
            revision = update["intentRevision"]
            support, world_support = right, "w:right-tray"
            place = start("place", {"on": support})
        verify(finish(place), support, world_support)
        if interruption == "revise-during-transfer":
            assert not call("POST", score_url, {"entity": "w:black-box", "support": "w:left-tray"})["complete"]
        if interruption == "re-pick-from-tray":
            revision = runtime.revise_intent(owner, "把左盘上的黑盒拿起来放到右盘中央")["intentRevision"]
            pick = finish(start("pick", {"target": box}))
            assert pick["state"] == "succeeded", pick
            verify_contact_lift("w:left-tray")
            verify(finish(start("place", {"on": right})), right, "w:right-tray")
    finally:
        runtime.stop()
        host.stop()
