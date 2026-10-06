"""
What this run IS, decided once by the server and carried.

The defect this exists for
--------------------------
`intent` was a required nested object on all five tools. Nine fields, retyped
on every catalogue read, every artifact read, every execution and the answer.
It does not change between them. A live Mac run against real Opus sent it as
a STRING and the reader got `intent must be an object` -- twice, in two
separate rounds, because each round made the parser more forgiving instead of
removing the restatement.

The restatement was never necessary. By the time the first provider call is
made, CreditProbe already knows:

  * WHICH BOOK this run reads -- the route resolved it from the thread and
    pinned it on the run record;
  * WHICH RELEASE, and the exact bytes of it;
  * WHAT KIND OF TURN this is -- `envelope.classify` names the policy family
    before anything is spent;
  * WHAT PERIOD the question means -- the book's own calendar says;
  * WHICH CATEGORY VALUES the question named -- `values.phrases` resolves
    them against the values the release actually holds.

None of that is the model's to decide, and three of the five were never
allowed to be. So the run opens with an envelope holding them, the analyst is
never asked to restate them, and the only part it still authors -- what it
understood the question to mean, what it assumed, what it mapped, what it
left out -- is stated FLAT on `finalize_response`, where it belongs, because
that part is answer content.

What an envelope is not
-----------------------
It is not a guess at the answer. It records what is already known and nothing
else: no measure is chosen here, no filter is applied, no aggregation is
decided. Those are the analysis, and the analysis is the analyst's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import contracts as contracts_mod


@dataclass(frozen=True)
class PeriodSemantics:
    """What a period IS in this run's book, and which one is current."""

    column: str
    noun: str
    frequency: str
    latest: str
    previous: str
    year_ago: str
    populated: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"column": self.column, "noun": self.noun,
                "frequency": self.frequency, "latest": self.latest,
                "previous": self.previous, "year_ago": self.year_ago,
                "count": len(self.populated)}


@dataclass(frozen=True)
class IntentEnvelope:
    """One run's settled intent. Server-authored; refined once by the answer."""

    intent_id: str
    #: Server decisions. A model cannot talk a Retail run into Corporate or a
    #: data analysis into product help by restating a field.
    domain_id: str
    domain_label: str
    release_id: str
    release_fingerprint: str
    query_mode: str
    owner: str
    budget_family: str
    period: PeriodSemantics
    #: What the question's own words were resolved to against the values this
    #: release holds. See `values.resolve`.
    resolved_entities: tuple[dict[str, Any], ...] = ()
    #: A phrase that names more than one real value. Asked, never guessed.
    open_questions: tuple[dict[str, Any], ...] = ()
    #: The reader-facing half, authored by the answer.
    understood_request: str = ""
    response_language: str = "en"
    blocking_ambiguities: tuple[str, ...] = ()
    resolved_assumptions: tuple[str, ...] = ()
    canonical_mappings: tuple[str, ...] = ()
    excluded_parts: tuple[str, ...] = ()
    public_rationale: str = ""
    seeded_from: dict[str, Any] = field(default_factory=dict)

    @property
    def may_execute(self) -> bool:
        """Execution is allowed for a Cockpit data analysis with no BLOCKING
        ambiguity. A resolution is not an ambiguity."""
        return (self.query_mode == contracts_mod.DATA_ANALYSIS
                and self.owner == contracts_mod.COCKPIT
                and not self.blocking_ambiguities)

    def as_intent(self) -> contracts_mod.Intent:
        """The shape the contracts layer and the published answer read."""
        return contracts_mod.Intent(
            query_mode=self.query_mode, owner=self.owner,
            understood_request=self.understood_request,
            response_language=self.response_language,
            blocking_ambiguities=self.blocking_ambiguities,
            resolved_assumptions=self.resolved_assumptions,
            canonical_mappings=self.canonical_mappings,
            excluded_parts=self.excluded_parts,
            public_rationale=self.public_rationale)

    def escalate_to_analysis(self) -> "IntentEnvelope":
        """The envelope after the run asked to execute analysis.

        The budget classifier is a BUDGET decision made before anything was
        spent, from the question's words alone -- and a question it reads as
        product help can still turn out to be analytical. Submitting SQL IS
        the declaration, so the mode widens here rather than the run being
        refused for a classification nobody asked the reader about.

        It only ever widens. A run cannot talk itself DOWN into product help
        after executing against the book, because that would move an
        analytical turn onto the cheaper clock after the fact.
        """
        from dataclasses import replace

        if self.query_mode == contracts_mod.DATA_ANALYSIS:
            return self
        return replace(self, query_mode=contracts_mod.DATA_ANALYSIS)

    def with_intent(self, intent: contracts_mod.Intent) -> "IntentEnvelope":
        """The envelope after the answer stated its reading of the question.

        The server's half is NOT overwritten: the domain, the release and the
        budget family are facts about the run, and an answer restating them
        differently is an answer describing a run that did not happen.
        """
        from dataclasses import replace

        return replace(
            self,
            understood_request=(intent.understood_request
                                or self.understood_request),
            response_language=(intent.response_language
                               or self.response_language),
            blocking_ambiguities=tuple(intent.blocking_ambiguities),
            resolved_assumptions=tuple(
                dict.fromkeys(self.resolved_assumptions
                              + tuple(intent.resolved_assumptions))),
            canonical_mappings=tuple(
                dict.fromkeys(self.canonical_mappings
                              + tuple(intent.canonical_mappings))),
            excluded_parts=tuple(intent.excluded_parts),
            public_rationale=(intent.public_rationale
                              or self.public_rationale))

    def to_dict(self) -> dict[str, Any]:
        body = {
            "intent_id": self.intent_id,
            "domain_id": self.domain_id,
            "domain_label": self.domain_label,
            "release_id": self.release_id,
            "release_fingerprint": self.release_fingerprint,
            "query_mode": self.query_mode,
            "owner": self.owner,
            "budget_family": self.budget_family,
            "period": self.period.to_dict(),
            "resolved_entities": [dict(e) for e in self.resolved_entities],
            "open_questions": [dict(q) for q in self.open_questions],
            "understood_request": self.understood_request,
            "response_language": self.response_language,
            "blocking_ambiguities": list(self.blocking_ambiguities),
            "resolved_assumptions": list(self.resolved_assumptions),
            "canonical_mappings": list(self.canonical_mappings),
            "excluded_parts": list(self.excluded_parts),
            "public_rationale": self.public_rationale,
        }
        if self.seeded_from:
            body["seeded_from"] = dict(self.seeded_from)
        return body

    def for_packet(self) -> dict[str, Any]:
        """What the analyst is TOLD it already has.

        Deliberately not the whole envelope: the intent id and the
        fingerprint are bookkeeping, and a packet that spends tokens on
        bookkeeping has fewer for the question.
        """
        body = {
            "book": self.domain_label,
            "release": self.release_id,
            "turn_is": self.query_mode,
            "period": self.period.to_dict(),
            "note": (
                "This is SETTLED. The book, the release, the kind of turn "
                "this is and what a period means here were decided before "
                "you were called, and no tool asks you to restate any of "
                "them. State what you UNDERSTOOD, what you ASSUMED and what "
                "you MAPPED on finalize_response, flat, once, at the end."),
        }
        if self.resolved_entities:
            body["already_resolved"] = [
                {"term": e.get("term", ""), "field": e.get("field", ""),
                 "value": e.get("value", ""), "say": e.get("say", "")}
                for e in self.resolved_entities]
        if self.open_questions:
            body["needs_a_question"] = [
                {"term": q.get("term", ""), "ask": q.get("question", "")}
                for q in self.open_questions]
        if self.seeded_from:
            body["opened_from"] = dict(self.seeded_from)
        return body


def open_envelope(*, domain_id: str, domain_label: str, release_id: str,
                  release_fingerprint: str, query_mode: str, owner: str,
                  budget_family: str, period: PeriodSemantics,
                  resolved_entities: tuple[dict[str, Any], ...] = (),
                  open_questions: tuple[dict[str, Any], ...] = (),
                  seeded_from: dict[str, Any] | None = None
                  ) -> IntentEnvelope:
    """Open a run's envelope. Called once, before the first provider call."""
    return IntentEnvelope(
        intent_id=f"int-{uuid.uuid4().hex[:12]}",
        domain_id=domain_id, domain_label=domain_label,
        release_id=release_id, release_fingerprint=release_fingerprint,
        query_mode=query_mode, owner=owner, budget_family=budget_family,
        period=period, resolved_entities=resolved_entities,
        open_questions=open_questions, seeded_from=dict(seeded_from or {}))


def period_of(catalog: Any, *, domain_id: str = "") -> PeriodSemantics:
    """What a period means in this book, read from the book itself.

    The CATALOGUE is the source, not the scope, because there are two scope
    shapes in this build -- the edge's `DomainScope` and the runtime's
    `ReadScope` -- and only the catalogue is common to both. Populated slots
    rather than published ones: a calendar slot carrying no rows is not a
    period an answer may report.
    """
    from backend.cockpit_v4 import schema as schema_mod
    from backend.cockpit_v4 import semantics as sem

    domain_id = domain_id or str(getattr(catalog, "domain_id", ""))
    calendar = getattr(catalog, "calendar", None)
    populated = tuple(getattr(calendar, "populated", ()) or ())
    if not populated:
        populated = tuple(getattr(calendar, "slots", ()) or ())
    noun = sem.period_noun(catalog)
    step = 4 if noun == "quarter" else 12
    return PeriodSemantics(
        column=schema_mod.period_column(domain_id) if domain_id else "",
        noun=noun, frequency=sem.frequency(catalog),
        latest=populated[-1] if populated else "",
        previous=populated[-2] if len(populated) > 1 else "",
        year_ago=populated[-(step + 1)] if len(populated) > step else "",
        populated=populated)


def _scope_facts(scope: Any) -> tuple[str, str, str]:
    """`(domain_id, release_id, release_fingerprint)` from either scope.

    `DomainScope` spells the release `release_id` and `ReadScope` spells it
    `dataset_release_id`. They are the same fact; reading only one of them
    is how an envelope ends up naming an empty release.
    """
    domain_id = str(getattr(scope, "domain_id", ""))
    release_id = str(getattr(scope, "release_id", "")
                     or getattr(scope, "dataset_release_id", ""))
    fingerprint = str(getattr(scope, "release_fingerprint", ""))
    return domain_id, release_id, fingerprint


def for_run(*, scope: Any, catalog: Any, verdict: Any = None,
            value_resolution: dict[str, Any] | None = None,
            seeded: dict[str, Any] | None = None,
            query_mode: str = "", owner: str = "") -> IntentEnvelope:
    """The envelope for one accepted run, before any provider call.

    Everything here is a fact the server already holds. `verdict` is
    `envelope.classify`'s answer -- the policy family and whether this turn
    is analytical -- and it decides the query mode, because a model that
    could restate the mode could talk a product question into an analytical
    budget.
    """
    from backend.cockpit_v4 import domains as dom

    domain_id, release_id, fingerprint = _scope_facts(scope)
    resolution = dict(value_resolution or {})
    analytical = bool(getattr(verdict, "analytical", True))
    seeded_from: dict[str, Any] = {}
    if seeded:
        seeded_from = {
            "item_id": str(seeded.get("item_id", "")),
            "headline": str(seeded.get("headline", "")),
            "segment": str(seeded.get("segment_label")
                           or seeded.get("segment", "")),
            "metric": str(seeded.get("metric_label")
                          or seeded.get("metric", "")),
        }
    return open_envelope(
        domain_id=domain_id,
        domain_label=dom.LABELS.get(domain_id, domain_id),
        release_id=release_id, release_fingerprint=fingerprint,
        query_mode=(query_mode or (contracts_mod.DATA_ANALYSIS if analytical
                                   else contracts_mod.PRODUCT_HELP)),
        owner=owner or contracts_mod.COCKPIT,
        budget_family=str(getattr(verdict, "family", "") or ""),
        period=period_of(catalog, domain_id=domain_id),
        resolved_entities=tuple(resolution.get("recognised") or ()),
        open_questions=tuple(resolution.get("needs_a_question") or ()),
        seeded_from=seeded_from)


__all__ = ["IntentEnvelope", "PeriodSemantics", "for_run", "open_envelope",
           "period_of"]
