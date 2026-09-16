# arcager

**Single-file HTML packer.** Bundle a page — or an entire static site — into one portable `.html` that unpacks itself in the browser. Optionally encrypt it, attach media, or hide arbitrary files inside an encrypted payload.

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none%20required-brightgreen.svg)](#installation)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey.svg)](#installation)

---

`arcager` takes an HTML file (or a directory containing one) and produces a single self-contained `.html` file that contains:

- The original HTML, compressed with gzip or Brotli.
- Embedded `data:` URIs, hoisted into a separate binary blob so they don't inflate the page.
- A tiny inline JavaScript unpacker that reassembles everything at load time using the browser's built-in `DecompressionStream`.
- Optional **visible attachments** (images, fonts, audio, video, documents) exposed to the page as `arcager.images`.
- Optional **hidden encrypted payloads** that the browser never touches.

The result is one file you can email, host, drop on a USB stick, or serve from anywhere. No server required.

> [!NOTE]
> On-disk format is compatible with the original `arcager.js`. Files packed by one can be unpacked by the other.

---

## Table of Contents

- [Why arcager](#why-arcager)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Modes](#command-modes)
- [Attachments](#attachments)
- [Encryption](#encryption)
- [Compression](#compression)
- [Examples](#examples)
- [Flag Reference](#flag-reference)
- [Browser Support](#browser-support)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Why arcager

| Use case | What arcager gives you |
|---|---|
| **Deliver a report to a client** | One `.html` file, optionally password-protected. No assets to lose. |
| **Archive a static site** | Entire site — HTML, CSS, JS, fonts, images — in one file, ~20% of original size. |
| **Ship a demo** | A page with an image library baked in, loadable offline. |
| **Exchange secret files** | A normal-looking HTML cover page with an encrypted payload only the recipient can open. |
| **Nest bundles** | Pack a packed file. Russian dolls all the way down. |

---

## Installation

**Requirements:** Python 3.8+. No third-party packages are required for basic pack / unpack / merge / list / extract.

Optional dependencies, only needed for specific features:

```bash
pip install cryptography      # for -E (payload encryption) and encrypted -u / -x
pip install brotli            # for --brotli
npm install -g terser         # for -m / --minify (JS squeeze only)
```

**Install the script:**

```bash
# Run in place
python arcager.py --help

# Or symlink on Unix
chmod +x arcager.py
sudo ln -s "$PWD/arcager.py" /usr/local/bin/arcager
arcager --help
```

On Windows: rename to `arcager.py`, add its folder to `PATH`, then invoke with `python arcager.py`.

---

## Quick Start

```bash
# Pack a page → packed_report.html
python arcager.py report.html

# Brotli + full minify pass
python arcager.py --brotli -m lossy report.html

# Encrypt with a password (browser prompts before unpacking)
python arcager.py -E report.html

# Attach a folder of images; <img src="flags/ge.png"> is auto-rewired
python arcager.py -a ./flags page.html

# Hide any file inside a normal-looking cover page
python arcager.py -a secret.zip hidden cover.html

# Merge a whole static site into one file
python arcager.py --merge ./docs --brotli -m

# Unpack back to HTML
python arcager.py -u packed_report.html

# Inspect a bundle (no password required)
python arcager.py -l packed_flags.html

# Extract hidden payloads
python arcager.py -x pack -O ./out packed_cover.html
```

---

## Command Modes

`arcager` has five modes. They are mutually exclusive.

| Mode | Command | Purpose |
|---|---|---|
| **pack** | `arcager [options] <input...>` | Pack HTML files into `packed_<name>.html`. |
| **unpack** | `arcager -u [options] <input...>` | Reverse a packed file back to HTML. Byte-exact for non-lossy packings. |
| **merge** | `arcager --merge <dir> [options]` | Flatten a whole directory (entry point `index.html`) into one file. |
| **list** | `arcager -l [TYPE] <input...>` | List media inside a packed or unpacked HTML file. No password needed. |
| **extract** | `arcager -x [TYPE] <input...> [-O DIR]` | Extract media into an output directory. Prompts for passwords as needed. |

### `-l` / `-x` types

| Type | What it matches |
|---|---|
| `all` | Everything (default) |
| `media` | Hoisted `data:` URIs from the payload HTML |
| `pack` | Attachments — visible *and* hidden |
| `images` `videos` `audio` `fonts` `docs` | Filter by category (works across media + pack) |

---

## Attachments

`-a PATH [hidden]` adds files to the bundle. Can be given multiple times. Visible and hidden attachments can be mixed in the same pack.

### Visible attachments — `-a PATH`

Media is decompressed in the browser and exposed to your page as `arcager.images`. Your HTML's `src`/`href`/`poster`/`srcset`/CSS `url(...)` references are auto-rewired at load time.

<details>
<summary><strong>Accepted inputs and file types</strong></summary>

**Inputs:**
- A folder of media (scanned recursively)
- A `.zip` / `.tar` / `.tar.gz` / `.tgz` / `.tbz2` / `.txz` archive of media
- A single media file

**File types:**
- **Images:** `png jpg jpeg webp gif svg bmp ico avif`
- **Fonts:** `woff woff2 ttf otf eot`
- **Audio:** `mp3 ogg oga wav m4a aac flac opus`
- **Video:** `mp4 m4v webm ogv mov`
- **Documents:** `html htm xhtml pdf eps txt md markdown`

Total uncompressed cap: **128 MB**.

</details>

### Runtime API

```javascript
await arcager.ready;

arcager.images['ge']            // data URL string
arcager.images['flags/ge']      // path-preserving key
arcager.getImage('ge')          // returns an HTMLImageElement
arcager.getImage('flags/ge')
```

Keys are the file's relative path with the extension stripped, using forward slashes. When a bare basename is unique across the whole attachment set, a short alias is added (so `'ge'` works when there is only one `ge.png` anywhere). Ambiguous aliases are dropped.

HTML/PDF/XHTML files inside an archive are exposed as `blob:` URLs so they can be loaded inside `<iframe>` elements.

When `-E` is used, visible attachments are encrypted with the payload password so the entire bundle stays opaque.

### Hidden attachments — `-a PATH hidden`

The file (or folder) is stored as an opaque, encrypted blob the browser-side loader never touches. No runtime API. Extract with:

```bash
python arcager.py -x pack -O ./out packed_cover.html
```

Accepted inputs: **any** file, **any** folder (zipped in memory at pack time), or any of the same archives. Total uncompressed cap: **512 MB**.

Hidden attachments use their own password, independent from the payload password. If both `-E` and `-a ... hidden` are used, the script prompts twice — clearly labelled **"Payload password"** and **"Attachment password"**.

> [!TIP]
> Because hidden attachments are encrypted with an attachment password and can coexist with any pack output, they're useful for exchanging secret files between consenting parties: the cover page loads cleanly for anyone, and only those with the attachment password can extract the payload.

---

## Encryption

```bash
# Payload encryption (browser prompts on load)
python arcager.py -E report.html

# Attachment encryption (extracted later with -x pack)
python arcager.py -a secret.zip hidden cover.html

# Both, with independent passwords
python arcager.py -E -a ./flags -a secret.zip hidden page.html
```

| Layer | Flag | What is encrypted | Who sees it |
|---|---|---|---|
| **Payload** | `-E` | HTML + hoisted data URIs | Browser prompts on load |
| **Attachment** | `-a PATH hidden` | Hidden blob only | Extracted offline with `-x pack` |

**Implementation:**
- AES-256-GCM authenticated encryption
- PBKDF2-SHA256, 600,000 iterations
- Separate salts and IVs for payload and attachment
- Wrong-password detection via an encrypted canary — clean *"Incorrect password."* message, never a decryption crash

> [!IMPORTANT]
> Encrypted payloads require a **secure context**: `https://`, `file://`, or `localhost`. `crypto.subtle` is not exposed on plain `http://` origins.

---

## Compression

Already-compressed files (PNG, JPEG, MP4, ZIP, 7z, WOFF2, PDF, DOCX, …) are **not** re-compressed. The packer only stores the compressed form when it is actually smaller. Folders zipped on the fly use `ZIP_STORED` so the outer compressor sees the raw contents.

**Typical ratios (gzip level 9):**

| Content type | Input | Packed | Ratio |
|---|---|---|---|
| Text-heavy article / docs | 100 KB | 30–40 KB | 30–40% |
| Bootstrap / jQuery landing | 400 KB | 90–120 KB | 22–30% |
| SPA bundle + media | 2 MB | 900 KB–1.2 MB | 45–60% |
| Media-dominated | 5 MB | 4.5–4.9 MB | 90–98% |
| Tiny page, no media | 4 KB | 6–8 KB | 150%+ |

- **Brotli (quality 11)** typically saves another 10–20% on text but requires Chrome 105+.
- **Hoisting `data:` URIs** into a binary blob is worth 30–50% on pages with many embedded images.
- **Files under ~8 KB grow** because the shell is a fixed ~3–4 KB overhead. Use `-m` to trim.
- **Encryption** adds a few hundred bytes of metadata, no meaningful size change.

---

## Examples

### Deliver a password-protected report to a client

```bash
python arcager.py -E --brotli -m \
    --base-href https://reports.example.com/ \
    q3_report.html
```

The client opens `packed_q3_report.html`, gets a password prompt, enters the password you shared out of band, and sees the report.

### Bundle a static site into one file

```bash
python arcager.py --merge ./docs --brotli -m
```

Scans `./docs/`, finds `./docs/index.html`, inlines local CSS, JS, fonts, images and icons, and writes `packed_docs.html` alongside the `docs/` folder. Review the merge report to see what could not be flattened.

### Bundle a page with a folder of flags

```bash
python arcager.py -a ./flags page.html
```

Every image in `./flags` becomes available at runtime. A reference like `<img src="flags/ge.png">` in `page.html` is rewritten to the in-memory attachment.

```javascript
await arcager.ready;
const flag = arcager.images['ge'];      // data URL
const img  = arcager.getImage('ge');    // <img> element
document.body.appendChild(img);
```

### Hide arbitrary files inside a normal-looking page

```bash
python arcager.py -a secret.zip hidden -a documents/ hidden cover.html
```

Produces a plain HTML cover page. `secret.zip` and a zipped copy of `documents/` are encrypted inside the page with an attachment password you choose at prompt time.

```bash
# Recover them
python arcager.py -x pack -O ./out packed_cover.html
```

### Inspect what a bundle contains

```bash
python arcager.py -l packed_flags.html
python arcager.py -l images packed_flags.html
python arcager.py -l pack packed_cover.html
```

No password needed — metadata is unencrypted.

### Batch-pack a directory tree

```bash
python arcager.py -r ./public --brotli -m -f
```

Every `.html` under `./public/` (recursively) is packed into `packed_<name>.html` in the same folder.

### Keep small vector assets inline

```bash
python arcager.py --ignore-uris image/svg+xml,image/gif page.html
```

Large base64 PNGs and JPEGs are hoisted into the binary blob. Tiny SVGs and GIFs stay as `data:` URIs where they are cheap and avoid an extra decode step at load time.

---

## Flag Reference

### Inputs
```
<input...>              Files, directories, or shell globs.
```

### Output
```
-o, --output PATH       Exact output file (single input, or --merge).
-O, --output-dir DIR    Output directory for any mode. Auto-created.
                        For -x, this is the extraction destination.
-f, --force             Overwrite output without prompting.
-r, --recursive         Recurse into subdirectories when scanning.
```

### Compression
```
--brotli                Use Brotli instead of gzip (Chrome 105+).
-m, --minify [lossy]    Minify the unpacker JS and strip HTML comments.
                        Add 'lossy' to also collapse inter-tag whitespace
                        and remove sourceMappingURL comments. Lossy.
--ignore-uris LIST      Comma-separated MIME prefixes to KEEP as data:
                        URIs rather than hoist.
```

### Payload modification (lossy)
```
--base-href URL         Inject <base href="URL">.
-m lossy                See --minify above.
```

### Encryption
```
-E, --encrypt           Encrypt the HTML payload.
--password PW           Non-interactive payload password.
--attach-password PW    Non-interactive attachment password.
```

### Attachments
```
-a, --attach PATH [hidden]
                        Attach media (or, with 'hidden', any file).
```

### Unpack
```
-u, --unpack            Reverse a packed file back to HTML.
```

### Merge
```
--merge DIR             Bundle a whole directory into one file.
```

### List / Extract
```
-l, --list [TYPE]              List media.
-x, --extract [TYPE]           Extract into an output directory.
```

### Misc
```
-v, --verbose           Per-stage diagnostics on stderr.
-V, --version           Print version.
-h, --help              Full help text.
```

---

## Browser Support

| Feature | Chrome | Firefox | Safari | Edge |
|---|---|---|---|---|
| gzip unpack | 80+ | 113+ | 16.4+ | 80+ |
| Brotli unpack | 105+ | 115+ | 16.4+ | 105+ |
| AES-GCM decrypt\* | 60+ | 57+ | 11+ | 79+ |
| `file://` context | ✅ | ✅ | ✅ | ✅ |

\* Requires a secure context (`https://`, `file://`, or `localhost`).

---

## Limitations

<details>
<summary><strong>Browser / runtime</strong></summary>

- Requires `DecompressionStream` (Chrome 80+, Firefox 113+, Safari 16.4+). Brotli additionally requires Chrome 105+ / Firefox 115+.
- Encrypted payloads need a secure context. Plain `http://` has no `crypto.subtle`.
- The unpacker is inline JavaScript. Under a CSP that forbids `'unsafe-inline'` scripts (without a matching nonce/hash), the shell will not run. Opening the file locally is the usual workaround.
- No support for JavaScript-disabled browsers.

</details>

<details>
<summary><strong>Merge mode</strong></summary>

- Only the entry `index.html` is bundled. Additional HTML pages referenced by `<a href>` or `<iframe src>` are not merged.
- No JavaScript module resolution. `<script src>` files are inlined verbatim; bare `import` specifiers will fail. Pre-bundle with esbuild / rollup / webpack first.
- No HTML template processing (SSI, PHP, Jinja, etc.).
- A `<base>` tag in the source produces a warning; relative URL resolution may be wrong.
- Runtime-built URLs (`img.src = 'pic' + n + '.png'`) cannot be resolved.
- CSS inside JS strings (styled-components, CSS-in-JS) is not scanned.
- Individual media files larger than 64 MB are skipped (`_MERGE_MAX_INLINE_BYTES` in source).
- Symlinks are followed but not normalized; cyclic symlinks can cause repeated reads.

</details>

<details>
<summary><strong>Attachments</strong></summary>

- Visible attachments: **128 MB** total uncompressed cap.
- Hidden payloads: **512 MB** total uncompressed cap.
- Keys must use letters, digits, `_`, `-`, `.`, `/`, space, `@`, `+`. Anything else aborts the pack with a clear error naming the offending file.

</details>

<details>
<summary><strong>Format</strong></summary>

- Packed files begin with the sentinel `<!--arcager:3-->`. Files without this sentinel are refused by `-u` with *"not a arcager file"*.
- Version 3 is not compatible with earlier 1.x or 2.x bundles. Repack older files with the current tool.

</details>

<details>
<summary><strong>Size</strong></summary>

The whole payload is held in memory during pack and unpack.

</details>

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DecompressionStream is not supported by this browser` | Browser is too old. Use modern Chrome / Firefox / Safari, or repack without `--brotli`. |
| `Brotli is not supported by this browser` | Repack without `--brotli`. Requires Chrome 105+ / Firefox 115+ / Safari 16.4+. |
| `Cannot unpack this file` / CSP warning | Page is under a restrictive Content-Security-Policy. Open it locally, serve from a permissive host, or ask the publisher for a non-bundled version. |
| `Encrypted bundles require a secure context` | Serve over `https://` or open locally. Plain `http://` has no `crypto.subtle`. |
| `Incorrect password.` | Passwords are case-sensitive and whitespace-significant. |
| `requires the 'cryptography' package` | `pip install cryptography` |
| `--brotli requires the 'brotli' package` | `pip install brotli` |
| `terser not found on PATH` | `npm install -g terser`. Packing still works without it; only the JS squeeze is skipped. |
| Merge report lists "unmergeable" entries | These are `<a href>` or `<iframe src>` pointing at other local `.html` files, which cannot be flattened into a single document. |
| Merge report lists "missing" entries | Run with `-v` to see the resolved path. Common causes: case mismatch, URL-encoded characters, dynamically-built URLs, or a `<base>` tag. |
| `output would overwrite input` | `-o` points at the input file. Choose a different output path. |
| `hidden attachments require an attachment password` | Pass `--attach-password` or run interactively. |
| Colored output renders as escape codes in Windows cmd | Use Windows Terminal, or set `NO_COLOR=1`. |
| Packed file is larger than input | Small or media-dominated inputs grow. Use `-m`, or skip packing and use a plain archiver for storage. |

---

## License

Released under the **[PolyForm Noncommercial License 1.0.0](LICENSE.txt)**.

In short: you may use, modify, and distribute this software for **any noncommercial purpose**, provided you preserve the copyright notice and license text. **Commercial use requires a separate license** from the author.

Third-party libraries used at runtime (`cryptography`, `brotli`, `terser`) keep their own licenses. None are linked into packed output — the packed file is pure HTML, CSS, and JavaScript that runs entirely in the browser.

---

## Credits

`arcager` is a Python port of the original `arcager.js` by the same author. Both implementations produce the same on-disk format; use whichever fits your stack.

**See also:** [`LICENSE.txt`](LICENSE.txt)