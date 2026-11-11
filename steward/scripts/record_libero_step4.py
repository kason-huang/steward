"""Tune or replay the Step 4 pick and place in pinned LIBERO."""

import argparse
from contextlib import ExitStack
import gzip
import json
from pathlib import Path
import subprocess

import imageio
import mujoco
import numpy as np

from record_libero_step3 import PINS, LIBERO_DUMMY_ACTION, _get_libero_env, benchmark, dump, snapshot

from libero_step4_evidence import CAN, CASES, SITE, sample, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--initial-index", type=int, choices=range(5), default=0)
    parser.add_argument("--case", choices=CASES, default="normal")
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text()) if args.protocol else None
    index = protocol["initial_index"] if protocol else args.initial_index
    case = protocol.get("case", "normal") if protocol else args.case
    for name, path in (("openpi", Path.cwd()), ("libero", Path.cwd() / "third_party/libero")):
        assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PINS[name]
    args.out.mkdir(parents=True)
    np.random.seed(7)
    suite = benchmark.get_benchmark_dict()["libero_object"]()
    env, _ = _get_libero_env(suite.get_task(0), 256, 7)
    try:
        env.reset()
        env.set_init_state(suite.get_task_init_states(0)[index])
        for _ in range(10):
            env.step(LIBERO_DUMMY_ACTION)
        model, data = env.sim.model._model, env.sim.data._data
        initial = snapshot(env, env.env.robots[0])
        commands, trace, physics = [], [], []
        native_step = env.sim.step

        def step():
            native_step()
            physics.append({"step": len(physics) + 1, **sample(model, data)})

        env.sim.step = step
        with ExitStack() as stack:
            writer = stack.enter_context(imageio.get_writer(args.out / "libero.mp4", fps=20)) if args.video else None

            def cycle(label, action):
                action = list(map(float, action))
                obs, _, _, _ = env.step(action)
                commands.append([label, action])
                assert len(physics) == 25 * len(commands)
                trace.append({"cycle": len(commands), "phase": label, "action": action,
                              "success": bool(env.check_success()), "control": snapshot(env, env.env.robots[0]),
                              **physics[-1]})
                if writer is not None:
                    frame = np.concatenate((obs["agentview_image"][::-1, ::-1],
                                            obs["robot0_eye_in_hand_image"][::-1, ::-1]), axis=1)
                    writer.append_data(np.ascontiguousarray(frame))

            if protocol:
                for label, action in protocol["actions"]:
                    cycle(label, action)
            else:
                start = data.body(CAN).xpos.copy()
                basket = data.site("basket_1_contain_region").xpos.copy()
                height = 0.22 if case == "empty" else 0.048
                if case == "offset":
                    start[1] += 0.04
                if case == "outside":
                    basket[0] -= 0.21
                stages = [
                    ("approach", [start[0], start[1], 0.22], -1, 65),
                    ("descend", [start[0], start[1], height], -1, 65),
                    ("close", [start[0], start[1], height], 1, 1 if case == "weak" else 20),
                    ("lift", [start[0], start[1], 0.25], 0, 70),
                    ("hover", [start[0], start[1], 0.25], 0, 12),
                    ("carry", [basket[0], basket[1], 0.25], 0, 130),
                    ("lower", [basket[0], basket[1], 0.115], 0, 55),
                    ("release", [basket[0], basket[1], 0.115], -1, 18),
                    ("withdraw", [basket[0], basket[1], 0.25], -1, 50),
                    ("settle", [basket[0], basket[1], 0.25], 0, 25),
                ]
                if case == "rim":
                    # Carry to the near rim, then lower the held can across its upper edge.
                    rim_y = basket[1] - 0.073
                    stages[5:7] = [
                        ("carry", [basket[0], rim_y, 0.25], 0, 130),
                        ("lower", [basket[0], rim_y, 0.14], 0, 55),
                    ]
                    stages[7:] = [
                        ("release", [basket[0], rim_y, 0.14], -1, 18),
                        ("withdraw", [basket[0], rim_y, 0.25], -1, 50),
                        ("settle", [basket[0], rim_y, 0.25], 0, 25),
                    ]
                for label, target, grip, count in stages:
                    for _ in range(count):
                        delta = np.clip((np.asarray(target) - data.site(SITE).xpos) * 18, -0.65, 0.65)
                        cycle(label, [*delta, 0, 0, 0, grip])
                    row = trace[-1]
                    print(label, np.round(row["tool"], 3), np.round(row["can"], 3), row["success"],
                          [(a, b, round(f, 2)) for a, b, f, _ in row["contacts"] if f > 0], flush=True)
        recorded = {"seed": 7, "initial_index": index, "warmup_cycles": 10,
                    "sources": PINS, "actions": commands}
        if case != "normal":
            recorded["case"] = case
        dump(args.out / "protocol.json", recorded)
        dump(args.out / "initial.json", initial)
        with gzip.open(args.out / "libero-trace.json.gz", "wt") as stream:
            json.dump(trace, stream, separators=(",", ":"))
        with gzip.open(args.out / "libero-physics.json.gz", "wt") as stream:
            json.dump(physics, stream, separators=(",", ":"))
        result = summarize(trace, physics, initial["joints"]["alphabet_soup_1_joint0"]["qpos"][2], case)
        dump(args.out / "libero-summary.json", result)
        print("cycles:", len(commands), result, flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
