import gc,hashlib,json
from pathlib import Path
import numpy as np
import jax.numpy as jnp
from openpi.models.model import restore_params
out=Path('/home/huawei/hqk/workspace/steward/steward/var/pi05-live-20261111T072702Z/diagnosis')
rows=[]
for index in range(2):
    params=restore_params('/home/huawei/hqk/workspace/pi05-step1/checkpoints/pi05_libero/params',restore_type=np.ndarray,dtype=jnp.bfloat16)
    tensor=params['PaliGemma']['img']['Transformer']['encoderblock']['MlpBlock_0']['Dense_1']['kernel']
    row={'index':index,'sha256':hashlib.sha256(tensor.tobytes()).hexdigest(),'shape':list(tensor.shape),'dtype':str(tensor.dtype),'finite':bool(np.isfinite(tensor).all()),'min':float(tensor.min()),'max':float(tensor.max())}
    np.save(out/('host-vision-kernel-'+str(index)+'.npy'),tensor.view(np.uint16))
    print(json.dumps(row),flush=True);rows.append(row)
    del tensor,params;gc.collect()
(out/'host-restore-comparison.json').write_text(json.dumps(rows,indent=2)+'\n')
