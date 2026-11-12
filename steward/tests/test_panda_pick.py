"""Object-independent pick admission, instruction and observed grasp evidence."""
import numpy as np
import pytest

from steward.ego.panda_pick import PickEvidence, pick_prompt, pickable


def target(category='milk'):
    return {'id': 'e:1:seen-2', 'category': category, 'role': category,
            'position': [0, 0, .03], 'sizeM': [.04, .04, .06], 'lastSeenAgoS': 0}


def frame(step, height, tool_height=None):
    depth = np.ones((64, 64), np.float32)
    depth[29:35, 29:35] = 1 - height
    rgb = np.zeros((64, 64, 3), np.uint8)
    rgb[29:35, 29:35] = [210, 20, 10]
    return {'episodeId': 1, 'step': step, 'stamp': step * .002,
            'tool': {'p': [0, 0, tool_height if tool_height is not None else height + .03], 'q': [1, 0, 0, 0]},
            'images': {'external': rgb, 'wrist': rgb}, 'depth': {'external': depth, 'wrist': depth},
            'cameras': {role: {'K': [[200, 0, 32], [0, 200, 32], [0, 0, 1]],
                               'pose': {'p': [0, 0, 1], 'q': [1, 0, 0, 0]}} for role in ('external', 'wrist')},
            'encoders': {}, 'selfVisual': []}


def test_pick_instruction_is_bound_to_selected_object():
    assert pick_prompt(target()) == 'pick up the milk'
    assert pick_prompt(target('cream_cheese')) == 'pick up the cream cheese'
    assert pick_prompt(target('unknown')) == 'pick up the selected object'
    assert pickable(target())
    assert not pickable(dict(target(), category='basket', role='basket'))
    assert not pickable(dict(target(), lastSeenAgoS=2))


@pytest.mark.parametrize('category,height', [('alphabet_soup', .075), ('milk', .14),
    ('butter', .018), ('cream_cheese', .02), ('salad_dressing', .147), ('tomato_sauce', .083)])
def test_pick_requires_visible_lift_coupled_to_tool_motion(category, height):
    selected = dict(target(category), position=[0, 0, height / 2], sizeM=[.04, .04, height])
    evidence = PickEvidence(selected, frame(0, height))
    for index, lift in enumerate((0, .015, .03, .045, .06, .075)):
        result = evidence.update(frame(index * 25, height + lift), .04)
    assert result['target'] == target()['id']
    assert result['grasp'] == 'confirmed'
    assert result['placement'] == 'unconfirmed'


def test_empty_gripper_and_stationary_target_never_count_as_pick_success():
    for width in (.0, .04):
        evidence = PickEvidence(target(), frame(0, .06))
        for index in range(8):
            result = evidence.update(frame(index * 25, .06, .09 + index * .02), width)
        assert result['grasp'] != 'confirmed'


def test_repeated_packet_or_missing_visual_evidence_cannot_confirm_lift():
    evidence = PickEvidence(target(), frame(0, .06))
    for _ in range(10):
        result = evidence.update(frame(25, .14), .04)
    assert result['grasp'] != 'confirmed'
    obs = frame(100, .16)
    obs['images'] = {role: np.zeros((64, 64, 3), np.uint8) for role in ('external', 'wrist')}
    result = evidence.update(obs, .04)
    assert result['grasp'] != 'confirmed'


def test_pick_prediction_does_not_authorize_contact_with_an_unselected_basket():
    from steward.ego.panda_prediction import PandaPrediction
    from steward.ego.panda_policy import PolicyFailure
    obs = frame(0, .06)
    for depth in obs['depth'].values(): depth[15:48, 15:48] = .94
    obs['toolRig'] = {'chain': [], 'mount': {'p': [0, 0, 0], 'q': [1, 0, 0, 0]}}
    prediction = PandaPrediction(obs, [0, 0, .03], target_size=[.04, .04, .06])
    for depth in obs['depth'].values(): depth[:] = 0
    prediction.require_observed(np.array([0., 0., .04]), contact=True)
    with pytest.raises(PolicyFailure, match='lacks visible'):
        prediction.require_observed(np.array([.2, .2, .08]), contact=True)


def test_moving_roi_over_a_static_red_surface_never_counts_as_target_motion():
    def static_frame(index):
        obs = frame(index * 25, .06)
        for role in ('external', 'wrist'):
            obs['depth'][role][:] = 1
            obs['images'][role][:] = 0
            obs['images'][role][:, 29:35] = [210, 20, 10]
            obs['cameras'][role]['pose'] = {'p': [0, -1, .1], 'q': [2**-.5, 2**-.5, 0, 0]}
        obs['tool']['p'] = [0, 0, .09 + index * .015]
        return obs
    selected = dict(target('unknown'), position=[0, 0, .06])
    evidence = PickEvidence(selected, static_frame(0))
    results = [evidence.update(static_frame(i), .04) for i in range(6)]
    assert all(r['grasp'] != 'confirmed' for r in results)


def test_moving_wrist_fov_over_static_surface_cannot_confirm_a_grasp():
    def observation(index):
        obs = frame(index * 25, .06)
        for role in ('external', 'wrist'):
            obs['depth'][role][:] = 1
            obs['images'][role][:] = 0
            obs['images'][role][:, 29:35] = [210, 20, 10]
            obs['cameras'][role]['pose'] = {'p': [0, -1, .1], 'q': [2**-.5, 2**-.5, 0, 0]}
        obs['cameras']['wrist']['K'] = [[1000, 0, 32], [0, 1000, 32], [0, 0, 1]]
        obs['cameras']['wrist']['pose']['p'][2] = .06 + index * .015
        obs['tool']['p'][2] = .09 + index * .015
        return obs
    evidence = PickEvidence(dict(target('unknown'), position=[0, 0, .06]), observation(0))
    assert all(evidence.update(observation(i), .04)['grasp'] != 'confirmed' for i in range(6))


def test_same_colour_static_components_cannot_be_rebound_to_the_selected_identity():
    def observation(index):
        obs = frame(index * 25, .06)
        for role in ('external', 'wrist'):
            obs['depth'][role][:] = 1
            obs['images'][role][:] = 0
            for k in range(5):
                x, z = -.1 + k * .045, .06 + k * .02
                column = round(x * 200 / (1-z) + 32)
                obs['depth'][role][30:34, column-2:column+2] = 1-z
                obs['images'][role][30:34, column-2:column+2] = [210, 20, 10]
        obs['tool']['p'] = [-.1 + index * .045, 0, .09 + index * .02]
        return obs
    selected = dict(target('unknown'), position=[-.1, 0, .06])
    evidence = PickEvidence(selected, observation(0))
    assert all(evidence.update(observation(i), .04)['grasp'] != 'confirmed' for i in range(6))
    assert evidence.position[0] < -.08


def test_failed_pick_binding_keeps_the_previous_identity_and_evidence(monkeypatch):
    import steward.ego.panda_runtime as module
    runtime = module.PandaEgoRuntime(None)
    old_entity, old_evidence = {'id': 'old'}, object()
    runtime.pick_entity, runtime.pick_evidence = old_entity, old_evidence
    runtime.scene['entities'] = [{'id': target()['id']}]
    runtime.sensor.observation = frame(0, .06)
    def fail(*args): raise ValueError('measurement failed')
    monkeypatch.setattr(module, 'PickEvidence', fail)
    with pytest.raises(ValueError, match='measurement failed'):
        runtime.bind_pick(target())
    assert runtime.pick_entity is old_entity and runtime.pick_evidence is old_evidence


def test_stopping_waits_for_catalog_load_and_never_publishes_after_stop(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import steward.ego.panda_surfaces as surfaces
    from steward.ego.panda_runtime import PandaEgoRuntime
    published = []
    runtime = PandaEgoRuntime(SimpleNamespace(telemetry=published.append))
    runtime.sensor.observation = frame(0, .06)
    runtime.latest = {'episodeId': 1}
    entered, release = threading.Event(), threading.Event()
    def load():
        entered.set()
        assert release.wait(5)
    runtime.visual_prior = SimpleNamespace(load=load)
    monkeypatch.setattr(runtime, 'observe', lambda: None)
    monkeypatch.setattr(surfaces, 'surface_entities', lambda *args: [])
    runtime.start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                assert entered.wait(2)
                stopping = pool.submit(runtime.stop)
                assert runtime.stop_event.wait(2)
                assert not stopping.done()
            finally:
                release.set()
            stopping.result(timeout=3)
        assert not runtime.thread.is_alive() and published == []
    finally:
        release.set()
        runtime.stop()
