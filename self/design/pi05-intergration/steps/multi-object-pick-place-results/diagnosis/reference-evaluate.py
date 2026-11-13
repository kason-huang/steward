"""Record a single episode using the pinned OpenPI LIBERO runner's conventions."""
import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import sys
import time
import traceback

import imageio
import numpy as np
from libero.libero import benchmark, get_libero_path
from openpi_client import image_tools
from openpi_client.websocket_client_policy import WebsocketClientPolicy

from main import LIBERO_DUMMY_ACTION, LIBERO_ENV_RESOLUTION, _get_libero_env, _quat2axisangle


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_image(raw):
    image = np.ascontiguousarray(raw[::-1, ::-1])
    return image_tools.convert_to_uint8(image_tools.resize_with_pad(image, 224, 224))


def save_model_details(env, path):
    task = env.env
    sim = task.sim
    robot = task.robots[0]
    controller = robot.controller
    camera_details = {}
    for name in ("agentview", "robot0_eye_in_hand"):
        index = sim.model.camera_name2id(name)
        body = int(sim.model.cam_bodyid[index])
        camera_details[name] = {
            "body": sim.model.body_id2name(body),
            "local_position": sim.model.cam_pos[index].tolist(),
            "local_quaternion_wxyz": sim.model.cam_quat[index].tolist(),
            "vertical_fov_degrees": float(sim.model.cam_fovy[index]),
            "world_position_at_reset": sim.data.cam_xpos[index].tolist(),
            "world_rotation_at_reset": sim.data.cam_xmat[index].tolist(),
            "source_resolution": [LIBERO_ENV_RESOLUTION, LIBERO_ENV_RESOLUTION],
        }
    path.write_text(json.dumps({
        "robot": type(robot.robot_model).__name__,
        "gripper": type(robot.gripper).__name__,
        "controller": type(controller).__name__,
        "joint_names": list(robot.robot_model.joints),
        "initial_qpos": np.asarray(robot.init_qpos).tolist(),
        "input_min": np.asarray(controller.input_min).tolist(),
        "input_max": np.asarray(controller.input_max).tolist(),
        "output_min": np.asarray(controller.output_min).tolist(),
        "output_max": np.asarray(controller.output_max).tolist(),
        "control_hz": task.control_freq,
        "physics_step_seconds": sim.model.opt.timestep,
        "controller_uses_delta": getattr(controller, "use_delta", None),
        "controller_controls_orientation": getattr(controller, "use_ori", None),
        "controller_end_effector": getattr(controller, "eef_name", None),
        "gripper_joints": list(robot.gripper.joints),
        "gripper_dof": robot.gripper.dof,
        "rendering": "EGL",
        "cameras": camera_details,
    }, indent=2) + "\n")


def evaluate(args):
    suite = benchmark.get_benchmark_dict()["libero_object"](args.task_order_index)
    matches = [i for i in range(suite.n_tasks) if suite.get_task(i).name == args.task]
    if len(matches) != 1:
        raise ValueError("Expected exactly one benchmark task matching " + args.task)
    task_id = matches[0]
    if args.task_id is not None and args.task_id != task_id:
        raise ValueError(f"Task {args.task} is {task_id}, expected {args.task_id}")
    task = suite.get_task(task_id)
    states = suite.get_task_init_states(task_id)
    if not 0 <= args.init_index < len(states):
        raise ValueError(f"Initial state {args.init_index} outside [0, {len(states)})")
    np.random.seed(args.seed)
    bddl = Path(get_libero_path("bddl_files")) / task.problem_folder / task.bddl_file
    attempt = args.output / args.attempt
    attempt.mkdir(parents=True, exist_ok=False)
    inputs = attempt / "policy"
    inputs.mkdir()
    result = {
        "attempt": args.attempt, "started_utc": utc_now(), "human_intervention": False,
        "suite": "libero_object", "task_id": task_id, "task": task.name,
        "task_order_index": args.task_order_index, "prompt": task.language,
        "bddl": str(bddl), "bddl_sha256": sha256(bddl), "seed": args.seed,
        "init_index": args.init_index, "control_budget": 280, "warmup_steps": 10,
        "wall_timeout_seconds": args.timeout, "execution_prefix": 5,
        "control_steps": 0, "success_signal": False, "command": sys.argv,
        "outcome": "incomplete", "termination": "interrupted",
    }
    result_path = attempt / "result.json"

    def write_result():
        temp = result_path.with_suffix(".tmp")
        temp.write_text(json.dumps(result, indent=2) + "\n")
        temp.replace(result_path)

    write_result()
    env = video = None
    started = time.monotonic()

    def on_timeout(signum, frame):
        raise TimeoutError(f"Episode exceeded {args.timeout} seconds")

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(args.timeout)
    try:
        initial_state_path = attempt / "initial-state.npy"
        np.save(initial_state_path, np.asarray(states[args.init_index]))
        result["initial_state_sha256"] = sha256(initial_state_path)
        env, prompt = _get_libero_env(task, LIBERO_ENV_RESOLUTION, args.seed)
        env.reset()
        obs = env.set_init_state(states[args.init_index])
        save_model_details(env, attempt / "simulator.json")
        client = WebsocketClientPolicy("127.0.0.1", args.port)
        video = imageio.get_writer(str(attempt / "episode.mp4"), fps=10)
        video.append_data(policy_image(obs["agentview_image"]))
        action_plan = collections.deque()
        inference_index = 0

        with (attempt / "trajectory.jsonl").open("w", buffering=1) as trajectory:
            def step(action, phase, model_step=None):
                nonlocal obs
                obs, reward, done, _ = env.step(action)
                success = bool(env.check_success())
                video.append_data(policy_image(obs["agentview_image"]))
                trajectory.write(json.dumps({
                    "phase": phase, "model_step": model_step,
                    "sim_time": float(env.env.sim.data.time), "action": action,
                    "done": bool(done), "success_signal": success, "reward": float(reward),
                }) + "\n")
                return done, success

            for _ in range(10):
                step(LIBERO_DUMMY_ACTION, "warmup")
            for index in range(280):
                if not action_plan:
                    observation = {
                        "observation/image": policy_image(obs["agentview_image"]),
                        "observation/wrist_image": policy_image(obs["robot0_eye_in_hand_image"]),
                        "observation/state": np.concatenate((
                            obs["robot0_eef_pos"], _quat2axisangle(obs["robot0_eef_quat"]), obs["robot0_gripper_qpos"],
                        )),
                        "prompt": str(prompt),
                    }
                    stem = inputs / f"{inference_index:03d}"
                    np.savez_compressed(stem.with_suffix(".npz"), **observation)
                    if inference_index == 0:
                        imageio.imwrite(attempt / "agentview-input.png", observation["observation/image"])
                        imageio.imwrite(attempt / "wrist-input.png", observation["observation/wrist_image"])
                    begin = time.perf_counter()
                    response = client.infer(observation)
                    elapsed = time.perf_counter() - begin
                    actions = np.asarray(response["actions"])
                    np.save(stem.with_suffix(".npy"), actions)
                    trajectory.write(json.dumps({
                        "phase": "inference", "model_step": index, "inference": inference_index,
                        "sim_time": float(env.env.sim.data.time), "action_shape": list(actions.shape),
                        "latency_seconds": elapsed, "policy_timing": response.get("policy_timing"),
                        "server_timing": response.get("server_timing"),
                    }) + "\n")
                    if actions.shape != (10, 7) or not np.isfinite(actions).all():
                        raise ValueError(f"Unexpected policy action chunk: {actions.shape}")
                    action_plan.extend(actions[:5])
                    inference_index += 1
                done, success = step(action_plan.popleft().tolist(), "policy", index)
                result.update(control_steps=index + 1, success_signal=success)
                if done:
                    result.update(outcome="success" if success else "failure", termination="environment_done")
                    break
            else:
                result.update(outcome="failure", termination="control_budget")
    except Exception as error:
        result.update(outcome="timeout" if isinstance(error, TimeoutError) else "exception",
                      termination=type(error).__name__, error=str(error))
        (attempt / "exception.txt").write_text(traceback.format_exc())
    finally:
        signal.alarm(0)
        try:
            if video is not None:
                video.close()
        except Exception as error:
            result["video_error"] = str(error)
        result.update(ended_utc=utc_now(), wall_seconds=time.monotonic() - started)
        write_result()
        if env is not None:
            env.close()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--task-order-index", type=int, required=True)
    parser.add_argument("--init-index", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18005)
    parser.add_argument("--timeout", type=int, default=600)
    evaluate(parser.parse_args())
