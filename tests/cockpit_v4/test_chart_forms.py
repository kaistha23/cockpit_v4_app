"""
A migration matrix is a grid, and every form is drawn as itself.

UNIT · REPRODUCTION. No model call.

The defect this exists for
--------------------------
A live answer published "Rating migration matrix 2026Q1 -> 2026Q2" as a flat
48-row table: `rating_from`, `rating_to`, `borrowers`, `ead_q2_sar_mn`, and
a "View all 48 rows (38 more)" link. A migration read by scrolling is a
migration nobody reads. It is one picture -- a grid, with the diagonal
carrying everything that did not move.

Three things had to be true before it could be drawn, and none was:

  * the `kind` enum was `bar|line|waterfall|scatter`, so the analyst could
    not ask for a matrix at all;
  * the contract carried ONE categorical axis (`x_column` plus measures).
    `series_column` was declared in the schema and read by nothing, so
    there was nowhere to put the second one;
  * `MAX_CHART_POINTS = 25` counted ROWS, and a 7x7 migration is 49 of
    them, so even a charted version would have been dropped as "past the
    25 a reader can take in".

Two neighbouring defects, found while fixing it:

  * `waterfall` and `scatter` had been legal in the contract all along and
    fell through the renderer's dispatch to `_bar_svg`. An analyst asking
    for a bridge got bars and was told nothing.
  * nothing in PYTHON ever validated `kind`. `parse_final` took each chart
    as a raw dict, so the JSON-schema enum was the only guard and an
    unknown kind reached the renderer and was drawn as a bar.

What is pinned here:

  the grid has two axes            -> rows, columns and cells
  the axis is the RESULT'S order   -> not alphabetical; the diagonal aligns
  absent is not zero               -> a pair nobody reported has no cell
  a matrix is counted in CATEGORIES -> 7x7 survives the point gate
  a box is five server-computed numbers -> not raw rows for a browser
  every kind draws as itself       -> including waterfall and scatter
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import export as export_mod
from backend.cockpit_v4.finalization import (BOX, CHART_KINDS, HEATMAP,
                                             MAX_CHART_POINTS, Finalizer)

#: The order the query returned, worst-to-best, as an ORDER BY produces it.
GRADES = ["AA", "A+", "A", "A-", "BBB+", "BBB", "BBB-"]


def _migration_rows(reach: int = 1):
    """A migration where a borrower moves at most `reach` grades."""
    return [{"rating_from": f, "rating_to": t, "borrowers": 50 - 7 * abs(i - j)}
            for i, f in enumerate(GRADES) for j, t in enumerate(GRADES)
            if abs(i - j) <= reach]


MATRIX_CHART = {
    "kind": HEATMAP, "title": "Rating migration",
    "artifact_id": "art-1", "x_column": "rating_to",
    "series_column": "rating_from", "y_columns": ["borrowers"],
    "unit": "borrowers"}


def _record(rows, columns):
    return {"columns": list(columns), "rows": list(rows)}


@pytest.fixture
def finalizer(store_db):
    """A Finalizer over a real store, with one artifact this run owns."""
    from backend.cockpit_v4.config import STANDARD_LIMITS

    return Finalizer(store=store_db, tenant_id="t", release_id="rel",
                     limits=STANDARD_LIMITS, run_artifacts={"art-1"})


@pytest.fixture
def stored(store_db):
    """Put a result in the store under the id the charts reference."""
    def _put(rows, columns):
        artifact_id = store_db.put_artifact(
            run_id="r-charts", tenant_id="t", kind="result",
            release_id="rel", scope={"step_id": "s1"},
            columns=list(columns), rows=list(rows))
        return artifact_id
    return _put


# ---- the grid ------------------------------------------------------------

def test_a_from_to_result_becomes_a_grid():
    rows = _migration_rows()
    matrix = Finalizer._matrix(
        MATRIX_CHART, _record(rows, ["rating_from", "rating_to", "borrowers"]),
        {"borrowers": "borrowers"}, "borrowers", disp)

    assert matrix["square"] is True
    assert matrix["rows"] == matrix["columns"] == GRADES
    assert matrix["measure"] == "borrowers"
    # A cell for every pair the result reported, and none for the rest.
    assert len(matrix["cells"]) == len(rows)


def test_the_axis_is_the_results_own_order_not_the_alphabet():
    """`A, A+, A-, AA, BBB` is not a rating scale.

    Sorting the labels puts the grades in dictionary order and the diagonal
    then runs through pairs that have nothing to do with each other. The
    result was ordered by the analyst's query; this draws that order, which
    is the same rule `render_tables` follows.
    """
    matrix = Finalizer._matrix(
        MATRIX_CHART,
        _record(_migration_rows(), ["rating_from", "rating_to", "borrowers"]),
        {}, "borrowers", disp)
    assert matrix["rows"] == GRADES
    assert matrix["rows"] != sorted(GRADES)
    # The diagonal is a diagonal: from-X sits against to-X.
    for index, grade in enumerate(matrix["rows"]):
        assert matrix["columns"][index] == grade


def test_a_pair_the_query_never_reported_has_no_cell():
    """ABSENT IS NOT ZERO.

    No row for AA -> BBB- means the query did not report that move. A zero
    would assert that nobody made it, which is a different claim and not
    one this result supports.
    """
    matrix = Finalizer._matrix(
        MATRIX_CHART,
        _record(_migration_rows(), ["rating_from", "rating_to", "borrowers"]),
        {}, "borrowers", disp)
    assert "AA|AA" in matrix["cells"]
    assert "AA|BBB-" not in matrix["cells"]


def test_the_cells_carry_the_published_string():
    matrix = Finalizer._matrix(
        MATRIX_CHART,
        _record(_migration_rows(), ["rating_from", "rating_to", "borrowers"]),
        {"borrowers": "borrowers"}, "borrowers", disp)
    assert matrix["display"]["AA|AA"] == "50 borrowers"


def test_a_rectangular_matrix_keeps_its_two_axes():
    """Not every grid is square. Sector against stage has no diagonal."""
    rows = [{"sector": s, "stage": g, "ecl": 10}
            for s in ("Energy", "Real Estate", "Retail")
            for g in ("Stage 1", "Stage 2", "Stage 3")]
    matrix = Finalizer._matrix(
        {"kind": HEATMAP, "x_column": "stage", "series_column": "sector",
         "y_columns": ["ecl"]},
        _record(rows, ["sector", "stage", "ecl"]), {}, "SAR million", disp)
    assert matrix["square"] is False
    assert matrix["rows"] == ["Energy", "Real Estate", "Retail"]
    assert matrix["columns"] == ["Stage 1", "Stage 2", "Stage 3"]


# ---- the point gate ------------------------------------------------------

def test_a_seven_by_seven_migration_is_seven_points_not_forty_nine(
        finalizer):
    """The gate that dropped the live matrix.

    49 rows is past `MAX_CHART_POINTS`, and counting them is what made a
    grid a reader takes in at a glance look like a wall of bars.
    """
    rows = _migration_rows(reach=6)
    assert len(rows) > MAX_CHART_POINTS, "the fixture must exceed the gate"
    problem = finalizer._chart_shape_problem(
        MATRIX_CHART, 0, _record(rows, ["rating_from", "rating_to",
                                        "borrowers"]))
    assert problem == "", problem


def test_a_grid_too_wide_to_read_is_still_dropped(finalizer):
    """The ceiling is not removed, only counted correctly."""
    wide = [{"rating_from": f"F{i}", "rating_to": f"T{j}", "n": 1}
            for i in range(30) for j in range(2)]
    problem = finalizer._chart_shape_problem(
        {**MATRIX_CHART, "x_column": "rating_to",
         "series_column": "rating_from", "y_columns": ["n"]},
        0, _record(wide, ["rating_from", "rating_to", "n"]))
    assert "past the" in problem


def test_a_heatmap_without_a_second_axis_is_refused(finalizer, stored):
    """One axis is not a grid, and drawing it would be a bar chart."""
    artifact = stored(_migration_rows(),
                      ["rating_from", "rating_to", "borrowers"])
    finalizer.run_artifacts = {artifact}
    problem = finalizer._check_chart(
        {**MATRIX_CHART, "artifact_id": artifact, "series_column": ""}, 0)
    assert "series_column" in problem


# ---- the box -------------------------------------------------------------

def test_the_five_numbers_are_computed_by_the_server():
    """Not raw observations for a browser to reduce.

    A quartile is a figure a reader acts on. Computing it in the client
    would put arithmetic outside the trace, which is the one thing the
    numeric contract exists to prevent.
    """
    rows = ([{"sector": "Energy", "ead": v} for v in (10, 20, 30, 40, 50)]
            + [{"sector": "Real Estate", "ead": v} for v in (5, 15, 25, 35)])
    boxes = Finalizer._boxes(
        {"kind": BOX, "x_column": "sector", "y_columns": ["ead"]},
        _record(rows, ["sector", "ead"]), {"ead": "SAR million"},
        "SAR million", disp)
    by_label = {b["label"]: b for b in boxes}

    energy = by_label["Energy"]
    assert energy["count"] == 5
    # Linear interpolation, which is what numpy.percentile and every
    # spreadsheet produce, so a reader checking against their own tooling
    # gets these numbers.
    assert (energy["minimum"], energy["q1"], energy["median"],
            energy["q3"], energy["maximum"]) == (
        "10", "20.00", "30.0", "40.00", "50")
    assert energy["display"]["median"] == "SAR 30 million"

    estate = by_label["Real Estate"]
    assert (estate["q1"], estate["median"], estate["q3"]) == (
        "12.50", "20.0", "27.50")


def test_a_box_plot_is_counted_in_boxes_not_observations(finalizer):
    """Its rows ARE the distribution and are supposed to be many."""
    rows = [{"sector": s, "ead": n}
            for s in ("Energy", "Real Estate") for n in range(40)]
    assert len(rows) > MAX_CHART_POINTS
    problem = finalizer._chart_shape_problem(
        {"kind": BOX, "x_column": "sector", "y_columns": ["ead"]},
        0, _record(rows, ["sector", "ead"]))
    assert problem == "", problem


def test_one_ungrouped_distribution_is_a_legitimate_single_box(
        finalizer):
    """"A chart needs two points" is about categories, not observations."""
    rows = [{"ead": n} for n in range(12)]
    problem = finalizer._chart_shape_problem(
        {"kind": BOX, "x_column": "", "y_columns": ["ead"]},
        0, _record(rows, ["ead"]))
    assert problem == "", problem


def test_too_few_observations_for_a_quartile_is_refused(finalizer):
    problem = finalizer._chart_shape_problem(
        {"kind": BOX, "x_column": "", "y_columns": ["ead"]},
        0, _record([{"ead": 1}, {"ead": 2}], ["ead"]))
    assert "quartiles" in problem


# ---- the kind ------------------------------------------------------------

def test_a_kind_no_renderer_knows_is_refused_not_drawn_as_a_bar(
        finalizer):
    """Nothing in Python validated `kind`.

    `parse_final` took each chart as a raw dict, so the JSON-schema enum
    was the only guard, and anything that got past it reached `chart_svg`,
    fell through its dispatch and was silently drawn as a bar.
    """
    problem = finalizer._check_chart({**MATRIX_CHART, "kind": "sankey"}, 0)
    assert "not a form CreditProbe draws" in problem


def test_the_python_and_json_vocabularies_agree():
    """Two lists of the same forms are two lists that can disagree."""
    import json
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "backend" / "cockpit_v4"
         / "contracts" / "shared_defs.schema.json").read_text())
    enum = schema["$defs"]["Chart"]["properties"]["kind"]["enum"]
    assert sorted(enum) == sorted(CHART_KINDS)


# ---- the renderer --------------------------------------------------------

def _lineage():
    return export_mod.Lineage(
        run_id="r", question="q", domain_id=dom.CORPORATE,
        domain_label="Corporate Credit", release_id="rel",
        release_fingerprint="f" * 64, tenant_id="t",
        reporting_currency="SAR", amount_scale="million",
        produced_at="2026-09-17T00:00:00+00:00")


def test_a_matrix_exports_as_a_grid_of_cells():
    matrix = Finalizer._matrix(
        MATRIX_CHART,
        _record(_migration_rows(), ["rating_from", "rating_to", "borrowers"]),
        {"borrowers": "borrowers"}, "borrowers", disp)
    svg = export_mod.chart_svg(
        chart={**MATRIX_CHART, "matrix": matrix}, lineage=_lineage())

    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count("<rect") >= len(matrix["cells"])
    assert "Rating migration" in svg
    # Every grade is labelled on both axes.
    for grade in GRADES:
        assert grade in svg
    # The diagonal is marked, so "did not move" is visible without reading.
    assert "#0ea5e9" in svg


def test_a_box_plot_exports_with_a_median_line_per_box():
    rows = [{"sector": s, "ead": n}
            for s in ("Energy", "Real Estate") for n in (1, 2, 3, 4, 9)]
    boxes = Finalizer._boxes(
        {"kind": BOX, "x_column": "sector", "y_columns": ["ead"]},
        _record(rows, ["sector", "ead"]), {"ead": "SAR million"},
        "SAR million", disp)
    svg = export_mod.chart_svg(
        chart={"kind": BOX, "title": "Spread", "boxes": boxes},
        lineage=_lineage())
    assert svg.startswith("<svg")
    assert "Energy" in svg and "Real Estate" in svg
    assert svg.count("<line") >= 4        # a whisker and a median each


@pytest.mark.parametrize("kind,mark", [
    ("bar", "<rect"),
    ("line", "<polyline"),
    ("scatter", "<circle"),
    ("waterfall", "<rect"),
])
def test_every_kind_draws_as_itself(kind, mark):
    """`waterfall` and `scatter` were legal and drawn as bars."""
    chart = {
        "kind": kind, "title": kind, "y_columns": ["v"],
        "unit": "SAR million",
        "points": [{"label": f"P{i}", "values": {"v": v},
                    "display": {"v": f"SAR {v} million"}}
                   for i, v in enumerate((30, -10, 20, 5))]}
    svg = export_mod.chart_svg(chart=chart, lineage=_lineage())
    assert mark in svg, f"{kind} did not draw its own mark"


def test_a_scatter_is_not_joined_up_and_a_line_is():
    """The one that distinguishes them: a scatter asserts no ordering."""
    points = [{"label": f"P{i}", "values": {"v": v},
               "display": {"v": str(v)}}
              for i, v in enumerate((3, 1, 4, 1, 5))]
    scatter = export_mod.chart_svg(
        chart={"kind": "scatter", "title": "s", "y_columns": ["v"],
               "points": points}, lineage=_lineage())
    line = export_mod.chart_svg(
        chart={"kind": "line", "title": "l", "y_columns": ["v"],
               "points": points}, lineage=_lineage())
    assert "<polyline" not in scatter
    assert "<polyline" in line


def test_a_waterfall_bar_starts_where_the_last_one_finished():
    """Drawn as ordinary bars it loses the only thing a bridge is for.

    Two series with the same values in a different order must produce
    different pictures: the running total is what a bridge shows.
    """
    def draw(values):
        return export_mod.chart_svg(chart={
            "kind": "waterfall", "title": "w", "y_columns": ["v"],
            "points": [{"label": f"P{i}", "values": {"v": v},
                        "display": {"v": str(v)}}
                       for i, v in enumerate(values)]}, lineage=_lineage())

    assert draw([50, -20, 10]) != draw([-20, 50, 10])
