"""Replay the archived initial-state-0 occlusion regression without robot commands."""
import argparse,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from steward.ego.panda_runtime import PandaEgoRuntime
from steward.ego.panda_visual_model import PandaVisualModel
from steward.ego.panda_surfaces import surface_entities

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sensors-dir',type=Path,required=True)
parser.add_argument('--bundle',type=Path,default=Path('var/step4/wsim-root/libero-step3'))
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
folder=args.sensors_dir
runtime=PandaEgoRuntime(SimpleNamespace(telemetry=lambda scene:None))
prior=PandaVisualModel(args.bundle);prior.load()
report=[]
for index,path in enumerate(sorted(folder.glob('*.npz'))):
    with np.load(path) as data:
        obs=json.loads(str(data['metadata']))
        obs['images']={k:data[k].copy() for k in ('external','wrist')}
        obs['depth']={k:data[k+'_depth'].copy() for k in ('external','wrist')}
    runtime.sensor.observation=obs
    runtime.sensor.read=lambda **kwargs:obs
    runtime.observe(force=True)
    if index%5 or runtime.evidence.grasp!='confirmed' or runtime.evidence.release is not None:continue
    known=[e for e in runtime.scene['entities'] if e['role']=='object' and e['category']['value']!='unknown']
    baseline=surface_entities(obs,f"e:{obs['episodeId']}:",known)
    runtime.observe_surfaces()
    def near_hand(entities):
        return [{'id':e.id,'appearance':m['category']} for e in entities
                if np.linalg.norm(np.array(e.pose.value['p'])-obs['tool']['p'])<.13
                and (m:=prior.matcher.match(e.to_dict())) is not None]
    report.append({'frame':index,'step':obs['step'],'before':near_hand(baseline),
                   'after':near_hand(runtime.visible_objects)})
args.out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2));assert any(r['before'] for r in report);assert not any(r['after'] for r in report)
