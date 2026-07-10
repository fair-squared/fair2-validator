# fair2-validator

Validate — and agent-fix — FAIR² JSON-LD documents against the canonical
[`fair2-spec`](https://github.com/fair-squared/fair2-spec).

It reproduces the manual **detect → diagnose → fix → re-validate** loop used to
reconcile the reference example, as a reusable tool:

- **Detection is deterministic.** SHACL validation (via `pyshacl`) against the
  spec's shapes — the **full** document or an **individual sub-schema**
  (e.g. just `methodSection` or `dataArticle`) — plus an `mlcroissant` load check
  and the documented [Compatibility Rules 1–6](https://github.com/fair-squared/fair2-spec/blob/main/docs/integration/ml-croissant.md).
- **Fixing is agent-driven and confirm-before-apply.** With `--fix`, a Claude
  agent analyses the findings, **plans** the change, shows you the operations,
  and applies them **only after you confirm** — then re-validates. Nothing is
  written without an explicit yes.

## Install

```bash
pip install fair2-validator
```

The pinned FAIR² spec (shapes + context + golden example) is **vendored into the
package**, so a plain `pip install` is self-contained — no submodule needed. The
agent-fix layer needs an extra: `pip install "fair2-validator[agent]"`.

For a development checkout (spec as an editable git submodule):

```bash
git clone --recurse-submodules <this repo>
cd fair2-validator
pip install -e ".[dev,agent]"
```

Spec resolution order: `--spec PATH` → `FAIR2_SPEC_PATH` → the `fair2-spec`
submodule → a sibling `../fair2-spec` checkout → the copy vendored in the
package. So a dev checkout uses the live submodule, while an installed wheel
falls back to the vendored spec.

## Usage

```bash
# full document: SHACL + mlcroissant + Rules
fair2-validate path/to/fair2.json

# only specific sub-schema(s)
fair2-validate path/to/fair2.json --schema methodSection,dataArticle

# only the mlcroissant + Rules checks
fair2-validate path/to/fair2.json --croissant-only

# list the sub-schema names
fair2-validate --list-schemas

# agent proposes a fix, shows the diff, applies only on confirmation
fair2-validate path/to/fair2.json --fix          # requires ANTHROPIC_API_KEY
fair2-validate path/to/fair2.json --fix --yes    # auto-confirm the agent's plan
```

### Sub-schemas

`dataset` (full), `distribution`, `recordSet`, `field`, `methodSection`,
`methodStep`, `substep`, `dataArticle`, `dataPortal`, `dataArchive`,
`contributor`, `creator`, `spatialCoverage`, `accessRights`, `changeLog`,
`domain`, `funding`, `subjectOf`, `provenance`.

Under the hood, a sub-schema loads only its shape file plus the transitive
`sh:node` dependency closure. Shapes with a `sh:targetClass` auto-target their
instances; helper shapes (e.g. `spatialCoverage`, `contributor`) are targeted at
the node(s) reached from the Dataset via the corresponding property.

## The agent (`--fix`)

Detection runs read-only. `--fix` invokes the agent, which returns a written plan
plus a set of **node-scoped operations** (`target` = `context` or a node `@id`;
`path` = a JSON Pointer within it). The tool renders the plan, applies the
operations to a copy, asks you to confirm, writes, and re-validates — looping up
to a few rounds until the document conforms and loads or you decline.

- Model: defaults to `claude-opus-4-8` (override with `FAIR2_AGENT_MODEL`).
- Requires `ANTHROPIC_API_KEY`. The deterministic detection core needs no key
  and runs fully offline.

## Keeping the spec current

The submodule pins an exact, reproducible spec commit. Advance it only when the
validator still passes against it:

```bash
make update-spec   # bumps the submodule, runs tests + validates the golden example,
                   # and re-vendors the spec into the package (src/fair2_validator/_spec_data)
```

Commit the submodule bump **and** the re-vendored `_spec_data/` only after that
succeeds. (`make sync-spec` re-vendors on its own; a test guards against drift.)

## Development

```bash
make test       # offline detection tests (agent-fix tests are not run in CI)
make validate   # validate the canonical golden example
```

## Publishing to PyPI

Releases are cut by pushing a version tag; a GitHub Actions workflow
(`.github/workflows/release.yml`) builds the artifacts and publishes them via
PyPI [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC — no
stored token).

```bash
# 1. bump `version` in pyproject.toml, commit
# 2. tag and push
git tag v0.2.0
git push origin v0.2.0
```

One-time PyPI setup: on the project's *Publishing* settings, add a GitHub trusted
publisher (owner `fair-squared`, repo `fair2-validator`, workflow `release.yml`,
environment `pypi`). To build/publish manually instead:

```bash
make build                  # re-vendors spec, builds sdist+wheel, verifies contents
python -m twine upload -r testpypi dist/*   # smoke-test on TestPyPI first
python -m twine upload dist/*
```
