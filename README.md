# Arcager 4.0

**One HTML file. A real packaging layer behind it.**

Arcager turns an HTML document or a static site into a **single self-unpacking HTML file**. It packages HTML, CSS, JavaScript, images, fonts, data, documents, and optional bundles into one portable artifact while keeping the original representations intact by default.

> **Give it a site. It figures out how to package it efficiently.**

Arcager 4.x is the **Solid Update**: more capable than 3.x, but deliberately simpler to use. The complicated decisions happen inside the packager instead of becoming a wall of CLI flags.

[![License](https://img.shields.io/badge/license-see%20LICENSE.txt-blue.svg)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Core dependencies](https://img.shields.io/badge/core%20dependencies-none-brightgreen.svg)](#installation)
[![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](#cross-platform)

## Why use Arcager?

A normal static site is a collection of files that must stay together: HTML points to CSS, CSS points to fonts and images, JavaScript loads data, and so on. Arcager turns that collection into **one file that can be copied, archived, emailed, published, or opened offline as a unit**.

That is useful for:

- **Offline websites and web apps** — distribute a static site as one `.html` file instead of a folder tree.
- **Scientific and technical material** — keep reports, figures, fonts, CSV/TSV datasets, JSON/XML data, and supporting documents together.
- **Demos and interactive examples** — package CSS/JS-heavy applications without asking the recipient to install a web stack.
- **Archival and reproducibility** — exact resources remain preserved by default, while the container records the relationships and integrity information needed to recover them.
- **Data-rich static sites** — repeated resources can be stored once, while text/data streams are compressed together.
- **Controlled distribution** — optionally protect the complete v4 container with AES-256-GCM, or attach a separate opaque encrypted hidden bundle.

### What 4.x brings together

| Goal | Arcager 4.0 |
|---|---|
| **One-file distribution** | Static sites become self-unpacking HTML. |
| **Preservation** | Original representations are kept by default. |
| **Efficient storage** | Exact duplicates share physical storage; text/data are compressed in MIME-oriented streams. |
| **Safe optimization** | Representation-changing media conversion requires explicit `lossy`; a conversion is kept only when it validates and wins on size. |
| **Privacy cleanup** | `--strip-metadata` is explicit and independent from `lossy`. |
| **Integrity** | v4 metadata and streams are checked; encrypted containers use authenticated AES-GCM. |
| **Bundles** | Visible assets or separate opaque hidden payloads can travel with the document. |
| **Data** | CSV/TSV, JSON, XML and other text/data resources participate in the same container. |
| **Simple runtime API** | `window.arcager.ready` and `window.arcager.error`, plus the existing image/CSV conveniences. |
| **Portability** | Python-side packaging targets Linux, macOS and Windows; the output is intended to run in a browser without external Arcager files. |

The core philosophy is simple:

> **Preserve by default. Change representation only when the command explicitly permits it.**

## Quick start

You can start with the normal preservation path without installing third-party Python packages:

```bash
# Pack one HTML document; GZIP is the default.
python arcager.py page.html

# Merge a static-site directory.
python arcager.py -M ./site

# Prefer higher-density Brotli compression.
python arcager.py -c brotli page.html

# Permit representation-changing media optimization.
python arcager.py -c gzip lossy page.html

# Explicitly remove non-essential metadata where safe.
python arcager.py --strip-metadata page.html

# Protect the complete package; Arcager prompts for the password.
python arcager.py -E page.html

# Add a visible bundle.
python arcager.py -b ./assets page.html

# Add an opaque encrypted hidden bundle; Arcager prompts for its password.
python arcager.py -b secret.zip hidden cover.html

# Restore the root HTML from a package.
python arcager.py -u packed_page.html

# Inspect or extract resources.
python arcager.py -l all packed_page.html
python arcager.py -x all -O ./extracted packed_page.html
```

For automation, `--password` and `--bundle-password` are available. Avoid putting passwords on the command line when shell history or process arguments are a concern.

Run `python arcager.py --help` for the complete CLI contract.

## See the numbers

Arcager ships **24 real benchmark projects** under [`benchmarks/fixtures/`](benchmarks/fixtures/), not synthetic descriptions. They cover image-heavy pages, CSS/JavaScript-heavy applications, a Tetris-style game, scientific data, 12 CSV files, exact duplicates, fonts, recursive sites, Unicode paths, high-entropy data, an opaque `.7z` payload, and a larger mixed workload.

The benchmark measures final package size, compression ratio, raw-resource and stream accounting, encode/decode throughput, and preservation. The bundled 3.2.3 implementation is a fixed historical baseline for comparison; 4.x does not need a second development branch.

### Representative v4 results

These are measurements from the v6 validation environment (Linux, Python 3.13.5). They are examples, not universal performance guarantees; run the shipped corpus on your own machine for local numbers.

| Workload | Source | Final HTML | Ratio | Change |
|---|---:|---:|---:|---:|
| PNG gallery | 8.09 MiB | 10.13 MiB | 0.80× | +25.2% |
| Tetris-style game | 1.38 MiB | 0.24 MiB | **5.70×** | **−82.5%** |
| 7z + 12 CSV datasets | 0.58 MiB | 0.66 MiB | 0.88× | +13.9% |
| Scientific JSON/XML/CSV | 4.15 MiB | 0.57 MiB | **7.31×** | **−86.3%** |
| Duplicate-rich site | 5.38 MiB | 0.28 MiB | **19.04×** | **−94.7%** |
| Font-heavy site | 1.77 MiB | 1.15 MiB | **1.54×** | **−35.0%** |

The PNG result is useful rather than embarrassing: already-compressed binary media can make a single HTML file larger because of serialization overhead. Arcager therefore **does not pretend that every input should shrink**. Solid compression is rejected when it does not pay for itself, and preservation mode does not rewrite the PNG merely to improve a benchmark number.

Run the corpus yourself:

```bash
# Verify every shipped fixture file.
python benchmarks/examples.py --verify

# Compare all 24 workloads with the bundled 3.2.3 baseline.
python benchmarks/run_benchmarks.py --cases all

# Run only one representative case.
python benchmarks/run_benchmarks.py --cases 09-tetris-game
```

See [`benchmarks/EXAMPLES.md`](benchmarks/EXAMPLES.md) for the scenario catalog and [`benchmarks/README.md`](benchmarks/README.md) for the accounting model.

## What makes the Solid Update different?

### Exact deduplication

Arcager identifies exact final-byte duplicates and stores one physical representation while preserving all of the logical paths that refer to it. The identity includes the final MIME representation, so different representations are not silently conflated.

Near-duplicates, different resolutions, crops, and perceptually similar media are intentionally **not** merged in 4.0.

### Solid streams that know when to stop

Resources are grouped into MIME-oriented streams. GZIP or Brotli is attempted where appropriate, but compression is retained only when its serialized cost beats the stored form. Already-compressed media can therefore remain raw instead of being pointlessly recompressed.

### Dependency-aware packaging

Merge mode builds a conservative static dependency graph. Directly referenced resources receive higher priority, while unreferenced local resources remain included as lower-priority fallbacks rather than being silently deleted.

External dependencies are reported as warnings. Missing local dependencies are errors.

### Preservation first, optimization second

The default path does not reinterpret media. `lossy` merely grants permission to try selected representation-changing conversions; an accepted candidate must validate and be smaller. Despite the CLI name, an accepted WebP candidate can itself be content-lossless.

`--strip-metadata` is a separate, explicit privacy operation. It is never implied by `lossy`.

## What can it package?

The generic v4 container can hold:

- HTML/XHTML
- CSS and JavaScript
- JSON, XML and TXT
- CSV/TSV
- SVG
- PNG, JPEG, WebP, GIF, BMP, ICO and AVIF
- WOFF/WOFF2/TTF/OTF/EOT fonts
- PDF/EPS
- common audio/video formats
- ZIP and other archive-backed bundle content

Many of these are deliberately kept opaque. Arcager does not need to understand the internals of every format to store, deduplicate, stream, integrity-check, list and extract it safely.

## Media optimization

Default packing preserves the source representation.

With `lossy`, the current media conversion candidates are:

```text
PNG  → lossless WebP candidate
JPEG → lossless WebP candidate
BMP  → lossless WebP candidate
GIF  → lossless WebP candidate, including an animation-aware path
```

A candidate is kept only if it validates and is smaller than the current representation. Failed or unsupported conversions fall back to the original.

Animated GIFs are handled separately from ordinary static-image conversion so animation cannot silently collapse to a single frame.

## Bundles and data

### Bundles

`-b PATH` adds a visible bundle that the packed page can access. `-b PATH hidden` stores a separate opaque encrypted payload that the main browser runtime does not interpret.

Current safety limits are:

- visible bundles: **128 MiB total uncompressed**;
- hidden bundles: **512 MiB total uncompressed**;
- merge media: **64 MiB per resource**.

An already-packed Arcager document is not recursively packed as an ordinary visible bundle in 4.0; it is skipped with a warning. Explicit repacking/reoptimization is future work.

### CSV / TSV

CSV/TSV can participate in the generic container and the existing merge workflow. A merged CSV reference can become an inline data block and is exposed through `window.arcager.csv` after the runtime is ready.

## Runtime API

The browser API is intentionally small.

### Readiness and errors

```javascript
try {
    await window.arcager.ready;
    console.log("Arcager package is ready");
} catch (error) {
    console.error(window.arcager.error || error);
}
```

- `window.arcager.ready` — Promise that resolves after the package has been unpacked and the runtime has prepared its resources.
- `window.arcager.error` — `null` on success; an `Error` object when initialization fails.

The old v3 `window.arcager.state` and `window.arcager.loaded` signals are removed in v4.

### Images

```javascript
await window.arcager.ready;

const image = window.arcager.getImage("logo.png");
if (image) document.body.appendChild(image);

const url = window.arcager.images["logo.png"];
```

`getImage()` returns an `Image` element with the packaged resource as its source, or `null` when the requested alias is unavailable. `arcager.images` contains convenient aliases for packaged image resources and their common path/stem forms.

### CSV

```javascript
await window.arcager.ready;

const table = window.arcager.csv.elements;
console.log(table.rows);
console.log(table.data);
```

CSV entries expose `rows` as arrays and `data` as header-keyed objects. The key comes from the merged CSV block's `data-key` when present, otherwise Arcager assigns one.

## Encryption and integrity

The complete serialized v4 container is constructed and compressed before it is protected as one **AES-256-GCM** ciphertext. Password-derived keys use PBKDF2-SHA256.

Hidden bundles have a separate password boundary and remain opaque to the main runtime.

The container also carries structural/integrity checks for its header, metadata and streams. Corruption should produce an explicit failure instead of silently yielding altered resources.

Encryption is a protection mechanism for the packaged data; once authorized plaintext has been rendered in a browser, Arcager does not claim to make that plaintext impossible to inspect or copy.

## Listing and extraction

Useful resource categories include:

```text
all, images, videos, audio, fonts, docs, text, csv, vector, pack, media
```

Examples:

```bash
python arcager.py -l images,fonts packed_site.html
python arcager.py -x docs,csv -O ./extract packed_site.html
```

If a resource was deliberately converted, extraction uses the **stored representation**. A resource stored as WebP is therefore extracted as WebP rather than pretending the original PNG/JPEG bytes still exist.

## Installation

Installation is intentionally boring because most users should not need to think about it before deciding whether Arcager is useful.

### Core

The preservation path has **no mandatory third-party Python dependency**.

Python **3.8+** is the intended compatibility baseline:

```bash
python arcager.py --help
```

### Optional capabilities

```bash
python -m pip install -r requirements-optional.txt
npm install -g terser     # optional JavaScript minification
```

Optional packages provide capabilities such as AES-GCM, Brotli, image conversion/metadata handling and Terser-based minification. If an optional component is unavailable, the preservation path remains usable and unsupported optional transformations are skipped.

Use the included diagnostic when an environment behaves unexpectedly:

```bash
python scripts/doctor.py
```

It reports the exact interpreter and optional modules Arcager can actually import. This is especially useful on Windows, where the Microsoft Store `python.exe` alias, another Python installation, and an activated virtual environment can be different interpreters. Arcager never silently installs packages.

## Cross-platform

Arcager uses normalized forward-slash logical paths while using the host platform's filesystem APIs for actual files. The repository's CI matrix covers Linux, macOS and Windows with Python 3.10 and 3.13.

A manually triggered Playwright workflow covers Chromium, Firefox and WebKit. The local release validation environment is Linux, so macOS/Windows/browser execution is not claimed as locally executed merely because CI definitions exist.

## Testing and benchmarks

The repository contains:

```text
tests/                 automated regression tests
benchmarks/fixtures/   actual 24-case benchmark corpus
benchmarks/            reproducible benchmark harness and accounting
docs/ARCHITECTURE.md   v4 design notes
docs/TESTING.md        validation strategy
.github/workflows/     cross-platform CI
```

Current v6 local validation:

```text
25 standard Solid tests passed
27 benchmark-corpus tests passed
```

Run them with:

```bash
python -m pytest -q tests/test_solid.py
python -m pytest -q -m benchmark tests/test_benchmark_corpus.py
```

The benchmark report is evidence, not marketing copy. It separates raw resource bytes, stream payload, Z85 overhead, HTML shell overhead and final package size so an already-compressed resource is not misrepresented as a failed compression operation.

## Important limitations

Arcager 4.0 is a **static-site packager, not a general-purpose web crawler or JavaScript bundler**. It does not promise:

- arbitrary server-side template evaluation;
- complete JavaScript dependency resolution;
- complete discovery of runtime-generated URLs;
- automatic remote-resource downloading;
- automatic flattening of multiple HTML documents;
- perceptual/near-duplicate media detection;
- automatic internal optimization of ZIP/7z/DOCX/XLSX/PPTX packages;
- dedicated SVG/vector optimization beyond ordinary text handling.

External resources are warned about rather than silently downloaded. If a local dependency cannot be found, packaging fails instead of producing a misleadingly incomplete standalone document.

Future work is tracked in [`TODO.md`](TODO.md).

## v4 compatibility

Arcager 4.0 is a clean container-format break:

```html
<!--arcager:4-->
```

v4 does not attempt to unpack v3 containers. An existing Arcager package supplied as a normal input is skipped rather than recursively repacked. Explicit repacking/reoptimization is intentionally a future feature.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the v4 container works.
- [`docs/TESTING.md`](docs/TESTING.md) — regression, corruption, browser and benchmark strategy.
- [`benchmarks/EXAMPLES.md`](benchmarks/EXAMPLES.md) — all 24 benchmark scenarios.
- [`benchmarks/README.md`](benchmarks/README.md) — benchmark accounting and reproducibility.
- [`TODO.md`](TODO.md) — deliberate future work and format boundaries.

## License

See [`LICENSE.txt`](LICENSE.txt).
