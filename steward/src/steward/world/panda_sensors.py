"""Synchronised camera and encoder observations for the fixed Panda rig."""

from __future__ import annotations

import base64
import hashlib
import json
import time

import mujoco
import numpy as np

from steward.glrender import encode_image
from steward.world.panda_osc import ARM_JOINTS

CAMERAS = {"external": "overview", "wrist": "robot0_eye_in_hand"}
FINGERS = ("gripper0_finger_joint1", "gripper0_finger_joint2")
SIZE = 256


def pose(position, quaternion):
    return {"p": position.tolist(), "q": quaternion.tolist()}


def chain(model, body_id):
    """Describe only the fixed mounts and robot joints on the requested path."""
    nodes = []
    while body_id:
        body = model.body(body_id)
        node = {"origin": pose(body.pos, body.quat)}
        count = int(body.jntnum[0])
        if count:
            if count != 1:
                raise ValueError("Panda rig expects at most one joint per link")
            joint = model.joint(int(body.jntadr[0]))
            if joint.name not in (*ARM_JOINTS, *FINGERS) or np.any(joint.pos) or model.qpos0[joint.qposadr[0]]:
                raise ValueError(f"Unsupported Panda rig joint {joint.name}")
            node.update({"joint": joint.name, "axis": joint.axis.tolist(), "range": joint.range.tolist(),
                         "type": "prismatic" if joint.type[0] == mujoco.mjtJoint.mjJNT_SLIDE else "revolute"})
        nodes.append(node)
        body_id = int(body.parentid[0])
    return list(reversed(nodes))


def calibration(model):
    cameras = {}
    for role, name in CAMERAS.items():
        camera = model.camera(name)
        if camera.mode[0] != 0:
            raise ValueError(f"Panda observation camera {name} must use a fixed mount")
        focal = SIZE / (2 * np.tan(np.deg2rad(camera.fovy[0]) / 2))
        center = (SIZE - 1) / 2
        cameras[role] = {
            "width": SIZE, "height": SIZE,
            "K": [[focal, 0, center], [0, focal, center], [0, 0, 1]],
            "chain": chain(model, int(camera.bodyid[0])),
            "mount": pose(camera.pos, camera.quat),
        }
    tool = model.site("gripper0_grip_site")
    hand = model.body("robot0_right_hand")
    collision = []
    visual = []
    for index in range(model.ngeom):
        geom = model.geom(index)
        body = model.body(int(geom.bodyid[0]))
        if body.name.startswith(("robot0_", "gripper0_")):
            geometry = {"chain": chain(model, body.id), "mount": pose(geom.pos, geom.quat),
                        "bounds": model.geom_aabb[index].tolist()}
            visual.append(geometry)
            if geom.contype[0] or geom.conaffinity[0]:
                collision.append(geometry)
    return {"cameras": cameras, "hand": {"chain": chain(model, hand.id),
                                          "mount": {"p": [0, 0, 0], "q": [1, 0, 0, 0]}}, "tool": {"chain": chain(model, int(tool.bodyid[0])),
                                         "mount": pose(tool.pos, tool.quat)},
            "selfCollision": collision, "selfVisual": visual,
            "cameraAxes": "mujoco_x_right_y_up_minus_z_forward"}


def read_panda(worker, sim, after=None):
    """Copy one physics state under lock, then render both images on the camera worker."""
    with sim.lock:
        sim.panda.require_active()
        if after is not None and (sim.episode, sim.step_count) <= after:
            return {"unchanged": True, "episodeId": sim.episode}
        if worker.source is not sim.model:
            worker.snapshot_model(sim.model)
        mujoco.mj_copyData(worker.data, worker.model, sim.data)
        options = sim.render_options()
        generation = sim.panda.generation
        captured_at = time.monotonic()
        packet = {"episodeId": sim.episode, "step": sim.step_count, "stamp": sim.time}
        packet["encoders"] = {
            name: {"position": float(sim.data.joint(name).qpos[0]),
                   "velocity": float(sim.data.joint(name).qvel[0])}
            for name in (*ARM_JOINTS, *FINGERS)
        }
    rig = calibration(worker.model)
    packet["calibration"] = rig
    packet["calibrationId"] = hashlib.sha256(json.dumps(rig, sort_keys=True).encode()).hexdigest()
    mujoco.mj_forward(worker.model, worker.data)
    renderer = worker.renderer_for(SIZE, SIZE)
    # MSAA depth at mesh silhouettes can represent no physical surface.
    # Keep policy RGB unchanged and measure depth in its own non-MSAA context.
    depth_renderer = worker.renderer_for(SIZE, SIZE, samples=0)
    packet["images"] = {}
    packet["depthMm"] = {}
    for role, name in CAMERAS.items():
        renderer.update_scene(worker.data, camera=name, scene_option=options)
        packet["images"][role] = base64.b64encode(encode_image(renderer.render(), "png")).decode("ascii")
        depth_renderer.update_scene(worker.data, camera=name, scene_option=options)
        depth_renderer.enable_depth_rendering()
        try:
            millimetres = np.clip(np.rint(depth_renderer.render() * 1000), 0, 65535).astype(np.uint16)
        finally:
            depth_renderer.disable_depth_rendering()
        packet["depthMm"][role] = base64.b64encode(encode_image(millimetres, "png")).decode("ascii")
    with sim.lock:
        if sim.episode == packet["episodeId"] and all(packet["images"].values()):
            token = sim.panda.issue_policy_observation(generation, packet["step"], captured_at)
            if token is not None:
                packet["observationId"] = token
    return packet
