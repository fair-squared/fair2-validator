"""Packaging guarantees: the spec vendored into the wheel is usable and current."""
import filecmp
from pathlib import Path

import pytest

from fair2_validator.spec import Spec, find_spec, SpecNotFound
from fair2_validator.validate import validate

BUNDLED = Path(__file__).resolve().parents[1] / "src" / "fair2_validator" / "_spec_data"
SUBMODULE = Path(__file__).resolve().parents[1] / "fair2-spec"


def test_bundled_spec_exists_and_is_self_contained():
    """The vendored copy must stand alone: shapes, example, and a VERSION."""
    assert (BUNDLED / "shapes" / "turtle" / "dataset.ttl").is_file()
    assert (BUNDLED / "examples" / "example-1" / "borja2025.json").is_file()
    assert (BUNDLED / "VERSION").read_text().strip()


def test_bundled_spec_validates_its_own_example():
    """A pip user (no submodule) gets a working validator off the vendored spec."""
    spec = Spec(root=BUNDLED)
    rep = validate(spec.example_file, spec)
    assert rep.shacl_conforms is True, rep.render()
    assert spec.version.startswith("v"), spec.version


def test_find_spec_bundled_fallback(monkeypatch):
    """With no --spec, no env, and no submodule/sibling matching, find_spec still
    resolves to the vendored copy (the last candidate) rather than raising."""
    monkeypatch.delenv("FAIR2_SPEC_PATH", raising=False)
    import fair2_validator.spec as spec_mod
    # make every candidate but the vendored copy look absent
    monkeypatch.setattr(spec_mod, "_looks_like_spec",
                        lambda p: p.resolve() == BUNDLED.resolve())
    spec = find_spec()
    assert spec.root == BUNDLED.resolve()


@pytest.mark.skipif(not (SUBMODULE / "shapes" / "turtle" / "dataset.ttl").is_file(),
                    reason="fair2-spec submodule not checked out")
def test_bundled_spec_matches_submodule():
    """Guard against drift: the vendored shapes must match the pinned submodule.
    If this fails, run `make sync-spec` and commit the result."""
    src = SUBMODULE / "shapes" / "turtle"
    dst = BUNDLED / "shapes" / "turtle"
    src_files = {p.name for p in src.glob("*.ttl")}
    dst_files = {p.name for p in dst.glob("*.ttl")}
    assert src_files == dst_files, (
        f"vendored shapes out of sync (run `make sync-spec`): "
        f"missing={src_files - dst_files} extra={dst_files - src_files}")
    mismatch = [n for n in src_files if not filecmp.cmp(src / n, dst / n, shallow=False)]
    assert not mismatch, f"vendored shapes differ from submodule (run `make sync-spec`): {mismatch}"


def test_version_matches_pyproject():
    """__version__ and pyproject's version are bumped by hand; keep them together."""
    import re
    import fair2_validator
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert fair2_validator.__version__ == declared
