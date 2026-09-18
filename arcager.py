#!/usr/bin/env python3
"""
arcager — single-file HTML packer (Python port of arcager.js).

Packs an HTML file (or, with --merge, an entire directory) into a single
self-contained HTML document that self-decompresses in the browser,
optionally with AES-256-GCM encryption.

Optional dependencies (only required for specific flags):
    cryptography  — for --encrypt / encrypted --unpack / --extract
    brotli        — for --brotli
    terser        — for --minify (invoked via PATH; npm i -g terser)

Modes:
    pack      pack an HTML file into packed_<name>.html
    -u        unpack a packed file back to HTML
    -M        merge a whole directory into one file
    -l        list media inside a packed or unpacked HTML file
    -x        extract media or bundles from a packed or unpacked file

New in 3.2.0:
    - CSV/TSV inlining via <link rel="csv" href="data.csv"> during --merge.
      At runtime: window.arcager.csv['key'].rows / .data
    - -l csv and -x csv scan and extract inlined CSV blocks.
    - -M is a short option for --merge.
    - window.arcager is always defined after load (empty when no
      bundles or CSV are present).

@Author: Bert Coder
@Update: Nov, 18, 2026
@Note: Alpha, pre-repository upload
"""

import base64
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
import subprocess
import sys
import tarfile
import time
import urllib.parse
import zipfile

# ---------------------------------------------------------------------------
# Optional third-party deps
# ---------------------------------------------------------------------------
try:
    import brotli as _brotli
    HAS_BROTLI = True
except ImportError:
    _brotli = None
    HAS_BROTLI = False


# ---------------------------------------------------------------------------
# Windows ANSI enablement
# ---------------------------------------------------------------------------
def _enable_windows_ansi() -> bool:
    if os.name != 'nt':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VT = 0x0004
        for hid in (-11, -12):
            h = kernel32.GetStdHandle(hid)
            if h in (0, -1, None):
                continue
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)) != 0:
                kernel32.SetConsoleMode(h, mode.value | ENABLE_VT)
        return True
    except Exception:
        return False


_VT_OK = _enable_windows_ansi()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VERSION   = '3.2.0'
SIGNATURE = '<!--arcager:3-->'
SIG_RE    = re.compile(r'^<!--arcager:(\d+)-->')

PBKDF2_ITERATIONS = 600000
PBKDF2_KEYLEN     = 32
SALT_LEN          = 16
IV_LEN            = 12
GCM_TAG_LEN       = 16
PASSWORD_CHECK    = b'arcager-ok'

Z85A = ("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        ".-:+=^!/*?&<>()[]{}@%$#")
Z85M = [-1] * 128
for _i, _ch in enumerate(Z85A):
    Z85M[ord(_ch)] = _i

_ALREADY_COMPRESSED_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.ico',
    '.mp3', '.ogg', '.oga', '.m4a', '.aac', '.flac', '.opus',
    '.mp4', '.m4v', '.webm', '.ogv', '.mov',
    '.zip', '.gz', '.tgz', '.bz2', '.tbz2', '.xz', '.txz',
    '.7z', '.rar', '.zst', '.lz4', '.br',
    '.woff', '.woff2', '.pdf',
    '.docx', '.xlsx', '.pptx', '.epub', '.jar',
}


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
_USE_COLOR = (
    _VT_OK
    and sys.stdout.isatty()
    and os.environ.get('NO_COLOR') is None
)
if _USE_COLOR:
    C = dict(reset='\x1b[0m', dim='\x1b[2m', bold='\x1b[1m',
             red='\x1b[31m', green='\x1b[32m', yellow='\x1b[33m',
             blue='\x1b[34m', cyan='\x1b[36m', gray='\x1b[90m')
else:
    C = {k: '' for k in ('reset', 'dim', 'bold', 'red', 'green',
                         'yellow', 'blue', 'cyan', 'gray')}


def out(*a):
    sys.stdout.write(' '.join(str(x) for x in a) + '\n')


def warn(*a):
    sys.stderr.write(f"{C['yellow']}warning:{C['reset']} " +
                     ' '.join(str(x) for x in a) + '\n')


def err(*a):
    sys.stderr.write(f"{C['red']}error:{C['reset']}   " +
                     ' '.join(str(x) for x in a) + '\n')


def die(msg):
    err(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Z85
# ---------------------------------------------------------------------------
def z85_encode(buf: bytes) -> str:
    pad = (4 - (len(buf) & 3)) & 3
    data = buf + b'\x00' * pad if pad else buf
    parts = []
    for g in range(len(data) >> 2):
        i = g << 2
        v = ((data[i] << 24) | (data[i + 1] << 16)
             | (data[i + 2] << 8) | data[i + 3])
        cc = [''] * 5
        for j in range(4, -1, -1):
            cc[j] = Z85A[v % 85]
            v //= 85
        parts.append(''.join(cc))
    return ''.join(parts)


def z85_decode(s: str, size: int) -> bytes:
    s = re.sub(r'\s+', '', s)
    outb = bytearray(size)
    o = 0
    i = 0
    while i + 5 <= len(s) and o < size:
        v = 0
        for j in range(5):
            idx = ord(s[i + j])
            d = Z85M[idx] if 0 <= idx < 128 else -1
            if d < 0:
                raise ValueError(f'bad z85 char at offset {i}')
            v = v * 85 + d
        if o < size: outb[o] = (v >> 24) & 0xFF; o += 1
        if o < size: outb[o] = (v >> 16) & 0xFF; o += 1
        if o < size: outb[o] = (v >> 8)  & 0xFF; o += 1
        if o < size: outb[o] = v         & 0xFF; o += 1
        i += 5
    return bytes(outb)


def z85_encode_script_safe(buf: bytes) -> str:
    """Z85-encode `buf` and prevent literal '</script' in the output."""
    s = z85_encode(buf)
    low = s.lower()
    if '</script' not in low:
        return s
    parts = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '<' and low[i:i + 8] == '</script':
            parts.append('<\n')
            i += 1
        else:
            parts.append(s[i])
            i += 1
    return ''.join(parts)


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------
def random_b64(n: int) -> str:
    return base64.b64encode(secrets.token_bytes(n)).decode('ascii')


def derive_key(password: str, salt_b64: str, iterations: int) -> bytes:
    salt = base64.b64decode(salt_b64)
    return hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt, iterations,
        dklen=PBKDF2_KEYLEN)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError:
        die("this operation requires the 'cryptography' package "
            "(pip install cryptography)")


def aes_gcm_encrypt(key: bytes, iv_b64: str, plaintext: bytes) -> bytes:
    AESGCM = _aesgcm()
    iv = base64.b64decode(iv_b64)
    return AESGCM(key).encrypt(iv, plaintext, None)


def aes_gcm_decrypt(key: bytes, iv_b64: str, ciphertext: bytes) -> bytes:
    AESGCM = _aesgcm()
    iv = base64.b64decode(iv_b64)
    if len(ciphertext) < GCM_TAG_LEN:
        raise ValueError('ciphertext too short')
    return AESGCM(key).decrypt(iv, ciphertext, None)


# ---------------------------------------------------------------------------
# Password prompts
# ---------------------------------------------------------------------------
def prompt_hidden_password(label: str) -> str:
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.rstrip('\n') if line else ''
    try:
        return getpass.getpass(label)
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write('\n')
        sys.exit(130)


def prompt_new_password(label: str = 'Enter password: ') -> str:
    while True:
        pw1 = prompt_hidden_password(label)
        if not pw1:
            warn('password must not be empty')
            continue
        if len(pw1) < 8:
            warn('note: password is shorter than 8 characters')
        pw2 = prompt_hidden_password('Confirm password: ')
        if pw1 != pw2:
            warn('passwords do not match, try again')
            continue
        return pw1


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def size_str(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    if n < 1024 * 1024 * 1024:
        return f'{n / 1024 / 1024:.2f} MB'
    return f'{n / 1024 / 1024 / 1024:.2f} GB'


def pct(a: int, b: int) -> str:
    if b == 0:
        return '  0.0'
    return f'{(a / b) * 100:5.1f}'


def escape_attr(s) -> str:
    return (str(s).replace('&', '&amp;').replace('"', '&quot;')
            .replace('<', '&lt;').replace('>', '&gt;'))


def escape_text(s) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def read_header(path: str, n: int = 512) -> str:
    with open(path, 'rb') as f:
        return f.read(n).decode('utf-8', errors='replace')


def read_signature(path: str):
    m = SIG_RE.match(read_header(path))
    return int(m.group(1)) if m else None


def stat_or_none(p: str):
    try:
        return os.stat(p)
    except OSError:
        return None


def b64_decode_lenient(s: str) -> bytes:
    pad = (-len(s)) % 4
    if pad:
        s += '=' * pad
    return base64.b64decode(s)


def read_text_file(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='replace', newline='') as f:
        return f.read()


def write_text_file(path: str, text: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Compression helpers
# ---------------------------------------------------------------------------
def maybe_compress(data: bytes, algo: str):
    """Return (payload, was_compressed).  Uses raw bytes when compression
    does not shrink the payload."""
    if not data:
        return data, 0
    if algo == 'brotli':
        if not HAS_BROTLI:
            raise RuntimeError('brotli required (pip install brotli)')
        compressed = _brotli.compress(data, quality=11,
                                      mode=_brotli.MODE_GENERIC, lgwin=24)
    else:
        compressed = gzip.compress(data, compresslevel=9, mtime=0)
    if len(compressed) >= len(data):
        return data, 0
    return compressed, 1


def maybe_decompress(data: bytes, algo: str, compressed: int) -> bytes:
    if not compressed:
        return data
    if algo == 'brotli':
        if not HAS_BROTLI:
            raise RuntimeError('brotli support missing')
        return _brotli.decompress(data)
    return gzip.decompress(data)


def _all_entries_precompressed(entries) -> bool:
    """True when every entry looks like it is already compressed."""
    if not entries:
        return False
    for e in entries:
        key = e.get('key') or e.get('src') or ''
        ext = os.path.splitext(key)[1].lower()
        if ext not in _ALREADY_COMPRESSED_EXTS:
            return False
    return True


# ---------------------------------------------------------------------------
# HTML minification
# ---------------------------------------------------------------------------
_PROTECT_RE = re.compile(
    r'<(pre|textarea|script|style)\b[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r'<!--(?!\!|\[if).*?-->', re.DOTALL)
_WS_RE      = re.compile(r'>\s+<')
_SOURCEMAP_JS_RE  = re.compile(
    r'^[ \t]*//[#@]\s*sourceMappingURL=[^\r\n]*\r?\n?', re.MULTILINE)
_SOURCEMAP_CSS_RE = re.compile(
    r'^[ \t]*/\*[#@]\s*sourceMappingURL=[^*]*\*/\r?\n?', re.MULTILINE)


def minify_html_lossy(html: str):
    """Lossy HTML minification: strip comments, collapse inter-tag
    whitespace, remove sourceMappingURL comments.  Only called when
    the user passes `-m lossy`.

    Returns (html, comments_stripped, whitespace_collapsed, sourcemaps_stripped).
    """
    segments = []
    last = 0
    for m in _PROTECT_RE.finditer(html):
        if m.start() > last:
            segments.append((False, html[last:m.start()]))
        segments.append((True, m.group(0)))
        last = m.end()
    if last < len(html):
        segments.append((False, html[last:]))

    stripped = 0
    collapsed = 0
    out_segs = []
    for keep, text in segments:
        if keep:
            out_segs.append(text)
            continue
        before = len(text)
        t = _COMMENT_RE.sub('', text)
        stripped += before - len(t)
        before = len(t)
        t = _WS_RE.sub('><', t)
        collapsed += before - len(t)
        out_segs.append(t)

    result = ''.join(out_segs)
    before = len(result)
    result = _SOURCEMAP_JS_RE.sub('', result)
    result = _SOURCEMAP_CSS_RE.sub('', result)
    sm = before - len(result)

    return result, stripped, collapsed, sm


# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------
_HTML_EXT_RE = re.compile(r'\.html?$', re.IGNORECASE)


def scan_dir(root: str, max_depth: int):
    files = []
    skipped = []

    def walk(d, depth):
        try:
            names = os.listdir(d)
        except OSError:
            return
        for name in names:
            p = os.path.join(d, name)
            try:
                if os.path.isdir(p):
                    if depth < max_depth:
                        walk(p, depth + 1)
                    else:
                        skipped.append(p)
                elif os.path.isfile(p) and _HTML_EXT_RE.search(name):
                    files.append(p)
            except OSError:
                continue

    walk(root, 0)
    return files, skipped


# ---------------------------------------------------------------------------
# Media tables
# ---------------------------------------------------------------------------
_BUNDLE_EXTS = {
    # Images
    '.png':  'image/png', '.jpg':  'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.gif': 'image/gif', '.svg': 'image/svg+xml',
    '.bmp':  'image/bmp',  '.ico': 'image/x-icon', '.avif': 'image/avif',
    # Fonts
    '.woff':  'font/woff', '.woff2': 'font/woff2',
    '.ttf':   'font/ttf',  '.otf':   'font/otf',
    '.eot':   'application/vnd.ms-fontobject',
    # Audio
    '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.oga': 'audio/ogg',
    '.wav': 'audio/wav',  '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    '.flac': 'audio/flac', '.opus': 'audio/opus',
    # Video
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm',
    '.ogv': 'video/ogg', '.mov': 'video/quicktime',
    # Documents
    '.html': 'text/html', '.htm': 'text/html',
    '.xhtml': 'application/xhtml+xml',
    '.pdf': 'application/pdf', '.eps': 'application/postscript',
    '.txt': 'text/plain', '.md': 'text/markdown',
    '.markdown': 'text/markdown',
    # Data
    '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
}

_ARCHIVE_EXTS = ('.zip', '.tar', '.tar.gz', '.tgz', '.tbz2', '.txz',
                 '.tar.bz2', '.tar.xz')

_SAFE_KEY_RE = re.compile(r'^[\w\-./ @+]+$', re.UNICODE)

_BUNDLE_MAX_TOTAL = 128 * 1024 * 1024
_HIDDEN_MAX_TOTAL = 512 * 1024 * 1024

# Types accepted by -l / -x.
_LIST_TYPES = ('all', 'images', 'videos', 'audio', 'fonts', 'docs',
               'media', 'pack', 'csv')

_EXT_BY_MIME = {
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
    'image/gif': '.gif', 'image/svg+xml': '.svg', 'image/bmp': '.bmp',
    'image/x-icon': '.ico', 'image/avif': '.avif',
    'font/woff': '.woff', 'font/woff2': '.woff2',
    'font/ttf': '.ttf', 'font/otf': '.otf',
    'application/vnd.ms-fontobject': '.eot',
    'audio/mpeg': '.mp3', 'audio/ogg': '.ogg', 'audio/wav': '.wav',
    'audio/mp4': '.m4a', 'audio/aac': '.aac', 'audio/flac': '.flac',
    'audio/opus': '.opus',
    'video/mp4': '.mp4', 'video/webm': '.webm', 'video/ogg': '.ogv',
    'video/quicktime': '.mov',
    'text/html': '.html', 'application/xhtml+xml': '.xhtml',
    'application/pdf': '.pdf', 'application/postscript': '.eps',
    'text/plain': '.txt', 'text/markdown': '.md',
    'text/csv': '.csv', 'text/tab-separated-values': '.tsv',
}


def media_category(mime):
    if not mime:
        return 'other'
    m = mime.lower().split(';', 1)[0].strip()
    if m == 'text/csv' or m == 'text/tab-separated-values':
        return 'csv'
    if m.startswith('image/'):
        return 'images'
    if m.startswith('video/'):
        return 'videos'
    if m.startswith('audio/'):
        return 'audio'
    if (m.startswith('font/')
            or m.startswith('application/font')
            or m.startswith('application/x-font')
            or m == 'application/vnd.ms-fontobject'):
        return 'fonts'
    if m in ('application/pdf', 'application/postscript',
             'text/plain', 'text/markdown', 'text/html',
             'application/xhtml+xml'):
        return 'docs'
    return 'other'


def _mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BUNDLE_EXTS:
        return _BUNDLE_EXTS[ext]
    mt, _ = mimetypes.guess_type(path)
    return mt or 'application/octet-stream'


def _mime_from_url(url):
    p = url.split('?', 1)[0].split('#', 1)[0]
    return _mime_for(p)


def _parse_type_list(value):
    """Return a list of type names if every comma-separated token is a
    known type, else None."""
    parts = [p.strip() for p in value.split(',') if p.strip()]
    if not parts:
        return None
    for p in parts:
        if p not in _LIST_TYPES:
            return None
    return parts


def _item_matches_types(item, types):
    """True when `item` (with 'source' and 'category' keys) matches the
    requested list of type names."""
    if not types or 'all' in types:
        return True
    if item['source'] in types:
        return True
    if item['category'] in types:
        return True
    return False


# ---------------------------------------------------------------------------
# CSV block scanning
# ---------------------------------------------------------------------------
_SCRIPT_TAG_RE = re.compile(r'<script\b([^>]*?)>([\s\S]*?)</script\s*>',
                            re.IGNORECASE)
_CSV_TYPE_RE   = re.compile(r'\btype\s*=\s*([\'"])text/csv\1',
                            re.IGNORECASE)
_CSV_KEY_RE    = re.compile(r'\bdata-key\s*=\s*([\'"])([^\'"]+)\1',
                            re.IGNORECASE)


def _scan_csv_blocks(html):
    """Find all <script type="text/csv"> blocks.

    Returns a list of {'key': str, 'body': str}.
    """
    found = []
    for m in _SCRIPT_TAG_RE.finditer(html):
        attrs = m.group(1) or ''
        if not _CSV_TYPE_RE.search(attrs):
            continue
        body = m.group(2)
        km = _CSV_KEY_RE.search(attrs)
        key = km.group(2) if km else f'csv{len(found)}'
        found.append({'key': key, 'body': body.strip('\r\n')})
    return found


# ---------------------------------------------------------------------------
# Bundle collection (visible and hidden)
# ---------------------------------------------------------------------------
def _validate_key(key: str, src: str):
    if not key:
        raise RuntimeError(f'bundle has empty key: {src}')
    if not _SAFE_KEY_RE.match(key):
        bad = sorted(set(ch for ch in key if not _SAFE_KEY_RE.match(ch)))
        raise RuntimeError(
            f'bundle key {key!r} (from {src}) contains unsafe '
            f'character(s) {bad!r}; rename the file so the key only '
            f'uses letters, digits, _, -, ., /, space, @, +')
    if key.endswith(('/', '.')):
        raise RuntimeError(
            f'bundle key {key!r} (from {src}) ends with a slash or dot')
    if '..' in key.split('/'):
        raise RuntimeError(
            f'bundle key {key!r} (from {src}) contains a ".." segment')


def _read_bytes(p: str) -> bytes:
    with open(p, 'rb') as f:
        return f.read()


def _walk_bundle_dir(root: str, root_name: str, out: list, vlog):
    for cur, _dirs, files in os.walk(root):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            mime = _BUNDLE_EXTS.get(ext)
            if not mime:
                continue
            abs_path = os.path.join(cur, f)
            rel = os.path.relpath(abs_path, root).replace(os.sep, '/')
            key = os.path.splitext(rel)[0]
            _validate_key(key, abs_path)
            data = _read_bytes(abs_path)
            alias = f'{root_name}/{key}' if root_name else None
            out.append({
                'key': key, 'alias': alias, 'data': data,
                'mime': mime, 'src': abs_path,
            })
            vlog(f'bundle: {key} ({mime}, {size_str(len(data))})')


def _read_bundle_archive(path: str, out: list, vlog):
    lower = path.lower()
    if lower.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith('/') or '__MACOSX' in name.split('/'):
                    continue
                if os.path.basename(name).startswith('.'):
                    continue
                info = z.getinfo(name)
                if info.flag_bits & 0x1:
                    raise RuntimeError(
                        f'zip entry {name!r} in {path} is encrypted.  '
                        f'Arcager cannot decrypt zip archives; '
                        f'either decrypt it first, or bundle the '
                        f'archive as-is with: -b {path} hidden')
                ext = os.path.splitext(name)[1].lower()
                mime = _BUNDLE_EXTS.get(ext)
                if not mime:
                    continue
                key = os.path.splitext(name.replace('\\', '/'))[0]
                _validate_key(key, f'{path}:{name}')
                data = z.read(name)
                out.append({
                    'key': key, 'alias': None, 'data': data,
                    'mime': mime, 'src': f'{path}:{name}',
                })
                vlog(f'bundle: {key} ({mime}, {size_str(len(data))})')
    else:
        with tarfile.open(path, 'r:*') as t:
            for m in t.getmembers():
                if not m.isfile():
                    continue
                if '__MACOSX' in m.name.split('/'):
                    continue
                if os.path.basename(m.name).startswith('.'):
                    continue
                ext = os.path.splitext(m.name)[1].lower()
                mime = _BUNDLE_EXTS.get(ext)
                if not mime:
                    continue
                key = os.path.splitext(m.name.replace('\\', '/'))[0]
                _validate_key(key, f'{path}:{m.name}')
                fh = t.extractfile(m)
                if fh is None:
                    continue
                data = fh.read()
                out.append({
                    'key': key, 'alias': None, 'data': data,
                    'mime': mime, 'src': f'{path}:{m.name}',
                })
                vlog(f'bundle: {key} ({mime}, {size_str(len(data))})')


def collect_visible_bundles(paths, vlog):
    out_files = []
    for p in paths:
        abs_p = os.path.abspath(p)
        if not os.path.exists(abs_p):
            raise RuntimeError(f'bundle not found: {p}')
        if os.path.isdir(abs_p):
            root_name = os.path.basename(abs_p.rstrip(os.sep))
            _walk_bundle_dir(abs_p, root_name, out_files, vlog)
        elif os.path.isfile(abs_p):
            lower = abs_p.lower()
            if any(lower.endswith(ext) for ext in _ARCHIVE_EXTS):
                _read_bundle_archive(abs_p, out_files, vlog)
            else:
                ext = os.path.splitext(abs_p)[1].lower()
                mime = _BUNDLE_EXTS.get(ext)
                if not mime:
                    raise RuntimeError(
                        f'unsupported visible bundle format: {p}\n'
                        f'  (supported: {", ".join(sorted(_BUNDLE_EXTS))}, '
                        f'plus .zip/.tar/.tar.gz/.tgz/.tbz2/.txz)')
                key = os.path.splitext(os.path.basename(abs_p))[0]
                _validate_key(key, abs_p)
                data = _read_bytes(abs_p)
                out_files.append({
                    'key': key, 'alias': None, 'data': data,
                    'mime': mime, 'src': abs_p,
                })
                vlog(f'bundle: {key} ({mime}, {size_str(len(data))})')
    return out_files


def build_visible_bundle_blob(entries, vlog):
    by_key = {}
    for e in entries:
        k = e['key']
        if k in by_key:
            raise RuntimeError(
                f'duplicate bundle key {k!r}: '
                f'{by_key[k]["src"]} and {e["src"]}')
        by_key[k] = e

    keys = sorted(by_key.keys())
    parts = []
    meta = []
    off = 0
    for k in keys:
        e = by_key[k]
        meta.append({'k': k, 'o': off, 'n': len(e['data']), 't': e['mime']})
        parts.append(e['data'])
        off += len(e['data'])

    aliases = {}
    ambiguous = set()
    for e in entries:
        a = e.get('alias')
        if not a or a in by_key or a in ambiguous:
            continue
        if a in aliases and aliases[a] != e['key']:
            aliases.pop(a, None)
            ambiguous.add(a)
            continue
        aliases[a] = e['key']

    total = sum(len(p) for p in parts)
    if total > _BUNDLE_MAX_TOTAL:
        raise RuntimeError(
            f'bundles total {size_str(total)} exceeds '
            f'{size_str(_BUNDLE_MAX_TOTAL)} limit')
    return b''.join(parts), meta, aliases


# ---------------------------------------------------------------------------
# Hidden bundle collection
# ---------------------------------------------------------------------------
def _zip_dir_in_memory(root: str) -> bytes:
    """Zip a folder in memory, using ZIP_STORED so the outer compressor
    can still do useful work on text."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        root_abs = os.path.abspath(root)
        for cur, _dirs, files in os.walk(root):
            for f in sorted(files):
                abs_path = os.path.join(cur, f)
                arc = os.path.relpath(abs_path, root_abs)
                z.write(abs_path, arc, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def collect_hidden_bundles(paths, vlog):
    """One hidden entry per -b input.

    Files are stored as-is.  Folders are zipped in memory.  Archives are
    stored whole, not unpacked.
    """
    out_files = []
    for p in paths:
        abs_p = os.path.abspath(p)
        if not os.path.exists(abs_p):
            raise RuntimeError(f'hidden bundle not found: {p}')
        if os.path.isdir(abs_p):
            data = _zip_dir_in_memory(abs_p)
            key = os.path.basename(abs_p.rstrip(os.sep)) + '.zip'
            _validate_key(key, abs_p)
            out_files.append({'key': key, 'data': data, 'src': abs_p})
            vlog(f'hidden: {key} (folder zipped, {size_str(len(data))})')
        elif os.path.isfile(abs_p):
            data = _read_bytes(abs_p)
            key = os.path.basename(abs_p)
            _validate_key(key, abs_p)
            out_files.append({'key': key, 'data': data, 'src': abs_p})
            vlog(f'hidden: {key} ({size_str(len(data))})')
        else:
            raise RuntimeError(f'unsupported hidden input: {p}')
    return out_files


def build_hidden_bundle_blob(entries, vlog):
    by_key = {}
    for e in entries:
        k = e['key']
        if k in by_key:
            raise RuntimeError(
                f'duplicate hidden key {k!r}: '
                f'{by_key[k]["src"]} and {e["src"]}')
        by_key[k] = e

    keys = sorted(by_key.keys())
    parts = []
    meta = []
    off = 0
    for k in keys:
        e = by_key[k]
        meta.append({'k': k, 'o': off, 'n': len(e['data'])})
        parts.append(e['data'])
        off += len(e['data'])

    total = sum(len(p) for p in parts)
    if total > _HIDDEN_MAX_TOTAL:
        raise RuntimeError(
            f'hidden payload {size_str(total)} exceeds '
            f'{size_str(_HIDDEN_MAX_TOTAL)} limit')
    return b''.join(parts), meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv):
    o = dict(
        verbose=False, brotli=False, minify=False, lossy=False,
        base_href=None, ignore_uris=[],
        force=False, unpack=False, recursive=False,
        output=None, output_dir=None, prefix=None, inputs=[],
        encrypt=False, password=None, bundle_password=None,
        merge=None,
        bundle=[],
        list_mode=False, list_types=['all'],
        extract_mode=False, extract_types=['all'],
    )
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a in ('-v', '--verbose'):
            o['verbose'] = True
        elif a == '--brotli':
            o['brotli'] = True
        elif a in ('-m', '--minify'):
            o['minify'] = True
            if i + 1 < n and argv[i + 1] == 'lossy':
                i += 1
                o['lossy'] = True
        elif a in ('--force', '-f'):
            o['force'] = True
        elif a in ('--unpack', '-u'):
            o['unpack'] = True
        elif a in ('-r', '--recursive'):
            o['recursive'] = True
        elif a in ('--encrypt', '-E'):
            o['encrypt'] = True
        elif a in ('--bundle', '-b'):
            i += 1
            if i >= n:
                die('--bundle needs a file or folder')
            path = argv[i]
            hidden = False
            if i + 1 < n and argv[i + 1] == 'hidden':
                i += 1
                hidden = True
            o['bundle'].append({'path': path, 'hidden': hidden})
        elif a in ('-l', '--list'):
            o['list_mode'] = True
            if i + 1 < n:
                tl = _parse_type_list(argv[i + 1])
                if tl is not None:
                    i += 1
                    o['list_types'] = tl
        elif a in ('-x', '--extract'):
            o['extract_mode'] = True
            if i + 1 < n:
                tl = _parse_type_list(argv[i + 1])
                if tl is not None:
                    i += 1
                    o['extract_types'] = tl
        elif a in ('-M', '--merge'):
            i += 1
            if i >= n:
                die('--merge needs a directory')
            o['merge'] = argv[i]
        elif a == '--password':
            i += 1
            if i >= n:
                die('--password needs a value')
            o['password'] = argv[i]
        elif a == '--bundle-password':
            i += 1
            if i >= n:
                die('--bundle-password needs a value')
            o['bundle_password'] = argv[i]
        elif a == '--base-href':
            i += 1
            if i >= n:
                die('--base-href needs a value')
            o['base_href'] = argv[i]
        elif a == '--ignore-uris':
            i += 1
            if i >= n:
                die('--ignore-uris needs a value')
            o['ignore_uris'] = [s.strip()
                                for s in argv[i].split(',') if s.strip()]
        elif a == '--prefix':
            i += 1
            if i >= n:
                die('--prefix needs a value')
            o['prefix'] = argv[i]
        elif a in ('-o', '--output'):
            i += 1
            if i >= n:
                die('--output needs a value')
            o['output'] = argv[i]
        elif a in ('-O', '--output-dir'):
            i += 1
            if i >= n:
                die('--output-dir needs a value')
            o['output_dir'] = argv[i]
        elif a in ('-h', '--help'):
            usage(); sys.exit(0)
        elif a in ('-V', '--version'):
            print(f'arcager {VERSION}'); sys.exit(0)
        elif a.startswith('-') and len(a) > 1:
            die('unknown option: ' + a)
        else:
            o['inputs'].append(a)
        i += 1
    return o


def usage():
    print(f"""arcager {VERSION} — single-file HTML packer

Usage:
  python arcager.py [options] <input...>              pack
  python arcager.py -u [options] <input...>           unpack
  python arcager.py -M <dir> [options]                merge a directory
  python arcager.py -l [TYPE,...] <input...>          list media
  python arcager.py -x [TYPE,...] <input...>          extract media

Inputs may be files, directories, or shell globs.  A directory input
scans its .html/.htm files at the top level only; use -r to descend.

Options:
  -v, --verbose           Per-stage diagnostics on stderr.
      --brotli            Use Brotli instead of gzip (Chrome 105+ only).
  -m, --minify [lossy]    Run Terser on the inline unpacker JS.  Add
                          'lossy' to ALSO strip HTML comments, collapse
                          inter-tag whitespace, and remove sourceMappingURL
                          comments from the payload.  Lossy.
      --base-href URL     Inject <base href="URL"> into the payload.
                          Lossy.
      --ignore-uris LIST  Comma-separated media-type prefixes to NOT hoist.
  -E, --encrypt           Encrypt the HTML payload with AES-256-GCM
                          (PBKDF2-SHA256, 600k iterations).  Prompts for a
                          payload password unless --password is given.
      --password PW       Non-interactive payload password.
      --bundle-password PW
                          Non-interactive bundle password (for hidden
                          bundles).
  -b, --bundle PATH [hidden]
                          Bundle media from a file, folder, or archive.
                          Can be given multiple times.  Without 'hidden',
                          files are exposed to the page at runtime as
                          window.arcager.images.  With 'hidden', files are
                          packed as an encrypted blob the browser never
                          loads; extract with '-x pack'.  Hidden bundles
                          use their own password.
  -M, --merge DIR         Merge a directory into one packed file.
                          Inlines local CSS, JS, fonts, images, video,
                          CSV and TSV.  <link rel="csv" href="data.csv">
                          is inlined as <script type="text/csv"> and
                          exposed at runtime as window.arcager.csv.
  -l, --list [TYPE,...]   List media inside a packed or unpacked HTML
                          file.  Listing never needs a password.
                          TYPE (comma-separated):
                            all    - everything (default)
                            media  - hoisted data URIs from the payload
                            pack   - bundles (visible + hidden)
                            csv    - inlined CSV / TSV data blocks
                            images | videos | audio | fonts | docs
                                   - filter by category
  -x, --extract [TYPE,...]  Extract media into an output directory.
                          TYPE as for -l.  Prompts for passwords as needed.
                          Destination comes from -O; defaults to ./extracted.
  -f, --force             Overwrite output without prompting.
  -u, --unpack            Reverse a packed file back to HTML.
  -r, --recursive         Recurse into subdirectories.
  -o, --output PATH       Output file path (single input, or --merge).
  -O, --output-dir DIR    Output directory for any mode.  Auto-created.
                          For -x it is the extraction destination.
      --prefix PREFIX     Prefix for pack/merge output names.  Defaults
                          to 'packed_'.  Also stripped during unpack.
  -V, --version           Print version.
  -h, --help              This message.""")


# ---------------------------------------------------------------------------
# Terser (optional, for --minify)
# ---------------------------------------------------------------------------
def maybe_minify_js(js: str, enabled: bool, vlog) -> str:
    if not enabled:
        return js
    terser = shutil.which('terser')
    if not terser:
        vlog('terser not found on PATH; --minify has no effect on JS '
             '(npm i -g terser)')
        return js
    try:
        r = subprocess.run(
            [terser, '--compress',
             'passes=2,unsafe_arrows=true,toplevel=true',
             '--mangle', 'toplevel'],
            input=js.encode('utf-8'), capture_output=True,
        )
    except OSError as e:
        vlog(f'terser error: {e}')
        return js
    if r.returncode != 0:
        vlog('terser error: ' + r.stderr.decode('utf-8', errors='replace'))
        return js
    code = r.stdout.decode('utf-8')
    vlog(f'terser: {len(js)} -> {len(code)} chars')
    return code


# ---------------------------------------------------------------------------
# Shell template (JS + markup)
# ---------------------------------------------------------------------------
JS_BODY = r'''(function(){"use strict";
var D=JSON.parse(document.getElementById('__pk').textContent);
var ALGO=D.a;
var ENC=!!D.pe;
var PANEL=document.getElementById('__csp_panel');
var LAB=document.getElementById('__l');
var FILL=document.getElementById('__f');
var ERR=document.getElementById('__e');
var LDR=document.getElementById('__ldr');
var TOTAL=(D.h.cs + (D.b?D.b.cs:0))||1;
function setP(p){if(p<0)p=0;if(p>100)p=100;if(LAB)LAB.textContent='Unpacking HTML\u2026 '+Math.floor(p)+'%';if(FILL)FILL.style.width=p+'%';}
function fail(m){if(LAB)LAB.textContent='Failed to unpack';if(ERR)ERR.textContent=String((m&&m.message)||m);if(window.console)console.error(m);}
if(typeof DecompressionStream==='undefined'){fail('DecompressionStream is not supported by this browser.');return}
if(ENC&&(!window.crypto||!window.crypto.subtle)){fail('Encrypted bundles require a secure context (https://, file://, or localhost).');return}
var AZ="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#",ZM={};
for(var zi=0;zi<85;zi++)ZM[AZ.charCodeAt(zi)]=zi;
function z85d(s,size){s=s.replace(/\s+/g,'');var o=new Uint8Array(size),k=0;for(var i=0;i+5<=s.length&&k<size;i+=5){var v=0;for(var j=0;j<5;j++){var d=ZM[s.charCodeAt(i+j)];if(d===undefined)throw new Error('bad z85 char at '+i);v=v*85+d}if(k<size)o[k++]=Math.floor(v/16777216)%256;if(k<size)o[k++]=Math.floor(v/65536)%256;if(k<size)o[k++]=Math.floor(v/256)%256;if(k<size)o[k++]=v%256;}return o;}
function b64(bytes){var s='',CH=0x8000;for(var i=0;i<bytes.length;i+=CH)s+=String.fromCharCode.apply(null,bytes.subarray(i,i+CH));return btoa(s);}
function b64d(s){var bin=atob(s);var o=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)o[i]=bin.charCodeAt(i);return o;}
async function deriveKeyFromPassword(pw,saltB64,iters){
  var enc=new TextEncoder();
  var base=await crypto.subtle.importKey('raw',enc.encode(pw),{name:'PBKDF2'},false,['deriveKey']);
  return await crypto.subtle.deriveKey({name:'PBKDF2',salt:b64d(saltB64),iterations:iters,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['decrypt']);
}
async function decBytes(key,ivB64,bytes){return new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:b64d(ivB64)},key,bytes));}
async function decompressBytes(bytes,algo,orig,onP){
  var ds;try{ds=new DecompressionStream(algo)}catch(e){throw new Error(algo==='brotli'?'Brotli is not supported by this browser. Repack without --brotli.':e.message)}
  var wr=ds.writable.getWriter(),rd=ds.readable.getReader();
  var ch=[],ol=0,CH=16384;
  var feeder=(async function(){for(var off=0;off<bytes.length;off+=CH){var end=Math.min(off+CH,bytes.length);await wr.write(bytes.subarray(off,end));}await wr.close();})();
  while(true){var r=await rd.read();if(r.done)break;ch.push(r.value);ol+=r.value.length;if(onP)onP(Math.min(99,ol/orig*100));}
  await feeder;
  var outb=new Uint8Array(ol),pos=0;
  for(var i=0;i<ch.length;i++){outb.set(ch[i],pos);pos+=ch[i].length}
  return outb;
}
function whenParsed(){return new Promise(function(res){if(document.readyState!=='loading')return res();document.addEventListener('DOMContentLoaded',function(){res()},{once:true});});}
function cleanupScript(){return '<scr'+'ipt>(function(){var p=document.getElementById("__csp_panel");if(p&&p.parentNode)p.parentNode.removeChild(p);var l=document.getElementById("__ldr");if(l&&l.parentNode)l.parentNode.removeChild(l);var ts=document.querySelectorAll("title[data-shell]");for(var i=0;i<ts.length;i++)ts[i].remove();})();<\/scr'+'ipt>';}
function commit(html){var m=html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);if(m){try{document.title=m[1].replace(/<[^>]*>/g,'').trim()}catch(e){}}var cl=cleanupScript();var full=/<\/body>/i.test(html)?html.replace(/<\/body>/i,cl+'</body>'):html+cl;document.open();document.write(full);document.close();setTimeout(function(){try{var p=document.getElementById('__csp_panel');if(p&&p.parentNode)p.parentNode.removeChild(p);var l=document.getElementById('__ldr');if(l&&l.parentNode)l.parentNode.removeChild(l);var ts=document.querySelectorAll('title[data-shell]');for(var i=0;i<ts.length;i++)ts[i].remove();}catch(e){}},0);}
function promptPasswordUI(meta){
  return new Promise(function(resolve,reject){
    var panel=document.getElementById('__pw');
    var input=document.getElementById('__pwi');
    var form=document.getElementById('__pwf');
    var errEl=document.getElementById('__pwe');
    var btn=document.getElementById('__pwb');
    if(!panel||!input||!form){reject(new Error('password UI missing'));return}
    setTimeout(function(){try{input.focus()}catch(e){}},0);
    function setBusy(b){input.disabled=b;btn.disabled=b;btn.textContent=b?'Unlocking\u2026':'Unlock';}
    async function attempt(){
      var pw=input.value;
      if(!pw)return;
      errEl.textContent='';
      setBusy(true);
      try{
        var key=await deriveKeyFromPassword(pw,meta.s,meta.i);
        var ok=false;
        try{var pt=await decBytes(key,meta.ivc,b64d(meta.ck));ok=new TextDecoder().decode(pt)==='arcager-ok';}catch(e){ok=false;}
        if(!ok){errEl.textContent='Incorrect password.';setBusy(false);input.value='';input.focus();return;}
        if(panel.parentNode)panel.parentNode.removeChild(panel);
        resolve(key);
      }catch(e){errEl.textContent='Error: '+((e&&e.message)||e);setBusy(false);}
    }
    form.addEventListener('submit',function(ev){ev.preventDefault();attempt();});
  });
}
function buildImages(raw,meta){
  var images={};
  var docRe=/^(text\/|application\/(pdf|postscript|xhtml))/;
  for(var i=0;i<meta.m.length;i++){
    var e=meta.m[i];
    var slice=raw.subarray(e.o,e.o+e.n);
    var url;
    if(docRe.test(e.t)){
      try{url=URL.createObjectURL(new Blob([slice],{type:e.t}));}
      catch(x){url='data:'+e.t+';base64,'+b64(slice);}
    }else{
      url='data:'+e.t+';base64,'+b64(slice);
    }
    images[e.k]=url;
  }
  if(meta.al){
    for(var a in meta.al){
      if(Object.prototype.hasOwnProperty.call(meta.al,a)){
        var tgt=meta.al[a];
        if(images[a]===undefined&&images[tgt]!==undefined)images[a]=images[tgt];
      }
    }
  }
  return images;
}
function remapURL(url,images){
  if(!url)return null;
  if(/^(data:|https?:|\/\/|blob:|#|javascript:|mailto:|tel:|ftp:|about:|ws:|wss:|file:)/i.test(url))return null;
  var clean=url.split('?')[0].split('#')[0];
  if(!clean)return null;
  var cands=[clean];
  if(clean.substring(0,2)==='./')cands.push(clean.substring(2));
  var n0=cands.length;
  for(var i=0;i<n0;i++){var v=cands[i];if(/\./.test(v))cands.push(v.replace(/\.[^.\/]+$/,''));}
  var n1=cands.length;
  for(var i=0;i<n1;i++){var v=cands[i];var b=v.split('/').pop();if(b&&b!==v)cands.push(b);}
  for(var i=0;i<cands.length;i++){
    var c=cands[i];
    if(images[c])return images[c];
    var lc=c.toLowerCase();
    if(images[lc])return images[lc];
  }
  return null;
}
function remapHTML(html,images){
  html=html.replace(/\b(src|href|poster|xlink:href)=(["'])([^"']+)\2/gi,function(m,attr,q,url){var v=remapURL(url,images);return v?attr+'='+q+v+q:m;});
  html=html.replace(/\bsrcset=(["'])([^"']+)\1/gi,function(m,q,list){
    var parts=list.split(','),outp=[];
    for(var i=0;i<parts.length;i++){
      var item=parts[i].trim();if(!item)continue;
      var toks=item.split(/\s+/),url=toks[0],rest=toks.slice(1).join(' ');
      var v=remapURL(url,images);
      outp.push(v?(v+(rest?' '+rest:'')):item);
    }
    return 'srcset='+q+outp.join(', ')+q;
  });
  html=html.replace(/url\(\s*(["']?)([^"')]+)\1\s*\)/gi,function(m,q,url){var v=remapURL(url,images);return v?'url('+q+v+q+')':m;});
  return html;
}
function parseCSV(text,delim){
  delim=delim||',';
  var rows=[],row=[],field='',inQ=false,i=0,n=text.length;
  function flushRow(){if(row.length||field.length){row.push(field);rows.push(row);row=[];field='';}}
  while(i<n){
    var c=text[i];
    if(inQ){
      if(c==='"'){
        if(i+1<n&&text[i+1]==='"'){field+='"';i+=2;continue;}
        inQ=false;i++;continue;
      }
      field+=c;i++;continue;
    }
    if(c==='"'){inQ=true;i++;continue;}
    if(c===delim){row.push(field);field='';i++;continue;}
    if(c==='\r'){flushRow();if(i+1<n&&text[i+1]==='\n')i++;i++;continue;}
    if(c==='\n'){flushRow();i++;continue;}
    field+=c;i++;
  }
  flushRow();
  return rows;
}
function csvToObjects(rows){
  if(!rows.length)return [];
  var headers=rows[0],out=[];
  for(var i=1;i<rows.length;i++){
    var obj={};
    for(var j=0;j<headers.length;j++){
      obj[headers[j]]=rows[i][j]!==undefined?rows[i][j]:'';
    }
    out.push(obj);
  }
  return out;
}
function loadCSVBlocks(){
  var blocks=document.querySelectorAll('script[type="text/csv"]');
  if(!blocks.length)return null;
  var csv={};
  for(var i=0;i<blocks.length;i++){
    var el=blocks[i];
    var key=el.getAttribute('data-key')||('csv'+i);
    var delim=el.getAttribute('data-delim')||',';
    var rows=parseCSV(el.textContent||'',delim);
    csv[key]={rows:rows,data:csvToObjects(rows)};
  }
  return csv;
}
(async function(){
  try{
    if(PANEL&&PANEL.parentNode)PANEL.parentNode.removeChild(PANEL);
    var KEY=null;
    if(ENC){KEY=await promptPasswordUI(D.pe);}
    if(LDR)LDR.hidden=false;
    var IMAGES=null;
    if(D.att&&D.att.m&&D.att.m.length){
      try{
        var attEl=document.getElementById('__att_data');
        if(!attEl)throw new Error('bundle container missing');
        var attBytes=z85d(attEl.textContent||'',D.att.cs);
        if(KEY&&D.pe&&D.pe.iv3)attBytes=await decBytes(KEY,D.pe.iv3,attBytes);
        var attRaw=D.att.c?await decompressBytes(attBytes,ALGO,D.att.n,null):attBytes;
        IMAGES=buildImages(attRaw,D.att);
      }catch(e){
        if(window.console)console.error('bundle load failed',e);
        IMAGES=null;
      }
    }
    var CSV=loadCSVBlocks();
    window.arcager={
      images:IMAGES||{},
      csv:CSV||{},
      getImage:function(k){
        if(!IMAGES||k===undefined||k===null)return null;
        var v=IMAGES[k]||IMAGES[String(k).toLowerCase()];
        if(!v)return null;
        var img=new Image();img.src=v;return img;
      },
      ready:Promise.resolve({images:IMAGES||{},csv:CSV||{}})
    };
    await whenParsed();
    var hzBytes=z85d(D.h.z,D.h.cs);
    if(KEY&&D.pe&&D.pe.iv1)hzBytes=await decBytes(KEY,D.pe.iv1,hzBytes);
    var htmlBytes=D.h.c?await decompressBytes(hzBytes,ALGO,D.h.os,function(p){setP(p*D.h.cs/TOTAL)}):hzBytes;
    var html=new TextDecoder('utf-8').decode(htmlBytes);
    if(D.m&&D.m.length&&D.b&&D.b.cs>0){
      var bb=z85d(D.b.z,D.b.cs);
      if(KEY&&D.pe&&D.pe.iv2)bb=await decBytes(KEY,D.pe.iv2,bb);
      if(D.b.c)bb=await decompressBytes(bb,ALGO,D.b.cs,null);
      for(var i=0;i<D.m.length;i++){var e=D.m[i];html=html.replace(e.p,(function(ee,sl){return function(){return 'data:'+ee.t+';base64,'+b64(sl);};})(e,bb.subarray(e.o,e.o+e.n)));}
    }
    if(IMAGES)html=remapHTML(html,IMAGES);
    setP(100);commit(html);
  }catch(x){fail(x)}
})();
})();'''


SHELL_TEMPLATE = r'''<!--arcager:3-->
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title data-shell="1">Loading…</title>
<style>
[hidden]{display:none!important}
html,body{margin:0;height:100%;background:#0e0e10;color:#e6e6e6;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
#__ldr{display:flex;height:100%;align-items:center;justify-content:center}
#__ldr .b{text-align:center;min-width:240px}
#__l{margin-bottom:10px;opacity:.9}
#__t{height:6px;background:#1c1c22;border-radius:3px;overflow:hidden}
#__f{height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#22d3ee);transition:width .1s linear}
#__e{margin-top:12px;color:#f87171;font-size:13px;white-space:pre-wrap}
#__csp_panel{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#0e0e10;z-index:2147483647}
#__csp_panel .box{max-width:520px;padding:24px;text-align:left}
#__csp_panel h2{margin:0 0 10px;font-size:16px;color:#fbbf24}
#__csp_panel p{margin:8px 0;line-height:1.5}
#__csp_panel code{background:#1c1c22;padding:2px 5px;border-radius:3px;font-size:12px}
#__pw{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#0e0e10;z-index:2147483646}
#__pw .box{max-width:440px;padding:28px 32px;background:#15151a;border-radius:8px;box-shadow:0 12px 40px rgba(0,0,0,.6)}
#__pw h2{margin:0 0 10px;font-size:17px;color:#e6e6e6;font-weight:600}
#__pw p{margin:6px 0 0;line-height:1.55;color:#999;font-size:13px}
#__pw code{background:#1c1c22;padding:1px 5px;border-radius:3px;font-size:12px;color:#c8c8d0;word-break:break-all}
#__pw form{display:flex;gap:8px;margin-top:18px}
#__pwi{flex:1;min-width:0;padding:9px 12px;border:1px solid #2a2a33;border-radius:5px;background:#0e0e10;color:#e6e6e6;font:inherit;font-size:14px;outline:none;transition:border-color .15s,box-shadow .15s}
#__pwi:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.22)}
#__pwi:disabled{opacity:.55}
#__pwb{padding:9px 18px;border:none;border-radius:5px;background:#3b82f6;color:#fff;font:inherit;font-size:14px;font-weight:500;cursor:pointer;transition:background-color .15s}
#__pwb:hover:not(:disabled){background:#2563eb}
#__pwb:disabled{opacity:.55;cursor:default}
#__pwe{margin-top:12px;color:#f87171;font-size:13px;min-height:1.4em}
</style></head><body>
<div id="__csp_panel"><div class="box">
  <h2>Cannot unpack this file</h2>
  <p>This is a self-contained HTML bundle that requires JavaScript to decompress its content at load time.</p>
  <p>Your browser or the site's Content-Security-Policy is blocking the inline unpacker. <code>script-src</code> must permit <code>'unsafe-inline'</code> (or a matching nonce/hash) for <code>__FILE_NAME__</code> to work.</p>
  <p>Options: open this file locally (<code>file://</code> usually isn't subject to CSP), serve it from a location with a permissive policy, or ask the publisher for a non-bundled version.</p>
</div></div>
__PW_PANEL__
<div id="__ldr"__LDR_HIDDEN__><div class="b">
<div id="__l">Unpacking HTML… 0%</div>
<div id="__t"><div id="__f"></div></div>
<div id="__e"></div>
</div></div>
<script type="application/json" id="__pk">__JSON_STR__</script>
__ATT_DATA_BLOCK__
__HID_DATA_BLOCK__
<script>
__JS_BODY__
</script>
</body></html>
'''


def build_shell(json_str: str, file_name: str, encrypted: bool,
                js_body: str, att_data_z85: str = '',
                hid_data_z85: str = '') -> str:
    if encrypted:
        pw_panel = (
            '\n<div id="__pw"><div class="box">\n'
            '  <h2>Password required</h2>\n'
            f'  <p>Enter the password to decrypt <code>{escape_text(file_name)}</code>.</p>\n'
            '  <form id="__pwf" autocomplete="off">\n'
            '    <input type="password" id="__pwi" autocomplete="new-password" spellcheck="false" autocapitalize="off">\n'
            '    <button type="submit" id="__pwb">Unlock</button>\n'
            '  </form>\n'
            '  <div id="__pwe" role="alert"></div>\n'
            '</div></div>'
        )
        ldr_hidden = ' hidden'
    else:
        pw_panel = ''
        ldr_hidden = ''

    att_block = ('<script type="text/plain" id="__att_data">'
                 + att_data_z85 + '</script>') if att_data_z85 else ''
    hid_block = ('<script type="text/plain" id="__hid_data">'
                 + hid_data_z85 + '</script>') if hid_data_z85 else ''

    return (SHELL_TEMPLATE
            .replace('__ATT_DATA_BLOCK__', att_block)
            .replace('__HID_DATA_BLOCK__', hid_block)
            .replace('__PW_PANEL__', pw_panel)
            .replace('__LDR_HIDDEN__', ldr_hidden)
            .replace('__FILE_NAME__', escape_text(file_name))
            .replace('__JSON_STR__', json_str)
            .replace('__JS_BODY__', js_body))


# ---------------------------------------------------------------------------
# Core packing pipeline
# ---------------------------------------------------------------------------
DATA_URI_RE = re.compile(r'data:([^,]*?;base64),([A-Za-z0-9+/=]+)')
MIN_EXTRACT = 256


def _pack_html_to_file(html: str, out_name: str, display_name: str,
                       opts, vlog, in_size: int) -> dict:
    # ---- lossy HTML minification -------------------------------------
    if opts['minify'] and opts['lossy']:
        before = len(html)
        html, cs, wc, sm = minify_html_lossy(html)
        vlog(f'minify lossy: -{before - len(html)} chars '
             f'(comments -{cs}, whitespace -{wc}, sourcemaps -{sm})')

    # ---- data URI hoisting -------------------------------------------
    uris = []
    for m in DATA_URI_RE.finditer(html):
        full, prefix, b64 = m.group(0), m.group(1), m.group(2)
        pad = 2 if b64.endswith('==') else (1 if b64.endswith('=') else 0)
        raw_len = (len(b64) * 3) // 4 - pad
        uris.append({'full': full, 'prefix': prefix, 'b64': b64,
                     'raw_len': raw_len})

    to_extract = []
    for u in uris:
        if u['raw_len'] < MIN_EXTRACT:
            continue
        mt = re.sub(r';base64$', '', u['prefix'])
        if any(mt.lower().startswith(p.lower())
               for p in opts['ignore_uris']):
            continue
        to_extract.append(u)
    vlog(f'data URIs: {len(uris)} found, {len(to_extract)} hoisted')

    meta = []
    bin_parts = []
    bin_off = 0
    for i, u in enumerate(to_extract):
        ph = f'%%PH_{i:04d}%%'
        if ph in html:
            raise RuntimeError('placeholder collision: ' + ph)
        raw = b64_decode_lenient(u['b64'])
        meta.append({
            'p': ph, 't': re.sub(r';base64$', '', u['prefix']),
            'o': bin_off, 'n': len(raw),
        })
        bin_parts.append(raw)
        bin_off += len(raw)
        idx = html.find(u['full'])
        if idx == -1:
            continue
        html = html[:idx] + ph + html[idx + len(u['full']):]

    bin_blob = b''.join(bin_parts)

    # ---- base href ----------------------------------------------------
    if opts['base_href']:
        tag = f'<base href="{escape_attr(opts["base_href"])}">'
        if re.search(r'<head[^>]*>', html, re.IGNORECASE):
            html = re.sub(r'(<head[^>]*>)', lambda m: m.group(1) + tag,
                          html, count=1, flags=re.IGNORECASE)
        elif re.search(r'<html[^>]*>', html, re.IGNORECASE):
            html = re.sub(r'(<html[^>]*>)', lambda m: m.group(1) + tag,
                          html, count=1, flags=re.IGNORECASE)
        else:
            html = tag + html
        vlog(f'injected base href: {opts["base_href"]}')

    # ---- compress HTML ----------------------------------------------
    html_buf = html.encode('utf-8')
    algo = 'brotli' if opts['brotli'] else 'gzip'

    if opts['brotli'] and not HAS_BROTLI:
        raise RuntimeError('--brotli requires the "brotli" package '
                           '(pip install brotli)')

    html_cmp, html_c = maybe_compress(html_buf, algo)
    vlog(f'html: {len(html_buf)} B -> {len(html_cmp)} B '
         f"({'compressed' if html_c else 'stored'})")

    # ---- payload encryption ------------------------------------------
    encrypt_payload = opts['encrypt']
    pe = None
    key = None

    if encrypt_payload:
        if not opts['password']:
            raise RuntimeError('--encrypt requires a password')
        salt = random_b64(SALT_LEN)
        iv1 = random_b64(IV_LEN)
        iv2 = random_b64(IV_LEN)
        iv3 = random_b64(IV_LEN)
        ivc = random_b64(IV_LEN)
        key = derive_key(opts['password'], salt, PBKDF2_ITERATIONS)
        html_cmp = aes_gcm_encrypt(key, iv1, html_cmp)
        if bin_blob:
            bin_blob = aes_gcm_encrypt(key, iv2, bin_blob)
        canary = aes_gcm_encrypt(key, ivc, PASSWORD_CHECK)
        pe = {
            's': salt, 'i': PBKDF2_ITERATIONS,
            'iv1': iv1, 'iv2': iv2, 'iv3': iv3, 'ivc': ivc,
            'ck': base64.b64encode(canary).decode('ascii'),
        }
        vlog(f'payload encrypted: html {len(html_buf)} B')

    # ---- visible bundles ---------------------------------------------
    att_z85 = ''
    att_meta = None
    visible_paths = [a['path'] for a in opts['bundle'] if not a['hidden']]
    if visible_paths:
        entries = collect_visible_bundles(visible_paths, vlog)
        if entries:
            att_raw, att_list, att_aliases = build_visible_bundle_blob(entries, vlog)
            pre = _all_entries_precompressed(entries)
            if pre:
                att_cmp, att_c = att_raw, 0
                vlog('bundles: all entries already compressed; storing raw')
            else:
                att_cmp, att_c = maybe_compress(att_raw, algo)

            if key is not None:
                att_cmp = aes_gcm_encrypt(key, pe['iv3'], att_cmp)

            att_z85 = z85_encode_script_safe(att_cmp)
            att_meta = {
                'cs': len(att_cmp), 'n': len(att_raw), 'c': att_c,
                'm': att_list, 'al': att_aliases,
            }
            vlog(f'visible bundles: {len(entries)} file(s), '
                 f'{size_str(len(att_raw))} raw -> '
                 f'{size_str(len(att_cmp))} final -> '
                 f'{len(att_z85)} z85 chars')

    # ---- hidden bundles ----------------------------------------------
    hid_z85 = ''
    hid_meta = None
    ae = None
    hidden_paths = [a['path'] for a in opts['bundle'] if a['hidden']]
    if hidden_paths:
        if not opts['bundle_password']:
            raise RuntimeError('hidden bundles require a bundle '
                               'password (--bundle-password or prompt)')
        entries = collect_hidden_bundles(hidden_paths, vlog)
        if entries:
            hid_raw, hid_list = build_hidden_bundle_blob(entries, vlog)
            pre = _all_entries_precompressed(entries)
            if pre:
                hid_cmp, hid_c = hid_raw, 0
                vlog('hidden: all entries already compressed; storing raw')
            else:
                hid_cmp, hid_c = maybe_compress(hid_raw, algo)

            salt = random_b64(SALT_LEN)
            ivh = random_b64(IV_LEN)
            ivc = random_b64(IV_LEN)
            hkey = derive_key(opts['bundle_password'], salt,
                              PBKDF2_ITERATIONS)
            hid_enc = aes_gcm_encrypt(hkey, ivh, hid_cmp)
            canary = aes_gcm_encrypt(hkey, ivc, PASSWORD_CHECK)
            ae = {
                's': salt, 'i': PBKDF2_ITERATIONS,
                'ivh': ivh, 'ivc': ivc,
                'ck': base64.b64encode(canary).decode('ascii'),
            }
            hid_z85 = z85_encode_script_safe(hid_enc)
            hid_meta = {
                'cs': len(hid_enc), 'n': len(hid_raw), 'c': hid_c,
                'm': hid_list,
            }
            vlog(f'hidden bundles: {len(entries)} file(s), '
                 f'{size_str(len(hid_raw))} raw -> '
                 f'{size_str(len(hid_enc))} encrypted -> '
                 f'{len(hid_z85)} z85 chars')

    # ---- JSON metadata ------------------------------------------------
    json_obj = {
        'v': 3,
        'a': algo,
        'h': {'z': z85_encode(html_cmp), 'cs': len(html_cmp),
              'os': len(html_buf), 'c': html_c},
        'm': meta,
    }
    if bin_blob:
        json_obj['b'] = {'z': z85_encode(bin_blob), 'cs': len(bin_blob),
                         'c': 0}
    if pe:
        json_obj['pe'] = pe
    if att_meta:
        json_obj['att'] = att_meta
    if hid_meta:
        json_obj['hid'] = hid_meta
    if ae:
        json_obj['ae'] = ae
    json_str = json.dumps(json_obj, separators=(',', ':'),
                          ensure_ascii=False).replace('<', '\\u003c')

    js_body = maybe_minify_js(JS_BODY, opts['minify'], vlog)
    shell = build_shell(json_str, display_name, bool(pe),
                        js_body, att_z85, hid_z85)
    write_text_file(out_name, shell)

    out_bytes = len(shell.encode('utf-8'))
    return {
        'out': out_name, 'status': 'ok',
        'inSize': in_size, 'outSize': out_bytes,
        'ratio': out_bytes / max(1, in_size),
        'hoisted': len(meta), 'binBytes': len(bin_blob),
        'encrypted': bool(pe),
        'hidden': bool(hid_meta),
        'attached': (len(att_meta['m']) if att_meta else 0),
        'hidden_count': (len(hid_meta['m']) if hid_meta else 0),
    }


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------
_DEFAULT_PACK_PREFIX = 'packed_'
_DEFAULT_UNPACK_PREFIX = 'unpacked_'


def _pack_prefix(opts) -> str:
    return opts['prefix'] if opts.get('prefix') else _DEFAULT_PACK_PREFIX


def _strip_known_prefix(base: str, opts) -> str:
    """Strip the pack prefix from a base name, for deriving the 'core'."""
    candidates = []
    if opts.get('prefix'):
        candidates.append(opts['prefix'])
    candidates.extend((_DEFAULT_PACK_PREFIX, _DEFAULT_UNPACK_PREFIX))
    for p in candidates:
        if p and base.lower().startswith(p.lower()):
            return base[len(p):]
    return base


def _prepare_dir(path: str, force: bool, check_empty: bool = False):
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise RuntimeError(f'{path} is not a directory')
        if check_empty and not force:
            entries = os.listdir(path)
            if entries:
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        f'{path} is not empty; use --force')
                try:
                    ans = input(
                        f"{C['yellow']}{path}{C['reset']} exists and is not "
                        f"empty. Overwrite? [y/N] ").strip()
                except EOFError:
                    raise RuntimeError('cannot confirm overwrite')
                if not re.match(r'^y(es)?$', ans, re.IGNORECASE):
                    raise RuntimeError('aborted by user')
    else:
        os.makedirs(path, exist_ok=True)


def _output_path(opts, default_name: str, default_dir: str):
    """Resolve the output path for pack/unpack/merge."""
    if opts['output']:
        return opts['output']
    if opts['output_dir']:
        _prepare_dir(opts['output_dir'], opts['force'])
        return os.path.join(opts['output_dir'], default_name)
    return os.path.join(default_dir, default_name)


# ---------------------------------------------------------------------------
# Pack (single file)
# ---------------------------------------------------------------------------
def pack_file(file, opts, vlog, prompt_overwrite):
    base = os.path.basename(file)
    dirp = os.path.dirname(file)
    stem = re.sub(r'\.html?$', '', base, flags=re.IGNORECASE)
    out_name = _output_path(opts, f'{_pack_prefix(opts)}{stem}.html', dirp)

    if read_signature(file) is not None:
        return {'file': file, 'status': 'skip-already-packed'}
    if os.path.abspath(out_name) == os.path.abspath(file):
        raise RuntimeError('output would overwrite input')

    if os.path.exists(out_name) and not opts['force']:
        if not callable(prompt_overwrite):
            raise RuntimeError(f'output exists: {out_name}  (use --force)')
        if not prompt_overwrite(out_name):
            return {'file': file, 'out': out_name,
                    'status': 'skip-no-overwrite'}

    vlog(f'reading {file}')
    with open(file, 'rb') as f:
        original = f.read()
    html = original.decode('utf-8', errors='replace')

    res = _pack_html_to_file(html, out_name, os.path.basename(out_name),
                             opts, vlog, len(original))
    res['file'] = file
    return res


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
_EXTERNAL_PREFIXES = (
    'http://', 'https://', '//', 'data:', 'mailto:', 'tel:',
    'javascript:', '#', 'blob:', 'about:', 'ftp://',
    'ws://', 'wss://', 'file:',
)
_LINK_RE       = re.compile(r'<link\b[^>]*>', re.IGNORECASE)
_SCRIPT_SRC_RE = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*([\'"])([^\'"]+)\1[^>]*>\s*</script\s*>',
    re.IGNORECASE | re.DOTALL,
)
_STYLE_BLOCK_RE = re.compile(r'<style\b[^>]*>([\s\S]*?)</style\s*>',
                             re.IGNORECASE)
_STYLE_ATTR_RE  = re.compile(r'(\bstyle\s*=\s*([\'"]))([\s\S]*?)\2',
                             re.IGNORECASE)
_MEDIA_TAG_RE   = re.compile(
    r'<\s*(img|source|video|audio|track|iframe|embed|input|a)\b[^>]*>',
    re.IGNORECASE)
_SRCSET_RE      = re.compile(r'\bsrcset\s*=\s*([\'"])([^\'"]+)\1',
                             re.IGNORECASE)
_CSS_IMPORT_URL_RE = re.compile(
    r'@import\s+url\(\s*([\'"]?)([^\'")]+)\1\s*\)\s*;', re.IGNORECASE)
_CSS_IMPORT_STR_RE = re.compile(
    r'@import\s+([\'"])([^\'"]+)\1\s*;', re.IGNORECASE)
_CSS_URL_RE = re.compile(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)', re.IGNORECASE)

_MERGE_MAX_INLINE_BYTES = 64 * 1024 * 1024


def _is_external(url: str) -> bool:
    if not url:
        return True
    lower = url.lower()
    return any(lower.startswith(p) for p in _EXTERNAL_PREFIXES)


def _resolve_local(base_dir: str, url: str):
    if _is_external(url):
        return None
    clean = url.split('?', 1)[0].split('#', 1)[0]
    if not clean:
        return None
    clean = urllib.parse.unquote(clean)
    if clean.startswith('/'):
        clean = clean.lstrip('/')
    return os.path.normpath(os.path.join(base_dir, clean))


def _read_media_as_data_uri(path: str) -> str:
    with open(path, 'rb') as f:
        raw = f.read()
    return f'data:{_mime_for(path)};base64,' + base64.b64encode(raw).decode('ascii')


def _find_index(dir_path: str):
    for name in ('index.html', 'index.htm', 'default.html', 'default.htm'):
        p = os.path.join(dir_path, name)
        if os.path.isfile(p):
            return p
    return None


def merge_directory(dir_path, opts, vlog, prompt_overwrite):
    if not os.path.isdir(dir_path):
        raise RuntimeError(f'not a directory: {dir_path}')

    dir_abs = os.path.abspath(dir_path)
    dir_name = os.path.basename(dir_abs)
    index_path = _find_index(dir_abs)
    if not index_path:
        raise RuntimeError(
            f'no index.html found in {dir_path} '
            f'(looked for index.html, index.htm, default.html, default.htm)')

    out_name = _output_path(opts, f'{_pack_prefix(opts)}{dir_name}.html',
                            os.path.dirname(dir_abs))
    if os.path.abspath(out_name) == os.path.abspath(index_path):
        raise RuntimeError('output would overwrite index.html')

    if os.path.exists(out_name) and not opts['force']:
        if not callable(prompt_overwrite):
            raise RuntimeError(f'output exists: {out_name}  (use --force)')
        if not prompt_overwrite(out_name):
            return {'file': dir_path, 'out': out_name,
                    'status': 'skip-no-overwrite'}

    vlog(f'merging directory {dir_path}')
    vlog(f'index: {os.path.relpath(index_path, dir_abs)}')

    stats = {'css': 0, 'js': 0, 'media': 0, 'fonts': 0, 'csv': 0, 'html': 1}
    missing = set()
    external = set()
    unmergeable = set()
    media_cache = {}
    seen_css = set()
    source_bytes = 0

    def get_data_uri(abs_path: str):
        abs_path = os.path.abspath(abs_path)
        if abs_path in media_cache:
            return media_cache[abs_path]
        try:
            sz = os.path.getsize(abs_path)
            if sz > _MERGE_MAX_INLINE_BYTES:
                vlog(f'skip large media ({size_str(sz)}): '
                     f'{os.path.relpath(abs_path, dir_abs)}')
                return None
            uri = _read_media_as_data_uri(abs_path)
            media_cache[abs_path] = uri
            cat = media_category(_mime_for(abs_path))
            if cat == 'fonts':
                stats['fonts'] += 1
            else:
                stats['media'] += 1
            return uri
        except Exception as e:
            vlog(f'warn: cannot read {abs_path}: {e}')
            return None

    def process_css_urls(css_text: str, css_dir: str, depth: int = 0) -> str:
        if depth > 16:
            return css_text

        def do_import(url, orig):
            if _is_external(url):
                return orig
            abs_path = _resolve_local(css_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url)
                return orig
            try:
                sub = read_text_file(abs_path)
                nonlocal source_bytes
                source_bytes += os.path.getsize(abs_path)
                return process_css_urls(sub, os.path.dirname(abs_path),
                                        depth + 1)
            except Exception as e:
                vlog(f'warn: @import failed ({url}): {e}')
                missing.add(url)
                return orig

        css_text = _CSS_IMPORT_URL_RE.sub(
            lambda m: do_import(m.group(2), m.group(0)), css_text)
        css_text = _CSS_IMPORT_STR_RE.sub(
            lambda m: do_import(m.group(2), m.group(0)), css_text)

        def repl_url(m):
            url = m.group(2).strip()
            if _is_external(url):
                return m.group(0)
            abs_path = _resolve_local(css_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url)
                return m.group(0)
            uri = get_data_uri(abs_path)
            if uri is None:
                missing.add(url)
                return m.group(0)
            return f'url({uri})'

        return _CSS_URL_RE.sub(repl_url, css_text)

    def inline_css_file(abs_path: str) -> str:
        nonlocal source_bytes
        source_bytes += os.path.getsize(abs_path)
        css = read_text_file(abs_path)
        return process_css_urls(css, os.path.dirname(abs_path))

    def repl_link(m):
        tag = m.group(0)
        href_m = re.search(r'\bhref\s*=\s*([\'"])([^\'"]+)\1',
                           tag, re.IGNORECASE)
        rel_m = re.search(r'\brel\s*=\s*([\'"])([^\'"]+)\1',
                          tag, re.IGNORECASE)
        if not href_m:
            return tag
        url = href_m.group(2)
        rel = rel_m.group(2).lower() if rel_m else ''
        rels = rel.split()

        # CSV / TSV inlining (rel="csv" or file extension)
        if ('csv' in rels
                or url.lower().endswith('.csv')
                or url.lower().endswith('.tsv')):
            if _is_external(url):
                external.add(url)
                return tag
            abs_path = _resolve_local(html_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url)
                return tag
            try:
                nonlocal source_bytes
                source_bytes += os.path.getsize(abs_path)
                raw = read_text_file(abs_path)
                key = os.path.splitext(os.path.basename(url))[0]
                key = re.sub(r'[^\w\-]', '_', key)
                safe = raw.replace('</script>', '<\\/script>')
                stats['csv'] += 1
                return (f'<script type="text/csv" data-key="{key}">'
                        f'\n{safe}\n</script>')
            except Exception as e:
                vlog(f'warn: csv inline failed ({url}): {e}')
                missing.add(url)
                return tag

        if 'stylesheet' in rels:
            if _is_external(url):
                external.add(url)
                return tag
            abs_path = _resolve_local(html_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url)
                return tag
            if abs_path in seen_css:
                return ''
            seen_css.add(abs_path)
            try:
                css = inline_css_file(abs_path)
                stats['css'] += 1
                return f'<style>\n{css}\n</style>'
            except Exception as e:
                vlog(f'warn: stylesheet inline failed ({url}): {e}')
                missing.add(url)
                return tag

        if _is_external(url):
            return tag
        abs_path = _resolve_local(html_dir, url)
        if not abs_path or not os.path.isfile(abs_path):
            missing.add(url)
            return tag
        uri = get_data_uri(abs_path)
        if uri is None:
            missing.add(url)
            return tag
        return tag[:href_m.start()] + f'href="{uri}"' + tag[href_m.end():]

    def repl_script(m):
        tag = m.group(0)
        src_m = re.search(r'\bsrc\s*=\s*([\'"])([^\'"]+)\1',
                          tag, re.IGNORECASE)
        if not src_m:
            return tag
        url = src_m.group(2)
        if _is_external(url):
            external.add(url)
            return tag
        abs_path = _resolve_local(html_dir, url)
        if not abs_path or not os.path.isfile(abs_path):
            missing.add(url)
            return tag
        try:
            nonlocal source_bytes
            source_bytes += os.path.getsize(abs_path)
            js = read_text_file(abs_path)
        except Exception as e:
            vlog(f'warn: script inline failed ({url}): {e}')
            missing.add(url)
            return tag
        keep = re.sub(r'\bsrc\s*=\s*([\'"])([^\'"]+)\1', '', tag,
                      flags=re.IGNORECASE)
        opener_m = re.match(r'<script\b[^>]*>', keep, re.IGNORECASE)
        opener = opener_m.group(0) if opener_m else '<script>'
        safe_js = js.replace('</script>', '<\\/script>')
        stats['js'] += 1
        return f'{opener}\n{safe_js}\n</script>'

    def inline_media_tag(tag: str, tag_name: str) -> str:
        def repl_attr(am):
            attr = am.group(1)
            quote = am.group(2)
            url = am.group(3)
            if _is_external(url):
                return am.group(0)
            abs_path = _resolve_local(html_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url)
                return am.group(0)
            if tag_name == 'iframe' and abs_path.lower().endswith(
                    ('.html', '.htm')):
                unmergeable.add(url)
                return am.group(0)
            uri = get_data_uri(abs_path)
            if uri is None:
                missing.add(url)
                return am.group(0)
            return f'{attr}={quote}{uri}{quote}'
        return re.sub(r'\b(src|href)\s*=\s*([\'"])([^\'"]+)\2',
                      repl_attr, tag)

    def process_anchor(tag: str) -> str:
        m = re.search(r'\bhref\s*=\s*([\'"])([^\'"]+)\1', tag, re.IGNORECASE)
        if not m:
            return tag
        url = m.group(2)
        if _is_external(url):
            return tag
        abs_path = _resolve_local(html_dir, url)
        if abs_path and os.path.isfile(abs_path):
            if abs_path.lower().endswith(('.html', '.htm')):
                unmergeable.add(url)
                return tag
            uri = get_data_uri(abs_path)
            if uri is None:
                missing.add(url)
                return tag
            return tag[:m.start()] + f'href="{uri}"' + tag[m.end():]
        elif abs_path:
            missing.add(url)
        return tag

    def process_media_tag(m):
        tag = m.group(0)
        name_m = re.match(r'<\s*([a-z]+)', tag, re.IGNORECASE)
        if not name_m:
            return tag
        name = name_m.group(1).lower()
        if name == 'a':
            return process_anchor(tag)
        return inline_media_tag(tag, name)

    def process_srcset(m):
        quote = m.group(1)
        value = m.group(2)
        parts = []
        for item in value.split(','):
            item = item.strip()
            if not item:
                continue
            toks = item.split()
            url = toks[0]
            rest = ' '.join(toks[1:])
            if _is_external(url):
                parts.append(item); continue
            abs_path = _resolve_local(html_dir, url)
            if not abs_path or not os.path.isfile(abs_path):
                missing.add(url); parts.append(item); continue
            uri = get_data_uri(abs_path)
            if uri is None:
                missing.add(url); parts.append(item); continue
            parts.append(uri + (' ' + rest if rest else ''))
        return f'srcset={quote}{", ".join(parts)}{quote}'

    html_dir = os.path.dirname(index_path)
    html = read_text_file(index_path)
    source_bytes += os.path.getsize(index_path)

    if re.search(r'<base\b', html, re.IGNORECASE):
        vlog('warn: <base> tag detected; relative resolution may be off')

    html = _LINK_RE.sub(repl_link, html)
    html = _SCRIPT_SRC_RE.sub(repl_script, html)
    html = _STYLE_BLOCK_RE.sub(
        lambda m: '<style>' + process_css_urls(m.group(1), html_dir) +
                  '</style>', html)
    html = _STYLE_ATTR_RE.sub(
        lambda m: (m.group(1) + process_css_urls(m.group(3), html_dir) +
                   m.group(2)), html)
    html = _MEDIA_TAG_RE.sub(process_media_tag, html)
    html = _SRCSET_RE.sub(process_srcset, html)

    vlog(f'merge: css={stats["css"]} js={stats["js"]} '
         f'media={stats["media"]} fonts={stats["fonts"]} '
         f'csv={stats["csv"]}, source {size_str(source_bytes)}')

    res = _pack_html_to_file(html, out_name, os.path.basename(out_name),
                             opts, vlog, source_bytes)
    res['file'] = dir_path
    res['merge'] = {
        'external': sorted(external),
        'unmergeable': sorted(unmergeable),
        'missing': sorted(missing),
        'stats': dict(stats),
    }
    return res


# ---------------------------------------------------------------------------
# Unpack
# ---------------------------------------------------------------------------
_JSON_BLOCK_RE = re.compile(
    r'<script type="application/json" id="__pk">([\s\S]*?)</script>'
)
_ATT_DATA_RE = re.compile(
    r'<script type="text/plain" id="__att_data">([\s\S]*?)</script>'
)
_HID_DATA_RE = re.compile(
    r'<script type="text/plain" id="__hid_data">([\s\S]*?)</script>'
)


def _parse_packed(path, vlog):
    sig = read_signature(path)
    if sig != 3:
        return None, None
    content = read_text_file(path)
    m = _JSON_BLOCK_RE.search(content)
    if not m:
        raise RuntimeError('pack metadata block not found')
    try:
        pk = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise RuntimeError('pack metadata invalid: ' + str(e))
    if pk.get('v') != 3:
        raise RuntimeError('unsupported pack version: ' + str(pk.get('v')))
    return pk, content


def unpack_file(file, opts, vlog, prompt_overwrite):
    base = os.path.basename(file)
    dirp = os.path.dirname(file)
    core = _strip_known_prefix(base, opts)
    out_name = _output_path(opts,
                            f'{_DEFAULT_UNPACK_PREFIX}{core}', dirp)

    if os.path.abspath(out_name) == os.path.abspath(file):
        raise RuntimeError('output would overwrite input')

    sig = read_signature(file)
    if sig is None:
        return {'file': file, 'status': 'skip-not-packed'}
    if sig != 3:
        return {'file': file, 'status': 'skip-unknown-version',
                'version': sig}

    if os.path.exists(out_name) and not opts['force']:
        if not callable(prompt_overwrite):
            raise RuntimeError(f'output exists: {out_name}  (use --force)')
        if not prompt_overwrite(out_name):
            return {'file': file, 'out': out_name,
                    'status': 'skip-no-overwrite'}

    content = read_text_file(file)
    m = _JSON_BLOCK_RE.search(content)
    if not m:
        raise RuntimeError('pack metadata block not found')
    pk = json.loads(m.group(1))

    key = None
    if pk.get('pe'):
        if not opts['password']:
            opts['password'] = prompt_hidden_password(
                f'Payload password for {base}: ')
            if not opts['password']:
                raise RuntimeError('payload password required')
        key = derive_key(opts['password'], pk['pe']['s'], pk['pe']['i'])
        try:
            check = aes_gcm_decrypt(key, pk['pe']['ivc'],
                                    base64.b64decode(pk['pe']['ck']))
            if check != PASSWORD_CHECK:
                raise RuntimeError('incorrect payload password')
        except Exception:
            raise RuntimeError('incorrect payload password')

    h = pk['h']
    html_cmp = z85_decode(h['z'], h['cs'])
    if key is not None:
        html_cmp = aes_gcm_decrypt(key, pk['pe']['iv1'], html_cmp)
    html_buf = maybe_decompress(html_cmp, pk['a'], h['c'])
    if len(html_buf) != h['os']:
        raise RuntimeError('decompressed size mismatch')
    html = html_buf.decode('utf-8')

    if pk.get('b') and pk['b'].get('cs', 0) > 0:
        bin_blob = z85_decode(pk['b']['z'], pk['b']['cs'])
        if key is not None:
            bin_blob = aes_gcm_decrypt(key, pk['pe']['iv2'], bin_blob)
        if pk['b'].get('c'):
            bin_blob = maybe_decompress(bin_blob, pk['a'], 1)
        for e in pk['m']:
            if e['p'] not in html:
                continue
            sl = bin_blob[e['o']:e['o'] + e['n']]
            uri = f'data:{e["t"]};base64,' + base64.b64encode(sl).decode('ascii')
            html = html.replace(e['p'], uri, 1)

    write_text_file(out_name, html)

    in_bytes = len(content.encode('utf-8'))
    out_bytes = len(html.encode('utf-8'))
    return {'file': file, 'out': out_name, 'status': 'ok',
            'inSize': in_bytes, 'outSize': out_bytes,
            'ratio': out_bytes / max(1, in_bytes),
            'restored': len(pk['m']), 'encrypted': key is not None}


# ---------------------------------------------------------------------------
# List / extract
# ---------------------------------------------------------------------------
def collect_input_files(inputs, recursive, vlog):
    files = []
    seen = set()
    all_skipped = []
    max_depth = float('inf') if recursive else 0
    for p in inputs:
        st = stat_or_none(p)
        if st is None:
            warn(f'no such file or directory: {p}')
            continue
        if os.path.isdir(p):
            batch, skipped = scan_dir(p, max_depth)
            all_skipped.extend(skipped)
        else:
            batch = [p]
        for f in batch:
            try:
                rp = os.path.realpath(f)
            except OSError:
                rp = os.path.abspath(f)
            if rp in seen:
                continue
            seen.add(rp)
            files.append(f)
    return files, all_skipped


def _list_unpacked_html(html):
    items = []
    seen = set()

    def add(url):
        if not url or url in seen:
            return
        seen.add(url)
        if url.lower().startswith('data:'):
            head = url[5:].split(',', 1)[0]
            mime = head.split(';', 1)[0].strip()
            items.append({
                'kind': 'data-uri',
                'name': f"data:{mime};base64,… ({len(url)} chars)",
                'mime': mime or 'application/octet-stream',
                'size': -1, 'category': media_category(mime),
                'source': 'media',
            })
        else:
            mime = _mime_from_url(url)
            items.append({
                'kind': 'reference', 'name': url, 'mime': mime,
                'size': -1, 'category': media_category(mime),
                'source': 'media',
            })

    for m in re.finditer(
            r'\b(?:src|href|poster|xlink:href)\s*=\s*([\'"])([^\'"]+)\1',
            html, re.IGNORECASE):
        add(m.group(2))
    for m in re.finditer(r'\bsrcset\s*=\s*([\'"])([^\'"]+)\1',
                         html, re.IGNORECASE):
        for item in m.group(2).split(','):
            item = item.strip()
            if item:
                add(item.split()[0])
    for m in re.finditer(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)',
                         html, re.IGNORECASE):
        add(m.group(2).strip())

    # CSV blocks
    for blk in _scan_csv_blocks(html):
        items.append({
            'kind': 'csv', 'name': blk['key'], 'mime': 'text/csv',
            'size': len(blk['body']), 'category': 'csv',
            'source': 'media',
        })

    return items


def _list_from_payload_html(html):
    """Return CSV items from a decompressed payload HTML string."""
    items = []
    for blk in _scan_csv_blocks(html):
        items.append({
            'kind': 'csv', 'name': blk['key'], 'mime': 'text/csv',
            'size': len(blk['body']), 'category': 'csv',
            'source': 'media',
        })
    return items


def _get_payload_html(pk, content, path, opts):
    """Return (html_string, key_or_None) by decoding the payload."""
    key = None
    if pk.get('pe'):
        if not opts['password']:
            opts['password'] = prompt_hidden_password(
                f"Payload password for {os.path.basename(path)}: ")
            if not opts['password']:
                raise RuntimeError('payload password required')
        key = derive_key(opts['password'], pk['pe']['s'], pk['pe']['i'])
        try:
            check = aes_gcm_decrypt(key, pk['pe']['ivc'],
                                    base64.b64decode(pk['pe']['ck']))
            if check != PASSWORD_CHECK:
                raise RuntimeError('incorrect payload password')
        except Exception:
            raise RuntimeError('incorrect payload password')
    h = pk['h']
    html_cmp = z85_decode(h['z'], h['cs'])
    if key is not None:
        html_cmp = aes_gcm_decrypt(key, pk['pe']['iv1'], html_cmp)
    html_buf = maybe_decompress(html_cmp, pk['a'], h['c'])
    if len(html_buf) != h['os']:
        raise RuntimeError('decompressed size mismatch')
    return html_buf.decode('utf-8'), key


def list_media(path, list_types, vlog):
    pk, content = _parse_packed(path, vlog)
    items = []

    if pk is None:
        if 'pack' in list_types and 'media' not in list_types:
            return []
        html = read_text_file(path)
        items = _list_unpacked_html(html)
    else:
        for e in pk.get('m', []):
            mime = e.get('t', '')
            items.append({
                'kind': 'data-uri', 'name': e.get('p', '?'),
                'mime': mime, 'size': int(e.get('n', 0)),
                'category': media_category(mime),
                'source': 'media',
            })
        att = pk.get('att')
        if att:
            for e in att.get('m', []):
                mime = e.get('t', '')
                items.append({
                    'kind': 'bundle', 'name': e.get('k', ''),
                    'mime': mime, 'size': int(e.get('n', 0)),
                    'category': media_category(mime),
                    'source': 'pack',
                })
        hid = pk.get('hid')
        if hid:
            for e in hid.get('m', []):
                name = e.get('k', '')
                mt, _ = mimetypes.guess_type(name)
                mt = mt or 'application/octet-stream'
                items.append({
                    'kind': 'hidden', 'name': name, 'mime': mt,
                    'size': int(e.get('n', 0)),
                    'category': media_category(mt),
                    'source': 'pack',
                })
        # CSV blocks live inside the payload HTML
        if 'csv' in list_types or 'all' in list_types:
            try:
                html, _key = _get_payload_html(pk, content, path, opts={})
                items.extend(_list_from_payload_html(html))
            except Exception as e:
                vlog(f'note: cannot inspect payload for CSV: {e}')

    items = [it for it in items if _item_matches_types(it, list_types)]
    order = {'media': 0, 'pack': 1}
    items.sort(key=lambda it: order.get(it['source'], 2))
    return items


def _print_media_items(items):
    if not items:
        out(f"      {C['dim']}(no matching media){C['reset']}")
        return
    name_w = max(6, min(64, max(len(it['name']) for it in items)))
    mime_w = max(6, min(28, max(len(it['mime']) for it in items)))
    kind_w = max(4, max(len(it['kind']) for it in items))
    out(f"      {'kind'.ljust(kind_w)}  {'name'.ljust(name_w)}  "
        f"{'type'.ljust(mime_w)}  {'size':>10}  category")
    out(f"      {C['gray']}{'-' * kind_w}  {'-' * name_w}  "
        f"{'-' * mime_w}  {'-' * 10}  {'-' * 8}{C['reset']}")
    for it in items:
        nm = it['name']
        if len(nm) > name_w:
            nm = nm[:name_w - 1] + '\u2026'
        sz = size_str(it['size']) if it['size'] >= 0 else '\u2014'
        out(f"      {it['kind'].ljust(kind_w)}  {nm.ljust(name_w)}  "
            f"{it['mime'].ljust(mime_w)}  {sz:>10}  {it['category']}")


def _print_listing(items):
    if not items:
        out(f"    {C['dim']}(no matching media){C['reset']}")
        return
    media_items = [it for it in items if it['source'] == 'media']
    pack_items = [it for it in items if it['source'] == 'pack']
    first = True
    if media_items:
        out(f"    {C['bold']}media{C['reset']} "
            f"{C['dim']}(from payload){C['reset']}")
        _print_media_items(media_items)
        first = False
    if pack_items:
        if not first:
            out('')
        out(f"    {C['bold']}pack{C['reset']} {C['dim']}(from -b / --bundle)"
            f"{C['reset']}")
        _print_media_items(pack_items)


def _handle_list(opts, vlog):
    files, skipped = collect_input_files(opts['inputs'], opts['recursive'],
                                         vlog)
    if skipped:
        vlog(f'{len(skipped)} subdirectory(ies) not scanned (pass -r)')
    if not files:
        die('no input files to process')

    total = 0
    types_str = ','.join(opts['list_types'])
    for f in files:
        label = os.path.basename(f)
        out('')
        out(f"  {C['bold']}{label}{C['reset']}  "
            f"{C['dim']}(types={types_str}){C['reset']}")
        try:
            items = list_media(f, opts['list_types'], vlog)
        except Exception as e:
            err(f'{label}: {e}')
            continue
        _print_listing(items)
        total += len(items)

    out('')
    out(f"  {C['gray']}{'-' * 76}{C['reset']}")
    out(f"  Listed {total} item(s) from {len(files)} file(s)")


def _write_extracted_file(path, data, vlog):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    vlog(f'extracted: {path} ({size_str(len(data))})')


def _decrypt_payload_or_die(pk, path, opts):
    if not pk.get('pe'):
        return None
    if not opts['password']:
        opts['password'] = prompt_hidden_password(
            f"Payload password for {os.path.basename(path)}: ")
        if not opts['password']:
            raise RuntimeError('payload password required')
    key = derive_key(opts['password'], pk['pe']['s'], pk['pe']['i'])
    try:
        check = aes_gcm_decrypt(key, pk['pe']['ivc'],
                                base64.b64decode(pk['pe']['ck']))
        if check != PASSWORD_CHECK:
            raise RuntimeError('incorrect payload password')
    except Exception:
        raise RuntimeError('incorrect payload password')
    return key


def _decrypt_bundle_or_die(pk, path, opts):
    if not pk.get('ae'):
        return None
    if not opts['bundle_password']:
        opts['bundle_password'] = prompt_hidden_password(
            f"Bundle password for {os.path.basename(path)}: ")
        if not opts['bundle_password']:
            raise RuntimeError('bundle password required')
    key = derive_key(opts['bundle_password'], pk['ae']['s'], pk['ae']['i'])
    try:
        check = aes_gcm_decrypt(key, pk['ae']['ivc'],
                                base64.b64decode(pk['ae']['ck']))
        if check != PASSWORD_CHECK:
            raise RuntimeError('incorrect bundle password')
    except Exception:
        raise RuntimeError('incorrect bundle password')
    return key


def _extract_csv_blocks(html, dest, list_types, vlog):
    if not ('csv' in list_types or 'all' in list_types):
        return 0
    count = 0
    for blk in _scan_csv_blocks(html):
        body = blk['body']
        if body and not body.endswith('\n'):
            body += '\n'
        name = blk['key'] + '.csv'
        _write_extracted_file(os.path.join(dest, name),
                              body.encode('utf-8'), vlog)
        count += 1
    return count


def _extract_from_unpacked(path, dest, list_types, vlog):
    html = read_text_file(path)
    base = os.path.splitext(os.path.basename(path))[0]
    count = 0
    for i, m in enumerate(DATA_URI_RE.finditer(html)):
        prefix = m.group(1)
        mime = prefix.replace(';base64', '')
        cat = media_category(mime)
        item = {'source': 'media', 'category': cat}
        if not _item_matches_types(item, list_types):
            continue
        raw = b64_decode_lenient(m.group(2))
        ext = _EXT_BY_MIME.get(mime, '.bin')
        name = f'{base}_datauri_{i:04d}{ext}'
        _write_extracted_file(os.path.join(dest, name), raw, vlog)
        count += 1
    count += _extract_csv_blocks(html, dest, list_types, vlog)
    return count


def _extract_from_packed(path, pk, content, dest, list_types, opts, vlog):
    base = os.path.splitext(os.path.basename(path))[0]
    count = 0

    def wanted(mime, source):
        return _item_matches_types(
            {'source': source, 'category': media_category(mime)},
            list_types)

    want_media = _item_matches_types(
        {'source': 'media', 'category': 'images'}, list_types)
    want_pack = _item_matches_types(
        {'source': 'pack', 'category': 'images'}, list_types)
    want_csv = 'csv' in list_types or 'all' in list_types

    payload_key = None

    if want_media and pk.get('b', {}).get('cs', 0) > 0:
        payload_key = _decrypt_payload_or_die(pk, path, opts)
        bin_blob = z85_decode(pk['b']['z'], pk['b']['cs'])
        if payload_key is not None:
            bin_blob = aes_gcm_decrypt(payload_key, pk['pe']['iv2'], bin_blob)
        if pk['b'].get('c'):
            bin_blob = maybe_decompress(bin_blob, pk['a'], 1)
        for i, e in enumerate(pk['m']):
            mime = e.get('t', '')
            if not wanted(mime, 'media'):
                continue
            data = bin_blob[e['o']:e['o'] + e['n']]
            ext = _EXT_BY_MIME.get(mime, '.bin')
            name = f'{base}_datauri_{i:04d}{ext}'
            _write_extracted_file(os.path.join(dest, name), data, vlog)
            count += 1

    if want_pack:
        att = pk.get('att')
        if att:
            att_m = _ATT_DATA_RE.search(content)
            if att_m:
                att_raw = z85_decode(att_m.group(1), att['cs'])
                if pk.get('pe'):
                    payload_key = payload_key or _decrypt_payload_or_die(
                        pk, path, opts)
                    att_raw = aes_gcm_decrypt(payload_key, pk['pe']['iv3'],
                                              att_raw)
                if att.get('c'):
                    att_raw = maybe_decompress(att_raw, pk['a'], 1)
                for e in att.get('m', []):
                    mime = e.get('t', '')
                    if not wanted(mime, 'pack'):
                        continue
                    data = att_raw[e['o']:e['o'] + e['n']]
                    name = e['k']
                    ext = _EXT_BY_MIME.get(mime, '')
                    if ext and not name.lower().endswith(ext.lower()):
                        name += ext
                    _write_extracted_file(os.path.join(dest, name),
                                          data, vlog)
                    count += 1
        hid = pk.get('hid')
        if hid:
            hid_m = _HID_DATA_RE.search(content)
            if hid_m:
                bundle_key = _decrypt_bundle_or_die(pk, path, opts)
                hid_raw = z85_decode(hid_m.group(1), hid['cs'])
                hid_raw = aes_gcm_decrypt(bundle_key, pk['ae']['ivh'],
                                          hid_raw)
                if hid.get('c'):
                    hid_raw = maybe_decompress(hid_raw, pk['a'], 1)
                for e in hid.get('m', []):
                    name = e['k']
                    mt, _ = mimetypes.guess_type(name)
                    mt = mt or 'application/octet-stream'
                    if not wanted(mt, 'pack'):
                        continue
                    data = hid_raw[e['o']:e['o'] + e['n']]
                    _write_extracted_file(os.path.join(dest, name),
                                          data, vlog)
                    count += 1

    if want_csv:
        try:
            html, _key = _get_payload_html(pk, content, path, opts)
            count += _extract_csv_blocks(html, dest, list_types, vlog)
        except Exception as e:
            vlog(f'note: cannot extract CSV from payload: {e}')

    return count


def _handle_extract(opts, vlog):
    files, skipped = collect_input_files(opts['inputs'], opts['recursive'],
                                         vlog)
    if skipped:
        vlog(f'{len(skipped)} subdirectory(ies) not scanned (pass -r)')
    if not files:
        die('no input files to process')

    dest_root = opts['output_dir'] or './extracted'
    _prepare_dir(dest_root, opts['force'], check_empty=True)

    single = (len(files) == 1)
    total = 0
    errors = 0
    for f in files:
        label = os.path.basename(f)
        dest = dest_root if single else os.path.join(
            dest_root, os.path.splitext(label)[0])
        try:
            pk, content = _parse_packed(f, vlog)
            if pk is None:
                if 'pack' in opts['extract_types'] and \
                        'media' not in opts['extract_types'] and \
                        'csv' not in opts['extract_types'] and \
                        'all' not in opts['extract_types']:
                    n = 0
                else:
                    n = _extract_from_unpacked(f, dest,
                                               opts['extract_types'], vlog)
            else:
                n = _extract_from_packed(f, pk, content, dest,
                                         opts['extract_types'], opts, vlog)
        except Exception as e:
            err(f'{label}: {e}')
            errors += 1
            continue
        out(f"  {C['green']}\u2713{C['reset']} {label.ljust(40)} "
            f"extracted {n} item(s)  {C['dim']}-> "
            f"{os.path.relpath(dest)}{C['reset']}")
        total += n

    out('')
    out(f"  {C['gray']}{'-' * 76}{C['reset']}")
    out(f"  Extracted {total} item(s) to "
        f"{C['cyan']}{dest_root}{C['reset']}")
    if errors:
        sys.exit(2)


# ---------------------------------------------------------------------------
# Overwrite prompter
# ---------------------------------------------------------------------------
def make_prompter(force: bool):
    if force or not sys.stdin.isatty():
        return None
    state = {'force_all': False}

    def prompt_overwrite(file):
        if state['force_all']:
            return True
        try:
            ans = input(f"overwrite {C['cyan']}{file}{C['reset']}? "
                        f"[y/N/a=all/q=quit] ").strip()
        except EOFError:
            return False
        if re.match(r'^a(ll)?$', ans, re.IGNORECASE):
            state['force_all'] = True
            return True
        if re.match(r'^q(uit)?$', ans, re.IGNORECASE):
            sys.exit(0)
        return bool(re.match(r'^y(es)?$', ans, re.IGNORECASE))

    return prompt_overwrite


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def status_line(idx, total, result):
    tag = f"[{str(idx).rjust(len(str(total)))}/{total}]"
    name = os.path.basename(result.get('out') or result['file'])
    name_pad = name[:31] + '...' if len(name) > 34 else name.ljust(34)

    if result['status'] == 'ok':
        grew = result['outSize'] > result['inSize']
        mark = f"{C['yellow']}!{C['reset']}" if grew else f"{C['green']}\u2713{C['reset']}"
        arrow = f"{C['red']}\u2191{C['reset']}" if grew else f"{C['green']}\u2193{C['reset']}"
        extras = []
        if result.get('encrypted'):
            extras.append(f"{C['cyan']}\U0001f512{C['reset']}")
        if result.get('attached'):
            extras.append(f"{C['blue']}\U0001f4ce{C['reset']}")
        if result.get('hidden'):
            extras.append(f"{C['yellow']}\U0001f576{C['reset']}")
        extra_s = (' ' + ' '.join(extras)) if extras else ''
        out(f"  {C['gray']}{tag}{C['reset']} {mark} {name_pad}"
            f"{size_str(result['inSize']).rjust(10)} {arrow} "
            f"{size_str(result['outSize']).rjust(10)}  "
            f"{C['dim']}({pct(result['outSize'], result['inSize'])}%)"
            f"{C['reset']}{extra_s}")
    elif result['status'] == 'skip-already-packed':
        out(f"  {C['gray']}{tag}{C['reset']} {C['yellow']}\u26a0{C['reset']} "
            f"{name_pad} {C['yellow']}skipped (already packed){C['reset']}")
    elif result['status'] == 'skip-not-packed':
        out(f"  {C['gray']}{tag}{C['reset']} {C['yellow']}\u26a0{C['reset']} "
            f"{name_pad} {C['yellow']}skipped (not an arcager file){C['reset']}")
    elif result['status'] == 'skip-unknown-version':
        out(f"  {C['gray']}{tag}{C['reset']} {C['yellow']}\u26a0{C['reset']} "
            f"{name_pad} {C['yellow']}skipped (format v"
            f"{result.get('version')}, this tool knows v3){C['reset']}")
    elif result['status'] == 'skip-no-overwrite':
        out(f"  {C['gray']}{tag}{C['reset']} {C['yellow']}\u26a0{C['reset']} "
            f"{name_pad} {C['yellow']}skipped (declined){C['reset']}")


def _print_merge_report(merge_info, file_label):
    ext = merge_info.get('external', [])
    unm = merge_info.get('unmergeable', [])
    mis = merge_info.get('missing', [])
    st = merge_info.get('stats', {})

    out('')
    out(f"  {C['gray']}{'-' * 76}{C['reset']}")
    out(f"  {C['bold']}Merge report{C['reset']} {C['dim']}\u00b7{C['reset']} "
        f"{os.path.basename(file_label)}")
    out('')
    out(f"    Inlined: {st.get('css', 0)} CSS \u00b7 {st.get('js', 0)} JS "
        f"\u00b7 {st.get('media', 0)} media \u00b7 {st.get('fonts', 0)} "
        f"fonts \u00b7 {st.get('csv', 0)} CSV \u00b7 1 HTML")

    def _bullets(title, items, colour, extra=''):
        if not items:
            return
        out('')
        out(f"  {colour}\u26a0{C['reset']}  {title}{extra}")
        shown = items[:8]
        for it in shown:
            out(f"      {C['dim']}{it}{C['reset']}")
        if len(items) > len(shown):
            out(f"      {C['dim']}\u2026 and {len(items) - len(shown)} more"
                f"{C['reset']}")

    _bullets("Local HTML page link(s) cannot be flattened into a single file:",
             unm, C['yellow'], '  (they will 404 in the packed output)')
    _bullets("Local reference(s) not found on disk:", mis, C['red'])
    _bullets("External link(s) kept as-is (require network at runtime):",
             ext, C['cyan'])


def summary(results, elapsed_ms, opts):
    ok = [r for r in results if r['status'] == 'ok']
    skip = [r for r in results if r['status'].startswith('skip')]
    fail = [r for r in results if r['status'] == 'error']
    in_total = sum(r['inSize'] for r in ok)
    out_total = sum(r['outSize'] for r in ok)

    for r in ok:
        if 'merge' in r:
            _print_merge_report(r['merge'], r['file'])

    out('')
    out(f"  {C['gray']}{'-' * 76}{C['reset']}")
    mode = ('merged' if opts.get('merge')
            else ('unpacked' if opts['unpack'] else 'packed'))
    out(f"  {C['bold']}Summary{C['reset']} {C['dim']}\u00b7{C['reset']} "
        f"{mode} {len(ok)} of {len(results)} file(s)")
    out('')

    rows = [('Files processed', str(len(ok)))]
    if skip:
        rows.append(('Files skipped', str(len(skip))))
    if fail:
        rows.append(('Files failed', f"{C['red']}{len(fail)}{C['reset']}"))

    if ok:
        if opts['unpack']:
            rows.append(('Packed input total', size_str(in_total)))
            rows.append(('Restored output total', size_str(out_total)))
            rows.append(('Expansion', pct(out_total, in_total) + '%'))
        else:
            label_in = ('Merge source total' if opts.get('merge')
                        else 'Input total')
            rows.append((label_in, size_str(in_total)))
            rows.append(('Output total', size_str(out_total)))
            r = (out_total / in_total) * 100 if in_total else 0
            cls = C['green'] if r < 100 else C['red']
            rows.append(('Reduction',
                         f"{cls}{(100 - r):.1f}%{C['reset']}   "
                         f"{C['dim']}({'saved' if in_total >= out_total else 'grew'} "
                         f"{size_str(abs(in_total - out_total))}){C['reset']}"))

    n_attached = sum(r.get('attached', 0) for r in ok)
    if n_attached:
        rows.append(('Visible bundles', str(n_attached)))
    n_hidden = sum(r.get('hidden_count', 0) for r in ok)
    if n_hidden:
        rows.append(('Hidden bundles', str(n_hidden)))

    rows.append(('Time', f'{elapsed_ms / 1000:.2f}s'))

    w = max(len(r[0]) for r in rows)
    for k, v in rows:
        out(f'    {k.ljust(w)}   {v}')

    if fail:
        out('')
        out(f"  {C['red']}Failed files:{C['reset']}")
        for f in fail:
            out(f"    {os.path.basename(f['file'])}  "
                f"{C['dim']}{f['error']}{C['reset']}")

    if not opts['unpack'] and ok:
        grown = [r for r in ok if r['outSize'] >= r['inSize']]
        if grown:
            out('')
            out(f"  {C['yellow']}\u26a0{C['reset']}  {len(grown)} file(s) "
                f"did not shrink. {C['dim']}Common causes: small inputs "
                f"(< 8 KB), or content dominated by already-compressed "
                f"media.{C['reset']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    opts = parse_args(sys.argv[1:])

    # ---- mode exclusivity --------------------------------------------
    modes = []
    if opts['list_mode']:
        modes.append('list')
    if opts['extract_mode']:
        modes.append('extract')
    if opts['unpack']:
        modes.append('unpack')
    if opts['merge']:
        modes.append('merge')
    if len(modes) > 1:
        die(f"modes are mutually exclusive; you gave: {', '.join(modes)}")

    if opts['output'] and opts['output_dir']:
        die('-o and -O are mutually exclusive')

    if opts['merge'] and opts['inputs']:
        die('--merge takes only a directory; do not pass positional inputs')

    # ---- list / extract modes ----------------------------------------
    if opts['list_mode'] or opts['extract_mode']:
        if not opts['inputs']:
            usage(); sys.exit(1)

        def vlog_early(*a):
            if opts['verbose']:
                sys.stderr.write(f"  {C['gray']}[v]{C['reset']} " +
                                 ' '.join(str(x) for x in a) + '\n')
        if opts['list_mode']:
            _handle_list(opts, vlog_early)
        else:
            _handle_extract(opts, vlog_early)
        return

    if not opts['inputs'] and not opts['merge']:
        usage(); sys.exit(1)
    if opts['output'] and len(opts['inputs']) > 1:
        die('-o is only allowed with a single input')

    def vlog(*a):
        if opts['verbose']:
            sys.stderr.write(f"  {C['gray']}[v]{C['reset']} " +
                             ' '.join(str(x) for x in a) + '\n')

    # ---- password acquisition ----------------------------------------
    hidden_paths = [a['path'] for a in opts['bundle'] if a['hidden']]

    if (opts['encrypt'] and not opts['unpack']
            and not opts['password']):
        opts['password'] = prompt_new_password('Payload password: ')

    if hidden_paths and not opts['unpack'] and not opts['bundle_password']:
        opts['bundle_password'] = prompt_new_password(
            'Bundle password (hidden payload): ')

    if opts['minify']:
        if opts['lossy']:
            warn("--minify lossy modifies the payload (comments, "
                 "whitespace, sourcemaps); files packed with it cannot be "
                 "restored byte-exact by -u.")
        else:
            warn("--minify runs Terser on the inline unpacker JS.  HTML "
                 "payload is unchanged; use '-m lossy' to also minify "
                 "the payload.")
    if opts['base_href']:
        warn("--base-href modifies the payload; files packed with it "
             "cannot be restored byte-exact by -u.")

    # ---- merge mode --------------------------------------------------
    if opts['merge']:
        prompter = make_prompter(opts['force'])
        enc_label = '  \u00b7 encrypted' if opts['encrypt'] else ''
        hidden_label = (f"  \u00b7 hidden="
                        f"{len(hidden_paths)}" if hidden_paths else '')
        vis_paths = [a['path'] for a in opts['bundle'] if not a['hidden']]
        vis_label = (f"  \u00b7 bundle={len(vis_paths)}"
                     if vis_paths else '')
        out(f"  {C['bold']}{C['cyan']}arcager {VERSION}{C['reset']}  "
            f"{C['dim']}\u00b7  merge  \u00b7  {opts['merge']}"
            f"  \u00b7 {'brotli' if opts['brotli'] else 'gzip'}"
            f"{'  \u00b7 minify' if opts['minify'] else ''}"
            f"{' (lossy)' if opts['lossy'] else ''}"
            f"{enc_label}{hidden_label}{vis_label}{C['reset']}")
        out('')
        t0 = time.time()
        results = []
        try:
            r = merge_directory(opts['merge'], opts, vlog, prompter)
            results.append(r)
            status_line(1, 1, r)
        except Exception as e:
            results.append({'file': opts['merge'], 'status': 'error',
                            'error': str(e)})
            out(f"  {C['gray']}[1/1]{C['reset']} {C['red']}\u2717{C['reset']} "
                f"{os.path.basename(opts['merge']).ljust(34)} "
                f"{C['red']}{e}{C['reset']}")
        summary(results, (time.time() - t0) * 1000, opts)
        if any(r['status'] == 'error' for r in results):
            sys.exit(2)
        if all(r['status'].startswith('skip') for r in results):
            sys.exit(3)
        return

    # ---- normal pack / unpack mode -----------------------------------
    files, all_skipped_dirs = collect_input_files(
        opts['inputs'], opts['recursive'], vlog)

    if all_skipped_dirs:
        sample = ', '.join(os.path.basename(d) for d in all_skipped_dirs[:3])
        tail = ', \u2026' if len(all_skipped_dirs) > 3 else ''
        vlog(f'{len(all_skipped_dirs)} subdirectory(ies) not scanned '
             f'(pass -r to recurse): {sample}{tail}')

    if not files:
        die('no input files to process')

    prompter = make_prompter(opts['force'])

    enc_label = '  \u00b7 encrypted' if opts['encrypt'] else ''
    hidden_label = (f"  \u00b7 hidden={len(hidden_paths)}"
                    if hidden_paths else '')
    vis_paths = [a['path'] for a in opts['bundle'] if not a['hidden']]
    vis_label = f"  \u00b7 bundle={len(vis_paths)}" if vis_paths else ''

    out(f"  {C['bold']}{C['cyan']}arcager {VERSION}{C['reset']}  "
        f"{C['dim']}\u00b7  {'unpack' if opts['unpack'] else 'pack'}  "
        f"\u00b7  {len(files)} file(s)"
        f"  \u00b7 {'brotli' if opts['brotli'] else 'gzip'}"
        f"{'  \u00b7 minify' if opts['minify'] else ''}"
        f"{' (lossy)' if opts['lossy'] else ''}"
        f"{enc_label}{hidden_label}{vis_label}{C['reset']}")
    out('')

    t0 = time.time()
    results = []

    for i, f in enumerate(files, 1):
        if opts['verbose']:
            sys.stderr.write(
                f"  {C['gray']}\u00b7\u00b7\u00b7 "
                f"{os.path.basename(f)}{C['reset']}\n")
        try:
            r = (unpack_file if opts['unpack'] else pack_file)(
                f, opts, vlog, prompter)
            results.append(r)
            status_line(i, len(files), r)
        except Exception as e:
            results.append({'file': f, 'status': 'error', 'error': str(e)})
            out(f"  {C['gray']}[{i}/{len(files)}]{C['reset']} "
                f"{C['red']}\u2717{C['reset']} "
                f"{os.path.basename(f).ljust(34)} {C['red']}{e}{C['reset']}")

    summary(results, (time.time() - t0) * 1000, opts)

    if any(r['status'] == 'error' for r in results):
        sys.exit(2)
    if all(r['status'].startswith('skip') for r in results):
        sys.exit(3)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write('\n')
        sys.exit(130)
    except Exception as e:
        err(str(e))
        sys.exit(1)