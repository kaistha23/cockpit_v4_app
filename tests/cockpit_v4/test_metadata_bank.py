"""REAL CATALOGUE · NO MODEL · BOTH BOOKS.

§37. D01-D10: the metadata questions, asked of each book's own catalogue.

These are the questions a reader asks before an analytical one -- what is in
here, what does this column mean, how do these tables join, what does this
book NOT have. Every answer is served by `CatalogService` from the book the
request is scoped to, so the same question asked in the two books returns two
different, correct answers rather than one answer with a label swapped.

The model is not involved. What is under test is whether the catalogue can
ANSWER, not whether an analyst chooses to ask.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod

from . import domain_oracles as oracle
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4.catalog_tool import CatalogService
from backend.cockpit_v4.contracts import parse_catalog

#: The dimension each book segments by, and the measure each is denominated
#: in. D06 and D07 turn on the two being DIFFERENT between the books.
PRIMARY = {
    dom.CORPORATE: {"relation": "corp_facility_quarter", "segment": "sector",
                    "counterparty": "borrower_id", "amount": "ead_sar_mn"},
    dom.RETAIL: {"relation": "retail_account_month", "segment": "product",
                 "counterparty": "customer_id", "amount": "ead_sar_mn"},
}


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


@pytest.fixture(scope="module")
def services():
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        book = arun.for_domain(domain_id)
        out[domain_id] = CatalogService(
            catalog=book.catalog,
            scope=book.read_scope({"id": "u1",
                                   "tenant": lake.DEFAULT_TENANT}),
            session=book.session)
    return out


def ask(service, **over):
    body = {"intent": {"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT",
                       "understood_request": "metadata",
                       "response_language": "en",
                       "blocking_ambiguities": [], "resolved_assumptions": [],
                       "canonical_mappings": [], "excluded_parts": [],
                       "public_rationale": "metadata"},
            "query": "", "relation_ids": [], "field_ids": [],
            "detail": ["discovery"], "reporting_periods": [],
            "sample_rows": 0, "cursor": ""}
    body.update(over)
    return service.inspect(parse_catalog(body))


BOOKS = list(dom.DOMAIN_IDS)


# ---- D01: what is in this book -----------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d01_what_tables_does_this_book_have(services, domain_id):
    result = ask(services[domain_id], detail=["discovery"])
    # An unscoped request is answered with what IS readable rather than with
    # the whole catalogue, which is the documented behaviour and also the
    # answer to "what tables does this book have".
    named = set(result["authorized_relations"])
    expected = set(schema_mod.relation_names(domain_id))
    assert named == expected, result
    other = next(d for d in BOOKS if d != domain_id)
    assert not (named & set(schema_mod.relation_names(other)))


# ---- D02: what does one column mean -------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d02_what_does_this_column_mean(services, domain_id):
    relation = PRIMARY[domain_id]["relation"]
    field_id = f"{relation}.{PRIMARY[domain_id]['amount']}"
    result = ask(services[domain_id], detail=["fields"], field_ids=[field_id])
    fields = {f["field_id"]: f for f in result["fields"]}
    assert set(fields) == {field_id}
    entry = fields[field_id]
    assert entry["definition"]
    assert entry["dtype"]
    assert entry["unit"] == "rcy"
    assert entry["aggregation"] == "additive"


# ---- D03: what UNIT is this in ------------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d03_what_currency_and_scale_is_this_book_in(services, domain_id):
    book = arun.for_domain(domain_id)
    assert book.catalog.reporting_currency == "SAR"
    assert book.catalog.amount_scale == "million"
    assert book.money_unit == "SAR million"
    outline = book.catalog.outline()
    assert outline["domain_id"] == domain_id
    assert outline["dataset_release_id"] == dom.DEFAULT_RELEASES[domain_id]


# ---- D04: how do these tables join --------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d04_how_do_these_tables_join(services, domain_id):
    result = ask(services[domain_id], detail=["relationships"],
                 relation_ids=[PRIMARY[domain_id]["relation"]])
    joins = result["relationships"]
    assert joins, "a book that cannot say how it joins cannot be queried"
    names = set(schema_mod.relation_names(domain_id))
    for join in joins:
        assert join["left"] in names and join["right"] in names
        assert join["on"], join
        assert join["warning"], (
            "a stated join without its repetition warning is half a fact")


# ---- D05: what period does it cover -------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d05_what_period_does_this_book_cover(services, domain_id):
    result = ask(services[domain_id], detail=["coverage"])
    coverage = result["coverage"]
    noun = schema_mod.period_noun(domain_id)
    other = "month" if noun == "quarter" else "quarter"

    assert coverage["reporting_frequency"] == schema_mod.frequency(domain_id)
    assert coverage["period_noun"] == noun
    # Both the neutral key and this book's own noun, and NEVER the other
    # book's noun: §3. A Corporate coverage block that lists
    # `reporting_months` invites a monthly answer over quarterly data.
    assert coverage["reporting_periods"] == list(oracle.periods(domain_id))
    assert len(coverage[f"reporting_{noun}s"]) == 20
    assert coverage[f"reporting_{noun}s"][-1] == oracle.latest_period(
        domain_id)
    assert f"reporting_{other}s" not in coverage, (
        f"a {schema_mod.frequency(domain_id)} book must not describe its "
        f"periods as {other}s")


# ---- D06: what does this book segment BY --------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d06_what_is_the_segment_dimension_here(services, domain_id):
    book = arun.for_domain(domain_id)
    block = sem.block(book.catalog)
    segment = PRIMARY[domain_id]["segment"]
    # "Segment" is an ALIAS of this book's own dimension. The packet must
    # carry it as one: an analyst told only about `sector` has not been told
    # what the word in the question resolves to.
    entry = next(m for m in block["canonical_measures"]
                 if m["column"] == segment)
    assert "segment" in ([entry["term"]] + entry.get("also_known_as", []))
    other = next(d for d in BOOKS if d != domain_id)
    assert segment != PRIMARY[other]["segment"]
    assert not any(m["column"] == PRIMARY[other]["segment"]
                   for m in block["canonical_measures"])


# ---- D07: what is a "customer" here -------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d07_what_does_customer_mean_in_this_book(services, domain_id):
    book = arun.for_domain(domain_id)
    mapped = {m["term"]: m for m in sem.measures(book.catalog)}
    assert mapped["customer"]["field"] == PRIMARY[domain_id]["counterparty"]
    if domain_id == dom.CORPORATE:
        assert "borrower" in mapped["customer"]["means"].lower()
        assert "wholesale" in mapped["customer"]["note"].lower()
    else:
        assert "retail customer" in mapped["customer"]["means"].lower()
        assert "no borrower" in mapped["customer"]["note"].lower()


# ---- D08: what does this book NOT have ----------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d08_a_column_this_book_does_not_have_is_said_so(services, domain_id):
    relation = PRIMARY[domain_id]["relation"]
    result = ask(services[domain_id], detail=["fields"],
                 field_ids=[f"{relation}.not_a_real_column"])
    unresolved = (result.get("unresolved_fields") or {}).get("requested", [])
    assert f"{relation}.not_a_real_column" in unresolved, result
    assert "No similar field was substituted" in \
        result["unresolved_fields"]["note"]
    alternatives = (result.get("alternatives") or {}).get(
        f"{relation}.not_a_real_column")
    assert alternatives is not None, (
        "an unresolved column must come back with the columns that DO exist")


# ---- D09: a relation of the other book ----------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d09_the_other_books_table_is_named_as_the_other_books(domain_id):
    other = next(d for d in BOOKS if d != domain_id)
    book = arun.for_domain(domain_id)
    theirs = schema_mod.relation_names(other)[0]
    with pytest.raises(cat.CrossDomainAccess) as raised:
        book.catalog.require_relation(theirs)
    message = str(raised.value)
    assert dom.LABELS[other] in message
    assert dom.LABELS[domain_id] in message
    assert "thread in that book" in message


# ---- D10: which release am I reading ------------------------------------

@pytest.mark.parametrize("domain_id", BOOKS)
def test_d10_which_release_and_which_bytes(domain_id):
    book = arun.for_domain(domain_id)
    summary = book.release_summary()
    assert summary["dataset_release_id"] == dom.DEFAULT_RELEASES[domain_id]
    assert summary["domain_id"] == domain_id
    assert summary["domain_label"] == dom.LABELS[domain_id]
    assert len(summary["release_fingerprint"]) == 64
    assert "no real" in str(summary["not_client_data"]).lower(), (
        "the release must say, in words, that it describes nobody real")
    assert summary["reporting_currency"] == "SAR"
    assert summary["geography_name"] == "Saudi Arabia"
    other = arun.for_domain(next(d for d in BOOKS if d != domain_id))
    assert summary["release_fingerprint"] != other.release_fingerprint
