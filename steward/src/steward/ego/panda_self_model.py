"""Panda self geometry and articulation expressed in the shared Ego SceneState model."""
import json
import numpy as np
from steward.math3d import pose_compose
from steward.scene.schema import Entity, Est, Joint, Link, RigidTree, exact, pose, primitive


def self_entity(observation, identifier, visuals=None):
    """Build robot-owned links from calibrated geometry; joint state comes only from encoders."""
    geometries = observation['selfVisual']
    if not geometries:raise ValueError('Missing Panda self calibration')
    root = geometries[0]['chain'][0]
    if 'joint' in root:
        raise ValueError('Panda base must be fixed')
    links = [Link('base', 'Panda base')]
    joints = []
    paths = {json.dumps([root], sort_keys=True): 'base'}
    for geometry in geometries:
        chain = geometry['chain']
        if chain[0] != root:
            raise ValueError('Panda self geometry has inconsistent base calibration')
        parent = 'base'
        for index, node in enumerate(chain[1:], 1):
            key = json.dumps(chain[:index+1], sort_keys=True)
            if key not in paths:
                child = 'link-'+str(len(links))
                paths[key] = child
                links.append(Link(child, node.get('joint', child)))
                encoder = observation['encoders'].get(node.get('joint'))
                joints.append(Joint(node.get('joint', 'fixed-'+child), parent, child,
                    exact(node.get('type', 'fixed')), exact(node['origin']),
                    Est([encoder['position']] if encoder else [], src='observed'),
                    Est([encoder['velocity']] if encoder else [], src='observed'),
                    axis=exact(node['axis']) if encoder else None))
            parent = paths[key]
        center, half = np.asarray(geometry['bounds']).reshape(2, 3)
        if np.any(half <= 0):continue
        at = pose_compose(geometry['mount'], pose(center))
        link = next(link for link in links if link.id == parent)
        if visuals and json.dumps(chain, sort_keys=True) in visuals:
            link.geometry['visual'] = visuals[json.dumps(chain, sort_keys=True)]
        link.geometry.setdefault('collision', []).append(primitive('box', (2*half).tolist(), at,
            rgba=[.80,.82,.85,1]))
    return Entity(identifier, 'robot', exact('libero-panda'), Est(root['origin'],src='prior'),
                  RigidTree(exact('fixed'),links,joints))
