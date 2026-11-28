"""XLeRobot encoder calibration and Phase 0 kitchen transport."""

from __future__ import annotations

import math
import os
import threading
import time
from unittest.mock import Mock

import mujoco
import pytest

from steward.assets.generators import TEMPLATE_ID
from steward.assets.xlerobot import DEFAULT_MODEL, GRIPPER
from steward.ego.bodies import prepare_body
from steward.ego.kinematics import _link_pose
from steward.ego.skills import Job, SkillExecutor, SkillFailed
from steward.ego.xlerobot_arm import XLeRobotArm, XLeRobotMotionPolicy
from steward.math3d import pose_compose, quat_from_yaw
from steward.scene.mjcf import build_mjcf
from steward.scene.schema import Entity, Link, RigidTree, SceneState, exact, pose, primitive

ROBOT = "w:robot-1"
OBJECT = "w:bottle-1"
DESTINATION = "w:cabinet-1"


def test_xlerobot_reports_the_measured_dock_without_moving_or_releasing(monkeypatch):
    from steward.ego.bodies import runtime_config

    state = {"stamp": 8.0, "odometry": {"x": 0.012, "y": 0.0, "yaw": 0.0}, "toolOffset": 0.12,
             "gripper": {"holding": True}}
    arm = Mock(spec=XLeRobotArm)
    arm.reachable.side_effect = lambda lift, reach, **kwargs: 0.08 <= reach <= 0.355
    policy = XLeRobotMotionPolicy(arm)
    command, translate, recovery = Mock(), Mock(), Mock()
    driver = SkillExecutor(command, lambda: state, lambda eid: eid, config=runtime_config("xlerobot").motion)
    monkeypatch.setattr(driver, "_translate_to", translate)
    monkeypatch.setattr(driver, "_safe_retract", recovery)
    plan = {"object": "e:load", "reach": 0.35, "approachLift": 0.855,
            "dock": [0.0, 0.0], "yaw": 0.0, "dockReachMarginM": 0.010}

    def place(job, plan):
        driver._arm_engaged = True
        policy.approach(driver, plan, [0.47, 0.0], 0.83)

    monkeypatch.setattr(driver, "_place", place)
    job = Job("j", "place", "r")
    driver._run(job, "place", {"plan": plan})

    result = job.to_dict()
    assert result["state"] == "failed"
    assert result["postcondition"] == {"reason": "dock-outside-receipt", "measuredBasePose": state["odometry"],
                                       "releaseExecuted": False, "nextAction": "refresh-observations-and-replan"}
    assert state["gripper"]["holding"]
    command.assert_not_called()
    translate.assert_not_called()
    recovery.assert_not_called()


@pytest.mark.parametrize("drop_during", ["dock", "settle_before_slow_open"])
def test_place_does_not_open_or_record_a_release_after_losing_the_load(monkeypatch, drop_during):
    from steward.ego.bodies import runtime_config

    state = {"stamp": 8.0, "gripper": {"holding": True}, "joints": {}}
    release = Mock()
    command = Mock()
    policy = XLeRobotMotionPolicy(Mock(spec=XLeRobotArm))
    grip = {"holding": True}
    executor = SkillExecutor(command, lambda: state, lambda eid: eid, on_release=release,
                             config=runtime_config("xlerobot").motion, motion_policy=policy,
                             read_place_grip=lambda: grip["holding"])
    job = Job("j", "place", "r")

    def dock(job, plan):
        assert job.expects_grip
        if drop_during == "dock":
            grip["holding"] = False

    def wait(seconds):
        if drop_during == "settle_before_slow_open" and seconds == 0.2:
            grip["holding"] = False

    monkeypatch.setattr(executor, "_dock", dock)
    monkeypatch.setattr(executor, "_wait", wait)
    monkeypatch.setattr(executor, "_over_support", lambda plan: True)
    for name in ("_align_lateral", "_restore_planned_reach", "_reach_over", "_settle_for_release"):
        monkeypatch.setattr(executor, name, lambda *args: None)
    move_jaw = Mock()
    monkeypatch.setattr(executor, "_move_joints", move_jaw)
    plan = {"object": "e:apple", "support": "e:plate", "spot": [0.0, 0.0], "lift": 0.8, "slowJawRelease": True}

    with pytest.raises(SkillFailed, match="load was lost before release"):
        executor._place(job, plan)
    move_jaw.assert_not_called()
    command.assert_not_called()
    release.assert_not_called()
    assert "releasedAt" not in plan


@pytest.mark.skipif(not DEFAULT_MODEL.is_file(), reason="requires the MuJoCo-GS-Web XLeRobot assets")
def test_the_world_model_shows_the_upstream_cart_and_head_meshes(monkeypatch):
    monkeypatch.delenv("STEWARD_XLEROBOT_MJCF", raising=False)
    entity = Entity.from_dict(prepare_body("xlerobot").model)
    entity.id = ROBOT
    built = build_mjcf(SceneState(mode="world", entities=[entity]))
    model = built.compile()
    for mesh in ("raskogbody", "raskogwheel1", "raskogwheel2", "tophead6"):
        mesh_id = model.mesh(f"{ROBOT}/{mesh}").id
        geoms = [gid for gid in range(model.ngeom) if model.geom_type[gid] == mujoco.mjtGeom.mjGEOM_MESH
                 and model.geom_dataid[gid] == mesh_id]
        assert any(model.geom_group[gid] == 2 and model.geom_rgba[gid, 3] > 0.9 for gid in geoms), mesh


@pytest.mark.skipif(not DEFAULT_MODEL.is_file() and not os.environ.get("STEWARD_XLEROBOT_MJCF"),
                    reason="requires the XLeRobot MJCF and meshes")
def test_encoder_kinematics_match_the_native_gripper():
    setup = prepare_body("xlerobot")
    entity = Entity.from_dict(setup.model)
    entity.id = ROBOT
    entity.pose = exact(pose([0.5, -0.4, 0.0], quat_from_yaw(0.8)))
    built = build_mjcf(SceneState(mode="world", entities=[entity]))
    model = built.compile()
    data = mujoco.MjData(model)
    built.apply_initial(model, data)
    names = built.names[ROBOT]
    chassis = data.body(names.bodies["base"])
    assert chassis.xpos == pytest.approx(entity.pose.value["p"], abs=1e-9)
    for sensor in entity.rigid.link("base").sensors:
        mount = pose_compose(entity.pose.value, sensor["mount"])
        assert data.camera(names.cameras[sensor["id"]]).xpos == pytest.approx(mount["p"], abs=1e-9)
    encoders = {joint.id: joint.q.value[0] for joint in entity.rigid.joints if joint.q.value}
    for lift, reach, pitch in [(0.82, 0.13, 0), (0.95, 0.23, 0.95), (0.77, 0.20, 0.35)]:
        encoders.update(setup.arm.command({"lift": lift, "reach": reach, "wrist_pitch": pitch})["joints"])
        for key, value in encoders.items():
            data.joint(names.joints[key]).qpos[0] = value
        mujoco.mj_forward(model, data)
        gripper = data.body(names.bodies[GRIPPER])
        measured = pose_compose(entity.pose.value, _link_pose(setup.model, encoders, GRIPPER))
        assert gripper.xpos == pytest.approx(measured["p"], abs=1e-6)
        assert setup.arm.joints_of(encoders) == pytest.approx(
            {"lift": lift, "reach": reach, "wrist_pitch": pitch, "wrist_yaw": 0, "wrist_roll": 0}, abs=1e-5)


@pytest.mark.skipif(not DEFAULT_MODEL.is_file(), reason="requires the MuJoCo-GS-Web XLeRobot assets")
def test_direct_pose_reports_measured_tcp_and_requires_task_control(tmp_path):
    from steward.authoring.service import AuthoringService, build_default_kitchen
    from steward.ego.runtime import EgoRuntime, RobotLink
    from steward.ego.server import ego_api
    from steward.math3d import quat_angle_between, quat_from_matrix
    from steward.net import HttpError, call
    from steward.world.server import StewardHost

    build_default_kitchen(AuthoringService(tmp_path))
    host = StewardHost(tmp_path, scenario="phase0-kitchen", body="xlerobot", phase="phase0").start()
    setup = prepare_body("xlerobot")
    link = RobotLink(host.robot_servers[ROBOT].url)
    runtime = EgoRuntime(link, setup.model, setup.config, arm=setup.arm)
    api = ego_api(runtime).start()
    try:
        target = {"position": [0.27, setup.arm.geometry.lateral, 0.85],
                  "orientation": quat_from_matrix(setup.arm.tool_rotation(0, 0, 0.20))}
        route = api.url + "/ego/body/command"
        available = call("GET", api.url + "/ego/plan")["availableS0Tools"]
        assert available["body_set_pose"] == available["body_set_gripper"] == "control"
        assert "body_set_joints" not in available
        capabilities = call("GET", api.url + "/ego/control")["capabilities"]
        assert capabilities["s0Tools"] == available
        assert capabilities["arms"][0]["poseMode"] == "planar"
        assert capabilities["tcp"]["orientationOrder"] == "wxyz"
        assert host.sim.status()["robots"][ROBOT]["graspMode"] == "contact"
        with pytest.raises(HttpError, match="active, controlled"):
            call("POST", route, {"pose": target})
        runtime.acquire_control("operator")
        scope = {"owner": "operator", "taskId": "xlerobot-direct-pose"}
        with pytest.raises(HttpError, match="cannot reach"):
            call("POST", route, {**scope, "pose": {**target, "position": [1.0, *target["position"][1:]]}})
        with pytest.raises(HttpError, match="fore-aft plane"):
            call("POST", route, {**scope, "pose": {**target, "position": [0.27, 0.0, 0.85]}})
        with pytest.raises(HttpError, match="finite"):
            call("POST", route, {**scope, "pose": {**target, "position": [True, *target["position"][1:]]}})
        with pytest.raises(HttpError, match="unit quaternion"):
            call("POST", route, {**scope, "pose": {**target, "orientation": [0, 0, 0, 0]}})
        moved = call("POST", route, {**scope, "pose": target}, timeout=9)
        assert moved["state"] == "reached"
        assert set(moved) == {"id", "state", "pose", "gripper", "stamp"}
        assert math.dist(moved["pose"]["position"], target["position"]) <= capabilities["tcp"]["positionToleranceM"]
        assert quat_angle_between(moved["pose"]["orientation"], target["orientation"]) <= (
            capabilities["tcp"]["orientationToleranceRad"])
        measured = call("GET", api.url + "/ego/gripper/pose")["tcp_pose"]
        assert measured["position"] == pytest.approx(moved["pose"]["position"], abs=0.008)
        closed = call("POST", route, {**scope, "gripper": "close"}, timeout=9)
        assert closed["gripper"]["state"] == "closed" and not closed["gripper"]["holding"]
        opened = call("POST", route, {**scope, "gripper": "open"}, timeout=9)
        assert opened["gripper"]["state"] == "open"
        runtime.stop_motion(task_id=scope["taskId"])
        with pytest.raises(HttpError, match="task has ended"):
            call("POST", route, {**scope, "pose": target})
    finally:
        api.stop()
        host.stop()


@pytest.mark.skipif(os.environ.get("STEWARD_TEST_XLEROBOT") != "1",
                    reason="set STEWARD_TEST_XLEROBOT=1 for the native kitchen transport")
@pytest.mark.parametrize(("category", "offset", "start_dock"), [
    pytest.param("pill_bottle", [-0.51, 0.10], [-0.45, -0.80], id="bottle-to-cabinet"),
    pytest.param("apple", [-0.29, 0.324], None, id="apple-from-north"),
    pytest.param("apple", [-0.42, -0.05], [-0.42, -0.95], id="apple-from-west"),
    pytest.param("orange", [-0.43, -0.015], None, id="orange-to-tray"),
])
def test_xlerobot_transports_physically_grasped_objects_in_the_kitchen(tmp_path, category, offset, start_dock):
    from steward.assets.generators import nesting_bowl
    from steward.authoring.service import AuthoringService, build_default_kitchen, kitchen_spec
    from steward.ego.runtime import EgoRuntime, RobotLink
    from steward.ego.server import ego_api
    from steward.net import call
    from steward.world.server import StewardHost

    authoring = AuthoringService(tmp_path)
    build_default_kitchen(authoring)
    scene = kitchen_spec()
    fruit = category != "pill_bottle"
    object_id = f"w:{category}-1" if fruit else OBJECT
    landing_category, landing_id = "plate", "w:plate-1"
    if category == "orange":
        landing_category, landing_id = "tray", "w:tray-1"
        tray = nesting_bowl(radius=0.12, height=0.035)
        tray.category = exact(landing_category)
        tray.rigid.links[0].affordances = [exact("support-surface")]
        authoring.library.publish(landing_category, tray.to_dict())
        landing_entity = next(ent for ent in scene["entities"] if ent["id"] == "w:plate-1")
        landing_entity.update(id=landing_id, asset=landing_category)
    if fruit:
        radius, color = (0.045, [0.78, 0.12, 0.1, 1]) if category == "apple" else (0.04, [0.9, 0.34, 0.07, 1])
        sphere = primitive("sphere", [radius], pose([0, 0, radius]), color)
        visual = [sphere]
        if category == "apple":
            visual.append(primitive("cylinder", [0.005, 0.025], pose([0, 0, 0.098]), [0.35, 0.22, 0.1, 1]))
        link = Link(id="body", name=category, geometry={"collision": [sphere], "visual": visual},
                    inertial=exact({"mass": 0.12}), material=exact({"friction": 0.7, "fragility": 0.3}),
                    affordances=[exact("graspable")])
        asset = Entity(id=TEMPLATE_ID, role="object", category=exact(category), pose=exact(pose()),
                       body=RigidTree(root=exact("free"), links=[link]))
        authoring.library.publish(category, asset.to_dict())
    scene["entities"].append({"id": object_id, "asset": category.replace("_", "-"), "on": "w:table-1", "offset": offset})
    authoring.compose_scene(scene)
    host = StewardHost(tmp_path, scenario="phase0-kitchen", body="xlerobot", phase="phase0",
                       speed=math.inf, seed=3, run_dir=tmp_path / "run").start()
    runtime = api = None
    errors = []

    def object_supports():
        return {(rel["b"]["entity"], rel["b"].get("link")) for rel in host.sim.world_state()["relations"]
                if rel["type"] == "supported-by" and rel["a"]["entity"] == object_id}

    try:
        setup = prepare_body("xlerobot")
        link = RobotLink(host.robot_servers[ROBOT].url)
        link.require_body("xlerobot")
        runtime = EgoRuntime(link, setup.model, setup.config, arm=setup.arm)
        runtime.start()
        api = ego_api(runtime).start()

        def observe(category, uncertainty=math.inf):
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                objects = call("GET", api.url + "/ego/summary")["objects"]
                match = next((o for o in objects if o["category"] == category), None)
                if match and match["uncertaintyM"] <= uncertainty:
                    return match
                time.sleep(0.1)
            pytest.fail(f"did not obtain a {category} track within {uncertainty} m: {match}, {runtime.last_error}")

        def skill(name, params, *, retry_dock=True):
            receipt = call("POST", api.url + "/ego/predict", {"skill": name, "params": params}, timeout=120)
            assert receipt["feasible"] and receipt["withinRiskLimit"], receipt["issues"]
            if name == "pick":
                assert receipt["plan"].get("fixedGraspHeight", False) == fruit
                if category == "apple" and start_dock is not None:
                    assert receipt["plan"]["wristPitch"] > 0
            if name == "place":
                assert receipt["plan"]["approachLift"] >= receipt["plan"]["lift"]
            job = call("POST", api.url + "/skill/run", {"receipt": receipt["id"]})
            done = call("POST", api.url + "/skill/wait", {"job": job["id"], "timeoutS": 150}, timeout=160)
            post = done.get("postcondition") or {}
            if name == "place" and retry_dock and post.get("reason") == "dock-outside-receipt":
                assert done["state"] == "failed" and post["releaseExecuted"] is False
                assert set(post["measuredBasePose"]) == {"x", "y", "yaw"}
                assert link.proprioception()["gripper"]["holding"]
                call("POST", api.url + "/ego/observe", {})
                return skill(name, params, retry_dock=False)
            assert done["state"] == "succeeded", (
                done, call("GET", api.url + "/ego/events")["events"][-4:], host.sim.events[-10:])
            return done

        def task():
            try:
                observe("table")
                assert ("w:table-1", None) in object_supports()
                initial_height = host.sim._entity_pose(object_id)["p"][2]
                if start_dock is not None:
                    skill("navigate", {"x": start_dock[0], "y": start_dock[1], "yaw": 0.0})
                    assert math.dist(host.sim._entity_pose(ROBOT)["p"][:2], start_dock) < 0.07
                target = observe(category, uncertainty=0.03)
                skill("pick", {"target": target["id"]})
                gripper = host.sim.robots[ROBOT].grippers[GRIPPER]
                assert gripper.attached is None and gripper.contact_entity == object_id
                assert host.sim._entity_pose(object_id)["p"][2] > initial_height + 0.005
                assert ("w:table-1", None) not in object_supports()
                if fruit:
                    destination = observe(landing_category, uncertainty=0.05)
                    placed = skill("place", {"on": destination["id"]})
                else:
                    skill("navigate", {"x": 1.65, "y": 0.6, "yaw": 0})
                    assert gripper.attached is None and gripper.contact_entity == object_id
                    assert host.sim._entity_pose(object_id)["p"][2] > 0.70
                    destination = observe("cabinet", uncertainty=0.05)
                    placed = skill("place", {"on": destination["id"], "offset": [0.17, 0.0], "closeRelease": True})
                link.wait_until(host.sim.time + (6.0 if fruit else 1.0))
                assert host.sim.robots[ROBOT].grippers[GRIPPER].held is None
                support = (landing_id, None) if fruit else (DESTINATION, "carcass")
                if fruit:
                    # A sphere may roll across the tray before the rim arrests it.
                    deadline = host.sim.time + (45.0 if landing_category == "tray" else 12.0)
                    speed = math.inf
                    with host.sim.lock:
                        body = host.sim.build.names[object_id].root_body
                    while support in object_supports():
                        with host.sim.lock:
                            speed = math.hypot(*host.sim.data.body(body).cvel[3:])
                        if speed < 0.005 or host.sim.time >= deadline:
                            break
                        link.wait_until(host.sim.time + 0.5)
                position = host.sim._entity_pose(object_id)["p"]
                observed_supports = object_supports()
                assert support in observed_supports, (position, observed_supports, placed)
                if fruit:
                    assert speed < 0.005
                else:
                    assert math.dist(position[:2], [2.53, 0.6]) < 0.07
                    assert position[2] == pytest.approx(0.8, abs=0.005)
                verdict = call("POST", api.url + "/ego/verify-placement", {
                    "job": placed["id"], "goal": {"entity": target["id"], "support": destination["id"]}})
                assert (verdict["effect"], verdict["intent"]) == ("satisfied", "satisfied"), repr(verdict)
                assert verdict["evidence"]["observation"]["source"] == "ideal_detection"
                assert runtime.last_error is None
                assert not any(w.number for w in host.sim.data.warning)
                assert all(event["type"] != "grasp/attach" for event in host.sim.events)
                print(f"{category} settled at {position} m on {support[0]}", flush=True)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=task)
        worker.start()
        while worker.is_alive():
            host.pump(0.02)
        worker.join()
        if errors:
            raise errors[0]
    finally:
        if api is not None:
            api.stop()
        if runtime is not None:
            runtime.stop()
        host.stop()
