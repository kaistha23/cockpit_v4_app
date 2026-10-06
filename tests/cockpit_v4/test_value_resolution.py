"""REAL RELEASE · REAL VALUES · NO MODEL.

§23-§30. The live failure was a follow-up of five words:

    "and within prject finance?"

The run answered it by asking the catalogue for `product_name`, then
`product_category`, then `product_type`, then `product`, then
`product_code`. It had `product_type` in the first call. What it did not
have was any way to decide that the string the reader typed was the string
the book spells `Project Finance`, so it kept looking for a field that would
obviously contain it -- and invented field names when none did.

That is VALUE RESOLUTION, and it is a different question from SCHEMA
RESOLUTION. This file tests it as one: against the values the releases
actually hold, read out of the releases, with no model in the loop.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod

from . import domain_oracles as oracle
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import values as val


@pytest.fixture(scope="module")
def books():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    val.clear_cache()
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        runtime = arun.for_domain(domain_id)
        out[domain_id] = val.dimensions(session=runtime.session,
                                        catalog=runtime.catalog)
    return out


@pytest.fixture
def corporate(books):
    return books[dom.CORPORATE]


@pytest.fixture
def retail(books):
    return books[dom.RETAIL]


def one(index, phrase) -> val.Resolution:
    """Resolve, and insist it resolved to exactly one value."""
    outcome = val.resolve(phrase, index=index)
    assert isinstance(outcome, val.Resolution), (
        f"{phrase!r} did not resolve to a single value: {outcome!r}")
    return outcome


# ---- normalisation: the same request written many ways ------------------

@pytest.mark.parametrize("phrase", [
    "project finance", "Project Finance", "PROJECT FINANCE",
    "project_finance", "Project-Finance", "project—finance",
    "  project   finance  ", "Project finance.", "project/finance",
    "PROJECT_FINANCE", "project  _  finance",
])
def test_every_spelling_of_project_finance_is_the_same_category(
        corporate, phrase):
    """§30. Case, spaces, underscores, hyphens and punctuation are the
    reader's typing, not a different question."""
    resolved = one(corporate, phrase)
    assert resolved.value == "project_finance"
    assert resolved.field_name == "product_type"
    assert resolved.exact is True, (
        f"{phrase!r} is a spelling variant, not a typo: it must resolve "
        f"exactly, with no fuzzy matching involved")


@pytest.mark.parametrize("phrase", [
    "prject finance", "projet finance", "project finence", "proect finance",
    "project financ", "porject finance",
])
def test_a_bounded_typo_still_reaches_the_right_category(corporate, phrase):
    """§27. Conservative, bounded tolerance. One slip in a two-word phrase
    is a slip; it is not a different product."""
    resolved = one(corporate, phrase)
    assert resolved.value == "project_finance"
    assert resolved.exact is False
    assert resolved.similarity >= val.MIN_SIMILARITY


def test_all_the_variants_agree_with_each_other(corporate):
    """§30's actual demand: they must all resolve to the SAME category."""
    forms = ["within project finance", "within project_finance",
             "within Project-Finance", "within prject finance"]
    values = {one(corporate, f.removeprefix("within ")).value for f in forms}
    assert values == {"project_finance"}


# ---- aliases, where they are governed and unambiguous -------------------

@pytest.mark.parametrize("phrase,expected", [
    ("info tech", "Information Technology"),
    ("it", "Information Technology"),
    ("information technology", "Information Technology"),
    ("construction", "Construction"),
    ("healthcare", "Healthcare"),
    ("real estate", "Real Estate"),
    ("metals and mining", "Metals and Mining"),
])
def test_corporate_sector_words_a_reader_actually_types(
        corporate, phrase, expected):
    resolved = one(corporate, phrase)
    assert resolved.value == expected
    assert resolved.field_name in {"sector", "sub_sector"}


@pytest.mark.parametrize("phrase,expected", [
    ("personal finance", "Personal Finance"),
    ("personl finance", "Personal Finance"),
    ("credit cards", "Credit Card"),
    ("credit card", "Credit Card"),
    ("cards", "Credit Card"),
    ("mortgages", "Mortgage"),
    ("mortgage", "Mortgage"),
    ("auto", "Auto Finance"),
    ("auto finance", "Auto Finance"),
])
def test_retail_product_words_a_reader_actually_types(
        retail, phrase, expected):
    resolved = one(retail, phrase)
    assert resolved.value == expected
    assert resolved.field_name == "product"


def test_a_real_value_outranks_an_authored_alias(corporate):
    """`Mining` is a sub-sector this book holds. The alias table also offers
    "mining" for `Metals and Mining`. The book wins: an alias is a
    convenience and a value is a fact."""
    assert "Mining" in corporate["sub_sector"].values
    assert one(corporate, "mining").value == "Mining"


# ---- refusing to guess ---------------------------------------------------

def test_a_half_named_family_is_asked_about_not_chosen(corporate):
    """§26. "finance" names four products in this book. Choosing one of
    them is choosing the analysis.

    FOUR, not three: supply chain finance was added to the book and the
    word it shares with the others is exactly the one a reader types. A
    resolver that narrowed to the three it used to know would be choosing
    the analysis and hiding the newest product while doing it.
    """
    outcome = val.resolve("finance", index=corporate)
    assert isinstance(outcome, val.Ambiguity)
    assert {c.value for c in outcome.candidates} == {
        "asset_finance", "project_finance", "trade_finance",
        "supply_chain_finance"}
    # Asked in the reader's spelling, whatever the book's own is.
    assert outcome.question == (
        "Did you mean Asset Finance, Project Finance, Supply Chain Finance "
        "or Trade Finance?")


def test_the_same_half_named_family_is_different_in_the_other_book(retail):
    outcome = val.resolve("finance", index=retail)
    assert isinstance(outcome, val.Ambiguity)
    assert {c.value for c in outcome.candidates} == {"Auto Finance",
                                                     "Personal Finance"}


@pytest.mark.parametrize("phrase", [
    "xyzzy", "", "   ", "islamic microfinance", "shipping and freight",
    "12345",
])
def test_a_value_this_book_does_not_hold_is_never_invented(
        corporate, phrase):
    """§28. The resolver has no authority to add a category to a release."""
    outcome = val.resolve(phrase, index=corporate)
    if outcome is None:
        return
    assert isinstance(outcome, val.Ambiguity) or (
        outcome.value in corporate[outcome.field_name].values)


def test_no_resolution_names_a_value_outside_the_book(corporate, retail):
    for index in (corporate, retail):
        for name, dim in index.items():
            for key, value in dim.lookup.items():
                assert value in dim.values, (
                    f"{name}: {key!r} maps to {value!r}, which the release "
                    f"does not hold")


def test_one_book_never_resolves_the_other_books_categories(
        corporate, retail):
    """Release isolation, at the value layer. A Corporate thread must not
    quietly understand "credit cards", and a Retail thread must not quietly
    understand "project finance": the value does not exist in its book."""
    assert val.resolve("credit card", index=corporate) is None
    assert val.resolve("covenant breach", index=retail) is None
    for phrase in ("project finance", "trade finance", "working capital"):
        outcome = val.resolve(phrase, index=retail)
        assert not isinstance(outcome, val.Resolution) or \
            outcome.value not in {"project_finance", "trade_finance",
                                  "working_capital"}


# ---- reading a whole question -------------------------------------------

def test_the_live_follow_up_resolves_in_one_step(corporate):
    """The exact wording that cost a live run its whole budget."""
    found = val.phrases("and within prject finance?", index=corporate)
    assert len(found) == 1
    assert isinstance(found[0], val.Resolution)
    assert found[0].value == "project_finance"
    assert found[0].raw == "prject finance", (
        "the recorded term must be the words that carry the category, not "
        "the preposition in front of them")


def test_the_other_live_follow_up_resolves_its_sector(corporate):
    found = val.phrases("How is risk building in Information Technology?",
                        index=corporate)
    values = [f.value for f in found if isinstance(f, val.Resolution)]
    assert "Information Technology" in values


def test_the_longer_reading_wins_over_the_shorter_one(corporate):
    """"project finance" is tried before "finance", so the unambiguous
    reading is the one that survives."""
    found = val.phrases("show me project finance", index=corporate)
    assert [f.value for f in found if isinstance(f, val.Resolution)] == [
        "project_finance"]
    assert not [f for f in found if isinstance(f, val.Ambiguity)]


def test_an_exact_match_wins_over_a_near_match_around_it(retail):
    """"how are credit cards doing" must record "credit cards", not "credit
    cards doing": a near match on a longer window must never outrank an
    exact match sitting inside it."""
    found = val.phrases("how are credit cards doing", index=retail)
    assert [(f.raw, f.value) for f in found] == [("credit cards",
                                                  "Credit Card")]


def test_two_categories_in_one_question_both_resolve(corporate):
    found = val.phrases("compare construction and project-finance",
                        index=corporate)
    assert {f.value for f in found if isinstance(f, val.Resolution)} == {
        "Construction", "project_finance"}


def test_a_question_naming_no_category_resolves_nothing(corporate):
    assert val.phrases("what is the ECL for the latest month",
                       index=corporate) == []


@pytest.mark.parametrize("filler", ["and", "the", "within", "in", "of",
                                    "show me", "please"])
def test_filler_words_are_never_a_category(corporate, filler):
    """"and" is a whole token of four sectors in this book. It is not a
    question about any of them."""
    assert val.phrases(filler, index=corporate) == []


# ---- what a run records --------------------------------------------------

def test_an_exact_resolution_is_recorded_as_a_canonical_mapping(corporate):
    resolved = one(corporate, "project finance")
    mapping = resolved.as_mapping()
    assert mapping == {"term": "project finance",
                       "field": "corp_facility_quarter.product_type",
                       "value": "project_finance"}
    assert "project finance" in resolved.as_assumption()
    assert "project_finance" in resolved.as_assumption()


def test_a_corrected_spelling_says_so_in_its_own_words(corporate):
    resolved = one(corporate, "prject finance")
    said = resolved.as_assumption()
    assert "prject finance" in said and "Project Finance" in said, said
    assert said.startswith("Interpreted"), (
        "a correction must read as a correction, so the reader can see it "
        "was made and disagree with it")


# ---- the bounded index ---------------------------------------------------

def test_only_low_cardinality_dimensions_are_enumerated(books):
    for domain_id, index in books.items():
        for name, dim in index.items():
            assert 0 < len(dim.values) <= val.MAX_CARDINALITY, (
                f"{domain_id}.{name} has {len(dim.values)} values")


def test_identifiers_periods_and_free_text_are_not_dimensions(books):
    banned = ("borrower_id", "facility_id", "customer_id", "account_id",
              "tenant_id", "dataset_release_id",
              "borrower_name", "rating_previous", "score_band_previous")
    for domain_id, index in books.items():
        for name in banned:
            assert name not in index, f"{domain_id} indexed {name}"

    # A CALENDAR is never a dimension, in either book and under either
    # noun. Naming only `reporting_month` here was what let the Corporate
    # book's `reporting_quarter`, `origination_quarter` and `waiver_quarter`
    # into the bounded enumeration the moment it started reporting quarters:
    # sixty dates offered to a reader as categories to pick from.
    for domain_id, index in books.items():
        for name in index:
            assert not any(name.endswith(f"_{noun}")
                           for noun in schema_mod.PERIOD_NOUNS.values()), (
                f"{domain_id} indexed the calendar column {name}")


def test_the_book_the_reader_is_in_is_the_book_that_is_indexed(books):
    assert "product_type" in books[dom.CORPORATE]
    assert "covenant_type" in books[dom.CORPORATE]
    assert "sector" in books[dom.CORPORATE]
    assert "product_type" not in books[dom.RETAIL]
    assert "covenant_type" not in books[dom.RETAIL]
    assert "sector" not in books[dom.RETAIL]
    assert "product" in books[dom.RETAIL]
    assert "delinquency_bucket" in books[dom.RETAIL]
    assert "score_band" in books[dom.RETAIL]
    assert "product" not in books[dom.CORPORATE]
    assert "score_band" not in books[dom.CORPORATE]


def test_the_packet_block_enumerates_what_it_says_it_enumerates(books):
    for index in books.values():
        block = val.block(index)
        names = {d["field"] for d in block["dimensions"]}
        assert names == set(index)
        for entry in block["dimensions"]:
            assert entry["count"] == len(entry["values"])
            assert entry["values"] == list(index[entry["field"]].values)


def test_the_block_stays_small_enough_to_carry_every_turn(books):
    import json
    for domain_id, index in books.items():
        size = len(json.dumps(val.block(index)))
        assert size < 8000, (
            f"{domain_id} enumeration is {size} characters; the point of a "
            f"bounded index is that it is bounded")


def test_the_index_is_cached_per_release_not_rebuilt_per_question():
    runtime = arun.for_domain(dom.CORPORATE)
    first = val.dimensions(session=runtime.session, catalog=runtime.catalog)
    second = val.dimensions(session=runtime.session, catalog=runtime.catalog)
    assert first is second
    val.clear_cache()
    third = val.dimensions(session=runtime.session, catalog=runtime.catalog)
    assert third is not first
    assert set(third) == set(first)


# ---- normalisation and display, on their own ----------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Project Finance", "project finance"),
    ("project_finance", "project finance"),
    ("PROJECT-FINANCE", "project finance"),
    ("  Project   Finance!  ", "project finance"),
    ("Agriculture and Agri-processing", "agriculture and agri processing"),
    ("90+", "90"),
    ("", ""),
])
def test_normalisation_makes_one_form_of_the_readers_many(raw, expected):
    assert val.normalize(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("project_finance", "Project Finance"),
    ("working_capital", "Working Capital"),
    ("Project Finance", "Project Finance"),
    ("Metals and Mining", "Metals and Mining"),
])
def test_a_canonical_value_is_shown_in_the_readers_form(raw, expected):
    assert val.pretty(raw) == expected


def test_title_case_product_types_would_resolve_the_same_way():
    """The Corporate release spells its facility types in snake_case. A book
    that spelled them for people would resolve the reader's words exactly the
    same way: the resolver reads the value, not its typography."""
    values = ("Project Finance", "Term Loan", "Working Capital",
              "Revolving Credit", "Trade Finance", "Overdraft",
              "Guarantee", "Asset Finance")
    index = {"product_type": val.Dimension(
        relation="corp_facility_quarter", field_name="product_type",
        values=values, lookup=val._lookup(values), own=val._own(values))}
    for phrase in ("project finance", "Project Finance", "project_finance",
                   "Project-Finance", "PROJECT FINANCE"):
        assert one(index, phrase).value == "Project Finance"
    assert one(index, "prject finance").value == "Project Finance"
    assert one(index, "working capital").value == "Working Capital"
    assert one(index, "revolver").value == "Revolving Credit"
    assert one(index, "term loan").value == "Term Loan"


# ---- the whole path: payload and run semantics ---------------------------

class _Recorder:
    """A provider that records what it was handed and then refuses."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def count_tokens(self, **_kwargs) -> int:
        return 100

    def converse(self, *, system, messages, tools=None, **_kwargs):
        self.sent.append({"system": system, "messages": messages,
                          "tools": tools})
        raise RuntimeError("payload captured")


def _payload(store_db, runtime, domain_id: str, question: str) -> dict:
    """Drive one REAL run to its first provider call and return the bytes."""
    import json

    from backend.cockpit_v4 import domain_resolver as resolver
    from backend.cockpit_v4 import states as st
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
    recorder = _Recorder()
    runtime.provider = recorder
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.FAILED
    assert recorder.sent, "no provider call was made"
    blocks = recorder.sent[0]["system"]
    merged: dict = {}
    for block in blocks:
        try:
            body = json.loads(block.get("text") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(body, dict):
            merged.update(body)
    return merged


@pytest.fixture(autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


def test_the_real_payload_carries_the_resolution_of_the_live_follow_up(
        store_db, runtime):
    """§29. The live run never got this. It got a catalogue and a typo."""
    body = _payload(store_db, runtime, dom.CORPORATE,
                    "and within prject finance?")
    recognised = body["value_resolution"]["recognised"]
    assert [r["value"] for r in recognised] == ["project_finance"]
    entry = recognised[0]
    assert entry["field"] == "product_type"
    assert entry["relation"] == "corp_facility_quarter"
    assert entry["exact"] is False
    assert entry["record_as"] == "resolved_assumption"
    assert body["value_resolution"]["needs_a_question"] == []


def test_the_real_payload_enumerates_this_books_governed_values(
        store_db, runtime):
    body = _payload(store_db, runtime, dom.CORPORATE,
                    "What is EAD by sector for the latest month?")
    fields = {d["field"]: d for d in body["governed_values"]["dimensions"]}
    assert "product_type" in fields
    assert "project_finance" in fields["product_type"]["values"]
    # Every governed value of this field, spelled the book's one way, and
    # checked against the PARQUET rather than a list written down beside it:
    # the enumeration's whole job is to be the release's own vocabulary, so
    # a second hand-maintained copy of it here would only ever be able to go
    # stale in the same direction.
    published = set(oracle.frame(
        dom.CORPORATE, "corp_facility_quarter")["product_type"].unique())
    assert set(fields["product_type"]["values"]) == published
    assert len(published) == 8, (
        "§8's seven facility types, plus the one the bank started writing "
        "in 2025Q4")
    assert all(v == v.lower() and " " not in v for v in published), (
        "a governed value is the identifier SQL filters on, not a label")
    assert "Information Technology" in fields["sector"]["values"]
    # The enumeration is the answer to "which field holds this word", so the
    # analyst has no reason to go looking for `product_name`.
    assert "product" not in fields


def test_the_retail_payload_enumerates_retails_values_and_not_corporates(
        store_db, runtime):
    body = _payload(store_db, runtime, dom.RETAIL,
                    "What is EAD by product for the latest month?")
    fields = {d["field"]: d for d in body["governed_values"]["dimensions"]}
    assert "Credit Card" in fields["product"]["values"]
    assert "product_type" not in fields
    blob = str(body["governed_values"])
    for corporate_only in ("project_finance", "trade_finance",
                           "working_capital", "DSCR"):
        assert corporate_only not in blob


def test_an_ambiguous_value_reaches_the_payload_as_a_question(
        store_db, runtime):
    body = _payload(store_db, runtime, dom.CORPORATE, "and within finance?")
    asked = body["value_resolution"]["needs_a_question"]
    assert len(asked) == 1
    assert asked[0]["term"] == "finance"
    assert {c["value"] for c in asked[0]["candidates"]} == {
        "asset_finance", "project_finance", "trade_finance",
        "supply_chain_finance"}
    assert body["value_resolution"]["recognised"] == []


def test_a_question_naming_no_value_carries_an_empty_resolution(
        store_db, runtime):
    body = _payload(store_db, runtime, dom.CORPORATE,
                    "What is total ECL for the latest month?")
    assert body["value_resolution"]["recognised"] == []
    assert body["value_resolution"]["needs_a_question"] == []
    assert body["value_resolution"]["how_to_use"]


def test_the_resolution_is_merged_into_the_declared_intent():
    """§29. The trace records the reading whether or not the analyst
    repeated it: a resolution the server made is the server's to declare."""
    import dataclasses

    from backend.cockpit_v4.contracts import Intent
    from backend.cockpit_v4.orchestration import Orchestrator

    declared = Intent(
        query_mode="DATA_ANALYSIS", owner="COCKPIT",
        understood_request="EAD within project finance",
        response_language="en", blocking_ambiguities=(),
        resolved_assumptions=("Period not specified: using 2026-08.",),
        canonical_mappings=("EAD read as ead_reported.",),
        excluded_parts=(), public_rationale="")
    orchestrator = object.__new__(Orchestrator)
    object.__setattr__(orchestrator, "value_resolution", {
        "recognised": [
            {"term": "prject finance", "field": "product_type",
             "value": "project_finance", "exact": False,
             "say": "Interpreted 'prject finance' as Project Finance "
                    "(product_type = 'project_finance')."},
            {"term": "construction", "field": "sector",
             "value": "Construction", "exact": True,
             "say": "Read 'construction' as sector = 'Construction'."},
        ]})
    merged = Orchestrator._with_value_resolution(orchestrator, declared)

    assert "Read 'construction' as sector = 'Construction'." in \
        merged.canonical_mappings
    assert "EAD read as ead_reported." in merged.canonical_mappings
    assert any("prject finance" in line
               for line in merged.resolved_assumptions)
    assert "Period not specified: using 2026-08." in merged.resolved_assumptions
    # A resolution is never a refusal.
    assert merged.blocking_ambiguities == ()
    assert merged.may_execute is True
    # And it is idempotent: re-declaring the same intent must not stack.
    twice = Orchestrator._with_value_resolution(orchestrator, merged)
    assert twice.to_dict() == merged.to_dict()
    assert dataclasses.is_dataclass(merged)


def test_a_run_with_no_value_resolution_leaves_the_intent_alone():
    from backend.cockpit_v4.contracts import Intent
    from backend.cockpit_v4.orchestration import Orchestrator

    declared = Intent(
        query_mode="PRODUCT_HELP", owner="COCKPIT",
        understood_request="who are you", response_language="en",
        blocking_ambiguities=(), resolved_assumptions=(),
        canonical_mappings=(), excluded_parts=(), public_rationale="")
    orchestrator = object.__new__(Orchestrator)
    object.__setattr__(orchestrator, "value_resolution", {})
    assert Orchestrator._with_value_resolution(
        orchestrator, declared) is declared
