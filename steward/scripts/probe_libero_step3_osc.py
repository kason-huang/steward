"""Isolate compiled Panda and OSC from WSim transport for model/physics diagnosis."""

import argparse
import gzip
import json
from pathlib import Path

import mujoco
import numpy as np

from steward.world.panda_osc import PandaOsc, SUBSTEPS


def restore(model, data, initial):
    for name, joint in initial["joints"].items():
        data.joint(name).qpos[:] = joint["qpos"]
        data.joint(name).qvel[:] = joint["qvel"]
    data.ctrl[:] = initial["ctrl"]
    data.time = initial["time"]
    mujoco.mj_forward(model, data)


def replay(model, trial, actuator_names):
    data = mujoco.MjData(model)
    restore(model, data, trial["initial"])
    osc = PandaOsc(model, data, trial["initial"])
    actuator_ids = [model.actuator(name).id for name in actuator_names]
    cycles = []
    for source in trial["cycles"]:
        action = np.array(source["action"])
        controls = []
        for index in range(SUBSTEPS):
            mujoco.mj_forward(model, data)
            osc.apply(action, update_goal=index == 0)
            controls.append(data.ctrl[actuator_ids].tolist())
            mujoco.mj_step(model, data)
        cycles.append({
            "label": source["label"],
            "action": source["action"],
            "time": data.time,
            "site_position": data.site_xpos[osc.site].tolist(),
            "site_rotation": data.site_xmat[osc.site].reshape(3, 3).tolist(),
            "goal_position": osc.goal_pos.tolist(),
            "goal_rotation": osc.goal_ori.tolist(),
            "grip_target": osc.grip.tolist(),
            "joints": {
                name: {"qpos": data.joint(name).qpos.tolist(), "qvel": data.joint(name).qvel.tolist()}
                for name in trial["initial"]["joints"]
            },
            "controls": controls,
        })
    return cycles


def angle(a, b):
    relative = np.asarray(a) @ np.asarray(b).T
    return float(np.arccos(np.clip((np.trace(relative) - 1) / 2, -1, 1)))


def max_errors(trial, cycles):
    errors = []
    for source, target in zip(trial["cycles"], cycles, strict=True):
        expected = source["state"]
        errors.append({
            "label": source["label"],
            "site_m": float(np.linalg.norm(np.subtract(expected["site_position"], target["site_position"]))),
            "site_rad": angle(expected["site_rotation"], target["site_rotation"]),
            "goal_m": float(np.linalg.norm(np.subtract(expected["goal_position"], target["goal_position"]))),
            "goal_rad": angle(expected["goal_rotation"], target["goal_rotation"]),
            "arm_torque": float(np.abs(np.subtract([s["ctrl"][:7] for s in source["substeps"]],
                                                   np.asarray(target["controls"])[:, :7])).max()),
            "finger_ctrl": float(np.abs(np.subtract([s["ctrl"][7:] for s in source["substeps"]],
                                                    np.asarray(target["controls"])[:, 7:])).max()),
        })
    return {
        "first_cycle": errors[0],
        "max": {key: max(row[key] for row in errors) for key in errors[0] if key != "label"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--start-after-warmup", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    model = mujoco.MjModel.from_xml_path(str(args.baseline / "panda.xml"))
    metadata = json.loads((args.baseline / "model.json").read_text())
    summary = {}
    for index in range(5):
        stem = f"init-{index}-repeat"
        with gzip.open(args.baseline / f"{stem}-0.json.gz", "rt") as stream:
            trial = json.load(stream)
        with gzip.open(args.baseline / f"{stem}-1.json.gz", "rt") as stream:
            repeat = json.load(stream)
        identical = repeat == trial
        if args.start_after_warmup:
            warmup = json.loads((args.baseline / "protocol.json").read_text())["warmup_cycles"]
            trial = {"initial": trial["cycles"][warmup - 1]["state"], "cycles": trial["cycles"][warmup:]}
        cycles = replay(model, trial, metadata["actuators"])
        with gzip.open(args.out / f"init-{index}.json.gz", "wt") as stream:
            json.dump(cycles, stream, separators=(",", ":"))
        summary[index] = {"repeat_identical": identical, **max_errors(trial, cycles)}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
