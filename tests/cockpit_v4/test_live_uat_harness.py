"""`--only` is an order, not just a filter.

UNIT · REPRODUCTION. No model call, no database, no money.

The defect
----------
The live-UAT harness selected journeys with a comprehension over the matrix:

    wanted = [j for j in matrix()
              if not args.only or j.jid in args.only.split(",")]

That keeps the DEFINITION order however the caller listed the ids. So an
approved queue whose whole point was to run the load-bearing evidence first
-- clarification, self-repair, ECL decomposition, the multi-turn chains --
would have executed in whatever order this file happens to declare them,
and nothing would have said so.

Run order is a spend decision. The harness stops the queue when committed
spend reaches the stop-at threshold, so the order decides WHAT IS ALREADY
PROVEN when it stops. Getting it wrong does not cost money; it costs the
evidence the money was spent to get.

An id that is not in the matrix now stops the run rather than being skipped,
because a typo in an approved queue must not quietly shorten it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")


@pytest.fixture(scope="module")
def uat():
    """Load the harness as a module.

    It has to be registered in `sys.modules` before `exec_module`, because
    `@dataclass` resolves annotations through `sys.modules[cls.__module__]`
    and a module that is not there yet raises on the lookup.
    """
    spec = importlib.util.spec_from_file_location("live_uat", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_uat"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("live_uat", None)


def select(uat, only: str):
    """The harness's own selection, lifted out of `main()`.

    Kept in step with the source by `test_the_selection_here_matches_the
    _harness` below: a copy that drifts proves nothing about the code that
    runs.
    """
    index = {j.jid: j for j in uat.matrix()}
    asked = [x.strip() for x in only.split(",") if x.strip()]
    unknown = [x for x in asked if x not in index]
    if unknown:
        raise KeyError(unknown)
    return [index[x] for x in asked]


# ---- the order the caller asked for is the order that runs -------------

def test_a_reversed_queue_runs_reversed(uat) -> None:
    ids = [j.jid for j in uat.matrix()]
    asked = ",".join(reversed(ids))
    assert [j.jid for j in select(uat, asked)] == list(reversed(ids))


def test_an_arbitrary_order_is_honoured(uat) -> None:
    """The specific shape the approved queue has: load-bearing single-turn
    journeys, then chains, then breadth -- none of it in matrix order."""
    asked = "L01,L02,L12,L17,L16,L18,L13,L15,L14,M05,M02,M04,L11,L05,L04,L08,L10"
    assert [j.jid for j in select(uat, asked)] == asked.split(",")


def test_the_matrix_order_would_not_have_produced_it(uat) -> None:
    """The guard above is only meaningful if the two orders differ."""
    asked = "L01,L02,L12,L17,L16,L18,L13,L15,L14,M05,M02,M04,L11,L05,L04,L08,L10"
    wanted = set(asked.split(","))
    definition = [j.jid for j in uat.matrix() if j.jid in wanted]
    assert definition != asked.split(",")


def test_a_duplicate_id_runs_twice(uat) -> None:
    """A caller who lists a journey twice means it twice. Silently
    de-duplicating would change an approved run count."""
    assert [j.jid for j in select(uat, "L01,L02,L01")] == ["L01", "L02", "L01"]


def test_whitespace_around_an_id_is_tolerated(uat) -> None:
    assert [j.jid for j in select(uat, " L02 , L01 ")] == ["L02", "L01"]


def test_an_unknown_id_is_refused_not_skipped(uat) -> None:
    with pytest.raises(KeyError) as caught:
        select(uat, "L01,L99,L02")
    assert "L99" in str(caught.value)


# ---- §16 mutation check ------------------------------------------------

def test_the_old_filter_would_have_lost_the_order(uat) -> None:
    """Reproduces the comprehension as it was and shows it ignoring the
    caller. If this ever passes, the ordering fix is not what is doing the
    work and the guards above are passing for some other reason."""
    asked = "M05,L18,L01"
    old = [j for j in uat.matrix() if j.jid in asked.split(",")]
    assert [j.jid for j in old] == ["L01", "L18", "M05"]
    assert [j.jid for j in old] != asked.split(",")
    assert [j.jid for j in select(uat, asked)] == asked.split(",")


# ---- the copy above is the code below ----------------------------------

def test_the_selection_here_matches_the_harness() -> None:
    """A lifted copy that drifts from the source proves nothing.

    This pins the three properties `select` relies on, read out of the
    harness itself: it indexes the matrix by id, it iterates the ASKED
    list, and it refuses an unknown id.
    """
    source = HARNESS.read_text(encoding="utf-8")
    assert "index = {j.jid: j for j in matrix()}" in source
    assert "wanted = [index[x] for x in asked]" in source
    assert "unknown journey id(s)" in source


def test_the_run_count_is_not_hard_coded(uat) -> None:
    """The harness must stay reusable. An approved run count belongs in the
    invocation, not in the file: a default of 28 here would silently cap a
    future UAT that was approved for more."""
    source = HARNESS.read_text(encoding="utf-8")
    assert '"--max-runs", type=int, default=0' in source
    assert "APPROVED_MAX_RUNS" not in source


# ---- the gates the run order exists to protect -------------------------

def test_the_guard_refuses_the_run_after_the_approved_count(uat) -> None:
    guard = uat.Guard(cap_usd=25.0, stop_at_usd=20.0,
                      per_run_ceiling_usd=1.5, max_runs=28)
    guard.runs = 27
    guard.before_run("28th")          # allowed
    guard.runs = 28
    with pytest.raises(uat.Stop) as caught:
        guard.before_run("29th")
    assert caught.value.condition == "run_count"


def test_no_max_runs_means_no_run_count_gate(uat) -> None:
    """`0` is "not configured", not "zero runs"."""
    guard = uat.Guard(cap_usd=25.0, stop_at_usd=20.0,
                      per_run_ceiling_usd=1.5, max_runs=0)
    guard.runs = 1000
    guard.before_run("any")
