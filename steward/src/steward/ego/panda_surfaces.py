"""Unlabelled visible surfaces from Panda RGB-D, independent of task recognition."""
from __future__ import annotations

import numpy as np

from steward.ego.panda_observation import rig_pose
from steward.ego.panda_perception import cloud, groups, VISIBLE_RANGE
from steward.math3d import matrix_from_quat
from steward.scene.schema import Entity, Est, Link, RigidTree, exact, pose


def surface_entities(observation, prefix, known, previous=()):
    """Track external-camera clusters without assigning unsupported semantic labels.

    Floor, calibrated self geometry, known task regions and depth discontinuities are
    excluded. Only measured faces are kept; hidden surfaces are never completed.
    """
    depth = observation['depth']['external']
    rgb = observation['images']['external']
    xyz = cloud(depth, observation['cameras']['external'])
    valid = ((depth > 0) & np.isfinite(xyz).all(axis=-1)
             & (np.abs(xyz[..., :2]) < VISIBLE_RANGE).all(axis=-1)
             & (xyz[..., 2] > .007) & (xyz[..., 2] < .40))
    for geometry in observation['selfVisual']:
        at = rig_pose(geometry, observation['encoders'])
        center, half = np.asarray(geometry['bounds']).reshape(2, 3)
        local = (xyz - at['p']) @ matrix_from_quat(at['q'])
        valid &= ~((np.abs(local - center) < half + .008).all(axis=-1))
    for entity in known:
        at = entity['pose']['value']
        half = np.asarray(entity['stateVars']['size']['value']) / 2
        local = (xyz - at['p']) @ matrix_from_quat(at['q'])
        valid &= ~((np.abs(local) < half + .015).all(axis=-1))

    flat_ids = np.flatnonzero(valid)
    rows = np.column_stack((xyz.reshape(-1, 3)[flat_ids], flat_ids))
    labels = np.zeros(depth.size, dtype=np.int32)
    clusters = []
    for points in groups(rows, pitch=.012):
        if len(points) < 12:
            continue
        clusters.append(points)
        labels[points[:, 3].astype(int)] = len(clusters)
    # Each triangle joins adjacent valid pixels of the same cluster. A depth edge
    # cannot become a stretched sheet connecting two objects or an object to floor.
    grid = np.arange(depth.size).reshape(depth.shape)
    a, b, c, d = (grid[:-1, :-1].ravel(), grid[1:, :-1].ravel(),
                   grid[:-1, 1:].ravel(), grid[1:, 1:].ravel())
    faces = np.concatenate((np.stack((a, b, c), axis=1), np.stack((b, d, c), axis=1)))
    vertices = xyz.reshape(-1, 3)[faces]
    keep = ((labels[faces[:, 0]] > 0) & (labels[faces] == labels[faces[:, :1]]).all(axis=1)
            & (np.linalg.norm(vertices - np.roll(vertices, 1, axis=1), axis=-1).max(axis=1) < .025))
    faces, vertices = faces[keep], vertices[keep]
    colours = np.rint(rgb.reshape(-1, 3)[faces].mean(axis=1)).astype(np.uint8)
    result, used = [], set()
    counter = max([int(e.id.rsplit('-', 1)[-1]) for e in previous] + [0])
    for index, points in enumerate(clusters, 1):
        selected = labels[faces[:, 0]] == index
        if np.count_nonzero(selected) < 2:
            continue
        low, high = points[:, :3].min(axis=0), points[:, :3].max(axis=0)
        center = (low + high) / 2
        candidates = [(np.linalg.norm(center - e.pose.value['p']), e.id)
                      for e in previous if e.id not in used]
        distance, identifier = min(candidates, default=(float('inf'), ''))
        if distance >= .08:
            counter += 1
            identifier = prefix + 'seen-' + str(counter)
        used.add(identifier)
        surface = {'kind': 'surface', 'triangles': (vertices[selected] - center).round(6).tolist(),
                   'rgb': colours[selected].tolist()}
        result.append(Entity(identifier, 'object', Est('unknown', src='observed'),
                             Est(pose(center.tolist()), src='observed'),
                             RigidTree(exact('fixed'), [Link('surface', 'visible surface', {'observed': [surface]})]),
                             state_vars={'size': exact(np.maximum(high - low, .004).tolist()),
                                         'camera': exact('external'),
                                         'observedAt': exact(observation.get('stamp', 0))}))
    return result
