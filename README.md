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

**Version 3.2.3** (current):

- `window.arcager` now exposes a small state machine so applications can inspect readiness without awaiting a Promise:
  - `arcager.state` — `'loading' | 'ready' | 'error'`
  - `arcager.error` — `Error` object when `state === 'error'`, else `null`
  - `arcager.loaded` — boolean, `true` iff `state === 'ready'`
  - `arcager.ready` — Promise that resolves when state becomes `'ready'` and rejects when state becomes `'error'`
- `state` is set to `'ready'` in the same statement that resolves `arcager.ready` and populates `arcager.images` / `arcager.csv`, so any of the three signals is authoritative.
- Applications that prefer polling can do so without awaiting.

**Earlier 3.2.x highlights:**

- **3.2.2** — `window.arcager` and `window.arcager.ready` are defined *before* any capability check. Unsupported browsers or insecure contexts reject the Promise with a clear `Error` instead of leaving `arcager` undefined.
- **3.2.1** — `arcager.ready` is a real pending Promise that resolves only after the payload HTML has been decompressed **and** every inlined `<script type="text/csv">` block has been parsed. CSV blocks are scanned from the decompressed payload (not the shell document).
- **3.2.0** — CSV/TSV inlining via `<link rel="csv" href="data.csv">` during `--merge`; `-l csv` / `-x csv`; `-M` short option for `--merge`.

On-disk format is compatible across 3.2.0–3.2.3. Files packed by 3.2.0 unpack correctly but expose an empty `arcager.csv` at runtime; repack with 3.2.3 to fix that.

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
- [Compatibility](#compatibility)
- [License](#license)

---

## Why arcager

| Use case | What arcager gives you |
|---|---|
| **Deliver a report to a client** | One `.html` file, optionally password-protected. No assets to lose. |
| **Archive a static site** | Entire site — HTML, CSS, JS, fonts, images, CSV — in one file, often ~20–40% of original size. |
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
# runtime: await arcager.ready; arcager.csv['elements'].data

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

Media is decompressed in the browser and exposed to your page as `arcager.images`. Your HTML's `src` / `href` / `poster` / `srcset` / CSS `url(...)` references are auto-rewired at load time.

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

### The ready contract (3.2.3)

`window.arcager` is defined synchronously by the inline unpacker, before any capability check or asynchronous work. Three signals are exposed, and they cannot disagree because they are set in the same statement:

| Signal | Type | Meaning |
|---|---|---|
| `arcager.ready` | Promise | Resolves once the payload HTML has been decompressed and every inlined `<script type="text/csv">` block has been parsed into `arcager.csv[key].{rows,data}`. Rejects if the pack fails to load. |
| `arcager.state` | `'loading' \| 'ready' \| 'error'` | Synchronous snapshot. `'ready'` is set at the same instant the Promise resolves. |
| `arcager.loaded` | boolean | Convenience mirror of `state === 'ready'`. |
| `arcager.error` | `Error \| null` | Set when `state === 'error'`. |

**Recommended usage:**

```javascript
try {
  await arcager.ready;
  // arcager.state === 'ready' and arcager.loaded === true here
  const rows = arcager.csv['elements'].rows;
} catch (e) {
  console.error('arcager failed to load:', e);
}
```

**Polling usage (no await):**

```javascript
switch (arcager.state) {
  case 'ready':  useData(arcager.csv); break;
  case 'error':  console.error(arcager.error); break;
  default:       /* 'loading' — try again later */ break;
}
```

Rejection reasons: unsupported browser (no `DecompressionStream`), missing secure context for an encrypted payload, incorrect password, or a decompression error. The same message is shown in the loader panel.

Reading `arcager.csv[...]` or `arcager.images[...]` while `state` is `'loading'` returns empty objects. The Promise resolves *before* the payload is committed to the document, so scripts inside the payload itself can also `await arcager.ready` and get fully populated data.

### Advantages over hard-coded data

A CSV file in a static site normally needs a `fetch()` at runtime, which fails for local files opened via `file://` and adds a network round-trip. Inlining removes both problems: the page is self-contained and the data is available the moment the page loads. You can keep the source CSV as an editable file in your repo — no build step required to change a row.

### Parser scope

The parser is RFC 4180-compliant for the common cases:

- Comma delimiter (default; per-block override via `data-delim`).
- Quoted fields (`"like this"`).
- Escaped quotes inside quoted fields (`"say ""hi"""`).
- Embedded commas and newlines inside quoted fields.
- CRLF and LF line endings.
- Optional trailing newline.
- Header row auto-detected from the first row.

It does **not** handle: BOM stripping, type coercion (every field is a string), or streaming large files. Pre-process if you need those, or extract with `-x csv` and use a full library.

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

- **Brotli (quality 11)** typically saves another 10–20% on text but requires Chrome 105+ / Firefox 115+ / Safari 16.4+.
- Hoisting `data:` URIs into a binary blob is worth 30–50% on pages with many embedded images.
- Inlined CSV is highly compressible and typically shrinks to 15–25% of its original size under gzip.
- Files under ~8 KB usually grow because the shell is a fixed ~3–4 KB overhead. Use `-m` to trim.
- Encryption adds a few hundred bytes of metadata and no meaningful change to the payload size.

---

## Examples

### 1. Password-protected report for a client

```bash
python arcager.py -E --brotli -m lossy \
    --base-href https://reports.example.com/ \
    q3_report.html
```

Produces `packed_q3_report.html`. Email it. The client opens it, gets a password prompt, enters the password you shared out of band, and sees the report.

### 2. Bundle a static site into one file

```bash
python arcager.py -M ./docs -m
```

Scans `./docs/`, finds `./docs/index.html`, inlines local CSS, JS, fonts, images and icons, and writes `packed_docs.html`. Review the merge report: it lists external URLs, unresolvable local links, and other HTML pages it could not flatten.

### 3. Bundle a page with a folder of flags

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

### 4. Inline CSV data for a periodic table

```bash
# site/index.html contains: <link rel="csv" href="data/elements.csv">
python arcager.py -M ./site
```

```javascript
await arcager.ready;
const grid = document.getElementById('grid');
for (const el of arcager.csv['elements'].data) {
  grid.innerHTML += '<div>' + el.symbol + '</div>';
}
```

### 5. Hide arbitrary files inside a normal-looking page

```bash
python arcager.py -b secret.zip hidden -b documents/ hidden cover.html
```

Produces a plain HTML cover page. `secret.zip` and a zipped copy of `documents/` are encrypted inside the page. Recover them later:

```bash
pip install cryptography
python arcager.py -x pack -O ./out packed_cover.html
```

### 6. Combine payload and bundle encryption

```bash
python arcager.py -E -b ./flags -b secret.zip hidden page.html
```

Three prompts (payload password ×2, then bundle password). Recover everything:

```bash
python arcager.py -u --password PW packed_page.html
python arcager.py -x all -O ./out --password PW --bundle-password BPW packed_page.html
```

### 7. Inspect what a pack contains

```bash
python arcager.py -l packed_flags.html
python arcager.py -l images,videos,fonts packed_flags.html
python arcager.py -l csv packed_table.html
python arcager.py -l pack packed_cover.html
```

### 8. Extract media or CSV

```bash
python arcager.py -x all -O ./out packed_flags.html
python arcager.py -x pack,images -O ./secret packed_cover.html
python arcager.py -x csv -O ./data packed_table.html
```

### 9. Custom output prefix

```bash
python arcager.py --prefix bundle_ report.html   # → bundle_report.html
python arcager.py --prefix bundle_ -M ./docs     # → bundle_docs.html
python arcager.py -u --prefix bundle_ bundle_report.html
```

### 10. Batch-pack a directory tree

```bash
python arcager.py -r ./public --brotli -m lossy -f
```

### 11. Keep small vector assets inline

```bash
python arcager.py --ignore-uris image/svg+xml,image/gif page.html
```

### 12. Poll `arcager.state` instead of awaiting

```javascript
function renderWhenReady() {
  if (arcager.state === 'error') {
    console.error('arcager failed to load:', arcager.error);
    return;
  }
  if (arcager.state !== 'ready') {
    setTimeout(renderWhenReady, 50);
    return;
  }
  const grid = document.getElementById('grid');
  for (const el of arcager.csv['elements'].data) {
    grid.innerHTML += '<div>' + el.symbol + '</div>';
  }
}
renderWhenReady();
```

---

## Flag Reference

| Flag | Description |
|---|---|
| **Inputs** | |
| `<input...>` | Files, directories, or shell globs. |
| **Output** | |
| `-o, --output PATH` | Exact output file (single input, or `--merge`). |
| `-O, --output-dir DIR` | Output directory for any mode. Auto-created. For `-x`, extraction destination. |
| `--prefix PREFIX` | Prefix for pack/merge output names. Default: `packed_`. Also stripped during unpack. |
| `-f, --force` | Overwrite output without prompting. |
| `-r, --recursive` | Recurse into subdirectories when scanning. |
| **Compression** | |
| `--brotli` | Use Brotli instead of gzip (Chrome 105+). |
| `-m, --minify [lossy]` | Run Terser on the inline unpacker JS. Add `lossy` to also strip HTML comments, collapse inter-tag whitespace, and remove `sourceMappingURL` comments from the payload. |
| `--ignore-uris LIST` | Comma-separated MIME prefixes to keep as `data:` URIs rather than hoist. |
| **Payload modification (lossy)** | |
| `--base-href URL` | Inject `<base href="URL">`. |
| **Encryption** | |
| `-E, --encrypt` | Encrypt the HTML payload. |
| `--password PW` | Non-interactive payload password. |
| `--bundle-password PW` | Non-interactive bundle password. |
| **Bundles** | |
| `-b, --bundle PATH [hidden]` | Bundle media (or, with `hidden`, any file). May be given multiple times. |
| **Modes** | |
| `-u, --unpack` | Reverse a packed file back to HTML. |
| `-M, --merge DIR` | Bundle a whole directory into one file. |
| `-l, --list [TYPE,...]` | List media. |
| `-x, --extract [TYPE,...]` | Extract into an output directory. |
| **Misc** | |
| `-v, --verbose` | Per-stage diagnostics on stderr. |
| `-V, --version` | Print version. |
| `-h, --help` | Full help text. |

---

## Browser Support

| Feature | Chrome | Firefox | Safari | Edge |
|---|---|---|---|---|
| gzip unpack | 80+ | 113+ | 16.4+ | 80+ |
| Brotli unpack | 105+ | 115+ | 16.4+ | 105+ |
| AES-GCM decrypt* | 60+ | 57+ | 11+ | 79+ |
| `file://` context | yes | yes | yes | yes |

\* Requires a secure context (`https://`, `file://`, or `localhost`).

---

## Limitations

**Browser / runtime**
- Requires `DecompressionStream` (Chrome 80+, Firefox 113+, Safari 16.4+). Brotli additionally needs Chrome 105+ / Firefox 115+.
- Encrypted payloads need a secure context. Plain `http://` has no `crypto.subtle`.
- The unpacker is inline JS. Under a Content-Security-Policy that forbids `'unsafe-inline'` scripts (without a matching nonce/hash), the shell will not run. Opening the file locally is the usual workaround.
- No support for JavaScript-disabled browsers.

**Compression**
- Media-dominated inputs may not shrink and can grow slightly.
- Very small inputs (< 8 KB) grow. Use `-m`.

**Lossy modes**
- `-m lossy` and `--base-href` modify the payload before compression. Files packed with either cannot be restored byte-exact by `-u`.
- `-m` without `lossy` only runs Terser on the unpacker JS, leaving the payload HTML untouched.

**Merge mode**
- Only the entry `index.html` is bundled. Additional HTML pages referenced by `<a href>` or `<iframe src>` are not merged.
- No JavaScript module resolution. `<script src>` files are inlined verbatim; bare `import` specifiers will fail. Pre-bundle with esbuild / rollup / webpack first.
- No HTML template processing (SSI, PHP, Jinja, etc.).
- A `<base>` tag in the source produces a warning; relative URL resolution may be wrong.
- Runtime-built URLs (e.g. `img.src = 'pic' + n + '.png'`) cannot be resolved.
- CSS inside JS strings (styled-components, CSS-in-JS) is not scanned.
- Individual media files larger than 64 MB are skipped.
- Symlinks are followed but not normalized; cyclic symlinks can cause repeated reads.

**CSV data**
- Comma delimiter by default. TSV files are accepted by the merge scanner but parsed with the comma delimiter unless the emitting block carries `data-delim="\t"`.
- The header row is always the first row.
- All fields are strings. Numbers, booleans, and dates are not coerced.
- No support for BOM, alternate encodings, or streaming very large files.

**Bundles**
- Visible bundles: 128 MB total uncompressed cap.
- Hidden bundles: 512 MB total uncompressed cap.
- Keys must use letters, digits, `_`, `-`, `.`, `/`, space, `@`, `+`. Anything else aborts the pack.
- Password-protected zip archives are refused when bundled as visible media. Use `-b FILE hidden` to bundle them as-is.

**Size / format**
- The whole payload is held in memory during pack and unpack.
- Packed files begin with the sentinel `<!--arcager:3-->`. Files without this sentinel are refused by `-u`.
- This version is not compatible with earlier 1.x or 2.x bundles.
- 3.2.3 is on-disk compatible with 3.2.2, 3.2.1, and 3.2.0.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DecompressionStream is not supported by this browser.` | Browser too old. Use modern Chrome / Firefox / Safari, or repack without `--brotli`. |
| `Brotli is not supported by this browser.` | Requires Chrome 105+ / Firefox 115+ / Safari 16.4+. Repack without `--brotli`. |
| CSP warning / page does not unpack | Content-Security-Policy blocks inline scripts. Open locally (`file://`) or serve from a permissive host. |
| `Encrypted bundles require a secure context.` | Serve over `https://` or open locally. Plain `http://` has no `crypto.subtle`. |
| `Incorrect password.` | Passwords are case-sensitive and whitespace-significant. |
| `requires the 'cryptography' package` | `pip install cryptography` |
| `--brotli requires the 'brotli' package` | `pip install brotli` |
| `terser not found on PATH` | `npm install -g terser`. Packing still works without it. |
| `zip entry ... is encrypted` | Decrypt the archive first, or bundle it as-is with `-b FILE hidden`. |
| Merge report lists "unmergeable" entries | Other local `.html` pages cannot be flattened into a single document. |
| Merge report lists "missing" entries | A local reference could not be found on disk. Run with `-v` to see the resolved path. |
| CSV data is empty in the browser | Await `arcager.ready` (or check `arcager.state`). Ensure the `<link rel="csv">` was in the entry `index.html`. Files packed by 3.2.0 always have empty `arcager.csv` — repack with 3.2.3. |
| CSV parses as a single column | Source is likely TSV. Convert to comma-separated, or set `data-delim="\t"` on the block. |
| `output would overwrite input` | Choose a different output path. |
| `output exists: ... (use --force)` | Confirm with `y`, pass `-f`, or delete the stale file. |
| Colored output renders as escape codes in Windows cmd | Use Windows Terminal, or set `NO_COLOR=1`. |
| Packed file is larger than the input | Small inputs and media-dominated inputs grow. Use `-m` to shrink the shell. |

---

## Compatibility

This version writes the sentinel `<!--arcager:3-->`. It is **not** compatible with earlier 1.x or 2.x bundles; repack older files with the current tool if you need to change their contents.

3.2.3 is on-disk compatible with 3.2.2, 3.2.1, and 3.2.0. The only behavioral additions are `arcager.state` and `arcager.error`; the `ready` Promise and `arcager.loaded` behave exactly as in 3.2.2. `window.arcager` and `window.arcager.ready` continue to be defined before any capability check, so applications can always:

```javascript
try { await arcager.ready; } catch (e) { ... }
```

and never encounter `ReferenceError: arcager is not defined`.

---

## License

arcager is released under the [PolyForm Noncommercial License 1.0.0](LICENSE.txt).

In short: you may use, modify, and distribute this software for any noncommercial purpose, provided you preserve the copyright notice and license text. Commercial use requires a separate license from the author.

arcager is a Python port of the original `arcager.js` by the same author. Only the Python version is actively maintained at the moment.

Third-party libraries used at runtime (`cryptography`, `brotli`, `terser`) keep their own licenses. None are linked into packed output — the packed file is pure HTML, CSS, and JavaScript that runs entirely in the browser.

---

**Author:** Bert Coder  
**Last updated:** 2026-11-18 (v3.2.3)  
**Repository:** https://github.com/bert2020-dev/Arcager
