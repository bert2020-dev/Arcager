# Arcager 4.0 testing

## Local regression suite

The repository includes `tests/test_solid.py` and runs with the standard pytest entry point:

```bash
python -m pytest -q
```

The current implementation validation covers 25 standard tests locally, including:

- CLI help contract;
- preservation-mode merge;
- exact logical/physical deduplication;
- explicit PNG→WebP optimization;
- visible bundle self-containment on CLI unpack;
- AES-GCM wrong-password and correct-password paths;
- corruption rejection;
- hidden-bundle round-trip with an independent password;
- extraction from an unpacked HTML containing a data URI;
- runtime `ready`/`error` contract and Node.js syntax validation when Node is installed;
- CSV listing/extraction after merge;
- standalone byte-exact preservation round-trip;
- v3 clean-break detection and already-packed skipping;
- external-resource warnings and missing-local failures;
- explicit metadata stripping with rendered-content validation;
- Unicode paths and recursive batch input;
- low-level header/metadata integrity failures;
- rejection of non-beneficial Solid compression candidates; rejection of compression candidates whose payload win is erased by final v4 stream metadata cost.

The suite is deliberately CLI-oriented: it exercises the same entry points that users invoke rather than only testing helper functions.

## Cross-platform validation

GitHub Actions runs the automated suite on:

- Ubuntu;
- macOS;
- Windows.

The CI workflow installs optional packages required by the regression corpus (`cryptography`, `brotli`, `Pillow`) so that optional capabilities are exercised rather than merely imported conditionally.

This local environment is Linux. It does not execute macOS/Windows binaries, Safari/WebKit, Firefox, or Brave. Those are therefore not claimed as locally executed validation results.

## Browser validation

The runtime is intentionally small and has a browser-oriented smoke contract in the repository. Run `python scripts/browser_smoke.py` after installing Playwright and its browser runtimes. The repository also includes a manual `workflow_dispatch` GitHub Actions job for Chromium, Firefox, and WebKit. The smoke contract exercises:

- GZIP;
- Brotli where the browser exposes the corresponding `DecompressionStream` capability;
- WebP;
- animated WebP;
- WOFF2 native loading;
- AES-GCM;
- merged pages and generic bundled assets.

The distinction between Arcager's JavaScript Brotli decoding and WOFF2's internal native Brotli decoding is important: Arcager must never modify WOFF2 merely to satisfy its own outer compression choice.

## Corruption testing

The test strategy deliberately corrupts independent layers:

```text
header
metadata
stream offsets
compressed data
raw data
CRC
encoded payload
AES-GCM ciphertext
```

The required result is a clear failure, never silently altered output.

## Benchmarks

The benchmark harness defines **24 deliberately different workloads** and compares the bundled Arcager 3.2.3 baseline with Arcager 4.0.0. The actual fixture projects are shipped under `benchmarks/fixtures/`, and `benchmarks/fixtures/manifest.json` records their sizes and SHA-256 digests. It measures user-facing outputs plus the accounting needed to explain them:

- source corpus size;
- raw resource representation size;
- stored/compressed stream payload size;
- v4 container metadata size;
- Z85 representation overhead;
- HTML shell overhead;
- final `.html` package size;
- compression ratio (`source / package`);
- encode throughput in MiB/s;
- decode/unpack throughput in MiB/s;
- byte-for-byte preservation of every non-root logical resource in the v4 preservation path.

The 24 workloads cover tiny pages, image-heavy sites, high-entropy inputs, CSS/JavaScript-heavy applications, a Tetris-style mini-game, CSV/JSON/XML datasets, a hidden opaque `.7z` payload, exact duplicates, near-duplicates, fonts, Unicode paths, recursive sites, and a larger mixed corpus.

Verify the shipped corpus:

```bash
python benchmarks/examples.py --verify
```

Run a focused comparison:

```bash
python benchmarks/run_benchmarks.py --cases 02-png-gallery
```

Run the full matrix:

```bash
python benchmarks/run_benchmarks.py --cases all
```

Slow optional capability variants (lossy media conversion, metadata stripping, encryption and hidden bundles) are opt-in so the normal benchmark remains a predictable reproducibility test:

```bash
python benchmarks/run_benchmarks.py --cases all --capabilities
```

The bundled 3.2.3 implementation under `benchmarks/baselines/3.2.3/` is a fixed historical baseline. It exists so new 4.x releases can be measured against the last 3.x implementation without maintaining a second development branch.

The decode number is CLI/container unpack throughput, not a browser-specific page-load benchmark. Browser timing should be reported separately when browser automation is available.

## Performance interpretation

The Solid Update is not tuned by chasing one benchmark number. The intended hierarchy is:

```text
correctness
integrity
semantic correctness
reliable extraction
secure encryption
reasonable memory
compression effectiveness
encoder speed
micro-optimization
```

A more expensive analysis step is acceptable when it makes the output safer, more deterministic, or substantially easier to reason about.


## Reproducible 24-scenario benchmark corpus

Arcager ships the full benchmark corpus under `benchmarks/fixtures/`; it is not synthesized only at benchmark time. Verify the shipped bytes with:

```bash
python benchmarks/examples.py --verify
```

Run a focused v3.2.3-vs-v4.0.0 comparison:

```bash
python benchmarks/run_benchmarks.py --cases 02-png-gallery
```

Run the full corpus:

```bash
python benchmarks/run_benchmarks.py --cases all
```

The benchmark separates raw resource representation, stream payload, v4 container, Z85 overhead, HTML shell overhead, final package size, and encode/decode throughput. This prevents already-compressed media from being misreported as a resource-level compression failure. The report also records the exact Python interpreter and optional-library availability used by the benchmark so an environment mismatch cannot be mistaken for a library defect.

Run the slower corpus-specific preservation tests with:

```bash
pytest -q -m benchmark tests/test_benchmark_corpus.py
```

The standard regression suite intentionally excludes these slower fixture tests by default.
