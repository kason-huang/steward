"""The fixed PiPER from PhyAgentOS's robot-control-sim skill."""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

import mujoco
import numpy as np

from steward.assets.generators import TEMPLATE_ID
from steward.math3d import quat_conj, quat_from_matrix, quat_mul
from steward.scene.schema import Entity, Joint, Link, RigidTree, exact, pose, primitive

BODY_ID = "phyagent-piper"
ARM_JOINTS = tuple(f"joint{i}" for i in range(1, 7))
WRIST_LINK = "link6"
WRIST_CAMERA = "wrist_cam"
WORKSPACE_CAMERA = "workspace_rgbd"
_LOCAL_MODEL = (Path(__file__).resolve().parents[3] / "var/phyagentos/robot-control-sim/0.1.3"
                / "assets/robots/piper/mjcf/PIPER.xml")
_MOUNT_TO_CAMERA = quat_from_matrix(np.array([[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))


def model_path() -> Path:
    configured = os.environ.get("STEWARD_PIPER_MJCF")
    if not configured:
        if _LOCAL_MODEL.is_file():
            return _LOCAL_MODEL
        raise ValueError("set STEWARD_PIPER_MJCF to the PhyAgentOS skill's piper/mjcf/PIPER.xml")
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"PiPER MJCF does not exist: {path}")
    return path


def _workspace_camera() -> dict:
    location = np.array([0.58, -0.47, 0.47])
    forward = np.array([0.30, 0.0, 0.02]) - location
    forward /= np.linalg.norm(forward)
    left = np.cross([0.0, 0.0, 1.0], forward)
    left /= np.linalg.norm(left)
    mount = pose(location, quat_from_matrix(np.column_stack((forward, left, np.cross(forward, left)))))
    return {"id": WORKSPACE_CAMERA, "kind": "rgbd", "mount": mount,
            "intrinsics": {"width": 640, "height": 480, "fovy": 56.0}}


@lru_cache(maxsize=2)
def _model(path: Path) -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_path(str(path))


def piper_entity() -> Entity:
    """Describe the native arm in robot-side frames for Ego-State and planning."""
    model = _model(model_path())
    links = []
    joints = []
    actuated = set(int(j) for j in model.actuator_trnid[:, 0])
    wrist = model.camera(WRIST_CAMERA).id
    wrist_mount = pose(model.cam_pos[wrist], quat_mul(model.cam_quat[wrist], quat_conj(_MOUNT_TO_CAMERA)))
    w, h = (int(v) for v in model.cam_resolution[wrist])
    # The camera's physical focal length determines this same vertical field of view in MuJoCo.
    height, focal_y = model.cam_sensorsize[wrist, 1], model.cam_intrinsic[wrist, 1]
    fovy = math.degrees(2 * math.atan(height / (2 * focal_y))) if focal_y > 0 and height > 0 else model.cam_fovy[wrist]
    wrist_camera = {"id": WRIST_CAMERA, "kind": "rgbd", "mount": wrist_mount,
                    "intrinsics": {"width": w, "height": h, "fovy": float(fovy)}}
    for bid in range(1, model.nbody):
        name = model.body(bid).name
        bounds = []
        for g in range(model.body_geomadr[bid], model.body_geomadr[bid] + model.body_geomnum[bid]):
            if model.geom_contype[g] or model.geom_conaffinity[g]:
                # SceneState keeps a compact envelope for perception. MuJoCo uses the native geom.
                bounds.append(primitive("box", 2 * model.geom_aabb[g, 3:],
                                        pose(model.geom_pos[g], model.geom_quat[g])))
        link = Link(id=name, name=name, geometry={"collision": bounds},
                    inertial=exact({"mass": float(model.body_mass[bid])}))
        if name == "base_link":
            link.sensors.append(_workspace_camera())
        if bid == model.cam_bodyid[wrist]:
            link.sensors.append(wrist_camera)
        if name == WRIST_LINK:
            tcp = pose(model.body_pos[model.body("link7").id])
            link.affordances = [exact("grasp-point"), exact({"tcp-offset": tcp})]
        links.append(link)
        if model.body_jntnum[bid]:
            jid = model.body_jntadr[bid]
            joint = model.joint(jid).name
            kind = "revolute" if model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE else "prismatic"
            lo, hi = (float(v) for v in model.jnt_range[jid])
            position = float(model.qpos0[model.jnt_qposadr[jid]])
            if joint == "joint7":
                position = hi
            elif joint == "joint8":
                position = lo
            joints.append(Joint(id=joint, parent=model.body(model.body_parentid[bid]).name, child=name,
                                type=exact(kind), origin=exact(pose(model.body_pos[bid], model.body_quat[bid])),
                                q=exact([position]), qd=exact([0.0]), axis=exact(model.jnt_axis[jid].tolist()),
                                limits=exact({"lo": lo, "hi": hi}),
                                actuator={"kind": "position"} if jid in actuated else None))
    return Entity(id=TEMPLATE_ID, role="robot", category=exact(BODY_ID), pose=exact(pose()),
                  body=RigidTree(root=exact("fixed"), links=links, joints=joints),
                  state_vars={"gripper": exact("open")}, asset={"id": BODY_ID})


def native_actuators() -> dict[str, str]:
    """Map measured joint ids to native actuator names, including the geared finger."""
    model = _model(model_path())
    return {model.joint(joint).name: model.actuator(a).name
            for a, (joint, _) in enumerate(model.actuator_trnid)}
