"""Capture or replay a Panda sensor packet and render its robot-owned Ego view.

No World State API, episode reset, robot command, or policy inference is performed.
Run from the steward Python package directory with its virtual environment.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import time

from steward.ego.panda_runtime import PandaEgoRuntime
from steward.ego.runtime import RobotLink
from steward.ego.view import EgoViewRenderer
from steward.viewloop import ViewSettings


class SnapshotLink:
    """Replay one synchronized sensor packet without writing telemetry to the world."""

    def __init__(self, packet):
        self.packet = packet

    def panda_observation(self, after=None):
        return self.packet

    def telemetry(self, scene):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-url', default='http://127.0.0.1:7410')
    parser.add_argument('--bundle', type=Path, default=Path('var/step4/wsim-root/libero-step3'))
    parser.add_argument('--packet', type=Path, help='Replay this saved packet instead of reading robot sensors')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    packet = (json.loads(args.packet.read_text()) if args.packet else
              RobotLink(args.robot_url).panda_observation())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'packet.json').write_text(json.dumps(packet) + '\n')
    runtime = PandaEgoRuntime(SnapshotLink(packet), visual_bundle=args.bundle)
    started = time.monotonic()
    runtime.observe(force=True)
    runtime.observe_surfaces()
    observe_s = time.monotonic() - started
    renderer = EgoViewRenderer()
    settings = ViewSettings.parse({'camera': {'lookat': [-.15, 0, .15], 'distance': 1.65,
                                             'azimuth': 90, 'elevation': -50},
                                   'fps': 3, 'width': 1024, 'height': 768})
    try:
        frame, _, _ = runtime._render(renderer, settings)
        (args.out / 'ego-replay.jpg').write_bytes(frame)
    finally:
        renderer.close()
    for role, data in packet['images'].items():
        (args.out / (role + '.png')).write_bytes(base64.b64decode(data))
    report = {'episodeId': packet['episodeId'], 'step': packet['step'], 'observeS': observe_s,
              'summary': runtime.summary(), 'appearancePriors': {
                  key: {'category': match['category'], 'score': match['score']}
                  for key, match in runtime.appearance_cache.items()}}
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'out': str(args.out.resolve()), 'objects': len(report['summary']['objects']),
                      'appearancePriors': report['appearancePriors']}, indent=2))


if __name__ == '__main__':
    main()
