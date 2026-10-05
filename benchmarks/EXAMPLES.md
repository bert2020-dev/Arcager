# 24 benchmark examples

| ID | Scenario | Generated source | What it demonstrates |
|---|---|---:|---|
| 01-tiny-static | Tiny static page | 0.00 MiB | Shell overhead and the lower usefulness bound of packaging |
| 02-png-gallery | Many PNG images | 8.09 MiB | Image-heavy, low-to-medium entropy source |
| 03-noisy-png | Noisy PNG images | 1.62 MiB | High-entropy raster media where compression has little room |
| 04-jpeg-gallery | JPEG gallery | 1.50 MiB | Already-compressed photographic media |
| 05-svg-duplicates | Many identical SVGs | 0.02 MiB | Exact content deduplication |
| 06-animated-gif | Animated GIFs | 0.05 MiB | Animation-aware representation conversion |
| 07-mixed-media | PNG + GIF + WAV | 2.09 MiB | Heterogeneous MIME streams and binary content |
| 08-css-heavy | CSS-heavy application | 0.09 MiB | Text redundancy and CSS stream behavior |
| 09-tetris-game | Tetris-style mini-game | 1.38 MiB | JavaScript + CSS heavy workload |
| 10-html-heavy | Hypertext-heavy documentation | 1.41 MiB | Large repetitive HTML documentation |
| 11-js-data-heavy | JavaScript carrying a large dataset | 6.57 MiB | Large JavaScript payload |
| 12-json-heavy | Large structured JSON | 1.99 MiB | Structured data redundancy |
| 13-twelve-csv | Twelve CSV datasets | 0.26 MiB | Merge-time data integration |
| 14-secret-7z-and-csv | Twelve CSV + secret .7z | 0.58 MiB | Data + hidden opaque bundle + encryption boundary |
| 15-scientific-data | Scientific data | 4.15 MiB | XML + JSON + CSV together |
| 16-many-tiny-files | 3,500 tiny resources | 0.16 MiB | Metadata/indexing and many-resource behavior |
| 17-duplicate-rich | Exact duplicate media | 5.38 MiB | Exact duplicate collapse |
| 18-near-duplicates | Near-duplicate media | 4.87 MiB | Proof that similarity is not silently treated as equality |
| 19-high-entropy | High-entropy binary + text | 4.10 MiB | Compression limits on random-like input |
| 20-precompressed | GZIP/ZIP payloads | 1.15 MiB | Raw/stored fallback and compressed-member behavior |
| 21-font-heavy | Local fonts | 1.77 MiB | Fonts as first-class resources |
| 22-unicode-i18n | Unicode/internationalized content | 0.07 MiB | UTF-8 paths and content |
| 23-recursive-site | Recursive multi-page site | 1.05 MiB | Dependency traversal and multi-page structure |
| 24-large-mixed | Large mixed corpus | 21.48 MiB | Larger end-to-end package behavior |


## Shipped corpus

Every scenario listed above is included as actual files under `benchmarks/fixtures/`. The repository also contains `benchmarks/fixtures/manifest.json`, which records the byte size and SHA-256 digest of every fixture file.

Scenario `14-secret-7z-and-csv` keeps `private-data.7z` outside its `site/` merge root so it can be tested exclusively as a hidden opaque bundle rather than accidentally becoming an ordinary site resource.
