"""Extract opdef: images with Houdini's resolver; invoke through uv and hython."""
import argparse
import json
from pathlib import Path
from pxr import Sdf, Usd, UsdShade, UsdLux
from houdini_resources import embedded_image


def resolve(source, output):
    if output.exists():
        raise FileExistsError('Choose a fresh embedded texture directory: ' + str(output))
    stage = Usd.Stage.Open(str(source.resolve()))
    if not stage:
        raise ValueError('Cannot open USD: ' + str(source))
    entries = {}
    for prim in stage.Traverse():
        shader = UsdShade.ConnectableAPI(prim)
        if not (prim.IsA(UsdShade.Shader) or prim.HasAPI(UsdLux.LightAPI)):
            continue
        for input_ in shader.GetInputs():
            value = input_.Get()
            uri = value.path if isinstance(value, Sdf.AssetPath) else value if isinstance(value, str) else ''
            if uri.startswith('opdef:') and uri not in entries:
                entries[uri] = embedded_image(uri, output / 'images')
    output.mkdir(parents=True, exist_ok=True)
    file = output / 'textures.json'
    file.write_text(json.dumps({'version': 1, 'textures': entries}, indent=2), encoding='utf-8')
    print(str(file.resolve()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    resolve(args.source, args.output)
