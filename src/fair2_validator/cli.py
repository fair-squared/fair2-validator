"""fair2-validate CLI: deterministic detection + agent-driven, confirm-before-apply fixing."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .spec import find_spec, SpecNotFound
from .report import Report
from . import shapes as S
from .validate import validate
from .croissant import check_croissant
from .meta import check_meta


def _run_detection(target: Path, spec, schemas, do_croissant: bool, croissant_only: bool) -> Report:
    # `meta` is a non-SHACL pseudo-schema (deterministic _meta.conformsTo check).
    meta_requested = (not croissant_only) and (schemas is None or "meta" in schemas)
    shacl_schemas = None if schemas is None else [s for s in schemas if s != "meta"]

    if croissant_only:
        rep = Report(target=str(target), spec_version=spec.version, scope="croissant")
    elif schemas is not None and not shacl_schemas:
        rep = Report(target=str(target), spec_version=spec.version, scope="meta")
    else:
        rep = validate(target, spec, shacl_schemas)

    if do_croissant or croissant_only:
        ok, findings = check_croissant(target)
        rep.croissant_ok = ok
        rep.findings.extend(findings)

    if meta_requested:
        mok, mfindings = check_meta(target, spec.version)
        rep.meta_ok = mok
        rep.findings.extend(mfindings)
    return rep


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fair2-validate",
        description="Validate a FAIR² JSON-LD document against the canonical fair2-spec "
                    "(full or per sub-schema), and optionally agent-fix it.")
    p.add_argument("target", nargs="?", help="path to the FAIR² JSON-LD file")
    p.add_argument("--schema", help="comma-separated sub-schema(s) to check "
                                    "(e.g. methodSection,dataArticle). Default: full document.")
    p.add_argument("--croissant-only", action="store_true", help="only run the mlcroissant + Rules checks")
    p.add_argument("--no-croissant", action="store_true", help="skip the mlcroissant + Rules checks")
    p.add_argument("--fix", action="store_true",
                   help="invoke the agent to plan a fix, show the diff, and apply only on confirmation")
    p.add_argument("--spec", help="path to a fair2-spec checkout (overrides submodule / FAIR2_SPEC_PATH)")
    p.add_argument("--list-schemas", action="store_true", help="list the sub-schema names and exit")
    p.add_argument("--yes", action="store_true", help="(with --fix) auto-confirm the agent's proposed patch")
    args = p.parse_args(argv)

    if args.list_schemas:
        print("Available sub-schemas (--schema):")
        for n in S.schema_names():
            print(f"  {n}")
        print("  meta        (deterministic root _meta.conformsTo check; not SHACL)")
        return 0

    if not args.target:
        p.error("target file is required (unless --list-schemas)")

    try:
        spec = find_spec(args.spec)
    except SpecNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    target = Path(args.target)
    if not target.is_file():
        print(f"error: target not found: {target}", file=sys.stderr)
        return 2

    schemas = [s.strip() for s in args.schema.split(",")] if args.schema else None
    rep = _run_detection(target, spec, schemas,
                         do_croissant=not args.no_croissant, croissant_only=args.croissant_only)
    print(rep.render())

    if rep.ok:
        return 0

    if not args.fix:
        print("\nRun with --fix to have the agent propose changes (it will ask before applying).")
        return 1

    try:
        from .agent import plan_and_fix
    except Exception as e:
        print(f"\nerror: agent layer unavailable: {e}", file=sys.stderr)
        return 3
    return plan_and_fix(target, rep, spec, auto_confirm=args.yes,
                        rerun=lambda: _run_detection(target, spec, schemas,
                                                     do_croissant=not args.no_croissant,
                                                     croissant_only=args.croissant_only))


if __name__ == "__main__":
    raise SystemExit(main())
