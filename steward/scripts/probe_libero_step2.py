"""Exercise the exported LIBERO floor, basket and can through WorldSim."""

import argparse
import json
from pathlib import Path
import subprocess

import mujoco
import numpy as np

from steward.scene.libero import load_scene
from steward.world.sim import WorldSim

CAN = "alphabet_soup_1"
BASKET = "basket_1"
BOTTOM = "basket_1_g1"
SITE = "basket_1_contain_region"
STEPS = 1250
SETTLED_WINDOW = 125
SPEED_LIMIT = 0.02


def contacts(sim):
    names = []
    for index in range(sim.data.ncon):
        contact = sim.data.contact[index]
        if contact.efc_address < 0:
            continue
        force = np.zeros(6)
        mujoco.mj_contactForce(sim.model, sim.data, index, force)
        if force[0] > 0:
            names.append((sim.model.geom(contact.geom1).name, sim.model.geom(contact.geom2).name, float(force[0])))
    return names


def can_on(pairs, support):
    return any((a.startswith(CAN + "_") and b == support) or
               (b.startswith(CAN + "_") and a == support) for a, b, _ in pairs)


def inside(sim):
    site = sim.data.site(SITE)
    local = site.xmat.reshape(3, 3).T @ (sim.data.body(CAN + "_main").xpos - site.xpos)
    return bool(np.all(np.abs(local) < sim.model.site(SITE).size))


def libero_contains(sim):
    # Pinned LIBERO SiteObject.in_box uses world-axis bounds with a 1 cm lower-Z allowance.
    site = sim.data.site(SITE)
    radius = np.abs(site.xmat.reshape(3, 3) @ sim.model.site(SITE).size)
    lower, upper = site.xpos - radius, site.xpos + radius
    lower[2] -= 0.01
    point = sim.data.body(CAN + "_main").xpos
    return bool(np.all(point > lower) and np.all(point < upper))


def geometry_differences(model, reference):
    differences = {}
    for index in range(model.ngeom):
        name = model.geom(index).name
        for field, wanted in reference[name].items():
            actual = getattr(model, "geom_" + field)[index]
            if not np.allclose(actual, wanted, rtol=0, atol=1e-8):
                differences.setdefault(name, {})[field] = {"baseline": wanted, "steward": actual.tolist()}
    return differences


def run(sim, case, out, ffmpeg, basket_pose):
    sim.reset()
    basket = sim.data.joint(BASKET + "_joint0")
    basket.qpos[:] = basket_pose
    basket.qvel[:] = 0
    mujoco.mj_forward(sim.model, sim.data)
    can = sim.data.joint(CAN + "_joint0")
    site = sim.data.site(SITE)
    start = site.xpos.copy()
    start[0] += 0.30 if case == "outside" else 0.0
    start[2] += 0.25
    can.qpos[:] = [*start, 1, 0, 0, 0]
    can.qvel[:] = 0
    mujoco.mj_forward(sim.model, sim.data)
    video = out / (case + ".mp4")
    command = [ffmpeg, "-loglevel", "error", "-n", "-f", "image2pipe", "-vcodec", "png",
               "-framerate", "25", "-i", "-", "-pix_fmt", "yuv420p", str(video)]
    rows = []
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            for step in range(STEPS + 1):
                if step:
                    sim.step()
                pairs = contacts(sim)
                rows.append({
                    "step": step, "time": float(sim.data.time), "position": can.qpos[:3].tolist(),
                    "speed": float(np.linalg.norm(can.qvel[:3])), "inside": inside(sim),
                    "libero_contains": libero_contains(sim),
                    "bottom_contact": can_on(pairs, BOTTOM), "floor_contact": can_on(pairs, "floor"),
                    "contacts": pairs,
                })
                if step % 20 == 0:
                    process.stdin.write(sim.render("overview", 640, 480))
        finally:
            process.stdin.close()
        if process.wait():
            raise RuntimeError(f"Video encoding failed for {case}")
    with (out / (case + ".jsonl")).open("w") as trace:
        trace.writelines(json.dumps(row) + "\n" for row in rows)
    last = rows[-SETTLED_WINDOW:]
    expected = case == "inside"
    return {
        "video": video.name,
        "first_contact": next((row["time"] for row in rows if row["bottom_contact"] or row["floor_contact"]), None),
        "first_inside": next((row["time"] for row in rows if row["inside"]), None),
        "ever_basket_bottom": any(row["bottom_contact"] for row in rows),
        "ever_floor": any(row["floor_contact"] for row in rows),
        "max_final_speed": max(row["speed"] for row in last),
        "final": rows[-1],
        "passed": all(row["inside"] == expected and row["libero_contains"] == expected and row["bottom_contact"] == expected
                      and row["floor_contact"] != expected and row["speed"] <= SPEED_LIMIT for row in last)
                  and not any(row["floor_contact"] if expected else row["bottom_contact"] or row["inside"] or row["libero_contains"] for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    states = json.loads((args.source / "initial-states.json").read_text())
    reference = json.loads((args.source / "reference.json").read_text())
    basket_pose = states["0"]["settled"][BASKET]["qpos"]
    (args.out / "protocol.json").write_text(json.dumps({
        "steps": STEPS, "settled_window": SETTLED_WINDOW, "max_settled_speed_m_s": SPEED_LIMIT,
        "initial_index": 0, "height_above_site_m": 0.25, "outside_offset_x_m": 0.30,
    }, indent=2) + "\n")
    scene, build = load_scene(args.source, 0, minimal=True)
    sim = WorldSim(scene, prepared_build=build)
    try:
        result = {
            "mujoco": mujoco.__version__, "reference_mujoco": reference["mujoco"],
            "geom_count": sim.model.ngeom,
            "geometry_differences": geometry_differences(sim.model, reference["geometries"]),
            "cases": {case: run(sim, case, args.out, args.ffmpeg, basket_pose) for case in ("inside", "outside")},
        }
    finally:
        sim.stop()
    (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if all(case["passed"] for case in result["cases"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
