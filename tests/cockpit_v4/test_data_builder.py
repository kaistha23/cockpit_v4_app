"""REAL DATABASE · REAL ROUTES · NO MODEL · BOTH BOOKS.

§12-§16, §52, §53. The Data Builder describes the books the Cockpit answers
from, and describes them from the SAME place.

Why this file exists
--------------------
The Mac UAT opened Data Builder from the sidebar and found the onboarding
estate -- domains, dataset families, quality position -- with nothing about
the two published analytical books. A reader could not find out what
CreditProbe could actually be asked about from the screen whose whole job is
to say what data there is.

The deeper risk is the one this file is mostly about. A Data Builder backed
by its own store would be a SECOND description of the data. It would agree
with the catalogue on the day it was written and drift afterwards, and when
it drifted nothing on either screen would say which of the two was wrong.
So every assertion below pairs a Data Builder answer with the analytical
path's own answer and requires them to be the same object, not merely a
similar one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod
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


def book(client, domain_id: str, relation: str = "") -> dict:
    params = {"domain": domain_id}
    if relation:
        params["relation"] = relation
    response = client.get(f"{P}/schema", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ---- §13: what a card must say -----------------------------------------

#: Every fact §13 asks a domain card to carry. Checked as a set so that
#: losing one is a failure here rather than a blank on a card.
CARD_FACTS = (
    "domain_id", "domain_label", "country", "country_name",
    "reporting_currency", "amount_scale", "reporting_frequency",
    "period_noun", "period_column", "reporting_periods", "earliest_period",
    "latest_period", "period_count", "entity_counts", "release_id",
    "release_fingerprint", "status", "relations", "joins", "subject_areas",
    "total_rows",
)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_card_carries_every_fact_the_page_shows(client, domain_id):
    body = book(client, domain_id)
    for fact in CARD_FACTS:
        assert fact in body, f"{domain_id} card has no {fact}"
        assert body[fact] not in (None, "", [], {}), (
            f"{domain_id} card shows an empty {fact}")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_card_states_this_books_own_calendar(client, domain_id):
    """§2, §3. Twenty quarters, or twenty months. Never inferred."""
    body = book(client, domain_id)
    scope = resolver.scope_for(domain_id)
    assert body["reporting_frequency"] == schema_mod.frequency(domain_id)
    assert body["period_noun"] == schema_mod.period_noun(domain_id)
    assert body["period_column"] == schema_mod.period_column(domain_id)
    assert body["period_count"] == 20
    assert body["reporting_periods"] == list(scope.periods)
    assert body["earliest_period"] == scope.periods[0]
    assert body["latest_period"] == scope.latest_period
    assert body["previous_period"] == scope.previous_period


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_entity_counts_are_the_counts_in_the_parquet(client, domain_id):
    """§5, §6, §13. A card that says 3,652 borrowers must be able to show
    3,652 borrowers -- recomputed here from the files, not from the manifest
    the card read."""
    counts = book(client, domain_id)["entity_counts"]
    if domain_id == dom.CORPORATE:
        borrowers = oracle.frame(domain_id, "corp_borrower_quarter")
        facilities = oracle.frame(domain_id, "corp_facility_quarter")
        assert counts["borrowers"] == borrowers["borrower_id"].nunique()
        assert counts["facilities"] == facilities["facility_id"].nunique()
        assert counts["sectors"] == borrowers["sector"].nunique()
        assert counts["borrowers"] >= 3000, "§5 asks for 3,000+ borrowers"
        assert counts["facilities"] >= 8000, "§5 asks for 8,000+ facilities"
    else:
        accounts = oracle.frame(domain_id, "retail_account_month")
        customers = oracle.frame(domain_id, "retail_customer_month")
        assert counts["accounts"] == accounts["account_id"].nunique()
        assert counts["customers"] == customers["customer_id"].nunique()
        assert counts["customers"] >= 3000, "§6 asks for 3,000+ customers"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_row_counts_are_the_rows_that_are_there(client, domain_id):
    body = book(client, domain_id)
    total = 0
    for entry in body["relations"]:
        rows = len(oracle.frame(domain_id, entry["relation"]))
        assert entry["rows"] == rows, entry["relation"]
        total += rows
    assert body["total_rows"] == total


# ---- §14: subject areas -------------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_subject_areas_are_the_catalogues_own_groups(client, domain_id):
    """§14. Not a second taxonomy invented for this page."""
    areas = {a["area"]: a for a in book(client, domain_id)["subject_areas"]}
    expected: dict[str, int] = {}
    for spec in schema_mod.relations(domain_id):
        for column in spec.fields:
            name = (column.group or "Other").strip() or "Other"
            expected[name] = expected.get(name, 0) + 1
    assert set(areas) == set(expected)
    for name, columns in expected.items():
        assert areas[name]["columns"] == columns, name
        assert areas[name]["relations"], name


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_areas_cover_what_the_book_is_required_to_hold(client, domain_id):
    """§7, §11. A corporate book with no rating area, no collateral area and
    no covenant area is not the credit-risk model §7 describes."""
    areas = {a["area"].lower()
             for a in book(client, domain_id)["subject_areas"]}
    required = (("identity", "segmentation", "exposure", "ifrs 9", "rating",
                 "financials", "collateral", "covenants")
                if domain_id == dom.CORPORATE
                else ("identity", "segmentation", "exposure", "ifrs 9",
                      "delinquency", "behaviour score", "behaviour variables",
                      "collateral"))
    for area in required:
        assert area in areas, (domain_id, area, sorted(areas))


# ---- §15, §16, §41: what a column holds ---------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_column_has_a_label_a_person_reads(client, domain_id):
    """§41. Human labels in the UI, canonical identifiers in SQL -- and BOTH
    shown, because a reader who is only given the label cannot write the
    filter and a reader who is only given `sub_sector` has to guess."""
    for spec in schema_mod.relations(domain_id):
        detail = book(client, domain_id, spec.name)
        for field in detail["fields"]:
            assert field["label"], (spec.name, field["name"])
            assert field["definition"], (spec.name, field["name"])
            assert field["name"] == field["name"].lower()


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_category_column_enumerates_exactly_what_it_holds(client,
                                                            domain_id):
    """§16. The governed set is the set the release holds, recomputed."""
    checked = 0
    for spec in schema_mod.relations(domain_id):
        frame = oracle.frame(domain_id, spec.name)
        for field in book(client, domain_id, spec.name)["fields"]:
            if "governed_values" not in field:
                continue
            published = sorted(str(v) for v in
                               frame[field["name"]].dropna().unique()
                               if str(v) != "")
            assert field["governed_values"] == published, (
                spec.name, field["name"])
            assert field["distinct_values"] == len(published)
            # Each value also written for a reader, and the mapping is
            # one-to-one: the label is a rendering of the value, never a
            # second identifier somebody might filter on.
            assert set(field["value_labels"]) == set(published)
            checked += 1
    assert checked >= 5, "this test found no category columns at all"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_sample_is_never_presented_as_a_governed_set(client, domain_id):
    """§16. `borrower_name` holds 3,652 values. Twelve of them are a sample,
    and a reader told they were the governed list would build a filter that
    silently excluded the other 3,640."""
    for spec in schema_mod.relations(domain_id):
        for field in book(client, domain_id, spec.name)["fields"]:
            if "sample_values" not in field:
                continue
            assert "governed_values" not in field, field["name"]
            assert len(field["sample_values"]) <= 12
            assert field["distinct_values_at_least"] >= len(
                field["sample_values"])


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_calendar_is_never_offered_as_a_category(client, domain_id):
    """§16. `reporting_quarter` holds twenty values and is bounded, so it
    would pass a cardinality test. It is still not a category a reader picks
    from -- it is the axis everything else is reported against."""
    for spec in schema_mod.relations(domain_id):
        for field in book(client, domain_id, spec.name)["fields"]:
            if not any(field["name"].endswith(f"_{noun}")
                       for noun in schema_mod.PERIOD_NOUNS.values()):
                continue
            assert "governed_values" not in field, field["name"]


# ---- §16: ONE SOURCE OF TRUTH -------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_data_builder_reads_the_catalogue_the_cockpit_reads(client,
                                                                domain_id):
    """§16. Not a similar description -- the same one.

    Release, fingerprint, denomination, calendar, relations, grains, keys,
    column names, labels and definitions all come from the analytical
    runtime's own catalogue. If this page could disagree with it, a reader
    could be shown a column that no question can select.
    """
    runtime = arun.for_domain(domain_id)
    catalog = runtime.catalog
    body = book(client, domain_id)

    assert body["release_id"] == catalog.dataset_release_id
    assert body["release_fingerprint"] == catalog.release_fingerprint
    assert body["reporting_currency"] == catalog.reporting_currency
    assert body["amount_scale"] == catalog.amount_scale
    assert body["reporting_frequency"] == catalog.calendar.frequency
    assert body["reporting_periods"] == list(catalog.calendar.slots)
    assert [r["relation"] for r in body["relations"]] == list(
        catalog.relations())
    assert body["joins"] == catalog.joins()

    for entry in body["relations"]:
        spec = catalog.spec(entry["relation"])
        assert entry["grain"] == spec.grain
        assert entry["period_column"] == spec.period_column
        assert entry["key_columns"] == list(spec.key_columns)
        assert entry["columns"] == len(spec.fields)

        detail = book(client, domain_id, entry["relation"])
        assert [f["name"] for f in detail["fields"]] == [
            f.name for f in spec.fields]
        for shown, governed in zip(detail["fields"], spec.fields):
            assert shown["label"] == governed.label
            assert shown["definition"] == governed.description
            assert shown["unit"] == governed.unit
            assert shown["dtype"] == governed.dtype


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_values_shown_are_the_values_the_resolver_resolves_against(
        client, domain_id):
    """§16, §39. The page offers `project_finance`; the reader types
    "prject finance"; the resolver matches it to `project_finance`. Those
    are the same list, or the page is teaching a vocabulary the analytical
    path does not accept."""
    runtime = arun.for_domain(domain_id)
    index = val_mod.dimensions(session=runtime.session,
                               catalog=runtime.catalog)
    shown: dict[str, list[str]] = {}
    for spec in schema_mod.relations(domain_id):
        for field in book(client, domain_id, spec.name)["fields"]:
            if "governed_values" in field:
                shown.setdefault(field["name"], field["governed_values"])

    for name, dimension in index.items():
        assert name in shown, (
            f"the resolver accepts {name} and the page never shows it")
        assert list(dimension.values) == shown[name], name


# ---- §17, §18: the two books do not leak into each other ----------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_asking_one_book_for_the_others_relation_is_refused_by_name(
        client, domain_id):
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    theirs = schema_mod.relation_names(other)[0]
    response = client.get(f"{P}/schema",
                          params={"domain": domain_id, "relation": theirs})
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["relation"] == theirs
    assert dom.LABELS[other] in detail["message"]


def test_the_two_cards_share_no_release_no_relation_and_no_period(client):
    corporate = book(client, dom.CORPORATE)
    retail = book(client, dom.RETAIL)
    assert corporate["release_id"] != retail["release_id"]
    assert corporate["release_fingerprint"] != retail["release_fingerprint"]
    assert not set(r["relation"] for r in corporate["relations"]) & set(
        r["relation"] for r in retail["relations"])
    assert not set(corporate["reporting_periods"]) & set(
        retail["reporting_periods"])
    assert corporate["period_noun"] != retail["period_noun"]


def test_an_unknown_book_is_refused_rather_than_defaulted(client):
    response = client.get(f"{P}/schema", params={"domain": "wholesale"})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "UNKNOWN_DOMAIN"


# ---- the enrichment, named ----------------------------------------------
#
# The convergence test above already proves the page shows exactly the
# governed field list, so any column added to `schema.py` is covered by it
# the moment it exists. That is the right shape for a rule and the wrong
# shape for a RECEIPT: it will pass just as happily on the day somebody
# drops `sub_product` from the schema, because the page would then agree
# with a catalogue that no longer has it.
#
# So the columns the enrichment was actually for are named here. This is
# the test that fails if the Data Builder stops exposing them -- or if the
# release stops carrying them, which is the same thing to a reader.

#: The Retail columns the enriched book added, and the group each belongs
#: to. All four are under `values.MAX_CARDINALITY`, so the page serves
#: their full value list rather than a sample.
RETAIL_ENRICHMENT: dict[str, tuple[str, int]] = {
    "sub_product": ("Product", 12),
    "origination_channel": ("Origination", 3),
    "employment_type": ("Segmentation", 4),
    "delinquency_bucket_fine": ("Delinquency", 8),
}


def test_the_data_builder_shows_the_new_retail_columns(client):
    detail = book(client, dom.RETAIL, "retail_account_month")
    shown = {f["name"]: f for f in detail["fields"]}

    for column, (group, cardinality) in RETAIL_ENRICHMENT.items():
        assert column in shown, f"{column} is not on the page"
        field = shown[column]
        assert field["group"] == group, (column, field["group"])
        # A column with no label and no definition is a column a reader
        # cannot use, whether or not it is listed.
        assert field["label"], column
        assert len(field["definition"]) > 30, column
        assert field["dtype"] == "string", column
        # Under the cardinality cap, so the page owes the reader the whole
        # set rather than a sample of it.
        assert "governed_values" in field, column
        assert "sample_values" not in field, column
        assert len(field["governed_values"]) == cardinality, (
            column, len(field["governed_values"]))


def test_the_new_retail_values_are_the_ones_in_the_release(client):
    """Read off the parquet, not off a list written down beside it."""
    frame = oracle.frame(dom.RETAIL, "retail_account_month")
    detail = book(client, dom.RETAIL, "retail_account_month")
    shown = {f["name"]: f for f in detail["fields"]}
    for column in RETAIL_ENRICHMENT:
        assert shown[column]["governed_values"] == sorted(
            frame[column].dropna().unique()), column


def test_the_fine_bands_are_offered_beside_the_coarse_ones(client):
    """Both bandings, because both are asked for.

    The coarse bucket is what every saved question and alias is written
    against; the fine one is what answers "which part of 1-29". A page that
    replaced one with the other would break the first kind of question to
    serve the second.
    """
    shown = {f["name"]: f for f in
             book(client, dom.RETAIL, "retail_account_month")["fields"]}
    assert set(shown["delinquency_bucket"]["governed_values"]) == {
        "Current", "1-29", "30-59", "60-89", "90+"}
    assert set(shown["delinquency_bucket_fine"]["governed_values"]) == {
        "Current", "1-9", "10-19", "20-29", "30-59", "60-89", "90-179",
        "180+"}


def test_the_corporate_enrichment_is_a_value_not_a_column(client):
    """Said precisely, because it would be easy to overstate.

    Corporate gained no new column. Its enrichment is a new governed VALUE
    in a column that was always there -- the product the bank started
    writing inside the window -- and a new ownership group among many. The
    first is under the cardinality cap and is served in full; the second is
    one of thousands of names and is correctly served as a sample, which is
    what this asserts rather than pretending otherwise.
    """
    detail = book(client, dom.CORPORATE, "corp_facility_quarter")
    shown = {f["name"]: f for f in detail["fields"]}
    assert "supply_chain_finance" in shown["product_type"]["governed_values"]
    assert len(shown["product_type"]["governed_values"]) == 8

    borrowers = {f["name"]: f for f in
                 book(client, dom.CORPORATE,
                      "corp_borrower_quarter")["fields"]}
    group = borrowers["group_name"]
    assert "governed_values" not in group, (
        "thousands of group names are not a governed category")
    assert group["distinct_values_at_least"] > val_mod.MAX_CARDINALITY
    assert "Building Contracting" in borrowers["sub_sector"][
        "governed_values"]
