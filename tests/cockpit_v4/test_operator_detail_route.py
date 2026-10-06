"""
The operator record an event points at, readable at last.

NO MODEL · REAL HTTP · in-process ASGI client. No paid provider call.

Every process event may carry a `detail_ref` -- which model, which
allowance, which check refused and on what evidence. Twenty-odd places in
the orchestrator write one and the trace hands it out on both the live and
the replay channel, but nothing could read one back: `get_detail` had one
definition and no route. An operator looking at a failed run saw
`dt-3f9a1c…` printed in the process panel and had nowhere to take it. The
reference was a promise the API did not keep.

Two things are pinned here. The route exists and is scoped exactly like a
result artifact -- the run is authorized first, and then the record's own
`run_id` must match, because a `detail_ref` is not a bearer token. And the
bodies it serves are redacted, which is now true of ALL of them: the
redaction moved to `RunStore.put_detail`, at the write, because two writers
-- the allowance envelope and the question-normalization report, which
stores eight kilobytes of the reader's own text -- reached the table
without passing through the orchestrator helper that used to do it. A
guarantee a caller can decline to honour is not a guarantee, and it stopped
being theoretical the moment this route existed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes

P = "/api/v1/cockpit-v4"
OTHER_TENANT = "someone-else"


@pytest.fixture
def client(store_db, runtime):
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def a_run(store_db, release_id):
    """One accepted run, owned by the tenant the client authenticates as."""
    def _make(tenant: str = lake.DEFAULT_TENANT):
        thread_id = store_db.create_thread(tenant_id=tenant,
                                           principal_id="u1")
        record, _ = store_db.accept_run(
            thread_id=thread_id, tenant_id=tenant, principal_id="u1",
            question="q", mode="standard", release_id=release_id,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="testsha", deadline_at="")
        return record
    return _make


def test_the_reference_an_event_hands_out_can_be_resolved(client, store_db,
                                                          a_run):
    """The whole point. A ref nothing can dereference is a dead end with a
    name."""
    record = a_run()
    ref = store_db.put_detail(record.run_id, {"failed_check": "join_grain",
                                              "measures_at_risk": ["ead"]})
    response = client.get(f"{P}/runs/{record.run_id}/details/{ref}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detail_ref"] == ref
    assert body["run_id"] == record.run_id
    assert body["body"]["failed_check"] == "join_grain"
    assert body["body"]["measures_at_risk"] == ["ead"]


def test_a_reference_from_another_run_is_not_found(client, store_db, a_run):
    """A `detail_ref` is not a capability. Authorizing the run in the path
    and then serving whatever ref was asked for would make the run id
    decorative."""
    mine, theirs = a_run(), a_run()
    ref = store_db.put_detail(theirs.run_id, {"secret_to_that_run": 1})
    response = client.get(f"{P}/runs/{mine.run_id}/details/{ref}")
    assert response.status_code == 404, response.text


def test_another_tenants_run_is_not_found_rather_than_forbidden(
        client, store_db, a_run):
    """The same refusal `_authorize` gives, for the same reason: 403 would
    confirm the run exists."""
    other = a_run(tenant=OTHER_TENANT)
    ref = store_db.put_detail(other.run_id, {"anything": 1})
    response = client.get(f"{P}/runs/{other.run_id}/details/{ref}")
    assert response.status_code == 404, response.text


def test_a_reference_that_does_not_exist_is_not_found(client, a_run):
    record = a_run()
    response = client.get(f"{P}/runs/{record.run_id}/details/dt-nothing")
    assert response.status_code == 404, response.text


def test_a_credential_never_reaches_the_table_whatever_the_caller_does(
        client, store_db, a_run):
    """Redaction at the WRITE, not at this read.

    A filter applied when a body is served protects only the readers who go
    through that filter -- and the same rows are reachable from a database
    file an operator can copy. This asserts the stored bytes, not the
    response.
    """
    record = a_run()
    ref = store_db.put_detail(record.run_id, {
        "api_key": "sk-live-abcdef0123456789",
        "authorization": "Bearer abcdef0123456789",
        "model": "mock-analyst"})
    stored = store_db.get_detail(ref)["body"]
    assert stored["api_key"] == "[redacted]"
    assert stored["authorization"] == "[redacted]"
    assert stored["model"] == "mock-analyst", "a diagnostic may name a model"

    served = client.get(f"{P}/runs/{record.run_id}/details/{ref}").json()
    assert "sk-live" not in str(served)


def test_every_writer_is_redacted_not_only_the_ones_that_asked_to_be(
        store_db, a_run):
    """The two that were not.

    `worker` wrote the allowance envelope and `routes` wrote the
    question-normalization report straight to `put_detail`, bypassing the
    orchestrator helper that redacted. Moving the redaction into the store
    is what makes the guarantee unbypassable -- so it is asserted on the
    store, where a future writer will also land.
    """
    record = a_run()
    ref = store_db.put_detail(record.run_id, {
        "envelope": {"session_token": "t-0123456789abcdef"},
        "normalization": {"original": "what is my exposure"}})
    stored = store_db.get_detail(ref)["body"]
    assert stored["envelope"]["session_token"] == "[redacted]"
    assert stored["normalization"]["original"] == "what is my exposure", (
        "the reader's own question is not a secret from the reader")
