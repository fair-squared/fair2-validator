"""Structured findings and reports for FAIR2 validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


SEVERITY_ORDER = {"violation": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    source: str          # "shacl" | "croissant" | "rule"
    severity: str        # "violation" | "warning" | "info"
    message: str
    locus: str = ""      # focus node / path / json pointer
    rule: str = ""       # e.g. "mlcroissant Rule 2"
    suggestion: str = "" # advisory hint (never auto-applied)


@dataclass
class Report:
    target: str
    spec_version: str
    scope: str                       # "full" or comma-joined sub-schema names
    shacl_conforms: Optional[bool] = None
    croissant_ok: Optional[bool] = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        shacl = self.shacl_conforms is not False
        croissant = self.croissant_ok is not False
        return shacl and croissant

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))

    def render(self) -> str:
        icon = {"violation": "✗", "warning": "!", "info": "i"}
        lines = [
            f"FAIR² validation report",
            f"  target:  {self.target}",
            f"  spec:    {self.spec_version}",
            f"  scope:   {self.scope}",
        ]
        if self.shacl_conforms is not None:
            lines.append(f"  SHACL:   {'CONFORMS' if self.shacl_conforms else 'FAILED'}")
        if self.croissant_ok is not None:
            lines.append(f"  Croissant: {'LOADS' if self.croissant_ok else 'FAILED'}")
        lines.append("")
        if not self.findings:
            lines.append("  No issues found. ✅")
            return "\n".join(lines)
        for f in self.sorted_findings():
            lines.append(f"  {icon.get(f.severity, '?')} [{f.source}] {f.message}")
            if f.locus:
                lines.append(f"      at: {f.locus}")
            if f.rule:
                lines.append(f"      rule: {f.rule}")
            if f.suggestion:
                lines.append(f"      hint: {f.suggestion}")
        lines.append("")
        lines.append(f"  {len(self.findings)} finding(s).")
        return "\n".join(lines)
