#!/usr/bin/env python3
"""Keep the documentation in step with the code. One command, no manual edits.

The version number lives in ONE place: ``VERSION = "x.y.z"`` in arcager.py.

    python scripts/sync_docs.py              update every managed place from arcager.py
    python scripts/sync_docs.py --set X.Y.Z  change VERSION in arcager.py, then update the docs
    python scripts/sync_docs.py --check      change nothing; exit 1 if anything is out of date
                                             (this is what the test-suite and CI run)

What is managed
  * "Arcager X.Y" headings/titles in the docs (the table RULES below).
  * Generated blocks between  <!-- BEGIN GENERATED: name -->  and  <!-- END GENERATED: name -->
    markers: the resource-type table and the full ``--help`` text, produced by arcager.py
    itself so the docs can never disagree with the program.

What is deliberately NOT managed
  * docs/RELEASE_VALIDATION.md - a dated record of one validation run; rewriting its
    version would falsify it. Update it by hand when you re-validate.
  * benchmarks/ - labels such as "4.0.0" and "3.2.3" name historical baselines.
  * Sentences about when a behaviour was introduced ("in 4.0") - those are history.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCAGER = ROOT / "arcager.py"
VERSION_LINE = re.compile(r'^(VERSION\s*=\s*")(\d+\.\d+\.\d+)(")', re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# (file, regex with 3 groups: before / version-text / after, "series" = X.Y or "version" = X.Y.Z)
RULES = [
    ("README.md", r"(?m)^( ?# Arcager )(\d+\.\d+(?:\.\d+)?)(\s*)$", "series"),
    ("README.md", r"(\| Goal \| Arcager )(\d+\.\d+(?:\.\d+)?)( \|)", "series"),
    ("docs/README.txt", r"(?m)^(Arcager )(\d+\.\d+(?:\.\d+)?)( documentation)$", "series"),
    ("docs/ARCHITECTURE.md", r"(?m)^(# Arcager )(\d+\.\d+(?:\.\d+)?)( architecture)$", "series"),
    ("docs/TESTING.md", r"(?m)^(# Arcager )(\d+\.\d+(?:\.\d+)?)( testing)$", "series"),
    ("docs/CLI.md", r"(?m)^(# Arcager )(\d+\.\d+(?:\.\d+)?)( command reference)$", "series"),
    ("docs/API.md", r"(?m)^(# Arcager )(\d+\.\d+(?:\.\d+)?)( browser API)$", "series"),
    ("README.md", r"(?m)^(arcager )(\d+\.\d+\.\d+)()$", "version"),          # sample output of -V
    ("docs/CLI.md", r"(?m)^(arcager )(\d+\.\d+\.\d+)()$", "version"),         # sample output of -V
    ("docs/RELEASING.md", r'(?m)^(VERSION = ")(\d+\.\d+\.\d+)(")$', "version"),
    ("docs/RELEASING.md", r'(Titles such as "Arcager )(\d+\.\d+)( architecture")', "series"),
]

GENERATED = {  # block name -> files that contain it
    "types": ["README.md", "docs/CLI.md"],
    "help": ["docs/CLI.md"],
}


def read_version() -> str:
    m = VERSION_LINE.search(ARCAGER.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit('error: could not find a line like  VERSION = "x.y.z"  in arcager.py')
    return m.group(2)


def set_version(new: str) -> None:
    if not SEMVER.match(new):
        raise SystemExit(f"error: '{new}' is not a version like 1.2.3 (three numbers separated by dots)")
    text = ARCAGER.read_text(encoding="utf-8")
    ARCAGER.write_text(VERSION_LINE.sub(lambda m: m.group(1) + new + m.group(3), text, count=1), encoding="utf-8")


def load_arcager():
    spec = importlib.util.spec_from_file_location("arcager_for_docs", ARCAGER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["arcager_for_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


def types_table(mod) -> str:
    rows = ["| Type | What it is | Matches |", "|---|---|---|"]
    for name, summary, what in mod.TYPE_INFO:
        what = what.replace("|", "\\|")
        rows.append(f"| `{name}` | {summary} | {what} |")
    return "\n".join(rows)


def generated_content(name: str, mod) -> str:
    if name == "types":
        return types_table(mod)
    if name == "help":
        return "```text\n" + mod.help_text().rstrip("\n") + "\n```"
    raise KeyError(name)


def sync(check: bool) -> list[str]:
    """Return the list of problems (check mode) or of files rewritten (write mode)."""
    version = read_version()
    series = ".".join(version.split(".")[:2])
    mod = load_arcager()
    if mod.VERSION != version:
        return [f"arcager.py: VERSION parsed as {mod.VERSION} but regex found {version}"]
    changes: list[str] = []
    problems: list[str] = []
    contents: dict[str, str] = {}

    def get(rel: str) -> str:
        if rel not in contents:
            path = ROOT / rel
            if not path.exists():
                problems.append(f"{rel}: file is listed in scripts/sync_docs.py but does not exist")
                contents[rel] = ""
            else:
                contents[rel] = path.read_text(encoding="utf-8")
        return contents[rel]

    for rel, pattern, kind in RULES:
        text = get(rel)
        if not text:
            continue
        want = series if kind == "series" else version
        regex = re.compile(pattern)
        if not regex.search(text):
            problems.append(f"{rel}: no match for {pattern!r}. If the heading was reworded, "
                            f"update RULES in scripts/sync_docs.py")
            continue
        new = regex.sub(lambda m: m.group(1) + want + m.group(3), text)
        if new != text:
            changes.append(f"{rel}: version text -> {want}")
            contents[rel] = new

    for name, files in GENERATED.items():
        body = generated_content(name, mod)
        block = re.compile(rf"(<!-- BEGIN GENERATED: {name} -->\n)(.*?)(<!-- END GENERATED: {name} -->)", re.S)
        for rel in files:
            text = get(rel)
            if not text:
                continue
            if not block.search(text):
                problems.append(f"{rel}: missing the markers <!-- BEGIN GENERATED: {name} --> / <!-- END GENERATED: {name} -->")
                continue
            new = block.sub(lambda m: m.group(1) + body + "\n" + m.group(3), text)
            if new != text:
                changes.append(f"{rel}: regenerated '{name}' block")
                contents[rel] = new

    if check:
        return problems + [f"{c} (out of date)" for c in changes]
    for rel in {c.split(":")[0] for c in changes}:
        (ROOT / rel).write_text(contents[rel], encoding="utf-8")
    return problems + changes


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync documentation with the version/help text in arcager.py (see module docstring).")
    ap.add_argument("--set", metavar="X.Y.Z", help="set the version in arcager.py first, then sync")
    ap.add_argument("--check", action="store_true", help="only verify; exit 1 if anything is out of date")
    ns = ap.parse_args()
    if ns.set and ns.check:
        ap.error("--set and --check cannot be combined: --check never changes anything")
    if ns.set:
        set_version(ns.set)
    results = sync(check=ns.check)
    version = read_version()
    if ns.check:
        if results:
            print(f"Docs are NOT in sync with arcager.py ({version}):", file=sys.stderr)
            for r in results:
                print("  -", r, file=sys.stderr)
            print("Fix: run  python scripts/sync_docs.py", file=sys.stderr)
            return 1
        print(f"Docs are in sync with arcager.py ({version}).")
        return 0
    problems = [r for r in results if "no match" in r or "missing the markers" in r or "does not exist" in r or "VERSION parsed" in r]
    for r in results:
        print(("PROBLEM  " if r in problems else "updated  ") + r)
    if not results:
        print(f"Nothing to do: everything already matches {version}.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
