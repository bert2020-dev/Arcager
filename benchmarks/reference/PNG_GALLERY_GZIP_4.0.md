# Audited reference: PNG-heavy gallery

This is one real benchmark execution from the shipped `02-png-gallery` fixture on Linux. Throughput numbers are machine-dependent. The byte accounting is deterministic for the fixture and implementation.

Command:

```bash
python benchmarks/run_benchmarks.py --cases 02-png-gallery --no-v3
```

Observed on the validated v6 build:

| Measurement | Bytes |
|---|---:|
| Source corpus | 8,483,598 |
| Raw v4 resource representation | 8,483,586 |
| Stream payload | 8,481,478 |
| v4 container | 8,481,928 |
| Z85 representation | 10,602,410 |
| HTML shell/runtime | 17,344 |
| Final HTML | 10,619,754 |

The eight PNGs occupy 8,480,907 bytes and remain in a stored/raw stream. The GZIP candidate for that stream is 8,483,515 bytes, so Solid **rejects** it as non-beneficial. The HTML, CSS and JavaScript streams are compressed successfully.

The approximately 2.02 MiB increase from the binary container to the final HTML is therefore not PNG recompression and not the unpacker code. It is the Z85 text representation required by the current v4 HTML embedding strategy. The shell/runtime is only about 17 KiB.

`Exact assets = yes` in the generated report means the non-root logical resources recovered from the v4 container matched the shipped fixture bytes byte-for-byte.

For the historical comparison, the same fixture produced approximately 11.24 MB under the bundled 3.2.3 implementation and 10.13 MB under the validated 4.0.0 implementation. The v4 benchmark reports the accounting layers separately so this difference is not presented as an unexplained compression failure.
