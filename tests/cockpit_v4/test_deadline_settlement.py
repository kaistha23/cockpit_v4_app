"""
A run that ran out of time keeps the rows it computed.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

The defect this exists for
--------------------------
Two live Corporate runs, both on the 120-second analytical allowance, both
of which executed their query successfully:

    compare between PD, LGD & CCF and tell me what is most dominant
    on this quarter's ECL change                     -> DEADLINE_EXPIRED

    give me a ECL decomposition and what is the impact of PD
                                                     -> DEADLINE_EXPIRED

Each reader was shown a red box:

    This request ran out of time
    Reason: DEADLINE_EXPIRED

and nothing else, while the rows sat in the artifact store.

TWO COMPONENTS ENFORCE THIS DEADLINE. The worker's own ledger raises inside
the loop and settles the run through `_stop`, which publishes the computed
result. The SUPERVISOR settles it from outside, has no access to what the
worker knows, and wrote the terminal row with no response at all.

They were racing on the same instant -- a 2-second settlement margin
against a 2-second poll -- and the watchdog won routinely. So which of two
components happened to write the row decided whether the reader got their
data. `DEADLINE_EXPIRED` had been on the result-only eligibility list the
whole time; the path was simply unreachable from the component that
usually ended these runs.

What is pinned here:

  the watchdog holds back        -> the worker settles the ordinary case
  the watchdog publishes anyway  -> defence in depth, rows are never binned
  a run with no rows             -> still a plain expiry, not a fake answer
  a published answer             -> is never overwritten by a late sweep
  the message is a sentence      -> not "passed while generation."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.supervisor import Supervisor


def _stamp(seconds_from_now: float) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(seconds=seconds_from_now)
            ).isoformat(timespec="milliseconds")


def _working_run(store, *, deadline_at: str, tenant: str = "t"):
    """A run that has been claimed and is mid-generation."""
    thread_id = store.create_thread(tenant_id=tenant, principal_id="p")
    record, _ = store.accept_run(
        thread_id=thread_id, tenant_id=tenant, principal_id="p",
        question="What is total EAD by sector?", mode="standard",
        release_id="r", ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="", deadline_at=deadline_at)
    claimed = store.claim_next("w-1")
    store.update_state(claimed.run_id, expect_version=claimed.version,
                       state=st.MODEL_RUNNING, operation="generation")
    return store.get_run(record.run_id)


def _with_rows(store, record, *, tenant: str = "t") -> str:
    """The result this run executed, stored the way the runner stores it."""
    return store.put_artifact(
        run_id=record.run_id, tenant_id=tenant, kind="result",
        release_id=record.release_id,
        scope={"step_id": "s1", "relations": ["corp_borrower_quarter"],
               "complete": True},
        columns=["sector", "ead_sar_mn"],
        rows=[{"row_id": "r1", "sector": "Real Estate",
               "ead_sar_mn": 40599.17},
              {"row_id": "r2", "sector": "Energy", "ead_sar_mn": 21044.5}])


# ---- the watchdog holds back --------------------------------------------

def test_a_run_just_past_its_deadline_is_left_to_settle_itself(store_db):
    """The worker owns the ordinary case.

    A cooperative stop publishes the rows; this cannot. Holding the
    watchdog back by more than a clean settlement takes is what stops the
    two from racing over a reader's data.
    """
    record = _working_run(store_db, deadline_at=_stamp(-1.0))
    settled = Supervisor(store=store_db, grace_seconds=15.0).sweep()
    assert settled["expired"] == 0

    after = store_db.get_run(record.run_id)
    assert after.state == st.MODEL_RUNNING, (
        "the watchdog settled a run the worker was still holding")


def test_a_run_well_past_its_deadline_is_settled(store_db):
    """The watchdog is still a watchdog. A worker that is gone is settled."""
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    settled = Supervisor(store=store_db, grace_seconds=15.0).sweep()
    assert settled["expired"] == 1
    assert store_db.get_run(record.run_id).error_code == st.DEADLINE_EXPIRED


# ---- and publishes what the run computed ---------------------------------

def test_the_rows_survive_a_watchdog_settlement(store_db):
    """THE DEFECT. Executed, stored, and thrown away by the reaper.

    Every precondition was satisfied in both live runs: the query had run,
    the artifacts were in the store, no answer had been published, and the
    code was on the eligibility list. The only thing that decided whether
    the reader saw their data was which component wrote the row.
    """
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    _with_rows(store_db, record)

    assert Supervisor(store=store_db,
                      grace_seconds=15.0).sweep()["expired"] == 1

    after = store_db.get_run(record.run_id)
    # The run still ran out of time, and still says so.
    assert after.error_code == st.DEADLINE_EXPIRED
    # But rows that exist are not a failure.
    assert after.state == st.PARTIAL

    published = after.final_response or {}
    assert published.get("result_only") is True
    assert published["disposition"] == "partial_answer"
    assert published["numeric_claims"] == [], "nothing here came from a model"
    rows = published["tables"][0]["rows"]
    assert len(rows) == 2
    assert rows[0]["canonical"]["sector"] == "Real Estate"
    assert "time allowance" in " ".join(published["limitations"])


def test_the_reader_is_told_the_result_was_kept(store_db):
    """An event a person can read, beside the one that says it stopped."""
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    _with_rows(store_db, record)
    Supervisor(store=store_db, grace_seconds=15.0).sweep()

    events = store_db.events_since(record.run_id)
    kinds = [e.event_type for e in events]
    assert ev.RUN_EXPIRED in kinds
    assert ev.ANALYSIS_PRESERVED in kinds
    said = " ".join(e.public_message for e in events)
    assert "published without the written answer" in said


def test_a_run_that_computed_nothing_is_a_plain_expiry(store_db):
    """No rows, no invented answer. The red box is correct here."""
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    assert Supervisor(store=store_db,
                      grace_seconds=15.0).sweep()["expired"] == 1

    after = store_db.get_run(record.run_id)
    assert after.state == st.EXPIRED
    assert after.error_code == st.DEADLINE_EXPIRED
    assert not after.final_response


def test_an_answer_that_was_published_is_not_overwritten(store_db):
    """A late sweep must not replace a real answer with a caveat."""
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    _with_rows(store_db, record)
    store_db.update_state(
        record.run_id, expect_version=record.version, state=st.MODEL_RUNNING,
        final_response={"disposition": "answer", "narrative": "The real one.",
                        "numeric_claims": [{"claim_id": "c1"}]})

    Supervisor(store=store_db, grace_seconds=15.0).sweep()
    after = store_db.get_run(record.run_id)
    assert after.final_response["narrative"] == "The real one."
    assert not after.final_response.get("result_only")


# ---- the message ---------------------------------------------------------

def test_the_expiry_message_is_a_sentence(store_db):
    """It read "The standard deadline passed while generation."

    `operation` is a machine tag dropped into a sentence that needed a
    clause. A reader is not helped by the orchestrator's vocabulary.
    """
    record = _working_run(store_db, deadline_at=_stamp(-60.0))
    Supervisor(store=store_db, grace_seconds=15.0).sweep()

    said = store_db.events_since(record.run_id)[-1].public_message
    assert "while generation" not in said, said
    assert "CreditProbe was writing" in said, said


@pytest.mark.parametrize("operation,clause", [
    ("generation", "CreditProbe was writing"),
    ("batch", "the query was running"),
    ("context", "the question was being prepared"),
    ("something_new", "this request was working"),
])
def test_every_operation_reads_as_a_clause(store_db, operation, clause):
    """Including one this table has never heard of."""
    from backend.cockpit_v4.supervisor import _doing

    class _R:
        pass

    record = _R()
    record.operation = operation
    assert _doing(record) == clause
