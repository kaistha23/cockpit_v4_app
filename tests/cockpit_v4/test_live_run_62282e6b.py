"""REAL DATABASE · MODEL MOCK · REPRODUCTION. One live Mac run, replayed.

run-62282e6be0ca48c5a4afdd3c468e3de8
"What is driving Stage 2 and ECL growth?"
Corporate, v4-saudi-corporate-20q-v3, 120-second allowance.

What the trace recorded
-----------------------
      0.137s  first action generation starts
     28.303s  rejected, operation=output_truncated:
              "The response was cut off before it was complete; asking
               again once."
     28.3xx   the recovery generation starts -- and the tool surface is
              BROADENED: "Product knowledge lookup available."
     55.918s  the run fails: "the one structure-regeneration attempt for
               choosing an action was already used."

     generations 2/12 · provider attempts 9/24 · executions 0/5 ·
     analysis rounds 0/3 · catalogue calls 0/4 · $0.33538 of $1.50 ·
     64.079s of 120s remaining

Nothing was exhausted. Not calls, not time, not money. The run was
reported as CALL_LIMIT and that was untrue.

The second attempt
------------------
The ledger cannot distinguish the two shapes a failed action can take,
because the counter it hit is charged for both. What it does establish:
the failure came from `spend_format_recovery(phase="action")`, which is
reached only when a turn needs ANOTHER structure recovery -- so the second
attempt either truncated again or came back with no tool call. The elapsed
time (27.6s, against the first attempt's 28.2s) makes a second truncation
the likely one.

That ambiguity is itself a defect and is fixed rather than guessed at: the
call report now records, per attempt, whether the turn was truncated, what
it parsed to, which tools it called and whether they were usable. Both
shapes are replayed below, and both must publish.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import action_state as acts
from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

import test_mac_action_replay as mac
from test_domain_execution import drive_domain, make_domain_run  # noqa: F401

RUN_ID = "run-62282e6be0ca48c5a4afdd3c468e3de8"
QUESTION = "What is driving Stage 2 and ECL growth?"

#: The live numbers, kept as data so an assertion can cite them.
LIVE = {
    "deadline_seconds": 120.0,
    "failed_at_seconds": 55.921,
    "remaining_seconds": 64.079,
    "spend_usd": 0.33538,
    "ceiling_usd": 1.50,
    "generation_attempts": (2, 12),
    "execution_submissions": (0, 5),
    "analysis_rounds": (0, 3),
    "catalog_calls": (0, 4),
    "action_format_recoveries": (1, 1),
    "first_attempt_rejected_at": 28.303,
    "first_attempt_operation": "output_truncated",
}


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


def first_attempt_truncated():
    """Attempt 1, exactly as the live provider ended it."""
    return mac.truncated_action()


def second_attempt_truncated():
    """Attempt 2, shape A: cut off again at the allowance."""
    return mac.truncated_action()


def second_attempt_prose():
    """Attempt 2, shape B: a complete turn with no tool call in it."""
    return ScriptedResult(
        text="Stage 2 exposure has risen in several sectors this quarter. "
             "To establish what is driving it I would compare ECL and EAD "
             "by sector between 2026Q2 and 2026Q1 ...",
        stop_reason="end_turn")


def working_run(second):
    """The live failure, then the action the state machine asks for."""
    quarter = mac.latest_quarter()
    return [first_attempt_truncated(), second,
            ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)]),
            mac.answer_from_result]


# ---- §14. the replay ---------------------------------------------------

@pytest.mark.parametrize("shape,second", [
    ("truncated_again", second_attempt_truncated()),
    ("no_tool_call", second_attempt_prose()),
])
def test_the_live_run_now_publishes(drive_domain, store_db, shape, second):
    """PASS means: recovery narrowed, a valid action arrived, SQL ran, a
    result artifact exists, the answer published, and neither CALL_LIMIT
    nor a deadline failure appeared."""
    outcome, provider, record = drive_domain(
        dom.CORPORATE, QUESTION, working_run(second))

    assert outcome.state == st.COMPLETED, f"{shape}: {outcome.message}"
    assert outcome.error_code not in (
        st.CALL_LIMIT, st.ACTION_FORMAT_EXHAUSTED, st.DEADLINE_EXPIRED)
    assert outcome.response["executed"] is True
    assert outcome.response["narrative"]

    # A result artifact exists and the answer is bound to it.
    run = store_db.get_run(record.run_id)
    assert run.state == st.COMPLETED
    claims = outcome.response.get("numeric_claims") or []
    assert claims and claims[0].get("evidence", {}).get("artifact_id")

    # The recovery NARROWED. Nothing was added between the attempts.
    surfaces = [set(t["name"] for t in sent["tools"])
                for sent in provider.sent]
    assert surfaces[1] <= surfaces[0], (
        f"{shape}: the re-ask offered {surfaces[1] - surfaces[0]}, which is "
        f"more than the attempt that failed")
    assert mac.catalog_calls(provider) == 0


def test_the_recovery_is_not_told_product_knowledge_is_available(
        drive_domain, store_db):
    """The live trace's own words, as a thing that must not happen again."""
    outcome, _, record = drive_domain(
        dom.CORPORATE, QUESTION, working_run(second_attempt_truncated()))
    assert outcome.state == st.COMPLETED, outcome.message

    said = " ".join((e.public_message or "") for e
                    in store_db.events_since(record.run_id, 0, 1000))
    assert "Product knowledge lookup available" not in said
    assert "inspect_product_knowledge" not in said


def test_every_attempt_on_this_run_is_diagnosable_from_the_report(
        drive_domain, store_db):
    """§13. The gap that made the live second attempt unclassifiable."""
    outcome, _, _ = drive_domain(
        dom.CORPORATE, QUESTION, working_run(second_attempt_prose()))
    assert outcome.state == st.COMPLETED, outcome.message

    calls = outcome.call_report["calls"]
    assert len(calls) == 4
    for call in calls:
        for key in ("purpose", "attempt", "phase", "tools_offered",
                    "counted_input_tokens", "output_allowance",
                    "call_timeout_seconds", "tool_choice", "outcome",
                    "provider_ms"):
            assert key in call, f"attempt {call['seq']} has no {key}"

    # Attempt 1: truncated, no tool call, and SAID so.
    assert calls[0]["outcome"] == "truncated"
    assert calls[0]["truncated"] is True
    assert calls[0]["parse_status"] == "incomplete"
    # Attempt 2: complete, but nothing usable in it. A different fact.
    assert calls[1]["outcome"] == "ok"
    assert calls[1]["truncated"] is False
    assert calls[1]["parse_status"] == "no_tool_call"
    assert calls[1]["usable"] is False
    # Attempt 3: the action that ran.
    assert calls[2]["parse_status"] == "tool_call"
    assert calls[2]["tool_names"] == ["execute_analysis"]
    assert calls[2]["usable"] is True
    # No chain of thought anywhere in it.
    blob = json.dumps(outcome.call_report).lower()
    for forbidden in ("thinking", "chain_of_thought", "reasoning_trace"):
        assert forbidden not in blob


def test_the_failure_this_run_reported_is_no_longer_that_failure(
        drive_domain, store_db):
    """§12. When BOTH attempts fail, the code names what actually ran out.

    The live run said CALL_LIMIT with ten generations, fifteen provider
    attempts, 64 seconds and $1.16 still available. What was spent was the
    one re-ask, and that is now what it is called.
    """
    # Three, because the re-ask is now allowed to change something: the
    # surface narrows and, after a truncation, the allowance is raised
    # once. When there is nothing left to change, the run stops.
    outcome, _, _ = drive_domain(dom.CORPORATE, QUESTION, [
        first_attempt_truncated(), second_attempt_truncated(),
        second_attempt_truncated()])

    assert outcome.state == st.FAILED
    assert outcome.error_code == st.ACTION_FORMAT_EXHAUSTED
    assert outcome.error_code != st.CALL_LIMIT

    report = outcome.call_report
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    assert report["generations"] < limits.generation_attempts, (
        "the run is not out of generations, and must not say it is")
    assert "re-ask" in outcome.message or "action" in outcome.message.lower()


# ---- §5. this question, in this book, is ready to execute -------------

def test_this_question_is_classified_ready_for_execution(store_db):
    """The classification the live run never made."""
    from backend.cockpit_v4 import context as ctx_mod

    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    packet = ctx_mod.build(
        question=QUESTION, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), session=book.session,
        analytical=True)
    readiness = packet.payload["analysis_readiness"]
    assert readiness["sufficient"] is True

    decision = acts.decide(executed=False, answer_only=False,
                           analytical=True, readiness=readiness)
    assert decision.state == acts.READY_FOR_EXECUTION
    assert decision.tools == ("execute_analysis",)
    assert decision.require == "execute_analysis"

    # Everything §5 says the context already knows.
    pinned = packet.payload["pinned_scope"]
    assert pinned["domain"] == dom.CORPORATE
    assert pinned["release_id"] == dom.DEFAULT_RELEASES[dom.CORPORATE]
    assert pinned["latest_populated_quarter"] == "2026Q2"
    terms = {m["term"] for m in
             readiness["governed_measures_already_resolved"]}
    assert {"ecl", "stage"} <= terms
