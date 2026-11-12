"""Static self/class visual priors; no episode snapshots or world object poses are read."""
import base64,copy,hashlib,json
from pathlib import Path
from io import BytesIO
from PIL import Image
import xml.etree.ElementTree as ET
import mujoco
import numpy as np
import threading
from steward.glrender import encode_image
from steward.world.panda_sensors import chain
from steward.math3d import pose_compose

CLASSES = ('alphabet_soup', 'basket', 'salad_dressing', 'cream_cheese', 'milk', 'tomato_sauce', 'butter')


class PandaVisualModel:
    def __init__(self,bundle):
        self.bundle=Path(bundle).resolve();self.robot=None;self.objects={}
        self._load_lock=threading.Lock();self._ready=False

    def load(self):
        with self._load_lock:
            if not self._ready:
                self._load()
                self._ready=True

    def _load(self):
        manifest=dict(line.split(maxsplit=1)[::-1] for line in (self.bundle/'SHA256SUMS').read_text().splitlines())
        def read(name):
            path=(self.bundle/name).resolve()
            if not path.is_relative_to(self.bundle):raise ValueError('Visual asset outside bundle')
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=manifest.get(name):raise ValueError('Visual asset checksum mismatch: '+name)
            return data
        original=ET.fromstring(read('panda.xml'));xml=ET.Element('mujoco')
        for tag in ('compiler','default','asset'):
            for element in original.findall(tag):xml.append(copy.deepcopy(element))
        world=ET.SubElement(xml,'worldbody')
        for name in ('robot0_base', *(category+'_1_main' for category in CLASSES)):
            node=copy.deepcopy(original.find("worldbody/body[@name='%s']"%name))
            if name!='robot0_base':
                node.set('pos','0 0 0');node.set('quat','1 0 0 0')
                for joint in list(node.findall('joint'))+list(node.findall('freejoint')):node.remove(joint)
            world.append(node)
        assets={}
        for node in xml.iter():
            if node.get('file'):
                filename=node.get('file');key=hashlib.sha256(filename.encode()).hexdigest()[:16]+Path(filename).suffix
                assets[key]=read(filename);node.set('file',key)
        model=mujoco.MjModel.from_xml_string(ET.tostring(xml,encoding='unicode'),assets)
        self.robot={}
        for index in range(model.ngeom):
            geom=model.geom(index)
            if geom.group[0]!=1:continue
            body=model.body(int(geom.bodyid[0]));name=body.name
            if not name.startswith(('robot0_','gripper0_', *(c+'_1' for c in CLASSES))):continue
            if geom.type[0]!=mujoco.mjtGeom.mjGEOM_MESH:continue
            mid=int(geom.dataid[0]);vadr=model.mesh_vertadr[mid];vn=model.mesh_vertnum[mid];fadr=model.mesh_faceadr[mid];fn=model.mesh_facenum[mid]
            lines=['v '+' '.join(map(str,v)) for v in model.mesh_vert[vadr:vadr+vn]]
            tadr=model.mesh_texcoordadr[mid];tn=model.mesh_texcoordnum[mid]
            if tadr>=0:lines+=['vt '+str(v[0])+' '+str(1-float(v[1])) for v in model.mesh_texcoord[tadr:tadr+tn]]
            for k,face in enumerate(model.mesh_face[fadr:fadr+fn]):
                tex=model.mesh_facetexcoord[fadr+k]
                lines.append('f '+' '.join(str(int(v)+1)+('/'+str(int(t)+1) if tadr>=0 and t>=0 else '') for v,t in zip(face,tex)))
            item={'kind':'mesh','data':base64.b64encode(('\n'.join(lines)+'\n').encode()).decode(),
                  'pose':{'p':geom.pos.tolist(),'q':geom.quat.tolist()},'rgba':geom.rgba.tolist()}
            material=int(geom.matid[0])
            if material>=0:
                item['rgba']=model.mat_rgba[material].tolist()
                tids=np.asarray(model.mat_texid[material]).ravel();tids=tids[tids>=0]
                if len(tids):
                    tid=int(model.mat_texid[material][int(mujoco.mjtTextureRole.mjTEXROLE_RGB)])
                    texture=original.find("asset/texture[@name='%s']"%model.texture(tid).name)
                    if texture is not None and texture.get('file'):
                        with Image.open(BytesIO(read(texture.get('file')))) as image:
                            item['meshTexture']=base64.b64encode(encode_image(np.array(image.convert('RGB')))).decode()
            if name.startswith(('robot0_','gripper0_')):
                key=json.dumps(chain(model,body.id),sort_keys=True)
                self.robot.setdefault(key,[]).append(item)
            else:
                category=next(c for c in CLASSES if name.startswith(c+'_1'))
                self.objects.setdefault(category,[]).append(item)
        # Recenter category templates geometrically, independent of any episode placement.
        data=mujoco.MjData(model);mujoco.mj_forward(model,data)
        for category in CLASSES:
            prefix=category+'_1'
            gids=[i for i in range(model.ngeom) if model.body(model.geom(i).bodyid[0]).name.startswith(prefix) and model.geom(i).group[0]==1]
            low=[];high=[]
            for gid in gids:
                center,half=model.geom_aabb[gid].reshape(2,3);rotation=data.geom_xmat[gid].reshape(3,3)
                center=data.geom_xpos[gid]+rotation@center;extent=np.abs(rotation)@half
                low.append(center-extent);high.append(center+extent)
            center=(np.min(low,axis=0)+np.max(high,axis=0))/2
            # The HOPE soup mesh's cylinder axis is +Y. The Ego position/size
            # estimate uses an upright +Z cylinder, unlike the source mesh frame.
            canonical = {'p': [0, 0, 0], 'q': [2**-.5, 2**-.5, 0, 0]
                         if category == 'alphabet_soup' else [1, 0, 0, 0]}
            for item in self.objects[category]:
                item['pose']['p']=(np.array(item['pose']['p'])-center).tolist()
                item['pose']=pose_compose(canonical,item['pose'])
        from steward.ego.visual_matching import VisualMatcher
        self.matcher=VisualMatcher(self.objects)
