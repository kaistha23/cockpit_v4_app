"""A plan can NAME a period, and the name is never replaced by 'latest'. NO MODEL; REAL DATABASE for the DuckDB cases.

Live failure: "Average ECL for Riyadh in 2026Q1" compiled to the latest
quarter (2026Q2) and a retail "Q4 2023 -> Q1 2026" question compiled to
MAX(reporting_month): silent wrong answers. These tests pin explicit periods
on synthetic books (format, convention and refusals), then run one plan per
domain against real release data where a fixture exists.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import plan_compiler as pc
from backend.cockpit_v4.contracts import Rejection

CORP_PERIODS = ("2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
RETAIL_PERIODS = tuple(f"2025-{m:02d}" for m in range(1, 13)) + ("2026-01", "2026-02", "2026-03", "2026-04")


def _corporate() -> pc.Book:
    metrics = {
        "ecl": pc.Metric("ecl", "Total ecl", "corp_facility_quarter", "SUM(ecl_sar_mn)", "SAR million"),
        "avg_ecl": pc.Metric("avg_ecl", "Simple average of ecl (unweighted)", "corp_facility_quarter",
                             "AVG(ecl_sar_mn)", "SAR million"),
    }
    fields = {"sector": pc.Field("sector", ("corp_facility_quarter",), ("Riyadh", "Jeddah"), False)}
    return pc.Book("corporate", metrics, fields, {"corp_facility_quarter": "reporting_quarter"},
                   {"corp_facility_quarter": CORP_PERIODS})


def _retail(populated=RETAIL_PERIODS) -> pc.Book:
    rel = "retail_account_month"
    metrics = {
        "ead": pc.Metric("ead", "Total ead", rel, "SUM(ead_sar_mn)", "SAR million"),
        "write_off_sar_mn": pc.Metric("write_off_sar_mn", "Total write off", rel,
                                      "SUM(write_off_sar_mn)", "SAR million"),
        "recovery_sar_mn": pc.Metric("recovery_sar_mn", "Total recovery", rel,
                                     "SUM(recovery_sar_mn)", "SAR million"),
    }
    fields = {"stage": pc.Field("stage", (rel,), ("1", "2", "3"), True)}
    return pc.Book("retail", metrics, fields, {rel: "reporting_month"}, {rel: tuple(populated)})


def _plan(values, **extra):
    return {"measures": ["ecl"], "period": {"kind": "explicit", "values": values}, **extra}


def _sql(plan, book):
    return pc.compile_plan(plan, book)[0][0]["code"]


def test_corporate_single_explicit_period_is_a_literal_with_no_period_dimension():
    sql = _sql(_plan(["2026Q1"], dimensions=["sector"]), _corporate())
    assert "reporting_quarter IN ('2026Q1')" in sql
    assert "MAX(" not in sql and "GROUP BY sector" in sql
    shape = pc.output_columns(_plan(["2026Q1"]), _corporate())["plan_1"]
    assert [c.role for c in shape.columns] == ["value"]


def test_corporate_several_periods_are_one_row_per_period_never_summed():
    book = _corporate()
    plan = _plan(["2026Q1", "2025Q4"], dimensions=["sector"])
    sql = _sql(plan, book)
    assert "SELECT reporting_quarter, sector, " in sql
    assert "reporting_quarter IN ('2025Q4', '2026Q1')" in sql
    assert "GROUP BY reporting_quarter, sector" in sql and "ORDER BY reporting_quarter, sector" in sql
    cols = pc.output_columns(plan, book)["plan_1"].columns
    assert cols[0].role == "period" and cols[0].name == "reporting_quarter"


@pytest.mark.parametrize("text", ["2026Q1", "2026 Q1", "Q1 2026", "q1-2026", "2026-02", "2026-2",
                                  "Feb 2026", "February 2026"])
def test_period_spellings_normalise_to_the_corporate_quarter(text):
    assert pc.normalize(_plan([text]), _corporate())["period"]["values"] == ["2026Q1"]


def test_values_survive_normalisation_deduplicated_and_sorted():
    p = pc.normalize(_plan(["2026Q1", "Q1 2026", "2025Q4"]), _corporate())
    assert p["period"] == {"kind": "explicit", "n": 0, "values": ["2025Q4", "2026Q1"], "aggregate": ""}


def test_values_without_a_kind_mean_explicit_but_a_conflicting_kind_is_refused():
    book = _corporate()
    assert pc.normalize({"measures": ["ecl"], "period": {"values": ["2026Q1"]}}, book)["period"]["kind"] == "explicit"
    with pytest.raises(Rejection, match="must be 'explicit'"):
        pc.normalize({"measures": ["ecl"], "period": {"kind": "latest", "values": ["2026Q1"]}}, book)


def test_more_than_twelve_periods_or_a_nonsense_period_is_refused():
    book = _corporate()
    with pytest.raises(Rejection, match="1 to 12"):
        pc.normalize(_plan([f"20{y}Q1" for y in range(10, 23)]), book)
    with pytest.raises(Rejection, match="is not a period"):
        pc.normalize(_plan(["last year"]), book)


def test_retail_stock_quarter_is_its_quarter_end_month_only():
    sql = _sql({"measures": ["ead"], "period": {"kind": "explicit", "values": ["2026Q1"]}}, _retail())
    assert "reporting_month IN ('2026-03')" in sql and "SELECT SUM(ead_sar_mn)" in sql


def test_retail_flow_quarter_is_three_months_summed_not_grouped_by_month():
    plan = {"measures": ["write_off_sar_mn", "recovery_sar_mn"],
            "period": {"kind": "explicit", "values": ["2026Q1"]}}
    steps, objective = pc.compile_plan(plan, _retail())
    sql = steps[0]["code"]
    assert "reporting_month IN ('2026-01', '2026-02', '2026-03')" in sql
    assert "GROUP BY" not in sql and not sql.startswith("SELECT reporting_month")
    assert "sum of 2026-01..2026-03" in objective and "sum of 2026-01..2026-03" in steps[0]["purpose"]
    assert [c.role for c in pc.output_columns(plan, _retail())["plan_1"].columns] == ["value", "value"]


def test_retail_flow_months_named_directly_stay_one_row_per_month():
    plan = {"measures": ["write_off_sar_mn"], "period": {"kind": "explicit", "values": ["2026-01", "2026-02"]}}
    assert "GROUP BY reporting_month" in _sql(plan, _retail())


def test_retail_flow_quarter_combined_with_another_period_is_refused():
    plan = {"measures": ["write_off_sar_mn"], "period": {"kind": "explicit", "values": ["2026Q1", "2025Q4"]}}
    with pytest.raises(Rejection, match="cannot be combined"):
        pc.compile_plan(plan, _retail())


def test_mixed_stock_and_flow_is_refused_with_advice_to_split():
    plan = {"measures": ["ead", "write_off_sar_mn"], "period": {"kind": "explicit", "values": ["2026Q1"]}}
    with pytest.raises(Rejection, match="split this into two plans"):
        pc.compile_plan(plan, _retail())


def test_an_unpopulated_period_is_refused_listing_what_exists_and_nothing_is_substituted():
    with pytest.raises(Rejection) as caught:
        pc.compile_plan(_plan(["2030Q1"]), _corporate())
    message = str(caught.value)
    assert "2030Q1 is not a period" in message and "2025Q1, 2025Q2, 2025Q3, 2025Q4, 2026Q1, 2026Q2" in message
    assert "Nothing was substituted" in message and "Nothing was executed" in message
    with pytest.raises(Rejection, match="2026-06 is not a period.*2025-01, 2025-02, 2025-03, \\.\\.\\., 2026-02"):
        pc.compile_plan({"measures": ["ead"], "period": {"kind": "explicit", "values": ["2026Q2"]}}, _retail())


def test_with_no_populated_periods_a_value_is_checked_by_format_only():
    book = _retail(populated=())
    assert "reporting_month IN ('2030-03')" in _sql(
        {"measures": ["ead"], "period": {"kind": "explicit", "values": ["2030Q1"]}}, book)


def test_compare_with_an_explicit_single_period_uses_literal_current_and_previous():
    sql = _sql(_plan(["2026Q1"], dimensions=["sector"], compare="prior"), _corporate())
    assert "reporting_quarter = '2026Q1'" in sql and "reporting_quarter = '2025Q4'" in sql
    sql = _sql(_plan(["2026Q1"], compare="year_ago"), _corporate())
    assert "reporting_quarter = '2025Q1'" in sql and "MAX(" not in sql
    sql = _sql({"measures": ["ead"], "compare": "prior",
                "period": {"kind": "explicit", "values": ["2026Q1"]}}, _retail())
    assert "reporting_month = '2026-03'" in sql and "reporting_month = '2026-02'" in sql


def test_compare_when_no_earlier_period_exists_is_refused_naming_the_populated_periods():
    with pytest.raises(Rejection) as caught:
        pc.compile_plan(_plan(["2025Q2"], compare="year_ago"), _corporate())
    assert "Periods held: 2025Q1" in str(caught.value)
    with pytest.raises(Rejection, match="exactly one explicit period"):
        pc.normalize(_plan(["2026Q1", "2025Q4"], compare="prior"), _corporate())


def test_latest_prior_and_last_n_are_unchanged():
    book = _corporate()
    assert "(SELECT MAX(reporting_quarter) FROM corp_facility_quarter)" in _sql(
        {"measures": ["ecl"]}, book)
    assert "OFFSET 1" in _sql({"measures": ["ecl"], "period": {"kind": "prior"}}, book)
    sql = _sql({"measures": ["ecl"], "period": {"kind": "last_n", "n": 3}}, book)
    assert "LIMIT 3" in sql and "GROUP BY reporting_quarter" in sql
    with pytest.raises(Rejection, match="period.kind must be 'latest'"):
        pc.normalize({"measures": ["ecl"], "period": {"kind": "last_n"}, "compare": "prior"}, book)


def test_the_schema_offers_explicit_values_and_the_plain_average_says_it_is_simple():
    schema = pc.plan_schema(_corporate())["properties"]["period"]["properties"]
    assert "explicit" in schema["kind"]["enum"] and schema["values"]["maxItems"] == 12
    assert "Simple average" in _corporate().metrics["avg_ecl"].label


def test_build_book_labels_plain_averages_as_simple_and_reads_populated_periods(runtime, session):
    book = pc.build_book(domain_id="corporate", catalog=runtime.catalog, session=session)
    averages = [m for m in book.metrics.values() if m.metric_id.startswith("avg_")]
    assert averages and all(m.label.startswith("Simple average of ") for m in averages)
    relation = next(r for r, c in book.period_column.items() if c == "reporting_quarter")
    held = book.populated[relation]
    assert held and list(held) == sorted(held)
    assert pc.build_book(domain_id="corporate", catalog=runtime.catalog, session=None).populated == {}


def test_a_named_quarter_runs_against_the_real_release(runtime, session):
    book = pc.build_book(domain_id="corporate", catalog=runtime.catalog, session=session)
    relation = book.metrics["ead_reported"].relation
    first = book.populated[relation][0]
    steps, _ = pc.compile_plan({"measures": ["ead_reported"],
                                "period": {"kind": "explicit", "values": [first]}}, book)
    rows = session.connection.execute(steps[0]["code"]).fetchall()
    expected = session.connection.execute(
        f"SELECT SUM({_ead_sum_col(book)}) FROM {relation} WHERE reporting_quarter = '{first}'").fetchone()
    assert len(rows) == 1 and rows[0][0] is not None
    assert rows[0][0] == pytest.approx(expected[0]) if expected[0] is not None else True


def _ead_sum_col(book):
    sql = book.metrics["ead_reported"].sql
    return sql[sql.index("(") + 1:sql.rindex(")")] if sql.startswith("SUM(") else "ead_sar_mn"
