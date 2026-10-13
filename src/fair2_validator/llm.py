"""Provider-agnostic structured-JSON call behind the agent-fix layer.

The agent needs exactly one capability from a model: given a system prompt, a
user prompt and a JSON Schema, return a JSON object. That is small enough to
express against each vendor's own SDK, so this module keeps two native
backends rather than routing one vendor through the other's wire format:

* ``anthropic`` — ``messages.create`` with ``output_config.format``
* ``openai``    — ``responses.create`` with ``text.format``

Configuration resolves CLI argument > environment variable > default:

===========================  ==========================  ======================
Setting                      Environment variable        Default
===========================  ==========================  ======================
provider                     ``FAIR2_AGENT_PROVIDER``    ``anthropic``
model                        ``FAIR2_AGENT_MODEL``       per provider, below
reasoning effort             ``FAIR2_AGENT_EFFORT``      the provider's own
max output tokens            ``FAIR2_AGENT_MAX_TOKENS``  16000
===========================  ==========================  ======================

With a model but no provider, the provider is inferred from the model name. An
explicit provider always wins over that inference, so a gateway that serves
``claude-*`` over the OpenAI wire format (``OPENAI_BASE_URL``) is reachable with
``--provider openai --model claude-...``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROVIDERS = ("anthropic", "openai")

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-5.1",
}
DEFAULT_MAX_TOKENS = 16000

# Model-name prefixes used to infer a provider when none is given.
_MODEL_PREFIXES = {
    "anthropic": ("claude",),
    "openai": ("gpt", "chatgpt", "o1", "o3", "o4"),
}

_PIP_EXTRA = {"anthropic": "agent", "openai": "openai"}


class ConfigError(ValueError):
    """The requested provider/model combination cannot be used."""


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    effort: str | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __str__(self) -> str:
        bits = f"{self.provider}:{self.model}"
        return f"{bits}, effort={self.effort}" if self.effort else bits


def infer_provider(model: str) -> str:
    """Infer the provider from a model name, or raise ConfigError."""
    name = model.strip().lower()
    for provider, prefixes in _MODEL_PREFIXES.items():
        if name.startswith(prefixes):
            return provider
    raise ConfigError(
        f"cannot tell which provider serves model {model!r}; "
        f"pass --provider ({' or '.join(PROVIDERS)}) or set FAIR2_AGENT_PROVIDER"
    )


def resolve(model=None, provider=None, effort=None, max_tokens=None, env=None) -> ModelConfig:
    """Resolve the agent's model configuration: argument > environment > default."""
    env = os.environ if env is None else env

    provider = provider or env.get("FAIR2_AGENT_PROVIDER") or None
    model = model or env.get("FAIR2_AGENT_MODEL") or None
    effort = effort or env.get("FAIR2_AGENT_EFFORT") or None
    raw_max = max_tokens or env.get("FAIR2_AGENT_MAX_TOKENS") or DEFAULT_MAX_TOKENS

    if provider:
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            raise ConfigError(
                f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
            )
        model = model or DEFAULT_MODELS[provider]
    elif model:
        provider = infer_provider(model)
    else:
        provider = DEFAULT_PROVIDER
        model = DEFAULT_MODELS[provider]

    try:
        max_tokens = int(raw_max)
    except (TypeError, ValueError):
        raise ConfigError(f"max tokens must be an integer, got {raw_max!r}") from None
    if max_tokens < 1:
        raise ConfigError(f"max tokens must be positive, got {max_tokens}")

    return ModelConfig(provider=provider, model=model.strip(),
                       effort=effort.strip().lower() if effort else None,
                       max_tokens=max_tokens)


# ---- preflight --------------------------------------------------------------

def _has_anthropic_credentials(env) -> bool:
    # An unset ANTHROPIC_API_KEY does not mean there are no credentials: the SDK
    # also reads ANTHROPIC_AUTH_TOKEN and an `ant auth login` profile on disk.
    if env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profiles = env.get("XDG_CONFIG_HOME")
    base = Path(profiles) if profiles else Path.home() / ".config"
    return (base / "anthropic").is_dir()


def preflight(cfg: ModelConfig, env=None) -> str | None:
    """Return a human-readable reason the provider is unusable, or None."""
    env = os.environ if env is None else env

    try:
        __import__(cfg.provider)
    except ImportError:
        extra = _PIP_EXTRA.get(cfg.provider, cfg.provider)
        return (f"the {cfg.provider} provider needs the '{cfg.provider}' package "
                f"(pip install \"fair2-validator[{extra}]\")")

    if cfg.provider == "anthropic":
        if not _has_anthropic_credentials(env):
            return ("no Anthropic credentials found: set ANTHROPIC_API_KEY, or run "
                    "`ant auth login`")
    elif cfg.provider == "openai":
        if not (env.get("OPENAI_API_KEY") or env.get("OPENAI_BASE_URL")):
            return "no OpenAI credentials found: set OPENAI_API_KEY"
    return None


# ---- request construction (pure, so it can be asserted on in tests) ---------

def anthropic_kwargs(cfg: ModelConfig, system: str, prompt: str, schema: dict) -> dict:
    output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
    if cfg.effort:
        output_config["effort"] = cfg.effort
    return {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "thinking": {"type": "adaptive"},
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": output_config,
    }


def openai_kwargs(cfg: ModelConfig, system: str, prompt: str, schema: dict,
                  schema_name: str = "fair2_fix_plan") -> dict:
    kwargs: dict = {
        "model": cfg.model,
        "instructions": system,
        "input": prompt,
        "max_output_tokens": cfg.max_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                # The operation schema allows a free-form `value` of any JSON
                # type, which OpenAI's strict mode cannot express, so adherence
                # is best-effort here and the caller validates the result.
                "strict": False,
            }
        },
    }
    if cfg.effort:
        # Only reasoning models (gpt-5 / o-series) accept this.
        kwargs["reasoning"] = {"effort": cfg.effort}
    return kwargs


# ---- the call ---------------------------------------------------------------

def complete_json(cfg: ModelConfig, system: str, prompt: str, schema: dict,
                  schema_name: str = "fair2_fix_plan") -> dict:
    """Ask the configured model for a JSON object matching `schema`."""
    if cfg.provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(**anthropic_kwargs(cfg, system, prompt, schema))
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError(f"{cfg.model} declined the request")
        text = next((b.text for b in resp.content if b.type == "text"), "")
    elif cfg.provider == "openai":
        import openai

        client = openai.OpenAI()
        resp = client.responses.create(
            **openai_kwargs(cfg, system, prompt, schema, schema_name))
        text = resp.output_text
    else:  # pragma: no cover - resolve() rejects anything else
        raise ConfigError(f"unknown provider {cfg.provider!r}")

    if not text.strip():
        raise RuntimeError(f"{cfg.model} returned an empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{cfg.model} did not return JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{cfg.model} returned {type(parsed).__name__}, expected an object")
    return parsed
