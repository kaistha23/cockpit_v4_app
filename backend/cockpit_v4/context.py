"""
The starting context: small, factual, inspectable, and never a template.

What is deliberately NOT here
-----------------------------
The 991-field catalogue, sample rows, the expanded macro pivot, every ratio
definition and the full missingness table. All of it remains available
through `inspect_catalog`; none of it is attached to a question that has not
asked for it. "Who are you?" gets product metadata and stops.

What IS here is fixed metadata with a version: who is asking and what they
may see, which release and quarter are pinned, the compact relation index,
the ownership registry with real route ids, and the recent thread turns. That
is the difference between context and a pre-written analysis: nothing in this
packet tells the analyst which fields to use or what method to apply.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.cockpit_v4 import PROMPT_VERSION
from backend.cockpit_v4 import investigation as inv_mod
from backend.cockpit_v4.config import Limits

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "analyst.md"

#: Default and maximum recent Q&A pairs carried into a new turn.
DEFAULT_RECENT_TURNS = 3
MAX_RECENT_TURNS = 8

#: How many of a clarification's offered choices ride along in history.
#:
#: Bounded because the block is carried on every turn of the rest of the
#: thread. The contract caps the options anyway; this is the belt.
CLARIFICATION_OPTIONS = 6


def analyst_instruction(catalog: Any = None) -> str:
    """The analyst prompt, speaking the period language of THIS book.

    The prompt carries `{{TOKEN}}` placeholders where it needs to name a
    period, because it used to carry a calendar: its worked example read
    "period not specified: latest populated quarter 2026Q2, against 2026Q1",
    and a live run reading the MONTHLY book was told so in its own
    instructions. With no catalogue the tokens resolve to neutral words and
    no calendar is named at all.
    """
    from backend.cockpit_v4 import semantics as sem

    return sem.substitute(
        PROMPT_PATH.read_text(encoding="utf-8").strip(), catalog)


@dataclass
class Packet:
    """The assembled starting context, with its own size report."""

    system_blocks: list[dict[str, Any]]
    first_user_message: str
    payload: dict[str, Any]
    prompt_version: str = PROMPT_VERSION

    def estimated_tokens(self) -> int:
        text = json.dumps(self.system_blocks, default=str) + \
            self.first_user_message
        return int(len(text) / 2.2) + 1


def _registry_compact(catalog: Any = None) -> list[dict[str, Any]]:
    """Which functionality owns what, with the routes that actually exist.

    The Cockpit's own line is written from the BOOK this run is reading. It
    used to be taken verbatim from the module registry, which says the
    Cockpit owns "data already stored in the twenty-quarter corporate
    domain" -- true of the pre-domain release and false of both books this
    Cockpit now serves. A run reading the monthly Retail book was handed that
    sentence as a product fact.
    """
    from backend.cockpit_v4 import domains as dom_mod
    from backend.cockpit_v4 import semantics as sem

    try:
        from backend.cockpit_agentic import registry as v3_registry
        doc = v3_registry.compact()
    except Exception:  # noqa: BLE001
        return []
    entries = doc.get("functionalities") if isinstance(doc, dict) else doc
    domain_id = sem.domain_of(catalog)
    label = dom_mod.LABELS.get(domain_id, "")
    periods = len(sem.populated_periods(catalog)) if catalog is not None else 0
    noun = sem.period_noun(catalog) if catalog is not None else "period"
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        body = {k: entry.get(k) for k in
                ("id", "name", "owns", "excludes", "route", "enabled")
                if entry.get(k) is not None}
        if body.get("id") == "cockpit" and label:
            owns = list(body.get("owns") or [])
            body["owns"] = [
                f"Querying, comparing and explaining data already stored in "
                f"the {label} book: {periods} reporting {noun}s to "
                f"{sem.vocabulary(catalog)['LATEST_PERIOD']}"
            ] + [line for line in owns[1:]]
            body["reads"] = label
        out.append(body)
    return out


def _catalog_index(catalog: Any, scope: Any) -> list[dict[str, Any]]:
    """Relation names, one-line grains and column counts. No definitions.

    The point of the index is that the analyst can tell WHICH relation to ask
    about without being told what every column in it means.
    """
    index = []
    outline: dict[str, Any] = {}
    getter = getattr(catalog, "outline", None)
    if callable(getter):
        try:
            outline = getter() or {}
        except Exception:  # noqa: BLE001
            outline = {}
    grains = {e.get("name"): (e.get("grain") or e.get("purpose") or "")
              for e in (outline.get("relations") or [])
              if isinstance(e, dict)}
    permits = getattr(scope, "permits", None)
    for relation in catalog.relations():
        if callable(permits) and not permits(relation):
            continue
        index.append({"relation": relation,
                      "grain": grains.get(relation, ""),
                      "columns": len(catalog.columns(relation))})
    return index


def build(*, question: str, principal: dict[str, Any], scope: Any,
          catalog: Any, limits: Limits, mode: str, release_summary:
          dict[str, Any], ui_filters: dict[str, Any] | None = None,
          recent_turns: list[dict[str, Any]] | None = None,
          summary: dict[str, Any] | None = None,
          location: str = "", capability: Any = None,
          investigation: dict[str, Any] | None = None,
          session: Any = None, analytical: bool = False) -> Packet:
    """Assemble the packet. Never trims the instruction to hit a target."""
    from backend.cockpit_v4 import semantics as sem
    from backend.cockpit_v4 import values as val

    # WHICH CATEGORY VALUES this book holds, and which of them this question
    # names. Resolving a value is a different question from resolving a
    # field, and conflating the two is what sent a live run round the
    # catalogue five times inventing `product_name`, `product_category` and
    # `product_code` because `facility_type` did not obviously contain
    # "prject finance".
    value_index: dict[str, Any] = {}
    if session is not None:
        try:
            value_index = val.dimensions(session=session, catalog=catalog)
        except Exception:  # noqa: BLE001 - a book without a live session
            value_index = {}
    named = val.phrases(question, index=value_index) if value_index else []
    resolved = [v for v in named if isinstance(v, val.Resolution)]
    asked = [v for v in named if isinstance(v, val.Ambiguity)]

    calendar = getattr(catalog, "calendar", None)
    slots = list(getattr(calendar, "slots", ()) or ())
    populated = list(getattr(calendar, "populated", ()) or ())
    noun = sem.period_noun(catalog)

    # WHICH BOOK, read off the catalogue this run was given. It used to be a
    # module constant, which is exactly how a Retail thread could be handed a
    # packet that told the analyst it was reading the corporate domain.
    domain_id = sem.domain_of(catalog) or str(
        release_summary.get("domain_id") or "")
    domain_label = str(release_summary.get("domain_label") or "")

    pinned: dict[str, Any] = {
        "domain": domain_id,
        "domain_label": domain_label,
        "release_id": getattr(scope, "dataset_release_id", "")
        or getattr(catalog, "dataset_release_id", ""),
        "release_fingerprint": getattr(catalog, "release_fingerprint", ""),
        "tenant": "server-pinned; not settable from this conversation",
        "reporting_frequency": sem.frequency(catalog),
        f"reporting_{noun}s": slots,
        f"populated_{noun}s": populated,
        f"latest_populated_{noun}": populated[-1] if populated else "",
        "reporting_currency": getattr(catalog, "reporting_currency", ""),
        "amount_scale": getattr(catalog, "amount_scale", ""),
        "mode": mode,
        "ui_filters": dict(ui_filters or {}),
        "cockpit_location": location,
        "release": {k: release_summary.get(k) for k in
                    ("dataset_release_id", "domain_id", "domain_label",
                     "release_fingerprint", "origin", "data_version",
                     "not_client_data", "reporting_currency", "amount_scale",
                     "reporting_frequency", "geography_name")
                    if k in release_summary},
    }
    if domain_id:
        pinned["domain_note"] = (
            f"This thread reads the {domain_label or domain_id} book and "
            f"nothing else. Its relations are the only ones open to it. A "
            f"question about the other book is a question for a thread "
            f"opened in that book: say so rather than answering it from "
            f"here.")

    budget = {
        "mode": limits.mode,
        "deadline_seconds": limits.deadline_seconds,
        "execution_submissions": limits.execution_submissions,
        "analysis_rounds": limits.analysis_rounds,
        "catalog_calls": limits.catalog_calls,
        "artifact_reads": limits.artifact_reads,
        "steps_per_batch": limits.steps_per_batch,
        "note": ("These are the limits for this run. If the scope genuinely "
                 "does not fit them, propose a narrower agreed scope; do not "
                 "silently drop part of the question."),
    }
    if capability is not None:
        budget["response_tokens_reserved"] = limits.reserved_output_tokens

    turns = list(recent_turns or [])[-MAX_RECENT_TURNS:]
    history = []
    for t in turns:
        prior = t.get("answer") or {}
        entry: dict[str, Any] = {
            "turn_id": t.get("turn_id"), "ordinal": t.get("ordinal"),
            "question": t.get("question"),
            "answer": prior.get("narrative", "")[:1500],
            "disposition": prior.get("disposition", ""),
        }
        # A CLARIFICATION IS HALF A CONVERSATION WITHOUT ITS QUESTION.
        #
        # A turn can end by putting one question back to the reader, who
        # answers it by clicking an option -- and what the next turn then
        # receives as its question is the option's TEXT. "Symmetric
        # allocation." Three words, arriving alone.
        #
        # The question those words answer lives in `clarification_question`,
        # and this block carried the narrative and the disposition and not
        # that. So the analyst saw that it had asked SOMETHING, and a short
        # phrase it had to work backwards from. The round trip completed
        # only when the analyst had happened to repeat its question inside
        # the narrative as well, which nothing requires it to do.
        #
        # The options come too. A reply is usually one of them word for
        # word, and seeing the list is what makes that unmistakable rather
        # than probable.
        if entry["disposition"] == "clarification":
            entry["you_asked"] = str(prior.get("clarification_question") or "")
            offered = [str(o) for o
                       in (prior.get("clarification_options") or ())]
            if offered:
                entry["you_offered"] = offered[:CLARIFICATION_OPTIONS]
            # AND THE QUESTION THAT IS STILL WAITING TO BE ANSWERED.
            #
            # `you_asked` and `you_offered` carry the clarification. They do
            # not carry the REQUEST it interrupted, and that is the thing the
            # next turn has to act on: "Exposure at default" supplies one
            # missing word, and everything else -- the dimension, the
            # ranking, the book, the period -- is still sitting in the
            # question above it, which this block was leaving the analyst to
            # reassemble from two separate fields and a hint.
            #
            # Stated once, as a field, so the effective request after the
            # reply is not something the analyst has to infer.
            if entry["question"]:
                entry["the_question_still_standing"] = str(entry["question"])
            entry["note"] = (
                "The question that follows this turn is most likely the "
                "reader ANSWERING it, often one of the offered choices word "
                "for word. Read it that way and carry on with the "
                "analysis rather than asking again. What you are answering "
                "is the request in `the_question_still_standing`: the reply "
                "supplies ONLY the part you asked about, and every other "
                "part of it -- dimension, ranking, filters, period, book -- "
                "stands exactly as written. A three-word reply is not a new "
                "three-word question. If it plainly asks something else, it "
                "is a new question.")
        history.append(entry)

    from backend.cockpit_v4 import product_knowledge as pk

    # Computed here, not guessed by the model: which product detail — if any
    # — this question names beyond what the synopsis below already carries.
    # `worker.py` uses the same verdict to decide whether
    # `inspect_product_knowledge` is offered on the first action.
    product_coverage = pk.coverage(question)

    # WHAT THIS QUESTION IS ABOUT, decided before the packet is built.
    #
    # An analytical turn does not carry the product pack. It is about five
    # kilobytes describing what CreditProbe IS, on a request that asks what
    # a book DID -- and `inspect_product_knowledge` is already off the tool
    # list for exactly the same reason. Every byte on an action turn is
    # something the model reads before it can decide anything, and the
    # measured action payload for the two questions that failed on the Mac
    # was sixty kilobytes.
    #
    # It is not a capability the run loses: the tool comes back on the
    # second action like any withheld one, and this is the only turn the
    # pack is missing from.
    product_pack: dict[str, Any] = {} if analytical else {
        # ~900 tokens of product facts. Enough to answer "Who are you?" or
        # "What is CreditProbe?" well in ONE generation; everything deeper
        # is a tool call away. Attaching the whole pack would be the V3
        # mistake in a new costume.
        "creditprobe": pk.synopsis(),
        "product_knowledge_coverage": {
            **product_coverage,
            "note": (
                "SYNOPSIS means the product facts above already cover "
                "this question: answer from them in this action rather "
                "than retrieving. RETRIEVAL means the question names "
                "detail the synopsis does not carry — read exactly those "
                "topics. Either way, if your first action does not "
                "finish the run, inspect_product_knowledge is available "
                "on every action after it."),
        },
        "product_functionalities": _registry_compact(catalog),
    }

    # The seeded packet first, because readiness depends on it: a thread
    # opened from a card already holds the relation, the segment value and
    # both periods, and a run that does not know that goes looking for them.
    seed_packet: dict[str, Any] = {}
    if investigation:
        seed_packet = inv_mod.analysis_packet(
            catalog=catalog, session=session, seed=investigation)
    _readiness = sem.readiness(
        catalog, question, seed_packet=seed_packet,
        value_resolution=_value_resolution(resolved, asked),
        # The reader's own earlier wording in this thread. A follow-up that
        # says "the top five by that measure" names no measure by itself and
        # was therefore sent to the catalogue for facts the thread had
        # already established. Their words only -- never the analyst's
        # declared mappings -- so no run can widen its own surface.
        already_asked=[str(t.get("question") or "")
                       for t in (recent_turns or [])])

    # OPT-IN worked plans (COCKPIT_V4_EXAMPLES=on), for runs whose model
    # writes governed plans. They go in the VOLATILE block below, never the
    # stable ones, because they change with the question and would otherwise
    # invalidate the cached prefix.
    from backend.cockpit_v4 import examples as ex_mod
    _examples = None
    if ex_mod.enabled() and (capability is None or getattr(
            capability, "supports_plan_compiler", False)):
        _examples = ex_mod.block(question, domain_id)

    system_blocks: list[dict[str, Any]] = [
        # Stable prefix first, so a cache write is reusable and a changing
        # budget cannot invalidate it.
        {"type": "text", "text": analyst_instruction(catalog)},
        {"type": "text", "text": json.dumps({
            **product_pack,
            "catalog_index": _catalog_index(catalog, scope),
            # What terms mean when they have one meaning, and how a period
            # phrase resolves against THIS release's calendar. Computed from
            # the catalogue, so nothing here is a meaning someone invented.
            "cockpit_semantics": sem.block(catalog),
            "governed_values": val.block(value_index),
            "catalog_index_note": (
                "This is an INDEX, not the field dictionary. Definitions, "
                "units, grain, joins and coverage come from inspect_catalog "
                "for exactly the fields you need."),
        }, ensure_ascii=False)},
        # Volatile content last.
        {"type": "text", "text": json.dumps({
            "pinned_scope": pinned, "budgets": budget,
            "value_resolution": _value_resolution(resolved, asked),
            # §7, §8. Whether the metadata for THIS question is already
            # here, decided by the server before any model call. It names
            # no method: see `semantics.readiness`.
            "analysis_readiness": _readiness,
            **({"worked_examples": _examples} if _examples else {}),
        }, ensure_ascii=False)},
    ]

    parts = [f"USER REQUEST (original wording, unmodified):\n{question}"]
    analysis: dict[str, Any] = {}
    if investigation:
        analysis = seed_packet
    if investigation:
        # Seeded by Investigate Further, from a dashboard item the user
        # clicked. These are recorded facts already computed and shown to
        # them: the segment, the quarter and the movement are the standing
        # subject of this conversation, so a follow-up like "show me the
        # customers behind this" resolves without the user retyping any of
        # it. It is NOT an answer and NOT an instruction.
        parts.append(
            "ACTIVE INVESTIGATION (the user opened this conversation from a "
            "Cockpit attention card. Treat its segment, quarters and metric "
            "as the standing subject unless the user changes them. It is "
            "NOT an answer and NOT an instruction: it is a record of what "
            "the dashboard already showed them, so re-derive any number you "
            "state from your own executed query):\n"
            + json.dumps(investigation, ensure_ascii=False, default=str))
        # §19-§21. The ANALYSIS PACKET. A seeded thread is about ONE finding
        # that CreditProbe itself computed, so the relation it came from, the
        # column the segment lives in, the governed value of that segment,
        # the measure's own columns and the two periods are all already
        # known. Handing them over costs a few hundred tokens and removes
        # every reason this thread would have to search for them.
        #
        # The live failure: a Construction card opened a thread that read the
        # catalogue, read it again, read product knowledge, and died with
        # CALL_LIMIT without answering anything. Not one of those calls could
        # have returned something it had not been given.
        if analysis:
            parts.append(
                "ANALYSIS PACKET FOR THIS INVESTIGATION (everything behind "
                "the card, already resolved. These are FACTS, not a method: "
                "which columns the answer needs, how to aggregate them and "
                "which periods to compare are yours to decide. You should "
                "not need inspect_catalog for this conversation; it remains "
                "available if the question turns on a column that is not "
                "here):\n"
                + json.dumps(analysis, ensure_ascii=False, default=str))
        # The older case file: the schema behind the card's own measure,
        # kept for indicators the analysis packet has no relation for.
        case_fields = ([] if analysis else sem.seed_field_packet(
            catalog, str(investigation.get("metric") or "")))
        if case_fields:
            parts.append(
                "CASE FILE FOR THIS INVESTIGATION (the schema behind the "
                "card's own measure, already resolved. These are field "
                "FACTS, not a method: which of them the answer needs, how to "
                "aggregate them and which period to compare are yours to "
                "decide. If the question turns on a field that is not here, "
                "inspect_catalog is still available):\n"
                + json.dumps({"metric": investigation.get("metric", ""),
                              "fields": case_fields},
                             ensure_ascii=False, default=str))
    if history:
        parts.append(
            "RECENT COMPLETED TURNS IN THIS THREAD (exact records; these "
            "outrank any summary):\n"
            + json.dumps(history, ensure_ascii=False))
    if summary:
        parts.append(
            "OLDER-HISTORY SUMMARY (lower authority than the exact turns "
            "above; retrieve the exact turn with read_artifact if it "
            "matters):\n" + json.dumps(summary.get("body"),
                                        ensure_ascii=False))
    parts.append(
        "Decide what this request is, who owns it, and take your next "
        "action now.")

    return Packet(
        system_blocks=system_blocks,
        first_user_message="\n\n".join(parts),
        payload={"pinned_scope": pinned, "budgets": budget,
                 # The action state machine reads this. It is the same
                 # object the analyst is shown, so the state a run is put
                 # into is the state it was told it is in.
                 "analysis_readiness": _readiness,
                 "value_resolution": _value_resolution(resolved, asked),
                 "recent_turns": history,
                 "investigation": investigation or {},
                 "investigation_packet": analysis,
                 "principal": {"id": principal.get("id", ""),
                               "tenant": principal.get("tenant", "")}})


def _value_resolution(resolved: list[Any], asked: list[Any]
                      ) -> dict[str, Any]:
    """What this question's own words were taken to mean, before any call.

    Handed over as FACTS, so the analyst neither has to search the catalogue
    for a value it already has nor has to decide, unaided, that "prject
    finance" is a typo. Where the phrase genuinely names more than one real
    value the candidates are listed and the question is left open: that is a
    blocking ambiguity, and picking one of them would be choosing the
    analysis on the reader's behalf.
    """
    return {
        "recognised": [
            {"term": r.raw, "relation": r.relation, "field": r.field_name,
             "value": r.value, "exact": r.exact,
             "record_as": "canonical_mapping" if r.exact
             else "resolved_assumption",
             "say": r.as_assumption()}
            for r in resolved],
        "needs_a_question": [
            {"term": a.raw, "question": a.question,
             "candidates": [{"field": c.field_name, "value": c.value}
                            for c in a.candidates]}
            for a in asked],
        "how_to_use": (
            "These values were matched against the values this release "
            "actually holds. An entry in `recognised` is settled: filter on "
            "`field` = `value` and record it -- do NOT call inspect_catalog "
            "looking for another field that might hold the term as the "
            "reader spelled it, and do NOT invent a field name. An entry in "
            "`needs_a_question` names more than one real value: ask it as a "
            "blocking ambiguity rather than choosing. An empty list here "
            "means the question named no governed category value, not that "
            "the book has none."),
    }


#: How to present the result, carried only on a turn that writes an answer.
#:
#: THE SHAPE OF AN ANALYTICAL ANSWER.
#:
#: `analyst.md` writes four worked shapes under "Writing the answer" and
#: every one of them is a PRODUCT question -- who are you, what does Early
#: Warning do, what is TAC. The shape it never describes is the one this
#: product exists for: the answer written with a result set in hand. The
#: "Analysis" section beside it is entirely about how to RUN the query.
#:
#: So the one act the whole system is built around was the one act nobody
#: had said what good looks like for, and the default an analyst falls back
#: to without it is narrating the table -- which is the one thing the reader
#: already has.
#:
#: This is standards, not a template. It says what an answer is held to, and
#: `length_follows_the_question` is in it because a checklist read as a form
#: to fill produces exactly the padding it is meant to prevent.
#:
#: Here rather than in `analyst.md` for the same reason the chart policy is:
#: it is only true on the turn that has the rows, and the instruction file is
#: carried on every action attempt and measured against a bound.
ANALYTICAL_ANSWER: dict[str, Any] = {
    "phase": "THE ANSWER",
    "who_is_reading": (
        "A senior credit officer with your table in front of them. They can "
        "read rows. What they cannot get from the rows is what you make of "
        "them."),
    "lead_with_the_answer": (
        "The first sentence answers the question that was asked and carries "
        "the figure. Not a restatement of the question, not what you ran, "
        "not what came back."),
    "a_figure_needs_something_beside_it": (
        "A number on its own leaves the reader to size it. Put it against "
        "something the RESULT already holds -- the prior period, the rest "
        "of the book, the other segments, a threshold the policy names. If "
        "the result holds no comparison, give the figure and say so. Do not "
        "reach for one that was not computed."),
    "say_where_it_sits": (
        "Concentration is the sentence a credit officer acts on: how much "
        "of the total sits in how few names, sectors or buckets, and "
        "whether a movement is broad or is one exposure. The rows will "
        "say. The reader should not have to add them up."),
    "what_moved_it": (
        "Where the result shows direction, name the driver and how much of "
        "the change it accounts for. Where it does not, say that this cut "
        "shows the movement and not its cause."),
    # CLOSURE-02. Here rather than in `analyst.md`: this is only true of a
    # turn that HOLDS two figures, and the instruction is carried on every
    # attempt and measured against a bound.
    "get_the_direction_right": (
        "Read both figures before describing the movement. A larger end "
        "figure has not eased, fallen or declined, and a smaller one has "
        "not risen -- whatever the move means for the book. Say it is "
        "small if it is small; do not say it went the other way."),
    "what_it_means_for_the_book": (
        "A sentence or two on the consequence, and what is worth doing "
        "next -- a name to look at, a limit to check, an analysis to run. "
        "Only what these rows support."),
    "what_this_does_not_establish": (
        "Say it once, plainly, where it matters: the grain, the "
        "missingness, the vintage, what this cut cannot see. A limitation "
        "stated once is analysis. One hedged onto every clause is noise."),
    "do_not_narrate_the_table": (
        "The table is published beside you. Walking its rows in prose is "
        "the commonest way an answer gets longer without getting better. "
        "Cite the figures that carry the argument; leave the rest to the "
        "table."),
    "do_not_narrate_the_process": (
        "The reader has the process panel and the governance trace. What "
        "you ran, how many rows came back and which tool you called are "
        "not the answer."),
    "length_follows_the_question": (
        "A one-number question gets a short answer. Padding a direct answer "
        "so it looks thorough is how a careful answer comes to read as "
        "automated."),
}


#: THE SHAPE OF A PRODUCT ANSWER, carried only on the turn that writes one.
#:
#: These four outlines lived in `analyst.md`, which is sent on EVERY action
#: attempt and measured against a payload bound. They describe how to write
#: a product answer, and an action turn cannot write one: it is choosing
#: what to run. So the run paid for them on every attempt and could act on
#: them on at most one -- exactly the argument that put the chart policy and
#: the policy pack here rather than there.
#:
#: Moving them freed the room the `result_rows` operation needed in the
#: instruction file. Nothing about their content changed; what changed is
#: which turn receives them.
PRODUCT_ANSWER: dict[str, Any] = {
    "phase": "THE ANSWER",
    "match_the_shape_to_the_question": (
        "Use the shape that fits. Do not walk through every section of one "
        "of these outlines because it is there."),
    "a_broad_product_question": (
        "\"Who are you?\", \"What problem does CreditProbe solve?\", \"Why "
        "would a CRO use this?\" wants: one strong line of positioning; the "
        "problem a senior credit officer actually has; how CreditProbe "
        "addresses it; the connected workflow -- Detect, Diagnose, Decide, "
        "Drive Alignment; the functionalities that matter to them; one "
        "concrete example; the governance boundary; and two or three "
        "questions worth asking next."),
    "a_single_module_question": (
        "\"What does Early Warning do?\" wants: what it is; why a senior "
        "credit officer needs it; how it works; what it produces; a "
        "practical example; how it connects to the rest; what to ask next."),
    "a_module_question_about_cockpit": (
        "\"What is Cockpit?\", \"Why should I use Cockpit?\", \"What problem "
        "does Cockpit solve?\" wants: what Cockpit is; the problem it solves "
        "for a senior credit officer; how it works -- spot the movement, "
        "explain the drivers, prioritise the follow-ups; what it owns and "
        "what it answers from; a practical example; where Cockpit ends and "
        "Early Warning or What-If begins; and what to ask next."),
    "a_narrow_concept_question": (
        "\"What is TAC?\" wants a direct answer. Do not produce a brochure "
        "for it."),
}


def product_blocks() -> list[dict[str, Any]]:
    """The product-answer shape, for the turn that can publish one.

    Appended on a PRODUCT_HELP turn, beside the credit policy, for the same
    reason: it is only true of a turn that writes a user-facing answer, and
    `finalization_system` -- which carries the ANALYTICAL shape -- runs only
    when the action state is RESULT_READY.
    """
    return [{"type": "text", "text": json.dumps(
        PRODUCT_ANSWER, ensure_ascii=False)}]


#: Four live answers came back as tables and nothing else, one of them to a
#: reader who had asked for a line chart by name. The instruction file did
#: not contain the word "chart", so nothing told the analyst that the form
#: was part of the answer or that the request was there to be honoured.
PRESENTATION: dict[str, Any] = {
    "phase": "PRESENTATION",
    "charts_are_part_of_the_answer": (
        "A result that moves, ranks, splits a total or spreads across a "
        "range reads better drawn than listed. Send `charts` with the "
        "answer, not instead of the table."),
    # THE OTHER HALF OF THE SAME INSTRUCTION. The block above was written
    # after four answers arrived with no chart, and it says "send charts"
    # six ways. Nothing said when NOT to, so the only failure it could
    # cause is the opposite one: a bar chart with a single bar under a
    # sentence that already gave the number, which reads as padding and
    # makes a careful answer look automated.
    "a_chart_is_not_always_the_answer": (
        "Not every result has a shape. One figure has none: state it and "
        "send no chart. Two or three values a reader compares at a glance "
        "are already clearer in the table. Draw the result when the FORM "
        "shows what the rows would make the reader work out -- a ranking, "
        "a movement, a concentration, a migration, a spread -- and leave "
        "it out when it would only repeat a small table."),
    # CLOSURE-04. The two blocks above answer "does this result have a
    # shape?" with a list of shapes, and an ORDERED DISTRIBUTION is not
    # obviously any of them: a delinquency mix is not a ranking (the order
    # is the bucket's, not the measure's), not a movement, and not a
    # concentration. The first live UAT returned a correct delinquency
    # table with no chart for exactly that reason. It is named here, as a
    # kind of shape, rather than as a rule about delinquency.
    "an_ordered_distribution_has_a_shape": (
        "A measure spread across bands with a natural ORDER -- delinquency "
        "buckets, rating grades, IFRS 9 stages, tenor bands, vintages, "
        "score bands -- has a shape even when no band stands out, because "
        "where the weight sits ALONG it is the finding. Several such bands "
        "read better drawn than listed. The order is the axis: draw a "
        "sequence or a composition of one whole, not as a ranking by "
        "size."),
    "say_why_in_one_line": (
        "Put that reason in `why_this_chart`: the reader's question this "
        "form answers. It is published with the chart and appears in the "
        "governance record. A chart you cannot justify in one line is "
        "usually a chart the answer does not need."),
    "an_explicit_request_wins": (
        "If the reader named a form -- \"give me a line chart\" -- send that "
        "`kind`. Their words are in the conversation above."),
    "otherwise_choose_by_shape": (
        "A sequence draws as line, step_line, area or waterfall; a ranking "
        "as bar or grouped_bar; a composition as pie, donut, stacked_bar or "
        "stacked_bar_100; a spread as histogram, box, scatter or bubble; a "
        "from-to grid as heatmap; a rate beside a volume as combo. The "
        "`kind` enum carries what each one means."),
    "a_bar_chart_states_an_order": (
        "bar and grouped_bar are read top-down as a ranking, so when the "
        "answer says \"the largest\" the result behind them must be ORDERED "
        "by the measure being charted. A series that moves up and down is a "
        "sequence, not a ranking: draw it as a line."),
    "several_small_charts": (
        "Prefer several small charts to one crowded one. A trend \"for each "
        "product\" is one chart per product, each titled for its product, "
        "rather than every series fighting over one axis. Use the chart "
        "allowance the budget gives you."),
    "a_correction_replaces_everything": (
        "If this answer comes back for correction, send the charts again "
        "with the corrected answer. A chart left out of a correction is a "
        "chart the reader never sees."),
}


def finalization_system(system_blocks: list[dict[str, Any]], *,
                        domain_id: str = "",
                        question: str = "",
                        undecided: Sequence[str] = ()
                        ) -> list[dict[str, Any]]:
    """The system context a turn needs when its job is to WRITE THE ANSWER.

    The starting context is built for AUTHORING an analysis: the catalogue
    index, the canonical semantics, the product synopsis and the module
    registry are all there so the analyst can decide what to run. Once the
    query has run, none of that decides anything -- the result is in hand and
    the task is "given this exact result, answer the question".

    Carrying it anyway costs about four thousand tokens on every answer turn,
    and on a run that already spent an action retry that is the difference
    between affording an answer and being refused one. So the heavy block is
    replaced with a compact one; the analyst INSTRUCTION and the pinned scope
    stay, because those still govern how the answer must be written.

    Nothing analytical is removed: the result, its evidence and the
    derived-claim contract all arrive in the tool result, not from here.

    HOW TO PRESENT the result is added here rather than in `analyst.md`,
    because it is only true on this turn. The action turn cannot send a
    chart, and the action payload has no room to spare -- the instruction is
    carried on every attempt and is measured against a bound. So the chart
    policy costs nothing until the turn that can act on it. It reaches the
    correction turn too: that turn answers as well, and a correction replaces
    the whole answer, charts included.
    """
    if not system_blocks:
        return system_blocks
    kept: list[dict[str, Any]] = []
    for index, block in enumerate(system_blocks):
        text = str(block.get("text") or "")
        if index == 0 or "pinned_scope" in text:
            # The instruction, and the volatile scope/budget block. The
            # worked plans are for AUTHORING a plan; every answer-turn token
            # costs local latency, so they do not ride into the answer.
            if "worked_examples" in text:
                block = {**block, "text": json.dumps({
                    k: v for k, v in json.loads(text).items()
                    if k != "worked_examples"}, ensure_ascii=False)}
            kept.append(block)
            continue
        if "catalog_index" in text or "creditprobe" in text:
            kept.append({"type": "text", "text": json.dumps({
                "phase": "FINAL ANSWER",
                "note": (
                    "The analysis has already run and its result is in the "
                    "tool result above. Write the answer from that result. "
                    "The catalogue index, the product synopsis and the "
                    "canonical semantics were carried while you were "
                    "choosing what to run and are not repeated here."),
            }, ensure_ascii=False)})
            continue
        kept.append(block)
    # WHAT the answer must be, then HOW to present it. In that order: the
    # prose is the answer and the charts accompany it, and a block list is
    # read in the order it is sent.
    kept.append({"type": "text", "text": json.dumps(
        ANALYTICAL_ANSWER, ensure_ascii=False)})
    kept.append({"type": "text", "text": json.dumps(
        PRESENTATION, ensure_ascii=False)})
    kept += policy_blocks(domain_id=domain_id, question=question,
                          undecided=undecided)
    return kept


#: HOW TO USE THE POLICY, carried with it.
#:
#: Here rather than in `analyst.md` because it is only true on a turn that
#: HAS the pack, exactly like the chart policy. The instruction is carried
#: on every attempt and is measured against a bound, so a rule about
#: material the action turn does not receive would be a rule the action turn
#: pays for and cannot use.
POLICY_RULE = (
    "These clauses are in front of you. A threshold you quote must name the "
    "clause that sets it -- the clause id is the binding for a policy "
    "figure, as an artifact cell is for a portfolio figure, and an uncited "
    "threshold reads as governance without being governed. Never say this "
    "book records no policy on something without reading what is here. "
    "A definition inside a clause governs THAT CLAUSE'S OWN TEST. It does "
    "not decide which measure a question that is not asking to apply it "
    "means: where the question names no clause and two governed readings "
    "would rank or total the book differently, that is a real ambiguity "
    "and the reader chooses, not the pack.")

#: AND THE OTHER HALF, sent only when the question reached a clause.
#:
#: The paid retest asked "Which sectors have the largest exposure?" -- a
#: generic ranking question that names no limit and no clause, and whose
#: three governed readings (`ead_sar_mn`, `limit_sar_mn`, `drawn_sar_mn`)
#: order the sectors differently. `cp.retrieve` returns nothing for it. The
#: synopsis is attached anyway, as it must be, and it carries CP-1.1 --
#: "Exposure is measured as EAD, funded and unfunded together" -- because
#: that is the rule CP-1.1's own breach test runs on.
#:
#: `POLICY_RULE` then said, unconditionally, that a term a clause settles is
#: settled and that treating it as open "invents an ambiguity the policy has
#: already closed". So the one turn whose entire job was to put the question
#: back to the reader was handed a rule telling it the question was invented.
#: It stopped asking, and the reader got one of three answers with no way to
#: know which.
#:
#: The distinction is not about exposure, CP-1.1 or sectors. A policy
#: definition settles terminology for the TEST IT DEFINES -- which the
#: reader has invoked when the question reaches that clause -- and settles
#: nothing for a data-analysis question that never invoked it. The server
#: already decides that deterministically: `cp.retrieve` returns the clauses
#: the question names, in the reader's own words. When it returns none,
#: nothing was invoked and this sentence is not sent at all.
POLICY_SETTLES_THE_TERM = (
    "This question reaches the clauses attached here, so it is asking to "
    "apply them. Where one of them settles a term -- which measure its "
    "limit is tested on, for instance -- that term is settled FOR THIS "
    "TEST, and treating it as open invents an ambiguity the policy has "
    "already closed.")

#: AND THE GUARD ON THAT, for the case retrieval gets generous.
#:
#: Retrieval matches a clause on the reader's own topic words, which is what
#: lets "single name" reach CP-1.1 without the reader knowing a clause id.
#: The same generosity reaches a clause from a question that merely uses a
#: policy word in passing -- "What is our concentration by sector?" reaches
#: CP-1.2 -- and there the sentence above would close a reading the reader
#: has not settled.
#:
#: So it is sent only when the server ALSO has nothing open. `readiness`
#: names the governed terms this question leaves undecided, deterministically
#: and before any model call, and an undecided term is precisely "an
#: alternative that can change the answer". When the server is still holding
#: one, no clause gets to close it quietly; the analyst is told which term,
#: and told to ask.
POLICY_DOES_NOT_CLOSE_AN_OPEN_READING = (
    "These clauses are here because the question names their topic. They do "
    "not decide which measure the question means: {terms}. Each clause's own "
    "definition governs that clause's own test. If two governed readings "
    "would rank or total the book differently, that is a real ambiguity -- "
    "put it to the reader rather than letting a rule about a different "
    "question answer this one.")


def policy_retrieval(*, domain_id: str, question: str) -> dict[str, Any]:
    """What this book's credit policy puts in front of THIS question.

    Deterministic and server-side. `synopsis` always carries the sections
    and every threshold a portfolio number can be measured against;
    `retrieve` attaches, in full, the clauses whose ids or topics the
    question names -- in the reader's own words, exactly like
    `value_resolution`.

    RETRIEVED RATHER THAN OFFERED AS A TOOL. It was a tool first and the
    tool could never be called: the state that holds a result REQUIRES
    `finalize_response`, so a companion beside it is a schema the run pays
    for and cannot reach. Retrieval needs no judgement, so the server does
    it and the analyst cannot fail to have asked.

    Returns `{}` for a run with no book -- a product question has no credit
    policy to cite -- or a book with no pack.
    """
    if not domain_id:
        return {}
    try:
        from backend.cockpit_v4 import credit_policy as cp

        synopsis = cp.synopsis(domain_id)
        found = cp.retrieve(domain_id, question=question)
    except Exception:  # noqa: BLE001 - a book with no policy pack
        return {}
    return {"synopsis": synopsis,
            "retrieved": found if found.get("clauses") else {}}


def policy_blocks(*, domain_id: str, question: str,
                  undecided: Sequence[str] = ()) -> list[dict[str, Any]]:
    """The credit policy as system blocks, for a turn that can collide
    with it.

    H-LIVE-05. These used to be appended by `finalization_system` alone,
    which `orchestration` applies only when the action state is
    RESULT_READY. A PRODUCT_HELP turn also publishes a user-facing answer,
    and it got no policy at all -- so the live answer to "Which sectors are
    above the single-name limit?" was written with an empty policy context
    and said "There is no recorded single-name policy limit in the
    Corporate Credit book", which is false: CP-1.1 exists, its keyword map
    holds "single name", and `retrieve` returns it for that exact question.

    An ACTION turn still gets none. It is choosing what to run and holds no
    number, so a threshold in front of it is a threshold nothing can be
    compared against.
    """
    retrieval = policy_retrieval(domain_id=domain_id, question=question)
    if not retrieval:
        return []
    blocks = [{"type": "text", "text": json.dumps(
        {**retrieval["synopsis"], "how_to_use_this": POLICY_RULE},
        ensure_ascii=False)}]
    if retrieval["retrieved"]:
        open_terms = [t for t in (str(x).strip() for x in undecided) if t]
        how = (POLICY_DOES_NOT_CLOSE_AN_OPEN_READING.format(
            terms=", ".join(sorted(set(open_terms))[:6]))
            if open_terms else POLICY_SETTLES_THE_TERM)
        blocks.append({"type": "text", "text": json.dumps(
            {**retrieval["retrieved"], "how_to_use_this": how},
            ensure_ascii=False)})
    return blocks


def policy_receipt(*, domain_id: str, question: str) -> dict[str, Any]:
    """WHAT THE ANSWER WAS GIVEN, recorded so a reader can check it.

    H-LIVE-06. A later live turn wrote "well inside the CP-1.1 single
    obligor limit of SAR 25,000 million". The figure is right --
    `credit_policy.json` sets `single_obligor_ead_sar_mn` to 25000 -- and
    the answer showed no retrieval, because there was none to show: the
    threshold reached that turn inside `cp.synopsis`, which the server
    attached. Correct is not the same as governed, and nothing in the
    published answer said where the number came from.

    This is that provenance: the pack, its version, which clauses were
    attached in full and which thresholds were in front of the answer. It
    is built from the same deterministic retrieval, so it cannot claim the
    answer was given something it was not.
    """
    retrieval = policy_retrieval(domain_id=domain_id, question=question)
    if not retrieval:
        return {}
    synopsis = retrieval["synopsis"]
    thresholds = {
        str(entry.get("clause") or ""): dict(entry.get("thresholds") or {})
        for entry in (synopsis.get("thresholds_a_portfolio_number_meets")
                      or ())}
    return {
        "book": str(synopsis.get("book") or domain_id),
        "policy": str(synopsis.get("policy") or ""),
        "pack_version": str(synopsis.get("pack_version") or ""),
        "how": "attached by the server to the turn that wrote the answer",
        # Every clause whose threshold was in front of the answer, whether
        # or not the question named it.
        "thresholds_in_context": thresholds,
        # The clauses the question reached, attached in full.
        "clauses_attached": [str(c.get("clause") or "")
                             for c in (retrieval["retrieved"].get("clauses")
                                       or ())],
    }


__all__ = ["DEFAULT_RECENT_TURNS", "MAX_RECENT_TURNS", "POLICY_RULE",
           "POLICY_DOES_NOT_CLOSE_AN_OPEN_READING",
           "POLICY_SETTLES_THE_TERM",
           "PRESENTATION", "PRODUCT_ANSWER",
           "Packet", "analyst_instruction", "build", "finalization_system",
           "policy_blocks", "policy_receipt", "policy_retrieval",
           "product_blocks"]
