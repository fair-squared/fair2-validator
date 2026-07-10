"""Deterministic check of the root-level `_meta` block.

`_meta` is document/package metadata that sits outside `@graph`, so it is not an
RDF graph node and cannot be constrained by SHACL. This module enforces the
required `_meta.conformsTo` (the FAIR² spec version) with a plain JSON check, and
— when the pinned spec version is known — warns if the document declares
conformance to a *different* version than the one it is being validated against.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .report import Finding

# https://fair2.ai/spec/vMAJOR.MINOR.PATCH  (matches the fair2-spec release tag)
SPEC_URL_RE = re.compile(r"^https?://fair2\.ai/spec/v\d+\.\d+\.\d+/?$")
VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _declared_version(conforms_to: str) -> str:
    """Extract the version tag (vX.Y.Z) from a conformsTo URL."""
    return conforms_to.rstrip("/").rsplit("/", 1)[-1]


def check_meta(target: Path, spec_version: str | None = None) -> tuple[bool, list[Finding]]:
    """Return (ok, findings). Requires _meta.conformsTo to be a spec-version URL.

    When `spec_version` (the pinned fair2-spec version, e.g. "v1.2.0") is given
    and the document declares a *different* version, add an advisory warning: the
    document is structurally valid, but it is being checked against shapes it
    doesn't claim to conform to. The warning does not fail the meta check."""
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
    if not (isinstance(ct, str) and SPEC_URL_RE.match(ct)):
        return False, [Finding(
            "meta", "violation", f"_meta.conformsTo '{ct}' is not a FAIR² spec-version URL",
            json_path="$._meta.conformsTo", rule="fair2 _meta.conformsTo",
            suggestion="use https://fair2.ai/spec/vX.Y.Z (matching a fair2-spec release tag).")]

    # Structurally valid. Warn if it targets a different spec than the pinned one.
    findings: list[Finding] = []
    if spec_version and VERSION_RE.match(spec_version):
        declared = _declared_version(ct)
        if declared != spec_version:
            findings.append(Finding(
                "meta", "warning",
                f"_meta.conformsTo declares {declared}, but this validator is pinned to spec {spec_version}",
                json_path="$._meta.conformsTo", rule="fair2 _meta.conformsTo",
                suggestion=(f"validate against spec {declared} (--spec / FAIR2_SPEC_PATH), "
                            f"or set _meta.conformsTo to {spec_version} if the document targets the pinned spec.")))
    return True, findings
