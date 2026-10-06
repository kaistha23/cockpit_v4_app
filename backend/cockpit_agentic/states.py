"""
The server-owned state machine, as an explicit transition registry.

The model requests transitions; this module alone approves them. A response
cannot set its own attempts remaining, bypass a prior refusal, reopen a
stopped request or acquire another module's data -- because none of those are
expressible here. `Machine.advance` is the only way a request changes state,
it checks the edge against `TRANSITIONS`, and a terminal state has no
outgoing edges at all.

Why a registry rather than an adjacency list
--------------------------------------------
An adjacency list says a transition is permitted. It does not say what causes
it, under what condition, or what it costs -- so a reviewer asking "can this
request loop forever?" has to reconstruct the answer from the runtime. The
registry below carries EVENT, CONDITION and SIDE_EFFECT on every edge, and the
side effect is where boundedness lives: each edge that returns to PLANNING
consumes something that cannot be replenished, so every cycle is finite by
construction rather than by discipline.

`tests/cockpit_agentic/test_state_machine.py` reads this table and proves the
properties that matter: every non-terminal state has an exit, every declared
edge names a real state, every path terminates, and no cycle is unbounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic.credential import COCKPIT_CREDENTIAL_VAR

# ---- working states ---------------------------------------------------

RECEIVED = "RECEIVED"
NORMALIZING_1 = "NORMALIZING_1"
NORMALIZING_2 = "NORMALIZING_2"
BUILDING_CONTEXT = "BUILDING_CONTEXT"
FUNCTIONALITY_ASSESSMENT = "FUNCTIONALITY_ASSESSMENT"
PLANNING = "PLANNING"
VALIDATING = "VALIDATING"
EXECUTING = "EXECUTING"
REVIEWING = "REVIEWING"
ANSWERING = "ANSWERING"
#: The final-answer evidence check, and the one rewrite it may ask for.
#: A state of its own because the rewrite is bounded at one and a bound needs
#: somewhere to be counted.
ANSWER_VALIDATION = "ANSWER_VALIDATION"
SUMMARIZING = "SUMMARIZING"

WORKING: tuple[str, ...] = (
    RECEIVED, NORMALIZING_1, NORMALIZING_2, BUILDING_CONTEXT,
    FUNCTIONALITY_ASSESSMENT, PLANNING, VALIDATING, EXECUTING, REVIEWING,
    ANSWERING, ANSWER_VALIDATION, SUMMARIZING)

# ---- terminal states --------------------------------------------------
#
# Every request settles on exactly one of these. They are grouped by what a
# reader would do about them, because "the request stopped" is not actionable
# and "the request stopped because it ran out of execution submissions" is.

#: Delivered.
COMPLETED = "COMPLETED"
PARTIAL = "PARTIAL"

#: Answered, but not by the Cockpit.
REDIRECTED = "REDIRECTED"
UNSUPPORTED = "UNSUPPORTED"

#: The request is over and the ball is with the user. The reply is a NEW
#: request in the same thread; this one does not resume.
WAITING_FOR_USER = "WAITING_FOR_USER"

#: A guardrail was reached. One state per guardrail, because the remedy
#: differs: more time will not help a request that used five submissions, and
#: a simpler question will not help one that hit the spend ceiling.
STOPPED_EXECUTION_LIMIT = "STOPPED_EXECUTION_LIMIT"
STOPPED_ANALYSIS_LIMIT = "STOPPED_ANALYSIS_LIMIT"
STOPPED_TOKEN_LIMIT = "STOPPED_TOKEN_LIMIT"
STOPPED_COST_LIMIT = "STOPPED_COST_LIMIT"
STOPPED_TIME_LIMIT = "STOPPED_TIME_LIMIT"
STOPPED_SECURITY = "STOPPED_SECURITY"

#: The environment cannot serve the request.
MODEL_CONFIGURATION_MISSING = "MODEL_CONFIGURATION_MISSING"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
#: No Cockpit Anthropic credential. Its own state rather than one of the two
#: above: those mean nobody said WHICH MODEL should answer, this means nobody
#: said WHICH ACCOUNT pays for it, and an operator sent to the wrong variable
#: is an operator who does not fix it.
PROVIDER_CREDENTIAL_MISSING = "PROVIDER_CREDENTIAL_MISSING"
PROVIDER_ERROR = "PROVIDER_ERROR"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
#: The model's REPLY would not fit the output allowance, twice. The mirror of
#: CONTEXT_TOO_LARGE, which is about what goes in. Its own state because the
#: remedy is a different one: the packet is not too big, the response was, and
#: an operator sent to look at the input cap is an operator who does not fix it.
STOPPED_OUTPUT_LIMIT = "STOPPED_OUTPUT_LIMIT"

#: The data could not answer it, or the attempts could not.
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
EXECUTION_FAILED = "EXECUTION_FAILED"

#: The user stopped it, or this application broke.
CANCELLED = "CANCELLED"
INTERNAL_ERROR = "INTERNAL_ERROR"

TERMINAL: tuple[str, ...] = (
    COMPLETED, PARTIAL, REDIRECTED, UNSUPPORTED, WAITING_FOR_USER,
    STOPPED_EXECUTION_LIMIT, STOPPED_ANALYSIS_LIMIT, STOPPED_TOKEN_LIMIT,
    STOPPED_COST_LIMIT, STOPPED_TIME_LIMIT, STOPPED_SECURITY,
    STOPPED_OUTPUT_LIMIT,
    MODEL_CONFIGURATION_MISSING, MODEL_UNAVAILABLE,
    PROVIDER_CREDENTIAL_MISSING, PROVIDER_ERROR, DATA_UNAVAILABLE, CONTEXT_TOO_LARGE, INSUFFICIENT_DATA, EXECUTION_FAILED,
    CANCELLED, INTERNAL_ERROR)

ALL_STATES: tuple[str, ...] = WORKING + TERMINAL

#: Every guardrail stop, so a caller can ask "did a limit end this?" without
#: enumerating them and missing one.
STOPPED_BY_GUARDRAIL = frozenset({
    STOPPED_EXECUTION_LIMIT, STOPPED_ANALYSIS_LIMIT, STOPPED_TOKEN_LIMIT,
    STOPPED_COST_LIMIT, STOPPED_TIME_LIMIT, STOPPED_SECURITY,
    STOPPED_OUTPUT_LIMIT})

#: A stop that happened before any analysis was possible, versus one that
#: happened with findings in hand. The renderer says different things.
STOPPED_WITHOUT_ANSWER = frozenset({
    REDIRECTED, WAITING_FOR_USER, INSUFFICIENT_DATA, EXECUTION_FAILED,
    UNSUPPORTED, CONTEXT_TOO_LARGE, CANCELLED, PROVIDER_ERROR,
    MODEL_CONFIGURATION_MISSING, MODEL_UNAVAILABLE,
    PROVIDER_CREDENTIAL_MISSING, DATA_UNAVAILABLE, INTERNAL_ERROR}) | STOPPED_BY_GUARDRAIL


# ---- the registry -----------------------------------------------------

@dataclass(frozen=True)
class Edge:
    """One permitted transition, and why it exists.

    `consumes` is the load-bearing field. An edge that returns to an earlier
    working state must consume something finite, or the graph has a cycle
    nothing closes. The test suite reads this and refuses an unconsumed
    back-edge.
    """

    state: str
    event: str
    condition: str
    next_state: str
    side_effect: str = ""
    #: The finite resource this edge spends: "execution_submission",
    #: "analysis_round", "answer_rewrite", or "" for edges that move forward.
    consumes: str = ""

    @property
    def terminal(self) -> bool:
        return self.next_state in TERMINAL

    @property
    def backward(self) -> bool:
        """True when the edge returns to a state already visited in the loop."""
        return (self.next_state in WORKING
                and WORKING.index(self.next_state) <= WORKING.index(self.state))

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "event": self.event,
                "condition": self.condition, "next_state": self.next_state,
                "side_effect": self.side_effect, "consumes": self.consumes,
                "terminal": self.terminal}


#: Reachable from EVERY working state, because each can happen at any moment:
#: the deadline expires, the user cancels, a budget preflight refuses the next
#: call, the provider fails, the models are unconfigured, the packet will not
#: fit, or this application breaks.
def _always(state: str) -> tuple[Edge, ...]:
    return (
        Edge(state, "deadline reached", "clock past the request deadline",
             STOPPED_TIME_LIMIT, "the stop names the deadline"),
        Edge(state, "user cancelled", "cancel flag set on the ledger",
             CANCELLED, "in-flight work is stopped; usage is retained"),
        Edge(state, "token ceiling reached", "cumulative input would exceed it",
             STOPPED_TOKEN_LIMIT, "the stop names the ceiling"),
        Edge(state, "spend ceiling reached", "the next call would exceed it",
             STOPPED_COST_LIMIT, "the stop names the ceiling"),
        Edge(state, "model call ceiling reached", "no provider calls remain",
             STOPPED_EXECUTION_LIMIT, "the stop names what was tried"),
        Edge(state, "provider failed", "the provider raised",
             PROVIDER_ERROR, "no deterministic substitute is produced"),
        Edge(state, "model roles unconfigured", "either role is unset",
             MODEL_CONFIGURATION_MISSING, "the stop names the variable"),
        Edge(state, "no Cockpit provider credential",
             f"{COCKPIT_CREDENTIAL_VAR} is unset or empty",
             PROVIDER_CREDENTIAL_MISSING,
             "the stop names the variable and never the value; no fallback to "
             "ANTHROPIC_API_KEY or any other credential"),
        Edge(state, "model refused by provider", "the provider will not serve it",
             MODEL_UNAVAILABLE, "the stop names the id"),
        Edge(state, "packet will not fit", "measured above the input cap",
             CONTEXT_TOO_LARGE, "nothing is sent"),
        Edge(state, "the reply would not fit twice",
             "the model's response reached the output allowance, and the one "
             "permitted compact regeneration did too",
             STOPPED_OUTPUT_LIMIT,
             "the partial response is discarded, not executed; no execution "
             "submission is consumed and this application writes no "
             "replacement"),
        Edge(state, "pinned release unavailable", "the release cannot be read",
             DATA_UNAVAILABLE, "no silent switch to another release"),
        Edge(state, "forbidden operation attempted",
             "the engine or sandbox refused a boundary violation",
             STOPPED_SECURITY, "the attempt is recorded; nothing runs"),
        Edge(state, "unexpected application failure", "an unhandled exception",
             INTERNAL_ERROR, "stage and error id recorded; no secrets, no "
                             "stack trace, no fallback"),
    )


_EDGES: tuple[Edge, ...] = (
    Edge(RECEIVED, "request accepted", "authenticated and authorized",
         NORMALIZING_1, "ledger opened; scope, release and deadline pinned"),
    Edge(RECEIVED, "authorization refused", "principal may not read this scope",
         STOPPED_SECURITY, "nothing is read; the refusal is recorded"),

    Edge(NORMALIZING_1, "pass 1 returned", "the question was cleaned",
         NORMALIZING_2),
    Edge(NORMALIZING_1, "pass 1 failed", "the call raised or returned nothing",
         NORMALIZING_2,
         "proceeds on the preserved raw text with the failure flagged; intent "
         "is never silently rewritten"),
    Edge(NORMALIZING_1, "cannot clean safely",
         "meaning would change to proceed", WAITING_FOR_USER,
         "a targeted clarification; this request closes"),

    Edge(NORMALIZING_2, "pass 2 returned", "a business request was produced",
         BUILDING_CONTEXT),
    Edge(NORMALIZING_2, "cannot understand safely", "the request is ambiguous",
         WAITING_FOR_USER, "a targeted clarification; this request closes"),

    Edge(BUILDING_CONTEXT, "gate packet assembled",
         "the light stage-A packet fits the input cap",
         FUNCTIONALITY_ASSESSMENT,
         "the question, the thread, the functionality registry and the domain "
         "in OUTLINE; no field dictionary, no coverage table, no sample rows "
         "and no execution contract"),

    # The gate is first, and it is a real fork. Everything that is not
    # DATA_ANALYSIS+COCKPIT leaves without touching the data -- and without
    # the complete dictionary ever being assembled for it.
    Edge(FUNCTIONALITY_ASSESSMENT, "gate returned",
         "query_mode=DATA_ANALYSIS and owner=COCKPIT and the score test passes",
         PLANNING,
         "the full stage-B analytical packet is built -- this is the ONLY "
         "edge that builds it -- and the first analysis round is consumed",
         "analysis_round"),
    Edge(FUNCTIONALITY_ASSESSMENT, "gate returned",
         "query_mode is PRODUCT_HELP or THEORY_CONCEPT", ANSWER_VALIDATION,
         "Opus answered in the gate turn, from the gate packet; zero "
         "submissions, zero rounds, and the field dictionary was never "
         "assembled"),
    Edge(FUNCTIONALITY_ASSESSMENT, "gate returned",
         "owner is another functionality", REDIRECTED,
         "a referral with a configured destination; zero execution"),
    Edge(FUNCTIONALITY_ASSESSMENT, "gate returned",
         "query_mode=CLARIFICATION_REQUIRED, or the score test fails",
         WAITING_FOR_USER, "a targeted question; this request closes"),
    Edge(FUNCTIONALITY_ASSESSMENT, "gate returned",
         "query_mode=UNSUPPORTED, including current external information",
         UNSUPPORTED, "the boundary is explained; nothing is fabricated"),

    Edge(PLANNING, "Opus submitted candidate code", "submissions remain",
         VALIDATING, "one execution submission is consumed",
         "execution_submission"),
    Edge(PLANNING, "Opus chose to answer without executing",
         "the plan needs no data", ANSWER_VALIDATION),
    Edge(PLANNING, "Opus asked for clarification", "the request is ambiguous",
         WAITING_FOR_USER, "this request closes"),
    Edge(PLANNING, "Opus explained and stopped",
         "the data cannot answer it", INSUFFICIENT_DATA),
    Edge(PLANNING, "the repair could not be dispatched",
         "the effective context could not be assembled, or the failure is "
         "not repairable", EXECUTION_FAILED,
         "an application defect is reported as one; no repair request is sent "
         "that Opus could not act on"),
    Edge(PLANNING, "submissions exhausted", "five have been consumed",
         STOPPED_EXECUTION_LIMIT, "Opus explains what was tried"),
    Edge(PLANNING, "rounds exhausted", "three have been consumed",
         STOPPED_ANALYSIS_LIMIT, "what was established is reported"),

    Edge(VALIDATING, "validation passed", "the candidate is safe and resolvable",
         EXECUTING),
    Edge(VALIDATING, "validation failed, repairable",
         "the diagnostic goes back to Opus and submissions remain", PLANNING,
         "CreditProbe authors nothing; Opus writes the next candidate"),
    Edge(VALIDATING, "validation failed, not repairable",
         "out of scope or permission denied", EXECUTION_FAILED,
         "fails closed; looking for a workaround would spend attempts on "
         "nothing"),
    Edge(VALIDATING, "duplicate candidate",
         "the code, parameters and release hash to a prior failure", PLANNING,
         "the submission is consumed and the code is NOT run again"),
    Edge(VALIDATING, "forbidden operation", "the candidate reaches outside the "
         "authorized domain", STOPPED_SECURITY),
    Edge(VALIDATING, "submissions exhausted", "five have been consumed",
         STOPPED_EXECUTION_LIMIT),

    Edge(EXECUTING, "execution succeeded", "results were produced", REVIEWING),
    Edge(EXECUTING, "execution failed, repairable",
         "the runtime diagnostic goes back to Opus and submissions remain",
         PLANNING, "CreditProbe authors nothing"),
    Edge(EXECUTING, "execution failed, not repairable",
         "no attempts remain or the failure is closed", EXECUTION_FAILED),
    Edge(EXECUTING, "sandbox refused the code",
         "the isolation boundary was reached", STOPPED_SECURITY),

    Edge(REVIEWING, "SUFFICIENT", "every subquestion is answered",
         ANSWERING),
    Edge(REVIEWING, "NEEDS_FURTHER_ANALYSIS",
         "the method must materially change and rounds remain", PLANNING,
         "one analysis round is consumed", "analysis_round"),
    Edge(REVIEWING, "NEEDS_FURTHER_ANALYSIS", "no rounds remain",
         STOPPED_ANALYSIS_LIMIT, "what was established is reported"),
    Edge(REVIEWING, "NEEDS_USER_CLARIFICATION", "the answer depends on a choice "
         "only the user can make", WAITING_FOR_USER),
    Edge(REVIEWING, "MISSING_DATA", "the domain does not hold it",
         INSUFFICIENT_DATA),
    Edge(REVIEWING, "MISSING_DATA", "partial findings are supported", PARTIAL),
    Edge(REVIEWING, "OUT_OF_SCOPE", "another functionality owns it", REDIRECTED),
    Edge(REVIEWING, "BUDGET_STOP", "a guardrail was reached",
         STOPPED_EXECUTION_LIMIT),

    Edge(ANSWERING, "Opus returned the final answer", "always",
         ANSWER_VALIDATION),

    Edge(ANSWER_VALIDATION, "validation passed", "every claim traces to "
         "evidence and the envelope is well formed", SUMMARIZING),
    Edge(ANSWER_VALIDATION, "validation failed",
         "no rewrite has been used yet", ANSWERING,
         "the ONE permitted answer-only rewrite; it cannot execute, cannot "
         "open a round and cannot reset a counter", "answer_rewrite"),
    Edge(ANSWER_VALIDATION, "validation failed again",
         "the rewrite has been used", PARTIAL,
         "only the safely supported content is rendered, with a limitation "
         "saying what was removed"),
    Edge(ANSWER_VALIDATION, "domain leak detected",
         "the answer names data outside the authorized domain",
         STOPPED_SECURITY),

    Edge(SUMMARIZING, "summary updated", "the call succeeded", COMPLETED),
    Edge(SUMMARIZING, "summary failed", "the call raised", COMPLETED,
         "the delivered answer stands; the turn is marked unsummarized and "
         "the last valid summary is retained. No retry loop"),
    Edge(SUMMARIZING, "partial answer summarized", "the answer was partial",
         PARTIAL),
    Edge(SUMMARIZING, "referral summarized", "the turn was a referral",
         REDIRECTED),
    Edge(SUMMARIZING, "clarification summarized", "the turn asked a question",
         WAITING_FOR_USER),
    Edge(SUMMARIZING, "stop summarized", "the turn stopped", UNSUPPORTED),
    Edge(SUMMARIZING, "stop summarized", "the data did not support it",
         INSUFFICIENT_DATA),
    Edge(SUMMARIZING, "stop summarized", "execution failed", EXECUTION_FAILED),
)

EDGES: tuple[Edge, ...] = _EDGES + tuple(
    edge for state in WORKING
    # SUMMARIZING runs after the answer already exists. A guardrail reached
    # while saving a summary must not take the answer away, so the always-on
    # stops do not apply there -- section 32.
    if state != SUMMARIZING
    for edge in _always(state))

#: The adjacency view, derived. Nothing writes it by hand, so it cannot drift
#: from the registry above.
TRANSITIONS: dict[str, tuple[str, ...]] = {}
for _edge in EDGES:
    _seen = TRANSITIONS.setdefault(_edge.state, ())
    if _edge.next_state not in _seen:
        TRANSITIONS[_edge.state] = _seen + (_edge.next_state,)
for _terminal in TERMINAL:
    TRANSITIONS.setdefault(_terminal, ())


def edges_from(state: str) -> tuple[Edge, ...]:
    return tuple(e for e in EDGES if e.state == state)


def registry() -> list[dict[str, Any]]:
    """The table, for the documentation build and the tests."""
    return [edge.to_dict() for edge in EDGES]


class IllegalTransition(RuntimeError):
    """A transition the server does not permit. Raised, never logged past."""


@dataclass
class Machine:
    """One request's state, and the only thing that may change it."""

    state: str = RECEIVED
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in ALL_STATES:
            raise IllegalTransition(f"{self.state!r} is not a state")
        if not self.history:
            self.history.append({"state": self.state, "why": "request received"})

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL

    def may(self, target: str) -> bool:
        return target in TRANSITIONS.get(self.state, ())

    def advance(self, target: str, why: str = "") -> str:
        """Move to `target`, or refuse. The single write path."""
        if target not in ALL_STATES:
            raise IllegalTransition(f"{target!r} is not a state")
        if self.finished:
            raise IllegalTransition(
                f"the request is already {self.state}; a stopped request is "
                f"not reopened by a further transition to {target}")
        if not self.may(target):
            raise IllegalTransition(
                f"{self.state} -> {target} is not a permitted transition")
        self.state = target
        self.history.append({"state": target, "why": why})
        return target

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "finished": self.finished,
                "history": list(self.history)}


#: What the user is told each state is doing. Section 11: progress messages
#: identify actual work, and never claim completion while work is pending.
PROGRESS: dict[str, str] = {
    RECEIVED: "Received your question",
    NORMALIZING_1: "Reading the question",
    NORMALIZING_2: "Understanding what is being asked",
    BUILDING_CONTEXT: "Gathering the Cockpit data catalogue",
    FUNCTIONALITY_ASSESSMENT: "Checking this is a Cockpit question",
    PLANNING: "Planning the analysis",
    VALIDATING: "Checking the analysis against the data",
    EXECUTING: "Executing submission {submission} of {submission_max}",
    REVIEWING: "Reviewing the results, round {round} of {round_max}",
    ANSWERING: "Preparing the answer",
    ANSWER_VALIDATION: "Checking every figure against the results",
    SUMMARIZING: "Saving the thread summary",
}


def progress(state: str, **counters: Any) -> str:
    template = PROGRESS.get(state, state.replace("_", " ").capitalize())
    try:
        return template.format(**counters)
    except (KeyError, IndexError):
        return template


__all__ = [
    "ALL_STATES", "ANSWERING", "ANSWER_VALIDATION", "BUILDING_CONTEXT",
    "CANCELLED", "COMPLETED", "CONTEXT_TOO_LARGE", "DATA_UNAVAILABLE",
    "EDGES", "EXECUTING", "EXECUTION_FAILED", "Edge",
    "FUNCTIONALITY_ASSESSMENT", "INSUFFICIENT_DATA", "INTERNAL_ERROR",
    "IllegalTransition", "MODEL_CONFIGURATION_MISSING", "MODEL_UNAVAILABLE",
    "Machine", "NORMALIZING_1", "NORMALIZING_2", "PARTIAL", "PLANNING",
    "PROGRESS", "PROVIDER_ERROR", "RECEIVED", "REDIRECTED", "REVIEWING",
    "STOPPED_ANALYSIS_LIMIT", "STOPPED_BY_GUARDRAIL", "STOPPED_COST_LIMIT",
    "STOPPED_EXECUTION_LIMIT", "STOPPED_OUTPUT_LIMIT", "STOPPED_SECURITY",
    "STOPPED_TIME_LIMIT",
    "STOPPED_TOKEN_LIMIT", "STOPPED_WITHOUT_ANSWER", "SUMMARIZING",
    "TERMINAL", "TRANSITIONS", "UNSUPPORTED", "VALIDATING",
    "WAITING_FOR_USER", "WORKING", "edges_from", "progress", "registry"]
