"""
Prompt caching for the stable prefix. §19.

What is stable and what is not
------------------------------
A CreditProbe prompt is mostly the same every time. The system policy, the
ontology summary, the tool and planner schemas and the security rules do not
change between two questions asked a second apart; the question, the
conversation and the retrieved teaching cases do. Caching pays for exactly
that shape — a long identical prefix followed by a short varying tail — and
pays nothing at all if the two are interleaved.

So the composition is ordered, and the order is the point: every cacheable
block comes first, in a fixed sequence, and the breakpoint goes at the end of
that run. A block inserted in the middle invalidates everything after it,
which turns a cache hit into a cache write and costs more than not caching.

What may never be cached
------------------------
§19: "Do not cache client-sensitive raw content beyond provider/data-retention
policy." A block marked sensitive is refused a cache breakpoint AND refused a
place in the stable prefix — both, because either alone leaves it cached: a
sensitive block before the breakpoint is inside the cached span even though it
carries no marker of its own.

That is the rule this module exists to make hard to get wrong. Everything else
here is bookkeeping.

Teaching cases in the prefix
----------------------------
§19 names "stable teaching cases where repeated" as cacheable. They are — but
only the ones that repeat: a pack retrieved for this question is part of the
varying tail, and caching it would write a new cache entry per question. So a
caller marks a teaching block cacheable only when it is a fixed set shown on
every request, and the default is not cacheable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

CACHING_VERSION = "1.0.0"

#: The provider's marker. Named once so a change is one edit rather than a
#: search, and so nothing else in the codebase spells it.
EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

#: The order §19 gives, and the order the composition enforces. A block whose
#: name is not here is appended after the stable run and never cached.
STABLE_ORDER: tuple[str, ...] = (
    "system_policy",
    "ontology",
    "tool_schema",
    "planner_schema",
    "security_rules",
    "teaching_cases",
)

#: Below this, a cache entry costs more to write than it saves. Providers put
#: the floor around a thousand tokens; the number here is characters, at the
#: same four-to-one estimate the Teaching Pack uses.
MIN_CACHEABLE_CHARS = 4000


@dataclass(frozen=True)
class Block:
    """One piece of a prompt, and whether it may be cached."""

    name: str
    text: str
    #: Whether this block is the same on the next request. Default False: a
    #: block is varying until somebody says otherwise, because the cost of
    #: wrongly calling something stable is a stale prompt and the cost of
    #: wrongly calling it varying is a few cents.
    cacheable: bool = False
    #: Client-derived content. Never cached, and never inside the cached span.
    sensitive: bool = False

    @property
    def stable(self) -> bool:
        return self.cacheable and not self.sensitive and \
            self.name in STABLE_ORDER


def _ordered(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    """The stable run, in §19's order, and everything else after it."""
    stable = [b for name in STABLE_ORDER
              for b in blocks if b.name == name and b.stable and b.text]
    rest = [b for b in blocks if b not in stable and b.text]
    return stable, rest


def identity(blocks: list[Block]) -> str:
    """A handle for the cached prefix.

    Changes when the prefix changes, which is the one thing telemetry needs to
    know: a cache hit rate that collapsed because a prompt was edited is a
    different fact from one that collapsed because traffic changed.
    """
    stable, _ = _ordered(blocks)
    blob = "\x1e".join(f"{b.name}\x1f{b.text}" for b in stable)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def worth_caching(blocks: list[Block]) -> bool:
    """Whether the stable run is long enough to be worth a cache entry."""
    stable, _ = _ordered(blocks)
    return sum(len(b.text) for b in stable) >= MIN_CACHEABLE_CHARS


def plain(blocks: list[Block]) -> str:
    """The whole prompt as one string, for a provider with no caching.

    Same order and same content as the cached form. A provider that cannot
    cache must still see the identical prompt, or two deployments of the same
    release answer differently.
    """
    stable, rest = _ordered(blocks)
    return "\n\n".join(b.text for b in [*stable, *rest] if b.text)


def compose(blocks: list[Block]) -> list[dict[str, Any]]:
    """The prompt as provider content blocks, with one cache breakpoint.

    One breakpoint, at the end of the stable run. Providers allow a handful,
    but every extra one is another entry to write and another chance for a
    partial hit; a single boundary between "same every time" and "different
    every time" is what the prompt's shape actually is.

    When nothing is stable — or the stable run is too short to pay for
    itself — no breakpoint is emitted at all and the result is ordinary
    content.
    """
    stable, rest = _ordered(blocks)
    out: list[dict[str, Any]] = []

    mark = bool(stable) and worth_caching(blocks)
    for index, block in enumerate(stable):
        entry: dict[str, Any] = {"type": "text", "text": block.text}
        if mark and index == len(stable) - 1:
            entry["cache_control"] = dict(EPHEMERAL)
        out.append(entry)

    out += [{"type": "text", "text": b.text} for b in rest]
    return out


def refusals(blocks: list[Block]) -> list[str]:
    """Every block that asked to be cached and was refused, with the reason.

    Surfaced rather than silent: a caller that marked a sensitive block
    cacheable has made a mistake worth telling them about, and a caller whose
    block name is not in the stable order has usually misspelled it.
    """
    out: list[str] = []
    for block in blocks:
        if not block.cacheable:
            continue
        if block.sensitive:
            out.append(f"{block.name}: client-sensitive content is never "
                       "cached (§19)")
        elif block.name not in STABLE_ORDER:
            out.append(f"{block.name}: not a stable prefix block; it would "
                       "invalidate the cache on every request")
    return out


def usage(raw: Any) -> dict[str, int]:
    """The cache numbers off a provider response, whatever it calls them.

    Read defensively. A provider that stops reporting cache usage must make
    the telemetry say zero, not raise inside a successful call.
    """
    def _int(name: str) -> int:
        try:
            return int(getattr(raw, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "cache_creation_input_tokens": _int("cache_creation_input_tokens"),
        "cache_read_input_tokens": _int("cache_read_input_tokens"),
    }


__all__ = ["CACHING_VERSION", "EPHEMERAL", "MIN_CACHEABLE_CHARS",
           "STABLE_ORDER", "Block", "compose", "identity", "plain",
           "refusals", "usage", "worth_caching"]
