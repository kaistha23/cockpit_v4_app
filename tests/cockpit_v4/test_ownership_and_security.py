"""
MODEL MOCK · REAL DATABASE/RUNNER.

Ownership decisions, the excluded-domain boundary, and what a trace may
contain. The honest limit is stated in the module docstring of the code under
test and repeated here: isolation prevents reading another domain; no
keyword list proves that arbitrary SQL is semantically safe. These tests
check the mechanical boundary, which is the part that can be checked.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import (Rejection, parse_catalog,
                                          parse_execution)


def _exec_args(sql: str, mode="DATA_ANALYSIS", owner="COCKPIT",
               ambiguities=()):
    return {"intent": intent(mode, owner, ambiguities=list(ambiguities)),
            "objective": "o", "subquestions": ["a"],
            "scope": {"reporting_periods": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": ["f"],
            "expected_output_grain": "g", "expected_units": "u",
            "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                       "parameters": {}, "purpose": "p",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}


# ---- ownership ---------------------------------------------------------

def test_a_referral_settles_without_executing_anything(drive):
    """V4-AT-021, V4-AT-022, V4-AT-025, V4-AT-029. No excluded-domain read, ever."""
    outcome, provider, _ = drive(
        "Why did this borrower's EWS score rise, and what if PD went up 200bp?",
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("OTHER_FUNCTIONALITY", "EWS",
                                understood="an EWS score movement and a new "
                                           "PD shock",
                                excluded=["the What-if PD shock"]),
                  disposition="referral", referral_owner="EWS",
                  referral_reason="Early Warning owns alert scores.",
                  narrative="Early Warning owns score movements; a new PD "
                            "shock belongs to What-if.",
                  coverage=[
                      {"subquestion": "why the EWS score rose",
                       "status": "referred", "evidence_refs": []},
                      {"subquestion": "recompute ECL with PD up 200bp",
                       "status": "referred", "evidence_refs": []}]))])])
    assert outcome.state == st.REFERRED
    assert outcome.response["executed"] is False
    statuses = {c["status"] for c in outcome.response["coverage"]}
    assert statuses == {"referred"}, (
        "a mixed cross-module request must not quietly drop the excluded part")


def test_execution_requires_a_declared_cockpit_data_analysis(store_db,
                                                             runtime,
                                                             release_id):
    """V4-AT-022. A theory or referral intent cannot execute."""
    from backend.cockpit_v4.execute_tool import ExecutionService

    service = ExecutionService(
        session=None, scope=None, catalog=runtime.catalog, store=store_db,
        run_id="r", tenant_id="t", release_id=release_id,
        limits=__import__("backend.cockpit_v4.config", fromlist=["x"])
        .STANDARD_LIMITS)
    for mode, owner in (("THEORY_CONCEPT", "COCKPIT"),
                        ("DATA_ANALYSIS", "WHAT_IF"),
                        ("PRODUCT_HELP", "COCKPIT")):
        submission = parse_execution(
            _exec_args("SELECT 1", mode=mode, owner=owner), max_steps=6)
        with pytest.raises(Rejection) as excinfo:
            service.validate_batch(submission)
        assert excinfo.value.code == st.SECURITY_DENIED


def test_unresolved_ambiguity_blocks_execution(store_db, runtime,
                                               release_id):
    """V4-AT-028. 'Exposure' is not silently resolved to a column."""
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService

    service = ExecutionService(
        session=None, scope=None, catalog=runtime.catalog, store=store_db,
        run_id="r", tenant_id="t", release_id=release_id,
        limits=STANDARD_LIMITS)
    submission = parse_execution(
        _exec_args("SELECT 1", ambiguities=["which exposure measure"]),
        max_steps=6)
    with pytest.raises(Rejection) as excinfo:
        service.validate_batch(submission)
    assert "BLOCKING ambiguity" in str(excinfo.value)
    assert "resolved_assumptions" in str(excinfo.value), (
        "the refusal must say where a resolution belongs instead")


def test_samples_are_refused_outside_a_data_analysis():
    """V4-AT-031. Product help reads coverage metadata, not borrower rows."""
    with pytest.raises(Rejection) as excinfo:
        parse_catalog({"intent": intent("PRODUCT_HELP", "COCKPIT"),
                       "query": "", "relation_ids": ["cockpit_facility_quarter"],
                       "field_ids": [], "detail": ["samples"],
                       "reporting_periods": [], "sample_rows": 5,
                       "cursor": ""})
    assert excinfo.value.code == st.SECURITY_DENIED


# ---- domain isolation --------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM ews_alerts LIMIT 1",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "ATTACH '/tmp/other.db' AS other",
    "INSTALL httpfs",
    "COPY (SELECT 1) TO '/tmp/leak.csv'",
    "SELECT * FROM cockpit_facility_quarter; DROP TABLE cockpit_facility_quarter",
])
def test_out_of_domain_and_escape_attempts_are_refused(sql, store_db,
                                                       runtime, release_id):
    """V4-AT-034, V4-AT-054. Refused by structure, authorization or the jail."""
    from backend.cockpit_agentic import sql as v3_sql
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService
    from backend.cockpit_v4.service import _Principal
    from backend.cockpit_agentic import scope as v3_scope

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    session = v3_sql.open_session(scope=scope, catalog=runtime.catalog)
    service = ExecutionService(
        session=session, scope=scope, catalog=runtime.catalog, store=store_db,
        run_id="r", tenant_id="demo-tenant", release_id=release_id,
        limits=STANDARD_LIMITS)
    submission = parse_execution(_exec_args(sql), max_steps=6)
    with pytest.raises(Rejection) as excinfo:
        service.validate_batch(submission)
    assert excinfo.value.code in (st.SQL_VALIDATION, st.SECURITY_DENIED)


def test_python_is_unavailable_rather_than_silently_run_as_sql(
        store_db, runtime, release_id):
    """V4-AT-055, V4-AT-056. No in-process substitute, and no language swap."""
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService
    from backend.cockpit_v4 import pyrunner

    args = _exec_args("SELECT 1")
    args["steps"][0]["language"] = "python"
    args["steps"][0]["code"] = "result = [{'x': 1}]"
    service = ExecutionService(
        session=None, scope=None, catalog=runtime.catalog, store=store_db,
        run_id="r", tenant_id="t", release_id=release_id,
        limits=STANDARD_LIMITS, python_runner=pyrunner.PythonRunner())
    submission = parse_execution(args, max_steps=6)
    with pytest.raises(Rejection) as excinfo:
        service.validate_batch(submission)
    assert excinfo.value.code == st.PYTHON_UNAVAILABLE
    assert "NOT rewritten as SQL" in excinfo.value.message


def test_an_artifact_from_another_tenant_is_not_confirmed_to_exist(store_db):
    """V4-AT-090. A denial that names the artifact is a disclosure."""
    from backend.cockpit_v4.artifacts import ArtifactService
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.contracts import parse_artifact

    artifact_id = store_db.put_artifact(
        run_id="r1", tenant_id="bank-a", kind="result", release_id="rel",
        scope={}, columns=["x"], rows=[{"x": 1}])
    service = ArtifactService(store=store_db, tenant_id="bank-b",
                              release_id="rel", limits=STANDARD_LIMITS)
    result = service.read(parse_artifact({
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "artifact_id": artifact_id, "artifact_kind": "result",
        "columns": [], "offset": 0, "limit": 10, "cursor": ""}))
    assert result["status"] == "not_available"
    assert artifact_id not in json.dumps(result)


def test_an_artifact_id_may_not_be_a_path():
    from backend.cockpit_v4.contracts import parse_artifact

    for bad in ("../../etc/passwd", "file:///etc/passwd", "a/b"):
        with pytest.raises(Rejection) as excinfo:
            parse_artifact({"intent": intent("DATA_ANALYSIS", "COCKPIT"),
                            "artifact_id": bad, "artifact_kind": "result",
                            "columns": [], "offset": 0, "limit": 1,
                            "cursor": ""})
        assert excinfo.value.code == st.SECURITY_DENIED


# ---- trace safety ------------------------------------------------------

def test_no_secret_reaches_a_persisted_detail(store_db, drive):
    """V4-AT-070. Redaction happens on the way IN, so exports cannot leak."""
    from backend.cockpit_v4.orchestration import _redact

    body = _redact({"api_key": "sk-live-abcdef", "authorization": "Bearer xyz",
                    "note": "sk-live-should-be-redacted-too",
                    "nested": {"cookie": "session=1", "model": "an-id"}})
    assert body["api_key"] == "[redacted]"
    assert body["authorization"] == "[redacted]"
    assert body["note"] == "[redacted]"
    assert body["nested"]["cookie"] == "[redacted]"
    assert body["nested"]["model"] == "an-id"


def test_the_trace_carries_no_private_reasoning(drive, store_db):
    """V4-AT-070. Public messages are business language, not model thinking."""
    outcome, provider, record = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT",
                                rationale="answer from product metadata")))])])
    events = store_db.events_since(record.run_id)
    dumped = json.dumps([e.to_dict() for e in events])
    for forbidden in ("thinking", "chain of thought", "sk-", "Bearer "):
        assert forbidden not in dumped
    for event in events:
        assert event.public_message, "every event says what happened"


def test_only_the_cockpit_credential_is_read(monkeypatch):
    """V4-AT-092. No fallback to a shared or ambient key."""
    from backend.cockpit_v4 import service

    monkeypatch.delenv("COCKPIT_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    with pytest.raises(service.PreflightFailed) as excinfo:
        service.credential()
    assert excinfo.value.code == st.PROVIDER_CREDENTIAL_MISSING
    assert "sk-should-not-be-used" not in str(excinfo.value)
    assert "COCKPIT_ANTHROPIC_API_KEY" in str(excinfo.value)
