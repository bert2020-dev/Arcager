# Arcager

**Single-file HTML packer.** Bundle a page — or an entire static site — into one portable `.html` that unpacks itself in the browser. Optionally encrypt it, attach media, hide arbitrary files inside an encrypted payload, or inline tabular data.

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none%20required-brightgreen.svg)](#installation)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey.svg)](#installation)

---

`arcager` takes an HTML file (or a directory containing one) and produces a single self-contained `.html` file that contains:

- The original HTML, compressed with gzip or Brotli.
- Embedded `data:` URIs, hoisted into a separate binary blob so they don't inflate the page.
- A tiny inline JavaScript unpacker that reassembles everything at load time using the browser's built-in `DecompressionStream`.
- Optional **visible bundles** (images, fonts, audio, video, documents) exposed to the page as `arcager.images`.
- Optional **inlined CSV data** exposed as `arcager.csv`.
- Optional **hidden encrypted payloads** that the browser never touches.

The result is one file you can email, host, drop on a USB stick, or serve from anywhere. No server required.

**Version 3.2.0** changes from earlier releases:

- CSV/TSV inlining via `<link rel="csv" href="data.csv">` during `--merge`. At runtime: `window.arcager.csv['key'].rows` / `.data`.
- `-l csv` and `-x csv` scan and extract inlined CSV blocks.
- `-M` is a short option for `--merge`.
- `window.arcager` is always defined after load (empty when no bundles or CSV are present).
- Earlier 3.1 changes: `-b` / `--bundle` replaces `-a` / `--attach`; `--bundle-password` replaces `--attach-password`; `--prefix`; comma-separated type lists for `-l`/`-x`; multiple `-b` flags; minify no longer strips HTML comments by default (`-m lossy` for that); encrypted zip archives are refused for visible bundles.

---

## Table of Contents

- [Why arcager](#why-arcager)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Modes](#command-modes)
- [Bundles](#bundles)
- [CSV & Tabular Data](#csv--tabular-data)
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
| **Archive a static site** | Entire site — HTML, CSS, JS, fonts, images, CSV — in one file, ~20% of original size. |
| **Ship a demo** | A page with an image library or tabular data baked in, loadable offline. |
| **Exchange secret files** | A normal-looking HTML cover page with an encrypted payload only the recipient can open. |
| **Nest bundles** | Pack a packed file. Russian dolls all the way down. |

---

## Installation

**Requirements:** Python 3.8+. No third-party packages are required for basic pack / unpack / merge / list / extract.

Optional dependencies, only needed for specific features:

```bash
pip install cryptography      # for -E (payload encryption) and encrypted -u / -x
pip install brotli            # for --brotli compression
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

# Bundle a folder of images; <img src="flags/ge.png"> is auto-rewired
python arcager.py -b ./flags page.html

# Hide any file inside a normal-looking cover page
python arcager.py -b secret.zip hidden cover.html

# Merge a whole static site into one file
python arcager.py -M ./docs -m

# Inline tabular data (source has <link rel="csv" href="elements.csv">)
python arcager.py -M ./site
# runtime: arcager.csv['elements'].data

# Unpack back to HTML
python arcager.py -u packed_report.html

# Inspect a bundle (no password required for most listings)
python arcager.py -l packed_flags.html

# Extract hidden payloads
python arcager.py -x pack -O ./out packed_cover.html
```

---

## Command Modes

`arcager` has five modes. They are mutually exclusive.

| Mode | Command | Purpose |
|---|---|---|
| **pack** | `arcager [options] <input...>` | Pack HTML files into `packed_<name>.html` (or custom `--prefix`). |
| **unpack** | `arcager -u [options] <input...>` | Reverse a packed file back to HTML. Byte-exact for non-lossy packings. |
| **merge** | `arcager -M <dir>` or `--merge <dir>` | Flatten a whole directory (entry point `index.html`) into one file. Inlines CSS, JS, fonts, media, and CSV. |
| **list** | `arcager -l [TYPE,...] <input...>` | List media inside a packed or unpacked HTML file. Metadata listing needs no password. |
| **extract** | `arcager -x [TYPE,...] <input...> [-O DIR]` | Extract media into an output directory. Prompts for passwords as needed. |

### `-l` / `-x` types

| Type | What it matches |
|---|---|
| `all` | Everything (default) |
| `media` | Hoisted `data:` URIs from the payload HTML |
| `pack` | Bundles — visible *and* hidden |
| `csv` | Inlined CSV data blocks |
| `images` `videos` `audio` `fonts` `docs` | Filter by category (works across media + pack) |

Types can be comma-separated, e.g. `-l images,videos` or `-x pack,docs,csv`.

---

## Bundles

`-b PATH [hidden]` (or `--bundle`) adds files to the pack. It can be given multiple times. Visible and hidden bundles can be mixed in the same pack.

### Visible bundles — `-b PATH`

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
- **Data:** `csv tsv`

Total uncompressed cap: **128 MB**.

Encrypted zip archives are refused; decrypt them first, or use hidden bundling (`-b archive.zip hidden`).

</details>

### Runtime API

```javascript
await arcager.ready;

arcager.images['ge']            // data URL string
arcager.images['flags/ge']      // path-preserving key
arcager.getImage('ge')          // returns an HTMLImageElement
arcager.getImage('flags/ge')
```

Keys are the file's relative path with the extension stripped, using forward slashes. When a bare basename is unique across the whole bundle set, a short alias is added (so `'ge'` works when there is only one `ge.png` anywhere). Ambiguous aliases are dropped.

HTML/PDF/XHTML files inside an archive are exposed as `blob:` URLs so they can be loaded inside `<iframe>` elements.

When `-E` is used, visible bundles are encrypted with the payload password so the entire pack stays opaque.

### Hidden bundles — `-b PATH hidden`

The file (or folder) is stored as an opaque, encrypted blob the browser-side loader never touches. No runtime API. Extract with:

```bash
python arcager.py -x pack -O ./out packed_cover.html
```

Accepted inputs: **any** file, **any** folder (zipped in memory at pack time), or any of the same archives. No type restriction. Total uncompressed payload cap: **512 MB**.

Hidden bundles use their own password, independent from the payload password. If both `-E` and `-b ... hidden` are used, the script prompts twice — clearly labelled **"Payload password"** and **"Bundle password"**.

> [!TIP]
> Because hidden bundles are encrypted with a bundle password and can coexist with any pack output, they're useful for exchanging secret files between consenting parties: the cover page loads cleanly for anyone, and only those with the bundle password can extract the payload.

---

## CSV & Tabular Data

`arcager` can inline CSV files into the packed HTML during `--merge`, and expose them to your page as `arcager.csv` at runtime.

### How to use it

In your source HTML, reference the CSV just like a stylesheet:

```html
<link rel="csv" href="data/elements.csv">
```

During `--merge`, arcager reads the file, escapes any `</script>` in the content, and replaces the `<link>` with:

```html
<script type="text/csv" data-key="elements">
symbol,name,atomic_number
H,Hydrogen,1
He,Helium,2
...
</script>
```

The `data-key` is derived from the file's basename with any non-word characters replaced by underscores. So `data/elements.csv` becomes `data-key="elements"`.

At runtime, the blocks are parsed automatically on load:

```javascript
await arcager.ready;

arcager.csv['elements'].rows
// [['symbol','name','atomic_number'], ['H','Hydrogen','1'], ...]

arcager.csv['elements'].data
// [{symbol:'H', name:'Hydrogen', atomic_number:'1'}, ...]
```

- `.rows` is an array of arrays (raw, header row first).
- `.data` is an array of objects keyed by the first row's headers.

### Advantages over hard-coded data

A CSV file in a static site normally needs a `fetch()` at runtime, which fails for local files opened via `file://` and adds a network round-trip. Inlining removes both problems: the page is self-contained and the data is available the moment the page loads. You can keep the source CSV as an editable file in your repo — no build step required to change a row.

### Parser scope

The parser is RFC 4180-compliant for the common cases:

- Comma delimiter (fixed).
- Quoted fields (`"like this"`).
- Escaped quotes inside quoted fields (`"say ""hi"""`).
- Embedded commas and newlines inside quoted fields.
- CRLF and LF line endings.
- Optional trailing newline.
- Header row auto-detected from the first row.

It does **not** handle: delimiters other than comma, BOM stripping, type coercion (every field is a string), or streaming large files. Pre-process if you need those, or extract with `-x csv` and use a full library.

### Listing and extracting CSV

CSV blocks appear under the `csv` type and the `media` source:

```bash
python arcager.py -l csv packed_table.html
python arcager.py -x csv -O ./out packed_table.html
```

Each block is written to `<data-key>.csv` in the destination folder.

Listing CSV from a packed file requires decompressing the payload. If the file is encrypted, `-l csv` will prompt for the payload password (metadata-only listings like `-l images` do not).

---

## Encryption

```bash
# Payload encryption (browser prompts on load)
python arcager.py -E report.html

# Bundle encryption (extracted later with -x pack)
python arcager.py -b secret.zip hidden cover.html

# Both, with independent passwords
python arcager.py -E -b ./flags -b secret.zip hidden page.html
```

| Layer | Flag | What is encrypted | Who sees it |
|---|---|---|---|
| **Payload** | `-E` | HTML + hoisted data URIs | Browser prompts on load |
| **Bundle** | `-b PATH hidden` | Hidden blob only | Extracted offline with `-x pack` |

**Implementation:**
- AES-256-GCM authenticated encryption
- PBKDF2-SHA256, 600,000 iterations
- Separate salts and IVs for payload and bundle
- Wrong-password detection via an encrypted canary — clean *"Incorrect password."* message

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
- **Inlined CSV** is highly compressible and typically shrinks to 15–25% of original size.
- **Files under ~8 KB grow** because the shell is a fixed ~3–4 KB overhead. Use `-m` to trim.
- **Encryption** adds a few hundred bytes of metadata, no meaningful size change.

---

## Examples

### Deliver a password-protected report to a client

```bash
python arcager.py -E --brotli -m lossy \
    --base-href https://reports.example.com/ \
    q3_report.html
```

The client opens `packed_q3_report.html`, gets a password prompt, enters the password you shared out of band, and sees the report.

### Bundle a static site into one file

```bash
python arcager.py -M ./docs -m
```

Scans `./docs/`, finds `./docs/index.html`, inlines local CSS, JS, fonts, images and icons, and writes `packed_docs.html` alongside the `docs/` folder. Review the merge report to see what could not be flattened.

### Bundle a page with a folder of flags

```bash
python arcager.py -b ./flags page.html
```

Every image in `./flags` becomes available at runtime. A reference like `<img src="flags/ge.png">` is rewritten to the in-memory bundle.

```javascript
await arcager.ready;
const flag = arcager.images['ge'];      // data URL
const img  = arcager.getImage('ge');    // <img> element
document.body.appendChild(img);
```

### Inline CSV data for a periodic table

```bash
# Source layout:
#   site/
#     index.html
#     data/elements.csv
#
# index.html contains:
#   <link rel="csv" href="data/elements.csv">

python arcager.py -M ./site
```

```javascript
await arcager.ready;
const grid = document.getElementById('grid');
for (const el of arcager.csv['elements'].data) {
  grid.innerHTML += '<div>' + el.symbol + '</div>';
}
```

The CSV stays editable in your repo. Changing a row and re-running arcager.py repacks the page with the new data. No `fetch()`, no server, no build step.

### Hide arbitrary files inside a normal-looking page

```bash
python arcager.py -b secret.zip hidden -b documents/ hidden cover.html
```

Produces a plain HTML cover page. `secret.zip` and a zipped copy of `documents/` are encrypted inside the page with a bundle password you choose at prompt time.

```bash
# Recover them
python arcager.py -x pack -O ./out packed_cover.html
```

### Combine payload and bundle encryption

```bash
python arcager.py -E -b ./flags -b secret.zip hidden page.html
```

Three prompts: payload password (and confirm), then bundle password. The page is encrypted; `./flags` is encrypted and auto-loaded into `window.arcager.images` after the payload password is entered; `secret.zip` is encrypted separately and never loaded by the browser.

```bash
# Recover everything
python arcager.py -u --password PW packed_page.html
python arcager.py -x all -O ./out --password PW \
    --bundle-password BPW packed_page.html
```

### Inspect what a pack contains

```bash
python arcager.py -l packed_flags.html
python arcager.py -l images packed_flags.html
python arcager.py -l images,videos,fonts packed_media.html
python arcager.py -l csv packed_table.html
python arcager.py -l pack packed_cover.html
```

No password is needed for `-l` on unencrypted payloads. For encrypted payloads, `-l csv` prompts once because it must decompress the HTML to find the CSV blocks.

### Extract media or CSV for auditing or reuse

```bash
python arcager.py -x all -O ./out packed_flags.html
python arcager.py -x pack,images -O ./secret packed_cover.html
python arcager.py -x csv -O ./data packed_table.html
```

Files land under the chosen directory, one subfolder per input when extracting from multiple files.

### Batch-pack a directory tree

```bash
python arcager.py -r ./public --brotli -m lossy -f
```

Every `.html` under `./public/` (recursively) is packed into `packed_<name>.html` in the same folder.

### Custom output prefix

```bash
python arcager.py --prefix mypack_ page.html
# → mypack_page.html

python arcager.py --prefix mypack_ -M ./docs
# → mypack_docs.html
```

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
--prefix PREFIX         Prefix for pack/merge output names.
                        Defaults to 'packed_'. Also stripped during unpack.
-f, --force             Overwrite output without prompting.
-r, --recursive         Recurse into subdirectories when scanning.
```

### Compression
```
--brotli                Use Brotli instead of gzip (Chrome 105+ only).
-m, --minify [lossy]    Run Terser on the inline unpacker JS.
                        Add 'lossy' to ALSO strip HTML comments,
                        collapse inter-tag whitespace, and remove
                        sourceMappingURL comments from the payload.
                        Lossy.
--ignore-uris LIST      Comma-separated MIME prefixes to KEEP as data:
                        URIs rather than hoist. Example:
                            --ignore-uris image/svg+xml,image/gif
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
--bundle-password PW    Non-interactive bundle password (for
                        hidden bundles).
```

### Bundles
```
-b, --bundle PATH [hidden]
                        Bundle media (or, with 'hidden', any file).
                        Can be given multiple times. Visible and
                        hidden bundles can be mixed freely.
```

### Unpack
```
-u, --unpack            Reverse a packed file back to HTML.
```

### Merge
```
-M, --merge DIR         Bundle a whole directory into one file.
                        Inlines CSS, JS, fonts, media, and CSV.
                        <link rel="csv" href="data.csv"> becomes a
                        <script type="text/csv"> block exposed at
                        runtime as arcager.csv.
```

### List / Extract
```
-l, --list [TYPE,...]          List media. TYPE can be a comma-
                               separated list, e.g. images,videos.
-x, --extract [TYPE,...]       Extract into an output directory.
                               TYPE as for -l.

TYPE: all | media | pack | csv | images | videos | audio | fonts | docs
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
- Individual media files larger than 64 MB are skipped.
- Symlinks are followed but not normalized; cyclic symlinks can cause repeated reads.

</details>

<details>
<summary><strong>CSV data</strong></summary>

- Comma delimiter only. TSV files are accepted by the merge scanner but currently parsed with the comma delimiter (producing a single-column result); convert to CSV first.
- Header row is always the first row.
- All fields are strings (no type coercion).
- No BOM stripping, alternate encodings, or streaming of very large files.

</details>

<details>
<summary><strong>Bundles</strong></summary>

- Visible bundles: **128 MB** total uncompressed cap.
- Hidden payloads: **512 MB** total uncompressed cap.
- Keys must use letters, digits, `_`, `-`, `.`, `/`, space, `@`, `+`. Anything else aborts the pack with a clear error naming the offending file.
- Encrypted zip archives are detected and refused with a hint to use hidden bundling.

</details>

<details>
<summary><strong>Format & size</strong></summary>

- Packed files begin with the sentinel `<!--arcager:3-->`. Files without this sentinel are refused by `-u`.
- Version 3 is not compatible with earlier 1.x or 2.x bundles. Repack older files with the current tool.
- The whole payload is held in memory during pack and unpack.

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
| Merge report lists "missing" entries | Run with `-v` to see the resolved path. Common causes: case mismatch, URL-encoded characters, dynamically-built URLs, or a `<base>` tag. Also includes missing CSV files referenced by `<link rel="csv">`. |
| CSV data is empty in the browser | Check that the `<link rel="csv">` was inside the entry `index.html`, the data-key matches the basename, the merge report did not list it under "missing", and you awaited `arcager.ready`. |
| CSV parses as a single column | Source is likely tab-separated. Convert to comma-separated first. |
| `output would overwrite input` | `-o` points at the input file. Choose a different output path. |
| `hidden bundles require a bundle password` | Pass `--bundle-password` or run interactively. |
| Encrypted zip archive encountered | Decrypt it first, or bundle the archive as-is with: `-b archive.zip hidden` |
| Colored output renders as escape codes in Windows cmd | Use Windows Terminal, or set `NO_COLOR=1`. |
| Packed file is larger than input | Small or media-dominated inputs grow. Use `-m`, or skip packing and use a plain archiver for storage. |

---

## License

Released under the **[PolyForm Noncommercial License 1.0.0](LICENSE.txt)**.

In short: you may use, modify, and distribute this software for **any noncommercial purpose**, provided you preserve the copyright notice and license text. **Commercial use requires a separate license** from the author.

Third-party libraries used at runtime (`cryptography`, `brotli`, `terser`) keep their own licenses. None are linked into packed output — the packed file is pure HTML, CSS, and JavaScript that runs entirely in the browser.

---

## Credits

`arcager` is a Python port of the original `arcager.js` by the same author. Only the Python version is actively maintained at the moment.

**See also:** [`LICENSE.txt`](LICENSE.txt)
