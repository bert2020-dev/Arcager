from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "benchmarks" / "fixtures"
ARCAGER = ROOT / "arcager.py"

pytestmark = pytest.mark.benchmark


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest():
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def run_arcager(*args):
    import subprocess, sys
    return subprocess.run([sys.executable, str(ARCAGER), *map(str, args)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_benchmark_manifest_is_complete():
    manifest = load_manifest()
    assert manifest["case_count"] == 24
    assert len(manifest["cases"]) == 24
    for case in manifest["cases"]:
        root = FIXTURES / case["id"]
        assert root.exists(), case["id"]
        files = [p for p in root.rglob("*") if p.is_file()]
        assert len(files) == case["file_count"]
        assert sum(p.stat().st_size for p in files) == case["source_bytes"]


@pytest.mark.parametrize("case_id", [
    "01-tiny-static", "02-png-gallery", "03-noisy-png", "04-jpeg-gallery",
    "05-svg-duplicates", "06-animated-gif", "07-mixed-media", "08-css-heavy",
    "09-tetris-game", "10-html-heavy", "11-js-data-heavy", "12-json-heavy",
    "13-twelve-csv", "14-secret-7z-and-csv", "15-scientific-data", "16-many-tiny-files",
    "17-duplicate-rich", "18-near-duplicates", "19-high-entropy", "20-precompressed",
    "21-font-heavy", "22-unicode-i18n", "23-recursive-site", "24-large-mixed",
])
def test_fixture_digests_are_reproducible(case_id):
    manifest = load_manifest()
    case = next(c for c in manifest["cases"] if c["id"] == case_id)
    root = FIXTURES / case_id
    expected = {x["path"]: x for x in case["files"]}
    actual = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
    assert set(actual) == set(expected)
    for rel, path in actual.items():
        data = path.read_bytes()
        assert len(data) == expected[rel]["size"], rel
        assert sha256(data) == expected[rel]["sha256"], rel




def test_font_heavy_roundtrip_accepts_expected_merge_rewrite(tmp_path):
    import subprocess, sys
    sys.path.insert(0, str(ROOT / "benchmarks"))
    from run_benchmarks import pack_and_decode
    case = FIXTURES / "21-font-heavy"
    source = next(c for c in load_manifest()["cases"] if c["id"] == "21-font-heavy")["source_bytes"]
    row = pack_and_decode([sys.executable], ARCAGER, "4.0.0", "21-font-heavy", case, source, "gzip", verify=True)
    assert row["asset_roundtrip_ok"] is True
    assert row["logical_transformed"] >= 1
    assert row["logical_exact"] >= 1

def test_png_case_demonstrates_solid_rejection_without_data_loss(tmp_path):
    # This is the concrete regression behind the confusing PNG benchmark result:
    # the PNG stream must remain raw when its GZIP candidate is not smaller.
    import sys
    sys.path.insert(0, str(ROOT / "benchmarks"))
    from run_benchmarks import v4_accounting, case_root
    import sys
    import tempfile
    import subprocess

    case = FIXTURES / "02-png-gallery"
    out = tmp_path / "packed.html"
    r = run_arcager("-M", case_root(case), "-f", "-o", out, "-c", "gzip")
    assert r.returncode == 0, r.stderr
    accounting = v4_accounting(out)
    image_streams = [s for s in accounting["solid_decisions"] if s["mime"] == "application/octet-stream"]
    assert image_streams, accounting
    assert all(s["decision"] == "stored" for s in image_streams)
    assert all(s["stored_bytes"] == s["raw_bytes"] for s in image_streams)
    # Most importantly, the test fails if a non-beneficial compressed stream is ever kept.
    assert all(not (s["decision"] == "compressed" and s["stored_bytes"] >= s["raw_bytes"]) for s in accounting["solid_decisions"])
