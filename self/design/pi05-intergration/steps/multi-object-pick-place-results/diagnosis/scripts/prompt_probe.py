"""Replay archived evidence without changing the live service. Requires --out.

Run with the Steward venv from the steward Python package directory.
"""
import argparse
import json,subprocess
from pathlib import Path
import numpy as np
package=Path('/home/huawei/hqk/workspace/steward/steward')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--job',type=Path,default=package/'var/step4/wsim-root/runs/20261113-115939/ego-panda/policy-jobs/panda-job-9ae791345f7445b6963996551c161524')
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
job=args.job.resolve()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
rows=[]
for prefix in ('prefix-000','prefix-007'):
 original=json.loads((job/prefix/'observation.json').read_text())
 for repeat in range(3):
  for name in ('cream cheese','alphabet soup','milk'):
   folder=out/(prefix+'-'+name.replace(' ','_')+'-'+str(repeat));folder.mkdir(exist_ok=True)
   payload=original|{'prompt':'pick up the '+name+' and place it in the basket'}
   source=folder/'observation.json';source.write_text(json.dumps(payload))
   with (folder/'client.log').open('w') as log:
    subprocess.run(['bash','/home/huawei/hqk/workspace/pi05-step1/client.sh',str(package/'scripts/openpi_step6_infer.py'),'18065',str(source),str(folder/'inference.json')],stdout=log,stderr=log,check=True,timeout=25)
   actions=np.clip(json.loads((folder/'inference.json').read_text())['actions'],-1,1)
   row={'prefix':prefix,'prompt':payload['prompt'],'repeat':repeat,'first5TranslationSumM':(.05*actions[:5,:3].sum(0)).tolist(),'horizonTranslationSumM':(.05*actions[:,:3].sum(0)).tolist(),'first5MeanAction':actions[:5].mean(0).tolist()}
   rows.append(row);print(json.dumps(row),flush=True)
(out/'summary.json').write_text(json.dumps(rows,indent=2))
