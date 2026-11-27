"""Phase 0 acceptance: the robot puts the cup on the table using only the Ego API."""

from __future__ import annotations

import time

import pytest

from steward.assets.generators import steward_bot
from steward.ego.runtime import EgoRuntime, RobotLink
from steward.ego.server import ego_api
from steward.mission import _approach_to_observe, _explore, _find, _run, _safe_receipt, cup_to_table, simtime_sleep
from steward.net import HttpError, call
from steward.world.server import StewardHost


def test_cup_to_table_end_to_end(kitchen_root, tmp_path):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6.0, seed=11, run_dir=tmp_path).start()
    runtime = EgoRuntime(RobotLink(host.robot_servers["w:robot-1"].url), steward_bot().to_dict())
    runtime.start()
    api = ego_api(runtime).start()
    try:
        result = cup_to_table(api.url)
        time.sleep(0.3)
        ws = host.sim.world_state()
        assert any(r["type"] == "supported-by" and r["a"]["entity"] == "w:cup-1" and r["b"]["entity"] == "w:table-1"
                   for r in ws["relations"])
        steps = [s["step"] for s in result["log"]]
        assert steps[-1] == "place"
        report = host.observer.report()["w:robot-1"]
        assert report["leaks"] == 0
        assert report["latest"]["meanPositionErrorM"] < report["first"]["meanPositionErrorM"]
        with_receipt = call("POST", f"{api.url}/ego/predict", {"skill": "place", "params": {"on": result["table"]}})
        assert not with_receipt["feasible"], "nothing is held after placing"
    finally:
        api.stop()
        runtime.stop()
        host.stop()


def test_revision_preserves_a_grasp_and_places_at_the_updated_goal(kitchen_root, tmp_path):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6.0, seed=11).start()
    link = RobotLink(host.robot_servers["w:robot-1"].url)
    runtime = EgoRuntime(link, steward_bot().to_dict(), out_dir=tmp_path)
    runtime.start()
    api = ego_api(runtime).start()
    try:
        assert _explore(api.url, 180)["state"] == "succeeded"
        summary = call("GET", f"{api.url}/ego/summary")
        cup, table = (_find(summary, category) for category in ("cup", "table"))
        assert cup and table
        _approach_to_observe(api.url, cup, distance=0.9)
        _run(api.url, _safe_receipt(api.url, "pick", {"target": cup["id"]}, cup, []), 180)

        def placement(offset, revision=None):
            return call("POST", f"{api.url}/ego/predict", {"skill": "place",
                        "params": {"on": table["id"], "offset": [0, offset]},
                        "intentRevision": revision})

        old = placement(-0.20)
        runtime.acquire_control("operator")
        revision = call("POST", f"{api.url}/ego/intent/revise",
                        {"owner": "operator", "instruction": "改放桌子另一侧"})
        updated = revision["intentRevision"]
        assert revision["interrupted"] is None
        assert link.proprioception()["gripper"]["holding"]
        with pytest.raises(HttpError, match="task intent is stale"):
            call("POST", f"{api.url}/skill/run", {"owner": "operator", "receipt": old["id"]})
        with pytest.raises(HttpError, match="earlier task intent"):
            call("POST", f"{api.url}/skill/run",
                 {"owner": "operator", "receipt": old["id"], "intentRevision": updated})
        goal = placement(0.20, updated)
        assert goal["withinRiskLimit"], goal["issues"]
        job = call("POST", f"{api.url}/skill/run",
                   {"owner": "operator", "receipt": goal["id"], "intentRevision": updated})
        assert runtime.wait(job["id"], timeout=40)["state"] == "succeeded"
        simtime_sleep(api.url, 1)
        outcome = call("POST", f"{api.url}/ego/verify-placement",
                       {"job": job["id"], "owner": "operator", "intentRevision": updated,
                        "goal": {"entity": cup["id"], "support": table["id"], "offset": [0, 0.20], "toleranceM": 0.03}})
        assert outcome["intentRevision"] == updated
        assert outcome["accepted"] and outcome["terminated"]
        assert outcome["intent"] == ("unknown" if outcome["effect"] == "unknown" else "satisfied")
        assert call("GET", f"{api.url}/ego/verifications")["records"] == [outcome]
        assert (tmp_path / "verifications.jsonl").is_file()
        world_goal = {"entity": "w:cup-1", "support": "w:table-1", "offset": [0, 0.20], "toleranceM": 0.03}
        score = call("POST", f"{host.wca.url}/wca/verify-placement", world_goal)
        assert score["complete"] and score["offsetErrorM"] < 0.03
        wrong_destination = {**world_goal, "offset": [0, -0.20]}
        assert not call("POST", f"{host.wca.url}/wca/verify-placement", wrong_destination)["complete"]
    finally:
        api.stop()
        runtime.stop()
        host.stop()


def test_robot_port_exposes_no_world_state(kitchen_root, tmp_path):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=1.0, run_dir=tmp_path).start()
    try:
        robot = host.robot_servers["w:robot-1"].url
        frame = call("GET", f"{robot}/sensors")
        assert "w:" not in str(frame)
        for path in ("/wca/scene", "/wca/status", "/assets"):
            try:
                call("GET", f"{robot}{path}")
                raise AssertionError(f"robot port served {path}")
            except HttpError as exc:
                assert exc.status == 404
    finally:
        host.stop()
