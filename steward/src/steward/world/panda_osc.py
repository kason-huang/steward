"""Fixed LIBERO OSC_POSE profile for the prepared Panda model.

Equations follow robosuite 1.4.1 controllers/osc.py and utils/control_utils.py.
See the robosuite license distributed with the prepared model.
"""

import mujoco
import numpy as np

ARM_JOINTS = tuple(f"robot0_joint{i}" for i in range(1, 8))
ARM_ACTUATORS = tuple(f"robot0_torq_j{i}" for i in range(1, 8))
FINGER_ACTUATORS = tuple(f"gripper0_gripper_finger_joint{i}" for i in (1, 2))
SUBSTEPS = 25


class PandaOsc:
    def __init__(self, model, data, initial):
        self.model = model
        self.data = data
        self.site = model.site("gripper0_grip_site").id
        self.position_indices = np.array([model.joint(name).qposadr[0] for name in ARM_JOINTS])
        self.dof_indices = np.array([model.joint(name).dofadr[0] for name in ARM_JOINTS])
        self.arm = np.array([model.actuator(name).id for name in ARM_ACTUATORS])
        self.fingers = np.array([model.actuator(name).id for name in FINGER_ACTUATORS])
        self.arm_min, self.arm_max = model.actuator_ctrlrange[self.arm].T
        finger_min, finger_max = model.actuator_ctrlrange[self.fingers].T
        self.finger_center = (finger_min + finger_max) / 2
        self.finger_scale = (finger_max - finger_min) / 2
        self.goal_pos = np.array(initial["goal_position"])
        self.goal_ori = np.array(initial["goal_rotation"])
        self.initial_joint = np.array(initial["reference"])
        self.grip = np.array(initial["grip_target"])
        self.jacobian = np.empty((6, model.nv))
        self.mass = np.empty((model.nv, model.nv))
        # MuJoCo releases supported by Steward ship both binding signatures.
        try:
            mujoco.mj_fullM(model, data, self.mass)
        except TypeError:
            self._mass_uses_data = False
        else:
            self._mass_uses_data = True
        for field, shape in (("goal_pos", (3,)), ("goal_ori", (3, 3)),
                             ("initial_joint", (7,)), ("grip", (2,))):
            values = getattr(self, field)
            if values.shape != shape or not np.isfinite(values).all():
                raise ValueError(f"Invalid initial Panda OSC {field}")

    def hold(self):
        self.goal_pos = self.data.site_xpos[self.site].copy()
        self.goal_ori = self.data.site_xmat[self.site].reshape(3, 3).copy()

    def apply(self, action=None, *, update_goal=False):
        """Write one physics substep of control after MuJoCo forward kinematics."""
        data = self.data
        site_pos = data.site_xpos[self.site]
        site_ori = data.site_xmat[self.site].reshape(3, 3)
        if update_goal:
            delta = np.clip(action[:6], -1.0, 1.0)
            self.goal_pos = site_pos + 0.05 * delta[:3]
            rotation = 0.5 * delta[3:]
            angle = np.linalg.norm(rotation)
            if angle > 0:
                quat = np.empty(4)
                matrix = np.empty(9)
                mujoco.mju_axisAngle2Quat(quat, rotation / angle, angle)
                mujoco.mju_quat2Mat(matrix, quat)
                self.goal_ori = matrix.reshape(3, 3) @ site_ori

        mujoco.mj_jacSite(self.model, data, self.jacobian[:3], self.jacobian[3:], self.site)
        if self._mass_uses_data:
            mujoco.mj_fullM(self.model, data, self.mass)
        else:
            mujoco.mj_fullM(self.model, self.mass, data.qM)
        jacobian = self.jacobian[:, self.dof_indices]
        mass = self.mass[np.ix_(self.dof_indices, self.dof_indices)]
        inverse_mass_jt = np.linalg.solve(mass, jacobian.T)
        inverse_inertia = jacobian @ inverse_mass_jt
        nullspace = np.eye(len(self.arm)) - inverse_mass_jt @ np.linalg.pinv(inverse_inertia) @ jacobian

        orientation_error = 0.5 * np.cross(site_ori.T, self.goal_ori.T).sum(axis=0)
        error = np.concatenate((self.goal_pos - site_pos, orientation_error))
        wrench = 150 * error - 2 * np.sqrt(150) * (self.jacobian @ data.qvel)
        wrench[:3] = np.linalg.pinv(inverse_inertia[:3, :3]) @ wrench[:3]
        wrench[3:] = np.linalg.pinv(inverse_inertia[3:, 3:]) @ wrench[3:]
        joint_error = self.initial_joint - data.qpos[self.position_indices]
        pose_torque = mass @ (10 * joint_error - 2 * np.sqrt(10) * data.qvel[self.dof_indices])
        torque = jacobian.T @ wrench + data.qfrc_bias[self.dof_indices] + nullspace.T @ pose_torque
        if not np.isfinite(torque).all():
            raise FloatingPointError("Nonfinite Panda OSC torque")
        data.ctrl[self.arm] = np.clip(torque, self.arm_min, self.arm_max)
        if action is not None:
            self.grip = np.clip(self.grip + 0.01 * np.sign(action[6]) * np.array([-1, 1]), -1, 1)
        data.ctrl[self.fingers] = self.finger_center + self.finger_scale * self.grip
