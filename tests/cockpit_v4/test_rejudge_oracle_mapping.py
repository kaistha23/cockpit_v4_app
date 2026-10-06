"""The predeclared join between an oracle and a governed output.

UNIT · REAL RELEASE · REAL DATABASE. No model call, no paid provider call.

CLOSURE-03
----------
The first offline rejudge reported five journeys not-ok: L15, L12, L11, L08
and L10. Only L15 is a real failure -- that run published nothing. The other
four were numerically right and could not be compared, because the matrix
declared the join using the names the BANK's own SQL gives its columns and
a live analyst writes its own SQL:

    L12   declared `coverage`      published `cov_on_ead_pct`
    L11   declared `coverage`      published `coverage_on_ead_pct`
    L08   declared `quarters`      published `avg_quarters_in_stage`
    L10   declared key `product_region`,
          published two governed key columns, `product` and `region`

and, for the two coverage journeys, because the oracle computes a
PROPORTION of one (0.0113...) while the governed answer is a PERCENTAGE
(1.1376...).

Renaming model output to suit an oracle would be the harness marking its own
homework, so the output is left alone. What the matrix now declares, frozen
before the run, is every governed name it will accept for the figure, how to
build the oracle's key out of the output's own key columns, and any fixed
conversion between the two units. Nothing is matched by shape, by position
or by resemblance at rejudge time.

The rows below are built from the parquet with pandas, in the units and
column names a live answer published, and reconciled against the bank's
oracle. Corrupting one row, mis-stating the scale, or making the declared key
fail to identify a row must each fail.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import domain_oracles as oracle
import pytest

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.run_store import RunStore

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")

TENANT = "t-uat"


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


def _settled(db: Path, *, columns: list[str], rows: list[dict],
             value_column: str) -> tuple[RunStore, str]:
    """A settled run whose artifact is what a live answer published."""
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
        columns=columns, rows=rows)
    store.update_state(
        record.run_id, expect_version=record.version, state=st.COMPLETED,
        terminal=True,
        final_response={
            "disposition": "answer", "narrative": "n",
            "numeric_claims": [{
                "claim_id": "figure", "unit": "percent",
                "evidence": {"artifact_id": artifact_id, "row_key": "r0",
                             "column_id": value_column}}],
            "tables": [{"title": "t", "artifact_id": artifact_id,
                        "columns": columns}],
            "charts": []})
    store.close()
    return store, record.run_id


def _report(uat, store, run_id, journey):
    taken = uat.capture(store, run_id, TENANT)
    return uat.reconcile(journey, taken, store=store, tenant_id=TENANT)


# ---- the four repaired mappings ---------------------------------------

def test_l12_compares_ead_coverage_across_the_ratio_percent_boundary(
        uat, index, tmp_path):
    """The oracle is a proportion of one; the answer is a percentage."""
    journey = index["L12"]
    frame = oracle.frame(dom.RETAIL, "retail_account_month")
    latest = oracle.latest_period(dom.RETAIL)
    month = frame[frame[oracle.period_column(dom.RETAIL)] == latest]
    published = (Decimal(str(month["ecl_sar_mn"].sum()))
                 / Decimal(str(month["ead_sar_mn"].sum()))) * 100

    store, run_id = _settled(
        tmp_path / "l12.sqlite3",
        columns=["cov_on_ead_pct"],
        rows=[{"cov_on_ead_pct": float(published)}],
        value_column="cov_on_ead_pct")
    report = _report(uat, store, run_id, journey)

    assert ["cov_on_ead_pct", "100"] in report["mapping"]["accepted_values"]
    assert report["claims"][0]["oracle_scale"] == "100"
    assert report["checked"] is True
    assert report["ok"] is True, report
    assert report["claims"][0]["ok"] is True


def test_l11_compares_sector_coverage_for_every_sector(uat, index, tmp_path):
    journey = index["L11"]
    truth = journey.oracle()
    rows = [{"sector": k, "coverage_on_ead_pct": float(v) * 100}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "l11.sqlite3",
        columns=["sector", "coverage_on_ead_pct"], rows=rows,
        value_column="coverage_on_ead_pct")
    report = _report(uat, store, run_id, journey)

    assert report["ok"] is True, report
    assert sorted(report["rows_matched"]) == sorted(truth)
    assert report["rows_mismatched"] == []
    assert report["rows_missing_from_the_answer"] == []


def test_l08_compares_stage_tenure_under_its_live_alias(uat, index,
                                                        tmp_path):
    journey = index["L08"]
    truth = journey.oracle()
    rows = [{"stage": k, "avg_quarters_in_stage": float(v)}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "l08.sqlite3",
        columns=["stage", "avg_quarters_in_stage"], rows=rows,
        value_column="avg_quarters_in_stage")
    report = _report(uat, store, run_id, journey)

    assert report["value_column_used"] == "avg_quarters_in_stage"
    assert report["ok"] is True, report
    assert sorted(report["rows_matched"]) == sorted(truth)


def test_l10_compares_every_product_region_cell(uat, index, tmp_path):
    """The oracle keys on one concatenation; the answer returns two
    governed columns, which is the better result."""
    journey = index["L10"]
    truth = journey.oracle()
    rows = []
    for key, value in sorted(truth.items()):
        product, _, region = key.partition(" / ")
        rows.append({"product": product, "region": region,
                     "ead_sar_mn": float(value)})

    store, run_id = _settled(
        tmp_path / "l10.sqlite3",
        columns=["product", "region", "ead_sar_mn"], rows=rows,
        value_column="ead_sar_mn")
    report = _report(uat, store, run_id, journey)

    assert ["product", "region"] in report["mapping"]["key_shapes"]
    assert report["ok"] is True, report
    assert len(report["rows_matched"]) == len(truth) > 1
    assert report["rows_missing_from_the_answer"] == []


# ---- and it still discriminates ---------------------------------------

def test_a_corrupted_row_fails(uat, index, tmp_path):
    journey = index["L11"]
    truth = journey.oracle()
    worst = sorted(truth)[0]
    rows = [{"sector": k,
             "coverage_on_ead_pct": float(v) * (0 if k == worst else 100)}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "bad.sqlite3",
        columns=["sector", "coverage_on_ead_pct"], rows=rows,
        value_column="coverage_on_ead_pct")
    report = _report(uat, store, run_id, journey)

    assert report["ok"] is False
    assert report["rows_mismatched"] == [worst]


def test_a_scale_error_fails(uat, index, tmp_path):
    """The proportion published as if it were the percentage."""
    journey = index["L11"]
    truth = journey.oracle()
    rows = [{"sector": k, "coverage_on_ead_pct": float(v)}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "scale.sqlite3",
        columns=["sector", "coverage_on_ead_pct"], rows=rows,
        value_column="coverage_on_ead_pct")
    report = _report(uat, store, run_id, journey)

    assert report["ok"] is False
    assert len(report["rows_mismatched"]) == len(truth), (
        "a hundredfold error on every row is not a rounding difference")


def test_a_composite_key_collision_fails(uat, index, tmp_path):
    """A declared key that does not identify a row cannot be compared."""
    journey = index["L10"]
    truth = journey.oracle()
    first = sorted(truth)[0]
    product, _, region = first.partition(" / ")
    rows = [{"product": product, "region": region,
             "ead_sar_mn": float(truth[first])} for _ in range(2)]

    store, run_id = _settled(
        tmp_path / "collide.sqlite3",
        columns=["product", "region", "ead_sar_mn"], rows=rows,
        value_column="ead_sar_mn")
    report = _report(uat, store, run_id, journey)

    assert report["key_collision"] == first
    assert report["published_rows"] == {}
    assert report["ok"] is False


def test_an_output_name_nobody_declared_is_not_matched(uat, index, tmp_path):
    """No resemblance matching. An undeclared name is a miss, reported."""
    journey = index["L11"]
    truth = journey.oracle()
    rows = [{"sector": k, "some_other_alias": float(v) * 100}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "unknown.sqlite3",
        columns=["sector", "some_other_alias"], rows=rows,
        value_column="some_other_alias")
    report = _report(uat, store, run_id, journey)

    assert report["ok"] is False
    assert report["published_rows"] == {}
    assert report["rows_missing_from_the_answer"] == sorted(truth)


# ---- the declarations themselves --------------------------------------

def test_every_repaired_mapping_is_declared_in_the_matrix(index):
    assert ("cov_on_ead_pct", "100") in index["L12"].value_scales
    assert ("coverage_on_ead_pct", "100") in index["L11"].value_scales
    assert ("avg_quarters_in_stage", "1") in index["L08"].value_scales
    assert ("product", "region") in index["L10"].key_shapes
    assert index["L10"].key_separator == " / "


def test_the_bank_name_is_first_and_carries_no_conversion(uat, index):
    """A repaired mapping ADDS names; it does not replace the bank's.

    The bank's own column is the one the oracle was written beside, in the
    oracle's own unit, so it converts by one. Without that the dry run --
    which executes the bank's SQL -- would be broken by the repair.
    """
    for jid in ("L11", "L12", "L08", "L10"):
        journey = index[jid]
        accepted = uat.accepted_values_of(journey)
        assert accepted[0] == (journey.value, "1")
        assert journey.value not in {n for n, _ in journey.value_scales}
    # And the bank's own key shape is tried first.
    assert uat.key_shapes_of(index["L10"])[0] == ("product_region",)


def test_the_banks_own_shape_still_reconciles(uat, index, tmp_path):
    """The dry run runs the bank's SQL. A repair that only understood the
    live shape would report every dry run not-ok."""
    journey = index["L11"]
    truth = journey.oracle()
    rows = [{"sector": k, "coverage": float(v)}
            for k, v in sorted(truth.items())]

    store, run_id = _settled(
        tmp_path / "bank.sqlite3", columns=["sector", "coverage"],
        rows=rows, value_column="coverage")
    report = _report(uat, store, run_id, journey)

    assert report["value_column_used"] == "coverage"
    assert report["ok"] is True, report
    assert sorted(report["rows_matched"]) == sorted(truth)


def test_the_two_shapes_carry_different_conversions(uat, index):
    """The same journey accepts a proportion column and a percent column,
    and knows which is which."""
    accepted = dict(uat.accepted_values_of(index["L11"]))
    assert accepted["coverage"] == "1"
    assert accepted["coverage_on_ead_pct"] == "100"
