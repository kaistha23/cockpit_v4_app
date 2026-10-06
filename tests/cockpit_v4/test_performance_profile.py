"""
What the pipeline costs when the model is not the thing being timed.

MODEL MOCK · REAL DATABASE/RUNNER. Every millisecond here is CreditProbe's
own: intake, context assembly, validation, the bind proof, DuckDB, the
artifact write, the finalizer and the event stream. The analyst turn returns
instantly, so these numbers are the FLOOR the product adds around whatever
the model takes -- which is the only part of latency a change to this
codebase can move.

They are NOT end-to-end user latency, and the artifact says so. A live run
adds the provider's time on top of every figure here.
"""

from __future__ import annotations

import json
import statistics
import time

import oracles
import pytest
import uat_question_bank as bank
import uat_sql as layer_b
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st
from test_overnight_benchmark import EVIDENCE, _execute_call, _finalizer

REPEATS = 5
SAMPLES: dict[str, list[float]] = {}


def _time(label: str, work) -> object:
    started = time.perf_counter()
    result = work()
    SAMPLES.setdefault(label, []).append(
        (time.perf_counter() - started) * 1000)
    return result


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "min_ms": round(ordered[0], 1),
        "median_ms": round(statistics.median(ordered), 1),
        "p90_ms": round(ordered[min(len(ordered) - 1,
                                    int(0.9 * len(ordered)))], 1),
        "max_ms": round(ordered[-1], 1),
    }


def test_product_help_costs_one_generation_and_almost_no_pipeline(drive):
    for _ in range(REPEATS):
        outcome, provider, _ = _time("product_help_end_to_end", lambda: drive(
            "Who are you?",
            [ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("PRODUCT_HELP", "COCKPIT")))])]))
        assert outcome.state == st.COMPLETED
        assert len(provider.sent) == 1


def test_an_analytical_run_end_to_end(drive, release_id):
    period = bank.periods(release_id)
    for _ in range(REPEATS):
        outcome, provider, _ = _time("analysis_end_to_end", lambda: drive(
            bank.BY_ID["Q01"]["text"],
            [ScriptedResult(tool_calls=[_execute_call("Q01", period)]),
             _finalizer("Q01", period)]))
        assert outcome.state == st.COMPLETED, outcome.message
        assert len(provider.sent) == 2


def test_the_heaviest_question_in_the_bank(drive, release_id):
    """Q20: four measures, two quarters, a join to the rating relation."""
    period = bank.periods(release_id)
    for _ in range(REPEATS):
        outcome, _, _ = _time("heaviest_analysis_end_to_end", lambda: drive(
            bank.BY_ID["Q20"]["text"],
            [ScriptedResult(tool_calls=[_execute_call("Q20", period)]),
             _finalizer("Q20", period)]))
        assert outcome.state == st.COMPLETED, outcome.message


def test_the_bind_proof_is_cheap_enough_to_run_before_announcing(session,
                                                                 release_id):
    """EXPLAIN executes nothing; it must not cost like a query."""
    from backend.cockpit_v4.sqlbind import prove_bindable

    period = bank.periods(release_id)
    statement = layer_b.sql_for("Q20")
    parameters = layer_b.parameters_for("Q20", period)
    for _ in range(REPEATS * 4):
        _time("bind_proof", lambda: prove_bindable(
            statement, session, parameters=parameters))
    median = statistics.median(SAMPLES["bind_proof"])
    assert median < 250, (
        f"the bind proof took {median:.0f}ms, which is no longer free enough "
        f"to run before announcing validation")


def test_the_attention_dashboards_are_computed_without_a_model(session,
                                                               runtime,
                                                               release_id):
    from backend.cockpit_v4 import attention

    catalog = runtime.catalog
    currency = " ".join(
        part for part in (getattr(catalog, "reporting_currency", ""),
                          getattr(catalog, "amount_scale", "")) if part)
    calendar = getattr(catalog, "calendar", None)
    quarters = [str(q) for q in (getattr(calendar, "populated", ()) or ())]
    for _ in range(REPEATS):
        # `refresh` on every pass: the cache is the product's answer to this
        # cost, and timing a cache hit would measure a dictionary lookup.
        feed = _time("attention_feed", lambda: attention.cached(
            session=session, release_id=release_id, tenant_id="demo-tenant",
            quarters=quarters, currency=currency, refresh=True))
        assert (feed["segments_requiring_attention"]
                or feed["ecl_highlights"]), (
            "the feed should not be empty on this release")
    median = statistics.median(SAMPLES["attention_feed"])
    assert median < 5_000, (
        f"the landing dashboards took {median:.0f}ms, which a reader waits "
        f"for before seeing anything")


def test_zz_write_the_performance_profile():
    if len(SAMPLES) < 5:
        pytest.skip("the profile is written by a full run of this module")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "performance.json").write_text(json.dumps({
        "evidence_class": "MODEL MOCK · REAL DATABASE/RUNNER",
        "paid_provider_calls": 0,
        "what_is_measured": (
            "CreditProbe's own time around the analyst turn: intake, context, "
            "validation, the bind proof, DuckDB, the artifact write, the "
            "finalizer and the event stream. The scripted analyst returns "
            "immediately."),
        "what_is_not_measured": (
            "provider latency. A live run adds the model's time on top of "
            "every figure here, and these are therefore a floor, not an "
            "end-to-end user-facing latency."),
        "measurements": {label: _stats(values)
                         for label, values in sorted(SAMPLES.items())},
    }, indent=2) + "\n")
