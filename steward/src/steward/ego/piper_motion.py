"""Fixed-base PiPER prediction from Ego-State and the native arm kinematics."""

from __future__ import annotations

import math

import mink
import mujoco
import numpy as np

from steward.assets.piper import ARM_JOINTS, WRIST_LINK, model_path
from steward.ego.esim import PredictionError, TargetFreshness
from steward.ego.planner import entity_box
from steward.math3d import pose_compose, pose_inverse
from steward.scene.schema import SceneState, pose

PIPER_SKILLS = ("pick", "place")
PICK_APPROACH_CLEARANCE_M = 0.038
PICK_MIN_APPROACH_CLEARANCE_M = 0.020
MIN_HOVER_HEIGHT_M = 0.080


class PiperMotion:
    """One planner for the table-mounted six-axis PiPER."""

    skill_names = PIPER_SKILLS
    target_freshness = TargetFreshness(max_age_s=0.7, max_drift_m=0.018)

    def __init__(self, robot_model: dict):
        self.model = mujoco.MjModel.from_xml_path(str(model_path()))
        self._joints = [self.model.joint(name).id for name in ARM_JOINTS]
        self._qadr = self.model.jnt_qposadr[self._joints]
        self._limit = mink.ConfigurationLimit(self.model)
        arm_dofs = set(self.model.jnt_dofadr[self._joints])
        self._freeze = mink.DofFreezingTask(self.model, dof_indices=[i for i in range(self.model.nv) if i not in arm_dofs])
        base = self.model.body("base_link").id
        self._moving_geoms = [
            g for g in range(self.model.ngeom)
            if self.model.geom_bodyid[g] > base and (self.model.geom_contype[g] or self.model.geom_conaffinity[g])
        ]
        wrist = next(link for link in robot_model["body"]["links"] if link["id"] == WRIST_LINK)
        self._tcp = np.asarray(next(a["value"]["tcp-offset"]["p"] for a in wrist["affordances"]
                                    if isinstance(a["value"], dict) and "tcp-offset" in a["value"]))
        # Tool down, with the jaw orientation held across the transfer.
        self._rotation = np.diag([-1.0, 1.0, -1.0])
        self._tool = mink.SO3.from_matrix(self._rotation)

    def _pose_goal(self, config: mink.Configuration, at: np.ndarray) -> dict[str, float]:
        task = mink.FrameTask(WRIST_LINK, "body", position_cost=1.0, orientation_cost=0.6,
                              gain=0.7, lm_damping=0.01)
        task.set_target(mink.SE3.from_rotation_and_translation(self._tool, at - self._rotation @ self._tcp))
        posture = mink.PostureTask(self.model, cost=5e-4)
        posture.set_target(config.q)
        for _ in range(60):
            error = task.compute_error(config)
            if np.linalg.norm(error[:3]) < 0.0018 and np.linalg.norm(error[3:]) < 0.025:
                return dict(zip(ARM_JOINTS, map(float, config.q[self._qadr]), strict=True))
            try:
                v = mink.solve_ik(config, [task, posture], 0.05, solver="daqp", constraints=[self._freeze],
                                  limits=[self._limit], damping=1e-4)
            except mink.NoSolutionFound as exc:
                raise PredictionError("PiPER inverse kinematics found no solution") from exc
            config.integrate_inplace(v, 0.05)
        raise PredictionError(f"PiPER cannot reach TCP {at.round(3).tolist()} in its down-facing working pose")

    def _configuration(self, es: SceneState) -> tuple[mink.Configuration, dict]:
        me = es.entity(es.self_id)
        q = self.model.qpos0.copy()
        for joint in me.rigid.joints:
            q[self.model.joint(joint.id).qposadr[0]] = joint.q.value[0]
        config = mink.Configuration(self.model, q)
        return config, me.pose.value

    def _check_path(self, config: mink.Configuration, start: np.ndarray, checkpoints: list[dict]) -> None:
        """Check each commanded interpolation against native self-contact and the mount surface."""
        data = config.data
        for waypoint in checkpoints:
            end = np.array([waypoint[name] for name in ARM_JOINTS])
            samples = max(2, math.ceil(float(np.max(np.abs(end - start))) / 0.05))
            for sample in np.linspace(start, end, samples + 1):
                data.qpos[self._qadr] = sample
                mujoco.mj_forward(self.model, data)
                if any(contact.dist < -0.004 for contact in data.contact):
                    raise PredictionError("PiPER interpolated path intersects the arm")
                for g in self._moving_geoms:
                    up = data.geom_xmat[g].reshape(3, 3)[2]
                    centre, half = np.split(self.model.geom_aabb[g], 2)
                    low_z = float(data.geom_xpos[g, 2] + up @ centre - np.abs(up) @ half)
                    if low_z < -0.006:
                        raise PredictionError("PiPER interpolated path reaches below the work surface")
            start = end

    def plan(self, es: SceneState, skill: str, params: dict) -> dict:
        if skill not in self.skill_names:
            raise PredictionError(f"PiPER supports {', '.join(self.skill_names)}")
        target_id = params.get("target" if skill == "pick" else "on")
        target = es.find(target_id) if isinstance(target_id, str) and target_id.startswith("e:") else None
        if (target is None or target.existence < 0.6 or target.pose.t is None
                or es.stamp - target.pose.t > self.target_freshness.max_age_s):
            raise PredictionError("observe a current robot-side target before predicting the motion")
        held = next((es.find(r.a["entity"]) for r in es.relations
                     if r.type == "attached" and r.b.get("entity") == es.self_id), None)
        config, mount = self._configuration(es)
        initial = config.q.copy()
        inverse_mount = pose_inverse(mount)
        target_p = np.asarray(target.pose.value["p"], dtype=float)
        local = lambda point: np.asarray(pose_compose(inverse_mount, pose(point))["p"])
        contact = local(target_p)
        plan = {"target": target.id, "targetP": target_p.tolist(), "targetCategory": target.category.value}
        if skill == "pick":
            box, _, top = entity_box(target)
            if held is not None or not any(a.value == "graspable" for a in target.rigid.links[0].affordances):
                raise PredictionError("pick requires an empty gripper and a graspable object")
            if min(2 * box.hx, 2 * box.hy) > 0.09:
                raise PredictionError("the object does not fit between the PiPER fingers")
            contact[2] += 0.005
            clearance_z = local([*target_p[:2], top])[2] + PICK_APPROACH_CLEARANCE_M
        else:
            if held is None:
                raise PredictionError("place requires a confirmed held object")
            if not any(a.value == "support-surface" for a in target.rigid.links[0].affordances):
                raise PredictionError("place requires a visible support surface")
            support, _, top = entity_box(target)
            item, bottom, _ = entity_box(held)
            offset = params.get("offset", [0.0, 0.0])
            if (not isinstance(offset, (tuple, list)) or len(offset) != 2
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in offset)):
                raise PredictionError("placement offset must contain two finite metres")
            padding = math.hypot(item.hx, item.hy) + 0.008
            if abs(offset[0]) > support.hx - padding or abs(offset[1]) > support.hy - padding:
                raise PredictionError("object footprint would be too close to the support edge")
            theta = support.yaw
            spot = [support.cx + offset[0] * math.cos(theta) - offset[1] * math.sin(theta),
                    support.cy + offset[0] * math.sin(theta) + offset[1] * math.cos(theta),
                    top + held.pose.value["p"][2] - bottom + 0.009]
            wrist = config.get_transform_frame_to_world(WRIST_LINK, "body")
            tcp = wrist.translation() + wrist.rotation().as_matrix() @ self._tcp
            contact = local(spot) - (local(held.pose.value["p"]) - tcp)
            clearance_z = contact[2] + 0.030
            plan.update(object=held.id, support=target.id, spot=spot)
        hover = contact.copy()
        hover[2] = max(clearance_z, MIN_HOVER_HEIGHT_M)
        try:
            approach = self._pose_goal(config, hover)
        except PredictionError:
            # At the sides of the table the wrist can reach the object even when
            # the nominal approach above it exceeds a joint limit.
            lower_z = max(clearance_z - PICK_APPROACH_CLEARANCE_M + PICK_MIN_APPROACH_CLEARANCE_M,
                          MIN_HOVER_HEIGHT_M)
            if skill != "pick" or lower_z >= hover[2]:
                raise
            config.update(initial)
            hover[2] = lower_z
            approach = self._pose_goal(config, hover)
        final = self._pose_goal(config, contact)
        program = {"approach": approach, "contact": final}
        checkpoints = [approach, final, approach]
        if skill == "place":
            program["clearView"] = dict(zip(ARM_JOINTS, map(float, self.model.qpos0[self._qadr]), strict=True))
            checkpoints.append(program["clearView"])
        self._check_path(config, initial[self._qadr], checkpoints)
        return plan | {"jointProgram": program}
