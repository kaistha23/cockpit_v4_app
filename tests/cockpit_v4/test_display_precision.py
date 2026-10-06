"""
The live rounding failure, and the canonical/display contract that answers it.

MODEL MOCK · REAL DATABASE. No paid provider call.

The run this module exists for computed exposure at default by sector
correctly and was then refused at publication:

    asserted  40599.17
    canonical 40599.1736630513815

`40,599.17` is how a credit officer writes that number. What refused it was a
relative tolerance of 1e-9, written to absorb a rounded display value, which
it does not: rounding five significant digits to two places moves the figure
by about 9e-8 relative -- ninety times that bound. The comment claimed
something the arithmetic never supported.

The first section measures that, so the diagnosis is a number rather than an
assertion. The rest proves the fix accepts a business-formatted figure, still
refuses a wrong one, and refuses a number that merely ROUNDS to the right
answer -- which is not the same as being it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import final, intent

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4.contracts import parse_final

#: The live figures, verbatim.
LIVE_CANONICAL = Decimal("40599.1736630513815")
LIVE_DISPLAY = Decimal("40599.17")
LIVE_WRONG = Decimal("40599.18")


# ---- 1. the old bound could not have accepted it ----------------------

def test_the_old_tolerance_was_too_tight_by_two_orders_of_magnitude():
    """The diagnosis, as arithmetic rather than as a claim."""
    relative = abs(LIVE_CANONICAL - LIVE_DISPLAY) / abs(LIVE_CANONICAL)
    assert relative > Decimal("1e-9"), (
        "if this ever passes, the live rejection had another cause")
    assert float(relative) == pytest.approx(9.02e-8, rel=0.01)
    # And the bound cannot simply be widened: at 1e-6 a figure wrong by
    # three thousand SAR million would pass on a book this size.
    wrong_by = LIVE_CANONICAL * Decimal("1e-6")
    assert wrong_by > Decimal("0.04"), (
        "a tolerance wide enough for display is wide enough to hide a real "
        "error, which is why the fix is not a wider tolerance")


# ---- 2. the contract, on the live figures -----------------------------

def test_the_live_display_value_is_accepted(store_db, release_id):
    """The figure the live run was refused for is accepted -- and rewritten.

    `40,599.17` is a correct reading of this value and the analyst is not
    argued with for sending it. It is not, however, how CreditProbe writes
    an amount: money shows no decimals, so the PUBLISHED figure is
    `SAR 40,599 million`. Accepting the cross-check and choosing the
    published string are separate decisions and only the second is the
    display policy's.
    """
    verdict = prec.check(str(LIVE_DISPLAY), LIVE_CANONICAL,
                         unit="SAR million", declared_precision=2,
                         label="total_ead")
    assert verdict.ok, verdict.problem
    assert verdict.canonical == LIVE_CANONICAL
    assert verdict.display == disp.quantize(LIVE_CANONICAL, 0)
    assert verdict.precision == 0


def test_the_full_canonical_value_is_also_accepted():
    """Ugly, but correct. Refusing a right answer would be the wrong fix."""
    verdict = prec.check(str(LIVE_CANONICAL), LIVE_CANONICAL,
                         unit="SAR million", declared_precision=2,
                         label="total_ead")
    assert verdict.ok, verdict.problem


def test_a_value_rounded_the_wrong_way_is_refused():
    verdict = prec.check(str(LIVE_WRONG), LIVE_CANONICAL,
                         unit="SAR million", declared_precision=2,
                         label="total_ead")
    assert not verdict.ok
    assert "40599.17" in verdict.problem


def test_a_number_that_merely_rounds_to_the_right_answer_is_refused():
    """40599.1699 displays as 40599.17 and is still not the value."""
    verdict = prec.check("40599.1699", LIVE_CANONICAL, unit="SAR million",
                         declared_precision=2, label="total_ead")
    assert not verdict.ok
    assert "rounds to the right figure" in verdict.problem


# ---- 3. the second live claim: mfg_vs_it_ticket -----------------------
#
# The brief is explicit that this one must not be assumed to be rounding.
# It is a RATIO of two average ticket sizes, and the two failure modes look
# alike in a log and are not alike at all: a ratio whose display was rounded
# is a presentation fault, and a ratio computed against the wrong denominator
# is a wrong answer that must keep failing. Both are pinned here.

MFG_TOTAL = Decimal("4821.7734029")
MFG_COUNT = Decimal("7")
IT_TOTAL = Decimal("9143.2211887")
IT_COUNT = Decimal("19")
MFG_TICKET = MFG_TOTAL / MFG_COUNT
IT_TICKET = IT_TOTAL / IT_COUNT
TRUE_RATIO = MFG_TICKET / IT_TICKET


def test_the_ratio_claim_passes_when_only_its_display_was_rounded():
    shown = prec.quantize(TRUE_RATIO, 2)
    verdict = prec.check(str(shown), TRUE_RATIO, unit="ratio",
                         declared_precision=2, label="mfg_vs_it_ticket")
    assert verdict.ok, verdict.problem


def test_the_ratio_claim_still_fails_when_the_arithmetic_is_wrong():
    """The denominator is IT's average ticket, not IT's total."""
    wrong = MFG_TICKET / IT_TOTAL
    verdict = prec.check(str(prec.quantize(wrong, 2)), TRUE_RATIO,
                         unit="ratio", declared_precision=2,
                         label="mfg_vs_it_ticket")
    assert not verdict.ok, (
        "a ratio against the wrong denominator must not be waved through as "
        "a rounding difference")


def test_the_ratio_claim_fails_when_it_is_inverted():
    verdict = prec.check(str(prec.quantize(Decimal(1) / TRUE_RATIO, 2)),
                         TRUE_RATIO, unit="ratio", declared_precision=2,
                         label="mfg_vs_it_ticket")
    assert not verdict.ok


def test_a_ratio_and_an_amount_are_governed_to_different_places():
    """And neither of them is negotiable.

    A ratio reads at two places and an amount at none. Both are business
    rules, and the difference between them is the unit, not what anybody
    asked for -- `PERMITTED` lists exactly one precision per governed class
    precisely so that asking changes nothing.
    """
    assert prec.allowed_precisions("ratio") == (2,)
    assert prec.allowed_precisions("SAR million") == (0,)
    for asked in (0, 1, 2, 3, 4):
        assert disp.resolve_decimals("ratio", asked) == 2
        assert disp.resolve_decimals("SAR million", asked) == 0


# ---- 4. scale and unit safety -----------------------------------------

def test_percent_and_ratio_are_not_interchangeable():
    """0.172 and 17.2% are the same quantity and not the same number."""
    canonical_ratio = Decimal("0.1723")
    as_percent = canonical_ratio * 100
    verdict = prec.check(str(prec.quantize(as_percent, 2)), canonical_ratio,
                         unit="ratio", declared_precision=2,
                         label="stage2_share")
    assert not verdict.ok, "a hundredfold error must not pass as rounding"


def test_percent_and_percentage_point_are_different_units():
    assert prec.classify("percent").kind == prec.PERCENT
    assert prec.classify("percentage points").kind == prec.PERCENTAGE_POINT
    assert prec.classify("bps").kind == prec.PERCENTAGE_POINT


def test_money_scale_is_read_and_not_assumed():
    million = prec.classify("SAR million")
    billion = prec.classify("SAR bn")
    assert million.kind == billion.kind == prec.MONEY
    assert million.currency == billion.currency == "SAR"
    assert prec.MONEY_SCALES[million.scale] == 1
    assert prec.MONEY_SCALES[billion.scale] == 1000


def test_a_thousandfold_scale_error_is_refused():
    """SAR 40,599.17 million restated as 40,599.17 billion is not rounding."""
    canonical = Decimal("40599.1736630513815")
    verdict = prec.check("40.60", canonical, unit="SAR million",
                         declared_precision=2, label="total_ead")
    assert not verdict.ok


def test_a_count_asking_for_decimal_places_simply_does_not_get_them():
    """There is no such thing as 22.00 breaches, and no argument about it.

    This used to be a refusal, which sent the whole answer back for a model
    turn over two characters of presentation. The count is right; it is
    written as a count and the run carries on.
    """
    verdict = prec.check("22.00", Decimal("22"), unit="count",
                         declared_precision=2, label="breaches")
    assert verdict.ok, verdict.problem
    assert verdict.precision == 0
    assert disp.format_value(verdict.canonical, "count",
                             verdict.precision) == "22"


def test_precision_is_read_from_the_unit_not_the_claim_name():
    """`total_ead` and `ead_share` differ by unit, not by spelling."""
    assert prec.default_precision("SAR million") == 0
    assert prec.default_precision("percent") == 2
    assert prec.default_precision("count") == 0
    assert prec.allowed_precisions("ratio") == (2,)


# ---- 5. zero, tiny, huge, and notation --------------------------------

@pytest.mark.parametrize("canonical,places,expected", [
    ("0", 2, "0.00"),
    ("-0", 2, "0.00"),
    ("0E+12", 2, "0.00"),
    ("0.000000001", 2, "0.00"),
    ("-0.000000001", 2, "0.00"),
    ("1E+3", 2, "1000.00"),
    ("123456789.987654", 2, "123456789.99"),
])
def test_no_user_facing_value_is_in_scientific_notation(canonical, places,
                                                        expected):
    shown = prec.quantize(Decimal(canonical), places)
    assert str(prec.plain(shown)) == expected
    assert "E" not in str(prec.plain(shown))


def test_a_negative_zero_reads_as_zero_not_as_minus_zero():
    text = prec.format_value(Decimal("-0.001"), "SAR million", 2)
    assert "-0.00" not in text, f"a reader should not be shown {text}"


def test_a_tiny_nonzero_movement_is_not_displayed_as_a_change():
    """Rounding to zero is honest; pretending it moved is not."""
    shown = prec.quantize(Decimal("0.0004"), 2)
    assert shown == 0
