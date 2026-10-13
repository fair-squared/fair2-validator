"""Agent fix layer: plan a fix, show it, apply ONLY on confirmation, re-validate.

Detection is deterministic; fixing is never auto-applied. The agent (Claude) is
given the findings, the relevant shape/Rule context, the advisory heuristics, and
the relevant slices of the target document. It returns a written plan plus a list
of node-scoped operations. The tool renders the plan + a diff, asks the user to
confirm, applies the operations, and re-runs detection — looping until the
document is clean, no progress is made, or the user declines.

The model is configurable across providers (Anthropic, OpenAI) — see `llm.py`
for the resolution order and the environment variables it reads.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .report import Report
from .heuristics import heuristics_text
from . import llm
from . import shapes as S

MAX_ROUNDS = 3

OPERATIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plan": {"type": "string", "description": "Human-readable summary of the fix and why."},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "rule": {"type": "string", "description": "Which finding/rule this addresses."},
                    "target": {"type": "string",
                               "description": "'context' for the @context object, or the @id of a @graph node."},
                    "op": {"type": "string", "enum": ["set", "delete"]},
                    "path": {"type": "string",
                             "description": "JSON Pointer within the target (e.g. /url or /spatialCoverage/containsPlace)."},
                    "value": {"description": "Value for 'set' (any JSON); omit for 'delete'."},
                },
                "required": ["description", "rule", "target", "op", "path"],
            },
        },
    },
    "required": ["plan", "operations"],
}

SYSTEM = (
    "You are the FAIR² fix agent. You repair a FAIR² JSON-LD document so it conforms to the "
    "canonical SHACL shapes and loads in mlcroissant, treating the fair2-spec as authoritative. "
    "You never rewrite the whole file: you return a small set of node-scoped operations. "
    "Prefer the smallest change that resolves each finding. Do not invent metadata values; only "
    "derive obvious ones (e.g. a RecordSet name from its @id). If a finding cannot be fixed "
    "without inventing data, explain that in the plan and emit no operation for it."
)


# ---- node / pointer helpers -------------------------------------------------

def _context_obj(doc):
    ctx = doc.get("@context")
    if isinstance(ctx, list):
        for i, el in enumerate(ctx):
            if isinstance(el, dict):
                return ctx, i
    return doc, "@context"


def _find_node(doc, target):
    if target in ("context", "@context"):
        ctx = doc.get("@context")
        if isinstance(ctx, list):
            for el in ctx:
                if isinstance(el, dict):
                    return el
        return ctx if isinstance(ctx, dict) else None
    for n in doc.get("@graph", []):
        if isinstance(n, dict) and n.get("@id") == target:
            return n
    return None


def _ptr_tokens(path):
    if not path or path == "/":
        return []
    return [t.replace("~1", "/").replace("~0", "~") for t in path.lstrip("/").split("/")]


def _apply_op(node, op, path, value):
    toks = _ptr_tokens(path)
    if not toks:
        raise ValueError("empty path")
    cur = node
    for t in toks[:-1]:
        if isinstance(cur, list):
            cur = cur[int(t)]
        else:
            cur = cur[t]
    last = toks[-1]
    if op == "delete":
        if isinstance(cur, list):
            del cur[int(last)]
        else:
            cur.pop(last, None)
    else:
        if isinstance(cur, list):
            idx = len(cur) if last == "-" else int(last)
            if idx == len(cur):
                cur.append(value)
            else:
                cur[idx] = value
        else:
            cur[last] = value


def apply_operations(doc, operations):
    """Return a deep-copied doc with operations applied; raise on a bad op."""
    new = copy.deepcopy(doc)
    applied = []
    for o in operations:
        node = _find_node(new, o["target"])
        if node is None:
            raise ValueError(f"target node not found: {o['target']}")
        _apply_op(node, o["op"], o["path"], o.get("value"))
        applied.append(o)
    return new, applied


# ---- agent context ----------------------------------------------------------

def _referenced_nodes(doc, report):
    """Collect @graph nodes referenced by finding loci, plus the Dataset node."""
    ids = set()
    for f in report.findings:
        loc = f.locus.split(" ")[0].split("→")[0].strip()
        if loc:
            ids.add(loc)
    nodes = {}
    ds = None
    for n in doc.get("@graph", []):
        if not isinstance(n, dict):
            continue
        t = n.get("@type")
        if t == "Dataset" or (isinstance(t, list) and "Dataset" in t):
            ds = n
        if n.get("@id") in ids:
            nodes[n["@id"]] = n
    if ds is not None:
        nodes.setdefault(ds.get("@id", "Dataset"), ds)
    # graph index so the agent knows valid targets
    index = [{"@id": n.get("@id"), "@type": n.get("@type"), "keys": list(n.keys())}
             for n in doc.get("@graph", []) if isinstance(n, dict)]
    return nodes, index


def _build_user_prompt(doc, report):
    nodes, index = _referenced_nodes(doc, report)
    ctx = _find_node(doc, "context")
    parts = [
        "## Findings\n" + report.render(),
        "## Advisory heuristics (from prior reconciliations; apply only if relevant)\n" + heuristics_text(),
        "## @context (object form)\n```json\n" + json.dumps(ctx, indent=2)[:8000] + "\n```",
        "## @graph node index (valid `target` @ids)\n```json\n" + json.dumps(index, indent=2)[:6000] + "\n```",
        "## Relevant nodes (full content)\n```json\n" + json.dumps(nodes, indent=2)[:40000] + "\n```",
        "Return a fix plan and node-scoped operations. `target` is 'context' or a node @id from the "
        "index; `path` is a JSON Pointer within that target.",
    ]
    return "\n\n".join(parts)


_REQUIRED_OP_FIELDS = ("description", "rule", "target", "op", "path")


def validate_plan(plan):
    """Check a reply before anything is rendered, applied or written.

    Only the Anthropic path can guarantee schema adherence: OpenAI's strict
    mode cannot express an operation `value` of arbitrary JSON type, so the
    schema is sent there as guidance and the reply is checked here instead.
    The check is provider-independent so both paths fail the same way.
    """
    if not isinstance(plan.get("plan", ""), str):
        raise ValueError("'plan' must be a string")
    ops = plan.get("operations", [])
    if not isinstance(ops, list):
        raise ValueError("'operations' must be a list")
    for i, o in enumerate(ops, 1):
        if not isinstance(o, dict):
            raise ValueError(f"operation {i} is not an object")
        missing = [f for f in _REQUIRED_OP_FIELDS if f not in o]
        if missing:
            raise ValueError(f"operation {i} is missing {', '.join(missing)}")
        if o["op"] not in ("set", "delete"):
            raise ValueError(f"operation {i} has unknown op {o['op']!r}")
        if o["op"] == "set" and "value" not in o:
            raise ValueError(f"operation {i} is a 'set' with no value")
    return plan


def _plan(doc, report, cfg):
    reply = llm.complete_json(cfg, SYSTEM, _build_user_prompt(doc, report), OPERATIONS_SCHEMA)
    return validate_plan(reply)


def _render_ops(plan):
    lines = ["", "Proposed fix plan:", "  " + plan.get("plan", "(no summary)"), "", "Operations:"]
    for i, o in enumerate(plan.get("operations", []), 1):
        v = "" if o["op"] == "delete" else f" = {json.dumps(o.get('value'))[:120]}"
        lines.append(f"  {i}. [{o['op']}] {o['target']}{o['path']}{v}")
        lines.append(f"      {o['description']}  ({o.get('rule','')})")
    return "\n".join(lines)


def plan_and_fix(target: Path, report: Report, spec, auto_confirm: bool = False, rerun=None,
                 cfg: llm.ModelConfig | None = None) -> int:
    target = Path(target)
    cfg = cfg or llm.resolve()
    problem = llm.preflight(cfg)
    if problem:
        print(f"error: --fix cannot run: {problem}")
        return 3

    current = report
    for rnd in range(1, MAX_ROUNDS + 1):
        doc = json.loads(target.read_text())
        print(f"\n=== Agent fix round {rnd}/{MAX_ROUNDS} ({cfg}) ===")
        try:
            plan = _plan(doc, current, cfg)
        except Exception as e:
            print(f"agent error: {type(e).__name__}: {str(e)[:200]}")
            return 3
        ops = plan.get("operations", [])
        if not ops:
            print("Agent proposed no operations.")
            print("  " + plan.get("plan", ""))
            return 1
        print(_render_ops(plan))

        try:
            new_doc, _ = apply_operations(doc, ops)
        except Exception as e:
            print(f"Could not apply proposed operations: {e}")
            return 3

        if not auto_confirm:
            ans = input("\nApply these changes? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("Declined. No changes written.")
                return 1

        target.write_text(json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n")
        print(f"Applied {len(ops)} operation(s) to {target}.")

        if rerun is None:
            return 0
        current = rerun()
        print("\n--- re-validation ---")
        print(current.render())
        if current.ok:
            print("\n✅ Document now conforms and loads.")
            return 0
    print("\nReached max rounds; some findings remain. Re-run to continue.")
    return 1
