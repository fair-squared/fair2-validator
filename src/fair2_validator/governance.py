"""Governance blocks: `fair2:access`, `fair2:constraints`, ... (spec v1.7.0 on).

The blocks are optional, and each is a JSON value: the spec types it @json and
defines its contents by the governance JSON Schema it vendors
(`schemas/fair2-governance.schema.json`). SHACL cannot see inside them, so they
are checked here, only when present:

- each block against the schema definition of the same name;
- the reading rules the schema cannot express (spec governance.md): a
  restriction on a dataset whose data is all L0, a restriction without a
  stated tier, `accessRights` naming another tier, and the deprecated
  Agreement Levels;
- governance keys written without their prefix, which fall through to
  schema.org instead of being read as governance.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .report import Finding

# Block key -> its definition in the governance schema.
BLOCKS = {
    "fair2:access": "AccessBlock",
    "fair2:constraints": "ConstraintsBlock",
    "fair2:embargo": "EmbargoBlock",
    "fair2:variableGovernance": "VariableGovernanceBlock",
    "fair2:temporalGovernance": "TemporalGovernanceBlock",
    "fair2:agentGovernance": "AgentGovernanceBlock",
    "fair2:consentGovernance": "ConsentGovernanceBlock",
    "fair2:indigenousGovernance": "IndigenousGovernanceBlock",
    "fair2:distributionGovernance": "DistributionGovernanceBlock",
    "fair2:encryption": "EncryptionBlock",
}
RULE = "fair2 governance"
DOCS = "see the spec's Governance chapter (specification/governance.md)"

# The Agreement Levels (deprecated in spec v1.7.0) and their closest tier.
_LEVEL_RE = re.compile(r"AgreementLevels?/?(\d)\b")
_LEVEL_TIER = {"0": "L1 or L2 with fair2:embargo", "1": "L0", "2": "L0", "3": "L2",
               "4": "L1 or L2 with communityGovernance", "5": "L2 with environment secureHuman",
               "6": "L2 with environment computeToData"}
# Fields the Agreement Level pages put in fair2:constraints; they belong in fair2:access.
_ACCESS_FIELDS_IN_CONSTRAINTS = ("identity", "environment", "communityGovernance")


def _path(key: str, parts) -> str:
    out = f'$["{key}"]'
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def _tier(value) -> str | None:
    """A tier as written: None when absent, null or empty (all mean L0)."""
    return value if isinstance(value, str) and value else None


def _restrictions(doc: dict) -> list[str]:
    """What the document declares that only works on governed (L1+) data."""
    found = []
    access = doc.get("fair2:access") if isinstance(doc.get("fair2:access"), dict) else {}
    cg = access.get("communityGovernance")
    cg = cg.get("requirement") if isinstance(cg, dict) else cg
    if cg not in (None, "none", "consultationRecommended"):
        found.append("communityGovernance (+G)")
    if access.get("environment") not in (None, "open"):
        found.append("environment (+S)")
    if access.get("clientCertification") == "required":
        found.append("clientCertification (+C)")
    if (access.get("purposeBinding") or {}).get("requirement") not in (None, "none"):
        found.append("purposeBinding (+P)")
    if (access.get("jurisdiction") or {}).get("requirement") == "required":
        found.append("jurisdiction (+J)")
    if (access.get("queryGovernance") or {}).get("enabled"):
        found.append("queryGovernance (+Q)")
    if doc.get("fair2:embargo"):
        found.append("fair2:embargo")
    consent = doc.get("fair2:consentGovernance")
    if isinstance(consent, dict) and consent.get("enforcement") in ("enforced", "row_level"):
        found.append("fair2:consentGovernance enforcement")
    return found


def _any_governed(doc: dict) -> bool:
    """Whether any data is at L1 or above, by the dataset tier or a finer rule."""
    governed = {"L1", "L2"}
    access = doc.get("fair2:access") if isinstance(doc.get("fair2:access"), dict) else {}
    if _tier(access.get("tier")) in governed:
        return True
    vg = doc.get("fair2:variableGovernance")
    if isinstance(vg, dict):
        # floorTier defaults to L1: a variableGovernance block governs unlisted
        # assets unless it lowers the floor to L0.
        if vg.get("floorTier", "L1") in governed:
            return True
        rules = list((vg.get("variables") or {}).values()) + list((vg.get("assetClasses") or {}).values()) \
            + list(vg.get("partitions") or [])
        if any(isinstance(r, dict) and r.get("tier") in governed for r in rules):
            return True
    tg = doc.get("fair2:temporalGovernance")
    phases = tg.get("phases") if isinstance(tg, dict) else None
    return any(isinstance(p, dict) and p.get("tier") in governed for p in phases or [])


def check_governance(target: Path, schema_path: Path) -> tuple[bool | None, list[Finding]]:
    """Return (ok, findings) for the governance blocks, or (None, []) when the
    document declares none - governance is optional."""
    doc = json.loads(Path(target).read_text())
    if not isinstance(doc, dict):
        return None, []
    findings: list[Finding] = []

    for key in BLOCKS:
        bare = key.split(":", 1)[1]
        if bare in doc and key not in doc:
            findings.append(Finding(
                "governance", "warning",
                f"`{bare}` is written without its prefix, so it is not read as governance "
                f"(it falls through to schema:{bare})",
                json_path=f"$.{bare}", rule=RULE,
                suggestion=f"rename it to `{key}`; {DOCS}"))

    access_rights = doc.get("accessRights") or doc.get("dct:accessRights")
    rights_ids = []
    for r in access_rights if isinstance(access_rights, list) else [access_rights]:
        if isinstance(r, dict):
            rights_ids += [v for v in (r.get("@id"), r.get("url")) if isinstance(v, str)]
        elif isinstance(r, str):
            rights_ids.append(r)
    for rid in rights_ids:
        m = _LEVEL_RE.search(rid)
        if m:
            findings.append(Finding(
                "governance", "warning",
                f"accessRights names Agreement Level {m.group(1)}, deprecated in spec v1.7.0",
                json_path="$.accessRights", rule=RULE,
                suggestion=f"state the tier instead ({_LEVEL_TIER[m.group(1)]}) as "
                           f"fair2:access and an accessRights of fair2:L0/L1/L2; {DOCS}"))
            break

    present = [k for k in BLOCKS if k in doc]
    if not present:
        return (None if not findings else True), findings

    from jsonschema import Draft202012Validator
    schema = json.loads(Path(schema_path).read_text())
    ok = True
    for key in present:
        validator = Draft202012Validator({"$ref": f"#/$defs/{BLOCKS[key]}", "$defs": schema["$defs"]})
        for err in sorted(validator.iter_errors(doc[key]), key=lambda e: list(e.absolute_path)):
            ok = False
            hint = DOCS
            if key == "fair2:constraints" and err.validator == "additionalProperties" and any(
                    f in doc[key] for f in _ACCESS_FIELDS_IN_CONSTRAINTS):
                hint = ("identity, environment and communityGovernance belong in fair2:access "
                        "(the Agreement Level pages put them in fair2:constraints); " + DOCS)
            findings.append(Finding(
                "governance", "violation", f"{key}: {err.message}",
                json_path=_path(key, err.absolute_path), rule=RULE, suggestion=hint))

    access = doc.get("fair2:access")
    tier = _tier(access.get("tier")) if isinstance(access, dict) else None
    restrictions = _restrictions(doc)
    if restrictions and not _any_governed(doc):
        ok = False
        findings.append(Finding(
            "governance", "violation",
            f"{', '.join(restrictions)} declared on a dataset whose data is all L0: "
            f"restrictions act through key release, so they need data at L1 or above",
            json_path='$["fair2:access"].tier', rule=RULE,
            suggestion=f"raise the tier (or the tier of the governed assets), or drop the restriction; {DOCS}"))
    elif restrictions and tier is None:
        findings.append(Finding(
            "governance", "warning",
            f"{', '.join(restrictions)} declared, but fair2:access states no tier, so the dataset reads as L0",
            json_path='$["fair2:access"]', rule=RULE,
            suggestion=f"state the tier explicitly; {DOCS}"))
    elif "fair2:access" in doc and tier is None:
        findings.append(Finding(
            "governance", "info",
            "fair2:access states no tier, so the dataset reads as L0 (open); a governance "
            "service requires an explicit tier to publish",
            json_path='$["fair2:access"]', rule=RULE))

    stated = {m.group(1) for rid in rights_ids for m in [re.search(r"(?:ns/|fair2:)(L[0-2])$", rid)] if m}
    if stated and stated != {tier or "L0"}:
        findings.append(Finding(
            "governance", "warning",
            f"accessRights names {', '.join(sorted(stated))} but fair2:access reads as {tier or 'L0'}",
            json_path="$.accessRights", rule=RULE,
            suggestion=f"make them agree; governance services act on fair2:access.tier; {DOCS}"))
    return ok, findings
