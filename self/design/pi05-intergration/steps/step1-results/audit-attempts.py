"""Check saved Step 1 attempts against the frozen plan and trajectory evidence."""
import hashlib
import json
from pathlib import Path
import sys

import imageio
import numpy as np

run = Path(sys.argv[1])
plan = json.loads((run / "frozen-batch.json").read_text())
summary = json.loads((run / "summary.json").read_text())
attempts = sorted(run.glob("attempt-*/result.json"))
assert len(attempts) == plan["num_episodes"] == len(summary["attempts"])
successes = 0
for result_path, initial_index in zip(attempts, plan["init_indices"]):
    attempt = result_path.parent
    result = json.loads(result_path.read_text())
    assert result["init_index"] == initial_index and result["seed"] == plan["seed"]
    assert result["task"] == plan["task"] and result["suite"] == plan["suite"]
    assert result["outcome"] in {"success", "failure", "exception", "timeout"}
    assert result["human_intervention"] is False
    initial = attempt / "initial-state.npy"
    assert hashlib.sha256(initial.read_bytes()).hexdigest() == result["initial_state_sha256"]
    trace = [json.loads(line) for line in (attempt / "trajectory.jsonl").read_text().splitlines()]
    steps = [row for row in trace if row["phase"] == "policy"]
    queries = [row for row in trace if row["phase"] == "inference"]
    assert len(steps) == result["control_steps"] <= plan["max_policy_control_steps"]
    assert sum(row["phase"] == "warmup" for row in trace) == plan["warmup_control_steps"]
    assert steps and queries and steps[-1]["success_signal"] == result["success_signal"]
    expected_keys = {"observation/image", "observation/wrist_image", "observation/state", "prompt"}
    for query in queries:
        start = query["model_step"]
        stem = attempt / "policy" / f"{query['inference']:03d}"
        with np.load(stem.with_suffix(".npz")) as observation:
            assert set(observation.files) == expected_keys
            for key in ["observation/image", "observation/wrist_image"]:
                assert observation[key].shape == (224, 224, 3) and observation[key].dtype == np.uint8
            assert observation["observation/state"].shape == (8,)
            assert observation["prompt"].item() == result["prompt"]
        actions = np.load(stem.with_suffix(".npy"))
        assert list(actions.shape) == query["action_shape"] == [plan["expected_action_horizon"], 7]
        assert np.isfinite(actions).all()
        executed = steps[start : start + plan["execution_prefix"]]
        np.testing.assert_array_equal([row["action"] for row in executed], actions[: len(executed)])
    for image in ["agentview-input.png", "wrist-input.png"]:
        assert imageio.imread(attempt / image).shape == (224, 224, 3)
    with imageio.get_reader(str(attempt / "episode.mp4")) as reader:
        assert reader.count_frames() == len(steps) + plan["warmup_control_steps"] + 1
    if result["outcome"] == "success":
        assert steps[-1]["done"] and steps[-1]["success_signal"]
        successes += 1
    print(result["attempt"], result["outcome"], "steps", len(steps), "queries", len(queries))
assert successes == summary["successes"] and summary["total"] == len(attempts)
assert successes / len(attempts) == summary["success_rate"]
print(f"Verified {successes}/{len(attempts)} successful")
