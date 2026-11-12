"""Configured OpenPI requests and robot-side policy execution records."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
import tomllib

import numpy as np

from steward.ego.panda_observation import policy_inputs
from steward.glrender import encode_image

SKILL = "libero_pick_place"
PROMPT = "pick up the alphabet soup and place it in the basket"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


@dataclass(frozen=True)
class PandaPolicyConfig:
    client_command: tuple[str, ...]
    port: int
    model_id: str
    request_timeout_s: float = 15.0
    max_actions: int = 280
    max_wall_s: float = 420.0
    prefix_size: int = 5
    receipt_wall_s: float = 30.0
    stop_wall_s: float = 20.0

    def __post_init__(self):
        if not self.client_command or not all(isinstance(item, str) and item for item in self.client_command):
            raise ValueError("Panda policy requires a client command")
        if type(self.port) is not int or not 1 <= self.port <= 65535 or not self.model_id:
            raise ValueError("Panda policy identity or port is invalid")
        if not 0 < self.request_timeout_s < 20 or not 0 < self.receipt_wall_s <= 120:
            raise ValueError("Panda request and receipt deadlines are invalid")
        if (type(self.prefix_size) is not int or not 1 <= self.prefix_size <= 5
                or type(self.max_actions) is not int or not 1 <= self.max_actions <= 280
                or not 0 < self.max_wall_s <= 420 or not 0 < self.stop_wall_s <= 30):
            raise ValueError("Panda policy execution budget is invalid")

    @classmethod
    def load(cls, path):
        values = tomllib.loads(Path(path).read_text())["policy"]
        values["client_command"] = tuple(values["client_command"])
        return cls(**values)


class PolicyFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def infer(config, observation, folder, cancelled, *, prompt=PROMPT, target=None):
    """Run the pinned client with one in-flight request and cancellable wall deadline."""
    folder.mkdir()
    inputs = policy_inputs(observation)
    payload = {
        "episodeId": observation["episodeId"], "step": observation["step"], "prompt": prompt,
        "observation/state": inputs["observation/state"].tolist(),
        **{key: base64.b64encode(encode_image(inputs[key])).decode("ascii")
           for key in ("observation/image", "observation/wrist_image")},
    }
    if target is not None:
        payload['target'] = target
    source, target = folder / "observation.json", folder / "inference.json"
    write_json(source, payload)
    command = [*config.client_command, str(config.port), str(source), str(target)]
    started = time.monotonic()
    with (folder / "client.log").open("w") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            while process.poll() is None:
                if cancelled.wait(.025):
                    raise PolicyFailure("cancelled", "Policy request cancelled")
                if time.monotonic() - started >= config.request_timeout_s:
                    raise PolicyFailure("inference_timeout", "Policy request exceeded its wall deadline")
            if cancelled.is_set():
                raise PolicyFailure("cancelled", "Policy reply arrived after cancellation")
            if process.returncode:
                raise PolicyFailure("inference_unavailable", f"Policy client exited with {process.returncode}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    try:
        result = json.loads(target.read_text())
        actions = np.asarray(result["actions"], dtype=float)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyFailure("invalid_action", f"Invalid policy reply: {exc}") from exc
    if (actions.ndim != 2 or actions.shape[0] < config.prefix_size or actions.shape[1] != 7
            or not np.isfinite(actions).all()):
        raise PolicyFailure("invalid_action", "Expected a finite sequence of seven-dimensional actions")
    return actions, result
