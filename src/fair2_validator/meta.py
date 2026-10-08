"""Check the FAIR² spec version a document declares.

Up to spec v1.3.0 this lived in a root-level `_meta` block that sat outside
`@graph`, so it was not an RDF node and SHACL could not constrain it; this
module enforced it with a plain JSON check. From v1.4.0 the block is gone and
the spec version is one of the Dataset's `dct:conformsTo` values, which
`fair2s:DatasetShape` requires with a pattern — so the *requirement* is now
SHACL's job and is not duplicated here.

What remains is the part SHACL cannot do: comparing the version the document
declares against the version of the spec this validator is pinned to. A
document can be perfectly valid and still be checked against shapes it never
claimed to conform to.
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


def _spec_conforms_to(doc: dict) -> str | None:
    """The FAIR² spec URL among the Dataset's conformsTo values, if present.

    conformsTo is multi-valued: it carries the Croissant version alongside the
    FAIR² spec version. Only the latter matches SPEC_URL_RE.
    """
    for container in (doc, *(n for n in doc.get("@graph", []) if isinstance(n, dict))):
        value = container.get("conformsTo")
        for candidate in (value if isinstance(value, list) else [value]):
            if isinstance(candidate, str) and SPEC_URL_RE.match(candidate):
                return candidate
    return None


def check_meta(target: Path, spec_version: str | None = None) -> tuple[bool, list[Finding]]:
    """Return (ok, findings) for the declared spec version.

    Never fails on absence: `fair2s:DatasetShape` already requires a spec
    `conformsTo` and will report it as a SHACL violation. Duplicating that here
    would report the same defect twice under two rule names.
    """
    doc = json.loads(Path(target).read_text())
    findings: list[Finding] = []

    if isinstance(doc.get("_meta"), dict):
        findings.append(Finding(
            "meta", "warning",
            "document carries a root-level `_meta` block, removed in spec v1.4.0",
            json_path="$._meta", rule="fair2 _meta",
            suggestion="move its fields onto the Dataset: conformsTo gains the spec "
                       "version alongside the Croissant one, and version/dateModified "
                       "become fair2:metadataVersion and fair2:metadataModified."))

    declared_url = _spec_conforms_to(doc)
    if declared_url is None:
        # Absent is a SHACL violation, reported there. Say nothing here.
        return True, findings

    if spec_version and VERSION_RE.match(spec_version):
        declared = _declared_version(declared_url)
        if declared != spec_version:
            findings.append(Finding(
                "meta", "warning",
                f"document declares spec {declared}, but this validator is pinned to {spec_version}",
                json_path="$.conformsTo", rule="fair2 conformsTo",
                suggestion=(f"validate against spec {declared} (--spec / FAIR2_SPEC_PATH), "
                            f"or update conformsTo to {spec_version} if the document "
                            f"targets the pinned spec.")))
    return True, findings
