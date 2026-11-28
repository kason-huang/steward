"""Failures shared by task execution and body motion policies."""


class SkillStopped(Exception):
    """The caller cancelled the current skill."""


class SkillFailed(Exception):
    """The requested skill could not be completed."""


class OutOfReach(SkillFailed):
    """The requested pose has no accepted arm solution; no command was sent."""


class DockOutsideReceipt(SkillFailed):
    """Stop before descent and give S2 the measured pose for a new receipt."""

    def __init__(self, reason: str, odometry: dict):
        super().__init__(f"{reason}; refresh observations and predict a new plan")
        self.measured_pose = {axis: float(odometry[axis]) for axis in ("x", "y", "yaw")}
