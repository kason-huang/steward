"""Validate a real pi05_libero rollout in an isolated initial-state-0 WSim."""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
from urllib.error import HTTPError
import subprocess
import time

import numpy as np
import mujoco

from libero_step4_evidence import BASE, LIMITS, PADS, force, sample
from probe_libero_step2 import libero_contains
from steward.world.panda_control import LIBERO_TASK_POLICY, LIBERO_TASK_PREFIX_STEPS, policy_libero_target
from steward.world.panda_osc import SUBSTEPS
from steward.world.server import StewardHost
from verify_libero_step3 import PANDA_ID, Robot, call, credentials
from verify_libero_step6 import SCENARIO, capture, infer, policy_preview, save, submit

MAX_PREFIX_SIZE = LIBERO_TASK_PREFIX_STEPS // SUBSTEPS
MAX_PREFIX_TRANSLATION_M = 0.25
WITHDRAWAL_M = 0.08


class PhysicalEvidence:
    """World-side measurement. The policy receives only robot observations."""

    def __init__(self, sim, trace):
        self.sim = sim
        self.trace = trace
        self.initial = sample(sim.model, sim.data)
        self.held = None
        self.first_grasp = None
        self.first_carry = None
        self.first_release = None
        self.peak_gripped_lift = 0.0
        self.best_bottom_run = 0
        self.settled = deque(maxlen=LIMITS["settle_steps"])
        self.row = self.initial

    def capture(self):
        sim = self.sim
        row = {"step": sim.step_count, **sample(sim.model, sim.data), "inside": libero_contains(sim)}
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
        row |= {"tool_clearance_m": clearance, "bottom_force_n": bottom,
                "can_linear_speed_m_s": linear, "can_angular_speed_rad_s": angular}
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


def record(video, frame):
    external, wrist = (frame["images"][role] for role in ("external", "wrist"))
    video.write(np.concatenate((external, wrist), axis=1).tobytes())


def execute(robot, args, evidence, video):
    robot.advance(0)
    start = robot.status()
    assert start["step"] == 0 and start["lastApplied"] is None and not start["leased"]
    lease = credentials(call(robot.url, "/control/acquire", credentials(start)
                             | {"ttlS": 30, "policy": LIBERO_TASK_POLICY}))
    sequence = 0
    prefixes = 0
    reason = "action_budget"
    error = None
    began = time.monotonic()
    try:
        frame = capture(robot)
        record(video, frame)
        with (args.out / "actions.jsonl").open("w", buffering=1) as actions:
            for first in range(0, args.max_actions, args.prefix_size):
                if time.monotonic() - began >= args.max_seconds:
                    reason = "wall_budget"
                    break
                call(robot.url, "/control/renew", lease | {"ttlS": 30})
                before = robot.status()
                assert frame["observationId"] and (frame["episodeId"], frame["step"]) == (
                    lease["episodeId"], before["step"])
                folder = args.out / f"prefix-{prefixes:03d}"
                folder.mkdir()
                prediction = infer(policy_preview(frame), args.port, folder)
                prefixes += 1
                prefix = prediction["actions"][:min(args.prefix_size, args.max_actions - first)]
                # Match the pinned robosuite actuator input saturation. Retain raw outputs.
                normalized = np.clip(prefix, -1, 1).tolist()
                # Each OSC goal is relative to the pose measured at application.
                translation = 0.05 * np.linalg.norm(np.asarray(normalized)[:, :3], axis=1).sum()
                if translation > MAX_PREFIX_TRANSLATION_M:
                    raise ValueError(f"Prefix translation command exceeds 0.25 m: {translation:.3f}")
                policy_libero_target(normalized[0], before["sitePosition"])
                for raw, action in zip(prefix, normalized, strict=True):
                    accepted = submit(robot, lease, sequence, action, frame["observationId"])
                    start_step = accepted["step"]
                    end_step = start_step + SUBSTEPS
                    assert accepted["pending"] and start_step == sequence * SUBSTEPS
                    robot.advance(end_step)
                    state = robot.status()
                    receipt = state["lastCompleted"]
                    assert receipt == {"sequence": sequence, "controlGeneration": lease["controlGeneration"],
                                       "startStep": start_step, "plannedEndStep": end_step, "endStep": end_step}
                    actions.write(json.dumps({
                        "sequence": sequence, "prefix": folder.name,
                        "episodeId": frame["episodeId"], "observation_step": frame["step"],
                        "raw_action": raw, "applied_action": action, "receipt": receipt,
                        "site_position": state["sitePosition"], "goal_position": state["goalPosition"],
                        "finger_position": state["fingerPosition"],
                    }, allow_nan=False) + "\n")
                    sequence += 1
                    updated = capture(robot)
                    assert (updated["episodeId"], updated["step"]) == (lease["episodeId"], end_step)
                    record(video, updated)
                frame = updated
                if evidence.summary()["success"]:
                    reason = "stable_basket_bottom"
                    break
                if prefixes % 10 == 0:
                    result = evidence.summary()
                    print(f"prefixes={prefixes} actions={sequence} step={frame['step']} "
                          f"gripped_lift={result['peak_gripped_lift_m']:.3f} "
                          f"bottom_steps={result['best_bottom_steps']}", flush=True)
    except Exception as exc:
        reason = "execution_error"
        error = repr(exc)
        if isinstance(exc, HTTPError):
            error += ": " + exc.read().decode("utf-8")
    finally:
        state = robot.status()
        if state["leased"]:
            robot.stop(lease)
    before_stop_hold = evidence.summary()
    # Observe continued physical support after releasing the controller lease.
    for _ in range(20):
        robot.advance(robot.status()["step"] + SUBSTEPS)
        record(video, capture(robot))
    stopped = robot.status()
    after_stop_hold = evidence.summary()
    no_late_action = (stopped["lastApplied"] == state["lastApplied"]
                      and not stopped["leased"] and not stopped["pending"])
    success = (reason == "stable_basket_bottom" and before_stop_hold["success"]
               and after_stop_hold["success"] and no_late_action and stopped["stopState"] == "stopped")
    return {
        "success": success, "reason": reason, "error": error, "initial_state_index": 0,
        "episode_id": lease["episodeId"], "generation": lease["controlGeneration"],
        "actions_applied": sequence, "inferences": prefixes, "prefix_size": args.prefix_size,
        "before_stop_hold": before_stop_hold, "after_stop_hold": after_stop_hold,
        "no_late_action": no_late_action, "stop_status": stopped,
    }


def rollout(host, args):
    robot = Robot(host.robot_servers[PANDA_ID].url)
    command = [args.ffmpeg, "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", "512x256", "-framerate", "20", "-i", "pipe:0",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(args.out / "external-wrist.mp4")]
    with gzip.open(args.out / "wsim-physics.jsonl.gz", "wt", compresslevel=4) as trace:
        evidence = PhysicalEvidence(host.sim, trace)
        unsubscribe = host.sim.subscribe_steps(evidence.capture)
        try:
            with subprocess.Popen(command, stdin=subprocess.PIPE) as video:
                try:
                    result = execute(robot, args, evidence, video.stdin)
                finally:
                    video.stdin.close()
                if video.wait() != 0:
                    raise RuntimeError("Could not encode synchronized video")
        finally:
            unsubscribe()
    save(args.out / "summary.json", result)
    print(json.dumps({key: result[key] for key in ("success", "reason", "error", "actions_applied", "inferences")}),
          flush=True)
    return result["success"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--port", type=int, default=18065)
    parser.add_argument("--prefix-size", type=int, choices=range(1, MAX_PREFIX_SIZE + 1), default=5)
    parser.add_argument("--max-actions", type=int, choices=range(1, 401), metavar="1..400", default=280)
    parser.add_argument("--max-seconds", type=int, default=420)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True)
    save(args.out / "configuration.json", {
        "scenario": SCENARIO, "initial_state_index": 0, "root": str(args.root.resolve()),
        "policy_port": args.port, "prefix_size": args.prefix_size,
        "max_actions": args.max_actions, "max_wall_seconds": args.max_seconds,
        "mujoco": mujoco.__version__, "control_period_s": 0.05, "physics_period_s": 0.002,
        "inference_clock": "frozen", "prefix_translation_command_limit_m": MAX_PREFIX_TRANSLATION_M,
    })
    host = StewardHost(args.root, scenario=SCENARIO, freeze_idle=True, dispatch_task=False,
                       speed=float("inf")).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(rollout, host, args)
            while not work.done():
                host.pump(timeout=.002)
            return 0 if work.result() else 1
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
