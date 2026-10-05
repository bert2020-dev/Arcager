# Arcager benchmarks

Arcager ships the **actual 24 benchmark examples** under `benchmarks/fixtures/`. The benchmark no longer creates a hidden temporary corpus and no longer relies on a hand-written sample report.

## Reproducible workflow

Verify the shipped fixture bytes:

```bash
python benchmarks/examples.py --verify
```

Run one example:

```bash
python benchmarks/run_benchmarks.py --cases 02-png-gallery
```

Run all 24 cases with the bundled **3.2.3 historical baseline and 4.0.0**:

```bash
python benchmarks/run_benchmarks.py --cases all
```

Because the full matrix includes the historical 3.2.3 path and deliberately large/high-entropy workloads, use a single case when you want a quick smoke test. `--no-v3` can be used for a 4.0-only run.

Brotli is opt-in because the historical maximum-quality path is substantially slower:

```bash
python benchmarks/run_benchmarks.py --cases all --brotli-samples
```

## What is measured

The report separates four layers that were previously conflated:

1. **Source bytes** — bytes in the shipped scenario.
2. **Raw resource representation** — bytes after representation selection and exact dedup, before stream compression.
3. **Stream payload** — bytes actually stored by the selected raw/compressed streams.
4. **Final HTML** — the complete standalone `.html` file.

The final HTML table also shows the Z85 representation overhead and HTML shell overhead. This makes an already-compressed PNG workload diagnosable instead of presenting its serialization cost as if Arcager had recompressed the PNG into something larger.

The benchmark also records encode/decode throughput and performs a v4 byte-for-byte logical-resource preservation check for every non-root resource unless `--skip-roundtrip-check` is supplied.

## Solid-compression invariant

A stream is only marked compressed when its serialized stream payload is smaller than the same stream stored raw. The benchmark runner checks this invariant and fails rather than silently reporting a non-beneficial “solid” stream.

For example, a PNG-heavy case can legitimately report:

```text
PNG resources       -> kept as raw/stored
GZIP candidate      -> larger than raw
Solid decision      -> rejected
Z85                 -> +25% representation overhead
HTML shell          -> small fixed overhead
```

That is a packaging trade-off, not evidence that the PNGs themselves were recompressed into a larger representation.

## The 24 shipped scenarios

See `EXAMPLES.md` for the complete scenario list. The fixture files are the canonical test inputs. `benchmarks/fixtures/manifest.json` records every file's size and SHA-256 digest.

## Python interpreter and optional libraries

The benchmark does not assume that the Python used to start the driver is the Python used by every user. Before running selected capabilities it probes the current interpreter and common platform launchers for `cryptography`, `brotli`, and `Pillow`. This avoids the Windows Store `python.exe` alias hiding an installed package in another interpreter.

For diagnostics:

```bash
python scripts/doctor.py
```

To force a known environment:

```bash
python benchmarks/run_benchmarks.py --python C:\\Users\\me\\venv\\Scripts\\python.exe --cases 14-secret-7z-and-csv
```
