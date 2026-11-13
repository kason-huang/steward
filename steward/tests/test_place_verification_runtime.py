"""Runtime verification owns motion until evidence is collected, without using release priors."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from steward.assets.generators import steward_bot
from steward.ego.runtime import EgoRuntime, RobotLink
from steward.ego.estimator import Track
from steward.ego.skills import SkillExecutor


@pytest.fixture
def runtime():
    r = EgoRuntime(RobotLink('http://127.0.0.1:9'), steward_bot().to_dict())
    for eid, cat, pos, size in [('e:table', 'table', [.7, 0, .375], [1, .8, .75]),
                                 ('e:piece', 'board', [.5, 0, .753], [.06, .06, .006])]:
        r.estimator.tracks[eid] = Track(eid, {cat: 10}, np.array(pos), np.ones(3)*1e-8,
                                       0, .001, np.array(size), 10, 0, 0, existence=1)
    r._receipt_generation['rcpt'] = r._generation
    r._place_releases['j'] = (r._generation, 1.)
    yield r
    r.stop()


def receipt():
    return {'id': 'rcpt', 'plan': {'object': 'e:piece', 'spot': [.5, 0], 'support': 'e:table'}, 'params': {}}


def test_release_at_without_new_observations_cannot_pass(runtime):
    runtime.estimator.release_at('e:piece', [.5, 0])
    clock = iter([2, 10])
    r = runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), receipt(),
                              lambda: {'stamp': next(clock), 'gripper': {'holding': False}})
    assert r['status'] == 'unknown'


def test_runtime_accepts_distinct_observations_and_never_retries_motion(runtime):
    clock = iter([2, 2.5])

    def tick():
        stamp = next(clock)
        runtime.estimator.stamp = stamp
        for t in runtime.estimator.tracks.values():
            t.last_seen = stamp
        return {'stamp': stamp, 'gripper': {'holding': False}}

    r = runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), receipt(), tick)
    assert r['status'] == 'passed'
    assert r['positionErrorM'] == 0


def test_reset_during_verification_is_unknown(runtime):
    def tick():
        runtime._generation += 1
        return {'stamp': 2, 'gripper': {'holding': False}}
    assert runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), receipt(), tick)['status'] == 'unknown'


def test_next_skill_is_rejected_while_verification_is_waiting(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    st = {'stamp': 2, 'odometry': {'x': 0, 'y': 0, 'yaw': 0}, 'joints': {}, 'gripper': {}}

    def verify(job, receipt, tick):
        entered.set()
        assert release.wait(10)
        return {'status': 'failed', 'checks': {'support': 'failed'}, 'reason': 'stacked'}

    executor = SkillExecutor(lambda c: None, lambda: st, lambda eid: eid,
                             verify_place=verify, on_finish=lambda j: finished.set())
    monkeypatch.setattr(executor, '_place', lambda job, plan: None)
    try:
        job = executor.start('place', receipt())
        assert entered.wait(10)
        assert job.state == 'running' and job.execution == 'succeeded'
        with pytest.raises(RuntimeError, match='still running'):
            executor.start('pick', receipt())
        release.set()
        assert finished.wait(10)
        assert job.state == 'succeeded' and job.verification['status'] == 'failed'
    finally:
        release.set()
        executor.cancel()
        assert finished.wait(10)


@pytest.mark.parametrize('actual_x,calibrated,stacked,expected', [(.5, True, False, 'passed'), (.55, True, False, 'failed'),
    (.5, False, False, 'unknown'), (.55, False, False, 'failed'), (.5, True, True, 'not-passed'), (.5, False, True, 'not-passed')])
def test_physical_sensor_observations_overrule_the_commanded_release_position(actual_x, calibrated, stacked, expected):
    from steward.assets.generators import building, dining_table, printed_board
    from steward.world.sim import WorldSim, PerceptionConfig
    from steward.ego.verification import VerificationConfig
    entities = [f().to_dict() for f in (building, steward_bot, dining_table, printed_board)]
    for ent, eid in zip(entities, ['w:room', 'w:robot', 'w:table', 'w:piece'], strict=True):
        ent['id'] = eid
    entities[2]['pose']['value']['p'] = [.7, 0, 0]
    entities[3]['pose']['value']['p'] = [actual_x, 0, .759 if stacked else .753]
    if stacked:
        under = printed_board().to_dict()
        under['id'] = 'w:under'
        under['pose']['value']['p'] = [actual_x, 0, .753]
        entities.append(under)
    sim = WorldSim({'mode': 'world', 'entities': entities},
                   perception=PerceptionConfig(odom_pos_sigma=0, odom_yaw_sigma=0, pos_sigma_base=.0001,
                                               pos_sigma_per_m=0, size_sigma_rel=0, yaw_sigma=0))
    r = EgoRuntime(RobotLink('http://127.0.0.1:9'), entities[1])
    try:
        r.config.verification = VerificationConfig(timeout_s=10)
        if calibrated:
            # Match the explicitly controlled detector noise; defaults deliberately remain
            # uncertain at millimetre contact resolution and must not report success.
            r.estimator.config.pos_sigma_base = .0001
            r.estimator.config.pos_sigma_per_m = 0
            r.estimator.config.process_noise = 1e-10
        # Seed the commanded pose, not the physical position. Only sensor updates can correct it.
        for eid, cat, pos, size in [('e:table', 'table', [.7, 0, .375], [1.2, .8, .75]),
                                     ('e:piece', 'board', [.5, 0, .753], [.12, .12, .006])]:
            r.estimator.tracks[eid] = Track(eid, {cat: 10}, np.array(pos), np.ones(3)*.02**2,
                                           0, .001, np.array(size), 10, 0, 0, existence=1)
        if stacked:
            r.estimator.tracks['e:under'] = Track('e:under', {'board': 10}, np.array([.5, 0, .753]),
                np.ones(3)*.02**2, 0, .001, np.array([.12, .12, .006]), 10, 0, 0, existence=1)
        r.estimator.release_at('e:piece', [.5, 0])
        r._receipt_generation['rcpt'] = r._generation
        r._place_releases['j'] = (r._generation, 0.)
        r.link.sensors = lambda: sim.sensors('w:robot')

        def tick():
            sim.step_seconds(.2)
            r._poll_once()
            return sim.sensors('w:robot')

        result = r._verify_place(SimpleNamespace(id='j', receipt='rcpt'), receipt(), tick)
        # World coordinates are used only here, after production verification returned.
        physical_x = sim._entity_pose('w:piece')['p'][0]
        assert abs(physical_x - actual_x) < .005
        if stacked:
            assert sim.entity_obb('w:piece').center[2] > .757
        assert (result['status'] != 'passed' if expected == 'not-passed' else result['status'] == expected), result
    finally:
        r.stop()
        sim.stop()


def test_cancel_during_verification_preserves_motion_outcome_and_settles(monkeypatch):
    entered, finished = threading.Event(), threading.Event()
    state = {'stamp': 2, 'odometry': {'x': 0, 'y': 0, 'yaw': 0}, 'joints': {}}

    def verify(job, receipt, tick):
        entered.set()
        while True:
            tick()

    executor = SkillExecutor(lambda c: None, lambda: state, lambda eid: eid,
                             verify_place=verify, on_finish=lambda j: finished.set())
    monkeypatch.setattr(executor, '_place', lambda job, plan: None)
    try:
        job = executor.start('place', receipt())
        assert entered.wait(10)
        executor.stop()
        assert finished.wait(10)
        assert job.state == 'stopped'
        assert job.execution == 'succeeded'
        assert job.verification['status'] == 'unknown'
    finally:
        executor.cancel()
        assert finished.wait(10)


def test_verification_exception_never_retracts_after_a_successful_release(monkeypatch):
    finished = threading.Event()
    state = {'stamp': 2}

    def broken(*args):
        raise RuntimeError('camera unavailable')

    executor = SkillExecutor(lambda c: None, lambda: state, lambda eid: eid,
                             verify_place=broken, on_finish=lambda j: finished.set())
    monkeypatch.setattr(executor, '_place', lambda job, plan: setattr(executor, '_arm_engaged', True))
    monkeypatch.setattr(executor, '_safe_retract', lambda: pytest.fail('verification must not issue recovery motion'))
    job = executor.start('place', receipt())
    assert finished.wait(10)
    assert job.execution == 'succeeded'
    assert job.state == 'failed' and job.verification['status'] == 'unknown'
    assert 'verification error' in job.message


def test_http_wait_and_status_preserve_verification_and_prevent_overlapping_motion(runtime, monkeypatch):
    from steward.ego.server import ego_api
    from steward.net import call, HttpError
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    body = {'stamp': 2, 'odometry': {'x': 0, 'y': 0, 'yaw': 0}, 'joints': {}}
    monkeypatch.setattr(runtime.link, 'wait_until', lambda *a, **k: 2)
    monkeypatch.setattr(runtime.link, 'arm', lambda *a, **k: None)
    monkeypatch.setattr(runtime.link, 'release', lambda *a: None)
    monkeypatch.setattr(runtime.executor, '_state', lambda: body)
    monkeypatch.setattr(runtime.executor, '_place', lambda *a: None)

    def verify(*args):
        entered.set()
        assert release.wait(10)
        return {'status': 'failed', 'checks': {'support': 'failed'}, 'reason': 'stacked'}

    runtime.executor._verify_place = verify
    runtime.executor._on_finish = lambda job: finished.set()
    for rid in ['rcpt', 'next']:
        runtime.receipts[rid] = {**receipt(), 'id': rid, 'skill': 'place', 'feasible': True,
                                 'expiresAt': 100, 'risk': 0, 'params': {}}
    api = ego_api(runtime).start()
    base = f'http://127.0.0.1:{api.port}'
    try:
        job = call('POST', base+'/skill/run', {'receipt': 'rcpt'})
        assert entered.wait(10)
        status = call('GET', base+'/skill/status?job='+job['id'])
        assert status['state'] == 'running' and status['execution'] == 'succeeded'
        with pytest.raises(HttpError) as exc:
            call('POST', base+'/skill/run', {'receipt': 'next'})
        assert exc.value.status == 409
        assert not runtime.receipts['next'].get('used', False)
        release.set()
        result = call('POST', base+'/skill/wait', {'job': job['id'], 'timeoutS': 5})
        assert result['state'] == 'succeeded'
        assert result['verification']['status'] == 'failed'
    finally:
        release.set()
        runtime.executor.cancel()
        if entered.is_set():
            assert finished.wait(10)
        api.stop()


def test_late_camera_results_are_not_treated_as_current_observation(runtime):
    stamp = [2.0]
    def tick():
        for t in runtime.estimator.tracks.values():
            t.last_seen = stamp[0]
        stamp[0] += .5
        return {'stamp': 20+stamp[0], 'gripper': {'holding': False}}
    assert runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), receipt(), tick)['status'] == 'unknown'


def test_floor_placement_requires_observed_floor_height(runtime):
    plan = receipt()
    plan['plan'] = {'object': 'e:piece', 'floor': [.5, 0]}
    runtime.estimator.tracks['e:piece'].pos[2] = .003
    clock = iter([2, 2.5])
    def tick():
        at = next(clock)
        runtime.estimator.stamp = at
        runtime.estimator.tracks['e:piece'].last_seen = at
        return {'stamp': at, 'gripper': {'holding': False}}
    assert runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), plan, tick)['status'] == 'passed'


def test_wrist_yaw_request_is_not_certified_by_a_box_estimate(runtime):
    plan = receipt()
    plan['params'] = {'yaw': 0}
    clock = iter([2, 2.5])
    def tick():
        at = next(clock)
        runtime.estimator.stamp = at
        for t in runtime.estimator.tracks.values():
            t.last_seen = at
        return {'stamp': at, 'gripper': {'holding': False}}
    result = runtime._verify_place(SimpleNamespace(id='j', receipt='rcpt'), plan, tick)
    assert result['checks']['position'] == 'passed'
    assert result['checks']['heading'] == 'unknown' and result['status'] == 'unknown'


def test_release_callback_records_measured_time_without_creating_observation(runtime, monkeypatch):
    runtime._place_releases.clear()
    runtime.executor.jobs['j'] = SimpleNamespace(id='j', skill='place', receipt='rcpt')
    monkeypatch.setattr(runtime.link, 'sensors', lambda: {'stamp': 7})
    errors = []
    def release():
        try:
            runtime._released('e:piece', [.5, 0])
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=release, name='skill-j')
    thread.start()
    thread.join(10)
    assert not thread.is_alive() and not errors
    assert runtime._place_releases['j'] == (runtime._generation, 7)
    assert runtime.estimator.tracks['e:piece'].last_seen == 0
