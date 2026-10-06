"""REAL DATABASE · NO MODEL · in-process ASGI client.

The switch, through the API. Two books, two dashboards, one pinned thread.

What these prove that the engine tests do not
---------------------------------------------
The engine tests show that two feeds CAN be computed differently. These show
that asking the API for one book gets that book -- that the switch reaches
the data rather than relabelling a page -- and that a conversation opened in
one book cannot be dragged into the other by a later request.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import attention_v2, lake, routes
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod

from . import domain_oracles as oracle

P = "/api/v1/cockpit-v4"


@pytest.fixture
def client(store_db, runtime):
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    attention_v2.clear_cache()
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


# ---- the runtime knows both books --------------------------------------

def test_both_domains_are_listed_with_their_own_readiness(client):
    body = client.get(f"{P}/domains").json()
    ids = {d["domain_id"] for d in body["domains"]}
    assert ids == set(dom.DOMAIN_IDS)
    assert body["default_domain"] == dom.CORPORATE
    for entry in body["domains"]:
        assert entry["ready"] is True
        assert entry["release_id"] == dom.DEFAULT_RELEASES[entry["domain_id"]]
        # Each book publishes 20 periods of ITS OWN kind: the Corporate
        # book 20 quarters, the Retail book 20 months. §2, §3.
        assert entry["latest_period"] == oracle.latest_period(
            entry["domain_id"])
        assert len(entry["periods"]) == 20
        assert entry["reporting_frequency"] == schema_mod.frequency(
            entry["domain_id"])
        assert entry["reporting_currency"] == "SAR"
        assert entry["amount_scale"] == "million"


# ---- the switch reaches the data ---------------------------------------

def test_asking_for_a_domain_returns_that_domains_dashboard(client):
    corporate = client.get(f"{P}/attention?domain=corporate").json()
    retail = client.get(f"{P}/attention?domain=retail").json()

    assert corporate["domain_id"] == dom.CORPORATE
    assert retail["domain_id"] == dom.RETAIL
    assert corporate["release_id"] != retail["release_id"]
    assert corporate["release_fingerprint"] != retail["release_fingerprint"]


def test_the_two_dashboards_share_no_card(client):
    """§26. Switching changes the DATA, not a label over one dataset."""
    corporate = client.get(f"{P}/attention?domain=corporate").json()
    retail = client.get(f"{P}/attention?domain=retail").json()

    corporate_ids = {i["item_id"]
                     for i in corporate["segments_requiring_attention"]}
    retail_ids = {i["item_id"]
                  for i in retail["segments_requiring_attention"]}
    assert corporate_ids and retail_ids
    assert not corporate_ids & retail_ids

    corporate_highlights = {h["headline"] for h in corporate["ecl_highlights"]}
    retail_highlights = {h["headline"] for h in retail["ecl_highlights"]}
    assert not corporate_highlights & retail_highlights


def test_each_dashboard_names_itself(client):
    corporate = client.get(f"{P}/attention?domain=corporate").json()
    retail = client.get(f"{P}/attention?domain=retail").json()
    assert corporate["attention_label"] == "Segments requiring attention"
    assert retail["attention_label"] == "Retail portfolio requiring attention"


def test_no_domain_named_means_the_default_book(client):
    assert client.get(f"{P}/attention").json()["domain_id"] == \
        dom.DEFAULT_DOMAIN


def test_an_unknown_domain_is_refused_not_defaulted(client):
    response = client.get(f"{P}/attention?domain=wholesale")
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


def test_rendering_a_dashboard_costs_no_model_call(client):
    for domain_id in dom.DOMAIN_IDS:
        body = client.get(f"{P}/attention?domain={domain_id}").json()
        assert body["model_calls"] == 0


# ---- a thread belongs to one book --------------------------------------

def test_a_new_thread_is_pinned_to_the_domain_it_was_opened_in(client):
    started = client.post(f"{P}/runs", json={
        "question": "What is EAD by sector for the latest month?",
        "mode": "standard", "domain": "corporate"})
    assert started.status_code == 202, started.text
    thread_id = started.json()["thread_id"]

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["domain_id"] == dom.CORPORATE
    assert body["domain_label"] == "Corporate Credit"
    assert body["domain_short_label"] == "Corporate"
    assert body["release_id"] == dom.DEFAULT_RELEASES[dom.CORPORATE]
    assert body["release_fingerprint"]


def test_a_retail_thread_carries_its_book_on_the_transcript(client,
                                                            store_db):
    """Pinning is a property of the thread, not of what can be asked in it."""
    thread_id = _retail_thread(store_db)
    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["domain_id"] == dom.RETAIL
    assert body["domain_label"] == "Retail Credit"
    assert body["domain_short_label"] == "Retail"
    assert body["release_id"] == dom.DEFAULT_RELEASES[dom.RETAIL]


def test_a_thread_with_no_domain_named_opens_in_the_default_book(client):
    started = client.post(f"{P}/runs", json={
        "question": "What is EAD by sector?", "mode": "standard"})
    thread_id = started.json()["thread_id"]
    assert client.get(f"{P}/threads/{thread_id}").json()["domain_id"] == \
        dom.DEFAULT_DOMAIN


def _retail_thread(store_db):
    """A settled retail conversation, so a follow-up is not refused as busy."""
    return store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.RETAIL, release_id=dom.DEFAULT_RELEASES[dom.RETAIL],
        release_fingerprint="fp")


def _corporate_thread(store_db):
    return store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.CORPORATE,
        release_id=dom.DEFAULT_RELEASES[dom.CORPORATE],
        release_fingerprint="fp")


def test_a_follow_up_in_the_same_book_is_accepted(client, store_db):
    thread_id = _corporate_thread(store_db)
    again = client.post(f"{P}/runs", json={
        "question": "Which sectors drove that?", "mode": "standard",
        "thread_id": thread_id, "domain": "corporate"})
    assert again.status_code == 202, again.text
    assert again.json()["thread_id"] == thread_id


def test_a_follow_up_saying_nothing_inherits_the_threads_book(client,
                                                              store_db):
    thread_id = _corporate_thread(store_db)
    again = client.post(f"{P}/runs", json={
        "question": "And the borrowers behind it?", "mode": "standard",
        "thread_id": thread_id})
    assert again.status_code == 202, again.text
    assert client.get(f"{P}/threads/{thread_id}").json()["domain_id"] == \
        dom.CORPORATE


def test_a_follow_up_cannot_drag_a_thread_into_the_other_book(client,
                                                              store_db):
    """§4. Not silently obeyed, not silently overruled. Refused, with an offer."""
    thread_id = _retail_thread(store_db)

    refused = client.post(f"{P}/runs", json={
        "question": "Now show corporate sectors.", "mode": "standard",
        "thread_id": thread_id, "domain": "corporate"})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error_code"] == "DOMAIN_PINNED"
    assert detail["thread_domain"] == dom.RETAIL
    assert detail["requested_domain"] == dom.CORPORATE
    assert detail["action"]["domain"] == dom.CORPORATE
    assert "Start a Corporate conversation" == detail["action"]["label"]
    # And the thread is untouched: no turn was added, no domain was changed.
    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["domain_id"] == dom.RETAIL


def test_the_offered_action_really_opens_a_thread_in_the_other_book(
        client, store_db):
    """The refusal is only honest if the way out works."""
    thread_id = _retail_thread(store_db)
    refused = client.post(f"{P}/runs", json={
        "question": "Now show corporate sectors.", "mode": "standard",
        "thread_id": thread_id, "domain": "corporate"})
    assert refused.status_code == 409, refused.text
    offered = refused.json()["detail"]["action"]["domain"]

    opened = client.post(f"{P}/runs", json={
        "question": "Now show corporate sectors.", "mode": "standard",
        "domain": offered})
    assert opened.status_code == 202
    assert opened.json()["thread_id"] != thread_id
    assert client.get(
        f"{P}/threads/{opened.json()['thread_id']}").json()["domain_id"] == \
        dom.CORPORATE


# ---- both books can be asked, and only while they can be opened --------

def test_both_books_accept_a_question(client):
    """§9. The temporary Retail refusal is gone because Retail executes.

    The proof that it executes is `test_domain_execution.py`, which runs
    retail SQL in a retail session and checks every figure against a pandas
    oracle. This is the acceptance half: a retail question is taken, and it
    opens a retail thread rather than a corporate one.
    """
    for domain_id, question in (
            (dom.CORPORATE, "What is EAD by sector for the latest month?"),
            (dom.RETAIL, "Which products saw the largest Stage 2 increase?")):
        accepted = client.post(f"{P}/runs", json={
            "question": question, "mode": "standard", "domain": domain_id})
        assert accepted.status_code == 202, accepted.text
        thread_id = accepted.json()["thread_id"]
        assert client.get(f"{P}/threads/{thread_id}").json()["domain_id"] == \
            domain_id


def test_a_book_whose_runtime_cannot_be_opened_is_refused_before_paying(
        client, monkeypatch):
    """§5, §9, §55. Askable is established per request, never assumed.

    Retail is wired, so the refusal is no longer a statement about the
    build. It is still a statement about the RUNTIME: a deployment where the
    retail release cannot be opened must refuse the question at acceptance
    -- before a model call is paid for and before a thread is left holding a
    question nothing can answer -- rather than reach for the other book.

    The failure is injected where it really happens: opening the book.
    """
    from backend.cockpit_v4 import analytical_runtime as arun
    from backend.cockpit_v4 import domain_resolver as resolver

    real = arun.for_domain

    def refuse_retail(domain_id, **kwargs):
        if domain_id == dom.RETAIL:
            raise arun.AnalyticalRuntimeUnavailable(
                "release 'v4-saudi-retail-20m-v1' is not published.")
        return real(domain_id, **kwargs)

    monkeypatch.setattr(arun, "for_domain", refuse_retail)

    refused = client.post(f"{P}/runs", json={
        "question": "Which products saw the largest Stage 2 increase?",
        "mode": "standard", "domain": "retail"})
    assert refused.status_code == 503, refused.text
    detail = refused.json()["detail"]
    assert detail["domain_id"] == dom.RETAIL
    assert detail["analysis_domains"] == [dom.CORPORATE]
    assert "answer from the wrong book" in detail["message"]
    assert "Data Builder" in detail["message"]
    assert "seed_domains.py" in detail["provision_command"]
    assert resolver.analysis_supported(dom.CORPORATE), (
        "one book being unopenable must not disable the other")

    # And the corporate question is still taken, in the same runtime.
    assert client.post(f"{P}/runs", json={
        "question": "What is EAD by sector for the latest month?",
        "mode": "standard", "domain": "corporate"}).status_code == 202


def test_the_switch_is_told_which_books_can_be_asked(client):
    """A control that offers a book must not promise an answer it cannot give."""
    body = client.get(f"{P}/domains").json()
    assert body["analysis_domains"] == [dom.CORPORATE, dom.RETAIL]
    by_id = {d["domain_id"]: d for d in body["domains"]}
    for domain_id in dom.DOMAIN_IDS:
        assert by_id[domain_id]["ready"] is True
        assert by_id[domain_id]["analysis_ready"] is True


# ---- the ECL panel, per book -------------------------------------------

def test_each_book_serves_its_own_ecl_panel(client):
    """§23, §24. Where the loss is and what moved it, computed per book."""
    seen = {}
    for domain_id in dom.DOMAIN_IDS:
        response = client.get(f"{P}/ecl", params={"domain": domain_id})
        assert response.status_code == 200, response.text
        body = response.json()
        profile, decomposition = body["profile"], body["decomposition"]
        assert profile["domain_id"] == domain_id
        assert decomposition["domain_id"] == domain_id
        assert profile["release_id"] == decomposition["release_id"]
        assert profile["release_fingerprint"] == \
            decomposition["release_fingerprint"]
        assert decomposition["reconciles"] is True
        assert abs(decomposition["residual"]) < 1e-6
        assert decomposition["model_calls"] == 0
        assert profile["model_calls"] == 0
        assert len(profile["stages"]) == 3
        seen[domain_id] = body

    corporate = seen[dom.CORPORATE]["profile"]
    retail = seen[dom.RETAIL]["profile"]
    assert corporate["release_id"] != retail["release_id"]
    assert corporate["total_ecl"] != retail["total_ecl"]
    assert corporate["relation"] != retail["relation"]
    assert corporate["exposure_grain"] == "facility"
    assert retail["exposure_grain"] == "account"


def test_the_ecl_panel_refuses_an_unknown_book(client):
    response = client.get(f"{P}/ecl", params={"domain": "treasury"})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


# ---- Investigate Further works from EVERY card -------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_card_on_the_page_can_be_investigated(client, domain_id):
    """§13. The drawer offers it on every card, so every card must support it.

    The ECL highlights were a short dict with a headline and a number while
    the route that opens a seeded thread read `segment`, `metric`,
    `what_changed`, `movement`, `key_numbers` and `evidence` off the item.
    Investigating one raised a KeyError and returned a 500 -- on the four
    cards a reader is most likely to click.
    """
    feed = client.get(f"{P}/attention",
                      params={"domain": domain_id}).json()
    cards = (feed["segments_requiring_attention"] + feed["ecl_highlights"])
    assert len(cards) >= 5
    for card in cards:
        opened = client.post(f"{P}/attention/{card['item_id']}/investigate")
        assert opened.status_code == 201, (
            f"{card['section']} card {card['headline']!r}: {opened.text}")
        body = opened.json()
        seed = body["seed"]
        assert seed["domain_id"] == domain_id
        assert seed["release_id"] == feed["release_id"]
        assert seed["release_fingerprint"] == feed["release_fingerprint"]
        assert seed["reporting_period"] == feed["reporting_period"]
        assert body["suggested_questions"], (
            "a seeded thread with nothing to ask next is a dead end")
        thread = client.get(f"{P}/threads/{body['thread_id']}").json()
        assert thread["domain_id"] == domain_id
