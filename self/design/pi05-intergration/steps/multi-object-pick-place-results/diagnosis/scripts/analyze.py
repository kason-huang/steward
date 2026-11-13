"""Replay archived evidence without changing the live service. Requires --out.

Run with the Steward venv from the steward Python package directory.
"""
import argparse
import json,base64,io
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from steward.ego.panda_prediction import PandaPrediction
from steward.ego.panda_policy import PolicyFailure
package=Path('/home/huawei/hqk/workspace/steward/steward')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--job',type=Path,default=package/'var/step4/wsim-root/runs/20261113-115939/ego-panda/policy-jobs/panda-job-9ae791345f7445b6963996551c161524')
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
job=args.job.resolve()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
receipt=json.loads((job/'receipt.json').read_text());target=receipt['plan']['object'];support=receipt['plan']['destination']
def observation(index):
 z=np.load(job/'sensors'/f'{index:04d}.npz');obs=json.loads(str(z['metadata']))
 obs['images']={k:z[k] for k in ('external','wrist')};obs['depth']={k:z[k+'_depth'] for k in ('external','wrist')}
 return obs
rows=[]
for index in (0,5,10,15,20,25,30,35,39):
 obs=observation(index);p=np.asarray(obs['tool']['p'])
 rows.append({'actionCount':index,'tool':p.tolist(),'distanceToCreamM':float(np.linalg.norm(p-target['position'])),
 'distanceToSoupM':float(np.linalg.norm(p-[-.11035747,-.2400338,.05716067]))})
obs=observation(39);actions=json.loads((job/'prefix-007/inference.json').read_text())['actions'];action=np.clip(actions[4],-1,1)
try: replay=PandaPrediction(obs,target['position'],support['position'],target['sizeM']).check([action])
except PolicyFailure as e:replay={'code':e.code,'message':str(e)}
canvas=Image.new('RGB',(3*450,2*485),'white');draw=ImageDraw.Draw(canvas)
for col,index in enumerate((0,20,39)):
 for row,role in enumerate(('external','wrist')):
  path=job/role/f'{index:04d}.png'
  im=Image.open(path).convert('RGB').resize((448,448));canvas.paste(im,(450*col,485*row+30))
  draw.text((450*col+5,485*row+5),f'{role}: applied actions {index}',fill='black')
canvas.save(out/'raw-camera-montage.jpg')
# Save the exact mirrored policy inputs alongside raw camera images.
source=json.loads((job/'prefix-000/observation.json').read_text())
for key,role in [('observation/image','external'),('observation/wrist_image','wrist')]:
 Image.open(io.BytesIO(base64.b64decode(source[key]))).save(out/f'policy-input-{role}.png')
result={'trajectory':rows,'rejectedAction':action.tolist(),'predictionReplay':replay,'target':target,'support':support}
(out/'offline-analysis.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
