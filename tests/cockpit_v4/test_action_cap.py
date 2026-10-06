"""NO MODEL. An action may not run into the finalization reserve.

`check_call_window` refuses to START an action inside the reserve, but an
action's own timeout used to be min(usable, deadline, action_call_seconds),
so one admitted just outside the reserve could outlive it. After the first
generation the timeout is also bounded by remaining - reserve - margin.
"""

from __future__ import annotations

MARGIN = 2.0


def _ledger(ledger_factory, *, remaining, generations):
    ledger = ledger_factory(deadline_seconds=100.0, action_call_seconds=45.0,
                            answer_call_seconds=100.0 - 5.0,
                            finalization_reserve_seconds=30.0)
    ledger.started_monotonic -= (100.0 - remaining)
    ledger.counters.generation_attempts = generations
    return ledger


def test_action_after_a_generation_leaves_the_reserve(ledger_factory):
    ledger = _ledger(ledger_factory, remaining=60.0, generations=1)
    timeout = ledger.call_timeout_seconds(phase="action")
    # 60 left, 30 reserved, 2 margin: 28 beats the 45s action window.
    assert 27.0 <= timeout <= 28.0
    assert timeout <= ledger.remaining_seconds - 30.0 - MARGIN + 0.01


def test_first_generation_is_not_capped_by_the_reserve(ledger_factory):
    ledger = _ledger(ledger_factory, remaining=60.0, generations=0)
    assert ledger.call_timeout_seconds(phase="action") == 45.0


def test_answer_phase_is_not_capped_by_the_reserve(ledger_factory):
    ledger = _ledger(ledger_factory, remaining=60.0, generations=2)
    ledger.counters.answer_format_recoveries = (
        ledger.limits.answer_format_regenerations)  # the last attempt
    timeout = ledger.call_timeout_seconds(phase="answer")
    assert 57.0 <= timeout <= 58.0  # remaining - margin, reserve is spendable


def test_cap_never_drops_below_one_second(ledger_factory):
    ledger = _ledger(ledger_factory, remaining=20.0, generations=1)
    assert ledger.call_timeout_seconds(phase="action") == 1.0
