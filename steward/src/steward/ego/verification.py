"""Task-level judgments made using only the robot's observations."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from steward.math3d import pose_inverse, pose_compose, quat_from_yaw

if TYPE_CHECKING:
    from steward.ego.estimator import EgoEstimator
    from steward.ego.skills import Job


def placement_verdict(estimator: EgoEstimator, job: Job, receipt: dict, goal: dict) -> dict:
    """Judge one placement against an explicit support and optional support-frame offset."""
    entity, wanted = goal.get("entity"), goal.get("support")
    if not all(isinstance(value, str) and value.startswith("e:") for value in (entity, wanted)):
        raise ValueError("placement verification needs robot-side entity and support identifiers")
    if job.skill != "place" or receipt["plan"].get("object") != entity:
        raise ValueError("job must be the placement of the requested entity")

    offset = goal.get("offset")
    tolerance = goal.get("toleranceM")
    if offset is not None:
        if not (isinstance(offset, list) and len(offset) == 2 and
                all(isinstance(v, (int, float)) and math.isfinite(v) for v in offset) and
                isinstance(tolerance, (int, float)) and math.isfinite(tolerance) and tolerance > 0):
            raise ValueError("a precise offset needs two finite coordinates and a positive toleranceM")

    result = {"job": job.id, "receipt": job.receipt, "accepted": True,
              "terminated": job.finished is not None, "jobState": job.state,
              "effect": "unknown", "intent": "unknown", "goal": goal,
              "egoVersion": estimator.version, "stamp": estimator.stamp}
    if job.finished is None:
        return {**result, "reason": "execution has not settled"}
    evidence = estimator.placement_evidence(entity)
    result["evidence"] = evidence
    observed = evidence.get("observation", {})
    max_age = estimator.config.placement_observation_max_age_s
    if (evidence["status"] != "observed" or
            not job.started <= evidence["releasedAt"] <= job.finished or
            estimator.stamp - observed["stamp"] > max_age):
        return {**result, "reason": "fresh, unobscured post-release evidence is required"}
    track = estimator.tracks.get(entity)
    support = None
    if track is not None and observed.get("source") == "ideal_detection":
        # Its full object box gives a measured bottom and footprint for support inference.
        measured = replace(track, pos=np.asarray(observed["centreM"]), size=np.asarray(observed["visibleSizeM"]))
        support = estimator.support_of(measured, observed_box=True)
    elif track is not None:
        support = estimator.support_of(track)
    if support is None or estimator.stamp - support.last_seen > max_age:
        return {**result, "reason": "support could not be inferred from fresh robot observations"}

    result.update(effect="satisfied", observedSupport=support.id)
    if support.id != wanted:
        return {**result, "intent": "unsatisfied", "reason": "observed support differs from the requested support"}
    if offset is not None:
        frame = {"p": support.pos.tolist(), "q": quat_from_yaw(support.yaw)}
        observed_pose = {"p": observed["centreM"], "q": [1, 0, 0, 0]}
        actual = pose_compose(pose_inverse(frame), observed_pose)["p"][:2]
        error = math.dist(offset, actual)
        result["offsetErrorM"] = error
        if error > tolerance:
            return {**result, "intent": "unsatisfied", "reason": "observed location is outside the requested tolerance"}
    return {**result, "intent": "satisfied", "reason": "observed placement matches the requested goal"}
