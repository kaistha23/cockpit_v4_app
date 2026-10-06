"""
What actually happened when CreditProbe called the model.

The defect this module exists to close
--------------------------------------
The product used to compute "is the AI connected?" as `bool(api_key)`. A key
that was expired, a model name the provider had retired, a laptop behind a
corporate proxy — all three reported CONNECTED, and every question quietly fell
through to the offline reader while the banner said otherwise. A user cannot
debug what the product will not admit.

So connection is now an **observation, not a configuration**. CONNECTED means a
real structured response came back. Nothing else earns that word.

What is recorded
----------------
Safe metadata only, and the list is closed: provider, model, purpose, when, how
long, whether it worked, the provider's own request id, whether the structured
output validated, and — on failure — a category and a sanitised reason.

The API key is never read by this module, never stored, and never appears in a
failure string: `_sanitise` strips anything that looks like one before a reason
is kept. That is belt and braces, because a provider's own exception text is not
under our control.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ states

#: No provider is configured at all — no key, or AI_PROVIDER=offline.
OFFLINE = "offline"
#: A key exists, but no request has been made yet. Honest about not knowing.
CONFIGURED = "configured"
#: A real structured response has come back. The only state that claims health.
CONNECTED = "connected"
#: A key exists and requests are failing. The state the old code could not say.
DEGRADED = "degraded"

LABELS: dict[str, str] = {
    # Not "AI OFFLINE". Nothing is off: with no external provider the
    # deterministic reader parses the question and the governed runtime
    # executes it, which is a supported mode and on some bank networks the
    # only permitted one. The label names the mode rather than reporting a
    # fault the deployment does not have.
    OFFLINE: "GOVERNED LOCAL READER",
    CONFIGURED: "AI CONFIGURED",
    CONNECTED: "AI CONNECTED",
    DEGRADED: "AI DEGRADED",
}

# ------------------------------------------------------------- categories

AUTH = "auth"
#: The key is valid and the account cannot pay for the call. Kept apart from
#: AUTH because the two send an administrator to completely different places:
#: one is a wrong key, the other is a purchase.
CREDIT = "credit"
MODEL_NOT_FOUND = "model_not_found"
RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
CONNECTION = "connection"
OVERLOADED = "overloaded"
SERVER = "server"
NOT_STRUCTURED = "not_structured"
SCHEMA_INVALID = "schema_invalid"
UNKNOWN = "unknown"

#: Said in the words a credit officer would use, because these strings reach a
#: settings page rather than a log aggregator.
CATEGORY_DETAIL: dict[str, str] = {
    AUTH: "The provider rejected the API key.",
    CREDIT: "The provider accepted the key and refused the call for billing "
            "reasons — the account is out of credit or its limit is reached.",
    MODEL_NOT_FOUND: "The provider does not recognise the configured model.",
    RATE_LIMIT: "The provider is rate-limiting this key.",
    TIMEOUT: "The provider did not answer in time.",
    CONNECTION: "CreditProbe could not reach the provider — check network "
                "access or a corporate proxy.",
    OVERLOADED: "The provider is overloaded.",
    SERVER: "The provider returned a server error.",
    NOT_STRUCTURED: "The model replied in prose instead of the required "
                    "structured form.",
    SCHEMA_INVALID: "The model's structured reply did not satisfy the schema.",
    UNKNOWN: "The request failed for an unrecognised reason.",
}

#: How long a success keeps the state at CONNECTED once failures start arriving.
#: Chosen so a single transient blip does not repaint the banner, while a
#: provider that is genuinely down is admitted within a couple of questions.
CONSECUTIVE_FAILURES_TO_DEGRADE = 2

#: Requests kept in memory. Enough to show a useful recent history in Settings
#: without becoming a memory leak in a long-running container.
HISTORY = 100

#: Patterns scrubbed from any reason string before it is stored. `sk-ant-…` is
#: Anthropic's key shape; the generic rules catch bearer tokens and anything
#: long enough to be a secret.
_SECRETS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"[A-Za-z0-9_\-]{40,}"),
)


def sanitise(text: str) -> str:
    """A failure reason with anything key-shaped removed.

    Applied to every string that enters the ledger, including exception text
    the provider SDK produced, which is not under CreditProbe's control.
    """
    out = str(text or "")
    for pattern in _SECRETS:
        out = pattern.sub("[redacted]", out)
    return out[:400]


def classify(error: Exception) -> str:
    """Which failure this is, from the SDK's exception type and message.

    Type first, because it is stable; message second, because provider SDKs do
    not expose a code for every case. Both are heuristics, and an unrecognised
    failure lands in UNKNOWN rather than being forced into a neighbouring
    category that would send somebody debugging the wrong thing.
    """
    name = type(error).__name__
    by_type = {
        "AuthenticationError": AUTH,
        "BillingError": CREDIT,
        "PermissionDeniedError": AUTH,
        "NotFoundError": MODEL_NOT_FOUND,
        "RateLimitError": RATE_LIMIT,
        "APITimeoutError": TIMEOUT,
        "TimeoutError": TIMEOUT,
        "APIConnectionError": CONNECTION,
        "ConnectionError": CONNECTION,
        "InternalServerError": SERVER,
        "OverloadedError": OVERLOADED,
    }
    if name in by_type:
        return by_type[name]

    text = str(error).lower()
    status = getattr(error, "status_code", None)
    # Checked before AUTH: a credit failure arrives as a 400 whose message
    # mentions billing, and reading it as a bad key sends an administrator to
    # rotate a key that is working perfectly.
    if ("credit balance" in text or "billing" in text or "quota" in text
            or "insufficient_quota" in text or "payment" in text):
        return CREDIT
    if status in (401, 403) or "authentication" in text or "invalid x-api-key" in text:
        return AUTH
    if status == 404 or "not_found_error" in text or "model:" in text and "not found" in text:
        return MODEL_NOT_FOUND
    if status == 429 or "rate limit" in text:
        return RATE_LIMIT
    if status == 529 or "overloaded" in text:
        return OVERLOADED
    if status is not None and int(status) >= 500:
        return SERVER
    if "in prose" in text or "rather than calling" in text:
        return NOT_STRUCTURED
    if "schema" in text:
        return SCHEMA_INVALID
    if "timed out" in text or "timeout" in text:
        return TIMEOUT
    if "connect" in text or "proxy" in text or "ssl" in text:
        return CONNECTION
    return UNKNOWN


@dataclass(frozen=True)
class Call:
    """One model request, as it may safely be shown to a user."""

    provider: str
    model: str
    #: What the call was for — "reading", "repair", "interpretation",
    #: "validation". Lets Settings say which stage is failing.
    purpose: str
    #: Which configured role served it, and how hard it was asked to think.
    #: Recorded per call rather than inferred from the purpose: an
    #: administrator who configured four models needs to see which one actually
    #: answered, and a product that reports differentiated routing it is not
    #: doing is a product whose certification means nothing.
    role: str = ""
    effort: str = ""
    at: float = 0.0
    latency_ms: int = 0
    ok: bool = False
    request_id: str = ""
    structured_valid: bool = False
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    #: §19's cache telemetry. Two numbers rather than a hit flag: a request
    #: that WROTE the cache and one that READ it both look like a hit if you
    #: only record a boolean, and they cost very different amounts.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    #: Which stable prefix served it. A cache hit rate that collapsed because
    #: a prompt was edited is a different fact from one that collapsed because
    #: traffic changed, and without this they are indistinguishable.
    cache_prefix: str = ""
    failure_category: str = ""
    failure_reason: str = ""
    #: What the orchestrator did instead, when it did something instead.
    fallback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model,
            "purpose": self.purpose, "role": self.role, "effort": self.effort,
            "at": _iso(self.at), "latency_ms": self.latency_ms,
            "ok": self.ok, "request_id": self.request_id,
            "structured_valid": self.structured_valid,
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_prefix": self.cache_prefix,
            "failure_category": self.failure_category,
            "failure_detail": CATEGORY_DETAIL.get(self.failure_category, ""),
            "failure_reason": self.failure_reason,
            "fallback": self.fallback,
        }


def _iso(at: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(at, _dt.UTC).isoformat(timespec="seconds")


@dataclass
class Ledger:
    """Every model call this process has made, bounded and thread-safe.

    Process-local on purpose. This is liveness, not audit: a value that must
    survive a restart would have to be written to the database on the hot path
    of every question, and a stale CONNECTED read from a previous container is
    exactly the lie this module was built to stop telling. Validation runs,
    which DO need to survive, are persisted separately.
    """

    calls: deque[Call] = field(default_factory=lambda: deque(maxlen=HISTORY))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, call: Call) -> Call:
        with self._lock:
            self.calls.append(call)
        if not call.ok:
            logger.warning(
                "Model call failed: provider=%s model=%s purpose=%s "
                "category=%s latency=%dms request_id=%s reason=%s",
                call.provider, call.model, call.purpose, call.failure_category,
                call.latency_ms, call.request_id or "-", call.failure_reason)
        return call

    def recent(self, limit: int = 20) -> list[Call]:
        with self._lock:
            return list(self.calls)[-limit:][::-1]

    def last_success(self) -> Call | None:
        with self._lock:
            for call in reversed(self.calls):
                if call.ok:
                    return call
        return None

    def last_failure(self) -> Call | None:
        with self._lock:
            for call in reversed(self.calls):
                if not call.ok:
                    return call
        return None

    def consecutive_failures(self) -> int:
        count = 0
        with self._lock:
            for call in reversed(self.calls):
                if call.ok:
                    break
                count += 1
        return count

    def counts(self) -> dict[str, int]:
        with self._lock:
            calls = list(self.calls)
        return {
            "total": len(calls),
            "succeeded": sum(1 for c in calls if c.ok),
            "failed": sum(1 for c in calls if not c.ok),
        }

    def median_latency_ms(self) -> int:
        with self._lock:
            good = sorted(c.latency_ms for c in self.calls if c.ok)
        if not good:
            return 0
        return good[len(good) // 2]

    def state(self, *, configured: bool) -> str:
        """The four-state answer, derived from what has actually happened.

        With no key the answer is OFFLINE regardless of history. With a key the
        question is whether the most recent evidence is a success: one blip
        after a good run is tolerated, a run of failures is not, and having
        never called is CONFIGURED rather than either optimistic word.
        """
        if not configured:
            return OFFLINE
        success = self.last_success()
        failures = self.consecutive_failures()
        if failures >= CONSECUTIVE_FAILURES_TO_DEGRADE:
            return DEGRADED
        if success is not None:
            return CONNECTED
        if failures:
            return DEGRADED
        return CONFIGURED

    def reset(self) -> None:
        """Forget the history. Used when the provider or model changes, and by
        tests; a state carried across a configuration change is meaningless."""
        with self._lock:
            self.calls.clear()


_ledger = Ledger()


def ledger() -> Ledger:
    return _ledger


def record_success(*, provider: str, model: str, purpose: str,
                   latency_ms: int, request_id: str = "",
                   attempts: int = 1, input_tokens: int = 0,
                   output_tokens: int = 0,
                   structured_valid: bool = True,
                   role: str = "", effort: str = "",
                   cache_write_tokens: int = 0, cache_read_tokens: int = 0,
                   cache_prefix: str = "") -> Call:
    return _ledger.record(Call(
        provider=provider, model=model, purpose=purpose, at=time.time(),
        role=role, effort=effort,
        latency_ms=latency_ms, ok=True, request_id=request_id,
        structured_valid=structured_valid, attempts=attempts,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens, cache_prefix=cache_prefix))


def record_failure(*, provider: str, model: str, purpose: str,
                   latency_ms: int, error: Exception | None = None,
                   category: str = "", reason: str = "",
                   request_id: str = "", attempts: int = 1,
                   fallback: str = "", role: str = "", effort: str = "") -> Call:
    resolved = category or (classify(error) if error else UNKNOWN)
    return _ledger.record(Call(
        provider=provider, model=model, purpose=purpose, at=time.time(),
        role=role, effort=effort,
        latency_ms=latency_ms, ok=False, request_id=request_id,
        structured_valid=False, attempts=attempts,
        failure_category=resolved,
        failure_reason=sanitise(reason or (str(error) if error else "")),
        fallback=fallback))


def cache_summary(window: int = 50) -> dict[str, Any]:
    """§19's cache telemetry, safely.

    Token counts and a rate — never prompt content. The whole point of caching
    is that a long prefix is reused, and a telemetry surface that showed which
    prefix would be showing the prompt.

    `served_by_cache` is read tokens over all prompt tokens, which is the
    number that answers the question anybody actually asks: how much of what
    we send are we paying full price for.
    """
    calls = [c for c in _ledger.recent(window) if c.ok]
    read = sum(c.cache_read_tokens for c in calls)
    written = sum(c.cache_write_tokens for c in calls)
    prompt = sum(c.input_tokens for c in calls) + read + written
    return {
        "calls": len(calls),
        "cache_read_tokens": read,
        "cache_write_tokens": written,
        "prompt_tokens": prompt,
        "served_by_cache": round(read / prompt, 4) if prompt else 0.0,
        "prefixes": sorted({c.cache_prefix for c in calls if c.cache_prefix}),
    }


def health(*, provider: str, model: str, configured: bool) -> dict[str, Any]:
    """Everything Settings, /ask/mode and the header chip need, in one shape."""
    state = _ledger.state(configured=configured)
    success = _ledger.last_success()
    failure = _ledger.last_failure()
    return {
        "provider": provider,
        "model": model,
        "configured": configured,
        "state": state,
        "label": LABELS.get(state, state.upper()),
        "live": state == CONNECTED,
        "counts": _ledger.counts(),
        "median_latency_ms": _ledger.median_latency_ms(),
        "consecutive_failures": _ledger.consecutive_failures(),
        "last_success": success.to_dict() if success else None,
        "last_failure": failure.to_dict() if failure else None,
        "recent": [c.to_dict() for c in _ledger.recent(10)],
        "cache": cache_summary(),
        "detail": _detail(state, provider, model, failure),
    }


def _detail(state: str, provider: str, model: str,
            failure: Call | None) -> str:
    """The internal sentence. Names the provider and the model.

    Shown on the Trace's technical layer and to an administrator, where
    knowing exactly which model produced an answer is the whole point of
    recording it. `_public_detail` is what a normal user sees — see §12.
    """
    if state == OFFLINE:
        return ("No AI provider key is configured. CreditProbe reads questions "
                "with its deterministic governed semantic reader and computes "
                "every figure in the governed runtime.")
    if state == CONNECTED:
        return (f"Questions are read and interpreted by {model} via {provider}. "
                "It never calculates a figure.")
    if state == DEGRADED:
        because = CATEGORY_DETAIL.get(
            failure.failure_category if failure else "", "")
        return (f"A key is configured for {provider}, but requests are failing. "
                + because + " CreditProbe is answering with its deterministic "
                "governed semantic reader in the meantime, and says so on every "
                "answer.")
    return (f"A key is configured for {provider} ({model}), but no request has "
            "been made yet, so CreditProbe cannot yet confirm the model is "
            "reachable. Ask a question or run an intelligence check.")


def _public_detail(state: str, failure: Call | None) -> str:
    """The same four states, said without naming a vendor. §12.

    Every sentence still carries the thing a user can act on — whether the
    intelligence layer is working, and what CreditProbe does when it is not.
    None of them carries the thing only a competitor benefits from.
    """
    if state == OFFLINE:
        return ("No AI provider is configured. CreditProbe reads questions "
                "with its deterministic governed semantic reader and computes "
                "every figure in the governed runtime.")
    if state == CONNECTED:
        return ("Questions are read and interpreted by the configured "
                "intelligence provider. It never calculates a figure.")
    if state == DEGRADED:
        because = CATEGORY_DETAIL.get(
            failure.failure_category if failure else "", "")
        return ("An intelligence provider is configured, but requests are "
                "failing. " + because + " CreditProbe is answering with its "
                "deterministic governed semantic reader in the meantime, and "
                "says so on every answer.")
    return ("An intelligence provider is configured, but no request has been "
            "made yet, so CreditProbe cannot yet confirm it is reachable. "
            "Ask a question or run an intelligence check.")


def public_health(*, provider: str, model: str,
                  configured: bool) -> dict[str, Any]:
    """`health`, with every vendor identity removed. §12.

    The provider and the model are not omitted from the SYSTEM — they stay in
    the ledger, in the Trace's audit layer, in the run key that makes an answer
    reproducible, and on the administrator's own view. They are omitted from
    the payload a normal product surface renders, because a header reading
    "interpreted by claude-opus-5 via anthropic" tells a credit officer
    nothing they can use and tells everyone else which vendor the bank is
    committed to.

    The keys stay present and empty rather than disappearing, so a reader
    written against the internal shape does not fall over — and so a test can
    assert they are EMPTY rather than merely absent, which is a stronger thing
    to assert.
    """
    body = health(provider=provider, model=model, configured=configured)
    body["provider"] = ""
    body["model"] = ""
    body["detail"] = _public_detail(body["state"], _ledger.last_failure())
    body["identity_withheld"] = True
    for call in [body.get("last_success"), body.get("last_failure"),
                 *(body.get("recent") or [])]:
        if isinstance(call, dict):
            call.pop("provider", None)
            call.pop("model", None)
    return body


__all__ = [
    "AUTH", "CONFIGURED", "CONNECTED", "CONNECTION", "CREDIT", "DEGRADED",
    "MODEL_NOT_FOUND", "NOT_STRUCTURED", "OFFLINE", "OVERLOADED", "RATE_LIMIT",
    "SCHEMA_INVALID", "SERVER", "TIMEOUT", "UNKNOWN",
    "Call", "Ledger", "cache_summary", "classify", "health", "ledger",
    "public_health", "record_failure", "record_success", "sanitise",
]
