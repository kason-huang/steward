"""Native LIBERO fixed-protocol capture or bounded pi05 rollout (pinned Python 3.8 client)."""

import argparse
import base64
from contextlib import ExitStack
import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import time

import mujoco
import numpy as np
from PIL import Image
from openpi_client import image_tools

from main import _quat2axisangle
from record_libero_step3 import PINS, LIBERO_DUMMY_ACTION, _get_libero_env, benchmark, snapshot
from libero_physical_evidence import PhysicalEvidence
from step8_capture import Capture, png


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def payload(obs, prompt):
    fields = {"prompt": prompt, "observation/state": np.concatenate((obs["robot0_eef_pos"],
        _quat2axisangle(obs["robot0_eef_quat"].copy()), obs["robot0_gripper_qpos"])).tolist()}
    for key, source in (("observation/image", "agentview_image"), ("observation/wrist_image", "robot0_eye_in_hand_image")):
        fields[key] = png(image_tools.resize_with_pad(np.ascontiguousarray(obs[source][::-1, ::-1]), 224, 224))
    return fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--initial-index", type=int, choices=range(5), default=0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--port", type=int, default=18068)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--expected-initial", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    for name, path in (("openpi", Path.cwd()), ("libero", Path.cwd() / "third_party/libero")):
        assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PINS[name]
    protocol = json.loads(args.protocol.read_text()) if args.protocol else None
    index = protocol["initial_index"] if protocol else args.initial_index
    suite = benchmark.get_benchmark_dict()["libero_object"]()
    np.random.seed(args.seed)
    env, prompt = _get_libero_env(suite.get_task(0), 256, args.seed)
    calibration = json.loads(args.calibration.read_text())
    began = time.monotonic()
    actions_applied = 0
    reason = "execution_budget"
    error = None
    stopped = False
    try:
        env.reset()
        obs = env.set_init_state(suite.get_task_init_states(0)[index])
        for _ in range(10):
            obs, _, _, _ = env.step(LIBERO_DUMMY_ACTION)
        model, data = env.sim.model._model, env.sim.data._data
        if abs(float(model.opt.timestep)-.002) > 1e-12:
            raise ValueError("Native physics timestep differs from frozen 2 ms")
        initial = snapshot(env, env.env.robots[0])
        dump(args.out / "initial.json", initial)
        expected = json.loads(args.expected_initial.read_text())
        differences = {name: max(float(np.max(np.abs(np.asarray(values[field]) -
                       np.asarray(expected["joints"][name][field])))) for field in ("qpos", "qvel"))
                       for name, values in initial["joints"].items()}
        matches = (set(initial["joints"]) == set(expected["joints"]) and max(differences.values()) <= 1e-8)
        dump(args.out / "initial-comparison.json", {"matches": matches, "maximumJointDifference": differences})
        if not matches:
            dump(args.out / "result.json", {"initialStateMatches": False, "reason": "initial_state_mismatch"})
            return 2
        dump(args.out / "metadata.json", {"environment": "libero", "initialIndex": index,
            "seed": args.seed, "prompt": prompt, "mujoco": mujoco.__version__, "python": sys.version,
            "policyRng": "fresh pinned server default; not controlled by client seed",
            "sources": PINS, "mode": "fixed" if protocol else "probe" if args.probe else "policy"})
        with gzip.open(args.out / "physics.jsonl.gz", "wt") as physics:
            evidence = PhysicalEvidence(model, data, physics)
            native_step = env.sim.step
            steps = [0]
            still = [0]

            def step():
                native_step()
                steps[0] += 1
                evidence.capture(steps[0])
                arm = max(abs(data.joint("robot0_joint" + str(i)).qvel[0]) for i in range(1, 8))
                fingers = max(abs(data.joint("gripper0_finger_joint" + str(i)).qvel[0]) for i in (1, 2))
                speed = np.empty(6)
                mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_SITE,
                                        model.site("gripper0_grip_site").id, speed, 0)
                quiet = arm <= .02 and fingers <= .005 and np.linalg.norm(speed[3:]) <= .01 and np.linalg.norm(speed[:3]) <= .05
                still[0] = still[0] + 1 if quiet else 0

            env.sim.step = step
            camera = Capture(model, calibration, args.out, args.ffmpeg,
                             {"external": "agentview", "wrist": "robot0_eye_in_hand"})
            try:
                camera.record(data, 0, "initial")
                if args.probe:
                    dump(args.out / "policy-preview.json", payload(obs, prompt))
                with (args.out / "actions.jsonl").open("w", buffering=1) as log:
                    def apply(phase, action, deadline=None):
                        nonlocal obs, actions_applied
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError("Execution wall deadline reached before action")
                        raw = list(map(float, action))
                        values = np.clip(raw, -1, 1).tolist()
                        start = steps[0]
                        obs, _, _, _ = env.step(values)
                        if steps[0] - start != 25:
                            raise ValueError("Native control cycle differs from frozen 25 substeps")
                        log.write(json.dumps({"sequence": actions_applied, "phase": phase, "raw": raw,
                                              "applied": values, "endStep": steps[0]}) + "\n")
                        actions_applied += 1
                        camera.record(data, steps[0], phase, sensors=(actions_applied % 2 == 0),deadline=deadline)

                    if protocol:
                        for phase, action in protocol["actions"]:
                            apply(phase, action)
                        reason = "fixed_protocol_complete"
                    elif not args.probe:
                        policy_began = time.monotonic()
                        execution_deadline = policy_began + 420
                        try:
                            for prefix in range(56):
                                if time.monotonic() - policy_began >= 420:
                                    reason = "execution_timeout"
                                    break
                                folder = args.out / ("prefix-%03d" % prefix)
                                folder.mkdir()
                                dump(folder / "observation.json", payload(obs, prompt))
                                command = [sys.executable, str(Path(__file__).with_name("openpi_step6_infer.py")),
                                           str(args.port), str(folder / "observation.json"), str(folder / "inference.json")]
                                with (folder / "client.log").open("w") as client:
                                    remaining = execution_deadline-time.monotonic()
                                    if remaining <= 0:
                                        raise TimeoutError("Execution wall deadline reached before inference")
                                    subprocess.run(command, stdout=client, stderr=subprocess.STDOUT,
                                                   timeout=min(15,remaining), check=True)
                                values = np.asarray(json.loads((folder / "inference.json").read_text())["actions"])
                                if values.shape != (10, 7) or not np.isfinite(values).all():
                                    raise ValueError("Invalid policy prefix")
                                for action in values[:5]:
                                    apply("policy", action, execution_deadline)
                        except subprocess.TimeoutExpired as exc:
                            reason = "execution_timeout" if time.monotonic() >= execution_deadline else "inference_timeout"
                            error = str(exc)
                        except TimeoutError as exc:
                            reason, error = "execution_timeout", str(exc)
                        except Exception as exc:
                            reason, error = "inference_error", str(exc)
                    # Stop uses the robot's own joint velocities; no task GT controls this loop.
                    stop_deadline = time.monotonic() + 20
                    for cycle in range(0 if args.probe else 100):
                        if time.monotonic() >= stop_deadline:
                            break
                        obs, _, _, _ = env.step([0.] * 7)
                        camera.record(data, steps[0], "stopping", sensors=True,deadline=stop_deadline)
                        if cycle >= 19 and still[0] >= 25:
                            stopped = True
                            break
                # The final capture is always a saved fresh sensor frame.
                camera.record(data, steps[0], "final", sensors=True)
            finally:
                camera.close()
            world = evidence.summary()
        dump(args.out / "result.json", {"world": world, "initialStateMatches": True,
             "reason": reason, "error": error, "actionsApplied": actions_applied,
             "stopStatus": "not_applicable" if args.probe else "confirmed" if stopped else "unknown",
             "stillSteps": still[0], "wallSeconds": time.monotonic() - began})
        print(json.dumps({"physicalSuccess": world["success"], "actionsApplied": actions_applied,
                          "reason": reason, "stopStatus": "confirmed" if stopped else "unknown"}), flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
