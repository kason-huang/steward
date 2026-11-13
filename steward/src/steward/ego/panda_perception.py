"""Task-specific RGB-D evidence for the LIBERO soup can and basket."""

from __future__ import annotations

from collections import deque

import numpy as np

from steward.math3d import matrix_from_quat

# These priors bound the tabletop and the appearance of this task's objects.
# No reset pose or simulator object identifier is used by the observer.
VISIBLE_RANGE = 0.45
EVIDENCE_WINDOW = 0.20


def cloud(depth, camera):
    y, x = np.indices(depth.shape)
    k = np.asarray(camera["K"])
    points = np.stack(((x - k[0, 2]) * depth / k[0, 0],
                       -(y - k[1, 2]) * depth / k[1, 1], -depth), axis=-1)
    return points @ matrix_from_quat(camera["pose"]["q"]).T + camera["pose"]["p"]


def groups(points, pitch=0.015):
    """Connected bins in the table plane, with each point assigned to its original bin."""
    if not len(points):
        return []
    cells = np.floor(points[:, :2] / pitch).astype(int)
    members = {}
    for index, cell in enumerate(map(tuple, cells)):
        members.setdefault(cell, []).append(index)
    clusters = []
    while members:
        cell, indices = members.popitem()
        pending = [cell]
        while pending:
            x, y = pending.pop()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                neighbor = x + dx, y + dy
                found = members.pop(neighbor, None)
                if found is not None:
                    pending.append(neighbor)
                    indices.extend(found)
        clusters.append(points[indices])
    return clusters


def external_objects(rgb, xyz, previous_can):
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    x, y, z = xyz.transpose(2, 0, 1)
    valid = (np.abs(x) < VISIBLE_RANGE) & (np.abs(y) < VISIBLE_RANGE)
    blue = valid & (b > r + 16) & (b > g + 8) & (z > .022) & (z < .34)
    cans = []
    for points in groups(xyz[blue], pitch=.012):
        if len(points) >= 35 and np.ptp(points[:, 2]) >= .018 and np.ptp(points[:, 1]) >= .025:
            position = np.median(points, axis=0)
            position[0] -= .022  # Front camera sees the near face of the cylindrical label.
            if previous_can is None or np.linalg.norm(position - previous_can) < .20:
                cans.append((len(points), position))
    can = max(cans, key=lambda entry: entry[0])[1] if cans else None

    white = valid & (r > 140) & (np.abs(r - b) < 32) & (z > .115) & (z < .145)
    rims = []
    for points in groups(xyz[white], pitch=.024):
        if len(points) < 200:
            continue
        lower, upper = np.quantile(points, [.01, .99], axis=0)
        if .10 < upper[0] - lower[0] < .20 and .12 < upper[1] - lower[1] < .20:
            rims.append((len(points), (lower[:2] + upper[:2]) / 2, float(np.median(points[:, 2]))))
    basket = max(rims, key=lambda entry: entry[0])[1:] if rims else None
    return can, basket


def wrist_can_top(rgb, xyz, center, rim_height):
    x, y, z = xyz.transpose(2, 0, 1)
    light = rgb.max(axis=-1)
    gray = light.astype(np.int16) - rgb.min(axis=-1) < 27
    region = (np.abs(x - center[0]) < .072) & (np.abs(y - center[1]) < .072)
    dark = region & gray & (light < 115) & (z > .045) & (z < rim_height - .017)
    candidates = []
    points = xyz[dark]
    # Slice by height first, so vertical basket walls do not connect separate surfaces.
    for height in np.arange(.049, rim_height - .016, .005):
        for top in groups(points[np.abs(points[:, 2] - height) < .003], pitch=.005):
            if len(top) < 90:
                continue
            width = np.ptp(top[:, :2], axis=0)
            if min(width) >= .020 and max(width) <= .083:
                candidates.append((len(top), np.median(top, axis=0)))
    return max(candidates, key=lambda entry: entry[0])[1] if candidates else None


class PandaEvidence:
    def __init__(self):
        self.last_can = None
        self.basket = None
        self.basket_seen_stamp = None
        self.start_can = None
        self.min_width = .08
        self.last_held = None
        self.release = None
        self.grasp = "unconfirmed"
        self.held = deque(maxlen=12)
        self.failed = deque(maxlen=12)
        self.placed = deque(maxlen=12)
        self.last_stamp = None

    @staticmethod
    def stable(rows):
        if len(rows) < 3 or rows[-1][0] - rows[0][0] < EVIDENCE_WINDOW:
            return False
        points = np.array([row[1] for row in rows])
        return bool(np.linalg.norm(np.ptp(points, axis=0)) <= .025)

    def update(self, observation, width):
        stamp = observation["stamp"]
        if self.last_stamp is not None and stamp - self.last_stamp > .6:
            self.held.clear()
            self.failed.clear()
            self.placed.clear()
        self.last_stamp = stamp
        tool = np.array(observation["tool"]["p"])
        images, depth, cameras = (observation[k] for k in ("images", "depth", "cameras"))
        if not all(np.any(images[role]) and np.any(depth[role]) for role in ("external", "wrist")):
            self.held.clear()
            self.failed.clear()
            self.placed.clear()
            return None, self.basket, "unconfirmed", "unconfirmed", "missing_visual_evidence"
        external = cloud(depth["external"], cameras["external"])
        can, basket = external_objects(images["external"], external, self.last_can)
        if basket is not None and (self.basket is None or np.linalg.norm(basket[0] - self.basket[0]) < .05):
            self.basket = basket
            self.basket_seen_stamp = stamp
        self.min_width = min(self.min_width, width)
        if can is not None:
            self.last_can = can
            if self.start_can is None:
                self.start_can = can
        closed = width < .071
        opened = width >= .075 and self.min_width < .071
        near = can is not None and np.linalg.norm(can - tool) < .09
        if closed and near and can[2] > self.start_can[2] + .075:
            self.held.append((stamp, can - tool))
            self.last_held = can
            if self.stable(self.held):
                self.grasp = "confirmed"
        else:
            self.held.clear()
        if (self.grasp != "confirmed" and self.min_width < .071 and can is not None
                and tool[2] > can[2] + .09 and can[2] < .09):
            self.failed.append((stamp, can))
            if self.stable(self.failed):
                self.grasp = "failed"
        else:
            self.failed.clear()
        if opened and self.grasp == "confirmed" and self.release is None:
            self.release = self.last_held
        placement = "unconfirmed"
        reason = "awaiting_visible_post_release_evidence"
        if opened and self.release is not None and self.basket is not None:
            center, rim = self.basket
            near_basket = np.linalg.norm(self.release[:2] - center) < .13
            target = center if near_basket else self.release[:2]
            wrist = cloud(depth["wrist"], cameras["wrist"])
            top = wrist_can_top(images["wrist"], wrist, target, rim)
            # The task's cylinder height gives a centre estimate from its visible lid.
            # Withdrawal depends on clearance from that estimate, including lower retreats.
            center_estimate = top - [0, 0, .0375] if top is not None else None
            if (top is not None and np.linalg.norm(top[:2] - self.release[:2]) < .13
                    and np.linalg.norm(tool - center_estimate) >= .08):
                inside = bool(np.all(np.abs(top[:2] - center) < .058))
                self.placed.append((stamp, top))
                if self.stable(self.placed):
                    placement = "confirmed" if inside else "failed"
                    reason = "stable_visible_can_below_rim" if inside else "stable_visible_can_outside_basket"
                    can = top
            else:
                self.placed.clear()
        elif opened and self.grasp == "failed" and can is not None and self.basket is not None:
            if np.linalg.norm(can[:2] - self.basket[0]) > .13:
                placement, reason = "failed", "can_remained_outside_basket"
        return can, self.basket, self.grasp, placement, reason
