"""Step 8 keeps failed slots and separates physical completion from chain completion."""

from pathlib import Path
import gzip
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from step8_metrics import aggregate, episode


def test_invalid_or_missing_episode_does_not_publish_a_smaller_denominator():
    slots = [{"runId": str(i), "environment": "wsim"} for i in range(3)]
    rows = [dict(slots[0], valid=True, physicalSuccess=True, functionalSuccess=True)]
    assert aggregate(slots, rows)["physicalRate"] is None
    rows += [dict(s, valid=True, physicalSuccess=False, functionalSuccess=False) for s in slots[1:]]
    result = aggregate(slots, rows)
    assert result["planned"] == 3 and result["physicalRate"] == 1 / 3
    rows[1]["valid"] = False
    assert aggregate(slots, rows)["functionalRate"] is None
    with pytest.raises(ValueError, match="duplicate"):
        aggregate(slots, rows + [rows[0]])


def test_physical_success_does_not_hide_unknown_goal_stop_or_capability_change():
    slot = {"runId": "one", "environment": "wsim"}
    result = {"world": {"success": True, "traceComplete": True}, "initialStateMatches": True,
              "job": {"state": "succeeded", "goalStatus": "achieved", "stopStatus": "confirmed"},
              "cleanup": {"pass": True}, "command": {"exitCode": 0, "timedOut": False}}
    assert episode(slot, result)["functionalSuccess"]
    result["job"]["goalStatus"] = "unconfirmed"
    row = episode(slot, result)
    assert row["physicalSuccess"] and not row["functionalSuccess"]
    result["job"]["goalStatus"] = "achieved"
    result["job"]["stopStatus"] = "unknown"
    assert not episode(slot, result)["functionalSuccess"]
    result["job"]["stopStatus"] = "confirmed"
    assert not episode(slot, result, capability_matches=False)["functionalSuccess"]
    result["interventions"] = ["operator moved can"]
    assert not episode(slot, result)["physicalSuccess"]


def test_process_failure_after_saved_physical_success_prevents_functional_success():
    result={"world":{"success":True,"traceComplete":True},"initialStateMatches":True,
        "job":{"state":"succeeded","goalStatus":"achieved","stopStatus":"confirmed"},
        "cleanup":{"pass":True},"command":{"exitCode":0,"timedOut":True}}
    row=episode({"runId":"one","environment":"wsim"},result)
    assert row["valid"] and row["physicalSuccess"] and not row["functionalSuccess"]
    result["command"]={"exitCode":1,"timedOut":False}
    assert not episode({"runId":"one","environment":"wsim"},result)["functionalSuccess"]


def test_completion_audit_rejects_premature_grasp_and_supported_placement(tmp_path):
    from run_libero_step8 import completion_check
    from libero_step4_evidence import BASE,PADS
    job=tmp_path/"runtime/ego-panda/policy-jobs/panda-job-test";job.mkdir(parents=True)
    (tmp_path/"world").mkdir()
    rows=[{"step":1,"judgment":{"grasp":"confirmed","placement":"unconfirmed"}},
          {"step":2,"judgment":{"grasp":"confirmed","placement":"confirmed"}}]
    (job/"observations.jsonl").write_text("\n".join(map(json.dumps,rows))+"\n")
    (tmp_path/"result.json").write_text(json.dumps({"world":{"initial_can":[0,0,.04],"release":{"step":2}}}))
    physics=[{"step":1,"can":[0,0,.1],"contacts":[[pad,"can",.2,0] for pad in PADS]},
             {"step":2,"inside":True,"bottom_force_n":.2,"tool_clearance_m":.1,"contacts":[[BASE,"can",.2,0]]}]
    def save():
        with gzip.open(tmp_path/"world/physics.jsonl.gz","wt") as f:f.write("\n".join(map(json.dumps,physics))+"\n")
    save();assert completion_check(tmp_path)["pass"]
    physics[0]["contacts"]=[];save();assert not completion_check(tmp_path)["pass"]
    physics[0]["contacts"]=[[pad,"can",.2,0] for pad in PADS]
    physics[1]["contacts"].append(["floor","can",.01,0]);save()
    assert not completion_check(tmp_path)["pass"]


def test_renderer_startup_failure_closes_acquired_renderer_before_encoder(monkeypatch,tmp_path):
    import step8_capture as module
    from types import SimpleNamespace
    closed=[]
    class Renderer:
        _gl_context=None
        def close(self):closed.append(True)
    def create(*args,**kwargs):
        if not getattr(create,"called",False):create.called=True;return Renderer()
        raise RuntimeError("depth allocation failed")
    monkeypatch.setattr(module.mujoco,"MjData",lambda _:None)
    monkeypatch.setattr(module.mujoco,"Renderer",create)
    monkeypatch.setattr(module,"Recording",lambda *args:pytest.fail("Encoder must not start before renderers"))
    model=SimpleNamespace(vis=SimpleNamespace(quality=SimpleNamespace(offsamples=4)))
    with pytest.raises(RuntimeError,match="depth allocation"):
        module.Capture(model,{},tmp_path,"unused",{})
    assert closed==[True] and model.vis.quality.offsamples==4


def test_stalled_encoder_pipe_obeys_wall_deadline():
    import subprocess,time
    from step8_capture import write_pipe
    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"],stdin=subprocess.PIPE)
    started=time.monotonic()
    try:
        with pytest.raises(TimeoutError,match="Video pipe"):
            write_pipe(child.stdin,b"x"*(1024*1024),started+.1)
        assert time.monotonic()-started<1
    finally:
        child.kill();child.wait(timeout=5);child.stdin.close()
