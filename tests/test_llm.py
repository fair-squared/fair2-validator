"""Model configuration: resolution order, provider inference, request shapes.

Offline — no provider is ever called. The request builders are pure, so the
payload each SDK would receive is asserted here rather than at runtime.
"""
import json

import pytest

from fair2_validator import llm
from fair2_validator.agent import OPERATIONS_SCHEMA, validate_plan


# ---- provider inference -----------------------------------------------------

@pytest.mark.parametrize("model,expected", [
    ("claude-opus-5-5", "anthropic"),
    ("claude-sonnet-5-5", "anthropic"),
    ("gpt-5.1", "openai"),
    ("gpt-4o", "openai"),
    ("o3-mini", "openai"),
    ("o4-mini", "openai"),
    ("chatgpt-4o-latest", "openai"),
])
def test_provider_inferred_from_model(model, expected):
    assert llm.infer_provider(model) == expected
    assert llm.resolve(model=model, env={}).provider == expected


def test_unknown_model_needs_an_explicit_provider():
    with pytest.raises(llm.ConfigError, match="cannot tell which provider"):
        llm.resolve(model="llama-3-70b", env={})
    # ...but naming the provider makes it usable (e.g. an OpenAI-compatible gateway)
    cfg = llm.resolve(model="llama-3-70b", provider="openai", env={})
    assert (cfg.provider, cfg.model) == ("openai", "llama-3-70b")


def test_explicit_provider_overrides_inference():
    """A gateway may serve claude-* over the OpenAI wire format."""
    cfg = llm.resolve(model="claude-opus-5-5", provider="openai", env={})
    assert cfg.provider == "openai"


def test_unknown_provider_rejected():
    with pytest.raises(llm.ConfigError, match="unknown provider"):
        llm.resolve(provider="cohere", env={})


# ---- resolution order -------------------------------------------------------

def test_defaults():
    cfg = llm.resolve(env={})
    assert cfg.provider == llm.DEFAULT_PROVIDER
    assert cfg.model == llm.DEFAULT_MODELS[llm.DEFAULT_PROVIDER]
    assert cfg.effort is None
    assert cfg.max_tokens == llm.DEFAULT_MAX_TOKENS


def test_provider_alone_picks_that_providers_default_model():
    assert llm.resolve(provider="openai", env={}).model == llm.DEFAULT_MODELS["openai"]
    assert llm.resolve(provider="anthropic", env={}).model == llm.DEFAULT_MODELS["anthropic"]


def test_environment_is_read():
    env = {"FAIR2_AGENT_PROVIDER": "openai", "FAIR2_AGENT_MODEL": "gpt-4o",
           "FAIR2_AGENT_EFFORT": "LOW", "FAIR2_AGENT_MAX_TOKENS": "2048"}
    cfg = llm.resolve(env=env)
    assert (cfg.provider, cfg.model, cfg.effort, cfg.max_tokens) == \
        ("openai", "gpt-4o", "low", 2048)


def test_arguments_beat_the_environment():
    env = {"FAIR2_AGENT_PROVIDER": "openai", "FAIR2_AGENT_MODEL": "gpt-4o"}
    cfg = llm.resolve(model="claude-sonnet-5-5", provider="anthropic", env=env)
    assert (cfg.provider, cfg.model) == ("anthropic", "claude-sonnet-5-5")


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-5"])
def test_bad_max_tokens_rejected(bad):
    with pytest.raises(llm.ConfigError):
        llm.resolve(env={"FAIR2_AGENT_MAX_TOKENS": bad})


# ---- request shapes ---------------------------------------------------------

def test_anthropic_request_shape():
    cfg = llm.resolve(model="claude-opus-5-5", effort="high", env={})
    kw = llm.anthropic_kwargs(cfg, "SYS", "PROMPT", OPERATIONS_SCHEMA)
    assert kw["model"] == "claude-opus-5-5"
    assert kw["system"] == "SYS"
    assert kw["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"] == {"type": "json_schema",
                                             "schema": OPERATIONS_SCHEMA}
    assert kw["output_config"]["effort"] == "high"
    # no budget_tokens: it is rejected on current models
    assert "budget_tokens" not in json.dumps(kw)


def test_anthropic_omits_effort_when_unset():
    cfg = llm.resolve(model="claude-opus-5-5", env={})
    assert "effort" not in llm.anthropic_kwargs(cfg, "s", "p", {})["output_config"]


def test_openai_request_shape():
    cfg = llm.resolve(model="gpt-5.1", effort="medium", env={})
    kw = llm.openai_kwargs(cfg, "SYS", "PROMPT", OPERATIONS_SCHEMA)
    assert kw["model"] == "gpt-5.1"
    assert kw["instructions"] == "SYS"
    assert kw["input"] == "PROMPT"
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] is OPERATIONS_SCHEMA
    assert fmt["strict"] is False
    assert kw["reasoning"] == {"effort": "medium"}


def test_openai_omits_reasoning_when_no_effort():
    """gpt-4o and other non-reasoning models reject `reasoning`."""
    cfg = llm.resolve(model="gpt-4o", env={})
    assert "reasoning" not in llm.openai_kwargs(cfg, "s", "p", {})


# ---- preflight --------------------------------------------------------------

def test_preflight_reports_a_missing_sdk():
    """Provider package absent — reported with the extra that installs it."""
    cfg = llm.ModelConfig(provider="not_an_installed_module", model="x")
    msg = llm.preflight(cfg, env={})
    assert msg and "pip install" in msg


def test_preflight_reports_missing_openai_credentials():
    pytest.importorskip("openai")
    msg = llm.preflight(llm.resolve(provider="openai", env={}), env={})
    assert msg and "OPENAI_API_KEY" in msg


def test_preflight_passes_with_credentials():
    pytest.importorskip("openai")
    pytest.importorskip("anthropic")
    assert llm.preflight(llm.resolve(provider="openai", env={}),
                         env={"OPENAI_API_KEY": "sk-test"}) is None
    assert llm.preflight(llm.resolve(provider="anthropic", env={}),
                         env={"ANTHROPIC_API_KEY": "sk-test"}) is None


def test_preflight_accepts_an_anthropic_oauth_profile(tmp_path):
    """An unset ANTHROPIC_API_KEY does not mean there are no credentials."""
    pytest.importorskip("anthropic")
    (tmp_path / "anthropic").mkdir()
    cfg = llm.resolve(provider="anthropic", env={})
    assert llm.preflight(cfg, env={"XDG_CONFIG_HOME": str(tmp_path)}) is None


# ---- reply validation -------------------------------------------------------

def test_valid_plan_accepted():
    plan = {"plan": "fix it", "operations": [
        {"description": "d", "rule": "r", "target": "t", "op": "set",
         "path": "/url", "value": "https://example.org"},
        {"description": "d", "rule": "r", "target": "t", "op": "delete", "path": "/x"},
    ]}
    assert validate_plan(plan) is plan


def test_empty_plan_accepted():
    assert validate_plan({"plan": "nothing to do", "operations": []})


@pytest.mark.parametrize("bad,match", [
    ({"plan": 7}, "must be a string"),
    ({"operations": {}}, "must be a list"),
    ({"operations": ["nope"]}, "not an object"),
    ({"operations": [{"op": "set"}]}, "missing"),
    ({"operations": [{"description": "d", "rule": "r", "target": "t",
                      "op": "rename", "path": "/x"}]}, "unknown op"),
    ({"operations": [{"description": "d", "rule": "r", "target": "t",
                      "op": "set", "path": "/x"}]}, "no value"),
])
def test_malformed_replies_rejected(bad, match):
    with pytest.raises(ValueError, match=match):
        validate_plan(bad)
