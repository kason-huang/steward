"""Embodiment-selected grasp simulation, independent of the actuator controller."""
from pathlib import Path

import numpy as np
import pytest

from steward.embodiments import EMBODIMENTS, simulation_grasp
from steward.assets.generators import steward_bot, bowl
from steward.assets.cobot import cobot_magic
from steward.scene.schema import SceneState, pose, exact
from steward.world.sim import WorldSim


def profile(monkeypatch, tmp_path, body, mode, extra=''):
    path = tmp_path / (body + '.yaml')
    path.write_text(f'id: {body}\nmechanical:\n  grasp:\n    sim: {mode}\n' + extra)
    monkeypatch.setitem(EMBODIMENTS, body, EMBODIMENTS[body] | {'profile': str(path)})
    return path


def physical_cobot():
    """Test-only contact-capable fingers; the stock Cobot uses visual-only fingers."""
    from steward.scene.schema import primitive
    robot = cobot_magic()
    for link in robot.rigid.links:
        if link.id in ('L_link7', 'L_link8', 'R_link7', 'R_link8'):
            link.geometry['collision'] = [primitive('box', [.01, .01, .04], pose())]
    return robot


def test_existing_profiles_select_the_declared_backend():
    assert simulation_grasp('steward-bot').mode == 'ideal-attach'
    assert simulation_grasp('cobot-magic').mode == 'ideal-attach'
    assert simulation_grasp('libero-panda').mode == 'contact'


@pytest.mark.parametrize('mode', ['unknown', 'null', '[]'])
def test_invalid_mode_is_rejected_without_falling_back(monkeypatch, tmp_path, mode):
    profile(monkeypatch, tmp_path, 'steward-bot', mode)
    with pytest.raises(ValueError, match='grasp.sim'):
        simulation_grasp('steward-bot')


def test_missing_or_mismatched_profile_is_rejected(monkeypatch, tmp_path):
    path = profile(monkeypatch, tmp_path, 'steward-bot', 'ideal-attach')
    path.write_text('id: steward-bot\nmechanical: {}\n')
    with pytest.raises(ValueError, match='grasp.sim'):
        simulation_grasp('steward-bot')
    path.write_text('id: different-body\nmechanical: {grasp: {sim: contact}}\n')
    with pytest.raises(ValueError, match='identity'):
        simulation_grasp('steward-bot')


def test_visual_only_gripper_cannot_silently_enable_contact(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'steward-bot', 'contact')
    with pytest.raises(ValueError, match='physical fingers'):
        WorldSim(SceneState('world', entities=[steward_bot()]).to_dict())


def test_contact_cobot_moves_fingers_without_any_ideal_grasp_side_effects(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact')
    robot = physical_cobot(); robot.id = 'w:contact'
    obj = bowl(); obj.id = 'w:bowl'; obj.pose = exact(pose([4, 4, .2]))
    sim = WorldSim(SceneState('world', entities=[robot, obj]).to_dict())
    try:
        rs = sim.robots[robot.id]
        assert rs.grasp.mode == 'contact' and sim.panda is None
        grip = next(iter(rs.grippers))
        g = rs.grippers[grip]
        sim.command(robot.id, {'gripper': {grip: 'close'}})
        sim.command(robot.id, {'gripper': {grip: 'open'}})
        controls = sim.data.ctrl.copy()
        sim.command(robot.id, {'gripper': {grip: 'close'}})
        assert not np.array_equal(controls, sim.data.ctrl)
        assert g.closed and g.attached is None and g.handle is None
        # Even stale ideal state must never move an object, disable its collision,
        # or apply a synthetic drawer force when this robot uses contact mode.
        g.attached = obj.id; g.rel = pose()
        g.handle = {'unexpected': 'ideal handle must not be evaluated'}
        positions = sim.data.qpos.copy()
        ct, ca = sim.model.geom_contype.copy(), sim.model.geom_conaffinity.copy()
        sim._try_attach(rs, g, grip)
        sim._suspend_contacts(rs, g)
        sim._drive_handles(); sim._follow_attached(); sim._release(rs, g)
        np.testing.assert_array_equal(sim.data.qpos, positions)
        np.testing.assert_array_equal(sim.model.geom_contype, ct)
        np.testing.assert_array_equal(sim.model.geom_conaffinity, ca)
        assert not any(e['type'].startswith('grasp/') for e in sim.events)
        g.attached = None; g.handle = None
        sim.command(robot.id, {'gripper': {grip: 'open'}})
        assert not g.closed
    finally:
        sim.stop()


def test_mixed_world_selects_grasp_per_robot_and_keeps_reset_configuration(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact')
    a, b = steward_bot(), physical_cobot()
    a.id, b.id = 'w:ideal', 'w:physical'
    b.pose = exact(pose([4, 0, 0]))
    sim = WorldSim(SceneState('world', entities=[a,b]).to_dict())
    try:
        for _ in range(2):
            assert sim.robots[a.id].grasp.mode == 'ideal-attach'
            assert sim.robots[b.id].grasp.mode == 'contact'
            sim.step(5); sim.reset()
    finally:
        sim.stop()


def test_declared_contact_fingers_must_exist_and_be_actuated(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact',
            '  grippers:\n    - finger_joints: [missing_finger_a, missing_finger_b]\n')
    with pytest.raises(ValueError, match='physical fingers'):
        WorldSim(SceneState('world', entities=[cobot_magic()]).to_dict())


def test_prepared_panda_uses_contact_profile_and_preserves_physics():
    from steward.scene.libero import load_scene
    bundle = Path(__file__).resolve().parents[1] / 'var/step4/wsim-root/libero-step3'
    if not bundle.exists(): pytest.skip('Requires prepared LIBERO Panda bundle')
    scene, build = load_scene(bundle, 0, panda=True)
    sim = WorldSim(scene, prepared_build=build)
    try:
        assert sim.robots['w:panda'].grasp.mode == 'contact'
        assert sim.panda is not None
        sim.step(25)
        assert not any(e['type'].startswith('grasp/') for e in sim.events)
    finally:
        sim.stop()


def test_stock_cobot_cannot_enable_contact_without_finger_colliders(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact')
    with pytest.raises(ValueError, match='collision geometry'):
        WorldSim(SceneState('world', entities=[cobot_magic()]).to_dict())


def test_contact_without_finger_drive_is_rejected(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact')
    robot = physical_cobot()
    for joint in robot.rigid.joints:
        if joint.id == 'L_joint7': joint.actuator = None
    with pytest.raises(ValueError, match='actuated physical fingers'):
        WorldSim(SceneState('world', entities=[robot]).to_dict())


def test_profile_mode_is_stable_until_new_episode(monkeypatch, tmp_path):
    path = profile(monkeypatch, tmp_path, 'cobot-magic', 'ideal-attach')
    robot = physical_cobot()
    sim = WorldSim(SceneState('world', entities=[robot]).to_dict())
    try:
        path.write_text(path.read_text().replace('ideal-attach', 'contact'))
        sim.step(2)
        sim._rebuild(sim.template)
        assert sim.robots[robot.id].grasp.mode == 'ideal-attach'
        sim.reset()
        assert sim.robots[robot.id].grasp.mode == 'contact'
    finally:
        sim.stop()


def test_contact_declarations_cannot_omit_a_gripper(monkeypatch, tmp_path):
    profile(monkeypatch, tmp_path, 'cobot-magic', 'contact',
            '  grippers:\n    - finger_joints: [L_joint7, L_joint8]\n')
    with pytest.raises(ValueError, match='every grasp-point gripper'):
        WorldSim(SceneState('world', entities=[physical_cobot()]).to_dict())


def test_bad_mode_change_on_reset_preserves_the_running_world(monkeypatch, tmp_path):
    path = profile(monkeypatch, tmp_path, 'cobot-magic', 'ideal-attach')
    sim = WorldSim(SceneState('world', entities=[cobot_magic()]).to_dict())
    try:
        model, episode = sim.model, sim.episode
        path.write_text(path.read_text().replace('ideal-attach', 'contact'))
        with pytest.raises(ValueError, match='collision geometry'):
            sim.reset()
        assert sim.model is model and sim.episode == episode
        assert sim.robots[next(iter(sim.robots))].grasp.mode == 'ideal-attach'
        sim.step(2)
    finally:
        sim.stop()


def test_panda_cannot_fall_back_to_contact_when_ideal_grasp_points_are_missing(monkeypatch, tmp_path):
    from steward.scene.libero import load_scene
    bundle = Path(__file__).resolve().parents[1] / 'var/step4/wsim-root/libero-step3'
    if not bundle.exists(): pytest.skip('Requires prepared LIBERO Panda bundle')
    profile(monkeypatch, tmp_path, 'libero-panda', 'ideal-attach')
    scene, build = load_scene(bundle, 0, panda=True)
    with pytest.raises(ValueError, match='ideal-attach requires grasp-point'):
        WorldSim(scene, prepared_build=build)
