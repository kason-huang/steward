"""World-only physical evaluation shared by native LIBERO and WSim (Python 3.8+)."""

from collections import deque
import json
import numpy as np

from libero_step4_evidence import BASE, LIMITS, PADS, sample, force

SUBSTEPS = 25
WITHDRAWAL_M = .08


def contains(model, data):
    site = data.site("basket_1_contain_region")
    radius = np.abs(site.xmat.reshape(3, 3) @ model.site("basket_1_contain_region").size)
    lower, upper = site.xpos - radius, site.xpos + radius
    lower[2] -= .01
    point = data.body("alphabet_soup_1_main").xpos
    return bool(np.all(point > lower) and np.all(point < upper))


class PhysicalEvidence:
    """World-side measurement. The policy receives only robot observations."""

    def __init__(self, model, data, trace):
        self.model, self.data = model, data
        self.trace = trace
        self.initial = sample(model, data)
        self.held = None
        self.first_grasp = None
        self.first_carry = None
        self.first_release = None
        self.peak_gripped_lift = 0.0
        self.best_bottom_run = 0
        self.settled = deque(maxlen=LIMITS["settle_steps"])
        self.row = dict(self.initial, step=0, inside=contains(model, data))
        self.last_step = 0

    def capture(self, step):
        if step != self.last_step + 1:
            raise ValueError("Physical trace has a missing, duplicate or reordered step")
        self.last_step = step
        row = {"step": step, **sample(self.model, self.data), "inside": contains(self.model, self.data)}
        self.row = row
        if all(force(row, pad) >= LIMITS["pad_force_n"] for pad in PADS):
            if self.held is None:
                self.held = row
            lift = row["can"][2] - self.initial["can"][2]
            self.peak_gripped_lift = max(self.peak_gripped_lift, lift)
            if not force(row, "floor") and lift >= .015 and self.first_grasp is None:
                self.first_grasp = row
            if (self.first_grasp is not None and not force(row, "floor") and lift >= .015
                    and row["step"] - self.held["step"] >= SUBSTEPS
                    and np.linalg.norm(np.subtract(row["can"][:2], self.held["can"][:2])) >= .035
                    and self.first_carry is None):
                self.first_carry = row
        else:
            self.held = None
        if self.first_carry is not None and self.first_release is None:
            carried_aperture = abs(self.first_carry["fingers"][0] - self.first_carry["fingers"][1])
            aperture = abs(row["fingers"][0] - row["fingers"][1])
            if (aperture >= carried_aperture + 0.004
                    and all(force(row, pad) < LIMITS["support_force_n"] for pad in PADS)):
                self.first_release = row
        clearance = float(np.linalg.norm(np.subtract(row["tool"], row["can"])))
        linear = float(np.linalg.norm(row["can_velocity"][3:]))
        angular = float(np.linalg.norm(row["can_velocity"][:3]))
        bottom = force(row, BASE)
        other_support = any(f > 0 and BASE not in (a, b) for a, b, f, _ in row["contacts"])
        row.update({"tool_clearance_m": clearance, "bottom_force_n": bottom,
                "can_linear_speed_m_s": linear, "can_angular_speed_rad_s": angular})
        if (row["inside"] and bottom >= LIMITS["support_force_n"] and not other_support
                and clearance >= WITHDRAWAL_M and linear <= LIMITS["linear_speed_m_s"]
                and angular <= LIMITS["angular_speed_rad_s"]):
            self.settled.append(row)
            self.best_bottom_run = max(self.best_bottom_run, len(self.settled))
        else:
            self.settled.clear()
        self.trace.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")

    def summary(self):
        window = list(self.settled)
        result = {
            "traceComplete": True, "physicsSteps": self.last_step,
            "initial_can": self.initial["can"], "initial_basket": self.initial["basket"],
            "grasp": self.first_grasp, "carry": self.first_carry, "release": self.first_release,
            "peak_gripped_lift_m": self.peak_gripped_lift,
            "stable_bottom_steps": len(window), "best_bottom_steps": self.best_bottom_run,
            "required_bottom_steps": LIMITS["settle_steps"], "required_withdrawal_m": WITHDRAWAL_M,
            "last_step": self.row,
        }
        if window:
            result["bottom_window"] = {
                "first_step": window[0]["step"], "last_step": window[-1]["step"],
                "minimum_bottom_force_n": min(row["bottom_force_n"] for row in window),
                "minimum_clearance_m": min(row["tool_clearance_m"] for row in window),
                "maximum_linear_speed_m_s": max(row["can_linear_speed_m_s"] for row in window),
                "maximum_angular_speed_rad_s": max(row["can_angular_speed_rad_s"] for row in window),
            }
        result["success"] = bool(self.first_release and len(window) == LIMITS["settle_steps"]
                                 and window[0]["step"] >= self.first_release["step"])
        return result
