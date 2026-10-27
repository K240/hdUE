"""Author a reversible wet override LOP on selected composed USD prims."""
import hou
from pxr import Sdf


def create(paths=None, input_node=None):
    import hdue_houdini
    viewer=hdue_houdini.scene_viewer()
    if paths is None: paths=list(viewer.currentSceneGraphSelection())
    paths=[str(p) for p in paths]
    if not paths: raise ValueError('Select one or more Mesh, GeomSubset or ancestor prims in the Scene Graph Tree.')
    for path in paths:
        if not Sdf.Path.IsValidPathString(path) or not Sdf.Path(path).IsAbsolutePath() or not Sdf.Path(path).IsPrimPath():
            raise ValueError('Expected an absolute prim path: '+path)
    network=viewer.pwd()
    input_node=input_node or network.displayNode()
    if not input_node: raise ValueError('Display a Solaris input stage before creating a wet override.')
    with hou.undos.group('Create hdUE wet surface override'):
        node=input_node.parent().createNode('pythonscript','hdUE_wet_surface')
        node.setInput(0,input_node)
        group=node.parmTemplateGroup()
        group.append(hou.StringParmTemplate('hdue_wet_paths','Prim paths',1,default_value=(' '.join(paths),)))
        group.append(hou.ToggleParmTemplate('hdue_wet_enabled','Enable water coating',default_value=True))
        group.append(hou.FloatParmTemplate('hdue_wet_amount','Coverage amount',1,default_value=(1.,),min=0,max=1,min_is_strict=True,max_is_strict=True))
        group.append(hou.FloatParmTemplate('hdue_wet_roughness','Water roughness',1,default_value=(.08,),min=0,max=1,min_is_strict=True,max_is_strict=True))
        group.append(hou.StringParmTemplate('hdue_wet_mask','Float mask primvar',1,default_value=('',),help='Base name, without primvars:. Empty means uniform coverage.'))
        node.setParmTemplateGroup(group)
        node.parm('python').set('import hdue_surface\nhdue_surface.cook(hou.pwd())')
        node.setDisplayFlag(True); node.setCurrent(True,clear_all_selected=True); node.moveToGoodPosition()
    return node


def cook(node):
    stage=node.editableStage()
    from surface_effects import FIELDS,PREFIX
    values=dict(enabled=bool(node.evalParm('hdue_wet_enabled')),amount=node.evalParm('hdue_wet_amount'),
                roughness=node.evalParm('hdue_wet_roughness'),maskPrimvar=node.evalParm('hdue_wet_mask'))
    for path in node.evalParm('hdue_wet_paths').split():
        if not stage.GetPrimAtPath(path): raise ValueError('Wet target does not exist in the composed stage: '+path)
        prim=stage.OverridePrim(path)
        for name,(kind,_) in FIELDS.items(): prim.CreateAttribute(PREFIX+name,kind,custom=True).Set(values[name])
