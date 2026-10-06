"""REAL API · REAL DATABASE · NO MODEL · EVERY CARD.

§6, §7, §8. Investigate Further must open a thread in the book the card
came from.

The live failure
----------------
A Retail Credit Card ECL card was opened with Investigate Further and the
answer came back: "The Credit Card ECL card sits in the retail book, and this
thread reads the corporate book only."

That is a routing defect with a large blast radius: the reader did everything
right, the seed named Retail, and the thread read Corporate. So this walks
EVERY card of EVERY family on BOTH dashboards -- not one fixture -- through
the whole flow: open the card, investigate, inspect the created thread, start
a run in it, and check the run's own book.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod

P = "/api/v1/cockpit-v4"


@pytest.fixture
def client(store_db, runtime):
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def cards(client, domain_id: str) -> list[dict]:
    feed = client.get(f"{P}/attention", params={"domain": domain_id}).json()
    assert feed["domain_id"] == domain_id
    return (feed["segments_requiring_attention"] + feed["ecl_highlights"],
            feed)


# ---- §7: every card, both books ----------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_card_opens_a_thread_in_its_own_book(client, store_db,
                                                   domain_id):
    items, feed = cards(client, domain_id)
    assert len(items) >= 5
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    seen_families = set()

    for item in items:
        opened = client.post(f"{P}/attention/{item['item_id']}/investigate")
        assert opened.status_code == 201, (
            f"{item['section']} / {item.get('family')} "
            f"{item['headline']!r}: {opened.text}")
        body = opened.json()
        seen_families.add(str(item.get("family") or item["section"]))

        # §8: the envelope, before navigation.
        seed = body["seed"]
        for key in ("domain_id", "release_id", "release_fingerprint",
                    "item_id", "reporting_period", "segment", "metric"):
            assert seed.get(key) not in (None, ""), (
                f"the seed is missing {key}: {seed}")
        assert seed["domain_id"] == domain_id
        assert seed["release_id"] == dom.DEFAULT_RELEASES[domain_id]
        assert seed["release_fingerprint"] == feed["release_fingerprint"]
        assert seed["item_id"] == item["item_id"]
        assert seed["reporting_period"] == feed["reporting_period"]

        # The THREAD, as persisted, before anything is asked in it.
        thread_id = body["thread_id"]
        pinned = store_db.thread_domain(thread_id)
        assert pinned["domain_id"] == domain_id, (
            f"a {domain_id} card opened a {pinned['domain_id']} thread")
        assert pinned["release_id"] == dom.DEFAULT_RELEASES[domain_id]
        assert pinned["release_fingerprint"] == feed["release_fingerprint"]

        transcript = client.get(f"{P}/threads/{thread_id}").json()
        assert transcript["domain_id"] == domain_id
        assert dom.LABELS[other] not in transcript.get("domain_label", "")

    # Every family on the page was walked, not one fixture.
    assert len(seen_families) >= 4, seen_families


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_run_started_in_a_seeded_thread_stays_in_that_book(client,
                                                             store_db,
                                                             domain_id):
    """Step 8 of the live flow: "Show me the customers behind this"."""
    items, _feed = cards(client, domain_id)
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    question = ("Show me the customers behind this."
                if domain_id == dom.RETAIL
                else "Show me the borrowers behind this.")

    for item in items[:4]:
        thread_id = client.post(
            f"{P}/attention/{item['item_id']}/investigate").json()["thread_id"]
        accepted = client.post(f"{P}/runs", json={
            "question": question, "mode": "standard",
            "thread_id": thread_id})
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        record = store_db.get_run(run_id)
        assert record.domain_id == domain_id, (
            f"a run in a {domain_id} seeded thread was accepted as "
            f"{record.domain_id}")
        assert record.release_id == dom.DEFAULT_RELEASES[domain_id]

        # And the book that run would actually open.
        book = arun.for_run(record, store=store_db)
        assert book.domain_id == domain_id
        assert book.release_id == dom.DEFAULT_RELEASES[domain_id]
        assert set(book.session.relations) == set(
            schema_mod.relation_names(domain_id))
        assert not (set(book.session.relations)
                    & set(schema_mod.relation_names(other)))

        # Settle it. No worker runs here, and the concurrency cap is two
        # per principal: leaving them open would make this test about the
        # cap rather than about the book.
        current = store_db.get_run(run_id)
        store_db.update_state(run_id, expect_version=current.version,
                              state="CANCELLED", terminal=True)


# ---- the shapes that could route a card to the wrong book --------------

def test_an_item_id_belongs_to_exactly_one_book(client):
    """The id is salted with the domain, so no id can be found twice."""
    everything: dict[str, str] = {}
    for domain_id in dom.DOMAIN_IDS:
        items, _feed = cards(client, domain_id)
        for item in items:
            assert item["item_id"] not in everything, (
                f"{item['item_id']} is in both "
                f"{everything.get(item['item_id'])} and {domain_id}")
            everything[item["item_id"]] = domain_id
            assert item["domain_id"] == domain_id

    for item_id, domain_id in everything.items():
        found = client.get(f"{P}/attention/{item_id}").json()
        assert found["domain_id"] == domain_id
        assert found["item"]["domain_id"] == domain_id


def test_the_corporate_dashboard_is_not_consulted_for_a_retail_card(client,
                                                                    store_db):
    """The finder walks the books in order. Order must not decide."""
    retail_items, _feed = cards(client, dom.RETAIL)
    # Warm the corporate feed first, exactly as the finder does.
    cards(client, dom.CORPORATE)
    for item in retail_items:
        opened = client.post(f"{P}/attention/{item['item_id']}/investigate")
        assert opened.status_code == 201
        assert opened.json()["seed"]["domain_id"] == dom.RETAIL
        assert store_db.thread_domain(
            opened.json()["thread_id"])["domain_id"] == dom.RETAIL


def test_an_unknown_item_is_not_routed_to_the_default_book(client):
    refused = client.post(f"{P}/attention/att-doesnotexist/investigate")
    assert refused.status_code == 404
    assert "No such attention item" in refused.json()["detail"]["message"]
