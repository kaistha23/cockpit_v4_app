"""
The explicit V4 state machine: top-level states, terminal outcomes, codes.

Two things kept deliberately separate
-------------------------------------
The STATE says where the run is. The ERROR CODE says what stopped it. V3
conflated them, so `STOPPED_OUTPUT_LIMIT` was both, and a UI that wanted to
render "did this succeed?" had to know every stop reason. Here `FAILED` is one
state and `OUTPUT_LIMIT` is one of the reasons a run can be in it, and the
mapping from V3's terminal envelopes is explicit in `V3_TERMINAL_MAP` so the
older UI keeps working without anyone reinterpreting a stop as a success.

Boundedness is a property of the edges, not of discipline: every edge that
returns to CONTEXT_READY consumes a generation attempt, an execution
submission, a format allowance, a correction allowance or elapsed time, and
none of those is replenished. `tests/cockpit_v4/test_state_machine.py` reads
this table and proves every cycle is finite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---- working states ----------------------------------------------------

ACCEPTED = "ACCEPTED"
CONTEXT_READY = "CONTEXT_READY"
MODEL_RUNNING = "MODEL_RUNNING"
ACTION_VALIDATING = "ACTION_VALIDATING"
TOOL_RUNNING = "TOOL_RUNNING"
FINAL_VALIDATING = "FINAL_VALIDATING"

WORKING_STATES: tuple[str, ...] = (
    ACCEPTED, CONTEXT_READY, MODEL_RUNNING, ACTION_VALIDATING, TOOL_RUNNING,
    FINAL_VALIDATING)

# ---- terminal states ---------------------------------------------------

COMPLETED = "COMPLETED"
PARTIAL = "PARTIAL"
WAITING_FOR_USER = "WAITING_FOR_USER"
REFERRED = "REFERRED"
UNSUPPORTED = "UNSUPPORTED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
INTERRUPTED = "INTERRUPTED"

TERMINAL_STATES: tuple[str, ...] = (
    COMPLETED, PARTIAL, WAITING_FOR_USER, REFERRED, UNSUPPORTED, FAILED,
    CANCELLED, EXPIRED, INTERRUPTED)

ALL_STATES: tuple[str, ...] = WORKING_STATES + TERMINAL_STATES

#: The terminal states that carry a validated user-facing response.
ANSWER_BEARING: frozenset[str] = frozenset(
    {COMPLETED, PARTIAL, WAITING_FOR_USER, REFERRED, UNSUPPORTED})

# ---- error codes -------------------------------------------------------

INPUT_CONTEXT_LIMIT = "INPUT_CONTEXT_LIMIT"
OUTPUT_LIMIT = "OUTPUT_LIMIT"
COST_LIMIT = "COST_LIMIT"
CALL_LIMIT = "CALL_LIMIT"
#: The run could not be given a usable ACTION, and the one re-ask for a
#: complete one was already spent.
#:
#: This is not a call limit and reporting it as one was untrue in a way that
#: cost a day: the live run that produced it had used 2 of 12 generations,
#: 9 of 24 provider attempts, $0.34 of $1.50 and 56 of 120 seconds. Nothing
#: was exhausted except the allowance for asking again after a response
#: arrived incomplete -- which is a different fact, with a different fix,
#: and it now has its own name.
ACTION_FORMAT_EXHAUSTED = "ACTION_FORMAT_EXHAUSTED"
#: The declarative plan could not be corrected within its own small,
#: pre-execution allowance.  This is not an execution limit: no SQL ran.
PLAN_FORMAT_EXHAUSTED = "PLAN_FORMAT_EXHAUSTED"
#: The same, for the turn that writes the answer. The analysis is preserved.
ANSWER_FORMAT_EXHAUSTED = "ANSWER_FORMAT_EXHAUSTED"
EXECUTION_LIMIT = "EXECUTION_LIMIT"
ROUND_LIMIT = "ROUND_LIMIT"
NO_PROGRESS = "NO_PROGRESS"
MODEL_CONFIGURATION_MISSING = "MODEL_CONFIGURATION_MISSING"
PROVIDER_CREDENTIAL_MISSING = "PROVIDER_CREDENTIAL_MISSING"
CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"
PROVIDER_AUTH = "PROVIDER_AUTH"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
#: The provider rejected the REQUEST, before any inference happened. Not
#: unavailability: the service answered, and what it said was that what
#: CreditProbe sent was malformed.
PROVIDER_REQUEST_INVALID = "PROVIDER_REQUEST_INVALID"
#: The same class, narrowed to the part a reader can act on: the tool schemas
#: CreditProbe published were not ones the provider accepts.
TOOL_SCHEMA_INVALID = "TOOL_SCHEMA_INVALID"
INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
SQL_VALIDATION = "SQL_VALIDATION"
SQL_RUNTIME = "SQL_RUNTIME"
PYTHON_UNAVAILABLE = "PYTHON_UNAVAILABLE"
SECURITY_DENIED = "SECURITY_DENIED"
ANSWER_VALIDATION = "ANSWER_VALIDATION"
STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
CANCELLED_BY_USER = "CANCELLED_BY_USER"
WORKER_LOST = "WORKER_LOST"

ERROR_CODES: tuple[str, ...] = (
    INPUT_CONTEXT_LIMIT, OUTPUT_LIMIT, COST_LIMIT, CALL_LIMIT,
    ACTION_FORMAT_EXHAUSTED, PLAN_FORMAT_EXHAUSTED, ANSWER_FORMAT_EXHAUSTED,
    EXECUTION_LIMIT, ROUND_LIMIT, NO_PROGRESS, MODEL_CONFIGURATION_MISSING,
    PROVIDER_CREDENTIAL_MISSING, CAPABILITY_UNVERIFIED, PROVIDER_AUTH,
    PROVIDER_UNAVAILABLE, PROVIDER_RATE_LIMIT, PROVIDER_REQUEST_INVALID,
    TOOL_SCHEMA_INVALID, INVALID_MODEL_OUTPUT,
    DATA_UNAVAILABLE, SQL_VALIDATION, SQL_RUNTIME, PYTHON_UNAVAILABLE,
    SECURITY_DENIED, ANSWER_VALIDATION, STORAGE_UNAVAILABLE, INTERNAL_ERROR,
    DEADLINE_EXPIRED, CANCELLED_BY_USER, WORKER_LOST)

#: Which failures a user may sensibly retry, and which are for an operator.
OPERATOR_CODES: frozenset[str] = frozenset({
    MODEL_CONFIGURATION_MISSING, PROVIDER_CREDENTIAL_MISSING,
    CAPABILITY_UNVERIFIED, PROVIDER_AUTH, PROVIDER_REQUEST_INVALID,
    TOOL_SCHEMA_INVALID, STORAGE_UNAVAILABLE,
    INTERNAL_ERROR, PYTHON_UNAVAILABLE})

#: V3's terminal envelopes, mapped explicitly so an existing UI keeps its
#: meaning. Nothing here turns a STOPPED_* envelope into a success.
V3_TERMINAL_MAP: dict[str, tuple[str, str]] = {
    "ANSWERED": (COMPLETED, ""),
    "PARTIAL": (PARTIAL, ""),
    "CLARIFICATION_REQUIRED": (WAITING_FOR_USER, ""),
    "REDIRECTED": (REFERRED, ""),
    "UNSUPPORTED": (UNSUPPORTED, ""),
    "CONTEXT_TOO_LARGE": (FAILED, INPUT_CONTEXT_LIMIT),
    "STOPPED_OUTPUT_LIMIT": (FAILED, OUTPUT_LIMIT),
    "STOPPED_COST": (FAILED, COST_LIMIT),
    "STOPPED_CALLS": (FAILED, CALL_LIMIT),
    "STOPPED_EXECUTIONS": (FAILED, EXECUTION_LIMIT),
    "STOPPED_ROUNDS": (FAILED, ROUND_LIMIT),
    "STOPPED_DEADLINE": (EXPIRED, DEADLINE_EXPIRED),
    "EXECUTION_FAILED": (FAILED, SQL_RUNTIME),
    "PROVIDER_ERROR": (FAILED, PROVIDER_UNAVAILABLE),
    "PROVIDER_CREDENTIAL_MISSING": (FAILED, PROVIDER_CREDENTIAL_MISSING),
    "MODEL_UNAVAILABLE": (FAILED, MODEL_CONFIGURATION_MISSING),
    "INTERNAL_ERROR": (FAILED, INTERNAL_ERROR),
}


@dataclass(frozen=True)
class Edge:
    """One permitted transition, with what causes it and what it costs.

    `consumes` is the important column. An edge back into CONTEXT_READY with
    an empty `consumes` would be an unbounded loop, and the state-machine test
    fails the build if one appears.
    """

    source: str
    event: str
    condition: str
    target: str
    consumes: str = ""
    side_effect: str = ""


TRANSITIONS: tuple[Edge, ...] = (
    Edge(ACCEPTED, "admission and lease taken",
         "durable store committed and initial context assembled",
         CONTEXT_READY, side_effect="pinned context + context.ready"),
    Edge(ACCEPTED, "preflight check failed",
         "capability, price, credential or release check failed",
         FAILED, side_effect="persist reason; nothing dispatched"),

    Edge(CONTEXT_READY, "generation dispatched",
         "capability, price and budget checks pass",
         MODEL_RUNNING, consumes="generation attempt + cost reservation",
         side_effect="reserve and dispatch exactly one attempt"),
    Edge(CONTEXT_READY, "no affordable attempt remains",
         "spend ceiling, call ledger or deadline exhausted",
         FAILED, side_effect="mechanical stop from the error record"),

    Edge(MODEL_RUNNING, "complete supported action received",
         "one parsed tool_use, arguments fully received",
         ACTION_VALIDATING, side_effect="parse + intent validation"),
    Edge(MODEL_RUNNING, "output truncated or malformed",
         "one format regeneration remains",
         CONTEXT_READY, consumes="format regeneration allowance",
         side_effect="roll back the incomplete assistant message"),
    Edge(MODEL_RUNNING, "transient transport failure",
         "one transport retry remains and time permits",
         CONTEXT_READY, consumes="transport retry allowance",
         side_effect="ledger preserved; same model"),
    Edge(MODEL_RUNNING, "refusal or nonrecoverable provider error",
         "no substitute is permitted",
         FAILED, side_effect="actual provider reason, no model switch"),

    Edge(ACTION_VALIDATING, "metadata or artifact read",
         "authorized and within call bounds",
         TOOL_RUNNING, consumes="catalog or artifact call allowance"),
    Edge(ACTION_VALIDATING, "execution submission accepted",
         "declared DATA_ANALYSIS + COCKPIT, batch statically valid",
         TOOL_RUNNING, consumes="execution submission + round"),
    Edge(ACTION_VALIDATING, "execution submission rejected",
         "schema, safety or budget rejection",
         CONTEXT_READY, consumes="execution submission",
         side_effect="matching tool error to the analyst"),
    Edge(ACTION_VALIDATING, "finalization requested",
         "terminal contract carried",
         FINAL_VALIDATING, side_effect="no new model call"),
    Edge(ACTION_VALIDATING, "invalid action with recovery available",
         "format allowance remains",
         CONTEXT_READY, consumes="format regeneration allowance"),
    Edge(ACTION_VALIDATING, "forbidden or unrecoverable action",
         "ownership, security or configuration failure",
         FAILED, side_effect="security/config reason"),

    Edge(TOOL_RUNNING, "tool completed",
         "results persisted as artifacts",
         CONTEXT_READY, consumes="the call allowance already taken",
         side_effect="matching tool_result appended in order"),
    Edge(TOOL_RUNNING, "repairable execution failure",
         "limits permit another analyst turn",
         CONTEXT_READY, consumes="the submission already taken",
         side_effect="diagnostic to the analyst; never an application repair"),
    Edge(TOOL_RUNNING, "terminal tool, budget or security failure",
         "no further work is permitted",
         FAILED, side_effect="preserve only verified partial evidence"),
    Edge(TOOL_RUNNING, "verified partial evidence only",
         "deadline or budget ended the work after some steps succeeded",
         PARTIAL, side_effect="partial artifacts preserved"),

    Edge(FINAL_VALIDATING, "answer accepted",
         "evidence, units and coverage check out",
         COMPLETED, side_effect="atomic answer + answer.ready"),
    Edge(FINAL_VALIDATING, "partial answer accepted",
         "some subquestions unsupported",
         PARTIAL, side_effect="atomic answer + coverage map"),
    Edge(FINAL_VALIDATING, "clarification accepted",
         "the analyst needs the user",
         WAITING_FOR_USER, side_effect="run settles; no process waits"),
    Edge(FINAL_VALIDATING, "referral accepted",
         "another functionality owns it",
         REFERRED, side_effect="configured destination only"),
    Edge(FINAL_VALIDATING, "unsupported accepted",
         "nothing configured owns it",
         UNSUPPORTED),
    Edge(FINAL_VALIDATING, "answer invalid and correction available",
         "one answer-only correction remains",
         CONTEXT_READY, consumes="answer correction allowance",
         side_effect="answer-only permissions; execution disabled"),
    Edge(FINAL_VALIDATING, "answer invalid again",
         "no correction remains or none is affordable",
         FAILED, side_effect="explicit failure, never a fabricated narrative"),

    Edge("*", "cancellation wins the atomic race", "any nonterminal state",
         CANCELLED, side_effect="stop new work; terminate own children"),
    Edge("*", "run deadline expired", "any nonterminal state",
         EXPIRED, side_effect="cancel active work; record last step"),
    Edge("*", "worker lease lost", "any nonterminal state",
         INTERRUPTED, side_effect="fence late writes; preserve evidence"),
)


def edges_from(state: str) -> tuple[Edge, ...]:
    return tuple(e for e in TRANSITIONS if e.source in (state, "*"))


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def to_dict() -> dict[str, Any]:
    return {
        "working_states": list(WORKING_STATES),
        "terminal_states": list(TERMINAL_STATES),
        "error_codes": list(ERROR_CODES),
        "edges": [{"source": e.source, "event": e.event,
                   "condition": e.condition, "target": e.target,
                   "consumes": e.consumes, "side_effect": e.side_effect}
                  for e in TRANSITIONS],
    }


__all__ = ["ACCEPTED", "ACTION_VALIDATING", "ALL_STATES", "ANSWER_BEARING",
           "ANSWER_VALIDATION", "CANCELLED", "CANCELLED_BY_USER",
           "ACTION_FORMAT_EXHAUSTED", "PLAN_FORMAT_EXHAUSTED", "ANSWER_FORMAT_EXHAUSTED",
           "CALL_LIMIT", "CAPABILITY_UNVERIFIED", "COMPLETED",
           "CONTEXT_READY", "COST_LIMIT", "DATA_UNAVAILABLE",
           "DEADLINE_EXPIRED", "ERROR_CODES", "EXECUTION_LIMIT", "EXPIRED",
           "Edge", "FAILED", "FINAL_VALIDATING", "INPUT_CONTEXT_LIMIT",
           "INTERNAL_ERROR", "INTERRUPTED", "INVALID_MODEL_OUTPUT",
           "MODEL_CONFIGURATION_MISSING", "MODEL_RUNNING", "NO_PROGRESS",
           "OPERATOR_CODES", "OUTPUT_LIMIT", "PARTIAL", "PROVIDER_AUTH",
           "PROVIDER_REQUEST_INVALID", "TOOL_SCHEMA_INVALID",
           "PROVIDER_CREDENTIAL_MISSING", "PROVIDER_RATE_LIMIT",
           "PROVIDER_UNAVAILABLE", "PYTHON_UNAVAILABLE", "REFERRED",
           "ROUND_LIMIT", "SECURITY_DENIED", "SQL_RUNTIME", "SQL_VALIDATION",
           "STORAGE_UNAVAILABLE", "TERMINAL_STATES", "TOOL_RUNNING",
           "TRANSITIONS", "UNSUPPORTED", "V3_TERMINAL_MAP",
           "WAITING_FOR_USER", "WORKER_LOST", "WORKING_STATES", "edges_from",
           "is_terminal", "to_dict"]
