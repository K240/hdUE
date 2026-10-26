import html
import json
import sys
from pathlib import Path

root=Path(sys.argv[1]).resolve()
result=json.loads((root/'verified.json').read_text(encoding='utf-8-sig'))
cards=[]
for frame in result['frames']:
    label=html.escape(frame['label'])
    cards.append(f'<figure><a href="{label}.png"><img src="{label}.png"></a><figcaption>{label} · '
                 f'{frame["nativeActors"]} loaded Actors / {frame["nativeMeshes"]} Meshes · '
                 f'{frame["completedSamples"]} samples</figcaption></figure>')
page='''<!doctype html><meta charset="utf-8"><title>hdUE native project asset review</title>
<style>body{background:#181a1e;color:#e8e9ed;font:16px system-ui;margin:32px}main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}figure{margin:0}img{width:100%;border-radius:6px}figcaption{padding:10px}p{color:#c8c8cc}</style>
<h1>hdUE native asset review</h1><p>Cooked Level, StaticMesh and Actor Blueprint rendering in Lumen and Path Tracer.
Consult the supplied catalog and verification results for unsupported classes or omitted actors.</p><main>'''+''.join(cards)+'</main>'
static=root.parent/'static-camera-final'
if not (static/'verified.json').is_file(): static=root.parent/'static-final'
if (static/'verified.json').is_file():
    page+='<h2>Direct StaticMesh reference · original pivot and material</h2><main>'
    for name in ('01-auto-realtime','12-pathtraced'):
        page+=f'<figure><a href="../{static.name}/{name}.png"><img src="../{static.name}/{name}.png"></a><figcaption>StaticMesh · {name}</figcaption></figure>'
    page+='</main>'
(root/'gallery.html').write_text(page,encoding='utf-8')
