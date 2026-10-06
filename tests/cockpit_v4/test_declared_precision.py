"""A figure is compared at the precision its evidence carries.

UNIT · REAL RELEASE · REAL DATABASE. No model call, no paid provider call.

L08 PRECISION CLOSURE
---------------------
The second offline rejudge against the immutable first-pass evidence
reported L08 not-ok on all three stages. It is not a portfolio-number
error.

The analyst's own SQL computed `ROUND(AVG(quarters_in_stage), 2)`, so the
authoritative artifact holds 16.62, 6.10 and 5.70 and nothing finer. The
independent oracle recomputes from the parquet at full double precision:

    stage 1   16.620207108872098   published 16.62   delta 0.0002071
    stage 2    6.096634281748786   published  6.10   delta 0.0033657
    stage 3    5.699785177228787   published  5.70   delta 0.0002148

Every one of those is inside half a unit in the second decimal place. The
matrix declared a 1e-6 RELATIVE tolerance, which is what an exact oracle
needs when the artifact carries full precision, and all three failed.

So the matrix now declares the comparison precision for this figure, before
the run, as a property of what a tenure in quarters IS: quarters and
hundredths of a quarter. A third decimal is a thousandth of a quarter,
which is under a day and is not a credit fact.

The report says which basis was used. A pass at a declared precision is a
weaker statement than a pass against the exact oracle, and hiding that the
submitted SQL rounded would be the wrong kind of green.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.run_store import RunStore

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")

TENANT = "t-uat"

#: The first pass, exactly as the preserved evidence holds it.
PUBLISHED = {"1": 16.62, "2": 6.10, "3": 5.70}


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


@pytest.fixture(scope="module")
def index(uat):
    return {j.jid: j for j in uat.matrix()}


def _settled(db: Path, *, rows: list[dict]) -> tuple[RunStore, str]:
    store = RunStore(db)
    thread_id = store.create_thread(
        tenant_id=TENANT, principal_id="u1", domain_id="corporate",
        release_id="rel-1")
    record, _ = store.accept_run(
        thread_id=thread_id, tenant_id=TENANT, principal_id="u1",
        question="q", mode="standard", release_id="rel-1",
        domain_id="corporate", release_fingerprint="fp-1", ui_filters={},
        idempotency_key="", body_digest="", startup_sha="live-uat",
        deadline_at="")
    artifact_id = store.put_artifact(
        run_id=record.run_id, tenant_id=TENANT, kind="table",
        release_id="rel-1", scope={"release_fingerprint": "fp-1"},
        columns=["stage", "avg_quarters_in_stage"], rows=rows)
    store.update_state(
        record.run_id, expect_version=record.version, state=st.COMPLETED,
        terminal=True,
        final_response={
            "disposition": "answer", "narrative": "n",
            "numeric_claims": [{
                "claim_id": "tenure", "unit": "quarters",
                "evidence": {"artifact_id": artifact_id, "row_key": "r0",
                             "column_id": "avg_quarters_in_stage"}}],
            "tables": [{"title": "t", "artifact_id": artifact_id,
                        "columns": ["stage", "avg_quarters_in_stage"]}],
            "charts": []})
    store.close()
    return store, record.run_id


def _report(uat, index, tmp_path, published, name="l08.sqlite3"):
    rows = [{"stage": k, "avg_quarters_in_stage": v}
            for k, v in sorted(published.items())]
    store, run_id = _settled(tmp_path / name, rows=rows)
    taken = uat.capture(store, run_id, TENANT)
    return uat.reconcile(index["L08"], taken, store=store, tenant_id=TENANT)


# ---- the reproduction --------------------------------------------------

def test_the_oracle_and_the_first_pass_differ_only_by_rounding(index):
    """The premise, measured against the live book."""
    from decimal import Decimal

    truth = index["L08"].oracle()
    assert set(truth) == set(PUBLISHED)
    half_a_unit = Decimal("0.005")
    for stage, exact in truth.items():
        delta = abs(Decimal(str(PUBLISHED[stage])) - Decimal(str(exact)))
        assert 0 < delta <= half_a_unit, (stage, exact, delta)


def test_the_old_relative_tolerance_fails_all_three(uat, index):
    """The second-rejudge result, reproduced exactly."""
    truth = index["L08"].oracle()
    for stage, exact in truth.items():
        assert uat._close(PUBLISHED[stage], exact, 1e-6) is False, stage


@pytest.mark.parametrize("stage", ["1", "2", "3"])
def test_each_stage_passes_at_the_declared_precision(uat, index, stage):
    exact = index["L08"].oracle()[stage]
    assert uat._close(PUBLISHED[stage], exact, index["L08"].tolerance,
                      index["L08"].comparison_decimals) is True


def test_the_whole_journey_reconciles(uat, index, tmp_path):
    report = _report(uat, index, tmp_path, PUBLISHED)
    assert report["ok"] is True, report
    assert sorted(report["rows_matched"]) == ["1", "2", "3"]
    assert report["rows_mismatched"] == []


# ---- the basis is declared, and said out loud -------------------------

def test_the_matrix_declares_the_precision_before_the_run(index):
    journey = index["L08"]
    assert journey.comparison_decimals == 2
    assert "two-decimal figure" in journey.comparison_reason
    assert journey.tolerance == pytest.approx(1e-6), (
        "the case's exact tolerance is untouched; the precision is declared "
        "beside it, not instead of it")


def test_the_report_says_which_basis_it_used(uat, index, tmp_path):
    report = _report(uat, index, tmp_path, PUBLISHED)
    assert report["comparison_basis"] == "declared_precision"
    assert report["comparison_precision"] == 2
    assert report["mapping"]["comparison_reason"]
    assert report["claims"][0]["comparison_basis"] == "declared_precision"


def test_a_journey_with_no_declaration_says_so(uat, index, tmp_path):
    """L01 is compared against the exact oracle and must still say so."""
    from test_rejudge_oracle_mapping import _settled as settled_generic

    journey = index["L01"]
    truth = journey.oracle()
    rows = [{"sector": k, "ead_sar_mn": float(v)}
            for k, v in sorted(truth.items())]
    store, run_id = settled_generic(
        tmp_path / "l01.sqlite3", columns=["sector", "ead_sar_mn"],
        rows=rows, value_column="ead_sar_mn")
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(journey, taken, store=store, tenant_id=TENANT)

    assert report["comparison_basis"] == "relative_tolerance"
    assert report["comparison_precision"] == -1
    assert report["ok"] is True


# ---- and a genuine error still fails ----------------------------------

@pytest.mark.parametrize("wrong,stage", [
    (6.09, "2"),      # one hundredth low: outside the interval
    (6.11, "2"),      # one hundredth high
    (16.63, "1"),
    (5.69, "3"),
    (6.0, "2"),       # a tenth out
    (60.96, "2"),     # a tenfold scale error
])
def test_a_value_outside_the_rounding_interval_fails(uat, index, wrong,
                                                     stage):
    exact = index["L08"].oracle()[stage]
    assert uat._close(wrong, exact, index["L08"].tolerance, 2) is False


def test_the_interval_is_exactly_half_a_unit_in_the_last_place(uat):
    """The boundary, measured rather than described.

    6.096634... + 0.005 = 6.101634..., so 6.1017 is out and 6.1016 is in.
    """
    exact = 6.096634281748786
    assert uat._close(6.1016, exact, 1e-6, 2) is True
    assert uat._close(6.1017, exact, 1e-6, 2) is False
    assert uat._close(6.0917, exact, 1e-6, 2) is True
    assert uat._close(6.0916, exact, 1e-6, 2) is False


def test_a_corrupted_stage_fails_the_whole_journey(uat, index, tmp_path):
    wrong = dict(PUBLISHED)
    wrong["2"] = 6.09
    report = _report(uat, index, tmp_path, wrong, name="bad.sqlite3")
    assert report["ok"] is False
    assert report["rows_mismatched"] == ["2"]


def test_that_guard_fails_when_the_declaration_is_removed(uat, index,
                                                          tmp_path,
                                                          monkeypatch):
    """The mutation check. Undeclare the precision and the second-rejudge
    result comes straight back."""
    from dataclasses import replace

    strict = replace(index["L08"], comparison_decimals=-1)
    rows = [{"stage": k, "avg_quarters_in_stage": v}
            for k, v in sorted(PUBLISHED.items())]
    store, run_id = _settled(tmp_path / "mut.sqlite3", rows=rows)
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(strict, taken, store=store, tenant_id=TENANT)

    assert report["comparison_basis"] == "relative_tolerance"
    assert report["ok"] is False
    assert sorted(report["rows_mismatched"]) == ["1", "2", "3"]


# ---- nothing else moved ------------------------------------------------

def test_no_other_journey_declares_a_precision(index):
    """Declared for the one figure that needs it, not raised globally."""
    declared = sorted(j.jid for j in index.values()
                      if j.comparison_decimals >= 0)
    assert declared == ["L08"]


def test_every_other_oracle_journey_keeps_its_own_tolerance(uat, index):
    """The tolerances came from the question bank and are unchanged."""
    import importlib

    qb = importlib.import_module("cockpit_v4.question_bank")
    cases = {c.case_id: c for c in qb.all_cases()}
    for jid, case_id in (("L01", "C01"), ("L02", "R01"), ("L04", "R05"),
                         ("L05", "C10"), ("L08", "C35"), ("L10", "R34"),
                         ("L11", "C05"), ("L12", "R15")):
        assert index[jid].tolerance == cases[case_id].tolerance, jid
