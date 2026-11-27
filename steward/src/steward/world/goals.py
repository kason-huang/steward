"""Scenario goals checked against the World State (Steward plane only; never sent to robots).

``relative-poses``: an arrangement stated as constraints between entity pairs, independent of where
on the support the arrangement lies or how it is turned. Each constraint says where ``b`` lies in
``a``'s frame (``offset`` [dx, dy] metres, ``yawDeg`` heading difference, default 0); with
``upright`` both must lie with their top face up. The report counts the satisfied constraints, the
largest group of entities joined by satisfied constraints (all entities: the arrangement is
complete), and lists each constraint's position and heading error, so a caller can see which
neighbourhood is off by how much instead of only that the whole is not yet right.
"""

from __future__ import annotations

import math

from steward.math3d import pose_compose, pose_inverse, quat_rotate, wrap_angle, yaw_from_quat


def score_placement(scene: dict, goal: dict) -> dict:
    """Independently score a released object on the requested support."""
    entity, support = goal["entity"], goal["support"]
    poses = {entry["id"]: entry["pose"]["value"] for entry in scene["entities"]}
    if entity not in poses or support not in poses:
        raise ValueError("placement score requires existing world entity and support IDs")
    supported = any(r["type"] == "supported-by" and r["a"]["entity"] == entity and r["b"]["entity"] == support
                    for r in scene["relations"])
    released = not any(r["type"] == "attached" and entity in (r["a"]["entity"], r["b"]["entity"])
                       for r in scene["relations"])
    actual = pose_compose(pose_inverse(poses[support]), poses[entity])["p"][:2]
    result = {"goal": goal, "supported": supported, "released": released, "observedOffsetM": actual,
              "stamp": scene["stamp"], "complete": supported and released}
    if "offset" in goal:
        error = math.dist(goal["offset"], actual)
        tolerance = goal.get("toleranceM")
        if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0 or not math.isfinite(error):
            raise ValueError("a precise world score requires a finite offset and positive toleranceM")
        result["offsetErrorM"] = error
        result["complete"] &= error <= tolerance
    return result


def evaluate(goal: dict, pose_of) -> dict:
    """Progress towards ``goal``.

    :param goal: the scenario's ``goal`` record.
    :param pose_of: World State root pose (``{p, q}``) of an entity id.
    :returns: ``{kind, satisfied, total, largestGroup, entities, complete, errors}``.
    """
    if goal.get("kind") != "relative-poses":
        raise ValueError(f"unknown goal kind {goal.get('kind')!r}")
    tol = goal.get("tolerance", {})
    pos_tol = float(tol.get("positionM", 0.03))
    yaw_tol = math.radians(float(tol.get("yawDeg", 15.0)))
    entities = list(goal["entities"])
    poses = {eid: pose_of(eid) for eid in entities}
    up = {eid: quat_rotate(p["q"], [0.0, 0.0, 1.0])[2] > math.cos(math.radians(20.0)) for eid, p in poses.items()}
    parent = {eid: eid for eid in entities}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    satisfied = 0
    errors = []
    for c in goal["constraints"]:
        a, b = poses[c["a"]], poses[c["b"]]
        rel = pose_compose(pose_inverse(a), b)
        dx, dy = c["offset"]
        pos_err = math.hypot(rel["p"][0] - dx, rel["p"][1] - dy)
        yaw_err = abs(wrap_angle(yaw_from_quat(rel["q"]) - math.radians(c.get("yawDeg", 0.0))))
        gated = goal.get("upright") and not (up[c["a"]] and up[c["b"]])
        ok = not gated and pos_err <= pos_tol and yaw_err <= yaw_tol
        errors.append({"a": c["a"], "b": c["b"], "satisfied": ok,
                       "positionErrM": round(pos_err, 4), "yawErrDeg": round(math.degrees(yaw_err), 2)})
        if not ok:
            continue
        satisfied += 1
        parent[find(c["a"])] = find(c["b"])
    groups: dict[str, int] = {}
    for eid in entities:
        root = find(eid)
        groups[root] = groups.get(root, 0) + 1
    largest = max(groups.values()) if groups else 0
    return {"kind": "relative-poses", "satisfied": satisfied, "total": len(goal["constraints"]),
            "largestGroup": largest, "entities": len(entities), "complete": largest == len(entities),
            "errors": errors}
