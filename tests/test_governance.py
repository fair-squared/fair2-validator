"""Governance blocks (spec v1.7.0): checked only when present, against the
vendored governance JSON Schema and the spec's reading rules."""
import json

import pytest

from fair2_validator.governance import check_governance
from fair2_validator.spec import SpecNotFound, find_spec


@pytest.fixture(scope="session")
def spec():
    try:
        return find_spec()
    except SpecNotFound as e:
        pytest.skip(f"fair2-spec not available: {e}")


@pytest.fixture
def check(spec, tmp_path):
    def run(doc):
        path = tmp_path / "fair2.json"
        path.write_text(json.dumps({"@type": "Dataset", "name": "x", **doc}))
        return check_governance(path, spec.governance_schema)
    return run


def _messages(findings, severity):
    return [f.message for f in findings if f.severity == severity]


def test_the_reference_example_states_an_open_tier_cleanly(spec):
    ok, findings = check_governance(spec.example_file, spec.governance_schema)
    assert ok is True and findings == []


def test_governance_is_optional(check):
    assert check({}) == (None, [])


def test_a_tier_outside_l0_to_l2_is_a_violation(check):
    ok, findings = check({"fair2:access": {"tier": "L3"}})
    assert ok is False
    [f] = [f for f in findings if f.severity == "violation"]
    assert f.json_path == '$["fair2:access"].tier' and "'L3'" in f.message


def test_a_restriction_on_all_l0_data_is_a_violation(check):
    ok, findings = check({"fair2:access": {"tier": "L0"},
                          "fair2:embargo": {"until": "2027-01-01T00:00:00Z"}})
    assert ok is False
    assert any("all L0" in m and "fair2:embargo" in m for m in _messages(findings, "violation"))


def test_governed_assets_make_a_restriction_valid(check):
    """A mixed dataset: L0 overall, but variableGovernance's default floor (L1)
    governs the unlisted columns, so the embargo has data to act on."""
    ok, findings = check({"fair2:access": {"tier": "L0"},
                          "fair2:embargo": {"until": "2027-01-01T00:00:00Z"},
                          "fair2:variableGovernance": {"variables": {"year": {"encrypted": False}}}})
    assert ok is True and _messages(findings, "violation") == []


def test_a_restriction_without_a_tier_is_a_warning(check):
    ok, findings = check({"fair2:access": {"environment": "secureHuman"},
                          "fair2:variableGovernance": {"floorTier": "L2"}})
    assert ok is True
    assert any("states no tier" in m for m in _messages(findings, "warning"))


def test_a_block_without_a_tier_reads_as_open(check):
    ok, findings = check({"fair2:access": {}})
    assert ok is True and any("reads as L0" in m for m in _messages(findings, "info"))


def test_agreement_level_fields_in_constraints_point_to_access(check):
    """The deprecated Agreement Level pages put identity and environment in
    fair2:constraints; the governance schema rejects them there."""
    ok, findings = check({"fair2:access": {"tier": "L1"},
                          "fair2:constraints": {"identity": "ORCID", "aiTraining": "allowed"}})
    assert ok is False
    [f] = [f for f in findings if f.severity == "violation"]
    assert "belong in fair2:access" in f.suggestion


@pytest.mark.parametrize("rights", [
    {"@id": "https://fair2.ai/ns/AgreementLevels1"},
    {"@id": "fair2:AgreementLevel3"},
    "https://fair2.ai/spec/AgreementLevels/1",
])
def test_a_deprecated_agreement_level_is_reported(check, rights):
    ok, findings = check({"accessRights": rights})
    assert ok is True
    assert any("Agreement Level" in m and "deprecated" in m for m in _messages(findings, "warning"))


def test_access_rights_must_agree_with_the_tier(check):
    _, findings = check({"accessRights": {"@id": "fair2:L2"}, "fair2:access": {"tier": "L0"}})
    assert any("names L2 but fair2:access reads as L0" in m for m in _messages(findings, "warning"))


def test_an_unprefixed_key_is_not_read_as_governance(check):
    _, findings = check({"access": {"tier": "L1"}})
    assert any("without its prefix" in m for m in _messages(findings, "warning"))


def test_the_cli_reports_governance(spec, tmp_path, capsys):
    from fair2_validator.cli import main
    doc = json.loads(spec.example_file.read_text())
    doc["fair2:access"] = {"tier": "L9"}
    bad = tmp_path / "fair2.json"
    bad.write_text(json.dumps(doc))
    assert main([str(bad), "--schema", "governance", "--spec", str(spec.root)]) == 1
    out = capsys.readouterr().out
    assert "Governance: FAILED" in out and "[governance]" in out
