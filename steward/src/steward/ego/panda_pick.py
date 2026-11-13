"""Target-bound Panda pick tasks and RGB-D lift evidence, independent of the policy model."""
from __future__ import annotations

from collections import deque
import copy

import numpy as np
from PIL import Image

from steward.ego.panda_observation import rig_pose
from steward.ego.panda_perception import cloud, groups, external_objects
from steward.ego.visual_matching import colour_histogram
from steward.math3d import matrix_from_quat

PICK = 'pick'


def pick_prompt(target):
    category = target['category']
    name = category.replace('_', ' ') if category != 'unknown' else 'selected object'
    return 'pick up the ' + name


def pickable(target):
    """Admit a fresh small object; support surfaces and stale references are not pick targets."""
    size = np.asarray(target.get('sizeM', []), dtype=float)
    return bool(target.get('id', '').startswith('e:') and target.get('category') != 'basket'
                and target.get('role') not in ('robot', 'environment')
                and target.get('lastSeenAgoS', float('inf')) <= .5
                and size.shape == (3,) and np.isfinite(size).all() and (size > .003).all()
                and min(size[:2]) <= .08 and max(size) <= .22)


def basket_support(support):
    """Require a fresh observed basket with finite pose and dimensions."""
    position = np.asarray(support.get('position', []), dtype=float)
    size = np.asarray(support.get('sizeM', []), dtype=float)
    return bool(support.get('id', '').startswith('e:') and support.get('category') == 'basket'
                and support.get('lastSeenAgoS', float('inf')) <= .5
                and position.shape == size.shape == (3,) and np.isfinite(position).all()
                and np.isfinite(size).all() and (size > .003).all())


class PickEvidence:
    """Track one selected identity; encoder motion alone never confirms a grasp."""

    def __init__(self, target, observation, support=None):
        self.target = copy.deepcopy(target)
        self.support = copy.deepcopy(support)
        self.placed = deque(maxlen=12)
        self.released = False
        self.episode_id = observation['episodeId']
        self.size = np.asarray(target['sizeM'])
        self.position = np.asarray(target['position'], dtype=float)
        self.initial_top = self.position[2] + self.size[2] / 2
        self.external_pose = copy.deepcopy(observation['cameras']['external']['pose'])
        self.previous_depth = None
        self.initial_depth = observation['depth']['external'].copy()
        self.new_occupancy = False
        self.palette = None
        self.offset = np.zeros(3)
        first = self.measure(observation, self.position)
        if first is not None:
            self.offset = first[0] - self.position
        self.previous_depth = observation['depth']['external'].copy()
        self.rows = deque(maxlen=12)
        self.last_key = None
        self.grasp = 'unconfirmed'
        self.ever_confirmed = False
        self.result = self.judgment(observation, 'awaiting_observed_lift', 0, 0)

    def measure(self, observation, expected):
        self.new_occupancy = False
        at = observation['cameras']['external']['pose']
        if (np.linalg.norm(np.asarray(at['p']) - self.external_pose['p']) > 1e-6
                or abs(np.dot(at['q'], self.external_pose['q'])) < 1 - 1e-6):
            return None
        boxes = []
        for geometry in observation['selfVisual']:
            at = rig_pose(geometry, observation['encoders'])
            center, half = np.asarray(geometry['bounds']).reshape(2, 3)
            boxes.append((np.asarray(at['p']), matrix_from_quat(at['q']), center, half))
        # The wrist can sweep across a stationary surface as the tool moves.
        # Only the calibrated fixed external view supplies independent lift evidence.
        for role in ('external',):
            depth = observation['depth'][role]
            xyz = cloud(depth, observation['cameras'][role])
            valid = ((depth > 0) & np.isfinite(xyz).all(axis=-1) & (xyz[..., 2] > .004)
                     & (xyz[..., 2] < .8) & (np.abs(xyz[..., :2]) < .5).all(axis=-1))
            points, rgb = xyz[valid], observation['images'][role][valid]
            pixels = np.argwhere(valid)
            if not len(points):
                continue
            outside = np.ones(len(points), bool)
            for at, rotation, center, half in boxes:
                outside &= ~(np.abs((points - at) @ rotation - center) < half + .0015).all(axis=-1)
            points, rgb, pixels = points[outside], rgb[outside], pixels[outside]
            if len(points) < 12:
                continue
            hsv = np.asarray(Image.fromarray(rgb.reshape(-1, 1, 3)).convert('HSV')).reshape(-1, 3)
            hue = hsv[:, 0].astype(int) * 12 // 256
            if self.palette is None:
                seed = (np.abs(points - expected) < np.maximum(self.size / 2 + .025, .04)).all(axis=-1)
                if seed.sum() < 12:
                    continue
                histogram = colour_histogram(rgb[seed])
                if histogram.sum() == 0:
                    continue
                palette = histogram > .04
            else:
                palette = self.palette
            selected = (hsv[:, 1] > 55) & palette[hue]
            if selected.sum() >= 12:
                candidates = []
                # Measure complete components, never a tool-following crop. Otherwise
                # sliding the crop over a static coloured surface invents object motion.
                members = np.column_stack((points[selected], pixels[selected]))
                for component in groups(members, pitch=.012):
                    visible, pixel = component[:, :3], component[:, 3:]
                    if (pixel.min(axis=0) <= 1).any() or (pixel.max(axis=0) >= np.asarray(depth.shape) - 2).any():
                        continue
                    if len(visible) < 12 or np.any(np.sort(np.ptp(visible, axis=0)) > np.sort(self.size + .03)):
                        continue
                    center = np.median(visible, axis=0)
                    distance = np.linalg.norm(center - expected - self.offset)
                    limit = np.linalg.norm(self.size / 2) + .035 if self.palette is None else .06
                    if distance <= limit:
                        changed = False
                        if self.previous_depth is not None:
                            y, x = pixel.astype(int).T
                            # A moving object occupies space that was farther away.
                            # Revealing a static surface from behind the hand does not.
                            changed = np.mean(self.previous_depth[y, x] - depth[y, x] > .002) >= .1
                        candidates.append((distance, center, visible, bool(changed), pixel))
                if candidates:
                    _, center, visible, changed, selected_pixels = min(candidates, key=lambda c: c[0])
                    self.palette = palette
                    y, x = selected_pixels.astype(int).T
                    self.new_occupancy = np.mean(self.initial_depth[y, x] - depth[y, x] > .003) >= .5
                    return center, float(np.quantile(visible[:, 2], .95)), len(visible), changed
        return None

    def judgment(self, observation, reason, lift, points):
        return {'grasp': self.grasp, 'placement': 'unconfirmed', 'reason': reason,
                'target': self.target['id'], 'basket': self.support['id'] if self.support else None, 'targetCategory': self.target['category'],
                'evidenceStep': observation['step'], 'liftAboveInitialTopM': float(lift),
                'observedTargetPoints': int(points), 'evidenceCamera': 'external',
                'independentMotionFrames': sum(int(row[3]) for row in self.rows)}

    def update(self, observation, width):
        if observation['episodeId'] != self.episode_id:
            raise ValueError('Pick target belongs to another episode')
        key = observation['episodeId'], observation['step']
        if key == self.last_key:
            return copy.deepcopy(self.result)
        self.last_key = key
        tool = np.asarray(observation['tool']['p'])
        closed = .005 < width < .071
        # Identity association stays anchored to the last real observation. Hand
        # prediction must never walk the selected id across other coloured objects.
        measured = self.measure(observation, self.position)
        self.previous_depth = observation['depth']['external'].copy()
        reason, lift, count = 'missing_target_visual_evidence', 0., 0
        if measured is None:
            self.rows.clear()
            self.grasp = 'unconfirmed'
        else:
            point, top, count, changed = measured
            self.position = point - self.offset
            lift = top - self.initial_top
            near = np.linalg.norm(self.position - tool) < .13
            if closed and near:
                was_confirmed = self.grasp == 'confirmed'
                self.grasp = 'unconfirmed'
                if self.rows and observation['stamp'] - self.rows[-1][0] > .3:
                    self.rows.clear()
                self.rows.append((observation['stamp'], self.position.copy(), tool.copy(), changed))
                if len(self.rows) >= 3 and self.rows[-1][0] - self.rows[0][0] >= .2:
                    objects = np.array([r[1] for r in self.rows])
                    tools = np.array([r[2] for r in self.rows])
                    relative = np.linalg.norm(np.ptp(objects - tools, axis=0)) < .03
                    moving = (np.linalg.norm(objects[-1] - objects[0]) >= .015
                              and np.linalg.norm(tools[-1] - tools[0]) >= .015
                              and sum(int(r[3]) for r in self.rows) >= 2
                              and np.linalg.norm((objects[-1] - objects[0]) - (tools[-1] - tools[0])) < .03)
                    if lift >= .035 and relative and (moving or was_confirmed):
                        self.grasp = 'confirmed'
                        self.ever_confirmed = True
                reason = 'observed_target_lift_and_tool_coupling' if self.grasp == 'confirmed' else 'awaiting_observed_lift'
                if lift < .025:
                    self.grasp = 'unconfirmed'
                    reason = 'target_not_lifted'
            else:
                self.rows.clear()
                self.grasp = 'failed' if self.ever_confirmed else 'unconfirmed'
                reason = 'target_released_or_detached' if self.ever_confirmed else 'gripper_not_holding_target'
        self.result = self.judgment(observation, reason, lift, count)
        if self.support is not None:
            self.update_placement(observation, width, measured)
        return copy.deepcopy(self.result)

    def update_placement(self, observation, width, measured):
        # A goal requires the selected identity after a real grasp, release and
        # tool withdrawal. Occluded targets stay unconfirmed; basket colour or
        # an encoder-only release never establishes placement.
        if self.ever_confirmed and width >= .075:
            self.released = True
        if not self.released or width < .075 or measured is None or not self.new_occupancy:
            self.placed.clear()
            return
        camera = observation['cameras']['external']
        _, basket = external_objects(observation['images']['external'],
                                     cloud(observation['depth']['external'], camera), None)
        expected = np.asarray(self.support['position'])
        if (basket is None or np.linalg.norm(basket[0] - expected[:2]) > .025
                or abs(basket[1] - expected[2] - self.support['sizeM'][2] / 2) > .025):
            self.placed.clear()
            return
        tool = np.asarray(observation['tool']['p'])
        if np.linalg.norm(tool - self.position) < np.linalg.norm(self.size / 2) + .05:
            self.placed.clear()
            return
        if self.placed and observation['stamp'] - self.placed[-1][0] > .3:
            self.placed.clear()
        self.placed.append((observation['stamp'], self.position.copy()))
        if len(self.placed) < 3 or self.placed[-1][0] - self.placed[0][0] < .3:
            return
        if np.linalg.norm(np.ptp([r[1] for r in self.placed], axis=0)) > .01:
            return
        half = np.asarray(self.support['sizeM']) / 2
        center = np.array([*basket[0], basket[1] - half[2]])
        inside = (np.abs(self.position[:2] - center[:2]) + self.size[:2] / 2 < half[:2] - .012).all()
        bottom = self.position[2] - self.size[2] / 2
        supported = abs(bottom - (center[2] - half[2])) <= .025
        below_rim = self.position[2] < center[2] + half[2]
        if inside and supported and below_rim:
            self.result.update(grasp='confirmed', placement='confirmed',
                               reason='selected_target_stable_in_basket_after_release_and_withdrawal')
        elif not inside:
            self.result.update(placement='failed', reason='selected_target_stable_outside_basket')
