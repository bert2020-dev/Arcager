#!/usr/bin/env python3
"""Arcager — Solid Update.

The version number lives in exactly one place: ``VERSION`` below. Documentation
is kept in sync by ``python scripts/sync_docs.py`` (see docs/RELEASING.md).

A cross-platform, dependency-free (for preservation mode) single-file HTML
packer with content-aware resources, exact deduplication, MIME-segmented
compression streams, binary v4 metadata, optional WebP conversion, AES-GCM
protection, bundle support, merge diagnostics, listing/extraction, and a
small browser runtime.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import difflib
import fnmatch
import getpass
import gzip
import hashlib
import io
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import List, Optional

try:
    import brotli as _brotli
    HAS_BROTLI = True
except ImportError:
    _brotli = None
    HAS_BROTLI = False

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    Image = ImageOps = None
    HAS_PIL = False

_AESGCM = None
_CRYPTOGRAPHY_ERROR = None

VERSION = "4.2.0"   # <- the ONE place to change the version; then run scripts/sync_docs.py
__version__ = VERSION
SIGNATURE = "<!--arcager:4-->"
SIG_RE = re.compile(r"^<!--arcager:(\d+)-->")
V4_MAGIC = b"ARCAGER4"
V4_VERSION = 4
V4_HEADER_SIZE = 64
PBKDF2_ITERATIONS = 600_000
PASSWORD_CHECK = b"arcager-ok"
GCM_TAG_LEN = 16
MIN_EXTRACT = 256
_DEFAULT_PACK_PREFIX = "packed_"
_DEFAULT_UNPACK_PREFIX = "unpacked_"
_HIDDEN_MAX_TOTAL = 512 * 1024 * 1024
_BUNDLE_MAX_TOTAL = 128 * 1024 * 1024
_MERGE_MAX_INLINE_BYTES = 64 * 1024 * 1024

Z85A = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#"
Z85M = [-1] * 128
for _i, _ch in enumerate(Z85A):
    Z85M[ord(_ch)] = _i

# Stable MIME ids. Keep order stable forever within v4.
MIME_TABLE = [
    "application/octet-stream",
    "text/html", "application/xhtml+xml", "text/css", "text/javascript",
    "application/javascript", "application/json", "application/xml",
    "text/xml", "text/plain", "text/csv", "text/tab-separated-values",
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml",
    "image/bmp", "image/x-icon", "image/avif",
    "font/woff", "font/woff2", "font/ttf", "font/otf", "application/vnd.ms-fontobject",
    "application/pdf", "application/postscript",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/mp4", "audio/aac", "audio/flac", "audio/opus",
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
    "text/markdown", "application/zip", "application/gzip", "application/x-7z-compressed",
]
MIME_TO_ID = {m: i for i, m in enumerate(MIME_TABLE)}
ID_TO_MIME = {i: m for i, m in enumerate(MIME_TABLE)}

_BUNDLE_EXTS = {
    ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp",
    ".gif":"image/gif", ".svg":"image/svg+xml", ".bmp":"image/bmp", ".ico":"image/x-icon",
    ".avif":"image/avif", ".woff":"font/woff", ".woff2":"font/woff2", ".ttf":"font/ttf",
    ".otf":"font/otf", ".eot":"application/vnd.ms-fontobject", ".mp3":"audio/mpeg",
    ".ogg":"audio/ogg", ".oga":"audio/ogg", ".wav":"audio/wav", ".m4a":"audio/mp4",
    ".aac":"audio/aac", ".flac":"audio/flac", ".opus":"audio/opus", ".mp4":"video/mp4",
    ".m4v":"video/mp4", ".webm":"video/webm", ".ogv":"video/ogg", ".mov":"video/quicktime",
    ".html":"text/html", ".htm":"text/html", ".xhtml":"application/xhtml+xml", ".pdf":"application/pdf",
    ".eps":"application/postscript", ".txt":"text/plain", ".md":"text/markdown", ".markdown":"text/markdown",
    ".csv":"text/csv", ".tsv":"text/tab-separated-values", ".json":"application/json", ".xml":"application/xml",
    ".css":"text/css", ".js":"text/javascript", ".mjs":"text/javascript",
}
_EXT_BY_MIME = {
    "image/png":".png", "image/jpeg":".jpg", "image/webp":".webp", "image/gif":".gif",
    "image/svg+xml":".svg", "image/bmp":".bmp", "image/x-icon":".ico", "image/avif":".avif",
    "font/woff":".woff", "font/woff2":".woff2", "font/ttf":".ttf", "font/otf":".otf",
    "application/vnd.ms-fontobject":".eot", "audio/mpeg":".mp3", "audio/ogg":".ogg", "audio/wav":".wav",
    "audio/mp4":".m4a", "audio/aac":".aac", "audio/flac":".flac", "audio/opus":".opus",
    "video/mp4":".mp4", "video/webm":".webm", "video/ogg":".ogv", "video/quicktime":".mov",
    "text/html":".html", "application/xhtml+xml":".xhtml", "application/pdf":".pdf",
    "application/postscript":".eps", "text/plain":".txt", "text/markdown":".md",
    "text/csv":".csv", "text/tab-separated-values":".tsv", "application/json":".json",
    "application/xml":".xml", "text/xml":".xml", "text/css":".css", "text/javascript":".js",
    "application/javascript":".js", "application/octet-stream":".bin",
}

_HTML_RE = re.compile(r"\.html?$", re.I)
_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*?)>([\s\S]*?)</script\s*>", re.I)
_CSV_TYPE_RE = re.compile(r"\btype\s*=\s*(['\"])text/csv\1", re.I)
_CSV_KEY_RE = re.compile(r"\bdata-key\s*=\s*(['\"])([^'\"]+)\1", re.I)
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:", "#", "blob:", "about:", "ftp://", "ws://", "wss://", "file:")
_SAFE_KEY_RE = re.compile(r"^[\w\-./ @+]+$", re.UNICODE)
_ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tbz2", ".txz", ".tar.bz2", ".tar.xz")
# Resource types accepted by -l / -x. This table is the single source for the
# --help text, error messages and the generated table in docs/CLI.md.
TYPE_INFO = [
    ("all",    "Every resource in the package",                  "everything readable without an extra password"),
    ("docs",   "Pages, styles, scripts and documents",           "HTML/XHTML, CSS, JavaScript (.js, .mjs), XML, Markdown, plain text, PDF, EPS"),
    ("images", "Raster images",                                  "PNG, JPEG, WebP, GIF, BMP, ICO, AVIF"),
    ("vector", "Vector graphics",                                "SVG"),
    ("fonts",  "Web fonts",                                      "WOFF, WOFF2, TTF, OTF, EOT"),
    ("audio",  "Audio",                                          "MP3, OGG/Opus, WAV, M4A, AAC, FLAC"),
    ("videos", "Video",                                          "MP4, WebM, OGV, MOV"),
    ("media",  "Images + vector + audio + video",                "images, vector, audio and videos together"),
    ("csv",    "Tables",                                         "CSV and TSV, including tables embedded in the page as <script type=\"text/csv\">"),
    ("data",   "Structured data",                                "JSON, XML, CSV, TSV"),
    ("text",   "Anything stored as text",                        "docs + data + SVG (everything you can open in a text editor)"),
    ("other",  "Everything else",                                "archives (ZIP, 7z, GZIP), unknown binaries"),
    ("pack",   "Hidden bundle contents",                         "encrypted payload added with -b PATH hidden; needs its own password"),
]
_LIST_TYPES = tuple(t[0] for t in TYPE_INFO)
_TYPE_ORDER_HINT = ", ".join(_LIST_TYPES)


def _valid_type_list(value: str) -> bool:
    parts = [x.strip().lower() for x in value.split(',') if x.strip()]
    return bool(parts) and all(x in _LIST_TYPES for x in parts)


# Merge/reference regexes. They intentionally handle common static references,
# while the docs explicitly do not promise a JavaScript bundler/module resolver.
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT_SRC_RE = re.compile(r"<script\b([^>]*?)\bsrc\s*=\s*(['\"])([^'\"]+)\2([^>]*)>[\s\S]*?</script\s*>", re.I)
_MEDIA_TAG_RE = re.compile(r"<\s*(img|source|video|audio|track|iframe|embed|input|a)\b[^>]*>", re.I)
_SRCSET_RE = re.compile(r"\bsrcset\s*=\s*(['\"])([^'\"]+)\1", re.I)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>([\s\S]*?)</style\s*>", re.I)
_STYLE_ATTR_RE = re.compile(r"(\bstyle\s*=\s*(['\"]))(.*?)\2", re.I | re.S)
_CSS_IMPORT_URL_RE = re.compile(r"@import\s+url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
_CSS_IMPORT_STR_RE = re.compile(r"@import\s+(['\"])([^'\"]+)\1", re.I)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
_DATA_URI_RE = re.compile(r"data:([^,\s]+),([A-Za-z0-9+/=\s]+)", re.I)
_MODULE_RE = re.compile(r"\b(?:import|export)\s+(?:[^'\"]*?\sfrom\s*)?(['\"])([^'\"]+)\1", re.I)
_IMPORT_CALL_RE = re.compile(r"\bimport\s*\(\s*(['\"])([^'\"]+)\1\s*\)", re.I)
_NEW_URL_RE = re.compile(r"new\s+URL\(\s*(['\"])([^'\"]+)\1\s*,\s*import\.meta\.url\s*\)", re.I)
_FETCH_RE = re.compile(r"\bfetch\(\s*(['\"])([^'\"]+)\1\s*\)", re.I)


def enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        for hid in (-11, -12):
            h = k.GetStdHandle(hid)
            if h in (0, -1, None):
                continue
            mode = wintypes.DWORD(0)
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


try:
    enable_windows_ansi()
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
if _USE_COLOR:
    C = {k:v for k,v in dict(reset='\x1b[0m',dim='\x1b[2m',bold='\x1b[1m',red='\x1b[31m',green='\x1b[32m',yellow='\x1b[33m',cyan='\x1b[36m',gray='\x1b[90m').items()}
else:
    C = {k:"" for k in ('reset','dim','bold','red','green','yellow','cyan','gray')}


def out(*a):
    print(*a)


def warn(*a):
    print(f"{C['yellow']}warning:{C['reset']}", *a, file=sys.stderr)


def err(*a):
    print(f"{C['red']}error:{C['reset']}", *a, file=sys.stderr)


def vlog(opts, *a):
    if opts.verbose:
        print(f"  {C['gray']}[v]{C['reset']}", *a, file=sys.stderr)


def size_str(n: int) -> str:
    x = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if x < 1024 or u == "GB":
            return f"{x:.1f} {u}" if u != "B" else f"{int(x)} B"
        x /= 1024
    return f"{int(n)} B"


def ratio_str(before: int, after: int) -> str:
    """Size comparison for humans: '46.8% · 2.14×'.

    The percentage is the new size as a share of the original (100 * after / before),
    so smaller output is below 100% and larger output is above it; no signs needed.
    The factor is how many times larger the original is (above 1× = smaller output).
    """
    if before <= 0:
        return "n/a"
    pct = after * 100.0 / before
    factor = (before / after) if after else float("inf")
    fac = f"{factor:.2f}×" if factor != float("inf") else "∞×"
    return f"{pct:.1f}% · {fac}"


def percent_of(part: int, whole: int) -> str:
    return f"{part * 100.0 / whole:.1f}%" if whole > 0 else "n/a"


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def write_bytes(path: str, data: bytes):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def write_text(path: str, text: str):
    write_bytes(path, text.encode("utf-8"))


def escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;"))


def escape_text(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

# ---------- Z85 ----------
def z85_encode(buf: bytes) -> str:
    pad = (4 - (len(buf) & 3)) & 3
    data = buf + b"\0" * pad if pad else buf
    out_parts = []
    for i in range(0, len(data), 4):
        v = int.from_bytes(data[i:i+4], "big")
        chars = [""] * 5
        for j in range(4, -1, -1):
            chars[j] = Z85A[v % 85]
            v //= 85
        out_parts.append("".join(chars))
    return "".join(out_parts)


def z85_decode(s: str, size: int) -> bytes:
    s = re.sub(r"\s+", "", s)
    expected = ((size + 3) // 4) * 5
    if len(s) != expected:
        raise ValueError(f"invalid Z85 length: expected {expected}, got {len(s)}")
    outb = bytearray()
    for i in range(0, len(s), 5):
        v = 0
        for ch in s[i:i+5]:
            o = ord(ch)
            if o >= 128 or Z85M[o] < 0:
                raise ValueError(f"bad Z85 character at offset {i}")
            v = v * 85 + Z85M[o]
        outb.extend(v.to_bytes(4, "big"))
        if len(outb) >= size:
            break
    return bytes(outb[:size])


def z85_script_safe(buf: bytes) -> str:
    s = z85_encode(buf)
    # Script elements are raw-text: a literal </script sequence terminates the
    # element before our decoder can see it. Insert ignored whitespace instead
    # of a backslash; backslash is not part of the Z85 alphabet and would make
    # the payload invalid. Both Python and JS decoders remove whitespace.
    if "</script" not in s.lower():
        return s
    return re.sub(r"</script", "</\nscript", s, flags=re.I)

# ---------- crypto ----------
def _get_aesgcm():
    global _AESGCM, _CRYPTOGRAPHY_ERROR
    if _AESGCM is not None:
        return _AESGCM
    if _CRYPTOGRAPHY_ERROR is not None:
        raise RuntimeError(
            "cryptography is unavailable for this Python interpreter "
            f"({sys.executable}): {_CRYPTOGRAPHY_ERROR}. "
            "Install it with: python -m pip install cryptography"
        ) from _CRYPTOGRAPHY_ERROR
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        _AESGCM = AESGCM
        return _AESGCM
    except Exception as exc:
        _CRYPTOGRAPHY_ERROR = exc
        raise RuntimeError(
            "cryptography could not be imported by this Python interpreter "
            f"({sys.executable}): {exc}. "
            "Install/reinstall it with: python -m pip install cryptography"
        ) from exc


def cryptography_status():
    try:
        aes = _get_aesgcm()
        module = __import__("cryptography")
        return {"available": True, "version": getattr(module, "__version__", "unknown"),
                "interpreter": sys.executable, "implementation": getattr(aes, "__module__", "unknown")}
    except Exception as exc:
        return {"available": False, "version": None, "interpreter": sys.executable, "error": str(exc)}


def ensure_core_dependencies():
    """Encryption is a core feature, so its library is a requirement, not an extra."""
    status = cryptography_status()
    if not status.get("available"):
        exe = sys.executable
        raise RuntimeError(
            "Arcager needs the 'cryptography' package (it provides the encryption that is built into Arcager), "
            f"but this Python cannot use it ({exe}).\n"
            f"  Install it with:  {exe} -m pip install -r requirements.txt\n"
            f"  (or just:         {exe} -m pip install cryptography)\n"
            f"  Reason: {status.get('error', 'unknown')}")


def terser_info():
    """(path, version, problem) for the optional Terser minifier. Never raises."""
    path = shutil.which("terser")
    if not path:
        return None, None, None
    try:
        r = subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        text = (r.stdout or b"").decode("utf-8", errors="replace").strip()
        m = re.search(r"(\d+\.\d+(?:\.\d+)?\S*)", text)
        if r.returncode == 0 and m:
            return path, m.group(1), None
        return path, None, "it did not report a version (" + ((r.stderr or b"").decode("utf-8", errors="replace").strip()[:80] or f"exit code {r.returncode}") + ")"
    except Exception as exc:
        return path, None, f"it could not be run ({exc})"


def version_text() -> str:
    """Text printed by -V: Arcager's version and which components it can use."""
    rows = [f"arcager {VERSION}", f"  Python        {sys.version.split()[0]}  ({sys.executable})"]
    crypto = cryptography_status()
    rows.append("  cryptography  " + (f"{crypto['version']}  (required, encryption)" if crypto.get("available")
                else "NOT AVAILABLE  (required: python -m pip install -r requirements.txt)"))
    path, ver, problem = terser_info()
    if path is None:
        rows.append("  Terser        not installed  (optional, for --minify: npm install -g terser)")
    elif ver:
        rows.append(f"  Terser        {ver}  ({path})")
    else:
        rows.append(f"  Terser        found at {path} but {problem}  (--minify will leave the loader unminified)")
    rows.append("  Brotli        " + (f"{getattr(_brotli, '__version__', 'installed')}  (optional, for -c brotli)" if HAS_BROTLI
                else "not installed  (optional, for -c brotli: python -m pip install brotli)"))
    rows.append("  Pillow        " + (f"{getattr(sys.modules.get('PIL'), '__version__', 'installed')}  (optional, for lossy / --strip-metadata)" if HAS_PIL
                else "not installed  (optional, for lossy / --strip-metadata: python -m pip install Pillow)"))
    return "\n".join(rows)


def prompt_password(label: str = "Password: ", confirm: bool = False) -> str:
    """Read a password consistently in TTY and piped/non-interactive modes."""
    if not sys.stdin.isatty():
        first = sys.stdin.readline().rstrip("\r\n")
        if not first:
            raise RuntimeError("password required")
        if confirm:
            second = sys.stdin.readline().rstrip("\r\n")
            if first != second:
                raise RuntimeError("passwords do not match")
        return first
    while True:
        try:
            first = getpass.getpass(label)
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(130)
        if not first:
            warn("password must not be empty")
            continue
        if not confirm:
            return first
        try:
            second = getpass.getpass("Confirm password: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(130)
        if first != second:
            warn("passwords do not match")
            continue
        return first


def derive_key(password: str, salt_b64: str, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    salt = base64.b64decode(salt_b64)
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def aes_encrypt(key: bytes, iv_b64: str, data: bytes) -> bytes:
    return _get_aesgcm()(key).encrypt(base64.b64decode(iv_b64), data, None)


def aes_decrypt(key: bytes, iv_b64: str, data: bytes) -> bytes:
    if len(data) < GCM_TAG_LEN:
        raise ValueError("ciphertext too short")
    return _get_aesgcm()(key).decrypt(base64.b64decode(iv_b64), data, None)

# ---------- compression ----------
def compress_bytes(data: bytes, algo: int) -> bytes:
    if algo == 0:
        return data
    if algo == 1:
        return gzip.compress(data, compresslevel=9, mtime=0)
    if algo == 2:
        if not HAS_BROTLI:
            raise RuntimeError("Brotli compression requires the 'brotli' package (pip install brotli)")
        return _brotli.compress(data, quality=11, mode=_brotli.MODE_GENERIC, lgwin=24)
    raise ValueError("bad compression algorithm")


def decompress_bytes(data: bytes, algo: int) -> bytes:
    if algo == 0:
        return data
    if algo == 1:
        return gzip.decompress(data)
    if algo == 2:
        if not HAS_BROTLI:
            raise RuntimeError("Brotli support missing")
        return _brotli.decompress(data)
    raise ValueError("unsupported compression algorithm")

# ---------- types ----------
def mime_for(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in _BUNDLE_EXTS:
        return _BUNDLE_EXTS[ext]
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"


def normalize_mime(mime: str) -> str:
    m = mime.lower().split(";", 1)[0].strip()
    return m


def mime_id(mime: str) -> int:
    m = normalize_mime(mime)
    if m not in MIME_TO_ID:
        return MIME_TO_ID["application/octet-stream"]
    return MIME_TO_ID[m]


_DOC_MIMES = frozenset((
    "text/html", "application/xhtml+xml", "text/css", "text/javascript",
    "application/javascript", "application/x-javascript", "text/plain",
    "text/markdown", "application/pdf", "application/postscript",
    "application/xml", "text/xml",
))
_DATA_MIMES = frozenset(("application/json",))   # XML is a document (docs) and is also matched by the "data" type


def media_category(mime: str) -> str:
    """Primary resource type of a MIME (the TYPE column of -l). See TYPE_INFO."""
    m = normalize_mime(mime)
    if m in ("text/csv", "text/tab-separated-values"):
        return "csv"
    if m == "image/svg+xml":
        return "vector"
    if m.startswith("image/"):
        return "images"
    if m.startswith("video/"):
        return "videos"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("font/") or m == "application/vnd.ms-fontobject":
        return "fonts"
    if m in _DATA_MIMES:
        return "data"
    if m in _DOC_MIMES:
        return "docs"
    if m.startswith("text/"):
        return "docs"
    return "other"


def is_textual(mime: str) -> bool:
    m = normalize_mime(mime)
    return (m.startswith("text/") or m in ("application/json", "application/xml", "application/xhtml+xml", "application/javascript", "application/x-javascript"))


def matches_type(mime: str, typ: str) -> bool:
    """True when a resource of this MIME belongs to the -l/-x type `typ`."""
    if typ == "all":
        return True
    cat = media_category(mime)
    if typ == "media":
        return cat in ("images", "vector", "videos", "audio")
    if typ == "data":
        return cat in ("data", "csv") or normalize_mime(mime) in ("application/xml", "text/xml")
    if typ == "text":
        return is_textual(mime) or normalize_mime(mime) == "image/svg+xml"
    return typ == cat


def extless(path: str) -> str:
    return str(Path(path).with_suffix("")) if Path(path).suffix else path


def safe_key(key: str, src: str):
    if not key or not _SAFE_KEY_RE.match(key) or key.endswith(("/", ".")) or ".." in key.split("/"):
        raise RuntimeError(f"unsafe bundle key {key!r} from {src}")

# ---------- CSV ----------
_CSV_SRC_RE = re.compile(r"\bdata-origin\s*=\s*(['\"])([^'\"]+)\1", re.I)
_SCRIPT_END_ESC_RE = re.compile(r"<(\\+)/(script)", re.I)


def escape_for_script(raw: str) -> str:
    """Make text safe inside <script>…</script>, reversibly.

    '</script' becomes '<\\/script'; text that already contains '<\\/script'
    gains one more backslash, so unescape_from_script() restores the input exactly.
    """
    return re.sub(r"<(\\*)/(script)", lambda m: "<" + m.group(1) + "\\/" + m.group(2), raw, flags=re.I)


def unescape_from_script(body: str) -> str:
    return _SCRIPT_END_ESC_RE.sub(lambda m: "<" + m.group(1)[:-1] + "/" + m.group(2), body)


def scan_csv_blocks(html: str):
    """Find <script type="text/csv"> blocks.

    Each result has: key (arcager.csv key), body (text between the tags, outer
    CR/LF trimmed), src (the original file path recorded by --merge in
    data-origin, or None), exact (True for blocks written by Arcager: ``body_raw``
    then restores the original file text exactly), span (start, end offsets of
    the whole tag) and body_raw.
    """
    found = []
    for m in _SCRIPT_TAG_RE.finditer(html):
        attrs = m.group(1) or ""
        if not _CSV_TYPE_RE.search(attrs):
            continue
        km = _CSV_KEY_RE.search(attrs)
        sm = _CSV_SRC_RE.search(attrs)
        key = km.group(2) if km else f"csv{len(found)}"
        inner = m.group(2)
        exact = sm is not None and inner.startswith("\n") and inner.endswith("\n")
        raw = unescape_from_script(inner[1:-1]) if exact else inner.strip("\r\n")
        found.append({"key": key, "body": inner.strip("\r\n"), "src": sm.group(2) if sm else None,
                      "exact": exact, "body_raw": raw, "span": (m.start(), m.end())})
    return found


# ---------- HTML lossy minify ----------
_COMMENT_RE = re.compile(r"<!--(?!\[if)[\s\S]*?-->")
_WS_TAG_RE = re.compile(r">\s+<")
_SOURCEMAP_RE = re.compile(r"(?:/\*#|//#)\s*sourceMappingURL\s*=.*?\*/?$", re.M)


def minify_html_lossy(html: str):
    before = len(html)
    comments = len(_COMMENT_RE.findall(html))
    html = _COMMENT_RE.sub("", html)
    before_ws = len(html)
    html = _WS_TAG_RE.sub("><", html)
    html = _SOURCEMAP_RE.sub("", html)
    return html, comments, before_ws - len(html)

# ---------- optional JavaScript minification ----------
def maybe_minify_js(js: str, enabled: bool, vlog_fn=None) -> str:
    if not enabled:
        return js
    terser = shutil.which("terser")
    if not terser:
        if vlog_fn:
            vlog_fn("terser not found on PATH; --minify leaves runtime JavaScript unchanged")
        return js
    try:
        r = subprocess.run(
            [terser, "--compress", "passes=2,unsafe_arrows=true,toplevel=true", "--mangle", "toplevel"],
            input=js.encode("utf-8"), capture_output=True, check=False,
        )
    except OSError as e:
        if vlog_fn:
            vlog_fn(f"terser error: {e}")
        return js
    if r.returncode != 0:
        if vlog_fn:
            vlog_fn("terser error: " + r.stderr.decode("utf-8", errors="replace"))
        return js
    code = r.stdout.decode("utf-8")
    if vlog_fn:
        vlog_fn(f"terser: {len(js)} -> {len(code)} chars")
    return code

# ---------- media conversion ----------
def _pil_format_for_mime(mime: str):
    return {
        "image/png":"PNG", "image/jpeg":"JPEG", "image/webp":"WEBP", "image/gif":"GIF",
        "image/bmp":"BMP", "image/x-icon":"ICO",
    }.get(normalize_mime(mime))


def _webp_candidate(data: bytes, src_mime: str, strip_metadata: bool=False):
    if not HAS_PIL:
        return None, "Pillow unavailable"
    fmt = _pil_format_for_mime(src_mime)
    if fmt not in ("PNG", "JPEG", "BMP", "GIF"):
        return None, "format not supported by initial conversion whitelist"
    try:
        img = Image.open(io.BytesIO(data))
        nframes = getattr(img, "n_frames", 1)
        src_w, src_h = img.size
        info = dict(getattr(img, "info", {}))
        icc = info.get("icc_profile")
        exif = None if strip_metadata else info.get("exif")
        xmp = None if strip_metadata else info.get("xmp")
        if strip_metadata:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
        out = io.BytesIO()
        kwargs = {"format":"WEBP", "lossless":True}
        if icc:
            kwargs["icc_profile"] = icc
        if exif:
            kwargs["exif"] = exif
        if xmp:
            kwargs["xmp"] = xmp
        if nframes > 1:
            frames = []
            durations = []
            disposals = []
            for i in range(nframes):
                img.seek(i)
                frame = img.convert("RGBA")
                frames.append(frame.copy())
                durations.append(int(img.info.get("duration", 0)))
                disposals.append(img.info.get("disposal", 0))
            first = frames[0]
            kwargs.update(save_all=True, append_images=frames[1:], duration=durations, loop=int(info.get("loop", 0)))
            # Pillow uses disposal consistently for GIF/WebP animation in current versions.
            if any(d is not None for d in disposals):
                kwargs["disposal"] = disposals
            first.save(out, **kwargs)
        else:
            src = ImageOps.exif_transpose(img) if strip_metadata else img
            src.save(out, **kwargs)
        candidate = out.getvalue()
        # Basic validation + animation contract.
        chk = Image.open(io.BytesIO(candidate))
        if chk.size != (src_w, src_h):
            return None, "canvas dimensions changed"
        if nframes > 1:
            if getattr(chk, "n_frames", 1) != nframes:
                return None, "frame count changed"
            src_dur = []
            for i in range(nframes):
                img.seek(i); src_dur.append(int(img.info.get("duration", 0)))
            dst_dur = []
            for i in range(nframes):
                chk.seek(i); dst_dur.append(int(chk.info.get("duration", 0)))
            if src_dur != dst_dur:
                return None, "frame timing changed"
            if int(chk.info.get("loop", 0)) != int(info.get("loop", 0)):
                return None, "loop behavior changed"
            # Validate rendered frame pixels as decoded by Pillow. This catches
            # disposal/transparency mistakes without requiring a full browser.
            for i in range(nframes):
                img.seek(i); src_px = img.convert("RGBA").tobytes()
                chk.seek(i); dst_px = chk.convert("RGBA").tobytes()
                if src_px != dst_px:
                    return None, f"rendered animation frame {i} changed"
        return candidate, None
    except Exception as e:
        return None, f"conversion error: {e}"


def _validate_image_equivalence(original: bytes, candidate: bytes):
    """Verify that an explicit metadata-only rewrite did not change rendered content."""
    if not HAS_PIL:
        return False, "Pillow unavailable"
    try:
        src = Image.open(io.BytesIO(original))
        dst = Image.open(io.BytesIO(candidate))
        if src.size != dst.size:
            return False, "canvas dimensions changed"
        ns = getattr(src, "n_frames", 1); nd = getattr(dst, "n_frames", 1)
        if ns != nd:
            return False, "frame count changed"
        src_loop = int(src.info.get("loop", 0)) if ns > 1 else 0
        dst_loop = int(dst.info.get("loop", 0)) if nd > 1 else 0
        if src_loop != dst_loop:
            return False, "loop behavior changed"
        for i in range(ns):
            src.seek(i); dst.seek(i)
            if int(src.info.get("duration", 0)) != int(dst.info.get("duration", 0)):
                return False, f"frame timing changed at frame {i}"
            if src.convert("RGBA").tobytes() != dst.convert("RGBA").tobytes():
                return False, f"rendered pixels changed at frame {i}"
        return True, None
    except Exception as e:
        return False, f"validation error: {e}"


def _strip_jpeg_metadata_lossless(data: bytes):
    """Remove JPEG EXIF/XMP/comment segments without decoding/re-encoding pixels."""
    if not data.startswith(b"\xff\xd8"):
        return data, "not a JPEG stream"
    out = bytearray(data[:2]); pos = 2; changed = False
    while pos < len(data):
        if data[pos] != 0xFF:
            out.extend(data[pos:]); break
        start = pos
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data): return data, "truncated JPEG marker"
        marker = data[pos]; pos += 1
        # Standalone markers.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.extend(data[start:pos])
            if marker == 0xD9: out.extend(data[pos:]); return bytes(out), None
            continue
        if pos + 2 > len(data): return data, "truncated JPEG segment"
        seglen = int.from_bytes(data[pos:pos+2], "big")
        if seglen < 2 or pos + seglen > len(data): return data, "invalid JPEG segment length"
        payload = data[pos+2:pos+seglen]
        # APP1: EXIF or XMP metadata. COM: arbitrary comment text.
        is_xmp = payload.startswith(b"http://ns.adobe.com/xap/1.0/\x00") or payload.startswith(b"http://ns.adobe.com/xmp/1.0/")
        is_exif = payload.startswith(b"Exif\x00\x00")
        if marker in (0xE1, 0xFE) and (marker == 0xFE or is_exif or is_xmp):
            changed = True
        else:
            out.extend(data[start:pos+seglen])
        pos += seglen
        if marker == 0xDA:  # Scan data is opaque; copy the rest verbatim.
            out.extend(data[pos:]); return bytes(out), None if changed else "no removable JPEG metadata found"
    return bytes(out), None if changed else "no removable JPEG metadata found"


def _strip_metadata_image(data: bytes, mime: str):
    if not HAS_PIL:
        return data, "Pillow unavailable"
    fmt = _pil_format_for_mime(mime)
    if fmt not in ("PNG", "JPEG", "WEBP", "GIF", "BMP", "ICO"):
        return data, "format not supported for metadata stripping"
    if fmt == "JPEG":
        candidate, why = _strip_jpeg_metadata_lossless(data)
        if candidate != data and why is None:
            ok, vwhy = _validate_image_equivalence(data, candidate)
            return (candidate, None) if ok else (data, f"metadata strip rejected: {vwhy}")
        return data, why
    try:
        img = Image.open(io.BytesIO(data))
        original_n = getattr(img, "n_frames", 1)
        icc = img.info.get("icc_profile")
        out = io.BytesIO()
        kwargs = {"format": fmt}
        if icc:
            kwargs["icc_profile"] = icc
        if original_n > 1 and fmt in ("GIF", "WEBP"):
            frames=[]; durations=[]; disposals=[]
            for i in range(original_n):
                img.seek(i)
                frames.append(img.convert("RGBA").copy())
                durations.append(int(img.info.get("duration", 0)))
                disposals.append(img.info.get("disposal", 0))
            first=frames[0]
            kwargs.update(save_all=True, append_images=frames[1:], duration=durations, loop=int(img.info.get("loop",0)))
            if fmt == "GIF":
                first.save(out, **kwargs)
            else:
                kwargs["lossless"] = True
                first.save(out, **kwargs)
        else:
            src = ImageOps.exif_transpose(img)
            if fmt == "JPEG":
                # Metadata stripping must not silently turn a preservation-mode JPEG
                # into a low-quality re-encode. Pillow's "keep" options preserve
                # the source JPEG quantization/subsampling when available.
                kwargs["quality"] = "keep"
                kwargs["subsampling"] = "keep"
                kwargs.pop("icc_profile", None) if False else None
            src.save(out, **kwargs)
        candidate = out.getvalue()
        ok, why = _validate_image_equivalence(data, candidate)
        if not ok:
            return data, f"metadata strip rejected: {why}"
        return candidate, None
    except Exception as e:
        return data, f"metadata strip failed: {e}"


def choose_representation(data: bytes, src_mime: str, src_path: str, lossy: bool, strip_metadata: bool, diagnostics=None):
    final = data
    mime = normalize_mime(src_mime)
    flags = 0
    reason = None
    if lossy and mime in ("image/png", "image/jpeg", "image/bmp", "image/gif"):
        cand, why = _webp_candidate(data, mime, strip_metadata=strip_metadata)
        if cand is not None and len(cand) < len(final):
            final = cand
            mime = "image/webp"
            flags |= 0x01  # converted
            reason = "WebP candidate accepted"
        elif diagnostics is not None:
            diagnostics.append((src_path, "WebP rejected" if cand is None else "WebP larger", why or "size"))
    if strip_metadata and not (flags & 0x01):
        cleaned, why = _strip_metadata_image(data, mime)
        if cleaned != data:
            final = cleaned
            flags |= 0x02  # metadata stripped
        elif diagnostics is not None and why and why != "Pillow unavailable":
            diagnostics.append((src_path, "metadata strip unchanged", why))
    elif strip_metadata and (flags & 0x01):
        flags |= 0x02
    return final, mime, flags, reason

# ---------- binary v4 container ----------
def uvarint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varint negative")
    b = bytearray()
    while n >= 0x80:
        b.append((n & 0x7f) | 0x80); n >>= 7
    b.append(n)
    return bytes(b)


def read_uvarint(data: bytes, pos: int):
    result=0; shift=0
    while pos < len(data):
        b=data[pos]; pos+=1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


@dataclasses.dataclass
class Node:
    key: str
    data: bytes
    mime: str
    source_mime: str
    priority: int
    flags: int = 0
    is_primary: bool = True
    source: str = ""
    id: int = 0


@dataclasses.dataclass
class Physical:
    data: bytes
    mime: str
    source_mime: str
    flags: int
    stream_id: int = 0
    offset: int = 0
    id: int = 0


class V4ContainerBuilder:
    def __init__(self, algo: int, strip_metadata=False, lossy=False, diagnostics=None):
        self.algo = algo
        self.strip_metadata = strip_metadata
        self.lossy = lossy
        self.convert_diags = diagnostics if diagnostics is not None else []
        self.nodes: List[Node] = []
        self.physicals: List[Physical] = []
        self._phys_by_key = {}
        self._path_to_node = {}
        self.stats = {
            "logical": 0, "physical": 0, "duplicate_count": 0,
            "duplicate_bytes": 0, "conversion_accepted": 0,
            "metadata_stripped": 0, "streams": 0,
            "stored_streams": 0, "compressed_streams": 0,
            "raw_bytes": 0, "stored_bytes": 0,
            "solid_attempts": [],
        }

    def add_node(self, key, data, mime, *, source_mime=None, priority=65535, flags=0, is_primary=True, source=""):
        key = key.replace("\\", "/").lstrip("/")
        if not key:
            raise ValueError("empty logical resource path")
        existing = self._path_to_node.get(key)
        if existing is not None:
            if priority < existing.priority:
                existing.priority = priority
            existing.flags |= flags
            return existing
        source_mime = normalize_mime(source_mime or mime)
        final, final_mime, conv_flags, reason = choose_representation(
            data, source_mime, source or key, self.lossy, self.strip_metadata, self.convert_diags)
        if conv_flags & 0x01:
            self.stats["conversion_accepted"] += 1
        if conv_flags & 0x02:
            self.stats["metadata_stripped"] += 1
        flags |= conv_flags
        digest = hashlib.blake2b(final, digest_size=16).digest()
        key_id = (len(final), normalize_mime(final_mime), digest)
        physical = self._phys_by_key.get(key_id)
        if physical is not None and physical.data != final:
            physical = None
        if physical is None:
            physical = Physical(final, normalize_mime(final_mime), source_mime, flags, id=len(self.physicals))
            self.physicals.append(physical)
            self._phys_by_key[key_id] = physical
        else:
            self.stats["duplicate_count"] += 1
            self.stats["duplicate_bytes"] += len(final)
        node = Node(key, final, normalize_mime(final_mime), source_mime, priority, flags, is_primary, source, len(self.nodes))
        node.physical_id = physical.id  # dynamic attr by design for compact implementation
        self.nodes.append(node)
        self._path_to_node[key] = node
        return node

    def add_alias(self, alias, node: Node, priority=65535):
        if not alias or alias in self._path_to_node:
            return
        alias_node = Node(alias.replace("\\", "/").lstrip("/"), node.data, node.mime, node.source_mime,
                          priority, node.flags | 0x08, False, node.source, len(self.nodes))
        alias_node.physical_id = node.physical_id
        self.nodes.append(alias_node)
        self._path_to_node[alias_node.key] = alias_node

    def lookup(self, key):
        return self._path_to_node.get(key)

    def _group_streams(self):
        groups = defaultdict(list)
        prio_by_phys = defaultdict(lambda: 65535)
        key_by_phys = {}
        for n in self.nodes:
            if n.is_primary:
                prio_by_phys[n.physical_id] = min(prio_by_phys[n.physical_id], n.priority)
            key_by_phys.setdefault(n.physical_id, n.key)
        for p in self.physicals:
            groups[p.mime].append(p)
        for arr in groups.values():
            # Must be monotonic by physical id so offset deltas in the
            # physical table (written in id order) stay non-negative.
            arr.sort(key=lambda p: p.id)
        return groups

    @staticmethod
    def _varint_len(value: int) -> int:
        return len(uvarint(value))

    def _layout_cost(self, groups, decisions, candidates, candidate_sizes, raw_sizes):
        """Return exact binary-container cost for a stream-selection plan.

        The calculation includes stream metadata, physical offset varints, the
        string/logical tables, the v4 header, and payload bytes. It deliberately
        excludes the outer Z85/HTML shell because that layer is monotonic with
        container size.
        """
        streams=[]
        payload_bytes=0
        stored_members=[]
        for mime in sorted(groups):
            members=groups[mime]
            if decisions.get(mime, False):
                sid=len(streams)
                off=payload_bytes
                payload_size=candidate_sizes[mime]
                raw_size=raw_sizes[mime]
                streams.append({"mime":mime,"members":members,"raw_size":raw_size,
                                "off":off,"payload_size":payload_size,"algo":self.algo})
                payload_bytes += payload_size
            else:
                stored_members.extend(members)
        if stored_members:
            stored_members.sort(key=lambda p:p.id)
            sid=len(streams)
            off=payload_bytes
            raw_size=sum(len(p.data) for p in stored_members)
            streams.append({"mime":"application/octet-stream","members":stored_members,
                            "raw_size":raw_size,"off":off,"payload_size":raw_size,"algo":0})
            payload_bytes += raw_size

        stream_by_phys={}
        offset_by_phys={}
        for sid,stream in enumerate(streams):
            cursor=0
            for member in stream["members"]:
                stream_by_phys[member.id]=sid
                offset_by_phys[member.id]=cursor
                cursor += len(member.data)

        strings=[n.key for n in self.nodes]
        meta_len=16
        prev_off=0
        for stream in streams:
            meta_len += 3
            meta_len += self._varint_len(len(stream["members"]))
            meta_len += self._varint_len(stream["off"]-prev_off)
            prev_off=stream["off"]
            meta_len += self._varint_len(stream["raw_size"])
            meta_len += 4  # stream CRC

        prev_by_stream=defaultdict(int)
        for physical in self.physicals:
            sid=stream_by_phys[physical.id]
            off=offset_by_phys[physical.id]
            meta_len += self._varint_len(sid)
            meta_len += self._varint_len(off-prev_by_stream[sid])
            prev_by_stream[sid]=off
            meta_len += 6

        for value in strings:
            encoded=value.encode("utf-8")
            meta_len += self._varint_len(len(encoded)) + len(encoded)
        str_index={value:i for i,value in enumerate(strings)}
        for node in self.nodes:
            meta_len += self._varint_len(str_index[node.key])
            meta_len += self._varint_len(node.physical_id)
            meta_len += self._varint_len(node.priority)
            meta_len += 2
        return V4_HEADER_SIZE + meta_len + payload_bytes, streams, stream_by_phys, offset_by_phys

    def serialize(self) -> bytes:
        groups=self._group_streams()
        self.stats["solid_attempts"]=[]
        candidate_by_mime={}
        candidate_sizes={}
        raw_sizes={}
        decisions={}

        for mime in sorted(groups):
            members=groups[mime]
            raw=b"".join(p.data for p in members)
            candidate=compress_bytes(raw,self.algo) if self.algo else raw
            candidate_sizes[mime]=len(candidate)
            candidate_by_mime[mime]=candidate if self.algo and len(candidate)<len(raw) else None
            raw_sizes[mime]=len(raw)
            decisions[mime]=bool(self.algo and len(candidate)<len(raw))

        # Refine the payload-size decision using the complete binary-container
        # serialization cost. This catches tiny compression wins that are erased
        # by creating another stream record and its physical-offset metadata.
        while True:
            current_cost,_,_,_=self._layout_cost(groups,decisions,candidate_by_mime,candidate_sizes,raw_sizes)
            changed=False
            for mime in sorted(groups):
                alternative=decisions.copy()
                alternative[mime]=not decisions[mime]
                if alternative[mime] and not (self.algo and candidate_by_mime[mime] is not None and candidate_sizes[mime]<raw_sizes[mime]):
                    continue
                alternative_cost,_,_,_=self._layout_cost(groups,alternative,candidate_by_mime,candidate_sizes,raw_sizes)
                if alternative_cost<current_cost:
                    decisions=alternative
                    current_cost=alternative_cost
                    changed=True
            if not changed:
                break

        final_cost,planned_streams,stream_by_phys,offset_by_phys=self._layout_cost(
            groups,decisions,candidate_by_mime,candidate_sizes,raw_sizes)

        streams=[]
        payload_parts=[]
        for sid,planned in enumerate(planned_streams):
            raw=b"".join(p.data for p in planned["members"])
            if planned["algo"]:
                payload=candidate_by_mime[planned["mime"]]
            else:
                payload=raw
            crc=binascii.crc32(raw)&0xffffffff
            payload_parts.append(payload)
            streams.append({"mime":planned["mime"],"members":planned["members"],
                            "raw_size":planned["raw_size"],"off":planned["off"],
                            "crc":crc,"algo":planned["algo"]})

        for physical in self.physicals:
            physical.stream_id=stream_by_phys[physical.id]
            physical.offset=offset_by_phys[physical.id]

        strings=[n.key for n in self.nodes]
        str_index={value:i for i,value in enumerate(strings)}
        meta=bytearray()
        meta += struct.pack("<I",len(streams))
        meta += struct.pack("<I",len(self.physicals))
        meta += struct.pack("<I",len(self.nodes))
        meta += struct.pack("<I",len(strings))
        prev_off=0
        for stream in streams:
            meta += struct.pack("<HB",mime_id(stream["mime"]),stream["algo"])
            meta += uvarint(len(stream["members"]))
            meta += uvarint(stream["off"]-prev_off); prev_off=stream["off"]
            meta += uvarint(stream["raw_size"])
            meta += struct.pack("<I",stream["crc"])
        prev_by_stream=defaultdict(int)
        for physical in self.physicals:
            sid=physical.stream_id
            meta += uvarint(sid)
            meta += uvarint(physical.offset-prev_by_stream[sid]); prev_by_stream[sid]=physical.offset
            meta += struct.pack("<HBBH",mime_id(physical.mime),physical.flags&0xff,0,mime_id(physical.source_mime))
        for value in strings:
            encoded=value.encode("utf-8")
            meta += uvarint(len(encoded)); meta += encoded
        for node in self.nodes:
            meta += uvarint(str_index[node.key])
            meta += uvarint(node.physical_id)
            meta += uvarint(node.priority)
            meta += struct.pack("<BB",node.flags&0xff,mime_id(node.source_mime))
        metadata=bytes(meta)
        payload=b"".join(payload_parts)
        flags=0
        header=bytearray(V4_HEADER_SIZE)
        struct.pack_into("<8sBBBBIIIIIQII16s",header,0,V4_MAGIC,V4_VERSION,flags,self.algo,0,
                         len(streams),len(self.physicals),len(self.nodes),len(strings),len(metadata),
                         len(payload),binascii.crc32(metadata)&0xffffffff,0,b"\0"*16)
        hcrc=binascii.crc32(bytes(header[:44]))&0xffffffff
        struct.pack_into("<I",header,44,hcrc)
        container=bytes(header)+metadata+payload
        if len(container)<V4_HEADER_SIZE+len(metadata):
            raise AssertionError("container assembly failure")
        if len(container)!=final_cost:
            raise AssertionError(f"v4 layout cost mismatch: planned {final_cost}, serialized {len(container)}")

        for mime in sorted(groups):
            comp=candidate_by_mime[mime]
            raw_size=raw_sizes[mime]
            can_compress=bool(self.algo and comp is not None and candidate_sizes[mime]<raw_size)
            comp_plan=decisions.copy(); comp_plan[mime]=can_compress
            stored_plan=decisions.copy(); stored_plan[mime]=False
            comp_cost=self._layout_cost(groups,comp_plan,candidate_by_mime,candidate_sizes,raw_sizes)[0]
            stored_cost=self._layout_cost(groups,stored_plan,candidate_by_mime,candidate_sizes,raw_sizes)[0]
            accepted=decisions[mime]
            comp_size=candidate_sizes[mime]
            reason=("accepted" if accepted else
                    ("stored: serialized metadata/stream cost" if can_compress else "stored: candidate not smaller"))
            self.stats["solid_attempts"].append({
                "mime":mime,"algorithm":self.algo,"raw_bytes":raw_size,
                "candidate_bytes":comp_size,"saved_bytes":raw_size-comp_size,
                "accepted":accepted,"serialized_compressed_bytes":comp_cost,
                "serialized_stored_bytes":stored_cost,"decision_reason":reason,
            })

        self.stats["logical"]=sum(1 for n in self.nodes if n.is_primary)   # runtime alias names are not separate resources
        self.stats["aliases"]=sum(1 for n in self.nodes if not n.is_primary)
        self.stats["physical"]=len(self.physicals)
        self.stats["streams"]=len(streams)
        self.stats["raw_bytes"]=sum(s["raw_size"] for s in streams)
        self.stats["stored_bytes"]=len(payload)
        self.stats["compressed_streams"]=sum(1 for s in streams if s["algo"])
        self.stats["stored_streams"]=sum(1 for s in streams if not s["algo"])
        return container

    @staticmethod
    def parse(container: bytes):
        if len(container) < V4_HEADER_SIZE or container[:8] != V4_MAGIC:
            raise ValueError("not an Arcager v4 container")
        fields = struct.unpack_from("<8sBBBBIIIIIQII16s", container, 0)
        magic, version, flags, default_algo, _rsv, scount, pcount, lcount, strcount, meta_len, payload_len, meta_crc, hcrc, _reserved = fields
        if version != V4_VERSION:
            raise ValueError(f"unsupported container version {version}")
        calc = binascii.crc32(container[:44]) & 0xffffffff
        if calc != hcrc:
            raise ValueError("corrupt v4 header")
        meta_start=V4_HEADER_SIZE; meta_end=meta_start+meta_len; payload_end=meta_end+payload_len
        if payload_end > len(container):
            raise ValueError("truncated v4 payload")
        if payload_end != len(container):
            raise ValueError("unexpected trailing v4 container data")
        metadata=container[meta_start:meta_end]
        if binascii.crc32(metadata)&0xffffffff != meta_crc:
            raise ValueError("corrupt v4 metadata")
        pos=0
        def ru():
            nonlocal pos
            v,pos=read_uvarint(metadata,pos); return v
        sc,pc,lc,stc = struct.unpack_from("<IIII", metadata, pos); pos += 16
        if (sc,pc,lc,stc)!=(scount,pcount,lcount,strcount):
            raise ValueError("v4 count mismatch")
        streams=[]
        prev=0
        for sid in range(sc):
            if pos+3>len(metadata): raise ValueError("truncated stream table")
            mid, algo = struct.unpack_from("<HB", metadata, pos)
            pos += 3
            mcount = ru()
            off = prev + ru(); prev=off
            raw_size=ru()
            if pos+4>len(metadata): raise ValueError("truncated stream CRC")
            crc=struct.unpack_from("<I", metadata, pos)[0]; pos+=4
            if mid >= len(MIME_TABLE): raise ValueError("unknown v4 stream MIME id")
            if off > payload_len: raise ValueError("invalid v4 stream offset")
            streams.append({"mime": ID_TO_MIME[mid],"algo":algo,"member_count":mcount,"off":off,"raw_size":raw_size,"crc":crc})
        physical=[]
        prevp=defaultdict(int)
        for _ in range(pc):
            stream_id=ru()
            if stream_id >= sc: raise ValueError("invalid v4 physical stream reference")
            off=prevp[stream_id]+ru(); prevp[stream_id]=off
            if pos+6>len(metadata): raise ValueError("truncated physical record")
            mid, flags, _r, smid = struct.unpack_from("<HBBH", metadata, pos)
            pos+=6
            if mid >= len(MIME_TABLE) or smid >= len(MIME_TABLE): raise ValueError("unknown v4 physical MIME id")
            if off > streams[stream_id]["raw_size"]: raise ValueError("invalid v4 physical offset")
            physical.append({"stream":stream_id,"off":off,"mime":ID_TO_MIME[mid],"flags":flags,"source_mime":ID_TO_MIME[smid]})
        strings=[]
        for _ in range(stc):
            n=ru(); end=pos+n
            if end>len(metadata): raise ValueError("truncated string table")
            strings.append(metadata[pos:end].decode("utf-8")); pos=end
        logical=[]
        for _ in range(lc):
            sid=ru(); pid=ru(); priority=ru()
            if pos+2>len(metadata): raise ValueError("truncated logical record")
            flags=metadata[pos]; smid=metadata[pos+1]; pos+=2
            if sid>=len(strings) or pid>=len(physical): raise ValueError("invalid v4 record reference")
            logical.append({"path":strings[sid],"physical":pid,"priority":priority,"flags":flags,"source_mime":ID_TO_MIME.get(smid,"application/octet-stream")})
        counts=[0]*sc
        for p in physical: counts[p["stream"]] += 1
        for sid,s in enumerate(streams):
            if counts[sid] != s["member_count"]: raise ValueError("v4 stream member-count mismatch")
        if pos != len(metadata): raise ValueError("unexpected trailing v4 metadata")
        next_off=[0]*len(physical)
        by_stream=defaultdict(list)
        for idx,p in enumerate(physical): by_stream[p["stream"]].append(idx)
        for sid,idxs in by_stream.items():
            idxs.sort(key=lambda i: physical[i]["off"])
            for j,idx in enumerate(idxs):
                cur=physical[idx]["off"]
                nxt=physical[idxs[j+1]]["off"] if j+1<len(idxs) else streams[sid]["raw_size"]
                if nxt < cur or nxt > streams[sid]["raw_size"]: raise ValueError("invalid v4 physical boundaries")
                next_off[idx] = nxt
        return {"version":4,"flags":flags,"default_algo":default_algo,"streams":streams,"physical":physical,"logical":logical,
                "next_off":next_off,"payload":container[meta_end:meta_end+payload_len],"payload_offset":meta_end,"metadata_len":meta_len,"container":container}

# ---------- bundles ----------
def validate_key(key, src):
    safe_key(key, src)


def walk_bundle_dir(root: str, root_name: str, out_list: list):
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        for f in files:
            ext=Path(f).suffix.lower(); mime=_BUNDLE_EXTS.get(ext)
            if not mime: continue
            p=os.path.join(cur,f)
            rel=os.path.relpath(p,root).replace(os.sep,"/")
            key=str(Path(rel).with_suffix("")); validate_key(key,p)
            out_list.append({"key":key,"alias":f"{root_name}/{key}","data":read_bytes(p),"mime":mime,"src":p})


def read_bundle_archive(path: str, out_list: list):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name=info.filename.replace("\\","/")
                if name.endswith("/") or "__MACOSX" in name.split("/"): continue
                if info.flag_bits & 0x1:
                    raise RuntimeError(f"zip entry {name!r} in {path} is encrypted; bundle archive as hidden or decrypt it first")
                ext=Path(name).suffix.lower(); mime=_BUNDLE_EXTS.get(ext)
                if not mime: continue
                key=str(Path(name).with_suffix("")); validate_key(key,f"{path}:{name}")
                out_list.append({"key":key,"alias":None,"data":z.read(name),"mime":mime,"src":f"{path}:{name}"})
    else:
        with tarfile.open(path,"r:*") as t:
            for m in t.getmembers():
                if not m.isfile(): continue
                name=m.name.replace("\\","/")
                if "__MACOSX" in name.split("/"): continue
                ext=Path(name).suffix.lower(); mime=_BUNDLE_EXTS.get(ext)
                if not mime: continue
                key=str(Path(name).with_suffix("")); validate_key(key,f"{path}:{name}")
                fh=t.extractfile(m)
                if fh: out_list.append({"key":key,"alias":None,"data":fh.read(),"mime":mime,"src":f"{path}:{name}"})


def collect_visible_bundles(paths, vlog_fn):
    entries=[]
    for p in paths:
        ap=os.path.abspath(p)
        if not os.path.exists(ap):
            raise RuntimeError(f"bundle not found: {p}")
        # v4 intentionally refuses already-packed Arcager files as visible bundles.
        try:
            sig=read_signature(ap)
        except Exception:
            sig=None
        if sig is not None:
            warn(f"{p}: already-packed Arcager file skipped as visible bundle; use 'hidden' to store it as opaque data")
            continue
        if os.path.isdir(ap):
            walk_bundle_dir(ap, os.path.basename(ap.rstrip(os.sep)), entries)
        elif any(ap.lower().endswith(ext) for ext in _ARCHIVE_EXTS):
            read_bundle_archive(ap, entries)
        else:
            ext=Path(ap).suffix.lower(); mime=_BUNDLE_EXTS.get(ext)
            if not mime:
                raise RuntimeError(f"unsupported visible bundle format: {p}")
            key=Path(ap).stem; validate_key(key, ap)
            entries.append({"key":key,"alias":None,"data":read_bytes(ap),"mime":mime,"src":ap})
    total=sum(len(e['data']) for e in entries)
    if total>_BUNDLE_MAX_TOTAL: raise RuntimeError(f"visible bundles exceed {size_str(_BUNDLE_MAX_TOTAL)}")
    for e in entries: vlog_fn(f"bundle: {e['key']} ({e['mime']}, {size_str(len(e['data']))})")
    return entries


def zip_dir_bytes(root: str) -> bytes:
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_STORED) as z:
        root_abs=os.path.abspath(root)
        for cur, dirs, files in os.walk(root,followlinks=False):
            dirs.sort(); files.sort()
            for f in files:
                p=os.path.join(cur,f); arc=os.path.relpath(p,root_abs).replace(os.sep,"/")
                z.write(p,arc,compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def collect_hidden_bundles(paths):
    out_list=[]
    for p in paths:
        ap=os.path.abspath(p)
        if not os.path.exists(ap): raise RuntimeError(f"hidden bundle not found: {p}")
        if os.path.isdir(ap):
            data=zip_dir_bytes(ap); key=os.path.basename(ap.rstrip(os.sep))+".zip"
        else:
            data=read_bytes(ap); key=os.path.basename(ap)
        validate_key(key,ap); out_list.append({"key":key,"data":data,"src":ap})
    total=sum(len(e["data"]) for e in out_list)
    if total>_HIDDEN_MAX_TOTAL: raise RuntimeError(f"hidden payload exceeds {size_str(_HIDDEN_MAX_TOTAL)}")
    return out_list

# ---------- signature / scanning ----------
def read_signature(path: str):
    try:
        with open(path,"rb") as f:
            head=f.read(128).decode("ascii",errors="ignore")
    except Exception:
        return None
    m=SIG_RE.match(head.strip("\ufeff\r\n\t "))
    return int(m.group(1)) if m else None


def scan_dir(root: str, recursive: bool):
    files=[]; skipped=[]
    root=os.path.abspath(root)
    visited=set()
    def walk(d, depth):
        try: st=os.stat(d, follow_symlinks=False)
        except OSError: return
        key=(st.st_dev,st.st_ino) if hasattr(st,"st_ino") else os.path.realpath(d)
        if key in visited: return
        visited.add(key)
        try: names=sorted(os.listdir(d))
        except OSError: return
        for name in names:
            p=os.path.join(d,name)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    if recursive: walk(p,depth+1)
                    else: skipped.append(p)
                elif os.path.isfile(p) and _HTML_RE.search(name): files.append(p)
            except OSError: continue
    walk(root,0)
    return files, skipped

# ---------- path/reference helpers ----------
def is_external(url: str) -> bool:
    u=url.strip()
    if not u: return False
    return any(u.lower().startswith(p) for p in _EXTERNAL_PREFIXES)


def split_ref(url: str):
    # Keep query/fragment suffix separately; fragments do not influence resource identity.
    base=url
    suffix=""
    for sep in ("?", "#"):
        i=base.find(sep)
        if i>=0:
            suffix=base[i:]; base=base[:i]; break
    return base, suffix


def resolve_local(base_dir: str, url: str):
    base,_=split_ref(url)
    if is_external(url) or not base: return None
    base=urllib.parse.unquote(base)
    if base.startswith("/"): base=base.lstrip("/")
    return os.path.normpath(os.path.join(base_dir,base))


def rel_key(root: str, path: str) -> str:
    return os.path.relpath(path,root).replace(os.sep,"/")


def token_for(node_id: int, suffix: str="") -> str:
    return f"arcager-res:{node_id}{suffix}"


# ---------- dependency graph / merge ----------
@dataclasses.dataclass
class GraphNode:
    id: int
    key: str
    path: str
    mime: str
    source_mime: str
    data: bytes
    priority: int
    entry: bool=False
    transform: str="none"


class MergeGraph:
    def __init__(self, root_dir: str, index_path: str, opts):
        self.root_dir=os.path.abspath(root_dir)
        self.index_path=os.path.abspath(index_path)
        self.opts=opts
        self.nodes=[]
        self.by_path={}
        self.external=set(); self.missing=set(); self.unmergeable=set(); self.dynamic=set()
        self.unreferenced=[]     # (path, bytes) of files nothing links to
        self.unlinked_pages=[]   # other .html files in the folder that are not packed
        self.linked_pages=set()  # pages the packed page links to but merge mode cannot include
        self._queue=deque()
        self.bundle_nodes={}
        self.csv_inlined={}      # absolute path -> arcager.csv key (tables stored once, as inline blocks)
        self.csv_keys={}         # arcager.csv key -> absolute path

    def add_bundle_nodes(self, entries):
        for e in entries:
            n=self._get_or_create(f"__bundle__/{e['key']}", None, e['mime'], e['mime'], e['data'], 65535)
            self.bundle_nodes[e['key']]=n
            self.bundle_nodes[f"{e['key']}{Path('.'+Path(e['key']).suffix)}"] = n if Path(e['key']).suffix else n

    def _get_or_create(self, key, path, mime, source_mime, data, priority, entry=False):
        key=key.replace("\\","/").lstrip("/")
        existing=self.by_path.get(key)
        if existing:
            if priority<existing.priority: existing.priority=priority
            return existing
        n=GraphNode(len(self.nodes),key,path,mime,source_mime,data,priority,entry)
        self.nodes.append(n); self.by_path[key]=n
        self._queue.append(n)
        return n

    def _bundle_lookup(self, url: str):
        base,_=split_ref(url)
        c=[base, base[2:] if base.startswith("./") else base]
        if "." in base: c.append(str(Path(base).with_suffix("")))
        for x in c:
            if x in self.bundle_nodes: return self.bundle_nodes[x]
            if x in self.by_path: return self.by_path[x]
        return None

    def _static_ref(self, src_node: GraphNode, url: str, *, kind="resource", rewrite=True):
        if is_external(url):
            if kind not in ("navigation", "fragment"):
                self.external.add(url)
            return None
        base,suffix=split_ref(url)
        if not base: return None
        # data URI gets handled separately by hoist_data_uris
        if base.lower().startswith("data:"):
            return None
        abs_path=resolve_local(os.path.dirname(src_node.path) if src_node.path else self.root_dir, url)
        if abs_path and os.path.isfile(abs_path):
            if _HTML_RE.search(abs_path):
                self.unmergeable.add(url)
                if kind=="navigation": self.linked_pages.add(rel_key(self.root_dir,abs_path))
                return None
            key=rel_key(self.root_dir,abs_path)
            mime=mime_for(abs_path)
            try:
                size=os.path.getsize(abs_path)
                if size > _MERGE_MAX_INLINE_BYTES and media_category(mime) in ("images","videos","audio","fonts","docs","vector"):
                    self.unmergeable.add(f"large resource skipped from merge ({size_str(size)}): {url}")
                    return None
                data=read_bytes(abs_path)
            except Exception as e:
                self.missing.add(f"{url} ({e})"); return None
            n=self._get_or_create(key,abs_path,mime,mime,data,src_node.priority+1)
            return n
        bnode=self._bundle_lookup(url)
        if bnode:
            if src_node.priority+1 < bnode.priority: bnode.priority=src_node.priority+1
            return bnode
        self.missing.add(url)
        return None

    def hoist_data_uris(self, text: str, src_node: GraphNode):
        # Only base64 data URIs above the extract threshold are hoisted.
        ignore = tuple(x.lower() for x in (self.opts.ignore_uris or []))
        for m in list(_DATA_URI_RE.finditer(text)):
            full=m.group(0); head=m.group(1); b64=m.group(2).replace("\n","").replace("\r","")
            if len(b64) < 8: continue
            try: raw=base64.b64decode(b64,validate=False)
            except Exception: continue
            if len(raw)<MIN_EXTRACT: continue
            mime=head.split(";",1)[0].lower()
            if any(mime.startswith(x) for x in ignore):
                continue
            key=f"__data__/{hashlib.blake2b(raw,digest_size=12).hexdigest()}{_EXT_BY_MIME.get(mime,'.bin')}"
            n=self.by_path.get(key)
            if n is None:
                n=self._get_or_create(key,None,mime,mime,raw,src_node.priority+1)
            text=text.replace(full,token_for(n.id),1)
        return text

    def scan_node(self, node: GraphNode):
        if node.mime in ("text/css",):
            text=node.data.decode("utf-8",errors="replace")
            text=self.hoist_data_uris(text,node)
            def import_repl(m):
                url=m.group(2)
                n=self._static_ref(node,url,kind="resource")
                return m.group(0) if n is None else m.group(0).replace(url,token_for(n.id))
            text=_CSS_IMPORT_URL_RE.sub(import_repl,text)
            text=_CSS_IMPORT_STR_RE.sub(import_repl,text)
            def url_repl(m):
                url=m.group(2).strip()
                n=self._static_ref(node,url,kind="resource")
                return m.group(0) if n is None else f"url({m.group(1)}{token_for(n.id,split_ref(url)[1])}{m.group(1)})"
            node.data=url_repl_text=_CSS_URL_RE.sub(url_repl,text).encode("utf-8")
            node.data=url_repl_text
            return
        if node.mime in ("text/javascript","application/javascript"):
            text=node.data.decode("utf-8",errors="replace")
            # Static ES module and common fetch/new URL forms. We do not promise arbitrary dynamic JS analysis.
            for rx, kind in ((_MODULE_RE,"resource"),(_IMPORT_CALL_RE,"resource"),(_NEW_URL_RE,"resource"),(_FETCH_RE,"resource")):
                for m in list(rx.finditer(text)):
                    url=m.group(2)
                    n=self._static_ref(node,url,kind=kind)
                    if n: text=text.replace(m.group(0),m.group(0).replace(url,token_for(n.id,split_ref(url)[1])),1)
            node.data=text.encode("utf-8")
            return
        if node.mime == "text/html" or node.mime == "application/xhtml+xml":
            # HTML entry/reference rewriting is handled in _transform_html so
            # attributes are not processed twice.
            text=node.data.decode("utf-8",errors="replace")
            text=self.hoist_data_uris(text,node)
            node.data=text.encode("utf-8")
            return
        if is_textual(node.mime) or node.mime=="image/svg+xml":
            text=node.data.decode("utf-8",errors="replace")
            text=self.hoist_data_uris(text,node)
            def attr_repl(m):
                attr=m.group(1); q=m.group(2); url=m.group(3)
                kind="navigation" if attr.lower()=="href" and node.mime.startswith("text/html") else "resource"
                n=self._static_ref(node,url,kind=kind)
                return m.group(0) if n is None else f"{attr}={q}{token_for(n.id,split_ref(url)[1])}{q}"
            text=re.sub(r"\b(src|href|poster|xlink:href)\s*=\s*(['\"])([^'\"]+)\2",attr_repl,text,flags=re.I)
            text=_SRCSET_RE.sub(lambda m:self._rewrite_srcset(node,m),text)
            node.data=text.encode("utf-8")

    def _csv_key(self, url: str, path: str) -> str:
        """arcager.csv key for a linked table: the sanitized file name, made unique."""
        base=re.sub(r'[^\w\-]','_',Path(url).stem) or 'csv'
        key=base; n=2
        while key in self.csv_keys:
            key=f"{base}_{n}"; n+=1
        if key!=base:
            other=os.path.relpath(self.csv_keys[base],self.root_dir).replace(os.sep,'/')
            warn(f"two tables are named '{base}' ({other} and {rel_key(self.root_dir,path)}); "
                 f"the second is exposed as arcager.csv.{key}. Rename one of the files to choose the key yourself.")
        return key

    @staticmethod
    def _read_csv_text(path: str) -> str:
        data=read_bytes(path)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            warn(f"{path} is not valid UTF-8; invalid bytes were replaced. Re-save it as UTF-8 for an exact round trip.")
            return data.decode('utf-8',errors='replace')

    def source_bytes(self) -> int:
        """Bytes of the original files that went into this package (before any rewriting)."""
        seen=set(); total=0
        for n in self.nodes:
            if n.path and n.path not in seen and os.path.isfile(n.path):
                seen.add(n.path); total+=os.path.getsize(n.path)
        for p in self.csv_inlined:
            if p not in seen and os.path.isfile(p):
                seen.add(p); total+=os.path.getsize(p)
        for n in {id(x):x for x in self.bundle_nodes.values()}.values():
            if not n.path: total+=len(n.data)    # -b bundle entries have no file of their own
        return total

    def _rewrite_srcset(self,node,m):
        q=m.group(1); val=m.group(2); parts=[]
        for item in val.split(','):
            toks=item.strip().split()
            if not toks: continue
            n=self._static_ref(node,toks[0],kind="resource")
            if n: toks[0]=token_for(n.id,split_ref(toks[0])[1])
            parts.append(" ".join(toks))
        return f"srcset={q}{', '.join(parts)}{q}"

    def _include_unreferenced_resources(self):
        """Include local non-HTML files not discovered by static dependency analysis.

        Priority is deliberately low: this is a fallback retention policy, not a
        claim that the resource loads during initial rendering. Keeping these files
        prevents static analysis from deleting assets that JavaScript may address
        dynamically later.
        """
        existing_paths = {os.path.abspath(n.path) for n in self.nodes if n.path}
        # Tables linked with <link rel="csv"> already live in the page as inline blocks
        # (window.arcager.csv); storing the file a second time would only add bytes.
        existing_paths |= {os.path.abspath(p) for p in self.csv_inlined}
        for cur, dirs, files in os.walk(self.root_dir, followlinks=False):
            dirs.sort(); files.sort()
            # Do not descend into VCS metadata or an already-packed output cache.
            dirs[:] = [d for d in dirs if d not in {'.git', '.hg', '.svn', '__pycache__'}]
            for name in files:
                path = os.path.abspath(os.path.join(cur, name))
                if path in existing_paths:
                    continue
                try:
                    if read_signature(path) is not None:
                        continue        # an Arcager package sitting in the folder (e.g. an earlier output)
                except OSError:
                    pass
                key = rel_key(self.root_dir, path)
                if _HTML_RE.search(name):
                    self.unlinked_pages.append(key)
                    continue
                try:
                    size = os.path.getsize(path)
                    self.unreferenced.append((key, size))
                    if self.opts.skip_unused:
                        continue
                    data = read_bytes(path)
                except OSError as exc:
                    self.missing.add(f"{key} ({exc})")
                    continue
                mime = mime_for(path)
                self._get_or_create(key, path, mime, mime, data, 65535)
                existing_paths.add(path)

    def process(self):
        self._get_or_create("index.html",self.index_path,mime_for(self.index_path),mime_for(self.index_path),read_bytes(self.index_path),0,True)
        root=self.by_path["index.html"]
        # Root HTML is transformed in a dedicated pass so references can become
        # graph edges without being mistaken for filesystem paths on the first scan.
        try:
            self._queue.remove(root)
        except ValueError:
            pass
        root_text=root.data.decode("utf-8",errors="replace")
        if self.opts.base_href:
            root_text=apply_base_href(root_text,self.opts.base_href)
        root_text=self.hoist_data_uris(root_text,root)
        root_text=self._transform_html(root,root_text)
        root.data=root_text.encode("utf-8")
        # Process all resources discovered from the entry point, including
        # transitive CSS/JS/SVG dependencies created during scanning.
        while self._queue:
            n=self._queue.popleft()
            self.scan_node(n)
        # Static discovery must not become an omission policy. Add remaining
        # local resources at fallback priority so dynamically addressed files
        # remain available to the packaged application. Newly added resources
        # can themselves reveal additional dependencies, so drain the queue again.
        self._include_unreferenced_resources()
        while self._queue:
            n=self._queue.popleft()
            self.scan_node(n)
        if self.missing:
            raise RuntimeError("missing local resources — these files are referenced by your HTML/CSS/JS but do not exist:\n  " + "\n  ".join(sorted(self.missing)) +
                               "\n  Check the spelling (paths are case-sensitive on Linux/macOS) and that they are inside the folder you are packing, "
                               "relative to the file that references them.")
        return root

    def _transform_html(self,node: GraphNode, html: str):
        html=self.hoist_data_uris(html,node)
        # link tags
        def link_repl(m):
            tag=m.group(0)
            hm=re.search(r"\bhref\s*=\s*(['\"])([^'\"]+)\1",tag,re.I)
            if not hm: return tag
            url=hm.group(2)
            relm=re.search(r"\brel\s*=\s*(['\"])([^'\"]+)\1",tag,re.I)
            rels=(relm.group(2).lower().split() if relm else [])
            if 'csv' in rels or url.lower().endswith(('.csv','.tsv')):
                if is_external(url): self.external.add(url); return tag
                p=resolve_local(os.path.dirname(node.path),url)
                if not p or not os.path.isfile(p): self.missing.add(url); return tag
                if p in self.csv_inlined: return ''   # same table linked twice: keep one block
                key=self._csv_key(url,p)
                raw=self._read_csv_text(p)
                rel=rel_key(self.root_dir,p)
                if rel.startswith('..'): rel=os.path.basename(p)
                self.csv_inlined[p]=key; self.csv_keys[key]=p
                return f'<script type="text/csv" data-key="{escape_attr(key)}" data-origin="{escape_attr(rel)}">\n{escape_for_script(raw)}\n</script>'
            if is_external(url):
                if url.lower().startswith(('http://','https://','//')): self.external.add(url)
                return tag
            n=self._static_ref(node,url,kind="resource")
            if n is None: return tag
            return tag[:hm.start(2)] + token_for(n.id,split_ref(url)[1]) + tag[hm.end(2):]
        html=_LINK_RE.sub(link_repl,html)
        # scripts
        def script_repl(m):
            pre=m.group(1); q=m.group(2); url=m.group(3); post=m.group(4)
            if is_external(url): self.external.add(url); return m.group(0)
            n=self._static_ref(node,url,kind="resource")
            if n is None: return m.group(0)
            return f'<script{pre}src={q}{token_for(n.id,split_ref(url)[1])}{q}{post}></script>'
        html=_SCRIPT_SRC_RE.sub(script_repl,html)
        # media / anchors
        def tag_repl(m):
            tag=m.group(0); name=re.match(r'<\s*([a-z]+)',tag,re.I).group(1).lower()
            def ar(am):
                attr=am.group(1); q=am.group(2); url=am.group(3)
                if is_external(url):
                    if name!='a': self.external.add(url)
                    return am.group(0)
                kind='navigation' if name=='a' and attr.lower()=='href' else 'resource'
                n=self._static_ref(node,url,kind=kind)
                return am.group(0) if n is None else f'{attr}={q}{token_for(n.id,split_ref(url)[1])}{q}'
            return re.sub(r'\b(src|href|poster|xlink:href)\s*=\s*([\'\"])([^\'\"]+)\2',ar,tag,flags=re.I)
        html=_MEDIA_TAG_RE.sub(tag_repl,html)
        html=_SRCSET_RE.sub(lambda m:self._rewrite_srcset(node,m),html)
        html=_STYLE_BLOCK_RE.sub(lambda m:'<style>'+self._rewrite_css_inline(node,m.group(1))+'</style>',html)
        html=_STYLE_ATTR_RE.sub(lambda m:m.group(1)+self._rewrite_css_inline(node,m.group(3))+m.group(2),html)
        # dynamic dependency notice without guessing.
        if re.search(r"(?:fetch\s*\(|\.src\s*=|new\s+URL\s*\(|import\s*\()",html,re.I):
            # HTML itself can contain script text; report at verbose level rather than failing.
            self.dynamic.add("possible runtime-generated URLs")
        return html

    def _rewrite_css_inline(self,node,text):
        text=self.hoist_data_uris(text,node)
        def repl(m):
            url=m.group(2).strip(); n=self._static_ref(node,url,kind="resource")
            return m.group(0) if n is None else f'url({m.group(1)}{token_for(n.id,split_ref(url)[1])}{m.group(1)})'
        return _CSS_URL_RE.sub(repl,text)

# Dark loader theme (shown only while the package unpacks, then the page replaces itself).
LOADER_CSS = (
    "[hidden]{display:none!important}"
    "html{color-scheme:dark;background:#0f1115}"
    "body{margin:0;background:#0f1115;color:#e6e8ec;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
    "#__pnl{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;background:#0f1115;z-index:2147483647}"
    "#__box{box-sizing:border-box;width:100%;max-width:420px;padding:26px 28px;border:1px solid #262b36;border-radius:14px;background:#171a21;box-shadow:0 18px 48px rgba(0,0,0,.55)}"
    "#__brand{font-size:11px;letter-spacing:.22em;color:#6f7a90;margin-bottom:10px}"
    "#__lbl{font-size:16px;line-height:1.4;color:#e6e8ec}"
    "#__bar{margin-top:16px;height:6px;background:#262b36;border-radius:6px;overflow:hidden}"
    "#__fill{width:2%;height:100%;border-radius:6px;background:linear-gradient(90deg,#6ea8ff,#8f7dff);transition:width .15s linear}"
    "#__pwf{display:flex;gap:8px;margin-top:16px}"
    "#__pw{flex:1;min-width:0;padding:10px 12px;font:inherit;color:#e6e8ec;background:#0f1115;border:1px solid #343b4a;border-radius:8px;outline:none}"
    "#__pw:focus{border-color:#6ea8ff;box-shadow:0 0 0 3px rgba(110,168,255,.22)}"
    "#__pwf button{padding:10px 16px;font:inherit;font-weight:600;color:#0b1020;background:#6ea8ff;border:0;border-radius:8px;cursor:pointer}"
    "#__pwf button:hover{background:#8dbbff}"
    "#__err{margin-top:12px;font-size:14px;line-height:1.45;color:#ff9a9a;white-space:pre-wrap;word-break:break-word}"
    "#__err:empty{display:none}"
    "@media (prefers-reduced-motion:reduce){#__fill{transition:none}}"
)

# ---------- container shell ----------
JS_RUNTIME = r'''(function(){"use strict";
var E=document.getElementById("__pk"),P=document.getElementById("__pnl"),L=document.getElementById("__lbl"),F=document.getElementById("__fill"),ER=document.getElementById("__err");
var readyResolve,readyReject;
window.arcager=window.arcager||{};
window.arcager.error=null;
window.arcager.ready=new Promise(function(a,b){readyResolve=a;readyReject=b;});
var Z="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#",ZM={};for(var zi=0;zi<85;zi++)ZM[Z.charCodeAt(zi)]=zi;
function z85d(s,n){s=s.replace(/\s+/g,"");var expected=Math.ceil(n/4)*5;if(s.length!==expected)throw Error("invalid Arcager encoding length");var o=new Uint8Array(n),k=0;for(var i=0;i<s.length&&k<n;i+=5){var v=0;for(var j=0;j<5;j++){var d=ZM[s.charCodeAt(i+j)];if(d===undefined)throw Error("invalid Arcager encoding at "+i);v=v*85+d;}if(k<n)o[k++]=(v>>>24)&255;if(k<n)o[k++]=(v>>>16)&255;if(k<n)o[k++]=(v>>>8)&255;if(k<n)o[k++]=v&255;}return o;}
function b64(a){var s="",C=0x8000;for(var i=0;i<a.length;i+=C)s+=String.fromCharCode.apply(null,a.subarray(i,i+C));return btoa(s);}
function b64d(s){var x=atob(s),o=new Uint8Array(x.length);for(var i=0;i<x.length;i++)o[i]=x.charCodeAt(i);return o;}
function varint(a,p){var v=0,m=1,b;do{if(p>=a.length)throw Error("truncated Arcager metadata");b=a[p++];v+=(b&127)*m;if(v>Number.MAX_SAFE_INTEGER)throw Error("Arcager metadata integer is too large");m*=128;}while(b&128);return [v,p];}
function u32(a,p){return (((a[p])|(a[p+1]<<8)|(a[p+2]<<16)|(a[p+3]<<24))>>>0);}
function u64(a,p){var v=u32(a,p)+u32(a,p+4)*4294967296;if(v>Number.MAX_SAFE_INTEGER)throw Error('v4 payload is too large for this browser runtime');return v;}
function fail(e){var x=e&&e.message?e.message:String(e);window.arcager.error=e instanceof Error?e:new Error(x);var pf=document.getElementById("__pwf");if(pf)pf.hidden=true;if(ER)ER.textContent=x;if(L)L.textContent="Failed to unpack";if(P)P.hidden=false;if(window.console)console.error(e);readyReject(window.arcager.error);}
function askPassword(note){return new Promise(function(res,rej){var f=document.getElementById("__pwf"),i=document.getElementById("__pw"),bar=document.getElementById("__bar");if(!f||!i){var v=prompt("Enter Arcager password:");if(v===null)rej(Error("password entry cancelled"));else res(v);return;}if(L)L.textContent="This page is password protected";if(ER)ER.textContent=note||"";if(bar)bar.hidden=true;f.hidden=false;i.value="";i.focus();f.onsubmit=function(ev){ev.preventDefault();var v=i.value;f.hidden=true;if(ER)ER.textContent="";if(L)L.textContent="Checking password\u2026";res(v);};});}
function progress(n){if(F)F.style.width=Math.max(0,Math.min(100,n))+'%';if(L)L.textContent='Unpacking… '+Math.floor(n)+'%';}
var M=["application/octet-stream","text/html","application/xhtml+xml","text/css","text/javascript","application/javascript","application/json","application/xml","text/xml","text/plain","text/csv","text/tab-separated-values","image/png","image/jpeg","image/webp","image/gif","image/svg+xml","image/bmp","image/x-icon","image/avif","font/woff","font/woff2","font/ttf","font/otf","application/vnd.ms-fontobject","application/pdf","application/postscript","audio/mpeg","audio/ogg","audio/wav","audio/mp4","audio/aac","audio/flac","audio/opus","video/mp4","video/webm","video/ogg","video/quicktime","text/markdown","application/zip","application/gzip","application/x-7z-compressed"];
function crc32(a){var c=~0;for(var i=0;i<a.length;i++){c^=a[i];for(var j=0;j<8;j++)c=(c>>>1)^(0xEDB88320&-(c&1));}return (~c)>>>0;}
async function ungzip(a){var ds=new DecompressionStream('gzip'),w=ds.writable.getWriter(),r=ds.readable.getReader();w.write(a);w.close();var ch=[],n=0;while(true){var x=await r.read();if(x.done)break;ch.push(x.value);n+=x.value.length;}var o=new Uint8Array(n),p=0;for(var i=0;i<ch.length;i++){o.set(ch[i],p);p+=ch[i].length;}return o;}
async function unbro(a){var ds=new DecompressionStream('brotli'),w=ds.writable.getWriter(),r=ds.readable.getReader();w.write(a);w.close();var ch=[],n=0;while(true){var x=await r.read();if(x.done)break;ch.push(x.value);n+=x.value.length;}var o=new Uint8Array(n),p=0;for(var i=0;i<ch.length;i++){o.set(ch[i],p);p+=ch[i].length;}return o;}
async function keyFromPassword(pw,s,it){var e=new TextEncoder(),b=await crypto.subtle.importKey('raw',e.encode(pw),{name:'PBKDF2'},false,['deriveKey']);return crypto.subtle.deriveKey({name:'PBKDF2',salt:b64d(s),iterations:it,hash:'SHA-256'},b,{name:'AES-GCM',length:256},false,['decrypt']);}
async function decrypt(k,iv,a){return new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:b64d(iv)},k,a));}
function parseContainer(c){
  if(c.length<64)throw Error('v4 container truncated');
  var td=new TextDecoder();
  var magic=td.decode(c.subarray(0,8));
  if(magic!=='ARCAGER4')throw Error('invalid Arcager v4 container');
  if(c[8]!==4)throw Error('unsupported Arcager container version '+c[8]);
  var hcrc=u32(c,44),hc=crc32(c.subarray(0,44));
  if(hcrc!==hc)throw Error('corrupt v4 header');
  var sc=u32(c,12),pc=u32(c,16),lc=u32(c,20),st=u32(c,24),ml=u32(c,28),payloadLen=u64(c,32);
  var metaStart=64,metaEnd=metaStart+ml,payloadEnd=metaEnd+payloadLen;
  if(metaEnd<64||metaEnd>c.length||payloadEnd>c.length)throw Error('corrupt v4 metadata boundary');
  if(payloadEnd!==c.length)throw Error('corrupt v4 container size');
  var meta=c.subarray(metaStart,metaEnd);
  if(crc32(meta)!==u32(c,40))throw Error('corrupt v4 metadata');
  if(meta.length<16)throw Error('truncated v4 metadata header');
  var p=0;
  var sc2=u32(meta,0),pc2=u32(meta,4),lc2=u32(meta,8),st2=u32(meta,12);
  if(sc2!==sc||pc2!==pc||lc2!==lc||st2!==st)throw Error('v4 count mismatch');
  p=16;
  var streams=[],prev=0;
  for(var i=0;i<sc;i++){
    if(p+3>meta.length)throw Error('truncated stream table');
    var mid=meta[p]|(meta[p+1]<<8),algo=meta[p+2];p+=3;
    if(mid>=M.length)throw Error('unknown v4 MIME id '+mid);
    var q=varint(meta,p),mc=q[0];p=q[1];
    q=varint(meta,p);var off=prev+q[0];p=q[1];prev=off;
    q=varint(meta,p);var rs=q[0];p=q[1];
    if(p+4>meta.length)throw Error('truncated stream CRC');
    var cr=u32(meta,p);p+=4;
    if(off>payloadLen)throw Error('invalid v4 stream offset');
    streams.push({mime:M[mid],algo:algo,count:mc,off:off,raw:rs,crc:cr});
  }
  var physical=[],prevByStream={};
  for(var j=0;j<pc;j++){
    q=varint(meta,p);var sid=q[0];p=q[1];
    if(sid>=sc)throw Error('invalid v4 physical stream reference');
    q=varint(meta,p);var oo=(prevByStream[sid]||0)+q[0];p=q[1];prevByStream[sid]=oo;
    if(p+6>meta.length)throw Error('truncated physical record');
    var pmid=meta[p]|(meta[p+1]<<8),fl=meta[p+2],sm=meta[p+4]|(meta[p+5]<<8);p+=6;
    if(pmid>=M.length||sm>=M.length)throw Error('unknown v4 physical MIME id');
    if(oo>streams[sid].raw)throw Error('invalid v4 physical offset');
    physical.push({stream:sid,off:oo,mime:M[pmid],flags:fl,source:M[sm]});
  }
  var counts=new Array(sc);for(var ci=0;ci<sc;ci++)counts[ci]=0;
  for(var pi=0;pi<physical.length;pi++)counts[physical[pi].stream]++;
  for(var si=0;si<sc;si++)if(counts[si]!==streams[si].count)throw Error('v4 stream member-count mismatch');
  var strings=[];
  for(var si=0;si<st;si++){q=varint(meta,p);var n=q[0];p=q[1];if(p+n>meta.length)throw Error('truncated string table');strings.push(new TextDecoder().decode(meta.subarray(p,p+n)));p+=n;}
  var logical=[];
  for(var li=0;li<lc;li++){var a=varint(meta,p),idx=a[0];p=a[1];a=varint(meta,p);var pi=a[0];p=a[1];a=varint(meta,p);var pr=a[0];p=a[1];if(p+2>meta.length)throw Error('truncated logical record');var fl=meta[p],sm=meta[p+1];p+=2;if(idx>=strings.length||pi>=physical.length||sm>=M.length)throw Error('invalid v4 logical reference');logical.push({path:strings[idx],physical:pi,priority:pr,flags:fl,source:M[sm]});}
  if(p!==meta.length)throw Error('unexpected trailing v4 metadata');
  var nextOff=new Array(physical.length);
  for(var sid=0;sid<streams.length;sid++){
    var idxs=[];for(var qi=0;qi<physical.length;qi++)if(physical[qi].stream===sid)idxs.push(qi);
    idxs.sort(function(a,b){return physical[a].off-b.off;});
    for(var qj=0;qj<idxs.length;qj++){var cur=physical[idxs[qj]].off,nxt=(qj+1<idxs.length?physical[idxs[qj+1]].off:streams[sid].raw);if(nxt<cur||nxt>streams[sid].raw)throw Error('invalid v4 physical boundaries');nextOff[idxs[qj]]=nxt;}
  }
  return {streams:streams,physical:physical,logical:logical,nextOff:nextOff,payload:c.subarray(metaEnd,payloadEnd)};
}
async function streamBytes(C,sid,cache){if(cache[sid])return cache[sid];var s=C.streams[sid],end=(sid+1<C.streams.length)?C.streams[sid+1].off:C.payload.length;if(s.off<0||end<s.off||end>C.payload.length)throw Error('invalid v4 stream payload boundary');var part=C.payload.subarray(s.off,end),raw;if(s.algo===0)raw=part;else if(s.algo===1)raw=await ungzip(part);else if(s.algo===2)raw=await unbro(part);else throw Error('unsupported stream compression');if(raw.length!==s.raw)throw Error('stream size mismatch');if(crc32(raw)!==s.crc)throw Error('stream integrity check failed');cache[sid]=raw;return raw;}
function extFor(m){var e={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif','image/svg+xml':'.svg','image/bmp':'.bmp','image/x-icon':'.ico','image/avif':'.avif','font/woff':'.woff','font/woff2':'.woff2','font/ttf':'.ttf','font/otf':'.otf','application/vnd.ms-fontobject':'.eot','audio/mpeg':'.mp3','audio/ogg':'.ogg','audio/wav':'.wav','audio/mp4':'.m4a','audio/aac':'.aac','audio/flac':'.flac','audio/opus':'.opus','video/mp4':'.mp4','video/webm':'.webm','video/ogg':'.ogv','video/quicktime':'.mov','text/html':'.html','application/xhtml+xml':'.xhtml','application/pdf':'.pdf','application/postscript':'.eps','text/plain':'.txt','text/markdown':'.md','text/csv':'.csv','text/tab-separated-values':'.tsv','application/json':'.json','application/xml':'.xml','text/xml':'.xml','text/css':'.css','text/javascript':'.js','application/javascript':'.js','application/octet-stream':'.bin'};return e[m]||'.bin';}
function buildURLs(C,streams){var byLogical={},images={},physRawUrl={},physUrl={},usedURLs=[];
  for(var i=0;i<C.physical.length;i++){var p=C.physical[i],st=streams[p.stream],nextOff=C.nextOff[i];var slice=st.subarray(p.off,nextOff);var u=URL.createObjectURL(new Blob([slice],{type:p.mime}));physRawUrl[i]=u;physUrl[i]=u;usedURLs.push(u);}
  for(var i=0;i<C.logical.length;i++){var n=C.logical[i];byLogical[i]=physUrl[n.physical];}
  function replaceTokens(text){return text.replace(/arcager-res:(\d+)([?#][^\s"'< >\)\]]*)?/g,function(_m,id,suf){var u=byLogical[Number(id)];if(!u)return _m;return u+(suf||'');});}
  function addImageAliases(path,u){images[path]=u;images[path.replace(/^\.\//,'')]=u;images[path.replace(/^\/+/, '')]=u;images[path.replace(/\.[^.\/]+$/,'')]=u;var bare=path.split('/').pop().replace(/\.[^.]+$/,'');if(images[bare]===undefined)images[bare]=u;}
  function plainCandidates(url){var b=url.split(/[?#]/,1)[0].replace(/\\/g,'/');var a=[b,b.replace(/^\.\//,''),b.replace(/^\/+/, '')];try{a.push(decodeURIComponent(b));}catch(e){}if(/\.[^.\/]+$/.test(b))a.push(b.replace(/\.[^.\/]+$/,''));return a;}
  function resolvePlain(url){var a=plainCandidates(url);for(var i=0;i<a.length;i++){if(images[a[i]])return images[a[i]];}return null;}
  function replacePlain(text){
    text=text.replace(/\b(src|href|poster|xlink:href)\s*=\s*(["'])([^"']+)\2/gi,function(m,attr,q,url){if(/^(?:https?:|data:|mailto:|tel:|javascript:|#|blob:)/i.test(url))return m;var u=resolvePlain(url);return u?attr+'='+q+u+(url.match(/[?#].*$/)||[''])[0]+q:m;});
    text=text.replace(/\bsrcset\s*=\s*(["'])([^"']+)\1/gi,function(m,q,val){var parts=val.split(',').map(function(item){var t=item.trim().split(/\s+/);if(!t[0])return item;var u=resolvePlain(t[0]);if(u)t[0]=u;return t.join(' ');});return 'srcset='+q+parts.join(', ')+q;});
    text=text.replace(/url\(\s*(["']?)([^"')]+)\1\s*\)/gi,function(m,q,url){if(/^(?:https?:|data:|blob:|#)/i.test(url))return m;var u=resolvePlain(url);return u?'url('+q+u+(url.match(/[?#].*$/)||[''])[0]+q+')':m;});
    return text;
  }
  var rewrittenPhys={};
  function resourceText(pid){var ph=C.physical[pid],sid=ph.stream,raw=streams[sid],next=C.nextOff[pid];return new TextDecoder().decode(raw.subarray(ph.off,next));}
  for(var pass=0;pass<2;pass++){
    for(var li=0;li<C.logical.length;li++){
      var ln=C.logical[li],pid=ln.physical,ph=C.physical[pid],m=ph.mime.toLowerCase();
      if(rewrittenPhys[pid]|| (m!=='text/css'&&m!=='text/javascript'&&m!=='application/javascript'&&m!=='image/svg+xml'))continue;
      rewrittenPhys[pid]=true;var txt=replaceTokens(resourceText(pid));var nu=URL.createObjectURL(new Blob([txt],{type:ph.mime}));
      if(physUrl[pid]&&physUrl[pid]!==physRawUrl[pid])try{URL.revokeObjectURL(physUrl[pid]);}catch(e){}physUrl[pid]=nu;for(var lj=0;lj<C.logical.length;lj++)if(C.logical[lj].physical===pid)byLogical[lj]=nu;
    }
  }
  for(var i=0;i<C.logical.length;i++){var n=C.logical[i],u=physUrl[n.physical];if(!(n.flags&8))addImageAliases(n.path,u);}
  function finalReplace(text){return replacePlain(replaceTokens(text));}
  return {byLogical:byLogical,physUrl:physUrl,images:images,replaceTokens:finalReplace,urls:usedURLs};}

function parseCSV(body){
  /* RFC 4180-style: quoted fields may contain delimiters, doubled quotes and line breaks. One delimiter per table (tab if the first line has a tab, else comma). Blank lines are skipped. */
  body=String(body).replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n');
  var first=body.split('\n',1)[0]||'',delim=first.indexOf('\t')>=0?'\t':',';
  var rows=[],row=[],cur='',inq=false,seen=false;
  for(var i=0;i<body.length;i++){
    var ch=body.charAt(i);
    if(inq){if(ch==='"'){if(body.charAt(i+1)==='"'){cur+='"';i++;}else inq=false;}else cur+=ch;}
    else if(ch==='"'){inq=true;seen=true;}
    else if(ch===delim){row.push(cur);cur='';seen=true;}
    else if(ch==='\n'){if(seen||cur!==''){row.push(cur);rows.push(row);}row=[];cur='';seen=false;}
    else{cur+=ch;seen=true;}
  }
  if(seen||cur!==''){row.push(cur);rows.push(row);}
  return rows;
}
async function main(){
  if(typeof DecompressionStream==='undefined')throw Error('This browser does not provide DecompressionStream.');
  if(!E)throw Error('Arcager v4 container block is missing.');
  var enc=E.getAttribute('data-e')==='1',rawSize=Number(E.getAttribute('data-n')||0),blob=z85d(E.textContent||'',rawSize),key=null;
  if(enc){if(!crypto||!crypto.subtle)throw Error('Encrypted Arcager packages require Web Crypto support.');var s=E.getAttribute('data-s'),iv=E.getAttribute('data-iv'),it=Number(E.getAttribute('data-i')),ck=E.getAttribute('data-ck');var note='';while(true){var pw=await askPassword(note);try{var k=await keyFromPassword(pw,s,it);var can=await decrypt(k,E.getAttribute('data-civ'),b64d(ck));if(new TextDecoder().decode(can)!=='arcager-ok')throw Error('bad password');key=k;break;}catch(e){if(window.console)console.warn('Arcager: incorrect password');note='Incorrect password. Please try again.';}}var bar0=document.getElementById('__bar');if(bar0)bar0.hidden=false;if(L)L.textContent='Unpacking\u2026';blob=await decrypt(key,iv,blob);}
  var C=parseContainer(blob),cache=[],need={},nodes=C.logical;
  for(var i=0;i<C.logical.length;i++)need[C.logical[i].physical]=true;
  var streamIds={};for(var i=0;i<C.physical.length;i++)streamIds[C.physical[i].stream]=true;
  var streams={};var ids=Object.keys(streamIds);for(var j=0;j<ids.length;j++){progress(5+85*(j/ids.length));streams[ids[j]]=await streamBytes(C,Number(ids[j]),cache);}progress(95);
  var U=buildURLs(C,streams);window.arcager.images=U.images;window.arcager.csv={};window.arcager.getImage=function(k){var s=String(k).replace(/\\/g,'/').replace(/^(?:\.\/|\/)+/,''),c=[s,s.replace(/\.[^.\/]+$/,'')],u=null;for(var i=0;i<c.length&&!u;i++)u=U.images[c[i]]||U.images[c[i].toLowerCase()]||null;if(!u)return null;var img=new Image();img.src=u;return img;};
  var rootIdx=-1;for(var i=0;i<nodes.length;i++){if(nodes[i].path==='index.html'){rootIdx=i;break;}}if(rootIdx<0)throw Error('v4 root HTML resource missing');
  var rp=nodes[rootIdx],pp=C.physical[rp.physical],rs=streams[pp.stream],next=C.nextOff[rp.physical];var html=new TextDecoder().decode(rs.subarray(pp.off,next));
  html=U.replaceTokens(html);
  for(var si=0;si<ids.length;si++)streams[ids[si]]=null; // Blob URLs own resource bytes now; release decompressed stream buffers.
  var csvBlocks=[];var re=/<script\b([^>]*?)>([\s\S]*?)<\/script\s*>/gi,m;while((m=re.exec(html))){if(/\btype\s*=\s*(['"])text\/csv\1/i.test(m[1]||'')){var km=(m[1]||'').match(/\bdata-key\s*=\s*(['"])([^'"]+)\1/i),keycsv=km?km[2]:'csv'+csvBlocks.length;csvBlocks.push({key:keycsv,body:m[2].trim()});}}
  for(var c=0;c<csvBlocks.length;c++){var rows=parseCSV(csvBlocks[c].body);var hdr=rows.length?rows[0]:[],data=rows.slice(1).map(function(r){var o={};for(var z=0;z<hdr.length;z++)o[hdr[z]]=r[z]===undefined?'':r[z];return o;});window.arcager.csv[csvBlocks[c].key]={rows:rows,data:data};}
  var cl='<scr'+'ipt>/* Arcager v4 cleanup */(function(){var p=document.getElementById("__pnl");if(p&&p.parentNode)p.parentNode.removeChild(p);var e=document.getElementById("__pk");if(e&&e.parentNode)e.parentNode.removeChild(e);})();<'+'/scr'+'ipt>';
  document.title=(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)||[])[1]||document.title;
  html=html.replace(/<\/body>/i,cl+'</body>');document.open();document.write(html);document.close();window.arcager.images=U.images;window.arcager.csv=window.arcager.csv;readyResolve();
}
main().catch(fail);
})();'''


def shell_html(container: bytes, *, password_info=None, hidden_info=None, title="Arcager", minify=False, vlog_fn=None):
    enc=password_info is not None
    attrs={"data-v":"4","data-e":"1" if enc else "0","data-n":str(len(container))}
    if enc: attrs.update(password_info)
    attrs_s=" ".join(f'{k}="{escape_attr(str(v))}"' for k,v in attrs.items())
    container_z85=z85_script_safe(container)
    hidden_block=""
    if hidden_info:
        hattrs=" ".join(f'{k}="{escape_attr(str(v))}"' for k,v in hidden_info.items() if k!="data")
        hidden_block=f'<script type="text/plain" id="__hid" {hattrs}>{z85_script_safe(hidden_info["data"])}</script>'
    runtime = maybe_minify_js(JS_RUNTIME, minify, vlog_fn)
    return f'''{SIGNATURE}\n<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title data-arcager-shell>{escape_text(title)}</title><style id="__arcager_style">{LOADER_CSS}</style></head><body><div id="__pnl"><div id="__box"><div id="__brand">ARCAGER</div><div id="__lbl">Unpacking…</div><div id="__bar"><div id="__fill"></div></div><form id="__pwf" hidden autocomplete="off"><input id="__pw" type="password" autocomplete="off" spellcheck="false" placeholder="Password" aria-label="Password"><button type="submit">Unlock</button></form><div id="__err" role="alert"></div></div></div><noscript><p style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;margin:0;background:#0f1115;color:#e6e8ec;font:16px system-ui,sans-serif;z-index:2147483647">This page is packed with Arcager and needs JavaScript to open.</p></noscript><script type="application/octet-stream" id="__pk" {attrs_s}>{container_z85}</script>{hidden_block}<script>{runtime}</script></body></html>'''

# ---------- pack pipeline ----------
def apply_base_href(html: str, base_href: str) -> str:
    tag=f'<base href="{escape_attr(base_href)}">'
    if re.search(r'<head[^>]*>', html, re.I):
        return re.sub(r'(<head[^>]*>)', lambda m: m.group(1)+tag, html, count=1, flags=re.I)
    return tag + html


def strip_data_uris_html(html: str, builder: V4ContainerBuilder, ignore_uris=None):
    # Standalone pack: hoist only large base64 data URIs while preserving all other HTML byte-for-byte.
    cache={}
    ignore_uris = tuple(x.lower() for x in (ignore_uris or []))
    def repl(m):
        full=m.group(0); head=m.group(1); b64=m.group(2).replace('\n','').replace('\r','')
        try: raw=base64.b64decode(b64,validate=False)
        except Exception: return full
        if len(raw)<MIN_EXTRACT: return full
        mime=head.split(';',1)[0].lower()
        if any(mime.startswith(x) or head.lower().startswith(x) for x in ignore_uris):
            return full
        key=f"__data__/{hashlib.blake2b(raw,digest_size=12).hexdigest()}{_EXT_BY_MIME.get(mime,'.bin')}"
        if key in cache: node=cache[key]
        else:
            node=builder.add_node(key,raw,mime,source_mime=mime,priority=1,flags=0,source="data URI")
            cache[key]=node
        return token_for(node.id)
    return _DATA_URI_RE.sub(repl,html)


def prepare_builder_for_html(html: str, visible_entries, opts, source_name="index.html", source_bytes=None):
    algo=opts.algo
    conv=[]
    b=V4ContainerBuilder(algo,opts.strip_metadata,opts.lossy,conv)
    # Hoist standalone data URIs before registering the root so the logical root
    # record points at the actual rewritten representation.
    rewritten=strip_data_uris_html(html,b,ignore_uris=opts.ignore_uris)
    root_node=b.add_node("index.html",rewritten.encode("utf-8"),"text/html",priority=0,source_mime="text/html",source=source_name)
    # Add visible bundles as low-priority first-class physical resources.
    bundle_nodes=[]
    alias_counts=defaultdict(int)
    for e in visible_entries:
        n=b.add_node(extless(e['key']) if Path(e['key']).suffix else e['key'],e['data'],e['mime'],priority=65535,source_mime=e['mime'],source=e['src'])
        bundle_nodes.append((e,n))
        bare=Path(e['key']).stem
        alias_counts[bare]+=1
        if e.get('alias'):
            b.add_alias(e['alias'],n)
    for e,n in bundle_nodes:
        bare=Path(e['key']).stem
        if alias_counts[bare]==1:
            b.add_alias(bare,n)
    return b,root_node,conv


def build_packed(opts, html: str, in_size: int, display_name: str, visible_entries, source_label: str):
    if opts.minify and opts.minify_lossy:
        html,_,_=minify_html_lossy(html)
    if opts.base_href:
        html = apply_base_href(html, opts.base_href)
    b,root,conv=prepare_builder_for_html(html,visible_entries,opts,source_label,in_size)
    if opts.strip_metadata:
        root.flags|=0x02
    container=b.serialize()
    enc_info=None
    if opts.encrypt:
        pw=opts.password or prompt_password("Payload password: ",confirm=True)
        salt=base64.b64encode(secrets.token_bytes(16)).decode('ascii')
        iv=base64.b64encode(secrets.token_bytes(12)).decode('ascii')
        civ=base64.b64encode(secrets.token_bytes(12)).decode('ascii')
        key=derive_key(pw,salt)
        canary=aes_encrypt(key,civ,PASSWORD_CHECK)
        container=aes_encrypt(key,iv,container)
        enc_info={"data-s":salt,"data-i":str(PBKDF2_ITERATIONS),"data-iv":iv,"data-civ":civ,
                  "data-ck":base64.b64encode(canary).decode('ascii')}
    shell=shell_html(container,password_info=enc_info,title=display_name,minify=opts.minify,vlog_fn=lambda *a: vlog(opts,*a))
    return shell,b,conv

# ---------- output helpers ----------
def output_path(opts, default_name, default_dir):
    if opts.output: return opts.output
    if opts.output_dir:
        os.makedirs(opts.output_dir,exist_ok=True)
        return os.path.join(opts.output_dir,default_name)
    return os.path.join(default_dir,default_name)


def confirm_overwrite(path, force):
    if not os.path.exists(path) or force: return True
    if not sys.stdin.isatty(): raise RuntimeError(f"output file {path} already exists. Use -f to overwrite it, or choose another name with -o / -O.")
    ans=input(f"overwrite {path}? [y/N] ").strip().lower()
    return ans in ('y','yes')

# ---------- merge orchestration ----------
def report_unused(g, opts, folder):
    """Tell the user about files that were found in the folder but are not used by the page."""
    if g.unreferenced:
        items = sorted(g.unreferenced, key=lambda x: -x[1])
        total = sum(sz for _, sz in items)
        limit = len(items) if opts.verbose else 5
        lines = "\n".join(f"    {k}  ({size_str(sz)})" for k, sz in items[:limit])
        more = f"\n    … and {len(items)-limit} more (use -v to list all)" if len(items) > limit else ""
        n = len(items)
        action = ("They were left out because of --skip-unused." if opts.skip_unused else
                  "They are included anyway, in case your scripts build file names at run time. "
                  "If they are not needed, delete them, or add --skip-unused to leave them out and make the package smaller.")
        warn(f"{n} file{'s' if n != 1 else ''} ({size_str(total)}) in '{folder}' {'are' if n != 1 else 'is'} not linked from "
             f"index.html, its CSS or its JavaScript:\n{lines}{more}\n  {action}")
    pages = sorted(set(g.unlinked_pages) | g.linked_pages)
    if pages:
        names = ", ".join(f"{p} (linked from your page: that link will not work)" if p in g.linked_pages else p for p in pages[:6])
        more = f" … and {len(pages)-6} more" if len(pages) > 6 else ""
        warn(f"{len(pages)} other HTML page{'s' if len(pages) != 1 else ''} in '{folder}' {'are' if len(pages) != 1 else 'is'} not included: {names}{more}. "
             f"Merge mode packs one page (index.html) with everything it uses. Pack another page on its own with: arcager PAGE.html")


def merge_directory(dir_path,opts,out_name,visible_entries):
    root=os.path.abspath(dir_path); index=None
    for name in ('index.html','index.htm','default.html','default.htm'):
        p=os.path.join(root,name)
        if os.path.isfile(p): index=p; break
    if not index: raise RuntimeError(f"no index.html found in '{dir_path}'. Merge mode (-M) starts from the folder's index.html (or index.htm / default.html). "
                                     f"Add one, or pack a single page with: arcager PAGE.html")
    g=MergeGraph(root,index,opts)
    # Bundle names are available as a secondary lookup path.
    for e in visible_entries:
        n=g._get_or_create(f"__bundle__/{e['key']}",None,e['mime'],e['mime'],e['data'],65535)
        g.bundle_nodes[e['key']]=n
        if Path(e['key']).suffix:
            g.bundle_nodes[extless(e['key'])]=n
    root_node=g.process()
    if opts.minify and opts.minify_lossy:
        minified, _, _ = minify_html_lossy(root_node.data.decode("utf-8",errors="replace"))
        root_node.data = minified.encode("utf-8")
    report_unused(g,opts,dir_path)
    if g.external:
        ext=sorted(g.external)
        shown=ext if opts.verbose else ext[:3]
        more=f" … and {len(ext)-len(shown)} more (use -v to list all)" if len(shown)<len(ext) else ""
        warn(f"{len(ext)} external resource(s) are loaded from the network, so the page is not fully standalone: "
             f"{', '.join(shown)}{more}. To make it work offline, download them into the folder and reference them by relative path.")
    if g.unmergeable and opts.verbose:
        for x in sorted(g.unmergeable): print(f"  local HTML not flattened: {x}",file=sys.stderr)
    if g.dynamic and opts.verbose:
        print("  note: dynamic dependencies may exist; static analysis does not guarantee runtime completeness",file=sys.stderr)
    conv=[]
    b=V4ContainerBuilder(opts.algo,opts.strip_metadata,opts.lossy,conv)
    # Preserve graph node ids in an explicit map; root path is exactly index.html.
    graph_to_builder={}
    for n in g.nodes:
        key=n.key if n.key!="index.html" else "index.html"
        bn=b.add_node(key,n.data,n.mime,source_mime=n.source_mime,priority=n.priority,flags=0,source=n.path or n.key)
        graph_to_builder[n.id]=bn
    # Expose visible bundle keys/aliases through the retained runtime API.
    for e in visible_entries:
        gn=g.bundle_nodes.get(e['key']) or g.bundle_nodes.get(extless(e['key']))
        if gn is not None:
            bn=graph_to_builder.get(gn.id)
            if bn is not None:
                b.add_alias(e['key'],bn)
                bare=Path(e['key']).stem
                if bare and bare not in b._path_to_node:
                    b.add_alias(bare,bn)
    # Mark data nodes/converted resources through flags already carried.
    container=b.serialize()
    enc_info=None
    if opts.encrypt:
        pw=opts.password or prompt_password("Payload password: ",confirm=True)
        salt=base64.b64encode(secrets.token_bytes(16)).decode('ascii'); iv=base64.b64encode(secrets.token_bytes(12)).decode('ascii'); civ=base64.b64encode(secrets.token_bytes(12)).decode('ascii')
        key=derive_key(pw,salt); canary=aes_encrypt(key,civ,PASSWORD_CHECK); container=aes_encrypt(key,iv,container)
        enc_info={"data-s":salt,"data-i":str(PBKDF2_ITERATIONS),"data-iv":iv,"data-civ":civ,"data-ck":base64.b64encode(canary).decode('ascii')}
    shell=shell_html(container,password_info=enc_info,title=os.path.basename(out_name),minify=opts.minify,vlog_fn=lambda *a: vlog(opts,*a))
    return shell,b,g

# ---------- hidden envelope ----------
def hidden_envelope(entries, password: str, algo: int):
    raw=bytearray()
    meta=[]; off=0
    for e in entries:
        meta.append((e['key'],off,len(e['data']))); raw.extend(e['data']); off+=len(e['data'])
    meta_blob=json.dumps(meta,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    body=struct.pack('<8sII',b'ARCAGHID',1,len(meta_blob))+meta_blob+bytes(raw)
    candidate=compress_bytes(body,algo)
    use_algo=algo if len(candidate)<len(body) else 0
    comp=candidate if use_algo else body
    salt=base64.b64encode(secrets.token_bytes(16)).decode('ascii'); iv=base64.b64encode(secrets.token_bytes(12)).decode('ascii'); civ=base64.b64encode(secrets.token_bytes(12)).decode('ascii')
    key=derive_key(password,salt); ck=aes_encrypt(key,civ,PASSWORD_CHECK); enc=aes_encrypt(key,iv,comp)
    return {"data":enc,"data-v":"1","data-e":"1","data-c":str(use_algo),"data-n":str(len(enc)),"data-s":salt,"data-i":str(PBKDF2_ITERATIONS),"data-iv":iv,"data-civ":civ,"data-ck":base64.b64encode(ck).decode('ascii')}

# ---------- packed decoding / runtime extraction ----------
def read_packed_v4(path: str):
    content=read_text(path)
    sig=read_signature(path)
    if sig!=4:
        if sig in (3,):
            raise RuntimeError("v3 package detected. Arcager 4.0 is intentionally v4-only and does not read v3 containers.")
        raise RuntimeError("input is not an Arcager v4 package")
    m=re.search(r'<script[^>]*id=[\'\"]__pk[\'\"][^>]*>([\s\S]*?)</script>',content,re.I)
    if not m: raise RuntimeError("v4 container block not found")
    attrs=dict((k.lower(),v or v2) for k,v,v2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')',m.group(0)))
    data=z85_decode(m.group(1),int(attrs.get('data-n','0')))
    encrypted=attrs.get('data-e')=='1'
    return content,attrs,data,encrypted


def decrypt_container(attrs,data,password=None):
    if attrs.get('data-e')!='1': return data,None
    if password is None: password=prompt_password("Payload password: ")
    key=derive_key(password,attrs['data-s'],int(attrs['data-i']))
    try:
        if aes_decrypt(key,attrs['data-civ'],base64.b64decode(attrs['data-ck']))!=PASSWORD_CHECK: raise ValueError
        return aes_decrypt(key,attrs['data-iv'],data),key
    except Exception:
        raise RuntimeError("incorrect password, or the file is damaged. Check the password and try again (--password PW, or type it at the prompt).")


def parse_pack_for_cli(path, password=None):
    content,attrs,data,encrypted=read_packed_v4(path)
    container,key=decrypt_container(attrs,data,password)
    C=V4ContainerBuilder.parse(container)
    return content,attrs,C,key


def materialize_logical(C, idx, stream_cache):
    n=C['logical'][idx]; p=C['physical'][n['physical']]; sid=p['stream']; s=C['streams'][sid]
    if sid not in stream_cache:
        end=C['streams'][sid+1]['off'] if sid+1<len(C['streams']) else len(C['payload'])
        stream_cache[sid]=decompress_bytes(C['payload'][s['off']:end],s['algo'])
        if len(stream_cache[sid])!=s['raw_size'] or (binascii.crc32(stream_cache[sid])&0xffffffff)!=s['crc']:
            raise RuntimeError(f"stream {sid} integrity check failed")
    raw=stream_cache[sid]
    next_off=C['next_off'][n['physical']]
    if p['off'] < 0 or next_off < p['off'] or next_off > len(raw):
        raise RuntimeError(f"physical resource {n['physical']} has invalid stream boundaries")
    return raw[p['off']:next_off]


def logical_to_data_uri(data,mime):
    return "data:"+mime+";base64,"+base64.b64encode(data).decode('ascii')


# ---------- package view: one parsed package shared by -l, -x and -u ----------
TOKEN_RE = re.compile(r'arcager-res:(\d+)([?#][^\s"\'<>\)\]]*)?')
_ATTR_RE = re.compile(r'''([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?''')
_EXTS_BY_MIME = defaultdict(set)
for _e, _m in _BUNDLE_EXTS.items():
    _EXTS_BY_MIME[normalize_mime(_m)].add(_e)
for _m, _e in _EXT_BY_MIME.items():
    _EXTS_BY_MIME[normalize_mime(_m)].add(_e)
# MIME types whose stored text may contain arcager-res:N tokens that point at other resources.
_TOKEN_TEXT_MIMES = frozenset((
    "text/html", "application/xhtml+xml", "text/css", "text/javascript", "application/javascript",
    "image/svg+xml", "text/xml", "application/xml", "text/markdown", "text/plain",
))
# MIME types that can be written into a data: URL as readable text instead of base64.
_TEXT_URL_MIMES = (_TOKEN_TEXT_MIMES - {"image/svg+xml"}) | frozenset(("application/json", "text/csv", "text/tab-separated-values"))


def extraction_name(path: str, mime: str) -> str:
    """File name used when a resource is written to disk (and shown by -l).

    The stored name is kept whenever its extension already fits the stored MIME
    (photo.jpeg stays photo.jpeg). A missing or mismatching extension - bundle
    keys are stored without one, and a PNG converted to WebP is no longer a
    PNG - is replaced by the canonical one for the stored MIME.
    """
    path = path.replace("\\", "/")
    m = normalize_mime(mime)
    if m == "application/octet-stream" or m not in _EXT_BY_MIME:
        return path
    base, ext = posixpath.splitext(path)
    if ext.lower() in _EXTS_BY_MIME[m]:
        return path
    return base + _EXT_BY_MIME[m]


def safe_extraction_filename(name: str) -> str:
    """Make a logical path safe to create below the destination folder."""
    parts = [x for x in name.replace("\\", "/").split("/") if x not in ("", ".", "..")]
    if os.name == "nt":
        parts = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", x).rstrip(" .") or "_" for x in parts]
    return "/".join(parts) if parts else "item.bin"


def _unique_path(path: str, used: set) -> str:
    if path.lower() not in used:
        used.add(path.lower()); return path
    base, ext = posixpath.splitext(path); n = 2
    while f"{base}~{n}{ext}".lower() in used:
        n += 1
    cand = f"{base}~{n}{ext}"; used.add(cand.lower()); return cand


def url_path(rel: str) -> str:
    """Percent-encode a relative path for use in an href/src attribute."""
    return urllib.parse.quote(rel, safe="/-_.~!$&()*+,;=:@")


def relative_url(target: str, from_file: str) -> str:
    start = posixpath.dirname(from_file) or "."
    return url_path(posixpath.relpath(target, start))


@dataclasses.dataclass
class Resource:
    """One thing a package can give back: a stored file, a table embedded in the page, or a hidden-bundle member."""
    raw_path: str
    out_path: str
    mime: str
    size: int
    source_mime: str = ""
    kind: str = "file"                 # file | embedded | hidden
    index: Optional[int] = None        # logical index inside the container (files)
    data: Optional[bytes] = None       # bytes of embedded / hidden resources
    is_root: bool = False
    converted: bool = False
    aliases: list = dataclasses.field(default_factory=list)   # extra runtime names (arcager.images keys)
    csv_keys: list = dataclasses.field(default_factory=list)  # arcager.csv keys that expose this resource

    @property
    def category(self) -> str:
        return "pack" if self.kind == "hidden" else media_category(self.mime)


class PackageView:
    """A parsed v4 package with a lazily built resource catalog."""

    def __init__(self, path: str, password: Optional[str] = None):
        self.path = path
        self.content, self.attrs, self.C, self.key = parse_pack_for_cli(path, password)
        self.cache = {}
        self.root_index = next((i for i, n in enumerate(self.C['logical']) if n['path'] == 'index.html'), None)
        self._catalog = None
        self._blocks = None
        self._path_of = {}

    # -- raw access
    def raw(self, i: int) -> bytes:
        return materialize_logical(self.C, i, self.cache)

    def mime(self, i: int) -> str:
        return self.C['physical'][self.C['logical'][i]['physical']]['mime']

    def root_text(self) -> str:
        if self.root_index is None:
            raise RuntimeError("this package has no index.html resource; it may be damaged. Re-create it from the original sources.")
        return self.raw(self.root_index).decode('utf-8', errors='replace')

    # -- catalog
    def catalog(self):
        """Every distinct resource exactly once.

        Runtime-only alias names are folded into their resource, and tables
        that --merge stored as inline blocks are matched with the file that
        holds the same data, so nothing is listed or extracted twice.
        """
        if self._catalog is not None:
            return self._catalog
        C = self.C; used = set(); res = []
        alias_by_phys = defaultdict(list)
        for i, n in enumerate(C['logical']):
            if n['flags'] & 8:
                alias_by_phys[n['physical']].append(n['path'])
        by_phys = defaultdict(list)
        for i, n in enumerate(C['logical']):
            if n['flags'] & 8:
                continue
            pid = n['physical']; p = C['physical'][pid]
            src = p.get('source_mime', p['mime'])
            r = Resource(raw_path=n['path'], out_path="", mime=p['mime'], size=C['next_off'][pid] - p['off'],
                         source_mime=src, kind='file', index=i, is_root=(i == self.root_index),
                         converted=bool(p['flags'] & 1) and src != p['mime'])
            r.out_path = _unique_path(safe_extraction_filename(extraction_name(n['path'], p['mime'])), used)
            res.append(r); by_phys[pid].append(r)
        for pid, names in alias_by_phys.items():
            owners = by_phys.get(pid)
            if not owners:
                continue
            for name in names:
                stem = posixpath.splitext(name)[0]
                owner = next((o for o in owners if posixpath.splitext(o.raw_path)[0].endswith(stem)), owners[0])
                owner.aliases.append(name)
        self._path_of = {r.index: r.out_path for r in res}
        for i, n in enumerate(C['logical']):       # alias logical ids resolve to their owner's path
            if n['flags'] & 8 and by_phys.get(n['physical']):
                self._path_of[i] = by_phys[n['physical']][0].out_path
        self._attach_embedded_tables(res, used)
        self._catalog = res
        return res

    def table_blocks(self):
        """Embedded tables found in the root page, each tagged with the Resource it corresponds to."""
        if self._blocks is None:
            self.catalog()
        return self._blocks

    def _attach_embedded_tables(self, res, used):
        blocks = []
        if self.root_index is not None:
            try:
                blocks = scan_csv_blocks(self.root_text())
            except Exception:
                blocks = []
        files = [r for r in res if r.kind == 'file' and normalize_mime(r.mime) in ("text/csv", "text/tab-separated-values")]
        by_path = {}
        for r in files:
            by_path[r.raw_path] = r; by_path[r.out_path] = r
        content_of = {}
        claimed = set()
        for b in blocks:
            target = None
            if b['src']:
                target = by_path.get(b['src'])
            if target is None:
                want = unescape_from_script(b['body']).strip("\r\n")
                for r in files:
                    if id(r) in claimed:
                        continue
                    if r.index not in content_of:
                        content_of[r.index] = self.raw(r.index).decode('utf-8', errors='replace').strip("\r\n")
                    if content_of[r.index] == want:
                        target = r; break
            if target is not None:
                claimed.add(id(target))
                target.csv_keys.append(b['key'])
                b['target'] = target
                continue
            # Not backed by a stored file: the block itself is the only copy.
            name = b['src'] or (b['key'] + ".csv")
            data = (b['body_raw'] if b['exact'] else unescape_from_script(b['body_raw']) + ("\n" if b['body_raw'] and not b['body_raw'].endswith("\n") else "")).encode('utf-8')
            r = Resource(raw_path=name, out_path=_unique_path(safe_extraction_filename(name), used), mime="text/csv",
                         size=len(data), source_mime="text/csv", kind='embedded', data=data, csv_keys=[b['key']])
            res.append(r); b['target'] = r
        self._blocks = blocks

    # -- turning stored text back into files that reference each other by path
    def restore_text(self, r: Resource) -> bytes:
        """Stored bytes of a file resource with arcager-res:N tokens turned back into relative paths.

        For index.html, inline tables written by --merge become <link rel="csv"> again.
        """
        data = self.raw(r.index)
        if normalize_mime(r.mime) not in _TOKEN_TEXT_MIMES:
            return data
        text = data.decode('utf-8', errors='replace')
        if r.is_root:
            for b in sorted(self.table_blocks(), key=lambda b: -b['span'][0]):
                t = b.get('target')
                if t is not None and (b['src'] or t.kind == 'file'):
                    link = f'<link rel="csv" href="{escape_attr(relative_url(t.out_path, r.out_path))}">'
                    text = text[:b['span'][0]] + link + text[b['span'][1]:]
        missing = [0]
        def rr(m):
            j = int(m.group(1)); target = self._path_of.get(j)
            if target is None:
                missing[0] += 1; return m.group(0)
            return relative_url(target, r.out_path) + (m.group(2) or '')
        return TOKEN_RE.sub(rr, text).encode('utf-8')

    def referenced_paths(self, r: Resource):
        """out_paths of the resources a file resource points at (used to warn about partial extractions)."""
        if r.kind != 'file' or normalize_mime(r.mime) not in _TOKEN_TEXT_MIMES:
            return set()
        text = self.raw(r.index).decode('utf-8', errors='replace')
        found = {self._path_of[int(m.group(1))] for m in TOKEN_RE.finditer(text) if int(m.group(1)) in self._path_of}
        if r.is_root:
            found |= {b['target'].out_path for b in self.table_blocks() if b.get('target') is not None and (b['src'] or b['target'].kind == 'file')}
        found.discard(r.out_path)
        return found


# ---------- selection ----------
def _only_match(r: Resource, patterns) -> bool:
    names = {r.out_path.lower(), r.raw_path.lower(), posixpath.basename(r.out_path).lower(),
             posixpath.splitext(r.out_path)[0].lower()}
    names |= {k.lower() for k in r.csv_keys} | {a.lower() for a in r.aliases}
    for pat in patterns:
        p = pat.strip().lower().replace("\\", "/")
        if p.startswith("./"):
            p = p[2:]
        if p.endswith("/"):
            p += "*"
        if any(fnmatch.fnmatchcase(n, p) for n in names):
            return True
    return False


def select_resources(resources, types, only):
    out = []
    for r in resources:
        if r.kind == 'hidden':
            continue
        if not any(matches_type(r.mime, t) for t in types):
            continue
        if only and not _only_match(r, only):
            continue
        out.append(r)
    return out


def _type_summary(resources) -> str:
    counts = defaultdict(int)
    for r in resources:
        counts[r.category] += 1
    return ", ".join(f"{k} ({v})" for k, v in sorted(counts.items())) or "nothing"


def _no_match_hint(view_resources, types, only) -> str:
    bits = []
    if only:
        bits.append(f"no resource name matches {', '.join(repr(x) for x in only)}")
    else:
        bits.append(f"no resource matches type {','.join(types)}")
    bits.append(f"this package contains: {_type_summary(view_resources)}")
    if only:
        sample = [r.out_path for r in view_resources[:5]]
        bits.append("names look like: " + ", ".join(sample) + (" …" if len(view_resources) > 5 else ""))
        bits.append("patterns are case-insensitive and may use * and ?, e.g. --only 'data/*.csv'")
    else:
        bits.append("use -l all to see everything")
    return "; ".join(bits)


# ---------- hidden bundle ----------
def load_hidden_bundle(content: str, opts, *, prompt: bool = True):
    """Decrypt the hidden bundle. Returns None if the package has none, else [(name, bytes)]."""
    hm = re.search(r'<script[^>]*id=[\'"]__hid[\'"][^>]*>([\s\S]*?)</script>', content, re.I)
    if not hm:
        return None
    tag = re.search(r'<script[^>]*id=[\'"]__hid[\'"][^>]*>', content, re.I).group(0)
    a = dict((k.lower(), v or v2) for k, v, v2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')', tag))
    raw = z85_decode(hm.group(1), int(a.get('data-n', '0')))
    pw = opts.bundle_password or (prompt_password('Hidden bundle password: ') if prompt else None)
    if pw is None:
        return None
    try:
        key = derive_key(pw, a['data-s'], int(a['data-i']))
        if aes_decrypt(key, a['data-civ'], base64.b64decode(a['data-ck'])) != PASSWORD_CHECK:
            raise ValueError("password check failed")
        body = decompress_bytes(aes_decrypt(key, a['data-iv'], raw), int(a.get('data-c', '0')))
        head = struct.unpack_from('<8sII', body, 0)
        if head[0] != b'ARCAGHID':
            raise ValueError('invalid hidden bundle header')
        ml = head[2]; meta = json.loads(body[16:16 + ml].decode('utf-8')); payload = body[16 + ml:]
    except Exception as e:
        raise RuntimeError("cannot open the hidden bundle: wrong --bundle-password, or the data is damaged. "
                           "The hidden bundle has its own password, separate from the package password.") from e
    return [(name, payload[off:off + n]) for name, off, n in meta]


def _hidden_resources(content, opts, types, only):
    """Hidden bundle members as Resources, honoring the 'pack' / 'all' policy (see --help)."""
    explicit = 'pack' in types
    if not explicit and not ('all' in types and opts.bundle_password):
        if 'all' in types and re.search(r'id=[\'"]__hid[\'"]', content):
            warn("this package also contains a hidden encrypted bundle, which is not included. "
                 "Add --bundle-password PASSWORD (or use the type 'pack') to include it.")
        return []
    items = load_hidden_bundle(content, opts, prompt=explicit)
    if items is None:
        if explicit:
            warn("this package has no hidden bundle (nothing was added with -b PATH hidden).")
        return []
    out = []
    for name, data in items:
        r = Resource(raw_path=name, out_path=safe_extraction_filename(name), mime=mime_for(name), size=len(data), kind='hidden', data=data)
        if only and not _only_match(r, only):
            continue
        out.append(r)
    return out


# ---------- list ----------
def _print_listing(rows, total_resources, opts, title=None):
    if title:
        out(f"{C['bold']}{title}{C['reset']}")
    out(f"{C['dim']}{'TYPE':7} {'BYTES':>9} {'MIME':32} PATH{C['reset']}")
    for r in rows:
        notes = []
        if r.converted:
            notes.append(f"stored as {r.mime}, was {r.source_mime}")
        if r.csv_keys:
            notes.append("exposed as arcager.csv." + ", arcager.csv.".join(r.csv_keys))
        if r.kind == 'embedded':
            notes.append("embedded in index.html")
        if opts.verbose and r.aliases:
            notes.append("also arcager.images[" + "], [".join(repr(a) for a in r.aliases) + "]")
        suffix = f"   {C['dim']}({'; '.join(notes)}){C['reset']}" if notes else ""
        out(f"{r.category:7} {r.size:>9} {r.mime:32} {r.out_path}{suffix}")
    total = sum(r.size for r in rows if r.kind != 'embedded')   # embedded tables are already inside index.html
    shown = len(rows)
    hidden_from_view = total_resources - sum(1 for r in rows if r.kind != 'hidden')
    tail = f"  ({hidden_from_view} more not shown by the current filter; use -l all)" if hidden_from_view > 0 else ""
    out(f"{C['dim']}{shown} resource{'s' if shown != 1 else ''} · {size_str(total)} stored{tail}{C['reset']}")


def list_packed(path, opts):
    view = PackageView(path, opts.password)
    resources = view.catalog()
    rows = select_resources(resources, opts.list_types, opts.only)
    rows += _hidden_resources(view.content, opts, opts.list_types, opts.only)
    if not rows:
        warn(_no_match_hint(resources, opts.list_types, opts.only))
        return []
    _print_listing(rows, len(resources), opts, title=(path if len(opts.inputs) > 1 else None))
    return rows


def _data_uri_size(payload: str) -> int:
    clean = payload.replace("\n", "").replace("\r", "").strip()
    return max(0, len(clean) * 3 // 4 - (len(clean) - len(clean.rstrip("="))))


def list_unpacked(path, opts):
    html = read_text(path)
    items = []; seen = set()
    def add(url):
        if url in seen:
            return
        seen.add(url)
        if url.lower().startswith("data:"):
            m = _DATA_URI_RE.match(url)
            mime = (m.group(1).split(";", 1)[0].lower() if m else "application/octet-stream")
            size = _data_uri_size(m.group(2)) if m else 0
            items.append((mime, size, f"(embedded data: URI, {mime})"))
        else:
            ext = " [external]" if url.lower().startswith(("http://", "https://", "//")) else ""
            items.append((_mime_from_url(url), 0, url + ext))
    for m in re.finditer(r'\b(?:src|href|poster|xlink:href)\s*=\s*([\'"])([^\'"]+)\1', html, re.I):
        add(m.group(2))
    for m in re.finditer(r'\bsrcset\s*=\s*([\'"])([^\'"]+)\1', html, re.I):
        for it in m.group(2).split(','):
            toks = it.strip().split()
            if toks:
                add(toks[0])
    for blk in scan_csv_blocks(html):
        items.append(("text/csv", len(blk['body'].encode('utf-8')), f"{blk['key']}.csv (embedded table)"))
    rows = [it for it in items if any(matches_type(it[0], t) for t in opts.list_types)]
    warn(f"{path} is a plain HTML file, not an Arcager package; listing the resources it references "
         f"(sizes are only known for embedded ones).")
    out(f"{C['dim']}{'TYPE':7} {'BYTES':>9} {'MIME':32} REFERENCE{C['reset']}")
    for mime, size, name in rows:
        out(f"{media_category(mime):7} {(size or '-'):>9} {mime:32} {name}")
    out(f"{C['dim']}{len(rows)} reference{'s' if len(rows) != 1 else ''}{C['reset']}")
    return rows


def _mime_from_url(url):
    p = url.split('?', 1)[0].split('#', 1)[0]; return mime_for(p)


# ---------- extract ----------
def _write_resource(dest, r: Resource, data: bytes):
    outp = os.path.join(dest, r.out_path.replace('/', os.sep))
    write_bytes(outp, data)
    return outp


def extract_packed(path, opts, dest):
    """Write the selected resources below `dest`. Returns {'count': n, 'files': [...]}."""
    view = PackageView(path, opts.password)
    resources = view.catalog()
    chosen = select_resources(resources, opts.extract_types, opts.only)
    chosen += _hidden_resources(view.content, opts, opts.extract_types, opts.only)
    _check_overwrite(dest, [r.out_path for r in chosen], opts)
    written = []; chosen_paths = {r.out_path for r in chosen if r.kind != 'hidden'}
    dangling = set()
    for r in chosen:
        if r.kind == 'file':
            data = view.restore_text(r)
            dangling |= {p for p in view.referenced_paths(r) if p not in chosen_paths}
        else:
            data = r.data
        _write_resource(dest, r, data)
        written.append((r.out_path, len(data)))
    if not chosen:
        warn(_no_match_hint(resources, opts.extract_types, opts.only))
    elif dangling:
        warn(f"the extracted pages refer to {len(dangling)} resource(s) that were not extracted "
             f"(e.g. {sorted(dangling)[0]}). Extract with -x all for a complete, working folder.")
    return {"count": len(written), "files": written}


def extract_unpacked(path, opts, dest):
    """Plain (non-Arcager) HTML: pull out embedded data: URIs and CSV tables."""
    html = read_text(path)
    base = Path(path).stem
    pending = []
    for i, m in enumerate(_DATA_URI_RE.finditer(html)):
        head, payload = m.group(1), m.group(2)
        mime = head.split(';', 1)[0].lower()
        if not any(matches_type(mime, t) for t in opts.extract_types):
            continue
        try:
            raw = base64.b64decode(payload.replace("\n", "").replace("\r", ""), validate=False)
        except Exception:
            continue
        pending.append((f"{base}_datauri_{i:04d}{_EXT_BY_MIME.get(mime, '.bin')}", raw))
    if any(matches_type("text/csv", t) for t in opts.extract_types):
        for blk in scan_csv_blocks(html):
            name = safe_extraction_filename(blk['src'] or (blk['key'] + '.csv'))
            body = blk['body_raw'] if blk['exact'] else blk['body'] + ("\n" if blk['body'] and not blk['body'].endswith("\n") else "")
            pending.append((name, body.encode('utf-8')))
    if not pending:
        warn(f"{path} is a plain HTML file (not an Arcager package) and contains no embedded data: URIs or CSV tables "
             f"matching '{','.join(opts.extract_types)}'. To extract resources from a package, point -x at the packed_*.html file.")
        return {"count": 0, "files": []}
    _check_overwrite(dest, [n for n, _ in pending], opts)
    os.makedirs(dest, exist_ok=True)
    for name, raw in pending:
        write_bytes(os.path.join(dest, name.replace('/', os.sep)), raw)
    return {"count": len(pending), "files": [(n, len(d)) for n, d in pending]}


# ---------- unpack (-u): one self-contained HTML file, text kept as text ----------
_PCT_SAFE = "".join(chr(c) for c in range(0x20, 0x7f) if chr(c) not in '%#"\'\\&<>`')
_PCT_SAFE_STRICT = _PCT_SAFE.replace(" ", "").replace("(", "").replace(")", "")


def pct_text(text: str, strict: bool = False) -> str:
    """Percent-encode text for a data: URL, leaving ordinary characters readable.

    strict=True additionally encodes space and parentheses so the result is safe
    inside an unquoted CSS url(...).
    """
    return urllib.parse.quote(text, safe=_PCT_SAFE_STRICT if strict else _PCT_SAFE)


def _strip_text_prefix(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


class _Unpacker:
    """Turns a package's root page back into one standalone HTML document."""

    def __init__(self, view: PackageView):
        self.v = view; self.C = view.C
        self.memo = {}; self.visiting = set()
        self.stats = defaultdict(int)
        self.cycle_paths = set()
        view.catalog()
        self.by_path = None

    # -- resources
    def valid(self, j): return 0 <= j < len(self.C['logical'])
    def mime(self, j): return normalize_mime(self.v.mime(j))
    def path(self, j): return self.C['logical'][j]['path']
    def legacy(self, j): return self.path(j).startswith("__data__/")   # came from an inline data: URI in the source

    def resolved(self, j: int) -> bytes:
        """Stored bytes with tokens inside text resources replaced by data: URLs."""
        if j in self.memo:
            return self.memo[j]
        raw = self.v.raw(j)
        if self.mime(j) not in _TOKEN_TEXT_MIMES:
            return raw
        if j in self.visiting:
            self.cycle_paths.add(self.path(j)); return raw
        self.visiting.add(j)
        try:
            text = raw.decode('utf-8', errors='replace')
            text = TOKEN_RE.sub(lambda m: self._token_url(m, strict=True), text)
            data = text.encode('utf-8')
        finally:
            self.visiting.discard(j)
        self.memo[j] = data
        return data

    def data_url(self, j: int, strict: bool = False) -> str:
        mime = self.mime(j); data = self.resolved(j)
        if mime in _TEXT_URL_MIMES and not self.legacy(j):
            self.stats['text_uris'] += 1
            return f"data:{mime};charset=utf-8,{pct_text(data.decode('utf-8', errors='replace'), strict)}"
        self.stats['binary_uris' if mime not in _TEXT_URL_MIMES else 'text_uris_base64'] += 1
        return logical_to_data_uri(data, self.v.C['physical'][self.C['logical'][j]['physical']]['mime'])

    def _token_url(self, m, strict=False) -> str:
        j = int(m.group(1)); suffix = m.group(2) or ''
        if not self.valid(j):
            self.stats['unresolved'] += 1; return m.group(0)
        frag = suffix[suffix.index('#'):] if '#' in suffix else ''     # ?query is meaningless in a data: URL
        return self.data_url(j, strict) + frag

    def _token_repl_in(self, m):
        before = m.string[max(0, m.start() - 12):m.start()]
        strict = bool(re.search(r'url\(\s*[\'"]?$|@import\s+[\'"]?$', before, re.I))
        return self._token_url(m, strict)

    # -- relative references that were never tokenized (standalone packs with visible bundles)
    def _plain_map(self):
        if self.by_path is None:
            self.by_path = {}
            for i, n in enumerate(self.C['logical']):
                if n['flags'] & 8:
                    continue
                p = n['path']
                while p.startswith("./"): p = p[2:]
                p = p.lstrip("/")
                self.by_path[p] = i
                if posixpath.splitext(p)[1]:
                    self.by_path[posixpath.splitext(p)[0]] = i
                    self.by_path[posixpath.basename(p)] = i
        return self.by_path

    def _resolve_plain(self, url):
        base = split_ref(url)[0]
        m = self._plain_map()
        keys = [base, base.lstrip('/'), urllib.parse.unquote(base)]
        while base.startswith("./"): base = base[2:]; keys.append(base)
        if posixpath.splitext(base)[1]: keys.append(posixpath.splitext(base)[0])
        for k in keys:
            if k in m: return m[k]
        return None

    def plain_refs(self, html: str) -> str:
        def attr_plain(m):
            attr, q, url = m.group(1), m.group(2), m.group(3)
            if is_external(url) or url.startswith("arcager-res:"): return m.group(0)
            j = self._resolve_plain(url)
            if j is None: return m.group(0)
            frag = split_ref(url)[1]; frag = frag[frag.index('#'):] if '#' in frag else ''
            return f'{attr}={q}{self.data_url(j)}{frag}{q}'
        html = re.sub(r'\b(src|href|poster|xlink:href)\s*=\s*(["\'])([^"\']+)\2', attr_plain, html, flags=re.I)
        def css_plain(m):
            q, url = m.group(1), m.group(2)
            if is_external(url) or url.startswith("arcager-res:"): return m.group(0)
            j = self._resolve_plain(url)
            if j is None: return m.group(0)
            frag = split_ref(url)[1]; frag = frag[frag.index('#'):] if '#' in frag else ''
            return f'url({q}{self.data_url(j, strict=True)}{frag}{q})'
        return _CSS_URL_RE.sub(css_plain, html)

    # -- <link rel=stylesheet href=token> and <script src=token> -> inline text
    @staticmethod
    def _attrs(text):
        return [(m.group(0), m.group(1).lower(), (m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4)))
                for m in _ATTR_RE.finditer(text)]

    def _text_of(self, j) -> str:
        return _strip_text_prefix(self.resolved(j).decode('utf-8', errors='replace'))

    def inline_structural(self, html: str) -> str:
        deferred = []
        keep_style = {"media", "id", "class", "title", "nonce"}

        def link_sub(m):
            tag = m.group(0)
            attrs = self._attrs(tag[len("<link"):].rstrip(">").rstrip("/"))
            d = {n: v for _, n, v in attrs}
            href = d.get("href") or ""
            tm = TOKEN_RE.fullmatch(href)
            if not tm or "stylesheet" not in (d.get("rel") or "").lower().split() or "alternate" in (d.get("rel") or "").lower().split():
                return tag
            j = int(tm.group(1))
            if not self.valid(j) or self.mime(j) != "text/css" or self.legacy(j):
                return tag
            css = self._text_of(j)
            css = re.sub(r'^\s*@charset\s+"[^"]*"\s*;\s*', '', css, count=1, flags=re.I)
            css = re.sub(r'</(style)', r'<\\/\1', css, flags=re.I)
            kept = "".join(" " + raw for raw, n, _ in attrs if n in keep_style or n.startswith("data-"))
            self.stats['inlined_css'] += 1
            return f"<style{kept}>\n{css.strip(chr(10))}\n</style>"

        def script_sub(m):
            pre, url, post = m.group(1), m.group(3), m.group(4)
            tm = TOKEN_RE.fullmatch(url)
            if not tm:
                return m.group(0)
            j = int(tm.group(1))
            if not self.valid(j) or self.mime(j) not in ("text/javascript", "application/javascript") or self.legacy(j):
                return m.group(0)
            attrs = self._attrs(pre) + self._attrs(post)
            d = {n: v for _, n, v in attrs}
            typ = (d.get("type") or "").strip().lower()
            if typ not in ("", "module", "text/javascript", "application/javascript"):
                return m.group(0)
            module = typ == "module"
            drop = {"src", "defer", "crossorigin", "integrity", "referrerpolicy", "fetchpriority", "blocking"}
            if not module:
                drop.add("async")      # inline classic scripts are never async
            kept = "".join(" " + raw for raw, n, _ in attrs if n not in drop)
            js = self._text_of(j)
            js = re.sub(r'</(script)', r'<\\/\1', js, flags=re.I)
            if re.search(r'<script', js, re.I):
                js = js.replace("<!--", "<\\!--")
            node = f"<script{kept}>\n{js.strip(chr(10))}\n</script>"
            self.stats['inlined_js'] += 1
            if "defer" in d and not module:
                deferred.append(node)   # an inline script cannot be deferred; run it after the page is parsed instead
                return ""
            return node

        html = _LINK_RE.sub(link_sub, html)
        html = _SCRIPT_SRC_RE.sub(script_sub, html)
        if deferred:
            block = "\n".join(deferred) + "\n"
            idx = html.lower().rfind("</body>")
            html = html + block if idx < 0 else html[:idx] + block + html[idx:]
        return html

    def build(self) -> str:
        html = self.v.root_text()
        html = self.plain_refs(html)
        html = self.inline_structural(html)
        html = TOKEN_RE.sub(self._token_repl_in, html)
        return html


def unpack_packed(path, opts, out_name):
    view = PackageView(path, opts.password)
    if view.root_index is None:
        raise RuntimeError("v4 root HTML resource missing; the package may be damaged. Re-create it from the original sources.")
    up = _Unpacker(view)
    html = up.build()
    write_text(out_name, html)
    if up.cycle_paths:
        warn("these resources reference each other in a loop, so one reference was left as arcager-res:N: "
             + ", ".join(sorted(up.cycle_paths)))
    if up.stats['unresolved']:
        warn(f"{up.stats['unresolved']} reference(s) pointed at resources that are not in the package and were left unchanged "
             f"(look for 'arcager-res:' in {out_name}). The package may be damaged or was edited by hand.")
    return {"inSize": len(view.content.encode()), "outSize": len(html.encode()), "restored": len([r for r in view.catalog() if r.kind == 'file']),
            "inlined_css": up.stats['inlined_css'], "inlined_js": up.stats['inlined_js'],
            "text_uris": up.stats['text_uris'], "binary_uris": up.stats['binary_uris']}


# ---------- CLI ----------
class UsageError(RuntimeError):
    """A mistake in how Arcager was invoked. The message must say how to fix it."""


@dataclasses.dataclass
class Opts:
    compression: str='gzip'
    algo: int=1
    lossy: bool=False
    strip_metadata: bool=False
    minify: bool=False
    minify_lossy: bool=False
    base_href: Optional[str]=None
    ignore_uris: list=dataclasses.field(default_factory=list)
    force: bool=False
    unpack: bool=False
    recursive: bool=False
    merge: Optional[str]=None
    inputs: list=dataclasses.field(default_factory=list)
    output: Optional[str]=None
    output_dir: Optional[str]=None
    prefix: Optional[str]=None
    encrypt: bool=False
    password: Optional[str]=None
    bundle_password: Optional[str]=None
    bundles: list=dataclasses.field(default_factory=list)
    verbose: bool=False
    list_mode: bool=False
    extract_mode: bool=False
    list_types: list=dataclasses.field(default_factory=lambda:['all'])
    extract_types: list=dataclasses.field(default_factory=lambda:['all'])
    only: list=dataclasses.field(default_factory=list)
    skip_unused: bool=False
    seen_pack_options: list=dataclasses.field(default_factory=list)   # pack-only options the user typed (for misuse warnings)


_KNOWN_OPTIONS = ("-h", "--help", "-V", "--version", "-v", "--verbose", "-f", "--force", "-r", "--recursive",
                  "-u", "--unpack", "-M", "--merge", "-o", "--output", "-O", "--output-dir", "--prefix",
                  "-E", "--encrypt", "--password", "--bundle-password", "--strip-metadata", "--base-href",
                  "--ignore-uris", "--skip-unused", "-m", "--minify", "-c", "--compression", "-b", "--bundle",
                  "-l", "--list", "-x", "--extract", "--only")


def help_text() -> str:
    w = max(len(t[0]) for t in TYPE_INFO)
    types = "\n".join(f"  {name:<{w}}  {summary}\n  {'':<{w}}  matches: {what}" for name, summary, what in TYPE_INFO)
    return f"""arcager {VERSION} — turn an HTML page or a static site into ONE self-unpacking HTML file

USAGE
  arcager [options] PAGE.html...     pack pages            -> packed_PAGE.html
  arcager -M SITE_DIR [options]      pack a whole site     -> packed_SITE_DIR.html
  arcager -l [TYPES] PACKED.html     list what is inside
  arcager -x [TYPES] PACKED.html     extract files from the package
  arcager -u PACKED.html             rebuild ONE standalone, readable HTML page
  (Run as `python arcager.py ...` if arcager is not installed as a command.)

START HERE
  arcager page.html                  pack a page (GZIP, originals kept untouched)
  arcager -M ./site                  pack a folder that has an index.html
  arcager -l packed_site.html        see what is inside
  arcager -x all -O ./out packed_site.html     get every file back, ready to re-pack
  arcager -u packed_site.html        get a single readable HTML file back

PACKING
  -M, --merge DIR      Pack a static site. Starts at DIR/index.html and follows its
                       CSS, JS, images, fonts and <link rel="csv"> tables. Other files
                       in DIR are kept too, in case your scripts load them later, and
                       Arcager warns you which files nothing links to.
  --skip-unused        With -M: leave out files that nothing links to (smaller package).
                       Only do this if your scripts never build file names at run time.
  -c, --compression gzip|brotli [lossy]
                       gzip   (default) works everywhere, no extra packages.
                       brotli smaller output; needs: pip install brotli
                       lossy  (write it right after the algorithm) lets Arcager try to
                              convert PNG/JPEG/BMP/GIF to lossless WebP. A conversion is
                              kept only if it is verified and smaller. Needs: pip install Pillow
                       Examples:  -c gzip   -c brotli   -c gzip lossy   -c brotli lossy
                       Default packing preserves original representations; nothing is
                       converted unless you add 'lossy'.
  --strip-metadata     Remove non-essential image metadata (e.g. EXIF, GPS) where safe.
                       Needs: pip install Pillow
  -E, --encrypt        Encrypt the whole package (AES-256-GCM). You are asked for a
                       password; opening the page asks for it again.
  -b, --bundle PATH [hidden]
                       Add extra files (a folder, a .zip/.tar archive or one file).
                       Visible bundles are available to your page (see arcager.images).
                       'hidden' stores them as an opaque encrypted payload with its own
                       password that the page never reads; extract it with -x pack.
  -m, --minify [lossy] Shrink Arcager's own loader script (needs: npm install -g terser).
                       'lossy' also minifies your HTML (comments/whitespace are removed).
  -r, --recursive      When an input is a folder, also pack the HTML files in its subfolders.
  --base-href URL      Insert <base href="URL"> so relative links resolve against URL.
  --ignore-uris LIST   Comma-separated MIME prefixes of data: URIs to leave inline,
                       e.g. --ignore-uris image/svg,font/
  Output size is reported for every file as a percentage of the original (e.g. 37.6%
  means the package is 37.6% of the source size) and as a factor (e.g. 2.66×).
  Already-compressed content (JPEG, PNG, video, WOFF2) cannot shrink much.

LIST / EXTRACT / UNPACK
  -l, --list [TYPES]   Show every resource once, with its type, size and path.
  -x, --extract [TYPES]
                       Write resources to disk (default folder: ./extracted).
                       Pages and styles come back as normal files that reference each
                       other by relative path, so the folder can be re-packed with -M.
                       Resources are written in their stored form: an image that was
                       converted to WebP comes back as WebP.
  --only PATTERN[,..]  Limit -l/-x to resources whose path, file name or table key
                       matches. * and ? are wildcards, case-insensitive.
                       e.g.  -x all --only 'data/*.csv'      --only elements
  -u, --unpack         Rebuild one self-contained HTML file. CSS and JavaScript become
                       normal <style> and <script> text; only images, fonts and other
                       binary files stay embedded as base64.

RESOURCE TYPES  (for -l and -x; comma-separated, default: all; e.g. -l docs,csv)
{types}
  If you ask for a type that is not present, Arcager tells you what the package
  does contain.

PASSWORDS
  --password PW          Package password (when packing with -E, or opening one).
  --bundle-password PW   Password of the hidden bundle (-b PATH hidden).
  Leave them out and Arcager asks, without echoing. Passwords on the command line can
  be seen in shell history and process lists, so prefer the prompt on shared machines.

OUTPUT LOCATION AND NAMES
  -o FILE      exact output file (one input only)      -O DIR   output folder
  --prefix P   prefix for packed file names (default: packed_; unpacked files are unpacked_)
  -f, --force  overwrite existing files without asking   -v, --verbose  show details
  -V, --version  version and which components are installed (incl. Terser)    -h, --help  this text

EXIT CODES
  0 success    1 usage error or unreadable input    2 some files failed
  3 nothing processed (every input skipped, or -x found nothing)    130 interrupted

REQUIREMENTS
  Python 3.8+ and the 'cryptography' package (pip install -r requirements.txt): encryption
  is part of Arcager itself. Optional extras: Brotli, Pillow, Terser (-V shows what is found).

MORE
  README.md            overview and examples         docs/CLI.md   command reference
  docs/API.md          window.arcager (images + CSV) docs/ARCHITECTURE.md   internals
  Extras: pip install -r requirements-optional.txt            Check your setup: arcager -V
"""


def _suggest(word: str, choices) -> str:
    m = difflib.get_close_matches(word, list(choices), n=1, cutoff=0.6)
    return f" Did you mean '{m[0]}'?" if m else ""


def _type_error(token: str) -> UsageError:
    bad = [x.strip() for x in token.split(',') if x.strip() and x.strip().lower() not in _LIST_TYPES]
    hint = "".join(_suggest(b.lower(), _LIST_TYPES) for b in bad[:1])
    return UsageError(
        f"'{token}' is not a resource type, and no file with that name exists.{hint}\n"
        f"  Types: {_TYPE_ORDER_HINT}\n"
        f"  To pick resources by name instead of type, use --only, e.g.  -x all --only '{bad[0] if bad else token}*'")


def parse_cli(argv):
    if any(a in ('-h','--help') for a in argv):
        print(help_text()); raise SystemExit(0)
    if any(a in ('-V','--version') for a in argv):
        print(version_text()); raise SystemExit(0)
    # -fv style clusters of flags that take no value
    expanded = []
    for a in argv:
        if re.fullmatch(r'-[vfruE]{2,}', a):
            expanded.extend('-' + ch for ch in a[1:])
        else:
            expanded.append(a)
    argv = expanded
    o=Opts(); i=0
    pending_type=[None]     # (token, list_or_extract) most recently taken as TYPES
    def value(opt, what):
        nonlocal i
        i+=1
        if i>=len(argv) or (argv[i] in _KNOWN_OPTIONS and opt not in ('--password','--bundle-password')):
            raise UsageError(f"{opt} needs {what}." + ("" if "e.g." in what else f" Example: {opt} {'./site' if what.startswith('a folder containing') else 'VALUE'}"))
        return argv[i]
    def type_arg(opt):
        """Optional TYPES after -l/-x. A word that is neither a type list nor a plausible path is a mistake worth explaining."""
        nonlocal i
        if i+1>=len(argv) or argv[i+1].startswith('-'):
            return None
        nxt=argv[i+1]
        if _valid_type_list(nxt):
            i+=1; pending_type[0]=(nxt,opt)
            return [x.strip().lower() for x in nxt.split(',') if x.strip()]
        if os.path.exists(nxt) or re.search(r'[\\/]|\.\w+$', nxt):
            return None                      # looks like (or is) a file: leave it for the input list
        raise _type_error(nxt)
    while i<len(argv):
        a=argv[i]
        if a in ('-v','--verbose'): o.verbose=True
        elif a in ('-f','--force'): o.force=True
        elif a in ('-r','--recursive'): o.recursive=True; o.seen_pack_options.append(a)
        elif a in ('-u','--unpack'): o.unpack=True
        elif a in ('-M','--merge'):
            o.merge=value(a,'a folder containing index.html')
        elif a in ('-o','--output'):
            o.output=value(a,'a file name')
        elif a in ('-O','--output-dir'):
            o.output_dir=value(a,'a folder name')
        elif a=='--prefix': o.prefix=value(a,'a text prefix')
        elif a in ('-E','--encrypt'): o.encrypt=True; o.seen_pack_options.append(a)
        elif a=='--password': o.password=value(a,'a password')
        elif a=='--bundle-password': o.bundle_password=value(a,'a password')
        elif a=='--skip-unused': o.skip_unused=True; o.seen_pack_options.append(a)
        elif a=='--strip-metadata': o.strip_metadata=True; o.seen_pack_options.append(a)
        elif a=='--base-href': o.base_href=value(a,'a URL'); o.seen_pack_options.append(a)
        elif a=='--ignore-uris':
            o.ignore_uris=[x.strip() for x in value(a,'a comma-separated list of MIME prefixes').split(',') if x.strip()]; o.seen_pack_options.append(a)
        elif a=='--only':
            o.only=[x.strip() for x in value(a,'a name or wildcard pattern, e.g. --only \'data/*.csv\'').split(',') if x.strip()]
        elif a in ('-m','--minify'):
            o.minify=True; o.seen_pack_options.append(a)
            if i+1<len(argv) and argv[i+1].lower()=='lossy' and not os.path.exists(argv[i+1]): i+=1; o.minify_lossy=True
        elif a in ('-c','--compression'):
            if i+1>=len(argv): raise UsageError('-c needs an algorithm: gzip or brotli. Example: -c brotli')
            i+=1
            algo=argv[i].lower()
            if algo=='lossy': raise UsageError("'lossy' must come right after the algorithm, e.g. -c gzip lossy")
            if algo not in ('gzip','brotli'): raise UsageError(f"unknown compression '{argv[i]}': choose gzip or brotli (e.g. -c brotli).{_suggest(algo,('gzip','brotli'))}")
            o.algo=1 if algo=='gzip' else 2; o.compression=algo; o.seen_pack_options.append(a)
            if i+1<len(argv) and argv[i+1].lower()=='lossy' and not os.path.exists(argv[i+1]): i+=1; o.lossy=True
        elif a in ('-b','--bundle'):
            path=value(a,'a path (folder, .zip/.tar archive or file)'); hidden=False
            if i+1<len(argv) and argv[i+1].lower()=='hidden' and not os.path.exists(argv[i+1]): i+=1; hidden=True
            o.bundles.append((path,hidden)); o.seen_pack_options.append(a)
        elif a in ('-l','--list'):
            o.list_mode=True
            t=type_arg(a)
            if t: o.list_types=t
        elif a in ('-x','--extract'):
            o.extract_mode=True
            t=type_arg(a)
            if t: o.extract_types=t
        elif a=='lossy' and not os.path.exists(a):
            raise UsageError("'lossy' must come right after the compression option, e.g.  -c gzip lossy  (see --help)")
        elif a.startswith('-') and len(a)>1:
            raise UsageError(f"unknown option '{a}'.{_suggest(a,_KNOWN_OPTIONS)} Run 'arcager --help' to see all options.")
        else: o.inputs.append(a)
        i+=1
    if not o.inputs and not o.merge and pending_type[0] and os.path.exists(pending_type[0][0]):
        # e.g. `arcager -l docs` where `docs` is an existing file/folder and no TYPES were meant
        o.inputs.append(pending_type[0][0])
        if pending_type[0][1] in ('-l','--list'): o.list_types=['all']
        else: o.extract_types=['all']
    return o


def usage_status(result,i,total,mode='pack'):
    st=result.get('status','')
    name=os.path.basename(result.get('out') or result.get('file',''))
    if st=='ok':
        b,a=result['inSize'],result['outSize']
        if mode=='unpack':
            out(f"  [{i}/{total}] {C['green']}✓{C['reset']} {name:34} {size_str(b).rjust(10)} → {size_str(a).rjust(10)}  {C['dim']}{ratio_str(b,a)}{C['reset']}")
        else:
            smaller=a<b; mark='✓' if smaller else '!'; arrow='↓' if smaller else '↑'
            out(f"  [{i}/{total}] {C['green'] if smaller else C['yellow']}{mark}{C['reset']} {name:34} {size_str(b).rjust(10)} {arrow} {size_str(a).rjust(10)}  {C['dim']}{ratio_str(b,a)}{C['reset']}")
    elif st.startswith('skip'):
        why={'skip-already-packed':"skipped — already-packed Arcager file; use -u to unpack it first",
             'skip-no-overwrite':f"skipped — {os.path.basename(result.get('out',''))} already exists; use -f to overwrite",
             'skip-not-packed':"skipped — not an Arcager package, nothing to unpack (to pack it, run without -u)",
             'skip-unknown-version':"skipped — written by an Arcager version this release cannot read"}.get(st,st)
        out(f"  [{i}/{total}] {C['yellow']}⚠{C['reset']} {os.path.basename(result.get('file','')):34} {why}")
    else:
        out(f"  [{i}/{total}] {C['red']}✗{C['reset']} {os.path.basename(result.get('file','')):34} {result.get('error','')}")


def _hidden_bundle_block(opts, paths):
    if not opts.bundle_password:
        opts.bundle_password=prompt_password('Hidden bundle password: ',confirm=True)
    entries=collect_hidden_bundles(paths)
    hi=hidden_envelope(entries,opts.bundle_password,opts.algo)
    block='<script type="text/plain" id="__hid" '+" ".join(f'{k}="{escape_attr(str(v))}"' for k,v in hi.items() if k!='data')+'>'+z85_script_safe(hi['data'])+'</script>'
    return block, sum(len(e['data']) for e in entries)


def build_single_file(file,opts,prompter,visible_entries):
    sig=read_signature(file)
    if sig is not None:
        return {'file':file,'status':'skip-already-packed'}
    raw=read_bytes(file); html=raw.decode('utf-8',errors='replace')
    stem=re.sub(r'\.html?$','',os.path.basename(file),flags=re.I); out_name=output_path(opts,(opts.prefix or _DEFAULT_PACK_PREFIX)+stem+'.html',os.path.dirname(file))
    if os.path.abspath(out_name)==os.path.abspath(file): raise RuntimeError(f'the output would overwrite the input ({file}). Choose another name with -o or another folder with -O.')
    if not confirm_overwrite(out_name,opts.force): return {'file':file,'out':out_name,'status':'skip-no-overwrite'}
    hidden=[x for x in opts.bundles if x[1]]; visible=[x for x in opts.bundles if not x[1]]
    entries=collect_visible_bundles([p for p,_ in visible],lambda *a:vlog(opts,*a)) if visible else []
    shell,b,conv=build_packed(opts,html,len(raw),os.path.basename(out_name),entries,file)
    extra_in=sum(len(e['data']) for e in entries)
    # Attach hidden bundle after shell has been built, preserving its own password boundary.
    if hidden:
        block,hidden_bytes=_hidden_bundle_block(opts,[p for p,_ in hidden]); extra_in+=hidden_bytes
        shell=shell.replace('</body></html>',block+'</body></html>')
    write_text(out_name,shell)
    return {'file':file,'out':out_name,'status':'ok','inSize':len(raw)+extra_in,'outSize':len(shell.encode()),'stats':b.stats,'conversion':conv}


def build_merge(dir_path,opts,prompter):
    root=os.path.abspath(dir_path); stem=os.path.basename(root.rstrip(os.sep)); out_name=output_path(opts,(opts.prefix or _DEFAULT_PACK_PREFIX)+stem+'.html',os.path.dirname(root))
    if not confirm_overwrite(out_name,opts.force): return {'file':dir_path,'out':out_name,'status':'skip-no-overwrite'}
    visible=[p for p,h in opts.bundles if not h]; entries=collect_visible_bundles(visible,lambda *a:vlog(opts,*a)) if visible else []
    shell,b,g=merge_directory(dir_path,opts,out_name,entries)
    hidden=[p for p,h in opts.bundles if h]; hidden_bytes=0
    if hidden:
        block,hidden_bytes=_hidden_bundle_block(opts,hidden)
        shell=shell.replace('</body></html>',block+'</body></html>')
    write_text(out_name,shell)
    source=g.source_bytes()+hidden_bytes
    return {'file':dir_path,'out':out_name,'status':'ok','inSize':source or os.path.getsize(os.path.join(root,'index.html')),'outSize':len(shell.encode()),'stats':b.stats,'merge':g}


def _check_overwrite(dest, rel_paths, opts):
    """Refuse to silently replace existing files when extracting."""
    existing=[p for p in rel_paths if os.path.exists(os.path.join(dest,p.replace('/',os.sep)))]
    if not existing or opts.force:
        return
    msg=f"{len(existing)} file(s) already exist in {dest} (e.g. {existing[0]})."
    if not sys.stdin.isatty():
        raise RuntimeError(msg+" Use -f to overwrite them, or -O to extract into a different folder.")
    ans=input(msg+" Overwrite? [y/N] ").strip().lower()
    if ans not in ('y','yes'):
        raise RuntimeError("extraction cancelled; nothing was written. Use -O to extract into a different folder.")


def _print_extracted(result, dest, opts):
    files=result['files']
    limit=len(files) if opts.verbose else 15
    for name,size in files[:limit]:
        out(f"  {C['dim']}{size_str(size).rjust(9)}{C['reset']}  {name}")
    if len(files)>limit:
        out(f"  {C['dim']}… and {len(files)-limit} more (use -v to list all){C['reset']}")
    out(f"Extracted {result['count']} file{'s' if result['count']!=1 else ''} to {dest}")


_PACK_ONLY_NOTE = {"-c":"compression","--compression":"compression","-E":"encryption","--encrypt":"encryption","-b":"bundles","--bundle":"bundles",
                   "-m":"minify","--minify":"minify","--strip-metadata":"metadata stripping","--base-href":"--base-href",
                   "--ignore-uris":"--ignore-uris","--skip-unused":"--skip-unused"}


def _check_inputs_for_reading(opts, verb):
    for f in opts.inputs:
        if os.path.isdir(f):
            raise UsageError(f"'{f}' is a folder. {verb} works on a packed .html file. To pack a folder, use: arcager -M {f}")
        if not os.path.isfile(f):
            raise UsageError(f"cannot find '{f}' (current folder: {os.getcwd()}). Check the spelling or give the full path.")


def main(argv=None):
    opts=parse_cli(sys.argv[1:] if argv is None else argv)
    ensure_core_dependencies()
    modes=[n for n,v in (('-l',opts.list_mode),('-x',opts.extract_mode),('-u',opts.unpack),('-M',bool(opts.merge))) if v]
    if len(modes)>1: raise UsageError(f"{' and '.join(modes)} cannot be used together; choose one per run (for example run -l first, then -x).")
    reading = bool(opts.list_mode or opts.extract_mode or opts.unpack)
    if opts.merge and opts.inputs: raise UsageError(f"-M takes exactly one folder, but extra argument(s) were given: {', '.join(opts.inputs)}. "
                                                    f"To add extra files to the package use -b PATH.")
    if not opts.inputs and not opts.merge:
        raise UsageError("no input given. Examples:\n  arcager page.html          pack a page\n  arcager -M ./site          pack a site folder\n  arcager -l packed.html     list a package\n  Run 'arcager --help' for everything.")
    if opts.output and len(opts.inputs)>1: raise UsageError(f"-o names ONE output file, but {len(opts.inputs)} inputs were given. Use -O FOLDER to choose where the outputs go.")
    if opts.output and (opts.list_mode or opts.extract_mode): raise UsageError("-o does not apply to -l/-x. To choose where extracted files go, use -O FOLDER.")
    if opts.algo==2 and not HAS_BROTLI and not reading:
        raise RuntimeError(f"Brotli needs the optional 'brotli' package, which this Python does not have. Install it with:\n  {sys.executable} -m pip install brotli\nor use the default (-c gzip).")
    if reading and opts.seen_pack_options:
        names=sorted({_PACK_ONLY_NOTE[x] for x in opts.seen_pack_options if x in _PACK_ONLY_NOTE})
        if names: warn(f"{', '.join(names)} only apply when packing and were ignored.")
    if not reading:
        if opts.skip_unused and not opts.merge:
            warn("--skip-unused only applies together with -M (merge a folder), so it was ignored.")
        if opts.password and not opts.encrypt:
            warn("--password was given without -E, so the package will NOT be encrypted. Add -E to encrypt it.")
        if opts.bundle_password and not any(h for _,h in opts.bundles):
            warn("--bundle-password was given but there is no hidden bundle; add -b PATH hidden to create one.")
        if (opts.lossy or opts.strip_metadata) and not HAS_PIL:
            what=" and ".join(x for x,on in (("lossy",opts.lossy),("--strip-metadata",opts.strip_metadata)) if on)
            warn(f"{what} {'need' if (opts.lossy and opts.strip_metadata) else 'needs'} Pillow, which is not installed, so images are kept exactly as they are. "
                 f"Install it with: {sys.executable} -m pip install Pillow")
        if opts.minify and shutil.which("terser") is None:
            warn("--minify needs Terser, which was not found, so the loader script is left unminified. Install it with: npm install -g terser")
    if (opts.list_mode or opts.extract_mode): _check_inputs_for_reading(opts, '-l' if opts.list_mode else '-x')

    if opts.list_mode:
        for f in opts.inputs:
            sig=read_signature(f)
            if sig==4: list_packed(f,opts)
            elif sig is not None and sig<4: raise RuntimeError(f'{f}: this is an Arcager {sig}.x package (v{sig}); Arcager 4 reads only v4 packages. Re-pack the original sources with Arcager 4.')
            elif sig is not None: raise RuntimeError(f'{f}: written by a newer Arcager (package v{sig}); update Arcager to read it.')
            else: list_unpacked(f,opts)
        return
    if opts.extract_mode:
        base_dest=opts.output_dir or './extracted'
        total=0
        for f in opts.inputs:
            sig=read_signature(f)
            dest=os.path.join(base_dest,re.sub(r'\.html?$','',os.path.basename(f),flags=re.I)) if len(opts.inputs)>1 else base_dest
            if sig==4:
                result=extract_packed(f,opts,dest)
            elif sig is not None and sig<4: raise RuntimeError(f'{f}: this is an Arcager {sig}.x package (v{sig}); Arcager 4 reads only v4 packages. Re-pack the original sources with Arcager 4.')
            elif sig is not None: raise RuntimeError(f'{f}: written by a newer Arcager (package v{sig}); update Arcager to read it.')
            else:
                result=extract_unpacked(f,opts,dest)
            total+=result['count']
            if result['count']: _print_extracted(result,dest,opts)
        if not total: raise SystemExit(3)
        return

    files=[]
    if opts.merge:
        if not os.path.isdir(opts.merge):
            raise UsageError(f"-M needs a folder, but '{opts.merge}' is {'a file' if os.path.isfile(opts.merge) else 'not found'}. "
                             f"{'To pack a single page, run: arcager '+opts.merge if os.path.isfile(opts.merge) else 'Check the spelling or give the full path.'}")
        files=[opts.merge]
    else:
        for p in opts.inputs:
            if os.path.isdir(p):
                fs,_=scan_dir(p,opts.recursive)
                if not fs: warn(f"no .html files found in '{p}'{'' if opts.recursive else ' (add -r to search subfolders, or use -M to pack it as a site)'}.")
                files.extend(fs)
            elif os.path.isfile(p): files.append(p)
            else: warn(f"cannot find '{p}' (current folder: {os.getcwd()}); skipping it. Check the spelling or give the full path.")
    # deterministic de-dup of batch inputs
    seen=set(); files=[f for f in files if not (os.path.realpath(f) in seen or seen.add(os.path.realpath(f)))]
    if not files: raise UsageError('nothing to process: no usable input files were found.')
    results=[]; t=time.time(); mode='unpack' if opts.unpack else 'pack'
    for i,f in enumerate(files,1):
        try:
            if opts.merge: r=build_merge(f,opts,None)
            elif opts.unpack:
                sig=read_signature(f)
                if sig!=4: r={'file':f,'status':'skip-not-packed' if sig is None else 'skip-unknown-version'}
                else:
                    stem=os.path.basename(f); core=stem[len(opts.prefix):] if opts.prefix and stem.startswith(opts.prefix) else (stem[len(_DEFAULT_PACK_PREFIX):] if stem.startswith(_DEFAULT_PACK_PREFIX) else stem)
                    out_name=output_path(opts,_DEFAULT_UNPACK_PREFIX+core,os.path.dirname(f))
                    if os.path.abspath(out_name)==os.path.abspath(f): raise RuntimeError(f'the output would overwrite the package itself ({f}). Choose another name with -o or another folder with -O.')
                    if not confirm_overwrite(out_name,opts.force): r={'file':f,'out':out_name,'status':'skip-no-overwrite'}
                    else:
                        rr=unpack_packed(f,opts,out_name); r={'file':f,'out':out_name,'status':'ok',**rr}
            else: r=build_single_file(f,opts,None,[])
        except SystemExit: raise
        except Exception as e:
            r={'file':f,'status':'error','error':str(e)}
        results.append(r); usage_status(r,i,len(files),mode)
    ok=[r for r in results if r['status']=='ok']; failed=[r for r in results if r['status']=='error']; skipped=[r for r in results if r['status'].startswith('skip')]
    out(''); out(f"Summary: {len(ok)} processed · {len(skipped)} skipped · {len(failed)} failed · {time.time()-t:.2f}s")
    if len(ok)>1:
        tb=sum(r['inSize'] for r in ok); ta=sum(r['outSize'] for r in ok)
        out(f"  total: {size_str(tb)} → {size_str(ta)}  ({ratio_str(tb,ta)})")
    for r in ok:
        if r.get('stats'):
            s=r['stats']
            dup=f" · {s['duplicate_count']} duplicate{'s' if s['duplicate_count']!=1 else ''} stored once (saved {size_str(s['duplicate_bytes'])})" if s['duplicate_count'] else ""
            out(f"  {s['logical'] or '-'} resource{'s' if s['logical']!=1 else ''} ({s['physical'] or '-'} distinct) in {s['streams']} stream{'s' if s['streams']!=1 else ''} · "
                f"payload {size_str(s['stored_bytes'])} from {size_str(s['raw_bytes'])} ({percent_of(s['stored_bytes'],s['raw_bytes'])} of raw){dup}")
            if opts.verbose and s.get('solid_attempts'):
                for a in s['solid_attempts']:
                    algo_name = {1: 'GZIP', 2: 'Brotli'}.get(a['algorithm'], 'stored')
                    verdict = 'accepted' if a['accepted'] else ('rejected' if a['algorithm'] else 'not attempted')
                    saved = a['saved_bytes']
                    delta_text = size_str(saved) if saved >= 0 else f"-{size_str(-saved)}"
                    reason = a.get('decision_reason')
                    suffix = f" ({reason})" if reason and reason not in ('accepted', 'stored: candidate not smaller') else ''
                    out(f"  solid: {a['mime']} · {algo_name} {size_str(a['raw_bytes'])} → {size_str(a['candidate_bytes'])} ({percent_of(a['candidate_bytes'],a['raw_bytes'])}) · {delta_text} · {verdict}{suffix}")
        if r.get('merge') and r['merge'].external:
            ext=sorted(r['merge'].external)
            out(f"  external resource dependencies: {len(ext)} (still loaded from the network when the page opens)")
        if r.get('conversion') and opts.verbose:
            for row in r['conversion']: print(f"  conversion: {row}",file=sys.stderr)
    if mode=='pack' and any(r['outSize']>r['inSize'] for r in ok):
        hint=" For the smallest result try -c brotli." if opts.algo==1 else ""
        out(f"  note: some packages are larger than their source. Already-compressed content (JPEG, PNG, video, WOFF2) cannot shrink, and the self-unpacking wrapper adds a little overhead.{hint}")
    if failed:
        for r in failed: err(f"{r['file']}: {r['error']}")
        raise SystemExit(2)
    if skipped and not ok: raise SystemExit(3)


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: raise SystemExit(130)
    except BrokenPipeError:
        try: sys.stdout = open(os.devnull, "w")     # reader (e.g. `| head`) went away: not an error
        except Exception: pass
        raise SystemExit(0)
    except SystemExit: raise
    except UsageError as e:
        err(str(e)); print("  Run 'arcager --help' for usage.",file=sys.stderr); raise SystemExit(1)
    except Exception as e:
        err(str(e)); raise SystemExit(1)
