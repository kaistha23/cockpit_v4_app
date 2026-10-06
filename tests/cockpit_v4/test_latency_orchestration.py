"""
Orchestration under realistic provider latency, without waiting for it.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call, and no wall-clock
sleeping: a provider that takes 35 seconds is simulated by advancing the
ledger's own clock, which is the clock every deadline decision reads.

Why this exists
---------------
The deterministic suite answers in milliseconds, so every deadline bound
passes trivially and none of them is actually exercised. A live Standard run
budgeted at 120 seconds reached about 143. Tests that never spend time cannot
see that, and adding real sleeps would trade one blind spot for a suite
nobody runs.

So the clock moves instead. `advance` rewinds `started_monotonic`, which is
exactly what `remaining_seconds` subtracts from -- the same arithmetic the
production guard uses, driven from the test.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import states as st
from test_orchestration_recovery import (_ead_call, _execution_result,
                                         _good_answer)


class Slow:
    """A provider turn that 'takes' a given number of seconds."""

    def __init__(self, seconds: float, result) -> None:
        self.seconds = seconds
        self.result = result

    def __call__(self, messages):
        _advance(self.seconds)
        return self.result(messages) if callable(self.result) else self.result


_LEDGER: list = []


def _advance(seconds: float) -> None:
    for ledger in _LEDGER:
        ledger.started_monotonic -= seconds


#: The allowance these timings are expressed against.
ALLOWANCE = config_mod.ANALYTICAL_STANDARD_LIMITS.deadline_seconds


def _burn(fraction: float) -> None:
    """Spend a FRACTION of the analysis allowance, not a count of seconds.

    These timings were absolute -- 55s of 120s, 45s then 72s -- and each one
    encoded the allowance twice: once in the number, once in the intent
    ("two turns and there is no room for a third"). Raising the allowance to
    180s turned every one of them into a run that finishes comfortably, and
    the suite reported a preserved-analysis path as COMPLETED rather than
    reporting that its own arithmetic had gone stale.

    A fraction says the intent directly and survives the next tuning. One of
    these tests already carried the instruction in an assertion message:
    retune it rather than letting the path go unchecked.
    """
    _advance(fraction * ALLOWANCE)


@pytest.fixture
def slow_drive(store_db, runtime, make_run, monkeypatch):
    """`drive`, with a handle on the ledger so turns can consume time."""
    from backend.cockpit_v4 import budgets
    from backend.cockpit_v4.worker import Worker
    from conftest import ScriptedProvider

    _LEDGER.clear()
    original = budgets.Ledger.__init__

    def remember(self, *args, **kwargs):
        original(self, *args, **kwargs)
        _LEDGER.append(self)

    monkeypatch.setattr(budgets.Ledger, "__init__", remember)

    def _drive(question: str, script, *, mode: str = "standard"):
        provider = ScriptedProvider(script)
        runtime.provider = provider
        record = make_run(question, mode=mode)
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
        return outcome, provider, record
    return _drive


# ---- the clean path fits the allowance --------------------------------

def test_a_simple_analysis_finishes_well_inside_the_standard_deadline(
        slow_drive, release_id):
    """Two turns at live-like latency, inside the allowance."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [Slow(18.0, ScriptedResult(tool_calls=[_ead_call(quarter)])),
         Slow(16.0, _good_answer)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2
    assert _LEDGER, "the ledger was never constructed"
    assert _LEDGER[0].elapsed_seconds < ALLOWANCE, (
        f"the run took {_LEDGER[0].elapsed_seconds:.0f}s of a "
        f"{ALLOWANCE:.0f}s allowance")


def test_a_run_with_a_recovery_still_fits(slow_drive, release_id):
    """A truncated action, a retry and an answer still fit."""
    from backend.cockpit_v4.provider import OutputTruncated

    quarter = oracles.latest_quarter(release_id)

    def truncate(messages):
        _burn(0.1667)
        return OutputTruncated("cut off", limit=4096)

    outcome, provider, _ = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [truncate,
         Slow(25.0, ScriptedResult(tool_calls=[_ead_call(quarter)])),
         Slow(20.0, _good_answer)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert _LEDGER[0].elapsed_seconds < ALLOWANCE


# ---- and the guard fires when it should not fit -----------------------

def test_no_further_action_is_started_once_the_reserve_is_all_that_is_left(
        slow_drive, store_db, release_id):
    """The defect: burn the allowance on actions, then have no time to answer.

    The run must not launch another action it cannot use. What it does
    instead is hand the reserve over and ask for the answer -- see §12 and
    `Orchestrator._demand_an_answer_if_time_is_short`. A scripted analyst
    that ignores that and sends another action gets it refused, and the run
    stops on the deadline with no fabricated answer.
    """
    quarter = oracles.latest_quarter(release_id)

    def glacial(messages):
        _burn(0.4583)
        return ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog",
            {"relations": ["cockpit_facility_quarter"], "detail": ["fields"],
             "field_ids": ["ead_reported"], "search": "",
             "sample_rows": 0})])

    outcome, provider, record = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [glacial, glacial, glacial,
         ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])

    assert outcome.state in (st.FAILED, st.EXPIRED, st.PARTIAL), outcome.state
    assert outcome.error_code == st.DEADLINE_EXPIRED, outcome.error_code
    assert (outcome.response or {}).get("disposition") != "answer"

    events = store_db.events_since(record.run_id, 0)
    operations = [e.operation for e in events]
    assert "answer_reserve" in operations, (
        "the reserve must be handed over as an answer turn, not discovered "
        "by an exception when the next action is refused")
    reserve = events[operations.index("answer_reserve")]
    assert "writing the answer" in reserve.public_message
    # And the analysis the run had no time for was never started.
    assert "execute_analysis" not in operations or all(
        e.status != "OK" for e in events
        if e.operation == "execute_analysis")


def test_the_last_seconds_are_spent_writing_the_answer_not_discovering_none(
        slow_drive, store_db, release_id):
    """§12. The analysis succeeded and the clock nearly ran out. A concise
    answer from evidence that already exists beats a bare deadline notice."""
    quarter = oracles.latest_quarter(release_id)

    def slow_action(messages):
        _burn(0.4167)
        return ScriptedResult(tool_calls=[_ead_call(quarter)])

    def nearly_out(messages):
        _burn(0.4583)
        return ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog",
            {"relations": ["cockpit_facility_quarter"], "detail": ["fields"],
             "field_ids": ["ead_reported"], "search": "",
             "sample_rows": 0})])

    outcome, provider, record = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [slow_action, nearly_out, _good_answer])

    events = store_db.events_since(record.run_id, 0)
    operations = [e.operation for e in events]
    assert "answer_reserve" in operations
    assert outcome.state == st.COMPLETED, outcome.message
    assert (outcome.response or {}).get("disposition") == "answer"
    # The demand told the analyst what it had, and the tools on that call
    # could not have started anything new.
    demanded = [m for m in provider.sent[-1]["messages"]
                if m["role"] == "user"]
    text = json.dumps(demanded, default=str)
    assert "finalize_response now" in text
    offered = {t["name"] for t in provider.sent[-1]["tools"]}
    assert "finalize_response" in offered
    # Reading a stored result is not starting an analysis; starting one is.
    assert offered <= {"finalize_response", "read_artifact"}, sorted(offered)


def test_the_answer_turn_may_spend_the_reserve_the_actions_could_not(
        slow_drive, release_id):
    """A finished analysis with little time left still gets written up."""
    quarter = oracles.latest_quarter(release_id)

    def slow_action(messages):
        # Inside the 60s the run starts on; the analytical allowance widens
        # to 120s once this action declares the turn analytical.
        _burn(0.375)
        return ScriptedResult(tool_calls=[_ead_call(quarter)])

    outcome, provider, _ = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [slow_action, Slow(60.0, _good_answer)])
    assert outcome.state == st.COMPLETED, (
        f"an analysis that succeeded was not published: {outcome.message}")
    assert len(provider.sent) == 2


def test_a_successful_analysis_is_preserved_when_time_runs_out(
        slow_drive, store_db, release_id):
    """Analysis success and answer success are different facts."""
    from backend.cockpit_v4 import events as ev

    quarter = oracles.latest_quarter(release_id)

    from test_orchestration_recovery import _bad_answer

    def slow_action(messages):
        _burn(0.375)
        return ScriptedResult(tool_calls=[_ead_call(quarter)])

    def slow_invalid_answer(messages):
        # The analysis has succeeded and the written answer does not bind.
        # A correction is allowed -- but by now there is no time to make
        # one, so the run stops with a stored result and no answer.
        _burn(0.6)
        return _bad_answer(messages)

    outcome, provider, record = slow_drive(
        "What is total exposure at default by sector in the latest quarter?",
        [slow_action, slow_invalid_answer, _good_answer])

    assert outcome.state != st.COMPLETED, (
        "this timing was meant to run out of time; retune it rather than "
        "letting the preservation path go unchecked")
    # A published answer that merely finished late is NOT this case: an
    # answer produced correctly is published, because discarding finished
    # work is the defect this whole round exists to remove.
    events = [e.event_type for e in store_db.events_since(record.run_id)]
    assert ev.ANALYSIS_PRESERVED in events, (
        "the SQL succeeded; a run that stops afterwards must say so rather "
        "than reading as if the mathematics failed")
    # And the result is still readable by an operator.
    run = store_db.get_run(record.run_id)
    assert run.error_code in (st.DEADLINE_EXPIRED, st.COST_LIMIT), \
        run.error_code
    artifacts = [e for e in store_db.events_since(record.run_id)
                 if e.event_type == ev.ANALYSIS_PRESERVED]
    assert artifacts, "the preserved-analysis event carries the artifact ids"
