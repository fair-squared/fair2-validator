# fair2-validator

Validate — and agent-fix — FAIR² JSON-LD documents against the canonical
[`fair2-spec`](https://github.com/fair-squared/fair2-spec).

It reproduces the manual **detect → diagnose → fix → re-validate** loop used to
reconcile the reference example, as a reusable tool:

- **Detection is deterministic.** SHACL validation (via `pyshacl`) against the
  spec's shapes — the **full** document or an **individual sub-schema**
  (e.g. just `methodSection` or `dataArticle`) — plus an `mlcroissant` load check
  and the documented [Compatibility Rules 1–6](https://github.com/fair-squared/fair2-spec/blob/main/docs/integration/ml-croissant.md).
  A separate `meta` check enforces the required `_meta.conformsTo` (which sits
  outside `@graph`, so SHACL can't see it) and **warns** when a document declares
  a different spec version than the one the validator is pinned to.
- **Findings point at the JSON.** Every finding carries the source-file location
  to edit (`json: $["@graph"][0].methodSection[0].name`); violations on blank
  nodes are described by their `name`/`@type` and their containing object.
- **Fixing is agent-driven and confirm-before-apply.** With `--fix`, a model
  analyses the findings, **plans** the change, shows you the operations, and
  applies them **only after you confirm** — then re-validates. Nothing is
  written without an explicit yes. The model is configurable: Anthropic or
  OpenAI, any model either serves.

## Install

Not on PyPI yet — install from a release tag:

```bash
pip install "fair2-validator @ git+https://github.com/fair-squared/fair2-validator@v0.4.2"
```

or, as a dependency in `pyproject.toml`:

```toml
"fair2-validator @ git+https://github.com/fair-squared/fair2-validator@v0.4.2"
```

Every release also attaches the wheel to its
[GitHub Release](https://github.com/fair-squared/fair2-validator/releases).

The pinned FAIR² spec (shapes + context + golden example) is **vendored into the
package**, so a plain `pip install` is self-contained — no submodule needed. The
agent-fix layer needs the SDK for whichever provider you use:

```bash
pip install "fair2-validator[anthropic]"   # or [agent], the same thing
pip install "fair2-validator[openai]"
pip install "fair2-validator[agent-all]"   # both
```

For a development checkout (spec as an editable git submodule):

```bash
git clone --recurse-submodules <this repo>
cd fair2-validator
pip install -e ".[dev,agent-all]"
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
fair2-validate path/to/fair2.json --fix          # default provider (Anthropic)
fair2-validate path/to/fair2.json --fix --yes    # auto-confirm the agent's plan

# pick a different model or provider
fair2-validate path/to/fair2.json --fix --model claude-sonnet-5-5
fair2-validate path/to/fair2.json --fix --model gpt-5.1 --effort high
fair2-validate path/to/fair2.json --fix --provider openai
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

The deterministic detection core needs no API key and runs fully offline; only
`--fix` calls a model.

### Choosing a model

Each setting takes a flag or an environment variable, and the flag wins:

| Flag | Environment | Default |
|---|---|---|
| `--provider {anthropic,openai}` | `FAIR2_AGENT_PROVIDER` | `anthropic` |
| `--model` | `FAIR2_AGENT_MODEL` | `claude-opus-5-5` / `gpt-5.1` |
| `--effort` | `FAIR2_AGENT_EFFORT` | the provider's own |
| `--max-tokens` | `FAIR2_AGENT_MAX_TOKENS` | `16000` |

Given a model but no provider, the provider is inferred from the model name
(`claude-*` → Anthropic; `gpt-*`, `o1/o3/o4-*`, `chatgpt-*` → OpenAI). Naming
the provider explicitly always wins over that inference, which is how you reach
a gateway that serves one vendor's models over the other's wire format:

```bash
OPENAI_BASE_URL=https://gateway.example/v1 \
  fair2-validate doc.json --fix --provider openai --model claude-opus-5-5
```

`--effort` only means something on reasoning models (Claude 4.6+, GPT-5 /
o-series); it is omitted from the request entirely when unset, so non-reasoning
models such as `gpt-4o` work unchanged.

Each provider is called through **its own SDK** — `anthropic.messages.create`
with `output_config.format`, `openai.responses.create` with `text.format` —
rather than routing one vendor through the other's compatibility shim.

**Credentials.** Anthropic reads `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or
an `ant auth login` profile; OpenAI reads `OPENAI_API_KEY`. `--fix` checks for a
usable credential before spending anything and tells you which one is missing.

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

## Releasing

Releases are cut by pushing a version tag. The workflow
(`.github/workflows/release.yml`) builds the wheel and sdist and attaches them to
a GitHub Release.

```bash
# 1. bump `version` in pyproject.toml and `__version__` in __init__.py, commit
#    (a test fails if the two disagree)
# 2. tag and push
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

**PyPI is off for now.** The workflow also has a PyPI job using
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC — no stored
token), skipped until it is set up. To enable it: on pypi.org add a pending
trusted publisher (project `fair2-validator`, owner `fair-squared`, repo
`fair2-validator`, workflow `release.yml`, environment `pypi`), then set the
repository variable `PYPI_PUBLISH=true`. Consumers then switch their dependency
to `fair2-validator>=X.Y.Z`. To build/publish manually instead:

```bash
make build                  # re-vendors spec, builds sdist+wheel, verifies contents
python -m twine upload -r testpypi dist/*   # smoke-test on TestPyPI first
python -m twine upload dist/*
```
