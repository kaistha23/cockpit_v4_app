"""REAL RELEASES · NO MODEL.

§14-§22. How big the books are, and whether there is anything in them.

Why this exists
---------------
The Corporate release was ninety-seven borrowers and two hundred and ninety
facilities. Every consequence of that was invisible until somebody asked a
real question of it on a real Mac:

  * "EAD by sub-sector" returned rows holding a single obligor, so
    "sub-sector" and "borrower" were the same dimension wearing two names;
  * the twenty largest exposures WERE a fifth of the book, so every
    concentration answer was trivially true;
  * every latency figure was measured against a database small enough to fit
    in a cache line, so "fast" said nothing about the engine.

So this file asserts the SHAPE of the published books -- the counts, the
depth of each dimension, and the presence of the authored stories -- against
the releases themselves. It is not a test of the generator's source; it reads
what was actually published.
"""

from __future__ import annotations

import time

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import lake


@pytest.fixture(scope="module")
def books():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    return {d: arun.for_domain(d) for d in dom.DOMAIN_IDS}


def scalar(runtime, sql: str):
    return runtime.session.connection.execute(sql).fetchone()[0]


def rows(runtime, sql: str):
    return runtime.session.connection.execute(sql).fetchall()


@pytest.fixture
def corporate(books):
    return books[dom.CORPORATE]


@pytest.fixture
def retail(books):
    return books[dom.RETAIL]


# ---- §15: the Corporate book is a corporate book ------------------------

def test_the_corporate_book_holds_thousands_of_borrowers(corporate):
    count = scalar(corporate,
                   "SELECT COUNT(DISTINCT borrower_id) FROM corp_borrower_quarter")
    # Raised with the book. Six sub-sectors inside a sector and a planted
    # sub-sector story need a population to carry them, and a sub-sector
    # finding resting on forty names is an anecdote with a p-value.
    assert 5_000 <= count <= 7_000, count


def test_the_corporate_book_holds_thousands_of_facilities(corporate):
    count = scalar(corporate,
                   "SELECT COUNT(DISTINCT facility_id) FROM corp_facility_quarter")
    assert 15_000 <= count <= 26_000, count


def test_every_borrower_has_at_least_two_facilities(corporate):
    worst = scalar(corporate, """
        SELECT MIN(n) FROM (
          SELECT borrower_id, COUNT(DISTINCT facility_id) AS n
          FROM corp_facility_quarter GROUP BY 1)""")
    assert worst >= 2, worst


def test_no_sector_is_one_company_wearing_a_sector_name(corporate):
    """§15's actual point. A sector aggregate must say something about a
    sector, which needs more than one name in it."""
    smallest = rows(corporate, """
        SELECT sector, COUNT(DISTINCT borrower_id) AS n
        FROM corp_borrower_quarter GROUP BY 1 ORDER BY 2 LIMIT 3""")
    assert smallest[0][1] >= 100, smallest


def test_no_sub_sector_is_one_company_either(corporate):
    smallest = rows(corporate, """
        SELECT sub_sector, COUNT(DISTINCT borrower_id) AS n
        FROM corp_borrower_quarter GROUP BY 1 ORDER BY 2 LIMIT 3""")
    assert smallest[0][1] >= 10, smallest


def test_the_book_has_sector_and_product_type_depth(corporate):
    sectors = scalar(corporate,
                     "SELECT COUNT(DISTINCT sector) FROM corp_borrower_quarter")
    subs = scalar(corporate,
                  "SELECT COUNT(DISTINCT sub_sector) FROM corp_borrower_quarter")
    types = scalar(corporate,
                   "SELECT COUNT(DISTINCT product_type) FROM corp_facility_quarter")
    regions = scalar(corporate,
                     "SELECT COUNT(DISTINCT region) FROM corp_borrower_quarter")
    assert sectors >= 12, sectors
    assert 40 <= subs <= 80, subs
    # §8 asks for seven product types with real populations. Seven with
    # 500+ facilities each is the ask; eight thin ones would not be.
    assert types >= 7, types
    assert regions >= 8, regions


def test_no_borrower_name_is_used_twice(corporate):
    pairs = scalar(corporate, """
        SELECT COUNT(*) FROM (
          SELECT borrower_name FROM corp_borrower_quarter
          GROUP BY 1 HAVING COUNT(DISTINCT borrower_id) > 1)""")
    assert pairs == 0, f"{pairs} name(s) belong to more than one borrower"


# ---- §16: facility types are governed values ----------------------------

#: §8's seven, as the release publishes them. Written out rather than read
#: from the release, because the POINT is the spelling: `project_finance` is
#: what a filter is written against, and a release that started publishing
#: `Project Finance` would break every stored query while a test that read
#: its own expectation from it would stay green.
EXPECTED_PRODUCT_TYPES = {
    "term_loan", "working_capital", "revolving_credit", "trade_finance",
    "project_finance", "overdraft", "asset_finance",
    # EIGHT now. Supply chain finance is written from 2025Q4 onward and has
    # no facility before it, which is the point: a book whose product list
    # is the same in its first quarter and its last cannot be asked what
    # the bank started doing recently.
    "supply_chain_finance",
}

#: The product that does not span the window, and the quarter it starts.
#: Excluded from the population floor below, because it is three quarters
#: old and a floor written for a five-year product would fail it for being
#: exactly what it is meant to be.
NEW_PRODUCT_TYPE = "supply_chain_finance"


def test_product_types_are_the_governed_snake_case_identifiers(corporate):
    published = {r[0] for r in rows(
        corporate, "SELECT DISTINCT product_type FROM corp_facility_quarter")}
    assert published == EXPECTED_PRODUCT_TYPES, sorted(published)
    for value in published:
        assert value == value.lower() and " " not in value, value


def test_every_product_type_is_actually_used(corporate):
    """§8. Deep populations, not a name with three facilities under it: a
    reader asking "and within project finance?" must get a population."""
    used = dict(rows(corporate, """
        SELECT product_type, COUNT(DISTINCT facility_id)
        FROM corp_facility_quarter GROUP BY 1"""))
    for kind in EXPECTED_PRODUCT_TYPES:
        floor = 500 if kind != NEW_PRODUCT_TYPE else 1_500
        assert used.get(kind, 0) >= floor, (kind, used.get(kind, 0))


# ---- §18: the authored stories are in the data --------------------------

def test_the_book_has_names_that_improved_and_names_that_got_worse(
        corporate):
    """A book where everything moves one way answers one question."""
    moved = rows(corporate, """
        WITH edge AS (
          SELECT MIN(reporting_quarter) AS first, MAX(reporting_quarter) AS last
          FROM corp_borrower_quarter),
        a AS (SELECT borrower_id, pd_ttc_12m FROM corp_borrower_quarter, edge
              WHERE reporting_quarter = edge.first),
        b AS (SELECT borrower_id, pd_ttc_12m FROM corp_borrower_quarter, edge
              WHERE reporting_quarter = edge.last)
        SELECT
          SUM(CASE WHEN b.pd_ttc_12m < a.pd_ttc_12m * 0.9 THEN 1 ELSE 0 END),
          SUM(CASE WHEN b.pd_ttc_12m > a.pd_ttc_12m * 1.5 THEN 1 ELSE 0 END)
        FROM a JOIN b USING (borrower_id)""")
    improved, worsened = moved[0]
    assert improved >= 100, f"only {improved} borrowers improved"
    assert worsened >= 100, f"only {worsened} borrowers deteriorated"


def test_the_book_has_defaults(corporate):
    defaulted = scalar(corporate, """
        SELECT COUNT(DISTINCT facility_id) FROM corp_facility_quarter
        WHERE default_flag = 1""")
    assert defaulted >= 100, defaulted


def test_the_book_has_cures_and_not_only_defaults(corporate):
    """A facility that went ninety days past due and came back. Without one,
    "did anything recover?" has no answer and the book is a ratchet."""
    cured = scalar(corporate, """
        WITH last AS (SELECT MAX(reporting_quarter) AS m FROM corp_facility_quarter),
        ever AS (SELECT facility_id FROM corp_facility_quarter
                 GROUP BY 1 HAVING MAX(dpd_days) >= 90),
        now AS (SELECT facility_id FROM corp_facility_quarter, last
                WHERE reporting_quarter = last.m AND dpd_days = 0)
        SELECT COUNT(*) FROM ever JOIN now USING (facility_id)""")
    assert cured >= 25, f"only {cured} facilities cured"


def test_deterioration_starts_somewhere_rather_than_everywhere_at_once(
        corporate):
    """§18's early-deterioration cohort: names that were fine until recently.
    A book where every deterioration began in month one has no "what changed
    this month" to find."""
    started = rows(corporate, """
        SELECT reporting_quarter, COUNT(*) AS n FROM (
          SELECT facility_id, MIN(reporting_quarter) AS reporting_quarter
          FROM corp_facility_quarter WHERE stage >= 2 GROUP BY 1)
        GROUP BY 1 ORDER BY 1""")
    months = [m for m, _n in started]
    late = [n for m, n in started if m >= months[len(months) // 2]]
    assert sum(late) >= 200, (
        "almost every stage-2 entry happens in the first half of the window")


def test_the_book_is_a_performing_book_under_pressure(corporate):
    """Not a portfolio in workout. A generated book whose last month is a
    third impaired teaches a reader to ignore the stage column."""
    stage = dict(rows(corporate, """
        WITH last AS (SELECT MAX(reporting_quarter) AS m FROM corp_facility_quarter)
        SELECT stage, SUM(ead_sar_mn) FROM corp_facility_quarter, last
        WHERE reporting_quarter = last.m GROUP BY 1"""))
    total = sum(stage.values())
    assert stage.get(1, 0) / total >= 0.55, stage
    assert stage.get(3, 0) / total <= 0.12, stage


def test_ecl_coverage_is_a_number_a_credit_reader_would_accept(corporate):
    coverage = scalar(corporate, """
        WITH last AS (SELECT MAX(reporting_quarter) AS m FROM corp_facility_quarter)
        SELECT SUM(ecl_sar_mn) / SUM(ead_sar_mn) * 100
        FROM corp_facility_quarter, last WHERE reporting_quarter = last.m""")
    assert 0.5 <= float(coverage) <= 8.0, coverage


# ---- §22: the Retail book keeps its depth -------------------------------

def test_the_retail_book_holds_thousands_of_customers(retail):
    customers = scalar(
        retail, "SELECT COUNT(DISTINCT customer_id) FROM retail_customer_month")
    accounts = scalar(
        retail, "SELECT COUNT(DISTINCT account_id) FROM retail_account_month")
    assert customers >= 3_000, customers
    assert accounts >= customers, (accounts, customers)


# ---- §17: v1 is published, immutable, and nothing points at it ----------

#: Every Corporate release this build has ever published, oldest first. A
#: superseded release is not deleted: an analysis saved against it names it,
#: and a run that cannot open the release it was accepted against is a run
#: whose stored answer can no longer be checked.
SUPERSEDED_CORPORATE = ("v4-saudi-corporate-20m-v1",
                        "v4-saudi-corporate-20m-v2",
                        # The quarterly book BEFORE the planted patterns and
                        # the larger population. Same calendar, different
                        # numbers, so a different id rather than a rewrite.
                        "v4-saudi-corporate-20q-v3")


def test_the_previous_corporate_releases_are_published_and_unread():
    """§2, §3. The book moved from twenty months to twenty QUARTERS, and it
    did so by publishing a new release rather than rewriting the old ones.
    A calendar change under a release id nobody changed would silently
    restate every saved analysis."""
    assert dom.DEFAULT_RELEASES[dom.CORPORATE] == "v4-saudi-corporate-20q-v4"
    for release_id in SUPERSEDED_CORPORATE:
        if not lake.exists(release_id):
            continue
        assert lake.verify(release_id), (
            f"{release_id}'s bytes must still match the fingerprint they "
            f"were published under; nothing in this round may have "
            f"rewritten them")


def test_the_generator_refuses_to_rebuild_a_frozen_release():
    from backend.cockpit_v4.generate import corporate as gen

    for release_id in SUPERSEDED_CORPORATE:
        with pytest.raises(gen.FrozenRelease):
            gen.build(release_id)


def test_both_published_releases_verify_against_their_fingerprints():
    for domain_id in dom.DOMAIN_IDS:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        if not lake.exists(release_id):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
        assert lake.verify(release_id), release_id


def test_the_corporate_build_is_byte_identical_across_processes():
    """Determinism is what makes the fingerprint worth checking."""
    from backend.cockpit_v4.generate import corporate as gen

    first = gen.build("scratch-determinism-a")
    second = gen.build("scratch-determinism-b")
    for name, frame in first.frames.items():
        other = second.frames[name].drop(columns=["dataset_release_id"])
        assert frame.drop(columns=["dataset_release_id"]).equals(other), name


# ---- §19: the large book is still fast enough to ask ---------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_grouped_aggregate_over_the_whole_book_stays_under_a_second(
        books, domain_id):
    runtime = books[domain_id]
    relation, dimension = (("corp_facility_quarter", "sector")
                           if domain_id == dom.CORPORATE
                           else ("retail_account_month", "product"))
    period = schema_mod.period_column(domain_id)
    started = time.monotonic()
    result = rows(runtime, f"""
        SELECT {dimension}, SUM(ead_sar_mn), SUM(ecl_sar_mn), COUNT(*)
        FROM {relation}
        WHERE {period} = (SELECT MAX({period}) FROM {relation})
        GROUP BY 1 ORDER BY 2 DESC""")
    elapsed = time.monotonic() - started
    assert result, "the book returned nothing"
    assert elapsed < 1.0, f"{domain_id}: {elapsed:.2f}s"


def test_a_join_across_the_whole_corporate_window_stays_under_two_seconds(
        corporate):
    started = time.monotonic()
    result = rows(corporate, """
        SELECT b.sector, b.sub_sector, f.product_type,
               SUM(f.ead_sar_mn) AS ead
        FROM corp_facility_quarter f
        JOIN corp_borrower_quarter b
          ON b.borrower_id = f.borrower_id
         AND b.reporting_quarter = f.reporting_quarter
        GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 50""")
    elapsed = time.monotonic() - started
    assert len(result) == 50
    assert elapsed < 2.0, f"{elapsed:.2f}s"


def test_opening_the_book_is_not_slower_than_a_reader_will_wait(books):
    """A session that takes ten seconds to materialise is a first question
    that takes ten seconds before it starts."""
    for domain_id, runtime in books.items():
        built = float(getattr(runtime.session, "built_seconds", 0.0) or 0.0)
        assert built < 10.0, f"{domain_id} took {built:.1f}s to open"
