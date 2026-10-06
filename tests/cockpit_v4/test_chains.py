"""REAL DATABASE · REAL WORKER · MODEL MOCK · TWENTY FOLLOW-UP CHAINS.

§44, §45. Ten multi-turn chains per book, four to six turns each, and ten
attention chains per book driven from a real card through investigate, a
suggested question, execution and publication.

Why chains and not more single questions
-----------------------------------------
A single question exercises the contract once. A CHAIN exercises what the
thread remembers: that the book is pinned after turn one, that "and within
project finance?" is a filter on the standing subject rather than a new
question, that the calendar does not drift between turns, and that the
fourth answer is still stamped with the release the first one read.

The live failures were all chain failures. The Construction investigation
died on its second turn. The corporate thread was told its book was
quarterly on turn one and carried that into every turn after. Neither would
have been caught by asking one question well.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_domain_execution import execute_call  # noqa: F401

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import investigation as inv
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import states as st
from backend.cockpit_v4 import values as val_mod

from . import domain_oracles as oracle

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


# ---- one turn, through the real worker ----------------------------------

def answer_for(column: str):
    def build(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="answer",
                  narrative="The figure is {{claim.figure}}.",
                  numeric_claims=[{
                      "claim_id": "figure", "unit": "SAR million",
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": "0",
                                   "column_id": column}}]))])
    return build


def turn(store_db, runtime, *, thread_id: str, domain_id: str,
         question: str, sql: str, fields, column: str, period: str):
    """One question asked inside an existing thread, end to end."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    scope = resolver.scope_for(domain_id)
    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question=question, mode="standard", release_id=scope.release_id,
        domain_id=domain_id, release_fingerprint=scope.release_fingerprint,
        ui_filters={}, idempotency_key="", body_digest="", startup_sha="t",
        deadline_at="")
    runtime.provider = ScriptedProvider([
        ScriptedResult(tool_calls=[execute_call(
            sql, purpose=question, grain="portfolio", units="SAR million",
            subquestions=[question], fields=list(fields), month=period)]),
        answer_for(column)])
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    return outcome, store_db.get_run(record.run_id)


# ---- the ten follow-up chains per book ----------------------------------

def corporate_chains():
    """Ten chains of four to six turns, each one narrowing the one before.

    A chain is not ten questions in a row: every turn after the first is
    about the SUBJECT the turn before established -- a sector, then a
    product inside it, then a rating band inside that. That is what a
    reader does, and it is what the thread has to remember.
    """
    latest = oracle.latest_period(dom.CORPORATE)
    previous = oracle.previous_period(dom.CORPORATE)
    f = "corp_facility_quarter"
    q = "reporting_quarter"

    def where(*clauses: str) -> str:
        return " AND ".join([f"{q} = '{latest}'", *clauses])

    def step(question: str, measure: str, *clauses: str, column: str = ""):
        column = column or measure.split(" AS ")[-1].strip()
        return (question,
                f"SELECT {measure} FROM {f} WHERE {where(*clauses)}",
                [f"{f}.ead_sar_mn", f"{f}.ecl_sar_mn"], column)

    sectors = ("Construction", "Real Estate", "Manufacturing",
               "Power and Utilities", "Healthcare", "Hospitality",
               "Chemicals", "Metals and Mining", "Retail Trade",
               "Transport and Logistics")
    chains = []
    for index, sector in enumerate(sectors, start=1):
        chain = [
            step("What is total exposure at default?",
                 "SUM(ead_sar_mn) AS ead_sar_mn"),
            step(f"And within {sector}?",
                 "SUM(ead_sar_mn) AS ead_sar_mn",
                 f"sector = '{sector}'"),
            step("And within project finance?",
                 "SUM(ead_sar_mn) AS ead_sar_mn",
                 f"sector = '{sector}'", "product_type = 'project_finance'"),
            step("How much of that is impaired?",
                 "SUM(ecl_sar_mn) AS ecl_sar_mn",
                 f"sector = '{sector}'", "product_type = 'project_finance'"),
        ]
        if index % 2 == 0:
            chain.append(step(
                "And what is the stage 2 exposure there?",
                "SUM(CASE WHEN stage = 2 THEN ead_sar_mn ELSE 0 END) "
                "AS ead_sar_mn",
                f"sector = '{sector}'", "product_type = 'project_finance'"))
        if index % 3 == 0:
            chain.append((
                "How did that compare a quarter ago?",
                f"SELECT SUM(ead_sar_mn) AS ead_sar_mn FROM {f} "
                f"WHERE {q} = '{previous}' AND sector = '{sector}' "
                f"AND product_type = 'project_finance'",
                [f"{f}.ead_sar_mn"], "ead_sar_mn"))
        chains.append((f"CH{index:02d}", dom.CORPORATE, sector, chain))
    return chains


def retail_chains():
    latest = oracle.latest_period(dom.RETAIL)
    previous = oracle.previous_period(dom.RETAIL)
    a = "retail_account_month"
    m = "reporting_month"

    def where(*clauses: str) -> str:
        return " AND ".join([f"{m} = '{latest}'", *clauses])

    def step(question: str, measure: str, *clauses: str, column: str = ""):
        column = column or measure.split(" AS ")[-1].strip()
        return (question,
                f"SELECT {measure} FROM {a} WHERE {where(*clauses)}",
                [f"{a}.ead_sar_mn", f"{a}.ecl_sar_mn"], column)

    products = ("Credit Card", "Personal Finance", "Auto Finance",
                "Mortgage")
    segments = ("Mass", "Payroll", "Affluent", "Private")
    chains = []
    for index in range(1, 11):
        product = products[(index - 1) % len(products)]
        segment = segments[(index - 1) % len(segments)]
        chain = [
            step("What is total exposure at default?",
                 "SUM(ead_sar_mn) AS ead_sar_mn"),
            step(f"And for {product}?",
                 "SUM(ead_sar_mn) AS ead_sar_mn",
                 f"product = '{product}'"),
            step(f"And within the {segment} segment?",
                 "SUM(ead_sar_mn) AS ead_sar_mn",
                 f"product = '{product}'",
                 f"customer_segment = '{segment}'"),
            step("How much of that is past due?",
                 "SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn ELSE 0 END) "
                 "AS ead_sar_mn",
                 f"product = '{product}'",
                 f"customer_segment = '{segment}'"),
        ]
        if index % 2 == 0:
            chain.append(step(
                "And what is the ECL on it?",
                "SUM(ecl_sar_mn) AS ecl_sar_mn",
                f"product = '{product}'",
                f"customer_segment = '{segment}'"))
        if index % 3 == 0:
            chain.append((
                "How did that compare a month ago?",
                f"SELECT SUM(ead_sar_mn) AS ead_sar_mn FROM {a} "
                f"WHERE {m} = '{previous}' AND product = '{product}' "
                f"AND customer_segment = '{segment}'",
                [f"{a}.ead_sar_mn"], "ead_sar_mn"))
        chains.append((f"RH{index:02d}", dom.RETAIL, product, chain))
    return chains


CHAINS = corporate_chains() + retail_chains()
CHAIN_IDS = [c[0] for c in CHAINS]


@pytest.mark.parametrize("chain_id,domain_id,subject,steps", CHAINS,
                         ids=CHAIN_IDS)
def test_a_follow_up_chain_stays_in_one_book_and_one_calendar(
        client, store_db, runtime, chain_id, domain_id, subject, steps):
    """§44. Four to six turns, each narrowing the one before, and every one
    of them answered from the same release on the same calendar."""
    assert 4 <= len(steps) <= 6, (chain_id, len(steps))
    thread_id = client.post(f"{P}/threads",
                            json={"domain": domain_id}).json()["thread_id"]
    scope = resolver.scope_for(domain_id)
    period = scope.latest_period
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)

    for ordinal, (question, sql, fields, column) in enumerate(steps, 1):
        outcome, stored = turn(
            store_db, runtime, thread_id=thread_id, domain_id=domain_id,
            question=question, sql=sql, fields=fields, column=column,
            period=period)
        assert outcome.state == st.COMPLETED, (chain_id, ordinal,
                                               outcome.message)
        assert stored.domain_id == domain_id, (chain_id, ordinal)
        assert stored.release_id == dom.DEFAULT_RELEASES[domain_id]
        # The calendar does not drift between turns.
        blob = json.dumps(outcome.response, default=str)
        assert dom.DEFAULT_RELEASES[other] not in blob, (chain_id, ordinal)
        for relation in schema_mod.relation_names(other):
            assert relation not in blob, (chain_id, ordinal, relation)

    # And the thread is still pinned after the last turn.
    refused = client.post(f"{P}/runs", json={
        "question": "and the other book?", "mode": "standard",
        "thread_id": thread_id, "domain": other})
    assert refused.status_code == 409, chain_id


@pytest.mark.parametrize("chain_id,domain_id,subject,steps", CHAINS,
                         ids=CHAIN_IDS)
def test_every_follow_up_in_a_chain_resolves_its_own_words(
        chain_id, domain_id, subject, steps):
    """§39, §40. "And within project finance?" is a FILTER on the standing
    subject. The resolver has to reach the right field from the reader's
    own words, and the trace has to quote what they wrote."""
    runtime = arun.for_domain(domain_id)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    resolved_any = False
    for question, sql, _fields, _column in steps:
        for resolution in val_mod.phrases(question, index=index):
            field = getattr(resolution, "field_name", "")
            value = getattr(resolution, "value", "")
            if not field:
                continue
            resolved_any = True
            # Whatever it resolved, the SQL of that same turn filters on it.
            assert f"{field} = '{value}'" in sql, (
                chain_id, question, field, value)
    assert resolved_any, (
        f"{chain_id} has no turn whose own words name a governed value, so "
        f"it is not testing follow-up resolution at all")


# ---- the ten attention chains per book ----------------------------------

@pytest.fixture(scope="module")
def feeds():
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        scope = resolver.scope_for(domain_id)
        session = cat.open_session(catalog=cat.build(domain_id=domain_id))
        out[domain_id] = (att.compute(session=session, scope=scope), scope,
                          session)
    return out


#: Ten chains per book, as `(card position, which suggested question)`.
#:
#: The feed publishes nine cards -- five movement cards and four ECL
#: highlights -- so ten DISTINCT chains are the nine cards each taken from
#: their first offered question, plus the top card taken from its second.
#: Running one card twice through the same question would be one chain
#: counted twice, which is not what §45 is asking for.
ATTENTION_CHAINS: tuple[tuple[int, int], ...] = tuple(
    [(position, 0) for position in range(9)] + [(0, 1)])


def attention_chain_ids(domain_id: str) -> tuple[tuple[int, int], ...]:
    return ATTENTION_CHAINS


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
@pytest.mark.parametrize("position,asked", ATTENTION_CHAINS,
                         ids=[f"card{p}q{a}" for p, a in ATTENTION_CHAINS])
def test_an_attention_chain_runs_from_a_card_to_a_published_answer(
        feeds, client, store_db, runtime, domain_id, position, asked):
    """§45. Card, investigate, take a suggested question, execute, publish.

    The live failure was exactly this path: the Construction card opened a
    thread that read the catalogue twice, read product knowledge, and died
    with CALL_LIMIT.
    """
    feed, _scope, session = feeds[domain_id]
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    assert len(cards) >= 9, (domain_id, len(cards))
    card = cards[position]

    opened = client.post(f"{P}/attention/{card['item_id']}/investigate")
    assert opened.status_code == 201, opened.text
    body = opened.json()
    thread_id = body["thread_id"]
    seed = body["seed"]
    assert seed["domain_id"] == domain_id

    # The suggested questions are executable; take the first one and run it.
    catalog = arun.for_domain(domain_id).catalog
    offered = card["drilldown"]["suggested_questions"]
    assert offered, card["item_id"]
    for question in offered:
        ok, why = inv.executable(catalog=catalog, session=session,
                                 question=question, domain_id=domain_id)
        assert ok, (card["item_id"], question["question"], why)

    drill = card["drilldown"]
    relation = drill["relation"]
    measure = (drill.get("measure_fields") or ["ead_sar_mn"])[0]
    period = schema_mod.period_column(domain_id)
    clauses = [f"{period} = '{seed['reporting_period']}'"]
    if card["scope"] == "segment":
        clauses.append(f"{card['segment_dimension']} = '{card['segment']}'")
    sql = (f"SELECT SUM({measure}) AS {measure} FROM {relation} "
           f"WHERE {' AND '.join(clauses)}")

    taken = offered[min(asked, len(offered) - 1)]
    outcome, stored = turn(
        store_db, runtime, thread_id=thread_id, domain_id=domain_id,
        question=taken["question"], sql=sql,
        fields=[f"{relation}.{measure}"], column=measure,
        period=seed["reporting_period"])

    assert outcome.state == st.COMPLETED, (card["item_id"], outcome.message)
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert stored.domain_id == domain_id
    assert stored.release_id == dom.DEFAULT_RELEASES[domain_id]


def test_the_chain_matrix_is_the_size_the_round_asks_for():
    """§44, §45: twenty follow-up chains, ten per book, four to six turns
    each; and twenty attention chains, ten per book."""
    corporate = [c for c in CHAINS if c[1] == dom.CORPORATE]
    retail = [c for c in CHAINS if c[1] == dom.RETAIL]
    assert len(corporate) == 10 and len(retail) == 10
    assert all(4 <= len(c[3]) <= 6 for c in CHAINS)
    assert sum(len(c[3]) for c in CHAINS) >= 80, (
        "twenty chains of four turns is eighty turns at minimum")
    # Ten attention chains per book, parametrized above.
    assert len(attention_chain_ids(dom.CORPORATE)) == 10
    assert len(attention_chain_ids(dom.RETAIL)) == 10
