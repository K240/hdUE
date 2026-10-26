"""Index native renderer screenshots without altering their pixels."""
import argparse
from html import escape
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("directory",type=Path)
    folder=parser.parse_args().directory
    data=json.loads((folder/"verified.json").read_text(encoding="utf-8"))
    if not data.get("opacity_verified") or not data.get("passed"): raise ValueError("A successful opacity run is required")
    def picture(name,caption):
        if not (folder/name).is_file(): raise FileNotFoundError(folder/name)
        return f'<figure><a href="{escape(name)}"><img loading="lazy" src="{escape(name)}"></a><figcaption>{escape(caption)}</figcaption></figure>'
    rows=['<h1>Review scene — displayOpacity</h1>',
        '<p>13 meshes · Lumen / Path Tracer 64 samples · opacity 0.7 / 1 / 0 · original USD unchanged</p>',
        '<div class="pair">'+picture('realtime-scene-07.png','Lumen · opacity 0.7')+picture('pathtraced-scene-07.png','Path Tracer · opacity 0.7 · 64 samples')+'</div>']
    for index,path in enumerate(data['targets']):
        rows.append(f'<h2>{index+1}. {escape(path)}</h2><div class="pair">')
        for mode,title in (("realtime","Lumen"),("pathtraced","Path Tracer · 64 samples")):
            rows.append('<section>'+picture(f'{mode}-{index:02}-07.png',title+' · opacity 0.7')+
                f'<p>Controls: <a href="{mode}-{index:02}-opaque.png">opaque (1)</a> · <a href="{mode}-{index:02}-clear.png">clear (0)</a></p></section>')
        rows.append('</div>')
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Review scene opacity verification</title>
<style>body{background:#17191d;color:#ddd;font:16px system-ui;margin:24px auto;max-width:1400px;padding:0 20px}
h2{font-size:16px;overflow-wrap:anywhere;margin-top:36px}a{color:#8ec7ff}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}
figure{margin:0}img{width:100%;height:auto;display:block}figcaption,p{margin:10px 0}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>
'''+''.join(rows)+'</html>'
    (folder/'gallery.html').write_text(html,encoding='utf-8')
    print(folder/'gallery.html')
if __name__=='__main__': main()
