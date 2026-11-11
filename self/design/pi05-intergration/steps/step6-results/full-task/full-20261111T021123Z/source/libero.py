"""Prepared MJCF and World State metadata for the pinned Step 2 LIBERO scene."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco

from steward.scene.mjcf import EntityNames, MjcfBuild
from steward.scene.schema import Entity, Link, RigidTree, SceneState, exact, pose

SCENARIO = "libero-object-alphabet-soup"
PANDA_BODY = "libero-panda"
PANDA_ID = "w:panda"
OBJECTS = ("alphabet_soup_1", "basket_1", "salad_dressing_1", "cream_cheese_1", "milk_1", "tomato_sauce_1", "butter_1")


def scenario_name(index: int, *, panda: bool = False) -> str:
    """Return the stable catalog name for one fixed initial state."""
    if type(index) is not int or not 0 <= index < 5:
        raise ValueError("LIBERO initial state must be an integer from 0 to 4")
    base = f"{SCENARIO}-panda" if panda else SCENARIO
    return base if index == 0 else f"{base}-init-{index}"


SCENARIO_NAMES = frozenset(scenario_name(index, panda=panda) for panda in (False, True) for index in range(5))

def load_scene(bundle: Path, index: int, *, minimal: bool = False, panda: bool = False) -> tuple[dict, MjcfBuild]:
    """Load checked source bytes and named initial state for a fixed-layout Scenario."""
    scenario_name(index)
    root = bundle.resolve()
    manifest = dict(line.split(maxsplit=1)[::-1] for line in (root / "SHA256SUMS").read_text().splitlines())

    def read(relative: str) -> bytes:
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"LIBERO asset escapes its bundle: {relative}")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest.get(relative):
            raise ValueError(f"LIBERO asset checksum mismatch: {relative}")
        return content

    objects = OBJECTS[:2] if minimal else OBJECTS
    if panda and minimal:
        raise ValueError("Panda requires the complete LIBERO scene")
    xml = ET.fromstring(read("panda.xml" if panda else "minimal.xml" if minimal else "scene.xml"))
    visual = xml.find("visual")
    if visual is None:
        visual = ET.SubElement(xml, "visual")
    framebuffer = visual.find("global")
    if framebuffer is None:
        framebuffer = ET.SubElement(visual, "global")
    # Match the standard WSim framebuffer for console views; this changes no physics option.
    for key, minimum in (("offwidth", 1280), ("offheight", 960)):
        framebuffer.set(key, str(max(int(framebuffer.get(key, "0")), minimum)))
    if panda:
        for name in ("gripper0_ft_frame", "gripper0_grip_site", "gripper0_grip_site_cylinder"):
            site = xml.find(f".//site[@name='{name}']")
            rgba = site.get("rgba").split()
            site.set("rgba", " ".join([*rgba[:3], "0"]))
    # The pinned LIBERO renderer predates PNG color-space detection in MuJoCo.
    if mujoco.mj_version() >= 333:
        for texture in xml.findall("asset/texture"):
            texture.set("colorspace", "linear")
    assets = {}
    for element in xml.iter():
        relative = element.get("file")
        if relative is None:
            continue
        if element.tag not in {"mesh", "texture"} or not relative.startswith("assets/"):
            raise ValueError(f"Unsupported LIBERO MJCF file reference: {relative}")
        basename = hashlib.sha256(relative.encode()).hexdigest()[:16] + Path(relative).suffix
        assets[basename] = read(relative)
        element.set("file", basename)

    initial = None
    if panda:
        protocol = json.loads(read("protocol.json"))
        trial = json.loads(gzip.decompress(read(f"init-{index}-repeat-0.json.gz")))
        initial = trial["cycles"][protocol["warmup_cycles"] - 1]["state"]
        state = {name: initial["joints"][name + "_joint0"] for name in objects}
        metadata = json.loads(read("model.json"))
        initial["ctrl_by_name"] = dict(zip(metadata["actuators"], initial["ctrl"], strict=True))
    else:
        state = json.loads(read("initial-states.json"))[str(index)]["reset"]
    worldbody = xml.find("worldbody")
    scene = SceneState("world")
    names: dict[str, EntityNames] = {}
    qpos: dict[str, list[float]] = {}
    qvel: dict[str, list[float]] = {}
    for name in ("floor", *objects):
        floor = name == "floor"
        body = name if floor else name + "_main"
        element = worldbody.find(f"body[@name='{body}']")
        if element is None:
            raise ValueError(f"Missing LIBERO body: {body}")
        eid = "w:" + name
        link = "floor" if floor else "root"
        entry = EntityNames(bodies={link: body})
        # Effective collision attributes come from the prepared model; this list is for entity lookup.
        entry.collision_geoms = [geom.attrib["name"] for geom in element.iter("geom")
                                 if geom.get("contype") != "0" or geom.get("conaffinity") != "0"]
        initial_pose = pose()
        if not floor:
            joint = name + "_joint0"
            values = state[name]
            positions, velocities = values["qpos"], values["qvel"]
            if len(positions) != 7 or len(velocities) != 6 or not all(map(math.isfinite, positions + velocities)):
                raise ValueError(f"Invalid LIBERO free joint state: {joint}")
            if element.find(f"joint[@name='{joint}'][@type='free']") is None:
                raise ValueError(f"Missing LIBERO free joint: {joint}")
            entry.root_joints.append(joint)
            qpos[joint], qvel[joint] = positions, velocities
            initial_pose = pose(positions[:3], positions[3:])
        scene.entities.append(Entity(eid, "environment" if floor else "object",
                                     exact(name if floor else name.rsplit("_", 1)[0]),
                                     exact(initial_pose), RigidTree(exact("fixed" if floor else "free"),
                                                                   [Link(link, body)])))
        names[eid] = entry
    if panda:
        base = worldbody.find("body[@name='robot0_base']")
        if base is None:
            raise ValueError("Missing Panda base")
        position = list(map(float, base.get("pos", "0 0 0").split()))
        quaternion = list(map(float, base.get("quat", "1 0 0 0").split()))
        if position != [-0.6, 0, 0] or quaternion != [1, 0, 0, 0]:
            raise ValueError("Unexpected Panda base pose")
        scene.entities.append(Entity(PANDA_ID, "robot", exact(PANDA_BODY),
                                     exact(pose(position, quaternion)),
                                     RigidTree(exact("fixed"), [Link("base", "Panda base")]),
                                     asset={"id": PANDA_BODY}))
        names[PANDA_ID] = EntityNames(
            bodies={"base": "robot0_base"} | {
                element.attrib["name"]: element.attrib["name"]
                for element in list(base.iter("body"))[1:]
            },
            joints={joint: joint for joint in initial["joints"]
                    if joint.startswith(("robot0_", "gripper0_"))},
            cameras={"wrist": "robot0_eye_in_hand"},
        )
        for joint, values in initial["joints"].items():
            if not all(map(math.isfinite, values["qpos"] + values["qvel"])):
                raise ValueError(f"Invalid Panda state: {joint}")
            qpos[joint], qvel[joint] = values["qpos"], values["qvel"]
    return scene.to_dict(), MjcfBuild(ET.tostring(xml, encoding="unicode"), names, qpos,
                                     assets=assets, shadows=True, initial_qvel=qvel, panda_initial=initial)
