"""The downloaded chart is the chart that was on screen.

UNIT · REPRODUCTION. No model call, no database.

`visuals.tsx` and `export.py` are two independent renderers of ONE payload,
and nothing made them agree about the scale. Each computed its own: the
browser normalised a bar to the largest value it could see, this file did
the same arithmetic separately, and a reader who downloaded a chart got
something subtly other than what they had been looking at.

The axis is now published once, by the server, and both read it. These
tests hold `export.py` to that -- that it takes the extent from the payload
rather than from the data, and that the tick STRINGS it draws are the
server's, never its own rounding of the same numbers.
"""

from __future__ import annotations

import re

import pytest

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import export as export_mod

#: Every value between 40 and 60. A renderer left to its own devices scales
#: to that band; the published axis says the chart runs from 0 to 100, and
#: which of the two happened is visible in the geometry.
POINTS = [{"label": f"P{index}", "row_id": f"r{index}",
           "values": {"v": value}, "display": {"v": f"SAR {value} million"}}
          for index, value in enumerate((40.0, 52.0, 47.0, 60.0))]

AXIS = {"kind": "measure", "label": "Exposure at default", "column": "v",
        "unit": "SAR_MN",
        "ticks": [{"value": 0, "display": "SAR 0 million"},
                  {"value": 25, "display": "SAR 25 million"},
                  {"value": 50, "display": "SAR 50 million"},
                  {"value": 75, "display": "SAR 75 million"},
                  {"value": 100, "display": "SAR 100 million"}]}

#: The forms drawn from ONE measure, through the single-series dispatch.
SINGLE = ("bar", "line", "step_line", "area", "scatter", "waterfall",
          "histogram")

#: The forms drawn from every named measure.
MULTI = ("stacked_bar", "grouped_bar", "combo", "bubble")


def _lineage():
    return export_mod.Lineage(
        run_id="r", question="q", domain_id=dom.CORPORATE,
        domain_label="Corporate Credit", release_id="rel",
        release_fingerprint="f" * 64, tenant_id="t",
        reporting_currency="SAR", amount_scale="million",
        produced_at="2026-09-17T00:00:00+00:00")


def _svg(kind: str, *, axis=AXIS, points=None, series=("v",)):
    chart = {"kind": kind, "title": "Exposure", "y_columns": list(series),
             "unit": "SAR_MN", "points": list(points or POINTS)}
    if axis is not None:
        chart["y_axis"] = axis
    return export_mod.chart_svg(chart=chart, lineage=_lineage())


def _multi_points():
    return [{"label": f"P{index}", "row_id": f"r{index}",
             "values": {"a": a, "b": b},
             "display": {"a": f"SAR {a} million", "b": f"{b}%"}}
            for index, (a, b) in enumerate(
                ((40.0, 1.2), (52.0, 1.9), (47.0, 1.4), (60.0, 2.1)))]


# ---- the ticks are drawn, and they are the server's strings -------------

@pytest.mark.parametrize("kind", SINGLE)
def test_every_single_series_form_draws_the_published_ticks(kind):
    """Marks on an empty page: the reader can see one bar is taller and
    cannot say by how much."""
    svg = _svg(kind)
    for tick in AXIS["ticks"]:
        assert tick["display"] in svg, f"{kind} lost {tick['display']}"


@pytest.mark.parametrize("kind", MULTI)
def test_every_multi_series_form_draws_the_published_ticks(kind):
    svg = _svg(kind, points=_multi_points(), series=("a", "b"))
    assert AXIS["ticks"][2]["display"] in svg


@pytest.mark.parametrize("kind", SINGLE)
def test_the_export_never_writes_its_own_number(kind):
    """The point of publishing the axis. A tick this file formatted could
    disagree with the sentence in the answer citing the same figure."""
    svg = _svg(kind)
    # The server wrote "SAR 25 million". A second implementation rounding
    # the same value would reach for "25.00", "25.0" or a bare "25".
    assert not re.search(r">\s*25\.0+\s*<", svg)


@pytest.mark.parametrize("kind", SINGLE)
def test_the_axis_is_named_for_a_reader(kind):
    """`ead_sar_mn` on an axis tells a credit officer nothing."""
    assert "Exposure at default" in _svg(kind)


# ---- the extent comes from the payload, not from the data ---------------

def _bar_widths(svg: str) -> list[float]:
    return [float(w) for w in re.findall(r'<rect x="[\d.]+" y="[\d.]+" '
                                         r'width="([\d.]+)"', svg)]


def test_the_scale_is_the_published_one_and_not_the_data_range():
    """THE DIVERGENCE THIS EXISTS FOR.

    The largest value is 60. Scaled to itself it fills the plot; scaled to
    the published axis, which runs to 100, it reaches three fifths of it.
    Which one happened is the whole question, and it is measurable.
    """
    with_axis = _bar_widths(_svg("bar"))
    without = _bar_widths(_svg("bar", axis=None))
    assert with_axis and without
    assert max(with_axis) < max(without) * 0.7


def test_the_widest_bar_is_proportional_to_its_value_on_that_scale():
    widths = _bar_widths(_svg("bar"))
    # 40 : 60 is the ratio of the shortest bar to the longest, whatever the
    # axis, because both are measured from the same zero.
    assert widths[0] / widths[3] == pytest.approx(40 / 60, rel=0.02)


# ---- a form with no scale is handed none --------------------------------

@pytest.mark.parametrize("kind", ["pie", "donut"])
def test_a_whole_divided_gets_no_ticks(kind):
    """A pie's magnitude is area. A ladder across it would be a second,
    wrong story about the same slices."""
    svg = _svg(kind)
    assert "SAR 25 million" not in svg


# ---- an old payload still exports ---------------------------------------

@pytest.mark.parametrize("kind", SINGLE)
def test_a_chart_published_before_axes_existed_still_draws(kind):
    """Saved threads hold answers rendered by the previous contract. An
    export that raised on them would lose a reader's own history."""
    svg = _svg(kind, axis=None)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_an_axis_with_no_usable_ticks_falls_back_to_the_data():
    svg = _svg("bar", axis={"kind": "measure", "label": "", "column": "v",
                            "unit": "SAR_MN", "ticks": []})
    assert svg.startswith("<svg")
    assert _bar_widths(svg)
