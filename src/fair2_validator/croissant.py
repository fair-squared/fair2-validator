"""mlcroissant load check + static mlcroissant-compatibility diagnostics.

The load check runs `mlcroissant.Dataset(jsonld=...)`. When it fails (or as a
pre-flight), the static checks implement the documented Compatibility Rules
(docs/integration/ml-croissant.md, Rules 1-6): reference cycles, @type:@id on
cross-graph properties, checksum predicate, FileObject @type, and the
distribution<->source @id match.
"""
from __future__ import annotations

import json
from pathlib import Path

from .report import Finding
from .nodepath import NodeIndex

CROSS_GRAPH_PROPS = {
    "citation", "dataArticle", "dataPortal", "dataArchive", "dataset",
    "wasAssociatedWith", "wasGeneratedBy", "wasDerivedFrom", "wasRevisionOf",
    "generated", "next", "url",
}


def _load_json(target: Path) -> dict:
    return json.loads(Path(target).read_text())


def _context_obj(doc: dict) -> dict:
    ctx = doc.get("@context")
    if isinstance(ctx, dict):
        return ctx
    if isinstance(ctx, list):
        merged = {}
        for el in ctx:
            if isinstance(el, dict):
                merged.update(el)
        return merged
    return {}


def _ref_cycles(doc: dict) -> list[list[str]]:
    """Detect cycles in the @id reference graph (Rules 1 & 3)."""
    edges: dict[str, set[str]] = {}

    def refs(obj, acc: set):
        if isinstance(obj, dict):
            if "@id" in obj and set(obj.keys()) <= {"@id", "@type"}:
                acc.add(obj["@id"]); return
            for k, v in obj.items():
                if k not in ("@id", "@context"):
                    refs(v, acc)
        elif isinstance(obj, list):
            for v in obj:
                refs(v, acc)

    def index(obj):
        if isinstance(obj, dict):
            if obj.get("@id") and len(obj) > 1:
                acc: set = set(); refs(obj, acc); edges[obj["@id"]] = acc
            for k, v in obj.items():
                if k not in ("@id", "@context"):
                    index(v)
        elif isinstance(obj, list):
            for v in obj:
                index(v)

    index(doc)
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    cycles: list[list[str]] = []

    def dfs(u, stack):
        color[u] = GREY; stack.append(u)
        for v in edges.get(u, ()):
            if v in edges:
                if color.get(v, WHITE) == GREY:
                    cycles.append(stack[stack.index(v):] + [v])
                elif color.get(v, WHITE) == WHITE:
                    dfs(v, stack)
        stack.pop(); color[u] = BLACK

    for n in list(edges):
        if color.get(n, WHITE) == WHITE:
            dfs(n, [])
    return cycles


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for k, v in obj.items():
            if k != "@context":
                yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def static_rules(target: Path) -> list[Finding]:
    doc = _load_json(target)
    ctx = _context_obj(doc)
    index = NodeIndex(target)
    out: list[Finding] = []

    # Rule 1 & 3 — reference cycles. Runs over the whole document (works for both
    # @graph and flat forms) but skips @context, whose term definitions are
    # vocabulary, not data nodes.
    for cyc in _ref_cycles(doc):
        out.append(Finding("rule", "violation",
                           "reference cycle in @id graph (mlcroissant inlines & mutates shared nodes)",
                           locus=" → ".join(cyc),
                           json_path=index.path_for(cyc[0]) if cyc else "",
                           rule="mlcroissant Rule 1/3",
                           suggestion="break the cycle: make the back-edge a plain string, drop it, or inline as a fresh blank node."))

    # Rule 2 — @type:@id on cross-graph properties
    for term, defn in ctx.items():
        if term in CROSS_GRAPH_PROPS and isinstance(defn, dict) and defn.get("@type") == "@id":
            out.append(Finding("rule", "violation",
                               f"@context term '{term}' uses \"@type\":\"@id\" on a cross-graph property",
                               json_path=index.context_path(term),
                               rule="mlcroissant Rule 2",
                               suggestion=f"remove \"@type\":\"@id\" from '{term}'; reference nodes with explicit {{'@id': ...}} objects instead."))

    # @language required by mlcroissant
    if "@language" not in ctx:
        out.append(Finding("rule", "warning",
                           "@context has no @language (mlcroissant requires it)",
                           json_path=index.context_path(),
                           rule="mlcroissant",
                           suggestion='add "@language": "en" to the @context.'))

    # Rules 5 & 6 — FileObject @type and distribution<->source id match
    dist_ids: set[str] = set()
    for node in _walk(doc):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).endswith("FileObject") for x in types if x):
            fid = node.get("@id")
            if fid:
                dist_ids.add(fid)
            if not any(str(x) in ("cr:FileObject", "FileObject") or str(x).endswith("croissant/FileObject") for x in types if x):
                out.append(Finding("rule", "warning", "distribution entry is not typed cr:FileObject",
                                   locus=str(fid), json_path=index.path_for(str(fid), "@type"),
                                   rule="mlcroissant Rule 6",
                                   suggestion='set "@type": "cr:FileObject".'))
            if not (node.get("sha256") or node.get("md5")):
                out.append(Finding("rule", "warning", "FileObject has no schema:sha256 or md5 checksum",
                                   locus=str(fid), json_path=index.path_for(str(fid)),
                                   rule="mlcroissant",
                                   suggestion="add schema:sha256 (mlcroissant expects schema:sha256, not cr:sha256)."))
    # source.fileObject references that don't match a distribution @id (Rule 5)
    for node in _walk(doc):
        src = node.get("source")
        if isinstance(src, dict):
            fo = src.get("fileObject")
            ref = fo.get("@id") if isinstance(fo, dict) else fo
            if ref and dist_ids and ref not in dist_ids:
                out.append(Finding("rule", "warning",
                                   f"field source.fileObject '{ref}' has no matching distribution @id",
                                   json_path=index.path_for(str(node.get("@id")), "source"),
                                   rule="mlcroissant Rule 5",
                                   suggestion="make the distribution @id match the field source.fileObject @id exactly."))
    return out


def check_croissant(target: Path) -> tuple[bool | None, list[Finding]]:
    """Return (loads_ok, findings). loads_ok is None if mlcroissant isn't installed."""
    findings = static_rules(target)
    try:
        import mlcroissant as mlc
    except Exception:
        findings.append(Finding("croissant", "info",
                                "mlcroissant not installed; ran static Rule checks only",
                                suggestion="pip install mlcroissant to enable the load check."))
        return None, findings
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mlc.Dataset(jsonld=str(target))
        return True, findings
    except Exception as e:
        findings.append(Finding("croissant", "violation",
                                f"mlcroissant failed to load: {type(e).__name__}: {str(e)[:300]}",
                                rule="mlcroissant load"))
        return False, findings
