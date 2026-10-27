"""Reuse the checked cooked MaterialX registry in a Solaris viewport."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import hou
from materialx_controls import MaterialXLibrary


class Signal:
    def connect(self, callback):
        pass


class PreparedMaterials:
    def __init__(self):
        self.model=SimpleNamespace(stage=None,currentFrame=None,signalStageReplaced=Signal(),signalPrimsChanged=Signal())
        self._renderer=None
        self.settings={}
        self.file=None
        self.library=None
        self.embedded={}
        self.applied=None

    def GetRendererSetting(self,name):
        return self.settings.get(name)

    def SetRendererSetting(self,name,value):
        self.settings[name]=value

    def sync(self,stage,time,file,session,api):
        identity=None
        if file:
            path=Path(hou.expandString(file)).resolve()
            identity=(str(path),path.stat().st_mtime_ns)
        if identity!=self.file:
            self.file=None
            self.library=None
            self.embedded={}
            self.settings={}
            if identity:
                controller=SimpleNamespace(_dataModel=self.model,_stageView=self)
                self.library=MaterialXLibrary(path,controller)
                # Original embedded resources must still match the cooked images.
                import json
                data=json.loads(path.read_text(encoding='utf-8-sig'))
                self.embedded=data.get('embedded_textures',{})
            self.file=identity
        for uri,entry in self.embedded.items():
            if hashlib.sha256(hou.readBinaryFile(uri)).hexdigest()!=entry['sha256']:
                raise ValueError('Embedded texture changed; prepare/import/cook again: '+uri)
        self.model.stage=stage; self.model.currentFrame=time
        self._renderer=session
        if self.library:
            self.library.invalidate()
            if not self.library.sync(): raise ValueError(self.library.preparation_error)
        assets=self.settings.get('nativeMaterialAssets',{})
        overrides=self.settings.get('nativeMaterialParameters',{})
        current=(session,assets,overrides)
        if current!=self.applied and api.materials(session,assets,overrides):
            self.applied=current
            return True
        return False
