"""Replay the frozen Step 4 action table through the WSim robot HTTP port."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import subprocess

import numpy as np

from libero_step4_evidence import sample, summarize
from probe_libero_step2 import libero_contains
from verify_libero_step3 import PANDA_ID, Robot, call, dump, request, scenario_name
from steward.world.server import StewardHost


def replay(host, source, out):
    protocol = json.loads((source / "protocol.json").read_text())
    assert protocol["warmup_cycles"] == 10
    initial = json.loads((source / "initial.json").read_text())
    loaded = call(host.wca.url, "/wca/episode", {"action": "load", "scenario": scenario_name(protocol["initial_index"], panda=True)})
    robot = Robot(loaded["robots"][PANDA_ID])
    robot.advance(0)
    sim = host.sim
    assert sim.step_count == 0 and sim.timestep == 0.002
    for name, values in initial["joints"].items():
        for field, expected in values.items():
            np.testing.assert_array_equal(getattr(sim.data.joint(name), field), expected)

    cycles, physics = [], []
    frames, wrist = out / "frames", out / "wrist"
    frames.mkdir()
    wrist.mkdir()

    def capture():
        physics.append({"step": sim.step_count, **sample(sim.model, sim.data)})

    dispose = sim.subscribe_steps(capture)
    lease = robot.acquire()
    try:
        for index, (phase, action) in enumerate(protocol["actions"]):
            if index % 20 == 0:
                call(robot.url, "/control/renew", lease | {"ttlS": 30})
            accepted = robot.action(lease, index, action)
            assert accepted["pending"] and accepted["acceptedSequence"] == index
            end = (index + 1) * 25
            robot.advance(end)
            state = robot.status()
            assert state["step"] == end and len(physics) == end
            assert state["lastCompleted"] == {
                "sequence": index, "controlGeneration": lease["controlGeneration"],
                "startStep": index * 25, "plannedEndStep": end, "endStep": end,
            }
            cycles.append({"cycle": index + 1, "phase": phase, "action": action,
                           "success": libero_contains(sim), "control": state, **physics[-1]})
            name = f"{index:03}.png"
            (frames / name).write_bytes(request(host.wca.url, "/wca/render?camera=overview&width=256&height=256"))
            (wrist / name).write_bytes(request(robot.url, "/camera/rgb?camera=wrist&width=256&height=256"))
            if (index + 1) % 100 == 0:
                print(f"replayed {index + 1} cycles through the robot port", flush=True)
    finally:
        dispose()
        robot.stop(lease)
    for name, rows in (("wsim-trace", cycles), ("wsim-physics", physics)):
        with gzip.open(out / f"{name}.json.gz", "wt") as stream:
            json.dump(rows, stream, separators=(",", ":"))
    result = summarize(cycles, physics, initial["joints"]["alphabet_soup_1_joint0"]["qpos"][2], protocol.get("case", "normal"))
    dump(out / "wsim-summary.json", result)
    print(result, flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    host = StewardHost(args.root, freeze_idle=True, dispatch_task=False, speed=float("inf")).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(replay, host, args.source, args.out)
            while not work.done():
                host.pump(timeout=0.002)
            result = work.result()
    finally:
        host.stop()
    subprocess.run([args.ffmpeg, "-loglevel", "error", "-framerate", "20", "-i",
                    str(args.out / "frames/%03d.png"), "-framerate", "20", "-i",
                    str(args.out / "wrist/%03d.png"), "-filter_complex", "hstack", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(args.out / "wsim.mp4")], check=True)


if __name__ == "__main__":
    main()
