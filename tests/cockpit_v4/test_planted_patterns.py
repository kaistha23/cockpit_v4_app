"""
The book has something in it to find, and it is still there.

REAL DATABASE · INDEPENDENT ORACLE. No model call, no paid provider call.

Why this file exists
--------------------
A synthetic book is only worth asking questions of if the answers are worth
having. The previous Retail book had four products, four segments, eight
regions and one `1-29` bucket holding 3,498 rows: a ~128-cell cross-tab in
which every question came back "the card book is worse", because that was
the only thing that had been written into it.

So both books now carry DELIBERATE STRUCTURE -- findings a credit committee
would be expected to reach, each by a different cut, and each with a wrong
answer sitting next to the right one. Structure that is not pinned is
structure that a tuning change buries silently, and the way that is
discovered is in a demonstration. Each pattern below is recovered here by
reading the published Parquet with pandas, which is a different engine and a
different code path from anything the product runs.

Each test also states the WRONG answer -- the cut that misses the finding --
because that is what makes the pattern a test of an analyst rather than a
label in the data.

The patterns
------------
  R1  a product that did not exist a year ago is carrying the losses
  R2  a sub-product x employment-type corner of the card book, entering
      at 20-29 rather than at 1-9
  R3  the portfolio ratio and the segment ratios disagree in sign
  R4  a vintage that underperforms at equal months on book
  R5  a seasonal spike that cures, which is not deterioration
  R6  one region, one sub-product
  C7  cash flow and covenants turn two quarters before the ratings
  C8  a group whose aggregate crosses a limit no single name is near
  C10 a product type the bank started writing three quarters ago
"""

from __future__ import annotations

import domain_oracles as oracles
import pytest

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4.generate import corporate as corp_gen
from backend.cockpit_v4.generate import retail as retail_gen


@pytest.fixture(scope="module")
def accounts():
    return oracles.frame(dom.RETAIL, "retail_account_month")


@pytest.fixture(scope="module")
def borrowers():
    return oracles.frame(dom.CORPORATE, "corp_borrower_quarter")


@pytest.fixture(scope="module")
def facilities():
    return oracles.frame(dom.CORPORATE, "corp_facility_quarter")


def _rate(frame, mask) -> float:
    return float(mask.mean() * 100) if len(frame) else 0.0


# ---- R1: the product that did not exist a year ago ----------------------

def test_the_new_retail_product_has_no_history_before_it_was_launched(
        accounts):
    new = accounts[accounts["product"] == retail_gen.NEW_PRODUCT]
    assert len(new), "the new product is absent from the book entirely"
    assert new["reporting_month"].min() == retail_gen.NEW_PRODUCT_FROM
    # And the rest of the book DOES go back to the start, so the absence is
    # the product's and not the window's.
    old = accounts[accounts["product"] != retail_gen.NEW_PRODUCT]
    assert old["reporting_month"].min() < retail_gen.NEW_PRODUCT_FROM


def test_the_new_retail_product_carries_losses_out_of_proportion(accounts):
    """The finding: small book, large share of the arrears."""
    last = accounts[accounts["reporting_month"] == "2026-08"]
    new = last[last["product"] == retail_gen.NEW_PRODUCT]
    rest = last[last["product"] != retail_gen.NEW_PRODUCT]
    new_rate = _rate(new, new["dpd_days"] >= 30)
    rest_rate = _rate(rest, rest["dpd_days"] >= 30)
    assert new_rate > 2 * rest_rate, (
        f"the new product is at {new_rate:.2f}% 30+ against {rest_rate:.2f}% "
        f"for the rest of the book; it is meant to stand out")
    # It is a SMALL book. A product that is a third of the portfolio does
    # not test whether a reader can find a small thing doing large damage.
    assert len(new) / len(last) < 0.20


# ---- R2: a corner of the card book, not the card book ------------------

def test_the_card_deterioration_is_one_sub_product_and_one_employment_type(
        accounts):
    product, sub_product, employment = retail_gen.GOLD_COHORT
    cards = accounts[accounts["product"] == product]

    def entry_rate(frame, month):
        here = frame[frame["reporting_month"] == month]
        return _rate(here, (here["dpd_days"] >= 1) & (here["dpd_days"] < 30))

    cohort = cards[(cards["sub_product"] == sub_product)
                   & (cards["employment_type"] == employment)]
    others = cards[(cards["sub_product"] != sub_product)
                   & (cards["employment_type"] == employment)]

    before = entry_rate(cohort, "2025-09")
    after = max(entry_rate(cohort, m) for m in
                ("2026-05", "2026-06", "2026-07", "2026-08"))
    assert after > 2 * before, (
        f"{sub_product} x {employment} went {before:.2f}% -> {after:.2f}%; "
        f"the cohort is meant to have moved")

    # THE WRONG CUT. The other sub-products held by the same employment
    # type did not move, so "non-salaried customers are deteriorating" is
    # the answer of someone who stopped one level too early.
    assert entry_rate(others, "2026-08") < after / 2


def test_the_cohort_enters_at_twenty_to_twenty_nine_days(accounts):
    """The drill-down the fine banding exists for.

    "More accounts in 1-29" is where the coarse bucket stops. WHICH part of
    1-29 says whether this is a forgotten payment or a salary date that
    moved, and they are not the same problem or the same remedy.
    """
    product, sub_product, employment = retail_gen.GOLD_COHORT
    cohort = accounts[(accounts["product"] == product)
                      & (accounts["sub_product"] == sub_product)
                      & (accounts["employment_type"] == employment)
                      & (accounts["reporting_month"] >= "2026-01")]
    early = cohort[(cohort["dpd_days"] >= 1) & (cohort["dpd_days"] < 30)]
    assert len(early) >= 30, "too few rows to say anything about the split"
    share = (early["delinquency_bucket_fine"] == "20-29").mean()
    assert share > 0.55, (
        f"only {share:.0%} of the cohort's 1-29 population is in 20-29")
    # Not ALL of it: a cohort with one band and nothing else is a label.
    assert share < 0.95


def test_the_fine_bands_nest_exactly_inside_the_coarse_ones(accounts):
    """A second banding is only useful if it reconciles to the first."""
    coarse = {"Current": {"Current"}, "1-29": {"1-9", "10-19", "20-29"},
              "30-59": {"30-59"}, "60-89": {"60-89"},
              "90+": {"90-179", "180+"}}
    counts = accounts.groupby(
        ["delinquency_bucket", "delinquency_bucket_fine"]).size()
    for bucket, fine in coarse.items():
        seen = {f for b, f in counts.index if b == bucket}
        assert seen <= fine, f"{bucket} contains unexpected {seen - fine}"


# ---- R3: the portfolio and its parts disagree --------------------------

def test_the_portfolio_ratio_and_the_product_ratios_disagree_in_sign(
        accounts):
    """Simpson's paradox, and the reason to report the mix.

    A bank quotes delinquency BY BALANCE. By balance this book improves --
    and it improves because mortgage, which is the cleanest product and the
    largest by balance, grew. By account it deteriorates. Both numbers are
    correct; only one of them is about credit quality.
    """
    first, last = "2025-01", "2026-08"

    def by_balance(month):
        here = accounts[accounts["reporting_month"] == month]
        bad = here[here["dpd_days"] >= 30]["ead_sar_mn"].sum()
        return float(bad / here["ead_sar_mn"].sum() * 100)

    def by_count(month):
        here = accounts[accounts["reporting_month"] == month]
        return _rate(here, here["dpd_days"] >= 30)

    assert by_balance(last) < by_balance(first), (
        f"by balance {by_balance(first):.3f}% -> {by_balance(last):.3f}%; "
        f"the headline number is meant to improve")
    assert by_count(last) > by_count(first), (
        f"by count {by_count(first):.2f}% -> {by_count(last):.2f}%; "
        f"the account-weighted number is meant to worsen")

    # And the mix is where the difference comes from.
    def mortgage_share(month):
        here = accounts[accounts["reporting_month"] == month]
        mortgage = here[here["product"] == retail_gen.MIX_SHIFT_PRODUCT]
        return float(mortgage["ead_sar_mn"].sum()
                     / here["ead_sar_mn"].sum() * 100)

    assert mortgage_share(last) - mortgage_share(first) > 5.0


def test_the_two_products_a_reader_would_name_both_got_worse(accounts):
    """The segments behind the improving headline are not improving."""
    for product in ("Credit Card", "Personal Finance"):
        here = accounts[accounts["product"] == product]

        def rate(month):
            m = here[here["reporting_month"] == month]
            return float(m[m["dpd_days"] >= 30]["ead_sar_mn"].sum()
                         / m["ead_sar_mn"].sum() * 100)

        assert rate("2026-08") > rate("2025-01"), (
            f"{product} is meant to deteriorate: "
            f"{rate('2025-01'):.2f}% -> {rate('2026-08'):.2f}%")


# ---- R4: a vintage, not a calendar month -------------------------------

def test_the_bad_vintage_is_only_visible_at_equal_months_on_book(accounts):
    same_age = accounts[(accounts["months_on_book"] >= 6)
                        & (accounts["months_on_book"] <= 9)]
    bad = same_age[same_age["origination_month"].isin(
        retail_gen.BAD_VINTAGE_MONTHS)]
    rest = same_age[~same_age["origination_month"].isin(
        retail_gen.BAD_VINTAGE_MONTHS)]
    bad_rate = _rate(bad, bad["dpd_days"] >= 30)
    rest_rate = _rate(rest, rest["dpd_days"] >= 30)
    assert len(bad) >= 500, "too few rows in the vintage to compare"
    assert bad_rate > 1.6 * rest_rate, (
        f"the 2025Q4 vintage is at {bad_rate:.2f}% against {rest_rate:.2f}% "
        f"at the same age")


# ---- R5: a season is not a trend ---------------------------------------

@pytest.mark.parametrize("spike", retail_gen.SEASON_MONTHS)
def test_the_seasonal_spike_reverses(accounts, spike):
    """Up, then back. An analysis that calls this deterioration is wrong.

    Compared against the months either side rather than against a trend,
    because the book is also genuinely deteriorating and a spike has to
    stand out from that.
    """
    def entries(month):
        here = accounts[accounts["reporting_month"] == month]
        return _rate(here, (here["dpd_days"] >= 1) & (here["dpd_days"] < 30))

    months = sorted(accounts["reporting_month"].unique())
    index = months.index(spike)
    before = entries(months[index - 1])
    at = entries(spike)
    after = entries(months[index + 2])
    assert at > 1.4 * before, f"{spike} did not spike: {before} -> {at}"
    assert after < at * 0.85, (
        f"{spike} did not revert: {at} -> {after} two months later")


# ---- R6: one region, one sub-product -----------------------------------

def test_the_regional_pocket_is_invisible_at_product_level(accounts):
    product, sub_product, region = retail_gen.REGION_POCKET
    last = accounts[accounts["reporting_month"] == "2026-08"]
    auto = last[last["product"] == product]
    pocket = auto[(auto["sub_product"] == sub_product)
                  & (auto["region"] == region)]
    elsewhere = auto[(auto["sub_product"] == sub_product)
                     & (auto["region"] != region)]
    assert len(pocket) >= 40, "too few accounts in the pocket to compare"
    assert _rate(pocket, pocket["dpd_days"] >= 30) > (
        2.0 * _rate(elsewhere, elsewhere["dpd_days"] >= 30))

    # THE WRONG CUT: the product line. Auto Finance is the book's improving
    # product, so a reader who stops there concludes it is fine.
    book = last[last["product"] != product]
    assert _rate(auto, auto["dpd_days"] >= 30) < _rate(
        book, book["dpd_days"] >= 30)


# ---- C7: the signal before the rating ----------------------------------

def test_the_cash_flow_turns_before_the_rating_does(borrowers):
    """The early-warning question, in one book.

    Debt service is a reading and a rating is a decision, so the two do not
    move together. Building Contracting's DSCR falls from 2025Q4; its
    grades only overtake the rest of Construction later. A book where both
    turn in the same quarter cannot be used to ask whether a deterioration
    was visible before it was recognised.
    """
    construction = borrowers[borrowers["sector"] == "Construction"]
    cohort = construction[
        construction["sub_sector"] == corp_gen.EARLY_SUB_SECTOR]
    rest = construction[
        construction["sub_sector"] != corp_gen.EARLY_SUB_SECTOR]

    def dscr(frame, quarter):
        return float(frame[frame["reporting_quarter"] == quarter][
            "dscr_x"].mean())

    def notches(frame, quarter):
        return float(frame[frame["reporting_quarter"] == quarter][
            "rating_notches_from_origination"].mean())

    # Before: indistinguishable on debt service.
    assert abs(dscr(cohort, "2025Q3") - dscr(rest, "2025Q3")) < 0.10
    # From the turn: clearly apart.
    assert dscr(cohort, "2026Q1") < dscr(rest, "2026Q1") - 0.30

    # And the ratings do NOT lead it. At the quarter the cash flow turns,
    # the cohort is no more downgraded than its neighbours.
    assert notches(cohort, corp_gen.EARLY_FROM_QUARTER) >= notches(
        rest, corp_gen.EARLY_FROM_QUARTER), (
        "the rating moved first, which is the opposite of the finding")


def test_the_covenant_breaches_rise_with_the_cash_flow(borrowers):
    covenants = oracles.frame(dom.CORPORATE, "corp_covenant_quarter")
    keys = borrowers[["borrower_id", "reporting_quarter", "sub_sector"]]
    joined = covenants.merge(keys.drop_duplicates(),
                             on=["borrower_id", "reporting_quarter"],
                             how="left")
    cohort = joined[joined["sub_sector"] == corp_gen.EARLY_SUB_SECTOR]

    def breaches(quarter):
        here = cohort[cohort["reporting_quarter"] == quarter]
        return float(here["breach_flag"].mean() * 100)

    assert breaches("2026Q2") > 2 * breaches("2025Q3")


# ---- C8: only visible after grouping -----------------------------------

def test_the_concentration_exists_only_in_the_aggregate(facilities,
                                                        borrowers):
    groups = borrowers[["borrower_id", "reporting_quarter",
                        "group_name"]].drop_duplicates()
    joined = facilities.merge(
        groups, on=["borrower_id", "reporting_quarter"], how="left")
    group = joined[joined["group_name"] == corp_gen.CONCENTRATION_GROUP]
    assert group["borrower_name"].nunique() == len(
        corp_gen.CONCENTRATION_MEMBERS)

    def total(quarter):
        return float(group[group["reporting_quarter"] == quarter][
            "ead_sar_mn"].sum())

    def largest_single(quarter):
        here = group[group["reporting_quarter"] == quarter]
        return float(here.groupby("borrower_name")["ead_sar_mn"].sum().max())

    before = total("2025Q4")
    after = total("2026Q2")
    assert after > 2.5 * before, (
        f"the group's exposure went {before:,.0f} -> {after:,.0f}; it is "
        f"meant to have jumped")
    # NO SINGLE NAME IS ANYWHERE NEAR. An exposure report ordered by
    # borrower shows nothing at all, which is the whole finding.
    assert largest_single("2026Q2") < after / 3


# ---- C10: the product the bank started writing last year ---------------

def test_the_new_corporate_product_has_no_history_either(facilities):
    new = facilities[facilities["product_type"] == corp_gen.NEW_PRODUCT_TYPE]
    assert len(new), "the new corporate product is absent from the book"
    assert new["reporting_quarter"].min() == corp_gen.NEW_PRODUCT_FROM_QUARTER


def test_the_new_corporate_product_is_a_fifth_of_the_ecl_it_is_not(
        facilities):
    last = facilities[facilities["reporting_quarter"] == "2026Q2"]
    new = last[last["product_type"] == corp_gen.NEW_PRODUCT_TYPE]
    ead_share = float(new["ead_sar_mn"].sum()
                      / last["ead_sar_mn"].sum() * 100)
    ecl_share = float(new["ecl_sar_mn"].sum()
                      / last["ecl_sar_mn"].sum() * 100)
    assert ecl_share > 1.8 * ead_share, (
        f"{ead_share:.2f}% of exposure and {ecl_share:.2f}% of ECL; the new "
        f"product is meant to be carrying more than its weight")
    # It is the worst product in the book by default rate, and by a margin.
    by_product = last.groupby("product_type").apply(
        lambda g: (g["stage"] == 3).mean(), include_groups=False)
    assert by_product.idxmax() == corp_gen.NEW_PRODUCT_TYPE


# ---- the shape of the books --------------------------------------------

def test_both_books_still_publish_twenty_periods():
    """Scale changed. The calendar did not, and eight test files say so."""
    assert len(oracles.periods(dom.CORPORATE)) == 20
    assert len(oracles.periods(dom.RETAIL)) == 20


def test_the_retail_book_is_deep_enough_to_drill_into(accounts):
    assert accounts["product"].nunique() == 5
    assert accounts["sub_product"].nunique() == 12
    assert accounts["employment_type"].nunique() == 4
    assert accounts["origination_channel"].nunique() == 3
    assert accounts["delinquency_bucket_fine"].nunique() >= 7
    # Product x sub-product x employment type x region is the drill-down
    # chain the live questions walk. It has to have populated cells at the
    # bottom of it, not one account per cell.
    cells = accounts[accounts["reporting_month"] == "2026-08"].groupby(
        ["product", "sub_product", "employment_type"]).size()
    assert cells.median() >= 100, (
        f"the median product x sub-product x employment cell holds "
        f"{cells.median():.0f} accounts")
