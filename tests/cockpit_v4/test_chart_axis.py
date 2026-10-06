"""The scale a chart is read against comes from the server.

UNIT · REPRODUCTION. No model call, no database.

The Cockpit charts had no axes at all, and the reason was a rule rather
than an oversight: `visuals.tsx` is forbidden to format, round or re-scale
a number, so the browser could not invent "0, 5, 10, 15 SAR mn" and nobody
had built the place those numbers could come from. A reader got bars with
no scale and a line with no vertical reference.

`axis.py` is that place. These tests hold it to the two things that make it
trustworthy: the tick VALUES are chosen by a rule a person can check, and
every tick STRING comes out of `display.py` -- the same policy that wrote
every claim in the answer, so an axis label is not a second opinion about
how to round.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import axis as axis_mod
from backend.cockpit_v4 import display as disp


# --------------------------------------------------------------------------
# The step
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("span", "expected"), [
    (Decimal(100), Decimal(20)),
    (Decimal(10), Decimal(2)),
    (Decimal(1), Decimal("0.2")),
    (Decimal(47), Decimal(10)),
    (Decimal(23), Decimal(5)),
    (Decimal("0.04"), Decimal("0.01")),
    (Decimal(3), Decimal(1)),
])
def test_the_step_is_one_two_or_five(span: Decimal, expected: Decimal) -> None:
    assert axis_mod.nice_step(span) == expected


@pytest.mark.parametrize("span", [Decimal(s) for s in (1, 3, 7, 19, 250, 9999)])
def test_no_step_a_reader_has_to_do_arithmetic_on(span: Decimal) -> None:
    """1, 2 and 5 times a power of ten, and nothing else. A scale advancing
    by 2.5 or by 3 is a scale the reader has to compute positions on."""
    step = axis_mod.nice_step(span)
    mantissa = step.scaleb(-step.adjusted()).normalize()
    assert mantissa in (Decimal(1), Decimal(2), Decimal(5)), step


def test_a_degenerate_span_still_yields_an_axis() -> None:
    """Every value identical -- a single-period chart -- must not produce a
    zero-width scale the renderer divides by."""
    ticks = axis_mod.tick_values(Decimal(7), Decimal(7))
    assert len(ticks) >= 2
    assert ticks[0] < ticks[-1]


def test_an_all_zero_span_yields_an_axis() -> None:
    assert axis_mod.tick_values(Decimal(0), Decimal(0)) == [Decimal(0),
                                                            Decimal(1)]


# --------------------------------------------------------------------------
# The range
# --------------------------------------------------------------------------

def test_the_axis_covers_the_data() -> None:
    """An axis that stops below the largest value clips a bar, which is a
    chart that understates a number -- the one thing this product may not
    do."""
    ticks = axis_mod.tick_values(Decimal("3.4"), Decimal("87.2"))
    assert ticks[0] <= Decimal("3.4")
    assert ticks[-1] >= Decimal("87.2")


def test_the_axis_begins_and_ends_on_a_round_number() -> None:
    ticks = axis_mod.tick_values(Decimal("3.4"), Decimal("87.2"))
    step = ticks[1] - ticks[0]
    for tick in ticks:
        assert tick % step == 0, tick


def test_the_ticks_are_evenly_spaced() -> None:
    ticks = axis_mod.tick_values(Decimal(-17), Decimal(41))
    gaps = {ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)}
    assert len(gaps) == 1


def test_a_negative_range_is_covered_at_both_ends() -> None:
    ticks = axis_mod.tick_values(Decimal(-93), Decimal(-4))
    assert ticks[0] <= Decimal(-93)
    assert ticks[-1] >= Decimal(-4)


def test_a_reversed_range_is_not_an_error() -> None:
    assert (axis_mod.tick_values(Decimal(80), Decimal(10))
            == axis_mod.tick_values(Decimal(10), Decimal(80)))


# --------------------------------------------------------------------------
# The strings
# --------------------------------------------------------------------------

def test_every_tick_string_comes_from_the_display_policy() -> None:
    """The point of the whole exercise. If the axis rounded on its own,
    "SAR 12.4mn" on the axis could sit beside "SAR 12.35mn" in the sentence
    that cites the same number."""
    built = axis_mod.measure_axis("Exposure at default", "ead_sar_mn",
                                  "SAR_MN", [3.4, 87.2, 41.0], disp)
    assert built is not None
    for tick in built["ticks"]:
        assert tick["display"] == disp.format_value(
            Decimal(str(tick["value"])), "SAR_MN")


def test_a_unitless_axis_is_still_written_for_a_person() -> None:
    built = axis_mod.measure_axis("Count", "n", "", [1, 2, 3], disp)
    assert built is not None
    assert all(tick["display"] for tick in built["ticks"])


def test_a_percentage_axis_is_written_as_a_percentage() -> None:
    built = axis_mod.measure_axis("Coverage", "coverage_pct", "PCT",
                                  [12.5, 48.31], disp)
    assert built is not None
    assert built["ticks"][-1]["display"].endswith("%")


# --------------------------------------------------------------------------
# Zero
# --------------------------------------------------------------------------

def test_a_length_encoded_axis_includes_zero() -> None:
    """A bar chart whose axis starts at 92 draws a 3% difference as a
    doubled bar. This is the single most common way a chart lies."""
    built = axis_mod.measure_axis("Exposure", "ead", "SAR_MN",
                                  [92.0, 95.0], disp, include_zero=True)
    assert built is not None
    assert Decimal(str(built["ticks"][0]["value"])) <= 0


def test_a_position_encoded_axis_need_not() -> None:
    """A line over time encodes by position and makes no magnitude claim,
    so forcing it through zero would flatten the movement it exists to
    show."""
    built = axis_mod.measure_axis("Exposure", "ead", "SAR_MN",
                                  [92.0, 95.0], disp, include_zero=False)
    assert built is not None
    assert Decimal(str(built["ticks"][0]["value"])) > 0


# --------------------------------------------------------------------------
# What an axis is not
# --------------------------------------------------------------------------

def test_a_category_axis_carries_no_ticks() -> None:
    """The categories ARE the points. A tick list would be a second copy of
    the same strings, free to drift from the first."""
    built = axis_mod.category_axis("Sector", "sector")
    assert built["ticks"] == []
    assert built["kind"] == axis_mod.CATEGORY


def test_an_axis_over_nothing_is_absent_rather_than_empty() -> None:
    assert axis_mod.measure_axis("x", "x", "", [], disp) is None
    assert axis_mod.measure_axis("x", "x", "", [None, "n/a"], disp) is None


# --------------------------------------------------------------------------
# The label
# --------------------------------------------------------------------------

class _Spec:
    def __init__(self, label: str) -> None:
        self.label = label


class _Catalog:
    def __init__(self, known: dict[tuple[str, str], str]) -> None:
        self._known = known

    def resolve(self, relation: str, column: str) -> _Spec:
        try:
            return _Spec(self._known[(relation, column)])
        except KeyError:
            raise LookupError(column) from None


def test_the_label_is_the_catalogues_business_name() -> None:
    """`ead_sar_mn` on an axis tells a credit officer nothing they did not
    already know from their own question."""
    catalog = _Catalog({("corporate_exposures", "ead_sar_mn"):
                        "Exposure at default"})
    assert axis_mod.field_label(catalog, ["corporate_exposures"],
                                "ead_sar_mn") == "Exposure at default"


def test_a_column_the_catalogue_cannot_name_keeps_its_own_name() -> None:
    """Inventing a prettier label would be the renderer asserting a meaning
    nobody governed."""
    catalog = _Catalog({})
    assert axis_mod.field_label(catalog, ["r"], "total_x") == "total_x"


def test_no_catalogue_is_not_an_error() -> None:
    assert axis_mod.field_label(None, [], "ead_sar_mn") == "ead_sar_mn"


def test_a_shared_scale_is_named_by_its_unit() -> None:
    """Two series on one axis cannot be named after whichever is first --
    "Stage 1 ECL" beside a Stage 2 line is a label that is wrong half the
    time."""
    assert axis_mod.unit_label("PCT", disp) == "%"
    assert "sar" in axis_mod.unit_label("SAR_MN", disp).lower()


def test_an_unnameable_unit_yields_no_label_rather_than_a_guess() -> None:
    assert axis_mod.unit_label("", disp) == ""


# --------------------------------------------------------------------------
# The stack
# --------------------------------------------------------------------------

def test_a_stack_is_measured_by_its_total() -> None:
    """An axis built from the tallest SEGMENT stops well below the tallest
    BAR and clips it."""
    row = {"stage_1": 10, "stage_2": 25, "stage_3": 5}
    assert axis_mod.stack_total(row, ["stage_1", "stage_2",
                                      "stage_3"]) == Decimal(40)


def test_a_missing_segment_is_not_a_hole_in_the_stack() -> None:
    row = {"stage_1": 10, "stage_2": None}
    assert axis_mod.stack_total(row, ["stage_1", "stage_2"]) == Decimal(10)
