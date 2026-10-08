"""Sub-schema map and SHACL shape dependency resolution.

Lets the validator check the full document OR an individual sub-schema
(e.g. just `methodSection`) by loading only the relevant shape file(s) plus
their transitive `sh:node` dependency closure (common.ttl is always included).
"""
from __future__ import annotations

from pathlib import Path
import rdflib

SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")

PREFIXES = {
    "fair2s": "https://fair2.ai/shapes/",
    "fair2": "https://fair2.ai/ns/",
    "schema": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/",
    "prov": "http://www.w3.org/ns/prov#",
}


def expand(curie: str) -> str:
    if curie and ":" in curie and not curie.startswith("http"):
        pfx, local = curie.split(":", 1)
        if pfx in PREFIXES:
            return PREFIXES[pfx] + local
    return curie


# name -> (shape_files, shape_iri, target_class|None, dataset_property|None)
# target_class None  => helper shape: validator injects sh:targetNode for the
#                       node(s) reached via dataset_property.
SHAPE_MAP: dict[str, dict] = {
    "dataset":         dict(files=["dataset.ttl"],      shape="fair2s:DatasetShape",          target="schema:Dataset",          prop=None),
    "distribution":    dict(files=["distribution.ttl"], shape="schema:DistributionShape",     target="cr:FileObject",           prop="schema:distribution"),
    "recordSet":       dict(files=["recordset.ttl"],    shape="fair2s:RecordSetShape",        target="cr:RecordSet",            prop="cr:recordSet"),
    "field":           dict(files=["recordset.ttl"],    shape="fair2s:FieldShape",            target="cr:Field",                prop=None),
    "methodSection":   dict(files=["method.ttl"],       shape="fair2s:MethodSectionShape",    target="fair2:Section",           prop="fair2:methodSection"),
    "methodStep":      dict(files=["method.ttl"],       shape="fair2s:MethodStepShape",       target="fair2:Step",              prop=None),
    "substep":         dict(files=["method.ttl"],       shape="fair2s:MethodSubstepShape",    target="fair2:Substep",           prop=None),
    "dataArticle":     dict(files=["data_article.ttl"], shape="fair2s:ArticleShape",          target="schema:ScholarlyArticle", prop="fair2:dataArticle"),
    "dataPortal":      dict(files=["data_portal.ttl"],  shape="fair2s:DataPortalShape",       target="fair2:DataPortal",        prop="fair2:dataPortal"),
    "dataArchive":     dict(files=["data_archive.ttl"], shape="fair2s:DataArchiveShape",      target="fair2:DataArchive",       prop="fair2:dataArchive"),
    "attribution":     dict(files=["contributor.ttl"],  shape="fair2s:AttributionShape",      target="prov:Attribution",        prop="prov:qualifiedAttribution"),
    "certification":   dict(files=["certification.ttl"],shape="fair2s:CertificationShape",    target="fair2:Certification",     prop=None),
    "creator":         dict(files=["contributor.ttl"],  shape="fair2s:PersonOrOrganizationShape", target=None,                  prop="schema:creator"),
    "spatialCoverage": dict(files=["coverage.ttl"],     shape="fair2s:SpatialCoverageShape",  target=None,                      prop="schema:spatialCoverage"),
    "accessRights":    dict(files=["access_rights.ttl"],shape="fair2s:AccessRightsShape",     target="schema:DefinedTerm",      prop="schema:accessRights"),
    "changeLog":       dict(files=["changelog.ttl"],    shape="fair2s:UpdateActionShape",     target="schema:UpdateAction",     prop="fair2:changeLog"),
    "domain":          dict(files=["domain.ttl"],       shape="fair2s:DomainShape",           target=None,                      prop="fair2:domain"),
    "funding":         dict(files=["common.ttl"],       shape="fair2s:GrantShape",            target=None,                      prop="schema:funding"),
    "subjectOf":       dict(files=["common.ttl"],       shape="fair2s:CreativeWorkShape",     target=None,                      prop="schema:subjectOf"),
    "provenance":      dict(files=["provenance.ttl"],   shape=None,                           target=None,                      prop=None),
}

ALWAYS = "common.ttl"  # TextShape, IdentifierShape, etc.


def schema_names() -> list[str]:
    return list(SHAPE_MAP)


def _index(shapes_dir: Path):
    """Return (defines, references): file -> set(shape IRIs defined / referenced via sh:node)."""
    defines: dict[str, set[str]] = {}
    references: dict[str, set[str]] = {}
    for ttl in sorted(shapes_dir.glob("*.ttl")):
        g = rdflib.Graph()
        g.parse(ttl, format="turtle")
        d = {str(s) for s in g.subjects(rdflib.RDF.type, SH.NodeShape) if isinstance(s, rdflib.URIRef)}
        r = {str(o) for o in g.objects(None, SH.node) if isinstance(o, rdflib.URIRef)}
        defines[ttl.name] = d
        references[ttl.name] = r
    return defines, references


def dependency_closure(shapes_dir: Path, start_files: list[str]) -> list[str]:
    """All shape files needed to evaluate the given start files (transitive sh:node)."""
    defines, references = _index(shapes_dir)
    iri_to_file = {iri: fn for fn, iris in defines.items() for iri in iris}
    needed = set(start_files) | {ALWAYS}
    frontier = list(needed)
    while frontier:
        fn = frontier.pop()
        for ref_iri in references.get(fn, ()):  # shapes this file points at
            f2 = iri_to_file.get(ref_iri)
            if f2 and f2 not in needed:
                needed.add(f2)
                frontier.append(f2)
    return [f for f in sorted(needed) if (shapes_dir / f).is_file()]
