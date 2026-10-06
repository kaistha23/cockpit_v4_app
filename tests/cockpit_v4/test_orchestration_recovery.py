"""
The live orchestration failure: one recovery token, two phases.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

A live analytical run did everything right and was thrown away at the end:

    ... first useful provider response ~38s
    ... a later response was CUT OFF before the action was complete
    ... one structure regeneration used
    ... regenerated execute_analysis succeeded
    ... query validated and bound
    ... query EXECUTED, rows returned
    ... publication stopped:
        "the one structure-regeneration attempt for this run was already used."
    ... terminal: CALL_LIMIT

The analysis succeeded. What failed was the bookkeeping around it. A single
`format_recoveries` counter, limit one, is spent by THREE different
situations in TWO different phases -- a truncated action, a turn with no tool
call, and a rejected batch -- so a hiccup while AUTHORING the analysis
silently disarmed the recovery that WRITING THE ANSWER would later need.

Those are different phases with different costs. Re-asking for a compact
action is cheap and happens before any work exists; re-asking for an answer
happens after the SQL has run and the result is already paid for. Sharing one
token between them means the second failure is charged for the first.
"""

from __future__ import annotations

import json
import pathlib

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import OutputTruncated


def _ead_call(quarter: str, call_id: str = "tu-1"):
    from test_vertical_slice import EAD_SQL, _execute_call

    return _execute_call(
        EAD_SQL, purpose="EAD by sector", grain="sector",
        units="amount", subquestions=["EAD by sector"],
        fields=["cockpit_facility_quarter.ead_reported"],
        quarter=quarter, call_id=call_id)


def _execution_result(messages):
    """The executed result, wherever it sits in the thread.

    After a rejected answer the LAST message is the rejection, not the
    execution -- and the analyst still has the successful result behind it.
    Reading only the last message would make this helper depend on nothing
    having gone wrong, which is the opposite of what these tests are for.
    """
    for message in reversed(messages):
        for block in (message.get("content") or []):
            if not isinstance(block, dict):
                continue
            raw = block.get("content")
            if not isinstance(raw, str):
                continue
            try:
                body = json.loads(raw)
            except ValueError:
                continue
            if isinstance(body, dict) and body.get("steps"):
                return body
    raise AssertionError("no executed result anywhere in the thread")


def _good_answer(messages):
    """A valid final answer built from whatever the execution returned."""
    body = _execution_result(messages)
    step = body["steps"][0]
    artifact = step["artifact_id"]
    cell = step["preview"][0]
    column = next(c for c in step["columns"] if c != "sector_name")
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="The largest sector carries {{claim.top}}.",
              coverage=[{"subquestion": "EAD by sector",
                         "status": "answered",
                         "evidence_refs": [
                             {"artifact_id": artifact, "row_key": "r0",
                              "column_id": column}]}],
              numeric_claims=[{
                  "claim_id": "top",
                  "decimal_value": str(cell[column]),
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact, "row_key": "r0",
                               "column_id": column}}],
              tables=[{"title": "EAD by sector", "artifact_id": artifact,
                       "columns": list(cell)}]))],
        output_tokens=400)


def _bad_answer(messages):
    """A final answer whose evidence does not resolve. Correctable."""
    artifact = _execution_result(messages)["steps"][0]["artifact_id"]
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="Total exposure is {{claim.total}}.",
              numeric_claims=[{
                  "claim_id": "total", "decimal_value": "1.00",
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact,
                               "row_key": "all sectors",
                               "column_id": "ead_reported_sar_mn"}}]))],
        output_tokens=400)


# ---- the live sequence, end to end ------------------------------------

def test_a_truncated_action_must_not_disarm_a_truncated_answer(
        drive, store_db, release_id):
    """THE live failure, reproduced exactly.

    The live message was "the one structure-regeneration attempt for this run
    was already used" -- which is the TRUNCATION recovery, not the answer
    correction. So the second failure was the FINAL ANSWER being cut off, not
    failing validation: a long written answer is exactly the turn most likely
    to hit its output allowance, and it arrives after the SQL has already run.

    Truncated action -> recover -> analysis succeeds -> truncated ANSWER ->
    recover -> COMPLETED.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [OutputTruncated("cut off", limit=4096),           # action cut off
         ScriptedResult(tool_calls=[_ead_call(quarter)]),  # compact retry
         OutputTruncated("cut off", limit=4096),           # ANSWER cut off
         _good_answer])                                    # answer retry

    assert outcome.state == st.COMPLETED, (
        f"the live sequence still does not complete: {outcome.message}")
    assert outcome.response["executed"] is True
    assert "{{claim." not in outcome.response["narrative"]


def test_a_truncated_action_must_not_disarm_the_answer_correction(
        drive, store_db, release_id):
    """The same split, for an answer that fails VALIDATION rather than
    being cut off. Already worked; pinned so it keeps working."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [OutputTruncated("cut off", limit=4096),
         ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _bad_answer, _good_answer])

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True


def test_each_phase_spends_only_its_own_allowance(drive, store_db,
                                                  release_id):
    """The ledger must show one of each, not two of one."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [OutputTruncated("cut off", limit=4096),
         ScriptedResult(tool_calls=[_ead_call(quarter)]),
         OutputTruncated("cut off", limit=4096), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    spend = store_db.get_run(record.run_id).budget
    assert spend["action_format_recoveries"][0] == 1, spend
    assert spend["answer_format_recoveries"][0] == 1, spend


# ---- the bounds still hold --------------------------------------------

def test_a_second_truncated_action_still_fails_closed(drive, release_id):
    """Separate buckets must not make the action phase unbounded.

    Three attempts, not two: the re-ask is granted while the request can
    still change (the surface narrows, and a truncation raises the
    allowance once). What it may never become is unbounded.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [OutputTruncated("cut off", limit=4096),
         OutputTruncated("cut off", limit=4096),
         OutputTruncated("cut off", limit=4096),
         ScriptedResult(tool_calls=[_ead_call(quarter)])])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.ACTION_FORMAT_EXHAUSTED
    assert (outcome.response or {}).get("disposition") != "answer"


def test_a_second_invalid_answer_still_fails_closed(drive, release_id):
    """Neither invalid narrative is published. The ROWS are.

    Failing closed is a statement about the NARRATIVE: an answer that
    cannot be reconciled with its evidence must never reach a reader, and
    after the one correction it does not. That has not moved, and is
    asserted below.

    What moved is what happens to the result. The query ran, its rows are
    stored and correct, and a reader used to be shown a red box over them.
    They now go out through the result-only channel -- server-rendered
    tables, a server-written caveat, no narrative and no claims -- so
    nothing the analyst wrote survives either way.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _bad_answer, _bad_answer, _good_answer])
    assert outcome.error_code == st.ANSWER_VALIDATION
    assert outcome.state == st.PARTIAL

    published = outcome.response or {}
    assert published.get("result_only") is True
    assert published["disposition"] == "partial_answer"
    # THE NARRATIVE FAILED CLOSED. Nothing the analyst wrote is in here.
    assert published["numeric_claims"] == []
    assert published["tables"], "the rows the query returned"
    assert "could not write" in published["narrative"]


def test_a_truncated_action_never_executes_partial_arguments(drive,
                                                             store_db,
                                                             release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [OutputTruncated("cut off", limit=4096),
         ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message
    # Exactly one execution: the truncated turn ran nothing.
    spend = store_db.get_run(record.run_id).budget
    assert spend["execution_submissions"][0] == 1, spend


# ---- the deadline must bound the CALL, not just be checked after it ----

def test_a_call_is_not_launched_without_time_to_finish_and_publish(
        store_db, capability, release_id):
    """A 120s run reached ~143s live. The guard fires only at zero.

    `check_deadline` raises when `remaining_seconds <= 0`, so a generation
    launched with a few seconds left is allowed to consume them and the run
    then discovers it has no time to write an answer. Time spent reaching a
    result nobody receives is time wasted twice.
    """
    from backend.cockpit_v4.budgets import BudgetExceeded, Ledger
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4 import states as st

    ledger = Ledger(limits=STANDARD_LIMITS, capability=capability,
                    store=store_db, run_id="run-deadline")
    # One action has already happened: the reserve protects writing up an
    # analysis, so it applies from the second generation onward, never to
    # the first.
    ledger.counters.generation_attempts = 1
    # Pretend the run has been going for almost its whole allowance.
    ledger.started_monotonic -= (STANDARD_LIMITS.deadline_seconds
                                 - 6.0)
    assert 0 < ledger.remaining_seconds < 10

    with pytest.raises(BudgetExceeded) as caught:
        ledger.check_call_window(phase="action")
    assert caught.value.code == st.DEADLINE_EXPIRED
    assert "publish" in str(caught.value).lower()


def test_the_first_generation_is_never_blocked_by_the_reserve(
        store_db, capability):
    """Nothing exists to protect yet, and the run has not widened its
    allowance: the analytical limit is adopted only once the analyst says
    the turn is analytical, so reserving against the tight starting bound
    would stop an analytical question before it could claim its own."""
    from backend.cockpit_v4.budgets import Ledger
    from backend.cockpit_v4.config import STANDARD_LIMITS

    ledger = Ledger(limits=STANDARD_LIMITS, capability=capability,
                    store=store_db, run_id="run-first")
    ledger.started_monotonic -= (STANDARD_LIMITS.deadline_seconds - 8.0)
    assert ledger.counters.generation_attempts == 0
    ledger.check_call_window(phase="action")


def test_an_answer_call_may_use_the_reserve_an_action_call_may_not(
        store_db, capability):
    """The reserve exists FOR the answer; the answer may spend it."""
    from backend.cockpit_v4.budgets import BudgetExceeded, Ledger
    from backend.cockpit_v4.config import STANDARD_LIMITS

    ledger = Ledger(limits=STANDARD_LIMITS, capability=capability,
                    store=store_db, run_id="run-reserve")
    ledger.counters.generation_attempts = 1
    ledger.started_monotonic -= (STANDARD_LIMITS.deadline_seconds
                                 - (STANDARD_LIMITS.finalization_reserve_seconds
                                    - 2.0))
    # Too little for another ACTION...
    with pytest.raises(BudgetExceeded):
        ledger.check_call_window(phase="action")
    # ...but the answer turn is exactly what the reserve was held for.
    ledger.check_call_window(phase="answer")


def test_the_provider_timeout_never_exceeds_the_time_the_run_has_left(
        store_db, capability):
    from backend.cockpit_v4.budgets import Ledger
    from backend.cockpit_v4.config import STANDARD_LIMITS

    ledger = Ledger(limits=STANDARD_LIMITS, capability=capability,
                    store=store_db, run_id="run-timeout")
    ledger.counters.generation_attempts = 1
    ledger.started_monotonic -= (STANDARD_LIMITS.deadline_seconds - 23.0)
    budget = ledger.call_timeout_seconds()
    remaining = ledger.remaining_seconds
    assert budget <= remaining - 1.0, (
        f"a {budget:.1f}s provider timeout with only {remaining:.1f}s of "
        f"run left leaves no margin to settle and stop cleanly")
    assert budget > 0
