"""Capture original LIBERO's policy inputs at selected frozen-action steps."""

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image

from main import LIBERO_DUMMY_ACTION, _get_libero_env, _quat2axisangle
from libero.libero import benchmark
from openpi_client import image_tools
from record_libero_step3 import PINS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    for name, path in (("openpi", Path.cwd()), ("libero", Path.cwd() / "third_party/libero")):
        assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PINS[name]
    protocol = json.loads(args.protocol.read_text())
    args.out.mkdir(parents=True)
    np.random.seed(7)
    suite = benchmark.get_benchmark_dict()["libero_object"]()
    env, prompt = _get_libero_env(suite.get_task(0), 256, 7)
    records = []
    try:
        env.reset()
        env.set_init_state(suite.get_task_init_states(0)[protocol["initial_index"]])
        for _ in range(protocol["warmup_cycles"]):
            env.step(LIBERO_DUMMY_ACTION)
        for index, (phase, action) in enumerate(protocol["actions"]):
            obs, _, _, _ = env.step(action)
            if index not in (0, 149, 231, 509):
                continue
            for role, key in (("external", "agentview_image"), ("wrist", "robot0_eye_in_hand_image")):
                pixels = np.ascontiguousarray(obs[key][::-1, ::-1])
                Image.fromarray(image_tools.resize_with_pad(pixels, 224, 224)).save(
                    args.out / f"{index:03}-{role}.png")
            records.append({
                "step": (index + 1) * 25, "phase": phase, "prompt": prompt,
                "state": np.concatenate((obs["robot0_eef_pos"], _quat2axisangle(obs["robot0_eef_quat"].copy()),
                                         obs["robot0_gripper_qpos"])).tolist(),
            })
    finally:
        env.close()
    (args.out / "reference.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
