"""REAL DATABASE · MODEL MOCK · UNIT. Slow turns, and what survives them.

§10, §20. The Mac failure was not only a malformed action -- it was a
malformed action that took eighty seconds, on a run with a hundred and
twenty. By the time it came back there was no time to ask again, so a run
that had been stopped by ONE bad call reported a call limit.

The provider double here is not the scripted one: it SLEEPS, and it honours
the timeout it is given the way an HTTP client does -- if the turn would run
past its deadline, the caller gets a timeout instead of an answer. That is
the only way to show that bounding one action leaves room for the next.

No paid call is made anywhere in this file. The latencies are simulated.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

import test_mac_action_replay as mac
from test_domain_execution import drive_domain, make_domain_run  # noqa: F401

#: Real time is compressed so the suite stays fast. One simulated second of
#: provider latency costs a fiftieth of a second here, and the BUDGETS are
#: scaled by the same factor -- so the arithmetic under test is the live
#: arithmetic, not a different one that happens to fit in a test run.
SCALE = 0.02


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


class SlowProvider:
    """A provider that takes time, and respects the deadline it is given.

    Each queued turn is `(simulated_seconds, result)`. If the turn would run
    past the timeout the caller set, this raises the way a client whose read
    deadline expired raises -- which is what the caller must survive.
    """

    def __init__(self, script):
        self.script = list(script)
        self.sent: list[dict] = []
        self.waited: list[float] = []

    def count_tokens(self, **_):
        return 4_000

    def converse(self, *, system, messages, tools=None, max_tokens=4096,
                 model="", purpose="", role="", timeout=0.0,
                 allow_retry=True, effort="", tool_choice=None,
                 output_config=None, temperature=None):
        assert allow_retry is False
        if not self.script:
            raise AssertionError("the slow provider ran out of turns")
        seconds, result = self.script.pop(0)
        self.sent.append({"purpose": purpose, "timeout": timeout,
                          "max_tokens": max_tokens,
                          "tool_choice": tool_choice,
                          "system": system,
                          "messages": [dict(m) for m in messages],
                          "simulated_seconds": seconds})
        allowed = timeout if timeout > 0 else seconds
        waited = min(seconds, allowed)
        self.waited.append(waited)
        time.sleep(waited * SCALE)
        if seconds > allowed:
            # The socket deadline the ledger set, reached. Nothing came back.
            raise TimeoutError(
                f"Request timed out after {allowed:.1f}s "
                f"(the turn was still generating)")
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(messages)
        return result


@pytest.fixture
def drive_slow(store_db, runtime):
    """One run in one book, against a provider that takes real time."""
    from backend.cockpit_v4.worker import Worker

    def _drive(domain_id, question, script, *, record=None, limits=None):
        provider = SlowProvider(script)
        runtime.provider = provider
        record = record or make_domain_run(store_db, domain_id, question)
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
        return outcome, provider, record
    return _drive


def _scaled(seconds: float) -> float:
    return seconds * SCALE


# ---- §20. a realistic slow run still finishes --------------------------

def test_a_slow_but_well_formed_run_completes_inside_its_budget(
        drive_slow, store_db):
    """10s action, then a 30s finalization. Ordinary, and it publishes."""
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (10.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
        (30.0, mac.answer_from_result)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert provider.waited == [10.0, 30.0]


def test_a_slow_malformed_action_is_cancelled_and_the_second_one_lands(
        drive_slow, store_db):
    """§10, §20. The exact shape that produced CALL_LIMIT on the Mac.

    The first action stalls past its own window. It is cancelled THERE --
    not at the run's deadline -- so the recovery still has time to be made,
    and the answer still has its reserve.
    """
    quarter = mac.latest_quarter()
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    started = time.monotonic()
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (90.0, ScriptedResult(text="an essay nobody asked for")),
        (12.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
        (25.0, mac.answer_from_result)])
    elapsed = time.monotonic() - started

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    # The stalled turn was stopped at the action window, not at 90 seconds.
    assert provider.waited[0] <= limits.action_call_seconds + 1
    simulated = sum(provider.waited)
    assert simulated <= limits.deadline_seconds, (
        f"{simulated:.0f}s of simulated provider time against a "
        f"{limits.deadline_seconds:.0f}s allowance")
    assert elapsed < 30, "the test itself must stay fast"


def test_the_first_action_cannot_spend_the_whole_deadline(drive_slow,
                                                          store_db):
    """The bound, asserted on what the provider was actually given."""
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (200.0, ScriptedResult(text="still writing")),
        (200.0, ScriptedResult(text="still writing"))])
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    for sent in provider.sent:
        assert sent["timeout"] <= limits.action_call_seconds, sent
    assert sum(provider.waited) <= 2 * limits.action_call_seconds + 1
    # Two stalled actions is a bounded failure, and it is named correctly.
    assert outcome.state == st.FAILED
    assert outcome.error_code in (st.CALL_LIMIT, st.PROVIDER_UNAVAILABLE,
                                  st.DEADLINE_EXPIRED)


def test_a_35s_malformed_action_leaves_room_for_recovery_and_an_answer(
        drive_slow, store_db):
    """§20's own list: 35s malformed, recovery, 30s finalization."""
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (35.0, mac.truncated_action()),
        (20.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
        (30.0, mac.answer_from_result)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert sum(provider.waited) <= (
        config_mod.ANALYTICAL_STANDARD_LIMITS.deadline_seconds)


def test_the_answer_turn_is_not_cut_short_by_the_action_window(drive_slow,
                                                               store_db):
    """The reserve exists to be SPENT on writing up work already paid for."""
    quarter = mac.latest_quarter()
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (5.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
        (limits.action_call_seconds + 10.0, mac.answer_from_result)])
    assert outcome.state == st.COMPLETED, outcome.message
    answer = provider.sent[-1]
    assert answer["timeout"] > limits.action_call_seconds, (
        "an answer turn bounded by the ACTION window would throw away the "
        "analysis it was about to write up")


# ---- §24. five repetitions, both Mac flows -----------------------------

@pytest.mark.parametrize("run", range(1, 6))
def test_the_stage2_flow_repeats_five_times_under_a_slow_provider(
        drive_slow, store_db, run):
    """An intermittent action-format failure is a defect, so it is looked
    for rather than hoped against: the same flow, five times, with the
    first action stalling past its window every time."""
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_slow(dom.CORPORATE, mac.STAGE2, [
        (55.0, ScriptedResult(text="an essay nobody asked for")),
        (15.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
        (25.0, mac.answer_from_result)])
    assert outcome.state == st.COMPLETED, f"run {run}: {outcome.message}"
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert outcome.response["executed"] is True


@pytest.mark.parametrize("run", range(1, 6))
def test_the_seeded_flow_repeats_five_times_under_a_slow_provider(
        drive_slow, store_db, run):
    record, _ = mac.seeded_construction_run(store_db)
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_slow(
        dom.CORPORATE, mac.CONSTRUCTION,
        [(35.0, mac.truncated_action()),
         (15.0, ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)])),
         (25.0, mac.answer_from_result)],
        record=record)
    assert outcome.state == st.COMPLETED, f"run {run}: {outcome.message}"
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert mac.catalog_calls(provider) == 0
