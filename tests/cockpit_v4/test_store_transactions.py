"""UNIT · REAL DATABASE. A write transaction always ends.

§57. An intermittent failure is a defect, and this was one: a full-suite run
stopped a chain at `sqlite3.OperationalError: database is locked` after
waiting out its whole sixty-second allowance, and the same test passed on
its own every time.

The cause was not contention. `RunStore._tx` reached its ROLLBACK only via
`except sqlite3.Error`, so ANY other exception raised inside the block -- a
validation failure, an HTTPException, a KeyError in the caller's own code --
propagated straight through with `BEGIN IMMEDIATE` still open on that
thread's connection, holding the database's write lock.

Nothing released it. Every later writer waited out its thirty-second
`busy_timeout` and failed with "database is locked": a message that reads as
a storage problem and is not one. It is one earlier caller's exception, still
holding the door, and which caller it was depends on test ordering -- which
is why it looked like a flake.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from backend.cockpit_v4 import lake
from backend.cockpit_v4.run_store import RunStore, StorageUnavailable


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "state.sqlite3")


def in_transaction(store: RunStore) -> bool:
    """Whether this thread's connection is still inside one."""
    return store._connect().in_transaction


# ---- the defect, directly ----------------------------------------------

def test_an_ordinary_exception_inside_a_transaction_still_ends_it(store):
    """The case that held the lock: not a sqlite error at all."""
    class TheCallersOwnProblem(RuntimeError):
        pass

    with pytest.raises(TheCallersOwnProblem):
        with store._tx() as conn:
            conn.execute(
                "INSERT INTO threads (thread_id, tenant_id, principal_id, "
                "created_at) VALUES ('t-1', 'demo', 'u1', '2026-01-01')")
            raise TheCallersOwnProblem("something in the route")

    assert not in_transaction(store), "the transaction was left open"
    # And the write was rolled back, which is the other half of ending it.
    rows = store._connect().execute(
        "SELECT COUNT(*) FROM threads WHERE thread_id = 't-1'").fetchone()
    assert rows[0] == 0


@pytest.mark.parametrize("raised", [
    KeyError("missing"), ValueError("bad"), TypeError("wrong"),
    AssertionError("no"), RuntimeError("stop"),
])
def test_no_exception_type_leaves_a_transaction_open(store, raised):
    with pytest.raises(type(raised)):
        with store._tx() as conn:
            conn.execute("SELECT 1")
            raise raised
    assert not in_transaction(store)


def test_a_sqlite_error_still_arrives_as_the_typed_outcome(store):
    """Rolling back on everything must not change WHAT a storage failure is
    reported as: the state machine declares STORAGE_UNAVAILABLE and stops
    the run rather than launching the next paid operation."""
    with pytest.raises(StorageUnavailable):
        with store._tx() as conn:
            conn.execute("SELECT * FROM a_table_that_does_not_exist")
    assert not in_transaction(store)


def test_a_successful_transaction_commits_and_ends(store):
    with store._tx() as conn:
        conn.execute(
            "INSERT INTO threads (thread_id, tenant_id, principal_id, "
            "created_at) VALUES ('t-2', 'demo', 'u1', '2026-01-01')")
    assert not in_transaction(store)
    rows = store._connect().execute(
        "SELECT COUNT(*) FROM threads WHERE thread_id = 't-2'").fetchone()
    assert rows[0] == 1


# ---- what the leak actually cost ---------------------------------------

def test_a_later_writer_is_not_blocked_by_an_earlier_failure(store):
    """The observable symptom, reproduced across threads.

    On the old code the second thread waited out its `busy_timeout` and then
    failed. With a thirty-second timeout this test would have taken thirty
    seconds to fail; the assertion below is that it does not wait at all.
    """
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with store._tx() as conn:
            conn.execute(
                "INSERT INTO threads (thread_id, tenant_id, principal_id, "
                "created_at) VALUES ('t-3', 'demo', 'u1', '2026-01-01')")
            raise Boom()

    done = threading.Event()
    failure: list[BaseException] = []

    def writer() -> None:
        try:
            with store._tx() as conn:
                conn.execute(
                    "INSERT INTO threads (thread_id, tenant_id, "
                    "principal_id, created_at) "
                    "VALUES ('t-4', 'demo', 'u1', '2026-01-01')")
        except BaseException as exc:  # noqa: BLE001 - recorded, not swallowed
            failure.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    # Five seconds is generous for a single insert and far under the thirty
    # second timeout the old code would have spent before failing.
    assert done.wait(5.0), (
        "a later writer is still waiting on a lock nobody holds")
    thread.join(5.0)
    assert not failure, failure

    rows = store._connect().execute(
        "SELECT COUNT(*) FROM threads WHERE thread_id = 't-4'").fetchone()
    assert rows[0] == 1


def test_many_failures_in_a_row_leave_the_store_usable(store):
    """A route that raises on every request must not degrade the database."""
    for index in range(25):
        with pytest.raises(RuntimeError):
            with store._tx() as conn:
                conn.execute("SELECT 1")
                raise RuntimeError(index)
        assert not in_transaction(store), index

    with store._tx() as conn:
        conn.execute(
            "INSERT INTO threads (thread_id, tenant_id, principal_id, "
            "created_at) VALUES ('t-5', 'demo', 'u1', '2026-01-01')")
    rows = store._connect().execute(
        "SELECT COUNT(*) FROM threads WHERE thread_id = 't-5'").fetchone()
    assert rows[0] == 1


def test_an_in_memory_store_is_never_discarded():
    """`_discard` drops a connection this thread cannot trust. The shared
    in-memory connection IS the database, and closing it would delete the
    store rather than repair it."""
    memory = RunStore(":memory:")
    conn = memory._connect()
    memory._discard(conn)
    assert memory._connect() is conn
    conn.execute("SELECT COUNT(*) FROM threads")


def test_a_real_run_survives_a_route_that_raised(store):
    """End to end, in the shape the sweep hit it: a failing call followed by
    a real accept on the same store."""
    with pytest.raises(RuntimeError):
        with store._tx() as conn:
            conn.execute("SELECT 1")
            raise RuntimeError("the route blew up")

    thread_id = store.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id="corporate", release_id="r", release_fingerprint="f")
    record, created = store.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question="q", mode="standard", release_id="r",
        domain_id="corporate", release_fingerprint="f", ui_filters={},
        idempotency_key="", body_digest="", startup_sha="t", deadline_at="")
    assert created
    assert store.get_run(record.run_id) is not None
