# /// script
# requires-python = ">=3.11"
# ///
"""Export checked prepared-material settings for the standalone Hydra host."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from pxr import Usd
from materialx_controls import MaterialXLibrary


def export(stage, library, time, substrate=None):
    class Signal:
        def connect(self, callback): pass
    class View:
        _renderer = object()
        def __init__(self): self.settings = {}
        def GetRendererSetting(self, name): return self.settings.get(name)
        def SetRendererSetting(self, name, value): self.settings[name] = value
    view = View()
    model = SimpleNamespace(stage=stage, currentFrame=time, signalStageReplaced=Signal(), signalPrimsChanged=Signal())
    controller = SimpleNamespace(_stageView=view, _dataModel=model)
    prepared = MaterialXLibrary(library, controller) if library else None
    if prepared and not prepared.sync(): raise ValueError(prepared.preparation_error)
    view.settings["wetProfiles"] = {
        path: entry.get("surface_effects", {}).get("wet", {}) for path, entry in prepared.bound_entries.items()} if prepared else {}
    if substrate:
        from substrate_library import SubstrateLibrary
        SubstrateLibrary(substrate).sync(stage,time,prepared.bound_entries if prepared else {},view.settings)
        view.settings['substratePrepared']=True
    return dict(version=1, settings=view.settings)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scene", type=Path)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--substrate-library", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--time", type=float, default=0)
    args = parser.parse_args()
    result = export(Usd.Stage.Open(str(args.scene.resolve())), args.library, Usd.TimeCode(args.time),args.substrate_library)
    args.output.write_text(json.dumps(result, default=list, indent=2), encoding="utf-8")
