"""The report: its findings' order."""
from fair2_validator.report import Finding, Report


def test_findings_in_a_full_order_whatever_order_they_came_in():
    findings = [
        Finding("rule", "violation", "cycle", json_path="$.spatialCoverage[0]"),
        Finding("shacl", "violation", "b", json_path="$.distribution[10]"),
        Finding("shacl", "warning", "w", json_path="$.name"),
        Finding("shacl", "violation", "b", json_path="$.distribution[2]"),
        Finding("shacl", "violation", "a", json_path="$.distribution[2]"),
        Finding("croissant", "violation", "load", json_path=""),
    ]
    expected = [
        ("shacl", "$.distribution[2]", "a"),
        ("shacl", "$.distribution[2]", "b"),
        ("shacl", "$.distribution[10]", "b"),
        ("croissant", "", "load"),
        ("rule", "$.spatialCoverage[0]", "cycle"),
        ("shacl", "$.name", "w"),
    ]
    for order in (findings, findings[::-1]):
        report = Report(target="t", spec_version="1", scope="full", findings=list(order))
        assert [(f.source, f.json_path, f.message) for f in report.sorted_findings()] == expected
