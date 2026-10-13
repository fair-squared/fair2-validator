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
                     "spatialCoverage", "recordSet", "attribution"):
        assert expected in names


def test_golden_full_conforms(spec, golden):
    rep = validate(golden, spec)
    assert rep.shacl_conforms is True, rep.render()


def test_golden_per_subschema_conforms(spec, golden):
    for name in ("methodSection", "dataArticle", "distribution", "spatialCoverage", "recordSet"):
        rep = validate(golden, spec, [name])
        assert rep.shacl_conforms is True, f"{name}:\n{rep.render()}"


def _dataset(doc):
    """The Dataset node.

    From spec v1.4.0 the document is single-rooted: the Dataset's properties
    sit at the top level and there is no @graph wrapper. Older documents are
    still accepted here so the tests work either way.
    """
    def _is_dataset(n):
        t = n.get("@type")
        return t == "Dataset" or (isinstance(t, list) and "Dataset" in t)

    if _is_dataset(doc):
        return doc
    for n in doc.get("@graph", []):
        if _is_dataset(n):
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
    # a field's unit is reached through an @id-bearing container but the
    # violation lands on a node with no @id of its own
    field = ds["recordSet"][0]["field"][0]
    source = field["source"]              # a cr:source object with no @id
    assert "@id" not in source
    source.pop("fileObject", None)        # required by SourceShape -> violation
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
        # locus names the nearest @id-bearing container by type, not a bnode id
        assert " in " in f.locus, f.locus
        assert re.search(r" in \w+ <https?://", f.locus), f.locus


def test_meta_conformsto_matches_pinned_spec(spec, golden):
    """The golden example declares the same spec version the validator is pinned to."""
    ok, findings = check_meta(golden, spec.version)
    assert ok is True
    assert not [f for f in findings if f.severity == "warning"], [f.message for f in findings]


def test_meta_conformsto_version_mismatch_warns(spec, golden, tmp_path):
    """A document declaring a different spec version passes but is flagged (warning)."""
    doc = json.loads(golden.read_text())
    ds = _dataset(doc)
    ds["conformsTo"] = ["http://mlcommons.org/croissant/1.0", "https://fair2.ai/spec/v0.0.1"]
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


def test_missing_spec_conformsto_is_a_shacl_violation(spec, golden, tmp_path):
    """Absence is DatasetShape's job, not check_meta's.

    Up to spec v1.3.0 the spec version lived in a non-RDF _meta block that
    SHACL could not see, so this module enforced it. From v1.4.0 it is a
    dct:conformsTo value with a pattern constraint, and reporting it here too
    would surface one defect under two rule names.
    """
    doc = json.loads(golden.read_text())
    ds = _dataset(doc)
    ds["conformsTo"] = "http://mlcommons.org/croissant/1.0"  # Croissant only
    broken = tmp_path / "no_spec_conformsto.json"
    broken.write_text(json.dumps(doc))

    ok, findings = check_meta(broken)
    assert ok is True and findings == []          # check_meta stays quiet

    rep = validate(broken, spec)
    assert rep.shacl_conforms is False            # SHACL catches it
    assert any("conformsTo" in f.message for f in rep.findings), rep.render()


def test_legacy_meta_block_is_flagged(spec, golden, tmp_path):
    """A pre-v1.4.0 document still carrying _meta gets a migration warning."""
    doc = json.loads(golden.read_text())
    doc["_meta"] = {"conformsTo": "https://fair2.ai/spec/v1.3.0", "version": "1.0.0"}
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(doc))

    ok, findings = check_meta(legacy)
    assert ok is True
    assert any("_meta" in f.message and f.severity == "warning" for f in findings), \
        [f.message for f in findings]


def test_malformed_spec_conformsto_is_a_shacl_violation(spec, golden, tmp_path):
    """A spec conformsTo that is not vX.Y.Z fails the DatasetShape pattern."""
    doc = json.loads(golden.read_text())
    ds = _dataset(doc)
    ds["conformsTo"] = ["http://mlcommons.org/croissant/1.0", "https://fair2.ai/spec/latest"]
    broken = tmp_path / "bad_conformsto.json"
    broken.write_text(json.dumps(doc))

    rep = validate(broken, spec)
    assert rep.shacl_conforms is False, rep.render()


def _golden_variant(golden, tmp_path, mutate):
    doc = json.loads(golden.read_text())
    mutate(doc)
    out = tmp_path / "variant.json"
    out.write_text(json.dumps(doc))
    return out


def _inline_context(doc):
    ctx = doc["@context"]
    return next(el for el in ctx if isinstance(el, dict)) if isinstance(ctx, list) else ctx


@pytest.mark.parametrize("schemas", [None, ["recordSet"], ["recordSet", "field"]])
def test_each_problem_is_reported_once_and_located(spec, golden, tmp_path, schemas):
    """A field is reached directly (FieldShape's target) and through its record
    set (sh:node), so pyshacl reports a missing description twice, the second
    time inside a 'does not conform to FieldShape' wrapper.  One finding, with
    the JSON location to edit."""
    variant = _golden_variant(golden, tmp_path,
                              lambda d: d["recordSet"][0]["field"][0].pop("description"))
    rep = validate(variant, spec, schemas)
    hits = [f for f in rep.findings if f.locus.endswith("→ description")]
    assert len(hits) == 1, [f.message for f in rep.findings]
    assert hits[0].json_path == "$.recordSet[0].field[0].description"
    assert not any("does not conform to Shape" in f.message for f in rep.findings)


def test_golden_has_no_croissant_rule_violations(golden):
    """The spec's own reference example must satisfy the Compatibility Rules as
    the spec states them; a stale rule list flagged three coercions it needs."""
    assert [f.message for f in static_rules(golden) if f.severity == "violation"] == []


def test_conformsto_coerced_to_iri_is_flagged(golden, tmp_path):
    """The Rule 2 case confirmed to break loading."""
    variant = _golden_variant(golden, tmp_path, lambda d: _inline_context(d).update(
        conformsTo={"@id": "dct:conformsTo", "@type": "@id"}))
    rule2 = [f for f in static_rules(variant) if f.rule == "mlcroissant Rule 2"]
    assert len(rule2) == 1 and "conformsTo" in rule2[0].message


def test_peer_artifact_coercion_is_not_flagged(golden):
    """dataArticle / dataPortal / dataArchive left Rule 2 in spec v1.4.0."""
    ctx = _inline_context(json.loads(golden.read_text()))
    assert ctx["dataArticle"].get("@type") == "@id"   # the spec relies on it
    assert not [f for f in static_rules(golden) if "dataArticle" in f.message]
