"""Policy authorization and stopping through the real Panda HTTP and physics paths."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
import numpy as np

from steward.ego.panda_policy import PandaPolicyConfig
from steward.ego.panda_runtime import PandaEgoRuntime, panda_ego_api
from steward.ego.runtime import RobotLink
from steward.net import HttpError, call
from steward.world.server import StewardHost
from steward.ego.panda_perception import PandaEvidence
from steward.ego.panda_prediction import PandaPrediction
from steward.ego.panda_policy import PolicyFailure


def off_main(host, operation):
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(operation)
        while not work.done():
            host.pump(timeout=.002)
        return work.result()


@pytest.fixture
def panda(tmp_path):
    root = Path(__file__).resolve().parents[1] / "var/step4/wsim-root"
    if not (root / "libero-step3/panda.xml").exists():
        pytest.skip("Requires the prepared fixed LIBERO scene")
    client = tmp_path / "policy.py"
    client.write_text("import json,sys,time\nfrom pathlib import Path\ntime.sleep(0.05)\n"
                      "Path(sys.argv[3]).write_text(json.dumps({'actions':[[0,0,0,0,0,0,-1]]*10}))\n")
    host = StewardHost(root, scenario="libero-object-alphabet-soup-panda", freeze_idle=True,
                       dispatch_task=False, speed=float("inf")).start()
    config = PandaPolicyConfig((sys.executable, str(client)), 18067, "test-policy", max_actions=5)
    runtime = PandaEgoRuntime(RobotLink(host.robot_servers["w:panda"].url), config, tmp_path / "robot")
    api = panda_ego_api(runtime, 0, 0).start()
    try:
        yield host, runtime, api.url, client
    finally:
        off_main(host, runtime.stop)
        api.stop()
        host.stop()


def receipt(url):
    objects = call("GET", url + "/ego/summary")["objects"]
    target = next(obj["id"] for obj in objects if obj["category"] == "alphabet_soup")
    basket = next(obj["id"] for obj in objects if obj["category"] == "basket")
    result = call("POST", url + "/ego/predict", {"skill": "libero_pick_place", "params": {"target": target, "on": basket}})
    assert result["feasible"], result
    return result


def test_rejected_receipts_and_unsupported_goals_have_no_applied_actions(panda):
    host, runtime, url, _ = panda

    def check():
        valid = receipt(url)
        with pytest.raises(HttpError, match="unknown"):
            call("POST", url + "/skill/run", {"receipt": "unknown"})
        denied = call("POST", url + "/ego/predict", {"skill": "navigate", "params": {"x": 0, "y": 0}})
        assert not denied["feasible"]
        with pytest.raises(HttpError):
            call("POST", url + "/skill/run", {"receipt": denied["id"]})
        runtime.executor.deadlines[valid["id"]] = time.monotonic() - 1
        with pytest.raises(HttpError, match="expired"):
            call("POST", url + "/skill/run", {"receipt": valid["id"]})
        assert runtime.executor.status()["lastApplied"] is None

    off_main(host, check)


def test_concurrent_run_creates_one_job_and_wait_timeout_keeps_it_queryable(panda):
    host, runtime, url, _ = panda

    def check():
        valid = receipt(url)

        def submit():
            try:
                return call("POST", url + "/skill/run", {"receipt": valid["id"]})
            except HttpError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        accepted = [value for value in results if isinstance(value, dict)]
        assert len(accepted) == 1 and len(runtime.executor.jobs) == 1
        job = accepted[0]
        immediate = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 0})
        assert immediate["id"] == job["id"]
        final = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 10})
        assert final["state"] == "failed" and final["stopStatus"] == "confirmed"
        assert final["actionsApplied"] == 5
        assert not runtime.executor.status()["pending"]
        assert not host.sim._holds

    off_main(host, check)


def test_owner_old_object_and_calibration_checks_reject_direct_http(panda):
    host, runtime, url, _ = panda

    def check():
        valid = receipt(url)
        denied = call("POST", url + "/ego/predict", {"skill": "libero_pick_place", "params": {
            "target": "e:old-episode:can", "on": valid["params"]["on"]}})
        assert not denied["feasible"]
        call("POST", url + "/ego/control", {"action": "acquire", "owner": "test-owner"})
        with pytest.raises(HttpError, match="another owner"):
            call("POST", url + "/skill/run", {"receipt": valid["id"]})
        with pytest.raises(HttpError, match="busy"):
            call("POST", url + "/ego/control", {"action": "acquire", "owner": "other-owner"})
        runtime.executor.receipts[valid["id"]]["calibrationId"] = "obsolete-calibration"
        with pytest.raises(HttpError, match="observation changed"):
            call("POST", url + "/skill/run", {"receipt": valid["id"], "owner": "test-owner"})
        call("POST", url + "/ego/control", {"action": "release", "owner": "test-owner"})
        assert runtime.executor.status()["lastApplied"] is None

    off_main(host, check)


def test_pick_admits_all_visible_objects_and_binds_milk_instruction_over_real_http(panda):
    host, runtime, url, _ = panda
    from steward.ego.panda_visual_model import PandaVisualModel
    runtime.visual_prior = PandaVisualModel(Path(__file__).parents[1] / 'var/step4/wsim-root/libero-step3')
    def check():
        runtime.observe(force=True)
        runtime.observe_surfaces()
        objects = call('GET', url + '/ego/summary')['objects']
        groceries = {o['category']: o for o in objects if o['graspable']}
        assert set(groceries) == {'alphabet_soup', 'milk', 'butter', 'cream_cheese', 'salad_dressing', 'tomato_sauce'}
        for category, obj in groceries.items():
            r = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': obj['id']}})
            assert r['feasible'], (category, r['issues'])
            assert r['plan']['object']['id'] == obj['id']
            assert r['plan']['prompt'] == 'pick up the ' + category.replace('_', ' ')
        basket = next(o for o in objects if o['category'] == 'basket')
        denied = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': basket['id']}})
        assert not denied['feasible']
        r = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': groceries['milk']['id']}})
        job = call('POST', url + '/skill/run', {'receipt': r['id']})
        final = call('POST', url + '/skill/wait', {'job': job['id'], 'timeoutS': 15})
        assert final['skill'] == 'pick' and final['actionsApplied'] == 5
        assert final['goalStatus'] == 'not_achieved' and final['stopStatus'] == 'confirmed'
        assert final['evidence']['target'] == groceries['milk']['id']
        request = json.loads((runtime.executor.out / job['id'] / 'prefix-000/observation.json').read_text())
        assert request['prompt'] == 'pick up the milk'
        assert request['target']['id'] == groceries['milk']['id']
        assert not host.sim._holds
    off_main(host, check)


def test_pick_stop_interrupts_inference_and_confirms_stationary_control(panda):
    host, runtime, url, client = panda
    from steward.ego.panda_visual_model import PandaVisualModel
    runtime.visual_prior = PandaVisualModel(Path(__file__).parents[1] / 'var/step4/wsim-root/libero-step3')
    client.write_text(client.read_text().replace('time.sleep(0.05)', 'time.sleep(8)'))
    def check():
        runtime.observe(force=True); runtime.observe_surfaces(); runtime.start()
        selected = call('GET', url + '/ego/query?category=milk')['objects'][0]
        receipt = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': selected['id']}})
        job = call('POST', url + '/skill/run', {'receipt': receipt['id']})
        deadline = time.monotonic() + 5
        while call('GET', url + '/skill/status?job=' + job['id'])['phase'] != 'waiting_inference':
            assert time.monotonic() < deadline
            time.sleep(.01)
        started = time.monotonic()
        stopped = call('POST', url + '/skill/stop', {}, timeout=12)
        assert time.monotonic() - started < 4
        assert stopped['stopStatus'] == 'confirmed'
        assert stopped['job']['state'] == 'stopped' and stopped['job']['actionsApplied'] == 0
        assert not host.sim._holds
    off_main(host, check)


def test_rejected_pick_prefix_requires_reset_before_automatic_retry(panda, monkeypatch):
    host, runtime, url, _ = panda
    from steward.ego.panda_visual_model import PandaVisualModel
    runtime.visual_prior = PandaVisualModel(Path(__file__).parents[1] / 'var/step4/wsim-root/libero-step3')
    original = PandaPrediction.check
    def reject_prefix(self, actions):
        if len(actions) > 1: raise PolicyFailure('prediction_rejected', 'deliberate obstacle in test')
        return original(self, actions)
    def check():
        runtime.observe(force=True); runtime.observe_surfaces()
        selected = call('GET', url + '/ego/query?category=milk')['objects'][0]
        r = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': selected['id']}})
        monkeypatch.setattr(PandaPrediction, 'check', reject_prefix)
        job = call('POST', url + '/skill/run', {'receipt': r['id']})
        final = call('POST', url + '/skill/wait', {'job': job['id'], 'timeoutS': 15})
        assert final['reasonCode'] == 'prediction_rejected' and final['actionsApplied'] == 0
        assert final['stopStatus'] == 'confirmed'
        monkeypatch.setattr(PandaPrediction, 'check', original)
        for _ in range(2):
            denied = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': selected['id']}})
            assert not denied['feasible'] and any('reset the episode' in issue for issue in denied['issues'])
            call('POST', url + '/skill/stop', {})
        assert len(runtime.executor.jobs) == 1 and not host.sim._holds
        host.on_main(host.sim.reset)
        runtime.observe(force=True); runtime.observe_surfaces()
        fresh = call('GET', url + '/ego/query?category=milk')['objects'][0]
        allowed = call('POST', url + '/ego/predict', {'skill': 'pick', 'params': {'target': fresh['id']}})
        assert allowed['feasible'], allowed['issues']
    off_main(host, check)


@pytest.mark.parametrize("fault", ["nan", "dimensions", "timeout"])
def test_policy_faults_stop_without_applying_an_action(panda, fault):
    host, runtime, url, client = panda
    if fault == "timeout":
        client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))
        runtime.executor.config = replace(runtime.executor.config, request_timeout_s=.15)
    else:
        payload = [[float("nan"), 0, 0, 0, 0, 0, -1]] * 5 if fault == "nan" else [[0, 0]] * 5
        client.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[3]).write_text(" +
                          repr(json.dumps({"actions": payload})) + ")\n")

    def check():
        job = call("POST", url + "/skill/run", {"receipt": receipt(url)["id"]})
        result = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 6})
        assert result["state"] == "failed" and result["stopStatus"] == "confirmed"
        assert result["reasonCode"] == ("inference_timeout" if fault == "timeout" else "invalid_action")
        assert result["actionsApplied"] == 0 and runtime.executor.status()["lastApplied"] is None
        assert not host.sim._holds

    off_main(host, check)


def test_stop_interrupts_an_inflight_policy_request_without_waiting_for_it(panda):
    host, runtime, url, client = panda
    runtime.start()
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))

    def check():
        job = call("POST", url + "/skill/run", {"receipt": receipt(url)["id"]})
        deadline = time.monotonic() + 5
        while call("GET", url + "/skill/status?job=" + job["id"])["phase"] != "waiting_inference":
            assert time.monotonic() < deadline
            time.sleep(.01)
        started = time.monotonic()
        stopped = call("POST", url + "/skill/stop", {}, timeout=10)
        assert time.monotonic() - started < 4
        assert stopped["stopStatus"] == "confirmed"
        result = stopped["job"]
        assert result["state"] == "stopped" and result["actionsApplied"] == 0
        assert runtime.executor.status()["lastApplied"] is None
        assert not host.sim._holds

    off_main(host, check)


def test_failed_stop_confirmation_blocks_a_later_job(panda, monkeypatch):
    host, runtime, url, client = panda
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))
    original = runtime.executor.control_call

    def fail_stop(operation, body=None):
        if operation == "stop":
            raise HttpError(503, "injected actuator communication failure")
        return original(operation, body)

    def check():
        unused = receipt(url)
        job = call("POST", url + "/skill/run", {"receipt": receipt(url)["id"]})
        monkeypatch.setattr(runtime.executor, "control_call", fail_stop)
        result = call("POST", url + "/skill/stop", {}, timeout=10)
        assert result["stopStatus"] == "unknown" and result["job"]["state"] == "failed"
        assert runtime.executor.blocked
        with pytest.raises(HttpError, match="unconfirmed"):
            call("POST", url + "/skill/run", {"receipt": unused["id"]})
        assert runtime.executor.status()["lastApplied"] is None
        monkeypatch.setattr(runtime.executor, "control_call", original)
        recovered = call("POST", url + "/skill/stop", {}, timeout=10)
        assert recovered["stopStatus"] == "confirmed" and not runtime.executor.blocked
        assert call("GET", url + "/skill/status?job=" + job["id"])["state"] == "failed"

    off_main(host, check)


def test_renewal_failure_during_inference_retains_the_original_fault(panda, monkeypatch):
    host, runtime, url, client = panda
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))
    original = runtime.executor.control_call

    def lose_lease(operation, body=None):
        if operation == "renew":
            raise HttpError(503, "injected renewal failure")
        return original(operation, body)

    def check():
        monkeypatch.setattr(runtime.executor, "control_call", lose_lease)
        job = call("POST", url + "/skill/run", {"receipt": receipt(url)["id"]})
        final = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 8})
        assert final["state"] == "failed" and final["reasonCode"] == "lease_lost"
        assert "injected renewal failure" in final["message"]
        assert final["stopStatus"] == "confirmed" and final["actionsApplied"] == 0
        assert not host.sim._holds

    off_main(host, check)


def test_paused_world_bounds_stop_and_explicit_recovery(panda):
    host, runtime, url, client = panda
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))
    runtime.executor.config = replace(runtime.executor.config, stop_wall_s=.35)

    def check():
        job = call("POST", url + "/skill/run", {"receipt": receipt(url)["id"]})
        with host.sim.lock:
            host.sim.paused = True
        try:
            began = time.monotonic()
            result = call("POST", url + "/skill/stop", {}, timeout=3)
            assert result["stopStatus"] == "unknown"
            final = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 2})
            assert time.monotonic() - began < 2
            assert final["state"] == "failed" and final["stopStatus"] == "unknown"
            assert runtime.executor.blocked and not host.sim._holds
            began = time.monotonic()
            recovery = call("POST", url + "/skill/stop", {}, timeout=3)
            assert recovery["stopStatus"] == "unknown" and time.monotonic() - began < 2
            assert not host.sim._holds
        finally:
            with host.sim.lock:
                host.sim.paused = False

    off_main(host, check)


def test_episode_reset_invalidates_inflight_policy_and_old_receipt(panda):
    host, runtime, url, client = panda
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))

    def check():
        old = receipt(url)
        job = call("POST", url + "/skill/run", {"receipt": old["id"]})
        host.on_main(lambda: host.load_episode("libero-object-alphabet-soup-panda"))
        final = call("POST", url + "/skill/wait", {"job": job["id"], "timeoutS": 5})
        assert final["state"] == "failed" and final["episodeId"] == old["episodeId"]
        assert runtime.executor.status()["episodeId"] != old["episodeId"]
        assert runtime.executor.status()["lastApplied"] is None
        assert call("GET", url + "/ego/receipt?id=" + old["id"])["expired"]
        with pytest.raises(HttpError):
            call("POST", url + "/skill/run", {"receipt": old["id"]})
        assert not host.sim._holds

    off_main(host, check)


def test_real_ego_process_exit_revokes_policy_and_releases_clock(panda, tmp_path):
    from steward.cli import _EgoProcess
    host, runtime, _, client = panda
    client.write_text(client.read_text().replace("time.sleep(0.05)", "time.sleep(8)"))
    config = tmp_path / "policy.toml"
    config.write_text("[policy]\nclient_command = " + json.dumps([sys.executable, str(client)]) +
                      "\nport = 18067\nmodel_id = 'test-policy'\nmax_actions = 5\n")

    def check():
        root = Path(__file__).resolve().parents[1] / "var/step4/wsim-root"
        process = _EgoProcess(SimpleNamespace(root=root, bind_wait=0, panda_policy_config=config),
                              host.robot_servers["w:panda"].url, tmp_path / "child-runtime",
                              embodiment="libero-panda", api_port=0)
        try:
            assert process.skills
            call("POST", process.url + "/skill/run", {"receipt": receipt(process.url)["id"]})
            process.stop()
            assert process.proc.poll() is not None
            status = runtime.executor.status()
            assert not status["leased"] and status["stopState"] == "stopped"
            assert status["lastApplied"] is None and not host.sim._holds
        finally:
            process.stop()

    off_main(host, check)


@pytest.mark.parametrize("thread_name", ["panda-job-", "panda-lease"])
def test_start_failure_publishes_unknown_and_releases_hold_even_if_revoke_fails(panda, monkeypatch, thread_name):
    import threading
    host, runtime, url, _ = panda
    original_start = threading.Thread.start
    original_control = runtime.executor.control_call

    def fail_start(thread):
        if thread.name.startswith(thread_name):
            raise RuntimeError("injected thread startup failure")
        return original_start(thread)

    def fail_stop(operation, body=None):
        if operation == "stop":
            raise HttpError(503, "injected stop failure")
        return original_control(operation, body)

    def check():
        valid = receipt(url)
        monkeypatch.setattr(threading.Thread, "start", fail_start)
        monkeypatch.setattr(runtime.executor, "control_call", fail_stop)
        if thread_name == "panda-job-":
            with pytest.raises(HttpError, match="startup failure"):
                call("POST", url + "/skill/run", {"receipt": valid["id"]})
        else:
            call("POST", url + "/skill/run", {"receipt": valid["id"]})
        identifier = call("GET", url + "/ego/receipt?id=" + valid["id"])["job"]
        final = call("POST", url + "/skill/wait", {"job": identifier, "timeoutS": 3})
        assert final["state"] == "failed"
        assert final["reasonCode"] == ("start_failed" if thread_name == "panda-job-" else "execution_error")
        assert final["stopStatus"] == "unknown" and "stopError" in final["evidence"]
        assert runtime.executor.blocked and runtime.executor.finished.is_set()
        assert not host.sim._holds and runtime.executor.status()["lastApplied"] is None
        monkeypatch.setattr(runtime.executor, "control_call", original_control)

    off_main(host, check)


@pytest.mark.parametrize("tool_height,expected", [(.175, "confirmed"), (.105, "unconfirmed")])
def test_visible_released_can_uses_robot_clearance_at_lower_retreats(monkeypatch, tool_height, expected):
    import steward.ego.panda_perception as perception

    monkeypatch.setattr(perception, "external_objects", lambda *_: (None, (np.array([0., 0.]), .13)))
    monkeypatch.setattr(perception, "wrist_can_top", lambda *_: np.array([0., 0., .09]))
    evidence = PandaEvidence()
    evidence.grasp = "confirmed"
    evidence.min_width = .06
    evidence.last_held = np.array([0., 0., .16])
    camera = {"K": [[10, 0, 3], [0, 10, 3], [0, 0, 1]],
              "pose": {"p": [0, 0, 1], "q": [1, 0, 0, 0]}}
    frame = {"tool": {"p": [0, 0, tool_height]},
             "images": {role: np.ones((8, 8, 3), dtype=np.uint8) for role in ("external", "wrist")},
             "depth": {role: np.ones((8, 8), dtype=float) for role in ("external", "wrist")},
             "cameras": {role: camera for role in ("external", "wrist")}}
    for stamp in (0.0, .1, .25):
        result = evidence.update(frame | {"stamp": stamp}, .08)
    assert result[3] == expected


def test_corrupted_msaa_depth_still_rejects_the_arm_sweep():
    """N1-02 frame 35 contains phantom MSAA surfaces; never weaken motion checks to pass it."""
    with np.load(Path(__file__).parent / "fixtures/panda-step7/visual-self.npz") as sample:
        observation = json.loads(str(sample["metadata"]))
        observation["depth"] = {role: sample[role] for role in ("external", "wrist")}
        predictor = PandaPrediction(observation, sample["target"], sample["basket"])
        with pytest.raises(PolicyFailure, match="intersects predicted arm sweep"):
            predictor.check(sample["actions"])


def test_depth_renderer_preserves_metric_edges_and_rgb_msaa():
    import mujoco
    from steward.world.sensor_cameras import CameraRenderer
    from steward.math3d import matrix_from_quat

    root = Path(__file__).resolve().parents[1] / "var/step4/wsim-root"
    if not (root / "libero-step3/panda.xml").exists():
        pytest.skip("Requires the prepared fixed LIBERO scene")
    fixture = json.loads((Path(__file__).parent / "fixtures/panda-step7/depth-edge.json").read_text())
    model = mujoco.MjModel.from_xml_path(str(root / "libero-step3/panda.xml"))
    data = mujoco.MjData(model)
    for name, value in fixture["encoders"].items():
        data.joint(name).qpos[:] = value["position"]
    joint = model.joint(model.body("alphabet_soup_1_main").jntadr[0])
    data.qpos[joint.qposadr[0]:joint.qposadr[0] + 7] = fixture["canPose"]
    mujoco.mj_forward(model, data)
    worker = CameraRenderer()
    worker.snapshot_model(model)
    try:
        rgb = worker.renderer_for(256, 256)
        depth = worker.renderer_for(256, 256, samples=0)
        assert rgb._mjr_context.offSamples == model.vis.quality.offsamples
        assert depth._mjr_context.offSamples == 0
        assert worker.model.vis.quality.offsamples == model.vis.quality.offsamples
        option = mujoco.MjvOption()
        option.geomgroup[0] = False
        depth.update_scene(data, camera="robot0_eye_in_hand", scene_option=option)
        depth.enable_depth_rendering()
        measured = float(depth.render()[192, 168])
        camera = fixture["camera"]
        k = camera["K"]
        ray = matrix_from_quat(camera["pose"]["q"]) @ np.array([
            (168 - k[0][2]) / k[0][0], -(192 - k[1][2]) / k[1][1], -1])
        hit = mujoco.mj_ray(model, data, np.array(camera["pose"]["p"]), ray,
                            None, 1, -1, np.array([-1], dtype=np.int32))
        assert abs(measured - hit) < .001
        assert abs(measured - fixture["corruptedDepthM"]) > .08
    finally:
        worker.close()
