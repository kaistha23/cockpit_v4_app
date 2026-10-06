"""MODEL MOCK · REAL DATABASE/RUNNER · BOTH BOOKS.

§44. What the dual-domain Cockpit costs, per book.

Every millisecond here is CreditProbe's own. The analyst is scripted and
returns immediately, so these are the FLOOR the product adds around whatever
a live model takes -- and the only part of latency this codebase can move.

Two books means two of everything: two catalogues, two materialized sessions,
two dashboards, two ECL panels. The figure that matters most is the one a
reader feels when they switch: opening the second book's session for the
first time is the cost that used to be hidden because there was only ever one.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import time

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import (execute_call,  # noqa: F401
                                   make_domain_run)

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import ecl as ecl_mod
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

EVIDENCE = (pathlib.Path(__file__).resolve().parents[2] / "docs"
            / "cockpit_v4" / "evidence" / "dual_domain_performance.json")

REPEATS = 5
SAMPLES: dict[str, list[float]] = {}

#: What a reader is entitled to wait for, in milliseconds. These are budgets
#: for CreditProbe's own work, not for a live answer.
BUDGETS = {
    "session_cold_open": 6000.0,
    "session_warm_open": 50.0,
    "dashboard_cold": 3000.0,
    "dashboard_cached": 25.0,
    "ecl_panel_cold": 3000.0,
    "ecl_panel_cached": 25.0,
    "schema_browse": 250.0,
    "analysis_end_to_end": 4000.0,
    "domain_switch": 3000.0,
}


def _time(label: str, work):
    started = time.perf_counter()
    result = work()
    SAMPLES.setdefault(label, []).append(
        (time.perf_counter() - started) * 1000)
    return result


def _stat(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1)))))
    return {"samples": len(ordered), "min_ms": round(ordered[0], 1),
            "median_ms": round(statistics.median(ordered), 1),
            "p90_ms": round(ordered[index], 1),
            "max_ms": round(ordered[-1], 1)}


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    yield
    if not SAMPLES:
        return
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps({
        "evidence_class": "MODEL MOCK · REAL DATABASE/RUNNER · BOTH BOOKS",
        "paid_provider_calls": 0,
        "what_is_measured": (
            "CreditProbe's own time, per book: opening a domain session cold "
            "and warm, computing a dashboard and an ECL panel cold and "
            "cached, browsing a schema, and driving one analytical run end "
            "to end with a scripted analyst."),
        "what_is_not_measured": (
            "provider latency, and browser rendering. A live answer adds the "
            "model's time on top of every figure here."),
        "budgets_ms": BUDGETS,
        "measurements": {k: _stat(v) for k, v in sorted(SAMPLES.items())},
    }, indent=2) + "\n", encoding="utf-8")


def _within(label: str, budget_key: str = "") -> None:
    stat = _stat(SAMPLES[label])
    budget = BUDGETS[budget_key or label]
    assert stat["p90_ms"] <= budget, (
        f"{label} p90 is {stat['p90_ms']}ms against a {budget}ms budget: "
        f"{SAMPLES[label]}")


# ---- opening a book ----------------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_opening_a_book_cold_and_then_warm(domain_id):
    """The cost a reader pays the first time they switch, and afterwards."""
    for _ in range(3):
        arun.reset()
        cat.reset_sessions()
        _time(f"session_cold_open:{domain_id}",
              lambda: arun.for_domain(domain_id))
        for _repeat in range(3):
            _time(f"session_warm_open:{domain_id}",
                  lambda: arun.for_domain(domain_id))
    _within(f"session_cold_open:{domain_id}", "session_cold_open")
    _within(f"session_warm_open:{domain_id}", "session_warm_open")


def test_both_books_open_together_without_paying_twice_for_either():
    """The second book must not slow the first one down."""
    arun.reset()
    cat.reset_sessions()
    first = _time("domain_switch:first", lambda: arun.for_domain(dom.CORPORATE))
    second = _time("domain_switch:second", lambda: arun.for_domain(dom.RETAIL))
    back = _time("domain_switch:back", lambda: arun.for_domain(dom.CORPORATE))
    assert first is back, "returning to the first book must not rebuild it"
    assert first is not second
    assert SAMPLES["domain_switch:back"][-1] < 50.0, (
        "switching back to an open book must be immediate")
    for label in ("domain_switch:first", "domain_switch:second"):
        assert _stat(SAMPLES[label])["max_ms"] <= BUDGETS["domain_switch"]


# ---- the server-computed panels ----------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_dashboard_costs_what_it_costs(domain_id):
    book = arun.for_domain(domain_id)
    scope = resolver.scope_for(domain_id)
    for _ in range(REPEATS):
        att.clear_cache()
        feed = _time(f"dashboard_cold:{domain_id}",
                     lambda: att.cached(session=book.session, scope=scope,
                                        tenant_id=lake.DEFAULT_TENANT))
        assert feed["model_calls"] == 0
        _time(f"dashboard_cached:{domain_id}",
              lambda: att.cached(session=book.session, scope=scope,
                                 tenant_id=lake.DEFAULT_TENANT))
    _within(f"dashboard_cold:{domain_id}", "dashboard_cold")
    _within(f"dashboard_cached:{domain_id}", "dashboard_cached")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_ecl_panel_costs_what_it_costs(domain_id):
    book = arun.for_domain(domain_id)
    scope = resolver.scope_for(domain_id)
    for _ in range(REPEATS):
        ecl_mod.clear_cache()
        body = _time(f"ecl_panel_cold:{domain_id}",
                     lambda: ecl_mod.cached(session=book.session, scope=scope,
                                            tenant_id=lake.DEFAULT_TENANT))
        assert body["decomposition"]["reconciles"] is True
        _time(f"ecl_panel_cached:{domain_id}",
              lambda: ecl_mod.cached(session=book.session, scope=scope,
                                     tenant_id=lake.DEFAULT_TENANT))
    _within(f"ecl_panel_cold:{domain_id}", "ecl_panel_cold")
    _within(f"ecl_panel_cached:{domain_id}", "ecl_panel_cached")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_browsing_the_schema_is_immediate(domain_id):
    book = arun.for_domain(domain_id)
    for _ in range(REPEATS):
        outline = _time(f"schema_browse:{domain_id}",
                        lambda: book.catalog.outline())
        assert outline["relations"]
    _within(f"schema_browse:{domain_id}", "schema_browse")


# ---- one whole run -----------------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_one_analytical_run_end_to_end(store_db, runtime, domain_id):
    """Everything the product does around a scripted analyst, per book."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    month = oracle.latest_month(domain_id)
    relation, dimension = (
        ("corp_facility_quarter", "sector") if domain_id == dom.CORPORATE
        else ("retail_account_month", "product"))
    sql = (f"SELECT {dimension}, SUM(ead_sar_mn) AS ead_sar_mn "
           f"FROM {relation} "
           f"WHERE {oracle.period_column(domain_id)} = '{month}' "
           f"GROUP BY {dimension} ORDER BY ead_sar_mn DESC")

    def answer(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        row = step["preview"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="The largest is {{claim.v}}.",
                  coverage=[{"subquestion": "EAD", "status": "answered",
                             "evidence_refs": [
                                 {"artifact_id": step["artifact_id"],
                                  "row_key": "r0",
                                  "column_id": "ead_sar_mn"}]}],
                  numeric_claims=[{
                      "claim_id": "v",
                      "decimal_value": str(row["ead_sar_mn"]),
                      "unit": "SAR million", "display_precision": 0,
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": "r0",
                                   "column_id": "ead_sar_mn"}}]))])

    for _ in range(REPEATS):
        record = make_domain_run(store_db, domain_id, "EAD please")
        runtime.provider = ScriptedProvider([
            ScriptedResult(tool_calls=[execute_call(
                sql, purpose="EAD", grain=dimension, units="SAR million",
                subquestions=["EAD"],
                fields=[f"{relation}.ead_sar_mn"], month=month)]),
            answer])
        outcome = _time(
            f"analysis_end_to_end:{domain_id}",
            lambda: Worker(store=store_db, runtime=runtime).execute(record))
        assert outcome.state == st.COMPLETED, outcome.message
    _within(f"analysis_end_to_end:{domain_id}", "analysis_end_to_end")
