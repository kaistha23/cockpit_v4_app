"""
MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

The remaining bounds, the storage and revocation boundaries, and the identity
facts a trace has to carry.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.budgets import BudgetExceeded, Ledger
from backend.cockpit_v4.config import STANDARD_LIMITS


def _ledger(store_db, capability, run_id, limits=None):
    return Ledger(limits=limits or STANDARD_LIMITS, capability=capability,
                  store=store_db, run_id=run_id)


@pytest.fixture
def run_id(store_db, release_id):
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="t", principal_id="p", question="q",
        mode="standard", release_id=release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="", deadline_at="")
    return record.run_id


# ---- rounds and call accounting ----------------------------------------

def test_a_fourth_successful_round_is_impossible(store_db, capability,
                                                 run_id):
    """V4-AT-058. And a repair stays inside the round it failed in."""
    ledger = _ledger(store_db, capability, run_id)
    for expected in (1, 2, 3):
        assert ledger.open_round() == expected
        # A repair inside the same round does NOT open a new one.
        assert ledger.open_round() == expected
        ledger.close_round()
    with pytest.raises(BudgetExceeded) as excinfo:
        ledger.open_round()
    assert excinfo.value.code == st.ROUND_LIMIT


def test_every_provider_attempt_including_counting_is_ledgered(
        drive, store_db):
    """V4-AT-059. Counting calls are HTTP attempts and are counted as such."""
    outcome, provider, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", final())])])
    budget = store_db.get_run(record.run_id).budget
    generations, _ = budget["generation_attempts"]
    provider_attempts, _ = budget["provider_attempts"]
    assert generations == 1
    assert provider_attempts > generations, (
        "the token count is an HTTP attempt too; if these are equal, "
        "something is not passing through the ledger")
    assert provider.count_calls >= 1


def test_a_partial_output_is_never_executed(drive, release_id):
    """V4-AT-060. Output accounting follows the stop reason, not a guess."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive("count", [
        ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT"), "objective": "o",
            "subquestions": ["a"],
            "scope": {"reporting_periods": [quarter], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": [],
            "expected_output_grain": "g", "expected_units": "u",
            "steps": [{"step_id": "s1", "language": "sql",
                       "code": (f"SELECT COUNT(*) AS n FROM "
                                f"cockpit_facility_quarter WHERE "
                                f"reporting_quarter='{quarter}'"),
                       "parameters": {}, "purpose": "p",
                       "input_artifact_ids": [],
                       "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}, "tu-1")],
            stop_reason="max_tokens"),
        ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT"),
            disposition="partial_answer",
            narrative="The first response was cut off.",
            limitations=["truncated response"]), "tu-2")])])
    assert outcome.state == st.PARTIAL
    assert outcome.response["executed"] is False


def test_a_large_but_affordable_request_is_not_refused_by_an_old_cap(
        store_db, capability, run_id):
    """V4-AT-061. V3's 64k input cutoff is gone; capacity is what matters."""
    from backend.cockpit_v4.provider import Analyst

    class Counter:
        def count_tokens(self, **_):
            return 64_626

        def converse(self, **_):
            raise AssertionError("this test never sends")

    ledger = _ledger(store_db, capability, run_id)
    analyst = Analyst(provider=Counter(), capability=capability,
                      ledger=ledger, system="s", tools=[])
    fits, counted, _ = analyst.fits(reserved_output=4096)
    assert counted == 64_626
    assert fits is True, (
        "64,626 tokens fits a 200k model with a 4k reservation; refusing it "
        "would be an obsolete product limit, not a capacity check")


def test_a_request_beyond_capacity_stops_with_input_context_limit(
        store_db, capability, run_id):
    """V4-AT-062. The actual hard limit, named as itself."""
    from backend.cockpit_v4.provider import Analyst, InputTooLarge

    class Huge:
        def count_tokens(self, **_):
            return 500_000

        def converse(self, **_):
            raise AssertionError("nothing should be sent")

    ledger = _ledger(store_db, capability, run_id)
    analyst = Analyst(provider=Huge(), capability=capability, ledger=ledger,
                      system="s", tools=[])
    with pytest.raises(InputTooLarge) as excinfo:
        analyst.ask(purpose="p", max_output_tokens=4096)
    assert excinfo.value.code == st.INPUT_CONTEXT_LIMIT
    assert "Nothing was sent" in str(excinfo.value)


def test_a_recorded_environment_change_permits_a_retry(store_db, capability,
                                                       run_id):
    """V4-AT-053. Without evading the submission cap."""
    ledger = _ledger(store_db, capability, run_id)
    deterministic = "key-same-error-class"
    store_db.record_submission(run_id=run_id, ordinal=1, round=1,
                               payload={}, status="failed",
                               no_progress_key=deterministic)
    with pytest.raises(BudgetExceeded) as excinfo:
        ledger.no_progress_check(deterministic)
    assert excinfo.value.code == st.NO_PROGRESS

    # A different error class is a different key: a transient failure is not
    # permanently cached as an invalid query.
    ledger.no_progress_check("key-different-error-class")
    # And the submission cap still applies regardless.
    for _ in range(5):
        ledger.spend_submission()
    with pytest.raises(BudgetExceeded) as excinfo:
        ledger.spend_submission()
    assert excinfo.value.code == st.EXECUTION_LIMIT


# ---- storage and revocation --------------------------------------------

def test_no_run_is_claimed_when_the_store_cannot_commit(tmp_path):
    """V4-AT-079. Accepted-then-evaporated is worse than a refusal."""
    from backend.cockpit_v4.run_store import RunStore, StorageUnavailable

    store = RunStore(tmp_path / "state.sqlite3")
    thread_id = store.create_thread(tenant_id="t", principal_id="p")

    # Break the store the way a full disk or a revoked mount does.
    store.close()
    (tmp_path / "state.sqlite3").write_text("not a database", encoding="utf-8")
    broken = RunStore.__new__(RunStore)
    broken.__dict__.update(store.__dict__)
    import threading

    broken._local = threading.local()

    with pytest.raises(StorageUnavailable):
        broken.accept_run(
            thread_id=thread_id, tenant_id="t", principal_id="p",
            question="q", mode="standard", release_id="r", ui_filters={},
            idempotency_key="", body_digest="", startup_sha="",
            deadline_at="")


def test_permission_revocation_is_enforced_at_the_artifact(store_db,
                                                           release_id):
    """V4-AT-089. A pinned release does not freeze authorization."""
    from backend.cockpit_v4.artifacts import ArtifactService
    from backend.cockpit_v4.contracts import parse_artifact

    artifact_id = store_db.put_artifact(
        run_id="r", tenant_id="bank-a", kind="result", release_id=release_id,
        scope={}, columns=["x"], rows=[{"x": 1}])

    allowed = ArtifactService(store=store_db, tenant_id="bank-a",
                              release_id=release_id, limits=STANDARD_LIMITS)
    body = {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
            "artifact_id": artifact_id, "artifact_kind": "result",
            "columns": [], "offset": 0, "limit": 10, "cursor": ""}
    assert allowed.read(parse_artifact(body))["status"] == "ok"

    # The same artifact, the same pinned release, a principal whose scope no
    # longer includes it.
    revoked = ArtifactService(store=store_db, tenant_id="bank-a-revoked",
                              release_id=release_id, limits=STANDARD_LIMITS)
    assert revoked.read(parse_artifact(body))["status"] == "not_available"


def test_evidence_from_another_release_is_labelled_comparison(store_db):
    """V4-AT-023. Historical evidence never poses as this release's result."""
    from backend.cockpit_v4.artifacts import ArtifactService
    from backend.cockpit_v4.contracts import parse_artifact

    artifact_id = store_db.put_artifact(
        run_id="r", tenant_id="t", kind="result", release_id="older-release",
        scope={}, columns=["ead"], rows=[{"ead": 1.0}])
    service = ArtifactService(store=store_db, tenant_id="t",
                              release_id="current-release",
                              limits=STANDARD_LIMITS)
    result = service.read(parse_artifact({
        "intent": intent("DATA_ANALYSIS", "COCKPIT"),
        "artifact_id": artifact_id, "artifact_kind": "result",
        "columns": [], "offset": 0, "limit": 10, "cursor": ""}))
    assert result["comparison_evidence"] is True
    assert "never as this release's result" in result["comparison_note"]


# ---- identity and delivery ---------------------------------------------

def test_the_startup_sha_is_recorded_on_the_run(store_db, release_id):
    """V4-AT-002. Not re-read from a checkout that may have moved."""
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="t", principal_id="p", question="q",
        mode="standard", release_id=release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="abc123def456",
        deadline_at="")
    stored = store_db._connect().execute(
        "SELECT startup_sha FROM runs WHERE run_id=?",
        (record.run_id,)).fetchone()
    assert stored["startup_sha"] == "abc123def456"


def test_generated_sent_and_rendered_are_distinct_facts(drive, store_db):
    """V4-AT-097. An unacknowledged answer is not recorded as seen."""
    outcome, _, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call("finalize_response", final())])])
    assert outcome.state == st.COMPLETED
    assert store_db.get_run(record.run_id).delivered_at == "", (
        "the answer was generated and persisted; nobody has said it was seen")
    store_db.mark_delivered(record.run_id)
    assert store_db.get_run(record.run_id).delivered_at != ""


def test_the_demo_principal_cannot_name_its_own_tenant(v4_config, runtime):
    """V4-AT-091. Server-controlled, loopback-only, release-scoped."""
    from backend.cockpit_v4 import app as v4app

    resolver = v4app._demo_resolver(v4_config, runtime)

    class Request:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()
            self.headers = {"X-Tenant": "some-other-bank"}
            self.state = type("S", (), {})()

    loopback = resolver(Request("127.0.0.1"))
    assert loopback is not None
    assert loopback["tenant"] == runtime.release_summary["tenants"][0], (
        "the demo principal is scoped to the pinned synthetic release's own "
        "tenant, not to anything a request names")
    assert resolver(Request("10.0.0.5")) is None, (
        "a demo principal that works off-host is not a demo profile")

    off = replace(v4_config, local_demo_auth=False)
    assert v4app._demo_resolver(off, runtime)(Request("127.0.0.1")) is None


def test_a_slow_subscriber_reads_a_bounded_page(store_db, release_id):
    """V4-AT-075. Delivery is paged, so a slow reader cannot grow memory."""
    from backend.cockpit_v4 import events as ev

    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="t", principal_id="p", question="q",
        mode="standard", release_id=release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="", deadline_at="")
    emitter = ev.Emitter(store_db, record.run_id,
                         started_monotonic=time.monotonic())
    for i in range(500):
        emitter.append(ev.MODEL_REQUESTED, stage="understanding",
                       operation="generate", status=ev.STATUS_STARTED,
                       public_message=f"step {i}")
    page = store_db.events_since(record.run_id, 0, limit=200)
    assert len(page) == 200, "the reader gets a bounded page, not everything"
    assert page[0].seq == 1 and page[-1].seq == 200
    later = store_db.events_since(record.run_id, page[-1].seq, limit=200)
    assert later[0].seq == 201
