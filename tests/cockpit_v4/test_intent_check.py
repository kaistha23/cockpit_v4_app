"""The plan must answer the question that was asked. UNIT, NO MODEL.

Live failures this pins: "Average ECL for Region Riyadh in 2026Q1" compiled to
the latest quarter; "for Riyadh" compiled to a GROUP BY region with no filter;
a Stage 1/2 -> Stage 3 migration question compiled to a single latest-month
plan. Synthetic books and a stub envelope only: the rules read the question,
the normalised plan and the book, nothing else.
"""

from __future__ import annotations

import types

import pytest

from backend.cockpit_v4 import intent_check as ic
from backend.cockpit_v4 import plan_compiler as pc
from backend.cockpit_v4.contracts import Rejection

CORP = ("2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
MONTHS = tuple(f"2025-{m:02d}" for m in range(1, 13)) + ("2026-01", "2026-02", "2026-03", "2026-04")


def _corporate() -> pc.Book:
    rel = "corp_facility_quarter"
    metrics = {
        "ecl": pc.Metric("ecl", "Total ecl", rel, "SUM(ecl_sar_mn)", "SAR million"),
        "avg_ecl": pc.Metric("avg_ecl", "Simple average of ecl (unweighted)", rel,
                             "AVG(ecl_sar_mn)", "SAR million"),
    }
    fields = {"region": pc.Field("region", (rel,), ("Riyadh", "Makkah", "Eastern"), False)}
    return pc.Book("corporate", metrics, fields, {rel: "reporting_quarter"}, {rel: CORP})


def _retail() -> pc.Book:
    rel = "retail_account_month"
    metrics = {"ead": pc.Metric("ead", "Total ead", rel, "SUM(ead_sar_mn)", "SAR million")}
    fields = {"stage": pc.Field("stage", (rel,), ("1", "2", "3"), True)}
    return pc.Book("retail", metrics, fields, {rel: "reporting_month"}, {rel: MONTHS})


def _env(*entries):
    return types.SimpleNamespace(resolved_entities=tuple(
        {"term": v, "field": f, "value": v, "exact": True, "say": f"{f}={v}"} for f, v in entries))


RIYADH = _env(("region", "Riyadh"))
RULES = lambda found: {f.rule for f in found if f.severity == "refuse"}  # noqa: E731


def _check(plan, question, env=RIYADH, book=None):
    return ic.check_plan(plan, question=question, envelope=env, book=book or _corporate())


def test_riyadh_2026q1_with_latest_period_is_refused_telling_the_analyst_what_to_send():
    found = _check({"measures": ["avg_ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}]},
                   "Average ECL for Region Riyadh in 2026Q1")
    assert RULES(found) == {"period_named"}
    assert '"kind": "explicit", "values": ["2026Q1"]' in found[0].message


def test_riyadh_only_as_a_dimension_is_a_scope_refusal_showing_the_filter():
    found = _check({"measures": ["avg_ecl"], "dimensions": ["region"],
                    "period": {"kind": "explicit", "values": ["2026Q1"]}},
                   "Average ECL for Region Riyadh in 2026Q1")
    assert RULES(found) == {"entity_scope"}
    assert '"field": "region", "values": ["Riyadh"]' in found[0].message


def test_a_stage_migration_between_two_named_periods_needs_steps_not_a_plan():
    found = ic.check_plan(
        {"measures": ["ead"]},
        question="How many facilities that were Stage 1 or 2 in Q4 2023 migrated to Stage 3 by Q1 2026?",
        envelope=_env(), book=_retail())
    assert RULES(found) == {"period_transition"}
    assert "steps" in found[0].message and "joins" in found[0].message


def test_the_correct_plan_passes():
    plan = {"measures": ["avg_ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}],
            "period": {"kind": "explicit", "values": ["2026Q1"]}}
    assert _check(plan, "Average ECL for Region Riyadh in 2026Q1") == []


def test_comparing_across_regions_with_a_region_dimension_is_not_a_scope_refusal():
    found = _check({"measures": ["ecl"], "dimensions": ["region"]},
                   "Compare ECL across regions, Riyadh included")
    assert "entity_scope" not in RULES(found)
    found = _check({"measures": ["ecl"], "dimensions": ["region"]}, "ECL by region, with Riyadh first")
    assert "entity_scope" not in RULES(found)


def test_two_resolved_values_of_one_field_are_a_comparison():
    env = _env(("region", "Riyadh"), ("region", "Makkah"))
    assert _check({"measures": ["ecl"], "dimensions": ["region"]}, "Riyadh vs Makkah ECL", env) == []
    assert _check({"measures": ["ecl"]}, "ECL for Riyadh and Makkah", env) == []


def test_a_fuzzy_match_or_an_ungoverned_field_is_not_enforced():
    fuzzy = types.SimpleNamespace(resolved_entities=(
        {"term": "ryad", "field": "region", "value": "Riyadh", "exact": False, "say": ""},))
    assert _check({"measures": ["ecl"]}, "ECL for ryad", fuzzy) == []
    other = _env(("facility_type", "Project Finance"))
    assert _check({"measures": ["ecl"]}, "ECL for project finance", other) == []


def test_missing_exact_flag_is_enforced_only_when_the_value_appears_in_the_question():
    bare = types.SimpleNamespace(resolved_entities=({"term": "x", "field": "region", "value": "Riyadh"},))
    assert RULES(_check({"measures": ["ecl"]}, "ECL for Riyadh", bare)) == {"entity_scope"}
    assert _check({"measures": ["ecl"]}, "ECL for the capital", bare) == []


def test_named_period_equal_to_latest_with_kind_latest_passes():
    assert _check({"measures": ["ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}]},
                  "ECL for Riyadh in 2026Q2") == []
    assert _check({"measures": ["ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}],
                   "period": {"kind": "prior"}}, "ECL for Riyadh in Q1 2026") == []


@pytest.mark.parametrize("text,label", [("in 2026 Q1", "2026Q1"), ("in Q1-2026", "2026Q1"),
                                        ("in the first quarter of 2026", "2026Q1"),
                                        ("in March 2026", "2026-03"), ("in Mar 2026", "2026-03"),
                                        ("in 2026-03", "2026-03")])
def test_period_spellings_in_the_question(text, label):
    named = ic.named_periods(f"ECL {text}")
    assert [ic._label(n) for n in named] == [label]


def test_retail_stock_quarter_as_its_quarter_end_month_passes_and_latest_is_refused():
    plan = {"measures": ["ead"], "period": {"kind": "explicit", "values": ["2026-03"]}}
    assert ic.check_plan(plan, question="EAD in 2026Q1", envelope=_env(), book=_retail()) == []
    found = ic.check_plan({"measures": ["ead"]}, question="EAD in 2026Q1", envelope=_env(),
                          book=_retail())
    assert RULES(found) == {"period_named"}


def test_aggregation_words_warn_never_refuse():
    found = _check({"measures": ["ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}]},
                   "What is the average ECL for Riyadh?")
    assert [(f.rule, f.severity) for f in found] == [("aggregation_word", "warn")]
    found = _check({"measures": ["avg_ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}]},
                   "What is the total ECL for Riyadh?")
    assert [(f.rule, f.severity) for f in found] == [("aggregation_word", "warn")]
    assert _check({"measures": ["avg_ecl"], "filters": [{"field": "region", "values": ["Riyadh"]}]},
                  "What is the average ECL for Riyadh?") == []


def test_an_invalid_plan_propagates_the_compilers_rejection():
    with pytest.raises(Rejection):
        _check({"measures": ["nonsense"]}, "ECL for Riyadh")


# ------------------------------------------------------------------- modes

BAD = {"measures": ["avg_ecl"]}
ASK = "Average ECL for Region Riyadh in 2026Q1"


def _gate(mode):
    return ic.gate(BAD, question=ASK, envelope=RIYADH, book=_corporate(), run_mode=mode)


def test_enforce_returns_a_rejection_with_the_intent_message_first():
    recorded, rejection = _gate("enforce")
    assert isinstance(rejection, Rejection) and rejection.code == pc.PLAN_INVALID
    assert rejection.message.startswith("Intent check: ") and rejection.field_path == "plan"
    assert {f["rule"] for f in rejection.detail["intent_findings"]} == {"period_named", "entity_scope"}
    assert recorded == []


def test_shadow_records_the_findings_without_refusing():
    recorded, rejection = _gate("shadow")
    assert rejection is None and {f.rule for f in recorded} == {"period_named", "entity_scope"}


def test_off_does_nothing_and_mode_defaults_to_enforce(monkeypatch):
    assert _gate("off") == ([], None)
    monkeypatch.delenv(ic.ENV, raising=False)
    assert ic.mode() == "enforce"
    for value, expected in (("off", "off"), ("SHADOW", "shadow"), ("bogus", "enforce")):
        monkeypatch.setenv(ic.ENV, value)
        assert ic.mode() == expected


def test_orchestrator_compiled_charges_one_plan_retry_and_stashes_warnings(monkeypatch):
    from backend.cockpit_v4.orchestration import Orchestrator

    book = _corporate()
    ledger = types.SimpleNamespace(
        limits=types.SimpleNamespace(plan_retries=3), spent=0)

    def spend():
        ledger.spent += 1
        return ledger.spent

    ledger.spend_plan_retry = spend
    orch = types.SimpleNamespace(
        ledger=ledger, envelope=RIYADH, _plan_book=lambda: book, _asked=lambda: ASK,
        emitter=types.SimpleNamespace(append=lambda *a, **k: None),
        _detail=lambda body: "ref")
    orch._check_intent = types.MethodType(Orchestrator._check_intent, orch)
    monkeypatch.setenv(ic.ENV, "enforce")
    with pytest.raises(Rejection) as caught:
        Orchestrator._compiled(orch, {"plan": BAD})
    assert caught.value.message.startswith("Intent check: ")
    assert ledger.spent == 1 and caught.value.detail["plan_retry"] == 1
    monkeypatch.setenv(ic.ENV, "shadow")
    out = Orchestrator._compiled(orch, {"plan": BAD})
    assert out["steps"] and len(orch._intent_findings) == 2
    monkeypatch.setenv(ic.ENV, "off")
    orch._intent_findings = []
    assert Orchestrator._compiled(orch, {"plan": BAD})["steps"] and orch._intent_findings == []


# ------------------------------------------ rule 4: operation_dropped (SHADOW)

SHARED_TOP10 = ("Which sub-sectors share top 10, that had the largest absolute and relative "
                "increase in Stage 3 EAD between 2025Q2 and 2026Q2?")
TOP5_ABS = "Which 5 sub-sectors had the largest increase in Stage 3 EAD between 2025Q2 and 2026Q2?"


def _movement_book() -> pc.Book:
    rel = "corp_facility_quarter"
    metrics = {"ead": pc.Metric("ead", "Total ead", rel, "SUM(ead_sar_mn)", "SAR million")}
    fields = {"sub_sector": pc.Field("sub_sector", (rel,), ("Hotels", "Marine Works"), False),
              "stage": pc.Field("stage", (rel,), ("1", "2", "3"), True)}
    return pc.Book("corporate", metrics, fields, {rel: "reporting_quarter"}, {rel: CORP})


#: The plan run b8aa21b3 actually sent: valid, covers both quarters, answers nothing asked.
RUN_B8AA = {"measures": ["ead"], "dimensions": ["sub_sector"],
            "filters": [{"field": "stage", "values": ["3"]}],
            "period": {"kind": "explicit", "values": ["2025Q2", "2026Q2"]},
            "compare": "none", "sort": "auto"}


def _ops(plan, question):
    found = ic.check_plan(plan, question=question, envelope=_env(), book=_movement_book())
    return {f.detail["operation"]: f for f in found if f.rule == "operation_dropped"}


def test_the_question_names_its_operations():
    assert ic.requested_operations(SHARED_TOP10) == {
        "change": ["2025Q2", "2026Q2"], "pct_change": True, "rank_change": True,
        "top_n": 10, "intersect": True}
    assert ic.requested_operations(TOP5_ABS) == {
        "change": ["2025Q2", "2026Q2"], "rank_change": True, "top_n": 5}


@pytest.mark.parametrize("question", [
    "How did ECL change by sector between the previous quarter and the latest quarter?",
    "What share of portfolio EAD sits in the top 10 borrowers?",
    "Show ECL by sector for 2025Q2 and 2026Q2",
    "Which sectors have the highest ECL in the latest quarter?",
    "How did ECL move year on year?",
])
def test_questions_that_ask_for_no_dropped_operation_say_nothing(question):
    ops = ic.requested_operations(question)
    assert "change" not in ops and "intersect" not in ops


def test_run_b8aa21b3s_plan_is_flagged_as_unable_to_intersect():
    found = _ops(RUN_B8AA, SHARED_TOP10)
    assert set(found) == {"intersect"} and "steps" in found["intersect"].message


def test_the_plannable_twin_is_flagged_for_each_dropped_operation_with_the_repair():
    found = _ops(RUN_B8AA, TOP5_ABS)
    assert set(found) == {"change", "top_n"}
    assert '"compare": "between"' in found["change"].message
    assert '"limit": 5' in found["top_n"].message
    level_sorted = {**RUN_B8AA, "compare": "between", "limit": 5}
    assert set(_ops(level_sorted, TOP5_ABS)) == {"rank_change"}
    assert '"sort": "change_desc"' in _ops(level_sorted, TOP5_ABS)["rank_change"].message
    assert _ops({**level_sorted, "sort": "change_desc"}, TOP5_ABS) == {}


def test_a_decrease_asks_for_the_ascending_change_sort():
    q = "Which 5 sub-sectors saw the largest decrease in Stage 3 EAD between 2025Q2 and 2026Q2?"
    found = _ops({**RUN_B8AA, "compare": "between", "limit": 5}, q)
    assert '"sort": "change_asc"' in found["rank_change"].message


def test_operation_findings_are_shadow_never_a_refusal_even_under_enforce():
    recorded, rejection = ic.gate(RUN_B8AA, question=SHARED_TOP10, envelope=_env(),
                                  book=_movement_book(), run_mode="enforce")
    assert rejection is None
    assert {f.severity for f in recorded} == {"shadow"}
