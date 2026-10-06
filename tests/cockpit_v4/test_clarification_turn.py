"""
The run can put a question back, instead of guessing and answering.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

The defect this exists for
--------------------------
A live Corporate run was asked:

    compare between PD, LGD & CCF and tell me what is most dominant
    on this quarter's ECL change

Every term in that question resolves. The METHOD does not: attribution can
be sequential, in which case the order decides which factor "dominates", or
it can be a symmetric allocation that does not depend on order. Those give
different rankings from the same rows. The run recorded

    Assumed: attribution is sequential (PD first, then LGD, then EAD)

and published a ranking built on a choice nobody asked for.

It was not the model being careless. Every part of the machine pushed it
there:

  * `blocking_ambiguities` stops execution and nothing else;
  * a run that is READY_FOR_EXECUTION is offered `execute_analysis` and
    REQUIRED to call it, so a refused submission comes straight back to the
    same compelled call;
  * the refusal itself advised that a resolution "belongs in
    resolved_assumptions ... which do not stop execution".

Guessing was the only move left. Meanwhile `disposition: "clarification"`
existed in the contract, mapped to WAITING_FOR_USER, and rendered in the
reader's panel as clickable options -- complete, end to end, and
unreachable.

What is pinned here:

  a declared ambiguity has somewhere to go -> NEEDS_CLARIFICATION
  the gate stops demanding execution       -> finalize is the only tool
  the refusal stops advising a guess       -> it says to ask
  the run settles as a question            -> WAITING_FOR_USER, with options
  a question with one reading is untouched -> no new round trip
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import action_state as acts
from backend.cockpit_v4 import states as st

#: The reading that changes the answer. Not a term in any catalogue.
THE_AMBIGUITY = ("attribution across PD, LGD and CCF can be sequential "
                 "(order decides the ranking) or symmetric (order-free)")

THE_OPTIONS = ["Sequential, PD first", "Sequential, LGD first",
               "Symmetric allocation (order-independent)"]


def _submits_with_a_blocking_ambiguity(quarter: str):
    """An `execute_analysis` whose intent says it cannot choose."""
    from test_vertical_slice import EAD_SQL

    return ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="attribute the ECL change",
                         ambiguities=[THE_AMBIGUITY]),
        "objective": "attribute the ECL change",
        "subquestions": ["which factor dominates"],
        "scope": {"reporting_periods": [quarter], "filters": {}},
        "metadata_receipt_ids": [],
        "fields_required": ["cockpit_facility_quarter.ead_reported"],
        "expected_output_grain": "sector", "expected_units": "amount",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": EAD_SQL.replace("?", f"'{quarter}'"),
                   "parameters": {}, "purpose": "attribution",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""})])


def _asks_the_reader(messages):
    """The finalize turn the run should now be able to make."""
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT",
                      understood="attribute the ECL change"),
        disposition="clarification",
        narrative=("Attribution across PD, LGD and CCF depends on how the "
                   "change is split, and the two conventions rank the "
                   "factors differently."),
        clarification_question=(
            "Which attribution should I use for the ECL change?"),
        clarification_options=THE_OPTIONS))])


# ---- the gate ------------------------------------------------------------

def test_a_declared_ambiguity_takes_execution_off_the_table():
    """The state that did not exist.

    Without it the run stays READY_FOR_EXECUTION, which REQUIRES
    `execute_analysis` -- so the turn after a refusal is compelled to submit
    the same thing again.
    """
    decision = acts.decide(
        executed=False, answer_only=False, analytical=True,
        readiness={"sufficient": True}, must_clarify=True)
    assert decision.state == acts.NEEDS_CLARIFICATION
    assert decision.tools == ("finalize_response",)
    assert decision.require == "finalize_response"


def test_the_same_run_without_an_ambiguity_still_executes():
    """The gate is not loosened for everybody.

    A question with one defensible reading must not acquire a round trip.
    """
    decision = acts.decide(
        executed=False, answer_only=False, analytical=True,
        readiness={"sufficient": True})
    assert decision.state == acts.READY_FOR_EXECUTION
    assert decision.require == "execute_analysis"


def test_a_result_that_already_exists_wins():
    """An ambiguity cannot outlive the result it prevented.

    If rows exist, the thing the ambiguity was blocking already happened,
    and the run's business is publishing them.
    """
    decision = acts.decide(
        executed=True, answer_only=False, analytical=True,
        readiness={"sufficient": True}, must_clarify=True)
    assert decision.state == acts.RESULT_READY


def test_every_state_tells_a_reader_what_it_is_doing():
    """The panel reads this table; a missing row shows an internal name."""
    for state in acts.STATES:
        assert acts.PUBLIC.get(state), state


# ---- the refusal ---------------------------------------------------------

def test_the_refusal_stops_advising_a_guess(service):
    """It said the doubt "belongs in resolved_assumptions".

    That is true of a doubt that does not change the figures, and it is
    exactly the wrong thing to say to one that does -- which is the only
    kind that should ever have been declared blocking.
    """
    from backend.cockpit_v4.contracts import Rejection, parse_execution

    submission = parse_execution({
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="attribute the change",
                         ambiguities=[THE_AMBIGUITY]),
        "objective": "attribute", "subquestions": ["which factor"],
        "scope": {"reporting_months": [], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": "SELECT 1 AS n", "parameters": {},
                   "purpose": "p", "input_artifact_ids": [],
                   "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, max_steps=4)

    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    said = caught.value.message
    assert "goes back to the reader" in said, said
    assert "would NOT change the figures" in said, said


# ---- end to end ----------------------------------------------------------

def test_a_run_that_cannot_choose_asks_instead_of_guessing(drive, store_db,
                                                           release_id):
    """The live sequence, with the ending it should have had.

    This one proves the DISPOSITION settles correctly. It does not prove
    the gate: a scripted analyst calls whatever the script says next,
    whether or not the tool was offered, so this test passes even with the
    gate change reverted. `test_the_turn_that_asks_was_not_offered_execution`
    is the one that holds the mechanism, and it does fail without it --
    checked, not assumed.
    """
    import oracles

    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "compare between PD, LGD & CCF and tell me what is most dominant",
        [_submits_with_a_blocking_ambiguity(quarter), _asks_the_reader])

    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    published = outcome.response or {}
    assert published["disposition"] == "clarification"
    assert published["clarification_question"]
    assert published["clarification_options"] == THE_OPTIONS

    # Nothing was executed under a reading nobody chose.
    assert not store_db.artifact_ids_for_run(
        record.run_id, tenant_id=record.tenant_id)


def test_the_turn_that_asks_was_not_offered_execution(drive, store_db,
                                                      release_id):
    """The mechanical claim: the compelled call is gone.

    The second request must carry `finalize_response` and nothing else --
    the run had been told to execute, and that is what made it guess.
    """
    import oracles

    quarter = oracles.latest_quarter(release_id)
    _outcome, provider, _record = drive(
        "compare between PD, LGD & CCF and tell me what is most dominant",
        [_submits_with_a_blocking_ambiguity(quarter), _asks_the_reader])

    offered = [t.get("name") for t in (provider.sent[-1].get("tools") or [])]
    assert offered == ["finalize_response"], offered


def test_a_published_result_says_what_first_went_wrong(drive, store_db,
                                                       release_id):
    """The reason reaches the ROWS, not only the process panel.

    A live run's first submission failed to parse -- `syntax error at or
    near "both"` -- recovered, executed, and then ran out of time. The
    reason it had lost those seconds sat one click away in the panel, for
    whoever thought to look. It belongs beside the result.
    """
    import oracles
    from conftest import ScriptedResult
    from test_orchestration_recovery import _ead_call
    from backend.cockpit_v4.provider import OutputTruncated

    quarter = oracles.latest_quarter(release_id)
    broken = ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT", understood="totals"),
        "objective": "totals", "subquestions": ["totals"],
        "scope": {"reporting_periods": [quarter], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "sector", "expected_units": "amount",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": "WITH both AS (SELECT 1) SELECT * FROM both",
                   "parameters": {}, "purpose": "totals",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""})])

    outcome, _provider, _record = drive(
        "What is total exposure at default by sector?",
        [broken, ScriptedResult(tool_calls=[_ead_call(quarter)]),
         OutputTruncated("cut off", limit=4096),
         OutputTruncated("cut off", limit=4096),
         OutputTruncated("cut off", limit=4096)])

    published = outcome.response or {}
    assert published.get("result_only") is True, outcome.message
    said = " ".join(published["limitations"])
    assert "first thing that went wrong" in said, said


def test_the_reader_is_told_a_question_is_being_put_back(drive, store_db,
                                                        release_id):
    """The panel says what the run is doing, in the run's own words."""
    import oracles

    quarter = oracles.latest_quarter(release_id)
    _outcome, _provider, record = drive(
        "compare between PD, LGD & CCF and tell me what is most dominant",
        [_submits_with_a_blocking_ambiguity(quarter), _asks_the_reader])

    said = " ".join(e.public_message
                    for e in store_db.events_since(record.run_id))
    assert "question" in said.lower()


# ---- the clarification gate (COCKPIT_V4_CLARIFY_GATE) ----------------------
#
# "What is driving Stage 2 and ECL growth?" maps in full. The local model asks
# about it on some runs and answers on others. The gate is the server saying
# which question was actually the reader's to answer.

STAGE2_ECL = "What is driving Stage 2 and ECL growth?"


def _asks_about_the_obvious(messages):
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT",
                      understood="what drives Stage 2 and ECL growth"),
        disposition="clarification",
        narrative="Which decomposition would you like?",
        clarification_question="Which decomposition should I use?",
        clarification_options=["By sector", "By stage"]))])


def test_an_ungrounded_clarification_is_refused_under_enforce(
        drive, store_db, release_id, monkeypatch):
    """The refusal reaches the analyst, and the NEXT request can execute.

    The analyst first declared a blocking ambiguity (which set `must_clarify`
    and left only finalize on offer), then asked. The gate must undo that
    state, or the correction it sends ("call execute_analysis") cannot be
    followed.
    """
    import oracles
    from backend.cockpit_v4 import clarify_gate

    monkeypatch.setenv(clarify_gate.ENV, "enforce")
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        STAGE2_ECL,
        [_submits_with_a_blocking_ambiguity(quarter), _asks_about_the_obvious,
         _asks_about_the_obvious])

    # Request 0 submits, 1 asks (refused), 2 is the retry.
    assert len(provider.sent) == 3, len(provider.sent)
    offered = [t.get("name") for t in (provider.sent[2].get("tools") or [])]
    assert "execute_analysis" in offered, offered
    refusal = json.dumps(provider.sent[2]["messages"][-1], default=str)
    assert "CLARIFICATION_UNGROUNDED" in refusal, refusal[:400]
    assert "call execute_analysis" in refusal, refusal[:600]
    said = " ".join(e.public_message
                    for e in store_db.events_since(record.run_id))
    assert "instead of asking" in said, said


def test_the_second_clarification_always_passes(drive, release_id,
                                                monkeypatch):
    import oracles
    from backend.cockpit_v4 import clarify_gate

    monkeypatch.setenv(clarify_gate.ENV, "enforce")
    quarter = oracles.latest_quarter(release_id)
    outcome, _provider, _record = drive(
        STAGE2_ECL,
        [_submits_with_a_blocking_ambiguity(quarter), _asks_about_the_obvious,
         _asks_about_the_obvious])
    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    assert (outcome.response or {})["disposition"] == "clarification"


def test_a_genuinely_ambiguous_term_still_asks_under_enforce(
        drive, monkeypatch):
    """Bare "exposure" has several readings, so the question is earned."""
    from backend.cockpit_v4 import clarify_gate

    monkeypatch.setenv(clarify_gate.ENV, "enforce")
    outcome, provider, _record = drive(
        "what is exposure",
        [_asks_about_the_obvious])
    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    assert len(provider.sent) == 1


def test_the_pd_lgd_ccf_question_still_asks_under_enforce(
        drive, store_db, release_id, monkeypatch):
    """A method choice is grounds: the existing flow is untouched."""
    import oracles
    from backend.cockpit_v4 import clarify_gate

    monkeypatch.setenv(clarify_gate.ENV, "enforce")
    quarter = oracles.latest_quarter(release_id)
    outcome, _provider, _record = drive(
        "compare between PD, LGD & CCF and tell me what is most dominant",
        [_submits_with_a_blocking_ambiguity(quarter), _asks_the_reader])
    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    assert (outcome.response or {})["clarification_options"] == THE_OPTIONS


def test_shadow_records_the_finding_and_refuses_nothing(
        drive, store_db, release_id, monkeypatch):
    import oracles
    from backend.cockpit_v4 import clarify_gate

    monkeypatch.delenv(clarify_gate.ENV, raising=False)
    assert clarify_gate.mode() == "shadow"
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        STAGE2_ECL,
        [_submits_with_a_blocking_ambiguity(quarter), _asks_about_the_obvious])
    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    assert len(provider.sent) == 2
    events = [e for e in store_db.events_since(record.run_id)]
    assert any("Clarification gate noted" in e.public_message
               for e in events)


def test_the_rejection_clears_the_state_and_spends_no_correction(
        monkeypatch):
    """Unit view of the hook: must_clarify and the sticky ambiguity go, and
    neither the answer-correction path nor `answer_only` is touched."""
    from types import SimpleNamespace
    from backend.cockpit_v4 import clarify_gate
    from backend.cockpit_v4 import orchestration as orch
    from backend.cockpit_v4.contracts import Rejection

    monkeypatch.setenv(clarify_gate.ENV, "enforce")
    appended = []
    spent = []

    import dataclasses

    @dataclasses.dataclass(frozen=True)
    class Env:
        query_mode: str = "DATA_ANALYSIS"
        open_questions: tuple = ()
        blocking_ambiguities: tuple = ("x",)

        def as_intent(self):
            return SimpleNamespace(
                blocking_ambiguities=self.blocking_ambiguities)

    fake = SimpleNamespace(
        executed=False, answer_only=False, _answer_demanded=False,
        _clarify_gate_used=False, analytical=True, must_clarify=True,
        envelope=Env(), intent=None, readiness={"sufficient": True,
                   "governed_measures_already_resolved": [
                       {"term": "ecl", "from": "this question"}]},
        value_resolution={}, _intent_findings=[],
        _can_afford_a_retry=lambda: True,
        ledger=SimpleNamespace(
            check_call_window=lambda **_: None,
            spend_answer_correction=lambda: spent.append(1)),
        emitter=SimpleNamespace(append=lambda *a, **k: appended.append(k)),
        _asked=lambda: STAGE2_ECL, _detail=lambda body: "ref")
    final_response = SimpleNamespace(
        disposition="clarification",
        intent=SimpleNamespace(excluded_parts=()), coverage=())
    with pytest.raises(Rejection) as caught:
        orch.Orchestrator._check_clarification(fake, final_response)
    assert caught.value.code == "CLARIFICATION_UNGROUNDED"
    assert "execute_analysis" in caught.value.detail["note"]
    assert "execute_analysis" not in caught.value.message  # reader-facing
    assert fake.must_clarify is False
    assert fake.envelope.blocking_ambiguities == ()
    assert fake.answer_only is False and not spent
    assert fake._clarify_gate_used is True
