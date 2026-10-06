"""REAL DATABASE · MODEL MOCK. A plan is compiled to SQL by code and run like any submission.

The plan path exists for models measured to write SQL poorly (slm_v1's
lesson): the model names governed measures, fields and periods; code writes
the SQL. These tests pin that the compiler refuses what the book does not
hold, never joins, and that a planned submission meets exactly the same
validation, execution and evidence rules as a hand-written one -- and that
a model whose profile does not ask for plans is never offered one.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.cockpit_v4 import plan_compiler as pc
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import Rejection
from backend.cockpit_v4.model_capabilities import ModelTraits
from conftest import ScriptedResult, intent, tool_call
from test_catalog_convergence import finish


@pytest.fixture
def book(session, runtime):
    return pc.build_book(domain_id="corporate", catalog=runtime.catalog, session=session)


def _rows(session, steps):
    return [session.connection.execute(s["code"]).fetchall() for s in steps]


def test_every_name_in_the_plan_schema_is_governed(book):
    schema = pc.plan_schema(book)
    measures = schema["properties"]["measures"]["items"]["enum"]
    assert "ead_reported" in measures and measures == sorted(book.metrics)
    assert schema["properties"]["dimensions"]["items"]["enum"] == sorted(book.fields)


def test_a_plan_compiles_to_one_self_contained_step_per_relation(book, session):
    steps, objective = pc.compile_plan(
        {"measures": ["ead_reported"], "dimensions": ["sector_name"], "sort": "measure_desc", "limit": 3},
        book)
    assert len(steps) == 1 and " JOIN " not in steps[0]["code"].upper()
    rows = _rows(session, steps)[0]
    assert len(rows) == 3 and rows[0][1] >= rows[1][1] >= rows[2][1]
    assert "by sector_name" in objective


def test_a_comparison_and_a_trend_compile_and_run(book, session):
    compare, _ = pc.compile_plan({"measures": ["ead_reported"], "dimensions": ["sector_name"],
                                  "compare": "prior"}, book)
    trend, _ = pc.compile_plan({"measures": ["ead_reported"], "period": {"kind": "last_n", "n": 3}}, book)
    assert "ead_reported_prev" in compare[0]["code"] and _rows(session, compare)[0]
    assert len(_rows(session, trend)[0]) == 3


@pytest.mark.parametrize("plan, says", [
    ({"measures": ["exposure"]}, "not a governed measure"),
    ({"measures": ["ead_reported"], "dimensions": ["colour"]}, "not a governed field"),
    ({"measures": ["ead_reported"], "filters": [{"field": "sector_name", "values": ["Nowhere"]}]},
     "is not a value of sector_name"),
    ({"measures": ["ead_reported"], "period": {"kind": "last_n"}, "compare": "prior"},
     "period.kind must be 'latest'"),
])
def test_what_the_book_does_not_hold_is_refused_and_nothing_is_substituted(book, plan, says):
    with pytest.raises(Rejection) as caught:
        pc.compile_plan(plan, book)
    assert says in str(caught.value) and "Nothing was executed" in str(caught.value)


def test_shapes_are_normalised_but_meanings_are_not(book):
    plan = pc.normalize({"measures": "EAD_REPORTED", "dimensions": "sector_name",
                         "period": "latest", "compare": "bogus"}, book)
    assert plan["measures"] == ["ead_reported"] and plan["dimensions"] == ["sector_name"]
    assert plan["compare"] == "none"


def test_four_governed_measures_are_a_valid_driver_bundle(book):
    """A deterioration view can need EAD, ECL, coverage and Stage 2 evidence."""
    by_relation = {}
    for metric in book.metrics.values():
        by_relation.setdefault(metric.relation, []).append(metric.metric_id)
    measures = next(ids[:4] for ids in by_relation.values() if len(ids) >= 4)
    normalised = pc.normalize({"measures": measures}, book)
    assert normalised["measures"] == measures


def test_lifetime_pd_sensitivity_is_explicit_period_row_level_and_not_relative(book):
    sensitivity = {
        "driver": "pd_lifetime",
        "shock": {"kind": "absolute_percentage_points", "value": 1},
        "population": {"sector": "Manufacturing", "stage": 2, "reporting_quarter": "2026Q1"},
        "held_constant": ["lgd_pct", "ead_sar_mn", "macro_overlay"],
        "denominator": "sector_total_recognised_ecl",
    }
    steps, _ = pc.compile_sensitivity(sensitivity, book)
    sql = steps[0]["code"]
    assert "reporting_quarter = '2026Q1'" in sql
    assert "stage = 2" in sql and "lgd_pct / 100.0" in sql
    assert "* 0.01" in sql and "SUM(ecl_sar_mn)" in sql


@pytest.mark.parametrize("change", [
    {"driver": "pd_pit_12m"},
    {"shock": {"kind": "relative_multiplier", "value": 1.01}},
    {"population": {"sector": "Manufacturing", "stage": 2, "reporting_quarter": "latest"}},
])
def test_lifetime_pd_sensitivity_refuses_the_common_ambiguity_traps(book, change):
    sensitivity = {
        "driver": "pd_lifetime",
        "shock": {"kind": "absolute_percentage_points", "value": 1},
        "population": {"sector": "Manufacturing", "stage": 2, "reporting_quarter": "2026Q1"},
        "held_constant": ["lgd_pct", "ead_sar_mn", "macro_overlay"],
        "denominator": "sector_total_recognised_ecl",
    }
    sensitivity.update(change)
    with pytest.raises(Rejection):
        pc.compile_sensitivity(sensitivity, book)


def test_a_planned_submission_runs_end_to_end_for_a_model_that_asks_for_plans(
        drive, runtime, capability):
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    plan_call = tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "plan": {"measures": ["ead_reported"], "dimensions": ["sector_name"],
                 "sort": "measure_desc"}}, "tu-p")
    outcome, provider, _ = drive("EAD by sector this quarter",
                                 [ScriptedResult(tool_calls=[plan_call]), _finish_from_plan])
    assert outcome.state == st.COMPLETED, outcome.message
    execute = next(t for t in provider.sent[0]["tools"] if t["name"] == "execute_analysis")
    assert "plan" in execute["input_schema"]["properties"]


def test_three_invalid_plans_do_not_consume_execution_submissions(
        drive, runtime, capability, store_db):
    """Compiler repairs are bounded separately: no SQL exists until compilation succeeds."""
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    bad = ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "plan": {"measures": []}}, "tu-bad")])
    good = ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "plan": {"measures": ["ead_reported"], "dimensions": ["sector_name"]}}, "tu-good")])
    outcome, _, record = drive(
        "Which sectors deteriorated most this quarter?",
        [bad, bad, bad, good, _finish_from_plan])
    assert outcome.state == st.COMPLETED, outcome.message
    submissions = store_db.submissions_for_run(record.run_id)
    assert len(submissions) == 1


def test_fourth_invalid_plan_stops_with_plan_format_limit(
        drive, runtime, capability, store_db):
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    bad = ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "plan": {"measures": []}}, "tu-bad")])
    outcome, _, record = drive("Which sectors deteriorated most this quarter?",
                               [bad, bad, bad, bad])
    assert outcome.error_code == st.PLAN_FORMAT_EXHAUSTED
    assert store_db.submissions_for_run(record.run_id) == []


def _finish_from_plan(messages):
    import json
    from conftest import final
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    cell = step["preview"][0]
    evidence = {"artifact_id": step["artifact_id"], "row_key": f"sector_name={cell['sector_name']}",
                "column_id": "ead_reported"}
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="The largest is {{claim.top}}.",
        coverage=[{"subquestion": "a", "status": "answered", "evidence_refs": [evidence]}],
        numeric_claims=[{"claim_id": "top", "decimal_value": repr(float(cell["ead_reported"])),
                         "unit": "SAR million", "display_precision": 2, "evidence": evidence}]),
        "tu-f")])


def test_a_model_without_the_trait_is_never_offered_a_plan(drive, runtime, capability):
    from test_catalog_convergence import EAD_SQL, execute_call
    import oracles
    quarter = oracles.latest_quarter(runtime.cfg.release_id)
    outcome, provider, _ = drive("EAD by sector this quarter",
                                 [ScriptedResult(tool_calls=[execute_call(EAD_SQL, {"1": quarter})]), finish])
    assert outcome.state == st.COMPLETED, outcome.message
    for sent in provider.sent:
        for tool in sent["tools"]:
            assert "plan" not in tool["input_schema"].get("properties", {})


# ------------------------------------------------- compare "between" (run b8aa21b3)

BETWEEN = {"measures": ["ead_reported"], "dimensions": ["sector_name"],
           "period": {"kind": "explicit", "values": ["2026Q2", "2025Q2"]}, "compare": "between"}


def test_between_compares_the_later_named_period_with_the_earlier(book, session):
    """Run b8aa21b3 asked for the increase 'between 2025Q2 and 2026Q2' and got
    raw totals per quarter: compare could only reach one step or one year back
    from ONE explicit period. `between` names both, in either order."""
    steps, objective = pc.compile_plan(BETWEEN, book)
    sql = steps[0]["code"]
    assert "reporting_quarter = '2026Q2'" in sql.split("prev AS")[0]
    assert "reporting_quarter = '2025Q2'" in sql.split("prev AS")[1]
    assert "FULL JOIN prev" in sql and "compared with the 2025Q2 period" in objective
    rows = _rows(session, steps)[0]
    cur = dict(session.connection.execute(
        "SELECT sector_name, SUM(ead_reported) FROM cockpit_facility_quarter "
        "WHERE reporting_quarter = '2026Q2' GROUP BY 1").fetchall())
    prev = dict(session.connection.execute(
        "SELECT sector_name, SUM(ead_reported) FROM cockpit_facility_quarter "
        "WHERE reporting_quarter = '2025Q2' GROUP BY 1").fetchall())
    for name, now, then, change, _pct in rows:
        assert now == pytest.approx(cur.get(name, 0)) and then == pytest.approx(prev.get(name, 0))
        assert change == pytest.approx(now - then)
    shape = pc.output_columns(BETWEEN, book)["plan_1"]
    assert [c.name for c in shape.columns] == [
        "sector_name", "ead_reported", "ead_reported_prev", "ead_reported_change",
        "ead_reported_pct_change"]


@pytest.mark.parametrize("sort, column", [("change_desc", 3), ("pct_change_desc", 4)])
def test_change_sorts_rank_the_movement_not_the_level(book, session, sort, column):
    steps, _ = pc.compile_plan({**BETWEEN, "sort": sort, "limit": 3}, book)
    sql = steps[0]["code"]
    assert "ORDER BY COALESCE(cur.ead_reported, 0) - COALESCE(prev.ead_reported, 0)" in sql \
        or "NULLIF(ABS(COALESCE(prev.ead_reported, 0)), 0) DESC NULLS LAST" in sql
    ranked = _rows(session, steps)[0]
    everything = _rows(session, pc.compile_plan(BETWEEN, book)[0])[0]
    best = sorted((r for r in everything if r[column] is not None),
                  key=lambda r: (-r[column], r[0]))[:3]
    assert [r[0] for r in ranked] == [r[0] for r in best]


def test_a_group_held_in_only_one_named_period_still_moved(book):
    """0 -> X is an increase of X. Totals read a missing side as zero; averages
    stay NULL because an average of nothing is not zero."""
    sql = pc.compile_plan(BETWEEN, book)[0][0]["code"]
    assert "COALESCE(prev.ead_reported, 0) AS ead_reported_prev" in sql
    avg = next(m for m, metric in book.metrics.items()
               if metric.relation == "cockpit_facility_quarter" and metric.sql.startswith("AVG("))
    sql = pc.compile_plan({**BETWEEN, "measures": [avg]}, book)[0][0]["code"]
    assert "COALESCE" not in sql


def test_prior_and_year_ago_sql_is_unchanged_by_between(book):
    """The existing comparisons keep their LEFT JOIN and level sort exactly."""
    sql = pc.compile_plan({"measures": ["ead_reported"], "dimensions": ["sector_name"],
                           "compare": "prior", "sort": "measure_desc"}, book)[0][0]["code"]
    assert "cur LEFT JOIN prev USING (sector_name)" in sql
    assert sql.endswith("ORDER BY cur.ead_reported DESC") and "COALESCE" not in sql


@pytest.mark.parametrize("plan, says", [
    ({**BETWEEN, "period": {"kind": "explicit", "values": ["2026Q2"]}}, "exactly two values"),
    ({**BETWEEN, "period": {"kind": "explicit", "values": ["2024Q2", "2025Q2", "2026Q2"]}},
     "exactly two values"),
    ({**BETWEEN, "period": {"kind": "latest"}}, "exactly two values"),
    ({**BETWEEN, "compare": "prior"}, "needs exactly one explicit period"),
    ({"measures": ["ead_reported"], "dimensions": ["sector_name"], "sort": "change_desc"},
     "needs plan.compare"),
])
def test_between_and_change_sorts_refuse_what_they_cannot_mean(book, plan, says):
    with pytest.raises(Rejection) as caught:
        pc.compile_plan(plan, book)
    assert says in str(caught.value)
