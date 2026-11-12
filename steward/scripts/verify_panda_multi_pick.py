"""Exercise six pick targets through real WSim/HTTP with an explicit no-op test policy.

Pass means correct target admission, instruction, execution and stopping, while correctly
refusing to claim a grasp. It is not a pi05 or physical grasp-success benchmark.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from steward.ego.panda_policy import PandaPolicyConfig
from steward.ego.panda_runtime import PandaEgoRuntime, panda_ego_api
from steward.ego.panda_visual_model import PandaVisualModel
from steward.ego.runtime import RobotLink
from steward.net import call
from steward.world.server import StewardHost

CATEGORIES = ('alphabet_soup', 'milk', 'butter', 'cream_cheese', 'salad_dressing', 'tomato_sauce')


def off_main(host, operation):
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(operation)
        while not work.done():
            host.pump(timeout=.002)
        return work.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('var/step4/wsim-root'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = args.out.resolve() / 'noop-policy.py'
    client.write_text("import json,sys\nfrom pathlib import Path\n"
                      "Path(sys.argv[3]).write_text(json.dumps({'actions':[[0,0,0,0,0,0,-1]]*5}))\n")
    prior = PandaVisualModel(args.root / 'libero-step3')
    prior.load()
    rows = []
    for category in CATEGORIES:
        folder = args.out / category
        host = StewardHost(args.root, scenario='libero-object-alphabet-soup-panda',
                           freeze_idle=True, dispatch_task=False, speed=float('inf')).start()
        config = PandaPolicyConfig((sys.executable, str(client)), 1, 'explicit-noop-system-test',
                                   max_actions=5, max_wall_s=30)
        runtime = PandaEgoRuntime(RobotLink(host.robot_servers['w:panda'].url), config, folder)
        runtime.visual_prior = prior
        api = panda_ego_api(runtime, 0, 0).start()
        try:
            def check():
                runtime.observe(force=True)
                runtime.observe_surfaces()
                summary = call('GET', api.url + '/ego/summary')
                selected = next(o for o in summary['objects'] if o['category'] == category)
                receipt = call('POST', api.url + '/ego/predict', {'skill': 'pick', 'params': {'target': selected['id']}})
                assert receipt['feasible'], receipt['issues']
                job = call('POST', api.url + '/skill/run', {'receipt': receipt['id']})
                final = call('POST', api.url + '/skill/wait', {'job': job['id'], 'timeoutS': 30})
                request = json.loads((runtime.executor.out / job['id'] / 'prefix-000/observation.json').read_text())
                assert request['prompt'] == 'pick up the ' + category.replace('_', ' ')
                assert request['target']['id'] == selected['id']
                assert final['skill'] == 'pick' and final['actionsApplied'] == 5
                assert final['state'] == 'failed' and final['goalStatus'] == 'not_achieved'
                assert final['stopStatus'] == 'confirmed' and not host.sim._holds
                for name, data in (('initial-summary', summary), ('receipt', receipt), ('job', final)):
                    (folder / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')
                return {'category': category, 'target': selected['id'], 'prompt': request['prompt'],
                        'systemCheck': 'passed', 'policy': 'explicit-noop-system-test',
                        'job': final['id'], 'actionsApplied': final['actionsApplied'],
                        'state': final['state'], 'goalStatus': final['goalStatus'],
                        'stopStatus': final['stopStatus']}
            row = off_main(host, check)
            rows.append(row)
            print(json.dumps(row), flush=True)
        finally:
            off_main(host, runtime.stop)
            api.stop()
            host.stop()
    (args.out / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
