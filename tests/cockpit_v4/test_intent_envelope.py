"""MODEL MOCK · REAL PARSER · REAL WORKER · BOTH BOOKS.

§24-§27. The run's intent is authored once, by the server, and no tool asks
the analyst to restate it.

The live failure this replays
-----------------------------
A Mac UAT run against real Opus returned `intent must be an object` -- twice,
in two separate rounds. Each round had made the parser more forgiving. The
field was a REQUIRED NESTED OBJECT on all five tools: nine fields retyped on
every catalogue read, every artifact read, every execution and the answer,
none of which changed between them.

A field restated for no reason is a field that will eventually come back
wrong. So the restatement is gone: `intent` is not a property of any tool
schema, and the run owns an `IntentEnvelope` from its first second holding
what the server already knew -- which book, which release, what kind of turn,
what a period means here, and what the question's own words resolved to.

What this file proves
---------------------
1. No tool schema carries `intent`, in either book. A malformed one cannot
   arrive because it cannot arrive at all.
2. Every shape a live model has ever sent -- a string, a sentence, an object,
   a flat dict, nothing -- is accepted by the real parser once the run has
   its envelope. None of them can produce `intent must be an object`.
3. The envelope is the run's intent BEFORE the first provider call, so the
   first tool call has something to merge into.
4. An answer may refine the reader-facing half and may NOT restate the
   server's half.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import envelope as budget
from backend.cockpit_v4 import intent_envelope as ienv
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4.contracts import provider_tools


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


def envelope_for(domain_id: str, question: str = "What is ECL by segment?"):
    runtime = arun.for_domain(domain_id)
    scope = runtime.read_scope({"id": "u1", "tenant": lake.DEFAULT_TENANT})
    return ienv.for_run(
        scope=scope, catalog=runtime.catalog,
        verdict=budget.classify(question=question, catalog=runtime.catalog))


# ---- 1: the field is gone from the wire --------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_no_tool_asks_the_analyst_for_an_intent(domain_id):
    """§24. `intent must be an object` is structurally impossible when no
    tool has an `intent` to send."""
    catalog = arun.for_domain(domain_id).catalog
    tools = provider_tools(catalog=catalog)
    assert tools, "a run with no tools cannot analyse anything"
    for tool in tools:
        schema = tool["input_schema"]
        assert "intent" not in schema["properties"], tool["name"]
        assert "intent" not in (schema.get("required") or []), tool["name"]
        # Nor nested one level down, which is where it would reappear if
        # somebody "kept it for compatibility".
        assert "intent" not in json.dumps(schema), tool["name"]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_only_the_answer_carries_the_reader_facing_half(domain_id):
    """§26. Flat, optional, and only where it is answer content."""
    catalog = arun.for_domain(domain_id).catalog
    by_name = {t["name"]: t for t in provider_tools(catalog=catalog)}
    finalize = by_name["finalize_response"]["input_schema"]
    for name in contracts_mod.FLAT_INTENT_FIELDS:
        assert name in finalize["properties"], name
        assert name not in finalize["required"], (
            f"{name} is answer content, not a precondition")
    for other in ("inspect_catalog", "execute_analysis", "read_artifact",
                  "inspect_product_knowledge"):
        blob = json.dumps(by_name[other]["input_schema"])
        for name in contracts_mod.FLAT_INTENT_FIELDS:
            assert name not in blob, f"{other} still asks for {name}"


# ---- 2: every shape a live model has sent ------------------------------

SHAPES = [
    ("a bare string", "DATA_ANALYSIS"),
    ("a sentence", "The user wants ECL by sector for the latest period."),
    ("an empty string", ""),
    ("absent", None),
    ("an empty object", {}),
    ("a list", ["DATA_ANALYSIS"]),
    ("a number", 7),
    ("json in a string", '{"understood_request": "ECL by sector"}'),
    ("the old nested object", {"query_mode": "PRODUCT_HELP",
                               "owner": "GENERAL_CREDITPROBE_HELP",
                               "understood_request": "what is ECL"}),
    ("a flat dict", {"understood_request": "ECL by sector",
                     "resolved_assumptions": ["period: latest"]}),
]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
@pytest.mark.parametrize("label,payload",
                         [(label, body) for label, body in SHAPES])
def test_no_shape_of_intent_can_reject_a_run_that_has_an_envelope(
        domain_id, label, payload):
    """§27, replayed through the REAL parser.

    The live failure was a rejection. With the envelope carried, there is no
    shape of this field that produces one -- and the two facts the server
    owns survive every shape.
    """
    carried = envelope_for(domain_id).as_intent()
    merged = contracts_mod.parse_intent(payload, carried=carried)
    assert merged.query_mode == carried.query_mode, label
    assert merged.owner == carried.owner, label


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_model_cannot_restate_the_servers_half(domain_id):
    """§25. The mode and the owner are decisions, not fields to fill in."""
    carried = envelope_for(domain_id).as_intent()
    merged = contracts_mod.parse_intent(
        {"query_mode": "PRODUCT_HELP", "owner": "GENERAL_CREDITPROBE_HELP",
         "understood_request": "something else"},
        carried=carried)
    assert merged.query_mode == carried.query_mode
    assert merged.owner == carried.owner
    assert merged.understood_request == "something else"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_declaration_may_widen_the_mode_and_never_narrow_it(domain_id):
    """§13, §25, at the PARSER.

    The two directions are not symmetric. A run the classifier read as
    product help -- "tell me more", in an analytical thread, names no
    measure at all -- that then declares an analysis is telling us something
    the classifier could not know, and refusing it would leave a real
    analysis on the sixty-second clock. A run that has already read the book
    does not get moved onto the cheaper clock by saying so at the end.
    """
    helpful = contracts_mod.Intent(
        query_mode=contracts_mod.PRODUCT_HELP, owner=contracts_mod.COCKPIT,
        understood_request="", response_language="en",
        blocking_ambiguities=(), resolved_assumptions=(),
        canonical_mappings=(), excluded_parts=(), public_rationale="")

    widened = contracts_mod.parse_intent(
        {"query_mode": contracts_mod.DATA_ANALYSIS}, carried=helpful)
    assert widened.query_mode == contracts_mod.DATA_ANALYSIS
    assert widened.owner == helpful.owner, "the OWNER never moves"

    analytical = envelope_for(domain_id).as_intent()
    assert analytical.query_mode == contracts_mod.DATA_ANALYSIS
    narrowed = contracts_mod.parse_intent(
        {"query_mode": contracts_mod.PRODUCT_HELP}, carried=analytical)
    assert narrowed.query_mode == contracts_mod.DATA_ANALYSIS

    # And every other mode is simply not the answer's to declare.
    for mode in (contracts_mod.THEORY_CONCEPT,
                 contracts_mod.OTHER_FUNCTIONALITY,
                 contracts_mod.UNSUPPORTED):
        assert contracts_mod.parse_intent(
            {"query_mode": mode}, carried=analytical
        ).query_mode == contracts_mod.DATA_ANALYSIS


# ---- 3: the envelope is what the server already knew --------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_envelope_states_this_books_own_calendar(domain_id):
    """§2, §3. Quarters for Corporate, months for Retail, from the book."""
    env = envelope_for(domain_id)
    assert env.domain_id == domain_id
    assert env.domain_label == dom.LABELS[domain_id]
    assert env.release_id == dom.DEFAULT_RELEASES[domain_id]
    assert len(env.release_fingerprint) == 64
    assert env.period.column == schema_mod.period_column(domain_id)
    assert env.period.noun == schema_mod.period_noun(domain_id)
    assert env.period.frequency == schema_mod.frequency(domain_id)
    assert len(env.period.populated) == 20
    assert env.period.latest == env.period.populated[-1]
    assert env.period.previous == env.period.populated[-2]
    step = 4 if env.period.noun == "quarter" else 12
    assert env.period.year_ago == env.period.populated[-(step + 1)]


def test_the_two_books_never_share_an_envelope():
    """§17, §18. Not one field of it."""
    corporate = envelope_for(dom.CORPORATE)
    retail = envelope_for(dom.RETAIL)
    assert corporate.intent_id != retail.intent_id
    assert corporate.release_id != retail.release_id
    assert corporate.release_fingerprint != retail.release_fingerprint
    assert corporate.period.column != retail.period.column
    assert corporate.period.noun != retail.period.noun
    assert not set(corporate.period.populated) & set(retail.period.populated)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_packet_tells_the_analyst_not_to_restate_any_of_it(domain_id):
    env = envelope_for(domain_id)
    packet = env.for_packet()
    assert packet["book"] == dom.LABELS[domain_id]
    assert packet["release"] == dom.DEFAULT_RELEASES[domain_id]
    assert packet["period"]["noun"] == schema_mod.period_noun(domain_id)
    assert "SETTLED" in packet["note"]
    other = "month" if packet["period"]["noun"] == "quarter" else "quarter"
    assert other not in json.dumps(packet["period"])


# ---- 4: what the answer may and may not change --------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_answer_refines_its_own_half_and_nothing_else(domain_id):
    env = envelope_for(domain_id)
    stated = contracts_mod.Intent(
        query_mode=contracts_mod.PRODUCT_HELP,
        owner="GENERAL_CREDITPROBE_HELP",
        understood_request="ECL by sector for the latest period",
        response_language="ar",
        blocking_ambiguities=(),
        resolved_assumptions=("Period not specified: using the latest.",),
        canonical_mappings=("exposure -> ead_sar_mn",),
        excluded_parts=(), public_rationale="Read from the recorded book.")
    after = env.with_intent(stated)

    assert after.query_mode == env.query_mode
    assert after.owner == env.owner
    assert after.domain_id == env.domain_id
    assert after.release_id == env.release_id
    assert after.release_fingerprint == env.release_fingerprint
    assert after.period == env.period
    assert after.intent_id == env.intent_id

    assert after.understood_request == stated.understood_request
    assert after.response_language == "ar"
    assert after.resolved_assumptions == stated.resolved_assumptions
    assert after.canonical_mappings == stated.canonical_mappings
    assert after.public_rationale == stated.public_rationale


def test_a_run_may_widen_into_analysis_and_never_narrow_out_of_it():
    """§13. The budget classifier reads the question before anything is
    spent and can read an analytical question as product help. Submitting
    SQL is the declaration; the run widens rather than being refused."""
    runtime = arun.for_domain(dom.CORPORATE)
    scope = runtime.read_scope({"id": "u1", "tenant": lake.DEFAULT_TENANT})
    # A question about the PRODUCT, naming no measure and no period: the
    # classifier reads it as product help, correctly, before anything is
    # spent.
    verdict = budget.classify(question="What can CreditProbe do?",
                              catalog=runtime.catalog)
    assert not verdict.analytical, verdict.reason
    helpful = ienv.for_run(scope=scope, catalog=runtime.catalog,
                           verdict=verdict)
    assert helpful.query_mode == contracts_mod.PRODUCT_HELP
    assert not helpful.may_execute

    widened = helpful.escalate_to_analysis()
    assert widened.query_mode == contracts_mod.DATA_ANALYSIS
    assert widened.may_execute
    # And widening twice is the same envelope, not a second escalation.
    assert widened.escalate_to_analysis() is widened


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_blocking_ambiguity_stops_execution_and_a_resolution_does_not(
        domain_id):
    """§8 of the earlier round, still true. The two are not the same thing."""
    env = envelope_for(domain_id)
    assert env.may_execute

    resolved = env.with_intent(contracts_mod.Intent(
        query_mode=env.query_mode, owner=env.owner, understood_request="x",
        response_language="en", blocking_ambiguities=(),
        resolved_assumptions=("period: latest",),
        canonical_mappings=(), excluded_parts=(), public_rationale=""))
    assert resolved.may_execute

    blocked = env.with_intent(contracts_mod.Intent(
        query_mode=env.query_mode, owner=env.owner, understood_request="x",
        response_language="en",
        blocking_ambiguities=('"exposure" could be three fields',),
        resolved_assumptions=(), canonical_mappings=(), excluded_parts=(),
        public_rationale=""))
    assert not blocked.may_execute
