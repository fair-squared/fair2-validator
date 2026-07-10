"""Detection tests — run offline against the pinned fair2-spec golden example."""
import json
import re
import pytest

from fair2_validator.spec import find_spec, SpecNotFound
from fair2_validator.validate import validate
from fair2_validator.croissant import static_rules, check_croissant
from fair2_validator.meta import check_meta
from fair2_validator import shapes as S


@pytest.fixture(scope="session")
def spec():
    try:
        return find_spec()
    except SpecNotFound as e:
        pytest.skip(f"fair2-spec not available: {e}")


@pytest.fixture(scope="session")
def golden(spec):
    return spec.example_file


def test_list_schemas():
    names = S.schema_names()
    for expected in ("dataset", "methodSection", "dataArticle", "distribution",
                     "spatialCoverage", "recordSet", "contributor"):
        assert expected in names


def test_golden_full_conforms(spec, golden):
    rep = validate(golden, spec)
    assert rep.shacl_conforms is True, rep.render()


def test_golden_per_subschema_conforms(spec, golden):
    for name in ("methodSection", "dataArticle", "distribution", "spatialCoverage", "recordSet"):
        rep = validate(golden, spec, [name])
        assert rep.shacl_conforms is True, f"{name}:\n{rep.render()}"


def _dataset(doc):
    for n in doc["@graph"]:
        t = n.get("@type")
        if t == "Dataset" or (isinstance(t, list) and "Dataset" in t):
            return n
    raise AssertionError("no Dataset node")


def test_subschema_isolation(spec, golden, tmp_path):
    """A broken method section is caught under methodSection but not under distribution."""
    doc = json.loads(golden.read_text())
    _dataset(doc)["methodSection"][0].pop("name", None)  # break it
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc))

    assert validate(broken, spec, ["methodSection"]).shacl_conforms is False
    assert validate(broken, spec, ["distribution"]).shacl_conforms is True


def test_shacl_finding_has_json_path(spec, golden, tmp_path):
    """A SHACL violation points at the exact JSON location that needs editing."""
    doc = json.loads(golden.read_text())
    ms = _dataset(doc)["methodSection"][0]
    ms.pop("name", None)  # force a minCount violation on schema:name
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc))

    rep = validate(broken, spec, ["methodSection"])
    assert rep.shacl_conforms is False
    paths = [f.json_path for f in rep.findings if f.json_path]
    assert paths, rep.render()
    assert all(p.startswith("$") for p in paths)
    assert any(p.endswith(".name") and "methodSection" in p for p in paths), paths


def test_blank_node_falls_back_to_container_path(spec, golden, tmp_path):
    """A violation on a blank node (no @id) reports the full path of the
    nearest @id-bearing container it belongs to, not an empty path."""
    doc = json.loads(golden.read_text())
    ds = _dataset(doc)
    person = ds["contributor"][0]  # a schema:Person with no @id -> blank node
    assert "@id" not in person
    person.pop("schema:name", None)
    person.pop("name", None)
    person["schema:email"] = 12345  # wrong datatype forces a violation
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc))

    rep = validate(broken, spec)
    assert rep.shacl_conforms is False
    blank = [f for f in rep.findings
             if f.source == "shacl" and f.locus and not f.locus.startswith("http")]
    assert blank, rep.render()
    for f in blank:
        assert f.json_path.startswith("$"), f.json_path
        # locus is human-readable: names the @type/name + container, never a raw bnode id
        assert not re.match(r"^N[0-9a-f]{16,}", f.locus), f.locus
        assert " in " in f.locus and "Dataset" in f.locus, f.locus


def test_meta_conformsto_matches_pinned_spec(spec, golden):
    """The golden example declares the same spec version the validator is pinned to."""
    ok, findings = check_meta(golden, spec.version)
    assert ok is True
    assert not [f for f in findings if f.severity == "warning"], [f.message for f in findings]


def test_meta_conformsto_version_mismatch_warns(spec, golden, tmp_path):
    """A document declaring a different spec version passes but is flagged (warning)."""
    doc = json.loads(golden.read_text())
    doc["_meta"]["conformsTo"] = "https://fair2.ai/spec/v0.0.1"
    other = tmp_path / "other.json"
    other.write_text(json.dumps(doc))

    ok, findings = check_meta(other, spec.version)
    assert ok is True  # structurally valid — mismatch is advisory, not a failure
    warns = [f for f in findings if f.severity == "warning"]
    assert len(warns) == 1, [f.message for f in findings]
    assert "v0.0.1" in warns[0].message and spec.version in warns[0].message
    # without a pinned version, no mismatch check (back-compat)
    assert check_meta(other) == (True, [])


def test_croissant_cycle_detected(spec, golden, tmp_path):
    """Introducing a self-referential url is flagged by the static Rule checks."""
    doc = json.loads(golden.read_text())
    ds = _dataset(doc)
    ds["url"] = {"@id": ds["@id"]}  # self-reference cycle
    broken = tmp_path / "cycle.json"
    broken.write_text(json.dumps(doc))
    findings = static_rules(broken)
    assert any("cycle" in f.message.lower() for f in findings), [f.message for f in findings]


def test_golden_croissant_loads(spec, golden):
    ok, _ = check_croissant(golden)
    if ok is None:
        pytest.skip("mlcroissant not installed")
    assert ok is True


def test_golden_meta_conformsto(spec, golden):
    ok, findings = check_meta(golden)
    assert ok is True, [f.message for f in findings]


def test_meta_missing_conformsto_flagged(spec, golden, tmp_path):
    doc = json.loads(golden.read_text())
    doc.get("_meta", {}).pop("conformsTo", None)
    broken = tmp_path / "no_conformsto.json"
    broken.write_text(json.dumps(doc))
    ok, findings = check_meta(broken)
    assert ok is False
    assert any("conformsTo" in f.message for f in findings)
    assert all(f.json_path.startswith("$._meta") for f in findings)


def test_meta_malformed_conformsto_flagged(spec, golden, tmp_path):
    doc = json.loads(golden.read_text())
    doc["_meta"]["conformsTo"] = "http://mlcommons.org/croissant/1.0"  # not a fair2 spec URL
    broken = tmp_path / "bad_conformsto.json"
    broken.write_text(json.dumps(doc))
    ok, _ = check_meta(broken)
    assert ok is False
