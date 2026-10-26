"""Generate a reference-safe, Y-up/meters MaterialX sample with OpenUSD.

Run with uv run -- <HFS>/bin/hython.exe scripts/generate_solaris_materialx_sample.py.
The original usdview scene keeps its existing prim paths and registry bindings.
"""
import hashlib
import os
from pathlib import Path

from pxr import Sdf,Usd,UsdGeom,UsdShade,UsdUtils
from scene_space import normalize_stage

ROOT=Path(__file__).resolve().parents[1]


def generate(source,output):
    original=hashlib.sha256(source.read_bytes()).hexdigest()
    stage=Usd.Stage.Open(Usd.Stage.Open(str(source)).Flatten())
    normalize_stage(stage,str(UsdGeom.GetStageUpAxis(stage)),UsdGeom.GetStageMetersPerUnit(stage))
    root=UsdGeom.Xform.Define(stage,'/MaterialXShowcase').GetPrim()
    for prim in list(stage.GetPseudoRoot().GetChildren()):
        if prim==root: continue
        editor=Usd.NamespaceEditor(stage)
        if not editor.MovePrimAtPath(prim.GetPath(),root.GetPath().AppendChild(prim.GetName())):
            raise ValueError('Cannot queue namespace move: '+str(prim.GetPath()))
        valid=editor.CanApplyEdits()
        if not valid: raise ValueError(str(valid))
        if not editor.ApplyEdits(): raise ValueError('Namespace move failed')
    stage.SetDefaultPrim(root)
    stage.GetRootLayer().documentation='Reference-safe MaterialX showcase for Solaris, generated from showcase.usda.'
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage,1.)
    def asset_path(value):
        path=Path(value)
        if path.is_absolute(): return os.path.relpath(path,output.parent).replace('\\','/')
        return value
    UsdUtils.ModifyAssetPaths(stage.GetRootLayer(),asset_path)
    stage.GetRootLayer().Export(str(output))
    verify_reference(output)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=original:
        raise AssertionError('Original scene changed')
    print(str(output)+': reference-safe MaterialX sample, Y-up / meters')


def verify_reference(file):
    stage=Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Imported','Xform').GetReferences().AddReference(str(file.resolve()))
    meshes=[prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]
    if not meshes: raise AssertionError('Referenced sample contains no geometry')
    for mesh in meshes:
        material,_=UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
        if not material or not material.GetPath().HasPrefix(Sdf.Path('/Imported')):
            raise AssertionError('Unbound material after reference: '+str(mesh.GetPath()))
        if not material.ComputeSurfaceSource()[0]:
            raise AssertionError('Missing preview surface: '+str(material.GetPath()))
    materials=[prim for prim in stage.Traverse() if prim.IsA(UsdShade.Material)]
    if len(materials)!=12 or len(meshes)!=21:
        raise AssertionError((len(materials),len(meshes)))
    print('PASS reference composition: 21 bound meshes / 12 materials')


if __name__=='__main__':
    generate(ROOT/'samples/materialx/showcase.usda',ROOT/'samples/materialx/showcase_solaris.usda')
