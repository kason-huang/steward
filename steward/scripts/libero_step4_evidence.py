"""Shared physical evidence for the Step 4 initial-state-0 trial."""

import mujoco
import numpy as np

CAN = "alphabet_soup_1_main"
BASKET = "basket_1_main"
SITE = "gripper0_grip_site"
PADS = ("gripper0_finger1_pad_collision", "gripper0_finger2_pad_collision")
BASE = "basket_1_g1"


def sample(model, data):
    tool = data.site(SITE)
    body = data.body(CAN)
    velocity = np.empty(6)
    mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_BODY, body.id, velocity, 0)
    contacts = []
    for i in range(data.ncon):
        contact = data.contact[i]
        if contact.efc_address < 0:
            continue
        a, b = (model.geom(j).name for j in contact.geom)
        if not any(name.startswith("alphabet_soup_1_") for name in (a, b)):
            continue
        force = np.empty(6)
        mujoco.mj_contactForce(model, data, i, force)
        contacts.append([a, b, float(force[0]), float(contact.dist)])
    return {
        "can": body.xpos.tolist(), "can_quat": body.xquat.tolist(),
        "basket": data.body(BASKET).xpos.tolist(), "can_velocity": velocity.tolist(),
        "can_tool": ((body.xpos - tool.xpos) @ tool.xmat.reshape(3, 3)).tolist(),
        "contacts": contacts,
        "tool": tool.xpos.tolist(),
        "can_tool_rotation": (tool.xmat.reshape(3, 3).T @ body.xmat.reshape(3, 3)).tolist(),
        "fingers": [float(data.joint(f"gripper0_finger_joint{i}").qpos[0]) for i in (1, 2)],
    }


LIMITS = {
    "pad_force_n": 0.1,
    "support_force_n": 0.05,
    "lift_m": 0.1,
    "slip_m": 0.01,
    "rotation_slip_rad": 0.20,
    "settle_steps": 125,
    "linear_speed_m_s": 0.005,
    "angular_speed_rad_s": 0.05,
}


def force(row, geom):
    return sum(f for a, b, f, _ in row["contacts"] if geom in (a, b) and f > 0)


CASES = ("normal", "empty", "offset", "weak", "rim", "outside")
RIM = tuple(f"basket_1_g{i}" for i in range(2, 6))


def summarize(cycles, physics, initial_z, case="normal"):
    def phase(name):
        rows = [row for row in cycles if row["phase"] == name]
        return physics[rows[0]["step"] - 25:rows[-1]["step"]]

    hold = phase("hover") + phase("carry")
    settled = phase("settle")[-LIMITS["settle_steps"]:]
    assert len(settled) == LIMITS["settle_steps"]
    slip = max(float(np.linalg.norm(np.subtract(row["can_tool"], hold[0]["can_tool"]))) for row in hold)
    rotation = np.asarray(hold[0]["can_tool_rotation"])
    rotation_slip = max(float(np.arccos(np.clip((np.trace(np.asarray(row["can_tool_rotation"])
                             @ rotation.T) - 1) / 2, -1, 1))) for row in hold)
    metrics = {
        "lift_m": min(row["can"][2] - initial_z for row in hold),
        "minimum_pad_force_n": {pad: min(force(row, pad) for row in hold) for pad in PADS},
        "maximum_slip_m": slip,
        "maximum_rotation_slip_rad": rotation_slip,
        "minimum_bottom_force_n": min(force(row, BASE) for row in settled),
        "maximum_settled_linear_speed_m_s": max(float(np.linalg.norm(row["can_velocity"][3:])) for row in settled),
        "maximum_settled_angular_speed_rad_s": max(float(np.linalg.norm(row["can_velocity"][:3])) for row in settled),
        "minimum_withdrawal_clearance_m": min(float(np.linalg.norm(np.subtract(row["tool"], row["can"]))) for row in settled),
        "other_settled_support_contacts": sorted({geom for row in settled for a, b, f, _ in row["contacts"]
                                                   for geom in (a, b) if f > 0 and geom != BASE
                                                   and not geom.startswith("alphabet_soup_1_")}),
        "predicate_after_withdrawal": all(row["success"] for row in cycles[-5:]),
        "final_can_position": settled[-1]["can"],
    }
    grip = (metrics["lift_m"] >= LIMITS["lift_m"]
            and min(metrics["minimum_pad_force_n"].values()) >= LIMITS["pad_force_n"]
            and slip <= LIMITS["slip_m"] and rotation_slip <= LIMITS["rotation_slip_rad"]
            and all(force(row, "floor") == 0 for row in hold))
    placement = (metrics["minimum_bottom_force_n"] >= LIMITS["support_force_n"]
                 and metrics["maximum_settled_linear_speed_m_s"] <= LIMITS["linear_speed_m_s"]
                 and metrics["maximum_settled_angular_speed_rad_s"] <= LIMITS["angular_speed_rad_s"]
                 and not metrics["other_settled_support_contacts"])
    task = grip and placement and metrics["predicate_after_withdrawal"]
    if case == "normal":
        checks = {"grip": grip, "placement": placement, "basket_predicate": metrics["predicate_after_withdrawal"]}
    else:
        closure = phase("close")
        pre_release = physics[:phase("release")[0]["step"] - 1]
        lowering = phase("lower")
        peak_rim = max(sum(force(row, geom) for geom in RIM) for row in lowering)
        rim_penetration = max((max(0.0, -distance)
                               for row in lowering for a, b, f, distance in row["contacts"]
                               if f > 0 and (a in RIM or b in RIM)), default=0.0)
        basket_motion = max(float(np.linalg.norm(np.subtract(row["basket"], lowering[0]["basket"])))
                            for row in lowering)
        rim_while_gripped = any(
            sum(force(row, geom) for geom in RIM) > LIMITS["support_force_n"]
            and all(force(row, pad) > LIMITS["pad_force_n"] for pad in PADS)
            for row in lowering
        )
        metrics.update({
            "max_lift_before_release_m": max(row["can"][2] - initial_z for row in pre_release),
            "max_closure_pad_force_n": {pad: max(force(row, pad) for row in closure) for pad in PADS},
            "maximum_rim_force_n": peak_rim,
            "maximum_rim_penetration_m": rim_penetration,
            "maximum_basket_motion_during_lower_m": basket_motion,
            "tool_height_after_lower_m": lowering[-1]["tool"][2],
            "rim_contact_while_gripped": rim_while_gripped,
            "minimum_floor_force_n": min(force(row, "floor") for row in settled),
            "maximum_can_contact_force_n": max(sum(f for _, _, f, _ in row["contacts"] if f > 0)
                                                for row in pre_release),
        })
        missed = (not grip and not metrics["predicate_after_withdrawal"]
                  and metrics["max_lift_before_release_m"] < LIMITS["lift_m"])
        checks = {
            "empty": {"missed": missed, "no_pad_contact": all(
                max(force(row, pad) for row in pre_release) < LIMITS["pad_force_n"] for pad in PADS)},
            "offset": {"missed": missed},
            "weak": {"missed": missed},
            "rim": {"carried": grip, "rim_contact_while_gripped": rim_while_gripped,
                    "physical_response": basket_motion >= 0.001 or lowering[-1]["tool"][2] >= 0.15,
                    "limited_penetration": rim_penetration <= 0.005},
            "outside": {"carried": grip, "basket_predicate_false": not metrics["predicate_after_withdrawal"],
                        "floor_support": metrics["minimum_floor_force_n"] >= LIMITS["support_force_n"],
                        "no_basket_support": max(force(row, BASE) for row in settled) == 0},
        }[case]
    return {"case": case, "limits": LIMITS, "metrics": metrics, "grip": grip, "placement": placement,
            "task_success": task, "checks": checks, "failure_reasons": [key for key, ok in checks.items() if not ok],
            "pass": all(checks.values())}
