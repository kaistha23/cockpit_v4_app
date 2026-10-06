"""Amounts and percentages in one result, each in its own unit.

MODEL MOCK · REAL DATABASE/RUNNER · REAL SQL.

H-LIVE-04
---------
The live answer to "What is ECL coverage of exposure?" returned one row:

    ecl_total            224.556...      SAR million
    ead_total          19738.832...      SAR million
    balance_total      19619.224...      SAR million
    limit_total        35932.222...      SAR million
    cov_on_ead_pct         1.1376...     percent
    cov_on_balance_pct     1.1445...     percent
    cov_on_limit_pct       0.6249...     percent

and published `balance_total` as **19,619.22%** and `limit_total` as
**35,932.22%**. The narrative used the right concepts; the table a reader
looks at was materially wrong.

The unit came from `Finalizer._units_for`. Its first source is "a numeric
claim already bound to this artifact and column", and for a DERIVED claim it
read the claim's unit onto every operand column of the derivation. The only
appearance of `balance_total` in any claim was as the DENOMINATOR of a
coverage percentage, so it was published in percent. `ecl_total` and
`ead_total` escaped only because direct claims had already named them in SAR
million and `setdefault` keeps the first answer -- which is why two of the
four amounts were right and two were not.

The fix is a declaration in `derivation`, beside the operation table, of
which operations preserve their operands' unit -- `identity`, `sum`, `min`,
`max`, `difference` do; `percentage`, `ratio`, `share_of_total`,
`percentage_change`, `count` and `rank` do not, and `weighted_average`
preserves it for its values and not its weights. It is derived from the
arithmetic each operation performs. Nothing anywhere knows the name
`balance_total`.
"""

from __future__ import annotations

import sys
from decimal import Decimal

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_orchestration_recovery import _execution_result
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import states as st

QUESTION = "What is ECL coverage of exposure?"

FIELDS = ["cockpit_facility_quarter.ecl_reported",
          "cockpit_facility_quarter.ead_reported",
          "cockpit_facility_quarter.drawn_balance",
          "cockpit_facility_quarter.approved_limit"]

#: Four amounts and three percentages, in ONE row, exactly as the live
#: query produced them.
SQL = """
SELECT SUM(ecl_reported) AS ecl_total,
       SUM(ead_reported) AS ead_total,
       SUM(drawn_balance) AS balance_total,
       SUM(approved_limit) AS limit_total,
       100 * SUM(ecl_reported) / SUM(ead_reported) AS cov_on_ead_pct,
       100 * SUM(ecl_reported) / SUM(drawn_balance) AS cov_on_balance_pct,
       100 * SUM(ecl_reported) / SUM(approved_limit) AS cov_on_limit_pct
FROM cockpit_facility_quarter
WHERE reporting_quarter = ?
"""

AMOUNTS = ("ecl_total", "ead_total", "balance_total", "limit_total")
PERCENTAGES = ("cov_on_ead_pct", "cov_on_balance_pct", "cov_on_limit_pct")


def _cell(artifact: str, column: str) -> dict:
    return {"artifact_id": artifact, "column_id": column, "row_ids": ["r0"]}


def _answer(messages):
    """The shape of the live answer.

    The amounts the analyst cites are bound directly, in SAR million; the
    three coverage figures are bound directly to the query's own percentage
    columns, in percent; and two of them are ALSO cross-checked with a
    `percentage` derivation over the amounts. Those two derivations are the
    only place `balance_total` and `limit_total` appear in any claim -- as
    denominators -- and that is the condition that published them as
    19,619.22% and 35,932.22%.
    """
    step = _execution_result(messages)["steps"][0]
    artifact = step["artifact_id"]

    def direct(claim_id: str, column: str, unit: str) -> dict:
        return {"claim_id": claim_id, "unit": unit,
                "evidence": {"artifact_id": artifact, "row_key": "r0",
                             "column_id": column}}

    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative=("ECL of {{claim.ecl}} against exposure of "
                         "{{claim.ead}} is {{claim.cov_ead}} of EAD, "
                         "{{claim.cov_balance}} of drawn balance and "
                         "{{claim.cov_limit}} of approved limit. "
                         "Recomputed from the amounts, coverage of balance "
                         "is {{claim.cov_balance_check}} and coverage of "
                         "limit is {{claim.cov_limit_check}}."),
              numeric_claims=[
                  direct("ecl", "ecl_total", "SAR million"),
                  direct("ead", "ead_total", "SAR million"),
                  direct("cov_ead", "cov_on_ead_pct", "percent"),
                  direct("cov_balance", "cov_on_balance_pct", "percent"),
                  direct("cov_limit", "cov_on_limit_pct", "percent"),
                  {"claim_id": "cov_balance_check", "unit": "percent",
                   "derivation": {"operation": "percentage", "operands": [
                       _cell(artifact, "ecl_total"),
                       _cell(artifact, "balance_total")]}},
                  {"claim_id": "cov_limit_check", "unit": "percent",
                   "derivation": {"operation": "percentage", "operands": [
                       _cell(artifact, "ecl_total"),
                       _cell(artifact, "limit_total")]}},
              ],
              tables=[{"title": "ECL coverage of exposure",
                       "artifact_id": artifact,
                       "columns": [*AMOUNTS, *PERCENTAGES]}]))])


@pytest.fixture
def published(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            SQL, purpose="ECL coverage of exposure", grain="portfolio",
            units="SAR million", subquestions=["ECL coverage"],
            fields=FIELDS, quarter=quarter)]),
        _answer])
    assert outcome.state == st.COMPLETED, outcome.message
    return outcome.response


# ---- the reproduction --------------------------------------------------

def test_an_amount_that_is_only_ever_a_denominator_is_not_a_percentage(
        published):
    """The exact defect. `balance_total` and `limit_total` appear in no
    claim except as the denominator of a coverage percentage."""
    units = published["tables"][0]["column_units"]
    assert units.get("balance_total") != "percent"
    assert units.get("limit_total") != "percent"


def test_the_published_amounts_do_not_read_as_percentages(published):
    table = published["tables"][0]
    shown = table["rows"][0]["display"]
    for column in AMOUNTS:
        assert not str(shown[column]).endswith("%"), (
            f"{column} was published as {shown[column]}, which is the live "
            f"defect: an amount written as a percentage")


def test_the_coverage_columns_are_still_percentages(published):
    """The other half. A fix that unset every unit would pass the test
    above and would be a different wrong table."""
    table = published["tables"][0]
    units = table["column_units"]
    shown = table["rows"][0]["display"]
    for column in PERCENTAGES:
        assert units[column] == "percent", (
            f"{column} lost its unit: {units.get(column)!r}")
        assert str(shown[column]).endswith("%"), shown[column]


def test_the_two_kinds_coexist_in_one_row(published):
    """Amount columns and percentage columns in the SAME result."""
    table = published["tables"][0]
    units = table["column_units"]
    assert {units[c] for c in PERCENTAGES} == {"percent"}
    assert "percent" not in {units.get(c) for c in AMOUNTS}
    # And the amounts a claim DID name are money, as they always were.
    assert units["ecl_total"] == "SAR million"
    assert units["ead_total"] == "SAR million"


def test_the_canonical_values_are_untouched(published):
    """Only the unit was wrong. The numbers were always the artifact's."""
    row = published["tables"][0]["rows"][0]
    ecl = Decimal(str(row["canonical"]["ecl_total"]))
    ead = Decimal(str(row["canonical"]["ead_total"]))
    balance = Decimal(str(row["canonical"]["balance_total"]))
    assert ecl > 0 and ead > ecl and balance > ecl
    assert Decimal(str(row["canonical"]["cov_on_ead_pct"])) == pytest.approx(
        Decimal(100) * ecl / ead)


# ---- the declaration this rests on ------------------------------------

@pytest.mark.parametrize("operation,expected", [
    ("identity", 1), ("sum", 1), ("min", 1), ("max", 1),
    ("difference", 2),
    ("ratio", 0), ("percentage", 0), ("percentage_change", 0),
    ("share_of_total", 0), ("count", 0), ("rank", 0),
    ("result_rows", 0),
    ("weighted_average", 1),
])
def test_which_operations_carry_their_unit_onto_their_operands(operation,
                                                               expected):
    arity = deriv.OPERATIONS[operation][0]
    body = {"operation": operation, "operands": [
        {"artifact_id": "a", "column_id": f"c{i}", "row_ids": ["r0"]}
        for i in range(arity)]}
    parsed = deriv.parse(body)
    assert len(deriv.operands_in_the_result_unit(parsed)) == expected


def test_every_operation_is_classified():
    """A new operation must be decided about, not defaulted."""
    classified = (deriv.UNIT_PRESERVING_OPERATIONS
                  | deriv.FIRST_OPERAND_CARRIES_THE_UNIT
                  | deriv.UNIT_CHANGING_OPERATIONS)
    assert set(deriv.OPERATIONS) == classified, (
        f"unclassified: {set(deriv.OPERATIONS) - classified}")


def test_the_three_classes_do_not_overlap():
    """One operation, one answer to 'is my operand in my unit?'."""
    sets = (deriv.UNIT_PRESERVING_OPERATIONS,
            deriv.FIRST_OPERAND_CARRIES_THE_UNIT,
            deriv.UNIT_CHANGING_OPERATIONS)
    for i, left in enumerate(sets):
        for right in sets[i + 1:]:
            assert not (left & right), (left & right)


# ---- the mutation check ------------------------------------------------

def test_that_guard_fails_against_the_old_propagation(drive, release_id,
                                                      monkeypatch):
    """Restore the old behaviour -- every operand takes the claim's unit --
    and the table must come back with amounts written as percentages."""
    monkeypatch.setattr(deriv, "operands_in_the_result_unit",
                        lambda derivation: derivation.operands)
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            SQL, purpose="ECL coverage of exposure", grain="portfolio",
            units="SAR million", subquestions=["ECL coverage"],
            fields=FIELDS, quarter=quarter)]),
        _answer])
    assert outcome.state == st.COMPLETED, outcome.message

    table = outcome.response["tables"][0]
    assert table["column_units"]["balance_total"] == "percent", (
        "the mutation did not reproduce the defect, so the guard above is "
        "not testing what it claims to")
    assert str(table["rows"][0]["display"]["balance_total"]).endswith("%")
