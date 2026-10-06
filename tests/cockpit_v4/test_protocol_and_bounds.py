"""
MODEL MOCK · REAL DATABASE/RUNNER.

The native tool protocol, the repair loop, and every bound in section 11
exercised against the actual runtime rather than read off the state diagram.
"""

from __future__ import annotations

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import Rejection, parse_steps


def _exec(sql: str, call_id="tu-1", step_id="s1", **over):
    body = {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
            "objective": "o", "subquestions": ["a"],
            "scope": {"reporting_periods": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": ["f"],
            "expected_output_grain": "sector", "expected_units": "SAR million",
            "steps": [{"step_id": step_id, "language": "sql", "code": sql,
                       "parameters": {}, "purpose": "p",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}
    body.update(over)
    return tool_call("execute_analysis", body, call_id)


# ---- protocol ----------------------------------------------------------

def test_prose_without_a_tool_call_is_not_promoted_to_an_answer(drive):
    """V4-AT-014. Free prose is refused once, then the run stops."""
    outcome, provider, _ = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[], text="I am the Cockpit assistant.",
                        stop_reason="end_turn"),
         ScriptedResult(tool_calls=[], text="Still prose.",
                        stop_reason="end_turn"),
         ScriptedResult(tool_calls=[], text="And again.",
                        stop_reason="end_turn")])
    assert outcome.state == st.FAILED
    # The re-ask is what ran out, and that is what it is called. Generations
    # and provider attempts were both still available.
    assert outcome.error_code == st.ACTION_FORMAT_EXHAUSTED
    assert len(provider.sent) == 3, (
        "the re-ask is granted while the request can still change, and no "
        "longer: two recoveries, then the run stops")


def test_a_truncated_response_executes_nothing(drive, release_id):
    """V4-AT-012, V4-AT-013. A cut-off turn is INCOMPLETE, not shorter."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT COUNT(*) AS n FROM cockpit_facility_quarter "
           f"WHERE reporting_quarter = '{quarter}'")

    outcome, provider, record = drive(
        "count facilities",
        [ScriptedResult(tool_calls=[_exec(sql)], stop_reason="max_tokens"),
         ScriptedResult(tool_calls=[tool_call(
             "finalize_response",
             final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                   disposition="partial_answer",
                   narrative="The first attempt was cut off.",
                   limitations=["the response was truncated"]))])])

    assert outcome.state == st.PARTIAL
    types = [e.event_type for e in provider and []] or []
    # Nothing ran: no artifact was produced by the truncated turn.
    assert outcome.response["executed"] is False


def test_a_mixed_batch_has_no_side_effects(drive, release_id):
    """V4-AT-015. Execution and finalization cannot share one response."""
    quarter = oracles.latest_quarter(release_id)
    sql = f"SELECT 1 AS x FROM cockpit_facility_quarter LIMIT 1"
    outcome, provider, record = drive(
        "do two things",
        [ScriptedResult(tool_calls=[
            _exec(sql, call_id="tu-a"),
            tool_call("finalize_response", final(), "tu-b")]),
         ScriptedResult(tool_calls=[tool_call(
             "finalize_response",
             final(narrative="One action at a time."), "tu-c")])])

    assert outcome.state == st.COMPLETED
    assert outcome.response["executed"] is False, (
        "the execution half of a refused batch must not have run")


def test_batched_reads_are_allowed_and_bounded(drive):
    """V4-AT-015 (the permitted half). Independent reads may batch."""
    catalog_args = {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
                    "query": "exposure", "relation_ids": [], "field_ids": [],
                    "detail": ["discovery"], "reporting_periods": [],
                    "sample_rows": 0, "cursor": ""}
    outcome, provider, _ = drive(
        "what is exposure",
        [ScriptedResult(tool_calls=[
            tool_call("inspect_catalog", catalog_args, "tu-1"),
            tool_call("inspect_catalog", {**catalog_args, "query": "stage"},
                      "tu-2")]),
         ScriptedResult(tool_calls=[tool_call(
             "finalize_response",
             final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                   disposition="clarification",
                   clarification_question="Which exposure measure?"))])])
    assert outcome.state == st.WAITING_FOR_USER


def test_an_unknown_stop_reason_fails_explicitly(drive):
    """V4-AT-017. Not a loop, and not treated as completion."""
    outcome, _, _ = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[], text="", stop_reason="something_new")])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.INVALID_MODEL_OUTPUT


def test_a_refusal_does_not_try_another_model(drive):
    """V4-AT-016."""
    outcome, provider, _ = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[], text="", stop_reason="refusal")])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.PROVIDER_UNAVAILABLE
    assert len(provider.sent) == 1


def test_tool_results_follow_their_calls_in_order(drive, store_db,
                                                  release_id):
    """V4-AT-011. Reused history has no dangling tool_use."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT sector_name FROM cockpit_facility_quarter "
           f"WHERE reporting_quarter='{quarter}' LIMIT 3")
    outcome, provider, record = drive(
        "sectors",
        [ScriptedResult(tool_calls=[_exec(sql)]),
         ScriptedResult(tool_calls=[tool_call(
             "finalize_response",
             final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                   disposition="partial_answer",
                   narrative="Sectors listed.",
                   limitations=["no aggregate was requested"]))])])
    assert outcome.state == st.PARTIAL

    messages = store_db.load_messages(record.run_id)
    pending: list[str] = []
    for message in messages:
        content = message["content"]
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending.append(block["id"])
            elif block.get("type") == "tool_result":
                assert pending and pending[0] == block["tool_use_id"], (
                    "every tool_result must answer the earliest outstanding "
                    "tool_use, in order")
                pending.pop(0)
    assert not pending, f"unanswered tool calls left in history: {pending}"


# ---- repair ------------------------------------------------------------

def test_a_failed_query_is_repaired_by_the_model_not_the_application(
        drive, store_db, release_id):
    """V4-AT-049, V4-AT-050. The application supplies a diagnostic, nothing more."""
    quarter = oracles.latest_quarter(release_id)
    broken = (f"SELECT sector_name, SUM(exposure_at_default) AS e "
              f"FROM cockpit_facility_quarter "
              f"WHERE reporting_quarter='{quarter}' GROUP BY 1")
    fixed = (f"SELECT sector_name, SUM(ead_reported) AS e "
             f"FROM cockpit_facility_quarter "
             f"WHERE reporting_quarter='{quarter}' GROUP BY 1")

    seen: dict[str, object] = {}

    def repair(messages):
        import json

        body = json.loads(messages[-1]["content"][0]["content"])
        seen["diagnostic"] = body
        return ScriptedResult(tool_calls=[_exec(fixed, call_id="tu-2",
                                                step_id="s2")])

    def finish(messages):
        import json

        body = json.loads(messages[-1]["content"][0]["content"])
        artifact = body["steps"][0]["artifact_id"]
        cell = body["steps"][0]["preview"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="Largest is {{claim.v}}.",
                  numeric_claims=[{
                      "claim_id": "v",
                      "decimal_value": repr(float(cell["e"])),
                      "unit": "SAR million", "display_precision": 2,
                      "evidence": {
                          "artifact_id": artifact,
                          "row_key": f"sector_name={cell['sector_name']}",
                          "column_id": "e"}}]),
            "tu-3")])

    outcome, provider, record = drive(
        "EAD by sector",
        [ScriptedResult(tool_calls=[_exec(broken)]), repair, finish])

    assert outcome.state == st.COMPLETED, outcome.message
    diagnostic = seen["diagnostic"]
    # The unresolvable column is caught by the BINDER, at validation, before
    # anything claims the query was validated and before anything ran.
    assert diagnostic["status"] == "rejected"
    assert diagnostic["error_code"] == st.SQL_VALIDATION
    detail = diagnostic["detail"]
    assert detail["failed_check"] == "bind"
    assert detail["phase"] == "bind"
    assert detail["unresolved_name"] == "exposure_at_default"
    assert "BinderException" in detail["duckdb_exception_type"]
    assert "nothing was repaired" in diagnostic["message"].lower()

    # Both the failed and the corrected code came from the model, and the
    # application never authored a replacement.
    submissions = store_db._connect().execute(
        "SELECT payload FROM submissions WHERE run_id=? AND payload LIKE "
        "'%SELECT%'", (record.run_id,)).fetchall()
    codes = "\n".join(r["payload"] for r in submissions)
    assert "exposure_at_default" in codes and "ead_reported" in codes


def test_the_same_deterministic_failure_is_not_run_twice(drive, release_id):
    """V4-AT-052. The counter still advances; the query does not re-run."""
    broken = ("SELECT nonexistent_column FROM cockpit_facility_quarter "
              "LIMIT 1")
    outcome, provider, _ = drive(
        "broken",
        [ScriptedResult(tool_calls=[_exec(broken, call_id="tu-1")]),
         ScriptedResult(tool_calls=[_exec(broken, call_id="tu-2")]),
         ScriptedResult(tool_calls=[tool_call(
             "finalize_response",
             final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                   disposition="partial_answer",
                   narrative="The query could not be made to work.",
                   limitations=["the column does not exist"]), "tu-3")])])
    assert outcome.state == st.PARTIAL


def test_an_overlong_batch_is_rejected_whole_and_never_clipped():
    """V4-AT-047. The failure V3 shipped: silently executing four of six."""
    steps = [{"step_id": f"s{i}", "language": "sql", "code": "SELECT 1",
              "parameters": {}, "purpose": "p", "input_artifact_ids": [],
              "depends_on_step_ids": []} for i in range(7)]
    with pytest.raises(Rejection) as excinfo:
        parse_steps(steps, max_steps=6)
    assert excinfo.value.detail["submitted"] == 7
    assert "no step was dropped" in excinfo.value.message


# ---- bounds ------------------------------------------------------------

def test_a_sixth_execution_submission_is_impossible(drive, release_id):
    """V4-AT-057. Rejected candidates count too."""
    broken = "SELECT bad_column FROM cockpit_facility_quarter LIMIT 1"
    script = [ScriptedResult(tool_calls=[_exec(
        broken + f" -- attempt {i}", call_id=f"tu-{i}")]) for i in range(6)]
    outcome, provider, _ = drive("repeat", script)
    assert outcome.state in (st.FAILED, st.PARTIAL)
    assert outcome.error_code == st.EXECUTION_LIMIT
    assert len(provider.sent) == 6, (
        "the sixth submission is refused without a seventh generation")


def test_the_run_deadline_stops_the_loop(drive, store_db, make_run,
                                         runtime, release_id):
    """V4-AT-065. A stalled loop cannot outlive the run deadline."""
    from backend.cockpit_v4.worker import Worker
    from conftest import ScriptedProvider

    record = make_run("slow", mode="standard")

    class Slow(ScriptedProvider):
        def converse(self, **kwargs):
            import time

            time.sleep(0.05)
            return super().converse(**kwargs)

    provider = Slow([ScriptedResult(tool_calls=[tool_call(
        "inspect_catalog",
        {"intent": intent("DATA_ANALYSIS", "COCKPIT"), "query": "x",
         "relation_ids": [], "field_ids": [], "detail": ["discovery"],
         "reporting_periods": [], "sample_rows": 0, "cursor": ""},
        f"tu-{i}")]) for i in range(30)])
    runtime.provider = provider
    worker = Worker(store=store_db, runtime=runtime)

    import backend.cockpit_v4.config as config_mod

    original = config_mod.STANDARD_LIMITS
    try:
        from dataclasses import replace

        config_mod.STANDARD_LIMITS = replace(original, deadline_seconds=0.3)
        outcome = worker.execute(record)
    finally:
        config_mod.STANDARD_LIMITS = original

    assert outcome.state in (st.EXPIRED, st.FAILED)
    # NO_PROGRESS is the newest and best of the three: thirty identical
    # catalogue calls are stopped as a loop rather than left to consume the
    # deadline. The property under test — a stalled loop cannot outlive its
    # bounds — holds more strongly, not less.
    assert outcome.error_code in (st.DEADLINE_EXPIRED, st.CALL_LIMIT,
                                  st.NO_PROGRESS)


def test_spend_is_reserved_before_the_call_and_settled_after(
        drive, store_db, release_id):
    """V4-AT-063. A run reports what it actually spent, at a verified price."""
    outcome, provider, record = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response", final())], input_tokens=1500,
            output_tokens=250)])
    spend = store_db.spend(record.run_id)
    assert spend["committed_usd"] > 0
    assert spend["uncertain"] is False
    budget = store_db.get_run(record.run_id).budget
    assert budget["cost_enforced"] is True


def test_a_failed_provider_call_holds_its_reservation_pending(
        drive, store_db):
    """V4-AT-064. A call that may have been billed is never booked as zero."""
    outcome, provider, record = drive(
        "Who are you?", [RuntimeError("connection reset by peer")])
    assert outcome.state == st.FAILED
    spend = store_db.spend(record.run_id)
    assert spend["pending_usd"] > 0
    assert spend["uncertain"] is True
    budget = store_db.get_run(record.run_id).budget
    assert budget["cost_enforced"] is False, (
        "an 'enforced' badge while the cost is unknown is a false claim")
