"""
The live EAD failure, and the evidence contract that now answers it.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call is made here.

The run this module exists for did everything right and was refused anyway:

    claim 'total_ead' names row 'all sectors', which is not in artifact ...
    claim 'top4_pct' names row 'top 4 sectors', which is not in artifact ...

Both figures are real and both are wanted. Neither is a cell, because the
query grouped by sector. The first half of this module proves the old
behaviour was a refusal and still is -- an invented row is still refused,
because loosening that would be the wrong fix. The second half proves the
same answer expressed as derivations is recomputed and accepted, and that a
derivation whose arithmetic is wrong is refused just as firmly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import Rejection, parse_final

SECTORS = [
    {"sector_name": "Information Technology", "ead_reported_sar_mn": "5231.58"},
    {"sector_name": "Real Estate", "ead_reported_sar_mn": "4102.10"},
    {"sector_name": "Manufacturing", "ead_reported_sar_mn": "3980.25"},
    {"sector_name": "Construction", "ead_reported_sar_mn": "2411.77"},
    {"sector_name": "Power and Utilities", "ead_reported_sar_mn": "1877.40"},
    {"sector_name": "Agriculture", "ead_reported_sar_mn": "1200.00"},
]
TOTAL = sum(Decimal(r["ead_reported_sar_mn"]) for r in SECTORS)
TOP4 = sum(Decimal(r["ead_reported_sar_mn"]) for r in SECTORS[:4])
ALL_ROWS = [deriv.row_id_for(i) for i in range(len(SECTORS))]


@pytest.fixture
def artifact(store_db, release_id):
    """One executed result, shaped exactly like the live EAD-by-sector run."""
    artifact_id = store_db.put_artifact(
        run_id="run-live", tenant_id="demo-tenant", kind="result",
        release_id=release_id, scope={},
        columns=["sector_name", "ead_reported_sar_mn"], rows=SECTORS)
    return artifact_id


@pytest.fixture
def finalizer(store_db, artifact, release_id):
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.finalization import Finalizer

    return Finalizer(store=store_db, tenant_id="demo-tenant",
                     release_id=release_id, limits=STANDARD_LIMITS,
                     run_artifacts={artifact})


def _cells(artifact_id, row_ids, column="ead_reported_sar_mn"):
    return {"artifact_id": artifact_id, "column_id": column,
            "row_ids": list(row_ids)}


# ---- 1. the live payload, verbatim, is still refused -------------------

def test_the_live_failure_payload_is_still_refused(finalizer, artifact):
    """An invented row is not evidence, and loosening that is not the fix."""
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Total exposure is {{claim.total_ead}}.",
        numeric_claims=[{
            "claim_id": "total_ead", "decimal_value": str(TOTAL),
            "unit": "SAR million", "display_precision": 2,
            "evidence": {"artifact_id": artifact,
                         "row_key": "all sectors",
                         "column_id": "ead_reported_sar_mn"}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert not report.ok
    assert any("all sectors" in p for p in report.problems)


def test_the_live_top_n_payload_is_still_refused(finalizer, artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="The largest four carry {{claim.top4_pct}}.",
        numeric_claims=[{
            "claim_id": "top4_pct", "decimal_value": "61.23",
            "unit": "percent", "display_precision": 2,
            "evidence": {"artifact_id": artifact,
                         "row_key": "top 4 sectors",
                         "column_id": "ead_reported_sar_mn"}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert not report.ok
    assert any("top 4 sectors" in p for p in report.problems)


# ---- 2. the same answer, expressed as derivations, is accepted ---------

def test_a_total_across_rows_is_recomputed_and_accepted(finalizer, artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Total exposure at default is {{claim.total_ead}}.",
        numeric_claims=[{
            "claim_id": "total_ead", "decimal_value": str(TOTAL),
            "unit": "SAR million", "display_precision": 2,
            "derivation": {"operation": "sum",
                           "operands": [_cells(artifact, ALL_ROWS)]}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert report.ok, report.problems
    assert "{{claim." not in report.rendered_narrative
    # The claim asked for two decimal places. It is an amount, so it gets
    # none -- and the answer is published rather than sent back over it.
    assert f"SAR {TOTAL:,.0f} million" in report.rendered_narrative


def test_a_top_n_share_is_recomputed_and_accepted(finalizer, artifact):
    expected = Decimal(100) * TOP4 / TOTAL
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="The four largest sectors carry {{claim.top4_pct}}.",
        numeric_claims=[{
            "claim_id": "top4_pct", "decimal_value": str(expected),
            "unit": "percent", "display_precision": 1,
            "derivation": {
                "operation": "percentage",
                "operands": [_cells(artifact, ALL_ROWS[:4]),
                             _cells(artifact, ALL_ROWS)]}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert report.ok, report.problems
    # Rendered at the precision the CLASS governs, from the value the server
    # recomputed -- not at the one decimal place the claim asked for, and
    # not from anything the test asserted independently.
    shown = expected.quantize(Decimal("0.01"))
    assert f"{shown}%" in report.rendered_narrative


def test_a_deliberately_wrong_share_is_refused(finalizer, artifact):
    """The arithmetic is redone; citing real rows is not enough."""
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="The four largest sectors carry {{claim.top4_pct}}.",
        numeric_claims=[{
            "claim_id": "top4_pct", "decimal_value": "61.23",
            "unit": "percent", "display_precision": 2,
            "derivation": {
                "operation": "percentage",
                "operands": [_cells(artifact, ALL_ROWS[:4]),
                             _cells(artifact, ALL_ROWS)]}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert not report.ok
    assert any("61.23" in p and "evidence gives" in p
               for p in report.problems), report.problems


def test_a_wrong_total_is_refused_even_though_every_row_is_real(finalizer,
                                                                artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Total exposure is {{claim.total_ead}}.",
        numeric_claims=[{
            "claim_id": "total_ead", "decimal_value": "99999.99",
            "unit": "SAR million", "display_precision": 2,
            "derivation": {"operation": "sum",
                           "operands": [_cells(artifact, ALL_ROWS)]}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert not report.ok
    assert any("99999.99" in p and "evidence gives" in p
               for p in report.problems), report.problems


def test_a_direct_claim_still_works_unchanged(finalizer, artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Information Technology holds {{claim.it_ead}}.",
        numeric_claims=[{
            "claim_id": "it_ead", "decimal_value": "5231.58",
            "unit": "SAR million", "display_precision": 2,
            "evidence": {"artifact_id": artifact, "row_key": "r0",
                         "column_id": "ead_reported_sar_mn"}}])
    report = finalizer.validate(parse_final(body), executed=True)
    assert report.ok, report.problems


def test_a_claim_may_not_carry_both_forms(artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="x {{claim.a}}",
        numeric_claims=[{
            "claim_id": "a", "decimal_value": "1", "unit": "SAR million",
            "evidence": {"artifact_id": artifact, "row_key": "r0",
                         "column_id": "ead_reported_sar_mn"},
            "derivation": {"operation": "sum",
                           "operands": [_cells(artifact, ALL_ROWS)]}}])
    with pytest.raises(Rejection) as caught:
        parse_final(body)
    assert "not both" in str(caught.value)


def test_a_claim_must_carry_one_of_them(artifact):
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="x {{claim.a}}",
        numeric_claims=[{"claim_id": "a", "decimal_value": "1",
                         "unit": "SAR million"}])
    with pytest.raises(Rejection) as caught:
        parse_final(body)
    assert "neither" in str(caught.value)


# ---- 3. fourteen ways a derivation can be wrong ------------------------
#
# Each must fail, and fail by NAME: a message that says which claim, which
# reference and what is wrong with it, because that message is the entire
# repair mechanism the analyst has.

def _validate(finalizer, artifact, **over):
    claim = {"claim_id": "x", "decimal_value": "1", "unit": "SAR million",
             "display_precision": 2}
    claim.update(over)
    body = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                 narrative="v {{claim.x}}", numeric_claims=[claim])
    return finalizer.validate(parse_final(body), executed=True)


def test_a_row_that_does_not_exist_is_named_in_the_refusal(finalizer,
                                                           artifact):
    report = _validate(finalizer, artifact, decimal_value=str(TOTAL),
                       derivation={"operation": "sum", "operands": [
                           _cells(artifact, ALL_ROWS + ["r99"])]})
    assert not report.ok
    assert any("r99" in p and "r0 to r5" in p for p in report.problems)


def test_a_column_that_does_not_exist_is_named_in_the_refusal(finalizer,
                                                              artifact):
    report = _validate(finalizer, artifact, derivation={
        "operation": "sum",
        "operands": [_cells(artifact, ALL_ROWS, column="ecl_reported")]})
    assert not report.ok
    assert any("ecl_reported" in p and "ead_reported_sar_mn" in p
               for p in report.problems)


def test_an_artifact_this_run_did_not_produce_is_refused(finalizer):
    report = _validate(finalizer, "art-somebody-elses", derivation={
        "operation": "sum",
        "operands": [_cells("art-somebody-elses", ["r0"])]})
    assert not report.ok
    assert any("did not produce" in p for p in report.problems)


def test_an_unauthorized_artifact_is_refused(store_db, release_id, artifact):
    """Another tenant's artifact, referenced by a run that knows its id."""
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.finalization import Finalizer

    theirs = store_db.put_artifact(
        run_id="run-theirs", tenant_id="other-bank", kind="result",
        release_id=release_id, scope={}, columns=["sector_name", "x"],
        rows=[{"sector_name": "IT", "x": "1"}])
    finalizer = Finalizer(store=store_db, tenant_id="demo-tenant",
                          release_id=release_id, limits=STANDARD_LIMITS,
                          run_artifacts={artifact, theirs})
    report = _validate(finalizer, theirs, derivation={
        "operation": "sum", "operands": [_cells(theirs, ["r0"], column="x")]})
    assert not report.ok
    assert any("not available to you" in p for p in report.problems)


def test_a_duplicated_row_reference_is_refused(finalizer, artifact):
    report = _validate(finalizer, artifact, derivation={
        "operation": "sum",
        "operands": [_cells(artifact, ["r0", "r0", "r1"])]})
    assert not report.ok
    assert any("more than once" in p for p in report.problems)


def test_dividing_by_zero_is_named_rather_than_published(store_db,
                                                         release_id):
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.finalization import Finalizer

    zeros = store_db.put_artifact(
        run_id="r", tenant_id="demo-tenant", kind="result",
        release_id=release_id, scope={}, columns=["sector_name", "ecl"],
        rows=[{"sector_name": "IT", "ecl": "0"},
              {"sector_name": "RE", "ecl": "0"}])
    finalizer = Finalizer(store=store_db, tenant_id="demo-tenant",
                          release_id=release_id, limits=STANDARD_LIMITS,
                          run_artifacts={zeros})
    report = _validate(finalizer, zeros, unit="percent", derivation={
        "operation": "percentage_change",
        "operands": [_cells(zeros, ["r0"], column="ecl"),
                     _cells(zeros, ["r1"], column="ecl")]})
    assert not report.ok
    assert any("divides by zero" in p for p in report.problems)


def test_a_null_operand_is_refused_rather_than_treated_as_zero(store_db,
                                                               release_id):
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.finalization import Finalizer

    holey = store_db.put_artifact(
        run_id="r", tenant_id="demo-tenant", kind="result",
        release_id=release_id, scope={}, columns=["sector_name", "ead"],
        rows=[{"sector_name": "IT", "ead": "10"},
              {"sector_name": "RE", "ead": None}])
    finalizer = Finalizer(store=store_db, tenant_id="demo-tenant",
                          release_id=release_id, limits=STANDARD_LIMITS,
                          run_artifacts={holey})
    report = _validate(finalizer, holey, decimal_value="10", derivation={
        "operation": "sum",
        "operands": [_cells(holey, ["r0", "r1"], column="ead")]})
    assert not report.ok
    assert any("NULL" in p and "not zero" in p for p in report.problems)


def test_a_share_whose_part_is_outside_its_whole_is_refused(finalizer,
                                                            artifact):
    report = _validate(finalizer, artifact, unit="ratio", derivation={
        "operation": "share_of_total",
        "operands": [_cells(artifact, ["r0", "r5"]),
                     _cells(artifact, ["r0", "r1", "r2"])]})
    assert not report.ok
    assert any("inside the whole" in p and "r5" in p
               for p in report.problems)


def test_a_percentage_declared_as_a_ratio_is_refused(finalizer, artifact):
    report = _validate(finalizer, artifact, unit="ratio", derivation={
        "operation": "percentage",
        "operands": [_cells(artifact, ALL_ROWS[:4]),
                     _cells(artifact, ALL_ROWS)]})
    assert not report.ok
    assert any("multiplies by a hundred" in p for p in report.problems)


def test_a_percentage_called_a_percentage_point_is_refused(finalizer,
                                                           artifact):
    report = _validate(finalizer, artifact, unit="percentage points",
                       derivation={
                           "operation": "percentage_change",
                           "operands": [_cells(artifact, ["r0"]),
                                        _cells(artifact, ["r1"])]})
    assert not report.ok
    assert any("percentage point" in p for p in report.problems)


def test_a_ratio_declared_as_a_percent_is_refused(finalizer, artifact):
    report = _validate(finalizer, artifact, unit="percent", derivation={
        "operation": "ratio",
        "operands": [_cells(artifact, ["r0"]), _cells(artifact, ALL_ROWS)]})
    assert not report.ok
    assert any("proportion of one" in p for p in report.problems)


def test_an_unsupported_operation_is_refused_by_name(finalizer, artifact):
    body = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                 narrative="v {{claim.x}}",
                 numeric_claims=[{
                     "claim_id": "x", "decimal_value": "1",
                     "unit": "SAR million",
                     "derivation": {"operation": "standard_deviation",
                                    "operands": [_cells(artifact, ALL_ROWS)]}}])
    with pytest.raises(Rejection) as caught:
        parse_final(body)
    assert "standard_deviation" in str(caught.value)
    assert "Supported operations are" in str(caught.value)


def test_the_wrong_number_of_operands_is_refused_before_any_arithmetic(
        artifact):
    body = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                 narrative="v {{claim.x}}",
                 numeric_claims=[{
                     "claim_id": "x", "decimal_value": "1", "unit": "percent",
                     "derivation": {"operation": "percentage",
                                    "operands": [_cells(artifact, ALL_ROWS)]}}])
    with pytest.raises(Rejection) as caught:
        parse_final(body)
    assert "takes 2 operands" in str(caught.value)


def test_identity_over_many_cells_is_refused(finalizer, artifact):
    report = _validate(finalizer, artifact, derivation={
        "operation": "identity", "operands": [_cells(artifact, ALL_ROWS)]})
    assert not report.ok
    assert any("exactly one cell" in p for p in report.problems)


def test_a_weighted_average_over_mismatched_rows_is_refused(finalizer,
                                                            artifact):
    report = _validate(finalizer, artifact, derivation={
        "operation": "weighted_average",
        "operands": [_cells(artifact, ["r0", "r1"]),
                     _cells(artifact, ["r1", "r2"])]})
    assert not report.ok
    assert any("same rows in the same order" in p for p in report.problems)
