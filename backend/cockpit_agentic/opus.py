"""
Opus: ownership, planning, authorship, repair and review.
Specification sections 6, 7.5, 7.8, 8 and 13.

One conversation, not four calls
--------------------------------
The gate, the plan, every repair and the review are turns of ONE conversation.
That is not an optimisation: section 8.1 requires every repair continuation to
carry the full effective context, and threading the messages is how the
catalogue, the approved ownership decision and the plan stay present without
being re-serialized into each error. The assistant's own blocks go back
verbatim, in order, because that is what the provider requires and what makes
the continuation the same conversation rather than a new one.

Section 6.2 permits the first response to carry both the functionality decision
and a candidate plan, so long as the gate is logically first and CreditProbe
approves before anything executes. It does, and it is: `runtime` checks
`decision.may_execute` and issues a server-side permission before a single
statement reaches the engine, and a referral response is required to carry no
executable plan at all.

What is not here
----------------
No method vocabulary, no template, no canned analysis. The schemas describe the
SHAPE of a plan and a submission — subquestions, fields, steps, code — and say
nothing about what the analysis should be. `AnalysisPlan.method_summary` is
free text. That is the difference between a contract and the template this
architecture removes.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from backend.cockpit_agentic import UNTRUSTED_NOTE
from backend.cockpit_agentic import contracts as K
from backend.cockpit_agentic import models as models_mod
from backend.cockpit_agentic import tokens as tokens_mod
from backend.cockpit_agentic.context import CockpitContextPacket
from backend.cockpit_agentic.contracts import as_text_list
from backend.cockpit_agentic.ledger import Ledger
from backend.cockpit_agentic.sonnet import prompt

logger = logging.getLogger(__name__)

OPUS_ROLE = "cockpit_reasoning"
OPUS_FAMILY = "opus"


class OpusUnavailable(RuntimeError):
    """No provider, or the provider cannot hold a conversation.

    Raised and reported. There is no deterministic substitute for a
    model-authored analysis, and section 17 forbids inventing one.
    """


class OutputTruncated(OpusUnavailable):
    """The reply reached the output allowance and stopped mid-answer.

    Its own type because the remedy is its own: the provider did exactly what
    it was told, nothing is wrong with the request, and a caller that can ask
    for a SHORTER answer should be able to do that rather than reporting a
    provider failure. Callers that cannot still get an `OpusUnavailable`.

    What it never means is "a shorter answer arrived". A truncated plan is half
    a plan, and half a SELECT is not something this application may run.
    """

    def __init__(self, message: str, *, purpose: str, limit: int) -> None:
        super().__init__(message)
        self.purpose = purpose
        self.limit = limit


class PlanTruncated(OpusUnavailable):
    """The planning reply was truncated, and so was the one permitted compact
    regeneration. The request stops; this application does not write the plan
    the model could not finish."""

    def __init__(self, message: str, *, attempts: int, limit: int) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.limit = limit
        self.status = K.PLAN_OUTPUT_TRUNCATED


# ------------------------------------------------------------------ schemas

_SCORE = {
    "type": "object",
    "properties": {
        "functionality_id": {"type": "string"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "justification": {"type": "string"},
    },
    "required": ["functionality_id", "score", "justification"],
}

_ALTERNATIVE = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "required_fields": {"type": "array", "items": {"type": "string"},
                            "minItems": 1},
        "available_periods": {"type": "array", "items": {"type": "string"}},
        "limitation": {"type": "string"},
    },
    "required": ["question", "required_fields"],
}

#: The plan object, BOUNDED. Every limit here is an orchestration control
#: from `contracts.PlanBounds` -- stated in the schema so the model is told,
#: and enforced in `contracts.bound_plan` so a model that ignores the schema
#: still produces something the runtime can hold.
#:
#: What the bounds are for: a planning turn writes executable instructions,
#: not a credit memo. Live UAT put one aggregation and one ranking into a
#: 4,096-token planning reply the provider cut off mid-answer, because this
#: contract asked for narrative reasoning and per-field justification at the
#: moment the model should have been writing SQL.
_PLAN = {
    "type": "object",
    "description": "The execution plan. Be concise: this is a plan, not the "
                   "final answer. Reference fields by canonical name only -- "
                   "the dictionary is in front of you and copying its "
                   "definitions back out wastes the allowance you need for "
                   "the SQL.",
    "properties": {
        "plan_id": {"type": "string", "maxLength": 40},
        "subquestions": {
            "type": "array", "maxItems": K.PLAN_BOUNDS.subquestions,
            "items": {"type": "string",
                      "maxLength": K.PLAN_BOUNDS.subquestion_chars}},
        "fields_required": {
            "type": "array", "maxItems": K.PLAN_BOUNDS.fields_required,
            "items": {"type": "string",
                      "maxLength": K.PLAN_BOUNDS.field_name_chars,
                      "description": "Canonical name only, e.g. "
                                     "\"ead_reported\". No description, no "
                                     "unit, no explanation."}},
        "joins_required": {
            "type": "array", "maxItems": K.PLAN_BOUNDS.joins_required,
            "items": {"type": "string"},
            "description": "Only if a join is actually needed."},
        "steps": {
            "type": "array", "maxItems": K.PLAN_BOUNDS.steps,
            "items": {"type": "string",
                      "maxLength": K.PLAN_BOUNDS.plan_step_chars},
            "description": "One short line per step. Not prose."},
        "assumptions": {
            "type": "array", "maxItems": K.PLAN_BOUNDS.assumptions,
            "items": {"type": "string",
                      "maxLength": K.PLAN_BOUNDS.assumption_chars},
            "description": "Only material ones."},
        "missingness_handling": {
            "type": "string",
            "maxLength": K.PLAN_BOUNDS.missingness_handling_chars,
            "description": "Only if it materially affects the result."},
        "expected_output_grain": {"type": "string", "maxLength": 200},
        "expected_units": {"type": "string", "maxLength": 80},
        # Free text on purpose, and bounded. There is no method vocabulary and
        # no enum: constraining WHAT the method may be would be the template
        # this architecture exists to remove. Constraining how many characters
        # it is described in does not touch the method.
        "method_summary": {
            "type": "string", "maxLength": K.PLAN_BOUNDS.method_summary_chars,
            "description": "What you are doing and why, in a few sentences. "
                           "Not a justification of every field."},
        "alternative_method": {
            "type": "string",
            "maxLength": K.PLAN_BOUNDS.alternative_method_chars,
            "description": "Optional. Only if the inputs may turn out to be "
                           "insufficient."},
    },
    "required": ["plan_id", "subquestions", "method_summary"],
}

_STEP = {
    "type": "object",
    "properties": {
        "step_id": {"type": "string", "maxLength": 40},
        "language": {"type": "string", "enum": ["sql"]},
        # NOT bounded, deliberately. A SELECT is exactly as long as
        # correctness requires; a query trimmed to fit an allowance is a query
        # that returns the wrong thing.
        "code": {"type": "string",
                 "description": "Exactly one SELECT statement."},
        "purpose": {"type": "string",
                    "maxLength": K.PLAN_BOUNDS.step_purpose_chars,
                    "description": "One line. What this step is for."},
    },
    "required": ["step_id", "language", "code"],
}

_TABLE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array"}},
        "units": {"type": "object", "additionalProperties": {"type": "string"}},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["title", "columns", "rows"],
}

_CHART = {
    "type": "object",
    "properties": {
        "kind": {"type": "string",
                 "enum": ["bar", "line", "waterfall", "scatter"]},
        "title": {"type": "string"},
        "series": {"type": "array", "items": {"type": "object",
                                              "additionalProperties": True}},
        "x_label": {"type": "string"},
        "y_label": {"type": "string"},
        "unit": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "title"],
}

_ANSWER = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "complete": {"type": "boolean"},
        "approximate": {"type": "boolean"},
        "tables": {"type": "array", "items": _TABLE},
        "charts": {"type": "array", "items": _CHART},
        "findings": {
            "type": "array", "items": {"type": "string"},
            "description": "What the analysis found, one statement each. "
                           "Every figure in these must come from an executed "
                           "result."},
        "suggested_questions": {
            "type": "array", "items": {"type": "string"}, "maxItems": 4,
            "description": "Cockpit questions the user could ask next. They "
                           "are checked against the actual field catalogue "
                           "and the available quarters, and any that names "
                           "something this domain does not have is dropped "
                           "without being repaired."},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "hypotheses": {
            "type": "array", "items": {"type": "string"},
            "description": "Claims the evidence supports only as "
                           "association. Never stated as cause."},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["narrative"],
}

#: The answer-only rewrite, section 29. Deliberately carries no plan and no
#: steps: this turn cannot execute, so a schema that let it propose code would
#: be offering something the runtime will refuse.
REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": _ANSWER,
                   "what_was_wrong": {"type": "string"}},
    "required": ["answer"],
}

GATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {"type": "array", "items": _SCORE, "minItems": 6},
        "best_fit": {"type": "string"},
        "requested_actions": {"type": "array", "items": {"type": "string"}},
        "relevant_exclusions": {"type": "array", "items": {"type": "string"}},
        "decision": {"type": "string",
                     "enum": list(K.DECISIONS)},
        "query_mode": {
            "type": "string", "enum": list(K.QUERY_MODES),
            "description": (
                "What KIND of request this is, decided fresh for this turn. "
                "PRODUCT_HELP: about CreditProbe itself. THEORY_CONCEPT: what "
                "a credit or accounting term means, with no reference to this "
                "book's numbers. DATA_ANALYSIS: needs the stored portfolio -- "
                "including a question that asks for BOTH an explanation and "
                "the numbers. OTHER_FUNCTIONALITY: another module owns it. "
                "CLARIFICATION_REQUIRED: it cannot be answered without a "
                "choice only the user can make. UNSUPPORTED: nothing here "
                "owns it, including anything about current external events, "
                "which must never be answered from memory.")},
        "owner": {"type": "string", "enum": list(K.OWNERS)},
        "requires_cockpit_data": {"type": "boolean"},
        "requires_sql": {"type": "boolean"},
        "requires_python": {"type": "boolean"},
        "decision_reason": {"type": "string"},
        "ambiguity": {"type": "string"},
        "answer": _ANSWER,
        "mixed_scope": {"type": "boolean"},
        "mixed_scope_explanation": {"type": "string"},
        "public_explanation": {"type": "string"},
        "referral_destination": {"type": "string"},
        "referral_reason": {"type": "string"},
        "alternatives": {"type": "array", "items": _ALTERNATIVE,
                         "maxItems": 3},
        "clarification_question": {"type": "string"},
        "clarification_options": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scores", "decision", "public_explanation"],
}

#: Stage B's first turn. Carries the plan and the first step, and NO gate
#: fields: ownership was decided in stage A and is not reopened here. A schema
#: that let this turn re-score the registry would be inviting the model to
#: overturn a decision the server has already acted on.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["submit_the_first_step", "ask_a_targeted_clarification",
                     "explain_and_stop"],
            "description": (
                "submit_the_first_step: the dictionary supports the analysis "
                "and you are supplying the plan and its first SELECT. "
                "ask_a_targeted_clarification: the dictionary shows the "
                "question needs a choice only the user can make. "
                "explain_and_stop: the dictionary shows this data cannot "
                "answer it, and you are saying why rather than approximating "
                "it.")},
        "plan": _PLAN,
        "steps": {"type": "array", "items": _STEP},
        "clarification_question": {"type": "string"},
        "clarification_options": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["action"],
}

REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["submit_repaired_code", "revise_the_analysis_plan",
                            "ask_a_targeted_clarification",
                            "explain_and_stop"]},
        "what_went_wrong": {"type": "string"},
        "plan": _PLAN,
        "steps": {"type": "array", "items": _STEP},
        "clarification_question": {"type": "string"},
        "clarification_options": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["action"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(K.REVIEW_DECISIONS)},
        "per_subquestion": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subquestion": {"type": "string"},
                    "answered": {"type": "boolean"},
                    "evidence_fact_ids": {"type": "array",
                                          "items": {"type": "string"}},
                    "gap": {"type": "string"},
                },
                "required": ["subquestion", "answered"],
            }},
        "gap_addressed": {"type": "string"},
        "plan": _PLAN,
        "steps": {"type": "array", "items": _STEP},
        "clarification_question": {"type": "string"},
        "clarification_options": {"type": "array", "items": {"type": "string"}},
        "answer": _ANSWER,
    },
    "required": ["decision"],
}


# ----------------------------------------------------------- the conversation

def system_blocks(packet: CockpitContextPacket,
                  contract: str) -> list[dict[str, Any]]:
    """The system blocks for one turn, stable prefix first, ONE breakpoint.

    Module-level rather than a method so that the size probe and the real
    dispatch cannot drift apart: whatever this returns is what goes out, and
    whatever this returns is what was measured.
    """
    return [
        # 1. Invariant for every request in this deployment.
        {"type": "text", "text": prompt("shared_preamble")},
        # 2. Invariant for every TURN of this STAGE: the release is pinned for
        #    the request's lifetime, and the packet says what belongs here --
        #    the domain outline for the gate, the complete dictionary for the
        #    analysis. The two stages are two conversations, so neither prefix
        #    ever changes under itself.
        {"type": "text",
         "text": (packet.pinned_heading() + "\n\n"
                  + json.dumps(packet.pinned(), separators=(",", ":"),
                               default=str)),
         # 3. The one breakpoint. Everything above is byte-identical across the
         #    turns of this stage; everything below moves.
         "cache_control": {"type": "ephemeral"}},
        # 4. The contract for THIS turn -- planning, repair or review. It
        #    changes between turns, so it must sit AFTER the breakpoint:
        #    putting it first made every turn a cache miss, which is what
        #    test_the_prefix_is_byte_identical_across_every_turn caught.
        {"type": "text", "text": prompt(contract)},
        {"type": "text", "text": UNTRUSTED_NOTE},
    ]


def opening_message(packet: CockpitContextPacket) -> str:
    """The dynamic half of the context, as the first user message."""
    return (packet.dynamic_heading() + "\n\n"
            + json.dumps(packet.dynamic(), separators=(",", ":"), default=str))


class Conversation:
    """One Opus conversation for one user request.

    Holds the message list and threads the assistant's own blocks back into it
    verbatim. Nothing here interprets a block: the provider gave it, the
    provider gets it back.
    """

    def __init__(self, *, provider: Any, ledger: Ledger,
                 packet: CockpitContextPacket, model: str = "") -> None:
        if provider is None or not hasattr(provider, "converse"):
            raise OpusUnavailable(
                "This runtime has no provider that can hold a multi-turn "
                "conversation, so the Cockpit cannot plan, author or repair an "
                "analysis. This is reported rather than substituted: there is "
                "no deterministic stand-in for a model-authored analysis, and "
                "presenting one would misrepresent what the system did.")
        self.provider = provider
        self.ledger = ledger
        self.packet = packet
        self.messages: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.counts: list[dict[str, Any]] = []
        #: A hook the runtime sets for a repair turn: it is handed the fully
        #: assembled request and may refuse it. Nothing else reads the outbound
        #: request, and nothing may modify it -- this is an inspection point,
        #: not an editing point.
        self.before_dispatch: Any = None
        self._pending = ""
        self.counter = tokens_mod.Counter(provider=provider, model=model)

    # -- the system prompt, and where the cache breakpoint goes ---------

    def system(self, contract: str) -> list[dict[str, Any]]:
        """System blocks, stable prefix first, with ONE cache breakpoint.

        Caching is a prefix match: any byte change anywhere before the
        breakpoint invalidates everything after it. So the blocks are ordered
        most-stable first and the breakpoint sits at the end of the last block
        that does not change within a request:

          1. the architecture instructions and the Cockpit-only restriction --
             identical for every request in this deployment;
          2. the pinned domain context: the complete compact field catalogue,
             the grains, the join definitions, the functionality registry and
             the execution contract -- identical for every turn of THIS
             request, because the release is pinned for its lifetime;
          3. <- cache breakpoint here.

        Everything that moves between turns -- the question, the Sonnet
        outputs, the filters, the thread summary, the plan, prior results, the
        failed code, the diagnostics, the remaining budgets -- lives in
        `messages`, after the breakpoint. That is what makes the repair turns
        of one request cache hits rather than four full re-reads of a
        22,000-token catalogue.

        Caching is an OPTIMISATION AND NOTHING ELSE. The catalogue is sent in
        full on every request whether it is served from cache or not; there is
        no hash, no id and no reference Opus would have to resolve. Section 8.1
        is satisfied by the content being present, not by it being cheap.
        """
        return system_blocks(self.packet, contract)

    def opening_context(self) -> str:
        """The dynamic half of the context, as the first user message.

        Sections A, B's request-specific scope, C, J and -- at the analysis
        stage -- F and G: the question in all three forms, the effective scope,
        the thread, the coverage, the sample rows and the remaining budget. All
        of it changes between requests, and J changes between the TURNS of a
        request, so none of it belongs in the cached prefix.
        """
        return opening_message(self.packet)

    # -- one turn ------------------------------------------------------

    def ask(self, *, contract: str, user: str, schema: dict[str, Any],
            tool_name: str, description: str, purpose: str,
            max_tokens: int = 0, finalization: bool = False
            ) -> dict[str, Any]:
        """One counted, reserved, settled turn that must answer through the tool.

        Counted first. Section 9.3 and the owner's instruction: the assembled
        request is measured against the model that will serve it, and refused
        before dispatch if it will not fit with room for the answer. A request
        that fails at the provider for size has spent latency and told the user
        nothing.
        """
        # The reasoning model id, resolved fresh and refused if absent. Every
        # analytical decision in this application is made here, so the one
        # thing that must never happen is this call reaching the provider with
        # an empty model and being served by whatever the SDK ships with.
        resolved = models_mod.resolve()
        model = models_mod.require(resolved.reasoning,
                                   role=models_mod.REASONING_ROLE)
        effort = (os.environ.get("AI_COCKPIT_REASONING_EFFORT")
                  or "").strip().lower()
        limit = max_tokens or self.ledger.limits.max_opus_output_tokens
        system_blocks = self.system(contract)

        if not self.messages:
            # The dynamic context opens the conversation, after the cached
            # prefix, so the catalogue above it stays byte-identical.
            self.messages.append({"role": "user",
                                  "content": self.opening_context()})
        # The conversation as it stands BEFORE this turn. A turn the provider
        # cut off leaves an assistant tool_use with no matching tool_result,
        # and a following request built on that history is malformed -- so a
        # truncated turn is rolled back to here and the caller may ask again
        # from a clean point.
        before = list(self.messages)
        pending = self.messages + [{"role": "user", "content": user}]
        tool = {"name": tool_name, "description": description,
                "input_schema": schema}

        # Section 8.1: read the request that is about to go out, not a log of
        # what was intended. A missing part of the effective context stops the
        # dispatch -- asking Opus to repair code it cannot see would produce
        # something shaped like a repair and arrived at by guessing.
        if self.before_dispatch is not None:
            self.before_dispatch(system_blocks, pending, [tool])

        counted = self.counter.fits(
            system=system_blocks, messages=pending, tools=[tool],
            cap=self.ledger.limits.max_input_tokens_per_call,
            reserve_output=limit)
        self.counts.append({"purpose": purpose, **counted.to_dict()})

        reservation = self.ledger.reserve(
            role=OPUS_ROLE, family=OPUS_FAMILY, purpose=purpose,
            input_tokens=counted.tokens, max_output_tokens=limit,
            finalization=finalization)

        self.messages = pending
        try:
            result = self.provider.converse(
                system=system_blocks, messages=self.messages, tools=[tool],
                max_tokens=limit, model=model, purpose=purpose,
                role=OPUS_ROLE, effort=effort,
                timeout=max(1.0, self.ledger.remaining_seconds))
        except Exception as e:                              # noqa: BLE001
            self.ledger.settle(reservation, output_tokens=0, error=str(e),
                               uncertain=True)
            raise

        self.ledger.settle(
            reservation, input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens)

        # The assistant's own blocks go back verbatim, in order. A tool_use
        # block MUST be answered by a matching tool_result on the next turn,
        # which `answer_tool` does.
        if result.assistant_blocks:
            self.messages.append({"role": "assistant",
                                  "content": result.assistant_blocks})

        self.turns.append({
            "purpose": purpose, "stop_reason": result.stop_reason,
            "tool_calls": len(result.tool_calls),
            "truncated": result.truncated,
            "counted_input_tokens": counted.tokens,
            "count_method": counted.method,
            "reported_input_tokens": result.input_tokens,
            "cache_read_tokens": result.cache_read_tokens,
            "cache_write_tokens": result.cache_write_tokens})

        if result.truncated:
            # Section 9.1: a truncated output is INCOMPLETE, not a shorter
            # answer. Treating half a plan as a plan is how a confident wrong
            # analysis gets built.
            #
            # The partial turn is removed from the history rather than left in
            # it. Keeping it would leave an unanswered tool_use that makes the
            # next request malformed, and would spend the allowance of the
            # retry on re-reading the prose that overran in the first place.
            self.messages = before
            self._pending = ""
            raise OutputTruncated(
                f"The model's response was cut off at its {limit}-token output "
                f"limit, so the {purpose} is incomplete. A truncated plan is "
                f"half a plan, not a smaller one.",
                purpose=purpose, limit=limit)

        for call in result.tool_calls:
            if call["name"] == tool_name:
                self._pending = call["id"]
                return dict(call["input"])

        raise OpusUnavailable(
            f"The model answered in prose rather than through {tool_name}: "
            f"{result.text[:200] or '(nothing)'}")

    def answer_tool(self, payload: str) -> None:
        """Pair the last tool_use with its tool_result, in order.

        Required by the provider and required by section 8.1's "keep the
        assistant tool-use and corresponding tool-result blocks paired and
        ordered correctly".
        """
        pending = getattr(self, "_pending", "")
        if not pending:
            return
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": pending,
                         "content": payload}]})
        self._pending = ""


# ------------------------------------------------------------- the three jobs

def envelope_from(raw: Any, *, kind: str = "answer"
                  ) -> K.AnswerEnvelope | None:
    """Build an answer envelope from the model's structured output.

    Shared by the gate (which answers PRODUCT_HELP and THEORY_CONCEPT in its
    own turn), the sufficiency review, and the single answer rewrite -- one
    builder, so the three cannot drift into carrying different fields.
    """
    if not isinstance(raw, dict) or not raw.get("narrative"):
        return None
    return K.AnswerEnvelope(
        kind=str(raw.get("kind") or kind),
        narrative=str(raw["narrative"]),
        complete=bool(raw.get("complete", True)),
        approximate=bool(raw.get("approximate", False)),
        tables=[K.AnswerTable(
            title=str(t.get("title") or ""),
            columns=as_text_list(t.get("columns")),
            rows=[list(r) for r in (t.get("rows") or [])],
            units={str(k): str(v) for k, v in (t.get("units") or {}).items()},
            fact_ids=as_text_list(t.get("fact_ids")),
            note=str(t.get("note") or ""))
            for t in (raw.get("tables") or [])],
        charts=[K.AnswerChart(
            kind=str(c.get("kind") or "bar"),
            title=str(c.get("title") or ""),
            series=[dict(x) for x in (c.get("series") or [])],
            x_label=str(c.get("x_label") or ""),
            y_label=str(c.get("y_label") or ""),
            unit=str(c.get("unit") or ""),
            fact_ids=as_text_list(c.get("fact_ids")))
            for c in (raw.get("charts") or [])],
        findings=as_text_list(raw.get("findings")),
        suggested_questions=as_text_list(raw.get("suggested_questions")),
        limitations=as_text_list(raw.get("limitations")),
        assumptions=as_text_list(raw.get("assumptions")),
        hypotheses=as_text_list(raw.get("hypotheses")),
        fact_ids=as_text_list(raw.get("fact_ids")))


#: What one turn's user message costs, in characters, over and above the
#: packet. The gate's instruction block and the planning brief both land near
#: 1,400 characters; 2,400 is that with headroom. It is a reservation rather
#: than a measurement, so it is named and `Conversation.counts` can be checked
#: against it after a run.
TURN_MESSAGE_CHARS = 2400

#: What to hold back, beyond the reply, for the turns AFTER the one being
#: sized. The analysis packet is built once and every later turn of the request
#: -- the review, each repair -- is appended to the same conversation, so a
#: packet sized to fill the cap exactly leaves the second turn nowhere to go.
#:
#: One further turn, measured at about 3,300 tokens: a repair turn carrying a
#: failure packet with its diagnostics and the results already obtained.
#:
#: Deliberately NOT the worst case, and this is a real trade-off rather than a
#: rounding. A review turn carrying a WIDE result -- a `SELECT *` over the
#: 198-column facility relation -- costs about 8,000, and four repairs cost
#: about 13,000; reserving for either would spend every rung of the reduction
#: ladder on every request, and in Standard mode would refuse the packet
#: outright, including for the great majority of questions that are answered
#: from their first submission. So the reserve buys the common case one further
#: turn, and where a wide result or a long repair sequence outgrows the cap
#: that is reported as CONTEXT_TOO_LARGE with the honest reason.
#: docs/cockpit_agentic_v3/CONTEXT_SIZING.md records how many turns each mode
#: affords and why Deep is the configuration that holds the full repair path.
CONVERSATION_RESERVE = 4000


def request_sizer(*, stage: str, contract: str, schema: dict[str, Any]):
    """A callable that estimates the FULLY ASSEMBLED request for a payload.

    The packet is not the request. A request also carries the shared preamble,
    the turn's contract, the untrusted-data note, the tool schema, the turn's
    own user message -- and, unavoidably, the packet re-serialized as a string
    inside the message envelope, which escapes every quote and inflates dense
    JSON by roughly a sixth. Measuring the packet alone against the per-call
    cap ignored all of that, and that is how a packet the builder believed was
    within budget became a request the provider refused.

    So the builder's reduction ladder is given this, not a guess: it renders a
    candidate payload through the SAME functions that will render the real
    request, and estimates the result. `system_blocks` and `opening_message`
    are module-level for exactly this reason -- the probe and the dispatch
    cannot drift apart.
    """
    def measure(payload: dict[str, Any]) -> int:
        probe = CockpitContextPacket(
            version="", request_id="", payload=payload, estimated_tokens=0,
            token_method="", stage=stage)
        return tokens_mod.estimate({
            "system": system_blocks(probe, contract),
            "messages": [
                {"role": "user", "content": opening_message(probe)},
                {"role": "user", "content": "x" * TURN_MESSAGE_CHARS}],
            "tools": [{"name": "t", "description": "d",
                       "input_schema": schema}]})

    return measure


def gate(conversation: Conversation, packet: CockpitContextPacket
         ) -> K.FunctionalityDecision:
    """STAGE A. Opus's first responsibility: what kind of request is this, and
    who owns it.

    Reads the gate packet -- the whole question, the thread, the functionality
    registry, the domain outline -- and returns a decision. It does NOT return
    a plan: it has not been shown the field dictionary, so a plan authored here
    would be authored from a guess about what the columns are called. Stage B
    is where planning happens, with the dictionary in front of it.

    This remains a full semantic judgement. Nothing in this function matches a
    keyword, and the model scores every functionality in the registry exactly
    as it did before; what changed is how much of the catalogue it had to read
    to do it.
    """
    request = packet.payload["A_request"]
    user = "\n\n".join([
        "Decide what kind of request this is and who owns it.",
        f"ORIGINAL: {request['original_question']}",
        f"BUSINESS REQUEST: {request['business_request']}",
        "SUBQUESTIONS:\n" + "\n".join(f"- {s}" for s in request["subquestions"]),
        ("UNRESOLVED AMBIGUITY (do not resolve it by guessing):\n"
         + "\n".join(f"- {a}" for a in request["unresolved_ambiguity"])
         if request["unresolved_ambiguity"] else
         "No ambiguity was flagged by the preprocessing passes."),
        ("FIRST decide the query_mode for THIS turn. Do not carry it over "
         "from the previous turn: a thread that has been discussing what "
         "lifetime PD means can turn to which borrowers' lifetime PD moved, "
         "and those are different modes."),
        ("PRODUCT_HELP and THEORY_CONCEPT answer from knowledge. Put your "
         "complete answer in `answer` in THIS response: no query runs and "
         "there is no second call. For THEORY_CONCEPT you may use general, "
         "stable credit and accounting knowledge -- but a question about how "
         "CREDITPROBE specifically does something is only answerable from the "
         "product information in this packet, and if it is not there, say it "
         "cannot be verified rather than describing what such a system "
         "usually does."),
        ("A question that asks BOTH what something means AND what this book "
         "shows is DATA_ANALYSIS, not THEORY_CONCEPT. Both halves are owed an "
         "answer."),
        ("Anything about current or recent external events -- announcements, "
         "today's rates, market news -- is UNSUPPORTED unless a configured "
         "functionality here genuinely owns it. It must never be answered "
         "from memory."),
        ("Then score every functionality in the registry. If the Cockpit is "
         "not the unique highest scorer, or the action is outside its "
         "ownership, refer or clarify."),
        ("Do NOT plan an analysis in this turn and do not name a column. You "
         "have the domain in outline only. If this is DATA_ANALYSIS and the "
         "Cockpit owns it, the complete field dictionary, the measured "
         "coverage, the sample rows and the execution contract are assembled "
         "and sent to you next, and you plan from those."),
    ])

    data = conversation.ask(
        contract="opus_gate", user=user, schema=GATE_SCHEMA,
        tool_name="functionality_decision",
        description="What kind of request this is and which functionality "
                    "owns it.",
        purpose="opus_gate")

    scores = [K.SuitabilityScore(
        functionality_id=str(s.get("functionality_id") or ""),
        score=int(s.get("score") or 0),
        justification=str(s.get("justification") or ""))
        for s in (data.get("scores") or [])]

    alternatives = [K.AlternativeQuestion(
        question=str(a.get("question") or ""),
        required_fields=as_text_list(a.get("required_fields")),
        available_periods=as_text_list(a.get("available_periods")),
        limitation=str(a.get("limitation") or ""))
        for a in (data.get("alternatives") or [])[:3]
        if a.get("question") and a.get("required_fields")]

    destination = str(data.get("referral_destination") or "")
    route, enabled = "", False
    if destination:
        from backend.cockpit_agentic import registry

        try:
            target = registry.entry(destination)
            route, enabled = target.route, target.enabled
        except LookupError:
            route, enabled = "", False

    mode = str(data.get("query_mode") or "")
    owner = str(data.get("owner") or "")
    raw_decision = str(data.get("decision") or K.CLARIFY_FUNCTIONALITY)

    # The mode is the semantic answer and `decision` is what the server does
    # about it. Where the model gives both and they disagree, the MODE wins:
    # it is the more specific statement, and a decision to execute alongside a
    # mode that executes nothing is a contradiction the runtime must not have
    # to resolve later.
    if mode in K.NO_EXECUTION_MODES:
        raw_decision = K.ANSWER_WITHOUT_DATA
    elif mode == K.OTHER_FUNCTIONALITY:
        raw_decision = K.REDIRECT
    elif mode == K.CLARIFICATION_REQUIRED:
        raw_decision = K.CLARIFY_FUNCTIONALITY
    elif mode == K.UNSUPPORTED_MODE:
        raw_decision = K.UNSUPPORTED_REQUEST

    answer = envelope_from(
        data.get("answer"),
        kind="explanation" if mode in K.NO_EXECUTION_MODES else "answer")
    if mode in K.NO_EXECUTION_MODES and answer is None:
        # The mode promises an answer in this turn. Without one there is
        # nothing to render and no second call coming, so it becomes a
        # clarification rather than an empty success.
        mode = K.CLARIFICATION_REQUIRED
        raw_decision = K.CLARIFY_FUNCTIONALITY

    return K.FunctionalityDecision(
        decision=raw_decision,
        query_mode=mode, owner=owner,
        requires_cockpit_data=bool(data.get("requires_cockpit_data")),
        requires_sql=bool(data.get("requires_sql"))
        if mode not in K.NO_EXECUTION_MODES else False,
        requires_python=bool(data.get("requires_python"))
        if mode not in K.NO_EXECUTION_MODES else False,
        decision_reason=str(data.get("decision_reason") or ""),
        ambiguity=str(data.get("ambiguity") or ""),
        answer=answer,
        scores=scores, best_fit=str(data.get("best_fit") or ""),
        requested_actions=as_text_list(data.get("requested_actions")),
        relevant_exclusions=as_text_list(data.get("relevant_exclusions")),
        mixed_scope=bool(data.get("mixed_scope")),
        mixed_scope_explanation=str(data.get("mixed_scope_explanation") or ""),
        referral_destination=destination,
        referral_reason=str(data.get("referral_reason") or ""),
        referral_route=route, referral_enabled=enabled,
        alternatives=alternatives,
        clarification_question=str(data.get("clarification_question") or ""),
        clarification_options=as_text_list(data.get("clarification_options")),
        public_explanation=str(data.get("public_explanation") or ""))


def _planning_brief(packet: CockpitContextPacket,
                    decision: K.FunctionalityDecision,
                    bounds: K.PlanBounds) -> list[str]:
    """The turn's own message. Everything else Opus needs is in the packet."""
    request = packet.payload["A_request"]
    lines = [
        "The gate has decided this is a Cockpit data analysis. Plan it.",
        # The approved decision, stated in this conversation because the gate
        # happened in a DIFFERENT one. Section 8.1 requires the effective
        # context of a repair to carry the approved functionality decision,
        # and the repair turns continue this conversation, not the gate's.
        (f"APPROVED FUNCTIONALITY DECISION: {decision.decision} "
         f"(query_mode={decision.query_mode}, owner={decision.owner}). "
         f"Ownership is settled and is not reopened."),
        f"ORIGINAL: {request['original_question']}",
        f"BUSINESS REQUEST: {request['business_request']}",
        "SUBQUESTIONS:\n" + "\n".join(f"- {s}" for s in request["subquestions"]),
    ]
    if decision.requested_actions:
        lines.append("REQUESTED ACTIONS:\n"
                     + "\n".join(f"- {a}" for a in decision.requested_actions))
    if decision.decision_reason:
        lines.append(f"WHY THIS IS YOURS: {decision.decision_reason}")
    if decision.public_explanation:
        lines.append("WHAT THE USER WAS TOLD ABOUT THAT DECISION: "
                     + decision.public_explanation)
    if request["unresolved_ambiguity"]:
        lines.append("UNRESOLVED AMBIGUITY (do not resolve it by guessing):\n"
                     + "\n".join(f"- {a}"
                                 for a in request["unresolved_ambiguity"]))
    # Deliberately short. Everything general -- what you have, what a plan is
    # for, what does not belong in it, the three ways out -- is in the
    # `opus_plan` contract, which sits in the SYSTEM blocks and is REPLACED on
    # the next turn. This message is appended to `messages` and is carried for
    # the rest of the request, so every sentence here is paid for again on
    # every repair and review turn that follows.
    lines.append(
        f"Be concise: at most {bounds.subquestions} subquestions, "
        f"{bounds.fields_required} fields, {bounds.steps} steps, canonical "
        f"field names only.")
    return lines


def plan_first_submission(conversation: Conversation,
                          packet: CockpitContextPacket,
                          decision: K.FunctionalityDecision, *,
                          bounds: K.PlanBounds | None = None,
                          on_retry: Any = None) -> dict[str, Any]:
    """STAGE B. The first analysis, planned with the complete dictionary.

    Reached only for `query_mode = DATA_ANALYSIS` and `owner = COCKPIT` that
    also passed the server's score test. This is a NEW conversation over the
    full packet, so the cached prefix it establishes is the one every repair
    and review turn of this request will reuse.

    Three ways out, all of them explicit: the first step, a targeted
    clarification, or an honest explanation that this data cannot answer it.
    There is no fourth, and in particular there is no branch that approximates
    an answer because the dictionary disappointed it.

    Truncation, and the one retry
    -----------------------------
    A planning reply that reaches the output allowance is INCOMPLETE. It is
    not sent for execution, it is not treated as a plan, and it does not
    consume an execution submission -- none has been submitted yet.

    Instead the turn is rolled out of the conversation and Opus is asked ONCE
    more, with the same context, a notice that its previous response overran,
    and the instruction to return the smallest complete valid plan. The retry
    is a provider call and is charged as one; it is not a second analysis
    round and it does not reset anything.

    OPUS writes that plan. This function has no branch that composes one, and
    a second truncation raises `PlanTruncated` rather than producing something
    of its own.
    """
    bounds = bounds or K.PLAN_BOUNDS
    brief = _planning_brief(packet, decision, bounds)

    try:
        data = conversation.ask(
            contract="opus_plan", user="\n\n".join(brief), schema=PLAN_SCHEMA,
            tool_name="analysis_plan",
            description="The analysis plan and the SQL for its first step.",
            purpose="opus_plan")
        attempts, regenerated = 1, False
    except OutputTruncated as first:
        logger.info("The Cockpit planning response was truncated at %s "
                    "tokens; asking for a compact plan once.", first.limit)
        if on_retry is not None:
            on_retry(first)
        compact = list(brief)
        compact.append(
            "YOUR PRIOR PLANNING RESPONSE EXCEEDED THE OUTPUT ALLOWANCE and "
            "was discarded unread. Return the SMALLEST COMPLETE VALID PLAN. "
            "Do not explain the catalogue, do not repeat field definitions, "
            "do not justify each field, do not discuss alternatives you are "
            "not taking, and do not write any final-answer prose. Name the "
            "fields, state the method in two or three sentences, and write "
            "the SQL.")
        try:
            data = conversation.ask(
                contract="opus_plan_compact", user="\n\n".join(compact),
                schema=PLAN_SCHEMA, tool_name="analysis_plan",
                description="The smallest complete valid execution plan.",
                purpose="opus_plan_compact")
        except OutputTruncated as second:
            # Two attempts is the whole allowance. There is no third, and
            # nothing here writes the plan the model could not finish.
            raise PlanTruncated(
                f"The analysis plan did not fit the {second.limit}-token "
                f"response allowance, and the one permitted compact "
                f"regeneration did not either. Nothing was executed.",
                attempts=2, limit=second.limit) from second
        attempts, regenerated = 2, True

    action = str(data.get("action") or "submit_the_first_step")
    plan_raw, plan_bounded = K.bound_plan(data.get("plan"), bounds)
    steps_raw, steps_bounded = K.bound_steps(data.get("steps"), bounds)
    plan = _plan_from(plan_raw, decision)
    submission = None
    if action == "submit_the_first_step":
        submission = _submission_from(steps_raw, plan=plan,
                                      analysis_round=1, submission_number=0)
        if submission is None:
            # It said it was submitting a step and did not supply one. That is
            # not a plan with a small omission; it is nothing to run, and the
            # runtime is told exactly that rather than being handed an empty
            # submission to discover later.
            action = "explain_and_stop"
    return {
        "action": action,
        "plan": plan,
        "submission": submission,
        "clarification_question": str(data.get("clarification_question") or ""),
        "clarification_options": as_text_list(data.get("clarification_options")),
        "explanation": str(data.get("explanation") or ""),
        "attempts": attempts,
        "regenerated": regenerated,
        "bounds_applied": plan_bounded + steps_bounded,
    }


def _plan_from(raw: Any, decision: K.FunctionalityDecision
               ) -> K.AnalysisPlan | None:
    if not isinstance(raw, dict):
        return None
    return K.AnalysisPlan(
        plan_id=str(raw.get("plan_id") or f"plan-{uuid.uuid4().hex[:6]}"),
        subquestions=as_text_list(raw.get("subquestions"))
        or ["(unstated)"],
        fields_required=as_text_list(raw.get("fields_required")),
        joins_required=as_text_list(raw.get("joins_required")),
        steps=as_text_list(raw.get("steps")),
        assumptions=as_text_list(raw.get("assumptions")),
        missingness_handling=str(raw.get("missingness_handling") or ""),
        expected_output_grain=str(raw.get("expected_output_grain") or ""),
        expected_units=str(raw.get("expected_units") or ""),
        method_summary=str(raw.get("method_summary") or ""),
        alternative_method=str(raw.get("alternative_method") or ""))


def _submission_from(raw: Any, *, plan: K.AnalysisPlan | None,
                     analysis_round: int, submission_number: int
                     ) -> K.ExecutionSubmission | None:
    if not raw or not isinstance(raw, list):
        return None
    steps = [K.ExecutionStep(
        step_id=str(s.get("step_id") or f"step-{i + 1}"),
        language=str(s.get("language") or "sql"),
        code=str(s.get("code") or ""),
        purpose=str(s.get("purpose") or ""))
        for i, s in enumerate(raw) if str(s.get("code") or "").strip()]
    if not steps:
        return None
    return K.ExecutionSubmission(
        submission_id=f"sub-{uuid.uuid4().hex[:8]}",
        plan_id=plan.plan_id if plan else "plan-1",
        analysis_round=analysis_round, submission_number=submission_number,
        steps=steps, authored_by="opus")


def repair(conversation: Conversation, packet_failure: K.ExecutionFailurePacket,
           *, plan: K.AnalysisPlan | None, analysis_round: int
           ) -> dict[str, Any]:
    """Hand back the facts and let Opus author the next candidate.

    Everything in this function is a report. Nothing in it is a repair, and
    there is no branch that writes SQL.
    """
    import json

    conversation.answer_tool(json.dumps(packet_failure.to_dict(), default=str))

    user = "\n\n".join([
        "Your query did not run. The exact failure is in the tool result "
        "above, with the fields that DO exist, the valid filter values, the "
        "results you already have and what you have already tried.",
        "CreditProbe has not edited your SQL and will not. The next candidate "
        "comes from you.",
        f"Permitted next actions: "
        f"{', '.join(packet_failure.permitted_next_actions)}.",
        (f"Submissions remaining: "
         f"{packet_failure.budget.get('submissions_remaining')}. "
         f"Analysis rounds remaining: "
         f"{packet_failure.budget.get('analysis_rounds_remaining')}. "
         f"Seconds remaining: "
         f"{packet_failure.budget.get('seconds_remaining')}."),
        ("Do not resubmit the same query: an identical candidate is blocked "
         "before execution. Do not change the user's question to make it "
         "executable."),
    ])

    data = conversation.ask(
        contract="opus_repair", user=user, schema=REPAIR_SCHEMA,
        tool_name="repair_decision",
        description="What you make of the failure, and the next candidate if "
                    "there is one.",
        purpose="opus_repair")

    revised_plan = _plan_from(data.get("plan"), None) or plan
    submission = _submission_from(data.get("steps"), plan=revised_plan,
                                  analysis_round=analysis_round,
                                  submission_number=0)
    return {"action": str(data.get("action") or "explain_and_stop"),
            "what_went_wrong": str(data.get("what_went_wrong") or ""),
            "plan": revised_plan, "submission": submission,
            "clarification_question": str(
                data.get("clarification_question") or ""),
            "clarification_options": as_text_list(data.get("clarification_options")),
            "explanation": str(data.get("explanation") or "")}


def review(conversation: Conversation, result: K.ExecutionResultPacket, *,
           plan: K.AnalysisPlan | None, analysis_round: int
           ) -> K.AnalysisReviewDecision:
    """Sufficiency review, and the final answer when the evidence supports one."""
    import json

    conversation.answer_tool(json.dumps(result.to_dict(), default=str))

    user = "\n\n".join([
        "The results are in the tool result above. Decide whether they answer "
        "the question, subquestion by subquestion.",
        ("A zero-row result means nothing matched. It does not mean the "
         "quantity is zero. A clipped table is not a complete aggregate."),
        ("Every figure in your answer must come from a result you were given. "
         "Report anything the evidence supports only as association as a "
         "hypothesis, never as a cause."),
        (f"Analysis rounds remaining: "
         f"{result.budget.get('analysis_rounds_remaining')}. Submissions "
         f"remaining: {result.budget.get('submissions_remaining')}. If you "
         f"revise, include the changed plan and the new SQL."),
    ])

    data = conversation.ask(
        contract="opus_review_and_answer", user=user, schema=REVIEW_SCHEMA,
        tool_name="review_and_answer",
        description="Whether the evidence suffices, and the answer if it does.",
        purpose="opus_review")

    per_subquestion = [K.SubquestionEvidence(
        subquestion=str(s.get("subquestion") or ""),
        answered=bool(s.get("answered")),
        evidence_fact_ids=as_text_list(s.get("evidence_fact_ids")),
        gap=str(s.get("gap") or ""))
        for s in (data.get("per_subquestion") or [])]

    decision = str(data.get("decision") or K.INSUFFICIENT_DATA)
    revised_plan = _plan_from(data.get("plan"), None) or plan
    submission = _submission_from(data.get("steps"), plan=revised_plan,
                                  analysis_round=analysis_round + 1,
                                  submission_number=0)

    envelope = envelope_from(data.get("answer"))

    if decision == K.REVISE_ANALYSIS and submission is None:
        # The contract refuses a revision with no code, and refusing it here
        # with a clear reason beats letting the dataclass raise later.
        decision = K.INSUFFICIENT_DATA

    return K.AnalysisReviewDecision(
        decision=decision, per_subquestion=per_subquestion,
        revised_plan=revised_plan, revised_submission=submission,
        gap_addressed=str(data.get("gap_addressed") or ""),
        clarification_question=str(data.get("clarification_question") or ""),
        clarification_options=as_text_list(data.get("clarification_options")),
        answer=envelope)


def rewrite_answer(conversation: Conversation, envelope: K.AnswerEnvelope,
                   instruction: str) -> K.AnswerEnvelope | None:
    """The ONE answer-only rewrite permitted by section 29.

    It cannot execute, cannot open an analysis round and cannot reset a
    counter -- and it is not given the means to: the schema carries no plan
    and no steps, and the caller discards anything else that arrives.

    CreditProbe does not edit the prose. It says what did not check out and
    hands the answer back.
    """
    user = "\n\n".join([
        instruction,
        "YOUR ANSWER, EXACTLY AS YOU WROTE IT:",
        json.dumps(envelope.to_dict(), default=str),
    ])
    data = conversation.ask(
        contract="opus_answer_rewrite", user=user, schema=REWRITE_SCHEMA,
        tool_name="rewritten_answer",
        description="The corrected answer. No code, no new analysis.",
        purpose="opus_answer_rewrite", finalization=True)
    return envelope_from(data.get("answer"), kind=envelope.kind)


__all__ = ["Conversation", "GATE_SCHEMA", "OPUS_ROLE", "OpusUnavailable",
           "PLAN_SCHEMA", "REPAIR_SCHEMA", "REVIEW_SCHEMA", "REWRITE_SCHEMA",
           "CONVERSATION_RESERVE", "TURN_MESSAGE_CHARS", "envelope_from",
           "gate",
           "opening_message", "plan_first_submission", "repair",
           "request_sizer", "system_blocks",
           "review", "rewrite_answer"]
