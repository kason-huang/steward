"""ESim placing: margins from the footprint of the load as it is turned."""

from __future__ import annotations

import math

import numpy as np
import pytest


def test_a_load_turned_45_degrees_is_kept_wholly_on_the_support():
    """D2: keep = max(hx, hy) is not a turned load's half width: at 45 deg a 0.20 m half-wide load
    passed a 0.28 m half-wide support and overhung it by up to 8 cm."""
    from steward.assets.generators import steward_bot
    from steward.ego.esim import Esim, PredictionError, entity_box
    from steward.ego.estimator import EgoEstimator, Track

    model = steward_bot().to_dict()
    est = EgoEstimator(robot_model=model)

    def track(oid, cat, pos, size):
        return Track(id=oid, category_counts={cat: 5.0}, pos=np.array(pos, float), var=np.full(3, 1e-10), yaw=0.0,
                     yaw_var=1e-6, size=np.array(size, float), observations=10, first_seen=0.0, last_seen=0.0, existence=1.0)

    est.tracks = {"e:4": track("e:4", "table", [0.8, 0.0, 0.375], [0.56, 0.56, 0.75]),
                  "e:2": track("e:2", "box", [0.3, -0.3, 0.8], [0.40, 0.40, 0.05])}
    es = est.to_scene()
    table, box = es.find("e:4"), es.find("e:2")
    esim = Esim(model)
    with pytest.raises(PredictionError):
        esim._choose_spot(es, table, entity_box(table)[0], box, entity_box(box)[0], [0.0, 0.0], math.pi / 4, 0.0)
    spot, _ = esim._choose_spot(es, table, entity_box(table)[0], box, entity_box(box)[0], [0.0, 0.0], 0.0, 0.0)
    assert spot == pytest.approx((0.8, 0.0))


def test_the_place_risk_uses_the_turned_footprint_too():
    """D2 (review): the spot check used the turned footprint but the receipt's edge risk still took
    max(hx, hy): a 0.20 m half-wide load turned 45 deg, centred on a 0.31 m half-wide support, is
    2.7 cm from the edge, and was rated as 11 cm."""
    from steward.ego.esim import Esim
    from steward.ego.planner import Box2D

    sbox = Box2D(id="t", cx=0.0, cy=0.0, yaw=0.0, hx=0.31, hy=0.31)
    load = Box2D(id="l", cx=0.0, cy=0.0, yaw=0.0, hx=0.20, hy=0.20)
    ex, ey = Esim._turned_half(load, math.pi / 4, sbox.yaw)
    assert ex == pytest.approx(0.2828, abs=1e-3) and ey == pytest.approx(0.2828, abs=1e-3)
    assert Esim._edge_margin(sbox, (0.0, 0.0), ex, ey) == pytest.approx(0.0272, abs=1e-3)
    # unturned, a long thin load is as far from the long edges as its short side allows
    thin = Box2D(id="n", cx=0.0, cy=0.0, yaw=0.0, hx=0.25, hy=0.02)
    assert Esim._edge_margin(sbox, (0.0, 0.0), *Esim._turned_half(thin, 0.0, 0.0)) == pytest.approx(0.06)


def test_placement_verification_distinguishes_no_evidence_wrong_goal_and_success():
    from types import SimpleNamespace

    from steward.ego.skills import Job
    from steward.ego.verification import placement_verdict

    observation = {"status": "observed", "releasedAt": 2.0, "observation": {"stamp": 3.0, "centreM": [0.2, 0, 0.75]}}
    support = SimpleNamespace(id="e:2", pos=np.array([0.0, 0.0, 0.35]), yaw=0.0, last_seen=3.0)
    estimator = SimpleNamespace(
        stamp=3.0, version=7, config=SimpleNamespace(placement_observation_max_age_s=0.5),
        tracks={"e:1": object()}, placement_evidence=lambda _: observation, support_of=lambda _: support,
    )
    job = Job("job-1", "place", "r-1", state="succeeded", started=1.0, finished=2.8)
    receipt = {"plan": {"object": "e:1"}}
    goal = {"entity": "e:1", "support": "e:2", "offset": [0.2, 0.0], "toleranceM": 0.05}
    verdict = placement_verdict(estimator, job, receipt, goal)
    assert verdict["accepted"] and verdict["terminated"]
    assert verdict["effect"] == verdict["intent"] == "satisfied"
    wrong_support = placement_verdict(estimator, job, receipt, {**goal, "support": "e:3"})
    assert wrong_support["effect"] == "satisfied" and wrong_support["intent"] == "unsatisfied"
    wrong_offset = placement_verdict(estimator, job, receipt, {**goal, "offset": [-0.2, 0.0]})
    assert wrong_offset["offsetErrorM"] == pytest.approx(0.4)
    assert wrong_offset["intent"] == "unsatisfied"
    estimator.stamp = 4.0
    stale = placement_verdict(estimator, job, receipt, goal)
    assert stale["effect"] == stale["intent"] == "unknown"
    estimator.stamp = 3.0
    observation["releasedAt"] = 2.9
    unrelated = placement_verdict(estimator, job, receipt, goal)
    assert unrelated["effect"] == unrelated["intent"] == "unknown"
    observation["status"] = "unknown"
    obscured = placement_verdict(estimator, job, receipt, goal)
    assert obscured["effect"] == obscured["intent"] == "unknown"


def test_ideal_detection_uses_the_measured_position_to_verify_the_support():
    from steward.assets.generators import steward_bot
    from steward.ego.estimator import EgoEstimator, Track
    from steward.ego.skills import Job
    from steward.ego.verification import placement_verdict

    def track(eid, category, position, size):
        return Track(id=eid, category_counts={category: 5.0}, pos=np.array(position),
                     size=np.array(size), var=np.full(3, 0.01**2), yaw=0.0, yaw_var=0.01,
                     observations=5, first_seen=0.0, last_seen=3.0)

    estimator = EgoEstimator(robot_model=steward_bot().to_dict())
    load = track("e:load", "box", [0.0, 0.0, 0.8], [0.10, 0.09, 0.10])
    target = track("e:target", "table", [0.0, 0.0, 0.375], [0.5, 0.5, 0.75])
    adjacent = track("e:adjacent", "cabinet", [0.6, 0.0, 0.375], [0.5, 0.5, 0.75])
    drawer = track("e:drawer", "drawer", [0.6, 0.0, 0.62], [0.49, 0.49, 0.20])
    estimator.tracks = {obj.id: obj for obj in (load, target, adjacent, drawer)}
    estimator.release_at(load.id, [0.0, 0.0], body={"stamp": 2.0})
    receipt = {"plan": {"object": load.id}}
    job = Job("j", "place", "r", state="succeeded", started=1.0, finished=3.0)
    goal = {"entity": load.id, "support": target.id}
    det = {"category": "box", "size": [0.10, 0.09, 0.10]}
    measured = np.array([0.6, 0.0, 0.8])

    estimator.stamp = 2.0
    estimator._fuse(load, det, measured, 0.0, 0.01)
    assert estimator.placement_evidence(load.id)["status"] == "unknown"

    estimator.stamp = 3.0
    estimator._fuse(load, det, measured, 0.0, 0.01)
    load.pos[:] = [0.0, 0.0, 0.8]
    verdict = placement_verdict(estimator, job, receipt, goal)
    assert verdict["evidence"]["observation"]["source"] == "ideal_detection"
    assert verdict["effect"] == "satisfied"
    assert verdict["intent"] == "unsatisfied"
    assert verdict["observedSupport"] == adjacent.id

    estimator.stamp = 3.1
    estimator._fuse(load, det, np.array([0.0, 0.0, 0.88]), 0.0, 0.01)
    verdict = placement_verdict(estimator, job, receipt, goal)
    assert verdict["effect"] == verdict["intent"] == "unknown"
