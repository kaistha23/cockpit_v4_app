"""MODEL MOCK · REAL DATABASE/RUNNER · REPRODUCTION.

One metric, one display format, chosen by CreditProbe.

The defect this exists for
--------------------------
`PERMITTED[MONETARY_AMOUNT]` was `(0, 1, 2, 3)`. An analyst could declare
`display_precision: 2` for an amount, and the published answer then read:

    prose   The largest reported exposure this quarter is SAR 7,013.12 million.
    table   SAR 7,013 million
    chart   SAR 7,013 million

Three renderings of one cell, on one screen. Both precisions were legal, and
that was the problem: a display class anyone may override is not a policy,
it is a default. A credit officer reading two figures that differ has to work
out which one to put in the paper, and nothing on the page tells them.

The governed rule
-----------------
Amounts show no decimals. Percentages, probabilities, point movements and
ratios show two. Counts are integers. For those classes the class decides and
a declared precision changes nothing -- it is ignored mechanically, never
refused, because refusing it would spend a model turn correcting two
characters of presentation in an analysis that was already right.

The canonical value is untouched underneath all of it.
"""

from __future__ import annotations

import re
from decimal import Decimal

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4 import states as st

#: The value from the live defect. Fifteen digits, and one way to write it.
CANONICAL = Decimal("7013.1167117986615")

#: Four or more consecutive decimals is machine precision, not a figure.
DEBRIS = re.compile(r"\d\.\d{4,}")


# ---- the policy itself --------------------------------------------------

@pytest.mark.parametrize("unit,expected", [
    ("SAR million", 0),
    ("SAR", 0),
    ("percent", 2),
    ("probability_0_1", 2),
    ("fraction_0_1", 2),
    ("percentage points", 2),
    ("times", 2),
    ("count", 0),
])
def test_a_governed_class_answers_for_itself(unit, expected):
    """§1. The business rule, stated as the only answer available."""
    assert disp.resolve_decimals(unit) == expected
    # And it is the same answer whatever was asked for.
    for asked in (0, 1, 2, 3, 4, 7):
        assert disp.resolve_decimals(unit, asked) == expected, (
            f"{unit!r} honoured a request for {asked} decimal places")


def test_money_permits_nothing_but_zero():
    """§2. The override that produced the defect no longer exists."""
    assert disp.PERMITTED[disp.MONETARY_AMOUNT] == (0,)
    assert disp.MONETARY_AMOUNT in disp.GOVERNED


def test_an_unclassifiable_unit_still_takes_the_analysts_word():
    """Nothing named it, so there is no business rule to consult instead.

    This is the one place a declared precision does anything, and it is the
    one place where refusing to listen would leave nobody deciding at all.
    """
    unit = "widgets per fortnight"
    assert disp.classify(unit) == disp.UNKNOWN
    assert disp.UNKNOWN not in disp.GOVERNED
    assert disp.resolve_decimals(unit, 3) == 3
    assert disp.resolve_decimals(unit) == 2


# ---- the cross-check is about truth, not presentation -------------------

@pytest.mark.parametrize("asserted", [
    "7013.1167117986615",   # the canonical value itself
    "7013",                 # rounded the way it will be published
    "7013.12",              # rounded to two, as the analyst used to be told to
    "7013.1167",            # rounded to four
])
def test_a_correctly_rounded_cross_check_is_accepted_at_any_precision(
        asserted):
    """§2. No model turn is spent on a decimal point.

    The analyst may round its own cross-check however it likes. That is a
    question about whether the number is RIGHT. How the figure is written is
    a different question and CreditProbe answers it.
    """
    verdict = prec.check(asserted, CANONICAL, unit="SAR million",
                         declared_precision=2, label="claim 'top'")
    assert verdict.ok, verdict.problem
    assert verdict.precision == 0, "the amount is still published at 0dp"
    assert str(verdict.display) == "7013"
    assert verdict.canonical == CANONICAL, "canonical precision is kept"


@pytest.mark.parametrize("asserted", [
    "7013.1699",   # rounds to 7013.17, but is not this value at any precision
    "7012",        # simply wrong
    "70131.17",    # an order of magnitude out
])
def test_a_number_that_is_not_this_value_is_still_refused(asserted):
    """Loosening presentation loosened nothing about arithmetic."""
    verdict = prec.check(asserted, CANONICAL, unit="SAR million",
                         declared_precision=2, label="claim 'top'")
    assert not verdict.ok, f"{asserted} was accepted as {CANONICAL}"


# ---- the worked examples from the policy --------------------------------

@pytest.mark.parametrize("canonical,unit,expected", [
    # A. an amount, at the value that produced the defect
    (CANONICAL, "SAR million", "SAR 7,013 million"),
    # C/D. PD and LGD: a fraction underneath, a percentage on the page
    (Decimal("0.043276"), "probability_0_1", "4.33%"),
    (Decimal("0.4512889"), "fraction_0_1", "45.13%"),
    # E. a stage share
    (Decimal("0.567043449985"), "probability_0_1", "56.70%"),
    (Decimal("56.7043449985"), "percent", "56.70%"),
    # F. a count
    (Decimal("412"), "count", "412"),
    # a point movement and a ratio
    (Decimal("1.23456"), "percentage points", "1.23 pp"),
    (Decimal("1.56906461"), "times", "1.57x"),
])
def test_the_policy_worked_examples(canonical, unit, expected):
    """§1, §3, §4. One value, one string, whatever asked otherwise."""
    assert disp.format_value(canonical, unit) == expected
    for asked in (0, 1, 2, 3, 4):
        assert disp.format_value(canonical, unit,
                                 disp.resolve_decimals(unit, asked)) == expected


def test_a_percentage_is_never_scaled_twice():
    """§4. No 100x error: the scale is the class's, not the value's."""
    assert disp.format_value(Decimal("0.567043449985"),
                             "probability_0_1") == "56.70%"
    assert disp.format_value(Decimal("56.7043449985"), "percent") == "56.70%"


def test_a_count_cannot_be_talked_into_decimals():
    """§1. There is no such thing as 412.00 borrowers."""
    assert disp.format_value(Decimal("412"), "count", 
                             disp.resolve_decimals("count", 2)) == "412"


# ---- and the same rule through a whole run ------------------------------

QUESTION = "What is total exposure at default by sector in the latest quarter?"


def _step(messages) -> dict:
    from test_orchestration_recovery import _execution_result

    return _execution_result(messages)["steps"][0]


def _answer(messages, *, declared: int | None):
    """One direct claim on the largest cell, with the narrative quoting it."""
    step = _step(messages)
    row = step["preview"][0]
    column = next(c for c in step["columns"] if c != "sector_name")
    claim: dict = {
        "claim_id": "top", "unit": "SAR million",
        "evidence": {"artifact_id": step["artifact_id"],
                     "row_key": f"sector_name={row['sector_name']}",
                     "column_id": column}}
    if declared is not None:
        claim["display_precision"] = declared
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="The largest reported exposure is {{claim.top}}.",
              numeric_claims=[claim],
              tables=[{"title": "Reported EAD by sector",
                       "artifact_id": step["artifact_id"],
                       "columns": ["sector_name", column]}],
              charts=[{"kind": "bar", "artifact_id": step["artifact_id"],
                       "title": "Reported EAD by sector",
                       "x_column": "sector_name",
                       "y_columns": [column]}]))])


def _run(drive, release_id, *, declared: int | None):
    quarter = oracles.latest_quarter(release_id)
    return drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        lambda messages: _answer(messages, declared=declared)])


def _surfaces(body: dict) -> dict[str, list[str]]:
    """Every visible numeric surface of a published answer."""
    return {
        "narrative": [body["narrative"]],
        "claim": [c.get("display_value", "") for c in body["numeric_claims"]],
        "table": [str(shown)
                  for table in body["tables"] for row in table.get("rows", [])
                  for shown in row["display"].values()],
        "chart": [str(shown)
                  for chart in body["charts"]
                  for point in chart.get("points", [])
                  for shown in point["display"].values()],
    }


@pytest.mark.parametrize("declared", [None, 0, 2, 3])
def test_one_metric_one_format_across_every_surface(drive, release_id,
                                                    declared):
    """A, B, §3. Prose, claim, table and chart agree, and never retry.

    `declared=2` is the live defect, driven end to end: the analyst asks for
    two decimal places on an amount and the published answer does not carry
    them anywhere -- and does not spend a generation saying so.
    """
    outcome, provider, _ = _run(drive, release_id, declared=declared)
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, (
        "a presentation request must not cost an answer correction")

    body = outcome.response
    top = body["numeric_claims"][0]
    assert top["display_precision"] == 0
    assert top["display_value"] in body["narrative"]

    for where, values in _surfaces(body).items():
        for shown in values:
            assert not DEBRIS.search(shown), (
                f"machine precision on the {where}: {shown!r}")
        for shown in values:
            # An amount, wherever it appears, is written one way.
            for amount in re.findall(r"SAR [\d,]+(?:\.\d+)? million", shown):
                assert "." not in amount, (
                    f"the {where} published an amount with decimals: "
                    f"{amount!r}")

    # And the figure itself is the same string in the prose and the table.
    published = top["display_value"]
    assert published in _surfaces(body)["table"], (
        f"the narrative says {published!r} and no table cell does")


def test_the_canonical_value_is_still_there_underneath(drive, release_id):
    """§3. Rounding is a display act. Nothing is rounded away."""
    outcome, _, _ = _run(drive, release_id, declared=2)
    assert outcome.state == st.COMPLETED, outcome.message
    cells = [value
             for table in outcome.response["tables"]
             for row in table.get("rows", [])
             for value in row["canonical"].values()]
    assert any(len(str(c).split(".")[-1]) > 4 for c in cells), (
        "the stored canonical values were rounded, not just their display")
