"""XLeRobot MJCF with Steward's planar drive and one working jaw."""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import mujoco
import numpy as np

from steward.assets.generators import TEMPLATE_ID
from steward.math3d import matrix_from_quat, pose_compose, quat_from_axis_angle, quat_from_matrix
from steward.scene.schema import Entity, Joint, Link, RigidTree, exact, pose, primitive

BODY_ID = "xlerobot"
NATIVE_ROOT = "chassis"
ROOT_JOINTS = ("slide_joint_x", "slide_joint_y", "hinge_joint_z")
ROOT_CONTROLS = ("root_x", "root_y", "root_yaw")
ARM_JOINTS = ("Pitch_R", "Elbow_R", "Wrist_Pitch_R")
GRIPPER = "pinch_R"
FINGERS = ("Fixed_Jaw_2", "Moving_Jaw_2")
JAW = "Jaw_R"
JAW_OPEN = 1.15
STOW_LIFT = 0.82
STOW_REACH = 0.13
MODEL_REVISION = "0d60421c6cd8b16525695f32d2f8c5ba1329d45d"
DEFAULT_MODEL = (Path(__file__).resolve().parents[3] / "var/xlerobot/mujoco-gs-web"
                 / MODEL_REVISION / "xlerobot.xml")
MODEL_FILE = "steward-xlerobot.xml"
_CART = "raskog_cart"
_CART_PAN = {"Rotation_L": math.pi / 2, "Rotation_R": -math.pi / 2}


def model_path() -> Path:
    """Resolve the locally installed upstream MJCF and its sibling meshes."""
    path = Path(os.environ.get("STEWARD_XLEROBOT_MJCF", str(DEFAULT_MODEL))).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"XLeRobot MJCF missing at {path}; run scripts/install_xlerobot_assets.py "
                         "or set STEWARD_XLEROBOT_MJCF")
    return path


def _numbers(values) -> str:
    return " ".join(str(float(value)) for value in values)


def _camera(name: str, pitch: float, fovy: float, position: list[float]) -> dict:
    return {"id": name, "kind": "rgbd",
            "mount": pose(position, quat_from_axis_angle([0, 1, 0], math.radians(pitch))),
            "intrinsics": {"width": 640, "height": 480, "fovy": fovy}}


_MJ_CAMERA_AXES = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]])


def _cameras(full_cart: bool) -> list[dict]:
    position = [0.15, -0.002, 1.18115] if full_cart else [0.05, 0.0, 1.25]
    return [_camera("head_camera", 22.0, 58.0, position), _camera("wide_camera", 52.0, 76.0, position)]


def _add_cart_visuals(chassis: ET.Element) -> None:
    """Draw a simplified cart for the older PhyAgentOS model."""
    frame = "0.58 0.65 0.69 1"
    tray = "0.71 0.76 0.78 1"
    housing = "0.19 0.22 0.25 1"

    def box(name: str, pos: tuple, half: tuple, color: str) -> None:
        ET.SubElement(chassis, "geom", name=name, type="box", pos=_numbers(pos), size=_numbers(half),
                      rgba=color, group="2", contype="0", conaffinity="0", mass="0")

    for x, end in ((-0.18, "rear"), (0.135, "front")):
        for y, side in ((-0.174, "right"), (0.174, "left")):
            box(f"cart-upright-{end}-{side}", (x, y, 0.455), (0.013, 0.013, 0.35), frame)

    for name, z in (("lower", 0.112), ("middle", 0.437), ("upper", 0.797)):
        box(f"cart-{name}-tray", (-0.023, 0, z), (0.191, 0.192, 0.012), tray)
        for y, side in ((-0.184, "right"), (0.184, "left")):
            box(f"cart-{name}-rim-{side}", (-0.023, y, z + 0.015), (0.191, 0.008, 0.017), frame)
        box(f"cart-{name}-rim-rear", (-0.206, 0, z + 0.015), (0.008, 0.176, 0.017), frame)

    for y, side in ((-0.15, "right"), (0.15, "left")):
        box(f"cart-arm-mount-{side}", (0.134, y, 0.815), (0.034, 0.032, 0.010), housing)
    box("cart-camera-post", (-0.122, 0, 1.022), (0.019, 0.022, 0.219), frame)
    box("cart-camera-mount", (-0.072, 0, 1.217), (0.062, 0.021, 0.016), frame)
    box("cart-camera-housing", (-0.010, 0, 1.235), (0.042, 0.051, 0.026), housing)


def _fit_full_cart(xml: ET.Element, cart: ET.Element, actuators: ET.Element) -> ET.Element:
    """Put the native cart under position servos in Steward's floor-aligned base frame."""
    free = cart.find("freejoint")
    contact = xml.find("contact")
    if free is None or contact is None:
        raise ValueError("the MuJoCo-GS-Web XLeRobot requires its floating chassis and contact definitions")
    cart.remove(free)
    cart.set("name", _CART)
    # The native camera looks along -X at home. Steward drives along +X.
    cart.set("quat", "0 0 0 1")
    world = xml.find("worldbody")
    world.remove(cart)
    chassis = ET.SubElement(world, "body", name=NATIVE_ROOT)
    ET.SubElement(chassis, "inertial", pos="0 0 0", mass="0.001", diaginertia="0.000001 0.000001 0.000001")
    for joint, actuator, kind, axis in zip(ROOT_JOINTS, ROOT_CONTROLS,
                                          ("slide", "slide", "hinge"), ("1 0 0", "0 1 0", "0 0 1"), strict=True):
        ET.SubElement(chassis, "joint", name=joint, type=kind, axis=axis)
        ET.SubElement(actuators, "position", name=actuator, joint=joint, kp="2200", kv="190")
    chassis.append(cart)
    for exclusion in contact.findall("exclude"):
        for key in ("body1", "body2"):
            if exclusion.get(key) == NATIVE_ROOT:
                exclusion.set(key, _CART)
    for geom in cart.iter("geom"):
        if geom.get("class") != "visual":
            geom.set("rgba", "0 0 0 0")
    for site in cart.iter("site"):
        site.set("rgba", "0 0 0 0")
    return chassis


def _fit_legacy_cart(chassis: ET.Element) -> None:
    """Keep the PhyAgentOS mounts relative to Steward's floor-level chassis origin."""
    chassis_pos = np.fromstring(chassis.attrib["pos"], sep=" ")
    chassis.set("pos", "0 0 0")
    for child in chassis:
        if child.tag in ("body", "geom"):
            child.set("pos", _numbers(np.fromstring(child.get("pos", "0 0 0"), sep=" ") + chassis_pos))
    enclosure = chassis.find("geom")
    if enclosure is None:
        raise ValueError("the PhyAgentOS XLeRobot requires a chassis collision box")
    enclosure.set("rgba", "0 0 0 0")
    _add_cart_visuals(chassis)


@lru_cache(maxsize=2)
def _adapted_model(path: Path) -> tuple[bytes, mujoco.MjModel]:
    xml = ET.parse(path).getroot()
    compiler = xml.find("compiler")
    chassis = xml.find(f"./worldbody/body[@name='{NATIVE_ROOT}']")
    jaw = xml.find(f".//body[@name='{FINGERS[0]}']")
    actuators = xml.find("actuator")
    tendon = xml.find("tendon")
    if any(node is None for node in (compiler, chassis, jaw, actuators, tendon)):
        raise ValueError(f"{path} is missing elements of the XLeRobot model")
    compiler.set("meshdir", str(path.parent / compiler.get("meshdir", "")))
    xml.remove(tendon)
    full_cart = xml.find("./asset/mesh[@name='raskogbody']") is not None
    fixed = {"head_pan_joint", "head_tilt_joint"}
    if full_cart:
        chassis = _fit_full_cart(xml, chassis, actuators)
        fixed.update(("left_wheel_joint", "right_wheel_joint"))
    else:
        _fit_legacy_cart(chassis)
    for actuator in list(actuators):
        joint = actuator.get("joint")
        if joint is None or joint in fixed:
            actuators.remove(actuator)
            continue
        if joint in ROOT_JOINTS:
            actuator.tag = "position"
            actuator.attrib.update(name=ROOT_CONTROLS[ROOT_JOINTS.index(joint)], kp="2200", kv="190")
        elif actuator.tag == "velocity":
            fixed.add(joint)
            actuators.remove(actuator)
        elif full_cart:
            actuator.attrib.update(kp="300", kv="10")
        else:
            actuator.attrib.update(kp="300", dampratio="1")
    for body in chassis.iter("body"):
        for joint in body.findall("joint"):
            if joint.get("name") in fixed:
                body.remove(joint)
    for joint in chassis.findall("joint"):
        joint.attrib.update(damping="0", armature="0")
    # Place the TCP between the native tips for a 40 mm load, with the fixed finger on the -X side.
    pinch_axes = np.array([[-1, 0, 0], [0, 0, -1], [0, -1, 0]])
    ET.SubElement(jaw, "body", name=GRIPPER, pos="-0.007 -0.093 0",
                  quat=_numbers(quat_from_matrix(pinch_axes)))
    for sensor in _cameras(full_cart):
        mount = sensor["mount"]
        axes = matrix_from_quat(mount["q"]) @ _MJ_CAMERA_AXES
        ET.SubElement(chassis, "camera", name=sensor["id"],
                      pos=_numbers(mount["p"]),
                      quat=_numbers(quat_from_matrix(axes)), fovy=str(sensor["intrinsics"]["fovy"]))
    payload = ET.tostring(xml)
    return payload, mujoco.MjModel.from_xml_string(payload.decode())


def native_model_xml() -> bytes:
    """Return the attachable MJCF with absolute upstream mesh paths."""
    return _adapted_model(model_path())[0]


def native_actuators() -> dict[str, str]:
    """Return the native position actuator name for each measured joint."""
    model = _adapted_model(model_path())[1]
    return {model.joint(j).name: model.actuator(i).name for i, (j, _) in enumerate(model.actuator_trnid)}


def _geometry(model: mujoco.MjModel, body: int) -> list[dict]:
    return [
        primitive("box", 2 * model.geom_aabb[g, 3:],
                  pose_compose(pose(model.geom_pos[g], model.geom_quat[g]), pose(model.geom_aabb[g, :3])))
        for g in range(model.body_geomadr[body], model.body_geomadr[body] + model.body_geomnum[body])
        if model.geom_contype[g] or model.geom_conaffinity[g]
    ]


def _cart_footprint(model: mujoco.MjModel) -> dict:
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    bodies = {model.body(name).id for name in (_CART, "left_wheel", "right_wheel")}
    geoms = [gid for gid in range(model.ngeom) if model.geom_bodyid[gid] in bodies
             and (model.geom_contype[gid] or model.geom_conaffinity[gid])]
    rotations = data.geom_xmat[geoms].reshape(-1, 3, 3)
    aabb = model.geom_aabb[geoms]
    centers = data.geom_xpos[geoms] + (rotations @ aabb[:, :3, None]).squeeze(-1)
    halves = (np.abs(rotations) @ aabb[:, 3:, None]).squeeze(-1)
    lo, hi = (centers - halves).min(axis=0), (centers + halves).max(axis=0)
    width, depth = 2 * np.maximum(abs(lo[:2]), abs(hi[:2]))
    return primitive("box", [width, depth, hi[2] - lo[2]], pose([0, 0, (lo[2] + hi[2]) / 2]))


def xlerobot_entity() -> Entity:
    """Describe the native links for perception, kinematics and position targets."""
    from steward.ego.xlerobot_arm import XLeRobotArm

    model = _adapted_model(model_path())[1]
    full_cart = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, _CART) >= 0
    names = ["world", "base", *(model.body(i).name for i in range(2, model.nbody))]
    actuated = native_actuators()
    links, joints = [], []
    for i, name in enumerate(names[1:], 1):
        link = Link(id=name, name=name, geometry={"collision": _geometry(model, i)},
                    inertial=exact({"mass": float(model.body_mass[i])}))
        links.append(link)
        if i == 1:
            if full_cart:
                link.geometry["collision"] = [_cart_footprint(model)]
            link.sensors = _cameras(full_cart)
            continue
        if name == GRIPPER:
            closed = float(model.joint(JAW).range[0])
            link.affordances = [exact("grasp-point"), exact({"finger-targets": {JAW: [closed, JAW_OPEN]}})]
        parent = int(model.body_parentid[i])
        origin = pose(model.body_pos[i], model.body_quat[i])
        if not model.body_jntnum[i]:
            joints.append(Joint(id=f"{name}-mount", parent=names[parent], child=name,
                                type=exact("fixed"), origin=exact(origin), q=exact([]), qd=exact([])))
            continue
        joint = model.joint(model.body_jntadr[i])
        ref = float(model.qpos0[joint.qposadr[0]])
        initial = _CART_PAN.get(joint.name, ref) if full_cart else ref
        axis, anchor = joint.axis, joint.pos
        unref = quat_from_axis_angle(axis, -ref)
        origin = pose_compose(origin, pose(anchor - matrix_from_quat(unref) @ anchor, unref))
        joints.append(Joint(id=joint.name, parent=names[parent], child=name, type=exact("revolute"),
                            origin=exact(origin), anchor=exact(anchor.tolist()), axis=exact(axis.tolist()),
                            q=exact([initial]), qd=exact([0.0]),
                            limits=exact(dict(zip(("lo", "hi"), joint.range.tolist(), strict=True))),
                            actuator={"kind": "position"} if joint.name in actuated else None))
    entity = Entity(id=TEMPLATE_ID, role="robot", category=exact(BODY_ID), pose=exact(pose()),
                    body=RigidTree(root=exact("planar"), links=links, joints=joints),
                    state_vars={"gripper": exact("open")}, asset={"id": BODY_ID})
    arm = XLeRobotArm(entity.to_dict())
    stow = arm.command({"lift": STOW_LIFT, "reach": STOW_REACH})["joints"]
    parked = {**stow, JAW: JAW_OPEN, **{name[:-1] + "L": value for name, value in stow.items()}}
    for joint in joints:
        if joint.id in parked:
            joint.q = exact([parked[joint.id]])
    return entity
