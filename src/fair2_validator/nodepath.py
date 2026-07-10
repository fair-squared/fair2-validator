"""Map RDF nodes / JSON-LD @ids back to a concrete JSON path in the source file.

SHACL (and the static Rule checks) report a *focus node* — an IRI. To a human
editing the JSON that IRI is often opaque: relative @ids are expanded against
`@context.@base`, so `steps/abc` surfaces as `https://…/steps/abc`. This module
walks the original JSON once and builds an index from both the raw and the
base-expanded @id to the node's JSON path (e.g.
`$["@graph"][0].methodSection[2].name`), so findings can point at the exact
location to edit.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin

from . import shapes as S


def _base_of(doc: dict) -> str:
    ctx = doc.get("@context")
    parts = ctx if isinstance(ctx, list) else [ctx]
    for el in parts:
        if isinstance(el, dict) and el.get("@base"):
            return str(el["@base"])
    return ""


def _expand(raw: str, base: str) -> str:
    """Best-effort JSON-LD @id expansion, matching rdflib's json-ld parser."""
    if not raw:
        return raw
    if "://" in raw:                     # already absolute
        return raw
    if ":" in raw and not raw.startswith(("/", "#", "?", ".")):
        # a CURIE (schema:name) — expand via the known prefix map, else leave it
        return S.expand(raw)
    if base:
        return urljoin(base, raw)
    return raw


def _segment(key) -> str:
    if isinstance(key, int):
        return f"[{key}]"
    if isinstance(key, str) and key.isidentifier():
        return f".{key}"
    return f'["{key}"]'


def _render_path(path: list) -> str:
    return "$" + "".join(_segment(k) for k in path)


class NodeIndex:
    """Resolves a node identifier (raw @id or expanded IRI) to its JSON path."""

    def __init__(self, target: Path):
        self.doc = json.loads(Path(target).read_text())
        self.base = _base_of(self.doc)
        self._by_raw: dict[str, tuple[list, int]] = {}
        self._by_iri: dict[str, tuple[list, int]] = {}
        self._index(self.doc, [])

    def _record(self, raw: str, path: list, weight: int) -> None:
        # Prefer the richest occurrence (the definition, not a bare {"@id": …} ref).
        for store, key in ((self._by_raw, raw), (self._by_iri, _expand(raw, self.base))):
            prev = store.get(key)
            if prev is None or weight > prev[1]:
                store[key] = (list(path), weight)

    def _index(self, obj, path: list) -> None:
        if isinstance(obj, dict):
            rid = obj.get("@id")
            if isinstance(rid, str):
                self._record(rid, path, len(obj))
            for k, v in obj.items():
                if k == "@context":      # vocabulary, not data nodes
                    continue
                self._index(v, path + [k])
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                self._index(v, path + [i])

    def _lookup(self, node_id: str) -> list | None:
        if not node_id:
            return None
        for store, key in ((self._by_raw, node_id),
                           (self._by_iri, node_id),
                           (self._by_iri, _expand(node_id, self.base))):
            hit = store.get(key)
            if hit is not None:
                return hit[0]
        return None

    def path_for(self, node_id: str, prop: str = "") -> str:
        """JSON path for `node_id`, optionally deepened by the failing property.

        Returns "" if the node isn't locatable (e.g. a blank node)."""
        path = self._lookup(node_id)
        if path is None:
            return ""
        if prop:
            path = path + [prop]
        return _render_path(path)

    def context_path(self, term: str = "") -> str:
        base = ["@context"]
        return _render_path(base + [term] if term else base)
