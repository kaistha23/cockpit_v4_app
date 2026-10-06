"""REAL DATABASE · MODEL MOCK · REAL ROUTES. The Mac UAT, replayed.

§46, §47. Every failure the latest Mac run produced, as a fixture that fails
on the code that produced it and passes on the code that replaced it.

What a replay fixture is for
----------------------------
A round that fixes what it was shown fixes what it was shown. These are the
exact shapes the live run produced -- not paraphrases of them -- so that the
NEXT round cannot reintroduce any of them without a named test going red.

Each case below records: what the reader did, what came back, and what the
structural cause turned out to be. The assertion is on the cause, because an
assertion on the symptom passes the moment the symptom is spelled differently.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import envelope as budget
from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import intent_envelope as ienv
from backend.cockpit_v4 import investigation as inv
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


def envelope_for(domain_id: str):
    runtime = arun.for_domain(domain_id)
    scope = runtime.read_scope({"id": "u1", "tenant": lake.DEFAULT_TENANT})
    return ienv.for_run(
        scope=scope, catalog=runtime.catalog,
        verdict=budget.classify(question="ECL by segment?",
                                catalog=runtime.catalog))


# ---- MAC-01: `intent must be an object` ---------------------------------
#
# The reader asked an ordinary analytical question. Opus called a tool with
# `intent` as a STRING, and the reader was shown "intent must be an object".
# Twice, in two separate rounds, because each round made the parser more
# forgiving instead of removing the restatement.

#: Exactly what the live payload carried, byte for byte in shape.
MAC_STRING_INTENT = {
    "intent": "DATA_ANALYSIS",
    "objective": "Total ECL by sector",
    "subquestions": ["Total ECL by sector"],
    "expected_output_grain": "sector",
    "expected_units": {"ecl_sar_mn": "SAR million"},
    "steps": [{"step_id": "s1", "language": "sql",
               "code": "SELECT 1", "purpose": "x"}],
}


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_mac01_a_string_intent_no_longer_rejects_anything(domain_id):
    """The cause was the FIELD, not the parser. It is gone from every tool
    schema, so the shape that produced this cannot be sent."""
    carried = envelope_for(domain_id).as_intent()
    submission = contracts_mod.parse_execution(
        MAC_STRING_INTENT, max_steps=6, carried=carried)
    assert submission.intent.query_mode == carried.query_mode
    assert submission.steps[0].code == "SELECT 1"

    catalog = arun.for_domain(domain_id).catalog
    for tool in contracts_mod.provider_tools(catalog=catalog):
        assert "intent" not in json.dumps(tool["input_schema"]), tool["name"]


def test_mac01_the_error_string_itself_is_unreachable():
    """A run that has its envelope cannot produce this message, whatever
    the payload. Checked by exhausting the shapes rather than by grepping
    for the sentence, which the next refactor would rename."""
    carried = envelope_for(dom.CORPORATE).as_intent()
    for shape in ("DATA_ANALYSIS", "", None, 7, [], {}, ["a"],
                  {"query_mode": "PRODUCT_HELP"}, "a sentence about intent"):
        merged = contracts_mod.parse_intent(shape, carried=carried)
        assert merged.query_mode == contracts_mod.DATA_ANALYSIS


# ---- MAC-02: "this book is recorded quarterly, not monthly" -------------
#
# A new Corporate thread asked for the latest month. Opus replied that the
# book was quarterly, offered 2026Q2, and answered from the legacy
# twenty-quarter release. The book was monthly at the time; the CAUSE was
# that the tool contract's worked example named a calendar the catalogue
# did not.

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_mac02_no_contract_names_a_calendar_this_book_does_not_have(
        domain_id):
    from backend.cockpit_v4 import semantics as sem

    catalog = arun.for_domain(domain_id).catalog
    noun = schema_mod.period_noun(domain_id)
    other = "month" if noun == "quarter" else "quarter"

    blob = json.dumps(contracts_mod.provider_tools(catalog=catalog))
    assert other not in blob.lower(), (domain_id, other)
    assert noun in blob.lower(), (
        "a contract that names no calendar leaves the analyst to assume one")

    instruction = sem.substitute(
        "{{PERIOD_EXAMPLE}} · {{PERIOD_FIELD}} · {{FREQUENCY}}", catalog)
    assert other not in instruction.lower(), (domain_id, instruction)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_mac02_the_legacy_release_is_not_reachable_from_a_domain_run(
        client, domain_id):
    refused = client.post(f"{P}/runs", json={
        "question": "ECL by segment", "mode": "standard",
        "domain": domain_id, "release_id": "v4-saudi-20q-v1"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error_code"] == "RELEASE_NOT_SETTABLE"


# ---- MAC-03: the Construction investigation died at CALL_LIMIT ----------
#
# A reader clicked the Construction card. The thread read the catalogue,
# read it again, read product knowledge, and stopped. It answered nothing.

def test_mac03_the_construction_card_is_handed_everything_it_needs(client):
    feed = client.get(f"{P}/attention",
                      params={"domain": dom.CORPORATE}).json()
    cards = feed["segments_requiring_attention"] + feed["ecl_highlights"]
    construction = [c for c in cards
                    if "construction" in str(c["segment"]).lower()]
    assert construction, "the authored Construction stress is not on the page"

    runtime = arun.for_domain(dom.CORPORATE)
    for card in construction:
        seed = client.post(
            f"{P}/attention/{card['item_id']}/investigate").json()["seed"]
        packet = inv.analysis_packet(catalog=runtime.catalog,
                                     session=runtime.session, seed=seed)
        assert packet, card["item_id"]
        # Everything the two catalogue calls would have gone looking for.
        assert packet["relation"]
        assert packet["period_column"] == "reporting_quarter"
        assert packet["reporting_period"] == seed["reporting_period"]
        assert packet["fields"], "a packet with no fields is not a packet"
        assert not inv.unresolved(catalog=runtime.catalog, packet=packet)
        if card["scope"] == "segment":
            assert packet["subject"]["value"] == card["segment"]


def test_mac03_every_question_the_construction_card_offers_is_runnable(
        client):
    feed = client.get(f"{P}/attention",
                      params={"domain": dom.CORPORATE}).json()
    runtime = arun.for_domain(dom.CORPORATE)
    for card in feed["segments_requiring_attention"]:
        if "construction" not in str(card["segment"]).lower():
            continue
        for question in card["drilldown"]["suggested_questions"]:
            ok, why = inv.executable(catalog=runtime.catalog,
                                     session=runtime.session,
                                     question=question,
                                     domain_id=dom.CORPORATE)
            assert ok, (question["question"], why)


# ---- MAC-04: "and within prject finance?" ------------------------------
#
# A follow-up with an obvious typo. The run asked a clarifying question
# instead of resolving it, and the reader had to retype.

def test_mac04_the_typo_resolves_without_a_clarifying_question():
    runtime = arun.for_domain(dom.CORPORATE)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    resolved = val_mod.phrases("and within prject finance?", index=index)
    assert len(resolved) == 1
    only = resolved[0]
    assert isinstance(only, val_mod.Resolution)
    assert only.field_name == "product_type"
    assert only.value == "project_finance"
    assert only.raw == "prject finance", "the trace quotes the reader"
    assert not only.exact, "a corrected spelling is declared, not hidden"


def test_mac04_the_same_phrase_is_silent_in_the_book_that_lacks_it():
    """And it does not become a clarifying question there either: the
    Retail book has no project finance, and asking "did you mean Personal
    Finance?" invites the reader to accept an answer to a different
    question."""
    runtime = arun.for_domain(dom.RETAIL)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    assert val_mod.phrases("and within prject finance?", index=index) == []


# ---- MAC-05: "EAD BY SECTOR" showed no Project Finance -----------------
#
# Reported as a defect. It is not one: a product is not a sector. What WAS
# a defect is that nothing on the screen said so, and the follow-up had no
# way to become a filter.

def test_mac05_a_product_is_not_a_sector_and_both_are_enumerated():
    runtime = arun.for_domain(dom.CORPORATE)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    assert "project_finance" in index["product_type"].values
    assert "project_finance" not in index["sector"].values
    assert not set(index["sector"].values) & set(
        index["product_type"].values)


def test_mac05_the_data_builder_shows_which_column_holds_the_value(client):
    """The answer to "why is Project Finance not in this list" is on the
    Data Builder page: the column that holds it, named, with its values."""
    body = client.get(f"{P}/schema",
                      params={"domain": dom.CORPORATE,
                              "relation": "corp_facility_quarter"}).json()
    fields = {f["name"]: f for f in body["fields"]}
    assert "project_finance" in fields["product_type"]["governed_values"]
    assert "project_finance" not in fields["sector"]["governed_values"]
    assert fields["product_type"]["label"] == "Product type"
    assert fields["product_type"]["value_labels"]["project_finance"] == (
        "Project Finance")


# ---- MAC-06: "not started" at 0s ---------------------------------------
#
# The process panel opened with two rows reading "not started", side by
# side, before anything had happened.

def test_mac06_a_run_that_has_done_nothing_has_no_steps():
    """Checked on the SERVER's stream: a panel can only draw a step that
    the stream opened, and the stream opens one when a stage starts."""
    class Recorder:
        def __init__(self):
            self.rows = []

        def append_event(self, event):
            event.seq = len(self.rows) + 1
            self.rows.append(event)
            return event

    store = Recorder()
    emitter = ev.Emitter(store, "run-1", started_monotonic=0.0)
    assert store.rows == []
    assert emitter.stage_instance_id == ""

    emitter.append(ev.RUN_STARTED, stage="accepted", operation="start",
                   status=ev.STATUS_OK, public_message="Working on it.")
    assert len(store.rows) == 1
    body = store.rows[0].to_dict()
    assert body["stage_instance_id"] == "accepted#1"
    assert body["stage_state"] == ev.STAGE_RUNNING
    assert body["closed_stages"] == []


def test_mac06_the_client_has_no_prospective_state_left_to_render():
    """The reducer's own type no longer admits one, which is checked here
    against the source because a TypeScript type cannot be asserted from
    Python and a comment can be deleted."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src"
              / "components" / "cockpit-v4" / "reducer.ts").read_text()
    declaration = re.search(r"export type StepState = ([^;]+);", source)
    assert declaration, "StepState is no longer declared where this looks"
    assert "prospective" not in declaration.group(1), declaration.group(1)
    assert re.search(r"INITIAL_STAGES[^=]*=\s*\[\s*\]", source), (
        "the panel must lay out no stage before one starts")


# ---- MAC-07: Data Builder showed no analytical books -------------------

def test_mac07_the_data_builder_route_serves_both_books(client):
    for domain_id in dom.DOMAIN_IDS:
        body = client.get(f"{P}/schema", params={"domain": domain_id}).json()
        assert body["domain_id"] == domain_id
        assert body["entity_counts"], domain_id
        assert body["subject_areas"], domain_id
        assert body["period_count"] == 20, domain_id


def test_mac07_the_sidebar_page_renders_the_books():
    """The route exists; the PAGE has to mount it, and a page that imports
    nothing renders nothing."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"
            / "data-builder" / "page.tsx").read_text()
    assert "DataBooks" in page, (
        "/data-builder does not render the analytical books")
    assert "data-builder-analytical-books" in page


# ---- the replay matrix is complete -------------------------------------

#: Every failure the Mac UAT produced, and the test that holds it closed.
MAC_FAILURES = {
    "MAC-01": "intent must be an object",
    "MAC-02": "this book is recorded quarterly, not monthly",
    "MAC-03": "the Construction investigation stopped at CALL_LIMIT",
    "MAC-04": "and within prject finance? asked for clarification",
    "MAC-05": "EAD BY SECTOR showed no Project Finance",
    "MAC-06": "the process panel read 'not started' at 0s",
    "MAC-07": "Data Builder showed no analytical books",
}


def test_every_recorded_mac_failure_has_a_replay_here():
    """§47. A failure with no fixture is a failure the next round can
    reintroduce silently."""
    import inspect

    source = inspect.getsource(inspect.getmodule(
        test_every_recorded_mac_failure_has_a_replay_here))
    for case in MAC_FAILURES:
        marker = case.lower().replace("-", "")
        assert f"def test_{marker}_" in source, (
            f"{case} ({MAC_FAILURES[case]}) has no replay fixture")
