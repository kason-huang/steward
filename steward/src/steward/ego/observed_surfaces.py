"""Draw measured coloured triangles through the common Ego renderer, without simulation."""
from __future__ import annotations

import mujoco
import numpy as np

from steward.math3d import matrix_from_quat, pose_compose


def append_surfaces(scene, ego):
    """Append ``geometry.observed`` surfaces in entity/link coordinates to an MjvScene.

    RGB values are bytes per face. The MJCF compiler ignores these non-colliding,
    incomplete observations; rendering them never changes simulated geometry.
    """
    for entity in ego.entities:
        for link in entity.rigid.links:
            for surface in link.geometry.get('observed', []):
                if surface.get('kind') != 'surface':
                    continue
                at = pose_compose(entity.pose.value, surface.get('pose') or
                                  {'p': [0, 0, 0], 'q': [1, 0, 0, 0]})
                vertices = np.asarray(surface['triangles']) @ matrix_from_quat(at['q']).T + at['p']
                for triangle, rgb in zip(vertices, surface['rgb'], strict=True):
                    edge1, edge2 = triangle[1:] - triangle[0]
                    normal = np.cross(edge1, edge2)
                    length = np.linalg.norm(normal)
                    if length < 1e-10:
                        continue
                    if scene.ngeom >= scene.maxgeom:
                        raise ValueError('Observed surfaces exceed Ego view geometry capacity')
                    geom = scene.geoms[scene.ngeom]
                    # MuJoCo's triangle has vertices (0,0), (1,0), (0,1).
                    transform = np.column_stack((edge1, edge2, normal / length))
                    mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_TRIANGLE, np.ones(3),
                                       triangle[0], transform.ravel(), np.r_[np.asarray(rgb) / 255, 1])
                    geom.category = mujoco.mjtCatBit.mjCAT_DECOR
                    geom.emission = .35
                    scene.ngeom += 1
