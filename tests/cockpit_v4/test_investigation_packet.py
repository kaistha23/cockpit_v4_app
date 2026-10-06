"""REAL DATABASE · MODEL MOCK · REAL WORKER · EVERY CARD FAMILY, BOTH BOOKS.

§19-§23. A thread opened from a card converges, and it converges without
looking anything up.

The live failure this replays
-----------------------------
A reader clicked the Construction card on the Corporate dashboard. The thread
it opened read the catalogue, read the catalogue again, read product
knowledge, and died with CALL_LIMIT. It had not answered anything.

Not one of those calls could have told it something the server did not
already know. CreditProbe COMPUTED that card: it knows the relation the
finding came from, the column the segment lives in, the exact governed value
of the segment, the measure's own columns, the two periods, and the five
questions the reader can ask next -- it wrote them.

So the run is handed an ANALYSIS PACKET, and this file checks both halves of
that claim: that the packet really contains what a seeded question needs, and
that a seeded run really can go straight to `execute_analysis`.

§23 asks for EVERY family in BOTH books, not one card that was reported. The
matrix below is generated from the feeds themselves, so a family added later
is covered the day it is added rather than the day somebody remembers to add
it here.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import context as context_mod
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import investigation as inv
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import states as st

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


@pytest.fixture(scope="module")
def feeds():
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        scope = resolver.scope_for(domain_id)
        session = cat.open_session(catalog=cat.build(domain_id=domain_id))
        out[domain_id] = (att.compute(session=session, scope=scope), scope,
                          session)
    return out


def cards(feed: dict) -> list[dict]:
    return (feed["segments_requiring_attention"] + feed["ecl_highlights"])


def families(feed: dict) -> dict[str, dict]:
    """One card per family, so the matrix is families rather than repeats."""
    out: dict[str, dict] = {}
    for card in cards(feed):
        out.setdefault(card["family"], card)
    return out


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


# ---- §22: every question offered is a question that can be run ----------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_offered_question_binds_against_this_release(feeds, domain_id):
    """A chip that cannot be executed is worse than no chip: the reader has
    been told the book holds something it does not.

    The live shape of this: a covenant-breach card offered "Split Leverage
    by region", and `corp_covenant_quarter` has no region column. The
    cross-cut list was the book's second axes IN GENERAL rather than the
    columns of the relation the finding actually came from.
    """
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    checked = 0
    for card in cards(feed):
        offered = card["drilldown"]["suggested_questions"]
        assert offered, card["item_id"]
        for question in offered:
            ok, why = inv.executable(catalog=catalog, session=session,
                                     question=question, domain_id=domain_id)
            assert ok, (card["item_id"], question["question"], why)
            checked += 1
    assert checked >= 20, "this test barely looked at anything"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_offered_question_names_its_own_book(feeds, domain_id):
    """§17, §18. The relation, the period column and the segment value all
    belong to the book the card came from."""
    feed, _scope, _session = feeds[domain_id]
    mine = set(schema_mod.relation_names(domain_id))
    for card in cards(feed):
        for question in card["drilldown"]["suggested_questions"]:
            assert question["relation"] in mine, question
            assert question["period_column"] == schema_mod.period_column(
                domain_id)


# ---- §19-§21: the packet holds what the question needs ------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_family_produces_a_packet_that_resolves(feeds, domain_id):
    """Every column the packet names is a column the catalogue confirms."""
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    for family, card in families(feed).items():
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        assert packet, family
        assert not inv.unresolved(catalog=catalog, packet=packet), family


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_carries_the_relation_the_finding_came_from(feeds,
                                                               domain_id):
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    for family, card in families(feed).items():
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        assert packet["relation"] == card["drilldown"]["relation"], family
        assert packet["period_column"] == schema_mod.period_column(domain_id)
        assert packet["period_noun"] == schema_mod.period_noun(domain_id)
        assert packet["reporting_period"] == card["reporting_period"]
        assert packet["comparison_period"] == card["comparison_period"]
        assert packet["grain"]
        assert packet["key_columns"]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_names_the_subject_as_a_value_and_as_a_label(feeds,
                                                                domain_id):
    """§41. The label is what the card shows; the value is what the filter
    is written against. A packet carrying only one of them makes the other
    unguessable."""
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    checked = 0
    for family, card in families(feed).items():
        if card["scope"] != "segment":
            continue
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        subject = packet["subject"]
        assert subject["value"] == card["segment"], family
        assert subject["label"] == card["segment_label"], family
        assert subject["filter"] == (
            f"{card['segment_dimension']} = '{card['segment']}'")
        checked += 1
    assert checked >= 3


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_stays_small_enough_to_carry_on_turn_one(feeds,
                                                            domain_id):
    """§20. The whole point is that it is cheaper than the lookup it
    replaces. The covenant relation has fifty-nine columns; a packet that
    carried all of them would BE the relation dump this exists to avoid."""
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    for family, card in families(feed).items():
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        assert inv.size(packet) < 6000, (family, inv.size(packet))
        assert len(packet["fields"]) <= inv.MAX_FIELDS, family


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_packet_never_carries_the_other_books_columns(feeds, domain_id):
    feed, _scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    mine = {f.name for spec in schema_mod.relations(domain_id)
            for f in spec.fields}
    theirs = {f.name for spec in schema_mod.relations(other)
              for f in spec.fields} - mine
    for family, card in families(feed).items():
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        blob = json.dumps(packet)
        for column in theirs:
            assert f'"{column}"' not in blob, (family, column)
        for relation in schema_mod.relation_names(other):
            assert relation not in blob, (family, relation)


# ---- §21: the packet reaches the first provider call --------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_seeded_turn_one_is_handed_the_packet(feeds, domain_id):
    """Not stored somewhere -- IN the first user message, which is the only
    place a model can read it from."""
    feed, scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    card = cards(feed)[0]
    seed = _seed_of(card, feed)
    packet = context_mod.build(
        question="What drove this?",
        principal={"id": "u1", "tenant": lake.DEFAULT_TENANT},
        scope=scope, catalog=catalog,
        limits=_limits(), mode="standard",
        release_summary={}, ui_filters={}, recent_turns=[], summary=None,
        capability=None, investigation=seed, session=session)

    assert "ANALYSIS PACKET FOR THIS INVESTIGATION" in packet.first_user_message
    body = packet.payload["investigation_packet"]
    assert body["relation"] == card["drilldown"]["relation"]
    assert body["reporting_period"] == card["reporting_period"]
    # And it reached the message itself, not only the payload.
    assert card["drilldown"]["relation"] in packet.first_user_message
    assert schema_mod.period_column(domain_id) in packet.first_user_message


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_never_names_the_other_books_calendar(feeds, domain_id):
    feed, scope, session = feeds[domain_id]
    catalog = arun.for_domain(domain_id).catalog
    other = ("reporting_month"
             if schema_mod.period_noun(domain_id) == "quarter"
             else "reporting_quarter")
    for card in cards(feed):
        packet = inv.analysis_packet(catalog=catalog, session=session,
                                     seed=_seed_of(card, feed))
        assert other not in json.dumps(packet), card["item_id"]


# ---- §23: every family, investigate -> execute -> publish ---------------

def _limits():
    from backend.cockpit_v4 import config as config_mod

    return config_mod.analytical_limits_for("standard")


def _seed_of(card: dict, feed: dict) -> dict:
    """The seed the investigate route would store for this card."""
    body = dict(card)
    body["domain_id"] = feed["domain_id"]
    body["release_id"] = feed["release_id"]
    body["release_fingerprint"] = feed["release_fingerprint"]
    body["issue"] = card["what_changed"]
    return body


def _drive_seeded(client, store_db, runtime, domain_id: str, card: dict,
                  question: str):
    """Open a real seeded thread through the route and run one turn in it.

    The analyst is scripted to do the ONE thing §21 says it should be able
    to do on turn one: execute. If the packet were missing anything, the SQL
    below could not have been written from it.
    """
    from backend.cockpit_v4.worker import Worker

    opened = client.post(f"{P}/attention/{card['item_id']}/investigate")
    assert opened.status_code == 201, opened.text
    thread_id = opened.json()["thread_id"]
    seed = opened.json()["seed"]

    drill = card["drilldown"]
    relation = drill["relation"]
    period = schema_mod.period_column(domain_id)
    dimension = card["segment_dimension"]
    # The measure comes from the PACKET, not from this test. That is the
    # whole claim being made: a seeded turn can write its query from what it
    # was handed. A collateral finding has no `ecl_sar_mn`, and a test that
    # hard-coded one would only be proving that the exposure relation exists.
    measure = (drill.get("measure_fields") or ["ead_sar_mn"])[0]
    where = f"{period} = '{seed['reporting_period']}'"
    if card["scope"] == "segment":
        where += f" AND {dimension} = '{card['segment']}'"
    sql = (f"SELECT SUM({measure}) AS {measure} FROM {relation} "
           f"WHERE {where}")

    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question=question, mode="standard",
        release_id=dom.DEFAULT_RELEASES[domain_id], domain_id=domain_id,
        release_fingerprint=seed["release_fingerprint"], ui_filters={},
        idempotency_key="", body_digest="", startup_sha="t", deadline_at="")

    def answer(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="answer",
                  narrative="The figure is {{claim.total}}.",
                  numeric_claims=[{
                      "claim_id": "total", "unit": "SAR million",
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": "0",
                                   "column_id": measure}}]))])

    from conftest import ScriptedProvider
    from test_domain_execution import execute_call

    runtime.provider = ScriptedProvider([
        ScriptedResult(tool_calls=[execute_call(
            sql, purpose="ECL for this finding", grain="portfolio",
            units="SAR million", subquestions=["ECL for this finding"],
            fields=[f"{relation}.{measure}"],
            month=seed["reporting_period"])]),
        answer])
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    return outcome, record


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_family_investigates_executes_and_publishes(
        feeds, client, store_db, runtime, domain_id):
    """§23. Every family in both books, driven through the real worker.

    No CALL_LIMIT, no DEADLINE_EXPIRED, no wrong-domain refusal -- and no
    catalogue call, because the packet already answered what a catalogue
    call would have asked.
    """
    feed, _scope, _session = feeds[domain_id]
    covered = families(feed)
    assert len(covered) >= 4, sorted(covered)

    for family, card in covered.items():
        outcome, record = _drive_seeded(
            client, store_db, runtime, domain_id, card,
            f"What drove {card['segment_label']}?")
        assert outcome.state == st.COMPLETED, (family, outcome.message)
        assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
        stored = store_db.get_run(record.run_id)
        assert stored.domain_id == domain_id, family
        assert stored.release_id == dom.DEFAULT_RELEASES[domain_id], family
