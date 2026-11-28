"""Shared S0 tool-centre pose and native actuator request."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from steward.math3d import quat_angle_between, quat_normalize

POSITION_TOLERANCE_M = 0.012
ORIENTATION_TOLERANCE_RAD = 0.05


@dataclass(frozen=True)
class TcpPose:
    """Tool-centre position in base metres and tool-to-base orientation as a wxyz quaternion."""

    position: tuple[float, ...]
    orientation: tuple[float, ...]

    @classmethod
    def parse(cls, value: object) -> TcpPose:
        if not isinstance(value, dict) or value.keys() != {"position", "orientation"}:
            raise ValueError("pose requires position [x,y,z] in metres and orientation [w,x,y,z]")
        if not all(isinstance(v, (list, tuple)) and len(v) == size
                   and all(type(n) in (int, float) and math.isfinite(n) for n in v)
                   for v, size in ((value["position"], 3), (value["orientation"], 4))):
            raise ValueError("pose requires finite position and orientation coordinates")
        if not math.isclose(math.hypot(*value["orientation"]), 1.0, abs_tol=1e-3):
            raise ValueError("orientation must be a unit quaternion in wxyz order")
        return cls(tuple(value["position"]), tuple(quat_normalize(value["orientation"])))

    def to_dict(self) -> dict:
        return {"position": list(self.position), "orientation": list(self.orientation)}

    def matches(self, target: TcpPose) -> bool:
        return (math.dist(self.position, target.position) <= POSITION_TOLERANCE_M
                and quat_angle_between(self.orientation, target.orientation) <= ORIENTATION_TOLERANCE_RAD)


@dataclass(frozen=True)
class BodyMove:
    """One native command and the encoder targets used to confirm motion."""

    command: dict
    joints: dict[str, float]
    tolerance: float
    target_pose: TcpPose | None = None
    accepted: Callable[[], None] | None = None


class BodyControl:
    """Resolve direct commands using a body's kinematics and encoder conventions."""

    tools = ("body_set_pose", "body_set_gripper")

    def __init__(self, model: dict):
        self.model = model
        self.limits = {joint["id"]: limits for joint in model["body"]["joints"]
                       if (limits := joint.get("limits", {}).get("value")) is not None}

    def describe_arm(self) -> dict:
        raise NotImplementedError

    def measured_pose(self, encoders: dict) -> TcpPose:
        raise NotImplementedError

    def pose_move(self, value: object, encoders: dict) -> BodyMove:
        raise NotImplementedError

    def gripper_move(self, action: str) -> BodyMove:
        raise NotImplementedError

    def joint_move(self, joints: object) -> BodyMove:
        raise ValueError("this body does not support direct joint targets")

    def prepare_resume(self, read_encoders: Callable[[], dict]) -> Callable[[], None] | None:
        """Stage any encoder-based setpoint update until motion resume succeeds."""
        return None
