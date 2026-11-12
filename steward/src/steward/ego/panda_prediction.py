"""Panda kinematic and observed RGB-D collision checks for the fixed task."""

from __future__ import annotations

import numpy as np

from steward.ego.estimator import _apply_joint
from steward.ego.panda_observation import rig_pose
from steward.ego.panda_perception import cloud
from steward.ego.panda_policy import PolicyFailure
from steward.math3d import matrix_from_quat, pose_compose
from steward.world.panda_control import policy_libero_target


def chain_positions(rig, encoders):
    transform = {"p": [0.0, 0.0, 0.0], "q": [1.0, 0.0, 0.0, 0.0]}
    positions = []
    for node in rig["chain"]:
        if "joint" in node:
            transform = _apply_joint(transform, {"type": "revolute", "origin": {"value": node["origin"]},
                                                "axis": node["axis"]}, encoders[node["joint"]]["position"])
            positions.append(transform["p"])
        else:
            transform = pose_compose(transform, node["origin"])
    positions.append(pose_compose(transform, rig["mount"])["p"])
    return np.asarray(positions)


def segment_distances(points, start, end):
    delta = end - start
    factor = np.clip((points - start) @ delta / max(float(delta @ delta), 1e-12), 0, 1)
    return np.linalg.norm(points - start - factor[:, None] * delta, axis=1)


class PandaPrediction:
    """Checks observed obstacles and differential FK, with task-scoped contact exemptions."""

    def __init__(self, observation, target, basket=None, target_size=None):
        self.observation = observation
        self.rig = observation["toolRig"]
        self.encoders = {name: dict(value) for name, value in observation["encoders"].items()}
        self.target = np.asarray(target)
        self.basket = np.asarray(basket) if basket is not None else None
        self.target_half = np.asarray(target_size) / 2 + .025 if target_size is not None else None
        points = []
        for role in ("external", "wrist"):
            depth = observation["depth"][role]
            xyz = cloud(depth, observation["cameras"][role])[::3, ::3].reshape(-1, 3)
            valid = (depth[::3, ::3].ravel() > 0) & (np.abs(xyz[:, :2]).max(axis=1) < .5)
            valid &= (xyz[:, 2] > .015) & (xyz[:, 2] < .6)
            points.append(xyz[valid])
        points = np.concatenate(points)
        if len(points) < 100:
            raise PolicyFailure("prediction_rejected", "Insufficient RGB-D coverage of the operation area")
        self.observed_points = len(points)
        self_points = np.zeros(len(points), dtype=bool)
        self.boxes = []
        # RGB-D also sees decorative robot meshes outside collision meshes.
        # Remove calibrated rendered self geometry before checking external obstacles.
        for geom in observation["selfVisual"]:
            transform = rig_pose(geom, self.encoders)
            rotation = matrix_from_quat(transform["q"])
            local = (points - transform["p"]) @ rotation
            center, size = np.asarray(geom["bounds"]).reshape(2, 3)
            self.boxes.append((np.asarray(transform["p"]), rotation, center, size))
            self_points |= np.all(np.abs(local - center) <= size + .006, axis=1)
        self.points = points[~self_points]

    def require_observed(self, point, contact=False):
        """Require depth or authorized contact for swept samples in the operating volume."""
        if max(abs(point[0]), abs(point[1])) > .32 or point[2] > .43:
            return
        for position, rotation, center, size in self.boxes:
            if np.all(np.abs((point - position) @ rotation - center) <= size + .006):
                return
        target_contact = (np.linalg.norm(point - self.target) < .075 if self.target_half is None else
                          np.all(np.abs(point - self.target) < self.target_half))
        if contact and (target_contact or self.basket is not None and
                        np.all(np.abs(point[:2] - self.basket[:2]) < .065) and .02 < point[2] < .17):
            return
        for role in ("external", "wrist"):
            camera = self.observation["cameras"][role]
            local = (point - camera["pose"]["p"]) @ matrix_from_quat(camera["pose"]["q"])
            distance = -local[2]
            if distance <= 0:
                continue
            k = camera["K"]
            x = int(round(k[0][0] * local[0] / distance + k[0][2]))
            y = int(round(-k[1][1] * local[1] / distance + k[1][2]))
            depth = self.observation["depth"][role]
            if 0 <= x < depth.shape[1] and 0 <= y < depth.shape[0]:
                observed = float(depth[y, x])
                if observed > 0 and distance <= observed + .02:
                    return
        raise PolicyFailure("prediction_rejected", f"Swept point lacks visible free-space evidence: {point.tolist()}")

    def joint_target(self, values):
        pose = rig_pose(self.rig, self.encoders)
        position = np.asarray(pose["p"])
        rotation = matrix_from_quat(pose["q"])
        joints = [node for node in self.rig["chain"] if "joint" in node]
        jacobian = np.empty((6, len(joints)))
        epsilon = 1e-5
        for column, node in enumerate(joints):
            name = node["joint"]
            shifted = {key: dict(value) for key, value in self.encoders.items()}
            shifted[name]["position"] += epsilon
            next_pose = rig_pose(self.rig, shifted)
            jacobian[:3, column] = (np.asarray(next_pose["p"]) - position) / epsilon
            relative = matrix_from_quat(next_pose["q"]) @ rotation.T
            jacobian[3:, column] = np.array([relative[2, 1] - relative[1, 2],
                                            relative[0, 2] - relative[2, 0],
                                            relative[1, 0] - relative[0, 1]]) / (2 * epsilon)
        displacement = np.asarray([*(.05 * values[:3]), *(.5 * values[3:6])])
        delta = jacobian.T @ np.linalg.solve(jacobian @ jacobian.T + .002 * np.eye(6), displacement)
        predicted = {key: dict(value) for key, value in self.encoders.items()}
        for node, change in zip(joints, delta):
            value = predicted[node["joint"]]["position"] + float(change)
            lower, upper = node["range"]
            if not lower <= value <= upper or abs(change) > .35:
                raise PolicyFailure("prediction_rejected", f"Predicted joint limit for {node['joint']}")
            predicted[node["joint"]]["position"] = value
        return predicted, delta

    def check(self, actions):
        values = np.asarray(actions, dtype=float)
        if values.ndim != 2 or values.shape[1] != 7 or not np.isfinite(values).all() or np.abs(values).max() > 1:
            raise PolicyFailure("invalid_action", "Invalid normalized action prefix")
        travel = float(.05 * np.linalg.norm(values[:, :3], axis=1).sum())
        if travel > .25:
            raise PolicyFailure("action_outside_authorization", "Prefix translation command exceeds 0.25 m")
        maximum_joint = 0.0
        nearest = 1.0
        # Each OSC command uses feedback at application. Whole-prefix checks use
        # the measured pose, and the caller repeats this check at every control tick.
        current = chain_positions(self.rig, self.encoders)
        position = np.asarray(self.observation["tool"]["p"])
        for action in values:
            try:
                goal = policy_libero_target(action, position)
            except ValueError as exc:
                raise PolicyFailure("action_outside_authorization", str(exc)) from exc
            predicted, delta = self.joint_target(action)
            maximum_joint = max(maximum_joint, float(np.abs(delta).max()))
            proposed = chain_positions(self.rig, predicted)
            for fraction in (.25, .5, .75, 1.0):
                sweep = current + fraction * (proposed - current)
                for index, (start, end) in enumerate(zip(sweep, sweep[1:])):
                    self.require_observed((start + end) / 2, contact=index >= len(sweep) - 3)
                    self.require_observed(end, contact=index >= len(sweep) - 3)
                    obstacles = self.points
                    if index >= len(sweep) - 3:
                        target_contact = (np.linalg.norm(obstacles - self.target, axis=1) < .085
                            if self.target_half is None else (np.abs(obstacles - self.target) < self.target_half).all(axis=1))
                        basket_contact = (np.zeros(len(obstacles), bool) if self.basket is None else
                            (np.abs(obstacles[:, :2] - self.basket[:2]).max(axis=1) < .09) & (obstacles[:, 2] < .17))
                        obstacles = obstacles[~(target_contact | basket_contact)]
                    if len(obstacles):
                        distance = float(segment_distances(obstacles, start, end).min())
                        nearest = min(nearest, distance)
                        if distance < .035:
                            closest = obstacles[segment_distances(obstacles, start, end).argmin()]
                            raise PolicyFailure("prediction_rejected", "Observed obstacle intersects predicted arm sweep: "
                                                f"segment={index}, clearance={distance:.6f}, point={closest.tolist()}")
                tool = position + fraction * (goal - position)
                self.require_observed(tool, contact=True)
                for point in (self.target,) + ((self.basket,) if self.basket is not None else ()):
                    if np.linalg.norm(tool[:2] - point[:2]) < .095:
                        break
                else:
                    if len(self.points) and np.linalg.norm(self.points - tool, axis=1).min() < .035:
                        raise PolicyFailure("prediction_rejected", "Observed obstacle intersects tool sweep")
        return {"observedPoints": self.observed_points, "minimumObservedClearanceM": nearest,
                "maximumPredictedJointDeltaRad": maximum_joint, "translationCommandM": travel,
                "coverage": ["encoder_fk", "joint_limits", "arm_and_tool_sweep", "observed_rgbd_obstacles",
                             "task_contact_regions", "operating_volume_visibility"],
                "scope": "calibrated self geometry and observed operating volume"}
