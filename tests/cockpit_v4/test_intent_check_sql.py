"""Raw-SQL intent checks are shadow-only and never break a run. UNIT, NO MODEL.

WHY. About one run in eight writes SQL instead of a governed plan, and none of
the plan rules can see it. `check_sql` reads only the statement's parse tree,
the question and a stub envelope (no database, no model). These tests pin what
it flags, what it must leave alone (the corporate migration SQL that joins two
explicit quarters, comparison questions), that an unparseable statement yields
no findings, and that the `validate_batch` hook can neither raise nor refuse
when the check itself fails.
"""

from __future__ import annotations

import types

from backend.cockpit_v4 import intent_check as ic
from backend.cockpit_v4.execute_tool import ExecutionService

ENV = types.SimpleNamespace(resolved_entities=(
    {"field": "region", "value": "Riyadh", "exact": True},))
NO_ENV = types.SimpleNamespace(resolved_entities=())

MIGRATION = """
WITH a AS (SELECT facility_id FROM corp_facility_quarter
           WHERE reporting_quarter = '2023Q4' AND stage IN (1, 2)),
     b AS (SELECT facility_id FROM corp_facility_quarter
           WHERE reporting_quarter = '2026Q1' AND stage = 3)
SELECT COUNT(*) FROM a JOIN b USING (facility_id)"""


def _rules(sql, question, envelope=NO_ENV):
    return [f.rule for f in ic.check_sql(sql, question=question, envelope=envelope)]


def test_migration_sql_with_explicit_literals_is_clean():
    q = "How many facilities that were Stage 1 or 2 in Q4 2023 migrated to Stage 3 by Q1 2026?"
    assert _rules(MIGRATION, q) == []


def test_max_subquery_period_for_named_quarter_is_flagged():
    sql = ("SELECT AVG(ecl_sar_mn) FROM corp_facility_quarter WHERE reporting_quarter = "
           "(SELECT MAX(reporting_quarter) FROM corp_facility_quarter)")
    found = ic.check_sql(sql, question="Average ECL in 2026Q1", envelope=NO_ENV)
    assert [f.rule for f in found] == ["sql_period"]
    assert found[0].severity == "shadow"


def test_months_of_named_quarter_count_as_literal():
    sql = ("SELECT 1 FROM t WHERE reporting_month IN ('2026-01','2026-02','2026-03') "
           "AND reporting_month = (SELECT MAX(reporting_month) FROM t)")
    assert _rules(sql, "ECL in 2026Q1") == []


def test_entity_without_filter_flagged_and_with_filter_clean():
    q = "Total EAD for Riyadh"
    assert _rules("SELECT SUM(ead_sar_mn) FROM corp_facility_quarter", q, ENV) == ["sql_entity"]
    ok = "SELECT SUM(ead_sar_mn) FROM corp_facility_quarter WHERE region = 'Riyadh'"
    assert _rules(ok, q, ENV) == []


def test_group_by_alone_does_not_excuse_entity():
    sql = "SELECT region, SUM(ead_sar_mn) FROM corp_facility_quarter GROUP BY region"
    assert _rules(sql, "Total EAD for Riyadh", ENV) == ["sql_entity"]


def test_comparison_question_is_left_alone():
    sql = "SELECT region, SUM(ead_sar_mn) FROM corp_facility_quarter GROUP BY region"
    assert _rules(sql, "Compare EAD of Riyadh with the other regions", ENV) == []


def test_ratio_sum_flagged():
    assert _rules("SELECT SUM(lgd_pct) FROM t", "lgd") == ["sql_ratio_sum"]
    assert _rules("SELECT SUM(pd_12m) FROM t", "pd") == ["sql_ratio_sum"]
    assert _rules("SELECT AVG(lgd_pct), SUM(ead_sar_mn) FROM t", "lgd") == []


def test_parse_failure_returns_empty():
    assert ic.check_sql("SELEC nonsense ((", question="2026Q1 Riyadh", envelope=ENV) == []
    assert ic.check_sql("", question="x", envelope=ENV) == []


def _service():
    svc = ExecutionService(session=None, scope=None, catalog=None, store=None, run_id="r",
                           tenant_id="t", release_id="x", limits=None)
    svc.intent_question = "Average ECL in 2026Q1"
    return svc


def test_hook_records_findings_without_refusing():
    svc = _service()
    step = types.SimpleNamespace(code="SELECT SUM(lgd_pct) FROM t")
    svc._shadow_intent(step)
    assert [f.rule for f in svc.shadow_findings] == ["sql_ratio_sum"]


def test_hook_never_raises_when_the_check_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("check bug")
    monkeypatch.setattr(ic, "check_sql", boom)
    svc = _service()
    svc._shadow_intent(types.SimpleNamespace(code="SELECT 1"))
    assert svc.shadow_findings == []


def test_hook_is_silent_when_mode_off(monkeypatch):
    monkeypatch.setenv(ic.ENV, "off")
    svc = _service()
    svc._shadow_intent(types.SimpleNamespace(code="SELECT SUM(lgd_pct) FROM t"))
    assert svc.shadow_findings == []


# ------------------------------------------ sql_operation_dropped (run b8aa21b3)

SHARED_TOP10 = ("Which sub-sectors share top 10, that had the largest absolute and relative "
                "increase in Stage 3 EAD between 2025Q2 and 2026Q2?")
LONG_FORMAT = ("SELECT reporting_quarter, sub_sector, SUM(ead_sar_mn) AS ead_sar_mn "
               "FROM corp_facility_quarter WHERE reporting_quarter IN ('2025Q2', '2026Q2') "
               "AND stage IN (3) GROUP BY reporting_quarter, sub_sector "
               "ORDER BY reporting_quarter, sub_sector")
CHANGE_ONLY = """
WITH s AS (SELECT sub_sector,
    sum(ead_sar_mn) FILTER (WHERE reporting_quarter = '2025Q2') AS a,
    sum(ead_sar_mn) FILTER (WHERE reporting_quarter = '2026Q2') AS b
  FROM corp_facility_quarter WHERE stage = 3 GROUP BY sub_sector)
SELECT sub_sector, b - a AS abs_change, 100 * (b / nullif(a, 0) - 1) AS pct_change
FROM s ORDER BY abs_change DESC"""


def _ops(sql, question=SHARED_TOP10):
    return sorted(f.detail["operation"] for f in ic.check_sql(sql, question=question)
                  if f.rule == "sql_operation_dropped")


def test_the_long_format_sql_of_run_b8aa21b3_drops_every_operation():
    assert _ops(LONG_FORMAT) == ["change", "intersect", "top_n"]


def test_a_change_without_ranking_still_misses_the_top_10_and_the_overlap():
    assert _ops(CHANGE_ONLY) == ["intersect", "top_n"]


def test_the_gold_sql_computes_every_operation():
    import json
    from pathlib import Path
    golden = json.loads((Path(__file__).parent / "golden" / "questions.json").read_text())
    item = next(q for q in golden["questions"] if q["id"] == "c_s3_overlap")
    assert item["question"] == SHARED_TOP10
    assert _ops(item["gold_sql"]) == []


def test_findings_are_shadow_severity():
    assert {f.severity for f in ic.check_sql(LONG_FORMAT, question=SHARED_TOP10)} == {"shadow"}
