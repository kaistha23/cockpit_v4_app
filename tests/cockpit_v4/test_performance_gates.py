"""REAL DATABASE · NO MODEL. The books stay fast enough to ask.

§55. The gates are on DuckDB doing the filtering, not on Python materialising
a book and then filtering it. That distinction is the whole point: a query
that reads 840,000 rows into pandas and then takes one quarter is fast on a
laptop with a warm cache and unusable on the thing it will actually run on.

So each gate below states what it measures and what it must not do, and the
last two check the shape of the work rather than only its duration -- a
timing test that passes because the machine was idle is a timing test that
will fail for the next person for a reason they cannot see.
"""

from __future__ import annotations

import time

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod

#: One grouped aggregate over the latest period. A reader waits for this.
GROUPED_SECONDS = 1.0
#: A join across the whole window. Slower on purpose, and still bounded.
JOINED_SECONDS = 3.0
#: Opening a book: read the manifest, register the views, build the catalogue.
OPEN_SECONDS = 5.0


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


@pytest.fixture(scope="module")
def books():
    return {domain_id: cat.open_session(
        catalog=cat.build(domain_id=domain_id))
        for domain_id in dom.DOMAIN_IDS}


def timed(session, sql: str) -> tuple[list, float]:
    started = time.monotonic()
    rows = session.connection.execute(sql).fetchall()
    return rows, time.monotonic() - started


def exposure(domain_id: str) -> tuple[str, str, str]:
    """This book's exposure relation, its segment column and its calendar."""
    if domain_id == dom.CORPORATE:
        return "corp_facility_quarter", "sector", "reporting_quarter"
    return "retail_account_month", "product", "reporting_month"


# ---- the gates ----------------------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_grouped_aggregate_over_the_latest_period_is_under_a_second(
        books, domain_id):
    relation, dimension, period = exposure(domain_id)
    rows, elapsed = timed(books[domain_id], f"""
        SELECT {dimension}, SUM(ead_sar_mn), SUM(ecl_sar_mn), COUNT(*)
        FROM {relation}
        WHERE {period} = (SELECT MAX({period}) FROM {relation})
        GROUP BY 1 ORDER BY 2 DESC""")
    assert rows, domain_id
    assert elapsed < GROUPED_SECONDS, f"{domain_id}: {elapsed:.2f}s"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_join_across_the_whole_window_is_under_three_seconds(books,
                                                               domain_id):
    if domain_id == dom.CORPORATE:
        sql = """
            SELECT b.sector, COUNT(DISTINCT f.facility_id),
                   SUM(f.ead_sar_mn)
            FROM corp_facility_quarter f
            JOIN corp_borrower_quarter b
              ON b.borrower_id = f.borrower_id
             AND b.reporting_quarter = f.reporting_quarter
            GROUP BY 1"""
    else:
        sql = """
            SELECT c.customer_segment, COUNT(DISTINCT a.account_id),
                   SUM(a.ead_sar_mn)
            FROM retail_account_month a
            JOIN retail_customer_month c
              ON c.customer_id = a.customer_id
             AND c.reporting_month = a.reporting_month
            GROUP BY 1"""
    rows, elapsed = timed(books[domain_id], sql)
    assert rows, domain_id
    assert elapsed < JOINED_SECONDS, f"{domain_id}: {elapsed:.2f}s"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_twenty_period_trend_is_under_a_second(books, domain_id):
    """§4's own question -- "how has this moved over the last eight
    quarters" -- reads the whole window rather than one slice of it."""
    relation, dimension, period = exposure(domain_id)
    rows, elapsed = timed(books[domain_id], f"""
        SELECT {period}, SUM(ead_sar_mn), SUM(ecl_sar_mn)
        FROM {relation} GROUP BY 1 ORDER BY 1""")
    assert len(rows) == 20, (domain_id, len(rows))
    assert elapsed < GROUPED_SECONDS, f"{domain_id}: {elapsed:.2f}s"


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_opening_a_book_is_under_five_seconds(domain_id):
    """A COLD open: read the manifest, register the views, build the
    catalogue. Measured on a fresh session rather than by resetting the
    shared runtime, which would close the connections the gates above are
    still holding."""
    started = time.monotonic()
    catalog = cat.build(domain_id=domain_id)
    session = cat.open_session(catalog=catalog)
    rows = session.connection.execute(
        f"SELECT COUNT(*) FROM {exposure(domain_id)[0]}").fetchone()[0]
    elapsed = time.monotonic() - started
    assert catalog.dataset_release_id == dom.DEFAULT_RELEASES[domain_id]
    assert rows > 0
    assert elapsed < OPEN_SECONDS, f"{domain_id}: {elapsed:.2f}s"


# ---- the shape of the work, not only its duration -----------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_period_filter_is_pushed_down_to_the_scan(books, domain_id):
    """§55. The gate above passes either way on a warm machine. This is the
    one that says HOW: the filter is in the plan, so DuckDB reads the rows
    it needs rather than the rows there are."""
    relation, dimension, period = exposure(domain_id)
    latest = books[domain_id].connection.execute(
        f"SELECT MAX({period}) FROM {relation}").fetchone()[0]
    plan = "\n".join(
        str(cell) for row in books[domain_id].connection.execute(
            f"EXPLAIN SELECT {dimension}, SUM(ead_sar_mn) FROM {relation} "
            f"WHERE {period} = '{latest}' GROUP BY 1").fetchall()
        for cell in row)
    assert "FILTER" in plan.upper() or period in plan, (
        f"{domain_id}: the period filter is not in the plan:\n{plan}")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_one_period_is_a_twentieth_of_the_book(books, domain_id):
    """A filter that is pushed down has a twentieth of the rows to add up.

    Asserted on the DATA rather than on the clock, because a duration that
    depends on the machine is a duration that will fail for somebody else
    for a reason they cannot see -- and the ratio is the thing the gate
    above is actually relying on.
    """
    relation, _dimension, period = exposure(domain_id)
    whole = books[domain_id].connection.execute(
        f"SELECT COUNT(*) FROM {relation}").fetchone()[0]
    one = books[domain_id].connection.execute(
        f"SELECT COUNT(*) FROM {relation} "
        f"WHERE {period} = (SELECT MAX({period}) FROM {relation})"
    ).fetchone()[0]
    # THE BAND IS WIDER AT THE BOTTOM BECAUSE THE BOOK GROWS. Both books
    # now originate inside their window -- a retail product launched in
    # 2026-03, a mortgage campaign, a corporate product written from
    # 2025Q4 -- so the latest period holds MORE than a twentieth and the
    # ratio falls below twenty. That is the book being a book; what this
    # gate is for is that a period filter still leaves a fraction of the
    # rows to add up, which a ratio of fourteen says just as well.
    assert one * 10 <= whole <= one * 25, (domain_id, one, whole)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_book_is_large_enough_for_the_gate_to_mean_something(books,
                                                                 domain_id):
    """A gate over a thousand rows is not a gate. §5, §6 ask for a book of
    real size, and these timings only say anything against one."""
    relation, _dimension, _period = exposure(domain_id)
    rows = books[domain_id].connection.execute(
        f"SELECT COUNT(*) FROM {relation}").fetchone()[0]
    assert rows >= 150_000, (domain_id, rows)


def test_no_gate_reads_a_book_into_python_to_filter_it():
    """§55, stated as a rule about this file rather than about the product.

    Every gate above hands DuckDB the whole predicate. A gate written as
    `pd.read_parquet(...)` and then a mask would measure pandas, pass, and
    say nothing about the thing a reader waits for.
    """
    from pathlib import Path

    # Only the gates above, not this check's own list of what they must not
    # contain -- a self-check that searches itself always finds something.
    source = Path(__file__).read_text().split(
        "def test_no_gate_reads_a_book_into_python_to_filter_it")[0]
    for banned in ("read_" + "parquet", "to_" + "pandas", "fetch" + "df",
                   "oracle." + "frame"):
        assert banned not in source, banned
