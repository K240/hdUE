"""Index original renderer screenshots and the optical controls used for review."""
import argparse
from html import escape
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('directory',type=Path)
    folder=parser.parse_args().directory
    data=json.loads((folder/'verified.json').read_text(encoding='utf-8'))
    if not data.get('glass_verified') or not data.get('passed'): raise ValueError('Successful glass evidence is required')
    def picture(name,caption):
        if not (folder/name).is_file(): raise FileNotFoundError(folder/name)
        return f'<figure><a href="{escape(name)}"><img loading="lazy" src="{escape(name)}"></a><figcaption>{escape(caption)}</figcaption></figure>'
    rows=['<h1>Review scene — refractive, absorbing glass</h1>',
        '<p>Four bottles and one measuring cup. Explicit review profile; original USD unchanged. RGB transmittance is specified at a reference distance in centimeters.</p>',
        '<p>Lumen uses screen-space refraction and an authored surface thickness. Path Tracer follows refracted rays through the actual mesh volume (64 samples, 16 bounces).</p>',
        '<div class="pair">'+picture('realtime-scene-glass.png','Lumen')+picture('pathtraced-scene-glass.png','Path Tracer · 64 samples')+'</div>']
    for index,path in enumerate(data['targets']):
        rows.append(f'<h2>{index+1}. {escape(path)}</h2>')
        for label,title in (('glass','Profile'),('ior-one','IOR = 1 (control)'),('green','Green absorption'),('blue','Blue absorption'),('strong-absorption','Stronger absorption (shorter reference distance)')):
            rows.append('<h3>'+title+'</h3><div class="pair">'+picture(f'realtime-{index:02}-{label}.png','Lumen')+
                        picture(f'pathtraced-{index:02}-{label}.png','Path Tracer · 64 samples')+'</div>')
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Review scene glass verification</title>
<style>body{background:#17191d;color:#ddd;font:16px system-ui;margin:24px auto;max-width:1400px;padding:0 20px}a{color:#8ec7ff}
h2{font-size:17px;overflow-wrap:anywhere;margin-top:40px}h3{font-size:15px;font-weight:normal;color:#aaa}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0}img{width:100%;height:auto;display:block}figcaption{margin:8px 0}
@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>'''+''.join(rows)+'</html>'
    (folder/'gallery.html').write_text(html,encoding='utf-8'); print(folder/'gallery.html')
if __name__=='__main__': main()
