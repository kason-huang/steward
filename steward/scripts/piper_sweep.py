"""Run paired PiPER simulations with and without target-motion monitoring."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from steward.ego.bodies import robot_model, runtime_config
from steward.ego.monitor import MonitorConfig
from steward.ego.runtime import EgoRuntime, RobotLink, RuntimeConfig
from steward.world.goals import score_placement
from steward.world.server import StewardHost

ROBOT = "w:robot-1"
GOAL = {"entity": "w:black-box", "support": "w:left-tray", "offset": [0, 0], "toleranceM": 0.035}


def run_trial(root: Path, seed: int, *, watch: bool, move: bool, speed: float) -> dict:
    host = StewardHost(root, scenario="piper-workstation", speed=speed, seed=seed).start()
    sim = host.sim
    link = RobotLink(host.robot_servers[ROBOT].url)
    body = host.embodiments[ROBOT]["id"]
    config = runtime_config(body, RuntimeConfig(monitor=MonitorConfig(watch_targets=watch)))
    runtime = EgoRuntime(link, robot_model(body), config)
    runtime.start()
    started = time.monotonic()
    initial_time = sim.time
    sample = {"seed": seed, "watchTargets": watch, "disturbance": move, "jobs": [],
              "recoveryAttempts": 0, "resumeAuthorizations": 0, "stage": "observe"}

    def await_value(read, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            host.pump(0.01)
            value = read()
            if value is not None:
                return value
        raise TimeoutError(f"deadline while {sample['stage']}")

    def observe(category, color):
        def fresh():
            candidates = [obj for obj in runtime.summary()["objects"]
                          if obj["category"] == category and obj["color"] == color
                          and obj["existence"] >= 0.75 and obj["lastSeenAgoS"] < 0.25]
            return min(candidates, key=lambda obj: obj["lastSeenAgoS"], default=None)
        return await_value(fresh)

    def start(skill, params):
        sample["stage"] = f"predict {skill}"
        receipt = runtime.predict(skill, params)
        if not receipt["withinRiskLimit"]:
            raise ValueError("; ".join(receipt["issues"]))
        sample["stage"] = skill
        return runtime.run(receipt["id"])

    def finish(job):
        complete = await_value(lambda: result if (result := runtime.job(job["id"]))["finished"] is not None else None,
                               timeout=22.0)
        sample["jobs"].append({key: complete[key] for key in ("id", "skill", "state", "message", "started", "finished")})
        return complete

    try:
        left = observe("tray", "blue")["id"]
        box = observe("box", "black")["id"]
        job = start("pick", {"target": box})
        if move:
            await_value(lambda: frame if (frame := link.proprioception())["joints"]["joint2"] >= 0.045 else None)
            with sim.lock:
                edit = host.edit({"action": "move", "entity": "w:black-box", "on": "w:table", "offset": [-0.05, 0]})
                sample["editAtS"] = sim.time
            if not edit["ok"]:
                raise RuntimeError("disturbance rejected")
        pick = finish(job)
        if pick["state"] != "succeeded":
            sample["recoveryAttempts"] += 1
            status = link.motion_status()
            if status["reason"] == "protection":
                hold = await_value(lambda: state if (state := link.motion_status())["state"] == "held" else None)
                sample["heldObservedAtS"] = hold["t"]
                runtime.resume_motion(hold["epoch"])
                sample["resumeAuthorizations"] += 1
            box = observe("box", "black")["id"]
            pick = finish(start("pick", {"target": box}))
        if pick["state"] == "succeeded":
            place = finish(start("place", {"on": left}))
            if place["state"] == "succeeded":
                sample["stage"] = "verify"
                verdict = runtime.verify_placement(place["id"],
                                                   {"entity": box, "support": left, "offset": [0, 0],
                                                    "toleranceM": 0.035})
                sample["robotIntent"] = verdict["intent"]
    except (TimeoutError, ValueError, PermissionError) as exc:
        sample["error"] = str(exc)
    finally:
        sample["world"] = score_placement(sim.world_state(), GOAL)
        sample["events"] = runtime.monitor.events(0)["events"]
        sample["wallS"] = time.monotonic() - started
        sample["simS"] = sim.time - initial_time
        runtime.stop()
        host.stop()
    return sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    parser.add_argument("--speed", type=float, default=2.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="steward-piper-sweep-") as root, args.out.open("w", encoding="utf-8") as stream:
        for seed in args.seeds:
            for move in (False, True):
                for watch in (False, True):
                    result = run_trial(Path(root), seed, watch=watch, move=move, speed=args.speed)
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                    stream.flush()
                    print(f"seed {seed}  move={move}  monitor={watch}  world={result['world']['complete']}  "
                          f"ego={result.get('robotIntent', 'unknown')}  retries={result['recoveryAttempts']} "
                          f"{result.get('error', '')}", flush=True)


if __name__ == "__main__":
    main()
