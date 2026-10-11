"""Tests for listing, extraction, unpacking, messages, versioning and the runtime CSV parser."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCAGER = ROOT / "arcager.py"


def run_arcager(*args, input_text=None, cwd=None):
    return subprocess.run([sys.executable, str(ARCAGER), *map(str, args)], input=input_text,
                          cwd=str(cwd or ROOT), text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)


# A realistic little site: CRLF CSVs, a stylesheet, scripts, a table that JS also fetches.
FILES = {
    "index.html": ('<!doctype html><html><head><meta charset="utf-8">'
                   '<link rel="stylesheet" href="css/app.css" media="screen">'
                   '<link rel="csv" href="data/elements.csv">'
                   '<link rel="csv" href="data/lookups.csv">'
                   '</head><body><h1>t</h1><script src="js/app.js"></script></body></html>'),
    "css/app.css": "body{margin:0}\n.x{color:red}\n",
    "js/app.js": "window.hello = 'hi';\n",
    "data/elements.csv": "z,sym\r\n1,H\r\n2,He\r\n",
    "data/lookups.csv": "k,v\r\na,1\r\n",
}


@pytest.fixture()
def site(tmp_path):
    root = tmp_path / "site"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


@pytest.fixture()
def package(site, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = run_arcager("-f", "-M", site, "-O", out)
    assert r.returncode == 0, r.stderr
    return out / f"packed_{site.name}.html"


def listing_paths(stdout):
    rows = [ln for ln in stdout.splitlines() if re.match(r"^\w+\s+\d+\s+\S+/\S+\s+\S", ln)]
    return [re.split(r"\s{2,}|\s+", ln, maxsplit=3)[3].split("   (")[0].strip() for ln in rows]


# ---------------------------------------------------------------- listing

def test_every_resource_is_listed_exactly_once(package):
    r = run_arcager("-l", package)
    assert r.returncode == 0, r.stderr
    paths = listing_paths(r.stdout)
    assert sorted(paths) == sorted(FILES), paths
    assert len(paths) == len(set(paths))
    assert "exposed as arcager.csv.elements" in r.stdout
    assert re.search(r"5 resources", r.stdout)


def load_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ARCAGER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_csv_is_stored_once_in_the_package(package):
    mod = load_module("arcager_mod_once")
    view = mod.PackageView(str(package))
    root = view.root_text()
    assert root.count('data-key="elements"') == 1
    assert 'data-origin="data/elements.csv"' in root
    # ...and there is no second, separate copy among the stored files.
    assert not any(n["path"] == "data/elements.csv" for n in view.C["logical"])


def test_list_type_filter_and_helpful_empty_result(package):
    r = run_arcager("-l", "csv", package)
    assert listing_paths(r.stdout) == ["data/elements.csv", "data/lookups.csv"]
    r = run_arcager("-l", "fonts", package)
    assert r.returncode == 0
    assert "this package contains" in r.stderr and "docs (3)" in r.stderr


def test_media_type_is_not_everything_and_docs_include_js(package):
    assert listing_paths(run_arcager("-l", "media", package).stdout) == []
    docs = listing_paths(run_arcager("-l", "docs", package).stdout)
    assert "js/app.js" in docs and "css/app.css" in docs and "index.html" in docs


# ---------------------------------------------------------------- extraction

def test_extract_all_roundtrips_every_file_byte_for_byte(package, site, tmp_path):
    dest = tmp_path / "ex"
    r = run_arcager("-x", "all", "-O", dest, package)
    assert r.returncode == 0, r.stderr
    for rel in FILES:
        assert (dest / rel).read_bytes() == (site / rel).read_bytes(), rel


def test_extract_docs_brings_back_index_html_and_csv_extracts_once(package, tmp_path):
    d1 = tmp_path / "docs"
    assert run_arcager("-x", "docs", "-O", d1, package).returncode == 0
    assert (d1 / "index.html").exists()
    d2 = tmp_path / "csv"
    assert run_arcager("-x", "csv", "-O", d2, package).returncode == 0
    assert sorted(p.relative_to(d2).as_posix() for p in d2.rglob("*") if p.is_file()) == ["data/elements.csv", "data/lookups.csv"]


def test_extracted_site_can_be_repacked_to_the_same_resources(package, tmp_path):
    again = tmp_path / "again"
    assert run_arcager("-x", "all", "-O", again, package).returncode == 0
    out = tmp_path / "out2"
    out.mkdir()
    assert run_arcager("-f", "-M", again, "-O", out).returncode == 0
    a = listing_paths(run_arcager("-l", package).stdout)
    b = listing_paths(run_arcager("-l", out / "packed_again.html").stdout)
    assert sorted(a) == sorted(b)


def test_partial_extract_warns_about_unextracted_references(package, tmp_path):
    r = run_arcager("-x", "docs", "-O", tmp_path / "d", package)
    assert "were not extracted" in r.stderr and "-x all" in r.stderr


def test_only_selects_by_name_pattern_and_table_key(package, tmp_path):
    d = tmp_path / "o"
    r = run_arcager("-x", "all", "--only", "elements", "-O", d, package)
    assert r.returncode == 0, r.stderr
    assert [p.name for p in d.rglob("*") if p.is_file()] == ["elements.csv"]
    d2 = tmp_path / "o2"
    run_arcager("-x", "all", "--only", "data/*.csv", "-O", d2, package)
    assert len([p for p in d2.rglob("*") if p.is_file()]) == 2
    r = run_arcager("-x", "all", "--only", "nomatch*", "-O", tmp_path / "o3", package)
    assert r.returncode == 3 and "this package contains" in r.stderr


def test_extract_never_overwrites_without_force(package, tmp_path):
    d = tmp_path / "ex"
    assert run_arcager("-x", "all", "-O", d, package).returncode == 0
    r = run_arcager("-x", "all", "-O", d, package)
    assert r.returncode == 1 and "-f" in r.stderr and "already exist" in r.stderr
    assert run_arcager("-f", "-x", "all", "-O", d, package).returncode == 0


def test_hidden_bundle_policy(site, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("shh", encoding="utf-8")
    out = tmp_path / "hp"
    out.mkdir()
    r = run_arcager("-f", "-M", site, "-b", secret, "hidden", "--bundle-password", "pw", "-O", out)
    assert r.returncode == 0, r.stderr
    pkg = out / f"packed_{site.name}.html"
    r = run_arcager("-l", "all", pkg, input_text="")
    assert r.returncode == 0 and "hidden encrypted bundle" in r.stderr and "--bundle-password" in r.stderr
    d = tmp_path / "x"
    r = run_arcager("-x", "pack", "--bundle-password", "pw", "-O", d, pkg)
    assert r.returncode == 0, r.stderr
    assert (d / "secret.txt").read_text(encoding="utf-8") == "shh"
    r = run_arcager("-x", "pack", "--bundle-password", "wrong", "-O", tmp_path / "y", pkg)
    assert r.returncode == 1 and "wrong --bundle-password" in r.stderr


# ---------------------------------------------------------------- unpack

def test_unpack_restores_css_and_js_as_readable_text(package, tmp_path):
    out = tmp_path / "un.html"
    r = run_arcager("-u", "-f", "-o", out, package)
    assert r.returncode == 0, r.stderr
    html = out.read_text(encoding="utf-8")
    assert "base64" not in html
    assert "arcager-res:" not in html
    assert '<style media="screen">' in html and ".x{color:red}" in html
    assert "<script>" in html and "window.hello = 'hi';" in html
    assert 'type="text/csv"' in html and "1,H" in html


def test_unpack_keeps_deferred_script_semantics_and_escapes_closers(tmp_path):
    site = tmp_path / "s"
    (site / "js").mkdir(parents=True)
    (site / "index.html").write_text(
        '<!doctype html><head><script src="js/a.js" defer></script></head>'
        '<body><p>x</p><script src="js/b.js"></script></body>', encoding="utf-8")
    (site / "js/a.js").write_text("window.a = document.querySelector('p') !== null;", encoding="utf-8")
    (site / "js/b.js").write_text("var s = '</script>'; // end", encoding="utf-8")
    out = tmp_path / "o"
    out.mkdir()
    assert run_arcager("-f", "-M", site, "-O", out).returncode == 0
    un = tmp_path / "u.html"
    assert run_arcager("-u", "-f", "-o", un, out / "packed_s.html").returncode == 0
    html = un.read_text(encoding="utf-8")
    assert "<\\/script>" in html                                  # closer inside JS text is escaped
    assert html.index("window.a") > html.index("<p>x</p>")        # deferred script moved after the markup
    assert html.count("</script>") == 2                           # exactly the two real script ends


def test_unpack_drops_query_suffix_from_embedded_data(tmp_path):
    site = tmp_path / "s"
    (site / "img").mkdir(parents=True)
    (site / "index.html").write_text('<!doctype html><img src="img/a.png?v=3">', encoding="utf-8")
    (site / "img/a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 3)
    out = tmp_path / "o"
    out.mkdir()
    assert run_arcager("-f", "-M", site, "-O", out).returncode == 0
    un = tmp_path / "u.html"
    assert run_arcager("-u", "-f", "-o", un, out / "packed_s.html").returncode == 0
    html = un.read_text(encoding="utf-8")
    m = re.search(r'src="(data:image/png;base64,[^"]*)"', html)
    assert m and "?" not in m.group(1)


# ---------------------------------------------------------------- output / messages

def test_pack_reports_percent_of_original_and_factor_without_signs(site, tmp_path):
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    m = re.search(r"(\d+\.\d)% · (\d+\.\d\d)×", r.stdout)
    assert m, r.stdout
    assert "−" not in r.stdout and not re.search(r"[+−]\d+\.\d%", r.stdout)
    assert float(m.group(1)) > 0 and float(m.group(2)) > 0      # exact math is covered by test_ratio_helper_*


def test_ratio_helper_is_percent_of_original():
    mod = load_module("arcager_mod_ratio")
    assert mod.ratio_str(1000, 250) == "25.0% · 4.00×"
    assert mod.ratio_str(1000, 1250) == "125.0% · 0.80×"
    assert mod.ratio_str(0, 5) == "n/a"


def test_explicit_lossy_is_not_warned_about(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<!doctype html><p>x</p>", encoding="utf-8")
    r = run_arcager("-f", "-c", "gzip", "lossy", "--strip-metadata", page, "-O", tmp_path)
    assert r.returncode == 0
    assert "lossy" not in (r.stdout + r.stderr).lower()
    assert r.stderr.strip() == ""


def test_password_without_encrypt_warns(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<!doctype html><p>x</p>", encoding="utf-8")
    r = run_arcager("-f", "--password", "abc", page, "-O", tmp_path)
    assert "will NOT be encrypted" in r.stderr and "-E" in r.stderr


@pytest.mark.parametrize("args, needle", [
    (["--strip-metdata", "x.html"], "Did you mean '--strip-metadata'"),
    (["-M"], "needs a folder"),
    (["-c", "zip", "x.html"], "choose gzip or brotli"),
    (["-c", "lossy", "x.html"], "right after the algorithm"),
    (["-x", "elements", "x.html"], "--only"),
    (["-l", "nosuchfile.html"], "cannot find"),
    (["-l", "-x", "x.html"], "cannot be used together"),
])
def test_usage_errors_guide_the_user(args, needle, tmp_path):
    r = run_arcager(*args, cwd=tmp_path)
    assert r.returncode == 1
    assert needle in r.stderr
    assert "--help" in r.stderr


def test_type_word_wins_over_same_named_folder(package, tmp_path):
    (tmp_path / "docs").mkdir()
    r = run_arcager("-l", "docs", package, cwd=tmp_path)
    assert r.returncode == 0 and "index.html" in r.stdout


def test_help_documents_modes_types_and_exit_codes():
    r = run_arcager("--help")
    assert r.returncode == 0
    for text in ("-l, --list", "-x, --extract", "-u, --unpack", "--only", "EXIT CODES",
                 "matches: CSV and TSV", "matches: HTML/XHTML, CSS, JavaScript", "TYPES"):
        assert text in r.stdout, text


def test_version_flag_matches_constant_and_reports_components():
    out = run_arcager("-V").stdout
    m = re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', ARCAGER.read_text(encoding="utf-8"), re.M)
    assert out.splitlines()[0] == f"arcager {m.group(1)}"
    assert run_arcager("--version").stdout == out
    for label in ("Python", "cryptography", "Terser", "Brotli", "Pillow"):
        assert label in out, out
    assert "required" in out            # cryptography is a core requirement


def make_fake_terser(tmp_path, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / "terser"
    f.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    f.chmod(0o755)
    return d


@pytest.mark.skipif(sys.platform.startswith("win"), reason="uses a POSIX shell script as a fake terser")
def test_version_reports_installed_terser_version(tmp_path):
    import os
    d = make_fake_terser(tmp_path, "echo 'terser 9.8.7'\n")
    env = dict(os.environ, PATH=str(d) + os.pathsep + os.environ["PATH"])
    out = subprocess.run([sys.executable, str(ARCAGER), "-V"], env=env, text=True, stdout=subprocess.PIPE).stdout
    assert re.search(r"Terser\s+9\.8\.7", out), out
    assert str(d) in out


@pytest.mark.skipif(sys.platform.startswith("win"), reason="uses a POSIX shell script as a fake terser")
def test_version_explains_a_broken_terser(tmp_path):
    import os
    d = make_fake_terser(tmp_path, "echo boom >&2\nexit 3\n")
    env = dict(os.environ, PATH=str(d) + os.pathsep + os.environ["PATH"])
    out = subprocess.run([sys.executable, str(ARCAGER), "-V"], env=env, text=True, stdout=subprocess.PIPE).stdout
    assert "did not report a version" in out and "boom" in out


def test_terser_reported_missing_when_not_on_path(tmp_path):
    import os
    env = dict(os.environ, PATH=str(tmp_path))     # nothing on PATH
    out = subprocess.run([sys.executable, str(ARCAGER), "-V"], env=env, text=True, stdout=subprocess.PIPE).stdout
    assert re.search(r"Terser\s+not installed", out) and "npm install -g terser" in out


# ---------------------------------------------------------------- versioning / docs

def test_docs_are_in_sync_with_version():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_docs.py"), "--check"],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stdout + r.stderr


def test_version_appears_in_exactly_one_source_line():
    text = ARCAGER.read_text(encoding="utf-8")
    assert len(re.findall(r'^VERSION = "', text, re.M)) == 1
    assert not re.search(r"Arcager \d+\.\d+\.\d+", text.split("VERSION =")[0]), "docstring must not repeat the version"


# ---------------------------------------------------------------- CSV block escaping

def test_script_escape_roundtrip_and_never_leaves_a_closing_tag():
    mod = load_module("arcager_mod_t")
    for s in ["</script>", "<\\/script>", "<\\\\/SCRIPT", "x</Script >", "<//script", "plain", ""]:
        e = mod.escape_for_script(s)
        assert "</script" not in e.lower()
        assert mod.unescape_from_script(e) == s


def test_tricky_csv_text_survives_pack_and_extract(tmp_path):
    site = tmp_path / "s"
    site.mkdir()
    (site / "index.html").write_text('<!doctype html><link rel="csv" href="t.csv">', encoding="utf-8")
    data = b't,v\r\n"</SCRIPT><b>",1\r\n"<\\/script>",2\r\n\xe2\x9c\x93,3\r\n'
    (site / "t.csv").write_bytes(data)
    out = tmp_path / "o"
    out.mkdir()
    assert run_arcager("-f", "-M", site, "-O", out).returncode == 0
    ex = tmp_path / "x"
    assert run_arcager("-x", "csv", "-O", ex, out / "packed_s.html").returncode == 0
    assert (ex / "t.csv").read_bytes() == data


def test_duplicate_table_names_get_unique_keys_with_warning(tmp_path):
    site = tmp_path / "s"
    (site / "a").mkdir(parents=True)
    (site / "b").mkdir()
    (site / "index.html").write_text('<link rel="csv" href="a/d.csv"><link rel="csv" href="b/d.csv">', encoding="utf-8")
    (site / "a/d.csv").write_text("x\n1\n", encoding="utf-8")
    (site / "b/d.csv").write_text("y\n2\n", encoding="utf-8")
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    assert "arcager.csv.d_2" in r.stderr and "Rename" in r.stderr
    listing = run_arcager("-l", tmp_path / "packed_s.html").stdout
    assert "arcager.csv.d" in listing and "arcager.csv.d_2" in listing


# ---------------------------------------------------------------- runtime CSV parser (needs Node)

@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_runtime_csv_parser_handles_quotes_multiline_tabs_and_blank_lines(tmp_path):
    src = ARCAGER.read_text(encoding="utf-8")
    start = src.index("function parseCSV(body){")
    end = src.index("async function main(){", start)
    cases = {
        "basic": 'name,qty,note\nWidget,3,"has, comma"\nGadget,5,"says ""hi"""\n\nLast,7,\n',
        "multiline": 'id,text\n1,"line one\nline two"\n2,ok\n',
        "tabs": "a\tb\n1\t2\n",
        "crlf": "a,b\r\n1,2\r\n",
        "bom": "\ufeffk,v\nx,1\n",
        "empty": "",
    }
    script = src[start:end] + "\nconst cases=" + json.dumps(cases) + \
        ";const o={};for(const k in cases)o[k]=parseCSV(cases[k]);console.log(JSON.stringify(o));"
    f = tmp_path / "t.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(f)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["basic"] == [["name", "qty", "note"], ["Widget", "3", "has, comma"], ["Gadget", "5", 'says "hi"'], ["Last", "7", ""]]
    assert got["multiline"] == [["id", "text"], ["1", "line one\nline two"], ["2", "ok"]]
    assert got["tabs"] == [["a", "b"], ["1", "2"]]
    assert got["crlf"] == [["a", "b"], ["1", "2"]]
    assert got["bom"] == [["k", "v"], ["x", "1"]]
    assert got["empty"] == []


if __name__ == "__main__":
    # Running this file directly used to print nothing at all (it only defines tests).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_tests import main as _run_tests
    raise SystemExit(_run_tests([str(Path(__file__).resolve())]))


# ---------------------------------------------------------------- cryptography is a core requirement

def run_without_cryptography(*args, cwd=None):
    code = ("import sys, runpy\nsys.modules['cryptography'] = None\n"
            f"sys.argv = ['arcager.py'] + {list(map(str, args))!r}\n"
            f"runpy.run_path({str(ARCAGER)!r}, run_name='__main__')\n")
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd or ROOT), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_missing_cryptography_stops_every_operation_with_install_instructions(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<!doctype html><p>x</p>", encoding="utf-8")
    r = run_without_cryptography(page, "-O", tmp_path)          # plain packing, no -E
    assert r.returncode == 1
    assert "needs the 'cryptography' package" in r.stderr
    assert "pip install -r requirements.txt" in r.stderr
    assert not (tmp_path / "packed_p.html").exists()


def test_help_and_version_still_work_without_cryptography():
    assert run_without_cryptography("--help").returncode == 0
    r = run_without_cryptography("-V")
    assert r.returncode == 0 and "NOT AVAILABLE" in r.stdout


def test_requirements_files_declare_cryptography_as_core():
    core = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    optional = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")
    assert re.search(r"^cryptography", core, re.M)
    assert not re.search(r"^cryptography", optional, re.M)


# ---------------------------------------------------------------- dark loader

def test_loader_shell_is_dark_and_uses_no_native_dialogs(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<!doctype html><p>x</p>", encoding="utf-8")
    assert run_arcager("-f", page, "-O", tmp_path).returncode == 0
    text = (tmp_path / "packed_p.html").read_text(encoding="utf-8")
    shell = text.split('<script type="application/octet-stream"')[0]
    assert 'name="color-scheme" content="dark"' in shell
    assert "background:#0f1115" in shell
    assert not re.search(r"#f{3,6}\b|#f7f7f7|rgba\(247", shell, re.I), "no bright backgrounds in the loader"
    runtime = text.split("<script>")[-1]
    assert "prompt(" in runtime and "askPassword" in runtime      # prompt() only as a no-form fallback
    assert "alert(" not in runtime                                # no native alert for wrong passwords
    assert 'id="__pwf"' in shell and "hidden" in shell            # in-page password form


# ---------------------------------------------------------------- xml and mjs are docs

def test_mjs_and_xml_are_docs_and_module_imports_are_followed(tmp_path):
    site = tmp_path / "s"
    (site / "js").mkdir(parents=True)
    (site / "index.html").write_text('<!doctype html><script type="module" src="js/main.mjs"></script>', encoding="utf-8")
    (site / "js/main.mjs").write_text("import { g } from './util.mjs'; fetch('../feed.xml'); g();", encoding="utf-8")
    (site / "js/util.mjs").write_text("export const g = () => 1;", encoding="utf-8")
    (site / "feed.xml").write_text('<?xml version="1.0"?><feed/>', encoding="utf-8")
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip() == ""                                    # nothing unused, nothing missing
    out = run_arcager("-l", "docs", tmp_path / "packed_s.html").stdout
    paths = listing_paths(out)
    assert {"js/main.mjs", "js/util.mjs", "feed.xml", "index.html"} <= set(paths)
    assert re.search(r"docs\s+\d+\s+text/javascript\s+js/util.mjs", out)    # module MIME: browsers refuse octet-stream modules
    assert "feed.xml" in run_arcager("-l", "data", tmp_path / "packed_s.html").stdout   # xml is still structured data too
    ex = tmp_path / "ex"
    assert run_arcager("-x", "docs", "-O", ex, tmp_path / "packed_s.html").returncode == 0
    assert (ex / "js/util.mjs").read_text(encoding="utf-8") == "export const g = () => 1;"   # .mjs name kept
    assert (ex / "feed.xml").exists()


# ---------------------------------------------------------------- unused resources

def make_site_with_unused(tmp_path):
    site = tmp_path / "us"
    (site / "old").mkdir(parents=True)
    (site / "js").mkdir()
    (site / "index.html").write_text('<!doctype html><a href="about.html">a</a><script src="js/a.js"></script>', encoding="utf-8")
    (site / "js/a.js").write_text("x=1", encoding="utf-8")
    (site / "about.html").write_text("about", encoding="utf-8")
    (site / "draft.html").write_text("draft", encoding="utf-8")
    (site / "old/logo.png").write_bytes(b"\x89PNG" + bytes(range(256)) * 20)
    (site / "notes.txt").write_text("notes", encoding="utf-8")
    return site


def test_merge_warns_about_files_nothing_links_to_and_still_includes_them(tmp_path):
    site = make_site_with_unused(tmp_path)
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    assert r.returncode == 0
    assert "2 files" in r.stderr and "old/logo.png" in r.stderr and "notes.txt" in r.stderr
    assert "included anyway" in r.stderr and "--skip-unused" in r.stderr
    assert r.stderr.index("old/logo.png") < r.stderr.index("notes.txt")        # largest first
    listing = listing_paths(run_arcager("-l", tmp_path / "packed_us.html").stdout)
    assert "old/logo.png" in listing and "notes.txt" in listing


def test_merge_warns_about_pages_that_are_not_packed(tmp_path):
    site = make_site_with_unused(tmp_path)
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    assert "2 other HTML pages" in r.stderr
    assert "about.html (linked from your page: that link will not work)" in r.stderr
    assert "draft.html" in r.stderr and "arcager PAGE.html" in r.stderr


def test_skip_unused_leaves_them_out_and_says_so(tmp_path):
    site = make_site_with_unused(tmp_path)
    r = run_arcager("-f", "-M", site, "--skip-unused", "-O", tmp_path)
    assert r.returncode == 0 and "left out because of --skip-unused" in r.stderr
    listing = listing_paths(run_arcager("-l", tmp_path / "packed_us.html").stdout)
    assert sorted(listing) == ["index.html", "js/a.js"]


def test_clean_site_produces_no_unused_warning(site, tmp_path):
    r = run_arcager("-f", "-M", site, "-O", tmp_path)
    assert "not linked" not in r.stderr and "not included" not in r.stderr


def test_skip_unused_without_merge_is_explained(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<!doctype html>", encoding="utf-8")
    r = run_arcager("-f", "--skip-unused", page, "-O", tmp_path)
    assert "only applies together with -M" in r.stderr


# ---------------------------------------------------------------- tests are runnable directly

def test_running_a_test_file_directly_reports_a_result():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_tests.py"), "-k", "test_ratio_helper"],
                       cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED" in r.stdout and "passed" in r.stdout
