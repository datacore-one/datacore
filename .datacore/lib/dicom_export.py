"""Export a DICOM disc (e.g. a hospital CD made for a Windows viewer) to PNGs
plus an offline HTML viewer that opens in any browser.

Usage: python3 dicom_export.py <disc_or_DICOM_dir> <out_dir>
Needs: pydicom, numpy, pillow. Output stays local; it contains patient data.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image


def to_8bit(ds):
    px = ds.pixel_array.astype(np.float32)
    px = px * float(ds.get("RescaleSlope", 1)) + float(ds.get("RescaleIntercept", 0))
    wc, ww = ds.get("WindowCenter"), ds.get("WindowWidth")
    if wc is not None and ww is not None:
        wc = float(wc[0] if isinstance(wc, pydicom.multival.MultiValue) else wc)
        ww = float(ww[0] if isinstance(ww, pydicom.multival.MultiValue) else ww)
        lo, hi = wc - ww / 2, wc + ww / 2
    else:
        lo, hi = np.percentile(px, (0.5, 99.5))
    px = np.clip((px - lo) / max(hi - lo, 1e-6), 0, 1) * 255
    if ds.get("PhotometricInterpretation") == "MONOCHROME1":
        px = 255 - px
    return Image.fromarray(px.astype(np.uint8))


def export(src: Path, out: Path):
    files = [p for p in src.rglob("*") if p.is_file() and p.name.upper() != "DICOMDIR"]
    series = {}
    for p in files:
        try:
            ds = pydicom.dcmread(p)
            ds.pixel_array  # skip non-image objects
        except Exception:
            continue
        series.setdefault(ds.SeriesInstanceUID, []).append((int(ds.get("InstanceNumber", 0)), p, ds))

    index = []
    for n, (uid, items) in enumerate(sorted(series.items(), key=lambda kv: (kv[1][0][2].get("StudyDescription", ""), int(kv[1][0][2].get("SeriesNumber", 0))))):
        items.sort(key=lambda t: t[0])
        ds0 = items[0][2]
        name = f"{n + 1:02d}_{ds0.get('SeriesDescription', 'series').replace(' ', '_').replace('/', '-')}"
        sdir = out / name
        sdir.mkdir(parents=True, exist_ok=True)
        imgs = []
        for i, (_, _, ds) in enumerate(items):
            img = to_8bit(ds)
            fn = f"{i + 1:03d}.png"
            img.save(sdir / fn)
            imgs.append(f"{name}/{fn}")
        # contact sheet
        thumbs = [Image.open(out / f).resize((160, 160)) for f in imgs]
        cols = 8
        sheet = Image.new("L", (cols * 160, ((len(thumbs) + cols - 1) // cols) * 160))
        for i, t in enumerate(thumbs):
            sheet.paste(t, ((i % cols) * 160, (i // cols) * 160))
        sheet.save(out / f"{name}_overview.png")
        index.append({
            "name": name,
            "study": str(ds0.get("StudyDescription", "")),
            "series": str(ds0.get("SeriesDescription", "")),
            "spacing": [float(x) for x in ds0.get("PixelSpacing", [0, 0])],
            "thickness": float(ds0.get("SliceThickness", 0) or 0),
            "images": imgs,
        })
        print(f"{name}: {len(imgs)} slices")

    (out / "index.html").write_text(VIEWER.replace("__DATA__", json.dumps(index).replace("</", "<\\/")))
    print(f"Viewer: {out / 'index.html'}")


VIEWER = """<!doctype html><meta charset=utf-8><title>MRI Viewer</title>
<style>
body{margin:0;background:#111;color:#ddd;font:14px system-ui;display:flex;height:100vh}
#side{width:280px;overflow:auto;border-right:1px solid #333;padding:8px}
#side div{padding:6px;cursor:pointer;border-radius:4px}#side div.on{background:#2a4a7a}
#side small{color:#888;display:block}
#main{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}
img{max-width:100%;max-height:82vh;image-rendering:auto}
#ctl{padding:8px;display:flex;gap:16px;align-items:center}
</style>
<div id=side></div><div id=main><img id=im><div id=ctl>
<span id=lbl></span><input type=range id=sl min=0 style="width:300px">
<label>Brightness <input type=range id=br min=50 max=250 value=100></label>
<label>Contrast <input type=range id=co min=50 max=300 value=100></label>
</div><small>Scroll or arrow keys = slices · click a series on the left</small></div>
<script>
const D=__DATA__;let s=0,i=0;
const side=document.getElementById('side'),im=document.getElementById('im'),sl=document.getElementById('sl');
D.forEach((x,k)=>{const d=document.createElement('div');d.innerHTML=`${x.series}<small>${x.study} · ${x.images.length} slices</small>`;d.onclick=()=>{s=k;i=Math.floor(x.images.length/2);draw()};side.appendChild(d)});
function draw(){const x=D[s];i=Math.max(0,Math.min(i,x.images.length-1));im.src=x.images[i];sl.max=x.images.length-1;sl.value=i;
document.getElementById('lbl').textContent=`${i+1} / ${x.images.length}`;[...side.children].forEach((c,k)=>c.className=k==s?'on':'');
im.style.filter=`brightness(${br.value}%) contrast(${co.value}%)`}
sl.oninput=()=>{i=+sl.value;draw()};br.oninput=co.oninput=draw;
addEventListener('wheel',e=>{i+=e.deltaY>0?1:-1;draw()});
addEventListener('keydown',e=>{if(e.key=='ArrowDown'||e.key=='ArrowRight')i++;if(e.key=='ArrowUp'||e.key=='ArrowLeft')i--;draw()});
i=Math.floor(D[0].images.length/2);draw();
</script>"""

if __name__ == "__main__":
    export(Path(sys.argv[1]), Path(sys.argv[2]))
