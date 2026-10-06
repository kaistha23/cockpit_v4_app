"""REAL DATABASE · REAL ROUTES · REAL WORKER · EVERY PATH.

§1, §17, §18. Two analytical books, and no silent cross-domain fallback on
any surface.

Why an AUDIT rather than a test per feature
-------------------------------------------
Every previous round fixed the leak it was shown. The dashboard leaked, and
the dashboard was fixed. Investigate Further opened the wrong book, and
Investigate Further was fixed. The export named the wrong period, and the
export was fixed. Each fix was correct and none of them prevented the next
one, because a leak is not a property of a feature -- it is a property of any
path that takes a domain from somewhere other than the thread.

So this enumerates the SURFACES and checks the rule on each: every path that
serves a book must serve the book it was asked for, must refuse the other
book by name rather than answering from the one in front of it, and must not
carry the other book's release, relations, periods or vocabulary.

A route added later without a row here is what the last assertion is for.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import values as val_mod

P = "/api/v1/cockpit-v4"


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def other(domain_id: str) -> str:
    return next(d for d in dom.DOMAIN_IDS if d != domain_id)


def foreign_words(domain_id: str) -> tuple[str, ...]:
    """Everything that belongs to the OTHER book and to nothing else.

    Its release, its relations, its periods, and the governed values that
    exist only there. Not its COLUMN names -- `region` and `collateral_type`
    are honestly in both books -- and not a shared word like `Riyadh`.
    """
    them = other(domain_id)
    words = [dom.DEFAULT_RELEASES[them], lake.fingerprint(
        dom.DEFAULT_RELEASES[them])[:16]]
    words += list(schema_mod.relation_names(them))
    mine = set(resolver.scope_for(domain_id).periods)
    words += [p for p in resolver.scope_for(them).periods if p not in mine]
    return tuple(words)


def assert_own_book(body, domain_id: str, where: str) -> None:
    blob = json.dumps(body, default=str)
    for word in foreign_words(domain_id):
        assert word not in blob, f"{where} leaked {word!r} into {domain_id}"
    assert dom.DEFAULT_RELEASES[domain_id] in blob or "release" not in blob, (
        f"{where} does not name the release it read")


# ---- the surfaces, one row each -----------------------------------------

#: Every GET that serves a book, and how to ask it for one.
#:
#: Written out rather than discovered so that ADDING a surface is a visible
#: act: the last test in this file fails when a route serves a domain and is
#: not listed here, which is the only way an audit stays an audit.
BOOK_SURFACES = (
    ("domains", lambda c, d: c.get(f"{P}/domains")),
    ("attention", lambda c, d: c.get(f"{P}/attention", params={"domain": d})),
    ("ecl", lambda c, d: c.get(f"{P}/ecl", params={"domain": d})),
    ("schema", lambda c, d: c.get(f"{P}/schema", params={"domain": d})),
)


@pytest.mark.parametrize("name,call", BOOK_SURFACES,
                         ids=[n for n, _ in BOOK_SURFACES])
@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_surface_serves_the_book_it_was_asked_for(client, name, call,
                                                        domain_id):
    response = call(client, domain_id)
    assert response.status_code == 200, (name, response.text)
    body = response.json()
    if name == "domains":
        # The listing is about BOTH books by construction; what must hold is
        # that each entry names its own release and no other.
        for entry in body["domains"]:
            assert entry["release_id"] == dom.DEFAULT_RELEASES[
                entry["domain_id"]]
        return
    assert body["domain_id"] == domain_id, name
    assert body["release_id"] == dom.DEFAULT_RELEASES[domain_id], name
    assert_own_book(body, domain_id, name)


@pytest.mark.parametrize("name,call", BOOK_SURFACES,
                         ids=[n for n, _ in BOOK_SURFACES])
def test_no_surface_serves_one_book_from_the_others_answer(client, name,
                                                           call):
    """Two calls, two different answers. A surface that cached on something
    other than the book would serve the first caller's book to the second,
    and both would look right on their own."""
    if name == "domains":
        pytest.skip("the listing is about both books by construction")
    first = call(client, dom.CORPORATE).json()
    second = call(client, dom.RETAIL).json()
    assert first != second
    assert first["domain_id"] == dom.CORPORATE
    assert second["domain_id"] == dom.RETAIL
    # And in the other order, from a cold cache.
    att.clear_cache()
    arun.reset()
    again_retail = call(client, dom.RETAIL).json()
    again_corporate = call(client, dom.CORPORATE).json()
    assert again_retail["release_id"] == second["release_id"]
    assert again_corporate["release_id"] == first["release_id"]


@pytest.mark.parametrize("name,call", BOOK_SURFACES,
                         ids=[n for n, _ in BOOK_SURFACES])
def test_an_unknown_book_is_refused_and_never_defaulted(client, name, call):
    if name == "domains":
        pytest.skip("the listing takes no domain")
    response = call(client, "wholesale")
    assert response.status_code == 400, (name, response.text)
    assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


# ---- the thread is the compartment --------------------------------------

def test_a_thread_keeps_its_book_and_refuses_the_other_by_name(client):
    """§18. Not silently switched, and not silently obeyed: refused, with
    the book it IS named and an offer to start a new conversation."""
    thread_id = client.post(f"{P}/threads",
                            json={"domain": dom.CORPORATE}
                            ).json()["thread_id"]
    accepted = client.post(f"{P}/runs", json={
        "question": "EAD by sector", "mode": "standard",
        "thread_id": thread_id, "domain": dom.CORPORATE})
    assert accepted.status_code == 202, accepted.text

    refused = client.post(f"{P}/runs", json={
        "question": "EAD by product", "mode": "standard",
        "thread_id": thread_id, "domain": dom.RETAIL})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error_code"] == "DOMAIN_PINNED"
    assert detail["thread_domain"] == dom.CORPORATE
    assert detail["requested_domain"] == dom.RETAIL
    # And it says what to do instead, rather than only what went wrong.
    assert detail["action"]["domain"] == dom.RETAIL
    assert dom.SHORT_LABELS[dom.RETAIL] in detail["action"]["label"]


def test_a_thread_with_no_domain_named_stays_in_the_one_it_opened_in(client):
    """Omitting the domain on a follow-up is not a request to change it."""
    for domain_id in dom.DOMAIN_IDS:
        thread_id = client.post(f"{P}/threads",
                                json={"domain": domain_id}
                                ).json()["thread_id"]
        body = client.post(f"{P}/runs", json={
            "question": "what moved?", "mode": "standard",
            "thread_id": thread_id}).json()
        assert body["domain_id"] == domain_id
        assert body["release_id"] == dom.DEFAULT_RELEASES[domain_id]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_an_investigation_opens_in_the_book_the_card_came_from(client,
                                                               domain_id):
    feed = client.get(f"{P}/attention", params={"domain": domain_id}).json()
    cards = (feed["segments_requiring_attention"] + feed["ecl_highlights"])
    assert cards
    for card in cards:
        opened = client.post(
            f"{P}/attention/{card['item_id']}/investigate")
        assert opened.status_code == 201, opened.text
        seed = opened.json()["seed"]
        assert seed["domain_id"] == domain_id, card["item_id"]
        assert_own_book(seed, domain_id, f"seed:{card['item_id']}")

        # And the thread it opened is pinned to that book, so the next
        # question in it cannot be answered from the other one.
        refused = client.post(f"{P}/runs", json={
            "question": "and the other book?", "mode": "standard",
            "thread_id": opened.json()["thread_id"],
            "domain": other(domain_id)})
        assert refused.status_code == 409, card["item_id"]


# ---- the vocabulary is compartmented too --------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_resolver_never_matches_the_other_books_vocabulary(domain_id):
    """§17. `project_finance` is a Corporate product and `Credit Card` is a
    Retail one. Each book resolves its own and NOTHING of the other's."""
    runtime = arun.for_domain(domain_id)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    mine = {v for dim in index.values() for v in dim.values}

    theirs = arun.for_domain(other(domain_id))
    their_index = val_mod.dimensions(session=theirs.session,
                                     catalog=theirs.catalog)
    exclusive = {v for dim in their_index.values() for v in dim.values} - mine

    for value in sorted(exclusive):
        for resolved in val_mod.phrases(f"what about {value}?", index=index):
            assert getattr(resolved, "value", "") != value, (
                f"the {domain_id} book resolved {value!r}, which only the "
                f"other book has")


def test_a_phrase_only_the_other_book_has_is_not_turned_into_a_question():
    """§17, §39. Asked of the Retail book, "and within project finance?"
    used to resolve the single word `finance` and ask "did you mean Auto
    Finance or Personal Finance?" -- a question about something the reader
    did not say, built by discarding the word that made their phrase
    unambiguous.

    The Retail book has no project finance. Saying nothing about it is the
    honest answer; asking is worse than silence because it invites the
    reader to pick one.
    """
    runtime = arun.for_domain(dom.RETAIL)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    assert val_mod.phrases("and within project finance?", index=index) == []

    # The Corporate book, where the phrase IS a value, resolves it exactly.
    corporate = arun.for_domain(dom.CORPORATE)
    corp_index = val_mod.dimensions(session=corporate.session,
                                    catalog=corporate.catalog)
    resolved = val_mod.phrases("and within project finance?",
                               index=corp_index)
    assert len(resolved) == 1
    assert resolved[0].field_name == "product_type"
    assert resolved[0].value == "project_finance"
    assert resolved[0].raw == "project finance", (
        "the trace must quote what the reader wrote")


# ---- §38-§41: labels, values and the cross-dimensional drilldown --------

def test_a_product_is_not_a_sector_and_the_drilldown_says_so(client):
    """§38, §39. "EAD by sector" need not show Project Finance -- a product
    is not a sector. The follow-up "and within project finance?" adds a
    FILTER on `product_type`; it does not re-read the sector column.
    """
    runtime = arun.for_domain(dom.CORPORATE)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)

    sectors = set(index["sector"].values)
    products = set(index["product_type"].values)
    assert not sectors & products, (
        "a value that is both a sector and a product makes the two "
        "indistinguishable in a filter")
    assert "project_finance" in products
    assert "project_finance" not in sectors

    resolved = val_mod.phrases("and within prject finance?", index=index)[0]
    assert resolved.field_name == "product_type"
    assert resolved.value == "project_finance"
    assert resolved.relation == "corp_facility_quarter"
    # A typo is resolved, not escalated: §39 says obvious misspellings do
    # not become clarifying questions.
    assert resolved.raw == "prject finance"
    assert "project_finance" in resolved.as_assumption()


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_governed_value_is_shown_as_a_label_and_filtered_as_itself(
        client, domain_id):
    """§41. `project_finance` is what SQL filters on; `Project Finance` is
    what a person reads. The Data Builder shows both, the dashboard shows
    the label, and no surface shows `asset_finance:` raw in a headline."""
    feed = client.get(f"{P}/attention", params={"domain": domain_id}).json()
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    assert cards
    for card in cards:
        assert card["segment_label"] == val_mod.pretty(card["segment"])
        # The headline a reader sees carries the LABEL, never the
        # identifier: an `asset_finance:` in a sentence reached a live
        # screen, and it reads as a bug to everyone who sees it.
        if "_" in str(card["segment"]):
            assert card["segment"] not in card["headline"], card["item_id"]
            assert card["segment_label"] in card["headline"], card["item_id"]


# ---- the audit stays an audit -------------------------------------------

def test_every_route_that_takes_a_domain_is_listed_in_this_file():
    """A surface added without a row here is a surface nobody audited."""
    import inspect

    listed = {name for name, _ in BOOK_SURFACES}
    # Routes that take a `domain` query parameter are book surfaces by
    # definition. Anything else reaches a book through a run or a thread,
    # which the pinning tests above cover.
    takes_domain = set()
    for route in routes.router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        try:
            signature = inspect.signature(endpoint)
        except (TypeError, ValueError):
            continue
        if "domain" not in signature.parameters:
            continue
        takes_domain.add(str(route.path).rsplit("/", 1)[-1])

    missing = takes_domain - listed - {"attention-legacy"}
    assert not missing, (
        f"these routes serve a book and are not audited here: "
        f"{sorted(missing)}. Add a row to BOOK_SURFACES.")


# ---- §51: reopening a conversation reopens its book ---------------------

def test_a_reopenable_thread_names_the_book_it_is_in(client, store_db):
    """§18, §51. "Continue where you left off" lists conversations, and a
    conversation belongs to ONE book. A row without its domain sends the
    reader into whatever the switch happened to be showing, and their next
    question in the thread is refused for a reason that has nothing to do
    with what they typed -- the thread's domain wins, correctly, and the
    refusal is the first they hear of it.
    """
    opened = {}
    for domain_id in dom.DOMAIN_IDS:
        thread_id = client.post(f"{P}/threads",
                                json={"domain": domain_id}
                                ).json()["thread_id"]
        store_db.append_turn(
            thread_id=thread_id, run_id=f"run-{domain_id}",
            question="what moved?",
            answer={"disposition": "answer", "narrative": "x"})
        opened[domain_id] = thread_id

    listed = {row["thread_id"]: row
              for row in client.get(f"{P}/session").json()["recent_threads"]}
    for domain_id, thread_id in opened.items():
        assert thread_id in listed, domain_id
        row = listed[thread_id]
        assert row["domain_id"] == domain_id, row
        assert row["release_id"] == dom.DEFAULT_RELEASES[domain_id], row


def test_the_home_page_switches_book_when_a_thread_is_reopened():
    """The rule as the page implements it. A component cannot be rendered
    from here, so this reads the source: the handler must consult the
    thread's own domain rather than the one on screen."""
    from pathlib import Path

    home = (Path(__file__).resolve().parents[2] / "frontend" / "src"
            / "components" / "cockpit-v4" / "cockpit-v4-home.tsx").read_text()
    reopen = home.split("const reopen", 1)[1].split("openThread(", 1)[0]
    assert "thread.domain_id" in reopen, (
        "reopening a conversation must honour the book it is in")
    assert "setDomain" in reopen and "rememberDomain" in reopen
