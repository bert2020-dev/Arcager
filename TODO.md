# Arcager future work

These items are intentionally outside the 4.0 Solid Update unless explicitly moved into a later release.

- URL input and remote static-site ingestion.
- External resource retrieval for remote/local parity.
- A strict standalone-dependency mode that can turn external-resource warnings into errors.
- Dedicated SVG/vector optimization.
- Map/tile/zoom-level vector and geographic dataset optimization.
- EPS→PDF conversion.
- PDF optimization.
- ZIP/7z/package-aware optimization.
- DOCX/XLSX/PPTX package-aware optimization.
- EPUB/package optimization.
- Adaptive one-stream versus multi-stream compression when benchmark evidence justifies the added complexity.
- Indexed/random-access solid extraction.
- Perceptual or near-duplicate media analysis.
- Additional safe media transformations.
- Explicit repack/reoptimize support for already-packed Arcager packages.
- Server-authorized encrypted-at-rest/session-controlled delivery.
- Asymmetric-key/session-encryption research.
- Multi-document packaging.
- Richer dynamic dependency analysis.
- Future deduplication extensions beyond exact byte identity.

## Security wording for future controlled delivery

A future server-authorized mode must be described as encrypted-at-rest or controlled-delivery protection. It must not claim to make plaintext immune to a headless browser, browser automation, screenshots, or AI systems once the content has been rendered for an authorized client.
