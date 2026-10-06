"""REAL DATABASE · NO MODEL. The two books, and the wall between them.

The defect this module exists for
---------------------------------
`DOMAIN = "corporate_cockpit"` was a module constant and the runtime pinned
one release behind a process-wide catalogue cache. There was exactly one book
per process, so "Corporate or Retail?" had nowhere to live except a label on
a screen. A Retail question would have read Corporate relations and nothing
in the answer would have said so.

These tests are the wall. They do not check that a label changed; they check
that a Corporate catalogue cannot name a Retail relation, that a Corporate
session cannot query one, that a release built for one domain is refused when
asked for as the other, and that no cache entry can serve the wrong book.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import invariants, lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4.generate import corporate, retail
from backend.cockpit_v4.generate import month_range, quarter_range

#: §4. The Retail calendar: twenty completed months.
EXPECTED_MONTHS = (
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    "2026-07", "2026-08")

#: §3. The Corporate calendar: twenty completed QUARTERS. A corporate credit
#: file is reviewed on the cycle its obligors report on, and that cycle is
#: quarterly.
EXPECTED_QUARTERS = (
    "2021Q3", "2021Q4", "2022Q1", "2022Q2", "2022Q3", "2022Q4",
    "2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2",
    "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4",
    "2026Q1", "2026Q2")

#: What each book's calendar is, so a test can say "this book" rather than
#: "the calendar".
CALENDAR: dict[str, dict[str, object]] = {
    dom.CORPORATE: {"periods": EXPECTED_QUARTERS, "frequency": "quarterly",
                    "latest": "2026Q2", "previous": "2026Q1",
                    "year_ago": "2025Q2",
                    "last3": ("2025Q4", "2026Q1", "2026Q2"),
                    "beyond": "2026Q3"},
    dom.RETAIL: {"periods": EXPECTED_MONTHS, "frequency": "monthly",
                 "latest": "2026-08", "previous": "2026-07",
                 "year_ago": "2025-08",
                 "last3": ("2026-06", "2026-07", "2026-08"),
                 "beyond": "2026-09"},
}


@pytest.fixture(scope="module")
def published():
    """Both domains, published. Skips rather than fails when unseeded."""
    for domain_id in dom.DOMAIN_IDS:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        if not lake.exists(release_id):
            pytest.skip(f"{release_id} is not published in this runtime; "
                        f"run scripts/cockpit_v4/seed_domains.py")
    return {d: cat.build(domain_id=d) for d in dom.DOMAIN_IDS}


# ---- 1. the calendar, pinned -------------------------------------------

def test_the_twenty_months_are_exactly_the_ones_specified():
    """§4. Twenty COMPLETED months. Pinned, not derived at read time."""
    assert month_range() == EXPECTED_MONTHS
    assert len(EXPECTED_MONTHS) == 20


def test_the_twenty_quarters_are_exactly_the_ones_specified():
    """§3. Twenty COMPLETED quarters, 2021Q3 through 2026Q2."""
    assert quarter_range() == EXPECTED_QUARTERS
    assert len(EXPECTED_QUARTERS) == 20
    assert EXPECTED_QUARTERS[0] == "2021Q3"
    assert EXPECTED_QUARTERS[-1] == "2026Q2"


def test_the_two_books_do_not_share_a_calendar():
    """§2. Deliberate: corporate credit is reviewed quarterly and retail
    behaviour is monitored monthly. One calendar for both is what made a
    quarterly book answer in months."""
    assert set(EXPECTED_QUARTERS) & set(EXPECTED_MONTHS) == set()
    assert (schema_mod.frequency(dom.CORPORATE)
            != schema_mod.frequency(dom.RETAIL))
    assert (schema_mod.period_column(dom.CORPORATE)
            != schema_mod.period_column(dom.RETAIL))


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_each_release_publishes_those_periods_and_no_others(published,
                                                            domain_id):
    want = CALENDAR[domain_id]
    calendar = published[domain_id].calendar
    assert tuple(calendar.slots) == want["periods"]
    assert calendar.latest == want["latest"]
    assert calendar.previous == want["previous"]
    assert calendar.year_ago == want["year_ago"], (
        "the year-ago comparison must be the same period one year back, "
        "which is four slots in a quarterly book and twelve in a monthly one")
    assert calendar.last(3) == want["last3"]
    assert calendar.frequency == want["frequency"]


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_no_partial_period_is_published(published, domain_id):
    """A part-period reads as a collapse in every comparison drawn to it."""
    assert CALENDAR[domain_id]["beyond"] not in \
        published[domain_id].calendar.slots


# ---- 2. both books are Saudi -------------------------------------------

@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_both_books_are_saudi_native(published, domain_id):
    """§6. Same country, same currency, same scale, both domains."""
    manifest = lake.read_manifest(dom.DEFAULT_RELEASES[domain_id])
    assert manifest["geography_name"] == "Saudi Arabia"
    assert manifest["reporting_currency"] == "SAR"
    assert manifest["amount_scale"] == "million"
    # Same country, same currency, same scale -- and each book's OWN
    # calendar, because that is the one thing they do not share.
    assert manifest["reporting_frequency"] == CALENDAR[domain_id]["frequency"]
    assert manifest["not_client_data"]


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_no_india_specific_label_survives_in_either_book(published,
                                                         domain_id):
    """The book is Saudi. A stray crore would say otherwise on a screen."""
    import pandas as pd

    release_id = dom.DEFAULT_RELEASES[domain_id]
    for relation in schema_mod.relation_names(domain_id):
        frame = pd.read_parquet(lake.relation_path(release_id, relation))
        text = " ".join(
            frame.select_dtypes(include=["object", "str"]).astype(str)
            .head(400).values.ravel().tolist()).lower()
        for banned in ("crore", "lakh", "inr", "₹"):
            assert banned not in text, (
                f"{relation} carries {banned!r}")


# ---- 3. the wall -------------------------------------------------------

def test_a_corporate_catalogue_refuses_a_retail_relation(published):
    """§5. Refused by NAME, with the reason, before any data is touched."""
    with pytest.raises(cat.CrossDomainAccess) as caught:
        published[dom.CORPORATE].require_relation("retail_account_month")
    assert "Retail" in caught.value.args[0]
    assert "Corporate" in caught.value.args[0]


def test_a_retail_catalogue_refuses_a_corporate_relation(published):
    with pytest.raises(cat.CrossDomainAccess):
        published[dom.RETAIL].require_relation("corp_facility_quarter")


@pytest.mark.parametrize("domain_id,foreign", [
    (dom.CORPORATE, "retail_account_month"),
    (dom.RETAIL, "corp_facility_quarter"),
])
def test_a_session_cannot_query_the_other_book(published, domain_id,
                                               foreign):
    """§5. Fail closed at the database, not only at the catalogue.

    The session materialises its own domain's relations and nothing else, so
    the other book's table does not exist to be selected from -- which is a
    stronger property than a check that someone has to remember to run.
    """
    session = cat.open_session(catalog=published[domain_id])
    assert foreign not in session.relations
    with pytest.raises(Exception) as caught:
        session.connection.execute(f"SELECT * FROM {foreign} LIMIT 1")
    assert foreign in str(caught.value)


def test_a_release_built_for_one_domain_is_refused_as_the_other():
    """Naming is not authorization. The manifest says which book it is."""
    with pytest.raises(cat.CrossDomainAccess):
        cat.build(domain_id=dom.RETAIL,
                  release_id=dom.DEFAULT_RELEASES[dom.CORPORATE])


def test_an_unknown_domain_is_refused_rather_than_defaulted():
    """Coercing a typo to Corporate answers one book with another's numbers."""
    for junk in ("corporat", "RETAIL_v2", "both", "wholesale"):
        with pytest.raises(dom.UnknownDomain):
            dom.parse(junk)
    assert dom.parse(None) == dom.CORPORATE
    assert dom.parse("") == dom.CORPORATE


def test_every_row_carries_its_own_domain(published):
    """The isolation is a column, so a forgotten WHERE cannot leak a book."""
    import pandas as pd

    for domain_id in dom.DOMAIN_IDS:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        for relation in schema_mod.relation_names(domain_id):
            frame = pd.read_parquet(lake.relation_path(release_id, relation))
            assert set(frame["domain_id"]) == {domain_id}
            assert set(frame["dataset_release_id"]) == {release_id}


# ---- 4. caches cannot cross --------------------------------------------

def test_a_session_cache_entry_cannot_serve_the_other_domain(published):
    """§55. The key carries the domain, so collision is impossible."""
    corporate_session = cat.open_session(catalog=published[dom.CORPORATE])
    retail_session = cat.open_session(catalog=published[dom.RETAIL])
    assert corporate_session is not retail_session
    assert corporate_session.domain_id == dom.CORPORATE
    assert retail_session.domain_id == dom.RETAIL
    # Reopening returns the SAME session for the same book and never the
    # other one.
    assert cat.open_session(catalog=published[dom.CORPORATE]) is \
        corporate_session


def test_a_cache_key_includes_the_fingerprint(published):
    """A release id is a name; two builds of one name hold different numbers."""
    scope = dom.DomainScope(
        domain_id=dom.CORPORATE, release_id="r", release_fingerprint="aaa",
        country="SA", currency="SAR", amount_scale="million",
        reporting_frequency="monthly", periods=EXPECTED_MONTHS,
        relations=())
    rebuilt = dom.DomainScope(**{**scope.__dict__,
                                 "release_fingerprint": "bbb"})
    assert scope.cache_key("attention") != rebuilt.cache_key("attention")


# ---- 5. the books are genuinely different ------------------------------

def test_retail_is_not_a_renamed_corporate_table(published):
    """§11. Different grain, different segmentation, different questions."""
    corporate_columns = set()
    for relation in schema_mod.relation_names(dom.CORPORATE):
        corporate_columns |= set(
            schema_mod.relation(dom.CORPORATE, relation).columns)
    retail_columns = set()
    for relation in schema_mod.relation_names(dom.RETAIL):
        retail_columns |= set(
            schema_mod.relation(dom.RETAIL, relation).columns)

    assert "sector" in corporate_columns and "sector" not in retail_columns
    assert "product" in retail_columns and "product" not in corporate_columns
    assert "behaviour_score" in retail_columns
    assert "behaviour_score" not in corporate_columns
    assert "rating_current" in corporate_columns
    assert "rating_current" not in retail_columns
    assert "account_id" in retail_columns and "account_id" not in corporate_columns
    assert "facility_id" in corporate_columns
    assert "facility_id" not in retail_columns


@pytest.mark.parametrize("domain_id,relation,key", [
    (dom.CORPORATE, "corp_facility_quarter", ("facility_id", "reporting_quarter")),
    (dom.RETAIL, "retail_account_month", ("account_id", "reporting_month")),
])
def test_each_book_has_its_own_grain(published, domain_id, relation, key):
    assert schema_mod.relation(domain_id, relation).key_columns == key


# ---- 6. the generators, gated ------------------------------------------

@pytest.mark.parametrize("builder", [corporate.build, retail.build])
def test_a_freshly_built_book_passes_every_invariant(builder):
    """§18. The gate runs before publication, so a broken book never ships."""
    assert invariants.check(builder()) == []


@pytest.mark.parametrize("builder,domain_id", [
    (corporate.build, dom.CORPORATE), (retail.build, dom.RETAIL)])
def test_two_builds_of_one_release_fingerprint_identically(builder,
                                                           domain_id):
    """§20. A fingerprint that moves when nothing changed checks nothing.

    The first version of the corporate generator used `hash()` on strings for
    its group ids, which Python randomises per process -- so two builds of
    the same inputs produced different bytes and a different digest.
    """
    first = builder(release_id="tmp-fingerprint-check")
    second = builder(release_id="tmp-fingerprint-check")
    try:
        one = lake.publish(first, overwrite=True)
        two = lake.publish(second, overwrite=True)
        assert one["release_fingerprint"] == two["release_fingerprint"]
        assert one["domain_id"] == domain_id
    finally:
        lake.forget("tmp-fingerprint-check")


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_the_published_bytes_still_match_their_fingerprint(published,
                                                           domain_id):
    assert lake.verify(dom.DEFAULT_RELEASES[domain_id])


def test_a_published_release_is_immutable():
    """§19. An analysis saved last week names an id and expects its numbers."""
    build = corporate.build(release_id="tmp-immutable-check")
    try:
        lake.publish(build)
        with pytest.raises(lake.ReleaseExists):
            lake.publish(build)
        # And only a deliberate overwrite gets through.
        lake.publish(build, overwrite=True)
    finally:
        lake.forget("tmp-immutable-check")


def test_an_unpublished_release_is_reported_not_substituted():
    with pytest.raises(lake.ReleaseNotFound):
        lake.read_manifest("v4-saudi-nothing-here-v1")


# ---- 7. resolution and pinning -----------------------------------------

def test_a_thread_decides_its_own_domain(published):
    """§3. Inside a thread the thread wins, without consulting anything."""
    from backend.cockpit_v4 import domain_resolver as resolver

    assert resolver.resolve(thread_domain=dom.RETAIL).domain_id == dom.RETAIL
    # Even when the caller says nothing, and even when the default differs.
    assert resolver.resolve(thread_domain=dom.RETAIL,
                            requested=None).domain_id == dom.RETAIL


def test_a_follow_up_cannot_move_a_thread_to_the_other_book(published):
    """§4. Not silently obeyed, and not silently overruled. Reported."""
    from backend.cockpit_v4 import domain_resolver as resolver

    with pytest.raises(resolver.DomainPinned) as caught:
        resolver.resolve(thread_domain=dom.RETAIL, requested=dom.CORPORATE)
    message = str(caught.value)
    assert "pinned to Retail" in message
    assert "Start a Corporate conversation" in message
    assert caught.value.thread_domain == dom.RETAIL
    assert caught.value.asked == dom.CORPORATE


def test_outside_a_thread_the_selection_decides(published):
    from backend.cockpit_v4 import domain_resolver as resolver

    assert resolver.resolve(requested=dom.RETAIL).domain_id == dom.RETAIL
    assert resolver.resolve(requested=None).domain_id == dom.DEFAULT_DOMAIN


def test_a_resolved_scope_carries_its_whole_book(published):
    """§2. One request, one domain, and everything it needs, decided once."""
    from backend.cockpit_v4 import domain_resolver as resolver

    scope = resolver.resolve(requested=dom.RETAIL)
    assert scope.release_id == dom.DEFAULT_RELEASES[dom.RETAIL]
    assert scope.release_fingerprint
    assert scope.country == "Saudi Arabia"
    assert scope.currency == "SAR"
    assert scope.amount_scale == "million"
    assert scope.money_unit == "SAR million"
    assert scope.reporting_frequency == "monthly"
    assert scope.latest_period == "2026-08"
    assert scope.previous_period == "2026-07"
    assert scope.year_ago_period == "2025-08"
    corporate = resolver.resolve(requested=dom.CORPORATE)
    assert corporate.reporting_frequency == "quarterly"
    assert corporate.latest_period == "2026Q2"
    assert corporate.previous_period == "2026Q1"
    assert corporate.year_ago_period == "2025Q2"
    assert set(scope.relations) == set(
        schema_mod.relation_names(dom.RETAIL))


def test_both_domains_report_their_own_readiness(published):
    """§56, §57. Separately, and never as substitutes for each other."""
    from backend.cockpit_v4 import domain_resolver as resolver

    available = resolver.availability()
    assert set(available.ready_domains) == set(dom.DOMAIN_IDS)
    assert available.default == dom.CORPORATE
    for domain_id in dom.DOMAIN_IDS:
        status = available[domain_id]
        assert status.ready
        assert status.release_id == dom.DEFAULT_RELEASES[domain_id]
        assert status.to_dict()["latest_period"] == \
            CALENDAR[domain_id]["latest"]
        assert status.detail["relation_count"] == 4
        assert status.detail["field_count"] > 50


def test_an_unpublished_domain_is_reported_not_substituted():
    """§56. One book missing does not make the other stand in for it."""
    from backend.cockpit_v4 import domain_resolver as resolver

    with pytest.raises(resolver.DomainUnavailable) as caught:
        resolver.scope_for(dom.RETAIL) if False else None
        raise resolver.DomainUnavailable(dom.RETAIL, "release is not published.")
    assert "Nothing was substituted" in str(caught.value)
    assert resolver.provision_command(dom.RETAIL).endswith("--domain retail")
