"""Episode-scoped Panda control state, used under the WorldSim lock."""

import secrets
import time

import mujoco
import numpy as np

from steward.world.panda_osc import PandaOsc, SUBSTEPS

STOP_WINDOW = 25
ARM_SPEED_LIMIT = 0.02
FINGER_SPEED_LIMIT = 0.005
SITE_SPEED_LIMIT = 0.01
SITE_ANGULAR_LIMIT = 0.05


class ControlConflict(ValueError):
    pass


class PandaControl:
    def __init__(self, model, data, initial, episode):
        self.osc = PandaOsc(model, data, initial)
        self.model = model
        self.data = data
        self.episode = episode
        self.finger_dofs = np.array([model.joint(f"gripper0_finger_joint{i}").dofadr[0] for i in (1, 2)])
        self.finger_qpos = np.array([model.joint(f"gripper0_finger_joint{i}").qposadr[0] for i in (1, 2)])
        self.retired = False
        self.fault = None
        self.generation = 0
        self.deadline = None
        self.lease_id = None
        self.sequence = -1
        self.pending = None
        self.running = None
        self.last_applied = None
        self.last_completed = None
        self.capture_hold = False
        self.still_steps = 0
        self.reason = "reset"
        by_name = initial["ctrl_by_name"]
        actuator_names = [model.actuator(i).name for i in range(model.nu)]
        if set(by_name) != set(actuator_names) or not all(np.isfinite(tuple(by_name.values()))):
            raise ValueError("Prepared Panda controls do not match compiled actuators")
        data.ctrl[:] = [by_name[name] for name in actuator_names]
        mujoco.mj_forward(model, data)

    def _expire(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.revoke("expired")

    def revoke(self, reason):
        self.generation += 1
        self.deadline = None
        self.lease_id = None
        self.pending = self.running = None
        self.capture_hold = True
        self.still_steps = 0
        self.reason = reason

    def retire(self):
        self.revoke("retired")
        self.retired = True

    def require_active(self):
        if self.retired:
            raise ControlConflict("Panda episode has ended")

    def request(self, operation, body, step):
        self.require_active()
        self._expire()
        if operation == "status":
            return self.status(step)
        if self.fault is not None:
            raise ControlConflict("Panda controller fault requires an episode reset")
        if type(body.get("episodeId")) is not int or body["episodeId"] != self.episode:
            raise ControlConflict("Stale Panda episode")
        if type(body.get("controlGeneration")) is not int or body["controlGeneration"] != self.generation:
            raise ControlConflict("Stale Panda control generation")
        if operation == "acquire":
            if self.deadline is not None:
                raise ControlConflict("Panda already has an active lease")
            self.deadline = time.monotonic() + self._ttl(body)
            self.lease_id = secrets.token_urlsafe(24)
            self.generation += 1
            self.sequence = -1
            self.reason = "leased"
            return self.status(step) | {"leaseId": self.lease_id}
        if self.deadline is not None and body.get("leaseId") != self.lease_id:
            raise ControlConflict("Invalid Panda lease")
        if operation == "stop":
            if self.deadline is not None:
                self.revoke("stopped")
        elif self.deadline is None:
            raise ControlConflict("Panda has no active lease")
        elif operation == "renew":
            self.deadline = time.monotonic() + self._ttl(body)
        elif operation == "action":
            sequence = body.get("sequence")
            values = body.get("action")
            if type(sequence) is not int or sequence < 0:
                raise ValueError("sequence must be a nonnegative integer")
            if not isinstance(values, list) or len(values) != 7 or any(
                type(value) not in (int, float) or (type(value) is float and not np.isfinite(value)) for value in values
            ):
                raise ValueError("action must contain seven finite numbers")
            if sequence <= self.sequence or self.pending is not None:
                raise ControlConflict("Panda action is stale or the pending slot is full")
            self.pending = (sequence, np.array([min(1, max(-1, value)) for value in values], dtype=float), self.generation)
            self.sequence = sequence
        return self.status(step)

    @staticmethod
    def _ttl(body):
        ttl = body.get("ttlS")
        if type(ttl) not in (int, float) or not 0.01 <= ttl <= 30:
            raise ValueError("ttlS must be between 0.01 and 30 wall seconds")
        return ttl

    def site_velocity(self):
        velocity = np.empty(6)
        mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_SITE, self.osc.site, velocity, 0)
        return velocity

    def status(self, step):
        osc = self.osc
        velocity = self.site_velocity()
        return {
            "episodeId": self.episode, "controlGeneration": self.generation, "step": step,
            "leased": self.deadline is not None, "reason": self.reason, "fault": self.fault,
            "acceptedSequence": self.sequence, "lastApplied": self.last_applied,
            "lastCompleted": self.last_completed, "pending": self.pending is not None,
            "stopState": ("leased" if self.deadline is not None else
                          "stopped" if self.still_steps >= STOP_WINDOW else "unknown"),
            "stillSteps": self.still_steps,
            "siteLinearSpeed": float(np.linalg.norm(velocity[3:])),
            "siteAngularSpeed": float(np.linalg.norm(velocity[:3])),
            "sitePosition": self.data.site_xpos[osc.site].tolist(),
            "siteRotation": self.data.site_xmat[osc.site].reshape(3, 3).tolist(),
            "armPosition": self.data.qpos[osc.position_indices].tolist(),
            "armVelocity": self.data.qvel[osc.dof_indices].tolist(),
            "fingerPosition": self.data.qpos[self.finger_qpos].tolist(),
            "fingerVelocity": self.data.qvel[self.finger_dofs].tolist(),
            "goalPosition": osc.goal_pos.tolist(), "goalRotation": osc.goal_ori.tolist(),
            "gripTarget": osc.grip.tolist(), "control": self.data.ctrl.tolist(),
        }

    def before_step(self, step):
        self._expire()
        mujoco.mj_forward(self.model, self.data)
        if self.capture_hold:
            self.osc.hold()
            self.capture_hold = False
        update_goal = self.running is None and self.pending is not None
        applied = None
        if update_goal:
            sequence, action, generation = self.pending
            self.pending = None
            self.running = (action, step + SUBSTEPS)
            applied = {"sequence": sequence, "controlGeneration": generation,
                       "startStep": step, "plannedEndStep": step + SUBSTEPS}
        self.osc.apply(self.running[0] if self.running is not None else None, update_goal=update_goal)
        if applied is not None:
            self.last_applied = applied

    def after_step(self, step):
        if self.running is not None and step == self.running[1]:
            self.last_completed = {**self.last_applied, "endStep": step}
            self.running = None
        if self.deadline is not None:
            return
        osc = self.osc
        velocity = self.site_velocity()
        still = (np.abs(self.data.qvel[osc.dof_indices]).max() <= ARM_SPEED_LIMIT
                 and np.abs(self.data.qvel[self.finger_dofs]).max() <= FINGER_SPEED_LIMIT
                 and np.linalg.norm(velocity[3:]) <= SITE_SPEED_LIMIT
                 and np.linalg.norm(velocity[:3]) <= SITE_ANGULAR_LIMIT)
        self.still_steps = self.still_steps + 1 if still else 0
