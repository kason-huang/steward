"""Visible RGB-D objects survive the task-specific Panda semantic filter."""
import numpy as np
import mujoco

from steward.ego.observed_surfaces import append_surfaces
from steward.ego.panda_surfaces import surface_entities
from steward.scene.schema import SceneState


def sample():
    # A down-looking camera sees two separated red/blue patches above the floor.
    depth = np.ones((24, 32), dtype=np.float32)
    depth[6:18, 3:11] = .95
    depth[6:18, 21:29] = .93
    rgb = np.zeros((24, 32, 3), dtype=np.uint8)
    rgb[6:18, 3:11] = [210, 20, 10]
    rgb[6:18, 21:29] = [10, 30, 220]
    return {'images': {'external': rgb}, 'depth': {'external': depth},
            'cameras': {'external': {'K': [[100, 0, 16], [0, 100, 12], [0, 0, 1]],
                                      'pose': {'p': [0, 0, 1], 'q': [1, 0, 0, 0]}}},
            'encoders': {}, 'selfVisual': [], 'worldObjects': [{'category': 'secret'}]}


def test_unlabelled_visible_objects_retain_measured_shape_and_colour():
    entities = surface_entities(sample(), 'e:1:', [])
    assert len(entities) == 2
    assert all(e.category.value == 'unknown' for e in entities)
    assert 'secret' not in str([e.to_dict() for e in entities])
    model = mujoco.MjModel.from_xml_string('<mujoco/>')
    rendered = mujoco.MjvScene(model, maxgeom=5000)
    append_surfaces(rendered, SceneState('ego', entities=entities))
    assert rendered.ngeom > 100
    assert all(g.type == mujoco.mjtGeom.mjGEOM_TRIANGLE for g in rendered.geoms[:rendered.ngeom])
    assert {tuple(np.round(g.rgba[:3] * 255).astype(int)) for g in rendered.geoms[:rendered.ngeom]} == {
        (210, 20, 10), (10, 30, 220)}
    assert all(.04 < g.pos[2] < .08 for g in rendered.geoms[:rendered.ngeom])


def test_known_regions_and_self_geometry_are_removed_without_connecting_depth_edges():
    obs = sample()
    known = [{'pose': {'value': {'p': [-.09, 0, .05], 'q': [1, 0, 0, 0]}},
              'stateVars': {'size': {'value': [.09, .15, .1]}}}]
    assert len(surface_entities(obs, 'e:', known)) == 1
    obs['selfVisual'] = [{'chain': [], 'mount': {'p': [.08, 0, .07], 'q': [1, 0, 0, 0]},
                          'bounds': [0, 0, 0, .05, .1, .03]}]
    assert surface_entities(obs, 'e:', known) == []


def test_tracks_keep_ids_but_do_not_keep_stale_surfaces_when_out_of_view():
    first = surface_entities(sample(), 'e:', [])
    second = surface_entities(sample(), 'e:', [], first)
    assert {e.id for e in first} == {e.id for e in second}
    obs = sample(); obs['depth']['external'][:] = 1
    assert surface_entities(obs, 'e:', [], first) == []


def test_surface_triangle_transform_and_empty_frame_do_not_leave_ghost_geometry():
    entities = surface_entities(sample(), 'e:', [])
    scene = SceneState('ego', entities=entities)
    model = mujoco.MjModel.from_xml_string('<mujoco/>')
    rendered = mujoco.MjvScene(model, maxgeom=5000)
    append_surfaces(rendered, scene)
    before = rendered.geoms[0].pos.copy()
    entities[0].pose.value['p'][0] += .25
    rendered.ngeom = 0
    append_surfaces(rendered, scene)
    np.testing.assert_allclose(rendered.geoms[0].pos, before + [.25, 0, 0], atol=1e-6)
    rendered.ngeom = 0
    append_surfaces(rendered, SceneState('ego'))
    assert rendered.ngeom == 0


def test_background_surface_work_does_not_block_policy_observation(monkeypatch):
    import threading
    from types import SimpleNamespace
    from concurrent.futures import ThreadPoolExecutor
    from steward.ego.panda_runtime import PandaEgoRuntime
    import steward.ego.panda_surfaces as surfaces
    runtime = PandaEgoRuntime(SimpleNamespace(telemetry=lambda scene: None))
    runtime.sensor.observation = dict(sample(), episodeId=1, step=0)
    runtime.latest = {'episodeId': 1, 'judgment': {}}
    monkeypatch.setattr(runtime.sensor, 'read', lambda **kwargs: None)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return []
    monkeypatch.setattr(surfaces, 'surface_entities', blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        background = pool.submit(runtime.observe_surfaces)
        assert entered.wait(2)
        try:
            policy = pool.submit(runtime.policy_observation, force=True)
            assert policy.result(timeout=1)[1] == {}
        finally:
            release.set()
        background.result()


def test_occluded_held_target_cannot_reappear_as_another_unknown_package():
    from types import SimpleNamespace
    from steward.ego.panda_runtime import PandaEgoRuntime
    runtime = PandaEgoRuntime(SimpleNamespace(telemetry=lambda scene: None))
    obs = dict(sample(), episodeId=1, step=0, tool={'p': [-.09, 0, .09]})
    runtime.sensor.observation = obs
    runtime.latest = {'episodeId': 1, 'judgment': {}}
    # The semantic can is absent in this frame because its label is occluded.
    runtime.evidence.grasp = 'confirmed'
    runtime.evidence.last_can = np.array([-.09, 0, .05])
    runtime.observe_surfaces()
    assert len(runtime.visible_objects) == 1
    assert runtime.visible_objects[0].pose.value['p'][0] > 0
