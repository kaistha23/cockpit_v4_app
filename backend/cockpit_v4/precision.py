"""
The canonical value, the display value, and who is allowed to choose.

The defect this exists for
--------------------------
A live run computed exposure at default by sector, correctly, and then had
its answer refused:

    asserted  40599.17
    canonical 40599.1736630513815

The analyst did the right thing. `40,599.17` is how a credit officer writes
that number, and `40,599.1736630513815` is not. What refused it was a
validator that compared the two at a relative tolerance of 1e-9 -- a bound
written to "absorb a decimal string the analyst rounded", which it does not:
rounding a five-digit figure to two places moves it by about 9e-8 relative,
nearly a hundred times that bound. The comment was wrong and the number
proved it.

The fix is not a wider tolerance. A wider tolerance buys presentation at the
cost of arithmetic, and at 1e-6 a genuinely wrong figure starts to pass.
What is needed is the distinction the old contract never drew:

    CANONICAL   the full-precision value CreditProbe computed from the
                executed evidence. It is the truth and it is never rounded
                away -- it is what gets stored and recomputed against.

    DISPLAY     the value a person reads. It is the canonical value
                quantized to a precision this module allows, by a rounding
                mode this module fixes.

An asserted figure is valid when it is one of exactly two things: the
canonical value, or the canonical value correctly rounded for display.
Anything else -- including a number that happens to round to the right
answer, like 40599.1699 -- is refused, because "rounds to something correct"
is not the same as "is correct".

Who owns precision
------------------
CreditProbe does. The analyst declares what it INTENDS, and this module says
whether that intent is permitted for the unit in question. A claim cannot
widen its own tolerance by declaring a coarser precision, and precision is
never inferred from a claim's name -- `total_ead` and `ead_share` are told
apart by their units, not by their spelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.cockpit_v4 import display as disp

#: Business rounding. Defined once, in the display policy.
ROUNDING = disp.ROUNDING

# ---- unit classes, as the display policy names them --------------------

#: The kind names this module used before the display policy existed.
#: Kept because they appear in stored verdicts and in test vocabulary, and
#: each is now just another spelling of a class in `display`.
MONEY = "money"
PERCENT = "percent"
PERCENTAGE_POINT = "percentage_point"
RATIO = "ratio"
COUNT = "count"
CATEGORICAL = "categorical"
UNKNOWN = "unknown"

#: Scale words and what each means relative to one million. In `display`.
MONEY_SCALES = disp.MONEY_SCALES

_KIND = {
    disp.MONETARY_AMOUNT: MONEY,
    disp.PERCENTAGE: PERCENT,
    disp.PROBABILITY: PERCENT,
    disp.PERCENTAGE_POINT: PERCENTAGE_POINT,
    disp.RATIO: RATIO,
    disp.COUNT: COUNT,
    disp.INTEGER: COUNT,
    disp.RATING: CATEGORICAL,
    disp.IFRS_STAGE: CATEGORICAL,
    disp.PERIOD: CATEGORICAL,
    disp.UNKNOWN: UNKNOWN,
}

#: There is deliberately NO module-level currency here.
#:
#: A previous version of this file named one, and `service.load_release` used
#: it whenever a release's manifest was silent about its own denomination.
#: The result was that a release whose DATA said one thing was reported as
#: another -- silently, because a silent manifest is not a wrong one.
#: Currency and scale belong to the selected release and are read from it.


@dataclass(frozen=True)
class Unit:
    """A parsed unit: what kind of quantity, and at what scale."""

    raw: str
    kind: str
    currency: str = ""
    scale: str = ""

    @property
    def is_money(self) -> bool:
        return self.kind == MONEY

    def describe(self) -> str:
        return self.raw or self.kind


def classify(unit: str) -> Unit:
    """What kind of number is this, read from the unit and nothing else."""
    raw = str(unit or "").strip()
    kind = _KIND[disp.classify(raw)]
    currency = scale = ""
    if kind == MONEY:
        match = disp._MONEY.match(raw.lower())
        if match:
            currency = match.group("currency").upper()
            scale = (match.group("scale") or "").lower()
    return Unit(raw, kind, currency=currency, scale=scale)


def money_unit(catalog: Any) -> str:
    """The money unit of ONE release. Empty when the release does not say."""
    return disp.money_unit(catalog)


def allowed_precisions(unit: str) -> tuple[int, ...]:
    return disp.permitted(unit)


def default_precision(unit: str) -> int:
    return disp.decimals(unit)


def quantize(value: Decimal, places: int) -> Decimal:
    """The display form of a canonical value. Deterministic, half up."""
    return disp.quantize(value, places)


def plain(value: Decimal) -> Decimal:
    """The same number, never in exponent notation."""
    return disp.plain(value)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    problem: str = ""
    canonical: Decimal = Decimal(0)
    display: Decimal = Decimal(0)
    precision: int = 2


#: How far a cross-check may have been rounded and still be recognisable as
#: this value. Beyond twelve places the contract will not accept the string
#: at all, so there is nothing past it to match.
_ROUNDINGS = range(0, 13)


def check(asserted: str, canonical: Decimal, *, unit: str,
          declared_precision: int, label: str) -> Verdict:
    """Is `asserted` this canonical value, written for a reader?

    Valid as the canonical value itself, or as that value correctly rounded
    -- to any number of places. A third number that merely rounds to the
    right answer is not one of them: `40599.1699` rounds to `40599.17` and
    is not the canonical value at any precision, so it is still refused.

    Note what this does NOT decide: how the figure is written. The analyst
    may have rounded its cross-check to two places for its own comfort; the
    published string is still CreditProbe's, at the precision the display
    class governs. Those two are separate questions and conflating them is
    what used to send a correct answer back for a model turn over a decimal
    point.
    """
    places = disp.resolve_decimals(unit, declared_precision)
    try:
        given = Decimal(asserted)
    except (InvalidOperation, ValueError):
        return Verdict(False, f"{label} asserts {asserted!r}, which is not a "
                              f"decimal.")

    canonical = plain(canonical)
    display = plain(quantize(canonical, places))
    if given == canonical or any(
            given == plain(quantize(canonical, p)) for p in _ROUNDINGS):
        return Verdict(True, canonical=canonical, display=display,
                       precision=places)

    rounds_right = quantize(given, places) == display
    detail = (
        f" It rounds to the right figure, which is not the same as being it: "
        f"send the value CreditProbe computed, or that value rounded."
        if rounds_right else "")
    return Verdict(
        False,
        f"{label} asserts {given} and the evidence gives {canonical}, which "
        f"displays at {places}dp as {display}.{detail}",
        canonical=canonical, display=display, precision=places)


def format_value(value: Decimal, unit: str,
                 places: int | None = None) -> str:
    """How the number reads once it is published. One policy, in `display`."""
    return disp.format_value(value, unit, places)


__all__ = ["CATEGORICAL", "COUNT", "MONEY", "MONEY_SCALES",
           "PERCENT", "PERCENTAGE_POINT",
           "RATIO", "ROUNDING", "UNKNOWN", "Unit", "Verdict",
           "allowed_precisions", "check", "classify", "default_precision",
           "format_value", "money_unit", "plain", "quantize"]
