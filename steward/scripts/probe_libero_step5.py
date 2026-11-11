"""Check Panda observation transport and FK against WSim in a separate host."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from steward.ego.panda_observation import PandaObservationRuntime
from steward.ego.runtime import RobotLink
from steward.math3d import matrix_from_quat
from steward.world.panda_sensors import CAMERAS
from steward.world.server import StewardHost
from verify_libero_step3 import Robot, call


def probe(host, out):
    url = host.robot_servers["w:panda"].url
    observer = PandaObservationRuntime(RobotLink(url))
    robot = Robot(url)
    sim = host.sim
    robot.advance(0)
    lease = robot.acquire()
    report = []
    for index in range(3):
        if index:
            robot.action(lease, index - 1, [0.3, -0.4, 0.2, 0.2, 0, -0.2, 1])
            robot.advance(index * 25)
        observation = observer.read()
        assert observation["episodeId"] == sim.episode and observation["step"] == index * 25
        assert observer.read() is None, "duplicate observation accepted"
        with sim.lock:
            data = mujoco.MjData(sim.model)
            mujoco.mj_copyData(data, sim.model, sim.data)
            mujoco.mj_forward(sim.model, data)
        poses = {role: {"position": data.camera(name).xpos,
                        "rotation": data.camera(name).xmat.reshape(3, 3),
                        "observed": observation["cameras"][role]["pose"]} for role, name in CAMERAS.items()}
        site = data.site("gripper0_grip_site")
        poses["tool"] = {"position": site.xpos, "rotation": site.xmat.reshape(3, 3),
                         "observed": observation["tool"]}
        errors = {}
        for name, expected in poses.items():
            observed = expected["observed"]
            errors[name] = {
                "position_m": float(np.max(np.abs(np.subtract(observed["p"], expected["position"])))),
                "rotation_matrix": float(np.max(np.abs(matrix_from_quat(observed["q"]) - expected["rotation"]))),
            }
            assert max(errors[name].values()) < 1e-8, (name, errors[name])
        for role, pixels in observation["images"].items():
            assert pixels.shape == (256, 256, 3) and pixels.dtype == np.uint8
            Image.fromarray(pixels).save(out / f"step-{index * 25}-{role}.png")
        report.append({"episodeId": observation["episodeId"], "step": observation["step"],
                       "stamp": observation["stamp"], "calibrationId": observation["calibrationId"],
                       "encoders": observation["encoders"], "errors": errors})
    robot.stop(lease)
    calibration = observer.link.panda_observation()["calibration"]
    (out / "calibration.json").write_text(json.dumps(calibration, indent=2) + "\n")
    call(host.wca.url, "/wca/episode", {"action": "reset"})
    restarted = observer.read()
    assert restarted["episodeId"] > report[-1]["episodeId"] and restarted["step"] == 0
    result = {"frames": report, "duplicatesRejected": len(report), "resetEpisode": restarted["episodeId"],
              "resetStep": restarted["step"]}
    (out / "transport-fk.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Verified {len(report)} synchronized frames and FK", flush=True)



def sample_replay(host, out, protocol):
    record = json.loads(protocol.read_text())
    assert record["initial_index"] == 0
    url = host.robot_servers["w:panda"].url
    observer = PandaObservationRuntime(RobotLink(url))
    robot = Robot(url)
    robot.advance(0)
    lease = robot.acquire()
    samples = []
    actions = record["actions"]
    try:
        for index, (phase, action) in enumerate(actions):
            if index % 20 == 0:
                call(url, "/control/renew", lease | {"ttlS": 30})
            robot.action(lease, index, action)
            step = (index + 1) * 25
            robot.advance(step)
            assert robot.status()["lastCompleted"]["sequence"] == index
            phase_ended = index + 1 == len(actions) or actions[index + 1][0] != phase
            if not phase_ended and index % 5:
                continue
            observation = observer.read()
            assert observation["step"] == step
            images = {}
            for role, pixels in observation["images"].items():
                name = f"{index:03}-{phase}-{role}.png"
                Image.fromarray(pixels).save(out / name)
                images[role] = name
            depth = f"{index:03}-{phase}-depth.npz"
            np.savez_compressed(out / depth, **observation["depth"])
            samples.append({k: value for k, value in observation.items() if k not in ("images", "depth")}
                           | {"images": images, "depth": depth, "phase": phase})
    finally:
        robot.stop(lease)
    (out / "observations.json").write_text(json.dumps(samples, indent=2) + "\n")
    print(f"Replayed {len(actions)} cycles, sampled {len(samples)} synchronized observations", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    host = StewardHost(args.root, scenario="libero-object-alphabet-soup-panda",
                       freeze_idle=True, dispatch_task=False, speed=float("inf")).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = (pool.submit(sample_replay, host, args.out, args.protocol)
                    if args.protocol else pool.submit(probe, host, args.out))
            while not task.done():
                host.pump(timeout=0.002)
            task.result()
    finally:
        host.stop()


if __name__ == "__main__":
    main()
