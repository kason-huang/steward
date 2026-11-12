"""Shared SceneState renderer supports self/class mesh appearances alongside collision boxes."""
import base64
import mujoco
from steward.scene.schema import Entity,Link,RigidTree,SceneState,exact,pose,primitive
from steward.scene.mjcf import build_mjcf


def test_shared_converter_preserves_mesh_visual_geometry():
    obj=b'v 0 0 0\nv .1 0 0\nv 0 .1 0\nv 0 0 .1\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n'
    mesh={'kind':'mesh','data':base64.b64encode(obj).decode(),'pose':pose(),'rgba':[1,1,1,1]}
    entity=Entity('e:test','object',exact('known'),exact(pose()),RigidTree(exact('fixed'),[Link('body','body',{'collision':[primitive('box',[.1,.1,.1])],'visual':[mesh]})]))
    build=build_mjcf(SceneState('ego',entities=[entity]));model=build.compile()
    assert model.nmesh==1
    assert model.geom('e:test/body::v0').type[0]==mujoco.mjtGeom.mjGEOM_MESH
    assert model.geom('e:test/body::c0').group[0]==3


def test_mesh_cannot_silently_replace_collision_primitives():
    import pytest
    entity=Entity('e:test','object',exact('known'),exact(pose()),RigidTree(exact('fixed'),[Link('body','body',{'collision':[{'kind':'mesh','data':''}]})]))
    with pytest.raises(NotImplementedError,match='visual-only'):
        build_mjcf(SceneState('ego',entities=[entity]))
