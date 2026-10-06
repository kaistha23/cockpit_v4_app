"""
Thread continuity in the restricted domain. Specification section 12.

A recent exchange is one COMPLETE question and its final answer, referral or
clarification — not one message. At question twenty the default context is
question twenty, the stored rolling summary, and complete pairs seventeen
through nineteen.

Three defaults, and what moves between them
-------------------------------------------
Three pairs by default, normally at most five when the question needs them, an
absolute maximum of eight, and all of it under the mode's history token cap.
`select` applies both bounds and reports which one bit, because "we sent three
pairs" and "we sent three pairs because eight would not fit" are different
facts about an answer.

An explicitly referenced older exchange can displace a less relevant recent one
INSIDE the same cap — it does not raise it.

The boundary applies to memory too
----------------------------------
Section 6.4: thread history, summaries and caches obey the domain boundary. A
result created by another module, or by an earlier and broader Cockpit, does
not become authorized by appearing in an old answer. So every stored exchange
carries its domain and release, and `select` drops the ones that do not match
rather than trusting that they were fine when they were written.

A referral is remembered as a referral. Summarising it as though the analysis
had been performed would let the NEXT question be answered against a memory of
work that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import DOMAIN
from backend.cockpit_agentic.contracts import (Exchange, SummaryRepair,
                                               ThreadSummary)
from backend.cockpit_agentic.context import estimate_tokens
from backend.cockpit_agentic.ledger import Limits

DEFAULT_PAIRS = 3
EXPANDED_PAIRS = 5
HARD_CAP_PAIRS = 8


@dataclass
class Selection:
    """The bounded history for one request, and why it is that size."""

    exchanges: list[dict[str, Any]] = field(default_factory=list)
    requested_pairs: int = DEFAULT_PAIRS
    delivered_pairs: int = 0
    limited_by: str = ""            # "" | "pairs" | "tokens" | "availability"
    dropped_out_of_domain: int = 0
    estimated_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivered_pairs": self.delivered_pairs,
            "requested_pairs": self.requested_pairs,
            "limited_by": self.limited_by,
            "dropped_out_of_domain": self.dropped_out_of_domain,
            "estimated_tokens": self.estimated_tokens,
            "note": ("A recent exchange is one complete question and its final "
                     "answer, referral or clarification. The full thread stays "
                     "in authorized storage for audit and is not resent."),
        }


def authorized(exchange: Exchange, *, dataset_release_id: str,
               tenant_id: str = "") -> bool:
    """Whether a stored exchange may be reused as context.

    Refuses anything from another domain, another release or another tenant.
    Section 12: reject inaccessible, expired or cross-domain prior artifacts
    even if a summary mentions them. Section 37: identical question text from
    a different tenant is a different question.
    """
    if exchange.domain_id != DOMAIN:
        return False
    if exchange.dataset_release_id and \
            exchange.dataset_release_id != dataset_release_id:
        return False
    if tenant_id:
        stored = str(exchange.scope.get("tenant_id") or "")
        if stored and stored != str(tenant_id):
            return False
    return True


def reusable_results(exchange: Exchange,
                     scope: dict[str, Any]) -> tuple[bool, str]:
    """Whether this exchange's RESULTS may be carried into a new request.

    Separate from `authorized`, which decides whether the exchange may be
    SEEN. An exchange about last quarter's construction sector is perfectly
    good context for a question about this quarter's manufacturing -- it says
    what was discussed. Its NUMBERS are not, and section 40 is about the
    numbers.
    """
    return exchange.compatible_with(scope)


def render(exchange: Exchange, scope: dict[str, Any] | None = None
           ) -> dict[str, Any]:
    """One exchange as the context packet carries it.

    The KIND travels with it. A referral rendered without its kind reads, to
    the next turn, exactly like an answer.
    """
    prefix = {
        "referral": "[This question was REFERRED to another part of "
                    "CreditProbe and was not answered here.]",
        "clarification": "[A clarification was asked; this question is still "
                         "open.]",
        "stop": "[This question stopped without an answer.]",
    }.get(exchange.kind, "")
    body: dict[str, Any] = {
        "exchange_id": exchange.exchange_id,
        "kind": exchange.kind,
        "question": exchange.question,
        "answer": (f"{prefix} {exchange.answer}".strip() if prefix
                   else exchange.answer),
        "fact_ids": list(exchange.fact_ids),
    }
    # Section 40: an earlier result computed under a different scope may be
    # READ as context and must not be REUSED as a number. Saying which is the
    # server's job; a model told only "here is a prior result" would have no
    # way to know it did not apply.
    if scope is not None:
        ok, why = reusable_results(exchange, scope)
        body["results_reusable"] = ok
        if not ok:
            body["fact_ids"] = []
            body["results_note"] = (
                f"The figures from this earlier turn do NOT apply to the "
                f"current request: {why}. Use it for what was discussed, not "
                f"for its numbers.")
    return body


def select(history: list[Exchange], *, limits: Limits,
           dataset_release_id: str, pairs: int = DEFAULT_PAIRS,
           referenced_ids: list[str] | None = None,
           scope: dict[str, Any] | None = None) -> Selection:
    """The bounded recent history, newest last.

    `pairs` is what the caller wants; the hard cap and the token cap are what
    it gets. `referenced_ids` names older exchanges the user explicitly pointed
    at, which may displace a less relevant recent pair WITHIN the same cap.
    """
    wanted = max(1, min(int(pairs), HARD_CAP_PAIRS))
    usable = [e for e in history
              if authorized(e, dataset_release_id=dataset_release_id,
                            tenant_id=str((scope or {}).get("tenant_id") or ""))]
    dropped = len(history) - len(usable)

    chosen: list[Exchange] = []
    referenced = [e for e in usable if e.exchange_id in set(referenced_ids or ())]
    for exchange in referenced[-wanted:]:
        chosen.append(exchange)
    for exchange in reversed(usable):
        if len(chosen) >= wanted:
            break
        if exchange in chosen:
            continue
        chosen.append(exchange)
    # Back into conversation order.
    chosen = [e for e in usable if e in chosen]

    limited_by = ""
    if len(usable) > wanted:
        limited_by = "pairs"
    elif len(chosen) < wanted:
        limited_by = "availability"

    rendered = [render(e, scope) for e in chosen]
    tokens = estimate_tokens(rendered)
    while rendered and tokens > limits.recent_history_tokens:
        rendered.pop(0)
        tokens = estimate_tokens(rendered)
        limited_by = "tokens"

    return Selection(exchanges=rendered, requested_pairs=wanted,
                     delivered_pairs=len(rendered), limited_by=limited_by,
                     dropped_out_of_domain=dropped, estimated_tokens=tokens)


@dataclass
class ThreadState:
    """One Cockpit thread: its rolling summary and its complete exchanges.

    In-memory here. The persistence seam is deliberate: `backend/services/
    threads.py` already stores messages durably, and this layer is about
    SELECTION and boundary, not storage.
    """

    thread_id: str
    dataset_release_id: str
    #: Part of this thread's identity, not decoration. Section 37: a thread
    #: reached from another tenant, another release or another catalogue
    #: version is a different thread, and these are carried so that is
    #: checkable rather than assumed from the key.
    tenant_id: str = ""
    catalogue_version: str = ""
    summary: ThreadSummary | None = None
    exchanges: list[Exchange] = field(default_factory=list)
    #: What the last `context_for` had to repair, if anything. Read by the
    #: service so a recovery is reported with the answer rather than silently
    #: absorbed.
    last_repair: SummaryRepair = field(default_factory=SummaryRepair)

    def record(self, exchange: Exchange) -> None:
        self.exchanges.append(exchange)

    def context_for(self, *, limits: Limits, pairs: int = DEFAULT_PAIRS,
                    referenced_ids: list[str] | None = None,
                    scope: dict[str, Any] | None = None
                    ) -> tuple[dict[str, Any], Selection]:
        selection = select(self.exchanges, limits=limits,
                           dataset_release_id=self.dataset_release_id,
                           pairs=pairs, referenced_ids=referenced_ids,
                           scope=scope)
        # Nothing corrupt is carried forward into a request. A summary stored
        # by an earlier release may hold a character-expanded field; it is
        # detected here, rebuilt where the expansion rejoins into the original
        # statements, and otherwise ONLY that field is cleared. The exchanges
        # and their fact ids are untouched -- the stored results and the
        # evidence references are exactly what they were -- and nothing is
        # invented to fill a field that was lost.
        self.last_repair = (self.summary.repair() if self.summary
                            else SummaryRepair())
        summary = self.summary.to_dict() if self.summary else {}
        if self.summary and self.summary.unsummarized_exchange_ids:
            summary["note"] = (
                "One or more exchanges after this summary were not "
                "summarised; they are included verbatim in the recent "
                "exchanges below.")
        if self.last_repair.occurred:
            summary["recovery"] = self.last_repair.to_dict()
            if self.last_repair.cleared:
                summary["recovery_note"] = (
                    "Part of this thread's stored summary was malformed and "
                    "could not be rebuilt, so it was cleared: "
                    + ", ".join(self.last_repair.cleared)
                    + ". Treat those as unknown rather than empty, and do not "
                      "state a conclusion the recent exchanges below do not "
                      "support.")
        return summary, selection

    def apply_summary(self, summary: ThreadSummary) -> ThreadSummary:
        """Store a newer summary, refusing a stale one.

        Section 7.9: serialize active work per thread or use version checks so
        a stale summary cannot overwrite a newer one.
        """
        if self.summary and summary.version <= self.summary.version:
            return self.summary
        summary.thread_id = self.thread_id
        # Repaired on the way in as well as on the way out. `__post_init__`
        # already prevents a fresh model response from being character-expanded
        # into one; this catches a summary handed in from anywhere else.
        summary.repair()
        self.summary = summary
        return summary


__all__ = ["DEFAULT_PAIRS", "EXPANDED_PAIRS", "HARD_CAP_PAIRS", "Selection",
           "ThreadState", "authorized", "render", "reusable_results",
           "select"]
