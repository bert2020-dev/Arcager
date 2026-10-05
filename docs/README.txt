Arcager 4.0 documentation
==========================

The public entry point is ../README.md.

Architecture
------------
See ARCHITECTURE.md for the logical/physical resource model, stream layout,
deduplication, conversion policy, encryption boundary and runtime contract.

Testing
-------
See TESTING.md for the regression strategy, benchmark philosophy and the
current local validation record. Cross-platform execution is covered by the
GitHub Actions matrix in ../.github/workflows/ci.yml.

The implementation is intentionally dependency-free for preservation-mode
packing. Optional packages are used only for optional capabilities such as
AES-GCM, Brotli, image conversion and Terser-based minification.
