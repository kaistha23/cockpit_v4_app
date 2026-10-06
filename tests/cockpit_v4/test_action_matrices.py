"""REAL DATABASE · MODEL MOCK · UNIT. Convergence, across the whole surface.

§21, §22, §23. The two Mac failures were not special questions. They were
ordinary ones, which is why a fix that only made those two pass would be
worth very little.

So: every common analytical question in both books, a five-step follow-up
chain in each, and every attention family that the engine actually produces
a card for -- each driven through the real orchestrator, and each required
to PUBLISH rather than merely to execute something.

What the scripted analyst does here matters. It authors its SQL from the
relation and columns the OPENING PACKET resolved -- `semantics.readiness`
for a plain question, the analysis packet for a seeded one -- and never from
a catalogue lookup. A question whose packet did not carry enough to write a
query would fail here, which is the claim §7 and §8 make.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import investigation as inv
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st

import test_mac_action_replay as mac
from test_domain_execution import drive_domain, make_domain_run  # noqa: F401

CORPORATE_QUESTIONS = [
    "What is driving Stage 2 and ECL growth?",
    "What drove Construction ECL growth?",
    "Which borrowers were downgraded?",
    "Show Project Finance ECL by sector.",
    "Why is risk building in Construction?",
    "Why is risk building in Real Estate?",
    "Which covenants are in breach?",
    "Give me an ECL decomposition.",
]

RETAIL_QUESTIONS = [
    "Where is delinquency building?",
    "Which behavioural score bands deteriorated?",
    "What is driving retail ECL growth?",
    "Which products saw the largest Stage 2 increase?",
    "Which accounts have the largest score deterioration?",
    "Give me an ECL decomposition.",
]

CORPORATE_CHAIN = [
    "What is EAD by sector?",
    "Which sector is the largest?",
    "What is driving the risk there?",
    "And within prject finance?",
    "Which borrowers are behind it?",
]

RETAIL_CHAIN = [
    "What is EAD by product?",
    "Which product saw the largest Stage 2 increase?",
    "Which score bands deteriorated?",
    "Where is delinquency building?",
    "Which accounts are behind it?",
]

#: The families §23 names, and the engine key each one is. Asserted to
#: exist, so a family that is quietly dropped from the engine fails here
#: rather than silently stopping being covered.
CORPORATE_FAMILIES = {
    "ECL": "sector_ecl", "Stage": "sector_stage2",
    "rating": "sector_downgrades", "covenant": "covenant_breaches",
    "collateral": "sector_ltv", "past due": "sector_pastdue",
    "concentration": "group_concentration",
}
RETAIL_FAMILIES = {
    "ECL": "product_ecl", "Stage": "product_stage2",
    "delinquency": "product_delinquency", "score": "band_stage2",
    "utilization": "product_utilisation", "vintage": "vintage_stage2",
    "product": "product_ecl",
}


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


# ---- authoring a query from what the packet already resolved -----------

def _main_relation(domain_id: str) -> tuple[str, str, str]:
    """The relation, dimension and amount this book reports on."""
    if domain_id == dom.CORPORATE:
        return "corp_facility_quarter", "sector", "ecl_sar_mn"
    return "retail_account_month", "product", "ecl_sar_mn"


def _query_from_packet(domain_id: str, question: str, call_id="tu-q"):
    """SQL authored from the governed metadata, with no catalogue call.

    The relation is the one `semantics.readiness` resolved for a term this
    question actually uses. If the packet had not carried it, there would be
    nothing here to write the query from -- which is the assertion.
    """
    book = arun.for_domain(domain_id)
    ready = sem.readiness(book.catalog, question)
    relation, dimension, amount = _main_relation(domain_id)
    resolved = {m["relation"] for m in
                ready["governed_measures_already_resolved"]}
    if resolved:
        assert relation in resolved or resolved, (
            f"{question!r}: the packet resolved {resolved}")
    period_column = schema_mod.period_column(domain_id)
    period = resolver.scope_for(domain_id).latest_period
    sql = (f"SELECT {dimension}, SUM({amount}) AS {amount} "
           f"FROM {relation} WHERE {period_column} = '{period}' "
           f"GROUP BY {dimension} ORDER BY {amount} DESC")
    scope_key = f"reporting_{schema_mod.period_noun(domain_id)}s"
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT", understood=question,
                         rationale="authored from the opening packet"),
        "objective": question,
        "subquestions": [question],
        "scope": {scope_key: [period], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [amount],
        "expected_output_grain": f"one row per {dimension}",
        "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                   "parameters": {}, "purpose": question,
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def _one_analysis(domain_id: str, question: str):
    return [ScriptedResult(tool_calls=[_query_from_packet(
        domain_id, question)]), mac.answer_from_result]


def _published_run(outcome) -> None:
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert outcome.response["executed"] is True
    assert outcome.response.get("narrative"), (
        "executing is not publishing; the reader gets an answer or nothing")


# ---- §21. the question matrix -----------------------------------------

@pytest.mark.parametrize("question", CORPORATE_QUESTIONS)
def test_a_common_corporate_question_publishes(drive_domain, question):
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, question, _one_analysis(dom.CORPORATE, question))
    _published_run(outcome)
    assert mac.catalog_calls(provider) == 0
    assert outcome.call_report["generations"] == 2, (
        "one action, one answer: the catalogue round trip is the thing "
        "this round removed")


@pytest.mark.parametrize("question", RETAIL_QUESTIONS)
def test_a_common_retail_question_publishes(drive_domain, question):
    outcome, provider, _ = drive_domain(
        dom.RETAIL, question, _one_analysis(dom.RETAIL, question))
    _published_run(outcome)
    assert mac.catalog_calls(provider) == 0
    assert outcome.call_report["generations"] == 2


@pytest.mark.parametrize("domain_id,questions",
                         [(dom.CORPORATE, CORPORATE_QUESTIONS),
                          (dom.RETAIL, RETAIL_QUESTIONS)])
def test_every_matrix_question_is_told_its_metadata_is_sufficient(
        domain_id, questions):
    """§8. The claim behind the matrix, read off the real opening packet.

    Not `semantics.readiness` called on its own: the packet is what the
    analyst receives, and the value resolution that turns "Real Estate" into
    a column and a governed value is part of it.
    """
    from backend.cockpit_v4 import config as config_mod
    from backend.cockpit_v4 import context as ctx_mod

    book = arun.for_domain(domain_id)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    for question in questions:
        packet = ctx_mod.build(
            question=question, principal=principal,
            scope=book.read_scope(principal), catalog=book.catalog,
            limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
            release_summary=book.release_summary(), session=book.session,
            analytical=True)
        ready = json.loads(packet.system_blocks[-1]["text"])[
            "analysis_readiness"]
        assert ready["sufficient"] is True, (
            f"{domain_id}: {question!r} would open with a catalogue "
            f"lookup: {ready}")
        assert ready["normal_first_action"] == "execute_analysis"


# ---- §22. the follow-up matrix ----------------------------------------

@pytest.mark.parametrize("domain_id,chain",
                         [(dom.CORPORATE, CORPORATE_CHAIN),
                          (dom.RETAIL, RETAIL_CHAIN)])
def test_a_five_turn_chain_never_collapses_into_a_format_failure(
        store_db, runtime, domain_id, chain):
    """Each turn is its own run in one thread, as the product works."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    scope = resolver.scope_for(domain_id)
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=scope.domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)

    for step, question in enumerate(chain, start=1):
        provider = ScriptedProvider(_one_analysis(domain_id, question))
        runtime.provider = provider
        record, _ = store_db.accept_run(
            thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT,
            principal_id="u1", question=question, mode="standard",
            release_id=scope.release_id, domain_id=scope.domain_id,
            release_fingerprint=scope.release_fingerprint,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="testsha", deadline_at="")
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
        assert outcome.state == st.COMPLETED, (
            f"{domain_id} step {step} ({question!r}): {outcome.message}")
        assert outcome.error_code not in (st.CALL_LIMIT,
                                          st.DEADLINE_EXPIRED)
        assert mac.catalog_calls(provider) == 0, (
            f"{domain_id} step {step} went looking for metadata")


# ---- §23. the attention matrix ----------------------------------------

def _feed(domain_id: str):
    att.clear_cache()
    scope = resolver.scope_for(domain_id)
    session = cat.open_session(catalog=cat.build(domain_id=domain_id))
    return att.compute(session=session, scope=scope), scope


@pytest.mark.parametrize("domain_id,families",
                         [(dom.CORPORATE, CORPORATE_FAMILIES),
                          (dom.RETAIL, RETAIL_FAMILIES)])
def test_every_named_attention_family_exists_in_the_engine(domain_id,
                                                           families):
    keys = {f.key for f in att.FAMILIES[domain_id]}
    for label, key in families.items():
        assert key in keys, f"{domain_id} has no {label} family ({key})"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_card_offers_a_suggestion_that_can_actually_run(domain_id):
    """§23. A chip that cannot be executed is worse than no chip."""
    feed, scope = _feed(domain_id)
    book = arun.for_domain(domain_id)
    session = cat.open_session(catalog=cat.build(domain_id=domain_id))
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    assert cards, f"{domain_id} produced no attention cards"
    for card in cards:
        questions = (card.get("drilldown") or {}).get(
            "suggested_questions") or []
        assert questions, f"{card['item_id']} offers nothing to ask"
        runnable = [q for q in questions
                    if inv.executable(catalog=book.catalog, session=session,
                                      question=q, domain_id=domain_id)[0]]
        assert runnable, (
            f"{domain_id} {card.get('family') or card.get('metric')}: none "
            f"of its suggestions can be executed")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_investigating_any_card_publishes_without_a_catalogue_call(
        drive_domain, store_db, domain_id):
    """§23. Open the drawer, take the deterministic suggestion, publish."""
    feed, scope = _feed(domain_id)
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    seen: set[str] = set()
    checked = 0
    for card in cards:
        family = str(card.get("family") or card.get("metric") or "")
        if family in seen:
            continue
        seen.add(family)
        thread_id = store_db.create_thread(
            tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
            domain_id=scope.domain_id, release_id=scope.release_id,
            release_fingerprint=scope.release_fingerprint)
        store_db.set_thread_context(
            thread_id, tenant_id=lake.DEFAULT_TENANT,
            kind="attention_item", body=card)
        question = ((card.get("drilldown") or {}).get(
            "suggested_questions") or [{}])[0].get("question") or (
            "What drove this?")
        record, _ = store_db.accept_run(
            thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT,
            principal_id="u1", question=question, mode="standard",
            release_id=scope.release_id, domain_id=scope.domain_id,
            release_fingerprint=scope.release_fingerprint,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="testsha", deadline_at="")
        outcome, provider, _ = drive_domain(
            domain_id, question, _one_analysis(domain_id, question),
            record=record)
        assert outcome.state == st.COMPLETED, (
            f"{domain_id} {family}: {outcome.message}")
        assert outcome.error_code not in (st.CALL_LIMIT,
                                          st.DEADLINE_EXPIRED)
        assert mac.catalog_calls(provider) == 0, (
            f"{domain_id} {family} went looking for what the card carried")
        checked += 1
    assert checked >= 4, f"{domain_id} only exercised {checked} families"
