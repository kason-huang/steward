"""Asset round-trip keeps the soup identity and canonical upright cylinder."""
import base64
from pathlib import Path

import numpy as np
import pytest

from steward.ego.panda_visual_model import PandaVisualModel
from steward.math3d import matrix_from_quat

BUNDLE = Path('var/step4/wsim-root/libero-step3')


@pytest.fixture(scope='module')
def prior():
    if not BUNDLE.exists():
        pytest.skip('requires the pinned LIBERO visual asset bundle')
    prior = PandaVisualModel(BUNDLE)
    prior.load()
    return prior


def test_soup_template_is_upright_and_uses_soup_texture(prior):
    points = []
    for visual in prior.objects['alphabet_soup']:
        obj = base64.b64decode(visual['data']).decode()
        verts = np.array([[float(x) for x in line.split()[1:]] for line in obj.splitlines()
                          if line.startswith('v ')])
        points.append(verts @ matrix_from_quat(visual['pose']['q']).T + visual['pose']['p'])
    size = np.ptp(np.concatenate(points), axis=0)
    assert size[2] > .08, f'soup cylinder is lying sideways: {size}'
    assert max(size[:2]) < .076
    assert {'alphabet_soup', 'basket', 'butter', 'milk'} <= set(prior.objects)
    from io import BytesIO
    from PIL import Image
    original = np.array(Image.open(BUNDLE / 'assets/libero/stable_hope_objects/alphabet_soup/texture_map.png').convert('RGB'))
    rendered = np.array(Image.open(BytesIO(base64.b64decode(prior.objects['alphabet_soup'][0]['meshTexture']))))
    np.testing.assert_array_equal(original, rendered)


def test_real_visible_packages_match_without_world_names_or_positions(prior):
    import json
    samples = json.loads((Path(__file__).parent / 'fixtures/panda-visible-surfaces.json').read_text())
    assert len(samples) == 5
    for sample in samples:
        entity = sample['entity']
        assert entity['category']['value'] == 'unknown'
        match = prior.matcher.match(entity)
        assert match is not None
        assert match['category'] == sample['expectedAppearance']
        assert entity['category']['value'] == 'unknown'
        # A shape prior completes downwards from the measured top, not above it.
        surface = entity['body']['links'][0]['geometry']['observed'][0]
        top = np.asarray(surface['triangles'])[..., 2].max()
        points = []
        for visual in match['visuals']:
            verts = np.array([[float(x) for x in line.split()[1:]]
                              for line in base64.b64decode(visual['data']).decode().splitlines()
                              if line.startswith('v ')])
            points.append(verts @ matrix_from_quat(visual['pose']['q']).T + visual['pose']['p'])
        assert abs(np.concatenate(points)[:, 2].max() - top) < .008


def test_uninformative_colour_or_incompatible_height_keeps_measured_surface(prior):
    import json, copy
    samples = json.loads((Path(__file__).parent / 'fixtures/panda-visible-surfaces.json').read_text())
    entity = copy.deepcopy(samples[1]['entity'])
    surface = entity['body']['links'][0]['geometry']['observed'][0]
    surface['rgb'] = [[180, 180, 180]] * len(surface['rgb'])
    assert prior.matcher.match(entity) is None
    surface['rgb'] = samples[1]['entity']['body']['links'][0]['geometry']['observed'][0]['rgb']
    surface['triangles'] = (np.asarray(surface['triangles']) * 10).tolist()
    assert prior.matcher.match(entity) is None
