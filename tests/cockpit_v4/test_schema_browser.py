"""REAL CATALOGUE · NO MODEL · REAL API.

§15, §16, §17. Two books to browse, and the columns behind each one.

The schema browser is what makes the dual-domain model visible without
asking a question: two cards, two releases, two fingerprints, two sets of
relations. The case that matters is the last one -- a relation belonging to
the other book is refused by name here too, because a browser that quietly
showed it would be teaching a reader something false about what a thread in
this book can reach.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_each_book_lists_its_own_relations_with_their_sizes(client,
                                                            domain_id):
    body = client.get(f"{P}/schema", params={"domain": domain_id}).json()
    assert body["domain_id"] == domain_id
    assert body["release_id"] == dom.DEFAULT_RELEASES[domain_id]
    assert len(body["release_fingerprint"]) == 64
    assert body["reporting_currency"] == "SAR"
    assert body["amount_scale"] == "million"
    assert body["reporting_frequency"] == schema_mod.frequency(domain_id)
    assert len(body["reporting_periods"]) == 20

    named = {r["relation"] for r in body["relations"]}
    assert named == set(schema_mod.relation_names(domain_id))
    for relation in body["relations"]:
        assert relation["rows"] > 0, relation
        assert relation["columns"] > 0
        assert relation["grain"]
    assert body["total_rows"] == sum(r["rows"] for r in body["relations"])
    assert body["joins"]


def test_the_two_books_are_two_releases_not_two_views(client):
    corporate = client.get(f"{P}/schema",
                           params={"domain": dom.CORPORATE}).json()
    retail = client.get(f"{P}/schema",
                        params={"domain": dom.RETAIL}).json()
    assert corporate["release_id"] != retail["release_id"]
    assert corporate["release_fingerprint"] != retail["release_fingerprint"]
    assert not ({r["relation"] for r in corporate["relations"]}
                & {r["relation"] for r in retail["relations"]})
    assert corporate["total_rows"] != retail["total_rows"]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_relation_drills_into_its_columns(client, domain_id):
    relation = schema_mod.relation_names(domain_id)[1]
    body = client.get(f"{P}/schema",
                      params={"domain": domain_id,
                              "relation": relation}).json()
    assert body["relation"] == relation
    assert body["grain"] and body["description"]
    assert body["period_column"] == schema_mod.period_column(domain_id)
    assert body["key_columns"]
    spec = schema_mod.relation(domain_id, relation)
    assert [f["name"] for f in body["fields"]] == list(spec.columns)
    for field in body["fields"]:
        assert field["dtype"]
        assert field["definition"], field
        assert field["aggregation"] in ("additive", "not_additive")
    amounts = [f for f in body["fields"] if f["unit"] == "rcy"]
    assert all(f["aggregation"] == "additive" for f in amounts)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_other_books_relation_is_refused_by_name(client, domain_id):
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    theirs = schema_mod.relation_names(other)[0]
    response = client.get(f"{P}/schema",
                          params={"domain": domain_id, "relation": theirs})
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert dom.LABELS[other] in detail["message"]
    assert dom.LABELS[domain_id] in detail["message"]
    assert detail["relation"] == theirs


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_relation_that_exists_nowhere_is_a_not_found(client, domain_id):
    response = client.get(f"{P}/schema",
                          params={"domain": domain_id,
                                  "relation": "ledger_entries"})
    assert response.status_code == 404
    assert dom.LABELS[domain_id] in response.json()["detail"]["message"]


def test_an_unknown_book_is_refused(client):
    response = client.get(f"{P}/schema", params={"domain": "treasury"})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_browser_says_this_is_not_client_data(client, domain_id):
    body = client.get(f"{P}/schema", params={"domain": domain_id}).json()
    assert "no real" in str(body["not_client_data"]).lower()
    assert body["geography_name"] == "Saudi Arabia"
