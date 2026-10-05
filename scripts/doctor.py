#!/usr/bin/env python3
"""Arcager environment diagnostic.

Reports the exact Python executable in use and the optional capabilities that
Arcager can see from that interpreter. It never installs anything.
"""
from __future__ import annotations
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

MODULES = ("cryptography", "brotli", "PIL")


def module_info(name: str):
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None, None
    try:
        module = __import__(name)
        return getattr(module, "__version__", "unknown"), getattr(module, "__file__", None)
    except Exception as exc:
        return f"import-error: {exc}", getattr(spec, "origin", None)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check Arcager's Python/runtime capabilities without installing anything.")
    ap.add_argument("--require", default="", help="comma-separated optional modules that must be available")
    ns = ap.parse_args()

    print(f"Python executable : {sys.executable}")
    print(f"Python version    : {sys.version.split()[0]}")
    print(f"Platform          : {sys.platform}")
    print()
    missing = []
    for name in MODULES:
        version, path = module_info(name)
        if version is None:
            print(f"{name:18} unavailable")
            missing.append(name)
        else:
            print(f"{name:18} {version}  ({path})")
    node = shutil.which("node")
    terser = shutil.which("terser")
    print(f"{'node':18} {node or 'unavailable'}")
    print(f"{'terser':18} {terser or 'unavailable'}")

    required = [x.strip() for x in ns.require.split(",") if x.strip()]
    required_missing = [name for name in required if name in missing]
    if required_missing:
        print("\nMissing required capability:", ", ".join(required_missing))
        print("Use this same interpreter with `python -m pip install ...` or activate the intended virtual environment first.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
