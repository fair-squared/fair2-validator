"""Locate and describe the canonical fair2-spec.

Resolution precedence:
  1. explicit --spec / spec_path argument
  2. FAIR2_SPEC_PATH environment variable
  3. the `fair2-spec/` submodule next to the repo root (dev checkout)
  4. a sibling `../fair2-spec` checkout
  5. the spec vendored inside the package (`_spec_data/`), so a plain
     `pip install fair2-validator` works without the submodule.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from dataclasses import dataclass


class SpecNotFound(Exception):
    pass


@dataclass
class Spec:
    root: Path

    @property
    def shapes_dir(self) -> Path:
        return self.root / "shapes" / "turtle"

    @property
    def context_file(self) -> Path:
        return self.root / "context" / "metadata" / "fair2_context.json"

    @property
    def published_context(self) -> Path:
        """The frozen JSON-LD context of this spec version (`context/vX.Y.Z.jsonld`).

        The context a FAIR2 document embeds or references. Every spec release
        cuts one, so it exists for any tagged version; a producer that emits it
        emits exactly the vocabulary this validator checks against."""
        return self.root / "context" / f"{self.version}.jsonld"

    @property
    def governance_schema(self) -> Path:
        """The JSON Schema for the governance blocks (`fair2:access`, ...), which the
        spec vendors from the FAIR² governance model (spec v1.7.0 on)."""
        return self.root / "schemas" / "fair2-governance.schema.json"

    @property
    def example_file(self) -> Path:
        return self.root / "examples" / "example-1" / "borja2025.json"

    @property
    def ontology_file(self) -> Path:
        """The ontology, which must be loaded alongside the shapes.

        SHACL class targets select instances via rdf:type/rdfs:subClassOf*, and
        that path can only be walked over triples the validator holds. Without
        this file fair2s:ArticleShape never reaches a node typed
        fair2:DataArticle, and the body goes unchecked while the report says the
        document conforms. See the spec's shacl-validation.md.
        """
        return self.root / "ontologies" / "fair2_ontology.ttl"

    @property
    def version(self) -> str:
        """The spec version as a git tag only — never an intermediate
        `<tag>-N-g<sha>` describe string. Releases are tag-only, so a pinned
        spec that isn't exactly on a tag is reported as `untagged@<sha>`.

        A vendored copy states its version in the `VERSION` file written at
        sync time, and that is read first: the copy inside this repository sits
        in the validator's own git checkout, where `git describe` answers with
        the validator's tag, not the spec's. A fair2-spec checkout has no
        `VERSION` file and is described by git."""
        vfile = self.root / "VERSION"                        # vendored copy
        if vfile.is_file():
            v = vfile.read_text().strip()
            if v:
                return v

        def _git(*args: str) -> str:
            try:
                out = subprocess.run(["git", "-C", str(self.root), *args],
                                     capture_output=True, text=True, timeout=5)
                return out.stdout.strip() if out.returncode == 0 else ""
            except Exception:
                return ""
        tag = _git("describe", "--tags", "--exact-match")   # HEAD is exactly a tag
        if tag:
            return tag
        pointed = _git("tag", "--points-at", "HEAD")         # tag(s) at this commit
        if pointed:
            return pointed.splitlines()[0]
        sha = _git("rev-parse", "--short", "HEAD")
        return f"untagged@{sha}" if sha else "unknown"


def _looks_like_spec(p: Path) -> bool:
    return (p / "shapes" / "turtle" / "dataset.ttl").is_file()


def find_spec(spec_path: str | os.PathLike | None = None) -> Spec:
    candidates: list[Path] = []
    if spec_path:
        candidates.append(Path(spec_path))
    env = os.environ.get("FAIR2_SPEC_PATH")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    # repo root is two levels up from src/fair2_validator/
    repo_root = here.parents[2]
    candidates.append(repo_root / "fair2-spec")
    candidates.append(repo_root.parent / "fair2-spec")
    # last resort: the spec vendored inside the installed package
    candidates.append(here.parent / "_spec_data")

    for c in candidates:
        c = c.expanduser()
        if _looks_like_spec(c):
            return Spec(root=c.resolve())
    tried = "\n  ".join(str(c) for c in candidates)
    raise SpecNotFound(
        "Could not locate fair2-spec. Tried:\n  " + tried +
        "\nInitialise the submodule (`git submodule update --init`) or set FAIR2_SPEC_PATH."
    )
