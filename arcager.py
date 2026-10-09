#!/usr/bin/env python3
"""Arcager 4.0.1 — Solid Update.

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
import getpass
import gzip
import hashlib
import io
import json
import mimetypes
import os
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

VERSION = "4.0.0"
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
    ".css":"text/css", ".js":"text/javascript",
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
_LIST_TYPES = ("all", "images", "videos", "audio", "fonts", "docs", "media", "pack", "csv", "text", "data", "vector")

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


def media_category(mime: str) -> str:
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
    if m.startswith("text/") or m in ("application/json", "application/xml", "application/xhtml+xml"):
        return "text" if m not in ("text/html", "text/css", "text/javascript", "text/csv", "text/tab-separated-values", "text/xml", "text/plain", "text/markdown") else "docs"
    if m in ("application/pdf", "application/postscript"):
        return "docs"
    return "other"


def is_textual(mime: str) -> bool:
    m = normalize_mime(mime)
    return (m.startswith("text/") or m in ("application/json", "application/xml", "application/xhtml+xml", "application/javascript", "application/x-javascript"))


def extless(path: str) -> str:
    return str(Path(path).with_suffix("")) if Path(path).suffix else path


def safe_key(key: str, src: str):
    if not key or not _SAFE_KEY_RE.match(key) or key.endswith(("/", ".")) or ".." in key.split("/"):
        raise RuntimeError(f"unsafe bundle key {key!r} from {src}")

# ---------- CSV ----------
def scan_csv_blocks(html: str):
    found = []
    for m in _SCRIPT_TAG_RE.finditer(html):
        attrs = m.group(1) or ""
        if not _CSV_TYPE_RE.search(attrs):
            continue
        km = _CSV_KEY_RE.search(attrs)
        key = km.group(2) if km else f"csv{len(found)}"
        found.append({"key": key, "body": m.group(2).strip("\r\n")})
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

        self.stats["logical"]=len(self.nodes)
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
        self._queue=deque()
        self.bundle_nodes={}

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
                self.unmergeable.add(url); return None
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
        for cur, dirs, files in os.walk(self.root_dir, followlinks=False):
            dirs.sort(); files.sort()
            # Do not descend into VCS metadata or an already-packed output cache.
            dirs[:] = [d for d in dirs if d not in {'.git', '.hg', '.svn', '__pycache__'}]
            for name in files:
                path = os.path.abspath(os.path.join(cur, name))
                if path in existing_paths:
                    continue
                if _HTML_RE.search(name):
                    continue
                try:
                    if read_signature(path) is not None:
                        warn(f"{path}: already-packed Arcager resource skipped")
                        continue
                    data = read_bytes(path)
                except OSError as exc:
                    self.missing.add(f"{rel_key(self.root_dir, path)} ({exc})")
                    continue
                key = rel_key(self.root_dir, path)
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
            raise RuntimeError("missing local resources:\n  " + "\n  ".join(sorted(self.missing)))
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
                raw=read_text(p); key=re.sub(r'[^\w\-]','_',Path(url).stem); raw=raw.replace('</script>','<\\/script>')
                return f'<script type="text/csv" data-key="{escape_attr(key)}">\n{raw}\n</script>'
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
function fail(e){var x=e&&e.message?e.message:String(e);window.arcager.error=e instanceof Error?e:new Error(x);if(ER)ER.textContent=x;if(L)L.textContent="Failed to unpack";if(P)P.hidden=false;if(window.console)console.error(e);readyReject(window.arcager.error);}
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

async function main(){
  if(typeof DecompressionStream==='undefined')throw Error('This browser does not provide DecompressionStream.');
  if(!E)throw Error('Arcager v4 container block is missing.');
  var enc=E.getAttribute('data-e')==='1',rawSize=Number(E.getAttribute('data-n')||0),blob=z85d(E.textContent||'',rawSize),key=null;
  if(enc){if(!crypto||!crypto.subtle)throw Error('Encrypted Arcager packages require Web Crypto support.');var s=E.getAttribute('data-s'),iv=E.getAttribute('data-iv'),it=Number(E.getAttribute('data-i')),ck=E.getAttribute('data-ck');while(true){var pw=prompt('Enter Arcager password:');if(pw===null)throw Error('password entry cancelled');try{var k=await keyFromPassword(pw,s,it);var can=await decrypt(k,E.getAttribute('data-civ'),b64d(ck));if(new TextDecoder().decode(can)!=='arcager-ok')throw Error('bad password');key=k;break;}catch(e){if(window.console)console.warn('Arcager: incorrect password');alert('Incorrect password.');}}blob=await decrypt(key,iv,blob);}
  var C=parseContainer(blob),cache=[],need={},nodes=C.logical;
  for(var i=0;i<C.logical.length;i++)need[C.logical[i].physical]=true;
  var streamIds={};for(var i=0;i<C.physical.length;i++)streamIds[C.physical[i].stream]=true;
  var streams={};var ids=Object.keys(streamIds);for(var j=0;j<ids.length;j++){progress(5+85*(j/ids.length));streams[ids[j]]=await streamBytes(C,Number(ids[j]),cache);}progress(95);
  var U=buildURLs(C,streams);window.arcager.images=U.images;window.arcager.csv={};window.arcager.getImage=function(k){var u=U.images[k]||U.images[String(k).toLowerCase()];if(!u)return null;var img=new Image();img.src=u;return img;};
  var rootIdx=-1;for(var i=0;i<nodes.length;i++){if(nodes[i].path==='index.html'){rootIdx=i;break;}}if(rootIdx<0)throw Error('v4 root HTML resource missing');
  var rp=nodes[rootIdx],pp=C.physical[rp.physical],rs=streams[pp.stream],next=C.nextOff[rp.physical];var html=new TextDecoder().decode(rs.subarray(pp.off,next));
  html=U.replaceTokens(html);
  for(var si=0;si<ids.length;si++)streams[ids[si]]=null; // Blob URLs own resource bytes now; release decompressed stream buffers.
  var csvBlocks=[];var re=/<script\b([^>]*?)>([\s\S]*?)<\/script\s*>/gi,m;while((m=re.exec(html))){if(/\btype\s*=\s*(['"])text\/csv\1/i.test(m[1]||'')){var km=(m[1]||'').match(/\bdata-key\s*=\s*(['"])([^'"]+)\1/i),keycsv=km?km[2]:'csv'+csvBlocks.length;csvBlocks.push({key:keycsv,body:m[2].trim()});}}
  for(var c=0;c<csvBlocks.length;c++){var body=csvBlocks[c].body.replace(/\r\n/g,'\n');var lines=body.split('\n'),rows=[];for(var q=0;q<lines.length;q++){var line=lines[q];if(!line){continue;}var delim=line.indexOf('\t')>=0?'\t':',';var arr=[];var cur='',quote=false;for(var z=0;z<line.length;z++){var ch=line[z];if(ch==='"'){if(quote&&line[z+1]==='"'){cur+='"';z++;}else quote=!quote;}else if(ch===delim&&!quote){arr.push(cur);cur='';}else cur+=ch;}arr.push(cur);rows.push(arr);}var hdr=rows.length?rows[0]:[],data=rows.slice(1).map(function(r){var o={};for(var z=0;z<hdr.length;z++)o[hdr[z]]=r[z]===undefined?'':r[z];return o;});window.arcager.csv[csvBlocks[c].key]={rows:rows,data:data};}
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
    return f'''{SIGNATURE}\n<!doctype html><html><head><meta charset="utf-8"><title data-arcager-shell>{escape_text(title)}</title><style id="__arcager_style">body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f7f7f7;color:#222}}#arcager-panel{{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(247,247,247,.96);z-index:2147483647}}#arcager-box{{min-width:280px;max-width:720px;padding:24px 28px;border:1px solid #bbb;border-radius:12px;background:#fff;box-shadow:0 14px 42px rgba(0,0,0,.12)}}#arcager-label{{font-size:16px}}#arcager-bar{{margin-top:14px;height:5px;background:#ddd;border-radius:4px;overflow:hidden}}#arcager-fill{{width:2%;height:100%;background:#555;transition:width .15s linear}}#arcager-error{{margin-top:12px;color:#a33;white-space:pre-wrap}}</style></head><body><div id="__pnl" style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(247,247,247,.96);z-index:2147483647"><div id="arcager-box"><div id="__lbl">Unpacking…</div><div id="arcager-bar"><div id="__fill"></div></div><div id="__err"></div></div></div><script type="application/octet-stream" id="__pk" {attrs_s}>{container_z85}</script>{hidden_block}<script>{runtime}</script></body></html>'''

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
    if not sys.stdin.isatty(): raise RuntimeError(f"output exists: {path} (use --force)")
    ans=input(f"overwrite {path}? [y/N] ").strip().lower()
    return ans in ('y','yes')

# ---------- merge orchestration ----------
def merge_directory(dir_path,opts,out_name,visible_entries):
    root=os.path.abspath(dir_path); index=None
    for name in ('index.html','index.htm','default.html','default.htm'):
        p=os.path.join(root,name)
        if os.path.isfile(p): index=p; break
    if not index: raise RuntimeError(f"no index.html found in {dir_path}")
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
    if g.external:
        warn(f"external resource dependencies detected: {len(g.external)}; output may still require network access")
        if opts.verbose:
            for x in sorted(g.external): print(f"  external: {x}",file=sys.stderr)
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
        raise RuntimeError("incorrect payload password or corrupted ciphertext")


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


def unpack_packed(path,opts,out_name):
    content,attrs,C,key=parse_pack_for_cli(path,opts.password)
    root_i=next((i for i,n in enumerate(C['logical']) if n['path']=='index.html'),None)
    if root_i is None: raise RuntimeError("v4 root HTML resource missing")
    cache={}
    # First build data URLs from stored representations. A second pass rewrites
    # textual resources that themselves contain v4 resource tokens.
    data_urls={}
    raw_resources={}
    for i,n in enumerate(C['logical']):
        if n['flags'] & 8: continue
        d=materialize_logical(C,i,cache); p=C['physical'][n['physical']]
        raw_resources[i]=(d,p['mime'])
        data_urls[i]=logical_to_data_uri(d,p['mime'])
    for _ in range(2):
        for i,(d,mime) in raw_resources.items():
            if not is_textual(mime) and mime!='image/svg+xml':
                continue
            text=d.decode('utf-8',errors='replace')
            def rr(m):
                j=int(m.group(1)); suffix=m.group(2) or ''
                return data_urls.get(j,m.group(0))+suffix
            rewritten=re.sub(r'arcager-res:(\d+)([?#][^\s"\'<>\)\]]*)?',rr,text)
            data_urls[i]=logical_to_data_uri(rewritten.encode('utf-8'),mime)
    html=materialize_logical(C,root_i,cache).decode('utf-8')
    def repl(m):
        idx=int(m.group(1)); suffix=m.group(2) or ''
        return data_urls.get(idx,m.group(0))+suffix
    html=re.sub(r'arcager-res:(\d+)([?#][^\s"\'<>\)\]]*)?',repl,html)
    # Standalone visible bundles may remain as ordinary relative URLs in the
    # stored root. Resolve those references during CLI unpack as well, so the
    # unpacked result remains self-contained.
    by_path={}
    for i,n in enumerate(C['logical']):
        if n['flags'] & 8: continue
        by_path[n['path']]=data_urls[i]
        by_path[n['path'].lstrip('./')]=data_urls[i]
        if Path(n['path']).suffix:
            by_path[str(Path(n['path']).with_suffix(''))]=data_urls[i]
            by_path[Path(n['path']).name]=data_urls[i]
    def resolve_plain(url):
        base=split_ref(url)[0]
        keys=[base,base.lstrip('./'),base.lstrip('/'),urllib.parse.unquote(base)]
        if Path(base).suffix:
            keys.append(str(Path(base).with_suffix('')))
        for k in keys:
            if k in by_path: return by_path[k]
        return None
    def attr_plain(m):
        attr,q,url=m.group(1),m.group(2),m.group(3)
        if is_external(url): return m.group(0)
        u=resolve_plain(url)
        return f'{attr}={q}{u}{split_ref(url)[1]}{q}' if u else m.group(0)
    html=re.sub(r'\b(src|href|poster|xlink:href)\s*=\s*(["\'])([^"\']+)\2',attr_plain,html,flags=re.I)
    def css_plain(m):
        q,url=m.group(1),m.group(2)
        u=resolve_plain(url)
        return f'url({q}{u}{split_ref(url)[1]}{q})' if u and not is_external(url) else m.group(0)
    html=_CSS_URL_RE.sub(css_plain,html)
    write_text(out_name,html)
    return {"inSize":len(content.encode()),"outSize":len(html.encode()),"restored":len(C['logical'])}

# ---------- list/extract ----------
def _matches_type(mime, typ):
    if typ in ('all','media'): return True
    cat=media_category(mime)
    return typ==cat or (typ=='docs' and cat=='docs') or (typ=='text' and (cat=='docs' or is_textual(mime)))


def list_hidden_bundle(path, content, opts):
    hm=re.search(r'<script[^>]*id=[\'"]__hid[\'"][^>]*>([\s\S]*?)</script>',content,re.I)
    if not hm:
        return []
    mat=re.search(r'<script[^>]*id=[\'"]__hid[\'"][^>]*>',content,re.I)
    attrs2=dict((k.lower(),v or v2) for k,v,v2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')',mat.group(0)))
    raw=z85_decode(hm.group(1),int(attrs2.get('data-n','0')))
    try:
        pw=opts.bundle_password or prompt_password('Hidden bundle password: ')
        key=derive_key(pw,attrs2['data-s'],int(attrs2['data-i']))
        if aes_decrypt(key,attrs2['data-civ'],base64.b64decode(attrs2['data-ck'])) != PASSWORD_CHECK:
            raise ValueError
        body=decompress_bytes(aes_decrypt(key,attrs2['data-iv'],raw),int(attrs2.get('data-c','0')))
        head=struct.unpack_from('<8sII',body,0)
        if head[0] != b'ARCAGHID': raise ValueError('invalid hidden bundle header')
        ml=head[2]; meta=json.loads(body[16:16+ml].decode('utf-8'))
        return [(name,n) for name,off,n in meta]
    except Exception as e:
        raise RuntimeError('unable to inspect hidden bundle: incorrect password or corrupted data') from e


def list_packed(path,opts):
    content,attrs,C,key=parse_pack_for_cli(path,opts.password)
    rows=[]
    for i,n in enumerate(C['logical']):
        p=C['physical'][n['physical']]; rows.append((n['path'],p['mime'],media_category(p['mime']),n['priority'],0,bool(p['flags']&1),i,p.get('source_mime',p['mime'])))
    # sizes via offsets without decompressing entire package where stream raw boundaries are available;
    # exact size of individual members is derivable from the metadata structure.
    for i,r in enumerate(rows):
        idx=r[6]; n=C['logical'][idx]; p=C['physical'][n['physical']]; s=C['streams'][p['stream']]
        pi=n['physical']
        sz=C['next_off'][pi]-p['off']
        rows[i]=(r[0],r[1],r[2],r[3],sz,r[5],r[6],r[7])
    types=opts.list_types
    for row in rows:
        if any(_matches_type(row[1],t) for t in types) or 'all' in types:
            conv=("  ("+row[7]+" -> "+row[1]+")") if row[5] and row[7] != row[1] else ""
            print(f"{row[2]:7} {str(row[4]).rjust(9)} {row[1]:32} {row[0]}{conv}")
    if 'csv' in types or 'all' in types:
        try:
            root_i=next((i for i,n in enumerate(C['logical']) if n['path']=='index.html'),None)
            if root_i is not None:
                root_html=materialize_logical(C,root_i,{})
                for blk in scan_csv_blocks(root_html.decode('utf-8',errors='replace')):
                    print(f"{'csv':7} {str(len(blk['body'].encode('utf-8'))).rjust(9)} {'text/csv':32} {blk['key']}")
        except Exception as e:
            if opts.verbose:
                warn(f"unable to inspect embedded CSV: {e}")
    if 'pack' in types or 'all' in types:
        try:
            hidden=list_hidden_bundle(path,content,opts)
            for name,nbytes in hidden:
                print(f"{'pack':7} {str(nbytes).rjust(9)} {'opaque hidden bundle':32} {name}")
        except RuntimeError as e:
            if opts.verbose:
                warn(str(e))
    return rows


def list_unpacked(path,opts):
    html=read_text(path); items=[]
    for m in re.finditer(r'\b(?:src|href|poster|xlink:href)\s*=\s*([\'\"])([^\'\"]+)\1',html,re.I):
        url=m.group(2); items.append((url,_mime_from_url(url)))
    for m in re.finditer(r'\bsrcset\s*=\s*([\'\"])([^\'\"]+)\1',html,re.I):
        for it in m.group(2).split(','):
            toks=it.strip().split();
            if toks: items.append((toks[0],mime_for(toks[0])))
    for blk in scan_csv_blocks(html): items.append((blk['key'],'text/csv'))
    for name,mime in items:
        if any(_matches_type(mime,t) for t in opts.list_types) or 'all' in opts.list_types:
            print(f"{media_category(mime):7} {mime:32} {name}")
    return items


def _mime_from_url(url):
    p=url.split('?',1)[0].split('#',1)[0]; return mime_for(p)


def extraction_name(path,mime):
    p=Path(path)
    ext=_EXT_BY_MIME.get(mime,p.suffix or '.bin')
    if p.suffix.lower()==ext.lower(): return str(p)
    return str(p.with_suffix(ext))


def extract_packed(path,opts,dest):
    content,attrs,C,key=parse_pack_for_cli(path,opts.password)
    os.makedirs(dest,exist_ok=True); cache={}; count=0
    for i,n in enumerate(C['logical']):
        if n['flags']&8 or n['path']=='index.html': continue
        p=C['physical'][n['physical']];
        if not ('all' in opts.extract_types or any(_matches_type(p['mime'],t) for t in opts.extract_types)): continue
        data=materialize_logical(C,i,cache); rel=extraction_name(n['path'],p['mime']); outp=os.path.join(dest,rel.replace('/',os.sep)); os.makedirs(os.path.dirname(outp),exist_ok=True); write_bytes(outp,data); count+=1
    # Hidden bundle metadata remains outside core and is extracted separately below.
    hm=re.search(r'<script[^>]*id=[\'\"]__hid[\'\"][^>]*>([\s\S]*?)</script>',content,re.I)
    if hm and ('all' in opts.extract_types or 'pack' in opts.extract_types):
        mat=re.search(r'<script[^>]*id=[\'\"]__hid[\'\"][^>]*>',content,re.I).group(0)
        attrs2=dict((k.lower(),v or v2) for k,v,v2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')',mat))
        raw=z85_decode(hm.group(1),int(attrs2.get('data-n','0')))
        pw=opts.bundle_password or prompt_password('Hidden bundle password: '); key2=derive_key(pw,attrs2['data-s'],int(attrs2['data-i']))
        try:
            if aes_decrypt(key2,attrs2['data-civ'],base64.b64decode(attrs2['data-ck']))!=PASSWORD_CHECK: raise ValueError
            body=aes_decrypt(key2,attrs2['data-iv'],raw)
            body=decompress_bytes(body,int(attrs2.get('data-c','0')))
        except Exception: raise RuntimeError('incorrect hidden bundle password or corrupted ciphertext')
        head=struct.unpack_from('<8sII',body,0); 
        if head[0]!=b'ARCAGHID': raise RuntimeError('invalid hidden bundle container')
        ml=head[2]; meta=json.loads(body[16:16+ml].decode('utf-8')); payload=body[16+ml:]
        for name,off,n in meta:
            outp=os.path.join(dest,name); os.makedirs(os.path.dirname(outp),exist_ok=True); write_bytes(outp,payload[off:off+n]); count+=1
    # CSV/TSV blocks embedded in the root remain a first-class extracted resource.
    if 'csv' in opts.extract_types or 'all' in opts.extract_types:
        root_i=next((i for i,n in enumerate(C['logical']) if n['path']=='index.html'),None)
        if root_i is not None:
            root_html=materialize_logical(C,root_i,cache).decode('utf-8',errors='replace')
            for blk in scan_csv_blocks(root_html):
                name=safe_extraction_filename(blk['key']+'.csv')
                outp=os.path.join(dest,name); write_text(outp,blk['body']+("\n" if blk['body'] and not blk['body'].endswith("\n") else "")); count+=1
    return count

def extract_unpacked(path, opts, dest):
    html = read_text(path)
    os.makedirs(dest, exist_ok=True)
    base = Path(path).stem
    count = 0
    for i, m in enumerate(_DATA_URI_RE.finditer(html)):
        head, payload = m.group(1), m.group(2)
        mime = head.split(';', 1)[0].lower()
        if not any(_matches_type(mime, t) for t in opts.extract_types):
            continue
        try:
            raw = base64.b64decode(payload.replace("\n", "").replace("\r", ""), validate=False)
        except Exception:
            continue
        ext = _EXT_BY_MIME.get(mime, ".bin")
        outp = os.path.join(dest, f"{base}_datauri_{i:04d}{ext}")
        write_bytes(outp, raw); count += 1
    if 'csv' in opts.extract_types or 'all' in opts.extract_types:
        for blk in scan_csv_blocks(html):
            name = safe_extraction_filename(blk['key'] + '.csv')
            outp = os.path.join(dest, name)
            write_text(outp, blk['body'] + ("\n" if blk['body'] and not blk['body'].endswith("\n") else "")); count += 1
    return count


def safe_extraction_filename(name: str) -> str:
    p = Path(name.replace("\\", "/"))
    parts = [x for x in p.parts if x not in ("", ".", "..")]
    return "/".join(parts) if parts else "item.bin"

# ---------- CLI ----------
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


def parse_cli(argv):
    if any(a in ('-h','--help') for a in argv):
        print(f"arcager {VERSION} — single-file HTML packager\n\nUsage:\n  arcager [options] <input...>\n  arcager -M <dir> [options]\n  arcager -u [options] <packed.html>\n  arcager -l [TYPE,...] <input...>\n  arcager -x [TYPE,...] <input...>\n\nCompression:\n  -c gzip             compatible default compression\n  -c brotli           higher-density optional compression\n  -c gzip lossy       permits representation-changing media optimization\n  -c brotli lossy     permits representation-changing media optimization\n\nOther core options:\n  --strip-metadata    explicitly remove non-essential privacy metadata\n  -m, --minify [lossy]  minify the runtime JS when Terser is available; lossy also minifies HTML payload\n  -E, --encrypt       AES-256-GCM protection for the v4 container\n  -b, --bundle PATH [hidden]  visible or opaque hidden bundle\n  -M, --merge DIR     merge a static-site entry point\n  -l, --list TYPE     list resources\n  -x, --extract TYPE  extract resources\n  -u, --unpack        restore the root HTML\n  -r, --recursive     recursively scan HTML inputs\n  -O, --output-dir DIR / -o PATH / --prefix PREFIX\n  -f, --force         overwrite without prompting\n  -v, --verbose       detailed diagnostics\n\nDefault packing preserves original representations. 'lossy' permits representation changes. Metadata removal is never automatic.")
        raise SystemExit(0)
    if any(a in ('-V','--version') for a in argv):
        print(f"arcager {VERSION}"); raise SystemExit(0)
    o=Opts(); i=0
    while i<len(argv):
        a=argv[i]
        if a in ('-v','--verbose'): o.verbose=True
        elif a in ('-f','--force'): o.force=True
        elif a in ('-r','--recursive'): o.recursive=True
        elif a in ('-u','--unpack'): o.unpack=True
        elif a in ('-M','--merge'):
            i+=1; o.merge=argv[i]
        elif a in ('-o','--output'):
            i+=1; o.output=argv[i]
        elif a in ('-O','--output-dir'):
            i+=1; o.output_dir=argv[i]
        elif a=='--prefix': i+=1; o.prefix=argv[i]
        elif a in ('-E','--encrypt'): o.encrypt=True
        elif a=='--password': i+=1; o.password=argv[i]
        elif a=='--bundle-password': i+=1; o.bundle_password=argv[i]
        elif a=='--strip-metadata': o.strip_metadata=True
        elif a=='--base-href': i+=1; o.base_href=argv[i]
        elif a=='--ignore-uris': i+=1; o.ignore_uris=[x.strip() for x in argv[i].split(',') if x.strip()]
        elif a in ('-m','--minify'):
            o.minify=True
            if i+1<len(argv) and argv[i+1].lower()=='lossy': i+=1; o.minify_lossy=True
        elif a in ('-c','--compression'):
            i+=1
            if i>=len(argv): raise RuntimeError('-c requires gzip or brotli')
            algo=argv[i].lower()
            if algo not in ('gzip','brotli'): raise RuntimeError('compression must be gzip or brotli')
            o.algo=1 if algo=='gzip' else 2; o.compression=algo
            if i+1<len(argv) and argv[i+1].lower()=='lossy': i+=1; o.lossy=True
        elif a in ('-b','--bundle'):
            i+=1
            if i>=len(argv): raise RuntimeError('-b needs a path')
            path=argv[i]; hidden=False
            if i+1<len(argv) and argv[i+1].lower()=='hidden': i+=1; hidden=True
            o.bundles.append((path,hidden))
        elif a in ('-l','--list'):
            o.list_mode=True
            if i+1<len(argv) and not argv[i+1].startswith('-') and _valid_type_list(argv[i+1]): i+=1; o.list_types=[x.strip() for x in argv[i].split(',') if x.strip()]
        elif a in ('-x','--extract'):
            o.extract_mode=True
            if i+1<len(argv) and not argv[i+1].startswith('-') and _valid_type_list(argv[i+1]): i+=1; o.extract_types=[x.strip() for x in argv[i].split(',') if x.strip()]
        elif a.startswith('-'): raise RuntimeError('unknown option: '+a)
        else: o.inputs.append(a)
        i+=1
    if o.algo==2 and not o.compression: o.compression='brotli'
    return o


def usage_status(result,i,total):
    if result.get('status')=='ok':
        arrow='↓' if result['outSize']<result['inSize'] else '↑'; mark='✓' if arrow=='↓' else '!'
        out(f"  [{i}/{total}] {C['green'] if mark=='✓' else C['yellow']}{mark}{C['reset']} {os.path.basename(result['out']):34} {size_str(result['inSize']).rjust(10)} {arrow} {size_str(result['outSize']).rjust(10)}")
    elif result.get('status','').startswith('skip'):
        out(f"  [{i}/{total}] {C['yellow']}⚠{C['reset']} {os.path.basename(result.get('file','')):34} {result.get('status')}")
    else:
        out(f"  [{i}/{total}] {C['red']}✗{C['reset']} {os.path.basename(result.get('file','')):34} {result.get('error','')}")


def build_single_file(file,opts,prompter,visible_entries):
    sig=read_signature(file)
    if sig is not None:
        warn(f"{file}: already-packed Arcager file (v{sig}) skipped")
        return {'file':file,'status':'skip-already-packed'}
    raw=read_bytes(file); html=raw.decode('utf-8',errors='replace')
    stem=re.sub(r'\.html?$','',os.path.basename(file),flags=re.I); out_name=output_path(opts,(opts.prefix or _DEFAULT_PACK_PREFIX)+stem+'.html',os.path.dirname(file))
    if os.path.abspath(out_name)==os.path.abspath(file): raise RuntimeError('output would overwrite input')
    if not confirm_overwrite(out_name,opts.force): return {'file':file,'out':out_name,'status':'skip-no-overwrite'}
    hidden=[x for x in opts.bundles if x[1]]; visible=[x for x in opts.bundles if not x[1]]
    entries=collect_visible_bundles([p for p,_ in visible],lambda *a:vlog(opts,*a)) if visible else []
    shell,b,conv=build_packed(opts,html,len(raw),os.path.basename(out_name),entries,file)
    # Attach hidden bundle after shell has been built, preserving its own password boundary.
    if hidden:
        if not opts.bundle_password: opts.bundle_password=prompt_password('Hidden bundle password: ',confirm=True)
        hentries=collect_hidden_bundles([p for p,_ in hidden]); hi=hidden_envelope(hentries,opts.bundle_password,opts.algo)
        block=f'<script type="text/plain" id="__hid" '+" ".join(f'{k}="{escape_attr(str(v))}"' for k,v in hi.items() if k!='data')+'>'+z85_script_safe(hi['data'])+'</script>'
        shell=shell.replace('</body></html>',block+'</body></html>')
    write_text(out_name,shell)
    return {'file':file,'out':out_name,'status':'ok','inSize':len(raw),'outSize':len(shell.encode()),'stats':b.stats,'conversion':conv}


def build_merge(dir_path,opts,prompter):
    root=os.path.abspath(dir_path); stem=os.path.basename(root.rstrip(os.sep)); out_name=output_path(opts,(opts.prefix or _DEFAULT_PACK_PREFIX)+stem+'.html',os.path.dirname(root))
    if not confirm_overwrite(out_name,opts.force): return {'file':dir_path,'out':out_name,'status':'skip-no-overwrite'}
    visible=[p for p,h in opts.bundles if not h]; entries=collect_visible_bundles(visible,lambda *a:vlog(opts,*a)) if visible else []
    shell,b,g=merge_directory(dir_path,opts,out_name,entries)
    hidden=[p for p,h in opts.bundles if h]
    if hidden:
        if not opts.bundle_password: opts.bundle_password=prompt_password('Hidden bundle password: ',confirm=True)
        hi=hidden_envelope(collect_hidden_bundles(hidden),opts.bundle_password,opts.algo)
        block=f'<script type="text/plain" id="__hid" '+" ".join(f'{k}="{escape_attr(str(v))}"' for k,v in hi.items() if k!='data')+'>'+z85_script_safe(hi['data'])+'</script>'
        shell=shell.replace('</body></html>',block+'</body></html>')
    write_text(out_name,shell)
    source=sum(len(n.data) for n in g.nodes if n.path and os.path.isfile(n.path))
    return {'file':dir_path,'out':out_name,'status':'ok','inSize':source or os.path.getsize(os.path.join(root,'index.html')),'outSize':len(shell.encode()),'stats':b.stats,'merge':g}


def main(argv=None):
    opts=parse_cli(sys.argv[1:] if argv is None else argv)
    modes=sum(bool(x) for x in (opts.list_mode,opts.extract_mode,opts.unpack,opts.merge))
    if modes>1: raise RuntimeError('list, extract, unpack and merge modes are mutually exclusive')
    if opts.merge and opts.inputs: raise RuntimeError('--merge takes a directory and no positional inputs')
    if (opts.list_mode or opts.extract_mode) and not opts.inputs: raise RuntimeError('no input files')
    if not opts.inputs and not opts.merge: raise RuntimeError('no input files')
    if opts.output and len(opts.inputs)>1: raise RuntimeError('-o is only allowed with one input')
    if opts.algo==2 and not HAS_BROTLI and not opts.unpack and not opts.list_mode and not opts.extract_mode: raise RuntimeError('Brotli requires the optional brotli package; use the same Python interpreter that runs Arcager: ' + sys.executable)
    if opts.encrypt:
        status=cryptography_status()
        if not status.get('available'):
            raise RuntimeError(status['error'])
    if opts.minify and shutil.which("terser") is None: warn('--minify requested but Terser was not found; runtime JavaScript is left unchanged. Install Terser to enable JS minification.')
    if opts.minify_lossy: warn('--minify lossy modifies the HTML payload and is not byte-preserving')
    if opts.lossy: warn('lossy mode permits representation-changing media optimization; it does not imply metadata stripping')
    if opts.strip_metadata: warn('--strip-metadata explicitly permits non-essential metadata cleanup; rendering-critical metadata is preserved')

    if opts.list_mode:
        for f in opts.inputs:
            sig=read_signature(f)
            if sig==4: list_packed(f,opts)
            elif sig==3: raise RuntimeError(f'{f}: v3 package detected; 4.0 is v4-only')
            else: list_unpacked(f,opts)
        return
    if opts.extract_mode:
        dest=opts.output_dir or './extracted'; os.makedirs(dest,exist_ok=True)
        total=0
        for f in opts.inputs:
            sig=read_signature(f)
            if sig==4: total+=extract_packed(f,opts,dest)
            elif sig==3: raise RuntimeError(f'{f}: v3 package detected; 4.0 is v4-only')
            else: total+=extract_unpacked(f,opts,dest)
        out(f"Extracted {total} item(s) to {dest}"); return

    files=[]
    if opts.merge: files=[opts.merge]
    else:
        for p in opts.inputs:
            if os.path.isdir(p):
                fs,_=scan_dir(p,opts.recursive); files.extend(fs)
            elif os.path.isfile(p): files.append(p)
            else: warn(f'no such input: {p}')
    # deterministic de-dup of batch inputs
    seen=set(); files=[f for f in files if not (os.path.realpath(f) in seen or seen.add(os.path.realpath(f)))]
    if not files: raise RuntimeError('no input files to process')
    results=[]; t=time.time()
    for i,f in enumerate(files,1):
        try:
            if opts.merge: r=build_merge(f,opts,None)
            elif opts.unpack:
                sig=read_signature(f)
                if sig!=4: r={'file':f,'status':'skip-not-packed' if sig is None else 'skip-unknown-version'}
                else:
                    stem=os.path.basename(f); core=stem[len(opts.prefix):] if opts.prefix and stem.startswith(opts.prefix) else (stem[len(_DEFAULT_PACK_PREFIX):] if stem.startswith(_DEFAULT_PACK_PREFIX) else stem)
                    out_name=output_path(opts,_DEFAULT_UNPACK_PREFIX+core,os.path.dirname(f));
                    if not confirm_overwrite(out_name,opts.force): r={'file':f,'out':out_name,'status':'skip-no-overwrite'}
                    else:
                        rr=unpack_packed(f,opts,out_name); r={'file':f,'out':out_name,'status':'ok',**rr}
            else: r=build_single_file(f,opts,None,[])
        except Exception as e:
            r={'file':f,'status':'error','error':str(e)}
        results.append(r); usage_status(r,i,len(files))
    ok=[r for r in results if r['status']=='ok']; failed=[r for r in results if r['status']=='error']; skipped=[r for r in results if r['status'].startswith('skip')]
    out(''); out(f"Summary: {len(ok)} processed · {len(skipped)} skipped · {len(failed)} failed · {time.time()-t:.2f}s")
    for r in ok:
        if r.get('stats'):
            s=r['stats']; out(f"  logical {s['logical'] or '-'} · physical {s['physical'] or '-'} · streams {s['streams']} · stored {size_str(s['stored_bytes'])} from {size_str(s['raw_bytes'])} · duplicates {s['duplicate_count']}")
            if opts.verbose and s.get('solid_attempts'):
                for a in s['solid_attempts']:
                    algo_name = {1: 'GZIP', 2: 'Brotli'}.get(a['algorithm'], 'stored')
                    verdict = 'accepted' if a['accepted'] else ('rejected' if a['algorithm'] else 'not attempted')
                    saved = a['saved_bytes']
                    delta_text = size_str(saved) if saved >= 0 else f"-{size_str(-saved)}"
                    reason = a.get('decision_reason')
                    suffix = f" ({reason})" if reason and reason not in ('accepted', 'stored: candidate not smaller') else ''
                    out(f"  solid: {a['mime']} · {algo_name} {size_str(a['raw_bytes'])} → {size_str(a['candidate_bytes'])} · {delta_text} · {verdict}{suffix}")
        if r.get('merge') and r['merge'].external: out(f"  external resource dependencies: {len(r['merge'].external)}")
        if r.get('conversion') and opts.verbose:
            for row in r['conversion']: print(f"  conversion: {row}",file=sys.stderr)
    if failed:
        for r in failed: err(f"{r['file']}: {r['error']}")
        raise SystemExit(2)
    if skipped and not ok: raise SystemExit(3)


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: raise SystemExit(130)
    except SystemExit: raise
    except Exception as e:
        err(str(e)); raise SystemExit(1)
