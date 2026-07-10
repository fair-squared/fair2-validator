"""Deterministic check of the root-level `_meta` block.

`_meta` is document/package metadata that sits outside `@graph`, so it is not an
RDF graph node and cannot be constrained by SHACL. This module enforces the
required `_meta.conformsTo` (the FAIR² spec version) with a plain JSON check.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .report import Finding

# https://fair2.ai/spec/vMAJOR.MINOR.PATCH  (matches the fair2-spec release tag)
SPEC_URL_RE = re.compile(r"^https?://fair2\.ai/spec/v\d+\.\d+\.\d+/?$")


def check_meta(target: Path) -> tuple[bool, list[Finding]]:
    """Return (ok, findings). Requires _meta.conformsTo to be a spec-version URL."""
    doc = json.loads(Path(target).read_text())
    meta = doc.get("_meta")
    if not isinstance(meta, dict):
        return False, [Finding(
            "meta", "violation", "document has no root-level `_meta` block",
            json_path="$._meta", rule="fair2 _meta",
            suggestion='add a _meta block, e.g. {"conformsTo": "https://fair2.ai/spec/v1.2.0", "version": "...", ...}')]
    ct = meta.get("conformsTo")
    if not ct:
        return False, [Finding(
            "meta", "violation", "_meta.conformsTo is missing (declares the FAIR² spec version)",
            json_path="$._meta.conformsTo", rule="fair2 _meta.conformsTo",
            suggestion="set _meta.conformsTo to https://fair2.ai/spec/vX.Y.Z matching the fair2-spec release tag.")]
    if isinstance(ct, str) and SPEC_URL_RE.match(ct):
        return True, []
    return False, [Finding(
        "meta", "violation", f"_meta.conformsTo '{ct}' is not a FAIR² spec-version URL",
        json_path="$._meta.conformsTo", rule="fair2 _meta.conformsTo",
        suggestion="use https://fair2.ai/spec/vX.Y.Z (matching a fair2-spec release tag).")]
