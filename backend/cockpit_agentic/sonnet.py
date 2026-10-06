"""
The two preprocessing passes and the rolling-summary update.
Specification sections 7.2, 7.3, 7.9 and 13.

Three bounded, schema-constrained calls, and nothing else
---------------------------------------------------------
Sonnet cleans and translates (pass 1), normalizes into a business request
(pass 2), and — once, after the answer is already rendered — updates the
rolling thread summary. It never chooses a method, never scores which
functionality owns the question, never computes anything and never names a
field: it has not seen the catalogue and there is no field in any of its
contracts for it to put one in.

Failure does not rewrite intent
-------------------------------
A failed pass 1 preserves the raw question and continues with the failure
flagged, because a fluent sentence that means something else is far worse than
an untranslated one. A failed pass 2 does the same. Section 7.3 forbids a third
cleanup loop, so there is no retry here beyond the provider's own single
transient one, and the flag travels into the context packet where Opus can see
that normalization was unreliable.

The summary call is the only one that happens after the user has their answer,
and section 7.9 is explicit that its failure must not erase a completed answer.
It cannot: `update_summary` returns the previous summary unchanged and records
the exchange as unsummarized for next time.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from backend.cockpit_agentic import PROMPT_VERSION, UNTRUSTED_NOTE
from backend.cockpit_agentic.contracts import (CleanedQuestion, Exchange,
                                               NormalizedQuestion,
                                               ThreadSummary, as_text_list)
from backend.cockpit_agentic import models as models_mod
from backend.cockpit_agentic.ledger import BudgetExceeded, Ledger

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"

SONNET_ROLE = "cockpit_preprocess"
SONNET_FAMILY = "sonnet"


def prompt(name: str) -> str:
    """A versioned prompt contract, read from its file."""
    return (PROMPTS / f"{name}.md").read_text()


# ------------------------------------------------------------------ schemas

CLEANUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string",
                     "description": "ISO 639-1 code, or 'mixed'."},
        "english_text": {"type": "string",
                         "description": "The same question, spelled correctly, "
                                        "in English, meaning exactly what it "
                                        "meant before."},
        "preserved_terms": {
            "type": "array", "items": {"type": "string"},
            "description": "The numbers, units, entities, negations and "
                           "qualifiers carried through unchanged."},
        "uncertainties": {
            "type": "array", "items": {"type": "string"},
            "description": "Anything ambiguous or untranslatable. Recorded, "
                           "not resolved."},
    },
    "required": ["language", "english_text"],
}

NORMALIZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_question": {"type": "string"},
        "subquestions": {"type": "array", "items": {"type": "string"}},
        "requested_measures": {"type": "array", "items": {"type": "string"}},
        "requested_actions": {"type": "array", "items": {"type": "string"}},
        "explicit_scope": {
            "type": "object", "additionalProperties": True,
            "description": "Scope the user stated in THIS message."},
        "inherited_scope": {
            "type": "object", "additionalProperties": True,
            "description": "Scope carried from the conversation or the screen "
                           "filters. Kept apart from explicit_scope."},
        "inherited_from_exchange_ids": {"type": "array",
                                        "items": {"type": "string"}},
        "periods": {"type": "array", "items": {"type": "string"}},
        "entity_references": {"type": "array", "items": {"type": "string"}},
        "preferred_presentation": {"type": "string"},
        "response_language": {"type": "string"},
        "unresolved_ambiguity": {
            "type": "array", "items": {"type": "string"},
            "description": "Genuine ambiguity, preserved rather than resolved. "
                           "Never guess a measure convention or a period."},
    },
    "required": ["business_question", "subquestions"],
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "current_topic": {"type": "string"},
        "settled_definitions": {"type": "array", "items": {"type": "string"}},
        "corrections": {"type": "array", "items": {"type": "string"}},
        "authorized_references": {"type": "array", "items": {"type": "string"}},
        "supported_conclusions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["current_topic"],
}


# -------------------------------------------------------------------- calls

def _call(provider: Any, ledger: Ledger, *, system: str, user: str,
          schema: dict[str, Any], tool_name: str, description: str,
          purpose: str, max_tokens: int,
          finalization: bool = False) -> dict[str, Any]:
    """One reserved, settled, schema-constrained preprocessing call.

    The model id comes from `models.resolve()` and from nowhere else. The
    shared role resolver would find something to run with -- another role,
    AI_MODEL, the SDK's default -- and here that is precisely the wrong
    behaviour: a preprocessing pass served by a model nobody chose produces a
    business request nobody can attribute.
    """
    resolved = models_mod.resolve()
    model = models_mod.require(resolved.preprocess,
                               role=models_mod.PREPROCESS_ROLE)
    effort = (os.environ.get("AI_COCKPIT_PREPROCESS_EFFORT") or "").strip().lower()
    estimated = int(len(system) + len(user)) // 4 + 1
    reservation = ledger.reserve(
        role=SONNET_ROLE, family=SONNET_FAMILY, purpose=purpose,
        input_tokens=estimated, max_output_tokens=max_tokens,
        finalization=finalization)
    try:
        result = provider.structured(
            system=system, prompt=user, schema=schema, tool_name=tool_name,
            tool_description=description, max_tokens=max_tokens,
            purpose=purpose, model=model, role=SONNET_ROLE,
            effort=effort)
    except Exception as e:                                  # noqa: BLE001
        ledger.settle(reservation, output_tokens=0, error=str(e),
                      uncertain=True)
        raise
    ledger.settle(reservation, input_tokens=result.input_tokens,
                  output_tokens=result.output_tokens,
                  cache_read_tokens=result.cache_read_tokens,
                  cache_write_tokens=result.cache_write_tokens)
    return result.data


def clean(question: str, provider: Any, ledger: Ledger, *,
          glossary: dict[str, str] | None = None,
          recent_names: list[str] | None = None) -> CleanedQuestion:
    """Pass 1. Faithful cleanup and translation, nothing else.

    Deliberately given very little: the question, a minimal glossary and just
    enough recent context to keep a name or a pronoun resolvable. Section 7.2
    forbids sending the full catalogue or irrelevant history here, and there is
    nothing in a translation task that the schema could help with.
    """
    text = str(question or "").strip()
    parts = [f"QUESTION AS TYPED:\n{text}"]
    if glossary:
        parts.append("GLOSSARY (terms this application uses):\n" + "\n".join(
            f"- {k}: {v}" for k, v in sorted(glossary.items())))
    if recent_names:
        parts.append(
            "NAMES MENTIONED RECENTLY (so a pronoun or short reference stays "
            "resolvable; do NOT introduce any of these into the translation "
            "unless the user referred to them):\n"
            + "\n".join(f"- {n}" for n in recent_names[:20]))
    parts.append(UNTRUSTED_NOTE)

    try:
        data = _call(provider, ledger, system=prompt("sonnet_pass1_cleanup"),
                     user="\n\n".join(parts), schema=CLEANUP_SCHEMA,
                     tool_name="cleaned_question",
                     description="The question, spelled correctly, in English, "
                                 "meaning exactly what it meant before.",
                     purpose="sonnet_pass1", max_tokens=800)
    except BudgetExceeded:
        raise
    except Exception as e:                                  # noqa: BLE001
        logger.warning("Cockpit pass 1 did not complete: %s", e)
        return CleanedQuestion(
            original_text=text, detected_language="unknown", english_text="",
            failed=True,
            failure_reason=f"the cleanup pass did not complete: {e}")

    return CleanedQuestion(
        original_text=text,
        detected_language=str(data.get("language") or "unknown"),
        english_text=str(data.get("english_text") or ""),
        preserved_terms=as_text_list(data.get("preserved_terms")),
        uncertainties=as_text_list(data.get("uncertainties")))


def normalize(cleaned: CleanedQuestion, provider: Any, ledger: Ledger, *,
              ui_filters: dict[str, Any] | None = None,
              rolling_summary: dict[str, Any] | None = None,
              recent_exchanges: list[dict[str, Any]] | None = None
              ) -> NormalizedQuestion:
    """Pass 2. The business request, with genuine ambiguity preserved."""
    parts = [
        f"ORIGINAL QUESTION AS TYPED:\n{cleaned.original_text}",
        f"CLEANED ENGLISH (from pass 1):\n{cleaned.english_text}",
    ]
    if cleaned.uncertainties:
        parts.append("PASS 1 WAS UNSURE ABOUT:\n" + "\n".join(
            f"- {u}" for u in cleaned.uncertainties))
    if cleaned.failed:
        parts.append(
            "NOTE: the cleanup pass did not complete, so the English above is "
            "the raw question. Read it literally and record anything you "
            "cannot resolve rather than smoothing it over.")
    if ui_filters:
        parts.append(
            "FILTERS CURRENTLY SELECTED ON THE USER'S SCREEN (these are "
            "INHERITED scope, not something the user said in this message):\n"
            + "\n".join(f"- {k}: {v}" for k, v in sorted(ui_filters.items())))
    if rolling_summary:
        # Serialized, not interpolated. `f"{a_dict}"` renders Python repr,
        # which is neither what the model is told to expect nor bounded; a
        # summary field that had been character-expanded rendered as thousands
        # of quoted single characters here.
        parts.append("CONVERSATION SO FAR (rolling summary):\n"
                     + json.dumps(rolling_summary, separators=(",", ":"),
                                  default=str))
    if recent_exchanges:
        rendered = "\n\n".join(
            f"[{e.get('exchange_id', '?')}] Q: {e.get('question', '')}\n"
            f"A: {str(e.get('answer', ''))[:600]}"
            for e in recent_exchanges[-3:])
        parts.append(f"RECENT COMPLETE EXCHANGES:\n{rendered}")
    parts.append(UNTRUSTED_NOTE)

    try:
        data = _call(provider, ledger,
                     system=prompt("sonnet_pass2_normalize"),
                     user="\n\n".join(parts), schema=NORMALIZE_SCHEMA,
                     tool_name="normalized_request",
                     description="The business request, its subquestions, its "
                                 "scope split into explicit and inherited, and "
                                 "the ambiguity that remains genuine.",
                     purpose="sonnet_pass2", max_tokens=1200)
    except BudgetExceeded:
        raise
    except Exception as e:                                  # noqa: BLE001
        logger.warning("Cockpit pass 2 did not complete: %s", e)
        # Section 7.3: a failed normalization must not silently rewrite the
        # user's intent. The cleaned text stands as the request, flagged.
        return NormalizedQuestion(
            business_question=cleaned.english_text or cleaned.original_text,
            subquestions=[cleaned.english_text or cleaned.original_text],
            inherited_scope=dict(ui_filters or {}),
            unresolved_ambiguity=list(cleaned.uncertainties),
            failed=True,
            failure_reason=f"the normalization pass did not complete: {e}")

    return NormalizedQuestion(
        business_question=str(data.get("business_question") or
                              cleaned.english_text),
        subquestions=as_text_list(data.get("subquestions")),
        requested_measures=as_text_list(data.get("requested_measures")),
        requested_actions=as_text_list(data.get("requested_actions")),
        explicit_scope=dict(data.get("explicit_scope") or {}),
        # The screen's filters are inherited scope whatever the model says, so
        # they are merged in here rather than trusted from the response.
        inherited_scope={**dict(ui_filters or {}),
                         **dict(data.get("inherited_scope") or {})},
        inherited_from_exchange_ids=as_text_list(data.get("inherited_from_exchange_ids")),
        periods=as_text_list(data.get("periods")),
        entity_references=as_text_list(data.get("entity_references")),
        preferred_presentation=str(data.get("preferred_presentation") or ""),
        response_language=str(data.get("response_language") or "en"),
        unresolved_ambiguity=(
            as_text_list(data.get("unresolved_ambiguity"))
            + list(cleaned.uncertainties)))


def update_summary(previous: ThreadSummary | None, exchange: Exchange,
                   provider: Any, ledger: Ledger) -> ThreadSummary:
    """The one bounded summary call, after the answer is already rendered.

    Section 7.9: a failed or expired summary call does not erase a completed
    answer. The previous summary is retained and the exchange is marked
    unsummarized so it is included verbatim next time.
    """
    fallback = previous or ThreadSummary(
        thread_id="", summary_through_exchange_id="")

    def kept(reason: str) -> ThreadSummary:
        unsummarized = list(fallback.unsummarized_exchange_ids)
        if exchange.exchange_id not in unsummarized:
            unsummarized.append(exchange.exchange_id)
        logger.info("The Cockpit summary was not updated (%s); the previous "
                    "summary stands.", reason)
        return ThreadSummary(
            thread_id=fallback.thread_id,
            summary_through_exchange_id=fallback.summary_through_exchange_id,
            current_topic=fallback.current_topic,
            settled_definitions=list(fallback.settled_definitions),
            corrections=list(fallback.corrections),
            authorized_references=list(fallback.authorized_references),
            supported_conclusions=list(fallback.supported_conclusions),
            unresolved_questions=list(fallback.unresolved_questions),
            version=fallback.version,
            unsummarized_exchange_ids=unsummarized)

    kind_note = {
        "referral": ("This exchange was a REFERRAL: the question belonged to "
                     "another part of the application and was NOT answered "
                     "here. Summarise it as a referral. It must not read as "
                     "though the analysis was performed."),
        "clarification": ("This exchange was a CLARIFICATION: the user was "
                          "asked something and the question is still open."),
        "stop": ("This exchange STOPPED without an answer. Record what was "
                 "asked and that it was not answered, and why."),
    }.get(exchange.kind, "This exchange was answered.")

    # The previous summary goes back to the model, so it is repaired FIRST. A
    # character-expanded field echoed into the next request is how one bad
    # response becomes a permanently corrupt thread -- and, because the
    # expansion is one element per character, how the summary section grows by
    # a factor of thirty every time it is carried forward.
    repair = fallback.repair()
    if repair.occurred:
        logger.warning(
            "The stored Cockpit summary was repaired before use: recovered=%s "
            "cleared=%s", repair.recovered, repair.cleared)

    user = "\n\n".join([
        "PREVIOUS SUMMARY:\n" + (
            json.dumps(previous.to_dict(), separators=(",", ":"), default=str)
            if previous
            else "(none - this is the first exchange in this thread)"),
        f"LATEST QUESTION:\n{exchange.question}",
        f"WHAT THE USER WAS GIVEN:\n{exchange.answer[:3000]}",
        kind_note,
        (f"AUTHORIZED RESULT REFERENCES: "
         f"{', '.join(exchange.fact_ids[:20]) or '(none)'}"),
        UNTRUSTED_NOTE,
    ])

    try:
        data = _call(provider, ledger, system=prompt("sonnet_summary"),
                     user=user, schema=SUMMARY_SCHEMA,
                     tool_name="thread_summary",
                     description="The updated rolling summary of this "
                                 "conversation.",
                     purpose="sonnet_summary", max_tokens=1000,
                     finalization=True)
    except BudgetExceeded as e:
        return kept(f"no budget remained: {e.reason}")
    except Exception as e:                                  # noqa: BLE001
        return kept(str(e))

    references = as_text_list(data.get("authorized_references"))
    # `as_text_list` is the whole of the fix for the corruption that started
    # here. `[str(r) for r in data.get("authorized_references")]` iterates a
    # STRING one character at a time when the model returns a string where the
    # schema asked for an array, and the result is stored, echoed back and
    # re-summarised forever. A scalar string is now one element; nothing is
    # ever character-expanded.
    # Section 7.9: CreditProbe validates that referenced results actually
    # exist. A summary that cites a fact id from nowhere is not stored with it.
    known = set(exchange.fact_ids) | set(fallback.authorized_references)
    validated = [r for r in references if r in known]

    return ThreadSummary(
        thread_id=fallback.thread_id or "",
        summary_through_exchange_id=exchange.exchange_id,
        current_topic=str(data.get("current_topic") or ""),
        settled_definitions=as_text_list(data.get("settled_definitions")),
        corrections=as_text_list(data.get("corrections")),
        authorized_references=validated,
        supported_conclusions=as_text_list(data.get("supported_conclusions")),
        unresolved_questions=as_text_list(data.get("unresolved_questions")),
        version=fallback.version + 1,
        unsummarized_exchange_ids=[])


__all__ = ["CLEANUP_SCHEMA", "NORMALIZE_SCHEMA", "PROMPTS", "PROMPT_VERSION",
           "SONNET_ROLE", "SUMMARY_SCHEMA", "clean", "normalize", "prompt",
           "update_summary"]
