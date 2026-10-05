from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCAGER = ROOT / "arcager.py"


def run_arcager(*args, input_text=None, cwd=None):
    cmd = [sys.executable, str(ARCAGER), *map(str, args)]
    return subprocess.run(
        cmd,
        input=input_text,
        cwd=str(cwd or ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def make_site(tmp_path: Path):
    site = tmp_path / "site"
    assets = site / "assets"
    assets.mkdir(parents=True)
    (site / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="assets/style.css">'
        "</head><body>"
        '<img src="assets/a.svg">'
        '<img src="assets/b.svg">'
        '<script src="assets/app.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>\n'
    (assets / "a.svg").write_text(svg, encoding="utf-8")
    (assets / "b.svg").write_text(svg, encoding="utf-8")
    (assets / "style.css").write_text("body{background:url('a.svg')}\n", encoding="utf-8")
    (assets / "app.js").write_text("console.log('arcager-test');\n", encoding="utf-8")
    return site


def pack_path(out_dir: Path, input_path: Path, *extra):
    result = run_arcager("-f", *extra, "-O", out_dir, input_path)
    assert result.returncode == 0, result.stderr
    outs = sorted(out_dir.glob("*.html"))
    assert outs
    return outs[-1]


def parse_container(package: Path):
    sys.path.insert(0, str(ROOT))
    import arcager  # pylint: disable=import-outside-toplevel
    return arcager.parse_pack_for_cli(str(package))[2]


def test_help_contract():
    r = run_arcager("--help")
    assert r.returncode == 0
    for text in (
        "-c gzip",
        "-c brotli",
        "-c gzip lossy",
        "--strip-metadata",
        "Default packing preserves original representations",
    ):
        assert text in r.stdout


def test_preservation_merge_dedup_and_unpack(tmp_path):
    site = make_site(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out, "-v")
    assert r.returncode == 0, r.stderr
    package = out / f"packed_{site.name}.html"
    assert package.exists()

    C = parse_container(package)
    assert len(C["logical"]) == 5
    assert len(C["physical"]) == 4
    assert any(p["mime"] == "image/svg+xml" for p in C["physical"])
    svg_phys = [n["physical"] for n in C["logical"] if n["path"] in {"assets/a.svg", "assets/b.svg"}]
    assert svg_phys[0] == svg_phys[1]

    unpacked = tmp_path / "unpacked.html"
    r = run_arcager("-f", "-u", "-o", unpacked, package)
    assert r.returncode == 0, r.stderr
    text = unpacked.read_text(encoding="utf-8")
    assert "arcager-res:" not in text
    assert "data:image/svg+xml;base64," in text
    assert "data:text/css;base64," in text




def test_merge_retains_unreferenced_assets_at_fallback_priority(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<img src="used.bin">', encoding="utf-8")
    (site / "used.bin").write_bytes(b"used-content")
    (site / "dynamic.bin").write_bytes(b"dynamic-content")
    out = tmp_path / "out"
    out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 0, r.stderr
    package = out / f"packed_{site.name}.html"
    C = parse_container(package)
    paths = {n["path"] for n in C["logical"]}
    assert "dynamic.bin" in paths
    dynamic = next(n for n in C["logical"] if n["path"] == "dynamic.bin")
    used = next(n for n in C["logical"] if n["path"] == "used.bin")
    assert dynamic["priority"] > used["priority"]

def test_lossy_conversion_is_explicit(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    img = site / "photo.png"
    from PIL import Image  # optional dependency is present in CI for this regression
    Image.new("RGB", (64, 64), (20, 120, 200)).save(img, format="PNG", compress_level=0)
    (site / "index.html").write_text('<img src="photo.png">', encoding="utf-8")

    out = tmp_path / "out"
    out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 0, r.stderr
    preservation = out / f"packed_{site.name}.html"
    C = parse_container(preservation)
    photo = [C["physical"][n["physical"]] for n in C["logical"] if n["path"] == "photo.png"][0]
    assert photo["mime"] == "image/png"

    r = run_arcager("-f", "-M", site, "-O", out, "--prefix", "lossy_", "-c", "gzip", "lossy")
    assert r.returncode == 0, r.stderr
    lossy = out / f"lossy_{site.name}.html"
    C2 = parse_container(lossy)
    photo2 = [C2["physical"][n["physical"]] for n in C2["logical"] if n["path"] == "photo.png"][0]
    assert photo2["mime"] == "image/webp"
    assert photo2["flags"] & 1


def test_visible_bundle_is_self_contained_on_cli_unpack(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    bundle = tmp_path / "bundle.svg"
    bundle.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="5"/></svg>', encoding="utf-8")
    (site / "index.html").write_text('<img src="bundle.svg">', encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    package = pack_path(out, site / "index.html", "-b", bundle)
    unpacked = tmp_path / "unpacked.html"
    r = run_arcager("-f", "-u", "-o", unpacked, package)
    assert r.returncode == 0, r.stderr
    assert "data:image/svg+xml;base64," in unpacked.read_text(encoding="utf-8")


def test_encryption_can_prompt_from_stdin_in_noninteractive_mode(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>stdin password</h1>", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    packed = out / "packed.html"
    r = run_arcager("-f", "-E", "-o", packed, site / "index.html", input_text="stdin-secret\nstdin-secret\n")
    assert r.returncode == 0, r.stderr
    assert packed.exists()
    unpacked = tmp_path / "unpacked.html"
    r = run_arcager("-f", "-u", "--password", "stdin-secret", "-o", unpacked, packed)
    assert r.returncode == 0, r.stderr
    assert unpacked.read_text(encoding="utf-8") == "<h1>stdin password</h1>"


def test_z85_script_safety_does_not_corrupt_payload(monkeypatch):
    import arcager
    monkeypatch.setattr(arcager, "z85_encode", lambda _buf: "ABC</scriptXYZ")
    safe = arcager.z85_script_safe(b"irrelevant")
    assert "</script" not in safe.lower()
    assert "</\nscript" in safe.lower()
    assert "\\" not in safe


def test_z85_decoder_rejects_trailing_encoded_data():
    import arcager
    encoded = arcager.z85_encode(b"hello")
    with pytest.raises(ValueError, match="invalid Z85 length"):
        arcager.z85_decode(encoded + encoded[:5], len(b"hello"))


def test_cryptography_status_reports_active_interpreter():
    import arcager
    status = arcager.cryptography_status()
    assert status["interpreter"] == sys.executable
    assert status["available"], status


def test_encryption_wrong_password_and_correct_unpack(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>secret</h1>", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    package = pack_path(out, site / "index.html", "--password", "payload-secret", "-E")

    bad = run_arcager("-f", "-u", "--password", "wrong", "-o", tmp_path / "bad.html", package)
    assert bad.returncode == 2  # failed item makes the CLI fail explicitly
    assert "failed" in bad.stdout.lower() or "incorrect payload password" in bad.stdout.lower() or "incorrect payload password" in bad.stderr.lower()

    good = run_arcager("-f", "-u", "--password", "payload-secret", "-o", tmp_path / "good.html", package)
    assert good.returncode == 0, good.stderr
    assert (tmp_path / "good.html").read_text(encoding="utf-8") == "<h1>secret</h1>"


def test_corrupt_package_is_rejected(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("hello", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    package = pack_path(out, site / "index.html")
    text = package.read_text(encoding="utf-8")
    start = text.find('<script type="application/octet-stream" id="__pk"')
    assert start >= 0
    data_start = text.find('>', start) + 1
    data_end = text.find('</script>', data_start)
    assert data_start > 0 and data_end > data_start
    encoded = text[data_start:data_end]
    corrupted = text[:data_start] + ("0" if encoded[0] != "0" else "1") + encoded[1:] + text[data_end:]
    bad = tmp_path / "corrupt.html"
    bad.write_text(corrupted, encoding="utf-8")
    r = run_arcager("-f", "-u", "-o", tmp_path / "bad-out.html", bad)
    assert r.returncode == 2
    assert "not an Arcager v4 container" in r.stderr or "corrupt" in r.stderr.lower()


def test_hidden_bundle_round_trip(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    hidden = tmp_path / "hidden.txt"
    hidden.write_text("opaque secret payload\n", encoding="utf-8")
    (site / "index.html").write_text("<h1>public</h1>", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    package = pack_path(out, site / "index.html", "-b", hidden, "hidden", "--bundle-password", "bundle-secret")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    r = run_arcager("-f", "-x", "pack", "--bundle-password", "bundle-secret", "-O", extracted, package)
    assert r.returncode == 0, r.stderr
    assert (extracted / "hidden.txt").read_text(encoding="utf-8") == hidden.read_text(encoding="utf-8")


def test_unpacked_data_uri_extraction(tmp_path):
    html = tmp_path / "index.html"
    payload = base64.b64encode(b"x" * 300).decode("ascii")
    html.write_text(f'<img src="data:text/plain;base64,{payload}">', encoding="utf-8")
    dest = tmp_path / "extract"
    r = run_arcager("-f", "-x", "all", "-O", dest, html)
    assert r.returncode == 0, r.stderr
    assert list(dest.glob("*_datauri_*.txt"))



def test_runtime_contract_and_node_syntax(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<h1>runtime</h1>', encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    package = pack_path(out, site / "index.html")
    text = package.read_text(encoding="utf-8")
    assert "window.arcager.ready" in text
    assert "window.arcager.error" in text
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    scripts = re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', text, re.I)
    runtime = tmp_path / "runtime.js"
    runtime.write_text(scripts[-1], encoding="utf-8")
    r = subprocess.run([node, "--check", runtime], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert r.returncode == 0, r.stderr


def test_csv_merge_list_and_extract(tmp_path):
    site = tmp_path / "site"
    assets = site / "assets"; assets.mkdir(parents=True)
    (site / "index.html").write_text('<link rel="preload" as="fetch" href="assets/data.csv">', encoding="utf-8")
    (assets / "data.csv").write_text('name,value\nA,1\nB,2\n', encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 0, r.stderr
    package = out / f"packed_{site.name}.html"
    listing = run_arcager("-l", "csv", package)
    assert listing.returncode == 0, listing.stderr
    assert "data" in listing.stdout
    dest = tmp_path / "extract"; dest.mkdir()
    r = run_arcager("-f", "-x", "csv", "-O", dest, package)
    assert r.returncode == 0, r.stderr
    assert any(p.suffix == ".csv" for p in dest.rglob("*"))


def test_standalone_preservation_is_byte_exact(tmp_path):
    src = tmp_path / "report.html"
    original = "<!doctype html>\n<!-- keep me -->\n<body>  spaced text  </body>\n"
    src.write_text(original, encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    package = pack_path(out, src)
    restored = tmp_path / "restored.html"
    r = run_arcager("-f", "-u", "-o", restored, package)
    assert r.returncode == 0, r.stderr
    assert restored.read_bytes() == src.read_bytes()


def test_v3_is_cleanly_rejected_and_already_packed_is_skipped(tmp_path):
    v3 = tmp_path / "old.html"
    v3.write_text("<!--arcager:3-->\n<!doctype html>", encoding="utf-8")
    r = run_arcager("-l", v3)
    assert r.returncode == 1
    assert "v3" in r.stderr.lower()

    src = tmp_path / "src.html"
    src.write_text("<p>x</p>", encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    package = pack_path(out, src)
    second = run_arcager("-f", package)
    assert second.returncode == 3
    assert "already-packed" in second.stdout


def test_external_warning_and_missing_local_error(tmp_path):
    site = tmp_path / "site"; site.mkdir()
    (site / "index.html").write_text(
        '<link rel="stylesheet" href="https://cdn.example.test/site.css">'
        '<img src="https://cdn.example.test/a.png">', encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 0
    assert "external resource dependencies" in r.stdout

    (site / "index.html").write_text('<img src="missing.png">', encoding="utf-8")
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 2
    assert "missing local resources" in r.stderr.lower()


def test_unicode_and_recursive_batch_paths(tmp_path):
    root = tmp_path / "raíz"
    nested = root / "sub dir"
    nested.mkdir(parents=True)
    (root / "one.html").write_text("<p>1</p>", encoding="utf-8")
    (nested / "two.html").write_text("<p>2</p>", encoding="utf-8")
    out = tmp_path / "outputs"; out.mkdir()
    r = run_arcager("-f", "-r", "-O", out, root)
    assert r.returncode == 0, r.stderr
    names = {p.name for p in out.glob("*.html")}
    assert "packed_one.html" in names
    assert "packed_two.html" in names


def test_low_level_integrity_failures_are_explicit(tmp_path):
    site = tmp_path / "site"; site.mkdir()
    (site / "index.html").write_text("A" * 2000, encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    package = pack_path(out, site / "index.html")
    sys.path.insert(0, str(ROOT))
    import arcager
    _, _, encoded, _ = arcager.read_packed_v4(str(package))
    # Header corruption must be rejected before resource access.
    broken_header = bytearray(encoded); broken_header[0] ^= 1
    with pytest.raises(ValueError, match="not an Arcager v4 container|corrupt v4 header"):
        arcager.V4ContainerBuilder.parse(bytes(broken_header))

    # Metadata CRC corruption must also be rejected deterministically.
    broken_meta = bytearray(encoded); broken_meta[64] ^= 1
    with pytest.raises(ValueError, match="corrupt v4 metadata"):
        arcager.V4ContainerBuilder.parse(bytes(broken_meta))


def test_strip_metadata_is_explicit_and_content_safe(tmp_path):
    from PIL import Image
    site = tmp_path / "site"; site.mkdir()
    img = site / "photo.jpg"
    im = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = im.getexif(); exif[0x010E] = "privacy-marker"
    im.save(img, format="JPEG", quality=90, exif=exif.tobytes())
    (site / "index.html").write_text('<img src="photo.jpg">', encoding="utf-8")
    out = tmp_path / "out"; out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out, "--strip-metadata")
    assert r.returncode == 0, r.stderr
    package = out / f"packed_{site.name}.html"
    C = parse_container(package)
    photo = [C["physical"][n["physical"]] for n in C["logical"] if n["path"] == "photo.jpg"][0]
    assert photo["flags"] & 2
    assert photo["mime"] == "image/jpeg"



def test_runtime_cleanup_script_has_real_html_closing_tag():
    import arcager

    # The cleanup script is inserted into the reconstructed HTML after the
    # outer runtime <script> has already been parsed. Escaping the slash here
    # would leave literal ``<\\/script>`` in the reconstructed HTML and
    # cause the cleanup script itself to become invalid JavaScript, leaving
    # the unpacking overlay stuck at 95% after the application appears.
    runtime = arcager.JS_RUNTIME
    assert "<' +' /scr' +'ipt>".replace(" ", "") in runtime
    assert "<\\/scr' +'ipt>" not in runtime


def test_runtime_parser_and_gzip_stream_accept_high_bit_crc(tmp_path):
    """Exercise the actual v4 JavaScript metadata/stream reader, not only Python parsing."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><html><body>" + ("A" * 20000) + "</body></html>", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    package = pack_path(out, site / "index.html")
    sys.path.insert(0, str(ROOT))
    import arcager  # pylint: disable=import-outside-toplevel
    _, _, encoded, _ = arcager.read_packed_v4(str(package))
    fragment_start = arcager.JS_RUNTIME.index("function varint")
    fragment_end = arcager.JS_RUNTIME.index("function extFor", fragment_start)
    fragment = arcager.JS_RUNTIME[fragment_start:fragment_end]
    script = fragment + "\n" + f"const c=Uint8Array.from(Buffer.from({json.dumps(base64.b64encode(encoded).decode())}, 'base64'));\n" + (
        "const C=parseContainer(c);\n"
        "streamBytes(C,0,[]).then(raw=>{if(raw.length!==C.streams[0].raw)throw Error('bad raw size');"
        "console.log('ok',C.streams.length,C.logical.length,raw.length)}).catch(e=>{console.error(e.message);process.exit(1);});\n"
    )
    probe = tmp_path / "runtime_probe.js"
    probe.write_text(script, encoding="utf-8")
    r = subprocess.run([node, str(probe)], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr or r.stdout
    assert r.stdout.startswith("ok ")

def test_solid_rejects_tiny_payload_win_when_extra_stream_metadata_costs_more(monkeypatch):
    import arcager

    original = arcager.compress_bytes
    def fake_compress(data, algo):
        if algo == 1 and data.startswith(b"A"):
            return data[:-1]
        return data

    monkeypatch.setattr(arcager, "compress_bytes", fake_compress)
    builder = arcager.V4ContainerBuilder(1)
    builder.add_node("a.css", b"A" * 1024, "text/css", source_mime="text/css")
    builder.add_node("noise.png", b"N" * 1024, "image/png", source_mime="image/png")
    container = builder.serialize()
    parsed = arcager.V4ContainerBuilder.parse(container)
    # The one-byte CSS payload win does not justify creating another stream
    # record and its physical-offset metadata, so both resources share storage.
    assert len(parsed["streams"]) == 1
    assert parsed["streams"][0]["algo"] == 0
    decisions = {d["mime"]: d for d in builder.stats["solid_attempts"]}
    assert decisions["text/css"]["candidate_bytes"] == 1023
    assert decisions["text/css"]["accepted"] is False
    assert decisions["text/css"]["serialized_compressed_bytes"] > decisions["text/css"]["serialized_stored_bytes"]


def test_non_beneficial_solid_candidate_is_rejected():
    import hashlib
    import arcager

    data = bytearray()
    counter = 0
    while len(data) < 65536:
        data.extend(hashlib.blake2b(counter.to_bytes(8, "little"), digest_size=64).digest())
        counter += 1
    data = bytes(data[:65536])
    assert len(arcager.compress_bytes(data, 1)) >= len(data)

    builder = arcager.V4ContainerBuilder(1)
    builder.add_node("noise.png", data, "image/png", source_mime="image/png")
    container = builder.serialize()
    parsed = arcager.V4ContainerBuilder.parse(container)
    assert len(parsed["streams"]) == 1
    assert parsed["streams"][0]["algo"] == 0
    assert parsed["streams"][0]["raw_size"] == len(data)
