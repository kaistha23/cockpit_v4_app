"""Which action the run is ALLOWED to take next, and who decides.

The division of labour
----------------------
CreditProbe knows the orchestration STATE: whether metadata is resolved,
whether a result exists, which book is open. Opus owns the analytical
CONTENT: which measure, which aggregation, which filters, which comparison,
what the numbers mean.

Those were mixed. Every action turn was offered every tool and asked to
choose among them -- including tools that could not legally do anything in
the state the run was actually in. A run with nothing executed was offered
`finalize_response`; a run whose governed metadata was fully resolved was
offered `inspect_catalog`; an analytical run was offered product knowledge.

The live failure that made this unavoidable
-------------------------------------------
run-62282e6be0ca48c5a4afdd3c468e3de8, "What is driving Stage 2 and ECL
growth?", Corporate, 120-second allowance:

    0.137s  first action generation starts
   28.303s  rejected: output_truncated -- no complete tool call
   28.3xx   recovery generation starts, WITH A BROADER TOOL SURFACE
            ("Product knowledge lookup available.")
   55.918s  failed: the one re-ask for a complete action was spent

2 of 12 generations. 0 of 5 executions. 0 of 4 catalogue calls. $0.34 of
$1.50. 64 seconds left. Nothing was exhausted except the re-ask, and the
re-ask was spent asking a harder question than the one that had just
failed.

`tool_choice: {"type": "any"}` was not enough. It requires SOME tool call;
it does not narrow WHAT the turn has to consider. With five tools on the
table and an answer contract among them, "any" still means "read all of
this, then decide" -- and the deciding is what ran out of output.

What this module does, and does not
-----------------------------------
It decides which tools are on the request and which one the provider is
told to call. It decides nothing about the analysis: not the relation, not
the measure, not the aggregation, not the periods, not the filters, not the
interpretation. `execute_analysis` is required in a state where executing
is the only legal transition -- WHAT to execute is entirely the analyst's,
and there is no code here that writes SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4.contracts import (TOOL_EXECUTE, TOOL_FINALIZE,
                                          TOOL_INSPECT, TOOL_PRODUCT,
                                          TOOL_READ)

#: The catalogue is not resolved for this question. The only useful action
#: is to ask for the field facts that are missing.
NEEDS_METADATA = "NEEDS_METADATA"
#: Everything a query needs is already in the packet. The only legal
#: transition is to run one.
READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
#: A validated result exists, or an answer has been refused and is being
#: corrected. The only legal transition is to publish.
RESULT_READY = "RESULT_READY"
#: Not an analytical turn at all.
PRODUCT_HELP = "PRODUCT_HELP"
#: THE ANALYST SAID IT CANNOT CHOOSE, AND WAS NOT ALLOWED TO SAY SO.
#:
#: `blocking_ambiguities` stops execution -- and nothing else. A submission
#: carrying one is refused, the run is still READY_FOR_EXECUTION, and that
#: state offers `execute_analysis` and REQUIRES it. So the next turn is
#: compelled to submit again, and the refusal it just read told it that a
#: resolution "belongs in resolved_assumptions, which do not stop
#: execution". The only move the gate left open was to downgrade the doubt
#: and guess.
#:
#: It did. A live run asked to attribute an ECL change across PD, LGD and
#: CCF recorded "Assumed: attribution is sequential (PD first, then LGD,
#: then EAD)" -- a methodology choice that changes every number in the
#: answer -- and proceeded. The reader was never asked.
#:
#: So a declared blocking ambiguity now has somewhere to go. The run stops
#: being able to execute and can only publish, which is the one state in
#: which `disposition: "clarification"` is reachable.
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"

STATES = (NEEDS_METADATA, READY_FOR_EXECUTION, RESULT_READY, PRODUCT_HELP,
          NEEDS_CLARIFICATION)

#: What a reader is told each state is doing. No internals.
PUBLIC: dict[str, str] = {
    NEEDS_METADATA: "Reading the field definitions this question needs",
    READY_FOR_EXECUTION: "Preparing the query",
    RESULT_READY: "Writing the answer from the result",
    PRODUCT_HELP: "Answering from CreditProbe's product knowledge",
    NEEDS_CLARIFICATION: "Putting one question back to the reader",
}


@dataclass(frozen=True)
class Decision:
    """The surface for ONE turn: what is offered, and what is required."""

    state: str
    #: Tool names, in contract order. Everything else is off the request.
    tools: tuple[str, ...]
    #: The tool the provider is told to call, or "" for "any of the above".
    require: str = ""
    #: Why this state, in one line, for the trace.
    because: str = ""
    #: True when this turn is a re-ask after a malformed one. A recovery may
    #: only ever NARROW: see `narrow`.
    recovering: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def stage(self) -> str:
        return PUBLIC.get(self.state, "Preparing the next action")

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "tools": list(self.tools),
                "require": self.require, "because": self.because,
                "recovering": self.recovering, **self.detail}

    def narrow(self) -> "Decision":
        """The same state, as a RECOVERY.

        A re-ask after a malformed action must never be a broader question
        than the one that failed. The live run restored a withheld tool
        between the two attempts, so the turn that had just run out of
        output while deciding was asked to decide among MORE things.

        Where the state already requires one tool there is nothing left to
        take away, and this only records that the turn is a recovery. Where
        it does not -- product help -- the surface is held at what it was.
        """
        return Decision(state=self.state, tools=self.tools,
                        require=self.require, because=self.because,
                        recovering=True, detail=dict(self.detail))


def decide(*, executed: bool, answer_only: bool, analytical: bool,
           readiness: dict[str, Any] | None,
           product_tool_withheld: bool = False,
           must_clarify: bool = False,
           repair_open: bool = False) -> Decision:
    """The state this run is in, and the one action it may take.

    `readiness` is `semantics.readiness`: a deterministic, server-side
    statement about whether the governed metadata for THIS question is
    already in the packet. It is computed before any model call and it
    names no method.

    `must_clarify` is set once a submission has been refused for a blocking
    ambiguity the ANALYST declared. Nothing here decides that a question is
    ambiguous -- that judgement is the analyst's and arrives in the intent
    it authored. What this decides is that a run which has said it cannot
    choose must stop being asked to execute.
    """
    ready = dict(readiness or {})
    # BEFORE `executed`: a run cannot both hold an unresolved ambiguity and
    # a result, because the ambiguity is what stopped the result existing.
    if must_clarify and not executed:
        return Decision(
            state=NEEDS_CLARIFICATION, tools=(TOOL_FINALIZE,),
            require=TOOL_FINALIZE,
            because=("the analyst declared a reading it cannot choose "
                     "between, so the question goes back to the reader"))

    if executed and repair_open and not answer_only:
        # A batch kept some results and then failed a step. The failure
        # message tells the analyst to author the correction, so the turn
        # must let it: finalize-only here made that instruction impossible
        # and the run published a partial answer it could have completed.
        # Neither tool is required -- answering from what already ran is
        # just as legal.
        return Decision(
            state=RESULT_READY, tools=(TOOL_EXECUTE, TOOL_FINALIZE),
            require="",
            because=("a step failed after others succeeded and submissions "
                     "remain, so the analyst may correct it or answer from "
                     "the results it has"))

    if executed or answer_only:
        return Decision(
            state=RESULT_READY, tools=(TOOL_FINALIZE,),
            require=TOOL_FINALIZE,
            because=("a validated result exists, so the only thing left to "
                     "do with it is publish an answer"))

    if not analytical:
        # THE PRODUCT-HELP SURFACE IS NOT A DEAD END.
        #
        # H-LIVE-03 and H-LIVE-05. `analytical` is `envelope.classify`'s
        # verdict, read off the question's words before any model call, and
        # it can be wrong. It read
        #
        #   "wat is the toatl expsoure at defalt by secter this qtr"
        #
        # as non-analytical, because no measure in its lexicon survives
        # those typos; and it read "Which sectors are above the single-name
        # limit?" the same way, because that question names a policy concept
        # rather than a measure.
        #
        # This state then offered `inspect_product_knowledge` and
        # `finalize_response`, and on a question the product synopsis
        # already covers it offered `finalize_response` ALONE and required
        # it. So the first live UAT put the analyst on a surface with one
        # tool and no way to reach the book. It understood the typo-heavy
        # question exactly -- exposure at default by sector for 2026Q2,
        # mapped to `corp_facility_quarter.ead_sar_mn` and
        # `corp_borrower_quarter.sector`, no blocking ambiguity, "every term
        # in the question resolves cleanly" -- and then published
        # PRODUCT_HELP / unsupported and offered to proceed if the reader
        # said "go ahead". That was not a failure of autonomy. It was the
        # only move the surface allowed.
        #
        # Both `envelope.classify` and `IntentEnvelope.escalate_to_analysis`
        # already promise the turn widens on the analyst's declaration, and
        # SUBMITTING SQL IS THAT DECLARATION -- `_do_execute` adopts the
        # analytical allowance and escalates the envelope before it parses a
        # field. Withholding `execute_analysis` here is what made that
        # promise unreachable, because the flat intent fields an answer may
        # restate do not include `query_mode`, so `finalize_response` cannot
        # carry the declaration either.
        #
        # So `execute_analysis` is offered, and nothing is required. This
        # decides NOTHING about whether a question is analytical: the
        # judgement stays the analyst's, made against the catalogue index
        # and the canonical semantics the opening packet already carries on
        # every turn. No lexicon is consulted, no question is rewritten and
        # no SQL is suggested.
        #
        # The one-generation economy for a broad product question is
        # unchanged: it is `inspect_product_knowledge` being off the first
        # action, not a required tool. A product question still answers in
        # one generation, because answering is what the analyst does with
        # a product question.
        tools = ((TOOL_EXECUTE, TOOL_FINALIZE) if product_tool_withheld
                 else (TOOL_PRODUCT, TOOL_EXECUTE, TOOL_FINALIZE))
        return Decision(
            state=PRODUCT_HELP, tools=tools, require="",
            because=("read as a product question, and the book is still "
                     "reachable if it is not: the synopsis in the opening "
                     "context covers this question"
                     if product_tool_withheld else
                     "read as a product question that names detail beyond "
                     "the synopsis, and the book is still reachable if it "
                     "is not"))

    if ready.get("sufficient"):
        return Decision(
            state=READY_FOR_EXECUTION, tools=(TOOL_EXECUTE,),
            require=TOOL_EXECUTE,
            because=("every field fact this question needs is already "
                     "resolved in the opening packet"),
            detail={"resolved_measures": [
                m.get("term") for m in
                (ready.get("governed_measures_already_resolved") or [])][:8]})

    return Decision(
        state=NEEDS_METADATA, tools=(TOOL_INSPECT,), require=TOOL_INSPECT,
        because=("something this question names is not resolved in the "
                 "opening packet"),
        detail={"unresolved": (ready.get("terms_still_needing_a_decision")
                               or ready.get("values_still_ambiguous") or [])})


def after_catalog(previous: Decision, readiness: dict[str, Any] | None
                  ) -> Decision:
    """Where a run goes once it has read what it asked the catalogue for.

    NEEDS_METADATA is not a place a run stays. It read the field facts it
    said were missing; the next legal transition is to use them. A run that
    could ask the catalogue again and again is the run that died having
    read it twice and answered nothing.

    A MISSING FACT AND AN UNDECIDED READING ARE NOT THE SAME MISSING THING,
    and this transition used to treat them alike.

    `readiness` arrives here and was ignored. So a run the server had itself
    flagged -- `terms_still_needing_a_decision: [{"term": "exposure",
    "candidate_fields": ["ead_sar_mn", "limit_sar_mn", "drawn_sar_mn"]}] --
    was sent to the catalogue, which returned the same three columns because
    that is all it holds, and then landed here, where the only offered tool
    was `execute_analysis` and it was REQUIRED. `finalize_response` was not
    on the turn at all, so `disposition: "clarification"` was unreachable.

    The live M06 turn 1 stood exactly there. Its own intent records that it
    saw the ambiguity -- "exposure is not uniquely defined in this book (EAD,
    drawn balance or sanctioned limit)" -- and it executed anyway, because
    executing was the only legal move. It was not the analyst choosing
    silently. It was the only move the surface allowed, which is the
    H-LIVE-03 finding in a different state.

    So when a governed term is STILL undecided after the catalogue has been
    read, both tools are offered and NEITHER is required. This decides
    nothing about whether the question is ambiguous -- that judgement stays
    the analyst's, made against the same candidate fields the server has
    been carrying all along. What it removes is a gate that could only be
    passed by guessing.
    """
    if previous.state != NEEDS_METADATA:
        return previous
    ready = dict(readiness or {})
    undecided = (ready.get("terms_still_needing_a_decision")
                 or ready.get("values_still_ambiguous") or [])
    if undecided:
        return Decision(
            state=READY_FOR_EXECUTION, tools=(TOOL_EXECUTE, TOOL_FINALIZE),
            require="",
            because=("the catalogue has been read and a governed term this "
                     "question names is still undecided, so the run may "
                     "either analyse or put the choice to the reader"),
            detail={"after": NEEDS_METADATA, "still_undecided": undecided})
    return Decision(
        state=READY_FOR_EXECUTION, tools=(TOOL_EXECUTE,),
        require=TOOL_EXECUTE,
        because=("the field facts this question was missing have now been "
                 "read"),
        detail={"after": NEEDS_METADATA})


#: Tools that may be BATCHED alongside the required one, because they are
#: reads with no side effects and the contract already allows batching them.
#: `read_artifact` is here so a run that stored a large result can fetch a
#: page of it without a state change.
COMPANIONS: dict[str, tuple[str, ...]] = {
    RESULT_READY: (TOOL_READ,),
}


def offered(decision: Decision) -> tuple[str, ...]:
    """Every tool name on the request for this turn, in contract order."""
    names = list(decision.tools)
    for extra in COMPANIONS.get(decision.state, ()):
        if extra not in names:
            names.append(extra)
    order = (TOOL_INSPECT, TOOL_PRODUCT, TOOL_EXECUTE, TOOL_READ,
             TOOL_FINALIZE)
    return tuple(name for name in order if name in names)


__all__ = ["COMPANIONS", "Decision", "NEEDS_METADATA", "PRODUCT_HELP",
           "PUBLIC", "READY_FOR_EXECUTION", "RESULT_READY", "STATES",
           "after_catalog", "decide", "offered"]
