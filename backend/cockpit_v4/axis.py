"""The axis a chart is read against, computed by the server.

Why this is not the browser's job
---------------------------------
`visuals.tsx` opens by stating the rule the whole published-figure contract
rests on: the frontend never formats, rounds or re-scales a number. Every
value a reader sees comes from the server already written, because a figure
rounded twice by two implementations is a figure the bank cannot defend.

That rule is why the charts had no axes. A y axis is a set of numbers a
reader reads off -- 0, 5, 10, 15 SAR mn -- and the browser was not allowed
to invent them, so there were none: a bar chart with no scale, a line with
no vertical reference, and a tooltip that had to carry the whole burden of
saying what anything was worth. The plan was never "the charts do not need
axes". It was "the axis has to come from the same place every other figure
comes from", and nobody had built that place.

This is that place. The tick VALUES are chosen here by a deterministic
1/2/5 algorithm, and each tick's STRING is written by `display.py` -- the
one display policy that already governs every published number, so an axis
label is rounded by the same rules as the claim that cites it. The browser
receives `{value, display}` pairs and does arithmetic on neither.

`export.py` reads the same axis, which closes a second gap: the on-screen
SVG and the downloaded SVG were two independent renderers of one payload,
free to disagree, and did.

What an axis is not
-------------------
A pie has no axis. A category axis has no ticks -- its categories are the
points, and repeating them in a tick list would be a second copy of the
same strings, free to drift from the first. Both cases are expressed by
absence rather than by an empty structure a reader of the payload has to
interpret.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

#: A measure axis is numeric and gets ticks. A category axis names what the
#: positions are and stops there -- the categories are the points.
MEASURE = "measure"
CATEGORY = "category"

#: How many ticks to aim for. Five intervals is the density a reader scans
#: without counting: fewer and the scale is coarse enough to mislead, more
#: and the labels collide at the width a chart gets inside a thread.
TARGET_INTERVALS = 5

#: The steps a person reads. A scale advancing by 2.5 or by 3 is arithmetic
#: the reader has to do; 1, 2 and 5 times a power of ten is the step nobody
#: has to think about.
_MANTISSAS = (Decimal(1), Decimal(2), Decimal(5), Decimal(10))


def _numeric(value: Any) -> Decimal | None:
    """A value as an exact decimal, or nothing.

    `Decimal(str(...))` rather than `Decimal(float)`: the canonical values
    arrive as JSON numbers, and going through the string keeps the figure
    the reader was shown rather than its binary neighbour.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def nice_step(span: Decimal, intervals: int = TARGET_INTERVALS) -> Decimal:
    """The step of a scale a person reads without doing arithmetic.

    Deterministic and exact: the raw step is taken to its power of ten and
    rounded UP to the next of 1, 2, 5, 10, so a scale never has more
    intervals than asked for and never lands on 2.5.
    """
    if span <= 0 or intervals <= 0:
        return Decimal(1)
    raw = span / Decimal(intervals)
    exponent = raw.adjusted()  # floor(log10(raw)) for a positive Decimal
    power = Decimal(1).scaleb(exponent)
    mantissa = raw / power
    for candidate in _MANTISSAS:
        if mantissa <= candidate:
            return candidate * power
    return _MANTISSAS[-1] * power


def tick_values(low: Decimal, high: Decimal,
                intervals: int = TARGET_INTERVALS) -> list[Decimal]:
    """The numbers on a measure axis, from a low and a high.

    The range is extended outwards to whole steps, so the axis begins and
    ends on a round number rather than on whatever the data happened to
    reach. A range with no width -- every value identical, which happens on
    a single-period chart -- still yields a usable axis rather than a
    degenerate one.
    """
    if high < low:
        low, high = high, low
    if low == high:
        if low == 0:
            return [Decimal(0), Decimal(1)]
        pad = abs(low) / Decimal(2)
        low, high = low - pad, high + pad
    step = nice_step(high - low, intervals)
    start = (low / step).to_integral_value(rounding="ROUND_FLOOR") * step
    stop = (high / step).to_integral_value(rounding="ROUND_CEILING") * step
    out: list[Decimal] = []
    current = start
    # A hard bound rather than `while current <= stop`: a step that somehow
    # came out non-positive must not spin.
    for _ in range(intervals * 4 + 2):
        out.append(current)
        if current >= stop or step <= 0:
            break
        current += step
    return out


def _json(value: Decimal) -> Any:
    """A decimal as the JSON number a reader's browser will position by."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def measure_axis(label: str, column: str, unit: str,
                 values: list[Any], disp: Any, *,
                 include_zero: bool = True,
                 intervals: int = TARGET_INTERVALS) -> dict[str, Any] | None:
    """A numeric axis with its ticks already written.

    `include_zero` is the bar-chart rule, and it is not cosmetic: a bar
    encodes magnitude by length, so a bar axis that starts at 92 shows a
    3% difference as a doubled bar. Position-encoded forms -- a line over
    time, a scatter -- carry no such claim and are allowed to start where
    the data does.
    """
    numbers = [n for n in (_numeric(v) for v in values) if n is not None]
    if not numbers:
        return None
    low, high = min(numbers), max(numbers)
    if include_zero:
        low, high = min(low, Decimal(0)), max(high, Decimal(0))
    ticks = [
        {"value": _json(value),
         # The same policy that wrote every claim in the answer. An axis
         # label is not a second opinion about how to round.
         "display": (disp.format_value(value, unit) if unit
                     else disp.format_unitless(value))}
        for value in tick_values(low, high, intervals)
    ]
    return {"kind": MEASURE, "label": label, "column": column,
            "unit": unit, "ticks": ticks}


def category_axis(label: str, column: str) -> dict[str, Any]:
    """A named axis whose positions are the points themselves."""
    return {"kind": CATEGORY, "label": label, "column": column,
            "unit": "", "ticks": []}


def unit_label(unit: str, disp: Any) -> str:
    """How a person spells a unit, for the axis of a multi-series chart.

    Two series on one scale cannot be named after whichever happens to be
    first -- an axis reading "Stage 1 ECL" beside a Stage 2 line is a label
    that is wrong half the time. The scale is what they share, so the scale
    is what the axis is called.

    The spelling is READ OFF `format_value` rather than listed here, the
    same derivation `written_affixes` uses: a unit whose rendering changes
    cannot leave this stale.
    """
    if not unit:
        return ""
    try:
        prefixes, suffixes = disp.written_affixes(unit)
    except Exception:  # noqa: BLE001
        return ""
    parts = [*(p for p in prefixes if p), *(suffixes[:1] if suffixes else ())]
    spelled = " ".join(parts).strip()
    return spelled.upper() if len(spelled) <= 3 else spelled.capitalize()


def stack_total(row: Any, columns: list[str]) -> Decimal:
    """The height of one stack.

    A stacked bar is read off its TOTAL, so an axis built from the tallest
    single segment would stop well below the tallest bar and clip it.
    """
    total = Decimal(0)
    for column in columns:
        value = _numeric(row.get(column))
        if value is not None:
            total += value
    return total


def field_label(catalog: Any, relations: list[str], column: str) -> str:
    """What the catalogue calls a column, for a reader.

    An axis reading `ead_sar_mn` tells a credit officer nothing they did not
    already know from the question; `Exposure at default` is the label the
    catalogue already carries for exactly this purpose. A column the
    catalogue cannot name keeps its own name -- inventing a prettier one
    would be the renderer asserting a meaning nobody governed.
    """
    if catalog is None or not column:
        return column
    for relation in relations:
        try:
            spec = catalog.resolve(relation, column)
        except Exception:  # noqa: BLE001
            continue
        label = str(getattr(spec, "label", "") or "").strip()
        if label:
            return label
    return column


__all__ = ["CATEGORY", "MEASURE", "TARGET_INTERVALS", "category_axis",
           "field_label", "measure_axis", "nice_step", "stack_total",
           "tick_values", "unit_label"]
