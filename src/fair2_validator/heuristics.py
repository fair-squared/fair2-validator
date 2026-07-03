"""Advisory fix heuristics — the patterns found while reconciling the canonical
example. These are passed to the agent as *context only*; they are never applied
automatically. The agent decides what (if anything) is relevant and always asks
for confirmation before any change is written.
"""

HEURISTICS = [
    ("reference cycle / mlcroissant KeyError",
     "mlcroissant inlines and mutates shared @id nodes, so a node reachable twice "
     "(a cycle or a diamond) crashes it. Break the back-edge: a self-referential "
     "`url` equal to the node @id should be a plain string; a `geoWithin` that "
     "mirrors a `containsPlace` is redundant — drop it; `DataPortal.dataset` / "
     "`DataArchive.dataset` / `DataArticle.wasDerivedFrom` pointing at the Dataset "
     "@id should be plain strings; `changeLog[].wasRevisionOf` typed `Dataset` "
     "re-targets DatasetShape — remove its @type."),
    ("@type:@id on cross-graph property (Rule 2)",
     "Remove \"@type\":\"@id\" from the @context term; reference nodes with explicit "
     "{\"@id\": ...} objects instead. Affects url, next, generated, geoWithin, "
     "wasDerivedFrom, wasRevisionOf, dataset, citation, dataArticle, dataPortal, dataArchive."),
    ("checksum not recognised by mlcroissant",
     "FileObject checksums must use schema:sha256 (not cr:sha256); map sha256 -> "
     "schema:sha256 in the context and use schema:sha256 in DistributionShape."),
    ("missing @language",
     "mlcroissant requires @context.@language; add \"@language\": \"en\"."),
    ("missing RecordSet name",
     "Each cr:RecordSet needs schema:name; a reasonable value is the title-cased "
     "last segment of its @id."),
    ("temporalCoverage format",
     "Use an ISO-8601 date or interval (e.g. 1995-01-01/2023-12-31) for Google "
     "Dataset Search compliance."),
    ("@context drift",
     "Every term the document uses must resolve correctly under the reference "
     "context; add any missing fair2/prov/skos term rather than relying on @vocab."),
    ("FileObject @type (Rule 6) / distribution↔source @id (Rule 5)",
     "FileObjects must be typed cr:FileObject, and each distribution @id must "
     "exactly match the field source.fileObject @id it is referenced by."),
]


def heuristics_text() -> str:
    return "\n".join(f"- {name}: {advice}" for name, advice in HEURISTICS)
