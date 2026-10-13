"""Deterministic SHACL validation: full document or individual sub-schemas."""
from __future__ import annotations

from pathlib import Path
import rdflib
from pyshacl import validate as pyshacl_validate

from .spec import Spec
from .report import Report, Finding
from .nodepath import NodeIndex
from . import shapes as S

SH = S.SH


def _document_base(target: Path) -> str:
    """The IRI a relative @id resolves against when the context sets no @base:
    the document's own location.  The parser and the JSON index are given the
    same one, so every focus node can be traced back to its place in the JSON."""
    return Path(target).resolve().as_uri()


def _load_data(target: Path) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(target), format="json-ld", publicID=_document_base(target))
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


def _short(term: rdflib.term.Node | None) -> str:
    """Compact an IRI to its last path/fragment segment (schema:Person, etc.)."""
    if term is None:
        return ""
    s = str(term).split("/")[-1].split("#")[-1]
    return s or str(term)


def _named_ancestor(data: rdflib.Graph, node: rdflib.term.Node,
                    _seen: set | None = None) -> tuple[str, str] | None:
    """Climb the data graph from a blank node to the nearest @id-bearing node.

    Returns (ancestor_iri, connecting_property) where connecting_property is the
    compacted predicate on that ancestor leading toward the blank subtree."""
    _seen = _seen if _seen is not None else set()
    if node in _seen:
        return None
    _seen.add(node)
    for parent, pred in data.subject_predicates(object=node):
        if isinstance(parent, rdflib.URIRef):
            return str(parent), _short(pred)
        hit = _named_ancestor(data, parent, _seen)
        if hit:
            return hit
    return None


def _findings_from_report(report_graph: rdflib.Graph, source: str,
                          index: NodeIndex | None = None,
                          data: rdflib.Graph | None = None) -> list[Finding]:
    out: list[Finding] = []
    # Each problem once.  A node reached along two routes - a field is both a
    # target of FieldShape and a value its record set's sh:node checks - fails
    # twice, and the second failure arrives as a wrapper ("does not conform to
    # Shape …") whose sh:detail repeats the first.  Results carrying a detail
    # are skipped: their causes are results in their own right, so nothing is
    # lost, and identical causes are collapsed below.
    q = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT DISTINCT ?focus ?path ?msg ?sev ?comp WHERE {
      ?r a sh:ValidationResult ; sh:focusNode ?focus .
      FILTER NOT EXISTS { ?r sh:detail ?detail }
      OPTIONAL { ?r sh:resultPath ?path }
      OPTIONAL { ?r sh:resultMessage ?msg }
      OPTIONAL { ?r sh:resultSeverity ?sev }
      OPTIONAL { ?r sh:sourceConstraintComponent ?comp }
    }"""
    seen: set[tuple] = set()
    for row in report_graph.query(q):
        key = (row.focus, row.path, row.msg, row.sev, row.comp)
        if key in seen:
            continue
        seen.add(key)
        path = str(row.path).split("/")[-1].split("#")[-1] if row.path else ""
        sev = "violation"
        if row.sev and str(row.sev).endswith("Warning"):
            sev = "warning"
        elif row.sev and str(row.sev).endswith("Info"):
            sev = "info"
        msg = str(row.msg) if row.msg else "constraint violation"
        focus_label = str(row.focus)
        json_path = ""
        if index is not None:
            json_path = index.path_for(str(row.focus), path)
        # Blank node (no @id): don't surface the opaque bnode id. Describe it by
        # its name / @type, and point the JSON path at its nearest @id-bearing
        # container (deepened by the connecting property).
        if isinstance(row.focus, rdflib.BNode) and data is not None:
            name = data.value(row.focus, rdflib.URIRef(S.expand("schema:name")))
            rtype = data.value(row.focus, rdflib.RDF.type)
            focus_label = (f'"{name}"' if name else "") or _short(rtype) or "blank node"
            if not json_path and index is not None:
                anc = _named_ancestor(data, row.focus)
                if anc:
                    anc_type = _short(data.value(rdflib.URIRef(anc[0]), rdflib.RDF.type))
                    focus_label += f" in {anc_type or 'node'} <{anc[0]}>"
                    json_path = index.path_for(anc[0], anc[1])
        out.append(Finding(source=source, severity=sev, message=msg,
                           locus=focus_label + (f" → {path}" if path else ""),
                           json_path=json_path))
    return out


def _ontology_graph(spec: Spec) -> rdflib.Graph | None:
    """The ontology, passed to pyshacl as ont_graph.

    Required, not optional: class targets follow rdf:type/rdfs:subClassOf* over
    the triples the validator holds, so without it a shape named against a
    superclass never reaches its subclass instances and they pass unchecked.
    """
    path = spec.ontology_file
    if not path.is_file():
        return None
    g = rdflib.Graph()
    g.parse(path, format="turtle")
    return g


def validate(target: Path, spec: Spec, schemas: list[str] | None = None) -> Report:
    target = Path(target)
    scope = "full" if not schemas or schemas == ["dataset"] else ",".join(schemas)
    rep = Report(target=str(target), spec_version=spec.version, scope=scope)
    data = _load_data(target)
    index = NodeIndex(target, default_base=_document_base(target))
    ont = _ontology_graph(spec)
    if ont is None:
        rep.add(Finding("shacl", "warning",
                        "ontology not found next to the shapes; class targeting cannot "
                        "follow rdfs:subClassOf, so some nodes will go unchecked",
                        rule="fair2 ontology",
                        suggestion="ensure ontologies/fair2_ontology.ttl ships with the spec"))

    if not schemas or schemas == ["dataset"]:
        # full validation: all shapes, auto-targeted
        files = sorted(p.name for p in spec.shapes_dir.glob("*.ttl"))
        shapes_g = _merge_shapes(spec.shapes_dir, files)
        # allow_warnings: a sh:Warning must be reported without failing the
        # document. The deprecated FAIR2-Validated form relies on this.
        conforms, rg, _ = pyshacl_validate(data, shacl_graph=shapes_g, ont_graph=ont,
                                           advanced=True, inplace=False,
                                           allow_warnings=True)
        rep.shacl_conforms = bool(conforms)
        rep.findings.extend(_findings_from_report(rg, "shacl", index, data))
        return rep

    rep.shacl_conforms = True
    # Overlapping sub-schemas (recordSet's closure includes field) would report
    # a shared problem once per sub-schema; the first to find it reports it.
    reported: set[tuple] = set()
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
        conforms, rg, _ = pyshacl_validate(data, shacl_graph=shapes_g, ont_graph=ont,
                                           advanced=True, inplace=False,
                                           allow_warnings=True)
        if not conforms:
            rep.shacl_conforms = False
        for f in _findings_from_report(rg, "shacl", index, data):
            key = (f.severity, f.message, f.locus)
            if key in reported:
                continue
            reported.add(key)
            f.message = f"[{name}] {f.message}"
            rep.add(f)
    return rep
