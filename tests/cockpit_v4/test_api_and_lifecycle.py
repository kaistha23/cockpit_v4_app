"""
MODEL MOCK · REAL DATABASE/RUNNER · REAL HTTP (in-process ASGI client).

Intake, idempotency, SSE replay, cancellation, worker death and delivery.
These use FastAPI's own test client against the real router and the real
store; the browser-level equivalents are in the browser evidence, labelled
separately.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from conftest import ScriptedProvider, ScriptedResult, final, intent, tool_call
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st


@pytest.fixture
def client(store_db, runtime):
    """The REAL runtime, as production builds it.

    This fixture used to install a stand-in carrying only a config -- the
    same shape the application itself substituted when preflight failed. So
    the suite and the broken runtime were fake in the same way, and every
    test here passed while a live Mac served a traceback from the attention
    endpoint and answered 202 for runs nothing would ever process.

    A test runtime that exposes less than production's does not prove
    production works. It proves the test agrees with itself.
    """
    app = FastAPI()

    def resolver(request: Request):
        tenant = request.headers.get("X-Test-Tenant", "demo-tenant")
        user = request.headers.get("X-Test-User", "u1")
        if tenant == "anonymous":
            return None
        return {"id": user, "tenant": tenant}

    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=resolver, startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


P = "/api/v1/cockpit-v4"


def test_intake_returns_202_and_a_durable_run(client, store_db):
    """202 means durable and claimable. It does not mean answered."""
    response = client.post(f"{P}/runs", json={"question": "Who are you?"})
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "ACCEPTED"
    assert "does not mean answered" in body["note"]
    assert store_db.get_run(body["run_id"]) is not None
    events = store_db.events_since(body["run_id"])
    assert events[0].event_type == ev.RUN_ACCEPTED


def test_the_same_key_and_body_returns_one_run(client):
    """V4-AT-071. A lost acceptance response must not buy a second run."""
    # A retry resends the SAME body. The thread it landed in is part of that
    # body, so the client sends the payload it already sent.
    thread_id = client.post(f"{P}/threads").json()["thread_id"]
    payload = {"question": "Who are you?", "thread_id": thread_id}
    first = client.post(f"{P}/runs", json=payload,
                        headers={"Idempotency-Key": "k1"}).json()
    second = client.post(f"{P}/runs", json=payload,
                         headers={"Idempotency-Key": "k1"}).json()
    assert second["run_id"] == first["run_id"]
    assert second["duplicate"] is True


def test_the_same_key_with_a_different_body_is_a_typed_conflict(client):
    """V4-AT-072."""
    client.post(f"{P}/runs", json={"question": "A"},
                headers={"Idempotency-Key": "k2"})
    response = client.post(f"{P}/runs", json={"question": "B"},
                           headers={"Idempotency-Key": "k2"})
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_CONFLICT"


def test_another_tenant_cannot_read_a_run(client, store_db):
    """V4-AT-090. Cross-tenant status, events and cancel reveal nothing."""
    run_id = client.post(f"{P}/runs", json={"question": "Q"}).json()["run_id"]
    headers = {"X-Test-Tenant": "other-bank"}
    for path in (f"{P}/runs/{run_id}", f"{P}/runs/{run_id}/events"):
        assert client.get(path, headers=headers).status_code == 404
    assert client.post(f"{P}/runs/{run_id}/cancel",
                       headers=headers).status_code == 404


def test_an_unauthenticated_request_is_refused(client):
    response = client.post(f"{P}/runs", json={"question": "Q"},
                           headers={"X-Test-Tenant": "anonymous"})
    assert response.status_code == 401


def test_a_thread_id_is_server_generated_and_not_global(client):
    """V4-AT-083. No shared hard-coded `cockpit-web` thread."""
    a = client.post(f"{P}/threads").json()["thread_id"]
    b = client.post(f"{P}/threads",
                    headers={"X-Test-User": "u2"}).json()["thread_id"]
    assert a != b
    assert "cockpit-web" not in a


def test_sse_replays_committed_events_from_a_cursor(client, store_db):
    """V4-AT-073, V4-AT-074. Reconnect never resubmits the question."""
    run_id = client.post(f"{P}/runs", json={"question": "Q"}).json()["run_id"]
    emitter = ev.Emitter(store_db, run_id, started_monotonic=time.monotonic())
    for i in range(3):
        emitter.append(ev.MODEL_REQUESTED, stage="understanding",
                       operation="generate", status=ev.STATUS_STARTED,
                       public_message=f"step {i}")
    emitter.append(ev.RUN_FAILED, stage="publishing", operation="stop",
                   status=ev.STATUS_FAILED, public_message="done")
    store_db.update_state(run_id,
                          expect_version=store_db.get_run(run_id).version,
                          state=st.FAILED, error_code=st.INTERNAL_ERROR,
                          terminal=True)

    with client.stream("GET", f"{P}/runs/{run_id}/events",
                       headers={"Last-Event-ID": "2"}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    seqs = [int(line.split(": ")[1]) for line in body.splitlines()
            if line.startswith("id: ")]
    assert seqs == [3, 4, 5], "replay must start after the cursor, in order"
    assert "run.settled" in body


def test_cancellation_is_idempotent_and_does_not_unsettle_a_finished_run(
        client, store_db):
    """V4-AT-077."""
    run_id = client.post(f"{P}/runs", json={"question": "Q"}).json()["run_id"]
    first = client.post(f"{P}/runs/{run_id}/cancel").json()
    assert first["cancelled"] is True
    assert client.post(f"{P}/runs/{run_id}/cancel").json()["cancelled"] is True

    store_db.update_state(run_id,
                          expect_version=store_db.get_run(run_id).version,
                          state=st.COMPLETED,
                          final_response={"disposition": "answer"},
                          terminal=True)
    after = client.post(f"{P}/runs/{run_id}/cancel").json()
    assert after["cancelled"] is False
    assert after["state"] == st.COMPLETED


def test_a_completed_answer_survives_a_delivery_failure(client, store_db,
                                                        runtime):
    """V4-AT-080, V4-AT-097. Retrievable by run id, with no second paid run."""
    from backend.cockpit_v4.worker import Worker

    run_id = client.post(f"{P}/runs",
                         json={"question": "Who are you?"}).json()["run_id"]
    runtime.provider = ScriptedProvider([ScriptedResult(tool_calls=[
        tool_call("finalize_response", final())])])
    Worker(store=store_db, runtime=runtime).execute(
        store_db.claim_next("w-test"))

    # The browser never acknowledged. The answer is still there.
    status = client.get(f"{P}/runs/{run_id}").json()
    assert status["state"] == st.COMPLETED
    assert status["final_response"]["disposition"] == "answer"
    assert status["delivered_at"] == "", (
        "generated is not delivered; an unacknowledged answer must not be "
        "recorded as seen")

    client.post(f"{P}/runs/{run_id}/delivered")
    assert client.get(f"{P}/runs/{run_id}").json()["delivered_at"] != ""


def test_a_dead_worker_is_settled_by_the_supervisor(store_db):
    """V4-AT-078. INTERRUPTED, and nothing paid is replayed."""
    from backend.cockpit_v4.supervisor import Supervisor

    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="t", principal_id="p", question="Q",
        mode="standard", release_id="r", ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="", deadline_at="")
    claimed = store_db.claim_next("w-dead")
    store_db.update_state(claimed.run_id, expect_version=claimed.version,
                          state=st.MODEL_RUNNING, operation="generation")

    supervisor = Supervisor(store=store_db, lease_stale_seconds=0.0)
    settled = supervisor.sweep()
    assert settled["interrupted"] == 1

    after = store_db.get_run(record.run_id)
    assert after.state == st.INTERRUPTED
    assert after.error_code == st.WORKER_LOST
    message = store_db.events_since(record.run_id)[-1].public_message
    assert "Nothing was replayed" in message


def test_a_late_worker_cannot_overwrite_a_settled_run(store_db):
    """V4-AT-076. Sequence and version fencing."""
    from backend.cockpit_v4.run_store import LeaseLost, TerminalAlready

    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="t", principal_id="p", question="Q",
        mode="standard", release_id="r", ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="", deadline_at="")
    stale_version = record.version
    store_db.update_state(record.run_id, expect_version=stale_version,
                          state=st.COMPLETED,
                          final_response={"disposition": "answer"},
                          terminal=True)
    with pytest.raises((LeaseLost, TerminalAlready)):
        store_db.update_state(record.run_id, expect_version=stale_version,
                              state=st.FAILED, terminal=True)
    assert store_db.get_run(record.run_id).state == st.COMPLETED


def test_diagnostics_separates_the_three_capabilities(client):
    """V4-AT-002, V4-AT-098. Not one misleading all-green badge."""
    body = client.get(f"{P}/diagnostics").json()
    for key in ("ready_for_product_help", "ready_for_sql_analysis",
                "ready_for_python_analysis"):
        assert key in body
    assert body["checks"]["python_runner"]["available"] is False
    assert body["settings"]["credential"] in ("PRESENT", "MISSING")
    dumped = json.dumps(body)
    assert "sk-" not in dumped
