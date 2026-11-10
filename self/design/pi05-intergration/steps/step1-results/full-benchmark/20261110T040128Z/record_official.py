"""Run pinned upstream LIBERO with unique videos and one JSON record per rollout."""
import datetime
import json
import hashlib
import os
from pathlib import Path
import time
import sys
from types import ModuleType

import main as upstream
import numpy as np
import tyro


def record(args, task_id, episode_idx, task, steps, done, success, duration, video, initial_state):
    row = {
        "suite": args.task_suite_name,
        "task_id": task_id,
        "task_name": task.name,
        "language": task.language,
        "initial_state_index": episode_idx,
        "initial_state_sha256": hashlib.sha256(np.asarray(initial_state).tobytes()).hexdigest(),
        "seed": args.seed,
        "policy_steps": steps,
        "official_done": bool(done),
        "libero_success": bool(success),
        "wall_seconds": duration,
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "video": video.name,
    }
    with Path(os.environ["PI05_EPISODES_JSONL"]).open("a") as stream:
        stream.write(json.dumps(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def instrument(source):
    changes = [
        ("            env.reset()", "            episode_start = time.monotonic()\n            env.reset()"),
        ("            replay_images = []", "            replay_images = []\n            policy_steps = 0\n            done = False"),
        ('                        action_chunk = client.infer(element)["actions"]', '                        action_chunk = client.infer(element)["actions"]\n                        if episode_idx < 2 and policy_steps == 0:\n                            logging.info("First block task=%d init=%d state=%s first=%s",\n                                         task_id, episode_idx, element["observation/state"].tolist(),\n                                         action_chunk[0].tolist())\n                        if not np.isfinite(action_chunk).all():\n                            raise FloatingPointError("Policy returned nonfinite actions")'),
        ("                    if done:", "                    policy_steps += 1\n                    if done:"),
        ('                    logging.error(f"Caught exception: {e}")\n                    break', "                    raise RuntimeError(f'Task {task_id}, initial state {episode_idx}') from e"),
        ('            imageio.mimwrite(\n                pathlib.Path(args.video_out_path) / f"rollout_{task_segment}_{suffix}.mp4",', '''            video = pathlib.Path(args.video_out_path) / f"task-{task_id:02d}_init-{episode_idx:02d}_{suffix}.mp4"
            imageio.mimwrite(
                video,'''),
        ('            logging.info(f"Success: {done}")', '''            record(args, task_id, episode_idx, task, policy_steps, done, env.check_success(),
                   time.monotonic() - episode_start, video, initial_states[episode_idx])
            logging.info(f"Success: {done}")'''),
        ('        logging.info(f"Current task success rate:', '        env.close()\n        logging.info(f"Current task success rate:'),
    ]
    for old, new in changes:
        if source.count(old) != 1:
            raise RuntimeError(f"Pinned runner changed at {old!r}")
        source = source.replace(old, new)
    return source


if __name__ == "__main__":
    upstream_file = Path(upstream.__file__)
    module = ModuleType("instrumented_upstream")
    module.__file__ = str(upstream_file)
    sys.modules[module.__name__] = module
    namespace = vars(module)
    namespace.update(record=record, time=time)
    exec(compile(instrument(upstream_file.read_text()), str(upstream_file), "exec"), namespace)
    upstream.logging.basicConfig(level=upstream.logging.INFO)
    tyro.cli(namespace["eval_libero"])
