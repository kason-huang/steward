"""Planar IK and measured tool pose for the XLeRobot's working arm."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from steward.assets.xlerobot import ARM_JOINTS, GRIPPER, JAW, JAW_OPEN, STOW_LIFT, STOW_REACH
from steward.ego.arm import Arm, ArmGeometry, VIRTUAL_JOINTS
from steward.ego.kinematics import _link_pose
from steward.ego.motion_errors import DockOutsideReceipt, OutOfReach, SkillFailed
from steward.ego.motion_policy import MotionPolicyConfig, PlanarMotionPolicy
from steward.ego.s0 import BodyControl, BodyMove, TcpPose
from steward.math3d import matrix_from_quat, quat_angle_between, quat_from_matrix

if TYPE_CHECKING:
    from steward.ego.skills import Job, SkillExecutor

REACH_MAX = 0.36
PITCH_MAX = 1.25
PLACE_APPROACH_CLEARANCE = 0.025
LOADED_JAW_OPEN_SPEED = 0.015  # rad/s until the fingers lose contact on a shallow support
EMPTY_JAW_OPEN_SPEED = 0.35


def _rotate(vector: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * vector[0] + s * vector[1], -s * vector[0] + c * vector[1]])


class XLeRobotArm(Arm):
    """Map lift, reach and tool pitch to the single supported elbow branch."""

    def __init__(self, model: dict):
        self.model = model
        joints = {joint["id"]: joint for joint in model["body"]["joints"]}
        self.limits = [joints[name]["limits"]["value"] for name in ARM_JOINTS]
        self.encoders = ("Rotation_R", *ARM_JOINTS, "Wrist_Roll_R")
        zero = dict.fromkeys(self.encoders, 0.0)
        zero["Rotation_R"] = joints["Rotation_R"]["q"]["value"][0]
        frames = [_link_pose(model, zero, joints[name]["child"]) for name in ARM_JOINTS]
        tcp = _link_pose(model, zero, GRIPPER)
        axis_y = [(matrix_from_quat(frame["q"]) @ joints[name]["axis"]["value"])[1]
                  for name, frame in zip(ARM_JOINTS, frames, strict=True)]
        if any(abs(y) < 0.999 for y in axis_y):
            raise ValueError("the XLeRobot working arm must pitch in the fore-aft plane")
        self.pitch_signs = tuple(math.copysign(1.0, y) for y in axis_y)
        forward = matrix_from_quat(tcp["q"])[:, 2]
        self.tool_angle = math.atan2(float(forward[0]), -float(forward[2]))
        points = np.array([frame["p"] for frame in (*frames, tcp)])
        self.shoulder = points[0, [0, 2]]
        upper, lower, self.tool = np.diff(points[:, [0, 2]], axis=0)
        self.lengths = (float(np.linalg.norm(upper)), float(np.linalg.norm(lower)))
        self.angles = (math.atan2(upper[1], upper[0]), math.atan2(lower[1], lower[0]))
        super().__init__(ArmGeometry(tool_offset=float(points[0, 0]), lateral=float(points[0, 1]), reach_min=0.08,
                                     reach_max=REACH_MAX, lift_min=0.66, lift_max=1.06, wrist_yaw_max=0.0,
                                     wrist_roll_max=0.0, gripper=GRIPPER, wrist_pitch_max=PITCH_MAX),
                         motion_policy=XLeRobotMotionPolicy(self))
        self._target = {"lift": STOW_LIFT, "reach": STOW_REACH,
                        "wrist_pitch": 0.0, "wrist_yaw": 0.0, "wrist_roll": 0.0}

    def _solve(self, lift: float, reach: float, pitch: float) -> tuple[float, float, float] | None:
        angle = self.tool_angle - pitch
        x, z = np.array([reach, lift - self.shoulder[1]]) - _rotate(self.tool, angle)
        a, b = self.lengths
        cosine = (x*x + z*z - a*a - b*b) / (2*a*b)
        if not -1 <= cosine <= 1:
            return None
        bend = -math.acos(cosine)
        upper, lower = self.angles
        shoulder = upper - math.atan2(z, x) + math.atan2(b * math.sin(bend), a + b * cosine)
        elbow = lower - upper - bend
        result = tuple(q * sign for q, sign in zip(
            (shoulder, elbow, angle - shoulder - elbow), self.pitch_signs, strict=True))
        return result if all(bounds["lo"] <= q <= bounds["hi"] for q, bounds in
                             zip(result, self.limits, strict=True)) else None

    def reachable(self, lift: float, reach: float, wrist_yaw: float = 0.0,
                  wrist_roll: float = 0.0, wrist_pitch: float = 0.0) -> bool:
        """Check the tool pose against the native joint stops."""
        return (super().reachable(lift, reach, wrist_yaw, wrist_roll, wrist_pitch)
                and self._solve(lift, reach, wrist_pitch) is not None)

    def pitch_for(self, lift: float, reach: float, wrist_yaw: float = 0.0,
                  wrist_roll: float = 0.0) -> float | None:
        """Return the least sampled tilt that fits the native joint limits."""
        for pitch in np.linspace(0.0, PITCH_MAX, 26):
            if self.reachable(lift, reach, wrist_yaw, wrist_roll, pitch):
                return float(pitch)
        return None

    def tool_rotation(self, yaw: float, roll: float, pitch: float = 0.0) -> np.ndarray:
        """Return the tool frame for the supported zero-yaw, zero-roll poses."""
        c, s = math.cos(pitch), math.sin(pitch)
        return np.array([[c, 0, s], [0, -1, 0], [s, 0, -c]])

    def _measured(self, encoders: dict) -> tuple[dict, float]:
        if not all(name in encoders for name in self.encoders):
            return {}, self.geometry.lateral
        tcp = _link_pose(self.model, encoders, GRIPPER)
        x, y, z = tcp["p"]
        tip = matrix_from_quat(tcp["q"])[:, 2]
        pitch = max(0.0, math.atan2(tip[0], -tip[2]))
        return {"lift": float(z), "reach": float(x) - self.geometry.tool_offset, "wrist_pitch": pitch,
                "wrist_yaw": 0.0, "wrist_roll": 0.0}, float(y)

    def joints_of(self, joints: dict, odometry: dict | None = None) -> dict:
        """Project native encoders to planar targets in the base frame."""
        return self._measured(joints)[0]

    def virtualize(self, state: dict) -> dict:
        """Add the measured planar joints and TCP lateral offset to the sensor packet."""
        measured, lateral = self._measured(state["joints"])
        out = super().virtualize(state)
        return {**out, "joints": {**state["joints"], **measured}, "armLateral": lateral}

    def command(self, cmd: dict) -> dict:
        """Resolve planar commands to native servo targets before changing the remembered target."""
        wanted = {key: float(cmd[key]) for key in VIRTUAL_JOINTS if key in cmd}
        output = {key: value for key, value in cmd.items() if key not in VIRTUAL_JOINTS and key != "turnOver"}
        if wanted:
            target = {**self._target, **wanted}
            output["joints"] = {**output.get("joints", {}), **self._joint_targets(target)}
            self._target = target
        return output

    def _joint_targets(self, target: dict) -> dict[str, float]:
        lift, reach, pitch = (target[k] for k in ("lift", "reach", "wrist_pitch"))
        allowed = super().reachable(lift, reach, target["wrist_yaw"], target["wrist_roll"], pitch)
        solution = self._solve(lift, reach, pitch) if allowed else None
        if solution is None:
            raise OutOfReach(f"XLeRobot cannot reach {reach:.3f} m at {lift:.3f} m with pitch {pitch:.3f} rad")
        return dict(zip(ARM_JOINTS, solution, strict=True))

    def pose_targets(self, x: float, z: float, pitch: float) -> tuple[dict, dict[str, float]]:
        """Resolve an absolute base-relative TCP target without changing the arm's setpoint."""
        target = {"lift": z, "reach": x - self.geometry.tool_offset, "wrist_pitch": pitch,
                  "wrist_yaw": 0.0, "wrist_roll": 0.0}
        return target, self._joint_targets(target)

    def remember_pose_target(self, target: dict) -> None:
        """Use an accepted S0 target when the next skill starts commanding individual axes."""
        self._target = target

class XLeRobotS0(BodyControl):
    """One planar TCP with fixed base-relative lateral position and tool roll."""

    def __init__(self, arm: XLeRobotArm):
        super().__init__(arm.model)
        self.arm = arm

    def describe_arm(self) -> dict:
        return {"id": "right", "gripper": GRIPPER, "poseMode": "planar",
                "fixedYM": self.arm.geometry.lateral, "pitchRangeRad": [0.0, PITCH_MAX],
                "zeroPitchOrientation": quat_from_matrix(self.arm.tool_rotation(0.0, 0.0)),
                "pitchAxisInBase": [0, -1, 0]}

    def measured_pose(self, encoders: dict) -> TcpPose:
        tcp = _link_pose(self.model, encoders, GRIPPER)
        return TcpPose(tuple(map(float, tcp["p"])), tuple(tcp["q"]))

    def pose_move(self, value: object, encoders: dict) -> BodyMove:
        pose = TcpPose.parse(value)
        x, y, z = pose.position
        tip = matrix_from_quat(pose.orientation)[:, 2]
        pitch = math.atan2(tip[0], -tip[2])
        rotation = quat_from_matrix(self.arm.tool_rotation(0.0, 0.0, pitch))
        if abs(y - self.arm.geometry.lateral) > 1e-3 or quat_angle_between(pose.orientation, rotation) > 1e-3:
            raise ValueError("pose must lie in the advertised fore-aft plane with fixed yaw and roll")
        try:
            target, joints = self.arm.pose_targets(x, z, pitch)
        except OutOfReach as exc:
            raise ValueError(str(exc)) from exc
        return BodyMove({"joints": joints}, joints, 0.025, pose, lambda: self.arm.remember_pose_target(target))

    def gripper_move(self, action: str) -> BodyMove:
        jaw = JAW_OPEN if action == "open" else self.limits[JAW]["lo"]
        return BodyMove({"gripper": action}, {JAW: jaw}, 0.025)

    def prepare_resume(self, read_encoders):
        target = self.arm.joints_of(read_encoders())
        if not target:
            raise RuntimeError("XLeRobot joint encoders are required to resume motion")
        return lambda: self.arm.remember_pose_target(target)


class XLeRobotMotionPolicy(PlanarMotionPolicy):
    """Approach worktops from above and keep the held object's tilt during retraction."""

    def __init__(self, arm: XLeRobotArm):
        super().__init__(MotionPolicyConfig(approach_floor=STOW_LIFT, intermediate_reaches=(0.15, 0.18, 0.22)))
        self.arm = arm

    def stage_before_dock(self, driver: SkillExecutor, plan: dict) -> None:
        """Raise the held object above the destination before driving up to it."""
        if "supportBox" not in plan or "object" not in plan or not driver._state()["gripper"]["holding"]:
            return
        driver._retract(plan)
        pitch = float(plan.get("wristPitch") or 0.0)
        current = float(driver._state()["joints"]["wrist_pitch"])
        driver._ramp_joints({"wrist_pitch": pitch}, abs(pitch - current) / driver.config.wrist_pitch_speed)
        driver._set_joint("lift", plan["approachLift"])

    def prepare_dock(self, driver: SkillExecutor, job: Job, plan: dict, retract: bool) -> None:
        """Keep the cleared placing posture through the final approach."""
        if "supportBox" not in plan or "object" not in plan:
            super().prepare_dock(driver, job, plan, retract)

    def approach(self, driver: SkillExecutor, plan: dict, xy: list[float], lift: float) -> None:
        """Raise the compact arm, extend over the support, then descend to the target."""
        pitch = float(plan.get("wristPitch") or 0.0)
        upright_pick = plan.get("topGraspProbes") and not plan.get("graspOffset") and pitch <= 0.15
        if upright_pick:
            # Close below the measured centre so the moving jaw clears caps and rims.
            lift = min(lift, float(plan["lift"])) - 0.011
        if "dockReachMarginM" not in plan:
            driver._align_lateral(xy, tol=0.005)
        else:
            driver._check_receipt_dock(plan, driver._state()["odometry"])
        reach = driver._reach_to(xy, clamp=False)
        compact_pick = plan.get("fixedGraspHeight")
        if "object" in plan or compact_pick:
            above = plan["approachLift"]
        else:
            above = max(float(plan.get("approachLift") or 0.0), lift + 0.045)

        if upright_pick and all(self.arm.reachable(z, reach) for z in (lift, above, plan["carryLift"])):
            pitch = 0.0

        def fits(reach: float) -> bool:
            if not compact_pick:
                heights = np.linspace(above, lift, 7) if "dockReachMarginM" in plan else (lift, above)
                return all(self.arm.reachable(z, reach, wrist_pitch=pitch) for z in heights)
            return self.arm.reachable_path([
                {"lift": z, "reach": reach, "wrist_pitch": pitch}
                for z in np.r_[np.linspace(above, lift, 7), np.linspace(lift, plan["carryLift"], 7)]
            ])

        margin = plan.get("dockReachMarginM")
        outside = margin is not None and abs(reach - plan["reach"]) > margin
        if "object" not in plan and not fits(reach):
            correction = reach - plan["reach"]
            if abs(correction) <= driver.config.dock_tol:
                o = driver._state()["odometry"]
                driver._translate_to(o["x"] + correction * math.cos(o["yaw"]),
                                     o["y"] + correction * math.sin(o["yaw"]), o["yaw"],
                                     tol=driver.config.dock_position_tol / 2)
                reach = driver._reach_to(xy, clamp=False)
        if outside or not fits(reach):
            if "object" in plan:
                raise DockOutsideReceipt(f"descent at measured reach {reach:.3f} m is outside the checked trajectory; "
                                         "load kept in gripper", driver._state()["odometry"])
            raise OutOfReach(f"XLeRobot cannot descend at the live dock reach {reach:.3f} m")
        driver._ramp_joints({"lift": above, "wrist_pitch": pitch}, 0.5)
        driver._set_joint("reach", reach, tol=0.015)
        driver._set_joint("lift", lift)

    def prepare_carry(self, driver: SkillExecutor, plan: dict) -> None:
        """Lift the load straight off the support before the skill retracts the arm."""
        driver._ramp_joints({"lift": float(plan["carryLift"])}, 0.7)

    def exit(self, driver: SkillExecutor, plan: dict) -> None:
        """Bring the tool back to a compact pose while retaining the current pitch."""
        reach = driver._state()["joints"]["reach"]
        compact = max(driver.config.retract_reach, plan.get("retractReach", 0.0))
        if reach > compact:
            driver._set_joint("reach", compact)

    def after_place(self, driver: SkillExecutor, plan: dict) -> None:
        """Clear the support before turning the empty jaw down into its stowed pose."""
        if plan.get("slowJawRelease"):
            driver._set_joint("lift", plan["approachLift"])
        self.exit(driver, plan)
        driver._backoff(plan)
        driver._set_joint("reach", STOW_REACH)
        driver._set_joint("lift", STOW_LIFT)
        driver._untilt()

    def release_onto(self, driver: SkillExecutor, job: Job, plan: dict) -> None:
        """Open the hinged jaw slowly for a low release onto a shallow support."""
        if not plan.get("slowJawRelease"):
            return super().release_onto(driver, job, plan)
        driver._wait(0.2)
        driver._allow_place_release(job)
        missing_at = None

        def detached(state: dict) -> bool:
            nonlocal missing_at
            if state["gripper"]["holding"]:
                missing_at = None
                return False
            stamp = state["stamp"]
            confirmed = missing_at is not None and stamp > missing_at
            missing_at = stamp
            return confirmed

        driver._move_joints({JAW: JAW_OPEN}, speed=LOADED_JAW_OPEN_SPEED, until=detached)
        driver._move_joints({JAW: JAW_OPEN}, speed=EMPTY_JAW_OPEN_SPEED)
        driver._command({"gripper": "open"})
        if driver._read_place_grip():
            raise SkillFailed("the gripper still reports contact after opening")
