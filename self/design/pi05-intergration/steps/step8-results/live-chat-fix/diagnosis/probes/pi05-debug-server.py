import argparse,hashlib,json,logging,os,time
from pathlib import Path
import numpy as np
import jax
import flax.nnx as nnx
from openpi.policies.policy_config import create_trained_policy
from openpi.serving.websocket_policy_server import WebsocketPolicyServer
from openpi.training.config import get_config
p=argparse.ArgumentParser();p.add_argument('--port',type=int);p.add_argument('--out',type=Path);a=p.parse_args()
logging.basicConfig(level=logging.INFO,force=True)
policy=create_trained_policy(get_config('pi05_libero'),'/home/huawei/hqk/workspace/pi05-step1/checkpoints/pi05_libero')
a.out.mkdir(parents=True,exist_ok=True)
weights=hashlib.sha256();leaves=[]
for path,value in jax.tree_util.tree_flatten_with_path(nnx.state(policy._model))[0]:
    if hasattr(value, "dtype") and jax.dtypes.issubdtype(value.dtype,jax.dtypes.prng_key):value=jax.random.key_data(value)
    array=np.asarray(jax.device_get(value))
    name=str(path);digest=hashlib.sha256(array.tobytes()).hexdigest()
    row={'path':name,'shape':list(array.shape),'dtype':str(array.dtype),'sha256':digest}
    leaves.append(row);weights.update(json.dumps(row,sort_keys=True).encode())
metadata={'paramsSHA256':weights.hexdigest(),'leaves':len(leaves),'pid':os.getpid(),'port':a.port,'OPENBLAS_NUM_THREADS':os.environ.get('OPENBLAS_NUM_THREADS'),'initialRng':np.asarray(jax.random.key_data(policy._rng)).tolist()}
(a.out/'weights.json').write_text(json.dumps({'metadata':metadata,'leaves':leaves},indent=2)+'\n')
original_transform=policy._input_transform
counter=0

def transform(inputs):
    global counter
    raw=original_transform(inputs)
    rows=[]
    for path,value in jax.tree_util.tree_flatten_with_path(raw)[0]:
        value=np.asarray(value)
        row={'path':str(path),'shape':list(value.shape),'dtype':str(value.dtype),'sha256':hashlib.sha256(value.tobytes()).hexdigest()}
        if value.size<200:row['values']=value.tolist()
        rows.append(row)
    (a.out/('input-%03d.json'%counter)).write_text(json.dumps({'rng':np.asarray(jax.random.key_data(policy._rng)).tolist(),'inputs':rows},indent=2)+'\n')
    counter+=1
    return raw
policy._input_transform=transform
print(json.dumps(metadata),flush=True)
WebsocketPolicyServer(policy,host='127.0.0.1',port=a.port,metadata=metadata).serve_forever()
