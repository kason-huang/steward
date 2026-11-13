"""Post-release placement checks over robot observations; no world-state access or motion commands."""
from dataclasses import dataclass
import math


@dataclass
class VerificationConfig:
    """Placement tolerances in metres and bounded observation duration in seconds.

    Support clearance uses the smaller of the absolute cap and a fraction of observed height,
    so tall objects tolerate perception noise without applying that clearance to thin pieces.
    """
    timeout_s: float = 5.0
    max_age_s: float = 1.0
    min_existence: float = 0.9
    identity_separation_m: float = 0.01
    confidence_sigma: float = 2.0
    stable_s: float = 0.25
    stable_motion_m: float = 0.003
    position_tolerance_m: float = 0.015
    floor_height_tolerance_m: float = 0.003
    support_height_tolerance_m: float = 0.01
    support_height_fraction: float = 0.25

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'verification {name} must be finite and positive')
        if self.support_height_fraction >= 1:
            raise ValueError('verification support_height_fraction must be less than one')
        if self.min_existence > 1:
            raise ValueError("verification min_existence must be at most one")
        if self.stable_s >= self.timeout_s:
            raise ValueError('verification stable_s must be shorter than timeout_s')


class PlacementVerifier:
    """Compare distinct post-release observations with a frozen executable placement plan.

    ``observe`` receives an independently observed target, or None if identity/visibility is
    insufficient. Holding is fresh proprioception, or None when unavailable. Heading and face
    conditions remain unknown: geometric box axes cannot establish printed-face orientation.
    """

    def __init__(self, plan: dict, params: dict, released_at: float, config: VerificationConfig):
        self.plan, self.params, self.released_at, self.config = plan, params, released_at, config
        self.previous = None
        self.stable_since = None

    def observe(self, observed: dict | None, holding: bool | None) -> dict:
        """Return checks and measured errors; repeated frames never establish stability."""
        checks = {'released': 'unknown', 'position': 'unknown', 'support': 'unknown'}
        checks['released'] = 'unknown' if holding is None else ('failed' if holding else 'passed')
        result = {'status': 'unknown', 'checks': checks, 'object': self.plan['object'],
                  'releasedAt': self.released_at, 'reason': 'need fresh stable target and support observations; inspect before continuing dependent placements'}
        if self.params.get('yaw') is not None:
            checks['heading'] = 'unknown'
        if self.params.get('flip') or self.params.get('roll') is not None:
            checks['face'] = 'unknown'
        if observed is None or observed['stamp'] <= self.released_at:
            self.previous = self.stable_since = None
            result['reason'] = 'no fresh target observation; inspect before continuing dependent placements'
        else:
            stamp, position = observed['stamp'], observed['position']
            if self.previous is None or stamp < self.previous['stamp'] or math.dist(position, self.previous['position']) > self.config.stable_motion_m:
                self.stable_since = stamp
                result['reason'] = 'need distinct stable target observations; inspect before continuing dependent placements'
            elif stamp - self.stable_since >= self.config.stable_s:
                goal = self.plan.get('spot', self.plan.get('floor'))
                error = math.dist(position[:2], goal[:2])
                uncertainty = self.config.confidence_sigma * observed['sigmaM']
                tol = self.config.position_tolerance_m
                checks['position'] = ('passed' if error + uncertainty <= tol else
                                      'failed' if error - uncertainty > tol else 'unknown')
                if observed['supportFresh'] and observed['support'] is not None:
                    gap = observed.get('supportGapM')
                    support_sigma = self.config.confidence_sigma * observed.get('supportSigmaM', 0.0)
                    tolerance = min(self.config.support_height_tolerance_m,
                                    observed['heightM'] * self.config.support_height_fraction)
                    result.update(supportGapM=gap, supportUncertaintyM=observed.get('supportSigmaM', 0.0),
                                  supportToleranceM=tolerance)
                    if gap is not None and abs(gap) - support_sigma > tolerance:
                        checks['support'] = 'failed'
                    elif gap is None or abs(gap) + support_sigma <= tolerance:
                        checks['support'] = 'passed' if observed['support'] == self.plan.get('support', 'floor') else 'failed'
                result.update(observedAt=stamp, positionErrorM=round(error, 4), uncertaintyM=observed['sigmaM'],
                              observedSupport=observed['support'])
                if checks['position'] == 'unknown':
                    result['reason'] = 'position uncertainty overlaps the tolerance; inspect before continuing dependent placements'
                elif checks['support'] == 'unknown':
                    result['reason'] = ('support contact uncertainty exceeds the height tolerance; inspect before continuing dependent placements'
                                        if observed['supportFresh'] and observed['support'] is not None else
                                        'no fresh support observation; inspect before continuing dependent placements')
            if self.previous is None or stamp >= self.previous['stamp']:
                self.previous = observed
        if 'failed' in checks.values():
            result.update(status='failed', reason='observed placement does not meet the requested conditions; re-plan before retrying')
        elif all(value == 'passed' for value in checks.values()):
            result.update(status='passed', reason='fresh stable observations confirm the requested placement')
        elif checks['position'] == checks['support'] == 'passed' and ('heading' in checks or 'face' in checks):
            result['reason'] = 'position and support confirmed; inspect appearance to verify heading or face before continuing'
        return result
