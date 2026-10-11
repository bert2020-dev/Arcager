#!/usr/bin/env python3
"""Run Arcager's tests and say clearly whether they passed.

    python scripts/run_tests.py              all normal tests (about 20 seconds)
    python scripts/run_tests.py --all        also the slower benchmark-corpus tests
    python scripts/run_tests.py tests/test_cli_experience.py     just one file
    python scripts/run_tests.py -k extract   only tests whose name contains "extract"

Every test file can also be run directly (python tests/test_solid.py); that now calls this
script. Note: a test file run WITHOUT this (or without pytest) prints nothing, because the
files only define tests. Silence never means "passed".
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import pytest
    except ImportError:
        print("The tests need pytest, which is not installed for this Python.\n"
              f"  Install it with:  {sys.executable} -m pip install -r requirements-dev.txt", file=sys.stderr)
        return 2
    run_all = "--all" in argv or any("benchmark" in a for a in argv)
    argv = [a for a in argv if a != "--all"]
    args = ["-c", str(ROOT / "pytest.ini"), "--rootdir", str(ROOT), "-ra", "--tb=short"]
    if run_all:
        args += ["-m", "benchmark or not benchmark"]
    code = int(pytest.main(args + (argv or [str(ROOT / "tests")])))
    print()
    if code == 0:
        print("PASSED: every selected test passed." + ("" if run_all else "  (add --all to include the slower benchmark tests)"))
    elif code == 1:
        print("FAILED: some tests failed. The list above shows which ones and why.")
    elif code == 5:
        print("NOTHING RAN: no tests matched. Check the file name or the -k filter.")
    else:
        print(f"ERROR: pytest stopped with exit code {code}.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
