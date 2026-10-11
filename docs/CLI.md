# Arcager 4.2 command reference

`arcager --help` prints the same reference as the last section of this page, so you always
have it offline. This page adds the explanations: what each mode does, how types work, and
what to do when something goes wrong.

```text
arcager [options] PAGE.html...     pack pages        -> packed_PAGE.html
arcager -M SITE_DIR [options]      pack a site       -> packed_SITE_DIR.html
arcager -l [TYPES] PACKED.html     list a package
arcager -x [TYPES] PACKED.html     extract files
arcager -u PACKED.html             rebuild one standalone page
```

(`python arcager.py ...` works anywhere; `arcager` is the same thing if you installed it as a command.)

## Choosing a mode

| I want to… | Run |
|---|---|
| Pack one page | `arcager page.html` |
| Pack a site (HTML + CSS + JS + images + data) | `arcager -M ./site` |
| See what a package contains | `arcager -l packed_site.html` |
| Get my files back to edit and re-pack | `arcager -x all -O ./site-again packed_site.html` |
| Get one readable HTML file back | `arcager -u packed_site.html` |
| Get only some files | `arcager -x csv packed_site.html` or `arcager -x all --only 'data/*'` |

## Packing

Arcager reports the result for every file as a **percentage of the original size** and a **factor**:

```text
  [1/1] ✓ packed_site.html      195.5 KB ↓    94.0 KB  48.1% · 2.08×
```

`48.1%` means the package is 48.1% of the source size (`100 × package ÷ source`); `2.08×` means the source is 2.08 times larger than the package. Below 100% (factor above `1×`) is smaller. Above 100% (factor below `1×`) is larger, which is normal for content that is already compressed (JPEG, PNG, video, WOFF2): see [the benchmark numbers](../README.md#see-the-numbers). The same style is used for the per-type "solid" lines under `-v` and for the payload line.

For `-M`, the "source" size is the total of the files that went into the package, not just
`index.html`. Below the per-file line you get the accounting: how many resources, how many
distinct after de-duplication, the compressed payload as a share of the raw payload, and how
many bytes de-duplication saved.

### Files that nothing uses (`-M`)

Merge mode follows every link from `index.html` through its CSS and JavaScript. After that it
looks at the rest of the folder and tells you what it found:

```text
warning: 3 files (4.9 KB) in 'site' are not linked from index.html, its CSS or its JavaScript:
    old/logo-v1.png  (4.9 KB)
    notes.txt  (6 B)
    .DS_Store  (0 B)
  They are included anyway, in case your scripts build file names at run time. If they are not
  needed, delete them, or add --skip-unused to leave them out and make the package smaller.
warning: 2 other HTML pages in 'site' are not included: about.html (linked from your page: that
  link will not work), draft.html. Merge mode packs one page (index.html) with everything it
  uses. Pack another page on its own with: arcager PAGE.html
```

* **Unlinked files** are listed largest first (`-v` lists them all). They are still packed by
  default because a script can legitimately load `"img/" + name + ".png"`, which no static
  analysis can see. **`--skip-unused`** leaves them out; use it when you know your scripts do not
  do that.
* **Other HTML pages** are never packed by `-M` (one package is one page). A page your page
  links to is called out, because that link will not work inside the package.
* A site where everything is used prints no such warning.

Options are explained in `--help` and in the [options table](#full-reference-arcager---help).
Notes beyond that:

* **`lossy`** goes directly after the algorithm: `-c brotli lossy`. Without it nothing is
  converted. Arcager does not warn when you use it, because you asked for it.
* **`-M`** keeps files that nothing links to (and warns about them, see above). Tables
  linked with `<link rel="csv">` are stored once, inline, and exposed as `window.arcager.csv`
  ([API](API.md#csv-tables)).
* **Encryption is built in.** `-E` needs the `cryptography` package, which is a *required*
  part of Arcager (`python -m pip install -r requirements.txt`). If it is missing, Arcager stops
  at once with the exact install command.
* **Passwords.** Leave `--password` out and Arcager asks without echoing it. On the command line
  it is visible in shell history. Giving `--password` without `-E` does not encrypt anything;
  Arcager warns you.

## Resource types

`-l` and `-x` take a comma-separated list of types; the default is `all`. A resource belongs to
every type that describes it, so `-l docs,csv` shows the union.

<!-- BEGIN GENERATED: types -->
| Type | What it is | Matches |
|---|---|---|
| `all` | Every resource in the package | everything readable without an extra password |
| `docs` | Pages, styles, scripts and documents | HTML/XHTML, CSS, JavaScript (.js, .mjs), XML, Markdown, plain text, PDF, EPS |
| `images` | Raster images | PNG, JPEG, WebP, GIF, BMP, ICO, AVIF |
| `vector` | Vector graphics | SVG |
| `fonts` | Web fonts | WOFF, WOFF2, TTF, OTF, EOT |
| `audio` | Audio | MP3, OGG/Opus, WAV, M4A, AAC, FLAC |
| `videos` | Video | MP4, WebM, OGV, MOV |
| `media` | Images + vector + audio + video | images, vector, audio and videos together |
| `csv` | Tables | CSV and TSV, including tables embedded in the page as <script type="text/csv"> |
| `data` | Structured data | JSON, XML, CSV, TSV |
| `text` | Anything stored as text | docs + data + SVG (everything you can open in a text editor) |
| `other` | Everything else | archives (ZIP, 7z, GZIP), unknown binaries |
| `pack` | Hidden bundle contents | encrypted payload added with -b PATH hidden; needs its own password |
<!-- END GENERATED: types -->

Notes:

* `docs` includes HTML, CSS, JavaScript (`.js` and ES modules `.mjs`) and XML. JSON is `data`
  only. XML is both: `-l docs` and `-l data` list it. `.mjs` files are typed `text/javascript`
  (browsers refuse modules served as anything else) and their `import`s are followed like any
  other script.
* `pack` is the encrypted hidden bundle. It has its own password (`--bundle-password`, or
  you are asked). `all` includes it only if you already supplied `--bundle-password`;
  otherwise Arcager tells you it is there and how to include it. It never prompts for a
  password you did not ask to use.
* Asking for a type the package does not have is not an error in `-l`. Arcager explains what
  the package does contain.

## Listing: `-l`

```text
$ arcager -l packed_site.html
TYPE        BYTES MIME                             PATH
docs        53756 text/html                        index.html
docs        31526 text/css                         css/app.css
docs       101243 text/javascript                  js/app.js
csv         10853 text/csv                         data/elements.csv   (exposed as arcager.csv.elements; embedded in index.html)
8 resources · 227.1 KB stored
```

* Every resource appears **once**, under the file name `-x` will give it.
* `BYTES` is the stored size. A table marked "embedded in index.html" lives inside the page
  (that is how `window.arcager.csv` gets it), so it is not added to the total.
* `-v` also shows the other names a resource answers to in `window.arcager.images`.
* `stored as image/webp, was image/png` means a `lossy` conversion was applied.
* On a plain HTML file (not a package) `-l` lists what the markup references.

Narrow it down with `--only` (see below), for example `arcager -l all --only 'img/*'`.

## Extracting: `-x`

```text
arcager -x [TYPES] [--only PATTERN] [-O DIR] [-f] PACKED.html
```

Files go to `./extracted` unless you choose `-O DIR`. With several packages, each gets its own
sub-folder.

What you get back:

* **Normal files with normal references.** `index.html` and your stylesheets and scripts are
  restored so that they point at each other by relative path again (`css/app.css`,
  `../img/logo.png`), and tables become `<link rel="csv">` again. The extracted folder can be
  re-packed with `-M`, and for a site packed by Arcager the round trip returns identical files.
* **The stored form.** An image converted to WebP by `lossy` comes back as WebP, named `.webp`,
  and the restored `index.html` points at that name.
* **Hidden bundle files** only if you asked for `pack` (or `all` with `--bundle-password`).

Safety and guidance:

* Existing files are never overwritten silently. Add `-f` to overwrite, or pick another `-O`.
* If you extract only some types, pages may refer to files you did not extract. Arcager says so
  and suggests `-x all`.
* If nothing matched, the exit code is `3` and the message says what the package contains.

### Picking resources by name: `--only`

`--only` limits `-l` and `-x` to resources whose path, file name, or table key matches one of
the comma-separated patterns. `*` and `?` are wildcards; matching ignores case; a trailing `/`
means "everything in this folder".

```bash
arcager -x all --only 'data/*.csv'        # every CSV in data/
arcager -x all --only elements            # the table that window.arcager.csv.elements shows
arcager -l all --only 'css/,js/'          # list css/ and js/ folders
```

## Unpacking: `-u`

`arcager -u packed_site.html` writes `unpacked_site.html`: one self-contained HTML file.

* `<link rel="stylesheet">` becomes a `<style>` element and `<script src>` becomes an inline
  `<script>`, both with the original text, readable and editable. Attributes such as `media`,
  `id` and `type="module"` are kept.
* A script that had `defer` is placed at the end of `<body>`, which is when a deferred script
  runs, so behaviour is unchanged.
* Tables stay as readable `<script type="text/csv">` blocks.
* Other text resources (JSON, plain text, HTML for an iframe) become `data:` URLs written as
  readable text rather than base64.
* Images, fonts, audio, video, PDFs and other binary data are embedded as base64, because
  there is no other way to put them in one file.
* `?query` strings on embedded resources are dropped (they mean nothing for embedded data).

Resources that came from a `data:` URI in your original page are put back as they were.

## Output names and locations

* Packing writes `packed_NAME.html` next to the input (unpacking: `unpacked_NAME.html`).
* `-O DIR` chooses the folder, `-o FILE` the exact file (one input only), `--prefix` the prefix of packed files (unpacked files are always `unpacked_`).
* An existing output is never replaced without asking: confirm at the prompt, or pass `-f`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage error or unreadable input (the message says how to fix it) |
| 2 | Some files failed |
| 3 | Nothing processed: every input was skipped, or `-x` found nothing to extract |
| 130 | Interrupted (Ctrl+C) |

## When something goes wrong

Every message is written to tell you what to do next. A few common ones:

| Message starts with | Do this |
|---|---|
| `unknown option '--strip-metdata'. Did you mean …` | Fix the spelling; Arcager suggests the closest option. |
| `'elements' is not a resource type` | `-l`/`-x` take types. To choose by name use `--only`. |
| `missing local resources` | A file your HTML/CSS/JS refers to is not there. Names are case-sensitive on Linux and macOS. |
| `Arcager needs the 'cryptography' package` | Run the command in the message. It is the one required package (`requirements.txt`). |
| `Brotli needs the optional 'brotli' package` | Run the pip command printed in the message, with the same Python that runs Arcager, or use `-c gzip`. |
| `incorrect password, or the file is damaged` | Check the password. The hidden bundle has a separate one (`--bundle-password`). |
| `N external resource(s) are loaded from the network` | The page still needs the internet for those files. Download them into the folder and reference them by relative path to make it fully offline. |
| `N files … are not linked from index.html` | See [Files that nothing uses](#files-that-nothing-uses--m). Delete them or add `--skip-unused`. |
| `lossy needs Pillow` | `lossy` / `--strip-metadata` need Pillow; without it images are kept unchanged. Install with the command shown. |
| `already-packed Arcager file` | The input is already a package. Use `-u` to unpack it first. |

## Checking your setup: `-V`

`arcager -V` prints the version and what Arcager can use on this machine, including whether
**Terser** is installed and which version:

```text
arcager 4.2.0
  Python        3.12.3  (/usr/bin/python3)
  cryptography  46.0.6  (required, encryption)
  Terser        5.51.2  (/usr/local/bin/terser)
  Brotli        1.2.0  (optional, for -c brotli)
  Pillow        12.1.1  (optional, for lossy / --strip-metadata)
```

A missing optional component says what it is for and how to install it. A Terser that is
found but cannot report a version is explained too. `python scripts/doctor.py` gives a more
detailed report (module paths, Node.js) and works even when Arcager itself cannot start.

## Full reference (`arcager --help`)

<!-- BEGIN GENERATED: help -->
```text
arcager 4.2.0 — turn an HTML page or a static site into ONE self-unpacking HTML file

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
  all     Every resource in the package
          matches: everything readable without an extra password
  docs    Pages, styles, scripts and documents
          matches: HTML/XHTML, CSS, JavaScript (.js, .mjs), XML, Markdown, plain text, PDF, EPS
  images  Raster images
          matches: PNG, JPEG, WebP, GIF, BMP, ICO, AVIF
  vector  Vector graphics
          matches: SVG
  fonts   Web fonts
          matches: WOFF, WOFF2, TTF, OTF, EOT
  audio   Audio
          matches: MP3, OGG/Opus, WAV, M4A, AAC, FLAC
  videos  Video
          matches: MP4, WebM, OGV, MOV
  media   Images + vector + audio + video
          matches: images, vector, audio and videos together
  csv     Tables
          matches: CSV and TSV, including tables embedded in the page as <script type="text/csv">
  data    Structured data
          matches: JSON, XML, CSV, TSV
  text    Anything stored as text
          matches: docs + data + SVG (everything you can open in a text editor)
  other   Everything else
          matches: archives (ZIP, 7z, GZIP), unknown binaries
  pack    Hidden bundle contents
          matches: encrypted payload added with -b PATH hidden; needs its own password
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
```
<!-- END GENERATED: help -->
