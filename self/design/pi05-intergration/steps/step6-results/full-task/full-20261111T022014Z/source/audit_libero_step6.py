"""Audit original predictions, application receipts and every physical support sample."""

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from libero_step4_evidence import BASE, LIMITS, PADS, force
from verify_libero_step6 import save


def audit(out):
    summary = json.loads((out / "summary.json").read_text())
    actions = [json.loads(line) for line in (out / "actions.jsonl").read_text().splitlines()]
    assert len(actions) == summary["actions_applied"]
    assert len(list(out.glob("prefix-*/inference.json"))) == summary["inferences"]
    for index, action in enumerate(actions):
        folder = out / action["prefix"]
        observation = json.loads((folder / "observation.json").read_text())
        prediction = json.loads((folder / "inference.json").read_text())
        assert observation["episodeId"] == action["episodeId"] == summary["episode_id"]
        assert observation["step"] == action["observation_step"]
        ordinal = index % summary["prefix_size"]
        assert observation["step"] == index // summary["prefix_size"] * summary["prefix_size"] * 25
        assert action["raw_action"] == prediction["actions"][ordinal]
        assert action["applied_action"] == np.clip(action["raw_action"], -1, 1).tolist()
        assert action["sequence"] == index
        assert action["receipt"] == {"sequence": index, "controlGeneration": summary["generation"],
                                     "startStep": index * 25, "plannedEndStep": (index + 1) * 25,
                                     "endStep": (index + 1) * 25}
    with gzip.open(out / "wsim-physics.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert [row["step"] for row in rows] == list(range(1, summary["stop_status"]["step"] + 1))
    for action in actions:
        np.testing.assert_allclose(rows[action["receipt"]["endStep"] - 1]["tool"],
                                   action["site_position"], rtol=0, atol=1e-9)
    windows = {}
    for name in ("before_stop_hold", "after_stop_hold"):
        evidence = summary[name]
        assert evidence["grasp"] and evidence["carry"] and evidence["release"]
        grasp, carry, release = (evidence[key] for key in ("grasp", "carry", "release"))
        assert grasp["step"] < carry["step"] < release["step"]
        assert all(force(grasp, pad) >= LIMITS["pad_force_n"] for pad in PADS)
        assert all(force(carry, pad) >= LIMITS["pad_force_n"] for pad in PADS)
        assert force(grasp, "floor") == force(carry, "floor") == 0
        assert all(force(release, pad) < LIMITS["support_force_n"] for pad in PADS)
        assert abs(release["fingers"][0] - release["fingers"][1]) >= abs(carry["fingers"][0] - carry["fingers"][1]) + .004
        window = evidence["bottom_window"]
        assert window["last_step"] - window["first_step"] + 1 == evidence["required_bottom_steps"] == 125
        assert window["first_step"] > release["step"]
        for step in range(window["first_step"], window["last_step"] + 1):
            row = rows[step - 1]
            assert row["inside"] and force(row, BASE) >= LIMITS["support_force_n"]
            assert row["tool_clearance_m"] >= evidence["required_withdrawal_m"]
            assert np.linalg.norm(row["can_velocity"][3:]) <= LIMITS["linear_speed_m_s"]
            assert np.linalg.norm(row["can_velocity"][:3]) <= LIMITS["angular_speed_rad_s"]
            assert all(f <= 0 or BASE in (a, b) for a, b, f, _ in row["contacts"])
        windows[name] = window
    stop = summary["stop_status"]
    assert summary["success"] and summary["no_late_action"] and stop["stopState"] == "stopped"
    assert not stop["leased"] and not stop["pending"]
    assert stop["lastApplied"]["sequence"] == len(actions) - 1
    return {"pass": True, "actions": len(actions), "physical_steps": len(rows),
            "inferences": summary["inferences"], "windows": windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    result = audit(args.out)
    save(args.out / "audit.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
