# Changelog

## 4.2.0

### Changed
- **Percentages are now "percent of the original"** (`100 × package ÷ source`), so there are no
  plus/minus signs to decode: `48.1% · 2.08×`. Below 100% is smaller, above 100% is larger. The
  factor is unchanged. Applies to the per-file result, totals, the payload line and the `-v`
  per-type lines, and to the README benchmark table.
- **Encryption is a core requirement.** `cryptography` moved from `requirements-optional.txt` to
  the new `requirements.txt`. If it is missing, Arcager stops immediately and prints the exact
  install command for the Python it runs under (`--help` and `-V` still work so you can diagnose).
  `scripts/doctor.py` marks it required and exits `2` when it is missing.
- **Dark loader.** The page shown while a package unpacks is now dark (no bright white flash),
  with a visible progress bar, `color-scheme: dark`, and a `<noscript>` message. The password
  prompt for encrypted packages is an in-page dark form instead of the browser's native
  `prompt()` / `alert()` dialogs; a wrong password is reported in the panel and asked again.
- `-V` / `--version` now lists the components Arcager can use.
- XML is classified as a document (`docs`; it is still matched by `data` as well).

### Added
- `-V` reports **whether Terser is installed and which version** (and explains a Terser that is
  found but broken), plus Python, cryptography, Brotli and Pillow.
- **`.mjs` support.** ES-module files are typed `text/javascript` (browsers refuse modules served
  as anything else), their `import`s are followed, and `-u` inlines them as `<script type="module">`.
- **Merge warns about unused files.** Files nothing links to are listed (largest first, with
  sizes) and other HTML pages that are not packed are named, including pages your page links to
  (those links cannot work in a single file). `--skip-unused` leaves unlinked files out.
- `scripts/run_tests.py` and `requirements-dev.txt`. Every test file can be run directly and now
  prints a result and a clear **PASSED** / **FAILED** line. Previously
  `python tests/test_solid.py` printed nothing at all because the file only defines tests.
- Warning when `lossy` or `--strip-metadata` is used without Pillow installed (images were
  silently left unchanged).
- Tests for all of the above (73 normal tests, 100 including the benchmark corpus).

### Documentation
- `docs/CLI.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md` updated; the `--help`
  text and type table in the docs are regenerated from the program.
- `docs/TESTING.md` explains the two benchmark failures caused by an incomplete
  `16-many-tiny-files` fixture and the verified one-line fix.

## 4.1.0

### Fixed
- **Tables were stored and listed twice.** A `<link rel="csv">` is now stored once, inline, and
  `-l` shows each resource exactly once under its real path. Packages made by earlier versions
  are also listed without duplicates.
- **`-x csv` / `-x all` produced duplicate, slightly different CSV files.** Each table is now
  extracted once, byte for byte.
- **`-x all` and `-x docs` did not bring back `index.html`.** They do now, with working
  relative references (and `<link rel="csv">` restored), so an extracted site can be re-packed.
- **`-x elements` printed `No such file or directory`.** Mistyped types now explain the valid
  types and point at `--only`.
- **Runtime CSV parser** split quoted fields that contain line breaks into separate rows and
  chose the delimiter per line. It is now quote-aware and picks one delimiter per table.
- **`getImage("logo.png")` returned `null` for files added with `-b`.** It now ignores a leading
  `./` or `/`, the extension, and case.
- Two linked CSV files with the same name overwrote each other in `arcager.csv`; the second is
  now exposed as `name_2` with a warning.
- `?query` suffixes could corrupt embedded base64 data on unpack; they are now dropped.
- Packages with `-b` listed every file again under its alias names (7 rows for 3 files).
- Extraction renamed files whose extension was fine (`photo.jpeg` became `photo.jpg`) or missing
  (`LICENSE` became `LICENSE.bin`). Names are now kept unless the stored type really differs
  (a PNG converted to WebP becomes `.webp`).
- `-l`/`-x` types: `media` matched everything and `data` matched nothing. `application/javascript`
  now counts as `docs`.

### Changed
- **`-u` is readable.** CSS and JavaScript become inline `<style>` / `<script>` text; only binary
  data stays base64. About 20% smaller on the periodic-table example.
- Packing prints the size change as a percentage and a factor (`−53.2% · 2.14×`) and explains
  when output is larger than the source.
- No warning is printed for options you chose explicitly (`lossy`, `--strip-metadata`, …).
- `--help` rewritten: modes, examples, every option, what each resource type matches, exit codes.
- Extraction refuses to overwrite existing files without `-f`.
- `-x` exits `3` when nothing was extracted.
- Error and warning messages say what to do next (and suggest the closest option name).

### Added
- `--only PATTERN` for `-l` / `-x`; `-V/--version`; `other` and `data` resource types.
- `docs/CLI.md`, `docs/API.md` (images and CSV, with examples), `docs/RELEASING.md`.
- **One place for the version**: `VERSION` in `arcager.py`. `scripts/sync_docs.py` updates the
  docs and generates the type table and help text; a test fails if they drift.
- Warnings for `--password` without `-E` (the package would not be encrypted), for pack-only
  options used with `-l/-x/-u`, and for non-UTF-8 CSV files.

### Upgrade notes
- Output of `-l` has a header and footer line; the four columns are unchanged.
- The inline table block carries a new `data-origin` attribute; it is ignored by the runtime.
- The container format is unchanged (v4).
