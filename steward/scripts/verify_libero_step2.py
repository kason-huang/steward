"""Verify pinned LIBERO model fidelity, reset repeatability and WCA integration."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import mujoco
import numpy as np

from probe_libero_step2 import geometry_differences
from steward.scene.libero import OBJECTS, load_scene, scenario_name
from steward.world.sim import WorldSim

LIMITS = {
    "reset_absolute_error": 1e-9,
    "geometry_absolute_error": 2e-6,
    "body_relative_error": 1e-5,
    "settled_position_m": 0.002,
    "settled_rotation_deg": 2.0,
    "settled_linear_speed_m_s": 0.02,
    "settled_angular_speed_rad_s": 0.05,
    "settled_penetration_m": 0.002,
    "settle_steps": 250,
}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def request(base, path, body=None):
    payload = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return response.read()


def call(base, path, body=None):
    return json.loads(request(base, path, body))


def snapshot(sim):
    return {name: {"qpos": sim.data.joint(name + "_joint0").qpos.tolist(),
                   "qvel": sim.data.joint(name + "_joint0").qvel.tolist()} for name in OBJECTS}


def rotation_error(actual, reference):
    dot = abs(np.dot(actual, reference) / (np.linalg.norm(actual) * np.linalg.norm(reference)))
    return float(np.degrees(2 * np.arccos(np.clip(dot, 0, 1))))


def verify_physics(bundle, index, wanted, reference):
    scene, build = load_scene(bundle, index)
    sim = WorldSim(scene, prepared_build=build, seed=7)
    try:
        assert sim.model.nbody == 9 and sim.model.nu == 0
        assert sim.timestep == reference["physics_step"]
        for key, value in reference["options"].items():
            np.testing.assert_allclose(getattr(sim.model.opt, key), value, rtol=0, atol=1e-12, err_msg=key)
        diffs = geometry_differences(sim.model, reference["geometries"])
        assert set(sim.model.geom(i).name for i in range(sim.model.ngeom)) == set(reference["geometries"])
        for attrs in diffs.values():
            for values in attrs.values():
                np.testing.assert_allclose(values["steward"], values["baseline"], rtol=0, atol=LIMITS["geometry_absolute_error"])
        body_differences = {}
        for body, fields in reference["bodies"].items():
            gid = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_BODY, body)
            assert gid >= 0
            body_differences[body] = {}
            for field, expected in fields.items():
                actual = getattr(sim.model, "body_" + field)[gid]
                if field == "iquat":
                    assert rotation_error(actual, expected) <= LIMITS["settled_rotation_deg"]
                else:
                    np.testing.assert_allclose(actual, expected, rtol=LIMITS["body_relative_error"], atol=1e-8,
                                               err_msg=f"{body} {field}")
                body_differences[body][field] = {"baseline": expected, "steward": actual.tolist()}
        raw = []
        for _ in range(2):
            assert sim.step_count == 0 and sim.time == 0
            observed = snapshot(sim)
            for name in OBJECTS:
                for key in ("qpos", "qvel"):
                    np.testing.assert_allclose(observed[name][key], wanted["reset"][name][key],
                                               rtol=0, atol=LIMITS["reset_absolute_error"])
            raw.append(observed)
            sim.step(LIMITS["settle_steps"])
            settled = snapshot(sim)
            sim.reset()
        assert raw[0] == raw[1]
        sim.step(LIMITS["settle_steps"])
        assert settled == snapshot(sim)
        errors = {}
        for name in OBJECTS:
            actual = settled[name]
            expected = wanted["settled"][name]
            row = {
                "position_m": float(np.linalg.norm(np.subtract(actual["qpos"][:3], expected["qpos"][:3]))),
                "rotation_deg": rotation_error(actual["qpos"][3:], expected["qpos"][3:]),
                "linear_speed_m_s": float(np.linalg.norm(actual["qvel"][:3])),
                "angular_speed_rad_s": float(np.linalg.norm(actual["qvel"][3:])),
            }
            assert all(value <= LIMITS["settled_" + key] for key, value in row.items()), (index, name, row)
            errors[name] = row
        penetration = max([0.0, *(-float(c.dist) for c in sim.data.contact[:sim.data.ncon])])
        assert penetration <= LIMITS["settled_penetration_m"]
        return {"raw_state_sha256": wanted["raw_sha256"], "reset_snapshots": raw, "settled": settled,
                "baseline_settled": wanted["settled"], "errors": errors, "max_penetration_m": penetration,
                "geometry_differences": diffs, "body_comparison": body_differences, "mujoco": mujoco.__version__}
    finally:
        sim.stop()


def verify_wca(base, index, wanted, out):
    name = scenario_name(index)
    loaded = call(base, "/wca/episode", {"action": "load", "scenario": name})
    assert loaded["scenario"] == name and loaded["robots"] == {}
    snapshots = []
    for _ in range(2):
        assert call(base, "/wca/episode", {"action": "reset"})["reset"] is True
        console = call(base, "/console/state")
        state = call(base, "/wca/scene")
        assert console["loaded"] and console["scenario"] == name and console["robots"] == {}
        assert console["t"] == 0 and state["stamp"] == 0 and len(console["groundTruth"]) == 8
        poses = {e["id"]: e["pose"]["value"] for e in state["entities"]}
        assert set(poses) == {"w:floor", *("w:" + item for item in OBJECTS)}
        for item in OBJECTS:
            target = wanted["reset"][item]["qpos"]
            np.testing.assert_allclose(poses["w:" + item]["p"], target[:3], rtol=0, atol=1e-9)
            assert rotation_error(poses["w:" + item]["q"], target[3:]) < 1e-5
        snapshots.append(poses)
    assert snapshots[0] == snapshots[1]
    png = request(base, "/wca/render?camera=overview&width=960&height=720")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    (out / f"init-{index}-overview.png").write_bytes(png)
    return {"scenario": name, "console": console, "reset_snapshots": snapshots}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:17420")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    dump(args.out / "protocol.json", LIMITS)
    states = json.loads((args.bundle / "initial-states.json").read_text())
    reference = json.loads((args.bundle / "reference.json").read_text())
    for index in range(5):
        result = verify_physics(args.bundle, index, states[str(index)], reference)
        result["wca"] = verify_wca(args.url, index, states[str(index)], args.out)
        dump(args.out / f"init-{index}.json", result)
        print("passed", index, flush=True)
    last = call(args.url, "/wca/scene")
    try:
        call(args.url, "/wca/episode", {"action": "load", "scenario": "libero-object-alphabet-soup-init-5"})
    except HTTPError as error:
        assert error.code >= 400
        assert call(args.url, "/wca/scene") == last
        dump(args.out / "invalid-index.json", {"status": error.code, "error": error.read().decode(), "scene_preserved": True})
    else:
        raise AssertionError("Invalid index unexpectedly loaded")


if __name__ == "__main__":
    main()
