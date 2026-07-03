"""Detection tests — run offline against the pinned fair2-spec golden example."""
import json
import pytest

from fair2_validator.spec import find_spec, SpecNotFound
from fair2_validator.validate import validate
from fair2_validator.croissant import static_rules, check_croissant
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
