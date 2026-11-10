"""Export the pinned LIBERO task for Steward's Step 2 physics checks."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
from libero.libero import benchmark, get_libero_path
from main import LIBERO_DUMMY_ACTION, _get_libero_env

OBJECTS = ("alphabet_soup_1", "basket_1", "salad_dressing_1", "cream_cheese_1", "milk_1", "tomato_sauce_1", "butter_1")
FLOOR = "floor"


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def snapshot(sim):
    bodies = {}
    for name in OBJECTS:
        qpos = sim.data.get_joint_qpos(name + "_joint0")
        qvel = sim.data.get_joint_qvel(name + "_joint0")
        bodies[name] = {"qpos": qpos.tolist(), "qvel": qvel.tolist()}
    return bodies


def geom_properties(model, names):
    result = {}
    for name in names:
        index = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if index < 0:
            raise ValueError(f"Unknown geometry {name}")
        result[name] = {
            prop: getattr(model, "geom_" + prop)[index].tolist()
            for prop in ("type", "size", "pos", "quat", "friction", "solref", "solimp", "contype", "conaffinity", "condim", "group")
        }
    return result


def export_xml(source, out, variant, names, assets_root):
    root = copy.deepcopy(source)
    compiler = root.find("compiler")
    for attribute in ("assetdir", "meshdir", "texturedir"):
        compiler.attrib.pop(attribute, None)
    world = root.find("worldbody")
    retained = {FLOOR, *(name + "_main" for name in names)}
    for item in list(world):
        if item.tag == "body" and item.get("name") not in retained:
            world.remove(item)
        elif item.tag == "camera" and item.get("name") != "agentview":
            world.remove(item)
    world.find("camera").set("name", "overview")
    for tag in ("actuator", "sensor", "tendon", "equality", "contact", "keyframe"):
        section = root.find(tag)
        if section is not None:
            if tag in {"equality", "contact"} and len(section):
                raise ValueError(f"Unexpected explicit {tag} declarations")
            root.remove(section)
    asset = root.find("asset")
    for tag, attribute in (("mesh", "mesh"), ("material", "material"), ("texture", "texture")):
        used = {item.get(attribute) for item in root.iter() if item.get(attribute)}
        for item in list(asset.findall(tag)):
            if item.get("name") not in used:
                asset.remove(item)
    for item in asset:
        file = item.get("file")
        if file is not None:
            path = Path(file).resolve()
            relative = Path("assets") / path.relative_to(assets_root)
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(path, destination)
            item.set("file", relative.as_posix())
    tree = ET.ElementTree(root)
    tree.write(out / (variant + ".xml"), encoding="unicode")
    return [item.get("name") for item in world.iter("geom")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    suite = benchmark.get_benchmark_dict()["libero_object"]()
    task = suite.get_task(0)
    states = suite.get_task_init_states(0)

    source_repo = Path.cwd()
    libero_repo = source_repo / "third_party/libero"
    revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
                 for name, path in (("openpi", source_repo), ("libero", libero_repo))}
    assert revisions == {"openpi": "215abfb217dbac7d5f1273282331b9b1866c0479",
                         "libero": "f78abd68ee283de9f9be3c8f7e2a9ad60246e95c"}
    bddl = Path(get_libero_path("bddl_files")) / task.problem_folder / task.bddl_file
    shutil.copyfile(libero_repo / "LICENSE", out / "LIBERO-LICENSE")
    np.random.seed(7)
    env, _ = _get_libero_env(task, 256, 7)
    try:
        env.reset()
        source = ET.fromstring(env.sim.model.get_xml())
        # MuJoCo versions infer different inertias from the same collision/visual meshes.
        # The effective baseline inertial values are the contract for these free bodies.
        model = env.sim.model._model
        for name in OBJECTS:
            body = name + "_main"
            element = source.find(f"worldbody/body[@name='{body}']")
            index = env.sim.model.body_name2id(body)
            inertial = element.find("inertial")
            if inertial is None:
                inertial = ET.Element("inertial")
                element.insert(0, inertial)
            inertial.attrib.clear()
            inertial.attrib.update(mass=str(model.body_mass[index]),
                                   diaginertia=" ".join(map(str, model.body_inertia[index])),
                                   pos=" ".join(map(str, model.body_ipos[index])),
                                   quat=" ".join(map(str, model.body_iquat[index])))
        geometry = export_xml(source, out, "scene", OBJECTS, Path(get_libero_path("assets")).resolve())
        export_xml(source, out, "minimal", OBJECTS[:2], Path(get_libero_path("assets")).resolve())
        base = env.sim.model.body_name2id("robot0_base")
        reference = {
            "source_revisions": revisions,
            "bddl_sha256": hashlib.sha256(bddl.read_bytes()).hexdigest(),
            "bodies": {
                body: {
                    prop: getattr(env.sim.model._model, "body_" + prop)[env.sim.model.body_name2id(body)].tolist()
                    for prop in ("mass", "inertia", "ipos", "iquat")
                } for body in (FLOOR, *(name + "_main" for name in OBJECTS))
            },
            "mujoco": mujoco.__version__,
            "prompt": task.language,
            "physics_step": float(env.sim.model.opt.timestep),
            "target_region": {
                "body": "basket_1_main", "name": "basket_1_contain_region",
                **{key: getattr(model.site("basket_1_contain_region"), key).tolist()
                   for key in ("pos", "quat", "size")},
            },
            "panda_base": {"position": env.sim.data.body_xpos[base].tolist(), "quaternion": env.sim.data.body_xquat[base].tolist()},
            "geometries": geom_properties(env.sim.model._model, geometry),
            "options": {key: getattr(env.sim.model.opt, key).tolist() if hasattr(getattr(env.sim.model.opt, key), "tolist")
                        else getattr(env.sim.model.opt, key) for key in ("gravity", "timestep", "integrator", "solver", "iterations", "cone")},
        }
        initial = {}
        for index in range(5):
            env.reset()
            env.set_init_state(states[index])
            raw = snapshot(env.sim)
            for _ in range(10):
                env.step(LIBERO_DUMMY_ACTION)
            initial[index] = {
                "raw_sha256": hashlib.sha256(np.asarray(states[index]).tobytes()).hexdigest(),
                "reset": raw, "settled": snapshot(env.sim),
            }
        dump(out / "reference.json", reference)
        dump(out / "initial-states.json", initial)
        files = [out / name for name in ("scene.xml", "minimal.xml", "reference.json", "initial-states.json", "LIBERO-LICENSE")]
        files += sorted(path for path in (out / "assets").rglob("*") if path.is_file())
        (out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(out)}\n" for path in files))
    finally:
        env.close()


if __name__ == "__main__":
    main()
