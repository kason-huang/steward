"""Prepared MJCF and World State metadata for the pinned Step 2 LIBERO scene."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from steward.scene.mjcf import EntityNames, MjcfBuild
from steward.scene.schema import Entity, Link, RigidTree, SceneState, exact, pose

SCENARIO = "libero-object-alphabet-soup"
OBJECTS = ("alphabet_soup_1", "basket_1", "salad_dressing_1", "cream_cheese_1", "milk_1", "tomato_sauce_1", "butter_1")


def scenario_name(index: int) -> str:
    """Return the stable catalog name for one fixed initial state."""
    if type(index) is not int or not 0 <= index < 5:
        raise ValueError("LIBERO initial state must be an integer from 0 to 4")
    return SCENARIO if index == 0 else f"{SCENARIO}-init-{index}"


SCENARIO_NAMES = frozenset(scenario_name(index) for index in range(5))

def load_scene(bundle: Path, index: int, *, minimal: bool = False) -> tuple[dict, MjcfBuild]:
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
    xml = ET.fromstring(read("minimal.xml" if minimal else "scene.xml"))
    visual = xml.find("visual")
    if visual is None:
        visual = ET.SubElement(xml, "visual")
    framebuffer = visual.find("global")
    if framebuffer is None:
        framebuffer = ET.SubElement(visual, "global")
    # Match the standard WSim framebuffer for console views; this changes no physics option.
    for key, minimum in (("offwidth", 1280), ("offheight", 960)):
        framebuffer.set(key, str(max(int(framebuffer.get(key, "0")), minimum)))
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
    return scene.to_dict(), MjcfBuild(ET.tostring(xml, encoding="unicode"), names, qpos,
                                     assets=assets, shadows=True, initial_qvel=qvel)
