"""Inspect pinned LIBERO task metadata and verify the original environment renders."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import imageio
import numpy as np
from libero.libero import benchmark, get_libero_path
from main import LIBERO_DUMMY_ACTION, LIBERO_ENV_RESOLUTION, _get_libero_env
from evaluate import policy_image, save_model_details

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=False)
suite = benchmark.get_benchmark_dict()["libero_object"]()
name = "pick_up_the_alphabet_soup_and_place_it_in_the_basket"
tasks = [{"id": i, "name": suite.get_task(i).name, "language": suite.get_task(i).language}
         for i in range(suite.n_tasks)]
task_id, = (task["id"] for task in tasks if task["name"] == name)
task = suite.get_task(task_id)
bddl = Path(get_libero_path("bddl_files")) / task.problem_folder / task.bddl_file
init_path = Path(get_libero_path("init_states")) / task.problem_folder / task.init_states_file
states = suite.get_task_init_states(task_id)
data = {
    "task_order_index": 0, "target_task_id": task_id, "tasks": tasks,
    "initial_state_count": len(states),
    "assets": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [bddl, init_path]},
    "versions": {name: importlib.metadata.version(name) for name in ["mujoco", "robosuite", "numpy", "torch", "imageio"]},
}
(output / "task-inventory.json").write_text(json.dumps(data, indent=2) + "\n")
env, _ = _get_libero_env(task, LIBERO_ENV_RESOLUTION, 7)
try:
    env.reset()
    obs = env.set_init_state(states[0])
    for _ in range(10):
        obs, _, _, _ = env.step(LIBERO_DUMMY_ACTION)
    assert not env.check_success()
    save_model_details(env, output / "simulator.json")
    for name in ("agentview", "robot0_eye_in_hand"):
        image = policy_image(obs[name + "_image"])
        assert image.shape == (224, 224, 3) and image.dtype == np.uint8
        imageio.imwrite(output / (name + ".png"), image)
finally:
    env.close()
print(json.dumps(data), flush=True)
