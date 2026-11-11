"""Decode Panda sensor packets and compute robot poses from calibrated encoder chains."""

from __future__ import annotations

import base64
from io import BytesIO
import math

import numpy as np
from PIL import Image

from steward.ego.estimator import _apply_joint
from steward.math3d import pose_compose


def rig_pose(rig, encoders):
    result = {"p": [0.0, 0.0, 0.0], "q": [1.0, 0.0, 0.0, 0.0]}
    for node in rig["chain"]:
        if "joint" in node:
            joint = {"type": "revolute", "origin": {"value": node["origin"]}, "axis": node["axis"]}
            result = _apply_joint(result, joint, encoders[node["joint"]]["position"])
        else:
            result = pose_compose(result, node["origin"])
    return pose_compose(result, rig["mount"])


class PandaObservationRuntime:
    """Observation-only Panda consumer using the same robot link as the Ego Runtime."""

    def __init__(self, link):
        self.link = link
        self.last_key = None
        self.observation = None

    def read(self):
        packet = self.link.panda_observation(self.last_key)
        if packet.get("unchanged"):
            return None
        key = packet["episodeId"], packet["step"]
        if self.last_key is not None and key <= self.last_key:
            return None
        calibration = packet["calibration"]
        encoders = packet["encoders"]
        images = {}
        depth = {}
        cameras = {}
        for role, rig in calibration["cameras"].items():
            rgb_data = packet.get("images", {}).get(role)
            depth_data = packet.get("depthMm", {}).get(role)
            if rgb_data is None or depth_data is None:
                images[role] = np.zeros((rig["height"], rig["width"], 3), dtype=np.uint8)
                depth[role] = np.zeros((rig["height"], rig["width"]), dtype=np.float32)
                cameras[role] = {"pose": rig_pose(rig, encoders), "K": rig["K"]}
                continue
            with Image.open(BytesIO(base64.b64decode(rgb_data, validate=True))) as image:
                if image.mode != "RGB" or image.size != (rig["width"], rig["height"]):
                    raise ValueError(f"Unexpected {role} camera image format")
                images[role] = np.array(image)
            with Image.open(BytesIO(base64.b64decode(depth_data, validate=True))) as image:
                if image.size != (rig["width"], rig["height"]):
                    raise ValueError(f"Unexpected {role} depth image format")
                depth[role] = np.asarray(image, dtype=np.float32) / 1000
            cameras[role] = {"pose": rig_pose(rig, encoders), "K": rig["K"]}
        self.observation = {
            "episodeId": key[0], "step": key[1], "stamp": packet["stamp"],
            "calibrationId": packet["calibrationId"], "encoders": encoders,
            "observationId": packet.get("observationId"),
            "images": images, "depth": depth, "cameras": cameras,
            "tool": rig_pose(calibration["tool"], encoders),
            "hand": rig_pose(calibration["hand"], encoders),
        }
        self.last_key = key
        return self.observation


def policy_inputs(observation):
    """Produce the vision and proprioception fields of the pinned LIBERO policy."""
    images = {}
    if any(role not in observation["images"] or not observation["images"][role].any()
           for role in ("external", "wrist")):
        raise ValueError("Panda policy preview requires both RGB views")
    for role, key in (("external", "observation/image"), ("wrist", "observation/wrist_image")):
        image = Image.fromarray(observation["images"][role]).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        images[key] = np.array(image.resize((224, 224), Image.Resampling.BILINEAR))
    w, *axis = observation["hand"]["q"]
    w = min(1.0, max(-1.0, w))
    denominator = math.sqrt(1.0 - w * w)
    rotation = np.zeros(3) if not denominator else np.asarray(axis) * (2 * math.acos(w) / denominator)
    fingers = [observation["encoders"][f"gripper0_finger_joint{i}"]["position"] for i in (1, 2)]
    return images | {"observation/state": np.array([*observation["tool"]["p"], *rotation, *fingers])}
