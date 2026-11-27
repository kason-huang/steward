"""Robot observation and action interface shared by the S1 skills."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from steward.ego.arm import Arm


class SkillRobot(Protocol):
    """A skill reads robot feedback and submits one primitive action at a time.

    Observations carry the robot's time in seconds, measured joint positions in metres
    or radians, gripper state and the latest perceived entities. Actions submit targets
    in the same units. The skill owns sequencing and verifies the effects in feedback.
    """

    def observe(self) -> dict: ...

    def command(self, action: dict) -> None: ...


class RobotSkillAdapter:
    """Bind the robot's native interface to the observation and action vocabulary of a skill.

    A planar arm maps its named setpoints and feedback using its own kinematics. For a
    joint-space arm the same interface carries named physical joints and the gripper.
    """

    def __init__(self, read_body: Callable[[], dict], send_body: Callable[[dict], None],
                 read_belief: Callable[[], dict], generation: Callable[[], int],
                 arm: Callable[[], Arm | None]):
        self._read_body = read_body
        self._send_body = send_body
        self._read_belief = read_belief
        self._generation = generation
        self._arm = arm

    def observe(self) -> dict:
        """Use live body values and the last Ego-State objects from the same episode."""
        belief = self._read_belief()
        state = {key: dict(value) if isinstance(value, dict) else value
                 for key, value in belief.items() if key != "generation"}
        try:
            body = self._read_body()
        except Exception:  # noqa: BLE001 - retain the belief and its original stamp if feedback is unavailable
            body = None
        if body is not None and self._matches_belief(body, belief):
            for key in ("stamp", "odometry", "joints", "gripper", "grippers"):
                if body.get(key) is not None:
                    state[key] = body[key]
        arm = self._arm()
        return arm.virtualize(state) if arm is not None else state

    def _matches_belief(self, body: dict, belief: dict) -> bool:
        stamp = body.get("stamp")
        belief_stamp = belief.get("stamp")
        if stamp is not None and belief_stamp is not None and stamp < belief_stamp - 1e-6:
            return False
        episode = body.get("episode")
        return (self._generation() == belief["generation"]
                and (episode is None or belief.get("episode") in (None, episode)))

    def command(self, action: dict) -> None:
        """Translate a skill target before using its existing motion authorization."""
        arm = self._arm()
        self._send_body(arm.command(action) if arm is not None else action)
