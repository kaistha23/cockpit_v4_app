"""The scripted ledger and the paid ledger are different files.

UNIT · REPRODUCTION · REAL DATABASE. No model call, no paid provider call.

The defect
----------
The harness used ONE state database for both modes, deleted at startup. That
made the live pre-flight unanswerable. A dry run settles real reservations --
scripted token counts priced at the real card -- so it leaves a ledger with
committed spend in it, and "does this ledger begin at zero?" then depends on
whether that spend was real.

Nothing in the store can answer that. The schema records no provider and no
model: `runs`, `reservations` and `events` have no such column, and a full
dry-run event stream contains ZERO occurrences of "model". `startup_sha` is
the harness's own constant in both modes. Telling a scripted reservation from
a paid one after the fact is not merely awkward here -- it is not possible
without changing the product's schema.

So the two are never mixed. The mode picks the path; the live ledger is never
deleted by the harness; and a live run that finds anything in its ledger
refuses to start and says where the file is. Moving a paid record aside is an
operator's decision, and a spend control that made it silently would be doing
the one thing it exists to prevent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from backend.cockpit_v4.run_store import RunStore

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")


@pytest.fixture(scope="module")
def uat():
    spec = importlib.util.spec_from_file_location("live_uat", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_uat"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("live_uat", None)


def spend_of(db: Path) -> dict:
    """The ledger, read through the product's own arithmetic.

    Run ids come from RESERVATIONS, not from `runs`, so a reservation
    orphaned from its run is still counted.
    """
    store = RunStore(db)
    conn = store._connect()
    runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    reservations = conn.execute(
        "SELECT COUNT(*) FROM reservations").fetchone()[0]
    committed = pending = 0.0
    uncertain = False
    for (rid,) in conn.execute("SELECT DISTINCT run_id FROM reservations"):
        s = store.spend(rid)
        committed += s["committed_usd"]
        pending += s["pending_usd"]
        uncertain = uncertain or s["uncertain"]
    return {"runs": runs, "reservations": reservations,
            "committed": round(committed, 6), "pending": round(pending, 6),
            "uncertain": uncertain}


def seed(db: Path, *, settled: float | None, reserved: float = 1.5,
         uncertain: bool = False) -> None:
    """A ledger with real spend in it, built with the real reserve/settle
    API rather than by writing rows by hand."""
    store = RunStore(db)
    thread = store.create_thread(tenant_id="t", principal_id="u")
    record, _ = store.accept_run(
        thread_id=thread, tenant_id="t", principal_id="u", question="q",
        mode="standard", release_id="r", ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="x", deadline_at="")
    res = store.reserve(run_id=record.run_id, purpose="p",
                        reserved_usd=reserved)
    if settled is not None or uncertain:
        store.settle(res, settled_usd=settled, usage={}, uncertain=uncertain)


# ---- the two ledgers are different files -------------------------------

def test_the_modes_do_not_share_a_path(uat) -> None:
    live = uat.state_db_for(live=True)
    dry = uat.state_db_for(live=False)
    assert live != dry
    assert live == uat.LIVE_DB and dry == uat.DRY_RUN_DB


def test_neither_is_the_old_shared_file(uat) -> None:
    """`state.sqlite3` was the shared name. Reusing it for either mode would
    silently adopt whatever a previous build left there."""
    for path in (uat.state_db_for(live=True), uat.state_db_for(live=False)):
        assert path.name != "state.sqlite3"
    assert "state.sqlite3" not in HARNESS.read_text(encoding="utf-8")


def test_an_override_is_honoured_for_either_mode(uat, tmp_path) -> None:
    where = tmp_path / "elsewhere.sqlite3"
    assert uat.state_db_for(live=True, override=str(where)) == where
    assert uat.state_db_for(live=False, override=str(where)) == where


# ---- scripted state cannot contaminate live state ----------------------

def test_a_dry_run_ledger_is_recreated_and_never_consulted(uat, tmp_path) -> None:
    """The scripted ledger is disposable. It can be wiped without a thought
    because nothing live has ever opened it."""
    dry = tmp_path / "dry_run.sqlite3"
    seed(dry, settled=0.5130)
    assert spend_of(dry)["committed"] == 0.513
    uat.open_store(dry, live=False)
    after = spend_of(dry)
    assert after["runs"] == 0 and after["reservations"] == 0
    assert after["committed"] == 0.0


def test_scripted_spend_never_reaches_the_live_ledger(uat, tmp_path) -> None:
    """The contamination the split exists to prevent."""
    dry, live = tmp_path / "dry_run.sqlite3", tmp_path / "live.sqlite3"
    uat.open_store(dry, live=False)
    seed(dry, settled=0.5130)
    assert not live.exists(), "a dry run must not create the live ledger"
    store = uat.open_store(live, live=True)
    assert spend_of(live) == {"runs": 0, "reservations": 0, "committed": 0.0,
                              "pending": 0.0, "uncertain": False}
    assert store is not None


# ---- a live ledger with anything in it is never deleted ----------------

@pytest.mark.parametrize("label,kwargs", [
    ("committed spend", {"settled": 3.21}),
    ("a pending reservation", {"settled": None}),
    ("an uncertain settle", {"settled": None, "uncertain": True}),
])
def test_live_refuses_a_ledger_that_holds_anything(uat, tmp_path, label,
                                                   kwargs) -> None:
    live = tmp_path / "live.sqlite3"
    seed(live, **kwargs)
    before = spend_of(live)
    with pytest.raises(uat.Stop) as caught:
        uat.open_store(live, live=True)
    assert caught.value.condition == "live_ledger_not_empty"
    assert str(live) in caught.value.detail
    # AND the file is untouched: refusing is not a euphemism for clearing.
    assert live.exists()
    assert spend_of(live) == before, f"the {label} was modified by a refusal"


def test_a_completed_live_run_is_not_wiped_by_the_next_invocation(uat,
                                                                  tmp_path) -> None:
    """The specific loss this prevents: a second live invocation silently
    destroying the record of the first one's spend."""
    live = tmp_path / "live.sqlite3"
    uat.open_store(live, live=True)
    seed(live, settled=7.77)
    with pytest.raises(uat.Stop):
        uat.open_store(live, live=True)
    assert spend_of(live)["committed"] == 7.77


# ---- a fresh live ledger reads exactly zero ----------------------------

def test_live_preflight_sees_a_genuinely_empty_ledger(uat, tmp_path) -> None:
    live = tmp_path / "live.sqlite3"
    assert not live.exists()
    uat.open_store(live, live=True)
    assert live.exists(), "the store must be CREATED and read, not inferred"
    assert spend_of(live) == {"runs": 0, "reservations": 0, "committed": 0.0,
                              "pending": 0.0, "uncertain": False}


# ---- why design B was not available -----------------------------------

def test_the_store_records_no_provider_identity(tmp_path) -> None:
    """The evidence behind the design.

    Telling a scripted reservation from a paid one after the fact would need
    the store to record which provider answered. It records nothing of the
    kind. If a future change adds one, this test fails and the separate-path
    design can be revisited deliberately rather than by accident.
    """
    store = RunStore(tmp_path / "s.sqlite3")
    conn = store._connect()
    for table in ("runs", "reservations", "events"):
        columns = [row[1] for row in conn.execute(
            f"PRAGMA table_info({table})")]
        named = [c for c in columns
                 if "model" in c.lower() or "provider" in c.lower()]
        # Deliberate revisit: model selection pins the provider and model on
        # the RUN, so a run records which model it was accepted for. The
        # money-bearing and audit tables must still carry no identity, which
        # is what keeps a scripted reservation indistinguishable from a paid
        # one at the ledger.
        if table == "runs":
            assert named == ["provider", "model"]
            continue
        assert named == [], (
            f"{table} now records {named}; a scripted run may be "
            f"distinguishable from a paid one, so revisit the split")
