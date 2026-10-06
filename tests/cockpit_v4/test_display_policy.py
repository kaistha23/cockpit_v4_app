"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

One numeric display policy, and what it says.

Formatting used to be in four places -- the prompt, the validator, the
frontend and whatever string the model happened to send -- and none of them
was the authority. A correct figure could be refused for being written the
way a credit officer writes it, and a run whose SQL had already succeeded
spent another model call removing two decimal places.

These tests pin the policy: what class a unit is, how many decimals that
class shows, and how the number reads once it is published. Every expected
string here is taken from the round's specification, so a change to the
policy has to change this file deliberately.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import display as disp


# ---- §14: the classes and their decimals -------------------------------

@pytest.mark.parametrize("kind,places", [
    (disp.MONETARY_AMOUNT, 0),
    (disp.PERCENTAGE, 2),
    (disp.PROBABILITY, 2),
    (disp.PERCENTAGE_POINT, 2),
    (disp.RATIO, 2),
    (disp.COUNT, 0),
    (disp.INTEGER, 0),
])
def test_each_class_shows_the_decimals_the_policy_says(kind, places):
    assert disp.DECIMALS[kind] == places


def test_a_count_may_not_declare_decimal_places():
    """There is no such thing as 22.5 borrowers."""
    assert disp.PERMITTED[disp.COUNT] == (0,)
    assert disp.PERMITTED[disp.INTEGER] == (0,)


def test_a_rating_and_a_stage_are_not_numbers():
    assert disp.RATING in disp.CATEGORICAL
    assert disp.IFRS_STAGE in disp.CATEGORICAL


# ---- §15: the catalogue's own units decide -----------------------------

CATALOGUE_CASES = [
    ("cockpit_facility_quarter", "ead_reported", disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "ecl_reported", disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "ecl_12m_reported",
     disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "ecl_lifetime_reported",
     disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "gross_carrying_amount",
     disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "drawn_balance", disp.MONETARY_AMOUNT, 0),
    ("cockpit_facility_quarter", "pd_pit_12m", disp.PROBABILITY, 2),
    ("cockpit_facility_quarter", "lgd_pit", disp.PROBABILITY, 2),
    ("cockpit_facility_quarter", "ecl_coverage_ratio", disp.PROBABILITY, 2),
    ("cockpit_facility_quarter", "collateral_coverage_ratio", disp.RATIO, 2),
    ("cockpit_facility_quarter", "days_past_due", disp.COUNT, 0),
]


@pytest.mark.parametrize("relation,column,kind,places", CATALOGUE_CASES)
def test_a_field_is_classified_by_what_the_release_says_it_holds(
        runtime, relation, column, kind, places):
    """Not by its name. `ecl_coverage_ratio` is not money for starting ECL."""
    unit = disp.unit_for_field(runtime.catalog, relation, column)
    assert disp.classify(unit) == kind, f"{column} unit={unit!r}"
    assert disp.decimals(unit) == places


def test_a_money_field_resolves_to_the_release_denomination(runtime):
    """`RCY` is not something a reader can be shown."""
    unit = disp.unit_for_field(runtime.catalog, "cockpit_facility_quarter",
                               "ead_reported")
    assert unit == "SAR million"


# ---- §17: PD and LGD are fractions, and the policy says so -------------

def test_pd_is_a_fraction_underneath_and_a_percentage_on_the_page():
    """The catalogue calls it probability_0_1, so 0.043276 IS 4.33%."""
    assert disp.classify("probability_0_1") == disp.PROBABILITY
    assert disp.DISPLAY_FACTOR[disp.PROBABILITY] == 100
    assert disp.format_value(Decimal("0.043276"), "probability_0_1") \
        == "4.33%"


def test_a_percentage_already_in_percent_is_not_scaled_again():
    """PROBABILITY and PERCENTAGE are different classes for this reason."""
    assert disp.format_value(Decimal("4.3276"), "percent") == "4.33%"


def test_the_scale_is_never_inferred_from_the_magnitude(runtime):
    """0.04 is an ordinary figure in percent as well as in fractions.

    A policy that guessed between them from the value would publish a PD two
    orders of magnitude wrong on exactly the small numbers that matter.
    """
    small = Decimal("0.04")
    assert disp.format_value(small, "probability_0_1") == "4.00%"
    assert disp.format_value(small, "percent") == "0.04%"


# ---- §18: percent and percentage point are not interchangeable ---------

def test_a_movement_in_points_is_not_a_percentage_change():
    """5.20% to 7.30% is +2.10 pp and +40.38%. Both true, not the same."""
    assert disp.format_value(Decimal("2.10"), "pp") == "2.10 pp"
    assert disp.format_value(Decimal("40.3846"), "percent") == "40.38%"
    assert disp.classify("pp") != disp.classify("percent")


# ---- §12, §13, §34: the round's own worked examples --------------------

@pytest.mark.parametrize("canonical,unit,expected", [
    ("40599.1736630513815", "SAR million", "SAR 40,599 million"),
    ("3421.173663", "SAR million", "SAR 3,421 million"),
    ("0.612372", "fraction", "61.24%"),
    ("0.043276", "probability_0_1", "4.33%"),
    ("1.2537", "times", "1.25x"),
    ("47", "borrowers", "47 borrowers"),
    ("2", "ifrs9 stage", "2"),
])
def test_the_worked_examples_render_as_specified(canonical, unit, expected):
    assert disp.format_value(Decimal(canonical), unit) == expected


# ---- §16: full precision underneath ------------------------------------

def test_rounding_happens_once_at_the_end_and_never_before(runtime):
    """Summing rounded parts is a different number from rounding the sum."""
    parts = [Decimal("3421.6"), Decimal("2810.6"), Decimal("1500.6")]
    canonical = sum(parts, Decimal(0))
    rounded_then_summed = sum(
        (disp.quantize(p, 0) for p in parts), Decimal(0))
    assert disp.quantize(canonical, 0) == Decimal("7733")
    assert rounded_then_summed == Decimal("7734")
    assert disp.quantize(canonical, 0) != rounded_then_summed, (
        "this is the arithmetic the rule exists to prevent")


def test_the_canonical_value_is_never_written_in_exponent_notation():
    assert str(disp.plain(Decimal("1E+3"))) == "1000"
    assert str(disp.plain(Decimal("0E+12"))) == "0"


def test_negative_zero_is_zero():
    """"SAR -0 million" reads as a loss too small to name."""
    assert disp.quantize(Decimal("-0.0001"), 0) == Decimal(0)
    assert not str(disp.quantize(Decimal("-0.0001"), 0)).startswith("-")


# ---- fail closed -------------------------------------------------------

def test_a_unit_that_names_no_currency_publishes_no_currency():
    """An honest bare number beats an invented denomination."""
    assert disp.format_value(Decimal("40599.17"), "amount") == "40,599"


def test_a_word_is_not_mistaken_for_a_currency_code():
    """The space is what keeps the open scale word honest."""
    for word in ("notional", "headroom", "exposure"):
        assert disp.classify(word) == disp.UNKNOWN, word


def test_an_unknown_unit_is_not_silently_called_money():
    assert disp.classify("") == disp.UNKNOWN
    assert disp.classify("widgets per fortnight") == disp.UNKNOWN
