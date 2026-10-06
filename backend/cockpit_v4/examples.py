"""
Verified worked plans, shown to a small local model beside a similar question.

WHY this exists
---------------
A local model that has never seen a governed plan for "ECL by sector" has to
invent the shape from the schema alone. Two solved neighbours in the same
domain remove most of that guessing. It is a MEASURED aid, not a given: it is
OFF unless `COCKPIT_V4_EXAMPLES=on`, and it is kept only if live paired
holdout runs say so.

WHY the examples are curated, not generated from the corpus
-----------------------------------------------------------
`questions.json` carries gold SQL, not plans. The plans in `examples.json`
are hand-written, drawn only from TRAIN items that are ready, and every one
is compiled AND EXECUTED against the live releases by scripts/cockpit_v4/verify_examples.py. Holdout and sealed items
are the instrument that decides whether this helps; showing the model their
shape (or their `group`, which links near-duplicates) would turn the
instrument into a leak, so a guard test pins that none of them is used.

WHY relative periods
--------------------
`latest`, `prior` and `last_n` carry no calendar, so the model cannot copy a
stale quarter out of an example. The one explicit-period example says in its
note that the value must come from the question.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

EXAMPLES_PATH = Path(__file__).resolve().parent / "examples.json"

ENV_FLAG = "COCKPIT_V4_EXAMPLES"

#: Below this token-Jaccard a neighbour is a different question; showing it
#: would teach the wrong shape, so nothing is shown instead.
MIN_SIMILARITY = 0.2

NOTE = ("Similar solved questions. Adapt the plan to THIS question; never "
        "copy periods or values that the question does not state.")

_STOPWORDS = frozenset((
    "a an the of in on at by for to from and or is are was were be been do "
    "does did what which who whom how much many give show me us our we i "
    "tell list get has have had with within over this that these those it "
    "its per as into than then there their latest current").split())

_TOKEN = re.compile(r"[a-z0-9]+")


def enabled() -> bool:
    """Read at call time so a test or an operator can flip it without a reload."""
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("on", "1", "true", "yes")


@lru_cache(maxsize=1)
def load() -> tuple[dict[str, Any], ...]:
    """The curated examples. Cached: the file ships with the code."""
    data = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    return tuple(data.get("examples") or ())


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall(str(text or "").lower())
                     if t not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def select(question: str, domain_id: str, k: int = 2) -> list[dict[str, Any]]:
    """Up to `k` same-domain neighbours at or above `MIN_SIMILARITY`, best first.

    Ties break on file order so the choice is deterministic.
    """
    asked = _tokens(question)
    scored = []
    for order, example in enumerate(load()):
        if example.get("domain") != domain_id:
            continue
        score = _jaccard(asked, _tokens(example.get("question", "")))
        if score >= MIN_SIMILARITY:
            scored.append((-score, order, example))
    scored.sort(key=lambda row: row[:2])
    return [row[2] for row in scored[:max(0, k)]]


def block(question: str, domain_id: str) -> dict[str, Any] | None:
    """The prompt fragment, or None when no neighbour is close enough."""
    chosen = select(question, domain_id)
    if not chosen:
        return None
    shown = []
    for example in chosen:
        item = {"question": example["question"], "plan": example["plan"]}
        if example.get("note"):
            item["note"] = example["note"]
        shown.append(item)
    return {"worked_examples": shown, "note": NOTE}
