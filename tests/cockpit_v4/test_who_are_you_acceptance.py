"""
MODEL MOCK · REAL DATABASE/RUNNER.

The Section 21 acceptance for "Who are you?", asserted mechanically.

The live run cost two generations and 29.4 seconds because the first, correct
answer was refused over a null optional field. This file pins the shape of a
correct run so that regression is visible immediately: one generation, no
catalog call, no SQL, no Python, no analysis round, `finalize_response`
accepted on the first attempt, published once.
"""

from __future__ import annotations

import json

from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st

#: A product-help answer as a model would actually send it: the optional
#: fields left null, because there is no clarification and no referral.
PRODUCT_HELP_ANSWER = {
    "intent": {
        "query_mode": "PRODUCT_HELP", "owner": "COCKPIT",
        "understood_request": "The user is asking who I am.",
        "response_language": "en",
        "blocking_ambiguities": None, "resolved_assumptions": None,
        "canonical_mappings": None, "excluded_parts": None,
        "public_rationale": "Answering from the product knowledge in context.",
    },
    "disposition": "answer",
    "narrative": (
        "# CreditProbe AI\n\n"
        "CreditProbe is an intelligent credit-investigation layer for risk "
        "teams.\n\n"
        "## The problem it addresses\n\n"
        "Credit teams rarely lack information; the difficulty is connecting "
        "it quickly enough.\n\n"
        "**Cockpit** — interrogate the recorded credit book.\n\n"
        "**Early Warning** — identify emerging deterioration.\n"),
    "coverage": None, "numeric_claims": None, "evidence_refs": None,
    "tables": None, "charts": None, "limitations": None,
    "suggested_questions": None, "clarification_question": None,
    "clarification_options": None, "referral_owner": None,
    "referral_reason": None,
}


def test_who_are_you_costs_exactly_one_generation(drive, store_db):
    outcome, provider, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 1, (
        f"product help must finish in one generation; it took "
        f"{len(provider.sent)}. Two is the defect this test exists for.")

    budget = store_db.get_run(record.run_id).budget
    assert budget["generation_attempts"][0] == 1
    assert budget["catalog_calls"][0] == 0, "no metadata call is needed"
    assert budget["execution_submissions"][0] == 0, "no SQL, no Python"
    assert budget["analysis_rounds"][0] == 0
    assert budget["steps_attempted"][0] == 0
    assert budget["format_recoveries"][0] == 0
    assert budget["answer_corrections"][0] == 0, (
        "the answer was accepted on its first attempt")


def test_the_run_publishes_exactly_one_answer(drive, store_db):
    outcome, _, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])

    events = store_db.events_since(record.run_id)
    ready = [e for e in events if e.event_type == ev.ANSWER_READY]
    assert len(ready) == 1
    failures = [e for e in events if e.status in ("failed", "rejected")]
    assert not failures, (
        f"a correct first answer must not be rejected: "
        f"{[e.public_message for e in failures]}")
    assert store_db.get_run(record.run_id).final_response is not None


def test_the_trace_is_truthful_for_this_run(drive, store_db):
    """Every stage the panel will show is a stage the backend emitted."""
    outcome, _, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])

    events = store_db.events_since(record.run_id)
    types = [e.event_type for e in events]
    # `run.accepted` is emitted by intake (POST /runs), which this fixture
    # bypasses; `test_intake_returns_202_and_a_durable_run` covers it.
    for required in (ev.RUN_STARTED, ev.CONTEXT_READY, ev.MODEL_REQUESTED,
                     ev.MODEL_RESPONSE_RECEIVED, ev.MODEL_PARSED,
                     ev.INTENT_VALIDATED, ev.ANSWER_VALIDATED,
                     ev.ANSWER_READY):
        assert required in types, f"{required} is missing from the trace"

    # Nothing analytical happened, so no analytical stage may appear.
    stages = {e.stage for e in events}
    assert "catalog" not in stages
    assert "executing" not in stages

    # And every event carries a real elapsed time, so the panel can show one.
    assert events[-1].elapsed_ms >= 0
    assert all(e.public_message for e in events)


def test_the_answer_is_markdown_the_panel_can_render(drive):
    outcome, _, _ = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])
    narrative = outcome.response["narrative"]
    assert narrative.startswith("# "), "a heading survives validation"
    assert "**Cockpit**" in narrative, (
        "bold survives validation; the panel turns it into an element")


def test_a_product_help_answer_needs_no_evidence_binding(drive):
    """It makes no portfolio claim, so there is nothing to bind."""
    outcome, _, _ = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])
    assert outcome.state == st.COMPLETED
    assert outcome.response["numeric_claims"] == []
    assert outcome.response["executed"] is False


def test_the_context_carries_enough_product_knowledge_for_one_call(
        drive, store_db):
    """The synopsis is why this does not need a tool call first."""
    outcome, provider, _ = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])
    sent = provider.first_input_text()
    for fact in ("intelligent credit investigation layer",
                 "Detect", "Diagnose", "Decide", "Drive Alignment",
                 "Cockpit", "Early Warning Analysis", "What-If Analysis",
                 "Scorecard Validation", "Playbook", "Lenses",
                 "AI Project Planner"):
        assert fact in sent, (
            f"{fact!r} is not in the starting context, so this question "
            f"could not be answered in one generation")
    # And the deck's own content is NOT there. These strings exist only in
    # the knowledge pack's worked examples, which live behind the tool.
    for deck_only in ("Cedar Infrastructure", "Horizon Manufacturing",
                      "counterintuitive fitted effect",
                      "sequential revaluation bridge"):
        assert deck_only not in sent, (
            f"{deck_only!r} is deck detail and belongs behind "
            f"inspect_product_knowledge, not in every prompt")


def test_the_starting_context_stays_within_its_soft_target(drive):
    """Adding product knowledge must not bloat every request."""
    outcome, provider, _ = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", PRODUCT_HELP_ANSWER, "tu-1")])])
    approx_tokens = len(provider.first_input_text()) / 3.5
    assert approx_tokens < 12_000, (
        f"the first request is ~{approx_tokens:.0f} tokens; the Standard soft "
        f"target is 6,000 and this is telemetry, but an order of magnitude "
        f"over it means the deck leaked into the prompt")
