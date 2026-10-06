"""Was that question back to the reader EARNED?

WHY this exists. The same question -- "What is driving Stage 2 and ECL
growth?" -- is answered on one run and returned as a clarification on the
next, because whether the local model decides to ask is a coin the server
does not control. Every term in it maps; the only choice left is a default
decomposition the analyst can state and run. A reader who is asked on Tuesday
what they were not asked on Monday cannot trust either answer.

The fix is not to tell the model to ask less, and it is never to read the
model's own clarification text: a model that is nervous will find something
to be nervous about. It is to let the SERVER say what is genuinely the
reader's choice, from facts it already holds before any model call
(`semantics.readiness`, the value resolution, the envelope's open questions,
the question's own words), and to refuse a clarification that rests on none
of them. No model call is added anywhere: this is arithmetic on what the run
already has.

Modes, from COCKPIT_V4_CLARIFY_GATE: `off` does nothing, `shadow` records
what it would have refused without refusing, `enforce` refuses once. The
default AND any unrecognised value is `shadow`. That differs from
`intent_check`, which fails to `enforce`: that check guards a plan that is
silently WRONG, whereas this one guards a question that is merely
unnecessary, and a wrongly refused clarification costs a reader a wrong
guess. Until the shadow findings have been measured on live traffic, an
unrecognised value must not quietly start refusing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import semantics

REFUSE = "refuse"
MODES = ("off", "shadow", "enforce")
ENV = "COCKPIT_V4_CLARIFY_GATE"
RULE = "ungrounded_clarification"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "refuse"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "detail": dict(self.detail)}


def mode() -> str:
    value = os.environ.get(ENV, "").strip().lower()
    return value if value in MODES else "shadow"


def grounds(*, question: str, readiness: dict[str, Any] | None,
            value_resolution: dict[str, Any] | None, envelope: Any,
            final: Any) -> list[str]:
    """Why a clarification is allowed here. Empty means it is not.

    Every ground is a fact the server computed, never something the analyst
    said. The last one is a precision guard rather than a ground in its own
    right: when readiness is not `sufficient` the packet cannot say what the
    question means (a follow-up, a deictic "that one"), so the server is not
    in a position to call a question back unnecessary.
    """
    # A question in another script can hold an ambiguous term the English-only
    # matchers cannot see, so the server cannot show a question was unneeded.
    if semantics.non_latin(question):
        return ["non_latin_question"]
    ready = dict(readiness or {})
    values = dict(value_resolution or {})
    found: list[str] = []
    if ready.get("terms_still_needing_a_decision"):
        found.append("undecided_term")
    if ready.get("values_still_ambiguous"):
        found.append("ambiguous_value")
    if values.get("needs_a_question"):
        found.append("value_needs_a_question")
    if getattr(envelope, "open_questions", ()):
        found.append("envelope_open_question")
    if semantics.ambiguous_methods_in(question):
        found.append("ambiguous_method")
    intent = getattr(final, "intent", None)
    if getattr(intent, "excluded_parts", ()):
        found.append("excluded_part")
    if any(getattr(c, "status", "") == "referred"
           for c in getattr(final, "coverage", ()) or ()):
        found.append("referred_part")
    if not ready.get("sufficient"):
        found.append("readiness_insufficient")
    # `sufficient` also counts measures carried over "from earlier in this
    # thread", so a deictic follow-up ("just the top five by that") is
    # sufficient without the question itself naming anything. Only a measure
    # THIS question names lets the server say it was understood in full.
    if not any(m.get("from") == "this question" for m in
               ready.get("governed_measures_already_resolved") or ()):
        found.append("no_measure_in_this_question")
    return found


def check(*, question: str, readiness: dict[str, Any] | None,
          value_resolution: dict[str, Any] | None, envelope: Any,
          final: Any) -> Finding | None:
    if grounds(question=question, readiness=readiness,
               value_resolution=value_resolution, envelope=envelope,
               final=final):
        return None
    return Finding(
        RULE, REFUSE,
        "Every term in the question maps and no method is left open, so "
        "there is nothing here that is the reader's choice to make.",
        {"question": question})
