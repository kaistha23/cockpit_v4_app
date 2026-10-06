"""
MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

Selective metadata, the answer contract's optional parts, thread continuity
and memory behaviour.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.config import STANDARD_LIMITS
from backend.cockpit_v4.contracts import parse_catalog, parse_final


@pytest.fixture
def catalog_service(runtime, release_id):
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_v4.catalog_tool import CatalogService
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    return CatalogService(catalog=runtime.catalog, scope=scope)


def request(**over):
    body = {"intent": intent("DATA_ANALYSIS", "COCKPIT"), "query": "",
            "relation_ids": [], "field_ids": [], "detail": ["discovery"],
            "reporting_periods": [], "sample_rows": 0, "cursor": ""}
    body.update(over)
    return parse_catalog(body)


# ---- catalog -----------------------------------------------------------

def test_a_requested_field_comes_back_complete(catalog_service):
    """V4-AT-032. Selective does not mean partial."""
    result = catalog_service.inspect(request(
        detail=["fields"],
        field_ids=["cockpit_facility_quarter.ead_reported",
                   "cockpit_facility_quarter.ifrs9_stage"]))
    fields = {f["field_id"]: f for f in result["fields"]}
    assert set(fields) == {"cockpit_facility_quarter.ead_reported",
                           "cockpit_facility_quarter.ifrs9_stage"}
    for field in fields.values():
        for key in ("definition", "dtype", "aggregation", "availability"):
            assert field.get(key), f"{field['field_id']} is missing {key}"
        assert len(field["definition"]) > 20, (
            "a truncated definition is worse than an absent one: the analyst "
            "cannot tell it is missing something")
    assert result["metadata_receipt_id"].startswith("mr-")


def test_an_unknown_field_gets_alternatives_not_a_substitution(
        catalog_service):
    """V4-AT-048. Nothing close-looking is silently offered as the answer."""
    result = catalog_service.inspect(request(
        detail=["fields"], field_ids=["cockpit_facility_quarter.exposure"]))
    assert result["fields"] == []
    unresolved = result["unresolved_fields"]
    assert "cockpit_facility_quarter.exposure" in unresolved["requested"]
    assert "No similar field was substituted" in unresolved["note"]


def test_an_empty_search_is_an_empty_search(catalog_service):
    """V4-AT-009. Not an invitation to return the whole catalogue."""
    result = catalog_service.inspect(request(query="zzz_nothing_matches_this"))
    assert result["discovery"] == []
    assert "No authorized relation matched" in result["discovery_note"]
    # The authorized list is offered so the analyst can ask again precisely.
    assert result["authorized_relations"]


def test_a_relation_outside_the_domain_is_named_not_invented(catalog_service):
    """V4-AT-034."""
    result = catalog_service.inspect(request(
        detail=["fields"], relation_ids=["ews_alerts"]))
    assert "ews_alerts" in result["unknown_relations"]["requested"]
    assert "Nothing was substituted" in result["unknown_relations"]["note"]


def test_a_large_field_request_paginates_rather_than_dropping_fields(
        catalog_service, runtime):
    """V4-AT-032. An EXPLICIT request that will not fit one page.

    An explicit list of field ids is always served, however long, because it
    is a request someone actually made. It is an unnamed relation EXPANSION
    that is refused — see the test below.
    """
    columns = list(runtime.catalog.columns("cockpit_facility_quarter"))
    assert len(columns) > 60, "this relation should not fit one page"
    field_ids = [f"cockpit_facility_quarter.{c}" for c in columns]

    result = catalog_service.inspect(request(
        detail=["fields"], field_ids=field_ids))
    assert result.get("next_cursor"), "an explicit long list still pages"
    assert result["omitted"]["remaining_fields"] > 0
    assert "nothing was abbreviated" in result["omitted"]["note"]
    page_two = catalog_service.inspect(request(
        detail=["fields"], field_ids=field_ids,
        cursor=result["next_cursor"]))
    first = {f["field_id"] for f in result["fields"]}
    second = {f["field_id"] for f in page_two["fields"]}
    assert first and second and not (first & second)


def test_expanding_a_relation_into_a_field_dump_is_refused(catalog_service):
    """The live loop's fuel: 60 definitions and an invitation to page 931.

    A partial dump that says more remains looks like progress and is not.
    """
    result = catalog_service.inspect(request(
        detail=["fields"], relation_ids=["cockpit_facility_quarter"]))
    assert result["status"] == "needs_scope"
    assert result.get("fields") is None
    assert result.get("next_cursor") is None
    assert "Name the field ids you need" in result["reason"]
    assert result["relation_sizes"]["cockpit_facility_quarter"] > 60
    assert result["added_new_information"] is False


def test_join_warnings_name_the_repetition_they_prevent(catalog_service):
    """V4-AT-032. The relationship detail says what goes wrong, not just how."""
    result = catalog_service.inspect(request(detail=["relationships"]))
    text = json.dumps(result["relationships"])
    assert "repeats once per facility" in text
    assert "ALLOCATED value" in text
    assert "Untested is not compliant" in text


def test_coverage_says_that_twenty_slots_are_not_twenty_observations(
        catalog_service):
    """V4-AT-033."""
    result = catalog_service.inspect(request(detail=["coverage"]))
    assert len(result["coverage"]["reporting_quarters"]) == 20
    assert "are not 20 observations" in result["coverage"]["note"]


def test_sufficient_starting_context_allows_execution_without_a_lookup(
        drive, release_id):
    """V4-AT-010. No compulsory discovery round."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT COUNT(*) AS n FROM cockpit_facility_quarter "
           f"WHERE reporting_quarter='{quarter}'")

    def finish(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="There are {{claim.n}} facility rows.",
                  numeric_claims=[{
                      "claim_id": "n",
                      "decimal_value": str(step["preview"][0]["n"]),
                      "unit": "rows", "display_precision": 0,
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": "0", "column_id": "n"}}]),
            "tu-2")])

    outcome, provider, _ = drive("how many facility rows this quarter", [
        ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT"),
            "objective": "count rows", "subquestions": ["how many"],
            "scope": {"reporting_periods": [quarter], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": [],
            "expected_output_grain": "release", "expected_units": "rows",
            "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                       "parameters": {}, "purpose": "count",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}, "tu-1")]),
        finish])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "no gratuitous catalog round"


# ---- ownership breadth --------------------------------------------------

@pytest.mark.parametrize("mode,owner,disposition,state", [
    ("THEORY_CONCEPT", "COCKPIT", "answer", st.COMPLETED),          # AT-008
    ("THEORY_CONCEPT", "COCKPIT", "answer", st.COMPLETED),          # AT-020
    ("OTHER_FUNCTIONALITY", "CREDIT_SCORING", "referral", st.REFERRED),
    ("UNSUPPORTED", "NONE", "unsupported", st.UNSUPPORTED),         # AT-026
])
def test_non_execution_modes_settle_without_touching_data(
        drive, mode, owner, disposition, state):
    """V4-AT-008, V4-AT-020, V4-AT-024, V4-AT-026.

    Theory, referral and unsupported settle without touching data: a generic
    EWS explanation needs no EWS read, a stored rating may be discussed but a
    new one may not be assigned, and a question about current external facts
    is unsupported rather than answered from memory.
    """
    body = final(intent=intent(mode, owner),
                 disposition=disposition,
                 narrative="A response that makes no portfolio claim.")
    if disposition == "referral":
        body["referral_owner"] = owner
        body["referral_reason"] = "That functionality owns it."
    outcome, provider, _ = drive("a question", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", body)])])
    assert outcome.state == state
    assert outcome.response["executed"] is False
    assert len(provider.sent) == 1


def test_a_mixed_request_marks_every_part(drive):
    """V4-AT-019, V4-AT-029. Nothing is quietly dropped."""
    outcome, _, _ = drive(
        "Explain DSCR and rank the lowest DSCR borrowers, and also run a "
        "downside PD shock",
        [ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT",
                          excluded=["the downside PD shock"]),
            disposition="clarification",
            narrative="Two of the three parts are mine; one is not.",
            clarification_question=(
                "Shall I answer the DSCR explanation and ranking here, and "
                "leave the PD shock to What-if?"),
            coverage=[
                {"subquestion": "explain DSCR", "status": "answered",
                 "evidence_refs": []},
                {"subquestion": "rank lowest DSCR borrowers",
                 "status": "needs_clarification", "evidence_refs": []},
                {"subquestion": "run a downside PD shock",
                 "status": "referred", "evidence_refs": []}]))])])
    assert outcome.state == st.WAITING_FOR_USER
    coverage = {c["subquestion"]: c["status"]
                for c in outcome.response["coverage"]}
    assert coverage["run a downside PD shock"] == "referred", (
        "an excluded part must appear in the coverage map, not vanish")
    assert len(coverage) == 3


def test_each_turn_recomputes_its_own_owner(drive, store_db, make_run,
                                            release_id):
    """V4-AT-027. A previous turn's owner authorizes nothing."""
    record = make_run("What is PIT versus TTC?")
    outcome, _, first = drive("What is PIT versus TTC?", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("THEORY_CONCEPT", "COCKPIT"),
            narrative="PIT reflects current conditions; TTC averages them."))])
    ], record=record)
    assert outcome.state == st.COMPLETED

    second_record, _ = store_db.accept_run(
        thread_id=first.thread_id, tenant_id="demo-tenant",
        principal_id="u1", question="Now shock PD by 200bp",
        mode="standard", release_id=release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="", deadline_at="")
    outcome2, _, _ = drive("Now shock PD by 200bp", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("OTHER_FUNCTIONALITY", "WHAT_IF"),
            disposition="referral", referral_owner="WHAT_IF",
            referral_reason="A new shock is a What-if simulation.",
            narrative="That is a new simulation, which What-if owns."))])
    ], record=second_record)
    assert outcome2.state == st.REFERRED
    assert outcome2.response["executed"] is False


# ---- answer contract ----------------------------------------------------

def _finalizer(store_db, release_id, artifacts=()):
    from backend.cockpit_v4.finalization import Finalizer

    return Finalizer(store=store_db, tenant_id="t", release_id=release_id,
                     limits=STANDARD_LIMITS, run_artifacts=set(artifacts))


def test_an_invalid_chart_is_dropped_without_another_analysis(store_db,
                                                              release_id):
    """V4-AT-095."""
    # Four sectors, because the valid chart has to survive on its own merits:
    # a one-row result is dropped by the SHAPE rule, and this test is about
    # the column rule. Both drop charts; only one of them is under test here.
    artifact_id = store_db.put_artifact(
        run_id="r", tenant_id="t", kind="result", release_id=release_id,
        scope={}, columns=["sector", "ead"],
        rows=[{"sector": "IT", "ead": 1.0},
              {"sector": "Manufacturing", "ead": 2.0},
              {"sector": "Real Estate", "ead": 3.0},
              {"sector": "Retail Trade", "ead": 4.0}])
    finalizer = _finalizer(store_db, release_id, {artifact_id})
    body = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                 narrative="Exposure is {{claim.v}}.",
                 numeric_claims=[{
                     "claim_id": "v", "decimal_value": "1.0",
                     "unit": "SAR million", "display_precision": 2,
                     "evidence": {"artifact_id": artifact_id,
                                  "row_key": "0", "column_id": "ead"}}],
                 charts=[{"kind": "bar", "title": "ok",
                          "artifact_id": artifact_id, "x_column": "sector",
                          "y_columns": ["ead"], "unit": "SAR million"},
                         {"kind": "bar", "title": "broken",
                          "artifact_id": artifact_id, "x_column": "nope",
                          "y_columns": ["also_nope"], "unit": "x"}])
    parsed = parse_final(body)
    report = finalizer.validate(parsed, executed=True)
    assert report.ok, report.problems
    assert any("dropped" in warning for warning in report.warnings)
    kept = finalizer.surviving_charts(parsed)
    assert len(kept) == 1 and kept[0]["title"] == "ok"


def test_suggestions_are_checked_against_the_catalog_not_executed(
        store_db, runtime, release_id):
    """V4-AT-096."""
    finalizer = _finalizer(store_db, release_id)
    parsed = parse_final(final(
        intent=intent("PRODUCT_HELP", "COCKPIT"),
        suggested_questions=[
            {"question": "Which sectors carry the most EAD?",
             "required_fields": ["cockpit_facility_quarter.ead_reported"],
             "required_quarters": [], "kind": "data_analysis"},
            {"question": "Show me the EWS alert queue",
             "required_fields": ["ews_alerts.priority"],
             "required_quarters": [], "kind": "data_analysis"},
            {"question": "What was 1999Q1 exposure?",
             "required_fields": ["cockpit_facility_quarter.ead_reported"],
             "required_quarters": ["1999Q1"], "kind": "data_analysis"}]))
    kept = finalizer.validate_suggestions(parsed, runtime.catalog)
    assert len(kept) == 1
    assert kept[0]["question"].startswith("Which sectors")


def test_only_one_answer_correction_is_available(drive, release_id):
    """V4-AT-094. And no new analysis during it."""
    bad = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                narrative="Exposure is {{claim.missing}}.")
    outcome, provider, _ = drive("EAD?", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", bad, "t1")]),
        ScriptedResult(tool_calls=[tool_call("finalize_response", bad, "t2")])])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.ANSWER_VALIDATION
    assert len(provider.sent) == 2, "exactly one correction, then a stop"


def test_no_new_execution_is_possible_during_an_answer_correction(
        drive, release_id):
    """V4-AT-094. Answer-only mode is enforced, not merely requested."""
    quarter = oracles.latest_quarter(release_id)
    bad = final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                narrative="Exposure is {{claim.missing}}.")
    execute = tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT"), "objective": "o",
        "subquestions": ["a"],
        "scope": {"reporting_periods": [quarter], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "g", "expected_units": "u",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": "SELECT 1 AS x", "parameters": {},
                   "purpose": "p", "input_artifact_ids": [],
                   "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, "t2")

    outcome, provider, _ = drive("EAD?", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", bad, "t1")]),
        ScriptedResult(tool_calls=[execute]),
        ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT"),
            disposition="partial_answer",
            narrative="Nothing could be bound to evidence.",
            limitations=["the claim had no evidence"]), "t3")])])
    assert outcome.state == st.PARTIAL
    assert outcome.response["executed"] is False, (
        "the execution attempted during answer-correction mode must not run")


# ---- thread continuity and memory ---------------------------------------

def test_an_earlier_turn_is_retrievable_exactly(store_db, release_id):
    """V4-AT-088. The exact turn, never a summary's paraphrase of it."""
    from backend.cockpit_v4.artifacts import ArtifactService
    from backend.cockpit_v4.contracts import parse_artifact

    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    turn_id = store_db.append_turn(
        thread_id=thread_id, run_id="r1", question="Which sector is worst?",
        answer={"disposition": "answer",
                "narrative": "Information Technology, by reported EAD."})
    service = ArtifactService(store=store_db, tenant_id="t",
                              release_id=release_id, limits=STANDARD_LIMITS)
    result = service.read(parse_artifact({
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "artifact_id": turn_id, "artifact_kind": "thread_turn",
        "columns": [], "offset": 0, "limit": 1, "cursor": ""}))
    assert result["status"] == "ok"
    assert result["question"] == "Which sector is worst?"
    assert "outranks any summary" in result["note"]


def test_a_failed_memory_job_changes_nothing_the_user_saw(store_db,
                                                          v4_config):
    """V4-AT-087. The answer is already published when memory runs."""
    from dataclasses import replace

    from backend.cockpit_v4 import memory

    cfg = replace(v4_config, memory_enabled=True, memory_model="a-model")
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    for i in range(12):
        store_db.append_turn(thread_id=thread_id, run_id=f"r{i}",
                             question=f"q{i}",
                             answer={"disposition": "answer",
                                     "narrative": f"a{i}",
                                     "numeric_claims": []})

    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("the memory store is unavailable")

    # The job runs on its own thread and swallows its own failure; what
    # matters is that the turns remain exactly retrievable afterwards.
    assert memory.maybe_schedule(store_db, thread_id, cfg=cfg) is True
    import time

    time.sleep(0.3)
    turns = store_db.recent_turns(thread_id, limit=3)
    assert len(turns) == 3
    assert turns[-1]["answer"]["narrative"] == "a11"


def test_a_summary_never_outranks_the_exact_turns_in_context(
        store_db, runtime, release_id):
    """V4-AT-088. The packet says which is authoritative."""
    from backend.cockpit_v4 import context as context_mod
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    packet = context_mod.build(
        question="and the one before that?",
        principal={"id": "u", "tenant": "demo-tenant"}, scope=scope,
        catalog=runtime.catalog, limits=STANDARD_LIMITS, mode="standard",
        release_summary=runtime.release_summary,
        recent_turns=[{"turn_id": "t1", "ordinal": 1, "question": "q",
                       "answer": {"narrative": "exact"}}],
        summary={"body": {"conclusions": []}})
    text = packet.first_user_message
    assert "these outrank any summary" in text
    assert "lower authority than the exact turns" in text
