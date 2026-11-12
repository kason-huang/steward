import numpy as np
from steward.ego.panda_self_model import self_entity
from steward.scene.schema import SceneState
from steward.scene.mjcf import build_mjcf


def observation(angle):
    return {'encoders':{'j':{'position':angle,'velocity':.2}},'selfVisual':[
        {'chain':[{'origin':{'p':[-.6,0,0],'q':[1,0,0,0]}},
                  {'origin':{'p':[0,0,0],'q':[1,0,0,0]},'joint':'j','axis':[0,0,1],'type':'revolute'}],
         'mount':{'p':[.2,0,0],'q':[1,0,0,0]},'bounds':[0,0,0,.05,.02,.02]}],
         'worldObjects':[{'id':'secret','pose':[9,8,7]}]}


def test_encoder_model_uses_shared_scene_converter_without_world_objects():
    robot=self_entity(observation(np.pi/2),'e:self')
    build=build_mjcf(SceneState('ego',entities=[robot],self_id=robot.id))
    model=build.compile()
    import mujoco
    data=mujoco.MjData(model);build.apply_initial(model,data)
    gid=model.geom(build.names[robot.id].collision_geoms[0]).id
    np.testing.assert_allclose(data.geom_xpos[gid],[-.6,.2,0],atol=1e-6)
    assert robot.pose.src=='prior' and robot.rigid.joints[0].q.src=='observed'
    assert 'secret' not in robot.to_dict().__str__()


def test_shared_calibrated_chain_is_not_duplicated():
    packet=observation(.4);packet['selfVisual']*=2
    robot=self_entity(packet,'e:self')
    assert len(robot.rigid.links)==2 and len(robot.rigid.joints)==1
    assert robot.rigid.joints[0].q.value==[.4]
    assert len(robot.rigid.links[1].geometry['collision'])==2
