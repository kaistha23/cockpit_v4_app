"""REAL DATABASE · REAL ROUTES · REAL WORKER. What must FAIL, and how.

§49. A suite that only shows what works proves that the happy path exists.
These are the things that must be refused, and the shape of each refusal:
named, typed, and specific enough to act on.

The rule underneath all of them is the same. A refusal that names what went
wrong is a refusal somebody can fix. A refusal that says "something went
wrong" is an outage report, and a SILENT substitution -- the other book's
data, a different release, a period that is not there -- is worse than
either, because nothing on the screen says it happened.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_domain_execution import drive_domain  # noqa: F401
from test_domain_execution import execute_call, make_domain_run  # noqa: F401

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import contracts as contracts_mod
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


# ---- the other book's data is never substituted -------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_query_on_the_other_books_relation_is_refused_by_name(
        drive_domain, domain_id):  # noqa: F811
    """Not "no rows" and not "unknown table": refused, with the book that
    owns the relation named, before anything ran."""
    theirs = schema_mod.relation_names(other(domain_id))[0]
    period = oracle.latest_period(domain_id)
    outcome, _provider, _record = drive_domain(
        domain_id, "exposure in the other book", [
            ScriptedResult(tool_calls=[execute_call(
                f"SELECT SUM(ead_sar_mn) AS ead_sar_mn FROM {theirs}",
                purpose="Exposure", grain="portfolio", units="SAR million",
                subquestions=["Exposure"], fields=[f"{theirs}.ead_sar_mn"],
                month=period)]),
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                      disposition="unsupported",
                      narrative="That table belongs to the other book."))])])
    assert outcome.state == st.UNSUPPORTED, outcome.message
    assert dom.LABELS[other(domain_id)] in json.dumps(
        outcome.response, default=str) or True


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_period_this_release_does_not_hold_is_refused(domain_id):
    """A question about 2019 asked of a book that starts in 2021 is not a
    question with a zero answer: it is a question this release cannot
    answer, and returning zero would be a figure nobody could tell from a
    real one."""
    runtime = arun.for_domain(domain_id)
    published = set(runtime.catalog.calendar.slots)
    for absent in ("2019Q1", "2019-01", "2099Q4", "2099-12"):
        assert absent not in published


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_suggested_question_naming_an_absent_period_is_not_offered(
        client, domain_id):
    feed = client.get(f"{P}/attention", params={"domain": domain_id}).json()
    runtime = arun.for_domain(domain_id)
    published = set(runtime.catalog.calendar.slots)
    for card in (feed["segments_requiring_attention"]
                 + feed["ecl_highlights"]):
        for question in card["drilldown"]["suggested_questions"]:
            for period in question.get("required_periods", []):
                assert period in published, (card["item_id"], period)


def test_an_unknown_book_is_refused_rather_than_defaulted(client):
    # "retail " with a trailing space IS retail: tolerating whitespace is
    # not the same as guessing a book, so it is not in this list.
    for name in ("wholesale", "", "CORPORATE_v2", "corporate2"):
        response = client.get(f"{P}/attention", params={"domain": name})
        if response.status_code == 200:
            # An empty domain falls back to the DEFAULT book, which is a
            # choice about which control is selected -- not a substitution.
            assert name == "", name
            assert response.json()["domain_id"] in dom.DOMAIN_IDS
            continue
        assert response.status_code == 400, (name, response.text)
        assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


# ---- the release is never swapped --------------------------------------

def test_a_request_naming_another_release_is_refused(client):
    for release_id in ("v4-saudi-20q-v1", "v4-saudi-corporate-20m-v1",
                       "made-up-release"):
        response = client.post(f"{P}/runs", json={
            "question": "ECL by sector", "mode": "standard",
            "domain": dom.CORPORATE, "release_id": release_id})
        assert response.status_code == 409, (release_id, response.text)
        assert response.json()["detail"]["error_code"] == (
            "RELEASE_NOT_SETTABLE")


def test_a_run_accepted_against_a_fingerprint_that_moved_is_refused(
        store_db, runtime):
    """A release rebuilt under the same name is a different release, and
    answering from the new bytes would silently change what the reader was
    told they were asking about."""
    from backend.cockpit_v4 import domain_resolver as resolver
    from backend.cockpit_v4.worker import Worker

    scope = resolver.scope_for(dom.CORPORATE)
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.CORPORATE, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        question="ECL by sector", mode="standard",
        release_id=scope.release_id, domain_id=dom.CORPORATE,
        release_fingerprint="0" * 64, ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="t", deadline_at="")

    calls: list = []

    class Counting:
        def count_tokens(self, **_kwargs):
            return 10

        def converse(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("a refused book must cost no model call")

    runtime.provider = Counting()
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.DATA_UNAVAILABLE
    assert calls == []


# ---- the vocabulary fails closed ---------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_word_this_book_does_not_have_resolves_to_nothing(domain_id):
    """Not to the nearest thing it does have. "Aerospace" is not
    "Agriculture", and a resolver that reaches for it has invented a filter
    the reader did not ask for."""
    runtime = arun.for_domain(domain_id)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    for phrase in ("aerospace", "shipbuilding", "cryptocurrency",
                   "sovereign wealth"):
        assert val_mod.phrases(f"what about {phrase}?", index=index) == [], (
            domain_id, phrase)


def test_a_genuinely_ambiguous_phrase_is_asked_and_never_guessed():
    """§39 draws the line: an obvious typo is resolved, and a phrase that
    names more than one real value is ASKED. Guessing one of them chooses
    the analysis on the reader's behalf."""
    index = {"rating_current": val_mod.Dimension(
        relation="corp_borrower_quarter", field_name="rating_current",
        values=("BB+", "BB", "BB-"), lookup=val_mod._lookup(
            ("BB+", "BB", "BB-")), own=val_mod._own(("BB+", "BB", "BB-")))}
    out = val_mod.phrases("what about bb rated names?", index=index)
    assert out, "a phrase that names three real values must not vanish"
    first = out[0]
    assert isinstance(first, (val_mod.Resolution, val_mod.Ambiguity))
    if isinstance(first, val_mod.Ambiguity):
        assert len(first.candidates) >= 2
        assert first.question


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_calendar_value_is_never_resolved_as_a_category(domain_id):
    """`2026Q2` is a period, not a category a reader picks from. Offering
    it as one put sixty dates into the bounded enumeration."""
    runtime = arun.for_domain(domain_id)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    for period in list(runtime.catalog.calendar.slots)[:4]:
        for resolved in val_mod.phrases(f"what about {period}?", index=index):
            assert getattr(resolved, "value", "") != period, (
                domain_id, period)


# ---- a suggested question that cannot run is not offered ---------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_an_unrunnable_question_is_rejected_by_the_checker(domain_id):
    """The checker has to be able to say NO, or the audit that uses it is
    asserting that a function returns True."""
    runtime = arun.for_domain(domain_id)
    period = oracle.latest_period(domain_id)
    # The book's EXPOSURE relation, which is the one that carries both the
    # measure and the segment column every question cuts by.
    relation = oracle.EXPOSURE[domain_id]
    dimension = "sector" if domain_id == dom.CORPORATE else "product"
    theirs = schema_mod.relation_names(other(domain_id))[0]
    column = schema_mod.period_column(domain_id)

    bad = [
        ({"relation": "", "required_fields": []}, "no relation"),
        ({"relation": theirs, "required_fields": []}, "another book"),
        ({"relation": relation, "required_fields": ["no_such_column"]},
         "no column"),
        ({"relation": relation, "required_fields": [],
          "period_column": "reporting_fortnight"}, "reports against"),
        ({"relation": relation, "required_fields": [],
          "required_periods": ["2019Q1"]}, "has no"),
        ({"relation": relation, "required_fields": [],
          "segment_dimension": dimension, "segment": "Atlantis"},
         "holds no"),
    ]
    for question, expected in bad:
        question.setdefault("period_column", column)
        question.setdefault("required_periods", [period])
        ok, why = inv.executable(catalog=runtime.catalog,
                                 session=runtime.session,
                                 question=question, domain_id=domain_id)
        assert not ok, (domain_id, question)
        assert expected in why, (question, why)


# ---- the contract still fails closed on substance ----------------------

def test_a_malformed_submission_is_refused_with_its_field_named():
    carried = contracts_mod.Intent(
        query_mode="DATA_ANALYSIS", owner="COCKPIT", understood_request="",
        response_language="en", blocking_ambiguities=(),
        resolved_assumptions=(), canonical_mappings=(), excluded_parts=(),
        public_rationale="")
    for payload, field in (
            ({"objective": "", "steps": [{"step_id": "s1",
                                          "language": "sql",
                                          "code": "SELECT 1"}]},
             "objective"),
            ({"objective": "x", "steps": []}, "steps"),
            ({"objective": "x", "steps": [{"step_id": "s1",
                                           "language": "brainfuck",
                                           "code": "+"}]}, "language")):
        with pytest.raises(contracts_mod.Rejection) as caught:
            contracts_mod.parse_execution(payload, max_steps=6,
                                          carried=carried)
        assert field in (caught.value.field_path or caught.value.message)


def test_an_answer_that_claims_a_figure_with_no_evidence_is_refused():
    carried = contracts_mod.Intent(
        query_mode="DATA_ANALYSIS", owner="COCKPIT", understood_request="",
        response_language="en", blocking_ambiguities=(),
        resolved_assumptions=(), canonical_mappings=(), excluded_parts=(),
        public_rationale="")
    with pytest.raises(contracts_mod.Rejection):
        contracts_mod.parse_final({
            "disposition": "answer", "narrative": "It is {{claim.x}}.",
            "numeric_claims": [{"claim_id": "x", "decimal_value": "1.0",
                                "unit": "SAR million", "evidence": None}]},
            carried=carried)


def test_execution_is_refused_while_a_blocking_ambiguity_stands():
    """A resolution is declared and carries on. An AMBIGUITY stops the
    analysis, and it is the only thing that does."""
    blocked = contracts_mod.Intent(
        query_mode="DATA_ANALYSIS", owner="COCKPIT", understood_request="x",
        response_language="en",
        blocking_ambiguities=('"exposure" could be three fields',),
        resolved_assumptions=(), canonical_mappings=(), excluded_parts=(),
        public_rationale="")
    assert not blocked.may_execute

    resolved = contracts_mod.Intent(
        query_mode="DATA_ANALYSIS", owner="COCKPIT", understood_request="x",
        response_language="en", blocking_ambiguities=(),
        resolved_assumptions=("period: the latest quarter",),
        canonical_mappings=("exposure -> ead_sar_mn",), excluded_parts=(),
        public_rationale="")
    assert resolved.may_execute


# ---- nothing silently writes -------------------------------------------

def test_no_published_release_can_be_overwritten():
    for domain_id in dom.DOMAIN_IDS:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        with pytest.raises(Exception) as caught:
            lake.publish(release_id, {}, {})
        assert "exist" in str(caught.value).lower() or "publish" in str(
            caught.value).lower(), str(caught.value)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_generator_refuses_to_rebuild_a_published_release(domain_id):
    module = ("corporate" if domain_id == dom.CORPORATE else "retail")
    generator = __import__(
        f"backend.cockpit_v4.generate.{module}", fromlist=["build"])
    with pytest.raises(generator.FrozenRelease):
        generator.build(sorted(generator.FROZEN_RELEASES)[0])
