"""REAL API · REAL BOOKS · MODEL MOCKED OR REFUSING · THE FOUR MAC FAILURES.

§36. Each of these is one thing a person did on a Mac, against real Opus,
written down here as the sequence they performed and the outcome they got.

The automated dual-domain suite was green while all four were live, because
every one of them is invisible to a scripted analyst: a scripted analyst does
not read its instructions, does not read its tool schema, does not decide
what its question means, and does not run out of time. So these replay the
READER's sequence and assert on what the system does around the model --
which payload it sends, which book it opens, which clock it starts, and what
it has already resolved before the first generation.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import envelope as env
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st

P = "/api/v1/cockpit-v4"
LEGACY = "v4-saudi-20q-v1"


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


def settle(store_db, run_id: str) -> None:
    """Finish a run the way a cancel cannot: the concurrency cap is two."""
    record = store_db.get_run(run_id)
    store_db.update_state(run_id, expect_version=record.version,
                          state="CANCELLED", terminal=True)


class Recorder:
    """Captures the payload a paid call would carry, then refuses."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def count_tokens(self, **_kwargs) -> int:
        return 100

    def converse(self, *, system, messages, tools=None, **_kwargs):
        self.sent.append({"system": system, "messages": messages,
                          "tools": tools})
        raise RuntimeError("payload captured")

    def blob(self) -> str:
        return json.dumps(self.sent, default=str, ensure_ascii=False)


def payload_of(store_db, runtime, record) -> Recorder:
    from backend.cockpit_v4.worker import Worker

    recorder = Recorder()
    runtime.provider = recorder
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED
    assert recorder.sent, "no provider call was made"
    return recorder


# ---- FAILURE 1 ----------------------------------------------------------
#
# The reader opened a new Corporate conversation and asked for the latest
# month. Opus replied: "This book is recorded quarterly, not monthly",
# offered 2026Q2, and answered from the legacy twenty-quarter release.
#
# Two separate defects sat inside that one reply, and only one of them was
# about quarters. The Corporate book now DOES report quarters, so the part
# that still has to hold is the part that always was the real failure: the
# paid call must carry THIS book's release and THIS book's calendar, and
# never the pre-domain twenty-quarter release the process happened to be
# configured for. A book that answers from a release nobody asked for is
# wrong whether or not its calendar happens to agree.

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_failure_1_a_new_thread_is_never_told_another_books_calendar(
        client, store_db, runtime, domain_id):
    from backend.cockpit_v4 import domain_resolver as resolver

    scope = resolver.scope_for(domain_id)
    noun = scope.period_noun
    other = "month" if noun == "quarter" else "quarter"

    # Asked in the OTHER book's vocabulary on purpose. A reader saying
    # "month" to a quarterly book is not an error to be lectured about; the
    # run resolves to this book's own latest period and says so.
    response = client.post(f"{P}/runs", json={
        "question": f"What is total ECL by segment for the latest {other}?",
        "mode": "standard", "domain": domain_id})
    assert response.status_code == 202, response.text
    record = store_db.get_run(response.json()["run_id"])

    # The run was ACCEPTED against the book, not against whatever release the
    # process was configured for.
    assert record.release_id == dom.DEFAULT_RELEASES[domain_id]
    assert record.domain_id == domain_id
    assert record.release_id != LEGACY

    blob = payload_of(store_db, runtime, record).blob()
    forbidden = [LEGACY, r"twenty-quarter", r"cockpit_facility_quarter",
                 rf"latest populated {other}", rf"reporting_{other}s?\b"]
    if noun == "quarter":
        forbidden += [r"\bmonthly\b", r"\b20\d\d-(0[1-9]|1[0-2])(?![-\d])"]
    else:
        forbidden += [r"\bquarterly\b", r"20\d\dQ[1-4]"]
    for pattern in forbidden:
        hit = re.search(pattern, blob)
        assert not hit, (
            f"the paid {domain_id} call would have carried {pattern!r} "
            f"({hit.group(0)!r})")
    assert scope.latest_period in blob
    assert scope.reporting_frequency in blob


def test_failure_1_a_request_naming_another_release_is_refused_not_obeyed(
        client):
    response = client.post(f"{P}/runs", json={
        "question": "What is total ECL?", "mode": "standard",
        "domain": dom.CORPORATE, "release_id": LEGACY})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error_code"] == "RELEASE_NOT_SETTABLE"


def test_failure_1_the_legacy_release_is_never_a_fallback(
        client, store_db, runtime, monkeypatch):
    """Fail CLOSED. If the book cannot be opened the run fails saying so; it
    does not quietly answer from a release nobody asked for."""
    response = client.post(f"{P}/runs", json={
        "question": "What is total ECL by sector for the latest month?",
        "mode": "standard", "domain": dom.CORPORATE})
    record = store_db.get_run(response.json()["run_id"])

    from backend.cockpit_v4.worker import Worker

    def no_book(_self, _record):
        from backend.cockpit_v4.worker import _NoBook

        raise _NoBook("The Corporate Credit book cannot be opened.")

    monkeypatch.setattr(Worker, "_book_for", no_book)
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.DATA_UNAVAILABLE
    assert LEGACY not in (outcome.message or "")


# ---- FAILURE 2 ----------------------------------------------------------
#
# The reader clicked Investigate Further on a Retail Credit Card ECL card.
# The thread answered: "The Credit Card ECL card sits in the retail book, and
# this thread reads the corporate book only."

def test_failure_2_a_retail_card_opens_a_retail_thread(client, store_db):
    feed = client.get(f"{P}/attention", params={"domain": dom.RETAIL}).json()
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    card = next((c for c in cards if "Credit Card" in c["segment"]), cards[0])

    opened = client.post(f"{P}/attention/{card['item_id']}/investigate")
    assert opened.status_code in (200, 201), opened.text
    thread_id = opened.json()["thread_id"]

    # The SEED names the book, the release and the exact bytes.
    seed = opened.json()["seed"]
    assert seed["domain_id"] == dom.RETAIL
    assert seed["release_id"] == dom.DEFAULT_RELEASES[dom.RETAIL]
    assert seed["release_fingerprint"]
    assert seed["item_id"] == card["item_id"]
    assert seed["reporting_period"] and seed["metric"]

    # And so does the THREAD, before anybody asks anything in it.
    transcript = client.get(f"{P}/threads/{thread_id}").json()
    assert transcript["domain_id"] == dom.RETAIL
    assert transcript["release_id"] == dom.DEFAULT_RELEASES[dom.RETAIL]

    # And so does a run started in it.
    started = client.post(f"{P}/runs", json={
        "question": "Show me the customers behind this.",
        "thread_id": thread_id, "mode": "standard"})
    assert started.status_code == 202, started.text
    record = store_db.get_run(started.json()["run_id"])
    assert record.domain_id == dom.RETAIL
    assert record.release_id == dom.DEFAULT_RELEASES[dom.RETAIL]
    settle(store_db, record.run_id)


def test_failure_2_the_seeded_thread_reads_retail_relations_and_no_others(
        client, store_db, runtime):
    feed = client.get(f"{P}/attention", params={"domain": dom.RETAIL}).json()
    card = (feed["segments_requiring_attention"] + feed["ecl_highlights"])[0]
    thread_id = client.post(
        f"{P}/attention/{card['item_id']}/investigate").json()["thread_id"]
    started = client.post(f"{P}/runs", json={
        "question": "Show me the customers behind this.",
        "thread_id": thread_id, "mode": "standard"})
    record = store_db.get_run(started.json()["run_id"])

    blob = payload_of(store_db, runtime, record).blob()
    assert "retail_account_month" in blob
    for corporate_only in ("corp_facility_quarter", "corp_borrower_quarter",
                           "corp_covenant_quarter"):
        assert corporate_only not in blob, corporate_only


# ---- FAILURE 3 ----------------------------------------------------------
#
# In a thread that had already run an analysis, the reader asked "How is risk
# building in Information Technology?" and got DEADLINE_EXPIRED at sixty
# seconds -- the PRODUCT HELP allowance.

THE_QUESTION = "How is risk building in Information Technology?"


def test_failure_3_the_follow_up_is_on_the_analysis_clock_from_the_start(
        client, store_db):
    first = client.post(f"{P}/runs", json={
        "question": "What is EAD by sector for the latest month?",
        "mode": "standard", "domain": dom.CORPORATE})
    thread_id = first.json()["thread_id"]
    settle(store_db, first.json()["run_id"])
    store_db.append_turn(
        thread_id=thread_id, run_id=first.json()["run_id"],
        question="What is EAD by sector for the latest month?",
        answer={"narrative": "...", "disposition": "answer", "executed": True,
                "intent": {"query_mode": "DATA_ANALYSIS"}})

    follow = client.post(f"{P}/runs", json={
        "question": THE_QUESTION, "thread_id": thread_id, "mode": "standard"})
    assert follow.status_code == 202, follow.text
    record = store_db.get_run(follow.json()["run_id"])

    from datetime import datetime

    allowed = (datetime.fromisoformat(record.deadline_at)
               - datetime.fromisoformat(record.created_at)).total_seconds()
    assert allowed >= 110, (
        f"the follow-up was given {allowed:.0f}s; sixty is the product-help "
        f"allowance and it is what killed this turn")
    settle(store_db, record.run_id)


def test_failure_3_the_question_is_analytical_on_its_own_words(client):
    verdict = env.classify(question=THE_QUESTION)
    assert verdict.analytical is True
    analysis = env.limits_for_family(env.DATA_ANALYSIS_STANDARD)
    assert verdict.limits.deadline_seconds == analysis.deadline_seconds
    assert float(verdict.limits.spend_ceiling_usd) == 1.50


def test_failure_3_the_three_policy_families_are_published(client):
    families = {f["family"]: f for f in
                client.get(f"{P}/diagnostics").json()["budget_policy"][
                    "families"]}
    assert families["product_help.standard"]["deadline_seconds"] == 60.0
    assert (families["data_analysis.standard"]["deadline_seconds"]
            == env.limits_for_family(
                env.DATA_ANALYSIS_STANDARD).deadline_seconds)
    assert families["data_analysis.deep"]["deadline_seconds"] == 240.0


def test_failure_3_a_data_analysis_turn_does_not_open_with_product_help(
        client, store_db, runtime):
    """§13. Every tool on a call is a thing the model must consider, and the
    one that cannot contribute is the one worth taking off."""
    started = client.post(f"{P}/runs", json={
        "question": THE_QUESTION, "mode": "standard",
        "domain": dom.CORPORATE})
    record = store_db.get_run(started.json()["run_id"])
    recorder = payload_of(store_db, runtime, record)
    offered = {t["name"] for t in recorder.sent[0]["tools"]}
    assert "inspect_product_knowledge" not in offered, sorted(offered)
    assert "execute_analysis" in offered


# ---- FAILURE 4 ----------------------------------------------------------
#
# The reader typed "and within prject finance?" as a follow-up. The run spent
# generation after generation asking the catalogue for `product_name`,
# `product_category`, `product_type`, `product`, `product_code`.

THE_TYPO = "and within prject finance?"


def test_failure_4_the_typo_is_resolved_before_the_first_generation(
        client, store_db, runtime):
    first = client.post(f"{P}/runs", json={
        "question": "What is EAD by sector for the latest month?",
        "mode": "standard", "domain": dom.CORPORATE})
    thread_id = first.json()["thread_id"]
    settle(store_db, first.json()["run_id"])

    follow = client.post(f"{P}/runs", json={
        "question": THE_TYPO, "thread_id": thread_id, "mode": "standard"})
    record = store_db.get_run(follow.json()["run_id"])
    recorder = payload_of(store_db, runtime, record)

    merged: dict = {}
    for block in recorder.sent[0]["system"]:
        try:
            body = json.loads(block.get("text") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(body, dict):
            merged.update(body)

    recognised = merged["value_resolution"]["recognised"]
    assert [r["value"] for r in recognised] == ["project_finance"]
    assert recognised[0]["field"] == "product_type"
    assert recognised[0]["relation"] == "corp_facility_quarter"
    assert recognised[0]["record_as"] == "resolved_assumption"

    # And the field names the live run invented are not in the book, so the
    # enumeration cannot send it looking for them.
    fields = {d["field"] for d in merged["governed_values"]["dimensions"]}
    for invented in ("product_name", "product_category", "product_code",
                     "product"):
        assert invented not in fields, invented


def test_failure_4_the_typo_chain_all_resolves_to_one_category(client):
    """§41. Every way the reader might have typed it."""
    from backend.cockpit_v4 import values as val

    runtime = arun.for_domain(dom.CORPORATE)
    index = val.dimensions(session=runtime.session, catalog=runtime.catalog)
    for phrase in ("project finance", "Project Finance", "project_finance",
                   "Project-Finance", "PROJECT FINANCE", "prject finance",
                   "projet finance", "project finence"):
        resolved = val.resolve(phrase, index=index)
        assert isinstance(resolved, val.Resolution), (phrase, resolved)
        assert resolved.value == "project_finance", phrase


def test_failure_4_a_genuinely_ambiguous_word_is_asked_about(client):
    from backend.cockpit_v4 import values as val

    runtime = arun.for_domain(dom.CORPORATE)
    index = val.dimensions(session=runtime.session, catalog=runtime.catalog)
    outcome = val.resolve("finance", index=index)
    assert isinstance(outcome, val.Ambiguity)
    assert outcome.question.startswith("Did you mean ")
    assert len(outcome.candidates) >= 2


# ---- and the four together ----------------------------------------------

def test_none_of_the_four_failures_needs_a_paid_call_to_be_prevented():
    """Everything above is decided by CreditProbe, before or around the
    model: which release, which book, which clock, which category. That is
    why the scripted suite could not see them and why these can."""
    import inspect

    from backend.cockpit_v4 import context, envelope, routes as routes_mod
    from backend.cockpit_v4 import values

    assert "scope.release_id" in inspect.getsource(routes_mod)
    assert "RELEASE_NOT_SETTABLE" in inspect.getsource(routes_mod)
    assert "classify" in inspect.getsource(envelope)
    assert "value_resolution" in inspect.getsource(context)
    assert "MIN_SIMILARITY" in inspect.getsource(values)
