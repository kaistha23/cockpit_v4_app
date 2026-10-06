"""
The failure packet returned to Opus. Specification sections 7.6A and 8.

The rule this module exists to enforce
--------------------------------------
CreditProbe never repairs, rewrites, edits, patches, completes or substitutes
Opus-authored SQL or Python. This module is where that rule could most
plausibly be broken — it is the code that looks at a broken query and knows
what would fix it — and it does not break it.

`available_alternatives` reports fields that EXIST and are near the name that
did not. That is a catalogue fact. Which one is analytically right is not a
catalogue fact: `pd_pit_12m` and `pd_ttc_12m` are both real, and only the
question decides between them. So the packet offers both, with their meanings
and units, and says nothing about which to use. There is no code path here that
produces SQL.

What "full effective context" means
-----------------------------------
Section 8.1: a request id or a schema hash alone is NOT memory. Every repair
continuation carries the original question, the business request, the scope,
the complete catalogue, the coverage, the approved ownership decision, the
current plan, the exact failed code, the diagnostics, the results already
obtained, the approaches already tried, and the remaining budget.

The static half of that belongs ONCE in the assembled conversation, not
duplicated inside every error JSON — section 8.1 says so explicitly. So this
packet carries the failure and the deltas, and `context_attached` NAMES the
parts that must be present in the outbound request. `runtime` asserts each
named part is genuinely there before dispatching, and a test reads the
serialized request to prove it.
"""

from __future__ import annotations

from typing import Any

from backend.cockpit_agentic import catalog as catalog_mod
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic import sql as sql_mod
from backend.cockpit_agentic.contracts import (CatalogAlternative,
                                               ExecutionFailurePacket,
                                               ExecutionStep,
                                               ExecutionSubmission,
                                               INVALID_FILTER_VALUE,
                                               MISSING_SOURCE_DATA,
                                               OUT_OF_SCOPE_ACCESS,
                                               PERMISSION_DENIED,
                                               RESOURCE_LIMIT,
                                               SANDBOX_UNAVAILABLE,
                                               UNRESOLVED_FIELD,
                                               UNRESOLVED_RELATION,
                                               UNSAFE_OPERATION)
from backend.cockpit_agentic.ledger import Ledger

#: The parts of the effective context that must be present in the outbound
#: repair request. Named here, asserted by the runtime, and read back off the
#: serialized request by the tests.
REQUIRED_CONTEXT: tuple[str, ...] = (
    "original_and_normalized_request",
    "full_authorized_compact_catalogue",
    "thread_and_scope_context",
    "functionality_decision",
    "current_plan_and_attempt_history",
    "coverage_profile",
    "completed_step_results",
    "actual_remaining_budget_ledger",
)

#: Which relation a failing step was most likely reading, when the error does
#: not say. Used only to look up alternatives; never to rewrite anything.
def _likely_relation(code: str, session: sql_mod.Session) -> str:
    lowered = str(code or "").lower()
    best, position = "", len(lowered) + 1
    for relation in session.relations:
        found = lowered.find(relation)
        if found >= 0 and found < position:
            best, position = relation, found
    return best or F.FACILITY_QUARTER


def build(*, error: sql_mod.SqlRejected, step: ExecutionStep,
          submission: ExecutionSubmission, session: sql_mod.Session,
          ledger: Ledger, plan_id: str, request_id: str,
          completed_steps: list[str] | None = None,
          reusable_artifacts: list[str] | None = None,
          previous_attempts: list[dict[str, Any]] | None = None,
          partial_complete_for: list[str] | None = None,
          phase: str = "validation") -> ExecutionFailurePacket:
    """Turn a refusal into facts Opus can act on. No repair is produced."""
    catalog = session.catalog
    relation = error.relation or _likely_relation(step.code, session)

    alternatives: list[CatalogAlternative] = []
    available_values: dict[str, list[str]] = {}
    suggested = ""
    user_input_needed = False
    operator_fix = False

    if error.category == UNRESOLVED_FIELD and error.unresolved:
        try:
            alternatives = list(catalog.alternatives(relation,
                                                     error.unresolved))
        except Exception:                                   # noqa: BLE001
            alternatives = []
        # Only where the ambiguity is GENUINE. If the alternatives differ only
        # by a convention the user already stated, there is nothing to ask.
        names = {a.field_name for a in alternatives}
        if any(n.startswith("pd_pit") for n in names) and \
                any(n.startswith("pd_ttc") for n in names):
            suggested = ("If the request did not say which, ask whether the "
                         "user means the point-in-time or the "
                         "through-the-cycle PD, and the twelve-month or the "
                         "lifetime horizon. Do not substitute one silently.")

    elif error.category == UNRESOLVED_RELATION:
        alternatives = []
        available_values["relation"] = list(session.relations)

    elif error.category == INVALID_FILTER_VALUE and error.unresolved:
        available_values[error.unresolved] = sql_mod.filter_values(
            relation, error.unresolved, session)

    elif error.category in (OUT_OF_SCOPE_ACCESS, PERMISSION_DENIED):
        # Section 8.3: these fail closed. There is no workaround and looking
        # for one spends the remaining attempts on nothing.
        operator_fix = error.category == PERMISSION_DENIED

    elif error.category == SANDBOX_UNAVAILABLE:
        operator_fix = True

    elif error.category == MISSING_SOURCE_DATA:
        user_input_needed = True

    # Where a filter matched nothing, the permitted values are worth having
    # whatever the category, because "your syntax was fine and nothing matched"
    # is a different problem from "your syntax was wrong".
    for column in ("reporting_quarter", "sector_name", "ifrs9_stage",
                   "portfolio_id", "scenario_id"):
        if column in str(step.code) and column not in available_values:
            values = sql_mod.filter_values(relation, column, session, limit=25)
            if values:
                available_values[column] = values

    grains = {r: F.GRAIN[r] for r in session.relations if r in F.GRAIN}
    joins = {f"{j['left']} -> {j['right']}": list(j["on"]) for j in F.JOINS}

    coverage: dict[str, Any] = {}
    if error.unresolved:
        for candidate in alternatives[:4]:
            spec = F.find(relation, candidate.field_name)
            if spec is not None:
                coverage[candidate.field_name] = {
                    "availability": spec.availability,
                    "aggregation": spec.aggregation,
                    "unit": spec.unit}

    repairable = error.category not in (OUT_OF_SCOPE_ACCESS, PERMISSION_DENIED,
                                        UNSAFE_OPERATION)
    if error.category == RESOURCE_LIMIT:
        # Repairable, but only by a genuinely cheaper analysis -- and only if
        # anything is left to spend.
        repairable = ledger.submissions_remaining > 0

    return ExecutionFailurePacket(
        request_id=request_id, plan_id=plan_id,
        analysis_round=submission.analysis_round,
        submission_id=submission.submission_id,
        submission_number=submission.submission_number,
        failing_step_id=step.step_id, phase=phase, language=step.language,
        category=error.category, message=str(error),
        submitted_code=step.code, parameters=dict(step.parameters),
        unresolved_name=error.unresolved, invalid_value=error.detail,
        available_alternatives=alternatives,
        available_filter_values=available_values,
        relevant_grains=grains, valid_join_keys=joins,
        field_coverage=coverage,
        completed_steps=list(completed_steps or []),
        failed_steps=[step.step_id],
        reusable_artifacts=list(reusable_artifacts or []),
        previous_failed_approaches=list(previous_attempts or []),
        partial_results_complete_for=list(partial_complete_for or []),
        repairable=repairable, user_input_needed=user_input_needed,
        suggested_clarification=suggested, operator_fix_required=operator_fix,
        dataset_release_id=session.scope.dataset_release_id,
        duplicate_fingerprint=step.fingerprint,
        budget=ledger.budget_view(),
        permitted_next_actions=_permitted(ledger, repairable),
        context_attached=list(REQUIRED_CONTEXT))


def _permitted(ledger: Ledger, repairable: bool) -> list[str]:
    """What Opus may actually do next. Server-owned, not negotiable."""
    actions: list[str] = []
    stopped = ledger.may_continue()
    if stopped:
        return ["explain_and_stop"]
    if repairable and ledger.submissions_remaining > 0:
        actions.append("submit_repaired_code")
    if ledger.rounds_remaining > 0 and ledger.submissions_remaining > 0:
        actions.append("revise_the_analysis_plan")
    actions.append("ask_a_targeted_clarification")
    actions.append("explain_and_stop")
    return actions


def summarize_attempt(step: ExecutionStep, error: sql_mod.SqlRejected
                      ) -> dict[str, Any]:
    """A compact record of a failed approach, so it is not repeated.

    Deliberately not the whole query: the point is to let Opus recognise that
    it already tried this shape, not to spend context re-reading its own SQL.
    """
    return {
        "step_id": step.step_id,
        "category": error.category,
        "what_failed": str(error)[:220],
        "unresolved": error.unresolved,
        "fingerprint": step.fingerprint,
        "code_opening": " ".join(step.code.split())[:160],
    }


# ===================================================================
# The outbound audit: sixteen things the repair request must contain
# ===================================================================
#
# Section 8.1 says to inspect the SERIALIZED API request and prove the model
# really receives the context, and the owner's pre-UAT instruction lists what
# "the context" means item by item. Naming them in a document is not proof and
# neither is naming them in `context_attached`, which is CreditProbe asserting
# its own compliance. So the assembled request is read here, immediately before
# it is dispatched, and a missing item stops the request.
#
# Each item is checked by looking for content that could only be there if the
# item is there: the question's own words, a field name from the far end of the
# dictionary, the failed code itself. A reference, an id or a hash does not
# satisfy any of them -- which is the point.


class IncompleteRepairContext(RuntimeError):
    """The assembled repair request is missing part of the effective context.

    Raised before dispatch rather than after. Sending a repair request without
    the catalogue, or without the code that failed, asks Opus to author a fix
    for something it cannot see -- and whatever came back would look like a
    repair while being a guess.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(
            "The outbound repair request does not carry: "
            + ", ".join(f"{k} ({OUTBOUND_ITEMS[k]})" for k in missing))


#: The sixteen, in the order they were specified.
OUTBOUND_ITEMS: dict[str, str] = {
    "original_question": "the question exactly as the user asked it",
    "cleaned_question": "the faithful business request Sonnet produced",
    "thread_context": "the rolling summary and recent exchanges",
    "scope_and_filters": "the effective scope and the filters in force",
    "field_dictionary_and_grain": "the complete authorized catalogue, with "
                                  "grains",
    "coverage_and_missingness": "the measured coverage of the release",
    "functionality_decision": "the approved ownership decision",
    "analysis_plan": "the plan currently being executed",
    "failed_code": "the exact SQL or Python that failed",
    "bound_parameters": "the parameters it was bound with",
    "diagnostics": "the validator's or the runtime's own error",
    "completed_results": "the intermediate results already obtained",
    "previous_approaches": "what has already been tried and failed",
    "submissions_remaining": "how many execution submissions are left",
    "rounds_remaining": "how many analysis rounds are left",
    "budgets_remaining": "the remaining calls, time, tokens and spend",
}


def outbound_expectations(*, packet: ExecutionFailurePacket,
                          step: ExecutionStep,
                          context_payload: dict[str, Any]) -> dict[
                              str, list[str]]:
    """What must be findable in the serialized request, and for which item.

    Every probe is content, never a label. `"grain"` appearing as a key would
    satisfy nothing; the grain of a specific relation appearing does.
    """
    request = context_payload.get("A_request") or {}
    scope = context_payload.get("B_scope") or {}
    thread = context_payload.get("C_thread") or {}
    catalogue = context_payload.get("D_E_catalogue") or {}
    coverage = context_payload.get("F_coverage") or {}

    original = str(request.get("original_question") or "")
    cleaned = str(request.get("business_request")
                  or request.get("english_text") or "")

    expectations: dict[str, list[str]] = {
        "original_question": [original[:80]] if original else [],
        "cleaned_question": [cleaned[:60]] if cleaned else [],
        # A thread with nothing in it still has to be REPORTED as empty, or the
        # continuation cannot tell "no history" from "history withheld".
        "thread_context": [str(thread.get("rolling_summary")
                               or thread.get("status") or "thread")[:60]],
        "scope_and_filters": [str(scope.get("dataset_release_id") or "")],
        # Two fields from opposite ends of the dictionary, and a grain. A hash
        # of the catalogue satisfies none of them.
        "field_dictionary_and_grain": ["pd_ttc_lifetime", "total_haircut",
                                       F.GRAIN[F.FACILITY_QUARTER][:40]],
        "coverage_and_missingness": [str(coverage.get("dataset_release_id")
                                         or "coverage")[:40]],
        "functionality_decision": [str(packet.plan_id)],
        "analysis_plan": [str(packet.plan_id)],
        "failed_code": [" ".join(str(step.code).split())[:120]],
        "bound_parameters": ["parameters"],
        "diagnostics": [str(packet.category), str(packet.message)[:60]],
        "completed_results": ["completed_steps"],
        "previous_approaches": ["previous_failed_approaches"],
        "submissions_remaining": ["submissions_remaining"],
        "rounds_remaining": ["analysis_rounds_remaining"],
        "budgets_remaining": ["model_requests_remaining", "seconds_remaining",
                              "tokens_remaining", "spend"],
    }
    return {key: [p for p in probes if p]
            for key, probes in expectations.items()}


def _comparable(text: Any) -> str:
    """Collapse whitespace and drop escaping.

    The failure packet is a JSON string nested inside the request's own JSON,
    so a database error carrying double quotes arrives twice-escaped. Matching
    on the raw bytes would report the diagnostic as absent while it is sitting
    there in the request, which is the wrong kind of false alarm: it would
    stop a repair that had everything it needed.
    """
    return " ".join(str(text).replace("\\", "").split())


def audit_outbound(serialized: str,
                   expectations: dict[str, list[str]]) -> list[str]:
    """Which of the sixteen the request does not carry. Empty is the pass."""
    flattened = _comparable(serialized)
    missing: list[str] = []
    for key in OUTBOUND_ITEMS:
        probes = expectations.get(key) or []
        if not probes or not all(_comparable(p) in flattened for p in probes):
            missing.append(key)
    return missing


__all__ = ["IncompleteRepairContext", "OUTBOUND_ITEMS", "REQUIRED_CONTEXT",
           "audit_outbound", "build", "outbound_expectations",
           "summarize_attempt"]
