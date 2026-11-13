"""Placement checks require independent post-release observations, not commanded positions."""
import pytest
from steward.ego.verification import PlacementVerifier, VerificationConfig


def view(t=2.0, p=(0.5, 0.0), on='e:table', sigma=0.001):
    return {'stamp': t, 'position': list(p), 'sigmaM': sigma, 'support': on,
            'supportFresh': True, 'settled': True}


def verifier(**kw):
    return PlacementVerifier({'object': 'e:piece', 'spot': [0.5, 0], 'support': 'e:table'},
                             {}, 1.0, VerificationConfig(**kw))


def test_commanded_position_and_old_observations_cannot_confirm_placement():
    v = verifier()
    assert v.observe(view(t=1), False)['status'] == 'unknown'
    assert v.observe(None, False)['status'] == 'unknown'


def test_two_distinct_stable_observations_confirm_position_and_support():
    v = verifier()
    assert v.observe(view(), False)['status'] == 'unknown'
    assert v.observe(view(), False)['status'] == 'unknown'
    assert v.observe(view(t=2.5), False)['status'] == 'passed'


@pytest.mark.parametrize('p,on,check', [((0.55, 0), 'e:table', 'position'),
                                      ((0.5, 0), 'e:other-piece', 'support')])
def test_stable_misplacement_reports_the_failed_check(p, on, check):
    v = verifier()
    v.observe(view(p=p, on=on), False)
    r = v.observe(view(t=2.5, p=p, on=on), False)
    assert r['status'] == 'failed'
    assert r['checks'][check] == 'failed'


def test_uncertainty_overlapping_position_tolerance_remains_unknown():
    v = verifier()
    v.observe(view(p=(0.514, 0), sigma=.004), False)
    assert v.observe(view(t=2.5, p=(0.514, 0), sigma=.004), False)['status'] == 'unknown'


def test_fresh_gripper_evidence_of_holding_is_a_failure():
    assert verifier().observe(None, True)['checks']['released'] == 'failed'


def test_heading_and_flip_are_not_verified_from_geometric_prior():
    v = PlacementVerifier({'object': 'e:piece', 'spot': [.5, 0], 'support': 'e:table'},
                          {'yaw': 0, 'flip': True}, 1, VerificationConfig())
    v.observe(view(), False)
    r = v.observe(view(t=2.5), False)
    assert r['status'] == 'unknown'
    assert r['checks']['heading'] == 'unknown'
    assert r['checks']['face'] == 'unknown'


def test_moving_or_missing_target_resets_stability_evidence():
    v = verifier()
    v.observe(view(), False)
    v.observe(None, False)
    assert v.observe(view(t=2.5), False)['status'] == 'unknown'
    assert v.observe(view(t=3), False)['status'] == 'passed'


def test_support_relation_with_a_large_vertical_gap_does_not_confirm_contact():
    v = verifier()
    a = {**view(), 'supportGapM': .06, 'supportSigmaM': .001}
    v.observe(a, False)
    result = v.observe({**a, 'stamp': 2.5}, False)
    assert result['status'] == 'failed'
    assert result['checks']['support'] == 'failed'


def test_ambiguous_contact_height_is_unknown_rather_than_confirmation():
    v = verifier()
    a = {**view(), 'supportGapM': .002, 'supportSigmaM': .002}
    v.observe(a, False)
    assert v.observe({**a, 'stamp': 2.5}, False)['checks']['support'] == 'unknown'


@pytest.mark.parametrize('field,value', [('timeout_s', 0), ('stable_s', -1), ('position_tolerance_m', float('nan'))])
def test_invalid_verification_configuration_fails_at_construction(field, value):
    with pytest.raises(ValueError):
        VerificationConfig(**{field: value})
