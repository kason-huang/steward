"""Conservative appearance priors fitted to measured surfaces; no scene-instance catalogue."""
from __future__ import annotations

import base64
from io import BytesIO

import numpy as np
from PIL import Image

from steward.math3d import matrix_from_quat, quat_from_axis_angle, pose_compose


def colour_histogram(rgb):
    """Compare chromatic hue; illumination-dependent gray pixels carry no class evidence."""
    pixels = np.asarray(rgb, dtype=np.uint8).reshape(-1, 1, 3)
    hsv = np.array(Image.fromarray(pixels).convert('HSV')).reshape(-1, 3)
    colourful = hsv[:, 1] > 55
    hue = np.bincount((hsv[colourful, 0].astype(int) * 12 // 256), minlength=12)
    result = hue.astype(float)
    if colourful.mean() < .05:
        return np.zeros(12)
    return result / max(1, result.sum())


class VisualMatcher:
    """Optional class appearances. A template never creates an unseen entity."""

    def __init__(self, objects):
        self.templates = {}
        for category, visuals in objects.items():
            if category in ('alphabet_soup', 'basket'):
                continue
            points, colours = [], []
            for visual in visuals:
                lines = base64.b64decode(visual['data']).decode().splitlines()
                vertices = np.array([[float(x) for x in line.split()[1:]] for line in lines if line.startswith('v ')])
                points.append(vertices @ matrix_from_quat(visual['pose']['q']).T + visual['pose']['p'])
                if visual.get('meshTexture'):
                    texture = np.array(Image.open(BytesIO(base64.b64decode(visual['meshTexture']))).convert('RGB'))
                    uv = np.array([[float(x) for x in line.split()[1:]] for line in lines if line.startswith('vt ')])
                    x = np.clip((uv[:, 0] * texture.shape[1]).astype(int), 0, texture.shape[1]-1)
                    y = np.clip(((1-uv[:, 1]) * texture.shape[0]).astype(int), 0, texture.shape[0]-1)
                    colours.append(texture[y, x])
            points = np.concatenate(points)
            # HOPE packages use several native axes. Tall packages stand upright;
            # the two flat packages rest on their broad face. This is a class prior.
            size = np.ptp(points, axis=0)
            axis = int(np.argmin(size) if category in ('butter', 'cream_cheese') else np.argmax(size))
            q = ([1, 0, 0, 0] if axis == 2 else
                 quat_from_axis_angle([0, -1, 0] if axis == 0 else [1, 0, 0], np.pi/2))
            transform = {'p': [0, 0, 0], 'q': q}
            points = points @ matrix_from_quat(q).T
            points -= (points.min(axis=0) + points.max(axis=0)) / 2
            self.templates[category] = {'points': points, 'size': np.ptp(points, axis=0),
                'histogram': colour_histogram(np.concatenate(colours)),
                'visuals': [dict(v, pose=pose_compose(transform, v['pose'])) for v in visuals]}

    def match(self, entity):
        """Return a fitted appearance only when both height and colour support it."""
        surface = entity['body']['links'][0]['geometry'].get('observed', [])
        if not surface:
            return None
        surface = surface[0]
        points = np.asarray(surface['triangles']).reshape(-1, 3)
        observed_size = np.ptp(points, axis=0)
        histogram = colour_histogram(surface['rgb'])
        ranked = []
        for category, template in self.templates.items():
            # Visible height loses the small strip next to the floor mask.
            ratio = (observed_size[2] + .007) / template['size'][2]
            height_error = abs(np.log(ratio))
            colour_error = 1 - np.sqrt(histogram * template['histogram']).sum()
            score = colour_error + .65 * height_error
            ranked.append((score, category, colour_error, height_error))
        ranked.sort()
        score, category, colour_error, height_error = ranked[0]
        if colour_error > .55 or height_error > .45 or ranked[1][0] - score < .08:
            return None
        template = self.templates[category]
        # In-plane principal axes orient elongated visible footprints. Symmetric
        # packages retain the class yaw prior rather than claiming measured yaw.
        def principal(xyz):
            values, vectors = np.linalg.eigh(np.cov(xyz[:, :2].T))
            return np.arctan2(vectors[1, -1], vectors[0, -1]), values[-1] / max(values[0], 1e-10)
        angle, ratio = principal(points)
        prior_angle, prior_ratio = principal(template['points'])
        yaw = angle - prior_angle if min(ratio, prior_ratio) > 1.5 else 0
        at = {'p': [0, 0, 0], 'q': quat_from_axis_angle([0, 0, 1], yaw)}
        # Recover the small unseen bottom strip from the known class height.
        at['p'][2] = float(points[:, 2].max() - template['size'][2] / 2)
        return {'category': category, 'score': float(score),
                'visuals': [dict(v, pose=pose_compose(at, v['pose'])) for v in template['visuals']]}
