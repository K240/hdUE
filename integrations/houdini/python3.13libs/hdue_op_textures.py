"""Resolve Houdini COP images without sending Houdini objects across the UE ABI."""
import hashlib
import re
import tempfile
from pathlib import Path

import hou
from pxr import Sdf, Usd, UsdShade, UsdLux

_cache = None


def _source(uri, validate=False):
    match = re.fullmatch(r'op:(/[^\[\]<>]+?)/?(?:\[(\d+)\])?', uri)
    if not match:
        raise ValueError('Expected an absolute COP path, optionally with [output]: ' + uri)
    node = hou.node(match[1])
    if not isinstance(node, (hou.CopNode, hou.Cop2Node)):
        raise ValueError('Texture op: path is not a COP/Copernicus node: ' + uri)
    output = int(match[2] or 0)
    # Querying a Copernicus output's data type can itself evaluate the node.
    # Do that only when exporting, after checking its dirty state.
    if validate and isinstance(node, hou.CopNode):
        if output >= len(node.outputDataTypes()) or node.outputDataTypes()[output] not in ('Mono', 'UV', 'RGB', 'RGBA'):
            raise ValueError('Texture op: output must be a 2D image: ' + uri)
    elif validate and isinstance(node, hou.Cop2Node) and output:
        raise ValueError('Legacy COP textures support the color plane at output 0: ' + uri)
    return node, output


class Cache:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hdue-cop-')
        self.entries = {}
        self.bindings = {}
        self.exporter = None

    def close(self):
        if self.exporter:
            try: self.exporter.destroy()
            except hou.ObjectWasDeleted: pass
            self.exporter = None
        self.entries.clear()
        self.bindings.clear()
        self.directory.cleanup()

    @staticmethod
    def stamp(node):
        # needsToCook includes changes to upstream inputs. cookCount also catches
        # changes already evaluated by another viewport before our polling tick.
        return (node.sessionId(), node.cookCount(),
                hou.frame() if node.isTimeDependent(for_last_cook=True) else None)

    def resolve(self, uri, raw=False):
        if uri.startswith('opdef:'):
            from houdini_resources import embedded_image
            entry=embedded_image(uri,self.directory.name)
            # Raw/color interpretation does not alter embedded source bytes.
            for interpretation in (False,True):
                self.entries[(uri,interpretation)]=dict(stamp=entry['sha256'],path=entry['path'])
            return entry['path']
        node, output = _source(uri)
        key = (uri, raw)
        previous = self.entries.get(key)
        if previous and not node.needsToCook() and previous['stamp'] == self.stamp(node):
            return previous['path']
        _source(uri, validate=True)
        root = Path(self.directory.name)
        pending = root / ('pending-' + hashlib.sha256(repr(key).encode()).hexdigest() + '.exr')
        if isinstance(node, hou.Cop2Node):
            # Limit the save to this frame; the default saves the whole sequence.
            frame=hou.frame()
            if frame!=int(frame):
                raise ValueError('Legacy COP image export requires an integer frame: ' + uri)
            node.saveImage(str(pending), (int(frame), int(frame)))
        else:
            # The Image ROP understands Copernicus GPU layers, including selected
            # output ports, without a Python loop over millions of pixels.
            if self.exporter is None:
                self.exporter = hou.node('/out').createNode('image', '__hdue_texture_export', run_init_scripts=False)
            rop = self.exporter
            rop.parm('coppath').set(node.path())
            rop.parm('copoutput').set(str(pending))
            rop.parm('savebackground').set(0)
            rop.parm('setudim').set('off')
            rop.parm('setprecision').set(1)
            rop.parm('precision').set('b32')
            rop.parm('outputaovs').set(1)
            rop.parm('enabled1').set(1)
            rop.parm('aov1').set('C')
            rop.parm('useport1').set(1)
            rop.parm('port1').set(output)
            rop.parm('colorconversion').set('raw' if raw else 'ocio')
            if not raw:
                # UE uses linear Rec.709. COP scene_linear can be ACEScg.
                rop.parm('ociocolorspace').set('Linear Rec.709 (sRGB)')
            rop.render(frame_range=(hou.frame(), hou.frame()))
        if not pending.is_file():
            raise RuntimeError('COP texture did not produce an image: ' + uri)
        # Immutable content paths invalidate Hydra/UE texture caches and let an
        # in-flight render continue reading its previous image safely.
        digest = hashlib.sha256(pending.read_bytes()).hexdigest()
        result = root / (digest + '.exr')
        if result.exists():
            pending.unlink()
        else:
            pending.replace(result)
        self.entries[key] = dict(stamp=self.stamp(node), path=str(result))
        return str(result)

    def dirty(self, bridge):
        for uri in self.bindings.get(bridge.path(), ()):
            try:
                if uri.startswith('opdef:'):
                    stamp=hashlib.sha256(hou.readBinaryFile(uri)).hexdigest()
                    if not any((uri,raw) in self.entries for raw in (False,True)):
                        return True
                    if any(self.entries[(uri,raw)]['stamp']!=stamp for raw in (False,True) if (uri,raw) in self.entries):
                        return True
                    continue
                node, _ = _source(uri)
                if not any((uri, raw) in self.entries for raw in (False, True)):
                    return True
                for raw in (False, True):
                    entry = self.entries.get((uri, raw))
                    if entry and (node.needsToCook() or entry['stamp'] != self.stamp(node)):
                        return True
            except (ValueError, hou.Error):
                return True
        return False


def cache():
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def close():
    global _cache
    if _cache:
        _cache.close()
        _cache = None


def resolve_stage(stage, bridge):
    """Author only into the caller's editable LOP layer; keep upstream op: URIs."""
    references = set()
    for prim in list(stage.Traverse()):
        shader = UsdShade.ConnectableAPI(prim)
        if not (prim.IsA(UsdShade.Shader) or prim.HasAPI(UsdLux.LightAPI)):
            continue
        for input_ in shader.GetInputs():
            attr = input_.GetAttr()
            value = attr.Get()
            uri = value.path if isinstance(value, Sdf.AssetPath) else value if isinstance(value, str) else ''
            previous = attr.GetCustomDataByKey('hdueOpTexture')
            if previous and uri == previous['resolved']:
                uri = previous['uri']
            if not uri.startswith(('op:','opdef:')):
                continue
            references.add(uri)
            cache().bindings[bridge.path()] = references
            color_space = shader.GetInput('sourceColorSpace')
            raw = bool(attr.GetColorSpace() == 'raw' or (color_space and color_space.Get() == 'raw'))
            entry = cache().entries.get((uri, raw))
            if not entry:
                raise ValueError('COP texture is awaiting preparation outside the LOP cook: ' + uri)
            path = entry['path']
            attr.Set(Sdf.AssetPath(path) if isinstance(value, Sdf.AssetPath) else path)
            attr.SetCustomDataByKey('hdueOpTexture', dict(uri=uri, resolved=path))
    cache().bindings[bridge.path()] = references


def prepare_stage(stage):
    """Export on the host thread, outside any LOP cook/Usd stage write lock."""
    for prim in Usd.PrimRange.Stage(stage,Usd.TraverseInstanceProxies()):
        shader = UsdShade.ConnectableAPI(prim)
        if not (prim.IsA(UsdShade.Shader) or prim.HasAPI(UsdLux.LightAPI)):
            continue
        for input_ in shader.GetInputs():
            attr = input_.GetAttr()
            value = attr.Get()
            uri = value.path if isinstance(value, Sdf.AssetPath) else value if isinstance(value, str) else ''
            previous = attr.GetCustomDataByKey('hdueOpTexture')
            if previous and uri == previous['resolved']:
                uri = previous['uri']
            if uri.startswith(('op:','opdef:')):
                color_space = shader.GetInput('sourceColorSpace')
                raw = bool(attr.GetColorSpace() == 'raw' or (color_space and color_space.Get() == 'raw'))
                cache().resolve(uri, raw)


def refresh(bridge):
    prepare_stage(bridge.inputs()[0].stage())
    bridge.cook(force=True)


def preview_fallbacks(stage):
    """Basic MaterialX base-color image fallback for the generic PBR renderer.

    Retain the authored MaterialX terminal. Full MaterialX graphs still use the
    existing prepared/cooked MaterialX route, not this small viewport fallback.
    """
    for prim in list(stage.Traverse()):
        material = UsdShade.Material(prim)
        if not material or material.GetSurfaceOutput().HasConnectedSource():
            continue
        output = material.GetSurfaceOutput('mtlx')
        source = output.GetConnectedSource() if output else None
        if not source:
            continue
        surface = UsdShade.Shader(source[0].GetPrim())
        identifier = surface.GetIdAttr().Get()
        if identifier not in ('ND_standard_surface_surfaceshader', 'ND_open_pbr_surface_surfaceshader'):
            continue
        base = surface.GetInput('base_color')
        connection = base.GetConnectedSource() if base else None
        if not connection:
            continue
        image = UsdShade.Shader(connection[0].GetPrim())
        if image.GetIdAttr().Get() not in ('ND_image_color3', 'ND_image_color4'):
            continue
        file = image.GetInput('file')
        if not file or not file.Get() or not file.GetAttr().GetCustomDataByKey('hdueOpTexture'):
            continue
        path = material.GetPath().AppendChild('__HdUECopPreview')
        if stage.GetPrimAtPath(path):
            raise ValueError('Reserved hdUE COP preview path is already in use: ' + str(path))
        preview = UsdShade.Shader.Define(stage, path)
        preview.CreateIdAttr('UsdPreviewSurface')
        preview.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set((1, 1, 1))
        for source_name, target_name, fallback in (
                ('specular_roughness', 'roughness', .5),
                ('metalness' if identifier.startswith('ND_standard') else 'base_metalness', 'metallic', 0.)):
            value = surface.GetInput(source_name)
            if value and value.HasConnectedSource():
                raise ValueError('COP preview requires constant ' + source_name + ' at ' + str(surface.GetPath()))
            preview.CreateInput(target_name, Sdf.ValueTypeNames.Float).Set(value.Get() if value and value.Get() is not None else fallback)
        texture = UsdShade.Shader.Define(stage, path.AppendChild('Image'))
        texture.CreateIdAttr('UsdUVTexture')
        texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(file.Get())
        texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('raw')
        texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        preview.GetInput('diffuseColor').ConnectToSource(texture.ConnectableAPI(), 'rgb')
        material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), 'surface')
