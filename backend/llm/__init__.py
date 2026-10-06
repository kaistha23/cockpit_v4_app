"""
Which model CreditProbe orchestrates with, decided in one place.

`AI_PROVIDER` names the provider and `AI_MODEL` optionally pins the model; the
key comes from the provider's own environment variable. Nothing else in the
codebase reads a key or constructs a client, so "is the AI configured" has one
answer and one place to change it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend.config import settings
from backend.llm import telemetry
from backend.llm.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from backend.llm.omlx_provider import OMLXProvider
from backend.llm.base import (
    LLMError,
    LLMProvider,
    LLMResult,
    NullProvider,
    ProviderStatus,
    factories,
    register,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_provider: Any = None
#: The (provider, model) the cached instance was built for. A change to either
#: invalidates the observed health, because a CONNECTED earned by one model
#: says nothing about another.
_built_for: tuple[str, str] = ("", "")

#: Explicitly-selected (per-run) providers, keyed by the normalised
#: (provider, model) pair. Separate from `_provider`/`_built_for` above: a
#: request-scoped selection must never REPLACE the process default client,
#: because simultaneous runs can select different providers -- but distinct
#: runs that happen to choose the SAME pair should still share one client
#: rather than opening a fresh connection per call. These provider objects
#: hold only configuration (key, model, base URL); `converse`/`structured`
#: never mutate `self`, so sharing one instance across concurrent callers is
#: safe. Never evicted automatically: the set of (provider, model) pairs an
#: operator qualifies is small and stable for the life of the process.
_explicit_cache: dict[tuple[str, str], LLMProvider] = {}
_explicit_lock = threading.Lock()


def get_provider(*, refresh: bool = False, provider: str = "", model: str = "") -> LLMProvider:
    """The configured provider, or NullProvider when there is no key.

    Cached: a provider holds an HTTP client, and building one per question adds
    a connection handshake to every answer.
    """
    global _provider, _built_for
    if provider or model:
        key = ((provider or settings.ai_provider or "anthropic").strip().lower(),
               (model or settings.ai_model or "").strip())
        with _explicit_lock:
            if refresh:
                _explicit_cache.pop(key, None)
            cached = _explicit_cache.get(key)
            if cached is not None:
                return cached
            built = _build(*key)
            # A provider that could not be built (no key yet, unknown name) is
            # NOT cached: the key may be configured a moment later, and a
            # remembered NullProvider would keep refusing every call after.
            if built.configured:
                _explicit_cache[key] = built
            return built
    with _lock:
        wanted = ((provider or settings.ai_provider or "").strip().lower(),
                  (model or settings.ai_model or "").strip())
        if _provider is not None and not refresh and wanted == _built_for:
            return _provider
        if wanted != _built_for and _built_for != ("", ""):
            # The configuration changed under us. Health observed against the
            # previous provider must not be reported against this one.
            telemetry.ledger().reset()
        _provider = _build(*wanted)
        _built_for = wanted
        return _provider


def _build(name: str = "", model: str = "") -> LLMProvider:
    name = (name or settings.ai_provider or "anthropic").strip().lower()
    model = (model or settings.ai_model or "").strip()

    if name in {"", "none", "offline"}:
        return NullProvider(reason="AI_PROVIDER is set to offline.")

    key = _key_for(name)
    if name != "omlx" and not key:
        return NullProvider(
            reason=f"No API key is configured for the '{name}' provider.")

    build = factories().get(name)
    if build is None:
        logger.warning("Unknown AI_PROVIDER '%s'; running offline.", name)
        return NullProvider(reason=f"'{name}' is not a provider CreditProbe knows.")
    try:
        return build(key, model)
    except Exception as e:  # noqa: BLE001 - a bad provider must not stop the app
        logger.warning("Could not build the '%s' provider: %s", name, e)
        return NullProvider(reason=f"The '{name}' provider could not be built: {e}")


def _key_for(name: str) -> str:
    return {"anthropic": settings.anthropic_api_key,
            "omlx": getattr(settings, "omlx_api_key", "")}.get(name, "")


def provider_status() -> ProviderStatus:
    return get_provider().status()


def is_configured() -> bool:
    """Whether a provider key exists.

    Necessary and not sufficient: it says a call CAN be attempted, never that
    one has succeeded. Anything reporting health to a user must read `health()`
    or `ProviderStatus.live` instead — presenting a configured key as a working
    model is the specific dishonesty this module was rebuilt to end.
    """
    return get_provider().configured


def is_live() -> bool:
    """Whether a real structured response has actually come back."""
    return get_provider().status().live


def health() -> dict[str, Any]:
    """The observed provider health, INCLUDING which provider and model.

    Contains no key and no request body — see backend/llm/telemetry for the
    closed list of what is recorded. It does contain the vendor's name and the
    model identifier, which governance, observability and the reproducibility
    run key all need, and which §12 forbids a normal product surface from
    rendering. Use `public_health` for anything a user reads.
    """
    provider = get_provider()
    return telemetry.health(provider=provider.name, model=provider.model,
                            configured=provider.configured)


def public_health() -> dict[str, Any]:
    """`health` with the vendor identity withheld. §12.

    The default for every product surface. `health` remains available to the
    audit layer and to administrators, and the two shapes are otherwise
    identical so nothing downstream has to branch.
    """
    provider = get_provider()
    return telemetry.public_health(provider=provider.name,
                                   model=provider.model,
                                   configured=provider.configured)


__all__ = [
    "DEFAULT_MODEL",
    "health", "public_health",
    "is_live",
    "telemetry",
    "AnthropicProvider",
    "OMLXProvider",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "NullProvider",
    "ProviderStatus",
    "get_provider",
    "is_configured",
    "provider_status",
    "register",
]
