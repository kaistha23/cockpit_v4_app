"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

Where a run's time and context actually went, per model call.

The defect this exists for
--------------------------
A live analytical run executed its query, returned seven borrower rows, and
then spent the rest of its deadline. The trace said the run stopped; it did
not say how many model calls were made, what each was for, how large each
request had grown, or whether the wall-clock went to the provider or to
CreditProbe assembling requests. "The model is slow" and "we spent the
deadline building payloads" looked identical from the outside.

`Analyst.turns` already existed and was never read by anything. It is now a
record of every generation ATTEMPT -- including the ones refused before they
were sent, which are precisely the ones a stopped run needs explained -- and
it reaches the reader on the outcome.
"""

from __future__ import annotations

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_orchestration_recovery import _bad_answer, _ead_call, _good_answer

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import OutputTruncated

QUESTION = "What is total exposure at default by sector in the latest quarter?"


def _analysis(drive, release_id, script):
    outcome, provider, record = drive(QUESTION, script)
    return outcome, provider, record


# ---- the report exists and is complete ---------------------------------

def test_every_call_is_reported_with_what_it_was_for(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    report = outcome.call_report
    assert report["generations"] == 2, (
        "a simple analysis is one action and one answer")
    for call in report["calls"]:
        for key in ("seq", "purpose", "phase", "attempt", "context_bytes",
                    "counted_input_tokens", "output_allowance", "outcome",
                    "provider_ms"):
            assert key in call, f"call {call.get('seq')} has no {key}"
        assert set(call["context_bytes"]) == {"system", "tools", "messages",
                                              "total"}
        assert call["context_bytes"]["total"] > 0


def test_the_purposes_name_the_two_halves_of_an_analysis(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    assert [c["purpose"] for c in outcome.call_report["calls"]] == [
        "ANALYSIS_ACTION", "FINAL_ANSWER"]
    assert [c["phase"] for c in outcome.call_report["calls"]] == [
        "action", "answer"]


def test_a_recovery_is_named_for_the_phase_it_recovers(drive, release_id):
    """The live bug, as a reading problem: both re-asks used to look alike."""
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        OutputTruncated("cut off", limit=4096),
        ScriptedResult(tool_calls=[_ead_call(quarter)]),
        OutputTruncated("cut off", limit=4096), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message
    assert [c["purpose"] for c in outcome.call_report["calls"]] == [
        "ANALYSIS_ACTION", "ACTION_FORMAT_RECOVERY",
        "FINAL_ANSWER", "ANSWER_FORMAT_RECOVERY"]


def test_a_rejected_answer_is_named_a_correction(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]),
        _bad_answer, _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.call_report["calls"][-1]["purpose"] == "ANSWER_CORRECTION"


def test_a_truncated_call_is_reported_as_truncated(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        OutputTruncated("cut off", limit=4096),
        ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.call_report["calls"][0]["outcome"] == "truncated"
    assert all(c["outcome"] == "ok"
               for c in outcome.call_report["calls"][1:])


# ---- provider time vs CreditProbe time ---------------------------------

def test_the_wall_clock_is_split_between_provider_and_creditprobe(
        drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    report = outcome.call_report
    assert report["provider_ms"] == sum(
        c["provider_ms"] for c in report["calls"])
    assert report["local_ms"] == max(
        0, report["elapsed_ms"] - report["provider_ms"])
    assert report["elapsed_ms"] >= 0


# ---- the answer turn really is the smaller one -------------------------

def test_the_answer_turn_carries_less_context_than_the_action(drive,
                                                              release_id):
    """The finalization trim, measured rather than asserted.

    The answer turn drops the catalogue index, the canonical semantics and
    the product synopsis. The conversation itself has GROWN by then -- it
    carries the executed result -- so the saving shows up in the system
    blocks.

    The TOOLS no longer compare that way, and deliberately. Each turn now
    carries only the tools its state can legally use: an execution-ready
    action carries `execute_analysis` and nothing else, and the turn that
    has a result to write up carries the full answer contract. Two requests
    of one tool each are not the same request, and the larger schema
    belongs on the turn that can use it.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = _analysis(drive, release_id, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    action, answer = outcome.call_report["calls"]
    assert answer["context_bytes"]["system"] < action["context_bytes"][
        "system"]
    assert action["tools_offered"] == ["execute_analysis"]
    assert "finalize_response" in answer["tools_offered"]
    assert not ({"execute_analysis", "inspect_catalog",
                 "inspect_product_knowledge"}
                & set(answer["tools_offered"]))


# ---- a call that never reached the provider ----------------------------

def test_a_call_refused_before_it_was_sent_is_still_reported(drive,
                                                             release_id,
                                                             monkeypatch):
    """The stop that has nothing to show for it is the one that needs the
    report most: no request id, no stop reason, no usage, no duration."""
    from backend.cockpit_v4.budgets import Ledger

    seen: list[Ledger] = []
    original = Ledger.__init__

    def _capture(self, *a, **k):
        original(self, *a, **k)
        seen.append(self)

    monkeypatch.setattr(Ledger, "__init__", _capture)

    quarter = oracles.latest_quarter(release_id)

    def _slow_answer(messages):
        # The analysis has run and the first written answer is about to be
        # rejected. Writing it took nearly the whole deadline. Rewinding the
        # ledger's start is how this suite simulates latency without
        # sleeping through it; two seconds are left, which is less than the
        # minimum a call may be given.
        for ledger in seen:
            ledger.started_monotonic -= max(0.0,
                                            ledger.remaining_seconds - 2.0)
        return _bad_answer(messages)

    outcome, _, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[_ead_call(quarter)]),
        _slow_answer, _good_answer])
    assert outcome.state in (st.PARTIAL, st.EXPIRED, st.FAILED), (
        f"the correction call should not have been affordable: "
        f"{outcome.state}")

    calls = outcome.call_report["calls"]
    refused = [c for c in calls if c["outcome"] == "refused_before_send"]
    assert refused, (
        f"a run that stopped without sending its last call reported only "
        f"{[c['outcome'] for c in calls]}")
    last = refused[-1]
    assert last["refusal"] == st.DEADLINE_EXPIRED
    assert last["purpose"] and last["phase"]
    assert "request_id" not in last, (
        "nothing was sent, so there is nothing to attribute it to")


# ---- what the reader watching the panel sees ---------------------------

def _messages(store_db, record):
    return [e.public_message for e in store_db.events_since(record.run_id)]


def test_the_panel_distinguishes_the_two_re_asks(drive, store_db,
                                                 release_id):
    """An action re-ask and an answer re-ask are different failures.

    They have different allowances and different costs -- one happens before
    any work exists, the other after the SQL has run and been paid for -- and
    a live run stopped because they shared a counter. A reader watching the
    panel must be able to tell which one just happened.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(QUESTION, [
        OutputTruncated("cut off", limit=4096),
        ScriptedResult(tool_calls=[_ead_call(quarter)]),
        OutputTruncated("cut off", limit=4096), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    said = _messages(store_db, record)
    action_reask = [m for m in said
                    if m.startswith("The response was cut off")]
    answer_reask = [m for m in said
                    if m.startswith("The written answer was cut off")]
    assert len(action_reask) == 1, said
    assert len(answer_reask) == 1, said
    assert "result is preserved" in answer_reask[0], (
        "a reader must not conclude the analysis was lost with the answer")
    # And each re-ask is followed by a line saying what is now being asked
    # for, rather than the same sentence printed twice.
    assert "Asking again for a complete action" in said
    assert "Asking again for a complete answer" in said


def test_the_panel_names_what_each_model_call_is_for(drive, store_db,
                                                     release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(QUESTION, [
        OutputTruncated("cut off", limit=4096),
        ScriptedResult(tool_calls=[_ead_call(quarter)]),
        _bad_answer, _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    said = _messages(store_db, record)
    for expected in ("Asking again for a complete action",
                     "Writing the answer from the result",
                     "Correcting the written answer against the result"):
        assert expected in said, f"the panel never said {expected!r}: {said}"
