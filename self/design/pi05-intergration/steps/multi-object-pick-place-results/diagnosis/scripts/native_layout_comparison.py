"""Compare the original cream task with cream selection in the native soup layout.

Runs isolated original LIBERO environments against an existing policy server.
The soup-layout cases replace only the prompt and goal predicate; no WSim or
Steward collision predictor is involved. Preserve every trial's own result/video.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference-root', type=Path, default=Path('/home/huawei/hqk/workspace/pi05-step1'))
parser.add_argument('--port', type=int, default=18065)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
reference = args.reference_root.resolve()
source = (reference / 'evaluate.py').read_text()
init_line = 'obs = env.set_init_state(states[args.init_index])'
record_line = '"success_signal": success, "reward": float(reward),'
if source.count(init_line) != 1 or source.count(record_line) != 1:
    raise RuntimeError('Pinned evaluator changed; inspect the initialization and step recording before patching')
patched = source.replace(init_line, init_line + '''
        env.env.parsed_problem["goal_state"] = [["in", "cream_cheese_1", "basket_1_contain_region"]]
        prompt = "pick up the cream cheese and place it in the basket"
        result.update(prompt=prompt, diagnosticLayout="native soup task layout with cream-cheese prompt and cream-cheese goal predicate")''')
patched = patched.replace(record_line, record_line + '''
                    "soup_in_basket": bool(env.env._eval_predicate(["in", "alphabet_soup_1", "basket_1_contain_region"])),
                    "cream_in_basket": bool(env.env._eval_predicate(["in", "cream_cheese_1", "basket_1_contain_region"])),''')
probe = out / 'native_soup_layout_cream.py'
probe.write_text(patched)
manifest = {'policyPort': args.port, 'sourceEvaluator': str(reference / 'evaluate.py'),
            'sourceEvaluatorSha256': hashlib.sha256(source.encode()).hexdigest(), 'trials': []}
trials = [('native-cream-cheese-init0', 'cream_cheese', 1, reference / 'evaluate.py')]
trials += [(f'native-soup-layout-cream-repeat{i}', 'alphabet_soup', 0, probe) for i in range(3)]
for name, task, task_id, evaluator in trials:
    command = ['bash', str(reference / 'client.sh'), str(evaluator), '--task',
               f'pick_up_the_{task}_and_place_it_in_the_basket', '--task-id', str(task_id),
               '--task-order-index', '0', '--init-index', '0', '--seed', '7', '--attempt', name,
               '--output', str(out), '--port', str(args.port), '--timeout', '180']
    with (out / (name + '.log')).open('w') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True, timeout=200)
    result = json.loads((out / name / 'result.json').read_text())
    row = {'attempt': name, 'command': command, 'outcome': result['outcome'],
           'steps': result['control_steps'], 'successSignal': result['success_signal']}
    if task_id == 0:
        records = [json.loads(line) for line in (out / name / 'trajectory.jsonl').read_text().splitlines()]
        row.update(soupInBasketEver=any(r.get('soup_in_basket') for r in records),
                   creamInBasketEver=any(r.get('cream_in_basket') for r in records))
    manifest['trials'].append(row)
    (out / 'comparison.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(row), flush=True)
