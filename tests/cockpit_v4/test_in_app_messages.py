"""UNIT · REAL DATABASE/RUNNER · REAL API. No model call.

Sending an analysis to a colleague, inside CreditProbe.

The defect this exists for
--------------------------
"Share this analysis" recorded an intention and the colleague was never
told. `Notifier.notify` wrote the outbox row and stopped, because no
transport was configured -- which is the default and the state of every
build that has not been handed a mail relay. The wording was honest about
it (`delivered: false`, with the reason), and it was still a feature that
did not work.

It also had no inbox. `GET /notifications` is the tenant's whole OUTBOX,
without the message bodies, which is what an operator wants and the
opposite of what a person wants. A reader who had been sent an analysis
had no route that would show it to them.

And the share panel asked for a free-text `audience_id` -- a field a
reader has to already know the answer to.

What is pinned here

  a message inside the bank is DELIVERED   no relay is needed to reach
                                           somebody who already has an
                                           account on this tenant
  a mail build with no relay still cannot  the two channels are separate
                                           fields, and one boolean cannot
                                           answer for both
  the inbox is addressed to the reader     not the tenant's whole outbox
  a team is a recipient                    `team:<id>` reaches everyone
  the wording never outruns the state      RECORDED is never worded "sent"
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import collaboration as collab
from backend.cockpit_v4 import lake

P = "/api/v1/cockpit-v4"


# ---- the recipient forms are kept apart --------------------------------

@pytest.mark.parametrize("handle", ["user:kamal.hassan", "team:corporate",
                                    "user:a", "team:risk-ops_2"])
def test_an_in_product_recipient_is_recognised(handle):
    assert collab.is_in_app(handle) is True


@pytest.mark.parametrize("not_a_handle", [
    "r.mehta@example-bank.test",     # a mailbox is the other channel
    "kamal.hassan",                  # unqualified: which is it?
    "user:",                         # nobody
    "group:everyone",                # not a kind this product has
    "user:../../etc/passwd",
    "user:has space",
])
def test_anything_else_is_not_an_in_product_recipient(not_a_handle):
    assert collab.is_in_app(not_a_handle) is False


def test_the_in_app_transport_refuses_a_mail_address():
    """Raising rather than silently succeeding. A receipt for an address
    this channel cannot reach is a message reported as delivered and
    sitting in nobody's inbox."""
    transport = collab.InAppTransport()
    with pytest.raises(ValueError):
        transport.send(recipient="r.mehta@example-bank.test",
                       subject="s", body="b")


def test_the_in_app_transport_delivers_to_a_handle():
    receipt = collab.InAppTransport().send(
        recipient="user:kamal.hassan", subject="s", body="b")
    assert "kamal.hassan" in receipt


# ---- the two channels answer separately ---------------------------------

def test_a_build_with_no_mail_relay_still_reports_it_cannot_mail():
    """THE modelling trap. A single `can_deliver` that went true the moment
    in-app delivery arrived would have quietly reported a build as able to
    mail people it cannot mail."""
    described = collab.Notifier(
        in_app_transport=collab.InAppTransport()).describe()
    assert described["can_deliver"] is False
    assert described["can_deliver_in_app"] is True


def test_the_policy_gates_mail_and_not_the_inside_of_the_product():
    """An in-app message reaches someone who already has an account on this
    tenant and can already read the analysis. The allow-list exists to stop
    a build mailing the open internet, and it still does."""
    policy = collab.RecipientPolicy()
    assert policy.permits("user:kamal.hassan") is True
    assert policy.permits("r.mehta@example-bank.test") is False


def test_a_deployment_can_still_switch_the_inside_channel_off():
    policy = collab.RecipientPolicy(in_app=False)
    assert policy.permits("user:kamal.hassan") is False


# ---- end to end ---------------------------------------------------------

@pytest.fixture
def client(store_db, runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cockpit_v4 import routes

    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "kamal.hassan", "tenant": lake.DEFAULT_TENANT,
                       "teams": ("corporate-credit",)},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def _notify(store, recipient: str, *, body: str = "Please review.",
            notifier=None):
    return (notifier or collab.Notifier(
        in_app_transport=collab.InAppTransport())).notify(
            store, tenant_id=lake.DEFAULT_TENANT, actor_id="r.mehta",
            recipient=recipient, subject="Shared with you: saved_analysis",
            body=body, subject_kind=collab.SAVED_ANALYSIS,
            subject_id="sa-1")


def test_a_message_to_a_colleague_is_actually_delivered(store_db):
    row = _notify(store_db, "user:kamal.hassan")
    assert row["state"] == collab.SENT
    assert row["delivered"] is True
    assert row["transport"] == "creditprobe"


def test_the_reader_can_read_it(client, store_db):
    _notify(store_db, "user:kamal.hassan", body="Have a look at Q2.")
    body = client.get(f"{P}/inbox").json()
    assert body["me"] == "kamal.hassan"
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    # The BODY, which the outbox listing withholds -- correctly, for an
    # outbox view, and uselessly for a person reading their own post.
    assert message["body"] == "Have a look at Q2."
    assert message["subject_id"] == "sa-1"
    assert message["delivered"] is True


def test_a_team_message_reaches_everyone_on_it(client, store_db):
    _notify(store_db, "team:corporate-credit", body="For the team.")
    messages = client.get(f"{P}/inbox").json()["messages"]
    assert [m["body"] for m in messages] == ["For the team."]


def test_somebody_elses_post_is_not_in_this_inbox(client, store_db):
    _notify(store_db, "user:someone.else")
    _notify(store_db, "team:a-team-i-am-not-on")
    assert client.get(f"{P}/inbox").json()["messages"] == []


def test_a_refused_mail_is_not_in_anybodys_inbox(client, store_db):
    """It was refused. An inbox that showed it would be an inbox showing a
    message that was never sent."""
    _notify(store_db, "r.mehta@example-bank.test")
    assert client.get(f"{P}/inbox").json()["messages"] == []


def test_sharing_sends_the_message_and_says_it_was_sent(client, store_db):
    saved = store_db.save_analysis(
        tenant_id=lake.DEFAULT_TENANT, principal_id="r.mehta",
        thread_id="th-1", run_id="run-x", title="Q2 exposure", note="",
        question="q", release_id="rel", body={})
    response = client.post(f"{P}/shares", json={
        "subject_kind": collab.SAVED_ANALYSIS,
        "subject_id": saved["saved_id"],
        "audience_id": "corporate-credit",
        "message": "Please review before Thursday.",
        "notify_in_app": "user:kamal.hassan"})
    assert response.status_code == 201, response.text
    notification = response.json()["notification"]
    assert notification["state"] == collab.SENT
    assert notification["delivered"] is True
    assert client.get(f"{P}/inbox").json()["messages"][0]["body"] == (
        "Please review before Thursday.")


def test_sharing_to_a_colleague_and_a_mailbox_reports_each_honestly(
        client, store_db):
    """One reaches its reader; the other is refused because this build has
    authorized nobody to receive mail. Neither outcome is described in the
    other's words."""
    saved = store_db.save_analysis(
        tenant_id=lake.DEFAULT_TENANT, principal_id="r.mehta",
        thread_id="th-1", run_id="run-x", title="Q2 exposure", note="",
        question="q", release_id="rel", body={})
    response = client.post(f"{P}/shares", json={
        "subject_kind": collab.SAVED_ANALYSIS,
        "subject_id": saved["saved_id"],
        "audience_id": "corporate-credit",
        "message": "Please review.",
        "notify_in_app": "user:kamal.hassan",
        "notify_email": "r.mehta@example-bank.test"})
    assert response.status_code == 201, response.text
    states = {n["recipient"]: n["state"]
              for n in response.json()["notifications"]}
    assert states["user:kamal.hassan"] == collab.SENT
    assert states["r.mehta@example-bank.test"] == collab.REFUSED


# ---- who a reader may send to -------------------------------------------

def test_the_directory_offers_people_who_have_used_this_tenant(client,
                                                               store_db):
    """The share panel asked for a free-text `audience_id`, which is a
    field a reader has to already know the answer to."""
    from test_domain_execution import make_domain_run
    from backend.cockpit_v4 import domains as dom

    make_domain_run(store_db, dom.CORPORATE, "q", principal="r.mehta")
    body = client.get(f"{P}/recipients").json()
    handles = {person["handle"] for person in body["people"]}
    assert "user:r.mehta" in handles
    # Not yourself. Sending an analysis to your own inbox is not a feature.
    assert "user:kamal.hassan" not in handles
    assert {"handle": "team:corporate-credit",
            "label": "corporate-credit"} in body["teams"]


def test_the_directory_admits_what_it_is(client):
    """Offering a list that quietly omits people is worse than one that
    says who is missing."""
    note = client.get(f"{P}/recipients").json()["note"]
    assert "not be listed" in note
