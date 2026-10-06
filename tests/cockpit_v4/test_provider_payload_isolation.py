"""MODEL MOCK · REAL CONTEXT PATH · BOTH BOOKS.

§4. What the live provider is ACTUALLY sent, inspected byte by byte.

Why this file exists
-------------------
Every automated suite in the build was green while a real Opus run against
the monthly Corporate book replied "this book is recorded quarterly, not
monthly" and offered 2026Q2. The scripted suites could not see it because a
scripted analyst does not READ its instructions or its tool schema — it
returns whatever the test told it to.

So this captures the payload the real Anthropic adapter would be handed, by
driving the SAME worker through the SAME context path with a provider that
records what it was given and then raises. No reduced shortcut: the system
blocks, the first user message and the tool schemas are exactly the ones a
paid call would carry.

The three leaks it exists to stop, all found here
-------------------------------------------------
1. `prompts/analyst.md` worked its resolved-assumption example in a FIXED
   calendar: "period not specified: latest populated quarter 2026Q2, against
   2026Q1" -- served unchanged to a book that reports months.
2. Every tool schema carried the same sentence, and `execute_analysis` named
   its field `reporting_quarters`. The analyst believes the CONTRACT over the
   catalogue, correctly, because the contract is what it has to fill in.
3. The module registry told it the Cockpit owns "the twenty-quarter corporate
   domain", which is true of neither book.

The rule is SYMMETRIC, and it has to be. The Corporate book now reports
twenty quarters and the Retail book twenty months, so "no quarters anywhere"
is no longer the check -- it would have passed a Retail payload written
entirely in quarters as long as Corporate's was too. What each payload must
carry is ITS OWN calendar, and what it must not carry is the OTHER one.
"""

from __future__ import annotations

import json
import re

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

#: The pre-domain release and its relations. These belong to NEITHER book
#: and may not appear in either payload, whatever the calendar.
LEGACY_RELEASE = "v4-saudi-20q-v1"
LEGACY = (
    LEGACY_RELEASE,
    r"twenty-quarter",
    r"cockpit_facility_quarter", r"cockpit_borrower_financial_quarter",
    r"cockpit_covenant_quarter", r"cockpit_collateral_quarter",
    r"cockpit_rating_ratio_quarter",
)

#: The calendar vocabulary of each book: the words and shapes that are true
#: of it and false of the other. A payload carries its own row and none of
#: the other book's.
CALENDAR_WORDS = {
    dom.CORPORATE: (r"2026Q[1-4]", r"2025Q[1-4]", r"reporting_quarters?\b",
                    r"latest quarter", r"populated quarter", r"quarterly"),
    # A month, and not the first two thirds of a `YYYY-MM-DD` build date:
    # the product facts carry a `pack_version` of "2026-09-11.1", which is
    # not a reporting period in anybody's calendar.
    dom.RETAIL: (r"20\d\d-(0[1-9]|1[0-2])(?![-\d])", r"reporting_months?\b",
                 r"latest month", r"populated month", r"monthly"),
}


def forbidden_for(domain_id: str) -> tuple[str, ...]:
    """What must NOT be in this book's payload: the other book's calendar,
    plus the pre-domain release nothing should still be carrying."""
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    return LEGACY + CALENDAR_WORDS[other]


QUESTIONS = {
    dom.CORPORATE: "What is EAD by sector for the latest period?",
    dom.RETAIL: "What is EAD by product for the latest period?",
}


class Recorder:
    """A provider that records what it was handed and then refuses.

    It raises rather than answering, because what is under test is the
    REQUEST. An answer would only add the test's own fiction to the bytes.
    """

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


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


def capture(store_db, runtime, domain_id: str, question: str) -> str:
    """Drive one real run to its first provider call and return the bytes."""
    from backend.cockpit_v4.worker import Worker

    scope = resolver.scope_for(domain_id)
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=scope.domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question=question, mode="standard", release_id=scope.release_id,
        domain_id=scope.domain_id,
        release_fingerprint=scope.release_fingerprint, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="t", deadline_at="")
    recorder = Recorder()
    runtime.provider = recorder
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED, (
        "the recorder refuses on purpose; the payload is the artifact")
    assert recorder.sent, "no provider call was made"
    return recorder.blob()


@pytest.fixture
def payloads(store_db, runtime):
    return {domain_id: capture(store_db, runtime, domain_id, question)
            for domain_id, question in QUESTIONS.items()}


# ---- the leak this round exists for ------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_no_other_books_calendar_reaches_this_book(payloads, domain_id):
    blob = payloads[domain_id]
    found = {pattern: len(re.findall(pattern, blob, re.I))
             for pattern in forbidden_for(domain_id)}
    leaked = {k: v for k, v in found.items() if v}
    assert leaked == {}, (
        f"the {domain_id} payload carries another book's calendar or the "
        f"pre-domain release: {leaked}. A live run believes the contract it "
        f"is asked to fill in over the catalogue it is shown.")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_this_books_own_calendar_does_reach_it(payloads, domain_id):
    """The other half of the same rule. A payload that names no calendar at
    all would pass the exclusion above and still leave the analyst to guess,
    which is exactly what produced "this book is recorded quarterly"."""
    blob = payloads[domain_id]
    for pattern in CALENDAR_WORDS[domain_id]:
        assert re.search(pattern, blob, re.I), (
            f"the {domain_id} payload never states {pattern!r}; a book that "
            f"does not say what its periods are invites the analyst to "
            f"assume")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_payload_names_this_book_and_its_own_calendar(payloads,
                                                          domain_id):
    blob = payloads[domain_id]
    scope = resolver.scope_for(domain_id)
    for needle in (domain_id, dom.LABELS[domain_id], scope.release_id,
                   scope.release_fingerprint[:16], scope.latest_period,
                   scope.reporting_frequency, scope.period_column):
        assert needle in blob, f"{needle!r} is missing from the payload"
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    assert dom.DEFAULT_RELEASES[other] not in blob
    for relation in arun.for_domain(other).catalog.relations():
        assert relation not in blob, (
            f"the other book's relation {relation} reached the payload")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_worked_example_in_every_tool_uses_this_book(payloads,
                                                         domain_id):
    """The schema's own example is where the calendar leaked from."""
    call = json.loads(payloads[domain_id])[0]
    tools = call["tools"] or []
    assert tools, "a run with no tools cannot analyse anything"
    scope = resolver.scope_for(domain_id)
    other_noun = "month" if scope.period_noun == "quarter" else "quarter"
    for tool in tools:
        text = json.dumps(tool, ensure_ascii=False)
        assert other_noun not in text.lower(), (
            f"{tool['name']} names a {other_noun} to a "
            f"{scope.reporting_frequency} book")
        if "resolved_assumptions" in text:
            worked = (f"latest populated {scope.period_noun} "
                      f"{scope.latest_period} against {scope.previous_period}")
            assert worked in text, (
                f"{tool['name']} does not carry this book's worked example "
                f"({worked!r})")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_instruction_and_the_registry_name_this_book(payloads,
                                                         domain_id):
    call = json.loads(payloads[domain_id])[0]
    scope = resolver.scope_for(domain_id)
    other_noun = "month" if scope.period_noun == "quarter" else "quarter"
    instruction = json.dumps(call["system"][0], ensure_ascii=False)
    assert other_noun not in instruction.lower()
    assert f"latest populated {scope.period_noun}" in instruction

    facts = json.dumps(call["system"][1], ensure_ascii=False)
    assert "twenty-quarter" not in facts
    # The BOOK, named in the payload the analyst actually receives.
    #
    # It used to be asserted against the product registry in block 1, which
    # an analytical turn no longer carries -- the pack describes what
    # CreditProbe is, and this turn was asked what a book did. The claim
    # that matters is unchanged and is checked where it now lives: the
    # instruction and the pinned scope both name this book, on every turn.
    whole = json.dumps(call["system"], ensure_ascii=False)
    assert dom.LABELS[domain_id] in whole, (
        "the payload must say which book this run reads")
    assert dom.LABELS[domain_id] in json.dumps(call["system"][-1],
                                               ensure_ascii=False)


def test_the_two_books_get_two_different_payloads(payloads):
    corporate, retail = payloads[dom.CORPORATE], payloads[dom.RETAIL]
    assert corporate != retail
    assert "corp_facility_quarter" in corporate
    assert "corp_facility_quarter" not in retail
    assert "retail_account_month" in retail
    assert "retail_account_month" not in corporate


# ---- the tool contract, directly ---------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_period_field_is_period_neutral_on_the_wire(domain_id):
    """One name for the field, whatever the book's calendar."""
    from backend.cockpit_v4 import contracts

    tools = contracts.provider_tools(
        catalog=arun.for_domain(domain_id).catalog)
    by_name = {t["name"]: t for t in tools}
    execute = json.dumps(by_name["execute_analysis"])
    assert '"reporting_periods"' in execute
    assert "reporting_quarters" not in execute
    inspect = json.dumps(by_name["inspect_catalog"])
    assert '"reporting_periods"' in inspect
    assert "reporting_quarters" not in inspect


def test_a_tool_built_without_a_book_names_no_calendar_at_all():
    """With nothing open, the safe thing to say is nothing."""
    from backend.cockpit_v4 import contracts

    blob = json.dumps(contracts.provider_tools())
    for pattern in (r"2026Q[1-4]", r"20\d\d-\d\d", "quarter", "monthly"):
        assert not re.search(pattern, blob, re.I), pattern


@pytest.mark.parametrize("spelling", ["reporting_periods", "reporting_months",
                                      "reporting_quarters"])
def test_every_spelling_of_which_periods_is_accepted(spelling):
    """A refused spelling is a refused tool call, and costs a generation."""
    from backend.cockpit_v4.contracts import parse_catalog

    request = parse_catalog({
        "intent": {"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT",
                   "understood_request": "x", "response_language": "en",
                   "blocking_ambiguities": [], "resolved_assumptions": [],
                   "canonical_mappings": [], "excluded_parts": [],
                   "public_rationale": "r"},
        "query": "", "relation_ids": [], "field_ids": [],
        "detail": ["coverage"], spelling: ["2026-08"],
        "sample_rows": 0, "cursor": ""})
    assert request.reporting_periods == ("2026-08",)
    assert request.reporting_quarters == ("2026-08",)


# ---- §2: the legacy release may not participate ------------------------

def test_a_new_thread_is_never_opened_against_the_legacy_release(store_db):
    """Every book a new thread can be opened in is a DOMAIN book."""
    for domain_id in dom.DOMAIN_IDS:
        scope = resolver.scope_for(domain_id)
        assert scope.release_id == dom.DEFAULT_RELEASES[domain_id]
        assert scope.release_id != LEGACY_RELEASE
        thread_id = store_db.create_thread(
            tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
            domain_id=scope.domain_id, release_id=scope.release_id,
            release_fingerprint=scope.release_fingerprint)
        pinned = store_db.thread_domain(thread_id)
        assert pinned["release_id"] == dom.DEFAULT_RELEASES[domain_id]


def test_the_legacy_release_is_not_a_fallback_for_a_domain_run(store_db,
                                                               runtime):
    """A domain run whose book cannot open is REFUSED, never downgraded."""
    from backend.cockpit_v4.worker import Worker

    scope = resolver.scope_for(dom.RETAIL)
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.RETAIL, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question="q", mode="standard", release_id=scope.release_id,
        domain_id=dom.RETAIL, release_fingerprint="0" * 64, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="t", deadline_at="")
    recorder = Recorder()
    runtime.provider = recorder
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.DATA_UNAVAILABLE
    assert recorder.sent == [], "a refused book must cost no model call"
    assert LEGACY_RELEASE not in outcome.message


def test_the_configured_release_does_not_decide_a_domain_runs_book(
        store_db, runtime):
    """The runtime is configured for the legacy release. A domain run
    reading it anyway would be the whole defect."""
    assert runtime.cfg.release_id == LEGACY_RELEASE, (
        "this fixture is configured for the pre-domain release on purpose")
    blob = capture(store_db, runtime, dom.CORPORATE, QUESTIONS[dom.CORPORATE])
    assert LEGACY_RELEASE not in blob
    assert dom.DEFAULT_RELEASES[dom.CORPORATE] in blob


# ---- §5: "latest month" is canonical, not a question -------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_latest_period_resolves_without_asking(domain_id):
    """The server resolves the period deterministically, before any call,
    in THIS book's calendar."""
    from backend.cockpit_v4 import semantics as sem

    scope = resolver.scope_for(domain_id)
    catalog = arun.for_domain(domain_id).catalog
    noun = scope.period_noun
    other = "month" if noun == "quarter" else "quarter"

    resolution = sem.periods(catalog)
    assert resolution["reporting_frequency"] == scope.reporting_frequency
    assert resolution["period_noun"] == noun
    assert resolution["latest_period"] == scope.latest_period
    assert resolution["prior_period"] == scope.previous_period
    assert resolution["resolution"][f"latest {noun}"] == scope.latest_period
    assert other not in json.dumps(resolution).lower(), (
        f"a {scope.reporting_frequency} book must not resolve into {other}s")
    assert sem.period_phrases(
        f"What is EAD by segment for the latest {noun}?",
        catalog) == ["latest_period"]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_exact_live_question_carries_no_blocking_ambiguity(domain_id):
    from backend.cockpit_v4 import semantics as sem

    catalog = arun.for_domain(domain_id).catalog
    assert sem.ambiguous_terms_in(QUESTIONS[domain_id], catalog) == [], (
        "a question built only from canonical terms and a resolved period "
        "must not be sent back as a question")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_states_the_resolved_period_as_a_fact(payloads, domain_id):
    """Not "which period?" — the answer, before the analyst is asked."""
    scope = resolver.scope_for(domain_id)
    noun = scope.period_noun
    other = "month" if noun == "quarter" else "quarter"
    call = json.loads(payloads[domain_id])[0]
    block = next(json.loads(b["text"]) for b in call["system"]
                 if "pinned_scope" in str(b.get("text", "")))
    pinned = block["pinned_scope"]
    assert pinned["reporting_frequency"] == scope.reporting_frequency
    assert pinned[f"latest_populated_{noun}"] == scope.latest_period
    assert pinned[f"populated_{noun}s"][-1] == scope.latest_period
    assert pinned["domain"] == domain_id
    assert pinned["release_id"] == dom.DEFAULT_RELEASES[domain_id]
    assert other not in json.dumps(pinned).lower(), (
        "the pinned scope must not name a calendar this book does not use")
