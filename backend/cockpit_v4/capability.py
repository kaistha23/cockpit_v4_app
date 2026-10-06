"""
The verified model capability record and the price card.

Why a record rather than a constant
-----------------------------------
V3 hard-coded Sonnet and Opus prices as settings and reserved against them.
That is a number in a config file, and a number in a config file is not a
verified price: if the provider's schedule moved, the "enforced" spend badge
was decoration. So V4 loads a versioned price card that names the exact
provider, model id, pricing terms and the time it was verified -- and if it
cannot, paid work FAILS CLOSED with `CAPABILITY_UNVERIFIED`. A user is never
shown an "enforced" cost badge while the cost is unknown.

Cache writes and cache reads are priced separately because they are billed
separately. Reserving only uncached input under-reserves a cached run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.cockpit_v4.states import CAPABILITY_UNVERIFIED


class CapabilityUnverified(RuntimeError):
    """No verified capability/price for the configured model. Fails closed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = CAPABILITY_UNVERIFIED


@dataclass(frozen=True)
class PriceCard:
    """USD per million tokens, by billing class. All four are required."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_write_usd_per_mtok: float
    cache_read_usd_per_mtok: float

    def cost(self, *, input_tokens: int = 0, output_tokens: int = 0,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0
             ) -> float:
        return round(
            input_tokens * self.input_usd_per_mtok / 1_000_000
            + output_tokens * self.output_usd_per_mtok / 1_000_000
            + cache_write_tokens * self.cache_write_usd_per_mtok / 1_000_000
            + cache_read_tokens * self.cache_read_usd_per_mtok / 1_000_000, 8)

    def to_dict(self) -> dict[str, Any]:
        return {"input_usd_per_mtok": self.input_usd_per_mtok,
                "output_usd_per_mtok": self.output_usd_per_mtok,
                "cache_write_usd_per_mtok": self.cache_write_usd_per_mtok,
                "cache_read_usd_per_mtok": self.cache_read_usd_per_mtok}


@dataclass(frozen=True)
class Capability:
    """What this runtime has actually verified about the analyst model."""

    provider: str
    model_id: str
    sdk_version: str
    context_tokens: int
    max_output_tokens: int
    supports_tools: bool
    supports_token_counting: bool
    price: PriceCard
    source: str
    verified_at: str
    #: True only when a live provider call confirmed the id serves. A card
    #: read from disk is a DECLARATION; it becomes verified when checked.
    live_verified: bool = False
    #: What this model ACCEPTS, from `model_capabilities`. Not from the
    #: price card: whether a model takes `tool_choice` is a fact about the
    #: provider's API, and an operator updating a price is not making a
    #: claim about a request parameter. See `model_capabilities`.
    traits: Any = None

    @property
    def supports_forced_tool_use(self) -> bool:
        return self._traits().forced_tool_use

    @property
    def supports_named_tool_forcing(self) -> bool:
        return self._traits().named_tool_forcing

    @property
    def supports_effort_control(self) -> bool:
        return self._traits().effort_control

    @property
    def supports_single_tool_per_turn(self) -> bool:
        return self._traits().single_tool_per_turn

    @property
    def supports_sampling_control(self) -> bool:
        return bool(getattr(self._traits(), "sampling_control", False))

    @property
    def supports_plan_compiler(self) -> bool:
        return bool(getattr(self._traits(), "plan_compiler", False))

    def _traits(self):
        from backend.cockpit_v4 import model_capabilities as caps

        return self.traits or caps.traits_for(self.model_id)

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model_id": self.model_id,
                "sdk_version": self.sdk_version,
                "context_tokens": self.context_tokens,
                "max_output_tokens": self.max_output_tokens,
                "supports_tools": self.supports_tools,
                "supports_token_counting": self.supports_token_counting,
                "traits": self._traits().to_dict(),
                "price": self.price.to_dict(), "source": self.source,
                "verified_at": self.verified_at,
                "live_verified": self.live_verified}


_REQUIRED_PRICE_KEYS = ("input_usd_per_mtok", "output_usd_per_mtok",
                        "cache_write_usd_per_mtok", "cache_read_usd_per_mtok")


def load_price_card(path: str | Path, *, model_id: str,
                    provider: str) -> Capability:
    """Read the versioned card. Every failure is explicit and fails closed."""
    p = Path(path).expanduser()
    if not p.exists():
        raise CapabilityUnverified(
            f"the price card {p} does not exist, so no paid request can be "
            f"reserved. Set COCKPIT_V4_PRICE_CARD to a verified card.")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityUnverified(
            f"the price card {p} could not be read: {exc}") from exc

    models = doc.get("models")
    if not isinstance(models, dict) or model_id not in models:
        raise CapabilityUnverified(
            f"the price card {p} carries no entry for model {model_id!r}. A "
            f"price for a different model is not this model's price.")
    entry = models[model_id]
    if str(entry.get("provider") or doc.get("provider") or "") != provider:
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} is for a different "
            f"provider than the configured {provider!r}.")
    price_raw = entry.get("price")
    if not isinstance(price_raw, dict):
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} has no price block.")
    missing = [k for k in _REQUIRED_PRICE_KEYS if k not in price_raw]
    if missing:
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} is missing "
            f"{', '.join(missing)}. Cache writes and reads are billed "
            f"separately and must be priced separately.")
    try:
        price = PriceCard(**{k: float(price_raw[k])
                             for k in _REQUIRED_PRICE_KEYS})
    except (TypeError, ValueError) as exc:
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} has a non-numeric "
            f"price: {exc}") from exc

    for key in ("context_tokens", "max_output_tokens"):
        if not isinstance(entry.get(key), int) or entry[key] <= 0:
            raise CapabilityUnverified(
                f"the price card entry for {model_id!r} must declare a "
                f"positive {key}.")

    # A PRICE NOBODY RECORDED CHECKING IS NOT A PRICE.
    #
    # The shipped card says, in its own note: "A missing or unverified entry
    # fails closed: no paid request runs." A missing one did. An UNVERIFIED
    # one did not -- `verified_at` was read into the Capability, surfaced in
    # `to_dict`, and gated nothing anywhere in the product. The field whose
    # only job is to stop an unpriced paid run was decorative.
    #
    # What that costs: the shipped card is a template whose one entry is
    # `REPLACE-WITH-YOUR-MODEL-ID`, `source: PLACEHOLDER`, `verified_at: ""`
    # and all four prices 0.0. Copy it, change the model id, and every paid
    # request is costed at zero. A spend cap can never be reached, the run
    # reports no cost, and nothing says the numbers were never real.
    source = str(entry.get("source") or doc.get("source") or "")
    verified = str(entry.get("verified_at") or doc.get("verified_at") or "")
    if not verified:
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} has no `verified_at`. A "
            f"price nobody recorded checking is not a price: put the date "
            f"you read it off the provider's published schedule.")
    try:
        _date.fromisoformat(verified[:10])
    except ValueError:
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} has `verified_at` "
            f"{verified!r}, which is not a date. Use an ISO-8601 date or "
            f"timestamp, so a reader can tell how old this price is."
        ) from None
    if not source or "placeholder" in source.lower():
        raise CapabilityUnverified(
            f"the price card entry for {model_id!r} still carries the "
            f"placeholder `source`. Name where the numbers came from -- the "
            f"provider's published schedule for this account -- or no paid "
            f"request can be reserved against them.")

    return Capability(
        provider=provider, model_id=model_id,
        sdk_version=str(entry.get("sdk_version") or doc.get("sdk_version")
                        or "unrecorded"),
        context_tokens=int(entry["context_tokens"]),
        max_output_tokens=int(entry["max_output_tokens"]),
        supports_tools=bool(entry.get("supports_tools", True)),
        supports_token_counting=bool(entry.get("supports_token_counting",
                                               True)),
        price=price,
        source=str(entry.get("source") or doc.get("source") or "unrecorded"),
        verified_at=str(entry.get("verified_at") or doc.get("verified_at")
                        or ""),
        live_verified=False)


def verify_live(capability: Capability, provider: Any) -> Capability:
    """Confirm the provider will actually serve this id, cheaply.

    Uses the token-counting endpoint where available: it names the model,
    costs nothing, and turns a configured-but-wrong id into an explicit
    capability failure rather than a confusing error three calls later.
    """
    counter = getattr(provider, "count_tokens", None)
    if not callable(counter):
        return capability
    try:
        counter(system="ok", messages=[{"role": "user", "content": "ok"}],
                model=capability.model_id)
    except Exception as exc:  # noqa: BLE001 - any failure means unverified
        raise CapabilityUnverified(
            f"the provider would not serve model {capability.model_id!r}: "
            f"{exc}") from exc
    return Capability(**{**capability.__dict__, "live_verified": True,
                         "verified_at": datetime.now(timezone.utc)
                         .isoformat(timespec="seconds")})


__all__ = ["Capability", "CapabilityUnverified", "PriceCard",
           "load_price_card", "verify_live"]
