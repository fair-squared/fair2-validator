#!/usr/bin/env python3
"""Vendor the pinned fair2-spec into the package so `pip install` is self-contained.

The `fair2-spec/` submodule is the source of truth. This copies the files the
validator actually reads into `src/fair2_validator/_spec_data/`, mirroring the
spec's directory layout so `Spec(root=_spec_data)` works unchanged, and writes a
`VERSION` file (there is no `.git` inside the wheel to `git describe`).

Run it whenever the submodule is bumped:  `make sync-spec`  (or `python scripts/sync_spec.py`).
The vendored copy is committed so a plain checkout / sdist builds a working wheel
without needing the submodule.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "fair2-spec"
DEST = REPO / "src" / "fair2_validator" / "_spec_data"

# (relative path under the spec) — copied verbatim into _spec_data/<same path>.
FILES = [
    "context/metadata/fair2_context.json",
    "context/validation/shacl_context.json",
    "examples/example-1/borja2025.json",
    "schemas/fair2-governance.schema.json",
    # Loaded with the shapes: SHACL class targeting follows rdfs:subClassOf,
    # and omitting it silently under-validates rather than erroring.
    "ontologies/fair2_ontology.ttl",
]
DIRS = [
    "shapes/turtle",  # all *.ttl shape files
]


def _spec_version() -> str:
    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", "-C", str(SPEC), *args],
                                 capture_output=True, text=True, timeout=5)
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            return ""
    return (git("describe", "--tags", "--exact-match")
            or (git("tag", "--points-at", "HEAD").splitlines() or [""])[0]
            or (f"untagged@{git('rev-parse', '--short', 'HEAD')}" if git("rev-parse", "--short", "HEAD") else "unknown"))


def main() -> int:
    if not (SPEC / "shapes" / "turtle" / "dataset.ttl").is_file():
        print("error: fair2-spec submodule not initialised "
              "(`git submodule update --init`)", file=sys.stderr)
        return 1

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    copied = 0
    for rel in FILES:
        src = SPEC / rel
        dst = DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for rel in DIRS:
        for src in sorted((SPEC / rel).glob("*.ttl")):
            dst = DEST / rel / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    version = _spec_version()
    (DEST / "VERSION").write_text(version + "\n")

    # The frozen context of the pinned release - what a document embeds - so a
    # producer can emit exactly the context of the spec it is validated against.
    frozen = SPEC / "context" / f"{version}.jsonld"
    if frozen.is_file():
        dst = DEST / "context" / frozen.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(frozen, dst)
        copied += 1

    print(f"Vendored {copied} spec file(s) at {version} -> {DEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
