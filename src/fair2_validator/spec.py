"""Locate and describe the canonical fair2-spec.

Resolution precedence:
  1. explicit --spec / spec_path argument
  2. FAIR2_SPEC_PATH environment variable
  3. the bundled `fair2-spec/` submodule next to the repo root
  4. a sibling `../fair2-spec` checkout
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
    def example_file(self) -> Path:
        return self.root / "examples" / "example-1" / "borja2025.json"

    @property
    def version(self) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "describe", "--tags", "--always", "--dirty"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
        return "unknown"


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

    for c in candidates:
        c = c.expanduser()
        if _looks_like_spec(c):
            return Spec(root=c.resolve())
    tried = "\n  ".join(str(c) for c in candidates)
    raise SpecNotFound(
        "Could not locate fair2-spec. Tried:\n  " + tried +
        "\nInitialise the submodule (`git submodule update --init`) or set FAIR2_SPEC_PATH."
    )
