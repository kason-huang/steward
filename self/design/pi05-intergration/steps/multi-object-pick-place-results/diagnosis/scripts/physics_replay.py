"""Replay archived evidence without changing the live service. Requires --out.

Run with the Steward venv from the steward Python package directory.
"""
import argparse
import json,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import mujoco
package=Path('/home/huawei/hqk/workspace/steward/steward');sys.path.insert(0,str(package/'scripts'))
from steward.world.server import StewardHost
from steward.world.panda_control import LIBERO_TASK_POLICY
from verify_libero_step3 import Robot,PANDA_ID,call,credentials
from verify_libero_step6 import capture,submit
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--job',type=Path,default=package/'var/step4/wsim-root/runs/20261113-115939/ego-panda/policy-jobs/panda-job-9ae791345f7445b6963996551c161524')
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
job=args.job.resolve()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
rows=[json.loads(l) for l in (job/'actions.jsonl').read_text().splitlines()]
actions=[r['applied_action'] for r in rows]
actions.append(np.clip(json.loads((job/'prefix-007/inference.json').read_text())['actions'][4],-1,1).tolist())
host=StewardHost(package/'var/step4/wsim-root',scenario='libero-object-alphabet-soup-panda',freeze_idle=True,dispatch_task=False,speed=float('inf')).start()
trace=[];maximum_error=0

def snapshot():
 m,d=host.sim.model,host.sim.data
 contacts=[]
 for c in d.contact:
  names=[m.body(m.geom_bodyid[g]).name for g in (c.geom1,c.geom2)]
  if any('alphabet_soup' in n for n in names) and any('robot0' in n or 'gripper0' in n for n in names):
   contacts.append({'bodies':names,'geoms':[m.geom(g).name for g in (c.geom1,c.geom2)],'distance':float(c.dist)})
 return contacts

def run():
 global maximum_error
 robot=Robot(host.robot_servers[PANDA_ID].url);robot.advance(0)
 lease=credentials(call(robot.url,'/control/acquire',credentials(robot.status())|{'ttlS':30,'policy':LIBERO_TASK_POLICY}))
 def sample():
  contacts=snapshot()
  if contacts:trace.append({'step':host.sim.step_count,'contacts':contacts})
 dispose=host.sim.subscribe_steps(sample)
 positions=[]; clearances=[]
 try:
  for index,action in enumerate(actions):
   frame=capture(robot)
   submit(robot,lease,index,action,frame['observationId'])
   robot.advance((index+1)*25)
   at=robot.status()['sitePosition'];positions.append(at)
   if index >= 38:
    def geometry_clearance():
     m,d=host.sim.model,host.sim.data
     soup=[g for g in range(m.ngeom) if 'alphabet_soup' in m.body(m.geom_bodyid[g]).name and (m.geom_contype[g] or m.geom_conaffinity[g])]
     arm=[g for g in range(m.ngeom) if any(n in m.body(m.geom_bodyid[g]).name for n in ('robot0','gripper0')) and (m.geom_contype[g] or m.geom_conaffinity[g])]
     values=[(float(mujoco.mj_geomDistance(m,d,a,b,1,np.zeros(6))),m.geom(a).name,m.geom(b).name) for a in arm for b in soup]
     return {'actionCount':index+1,'nearestRobotSoupGeometry':min(values) if values else None,'soupCollisionGeometries':len(soup)}
    clearances.append(host.on_main(geometry_clearance))
   if index<len(rows):maximum_error=max(maximum_error,float(np.linalg.norm(np.subtract(at,rows[index]['site_position']))))
  robot.stop(lease);robot.advance(robot.status()['step']+25)
 finally:
  dispose()
  if robot.status()['leased']:robot.stop(lease)
 return {'scope':'isolated WSim, recorded 39 actions plus the single rejected 40th action; live service untouched','replayedActions':len(actions),'maximumToolPositionErrorMForAccepted39':maximum_error,'soupRobotContacts':trace,'finalToolPosition':positions[-1],'meshClearances':clearances}
try:
 with ThreadPoolExecutor(max_workers=1) as pool:
  f=pool.submit(run)
  while not f.done():host.pump(timeout=.002)
  result=f.result()
 (out/'physics-replay.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
finally:host.stop()
