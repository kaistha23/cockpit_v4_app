"""
Counting tokens before a request, against the model that will serve it.
Specification section 9.3, and the owner's instruction for this UAT phase.

Why a provider count and not an estimate
----------------------------------------
The local estimate this package shipped with is a character heuristic. It is
conservative by design, but it is still a guess about a tokenizer, and the
tokenizer differs between model families. A guess that is 20% low lets an
oversized packet through and the request fails at the provider with a 400 that
tells the user nothing useful; a guess that is 20% high refuses work that would
have fit.

So `count` asks the provider to count, for the exact model that will serve the
request, through `client.messages.count_tokens`. The local estimate stays as
the fallback for when there is no credential or the endpoint is unreachable,
and every result says which method produced it -- an estimate must never be
reported as a measurement.

The counting endpoint is itself a network call
-----------------------------------------------
Section 9.3 requires that provider token-counting activity be tracked
separately and never become an unbounded preflight loop. `Counter` therefore
caches by payload fingerprint, bounds the number of counting calls per request,
and records every one. It does not consume the request's model-call budget --
counting is not inference -- but it is reported, because an unmeasured network
call in the hot path is how a latency budget quietly disappears.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Characters per token for the local fallback, CALIBRATED against the
#: provider's own counter rather than assumed.
#:
#: This shipped at 3.4, reasoning that dense JSON tokenizes at roughly 3.5-4
#: characters per token. Live UAT measured that wrong for this content: the
#: same assembled gate request estimated 41,261 tokens here and counted 60,530
#: at the provider -- 2.32 characters per token. Short keys, punctuation and
#: identifiers tokenize far denser than prose.
#:
#: A 47% under-count is the failure that matters, because it is the one that
#: lets an oversized request through. 2.2 is the measured 2.32 with a small
#: margin the safe way. It remains a FALLBACK: whenever a credential is
#: configured, `Counter` asks the provider and reports the answer as measured.
CHARS_PER_TOKEN = 2.2

MEASURED = "provider_count_tokens"
ESTIMATED = "local_conservative_estimate"

#: Counting calls permitted per request. Generous enough for a packet, a
#: repair and a review, small enough that a loop is visible immediately.
MAX_COUNT_CALLS = 12


def estimate(payload: Any) -> int:
    """The local fallback. Never reported as a measurement."""
    text = (payload if isinstance(payload, str)
            else json.dumps(payload, separators=(",", ":"), default=str))
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _fingerprint(**parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.blake2b(blob.encode(), digest_size=16).hexdigest()


@dataclass
class Count:
    """A token count, and how it was arrived at."""

    tokens: int
    method: str
    model: str = ""
    cached: bool = False
    error: str = ""

    @property
    def measured(self) -> bool:
        return self.method == MEASURED

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "method": self.method,
                "model": self.model, "measured": self.measured,
                "cached": self.cached, "error": self.error}


class TooLargeToSend(RuntimeError):
    """The assembled request will not fit. Refused before dispatch."""

    def __init__(self, message: str, *, counted: Count, cap: int) -> None:
        super().__init__(message)
        self.counted = counted
        self.cap = cap


@dataclass
class Counter:
    """Counts tokens for one request, with the provider where possible."""

    provider: Any = None
    model: str = ""
    calls: int = 0
    fallbacks: int = 0
    _cache: dict[str, Count] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def available(self) -> bool:
        """Whether a provider count can even be attempted."""
        return bool(self.provider is not None
                    and getattr(self.provider, "configured", False)
                    and hasattr(self.provider, "count_tokens"))

    def count(self, *, system: Any, messages: list[dict[str, Any]],
              tools: list[dict[str, Any]] | None = None) -> Count:
        """Count the assembled request, against the model that will serve it."""
        key = _fingerprint(system=system, messages=messages, tools=tools,
                           model=self.model)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return Count(tokens=cached.tokens, method=cached.method,
                             model=cached.model, cached=True,
                             error=cached.error)

        local = estimate({"system": system, "messages": messages,
                          "tools": tools})

        if not self.available:
            result = Count(tokens=local, method=ESTIMATED, model=self.model,
                           error="no provider token counter is configured")
        elif self.calls >= MAX_COUNT_CALLS:
            # Section 9.3: token-counting activity must not become an
            # unbounded network preflight loop.
            self.fallbacks += 1
            result = Count(
                tokens=local, method=ESTIMATED, model=self.model,
                error=f"the {MAX_COUNT_CALLS}-call token-counting budget for "
                      f"this request is spent")
        else:
            try:
                self.calls += 1
                counted = self.provider.count_tokens(
                    system=system, messages=messages, tools=tools,
                    model=self.model)
                result = Count(tokens=int(counted), method=MEASURED,
                               model=self.model)
            except Exception as e:                          # noqa: BLE001
                self.fallbacks += 1
                logger.info("The provider token count failed; falling back to "
                            "the local estimate: %s", e)
                result = Count(tokens=local, method=ESTIMATED,
                               model=self.model, error=str(e)[:200])

        with self._lock:
            self._cache[key] = result
        return result

    def fits(self, *, system: Any, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None, cap: int,
             reserve_output: int) -> Count:
        """Count, and refuse the request if it will not fit safely.

        `reserve_output` is the output allowance the same call needs. A packet
        that fits the input cap but leaves no room for the answer has not
        actually fitted, and finding that out from a truncated response costs a
        whole call.
        """
        counted = self.count(system=system, messages=messages, tools=tools)
        if counted.tokens > cap:
            raise TooLargeToSend(
                f"The assembled request is {counted.tokens:,} tokens "
                f"({'measured against ' + counted.model if counted.measured else 'estimated locally'}) "
                f"against a {cap:,}-token per-call limit. It was not sent.",
                counted=counted, cap=cap)
        headroom = cap - counted.tokens
        if headroom < reserve_output:
            raise TooLargeToSend(
                f"The assembled request is {counted.tokens:,} tokens and needs "
                f"{reserve_output:,} tokens of output allowance, which exceeds "
                f"the {cap:,}-token limit by "
                f"{reserve_output - headroom:,}. It was not sent: a request "
                f"that fits only if the answer is truncated has not fitted.",
                counted=counted, cap=cap)
        return counted

    def report(self) -> dict[str, Any]:
        return {
            "provider_counting_available": self.available,
            "count_calls_made": self.calls,
            "count_call_budget": MAX_COUNT_CALLS,
            "fell_back_to_estimate": self.fallbacks,
            "model": self.model,
            "note": ("Token-counting calls are tracked separately from the "
                     "request's model-call budget -- counting is not inference "
                     "-- and are bounded so a preflight loop is visible."),
        }


__all__ = ["CHARS_PER_TOKEN", "Count", "Counter", "ESTIMATED",
           "MAX_COUNT_CALLS", "MEASURED", "TooLargeToSend", "estimate"]
