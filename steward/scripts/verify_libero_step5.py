"""Replay frozen Step 4 actions through a supervised Panda Ego Runtime."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from steward.scene.validate import validate_ego
from verify_libero_step3 import Robot, call


def replay(addresses, protocol, out):
    robot = Robot(addresses["robots"]["w:panda"]["url"])
    ego = addresses["robots"]["w:panda"]["egoApi"]
    robot.advance(0)
    lease = robot.acquire()
    observations = []
    previews = []
    try:
        initial = call(ego, "/ego/observe", {})
        assert initial["step"] == 0
        actions = protocol["actions"]
        for index, (phase, action) in enumerate(actions):
            if index % 20 == 0:
                call(robot.url, "/control/renew", lease | {"ttlS": 30})
            robot.action(lease, index, action)
            step = (index + 1) * 25
            robot.advance(step)
            assert robot.status()["lastCompleted"]["sequence"] == index
            phase_end = index + 1 == len(actions) or actions[index + 1][0] != phase
            if index % 5 and not phase_end:
                continue
            state = call(ego, "/ego/observe", {})
            assert state["step"] == step and state["episodeId"] == initial["episodeId"]
            assert not validate_ego(state["scene"])
            observations.append({"phase": phase, **state})
            if index in (0, 149, 231, 509):
                preview = call(ego, "/ego/observation")
                assert preview["step"] == step
                previews.append(preview)
    finally:
        robot.stop(lease)
    (out / "policy-preview.json").write_text(json.dumps(previews) + "\n")
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    index = protocol["initial_index"]
    scenario = "libero-object-alphabet-soup-panda" + (f"-init-{index}" if index else "")
    args.out.mkdir(parents=True)
    with (args.out / "service.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "steward.cli", "--root", str(args.root), "up", "--scenario", scenario,
             "--wca-port", "0", "--robot-port", "0", "--api-port", "0", "--speed", "max", "--freeze-idle",
             "--no-dispatch-task"],
            stdout=subprocess.PIPE, stderr=log, text=True,
        )
        try:
            address = json.loads(server.stdout.readline())
            history = replay(address, protocol, args.out)
            (args.out / "ego-state.json").write_text(json.dumps(history, indent=2) + "\n")
            result = {"case": protocol.get("case", "normal"), "initial_index": index,
                      "samples": len(history), "final": history[-1]["judgment"]}
            (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(result, flush=True)
            before = history[-1]["episodeId"]
            call(address["wca"], "/wca/episode", {"action": "reset"})
            fresh_ego = call(address["wca"], "/console/state")["robots"]["w:panda"]["ego"]
            assert fresh_ego is None or fresh_ego["stamp"] == 0
            # The supervisor assigns a new Ego API endpoint on reset. The world
            # state remains at time zero until the next fixed-action client runs.
            (args.out / "reset.json").write_text(json.dumps({"priorEpisode": before, "ego": fresh_ego}) + "\n")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
