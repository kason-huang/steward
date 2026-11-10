"""Record fixed OSC and PandaGripper actions in the pinned LIBERO environment."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import imageio
import mujoco
import numpy as np
import robosuite
from libero.libero import benchmark, get_libero_path
from main import LIBERO_DUMMY_ACTION, _get_libero_env

PINS = {
    "openpi": "215abfb217dbac7d5f1273282331b9b1866c0479",
    "libero": "f78abd68ee283de9f9be3c8f7e2a9ad60246e95c",
}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def actions():
    zero = [0.0] * 7
    sequence = []
    for axis in range(6):
        for sign in (1, -1):
            command = zero.copy()
            command[axis] = sign * 0.16
            sequence += [(f"axis-{axis}-{sign:+d}", command), ("hold", zero)]
    for grip, count in ((1, 5), (0, 3), (-1, 5), (0, 3)):
        sequence += [(f"grip-{grip:+d}", zero[:6] + [float(grip)])] * count
    sequence += [("mixed", [0.12, -0.08, 0.12, 0.04, 0.05, -0.09, 0.0])] * 6
    sequence += [("mixed-return", [-0.12, 0.08, -0.12, -0.04, -0.05, 0.09, 0.0])] * 6
    sequence += [("hold", zero)] * 3
    return sequence


def names(model, kind):
    count = {"joint": model.njnt, "actuator": model.nu}[kind]
    return [getattr(model, kind)(index).name for index in range(count)]


def snapshot(env, robot):
    sim = env.sim
    site = sim.data._data.site(robot.controller.eef_name)
    return {
        "time": float(sim.data.time),
        "site_position": site.xpos.tolist(),
        "site_rotation": site.xmat.reshape(3, 3).tolist(),
        "joints": {name: {"qpos": np.atleast_1d(sim.data.get_joint_qpos(name)).tolist(),
                          "qvel": np.atleast_1d(sim.data.get_joint_qvel(name)).tolist()}
                   for name in names(sim.model._model, "joint")},
        "ctrl": sim.data.ctrl.tolist(),
        "reference": robot.controller.initial_joint.tolist(),
        "grip_target": robot.gripper.current_action.tolist(),
        "goal_position": np.asarray(robot.controller.goal_pos).tolist(),
        "goal_rotation": np.asarray(robot.controller.goal_ori).tolist(),
    }


def export_assets(env, out):
    model = env.sim.model._model
    xml = ET.fromstring(env.sim.model.get_xml())
    compiler = xml.find("compiler")
    for attr in ("assetdir", "meshdir", "texturedir"):
        compiler.attrib.pop(attr, None)
    xml.find("worldbody/camera[@name='agentview']").set("name", "overview")
    for body in xml.findall(".//worldbody//body"):
        index = model.body(body.attrib["name"]).id
        if model.body_mass[index] <= 0 or not np.all(model.body_inertia[index] > 0):
            continue
        inertial = body.find("inertial")
        if inertial is None:
            inertial = ET.Element("inertial")
            body.insert(0, inertial)
        inertial.attrib.clear()
        inertial.attrib.update(
            mass=str(model.body_mass[index]),
            diaginertia=" ".join(map(str, model.body_inertia[index])),
            pos=" ".join(map(str, model.body_ipos[index])),
            quat=" ".join(map(str, model.body_iquat[index])),
        )
    roots = {"libero": Path(get_libero_path("assets")).resolve(),
             "robosuite": Path(robosuite.__file__).resolve().parent / "models" / "assets"}
    provenance = {}
    for element in xml.iter():
        if "file" not in element.attrib:
            continue
        file = Path(element.attrib["file"]).resolve()
        for origin, root in roots.items():
            try:
                relative = file.relative_to(root)
            except ValueError:
                continue
            target = Path("assets") / origin / relative
            (out / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, out / target)
            element.set("file", target.as_posix())
            provenance[target.as_posix()] = {"origin": origin, "path": relative.as_posix(),
                                             "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
            break
        else:
            raise ValueError(f"Unrecognized source asset {file}")
    ET.ElementTree(xml).write(out / "panda.xml", encoding="unicode")
    dump(out / "asset-provenance.json", provenance)
    shutil.copyfile(Path.cwd() / "third_party/libero/LICENSE", out / "LIBERO-LICENSE")
    import importlib.metadata

    dist = importlib.metadata.distribution("robosuite")
    license_file = next(f for f in dist.files if ".dist-info/" in str(f) and f.name == "LICENSE")
    shutil.copyfile(dist.locate_file(license_file), out / "robosuite-LICENSE")
    dump(out / "model.json", {
        "model_version": mujoco.__version__,
        "robosuite": dist.version,
        "physics_step": float(model.opt.timestep),
        "control_period": float(env.env.control_timestep),
        "actuators": names(model, "actuator"),
        "effective": {
            "joint": {model.joint(i).name: {
                field: np.asarray(getattr(model.joint(i), field)).tolist()
                for field in ("type", "axis", "limited", "range")
            } for i in range(model.njnt)},
            "actuator": {model.actuator(i).name: {
                **{field: np.asarray(getattr(model.actuator(i), field)).tolist()
                   for field in ("trntype", "gear", "ctrlrange", "ctrllimited", "forcerange", "forcelimited",
                                 "gaintype", "biastype", "gainprm", "biasprm")},
                "joint": model.joint(int(model.actuator(i).trnid[0])).name,
            }
                for i in range(model.nu)},
            "geom": {model.geom(i).name: {
                field: np.asarray(getattr(model.geom(i), field)).tolist()
                for field in ("contype", "conaffinity", "condim")
            } for i in range(model.ngeom)},
            "site": {"gripper0_grip_site": {
                field: np.asarray(getattr(model.site("gripper0_grip_site"), field)).tolist()
                for field in ("pos", "quat")
            }},
        },
        "joints": names(model, "joint"),
        "bodies": {
            model.body(i).name: {field: getattr(model, "body_" + field)[i].tolist()
                                 for field in ("mass", "inertia", "ipos", "iquat")}
            for i in range(1, model.nbody)
        },
        "source_revisions": PINS,
    })


def record_trial(env, initial, sequence, video=None):
    env.reset()
    observation = env.set_init_state(initial)
    robot = env.env.robots[0]
    raw = snapshot(env, robot)
    original = robot.control
    substeps = []

    def control(action, policy_step=False):
        original(action, policy_step=policy_step)
        substeps.append({"goal_update": bool(policy_step), "ctrl": env.sim.data.ctrl.tolist()})

    robot.control = control
    cycles = []
    try:
        for label, action in [("warmup", LIBERO_DUMMY_ACTION)] * 10 + sequence:
            substeps.clear()
            observation, _, _, _ = env.step(action)
            if video is not None:
                video.append_data(np.ascontiguousarray(observation["agentview_image"][::-1, ::-1]))
            if len(substeps) != 25 or sum(step["goal_update"] for step in substeps) != 1:
                raise RuntimeError(f"Unexpected controller cadence at {label}: {len(substeps)}")
            cycles.append({"label": label, "action": action, "state": snapshot(env, robot), "substeps": list(substeps)})
    finally:
        robot.control = original
    return {"initial": raw, "cycles": cycles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    for name, path in (("openpi", Path.cwd()), ("libero", Path.cwd() / "third_party/libero")):
        revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if revision != PINS[name]:
            raise RuntimeError(f"Unexpected {name} revision {revision}")
    out.mkdir(parents=True, exist_ok=False)
    suite = benchmark.get_benchmark_dict()["libero_object"]()
    task = suite.get_task(0)
    initial = suite.get_task_init_states(0)
    sequence = actions()
    dump(out / "protocol.json", {"seed": 7, "indices": list(range(5)), "warmup_cycles": 10,
                                 "repetitions": 2, "actions": sequence, "prompt": task.language})
    states = {}
    for index in range(5):
        for repetition in range(2):
            np.random.seed(7)
            env, _ = _get_libero_env(task, 256, 7)
            try:
                if index == repetition == 0:
                    export_assets(env, out)
                    with imageio.get_writer(out / "libero-init0.mp4", fps=20) as video:
                        record = record_trial(env, initial[index], sequence, video)
                else:
                    record = record_trial(env, initial[index], sequence)
                states[index] = record["initial"]
                path = out / f"init-{index}-repeat-{repetition}.json.gz"
                with gzip.open(path, "wt") as stream:
                    json.dump(record, stream, separators=(",", ":"))
                print("recorded", index, repetition, len(record["cycles"]), flush=True)
            finally:
                env.close()
    dump(out / "initial-states.json", states)
    files = sorted(path for path in out.rglob("*") if path.is_file())
    (out / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(out)}\n" for path in files))


if __name__ == "__main__":
    main()
