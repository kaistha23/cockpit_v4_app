"""
What a credit officer does after reading an answer, and across five sessions.

MODEL MOCK · REAL DATABASE · REAL HTTP (in-process ASGI client).

Two things are proven here and one is deliberately not.

PROVEN: a finished analysis can be saved, gathered into an investigation,
commented on, shared with a named colleague, and that every one of those is
tenant-scoped -- a second tenant holding the same identifier sees nothing.

PROVEN: the notification outbox records what would be sent and reports
`delivered: false` with the reason, because this build has no transport and
an empty recipient allow-list. A recipient outside the allow-list is REFUSED
and never reaches a transport at all.

NOT CLAIMED: that any message was delivered. No external mail, chat or
webhook call is made by this module, and the one test that exercises a
transport installs a recording double whose name appears on the row so the
audit can tell it apart from a real one.
"""

from __future__ import annotations

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.cockpit_v4 import collaboration as collab
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st

P = "/api/v1/cockpit-v4"


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()

    def resolver(request: Request):
        tenant = request.headers.get("X-Test-Tenant", "demo-tenant")
        user = request.headers.get("X-Test-User", "u1")
        if tenant == "anonymous":
            return None
        return {"id": user, "tenant": tenant}

    routes.install(store=store_db, runtime=runtime,
                   cfg=runtime.cfg,
                   principal_resolver=resolver, startup_sha="testsha")
    routes._STATE.pop("notifier", None)
    app.include_router(routes.router)
    return TestClient(app)


def _answered(drive, question="What is total EAD by sector?"):
    """One completed run whose answer is real enough to be worth saving."""
    outcome, _, record = drive(
        question,
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                  narrative="The Cockpit answers corporate credit "
                            "questions over the published release."))])])
    assert outcome.state == st.COMPLETED, outcome.message
    return record


# ---- saving an analysis ------------------------------------------------

def test_a_finished_analysis_can_be_saved_with_the_answer_it_published(
        client, drive, store_db):
    record = _answered(drive)
    response = client.post(f"{P}/saved-analyses",
                           json={"run_id": record.run_id,
                                 "title": "EAD by sector, 2026Q2",
                                 "note": "for the quarterly pack"})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["question"] == record.question
    published = store_db.get_run(record.run_id).final_response
    assert saved["body"]["narrative"] == published["narrative"], (
        "a saved analysis keeps the published words, not a re-rendering")
    assert client.get(f"{P}/saved-analyses/{saved['saved_id']}"
                      ).json()["title"] == "EAD by sector, 2026Q2"
    assert [s["saved_id"] for s in
            client.get(f"{P}/saved-analyses").json()["saved"]] == [
        saved["saved_id"]]


def test_an_unfinished_run_cannot_be_saved(client, make_run):
    record = make_run("What is total EAD by sector?")
    response = client.post(f"{P}/saved-analyses",
                           json={"run_id": record.run_id})
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "NOT_FINISHED"


def test_another_tenant_cannot_read_a_saved_analysis(client, drive):
    record = _answered(drive)
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": record.run_id}).json()
    other = client.get(f"{P}/saved-analyses/{saved['saved_id']}",
                       headers={"X-Test-Tenant": "other-bank"})
    assert other.status_code == 404, (
        "a wrong tenant must not learn that the row exists")


# ---- investigations ----------------------------------------------------

def test_an_investigation_gathers_saved_work(client, drive):
    first = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id,
                              "title": "Sector EAD"}).json()
    second = client.post(
        f"{P}/saved-analyses",
        json={"run_id": _answered(drive, "Which sectors moved?").run_id,
              "title": "Sector movement"}).json()

    created = client.post(f"{P}/investigations", json={
        "title": "Manufacturing deterioration, 2026Q2",
        "summary": "Stage 2 and ECL both moved.",
        "origin": "attention:segment",
        "saved_ids": [first["saved_id"], second["saved_id"]]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == collab.OPEN
    assert [i["ref_id"] for i in body["items"]] == [first["saved_id"],
                                                    second["saved_id"]]

    moved = client.post(
        f"{P}/investigations/{body['investigation_id']}/status",
        json={"status": collab.IN_REVIEW})
    assert moved.status_code == 200
    assert moved.json()["status"] == collab.IN_REVIEW


def test_an_investigation_will_not_take_another_tenants_saved_work(client,
                                                                   drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    refused = client.post(f"{P}/investigations",
                          json={"title": "Borrowed evidence",
                                "saved_ids": [saved["saved_id"]]},
                          headers={"X-Test-Tenant": "other-bank"})
    assert refused.status_code == 404


def test_an_unknown_status_is_refused_by_name(client):
    created = client.post(f"{P}/investigations",
                          json={"title": "Anything"}).json()
    bad = client.post(
        f"{P}/investigations/{created['investigation_id']}/status",
        json={"status": "escalated-to-board"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["error_code"] == "UNKNOWN_STATUS"
    assert "open" in bad.json()["detail"]["message"]


# ---- comments ----------------------------------------------------------

def test_comments_attach_to_saved_work_and_to_investigations(client, drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    investigation = client.post(f"{P}/investigations",
                                json={"title": "Review"}).json()
    for kind, subject_id in ((collab.SAVED_ANALYSIS, saved["saved_id"]),
                             (collab.INVESTIGATION,
                              investigation["investigation_id"])):
        posted = client.post(f"{P}/comments",
                             json={"subject_kind": kind,
                                   "subject_id": subject_id,
                                   "body": "Check the collateral first."})
        assert posted.status_code == 201, posted.text
        listed = client.get(f"{P}/comments", params={
            "subject_kind": kind, "subject_id": subject_id}).json()["comments"]
        assert [c["body"] for c in listed] == ["Check the collateral first."]
        assert listed[0]["author_id"] == "u1"


def test_a_comment_on_something_that_is_not_yours_is_not_accepted(client,
                                                                 drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    refused = client.post(f"{P}/comments",
                          json={"subject_kind": collab.SAVED_ANALYSIS,
                                "subject_id": saved["saved_id"],
                                "body": "hello"},
                          headers={"X-Test-Tenant": "other-bank"})
    assert refused.status_code == 404


def test_a_comment_about_an_unknown_kind_of_thing_is_refused(client):
    investigation = client.post(f"{P}/investigations",
                                json={"title": "Review"}).json()
    refused = client.post(f"{P}/comments",
                          json={"subject_kind": "borrower",
                                "subject_id": investigation[
                                    "investigation_id"],
                                "body": "hello"})
    assert refused.status_code == 400
    assert refused.json()["detail"]["error_code"] == "UNKNOWN_SUBJECT_KIND"


# ---- sharing inside the bank ------------------------------------------

def test_sharing_records_the_audience_and_reports_no_delivery(client, drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    shared = client.post(f"{P}/shares",
                         json={"subject_kind": collab.SAVED_ANALYSIS,
                               "subject_id": saved["saved_id"],
                               "audience_id": "credit.committee",
                               "message": "For Thursday."})
    assert shared.status_code == 201, shared.text
    body = shared.json()
    assert body["share"]["audience_id"] == "credit.committee"
    assert body["notification"] is None, (
        "no address was given, so nothing should have been recorded")
    assert body["delivery"]["can_deliver"] is False
    listed = client.get(f"{P}/shares", params={
        "subject_kind": collab.SAVED_ANALYSIS,
        "subject_id": saved["saved_id"]}).json()["shares"]
    assert len(listed) == 1


def test_a_share_of_another_tenants_work_is_refused(client, drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    refused = client.post(f"{P}/shares",
                          json={"subject_kind": collab.SAVED_ANALYSIS,
                                "subject_id": saved["saved_id"],
                                "audience_id": "someone"},
                          headers={"X-Test-Tenant": "other-bank"})
    assert refused.status_code == 404


# ---- the outbox, and what it refuses to claim --------------------------

def test_this_build_records_notifications_and_sends_none(client, drive):
    """The default posture: no transport, no authorized recipient."""
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    shared = client.post(f"{P}/shares",
                         json={"subject_kind": collab.SAVED_ANALYSIS,
                               "subject_id": saved["saved_id"],
                               "audience_id": "r.mehta",
                               "message": "Please review.",
                               "notify_email": "r.mehta@example-bank.test"})
    notification = shared.json()["notification"]
    assert notification["delivered"] is False
    assert notification["state"] == collab.REFUSED
    assert notification["transport"] == ""
    assert "not an authorized recipient" in notification["reason"]

    outbox = client.get(f"{P}/notifications").json()
    assert outbox["delivery"]["can_deliver"] is False
    assert all(row["delivered"] is False for row in outbox["notifications"])


def test_an_address_outside_the_allow_list_never_reaches_a_transport(
        client, store_db, drive):
    """A refusal is recorded for the audit and handed to nobody."""
    attempted: list[str] = []

    class RecordingTransport:
        name = "recording-double"

        def send(self, *, recipient, subject, body):
            attempted.append(recipient)
            return "receipt-1"

    routes._STATE["notifier"] = collab.Notifier(
        policy=collab.RecipientPolicy(
            allowed=frozenset({"uat@creditprobe.test"}),
            label="the single UAT mailbox"),
        transport=RecordingTransport())
    try:
        saved = client.post(f"{P}/saved-analyses",
                            json={"run_id": _answered(drive).run_id}).json()
        outside = client.post(
            f"{P}/shares",
            json={"subject_kind": collab.SAVED_ANALYSIS,
                  "subject_id": saved["saved_id"], "audience_id": "x",
                  "notify_email": "someone@a-real-company.com"}).json()
        assert outside["notification"]["state"] == collab.REFUSED
        assert outside["notification"]["delivered"] is False
        assert attempted == [], (
            "an unauthorized address was handed to a transport")

        inside = client.post(
            f"{P}/shares",
            json={"subject_kind": collab.SAVED_ANALYSIS,
                  "subject_id": saved["saved_id"], "audience_id": "x",
                  "message": "Review this.",
                  "notify_email": "uat@creditprobe.test"}).json()
        assert attempted == ["uat@creditprobe.test"]
        assert inside["notification"]["state"] == collab.SENT
        assert inside["notification"]["delivered"] is True
        assert inside["notification"]["transport"] == "recording-double", (
            "the row must name the transport, so an audit can tell a test "
            "double from a real channel")
    finally:
        routes._STATE.pop("notifier", None)


def test_a_transport_that_refuses_is_recorded_as_failed_not_as_sent():
    class BrokenTransport:
        name = "broken"

        def send(self, *, recipient, subject, body):
            raise RuntimeError("relay closed the connection")

    class Recorder:
        def __init__(self):
            self.rows = []

        def put_notification(self, **kwargs):
            self.rows.append(kwargs)
            return {**kwargs, "delivered": kwargs["state"] == collab.SENT}

    store = Recorder()
    notifier = collab.Notifier(
        policy=collab.RecipientPolicy(allowed=frozenset({"uat@x.test"})),
        transport=BrokenTransport())
    row = notifier.notify(store, tenant_id="t", actor_id="u",
                          recipient="uat@x.test", subject="s", body="b")
    assert row["state"] == collab.FAILED
    assert row["delivered"] is False
    assert "relay closed" in row["reason"]


def test_an_empty_allow_list_permits_nobody():
    policy = collab.RecipientPolicy()
    for address in ("anyone@anywhere.com", "uat@creditprobe.test",
                    "not-an-address"):
        assert policy.permits(address) is False


def test_a_domain_allow_list_admits_that_domain_and_no_other():
    policy = collab.RecipientPolicy(
        allowed_domains=frozenset({"creditprobe.test"}))
    assert policy.permits("anyone@creditprobe.test")
    assert not policy.permits("anyone@creditprobe.test.evil.com")
    assert not policy.permits("anyone@example.com")


def test_the_outbox_is_tenant_scoped(client, drive):
    saved = client.post(f"{P}/saved-analyses",
                        json={"run_id": _answered(drive).run_id}).json()
    client.post(f"{P}/shares",
                json={"subject_kind": collab.SAVED_ANALYSIS,
                      "subject_id": saved["saved_id"], "audience_id": "x",
                      "notify_email": "a@b.test"})
    mine = client.get(f"{P}/notifications").json()["notifications"]
    theirs = client.get(f"{P}/notifications",
                        headers={"X-Test-Tenant": "other-bank"}
                        ).json()["notifications"]
    assert len(mine) == 1 and theirs == []


# ---- T01-T05: five conversations -------------------------------------
#
# Each thread is driven through the REAL worker, so what the second question
# receives is whatever the store and the context builder actually assembled.
# The assertions read the bytes that would have gone to a provider.

def _answer(text, mode="PRODUCT_HELP"):
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent(mode, "COCKPIT"), narrative=text))])


def _turn(drive, store_db, thread_record, question, text):
    """Ask a follow-up in the SAME thread and return what was sent."""
    from backend.cockpit_v4.worker import Worker

    record, _ = store_db.accept_run(
        thread_id=thread_record.thread_id, tenant_id=thread_record.tenant_id,
        principal_id=thread_record.principal_id, question=question,
        mode="standard", release_id=thread_record.release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="testsha",
        deadline_at="")
    outcome, provider, _ = drive(question, [_answer(text)], record=record)
    return outcome, provider, record


def test_t01_a_follow_up_resolves_against_the_previous_turn(drive, store_db):
    """'Show me the borrowers behind that' must not need the whole sentence."""
    _, _, first = _answer_in_new_thread(
        drive, store_db, "Which sectors had the largest ECL increase?",
        "Manufacturing rose most, by 3.07 SAR million.")
    outcome, provider, _ = _turn(
        drive, store_db, first, "Show me the borrowers behind that.",
        "Three borrowers carry the move; BRW0055 is the largest.")
    assert outcome.state == st.COMPLETED
    sent = provider.first_input_text()
    assert "Which sectors had the largest ECL increase?" in sent, (
        "the previous question must be in the thread the follow-up reads")
    assert "Manufacturing rose most" in sent, (
        "the previous ANSWER is what 'that' refers to")


def _answer_in_new_thread(drive, store_db, question, text):
    outcome, provider, record = drive(question, [_answer(text)])
    assert outcome.state == st.COMPLETED, outcome.message
    return outcome, provider, record


def test_t02_a_period_switch_keeps_the_measure_and_the_grouping(drive,
                                                                store_db):
    _, _, first = _answer_in_new_thread(
        drive, store_db, "Stage 2 EAD by sector for the latest quarter?",
        "Manufacturing 218.97 and Real Estate 53.11 SAR million.")
    _, provider, _ = _turn(drive, store_db, first, "And a year ago?",
                           "A year ago the same measure stood lower.")
    sent = provider.first_input_text()
    assert "Stage 2 EAD by sector" in sent
    assert "And a year ago?" in sent, (
        "the new question must reach the analyst unmodified")


def test_t03_a_thread_seeded_from_an_attention_card_carries_the_segment(
        drive, store_db, release_id):
    """The seed is on the THREAD, so every turn in it has the subject."""
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.set_thread_context(thread_id, tenant_id="demo-tenant",
                                kind="attention_item", body={
                                    "segment": "Manufacturing",
                                    "reporting_quarter": "2026Q2",
                                    "comparison_quarter": "2026Q1",
                                    "metric": "ecl_reported",
                                    "headline": "ECL rose in Manufacturing"})
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question="Show me the customers behind this.", mode="standard",
        release_id=release_id, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="testsha",
        deadline_at="")
    outcome, provider, _ = drive("Show me the customers behind this.",
                                 [_answer("Three borrowers carry the move.")],
                                 record=record)
    assert outcome.state == st.COMPLETED, outcome.message
    sent = provider.first_input_text()
    assert "ACTIVE INVESTIGATION" in sent
    assert "Manufacturing" in sent and "2026Q2" in sent
    assert "NOT an answer" in sent or "not an instruction" in sent.lower(), (
        "the seed must be labelled as recorded facts, not as an answer")


def test_t04_a_new_subject_does_not_inherit_the_old_ones_conclusion(drive,
                                                                    store_db):
    """History is offered as record, never as an answer to reuse."""
    _, _, first = _answer_in_new_thread(
        drive, store_db, "Which sectors had the largest ECL increase?",
        "Manufacturing rose most.")
    _, provider, _ = _turn(
        drive, store_db, first,
        "Separately, what is CreditProbe's What-If module for?",
        "What-If owns scenario analysis.")
    sent = provider.first_input_text()
    assert "outrank any summary" in sent, (
        "the turns must be labelled as exact records")
    assert "Separately, what is CreditProbe's What-If module for?" in sent


def test_t05_a_thread_reopens_with_its_real_turns_and_no_invented_ones(
        client, drive, store_db):
    _, _, first = _answer_in_new_thread(
        drive, store_db, "Which sectors had the largest ECL increase?",
        "Manufacturing rose most.")
    _turn(drive, store_db, first, "Show me the borrowers behind that.",
          "BRW0055 is the largest.")

    reopened = client.get(f"{P}/threads/{first.thread_id}")
    assert reopened.status_code == 200
    turns = reopened.json()["turns"]
    assert [t["question"] for t in turns] == [
        "Which sectors had the largest ECL increase?",
        "Show me the borrowers behind that."]
    assert all(t["answer"] for t in turns), (
        "a reopened thread shows the answers that were published, not "
        "placeholders")

    stranger = client.get(f"{P}/threads/{first.thread_id}",
                          headers={"X-Test-Tenant": "other-bank"})
    assert stranger.status_code == 404


def test_the_landing_page_can_reopen_only_this_principals_threads(client,
                                                                  drive,
                                                                  store_db):
    _answer_in_new_thread(drive, store_db, "Who are you?", "The Cockpit.")
    mine = client.get(f"{P}/session").json()
    assert mine["recent_threads"], "a real thread should be reopenable"
    theirs = client.get(f"{P}/session",
                        headers={"X-Test-Tenant": "other-bank"}).json()
    assert theirs["recent_threads"] == []
