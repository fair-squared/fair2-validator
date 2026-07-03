"""Deterministic SHACL validation: full document or individual sub-schemas."""
from __future__ import annotations

from pathlib import Path
import rdflib
from pyshacl import validate as pyshacl_validate

from .spec import Spec
from .report import Report, Finding
from . import shapes as S

SH = S.SH


def _load_data(target: Path) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(target), format="json-ld")
    return g


def _merge_shapes(shapes_dir: Path, files: list[str]) -> rdflib.Graph:
    g = rdflib.Graph()
    for fn in files:
        g.parse(str(shapes_dir / fn), format="turtle")
    return g


def _dataset_node(data: rdflib.Graph) -> rdflib.term.Node | None:
    return data.value(predicate=rdflib.RDF.type, object=S.expand("schema:Dataset"))


def _inject_target_nodes(shapes_g: rdflib.Graph, data: rdflib.Graph,
                         shape_iri: str, dataset_property: str) -> int:
    """For a helper shape (no targetClass), target the node(s) reached from the
    Dataset via `dataset_property`. Returns how many target nodes were added."""
    ds = _dataset_node(data)
    if ds is None:
        return 0
    prop = rdflib.URIRef(S.expand(dataset_property))
    shape = rdflib.URIRef(S.expand(shape_iri))
    n = 0
    for obj in data.objects(ds, prop):
        shapes_g.add((shape, SH.targetNode, obj))
        n += 1
    return n


def _findings_from_report(report_graph: rdflib.Graph, source: str) -> list[Finding]:
    out: list[Finding] = []
    q = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT ?focus ?path ?msg ?sev WHERE {
      ?r a sh:ValidationResult ; sh:focusNode ?focus .
      OPTIONAL { ?r sh:resultPath ?path }
      OPTIONAL { ?r sh:resultMessage ?msg }
      OPTIONAL { ?r sh:resultSeverity ?sev }
    }"""
    for row in report_graph.query(q):
        path = str(row.path).split("/")[-1].split("#")[-1] if row.path else ""
        sev = "violation"
        if row.sev and str(row.sev).endswith("Warning"):
            sev = "warning"
        elif row.sev and str(row.sev).endswith("Info"):
            sev = "info"
        msg = str(row.msg) if row.msg else "constraint violation"
        out.append(Finding(source=source, severity=sev, message=msg,
                           locus=f"{row.focus}" + (f" → {path}" if path else "")))
    return out


def validate(target: Path, spec: Spec, schemas: list[str] | None = None) -> Report:
    target = Path(target)
    scope = "full" if not schemas or schemas == ["dataset"] else ",".join(schemas)
    rep = Report(target=str(target), spec_version=spec.version, scope=scope)
    data = _load_data(target)

    if not schemas or schemas == ["dataset"]:
        # full validation: all shapes, auto-targeted
        files = sorted(p.name for p in spec.shapes_dir.glob("*.ttl"))
        shapes_g = _merge_shapes(spec.shapes_dir, files)
        conforms, rg, _ = pyshacl_validate(data, shacl_graph=shapes_g, advanced=True,
                                           inplace=False)
        rep.shacl_conforms = bool(conforms)
        if not conforms:
            rep.findings.extend(_findings_from_report(rg, "shacl"))
        return rep

    rep.shacl_conforms = True
    for name in schemas:
        if name not in S.SHAPE_MAP:
            rep.add(Finding("shacl", "warning", f"unknown sub-schema '{name}'",
                            suggestion=f"known: {', '.join(S.schema_names())}"))
            continue
        spec_entry = S.SHAPE_MAP[name]
        files = S.dependency_closure(spec.shapes_dir, spec_entry["files"])
        shapes_g = _merge_shapes(spec.shapes_dir, files)
        if spec_entry["target"] is None and spec_entry["prop"]:
            added = _inject_target_nodes(shapes_g, data, spec_entry["shape"], spec_entry["prop"])
            if added == 0:
                rep.add(Finding("shacl", "info",
                                f"[{name}] no nodes found via {spec_entry['prop']}; nothing to check"))
                continue
        conforms, rg, _ = pyshacl_validate(data, shacl_graph=shapes_g, advanced=True,
                                           inplace=False)
        if not conforms:
            rep.shacl_conforms = False
            for f in _findings_from_report(rg, "shacl"):
                f.message = f"[{name}] {f.message}"
                rep.add(f)
    return rep
