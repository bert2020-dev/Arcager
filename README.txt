================================================================================
  arcager — Single-file HTML packer
  Version 3.2.0
  Last updated: 2026-11-18
================================================================================

  Turn any HTML page — and optionally its whole directory — into a single,
  self-contained HTML file that unpacks itself in the browser.  Optionally
  encrypt it with a password, bundle media, hide arbitrary files inside an
  encrypted payload, or inline tabular data.  Optionally merge an entire
  static site (HTML + CSS + JS + fonts + media + CSV) into one file.

  Licensed under the PolyForm Noncommercial License 1.0.0.  See LICENSE.txt.

  Python port of the original arcager.js, on-disk-format compatible with
  the Node version.


================================================================================
  1. QUICK START
================================================================================

  Install the extras you need, once:

      pip install cryptography brotli
      npm install -g terser          # optional, for -m / --minify

  Pack a page (output: packed_<name>.html next to the input):

      python arcager.py report.html

  Pack with Brotli and the full minify pass:

      python arcager.py --brotli -m lossy report.html

  Encrypt the payload with a password (browser prompts before unpacking):

      python arcager.py -E report.html

  Bundle a folder of images; <img src="flags/ge.png"> is auto-rewired:

      python arcager.py -b ./flags page.html

  Hide any file inside a normal-looking cover page:

      python arcager.py -b secret.zip hidden cover.html

  Merge a whole static site into one file:

      python arcager.py -M ./docs -m

  Inline tabular data and expose it at runtime:

      # index.html contains:  <link rel="csv" href="elements.csv">
      python arcager.py -M ./site
      # runtime:  arcager.csv['elements'].data

  Unpack back to HTML:

      python arcager.py -u packed_report.html

  List what's inside a packed file (no password needed):

      python arcager.py -l packed_flags.html

  Extract everything hidden inside a pack:

      python arcager.py -x pack -O ./out packed_cover.html


================================================================================
  2. WHAT IT IS
================================================================================

  arcager produces a single .html file containing:

    - The original HTML, compressed with gzip or Brotli.
    - Any embedded data: URIs, hoisted into a separate binary blob.
    - An inline JavaScript unpacker that reassembles the page at load
      time using the browser's built-in DecompressionStream.
    - Optional visible bundles (images, fonts, audio, video, documents)
      exposed to the page as window.arcager.images.
    - Optional inlined CSV data exposed as window.arcager.csv.
    - Optional hidden encrypted payloads the browser never touches.

  The result is one file you can email, host, drop on a USB stick, or
  serve from anywhere.  The browser does the work — no server needed.

  With -E, the compressed HTML payload is encrypted with AES-256-GCM; the
  key is derived from a password via PBKDF2-SHA256 with 600,000 iterations.
  The browser prompts before unpacking.

  With -b PATH hidden, files are stored as an opaque encrypted blob that
  the browser loader ignores; extract them later with -x pack.

  With -M, a directory can be flattened into one file: local CSS and JS
  are inlined, fonts and media are converted to data URIs, and CSV files
  referenced via <link rel="csv"> become inline data blocks.


================================================================================
  3. INSTALLATION
================================================================================

  REQUIREMENTS
    Python 3.8 or newer.  No third-party packages required for basic
    pack / unpack / merge / list / extract.

  OPTIONAL DEPENDENCIES

    -E, encrypted -u, encrypted -x:
        pip install cryptography

    --brotli:
        pip install brotli

    -m / --minify (JavaScript squeeze only; the HTML squeeze requires
    the 'lossy' sub-flag):
        npm install -g terser

  INSTALL THE SCRIPT

    Run in place:
        python arcager.py --help

    Symlink on Unix:
        chmod +x arcager.py
        sudo ln -s "$PWD/arcager.py" /usr/local/bin/arcager

    On Windows: rename to arcager.py, add its folder to PATH, and
    invoke with `python arcager.py`.


================================================================================
  4. COMMAND MODES
================================================================================

  arcager has five modes.  Modes are mutually exclusive.

  ── pack ──────────────────────────────────────────────────────────────

      python arcager.py [options] <input...>

      Pack one or more HTML files into packed_<name>.html.  Accepts
      files, directories (top-level .html only — use -r to recurse),
      or shell globs.  Batch inputs are processed independently.

      Use --prefix PREFIX to change the default 'packed_' prefix.

  ── unpack ────────────────────────────────────────────────────────────

      python arcager.py -u [options] <input...>

      Reverse a packed file back to its original HTML.  Byte-exact for
      non-lossy packings.  Prompts for the payload password on encrypted
      bundles unless --password is given.

      -u only reverses the HTML payload.  It does not extract visible
      bundles, hidden payloads, or inlined CSV — that's what -x is for.

  ── merge ─────────────────────────────────────────────────────────────

      python arcager.py -M <dir> [options]

      Take a directory, find its index.html (or index.htm,
      default.html, default.htm), and flatten the whole site into one
      packed file: local stylesheets, JS, fonts, images, video,
      CSV data, CSS url() references, and CSS @import chains.  Output
      defaults to packed_<dirname>.html next to the directory.

      A merge report lists external URLs, missing files, and other
      local HTML pages that cannot be flattened.

      -M is a short option for --merge; both spellings are equivalent.

  ── list ──────────────────────────────────────────────────────────────

      python arcager.py -l [TYPE,...] <input...>

      List media inside a packed or unpacked HTML file.  Listing never
      needs a password: metadata is unencrypted.

      TYPE accepts one type, or a comma-separated list.  Default: all.

        all      — everything
        media    — hoisted data URIs from the payload
        pack     — bundles (visible + hidden)
        csv      — inlined CSV data blocks
        images   — filter by category (works across media + pack)
        videos
        audio
        fonts
        docs

      Examples:
        python arcager.py -l images packed_page.html
        python arcager.py -l images,videos,fonts packed_page.html
        python arcager.py -l csv packed_table.html

  ── extract ───────────────────────────────────────────────────────────

      python arcager.py -x [TYPE,...] <input...> [-O DIR]

      Extract media or data into an output directory.  TYPE as for -l.
      Destination comes from -O; defaults to ./extracted.  Prompts for
      the payload and/or bundle password as needed, and for overwriting
      a non-empty destination unless --force.

      With multiple inputs, each file gets its own subdirectory of DIR.
      For unpacked HTML sources, -x extracts only embedded data: URIs
      and inlined CSV blocks (local file references already exist on
      disk).

      CSV extraction writes each <script type="text/csv" data-key="...">
      block to <key>.csv in the destination directory.


================================================================================
  5. BUNDLES
================================================================================

  --bundle / -b PATH [hidden] adds files to the pack.  It can be
  given multiple times.  Visible and hidden bundles can be mixed in the
  same pack.

  ── VISIBLE BUNDLES (-b PATH) ────────────────────────────────────────

  Media is decompressed in the browser and exposed to your page as
  window.arcager.images.  Your HTML's src/href/poster/srcset/CSS
  url(...) references are auto-rewired at load time.

  Accepted inputs:
    - A folder of media (scanned recursively).
    - A .zip / .tar / .tar.gz / .tgz / .tbz2 / .txz archive of media.
    - A single media file.

  Recognized file types:
    Images:     png jpg jpeg webp gif svg bmp ico avif
    Fonts:      woff woff2 ttf otf eot
    Audio:      mp3 ogg oga wav m4a aac flac opus
    Video:      mp4 m4v webm ogv mov
    Documents:  html htm xhtml pdf eps txt md markdown
    Data:       csv tsv

  Total uncompressed bundle cap: 128 MB.

  Password-protected zip archives are refused when bundled as visible
  media.  Decrypt them first, or bundle the archive as-is with
  '-b FILE hidden'.

  Runtime API (available once arcager.ready resolves):

      await arcager.ready;

      arcager.images['ge']            // data URL string
      arcager.images['flags/ge']      // path-preserving key
      arcager.getImage('ge')          // returns an HTMLImageElement
      arcager.getImage('flags/ge')

  Keys are the file's relative path with the extension stripped, using
  forward slashes.  When a bare basename is unique across the whole
  bundle set, a short alias is added (so 'ge' works when there is
  only one ge.png anywhere).  Ambiguous aliases are dropped.

  If the archive contains HTML/PDF/XHTML files, they are exposed as
  blob: URLs so they can be loaded inside <iframe> elements.

  When -E is used, visible bundles are encrypted with the payload
  password so the entire pack stays opaque.

  ── HIDDEN BUNDLES (-b PATH hidden) ──────────────────────────────────

  The file (or folder) is stored as an opaque, encrypted blob the
  browser-side loader never touches.  No runtime API.  Extract with:

      python arcager.py -x pack -O ./out packed_cover.html

  Accepted inputs: any file, any folder (zipped in memory at pack time),
  or any of the same archives.  No type restriction.  Total uncompressed
  payload cap: 512 MB.

  Hidden bundles use their own password, independent from the payload
  password.  If both -E and -b ... hidden are used, the script prompts
  twice — clearly labelled "Payload password" and "Bundle password".

  Because hidden bundles are encrypted with a bundle password and can
  coexist with any pack output, they're useful for exchanging secret
  files between consenting parties: the cover page (if any) loads
  cleanly for anyone, and only those with the bundle password can
  extract the payload.

  ── COMPRESSION ──────────────────────────────────────────────────────

  Already-compressed files (PNG, JPEG, MP4, ZIP, 7z, WOFF, WOFF2, PDF,
  DOCX, ...) are not re-compressed.  The packer only stores the
  compressed form when it is actually smaller.  Folders zipped on the
  fly use ZIP_STORED so the outer compressor sees the raw contents.


================================================================================
  6. CSV & TABULAR DATA
================================================================================

  arcager can inline CSV files into the packed HTML during --merge, and
  expose them to your page as arcager.csv at runtime.

  ── HOW TO USE IT ────────────────────────────────────────────────────

  In your source HTML, reference the CSV just like a stylesheet:

      <link rel="csv" href="data/elements.csv">

  During --merge, arcager reads the file, escapes any '</script>' in
  the content, and replaces the <link> with:

      <script type="text/csv" data-key="elements">
      symbol,name,atomic_number
      H,Hydrogen,1
      He,Helium,2
      ...
      </script>

  The data-key is derived from the file's basename with any
  non-word characters replaced by underscores.  So
  data/elements.csv becomes data-key="elements".

  At runtime, the blocks are parsed automatically on load:

      await arcager.ready;

      arcager.csv['elements'].rows
      // [['symbol','name','atomic_number'], ['H','Hydrogen','1'], ...]

      arcager.csv['elements'].data
      // [{symbol:'H', name:'Hydrogen', atomic_number:'1'}, ...]

  .rows is an array of arrays (raw, header row first).
  .data is an array of objects keyed by the first row's headers.

  ── ADVANTAGES OVER HARD-CODED DATA ──────────────────────────────────

  A CSV file in a static site normally needs a fetch() at runtime,
  which fails for local files opened via file:// and adds a round
  trip on the network.  Inlining the data removes both problems: the
  page is self-contained and the data is available the moment the
  page loads.  You can also keep the source CSV as an editable file
  in your repo — no build step required to change a row.

  ── PARSER SCOPE ─────────────────────────────────────────────────────

  The parser is RFC 4180-compliant for the common cases:

    - Comma delimiter (fixed).
    - Quoted fields ("like this").
    - Escaped quotes inside quoted fields ("say ""hi""").
    - Embedded commas inside quoted fields ("a,b").
    - Embedded newlines inside quoted fields.
    - CRLF and LF line endings.
    - Optional trailing newline.
    - Header row auto-detected from the first row.

  The parser is deliberately small (~30 lines of JavaScript).  It does
  not handle: delimiters other than comma, BOM stripping, type coercion
  (every field is a string), or streaming large files.  If you need
  any of those, pre-process your CSV before packing, or extract it
  with -x csv and use a full library.

  ── LISTING AND EXTRACTING CSV ───────────────────────────────────────

  CSV data blocks appear in -l and -x listings under the 'csv' type
  and the 'media' source, because they live in the payload HTML
  rather than in the attachment blob:

      python arcager.py -l csv packed_table.html
      python arcager.py -x csv -O ./out packed_table.html

  Each block is written to <data-key>.csv in the destination folder.

  Listing CSV from a packed file requires decompressing the payload
  first.  If the file is encrypted, -l csv will prompt for the payload
  password even though metadata-only listings (like -l images) do not.
  Use -l images,videos,fonts to skip the prompt when you only want
  to inspect attachments.


================================================================================
  7. ENCRYPTION
================================================================================

  -E, --encrypt

      The compressed HTML payload and hoisted data URIs are encrypted
      with AES-256-GCM.  The browser shows a password prompt before
      unpacking the page.  Prompts for a payload password unless
      --password is given.

  -b PATH hidden

      The hidden bundle blob is encrypted with the bundle password.
      Prompts for a password unless --bundle-password is given.

  Both can be combined.  Two prompts, two independent passwords.

  Implementation details:
    - Key derivation: PBKDF2-SHA256, 600,000 iterations.
    - Separate salts and IVs for payload and bundle.
    - Wrong-password detection uses an encrypted canary, so users get
      a clean "Incorrect password." message instead of a decryption
      error.

  Constraints:
    - Encrypted payloads require a secure context (https://, file://,
      or localhost).  crypto.subtle is not exposed on plain http://
      origins.
    - Passwords are prompted twice on pack and once on unpack/extract,
      unless --password / --bundle-password is given.


================================================================================
  8. FLAG REFERENCE
================================================================================

  INPUTS
    <input...>              Files, directories, or shell globs.

  OUTPUT
    -o, --output PATH       Exact output file (single input, or --merge).
    -O, --output-dir DIR    Output directory for any mode.  Auto-created.
                            For -x, this is the extraction destination.
        --prefix PREFIX     Prefix for pack/merge output names.  Defaults
                            to 'packed_'.  Also stripped during unpack.
    -f, --force             Overwrite output without prompting.
    -r, --recursive         Recurse into subdirectories when scanning.

  COMPRESSION
    --brotli                Use Brotli instead of gzip (Chrome 105+).
    -m, --minify [lossy]    Run Terser on the inline unpacker JS.  Add
                            'lossy' to ALSO strip HTML comments, collapse
                            inter-tag whitespace, and remove
                            sourceMappingURL comments from the payload.
                            Lossy.  (Terser via `npm install -g terser`.)
    --ignore-uris LIST      Comma-separated MIME prefixes to KEEP as
                            data: URIs rather than hoist.  Example:
                                --ignore-uris image/svg+xml,image/gif

  PAYLOAD MODIFICATION (lossy)
    --base-href URL         Inject <base href="URL">.
    -m lossy                See --minify above.

  ENCRYPTION
    -E, --encrypt           Encrypt the HTML payload.
    --password PW           Non-interactive payload password.
    --bundle-password PW    Non-interactive bundle password.

  BUNDLES
    -b, --bundle PATH [hidden]
                            Bundle media (or, with 'hidden', any file).
                            May be given multiple times.

  UNPACK
    -u, --unpack            Reverse a packed file back to HTML.

  MERGE
    -M, --merge DIR         Bundle a whole directory into one file.
                            Inlines CSS, JS, fonts, media, and CSV.
                            <link rel="csv" href="data.csv"> becomes a
                            <script type="text/csv"> block exposed at
                            runtime as arcager.csv.

  LIST / EXTRACT
    -l, --list [TYPE,...]          List media.  TYPE can be a comma-
                                   separated list, e.g. images,videos.
    -x, --extract [TYPE,...]       Extract into an output directory.
                                   TYPE as for -l.

    TYPE (comma-separated):
        all | media | pack | csv | images | videos | audio | fonts | docs

  MISC
    -v, --verbose           Per-stage diagnostics on stderr.
    -V, --version           Print version.
    -h, --help              Full help text.


================================================================================
  9. EXAMPLES
================================================================================

  ── EXAMPLE 1: Deliver a password-protected report to a client ───────

      python arcager.py -E --brotli -m lossy \
          --base-href https://reports.example.com/ \
          q3_report.html

      Produces packed_q3_report.html.  Email it.  The client opens it,
      gets a password prompt, enters the password you shared out of
      band, and sees the report.

  ── EXAMPLE 2: Bundle a static site into one file ────────────────────

      python arcager.py -M ./docs -m

      Scans ./docs/, finds ./docs/index.html, inlines local CSS, JS,
      fonts, images and icons, and writes packed_docs.html alongside
      the docs/ folder.  Review the merge report: it lists external
      URLs, unresolvable local links, and other HTML pages it could
      not flatten.

  ── EXAMPLE 3: Bundle a page with a folder of flags ──────────────────

      python arcager.py -b ./flags page.html

      Every image in ./flags becomes available at runtime.  A
      reference like <img src="flags/ge.png"> in page.html is
      rewritten to the in-memory bundle.

      In JavaScript:

          await arcager.ready;
          const flag = arcager.images['ge'];      // data URL
          const img  = arcager.getImage('ge');    // <img> element
          document.body.appendChild(img);

  ── EXAMPLE 4: Inline CSV data for a periodic table ──────────────────

      # Source layout:
      #   site/
      #     index.html
      #     data/elements.csv

      # index.html contains:
      #   <link rel="csv" href="data/elements.csv">

      python arcager.py -M ./site

      In the packed page:

          await arcager.ready;
          const grid = document.getElementById('grid');
          for (const el of arcager.csv['elements'].data) {
            grid.innerHTML += '<div>' + el.symbol + '</div>';
          }

      The CSV stays editable in your repo.  Changing a row and
      re-running arcager.py repacks the page with the new data.  No
      fetch(), no server, no build step.

  ── EXAMPLE 5: Hide arbitrary files inside a normal-looking page ─────

      python arcager.py -b secret.zip hidden -b documents/ hidden \
          cover.html

      Produces a plain HTML cover page.  secret.zip and a zipped copy
      of documents/ are encrypted inside the page with a bundle
      password you choose at prompt time.  To recover them:

          pip install cryptography
          python arcager.py -x pack -O ./out packed_cover.html

      You are prompted for the bundle password.  Both are recovered:
      secret.zip as-is, and documents/ as documents.zip (folders are
      zipped at pack time).

  ── EXAMPLE 6: Combine payload and bundle encryption ────────────────

      python arcager.py -E -b ./flags -b secret.zip hidden page.html

      Three prompts:
        1. Payload password (from -E)
        2. Confirm payload password
        3. Bundle password (for secret.zip)

      The page is encrypted; ./flags is encrypted and auto-loaded into
      window.arcager.images after the payload password is entered;
      secret.zip is encrypted separately and never loaded by the
      browser.

      Recover everything:

          python arcager.py -u --password PW packed_page.html
          python arcager.py -x all -O ./out --password PW \
              --bundle-password BPW packed_page.html

  ── EXAMPLE 7: Inspect what a pack contains ─────────────────────────

      python arcager.py -l packed_flags.html
      python arcager.py -l images packed_flags.html
      python arcager.py -l images,videos,fonts packed_flags.html
      python arcager.py -l csv packed_table.html
      python arcager.py -l pack packed_cover.html

      No password is needed for -l on unencrypted payloads.  For
      encrypted payloads, -l csv prompts once for the payload
      password because it must decompress the HTML to find the CSV
      blocks.

  ── EXAMPLE 8: Extract media or CSV for auditing or reuse ───────────

      python arcager.py -x all -O ./out packed_flags.html
      python arcager.py -x pack,images -O ./secret packed_cover.html
      python arcager.py -x csv -O ./data packed_table.html

      Files land under ./out/ (or ./secret/, or ./data/), one
      subfolder per input when extracting from multiple files.

  ── EXAMPLE 9: Custom output prefix ─────────────────────────────────

      python arcager.py --prefix bundle_ report.html
      # → bundle_report.html

      python arcager.py --prefix bundle_ -M ./docs
      # → bundle_docs.html

      python arcager.py -u --prefix bundle_ bundle_report.html
      # → unpacked_report.html

  ── EXAMPLE 10: Batch-pack a directory tree ─────────────────────────

      python arcager.py -r ./public --brotli -m lossy -f

      Every .html under ./public/ (recursively) is packed into
      packed_<name>.html in the same folder.

  ── EXAMPLE 11: Archive an encrypted bundle and recover later ───────

      # Sender:
      python arcager.py -E --password "$KEY" secrets.html

      # Months later, on another machine:
      pip install cryptography
      python arcager.py -u --password "$KEY" packed_secrets.html

  ── EXAMPLE 12: Keep small vector assets inline ─────────────────────

      python arcager.py --ignore-uris image/svg+xml,image/gif \
          page.html

      Large base64 PNGs and JPEGs are hoisted into the binary blob.
      Tiny SVGs and GIFs stay as data: URIs where they are cheap and
      avoid an extra decode step at load time.


================================================================================
  10. COMPRESSION RESULTS
================================================================================

  Ratios depend heavily on content.  Typical figures, gzip level 9:

    Content type                         Input     Packed        Ratio
    ------------------------------------------------------------------
    Text-heavy article / docs            100 KB    30–40 KB      30–40%
    Bootstrap/jQuery landing             400 KB    90–120 KB     22–30%
    SPA bundle + media                   2 MB      900 KB–1.2 MB 45–60%
    Media-dominated (photos, video)      5 MB      4.5–4.9 MB    90–98%
    Tiny page (no media)                 4 KB      6–8 KB        150%+

  Notes:
    - Brotli (quality 11) typically saves another 10–20% on text
      (HTML/CSS/JS), but is slower to compress and needs Chrome 105+.
    - Hoisting data: URIs into a binary blob is worth 30–50% on pages
      with many embedded images.
    - Inlined CSV is highly compressible (like HTML) and typically
      shrinks to 15–25% of its original size under gzip.
    - Already-compressed bundles are stored without re-compression.
    - Pages already dominated by compressed media will not shrink much;
      packing them is worth it for the single-file convenience.
    - Files under ~8 KB usually grow because the shell is a fixed
      ~3–4 KB overhead.  Use -m to trim.
    - Encryption adds a few hundred bytes of metadata and no meaningful
      change to the payload size.


================================================================================
  11. LIMITATIONS
================================================================================

  BROWSER / RUNTIME
    - Requires DecompressionStream (Chrome 80+, Firefox 113+, Safari
      16.4+).  Brotli additionally requires Chrome 105+ / Firefox 115+.
      gzip is the safest default.
    - Encrypted payloads need a secure context (https://, file://, or
      localhost).  Plain http:// has no crypto.subtle.
    - The unpacker is inline JS.  Under a Content-Security-Policy that
      forbids 'unsafe-inline' scripts (without a matching nonce/hash),
      the shell will not run.  Opening the file locally is the usual
      workaround.
    - No support for JavaScript-disabled browsers.

  COMPRESSION
    - Media-dominated inputs may not shrink and can grow slightly.
    - Very small inputs (< 8 KB) grow.  Use -m.

  LOSSY MODES
    - -m lossy and --base-href modify the payload before compression.
      Files packed with either cannot be restored byte-exact by -u.
      -m without 'lossy' only runs Terser on the unpacker JS, leaving
      the payload HTML untouched.

  MERGE MODE
    - Only the entry index.html is bundled.  Additional HTML pages
      referenced by <a href> or <iframe src> are not merged.
    - No JavaScript module resolution.  <script src> files are inlined
      verbatim; bare `import` specifiers will fail.  Pre-bundle with
      esbuild / rollup / webpack first.
    - No HTML template processing (SSI, PHP, Jinja, etc.).
    - A <base> tag in the source produces a warning; relative URL
      resolution may be wrong.
    - Runtime-built URLs (e.g. img.src = 'pic' + n + '.png') cannot be
      resolved.
    - CSS inside JS strings (styled-components, CSS-in-JS) is not
      scanned.
    - Individual media files larger than 64 MB are skipped
      (_MERGE_MAX_INLINE_BYTES in source).
    - Symlinks are followed but not normalized; cyclic symlinks can
      cause repeated reads.

  CSV DATA
    - Comma delimiter only.  TSV files are accepted by the merge
      scanner but currently parsed with the comma delimiter, producing
      a single-column result; convert to CSV or set the delimiter
      manually before packing.
    - The header row is always the first row.  There is no way to skip
      metadata rows or specify a header line number.
    - All fields are strings.  Numbers, booleans, and dates are not
      coerced; convert them in your JavaScript.
    - No support for BOM, alternate encodings, or streaming very large
      files.  Anything bigger than a few MB is better handled with a
      pre-processed format.

  BUNDLES
    - Visible bundles: 128 MB total uncompressed cap.
    - Hidden bundles: 512 MB total uncompressed cap.
    - Keys must use letters, digits, '_', '-', '.', '/', space, '@',
      '+'.  Anything else aborts the pack with a clear error naming
      the offending file.
    - Password-protected zip archives are refused when bundled as
      visible media.  Use '-b FILE hidden' to bundle them as-is.

  SIZE
    - The whole payload is held in memory during pack and unpack.

  FORMAT
    - Packed files begin with the sentinel <!--arcager:3-->.  Files
      without this sentinel are refused by -u with "not an arcager
      file".
    - This version is not compatible with earlier 2.x bundles.


================================================================================
  12. TROUBLESHOOTING
================================================================================

  "DecompressionStream is not supported by this browser."
      The browser is too old.  Use a modern Chrome, Firefox, or Safari,
      or repack without --brotli.

  "Brotli is not supported by this browser."
      Brotli requires Chrome 105+ / Firefox 115+ / Safari 16.4+.
      Repack without --brotli.

  "Cannot unpack this file" / CSP warning shown instead of the page
      The page is served under a Content-Security-Policy that blocks
      inline scripts.  Open the file locally (file:// is typically not
      CSP-restricted), serve from a permissive host, or ask the
      publisher for a non-bundled version.

  "Encrypted bundles require a secure context."
      The bundle is loaded over plain http:// from a non-localhost
      origin.  Serve over https:// or open locally.

  "Incorrect password."
      Passwords are case-sensitive and whitespace-significant.

  "requires the 'cryptography' package"
      pip install cryptography

  "--brotli requires the 'brotli' package"
      pip install brotli

  "terser not found on PATH; --minify has no effect on JS"
      npm install -g terser.  Packing still works without it.

  "zip entry ... is encrypted"
      Decrypt the archive first, or bundle it as-is with
      '-b FILE hidden'.

  Merge report lists "unmergeable" entries
      These are <a href> or <iframe src> pointing at other local .html
      files, which cannot be flattened into a single document.

  Merge report lists "missing" entries
      A local reference could not be found on disk.  Run with -v to
      see the resolved path.  This includes missing CSV files
      referenced by <link rel="csv">.

  CSV data is empty in the browser
      arcager.csv['key'] is undefined.  Check that:
        - The <link rel="csv"> tag was inside <head> or <body> of the
          entry index.html (not a page reached by <a href>).
        - The data-key matches the file's basename (e.g. elements.csv
          → 'elements').
        - The merge report did not list the CSV under "missing".
        - You awaited arcager.ready before reading arcager.csv.

  CSV parses as a single column
      The source file is likely tab-separated.  arcager parses CSV
      with a comma delimiter only; convert the file to comma-
      separated or process it manually.

  "output would overwrite input"
      -o points at the input file.  Choose a different output path.

  "output exists: ...  (use --force)"
      Confirm with y at the prompt, or pass -f, or delete the stale
      file.

  "hidden bundles require a bundle password"
      Pass --bundle-password or run interactively.

  Colored output renders as escape codes in Windows cmd
      Use Windows Terminal, or set NO_COLOR=1 to disable colors.

  Packed file is larger than the input
      Small inputs and media-dominated inputs grow.  Use -m to shrink
      the shell, or skip packing and use a plain archiver for storage.


================================================================================
  13. COMPATIBILITY
================================================================================

  This version writes the sentinel <!--arcager:3-->.  It is not
  compatible with earlier 1.x or 2.x bundles; repack older files with
  the current tool if you need to change their contents.

  BROWSER SUPPORT MATRIX

      Feature                  Chrome   Firefox   Safari   Edge
      ------------------------------------------------------------------
      gzip unpack               80+     113+      16.4+    80+
      Brotli unpack            105+     115+      16.4+   105+
      AES-GCM decrypt*         60+      57+       11+      79+
      file:// context            yes     yes       yes      yes

      * Requires a secure context (https://, file://, localhost).


================================================================================
  14. LICENSE / CREDITS
================================================================================

  arcager is released under the PolyForm Noncommercial License 1.0.0.
  See LICENSE.txt for the full terms.

  In short: you may use, modify, and distribute this software for any
  noncommercial purpose, provided you preserve the copyright notice
  and license text.  Commercial use requires a separate license from
  the author.

  arcager is a Python port of the original arcager.js by the same
  author.  Only the Python version is actively maintained at the moment.

  Third-party libraries used at runtime (cryptography, brotli, terser)
  keep their own licenses.  None are linked into packed output — the
  packed file is pure HTML, CSS, and JavaScript that runs entirely in
  the browser.

================================================================================