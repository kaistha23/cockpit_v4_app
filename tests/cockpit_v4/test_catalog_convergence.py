"""
Does the catalogue loop converge?

Evidence: REAL DATABASE for the catalogue reads, which run against the pinned
release's real catalog, and MODEL MOCK for the runs, which use the scripted
provider. No paid provider call is made here.

The defect this module exists for: a live UAT asked "what is total exposure at
default by sector in the latest quarter?" — a question whose every term the
server had already resolved — and spent three `inspect_catalog` calls, a
truncated generation and its entire 120-second deadline without reaching SQL.
One of those calls returned 60 field definitions across 11 relations and said
931 more remained, which is an invitation to keep going.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import orchestration as orch
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import parse_catalog


def catalog_call(call_id="tu-c", **body):
    payload = {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "query": None, "relation_ids": None, "field_ids": None,
        "detail": None, "reporting_periods": None, "sample_rows": None,
        "cursor": None,
    }
    payload.update(body)
    return tool_call("inspect_catalog", payload, call_id)


def request(**body):
    payload = {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "query": None, "relation_ids": None, "field_ids": None,
        "detail": None, "reporting_periods": None, "sample_rows": None,
        "cursor": None,
    }
    payload.update(body)
    return parse_catalog(payload)


EAD_SQL = ("SELECT sector_name, SUM(ead_reported) AS ead_sar_mn FROM "
           "cockpit_facility_quarter WHERE reporting_quarter = ? "
           "GROUP BY 1 ORDER BY 2 DESC")


def execute_call(sql, parameters, call_id="tu-e"):
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "objective": "o", "subquestions": ["a"],
        "scope": {"reporting_periods": [], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": ["f"],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                   "parameters": dict(parameters), "purpose": "p",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def finish(messages):
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    cell = step["preview"][0]
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="The largest is {{claim.top}}.",
              coverage=[{"subquestion": "a", "status": "answered",
                         "evidence_refs": [{
                             "artifact_id": step["artifact_id"],
                             "row_key": f"sector_name={cell['sector_name']}",
                             "column_id": "ead_sar_mn"}]}],
              numeric_claims=[{
                  "claim_id": "top",
                  "decimal_value": repr(float(cell["ead_sar_mn"])),
                  "unit": "SAR million", "display_precision": 2,
                  "evidence": {"artifact_id": step["artifact_id"],
                               "row_key": f"sector_name={cell['sector_name']}",
                               "column_id": "ead_sar_mn"}}]),
        "tu-f")])


# ---- what generation 1 already sees ------------------------------------

def test_the_first_generation_already_holds_the_schema_facts(drive):
    """Relation, column, type, unit, grain, period column and join key."""
    script = [ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent(), disposition="answer", narrative="hi"))])]
    _outcome, provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        script)
    sent = provider.first_input_text()
    for fact in ("ead_reported", "sector_name", "reporting_quarter",
                 "one row per facility per reporting quarter",
                 "canonical_measures", "2026Q2"):
        assert fact in sent, fact


def test_the_packet_carries_type_and_unit_not_just_a_field_name(runtime):
    packet = {e["field_id"]: e for e in sem.field_packet(runtime.catalog)}
    ead = packet["cockpit_facility_quarter.ead_reported"]
    assert ead["dtype"]
    assert ead["unit"]
    assert ead["aggregation"] == "additive"
    assert ead["period_field"] == "reporting_quarter"
    assert ead["key"] == "facility_id"
    assert ead["currency"] and ead["amount_scale"]


def test_the_packet_states_facts_and_never_a_method(runtime):
    blob = json.dumps(sem.field_packet(runtime.catalog)).lower()
    for method in ("select ", "group by", "sum(", "order by", "you should",
                   "the answer"):
        assert method not in blob, method


# ---- A, B, C: the three UAT questions reach execute_analysis -----------

@pytest.mark.parametrize("question", [
    "What is total exposure at default by sector in the latest quarter?",
    "Show me ECL by sector in the latest quarter and rank the top five "
    "sectors.",
    "Which sectors saw the largest increase in Stage 2 exposure over the "
    "latest year?",
])
def test_a_canonical_question_can_reach_sql_without_a_catalogue_call(
        question, drive, store_db, release_id):
    """No catalogue call is REQUIRED: the packet already carries the facts."""
    quarter = oracles.latest_quarter(release_id)
    script = [ScriptedResult(tool_calls=[execute_call(EAD_SQL,
                                                      {"1": quarter})]),
              finish]
    outcome, provider, record = drive(question, script)
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "one action, one finalization"
    budget = store_db.get_run(record.run_id).budget
    assert budget["catalog_calls"][0] == 0


def test_one_targeted_catalogue_call_still_converges(drive, store_db,
                                                     release_id):
    quarter = oracles.latest_quarter(release_id)

    def after_catalog(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        assert body["coverage_complete_for_request"] is True
        assert body["returned"]["field_ids"]
        return ScriptedResult(tool_calls=[execute_call(EAD_SQL,
                                                       {"1": quarter})])

    script = [
        ScriptedResult(tool_calls=[catalog_call(
            field_ids=["cockpit_facility_quarter.ead_reported",
                       "cockpit_facility_quarter.sector_name"],
            detail=["fields"])]),
        after_catalog, finish]
    outcome, _provider, record = drive("EAD by sector", script)
    assert outcome.state == st.COMPLETED, outcome.message
    assert store_db.get_run(record.run_id).budget["catalog_calls"][0] == 1


# ---- D: two DISTINCT metadata calls are still allowed ------------------

def test_two_distinct_catalogue_calls_are_not_blocked(catalog_service):
    """The guard is on repeating, not on exploring."""
    first = catalog_service.inspect(request(
        field_ids=["cockpit_facility_quarter.ead_reported"],
        detail=["fields"]))
    second = catalog_service.inspect(request(
        field_ids=["cockpit_covenant_quarter.headroom_value"],
        detail=["fields"]))
    third = catalog_service.inspect(request(detail=["relationships"],
                                            relation_ids=[
                                                "cockpit_facility_quarter"]))
    for result in (first, second, third):
        assert result["added_new_information"] is True
        assert result["status"] == "ok"
    assert third["known_so_far"]["field_count"] == 2


# ---- E: a repeat is marked, and bounded --------------------------------

def test_a_repeated_request_is_marked_as_adding_nothing(catalog_service):
    fields = ["cockpit_facility_quarter.ead_reported",
              "cockpit_facility_quarter.sector_name"]
    first = catalog_service.inspect(request(field_ids=fields,
                                            detail=["fields"]))
    assert first["added_new_information"] is True
    assert first["already_known"] == []

    second = catalog_service.inspect(request(field_ids=fields,
                                             detail=["fields"]))
    assert second["added_new_information"] is False
    assert "already available" in second["no_new_information"]
    assert sorted(second["already_known"]) == sorted(fields)


def test_a_catalogue_loop_ends_before_the_deadline_does(drive, store_db):
    """Three barren calls end the run; they do not consume 120 seconds."""
    fields = ["cockpit_facility_quarter.ead_reported"]
    script = [ScriptedResult(tool_calls=[catalog_call(
        call_id=f"tu-{i}", field_ids=fields, detail=["fields"])])
        for i in range(6)]
    outcome, provider, record = drive("EAD by sector", script)

    assert outcome.state == st.FAILED
    assert outcome.error_code == st.NO_PROGRESS
    assert "added no new information" in outcome.message
    assert len(provider.sent) <= 4, (
        f"the loop must stop early, not run out of turns; it made "
        f"{len(provider.sent)} generations")
    budget = store_db.get_run(record.run_id).budget
    assert budget["elapsed_seconds"] < 30


def test_the_analyst_is_warned_before_the_run_is_ended(drive):
    fields = ["cockpit_facility_quarter.ead_reported"]
    seen: list[dict] = []

    def watch(messages):
        seen.append(json.loads(messages[-1]["content"][0]["content"]))
        return ScriptedResult(tool_calls=[catalog_call(
            call_id=f"tu-w{len(seen)}", field_ids=fields, detail=["fields"])])

    script = [ScriptedResult(tool_calls=[catalog_call(
        field_ids=fields, detail=["fields"])])] + [watch] * 4
    drive("EAD by sector", script)
    warned = [body for body in seen if "no_progress" in body]
    assert warned, "the analyst is told before the run is ended"
    assert "ends this run" in warned[0]["no_progress"]["message"]


# ---- F: an empty request is not a request for everything ---------------

def test_an_unscoped_request_returns_a_compact_refusal(catalog_service):
    result = catalog_service.inspect(request(detail=["fields"]))
    assert result["status"] == "needs_scope"
    assert result.get("fields") is None
    assert result.get("discovery") is None
    assert "Specify relation(s), field(s), or a query" in result["reason"]
    assert len(json.dumps(result)) < 2_000, "a refusal must be small"


def test_an_unscoped_request_is_never_sixty_fields(catalog_service, runtime):
    relations = list(runtime.catalog.relations())
    for body in ({"detail": ["fields"]},
                 {"detail": ["discovery", "fields"],
                  "relation_ids": relations},
                 {"detail": ["fields"], "relation_ids": relations}):
        result = catalog_service.inspect(request(**body))
        assert len(result.get("fields") or []) == 0, body
        assert result["status"] == "needs_scope", body


# ---- G: sample_rows cannot be valid-by-schema and refused --------------

def test_sample_rows_without_samples_in_detail_is_accepted(catalog_service):
    """The live message "sample_rows requires 'samples' in detail" is gone."""
    parsed = request(detail=["fields"], sample_rows=3,
                     field_ids=["cockpit_facility_quarter.ead_reported"])
    assert "samples" in parsed.detail
    assert parsed.sample_rows == 3


def test_samples_in_detail_without_a_row_count_is_accepted():
    parsed = request(detail=["samples"],
                     relation_ids=["cockpit_facility_quarter"])
    assert parsed.sample_rows > 0


def test_a_sample_reads_only_the_requested_columns(catalog_service, session):
    catalog_service.session = session
    result = catalog_service.inspect(request(
        detail=["samples"], sample_rows=2,
        field_ids=["cockpit_facility_quarter.ead_reported",
                   "cockpit_facility_quarter.sector_name"]))
    sample = result["samples"]["cockpit_facility_quarter"]
    assert sample["scope"]["columns"] == ["ead_reported", "sector_name"]
    assert sample["scope"]["row_limit"] == 2
    assert len(sample["rows"]) <= 2
    assert set(sample["columns"]) == {"ead_reported", "sector_name"}


def test_an_unscoped_sample_is_refused_rather_than_selecting_star(
        catalog_service, session):
    catalog_service.session = session
    result = catalog_service.inspect(request(detail=["samples"],
                                             sample_rows=2))
    assert result["samples"]["status"] == "needs_scope"


# ---- the guard's own bounds are documented -----------------------------

def test_the_no_progress_bounds_are_on_repeating_not_on_asking():
    assert orch.BARREN_CATALOG_WARN == 2
    assert orch.BARREN_CATALOG_TERMINAL == 3
    from backend.cockpit_v4.config import STANDARD_LIMITS

    assert STANDARD_LIMITS.catalog_calls == 4, (
        "distinct, useful catalogue calls stay available; the fix is not a "
        "lower global limit")


def test_discovery_finds_columns_not_only_relations(catalog_service):
    """A search for a column's words returns that column, with its id.

    Discovery used to read relation names and grains only, so a search for
    "sector" returned nothing a query could use and the analyst guessed a
    column name instead -- the invented columns that failed to bind live.
    """
    result = catalog_service.inspect(request(query="sector", detail=["discovery"]))
    fields = [f["field_id"] for f in result.get("matching_fields", [])]
    assert any(f.endswith(".sector_name") for f in fields), fields
    assert all("." in f for f in fields)
