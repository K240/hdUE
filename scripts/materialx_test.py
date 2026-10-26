"""Real MaterialX/Substrate rendering, editable inputs and C ABI contract gates."""
import ctypes as C
import hashlib
import json
import threading
import time
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdShade
from material_api import MaterialApi, Api, RendererInfo
from usdview_test import SmokeTest, PLUGIN


class MaterialXTest(SmokeTest):
    def __init__(self,*args):
        super().__init__(*args)
        self.deadline=time.monotonic()+600

    def shader_for(self,name):
        entry=self.launcher.materialx_library.bound_entries["/Looks/"+name]
        path=entry.get("usd_shader","/Looks/"+name+"/MaterialX")
        return UsdShade.Shader(self.controller._dataModel.stage.GetPrimAtPath(path))

    def texture_for(self,name):
        entry=self.launcher.materialx_library.bound_entries["/Looks/"+name]
        if "usd_bindings" in entry:
            fields=[p for p in entry["usd_bindings"] if p["type"]=="texture"]
            if len(fields)!=1:
                raise AssertionError(f"Fixture must have one texture: {name}")
            field=fields[0]
            path=Sdf.Path(field["attribute"])
            shader=UsdShade.Shader(self.controller._dataModel.stage.GetPrimAtPath(path.GetPrimPath()))
            port=shader.CreateInput(path.name.removeprefix("inputs:"),Sdf.ValueTypeNames.String)
        else:
            field=entry["bindings"]["hdue:base_color_texture"]
            port=self.shader_for(name).GetInput("hdue:base_color_texture")
        return port,port.Get() or field["default"]

    def image(self,label,previous=0,samples=0):
        rgb=yield from self.wait_frame(label,previous,samples,require_red=False)
        stats=self.stats()
        if not self.view._renderer.InvokeRendererCommand("hdue:sampleMemory",{}):
            raise AssertionError("GPU memory sampling is unavailable")
        stats=self.stats()
        if not stats.get("gpuMemoryAvailable") or not stats.get("gpuLocalMemoryBytes"):
            raise AssertionError("GPU memory measurement failed")
        self.frames[-1].update(gpuLocalMemoryBytes=stats["gpuLocalMemoryBytes"],gpuMemoryAvailable=True)
        if not stats.get("substrateEnabled") or not stats.get("adaptiveGBuffer") or stats.get("meshes") != 21:
            raise AssertionError(f"Incorrect Substrate/scene configuration: {stats}")
        if rgb.mean()<3:
            raise AssertionError(f"{label}: MaterialX scene is black")
        return rgb

    def compare_effects(self, stage, reference, mode, comparisons, samples=0):
        for family in ("SS","OP"):
            for column,kind in enumerate(("Metal","Coat","Anisotropy","Glass","Subsurface")):
                name=family+"_"+kind
                field={
                    "Metal":"metalness" if family=="SS" else "base_metalness",
                    "Coat":"coat" if family=="SS" else "coat_weight",
                    "Anisotropy":"specular_anisotropy" if family=="SS" else "specular_roughness_anisotropy",
                    "Glass":"transmission_color",
                    "Subsurface":"subsurface" if family=="SS" else "subsurface_weight"}[kind]
                shader=self.shader_for(name)
                source_input=shader.GetInput(field)
                old=source_input.Get()
                before_meshes=self.stats()["submittedMeshes"]
                source_input.Set(Gf.Vec3f(0.95,0.03,0.01) if kind=="Glass" else 0.05)
                changed=yield from self.image(mode+"-effect-"+name,self.frames[-1]["generation"],samples)
                camera=self.view.resolveCamera()[0]
                matrix=camera.frustum.ComputeViewMatrix()*camera.frustum.ComputeProjectionMatrix()
                center=Gf.Vec3d((column-2)*140,0,65+(0 if family=="SS" else 1)*145)
                def project(point):
                    clip=Gf.Vec4d(point[0],point[1],point[2],1)*matrix
                    return np.array(((clip[0]/clip[3]+1)*changed.shape[1]*0.5,(1-clip[1]/clip[3])*changed.shape[0]*0.5))
                xy=project(center)
                edge=project(center+camera.transform.TransformDir(Gf.Vec3d(35,0,0)))
                radius=max(5,int(np.linalg.norm(edge-xy)))
                x,y=map(int,xy)
                a=reference[max(0,y-radius):y+radius,max(0,x-radius):x+radius].astype(float)
                b=changed[max(0,y-radius):y+radius,max(0,x-radius):x+radius].astype(float)
                delta=float(np.mean(np.abs(a-b))) if a.size else 0
                comparisons.append(dict(mode=mode,material=name,input=field,roi_mean_absolute_difference=delta))
                (self.output/"effect-comparisons.json").write_text(json.dumps(comparisons,indent=2))
                if self.stats()["submittedMeshes"]!=before_meshes:
                    raise AssertionError(f"Material editing resubmitted geometry: {comparisons[-1]}")
                source_input.Set(old)
                reference=yield from self.image(mode+"-restore-"+name,self.frames[-1]["generation"],samples)
        missing=[p for p in comparisons if p["mode"]==mode and p["roi_mean_absolute_difference"]<0.2]
        if missing:
            raise AssertionError(f"MaterialX effects are not visible: {missing}")

    def run(self):
        model=self.controller._dataModel
        stage=model.stage
        root,session=stage.GetRootLayer(),stage.GetSessionLayer()
        source_hash=hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()
        original_session=session.ExportToString()
        edit_target=stage.GetEditTarget()
        model.viewSettings.showHUD=False
        model.viewSettings.showBBoxes=False
        model.viewSettings.cameraPrim=stage.GetPrimAtPath("/Camera")
        self.controller._mainWindow.resize(1600,1000)
        self.view.setFixedSize(1280,720)
        yield
        original=yield from self.image("01-materialx-realtime")
        library=self.launcher.materialx_library
        if not library or len(library.bindings)!=10:
            raise AssertionError("Not all ten MaterialX materials were bound")
        api=MaterialApi(self.launcher.runtime)
        renderer=api.renderer_info()
        if not renderer["substrate_enabled"] or not renderer["adaptive_gbuffer"]:
            raise AssertionError("Material extension did not report Substrate Adaptive")
        bad=Api()
        if api.get_api(999,C.sizeof(bad),C.byref(bad))!=-2 or api.get_api(1,C.sizeof(bad)-1,C.byref(bad))!=-6 or bad.set_parameters:
            raise AssertionError("Material extension version/capacity contract failed")
        worker_result=[]
        def worker():
            info=RendererInfo(struct_size=C.sizeof(RendererInfo))
            worker_result.append(api.get_info(C.byref(info)))
        thread=threading.Thread(target=worker); thread.start(); thread.join()
        if worker_result!=[-3]:
            raise AssertionError("Material extension accepted a foreign thread")
        handle=self.stats()["sessionHandle"]
        entry=library.bound_entries["/Looks/SS_Metal"]
        if "usd_bindings" in entry:
            parameter_names={p["attribute"].split(".inputs:")[-1]:p["parameter"] for p in entry["usd_bindings"]}
        else:
            parameter_names={name:p["parameter"] for name,p in entry["bindings"].items()}
        color_parameter=parameter_names["base_color"]
        rough_parameter=parameter_names["specular_roughness"]
        rejected={
            "wrong_type":api.edit(handle,"/Looks/SS_Metal",[(color_parameter,0.5)],check=False),
            "nonfinite":api.edit(handle,"/Looks/SS_Metal",[(rough_parameter,float("nan"))],check=False),
            "duplicate":api.edit(handle,"/Looks/SS_Metal",[(rough_parameter,0.1),(rough_parameter,0.2)],check=False),
            "invalid_session":api.edit(0,"/Looks/SS_Metal",[],check=False)}
        if any(code!=-1 for code in rejected.values()):
            raise AssertionError(f"Invalid material updates were accepted: {rejected}")
        material_stats={p["path"]:api.material_stats(handle,p["path"]) for p in library.bindings}
        if any(not p["substrate_enabled"] or p["closure_count"]<1 for p in material_stats.values()):
            raise AssertionError(f"Missing compiled Substrate closures: {material_stats}")
        stage.SetEditTarget(session)
        comparisons=[]
        inline_verified=False
        try:
            yield from self.compare_effects(stage,original,"lumen",comparisons)
            if "usd_bindings" in entry:
                # Exercise Material interface forwarding and a value inside a
                # nested NodeGraph through the actual runtime mapping.
                for path in ("/Looks/OP_Metal.inputs:tint","/Looks/OP_Coat.inputs:textureTint"):
                    attr=stage.GetAttributeAtPath(path)
                    old=attr.Get()
                    meshes_before=self.stats()["submittedMeshes"]
                    attr.Set(Gf.Vec3f(0.05,0.7,0.85))
                    changed=yield from self.image("inline-interface-"+str(len(self.frames)),self.frames[-1]["generation"])
                    if np.mean(np.abs(original.astype(float)-changed))<0.05 or self.stats()["submittedMeshes"]!=meshes_before:
                        raise AssertionError(f"Inline MaterialX interface update failed: {path}")
                    attr.Set(old)
                    yield from self.image("inline-restore-"+str(len(self.frames)),self.frames[-1]["generation"])
                identity=self.shader_for("SS_Metal").GetIdAttr()
                old=identity.Get()
                before_block=self.stats()
                identity.Set("ND_unsupported_changed_graph")
                library.invalidate()
                try:
                    self.launcher.sync_stage()
                    self.view.updateGL()
                    for _ in range(4):
                        yield
                    blocked=self.stats()
                    if (self.launcher.failure or not blocked.get("materialGraphBlocked") or
                        blocked["sceneSubmissions"]!=before_block["sceneSubmissions"] or
                        "graph/static input changed" not in (library.preparation_error or "")):
                        raise AssertionError("Changed inline graph closed the viewer or used stale cooked shaders")
                finally:
                    identity.Set(old); library.invalidate()
                    self.launcher.sync_stage()
                if self.stats().get("materialGraphBlocked"):
                    raise AssertionError("Undo did not resume inline MaterialX rendering")
                inline_verified=True
            metal=self.shader_for("SS_Metal")
            color,rough=metal.GetInput("base_color"),metal.GetInput("specular_roughness")
            old_color,old_rough=color.Get(),rough.Get()
            meshes=self.stats()["submittedMeshes"]
            previous=self.frames[-1]["generation"]
            color.Set(Gf.Vec3f(0.02,0.7,0.08))
            edited=yield from self.image("02-edited-base-color",previous)
            if np.mean(np.abs(original.astype(float)-edited))<0.05:
                raise AssertionError("MaterialX base color edit did not change the image")
            previous=self.frames[-1]["generation"]
            rough.Set(0.85)
            rough_image=yield from self.image("03-edited-roughness",previous)
            if np.mean(np.abs(edited.astype(float)-rough_image))<0.02:
                raise AssertionError("MaterialX roughness edit did not change the image")
            texture,old_texture=self.texture_for("SS_Coat")
            _,other=self.texture_for("OP_Coat")
            previous=self.frames[-1]["generation"]
            texture.Set(other)
            textured=yield from self.image("04-edited-texture",previous)
            if np.mean(np.abs(textured.astype(float)-rough_image))<0.05:
                raise AssertionError("MaterialX texture edit did not change the image")
            if self.stats()["submittedMeshes"]!=meshes:
                raise AssertionError("Material editing resubmitted geometry")
            previous=self.frames[-1]["generation"]
            color.Set(old_color); rough.Set(old_rough); texture.Set(old_texture)
            yield from self.image("05-restored-materials",previous)
            self.launcher.select_mode("pathtraced")
            pathtraced=yield from self.image("06-materialx-pathtraced",self.frames[-1]["generation"],64)
            yield from self.compare_effects(stage,pathtraced,"pathtraced",comparisons,64)
            previous=self.frames[-1]["generation"]
            rough.Set(0.08)
            self.view.updateGL()
            reset_seen=False
            while self.stats()["generation"]<=previous:
                yield
            reset_seen=self.stats().get("completedSamples",0)<64
            yield from self.image("07-edited-pathtraced",previous,64)
            if not reset_seen:
                raise AssertionError("Path Tracer edit did not expose an accumulation reset")
            previous=self.frames[-1]["generation"]
            rough.Set(old_rough)
            self.launcher.select_mode("realtime")
            baseline=yield from self.image("08-restored-realtime",previous)
            # A valid update followed by an invalid field must not partially
            # modify the instance. Rerender without reapplying scene materials.
            handle=self.stats()["sessionHandle"]
            code=api.edit(handle,"/Looks/Grey",[("BaseColor",(1,0,1,1)),("missing_parameter",0.5)],check=False)
            if code!=-1:
                raise AssertionError("Unknown material parameter was accepted")
            before_materials=self.stats()["submittedMaterials"]
            previous=self.frames[-1]["generation"]
            model.currentFrame=Usd.TimeCode(1)
            atomic=yield from self.image("09-atomic-invalid-update",previous)
            if self.stats()["submittedMaterials"]!=before_materials or np.mean(np.abs(atomic.astype(float)-baseline))>1.0:
                raise AssertionError("Failed parameter batch changed the material / needed restoration")
            self.switch("HdStormRendererPlugin")
            for _ in range(5):
                yield
            self.switch(PLUGIN)
            yield from self.image("10-recreated-materialx")
        finally:
            session.ImportFromString(original_session)
            stage.SetEditTarget(edit_target)
        if hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()!=source_hash:
            raise AssertionError("MaterialX GUI check modified its USD source")
        modules=self.launcher.runtime.module_handles()
        if not all(modules.values()):
            raise AssertionError("UE and Hydra are not in the same process")
        self.controller._mainWindow.grab().save(str(self.output/"materialx-window.png"))
        self.launcher.test_result=dict(frames=self.frames,materialx_bindings=library.bindings,
            effect_comparisons=comparisons,
            material_stats=material_stats,renderer_info=renderer,source_unchanged=True,
            inline_materialx_verified=inline_verified,
            rejected_parameter_updates=rejected,
            parameter_edits_without_mesh_updates=True,atomic_parameter_updates=True,pathtracer_reset=reset_seen,
            module_handles=modules,same_process=True)
