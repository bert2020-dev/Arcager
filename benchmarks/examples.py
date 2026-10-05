#!/usr/bin/env python3
"""Deterministic 24-scenario corpus for Arcager benchmarks.

The generator creates temporary example sites rather than shipping large binary fixtures.
Each scenario is deliberately shaped to stress a different part of the packaging model:
text entropy, media entropy, exact duplicates, near-duplicates, dependency graphs,
archives, CSV/JSON/XML data, fonts, recursion, encryption/metadata, and large mixed sites.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
import random
import shutil
import struct
import wave
import zipfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None
    ImageDraw = None

SEED = 4012026
R = random.Random(SEED)


def wtext(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def wbytes(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def deterministic_bytes(length: int, seed: int) -> bytes:
    import hashlib
    out=bytearray()
    counter=0
    key=str(seed).encode('ascii')
    while len(out)<length:
        out.extend(hashlib.blake2b(key+counter.to_bytes(8,'little'), digest_size=64).digest())
        counter += 1
    return bytes(out[:length])


def png(path: Path, size=(768, 512), noisy=False, variant=0, mode="pattern"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        raise RuntimeError("Pillow is required to generate image benchmark examples")
    sx, sy = size
    rr = random.Random(SEED + variant * 17 + (1 if noisy else 0))
    # Keep the fixture generator cheap: build deterministic texture at a small
    # resolution, then scale it. The resulting files still exercise entropy and
    # media handling without making benchmark setup itself a bottleneck.
    tw, th = min(256, sx), min(192, sy)
    raw = bytearray(rr.getrandbits(8) for _ in range(tw * th * 3))
    tex = Image.frombytes("RGB", (tw, th), bytes(raw))
    if noisy:
        im = tex.resize((sx, sy), Image.Resampling.NEAREST)
    elif mode == "photo":
        im = tex.resize((sx, sy), Image.Resampling.BILINEAR)
        # Blend with a smooth deterministic gradient.
        gw, gh = min(128, sx), min(96, sy)
        grad_small = Image.new("RGB", (gw, gh)); gp = grad_small.load()
        for y in range(gh):
            r0 = int(255 * y / max(1, gh - 1))
            for x in range(gw):
                gp[x, y] = ((x * 7 + variant * 19) & 255, (r0 + x // 3) & 255, ((x + y) // 2 + variant * 11) & 255)
        grad = grad_small.resize((sx, sy), Image.Resampling.BILINEAR)
        im = Image.blend(im, grad, 0.38)
    else:
        im = tex.resize((sx, sy), Image.Resampling.NEAREST)
        d = ImageDraw.Draw(im)
        for i in range(20):
            x0 = (i * 83 + variant * 11) % max(1, sx - 100)
            y0 = (i * 47 + variant * 17) % max(1, sy - 80)
            d.rectangle((x0, y0, x0 + 80, y0 + 60), outline=(255, 255, 255), width=3)
    im.save(path, format="PNG", optimize=False)


def jpeg(path: Path, size=(1024, 768), quality=88, variant=0, metadata=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        raise RuntimeError("Pillow is required to generate image benchmark examples")
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x * 9 + variant * 17) & 255, (y * 7 + variant * 29) & 255, ((x * 3 + y * 2) + variant * 13) & 255)
    info = None
    if metadata:
        # Pillow preserves the EXIF blob if provided by an image info object.
        try:
            from PIL import ImageExif
            exif = ImageExif.Exif()
            exif[270] = "Arcager benchmark metadata payload " + ("X" * 4000)
            info = exif.tobytes()
        except Exception:
            info = None
    kwargs = {"quality": quality, "optimize": False}
    if info:
        kwargs["exif"] = info
    im.save(path, format="JPEG", **kwargs)


def webp_or_png(path: Path, size=(512, 384), variant=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        return png(path.with_suffix(".png"), size=size, variant=variant)
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for i in range(12):
        d.rectangle((i * 31, i * 13, size[0] - i * 7, size[1] - i * 9), fill=((i * 27) & 255, (i * 41) & 255, (i * 53) & 255))
    try:
        im.save(path, format="WEBP", lossless=True)
    except Exception:
        im.save(path.with_suffix(".png"), format="PNG")


def animated_gif(path: Path, frames=18, size=(320, 220)):
    path.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        raise RuntimeError("Pillow is required to generate GIF benchmark examples")
    imgs = []
    for k in range(frames):
        im = Image.new("RGB", size, (245, 245, 250))
        d = ImageDraw.Draw(im)
        x = 10 + (size[0] - 70) * k // max(1, frames - 1)
        d.ellipse((x, 80, x + 52, 132), fill=(40, 90, 180), outline=(10, 30, 70), width=3)
        d.rectangle((20, 20, size[0] - 20, size[1] - 20), outline=(120, 120, 130), width=2)
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=60, loop=0)


def wav(path: Path, seconds=2.0, rate=22050):
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = int(16000 * math.sin(2 * math.pi * (220 + 40 * math.sin(i / 400)) * i / rate))
        frames.extend(struct.pack("<h", v))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)


def csv_file(path: Path, rows=120, cols=8, seed=0):
    rr = random.Random(SEED + seed)
    buf = io.StringIO(newline="")
    out = csv.writer(buf)
    out.writerow([f"field_{i}" for i in range(cols)])
    for r in range(rows):
        vals = []
        for c in range(cols):
            vals.append(f"{rr.randrange(0, 100000) / 17.0:.4f}" if c % 3 else f"crop_{r % 9}")
        out.writerow(vals)
    wtext(path, buf.getvalue())


def base_site(root: Path, *, css="site.css", js="site.js", extra_body=""):
    wtext(root / "index.html", f'''<!doctype html><html><head><meta charset="utf-8"><title>Arcager benchmark</title><link rel="stylesheet" href="{css}"></head><body><main><h1>Arcager benchmark scenario</h1>{extra_body}</main><script src="{js}"></script></body></html>''')


def css_text(repeat=30):
    block = ".card{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:12px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.18)}"
    return ("body{font-family:system-ui,sans-serif;background:#f6f8fb;color:#1d2430}" + block + "\n") * repeat


def js_text(repeat=30):
    block = '''function reduceValues(values){return values.reduce((a,b)=>a+b,0);}\nconst data=[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15];\nconsole.log('Arcager benchmark', reduceValues(data));\n'''
    return block * repeat


def make_case_01(root):
    base_site(root, extra_body="<p>Small package dominated by shell overhead.</p>")
    wtext(root / "site.css", "body{font:16px sans-serif}\n")
    wtext(root / "site.js", "console.log('tiny');\n")


def make_case_02(root):
    imgs=[]
    for i in range(8):
        p=root/f"img/photo-{i:02d}.png"; png(p, size=(900,600), variant=i, mode="photo"); imgs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}" width="350">' for x in imgs))
    wtext(root/"site.css", css_text(8)); wtext(root/"site.js", js_text(3))


def make_case_03(root):
    imgs=[]
    for i in range(8):
        p=root/f"img/noise-{i:02d}.png"; png(p, size=(640,480), noisy=True, variant=i); imgs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in imgs))
    wtext(root/"site.css", css_text(4)); wtext(root/"site.js", "console.log('noisy media');\n")


def make_case_04(root):
    imgs=[]
    for i in range(7):
        p=root/f"img/hero-{i}.jpg"; jpeg(p, variant=i, quality=87); imgs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in imgs))
    wtext(root/"site.css", css_text(6)); wtext(root/"site.js", js_text(4))


def make_case_05(root):
    svg='''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><defs><linearGradient id="g"><stop stop-color="#f5a"/><stop offset="1" stop-color="#4af"/></linearGradient></defs><rect width="640" height="480" fill="url(#g)"/><circle cx="320" cy="240" r="150" fill="none" stroke="white" stroke-width="14"/><text x="320" y="250" text-anchor="middle" fill="white" font-size="42">Arcager</text></svg>'''
    refs=[]
    for i in range(30):
        p=root/f"icons/icon-{i:02d}.svg"; wtext(p, svg); refs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in refs))
    wtext(root/"site.css", css_text(12)); wtext(root/"site.js", js_text(6))


def make_case_06(root):
    refs=[]
    for i in range(6):
        p=root/f"anim/scene-{i}.gif"; animated_gif(p, frames=16+i); refs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in refs))
    wtext(root/"site.css", css_text(3)); wtext(root/"site.js", "console.log('animation');\n"*20)


def make_case_07(root):
    media=[]
    for i in range(5):
        p=root/f"media/picture-{i}.png"; png(p, size=(520,340), variant=i, mode="photo"); media.append(p.relative_to(root).as_posix())
    w=root/"media/tone.wav"; wav(w, seconds=2.2); media.append(w.relative_to(root).as_posix())
    p=root/"media/anim.gif"; animated_gif(p, frames=14); media.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' if x.endswith(('.png','.gif')) else f'<audio controls src="{x}"></audio>' for x in media))
    wtext(root/"site.css", css_text(5)); wtext(root/"site.js", js_text(4))


def make_case_08(root):
    base_site(root, extra_body='<div class="card">' * 4 + 'CSS stress' + '</div>' * 4)
    wtext(root/"site.css", css_text(450))
    wtext(root/"site.js", "console.log('css heavy');\n"*40)


def make_case_09(root):
    css='''html,body{margin:0;height:100%;background:#111;color:#eee;font-family:monospace}#game{width:300px;height:600px;border:4px solid #999;display:grid;grid-template-columns:repeat(10,1fr);grid-template-rows:repeat(20,1fr)}.cell{border:1px solid #222}.i{background:#16c}.j{background:#28a}.l{background:#d83}.o{background:#da2}.s{background:#2a6}.t{background:#83c}.z{background:#c35}'''
    js='''const board=Array.from({length:200},()=>0); const pieces={I:[[1,1,1,1]],O:[[1,1],[1,1]],T:[[0,1,0],[1,1,1]],S:[[0,1,1],[1,1,0]],Z:[[1,1,0],[0,1,1]],J:[[1,0,0],[1,1,1]],L:[[0,0,1],[1,1,1]]};
function rotate(piece){return piece[0].map((_,x)=>piece.map(row=>row[x]).reverse())}
function spawn(){const ks=Object.keys(pieces);return pieces[ks[Math.floor(Math.random()*ks.length)]]}
function collide(a,x,y){for(let r=0;r<a.length;r++)for(let c=0;c<a[r].length;c++)if(a[r][c]&&board[(y+r)*10+x+c])return true;return false}
function tick(){const p=spawn();let x=3,y=0;if(!collide(p,x,y)){while(!collide(p,x,y+1))y++;}}
for(let i=0;i<1800;i++)tick();
'''
    base_site(root, extra_body='<div id="game"></div><p>Tetris-style deterministic logic benchmark.</p>')
    css_unique=css + ''.join(f'.tile-{i}{{background:rgb({(i*37)%256},{(i*71)%256},{(i*113)%256});transform:translate({i%17}px,{i%23}px);}}\n' for i in range(7500))
    js_unique=js + ''.join(f'const level_{i}={{speed:{(i%19)+1},gravity:{(i%13)+1},score:{i*i%100000}}};\n' for i in range(18000))
    wtext(root/"site.css", css_unique)
    wtext(root/"site.js", js_unique)


def make_case_10(root):
    blocks=[]
    for i in range(2400):
        blocks.append(f"<article><h2>Section {i}</h2><p>{('This is intentionally repetitive educational prose for compression measurement. ' * 7)}</p></article>")
    base_site(root, extra_body=''.join(blocks))
    wtext(root/"site.css", css_text(90)); wtext(root/"site.js", js_text(25))


def make_case_11(root):
    data=','.join(str(i*i % 1000003) for i in range(250000))
    base_site(root, extra_body='<pre id="data"></pre>')
    wtext(root/"site.css", css_text(8))
    wtext(root/"site.js", f"const payload='{data}';document.getElementById('data').textContent=payload.length;\n"*4)


def make_case_12(root):
    cfg={"version":"2026.09","features":{f"flag_{i}":True for i in range(160)},"records":[{"id":i,"name":f"record-{i:05d}","category":f"group-{i%17}","value":round((i*1.234567)%991,6)} for i in range(14000)]}
    import json
    base_site(root, extra_body='<pre id="cfg"></pre>')
    wtext(root/"site.css", css_text(12)); wtext(root/"site.js", "const cfg=%s; document.getElementById('cfg').textContent=cfg.records.length;\n" % json.dumps(cfg, separators=(',',':')))
    wtext(root/"config.json", json.dumps(cfg, separators=(',',':')))


def make_case_13(root):
    links=[]
    for i in range(12):
        p=root/f"data/crop-{i:02d}.csv"; csv_file(p, rows=220+i*10, cols=9, seed=i); links.append(p.relative_to(root).as_posix())
    extra=''.join(f'<link rel="csv" href="{x}">' for x in links)
    base_site(root, extra_body=extra+'<p>12 agricultural and general datasets.</p>')
    wtext(root/"site.css", css_text(10)); wtext(root/"site.js", js_text(5))


def make_case_14(root):
    # Keep the secret archive outside the merge root so it is tested only as
    # an opaque hidden bundle, never as an ordinary site resource.
    site = root / "site"
    links=[]
    for i in range(12):
        p=site/f"data/general-{i:02d}.csv"; csv_file(p, rows=180, cols=8, seed=50+i); links.append(p.relative_to(site).as_posix())
    secret=root/"private-data.7z"
    # Opaque payload by design: hidden bundles are stored without inspecting the archive format.
    wbytes(secret, deterministic_bytes(450_000, SEED+700))
    extra=''.join(f'<link rel="csv" href="{x}">' for x in links)
    base_site(site, extra_body=extra+'<p>Includes a secret opaque .7z payload supplied as a hidden bundle.</p>')
    wtext(site/"site.css", css_text(8)); wtext(site/"site.js", js_text(4))
    wtext(site/"bundle-note.txt", "The .7z fixture is intentionally opaque; benchmark focus is hidden bundling and compression, not archive decoding.")


def make_case_15(root):
    import json
    records=[{"sample":i,"element":f"E{i%118}","temperature_K":round(250+i*0.37,4),"pressure_kPa":round(95+i*0.11,4),"note":"scientific measurement series"} for i in range(18000)]
    wtext(root/"data.xml", '<dataset>'+''.join(f'<row sample="{r["sample"]}" element="{r["element"]}" temperature_K="{r["temperature_K"]}" pressure_kPa="{r["pressure_kPa"]}" note="{r["note"]}"/>' for r in records)+'</dataset>')
    wtext(root/"data.json", json.dumps(records,separators=(',',':')))
    csv_file(root/"data.csv", rows=1200, cols=10, seed=91)
    base_site(root, extra_body='<p>Scientific XML/JSON/CSV dataset.</p><pre id="x"></pre>')
    wtext(root/"site.css", css_text(20)); wtext(root/"site.js", js_text(8))


def make_case_16(root):
    base_site(root, extra_body='<p>Thousands of tiny resources.</p>')
    wtext(root/"site.css", css_text(4)); wtext(root/"site.js", js_text(2))
    for i in range(3500):
        wtext(root/f"tiny/t-{i:05d}.txt", f"tiny resource {i}\n" * (1 + (i % 4)))


def make_case_17(root):
    refs=[]
    p=root/"img/shared.png"; png(p, size=(900,600), variant=77); refs.append(p.relative_to(root).as_posix())
    for i in range(24):
        cp=root/f"dup/copy-{i:02d}.png"; cp.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p,cp); refs.append(cp.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in refs))
    wtext(root/"site.css", css_text(12)); wtext(root/"site.js", js_text(5))


def make_case_18(root):
    refs=[]
    for i in range(24):
        p=root/f"near/n-{i:02d}.png"; png(p, size=(760,500), variant=100+i); refs.append(p.relative_to(root).as_posix())
    base_site(root, extra_body=''.join(f'<img src="{x}">' for x in refs))
    wtext(root/"site.css", css_text(12)); wtext(root/"site.js", js_text(5))


def make_case_19(root):
    wbytes(root/"assets/random.bin", deterministic_bytes(2_500_000, SEED+900))
    # High-entropy textual payload is base64, which should retain recognizable text characteristics but compress poorly.
    s=base64.b64encode(deterministic_bytes(1_350_000, SEED+901)).decode('ascii')
    base_site(root, extra_body='<p>High entropy binary + textual payload.</p>')
    wtext(root/"site.css", "body{font-family:monospace}\n"*8)
    wtext(root/"site.js", f"const entropy='{s}';console.log(entropy.length);\n")


def make_case_20(root):
    base_site(root, extra_body='<p>Already-compressed style payloads.</p>')
    raw=deterministic_bytes(600_000, SEED+1000)
    wbytes(root/"assets/random.gz", __import__('gzip').compress(raw, compresslevel=9))
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        z.writestr('payload.bin',raw)
    wbytes(root/"assets/archive.zip", buf.getvalue())
    wtext(root/"site.css", css_text(4)); wtext(root/"site.js", js_text(3))


def make_case_21(root):
    fonts=[]
    candidates=[
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'),
    ]
    for i,p in enumerate(candidates):
        if p.exists():
            dst=root/f"fonts/font-{i}.ttf"; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p,dst); fonts.append(dst.relative_to(root).as_posix())
    base_site(root, extra_body='<p class="font">Font-heavy page with local faces.</p>')
    css='body{font-family:system-ui}.font{font-family:ArcFont,sans-serif}@font-face{font-family:ArcFont;src:url("%s") format("truetype");}\n' % (fonts[0] if fonts else 'fonts/missing.ttf')
    wtext(root/"site.css", css*16); wtext(root/"site.js", js_text(4))


def make_case_22(root):
    text='\n'.join([
        'Português: áéíóú ç ã õ ü — Ribeirão Preto — ciência e engenharia.',
        'Español: información técnica, energía, señal, análisis.',
        'Deutsch: Wärme, Größe, Prüfung, Straße.',
        'Ελληνικά: χημεία, ενέργεια, δεδομένα.',
        '日本語: データ圧縮とブラウザでの配布。',
        '中文: 科学数据与离线文档。',
        'العربية: بيانات علمية مضغوطة.',
    ])
    base_site(root, extra_body='<p lang="pt-BR">'+text.replace('\n','<br>')+'</p>')
    wtext(root/"site.css", ('body{font-family:sans-serif}\n'*30)+text*40)
    wtext(root/"site.js", ("console.log('unicode ✓');\n"*120))
    wtext(root/"café-日本.txt", text*120)


def make_case_23(root):
    base_site(root, extra_body='<p>Multipage recursive static site.</p><a href="pages/section-0/a.html">A</a><a href="pages/section-1/a.html">B</a>')
    wtext(root/"site.css", css_text(20)); wtext(root/"site.js", js_text(8))
    for i in range(6):
        sub=root/f"pages/section-{i}"; sub.mkdir(parents=True,exist_ok=True)
        wtext(sub/"a.html", f'<html><body><h1>Section {i}</h1><img src="../../img-{i}.png"></body></html>')
        png(sub.parent/f"img-{i}.png", size=(500,300), variant=200+i)
        wtext(sub/"detail.css", css_text(15))
        wtext(sub/"detail.js", js_text(6))
        wtext(sub/"nested.json", '{"section":%d,"values":%s}' % (i, str(list(range(300+i)))))


def make_case_24(root):
    # Approximately 20 MB source: several multi-megabyte images plus text/data.
    refs=[]
    for i in range(10):
        p=root/f"large/picture-{i:02d}.png"; png(p, size=(1280,820), variant=400+i, mode="photo"); refs.append(p.relative_to(root).as_posix())
    # Add a high-entropy block so the case does not become purely compressibility driven by repeated patterns.
    wbytes(root/"large/noise.bin", deterministic_bytes(4_000_000, SEED+2400))
    base_site(root, extra_body=''.join(f'<img src="{x}" loading="lazy">' for x in refs) + '<p>Large mixed corpus.</p>')
    wtext(root/"site.css", css_text(100)); wtext(root/"site.js", js_text(40))


CASES = [
    ("01-tiny-static", "Tiny static page / overhead-dominated", make_case_01),
    ("02-png-gallery", "Many PNG images / visual-media dominated", make_case_02),
    ("03-noisy-png", "High-entropy PNG images / poorly compressible media", make_case_03),
    ("04-jpeg-gallery", "JPEG gallery / already-compressed media", make_case_04),
    ("05-svg-duplicates", "Many identical SVGs / exact dedup candidate", make_case_05),
    ("06-animated-gif", "Animated GIFs / animation-safe conversion", make_case_06),
    ("07-mixed-media", "PNG + GIF + WAV / mixed binary media", make_case_07),
    ("08-css-heavy", "CSS-heavy application shell", make_case_08),
    ("09-tetris-game", "Tetris-style mini-game / CSS + JavaScript heavy", make_case_09),
    ("10-html-heavy", "Hypertext-heavy documentation page", make_case_10),
    ("11-js-data-heavy", "JavaScript carrying a very large dataset", make_case_11),
    ("12-json-heavy", "Large structured JSON configuration/data", make_case_12),
    ("13-twelve-csv", "12 CSV datasets / agriculture and general data", make_case_13),
    ("14-secret-7z-and-csv", "12 CSV datasets + opaque secret .7z hidden bundle", make_case_14),
    ("15-scientific-data", "Scientific XML + JSON + CSV corpus", make_case_15),
    ("16-many-tiny-files", "3,500 tiny resources / metadata and indexing stress", make_case_16),
    ("17-duplicate-rich", "Exact duplicate media under many names", make_case_17),
    ("18-near-duplicates", "Near-duplicate media / dedup must stay exact-only", make_case_18),
    ("19-high-entropy", "High-entropy binary + text payload", make_case_19),
    ("20-precompressed", "GZIP/ZIP already-compressed payloads", make_case_20),
    ("21-font-heavy", "Local font-heavy web page", make_case_21),
    ("22-unicode-i18n", "Unicode / internationalized content", make_case_22),
    ("23-recursive-site", "Recursive multi-page static site", make_case_23),
    ("24-large-mixed", "Large ~20 MB mixed corpus", make_case_24),
]


def generate_case(case_id: str, root: Path):
    for cid, desc, fn in CASES:
        if cid == case_id:
            root.mkdir(parents=True, exist_ok=True)
            fn(root)
            return {"id": cid, "description": desc, "path": str(root)}
    raise KeyError(case_id)


def generate_all(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    out=[]
    for cid, desc, fn in CASES:
        r=root/cid
        if r.exists():
            shutil.rmtree(r)
        r.mkdir(parents=True, exist_ok=True)
        fn(r)
        out.append({"id":cid,"description":desc,"path":str(r)})
    return out


def generate_selected(root: Path, selected_ids):
    root.mkdir(parents=True, exist_ok=True)
    wanted=set(selected_ids)
    out=[]
    for cid, desc, fn in CASES:
        if cid not in wanted:
            continue
        r=root/cid
        if r.exists():
            shutil.rmtree(r)
        r.mkdir(parents=True, exist_ok=True)
        fn(r)
        out.append({"id":cid,"description":desc,"path":str(r)})
    return out


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _records(root: Path):
    records=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        records.append({
            'path': path.relative_to(root).as_posix(),
            'size': path.stat().st_size,
            'sha256': _sha256(path),
        })
    return records


def build_manifest(root: Path):
    cases=[]
    for cid, desc, _ in CASES:
        c=root/cid
        if not c.exists():
            raise FileNotFoundError(c)
        records=_records(c)
        cases.append({
            'id': cid,
            'description': desc,
            'file_count': len(records),
            'source_bytes': sum(r['size'] for r in records),
            'files': records,
        })
    return {'format': 1, 'seed': SEED, 'case_count': len(cases), 'cases': cases}


def main():
    ap=argparse.ArgumentParser(description='Create and verify Arcager\'s canonical 24 benchmark fixtures.')
    ap.add_argument('--output', default=str(Path(__file__).resolve().parent/'fixtures'), help='fixture directory (default: benchmarks/fixtures)')
    ap.add_argument('--cases', default='all', help='a case ID, comma-separated IDs, or all')
    ap.add_argument('--force', action='store_true', help='replace selected existing case directories')
    ap.add_argument('--verify', action='store_true', help='verify the existing manifest instead of generating fixtures')
    ns=ap.parse_args()
    out=Path(ns.output).resolve()
    selected=[cid for cid,_,_ in CASES] if ns.cases=='all' else ns.cases.split(',')
    unknown=sorted(set(selected)-{cid for cid,_,_ in CASES})
    if unknown:
        raise SystemExit('unknown case(s): '+', '.join(unknown))
    out.mkdir(parents=True, exist_ok=True)
    manifest_path=out/'manifest.json'

    if ns.verify:
        if not manifest_path.exists():
            raise SystemExit(f'manifest missing: {manifest_path}')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('case_count') != 24:
            raise SystemExit('fixture manifest does not describe all 24 canonical cases')
        failures=[]
        for case in manifest['cases']:
            root=out/case['id']
            actual={r['path']:r for r in _records(root)}
            expected={r['path']:r for r in case['files']}
            if actual.keys()!=expected.keys():
                failures.append(f"{case['id']}: file set changed")
                continue
            for rel, rec in expected.items():
                got=actual[rel]
                if got['size']!=rec['size'] or got['sha256']!=rec['sha256']:
                    failures.append(f"{case['id']}/{rel}: content mismatch")
                    break
        if failures:
            print('\n'.join(failures))
            raise SystemExit(1)
        print(f'Verified {len(manifest["cases"])} benchmark cases and {sum(c["file_count"] for c in manifest["cases"])} files.')
        return

    wanted=set(selected)
    for cid, _, fn in CASES:
        if cid not in wanted:
            continue
        root=out/cid
        if root.exists() and not ns.force:
            print(f'{cid}: already exists (use --force to regenerate)')
            continue
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        fn(root)
        total=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        count=sum(1 for p in root.rglob('*') if p.is_file())
        print(f'{cid}: wrote {count} files / {total:,} bytes')

    # A complete manifest is useful even when only some cases were selected.
    complete=True
    for cid,_,_ in CASES:
        if not (out/cid).exists():
            complete=False
            break
    if complete:
        manifest=build_manifest(out)
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        print(f'Wrote canonical manifest: {manifest_path}')
    else:
        print('Selected fixtures generated; canonical manifest not rewritten until all 24 cases exist.')


if __name__=='__main__':
    main()
