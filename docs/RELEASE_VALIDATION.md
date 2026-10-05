# Arcager 4.0.0 release validation

This document records the final v6 validation pass for the candidate Arcager 4.0.0 Solid Update repository. It is intentionally a concise release record rather than a historical audit log.

## Local environment

- Python: 3.13.5 on Linux
- pytest: 9.0.2
- Pillow: 12.3.0
- cryptography: 46.0.4
- brotli: 1.2.0
- Node.js: 22.16.0
- ImageMagick: available
- ffmpeg: available
- Terser CLI: not installed locally; the Terser path remains an optional capability and is covered by the repository's optional-path handling rather than claimed as locally encoded

## Regression suite

```bash
python -m pytest -q tests/test_solid.py
```

Result: **25 passed**.

Coverage includes preservation, merge/reference handling, exact deduplication, explicit WebP conversion, visible and hidden bundles, encryption, corruption, v3 rejection, already-packed detection, CSV/TSV paths, data-URI extraction, runtime syntax, Unicode/recursive scanning, integrity checks, metadata stripping, dependency-priority behavior, and Solid-compression cost decisions.

The slower benchmark-corpus suite was also run:

```bash
python -m pytest -q -m benchmark tests/test_benchmark_corpus.py
```

Result: **27 passed**.

## Benchmark smoke

The shipped 24-case fixture corpus was verified and representative v4 workloads were benchmarked with the v6 harness.

A representative local run produced:

| Workload | Source | Final HTML | Ratio | Change |
|---|---:|---:|---:|---:|
| PNG gallery | 8.09 MiB | 10.13 MiB | 0.80× | +25.2% |
| Tetris-style game | 1.38 MiB | 0.24 MiB | 5.70× | −82.5% |
| 7z + 12 CSV datasets | 0.58 MiB | 0.66 MiB | 0.88× | +13.9% |
| Scientific JSON/XML/CSV | 4.15 MiB | 0.57 MiB | 7.31× | −86.3% |
| Duplicate-rich site | 5.38 MiB | 0.28 MiB | 19.04× | −94.7% |
| Font-heavy site | 1.77 MiB | 1.15 MiB | 1.54× | −35.0% |

These numbers are machine-dependent examples, not universal guarantees. The important accounting distinction is that source bytes, final representation bytes, stream payload, serialization overhead and shell overhead are measured separately.

## Cross-platform/browser execution

The local release environment is Linux. macOS and Windows are covered by `.github/workflows/ci.yml`, which runs the regression suite on Ubuntu, macOS and Windows with Python 3.10 and 3.13. Those operating systems were not executed locally during this validation pass.

`.github/workflows/browser-smoke.yml` provides a manually triggered Playwright smoke workflow for Chromium, Firefox and WebKit. This validation record therefore does not claim local execution of Safari/WebKit, Firefox or Brave.

## Final repository checks

- v4-only container detection is present.
- The obsolete v4 audit files are not part of the release tree.
- `README.md` is the primary public entry point and documents use cases, representative measurements, quick start, API, limitations and installation.
- `docs/ARCHITECTURE.md` documents the logical/physical model, dependency priority, exact deduplication, stream decisions, metadata, integrity, encryption and runtime contract.
- `docs/TESTING.md` documents the regression, corruption, browser and benchmark strategy.
- `TODO.md` contains future features deliberately excluded from 4.0.
