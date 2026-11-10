"""Register the five pinned LIBERO Step 2 scenarios in a Steward data root."""

import argparse
import json
from pathlib import Path

from steward.authoring.service import AuthoringService
from steward.scene.libero import PANDA_ID, load_scene, scenario_name
from steward.scene.validate import require_valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--panda", action="store_true", help="register the Step 3 Panda variants")
    args = parser.parse_args()
    service = AuthoringService(args.root)
    bundle = args.root / ("libero-step3" if args.panda else "libero-step2")
    metadata = "protocol.json" if args.panda else "reference.json"
    prompt = json.loads((bundle / metadata).read_text())["prompt"]
    entries = []
    for index in range(5):
        scene, _ = load_scene(bundle, index, panda=args.panda)
        require_valid(scene)
        name = scenario_name(index, panda=args.panda)
        scene_path = args.root / "scenes" / f"{name}.json"
        scenario_path = args.root / "scenarios" / f"{name}.json"
        if scene_path.exists() or scenario_path.exists():
            raise FileExistsError(f"Scenario {name} is already registered")
        entries.append((scene_path, scenario_path, scene, {
            "name": name, "scene": name, "robots": [PANDA_ID] if args.panda else [], "task": prompt, "liberoInitialState": index,
            **({"liberoPanda": True} if args.panda else {}),
            "view": {"lookat": [0, 0, 0.10], "distance": 1.15, "azimuth": 0, "elevation": -50},
        }))
    created = []
    try:
        for scene_path, scenario_path, scene, scenario in entries:
            with scene_path.open("x") as stream:
                created.append(scene_path)
                json.dump(scene, stream, indent=2)
            # Reserve the filename before handing its validated contents to the authoring service.
            with scenario_path.open("x"):
                created.append(scenario_path)
            service.save_scenario(scenario)
    except Exception:
        for path in reversed(created):
            path.unlink()
        raise
    for _, _, _, scenario in entries:
        print(scenario["name"])


if __name__ == "__main__":
    main()
