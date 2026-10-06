"""Every chart that has a scale is published with it.

UNIT · REAL DATABASE. No provider call.

`test_chart_axis.py` holds the tick algorithm. This holds the WIRING: that
`render_charts` puts an axis on each form that has one, leaves it off the
forms that do not, and picks the right axis for each -- which is not the
same question for a bar, a line, a box plot and a heatmap.

The three that are easy to get wrong, and are therefore each pinned:

  a bar axis includes zero     a bar encodes magnitude by length, so a
                               scale starting at 92 draws a 3% difference
                               as a doubled bar
  a stack is read by total     an axis built from the tallest SEGMENT
                               stops below the tallest BAR and clips it
  a box plot is transposed     its categories run down the page and its
                               measure across, so its axes are the
                               opposite way round from every other form
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4.axis import CATEGORY, MEASURE
from backend.cockpit_v4.config import STANDARD_LIMITS
from backend.cockpit_v4.finalization import CHART_KINDS, Finalizer

TREND = [{"period": "2026Q1", "ead_sar_mn": 92.0, "ecl_sar_mn": 3.1},
         {"period": "2026Q2", "ead_sar_mn": 95.0, "ecl_sar_mn": 3.4},
         {"period": "2026Q3", "ead_sar_mn": 93.5, "ecl_sar_mn": 4.0}]

STACK = [{"band": "0-29", "stage_1": 40.0, "stage_2": 25.0, "stage_3": 5.0},
         {"band": "30-59", "stage_1": 10.0, "stage_2": 30.0, "stage_3": 12.0}]

MIGRATION = [{"rating_from": "BBB", "rating_to": "BB", "borrowers": 14},
             {"rating_from": "BB", "rating_to": "B", "borrowers": 9}]


@pytest.fixture
def render(store_db):
    """Render one chart over one stored result, the way a run does."""
    def _render(kind: str, *, rows, columns, x_column: str,
                y_columns: list[str], series_column: str = "",
                unit: str = "SAR_MN"):
        artifact_id = store_db.put_artifact(
            run_id="r-axis", tenant_id="t", kind="result", release_id="rel",
            scope={"step_id": "s1"}, columns=list(columns), rows=list(rows))
        finalizer = Finalizer(store=store_db, tenant_id="t", release_id="rel",
                              limits=STANDARD_LIMITS,
                              run_artifacts={artifact_id})
        chart = {"kind": kind, "title": "T", "artifact_id": artifact_id,
                 "x_column": x_column, "y_columns": list(y_columns),
                 "unit": unit}
        if series_column:
            chart["series_column"] = series_column
        return finalizer.render_charts([chart])[0]
    return _render


@pytest.fixture
def trend(render):
    def _trend(kind: str, y_columns=("ead_sar_mn",), **kwargs):
        return render(kind, rows=TREND,
                      columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                      x_column="period", y_columns=list(y_columns), **kwargs)
    return _trend


# ---- every form is decided about, none is forgotten ---------------------

#: The forms that divide one whole. There is no scale to read them
#: against -- magnitude is area, and an axis would be a second, wrong
#: story about the same slices.
NO_AXIS = {"pie", "donut"}


@pytest.mark.parametrize("kind", sorted(set(CHART_KINDS) - NO_AXIS))
def test_every_form_with_a_scale_publishes_one(render, kind):
    """A chart with no axis is a chart with no scale, which is what the
    reader was looking at before this existed."""
    if kind == "heatmap":
        body = render(kind, rows=MIGRATION,
                      columns=["rating_from", "rating_to", "borrowers"],
                      x_column="rating_to", y_columns=["borrowers"],
                      series_column="rating_from", unit="count")
    elif kind in ("scatter", "bubble"):
        # These two relate two MEASURES, so their x is a measure too.
        body = render(kind, rows=TREND,
                      columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                      x_column="ead_sar_mn", y_columns=["ecl_sar_mn"])
    else:
        body = render(kind, rows=TREND,
                      columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                      x_column="period",
                      y_columns=["ead_sar_mn", "ecl_sar_mn"])
    assert body.get("x_axis"), f"{kind} has no x axis"
    assert body.get("y_axis"), f"{kind} has no y axis"


@pytest.mark.parametrize("kind", sorted(NO_AXIS))
def test_a_whole_divided_has_no_axis(trend, kind):
    body = trend(kind)
    assert "x_axis" not in body
    assert "y_axis" not in body


# ---- the shape of what is published -------------------------------------

def test_the_category_axis_names_the_column_and_carries_no_ticks(trend):
    body = trend("bar")
    assert body["x_axis"]["kind"] == CATEGORY
    assert body["x_axis"]["column"] == "period"
    assert body["x_axis"]["ticks"] == []


def test_the_measure_axis_carries_value_and_display_together(trend):
    body = trend("bar")
    axis = body["y_axis"]
    assert axis["kind"] == MEASURE
    assert axis["ticks"], "a measure axis with no ticks is not an axis"
    for tick in axis["ticks"]:
        assert isinstance(tick["value"], (int, float))
        assert isinstance(tick["display"], str) and tick["display"]


def test_the_browser_is_never_asked_to_format_a_tick(trend):
    """The whole reason the axis is computed here. A tick the frontend had
    to round could disagree with the sentence citing the same number."""
    body = trend("bar")
    for tick in body["y_axis"]["ticks"]:
        assert tick["display"] == disp.format_value(
            Decimal(str(tick["value"])), body["y_axis"]["unit"])


# ---- zero ----------------------------------------------------------------

def test_a_bar_axis_includes_zero(trend):
    """92 and 95 on an axis starting at 90 is a 3% difference drawn as a
    doubled bar."""
    assert Decimal(str(trend("bar")["y_axis"]["ticks"][0]["value"])) <= 0


def test_a_line_axis_need_not(trend):
    """A line encodes by position and asserts no magnitude, so forcing it
    through zero flattens the movement it exists to show."""
    assert Decimal(str(trend("line")["y_axis"]["ticks"][0]["value"])) > 0


def test_a_waterfall_axis_includes_zero(trend):
    """A waterfall bridges TO a total. Read off a floating baseline it
    bridges to nothing."""
    assert Decimal(str(trend("waterfall")["y_axis"]["ticks"][0]["value"])) <= 0


# ---- a stack is read off its total --------------------------------------

def test_a_stack_axis_reaches_the_tallest_bar(render):
    """The tallest SEGMENT here is 40; the tallest BAR is 70. An axis
    stopping at 40 clips half the chart."""
    body = render("stacked_bar", rows=STACK,
                  columns=["band", "stage_1", "stage_2", "stage_3"],
                  x_column="band",
                  y_columns=["stage_1", "stage_2", "stage_3"])
    assert Decimal(str(body["y_axis"]["ticks"][-1]["value"])) >= 70


def test_a_hundred_percent_stack_is_measured_in_share(render):
    """Its segments are re-scaled to the whole, so labelling the height in
    SAR would put a denomination on a proportion."""
    body = render("stacked_bar_100", rows=STACK,
                  columns=["band", "stage_1", "stage_2", "stage_3"],
                  x_column="band",
                  y_columns=["stage_1", "stage_2", "stage_3"])
    axis = body["y_axis"]
    assert axis["unit"] == "PCT"
    assert [tick["value"] for tick in axis["ticks"]] == [0, 25, 50, 75, 100]


# ---- the forms whose axes are not the usual way round -------------------

def test_a_scatter_has_a_measure_on_both_axes(render):
    body = render("scatter", rows=TREND,
                  columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                  x_column="ead_sar_mn", y_columns=["ecl_sar_mn"])
    assert body["x_axis"]["kind"] == MEASURE
    assert body["y_axis"]["kind"] == MEASURE


def test_a_box_plot_is_transposed(render):
    """Its categories run down the page and its measure across."""
    body = render("box", rows=TREND,
                  columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                  x_column="period", y_columns=["ead_sar_mn"])
    assert body["y_axis"]["kind"] == CATEGORY
    assert body["x_axis"]["kind"] == MEASURE


def test_a_heatmap_names_both_of_its_categorical_axes(render):
    body = render("heatmap", rows=MIGRATION,
                  columns=["rating_from", "rating_to", "borrowers"],
                  x_column="rating_to", y_columns=["borrowers"],
                  series_column="rating_from", unit="count")
    assert body["x_axis"] == {"kind": CATEGORY, "label": "rating_to",
                              "column": "rating_to", "unit": "", "ticks": []}
    assert body["y_axis"]["column"] == "rating_from"


def test_a_combo_publishes_both_scales_named(render):
    """The one form this product draws on two scales, because a rate over
    the volumes it is a rate of is what it is FOR. Both are published, so a
    reader can see which mark belongs to which -- an unnamed second axis is
    the chart mistake this is otherwise indistinguishable from.
    """
    body = render("combo", rows=TREND,
                  columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                  x_column="period", y_columns=["ead_sar_mn", "ecl_sar_mn"])
    assert body["y_axis"]["column"] == "ead_sar_mn"
    assert body["y_axis_secondary"]["column"] == "ecl_sar_mn"


def test_two_series_on_one_scale_are_named_by_the_scale(trend):
    """Not by whichever series happens to be first: "Exposure at default"
    over an ECL line is a label that is wrong half the time."""
    body = trend("bar", y_columns=("ead_sar_mn", "ecl_sar_mn"))
    assert body["y_axis"]["column"] == ""
    assert body["y_axis"]["label"]


# ---- an axis is never invented ------------------------------------------

def test_a_scatter_over_a_non_measure_still_names_its_positions(render):
    """Not a scatter, strictly. But the positions still have names, and
    saying what they are beats publishing no axis at all."""
    body = render("scatter", rows=TREND,
                  columns=["period", "ead_sar_mn", "ecl_sar_mn"],
                  x_column="period", y_columns=["ead_sar_mn"])
    assert body["x_axis"]["kind"] == CATEGORY


def test_a_chart_over_no_numbers_gets_no_measure_axis(render):
    """Absent rather than empty: a zero-tick axis is a structure the
    renderer has to interpret, and it would draw an empty scale."""
    body = render("bar", rows=[{"period": "2026Q1", "ead_sar_mn": None}],
                  columns=["period", "ead_sar_mn"],
                  x_column="period", y_columns=["ead_sar_mn"])
    assert "y_axis" not in body


def test_an_unrendered_chart_is_untouched(store_db):
    """A chart pointing at an artifact this run did not create is passed
    through as authored -- it gets no points, so it gets no axis either."""
    finalizer = Finalizer(store=store_db, tenant_id="t", release_id="rel",
                          limits=STANDARD_LIMITS, run_artifacts=set())
    chart = {"kind": "bar", "title": "T", "artifact_id": "a-elsewhere",
             "x_column": "period", "y_columns": ["ead_sar_mn"],
             "unit": "SAR_MN"}
    body = finalizer.render_charts([chart])[0]
    assert body == chart
