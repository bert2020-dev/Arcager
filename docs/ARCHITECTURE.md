# Arcager 4.2 architecture

Arcager 4.x is built around one practical goal: **turn a collection of related web resources into one reliable HTML artifact without making the user manage the internal packaging decisions**.

The architecture deliberately favors correctness, integrity, semantic preservation, reliable extraction and explainable decisions before encoder speed or micro-optimizations.

## 1. The resource model

Every source reference becomes a **logical resource**. A logical resource records information such as:

- logical path/reference used by the source document;
- source MIME and final MIME;
- priority;
- flags describing representation changes and other state;
- the physical resource that supplies its final bytes.

A **physical resource** is one actual final byte representation. Exact duplicates can therefore share storage while their logical paths remain independent.

The representation pipeline is:

```text
source / merge graph
        ↓
logical resources
        ↓
optional representation conversion
        ↓
final representation
        ↓
exact content identity / deduplication
        ↓
logical → physical mapping
        ↓
dependency priority
        ↓
MIME-oriented stream assignment
        ↓
per-stream compression decision
        ↓
binary v4 metadata
        ↓
optional AES-256-GCM encryption
        ↓
Z85 + self-unpacking HTML shell
```

Conversion happens **before deduplication** because two different source files can become the same final representation.

## 2. Dependency discovery and priority

Merge mode performs conservative static dependency discovery across resolvable HTML, CSS, JavaScript, SVG and data references.

Priority is a packaging/order property, not a deletion policy. A resource directly reached from the entry document receives a higher priority than one reached through another resource:

```text
index.html -> 0
main.css   -> 1
app.js     -> 1
logo.svg   -> 1
bg.svg     -> 2
```

Resources that are not statically referenced remain included as lower-priority fallbacks. The exception is a table linked with `<link rel="csv">`: it is already stored in the page as an inline block (below), so the file is not stored a second time. This is important because a resource can still be needed by dynamic application logic that a static packager cannot prove from source text alone.

External dependencies produce warnings rather than being silently converted into local resources. Missing local dependencies are errors because Arcager cannot fulfill a requested local reference.

The graph is intentionally conservative. Arcager does not attempt to become a full JavaScript bundler or execute arbitrary application code while packaging.

## 3. Exact deduplication

Deduplication operates on the **final representation**.

The current path uses:

1. inexpensive size/MIME grouping;
2. a streaming BLAKE2 digest;
3. exact byte verification before physical-resource reuse.

The final MIME representation participates in identity, so a PNG and a WebP are never considered identical merely because they render similarly.

Fragments are removed from resource identity during reference resolution. Query parameters are retained unless the reference model establishes that they do not affect the resource identity.

Arcager intentionally does not perform perceptual or near-duplicate detection in 4.0. Different resolutions, crops and different bytes remain distinct.

## 4. Representation conversion

Default packing preserves the source representation.

When `lossy` is explicitly enabled, selected image conversions may be attempted. The current candidate set is:

```text
PNG  → lossless WebP candidate
JPEG → lossless WebP candidate
BMP  → lossless WebP candidate
GIF  → lossless WebP candidate, including animated GIFs
```

A candidate is accepted only when it encodes successfully, passes the relevant validation, preserves required image semantics, and is smaller than the current representation.

Animated GIF conversion has an animation-aware validation path covering frame count, canvas dimensions, timing where available, loop behavior and decoded frame content. A failed validation leaves the source representation intact.

`--strip-metadata` is a separate explicit operation. Rendering-critical information is not intentionally removed, and a cleanup that cannot be performed safely is rejected or left unchanged.

## 5. Solid stream architecture

The v4 container uses **MIME-oriented solid streams**. Physical resources are assigned to streams according to their final representation and affinity.

For a compressible stream candidate:

```text
raw members
    ↓
selected compressor
    ↓
compare serialized compressed form with stored form
    ↓
keep compression only when it actually wins
```

Resources that do not benefit from external compression can share the stored/raw stream. This is especially useful for already-compressed media such as PNG, JPEG, WebP, WOFF2, archives and many audio/video formats.

The important decision is not simply:

> "Did the compressor make the bytes smaller?"

It is:

> "Did using this compressed stream make the **serialized v4 container** smaller?"

That distinction prevents a tiny compression win from being erased by additional stream metadata.

The runtime does not depend on physical ordering. Logical records refer to physical IDs, and physical records refer to stream IDs and offsets.

## 6. Binary v4 metadata

The container starts with a fixed-size, explicitly versioned header followed by binary metadata and stream payloads.

The metadata records information including:

- stream count;
- physical-resource count;
- logical-resource count;
- path/string count;
- stream MIME and compression algorithm;
- delta-encoded stream offsets;
- physical stream/member offsets;
- final/source MIME IDs;
- physical flags;
- logical path IDs;
- priorities;
- logical flags;
- per-stream raw size and CRC.

Where possible, lengths are derived from boundaries rather than stored redundantly:

- a physical member ends at the next member offset in the same stream;
- a stream ends at the next stream offset or the payload end.

This keeps extraction deterministic while reducing metadata duplication.

The browser runtime validates table counts, boundaries, MIME IDs, offsets, member counts and metadata termination before accepting the container.

## 7. Integrity

The container has structural/integrity checks for:

- the header CRC;
- metadata CRC;
- each raw stream CRC;
- decompressed-size expectations;
- stream and member boundaries.

These checks are intended to turn accidental corruption into an explicit failure instead of silently producing altered resources.

When encryption is enabled, AES-GCM additionally authenticates the encrypted container. CRCs remain useful for unencrypted packages and for diagnosing structural corruption.

## 8. Encryption boundary

Compression and container construction complete before encryption:

```text
final v4 container
        ↓
AES-256-GCM envelope
        ↓
Z85 / HTML shell
```

The initial v4 package uses one AES-GCM envelope around the serialized container rather than independently encrypting every stream.

Hidden bundles have a separate password boundary. Their contents remain opaque to the main runtime; Arcager does not reinterpret their internal files.

## 9. Runtime architecture

The HTML shell contains the v4 container and a small browser runtime. The public API is intentionally limited:

```javascript
window.arcager.ready
window.arcager.error
```

The runtime:

1. reads the encoded container block;
2. authenticates/decrypts it when necessary;
3. validates the v4 header and metadata;
4. decompresses the streams actually needed by the package;
5. verifies decompressed sizes and stream CRCs;
6. creates Blob URLs for physical resources;
7. resolves `arcager-res:N` references;
8. rewrites supported plain resource references where necessary;
9. prepares the image and CSV convenience APIs;
10. restores the root HTML and removes the temporary unpacking shell.

The runtime uses the browser's native `DecompressionStream`. GZIP is the default compatibility path. Brotli is explicit and requires browser support for `DecompressionStream('brotli')`.

WOFF2 is not decoded by Arcager's outer compression runtime. Its internal font encoding remains the browser's responsibility.

The runtime exposes image aliases through `window.arcager.images` and `window.arcager.getImage()`. Merged CSV blocks are parsed into `window.arcager.csv`, with `rows` and header-keyed `data` entries.

Decompressed stream buffers are released after the runtime has created the resource URLs and recovered the root HTML, avoiding unnecessary retention of duplicate byte arrays.

## 10. Runtime API contract

The complete, example-driven reference is [`API.md`](API.md). The contract in brief:

- `window.arcager.ready` resolves when initialization completes and rejects when it fails; `window.arcager.error` is `null` or the `Error`.
- `window.arcager.images` maps names to `blob:` URLs for **every** logical resource (not only images). Each resource is registered under its path, its path without extension, and its bare stem (first one wins on collisions). Runtime-only alias entries are not exposed. Identical content shares one URL.
- `window.arcager.getImage(name)` returns a new `Image` or `null`. It tolerates a leading `./` or `/`, a missing or extra extension, and case.
- `window.arcager.csv[key]` is `{ rows, data }`, produced by an RFC 4180-style parser: quoted fields may contain delimiters, doubled quotes and line breaks; one delimiter per table (tab if the first line has a tab); blank lines skipped; all values are strings.

### How CSV tables are stored

In merge mode a `<link rel="csv" href="data/x.csv">` becomes
`<script type="text/csv" data-key="x" data-origin="data/x.csv">` holding the file text (made safe
for a script element by a reversible escape of `</script`). The file itself is **not** stored again.
`data-key` is the `arcager.csv` key (made unique, with a warning, if two files share a name);
`data-origin` records the original path so that listing, extraction and unpacking can present the
table once, under its real name, and restore the exact bytes. Packages written before 4.1 stored the
file as well; the catalog (section 11) recognizes the pair by content and still shows one resource.

The old v3 `window.arcager.state` and `window.arcager.loaded` signals are intentionally removed in v4. The public readiness model is reduced to `ready` plus `error`.

## 11. Bundles and extraction

Visible bundles become ordinary logical/physical resources inside the v4 container. Hidden bundles use a separate opaque envelope and password boundary.

### The resource catalog

`-l`, `-x` and `-u` all read a package through one `PackageView`, whose **catalog** lists every distinct resource exactly once:

- runtime alias entries are folded into the resource they point at;
- tables stored as inline blocks are matched to their file (by `data-origin`, otherwise by identical content) or, when no file exists, become a resource of their own;
- hidden-bundle members are added only when requested (`pack`, or `all` with `--bundle-password`).

Each resource has one output name (`extraction_name`): the stored name when its extension fits the stored MIME, otherwise the canonical extension for that MIME. Listing, extraction and the references written into restored pages all use that same name, so they cannot disagree.

### Extraction

Listing and extraction operate on the stored representation. If a PNG was deliberately converted to WebP, extraction returns the stored WebP rather than fabricating the original PNG bytes.

Stored HTML, CSS, JavaScript and SVG refer to other resources by `arcager-res:N` token (N is the logical index). Extraction turns each token back into a relative path computed from the referring file, and turns matched inline tables back into `<link rel="csv">`, so extracted files reference each other like the originals and can be re-packed. Extraction never overwrites existing files unless `--force` is given.

### Unpacking

`-u` rebuilds one standalone page. A `<link rel="stylesheet">` whose target is stored CSS becomes `<style>`; a `<script src>` whose target is stored JavaScript becomes an inline `<script>` (classic `defer` scripts move to the end of `<body>`, which is when they would have run; `</script` and `</style` inside the text are escaped). Every other reference becomes a `data:` URL: readable percent-encoded text for text resources (HTML, JSON, plain text), base64 for binary data. Resources that originated as `data:` URIs in the source (`__data__/…`) are restored as the base64 URI they were. Reference cycles between stored stylesheets are detected and reported.

An already-packed Arcager document is not recursively packed as an ordinary visible bundle in v4. Explicit repack/reoptimization is future work.

## 12. Compression choices

GZIP is the default CLI compression because compatibility is more important than squeezing every last byte from every workload.

Brotli is explicit:

```text
-c gzip
-c brotli
```

The choice applies to the stream-compression layer. It does not change the logical resource model, deduplication rules, extraction semantics or encryption boundary.

## 13. Cross-platform behavior

Logical resource paths use normalized forward slashes. Host filesystem access uses Python's platform-aware path APIs.

The implementation is designed for Linux, macOS and Windows and avoids assuming POSIX-only path syntax, shell behavior or temporary-file conventions.

CI exercises Python 3.10 and 3.13 on all three operating-system families. Browser smoke coverage is separately provided through Playwright for Chromium, Firefox and WebKit.

## 14. Deliberate boundaries

Arcager 4.0 does not attempt to provide:

- complete JavaScript dependency resolution;
- complete discovery of runtime-generated URLs;
- remote-site crawling or automatic remote-resource retrieval;
- server-side template evaluation;
- automatic flattening of multiple HTML entry documents;
- perceptual/near-duplicate detection;
- internal optimization of ZIP/7z/DOCX/XLSX/PPTX packages;
- dedicated SVG/vector optimization.

These are boundaries, not hidden promises. Future work is tracked in `TODO.md`.

## 15. Why the format is v4-only

The HTML shell carries:

```html
<!--arcager:4-->
```

and the binary container begins with the `ARCAGER4` magic/version marker.

This is a clean container-format break. The v4 core does not contain a v3 decoder. Already-packed Arcager input is skipped in ordinary packing rather than recursively repacked.

Explicit repacking/reoptimization is intentionally separate from the core v4 format so the normal packaging path stays predictable.

## 13. Loader shell, password prompt and encryption dependency

The HTML shell around every package is a dark loader (`LOADER_CSS`): `color-scheme: dark`, a dark panel, and a progress bar with a visible fill. It exists only while the package unpacks; the unpacked page then replaces the whole document, so it never leaks into your page's styling. The password prompt for encrypted packages is an in-page form (`#__pwf`) rather than the browser's native `prompt()`/`alert()` dialogs (which are bright and cannot be styled); `prompt()` remains only as a fallback if the form is missing. A wrong password shows its message in the panel and asks again. A `<noscript>` message explains that JavaScript is needed.

On the Python side `cryptography` is a **required** dependency, because encryption is a core feature. `ensure_core_dependencies()` runs at the start of every operation (but not for `--help` / `-V`, which must still work to explain the problem) and fails with the exact install command. Brotli, Pillow and Terser stay optional; `-V` reports all of them, including the Terser version (`terser_info()`).

## 14. Unreferenced files in merge mode

After the dependency walk, `_include_unreferenced_resources()` visits the rest of the folder. Non-HTML files nothing links to are recorded in `MergeGraph.unreferenced` (path, size) and, unless `--skip-unused` is set, still stored at low priority so scripts that compute file names keep working. Other `.html` files are recorded in `unlinked_pages`; pages the packed page links to are recorded in `linked_pages` when `_static_ref` meets them. `report_unused()` turns these into the two warnings described in `CLI.md`. Arcager packages found in the folder (for example an earlier output) are ignored silently.

## 15. Document types

`media_category()` assigns each MIME one primary type; `matches_type()` adds the overlaps (`media`, `text`, and `data`, which also matches XML). XML is a document (`docs`). `.mjs` is mapped to `text/javascript` in `_BUNDLE_EXTS`, so it takes the same path as `.js`: ES-module `import`s are followed, tokens are rewritten, and `-u` inlines it as `<script type="module">`.
