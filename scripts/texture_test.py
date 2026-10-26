"""Real normal-direction/strength and UDIM-layout checks in both renderers."""
import hashlib
import math
import time
from pathlib import Path
import numpy as np
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade,Vt
from material_api import MaterialApi
from usdview_test import SmokeTest,PLUGIN


class TextureTest(SmokeTest):
    def __init__(self,*args):
        super().__init__(*args); self.deadline=time.monotonic()+600

    def texture(self,name):
        entry=self.launcher.materialx_library.bound_entries["/Looks/"+name]
        return next(p for p in entry["parameters"] if p["type"]=="texture")

    def override(self,name,value):
        shader=UsdShade.Shader(self.controller._dataModel.stage.GetPrimAtPath("/Looks/"+name+"/Image"))
        shader.CreateInput("hdue:texture",Sdf.ValueTypeNames.String).Set(value)

    def strengths(self,value):
        for name in ("SS_Normal","OP_Normal","UDIM_Normal"):
            self.controller._dataModel.stage.GetAttributeAtPath("/Looks/"+name+"/Normal.inputs:scale").Set(value)

    def references(self,scale):
        length=math.sqrt(0.8**2+(0.6*scale)**2)
        normal=Gf.Vec3f(0,-0.8/length,0.6*scale/length)
        for family in ("SS","OP"):
            mesh=UsdGeom.Mesh(self.controller._dataModel.stage.GetPrimAtPath("/World/"+family+"_Reference"))
            mesh.GetNormalsAttr().Set(Vt.Vec3fArray([normal]*4))

    def patch(self,rgb,point,radius=5):
        camera=self.view.resolveCamera()[0]
        matrix=camera.frustum.ComputeViewMatrix()*camera.frustum.ComputeProjectionMatrix()
        clip=Gf.Vec4d(*point,1)*matrix
        x=int((clip[0]/clip[3]+1)*rgb.shape[1]*0.5); y=int((1-clip[1]/clip[3])*rgb.shape[0]*0.5)
        data=rgb[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]
        if not data.size:
            raise AssertionError(f"Projected sample is offscreen: {point}")
        return np.median(data,axis=(0,1)).astype(float)

    def check_udim(self,rgb,label,swapped=False):
        expected={1001:"B",1002:"R",1011:"G",1012:"M"} if swapped else {1001:"R",1002:"G",1011:"B",1012:"Y"}
        for index,color in expected.items():
            u,v=(index-1001)%10,(index-1001)//10
            pixel=self.patch(rgb,(-350+u*100,0,160+v*100))
            r,g,b=pixel
            good={"R":r>g*3 and r>b*3,"G":g>r*3 and g>b*3,"B":b>r*3 and b>g*3,
                  "Y":r>b*3 and g>b*3,"M":r>g*3 and b>g*3}[color]
            self.udim.append(dict(frame=label,tile=index,color=color,rgb=pixel.tolist()))
            if not good or max(pixel)<100:
                raise AssertionError(f"UDIM tile {index} is wrong: {pixel}, expected {color}")
        marker=self.patch(rgb,(-387.5,0,197.5),2)
        if min(marker)<170:
            raise AssertionError(f"UDIM image V orientation is wrong: {marker}")

    def check_normals(self,rgb,label):
        for family,z in (("SS",130),("OP",320)):
            actual=self.patch(rgb,(-70,0,z)); reference=self.patch(rgb,(110,0,z))
            error=float(np.mean(np.abs(actual-reference))/max(1,np.mean(reference)))
            self.normal_comparisons.append(dict(frame=label,model=family,normal_rgb=actual.tolist(),geometry_rgb=reference.tolist(),relative_difference=error))
            if error>0.04:
                raise AssertionError(f"{family} normal direction/strength disagrees with geometry: {self.normal_comparisons[-1]}")

    def image(self,label,samples=0,previous=None):
        if previous is None: previous=self.frames[-1]["generation"] if self.frames else 0
        rgb=yield from self.wait_frame(label,previous,samples,require_red=False)
        stats=self.stats()
        if stats["meshes"]!=6 or not stats["substrateEnabled"] or not stats["adaptiveGBuffer"]:
            raise AssertionError(f"Incorrect texture scene: {stats}")
        self.view._renderer.InvokeRendererCommand("hdue:sampleMemory",{})
        self.frames[-1]["gpuLocalMemoryBytes"]=self.stats()["gpuLocalMemoryBytes"]
        return rgb

    def run(self):
        model=self.controller._dataModel; stage=model.stage
        root,session=stage.GetRootLayer(),stage.GetSessionLayer()
        source_hash=hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()
        original_session=session.ExportToString(); edit_target=stage.GetEditTarget()
        model.viewSettings.showHUD=False; model.viewSettings.showBBoxes=False
        model.viewSettings.cameraPrim=stage.GetPrimAtPath("/Camera")
        self.controller._mainWindow.resize(1600,1000); self.view.setFixedSize(1280,720)
        self.udim=[]; self.normal_comparisons=[]; normal_udim=[]
        yield
        stage.SetEditTarget(session)
        try:
            for mode,samples in (("realtime",0),("pathtraced",64)):
                if mode=="pathtraced": self.launcher.select_mode(mode)
                rgb=yield from self.image(mode+"-baseline",samples)
                self.check_udim(rgb,mode+"-baseline")
                self.strengths(1.0)
                meshes=self.stats()["submittedMeshes"]
                slope=yield from self.image(mode+"-normal-one",samples)
                if self.stats()["submittedMeshes"]!=meshes: raise AssertionError("Normal strength resubmitted geometry")
                self.check_normals(slope,mode+"-strength-one")
                self.strengths(0.0)
                flat=yield from self.image(mode+"-normal-zero",samples)
                for index in (1002,1011,1012):
                    u,v=(index-1001)%10,(index-1001)//10
                    point=(290+100*u,0,160+100*v)
                    delta=float(np.mean(np.abs(self.patch(slope,point)-self.patch(flat,point))))
                    normal_udim.append(dict(mode=mode,tile=index,difference=delta))
                    if delta<1: raise AssertionError(f"UDIM normal tile {index} has no shading effect: {delta}")
                self.strengths(0.5)
                meshes=self.stats()["submittedMeshes"]
                yield from self.image(mode+"-normal-half",samples)
                if self.stats()["submittedMeshes"]!=meshes: raise AssertionError("Normal strength resubmitted geometry")
                self.references(0.5)
                half=yield from self.image(mode+"-geometry-half",samples)
                self.check_normals(half,mode+"-strength-half")
                self.references(1); self.strengths(0.7)
                yield from self.image(mode+"-restored",samples)
                self.override("UDIM",self.texture("UDIM_Swap")["default"])
                meshes=self.stats()["submittedMeshes"]
                changed=yield from self.image(mode+"-udim-swap",samples)
                self.check_udim(changed,mode+"-swap",True)
                if self.stats()["submittedMeshes"]!=meshes: raise AssertionError("UDIM texture swap resubmitted geometry")
                self.override("UDIM",self.texture("UDIM")["default"])
                yield from self.image(mode+"-udim-restored",samples)
                if mode=="realtime":
                    self.strengths(1)
                    self.override("SS_Normal",self.texture("Flat_Normal")["default"])
                    changed=yield from self.image("normal-flat-texture")
                    if np.mean(np.abs(self.patch(changed,(-70,0,130))-self.patch(flat,(-70,0,130))))>2:
                        raise AssertionError("Cooked normal texture override did not flatten the surface")
                    self.override("SS_Normal",self.texture("SS_Normal")["default"]); self.strengths(0.7)
                    yield from self.image("normal-texture-restored")
            self.launcher.select_mode("realtime")
            baseline=yield from self.image("sampler-contract-baseline")
            api=MaterialApi(self.launcher.runtime); handle=self.stats()["sessionHandle"]
            entry=self.launcher.materialx_library.bound_entries["/Looks/SS_Normal"]
            scale=next(p["parameter"] for p in entry["usd_bindings"] if p["attribute"].endswith(".inputs:scale"))
            texture=self.texture("SS_Normal")
            rejected=api.edit(handle,"/Looks/SS_Normal",[(scale,0.0),(texture["name"],self.texture("UDIM")["default"])],check=False)
            rejected_vt=api.edit(handle,"/Looks/SS_Normal",[(texture["name"],self.texture("UDIM_Normal")["default"])],check=False)
            if rejected!=-1 or rejected_vt!=-1: raise AssertionError("Incompatible texture sampler kinds were accepted")
            submitted=self.stats()["submittedMaterials"]
            model.currentFrame=Usd.TimeCode(1)
            atomic=yield from self.image("sampler-invalid-atomic")
            if self.stats()["submittedMaterials"]!=submitted or np.mean(np.abs(baseline.astype(float)-atomic))>1:
                raise AssertionError("Invalid texture batch partially changed the normal strength")
            self.switch("HdStormRendererPlugin")
            for _ in range(4): yield
            self.switch(PLUGIN)
            yield from self.image("textures-recreated",previous=0)
        finally:
            session.ImportFromString(original_session); stage.SetEditTarget(edit_target)
        if hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()!=source_hash:
            raise AssertionError("Texture check modified its USD source")
        modules=self.launcher.runtime.module_handles()
        if not all(modules.values()): raise AssertionError("UE and Hydra were not loaded in one process")
        self.launcher.test_result=dict(frames=self.frames,udim_checks=self.udim,normal_comparisons=self.normal_comparisons,
            udim_normal_checks=normal_udim,source_unchanged=True,same_process=True,module_handles=modules,
            sampler_mismatch_rejected=True,atomic_parameter_updates=True,normal_and_udim_verified=True,
            textures={p["path"]:self.texture(p["path"].split("/")[-1]) for p in self.launcher.materialx_library.bindings if p["path"].split("/")[-1] in ("UDIM","UDIM_Normal","SS_Normal","OP_Normal","Flat_Normal","UDIM_Swap")})
