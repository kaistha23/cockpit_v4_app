"""
A Saudi retail credit book, twenty months of it, at a real retail grain.

Retail is not corporate with different words
--------------------------------------------
A corporate facility is one large exposure to one obligor, watched
individually. A retail account is one of thousands, watched as a population:
what matters is how a PRODUCT, a SCORE BAND, a DELINQUENCY BUCKET or a
VINTAGE is moving, and the individual account matters when it falls out of
one of those. So the grain here is customer x account x month, the
behavioural score and the variables behind it are first-class, and the
segmentation is product and score band rather than sector.

The authored stories
--------------------
Credit Card utilisation and delinquency build through the window while
Mortgage stays sound; the 2024 Personal Finance vintage deteriorates well
ahead of the 2025 one; score band D thins as its accounts fall into E; and a
cohort of Auto Finance accounts cures and improves. Not everything worsens,
because a book where it does teaches an analyst nothing about telling signal
from level.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4.generate import month_range, months_between
from backend.cockpit_v4.generate.totals import exact_mean, exact_total, whole_total

SEED = 20260802

#: A retail book is a POPULATION. At nine hundred customers the portfolio
#: came to six million riyals of ECL and the attention cards read "up SAR 0
#: million" -- arithmetically true and useless, because the book was too
#: small for the unit it is denominated in. A mass-market book is tens of
#: thousands of accounts; this is the smallest number that makes the figures
#: read like a portfolio rather than a branch.
CUSTOMERS = 26000

#: Releases this generator no longer produces. The earlier ids are published,
#: fingerprinted books of a smaller population.
FROZEN_RELEASES: frozenset[str] = frozenset({
    "v4-saudi-retail-20m-v1",
    # -20m-v3 is the book before sub-product, employment type, the fine
    # arrears bands and Buy Now Pay Later. Published, readable, and not
    # rebuildable from here.
    "v4-saudi-retail-20m-v3",
    # -20m-v4 is the book this generator built back when the customer mean
    # went through the builtin `sum`, whose float behaviour CPython changed
    # in 3.12. The id therefore names one book on an interpreter before
    # 3.12 and a different one after it, and an id that means two things is
    # an id nothing can be pinned to. Frozen rather than corrected: the
    # copies already published under it are real and still readable, and
    # rebuilding it here could only produce one of its two meanings.
    "v4-saudi-retail-20m-v4"})


class FrozenRelease(ValueError):
    """A published release this generator must not rebuild."""

#: The product a BNPL book is written under, and the month it starts.
#:
#: A product that did not exist a year ago and is carrying the losses is the
#: thing a retail credit committee most often has to find, and a book whose
#: product list is constant across its whole window cannot pose that
#: question. Its `share` is zero because BNPL accounts are not drawn from
#: the weighted origination -- they are booked explicitly from
#: `NEW_PRODUCT_FROM` onward, so no row of it exists before that month.
NEW_PRODUCT = "Buy Now Pay Later"
NEW_PRODUCT_FROM = "2026-03"

PRODUCTS: tuple[tuple[str, int, float, float, float], ...] = (
    # (product, secured, share, base limit SAR mn, base LGD)
    ("Mortgage", 1, 0.22, 1.35, 0.18),
    ("Personal Finance", 0, 0.31, 0.22, 0.62),
    ("Auto Finance", 1, 0.19, 0.16, 0.42),
    ("Credit Card", 0, 0.28, 0.055, 0.78),
    (NEW_PRODUCT, 0, 0.00, 0.011, 0.71),
)

#: What a product is actually sold as. A review that stops at "Credit Card"
#: cannot answer "which card", and "which card" is usually where the answer
#: is: a book is rarely uniformly bad, it has a bad corner. Shares are
#: within the product.
SUB_PRODUCTS: dict[str, tuple[tuple[str, float], ...]] = {
    "Mortgage": (("Fixed Rate", 0.58), ("Variable Rate", 0.42)),
    "Personal Finance": (("Salary Advance", 0.41),
                         ("Consumer Durable", 0.24),
                         ("Debt Consolidation", 0.35)),
    "Auto Finance": (("New Vehicle", 0.62), ("Used Vehicle", 0.38)),
    "Credit Card": (("Classic", 0.52), ("Gold", 0.33), ("Signature", 0.15)),
    NEW_PRODUCT: (("Instalment 3M", 0.64), ("Instalment 6M", 0.36)),
}

#: How the customer is paid. In Saudi retail this is the strongest policy
#: lever there is: salary transfer to the lending bank, a government versus
#: a private employer, and self-employed or non-salaried income all carry
#: different limits, different debt-burden caps and different cut-offs.
EMPLOYMENT_TYPES: tuple[tuple[str, float], ...] = (
    ("Salaried-Government", 0.31),
    ("Salaried-Private", 0.42),
    ("Self-employed", 0.16),
    ("Non-salaried", 0.11),
)

#: Where the account was written. A hazard that follows the CHANNEL rather
#: than the product is a different finding and a different remedy.
CHANNELS: tuple[tuple[str, float], ...] = (
    ("Branch", 0.44), ("Digital", 0.38), ("Partner", 0.18))

SEGMENTS = ("Mass", "Affluent", "Private", "Payroll")
REGIONS = ("Riyadh", "Makkah", "Eastern Province", "Madinah", "Qassim",
           "Asir", "Tabuk", "Hail")
BANDS = ("A", "B", "C", "D", "E")

#: How hard each product's population is pushed over the window.
PRODUCT_STRESS: dict[str, float] = {
    "Credit Card": 1.00,
    "Personal Finance": 0.62,
    "Auto Finance": -0.25,
    "Mortgage": 0.08,
    NEW_PRODUCT: 1.30,
}

#: What the composite pressure is worth in absolute terms.
#:
#: Tuned against the book it produces, not chosen first. At 1.0 the stressed
#: products came out with ninety-seven per cent of card accounts in Stage 2 --
#: arithmetically consistent and not a portfolio any bank would recognise, so
#: every question asked of it would have a silly answer. The deterioration is
#: meant to be findable, not total.
STRESS_SCALE = 0.27

#: Monthly probability that a CURRENT account misses a payment, before the
#: account's own fragility and the product's stress are applied.
#:
#: Delinquency is not a threshold on a smooth stress index. Modelled that
#: way -- which is how this generator began -- the worst account in the book
#: reached eighty days past due and SEVEN accounts out of twelve thousand
#: ever defaulted, because a smooth index has no tail. A retail book without
#: a tail has no non-performing population, so every question about arrears,
#: Stage 3, write-offs, cures or coverage answers "approximately nothing" and
#: means nothing.
#:
#: So arrears are a ROLL-RATE process: an account enters at a hazard, then
#: each month either cures or rolls thirty days deeper, and one deep enough
#: is charged off and leaves the book. Stress raises the entry hazard and
#: slows the cure; it does not decide the outcome.
ENTRY_HAZARD: dict[str, float] = {
    "Credit Card": 0.022,
    "Personal Finance": 0.015,
    "Auto Finance": 0.008,
    "Mortgage": 0.0035,
    # A new product written fast through a partner channel, with no
    # performance history behind its cut-offs. Three times the card book.
    NEW_PRODUCT: 0.066,
}

#: Monthly probability that a delinquent account cures, before depth,
#: fragility and stress. Cure gets materially harder the deeper the arrears:
#: a bucket an account has sat in for four months is not one it walks out of
#: at the same rate as the first missed payment.
CURE_RATE: dict[str, float] = {
    "Credit Card": 0.30,
    "Personal Finance": 0.27,
    "Auto Finance": 0.34,
    "Mortgage": 0.40,
    # Small tickets on short tenors: the ones that cure, cure quickly.
    NEW_PRODUCT: 0.38,
}

#: Days past due at which the account is charged off, written down and
#: leaves the book. Saudi retail practice and IFRS 9 both put the
#: write-off point well past the ninety-day default marker.
CHARGE_OFF_DPD = 210

#: How much of the exposure is written off at charge-off, and how much of
#: THAT comes back. Secured products recover more, which is what the security
#: is for.
WRITE_OFF_FRACTION = 0.82
RECOVERY_OF_WRITE_OFF = {0: 0.14, 1: 0.46}

# ---- the planted patterns ------------------------------------------------
#
# Everything above shapes the book as a whole. What follows is deliberate
# STRUCTURE inside it: six findings a credit team would be expected to
# reach, each reachable by a different cut, and each with a wrong answer
# sitting next to the right one. A book that only trends teaches an analyst
# nothing about telling a segment from a mix or a season from a slide.
#
# Every one of them is recovered by a direct SQL oracle in
# `tests/cockpit_v4/domain_oracles.py`, so a tuning change that quietly
# buries one fails a test rather than being discovered in a demonstration.

#: R2. The corner of the card book that is actually moving.
#:
#: Credit Card - Gold held by Non-salaried customers, from 2026-01. The
#: product total barely moves, because Classic is two-thirds of it and
#: Classic is flat; the answer is only visible at product x sub-product x
#: employment type. Entries land in 20-29 first and reach 30-59 two months
#: later, so a reader who stops at the `1-29` bucket sees a small rise and a
#: reader who splits it sees where the rise is.
GOLD_COHORT = ("Credit Card", "Gold", "Non-salaried")
GOLD_FROM = "2026-01"
GOLD_HAZARD_MULTIPLE = 4.2

#: R6. One region, one sub-product. Used-vehicle Auto Finance in Qassim,
#: from 2025-11. Auto Finance is the book's IMPROVING product, so a reader
#: who looks at the product line concludes it is fine.
REGION_POCKET = ("Auto Finance", "Used Vehicle", "Qassim")
REGION_POCKET_FROM = "2025-11"
REGION_POCKET_MULTIPLE = 5.2

#: R4. A vintage, not a calendar month. Accounts written in the last quarter
#: of 2025 underperform at EQUAL months on book -- a cut-off that was moved
#: and moved back. In calendar time they are indistinguishable, because they
#: are young and a young account is current whatever was wrong with it.
BAD_VINTAGE_MONTHS = ("2025-10", "2025-11", "2025-12")
BAD_VINTAGE_HAZARD_MULTIPLE = 2.3

#: R5. Ramadan and Eid. Spending rises, a payment is missed, and it is made
#: up within two months. This is NOT deterioration and an analysis that
#: calls it deterioration is wrong, which is the point of including it.
#: Ramadan fell in March 2025 and February 2026 over this window.
SEASON_MONTHS = ("2025-03", "2026-02")
SEASON_HAZARD_MULTIPLE = 2.1
#: The two months after a seasonal spike, when it cures.
SEASON_CURE_MULTIPLE = 1.9

#: R3. Simpson's paradox, planted rather than hoped for.
#:
#: Mortgage is the cleanest product in the book and it takes a growing share
#: of new accounts across the window, so a portfolio rate is being diluted
#: at the same time as every product's own rate rises. The tilt below does
#: that gently across all vintages; `MORTGAGE_CAMPAIGN_FROM` does it hard at
#: the end, where a reader is looking.
#:
#: The claim this supports is NOT that the book's arrears fall to nothing --
#: that would take a book nine-tenths mortgage, which is not a book. It is
#: the real and far commoner version: over the closing months the PORTFOLIO
#: delinquency rate is flat or falling while the rate inside EVERY product
#: is rising. "Is the book improving?" then has two defensible answers, only
#: one of them is right, and an analysis that reports the portfolio line
#: without the mix has given the wrong one.
MIX_SHIFT_PRODUCT = "Mortgage"
MIX_SHIFT_WEIGHT = 1.75

#: The campaign that does the diluting: clean mortgage accounts written in
#: volume over the closing months, as a share of the customer base. Real,
#: and the ordinary reason a portfolio ratio moves without any account in it
#: having changed.
MORTGAGE_CAMPAIGN_FROM = "2025-11"
MORTGAGE_CAMPAIGN_SHARE = 0.20

#: Older vintages carry more of the stress. A 2024 book has been through more
#: than a 2026 one and it should show.
VINTAGE_STRESS: dict[int, float] = {2021: 0.15, 2022: 0.30, 2023: 0.55,
                                    2024: 0.95, 2025: 0.45, 2026: 0.10}


def _band(score: float) -> str:
    if score >= 780:
        return "A"
    if score >= 700:
        return "B"
    if score >= 620:
        return "C"
    if score >= 540:
        return "D"
    return "E"


def _bucket(dpd: int) -> str:
    if dpd <= 0:
        return "Current"
    if dpd < 30:
        return "1-29"
    if dpd < 60:
        return "30-59"
    if dpd < 90:
        return "60-89"
    return "90+"


def _fine_bucket(dpd: int) -> str:
    """The arrears band a collections team actually works to.

    `delinquency_bucket` keeps its five values, because every alias, test
    and saved question in this product is written against them. This sits
    BESIDE it. The two splits are the ones that carry information the
    coarse banding hides: the first thirty days, where an account is still
    recoverable by a phone call and the tenth day is a different problem
    from the twenty-fifth, and the non-performing tail, where 90-179 and
    180+ are a provisioning difference rather than a shade of the same
    thing.
    """
    if dpd <= 0:
        return "Current"
    if dpd < 10:
        return "1-9"
    if dpd < 20:
        return "10-19"
    if dpd < 30:
        return "20-29"
    if dpd < 60:
        return "30-59"
    if dpd < 90:
        return "60-89"
    if dpd < 180:
        return "90-179"
    return "180+"


def _pick(rng: random.Random, choices: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([c[0] for c in choices],
                       weights=[c[1] for c in choices], k=1)[0]


def _in_cohort(account: dict[str, Any], customer: dict[str, Any],
               cohort: tuple[str, str, str]) -> bool:
    product, sub_product, third = cohort
    if account["product"] != product or account["sub_product"] != sub_product:
        return False
    return third in (customer.get("employment_type"), customer.get("region"))


def _entry_multiple(account: dict[str, Any], customer: dict[str, Any],
                    month: str) -> float:
    """How much likelier THIS account is to miss a payment THIS month.

    One multiplier on the entry hazard carries every planted pattern, so a
    pattern is a statement about who and when rather than a second process
    bolted beside the roll-rate one. A pattern that is not in force returns
    1.0 and the book behaves exactly as it did.
    """
    multiple = 1.0
    if (month >= GOLD_FROM
            and _in_cohort(account, customer, GOLD_COHORT)):
        multiple *= GOLD_HAZARD_MULTIPLE
    if (month >= REGION_POCKET_FROM
            and _in_cohort(account, customer, REGION_POCKET)):
        multiple *= REGION_POCKET_MULTIPLE
    if account["origination_month"] in BAD_VINTAGE_MONTHS:
        multiple *= BAD_VINTAGE_HAZARD_MULTIPLE
    if month in SEASON_MONTHS:
        multiple *= SEASON_HAZARD_MULTIPLE
    return multiple


def _season_cure(month: str) -> float:
    """The months a seasonal miss is made up in.

    Without this the Ramadan spike is indistinguishable from a slide: the
    accounts that entered would roll on at the ordinary rate and the shape
    would be a step, not a bump. A season is a thing that REVERSES, and a
    book where it does not reverse cannot be used to ask whether a reader
    can tell the two apart.
    """
    for spike in SEASON_MONTHS:
        year, mon = (int(p) for p in spike.split("-"))
        for ahead in (1, 2):
            total = year * 12 + (mon - 1) + ahead
            if f"{total // 12:04d}-{total % 12 + 1:02d}" == month:
                return SEASON_CURE_MULTIPLE
    return 1.0


def _entry_dpd(account: dict[str, Any], customer: dict[str, Any],
               month: str, draw: float) -> int:
    """How deep an account lands when it first misses.

    The Gold cohort enters LATE in the month rather than early -- a missed
    salary date rather than a forgotten payment -- so its entries pile into
    20-29 and only reach 30-59 when they roll. That is the drill-down the
    reader is meant to be able to make: the 1-29 population rose, and WHICH
    part of 1-29 it rose in says whether this is friction or distress.
    """
    if month >= GOLD_FROM and _in_cohort(account, customer, GOLD_COHORT):
        # MOSTLY 20-29, not entirely. A cohort every one of whose entries
        # lands in one band is a label, not a distribution, and an analyst
        # who finds it learns that the data was written rather than that
        # the salary date moved. Seven in ten is a concentration a reader
        # has to notice rather than trip over.
        if draw < 0.70:
            return 20 + int(9 * (draw / 0.70))
        return 4 + int(15 * ((draw - 0.70) / 0.30))
    return 4 + int(24 * draw)


def _unit(key: str, month: str) -> float:
    """A stable uniform draw in [0, 1) for one entity in one month.

    Hashed rather than drawn from a stream, so the roll-rate process is a
    pure function of who and when: two builds produce the same arrears, and
    adding a customer does not reshuffle everyone else's.
    """
    digest = hashlib.sha256(f"roll|{key}|{month}".encode("utf-8")).digest()
    return int.from_bytes(digest[:6], "big") / float(1 << 48)


def _jitter(key: str, month: str, spread: float) -> float:
    """A per-(entity, month) wobble that is stable across builds.

    Without it every account drifts the same way every month and the book
    reads as a metronome: eight hundred customers "DETERIORATED" and fifteen
    "IMPROVED", which tells an analyst nothing because it is true of
    everyone. Real months are mixed even inside a deteriorating trend, and a
    score that only ever falls is not a score anybody would act on.

    Hashed rather than drawn, so it is a pure function of who and when and
    two builds fingerprint identically.
    """
    digest = hashlib.sha256(f"{key}|{month}".encode("utf-8")).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return (unit - 0.5) * 2 * spread


def _ramp(index: int, total: int) -> float:
    if total <= 1:
        return 1.0
    x = index / (total - 1)
    # Eases in and keeps going. Smoothstep was the first shape here and its
    # slope falls to zero at BOTH ends, so the last few months of the window
    # barely moved -- which made every month-on-month attention card tiny and
    # left the feed with two findings on a book that had plainly changed. A
    # book whose story stops before its last month is a book nobody can ask
    # "what moved this month".
    return x ** 1.55


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build(release_id: str = "",
          tenant_id: str = lake.DEFAULT_TENANT) -> lake.Build:
    import pandas as pd

    release_id = release_id or dom.DEFAULT_RELEASES[dom.RETAIL]
    if release_id in FROZEN_RELEASES:
        raise FrozenRelease(
            f"{release_id} is published and immutable, and this generator "
            f"now writes a larger book. The current Retail release is "
            f"{dom.DEFAULT_RELEASES[dom.RETAIL]}.")
    months = month_range()
    rng = random.Random(SEED)

    openable = [p for p in PRODUCTS if p[2] > 0]
    weights = [p[2] for p in openable]
    customers: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    def _open(customer_id: str, slot: str, product_row, origin: str,
              *, channel: str = "") -> dict[str, Any]:
        product, secured, _share, base_limit, base_lgd = product_row
        return {
            "account_id": f"RA{customer_id[2:]}{slot}",
            "customer_id": customer_id,
            "product": product,
            "sub_product": _pick(rng, SUB_PRODUCTS[product]),
            "secured_flag": secured,
            "origination_channel": channel or _pick(rng, CHANNELS),
            "origination_month": origin,
            "vintage_year": int(origin[:4]),
            "base_limit": round(base_limit * rng.uniform(0.5, 2.4), 4),
            "base_util": rng.uniform(0.18, 0.94),
            "base_lgd": base_lgd,
            "wobble": rng.uniform(-0.04, 0.04),
            "cure_cohort": rng.random() < 0.08,
            # Heavy-tailed on purpose: most accounts are near zero and a
            # few are genuinely fragile. A uniform draw here would give
            # every account the same modest chance of arrears and
            # produce a book with no tail, which is the defect this
            # replaces.
            "fragility": round(rng.random() ** 3.0, 6),
        }

    for index in range(CUSTOMERS):
        customer_id = f"RC{index + 1:06d}"
        segment = SEGMENTS[index % len(SEGMENTS)]
        customers.append({
            "customer_id": customer_id,
            "customer_segment": segment,
            "region": REGIONS[(index * 3) % len(REGIONS)],
            "employment_type": _pick(rng, EMPLOYMENT_TYPES),
            "base_tenure": rng.randint(6, 168),
            "base_score": rng.uniform(520, 860),
            "quality": rng.uniform(0.0, 1.0),
        })
        for slot in range(1 if index % 3 else 2):
            # Origination is spread back before the window so vintages differ.
            origin_year = rng.choices([2021, 2022, 2023, 2024, 2025, 2026],
                                      weights=[5, 9, 16, 26, 30, 14], k=1)[0]
            origin_month = rng.randint(1, 12)
            if origin_year == 2026:
                origin_month = min(origin_month, 8)
            # R3, SIMPSON'S PARADOX, PLANTED AT ORIGINATION. The mortgage
            # share of NEW accounts grows across the window, so the book's
            # average PD is pulled down by mix while every product's own PD
            # is pushed up by stress. Mixing the two is the commonest way an
            # otherwise correct portfolio number says the opposite of what
            # is happening, and an analysis that cannot be caught by it
            # cannot be trusted when it says the book is improving.
            recency = _clamp((origin_year - 2021) / 5.0, 0.0, 1.0)
            tilted = [w * (1 + (MIX_SHIFT_WEIGHT - 1) * recency)
                      if p[0] == MIX_SHIFT_PRODUCT else w
                      for p, w in zip(openable, weights)]
            product_row = rng.choices(openable, weights=tilted, k=1)[0]
            accounts.append(_open(
                customer_id, str(slot), product_row,
                f"{origin_year:04d}-{origin_month:02d}"))

    # R1. THE PRODUCT THAT DID NOT EXIST A YEAR AGO.
    #
    # Booked separately, because every account of it is originated INSIDE
    # the window and none before: a book whose BNPL rows start in 2026-03 is
    # the only kind that can be asked "what is new in this book". Written
    # fast and almost entirely through the partner channel, at three times
    # the card book's entry hazard, so it is carrying losses out of
    # proportion to its size within two months of launch.
    bnpl_row = next(p for p in PRODUCTS if p[0] == NEW_PRODUCT)
    bnpl_months = [m for m in months if m >= NEW_PRODUCT_FROM]
    for offset in range(int(CUSTOMERS * 0.16)):
        holder = customers[(offset * 7 + 3) % len(customers)]
        origin = bnpl_months[min(int(_unit(f"bnpl{offset}", "open")
                                     ** 0.7 * len(bnpl_months)),
                                 len(bnpl_months) - 1)]
        accounts.append(_open(
            holder["customer_id"], f"B{offset % 10}", bnpl_row, origin,
            channel="Partner" if offset % 5 else "Digital"))

    # R3. THE MORTGAGE CAMPAIGN, booked over the closing months.
    #
    # The same mechanism as the BNPL block and the opposite finding: a
    # volume of the CLEANEST product, written late, diluting every
    # portfolio ratio it lands in. Without it the mix tilt above is real
    # but small, and a paradox nobody can see is not a test of anything.
    mortgage_row = next(p for p in PRODUCTS if p[0] == MIX_SHIFT_PRODUCT)
    campaign_months = [m for m in months if m >= MORTGAGE_CAMPAIGN_FROM]
    for offset in range(int(CUSTOMERS * MORTGAGE_CAMPAIGN_SHARE)):
        holder = customers[(offset * 11 + 5) % len(customers)]
        origin = campaign_months[
            int(_unit(f"campaign{offset}", "open") * len(campaign_months))]
        accounts.append(_open(
            holder["customer_id"], f"M{offset // len(customers)}",
            mortgage_row, origin,
            channel="Digital" if offset % 3 else "Branch"))

    by_id = {c["customer_id"]: c for c in customers}
    gov = {"tenant_id": tenant_id, "dataset_release_id": release_id,
           "domain_id": dom.RETAIL, "reporting_currency": lake.CURRENCY}

    account_rows: list[dict[str, Any]] = []
    behaviour_rows: list[dict[str, Any]] = []
    collateral_rows: list[dict[str, Any]] = []
    customer_rows: list[dict[str, Any]] = []
    previous_score: dict[str, float] = {}
    previous_util: dict[str, float] = {}
    previous_balance: dict[str, float] = {}
    streak: dict[str, int] = {}
    #: Days past due carried into the next month, and the accounts that have
    #: been charged off and no longer report.
    arrears: dict[str, int] = {}
    charged_off: set[str] = set()

    # THE BOOK DOES NOT OPEN CLEAN. `arrears` starting empty meant every
    # account was Current in the first month of the window, so the book's
    # delinquency rate began at exactly zero and every comparison drawn
    # against the first month measured the generator warming up rather than
    # anything about the portfolio. A twenty-month window is a slice out of
    # a book that was already running; the accounts already open carry the
    # arrears they had.
    for account in accounts:
        if months_between(account["origination_month"], months[0]) < 0:
            continue
        opening = _unit(account["account_id"], "opening")
        standing = (ENTRY_HAZARD[account["product"]] * 4.2
                    * (0.25 + 3.1 * account["fragility"]))
        if opening >= standing:
            continue
        # Deeper buckets are thinner, because an account has to survive
        # every earlier one to reach them.
        depth = _unit(account["account_id"], "opening-depth")
        arrears[account["account_id"]] = (
            4 + int(24 * depth) if depth < 0.62
            else 30 + 30 * int((depth - 0.62) / 0.095))

    for m_index, month in enumerate(months):
        ramp = _ramp(m_index, len(months))
        season = math.sin((m_index % 12) / 12 * 2 * math.pi)
        per_customer: dict[str, list[dict[str, Any]]] = {}

        for account in accounts:
            if months_between(account["origination_month"], month) < 0:
                continue  # not opened yet: no row rather than a zero row
            if account["account_id"] in charged_off:
                continue  # written off and closed: no row rather than zeros
            customer = by_id[account["customer_id"]]
            on_book = months_between(account["origination_month"], month)

            stress = PRODUCT_STRESS.get(account["product"], 0.0) * ramp
            stress *= 0.6 + 0.9 * VINTAGE_STRESS.get(account["vintage_year"],
                                                     0.4)
            personal = (stress * STRESS_SCALE
                        * (0.5 + 1.0 * (1 - customer["quality"])))
            personal += _jitter(account["account_id"] + "p", month, 0.07)
            if account["cure_cohort"] and m_index > len(months) // 2:
                # A cohort that works its way back. Real books have these and
                # a book without them reads as a one-way slide.
                personal -= 0.35 * _ramp(m_index - len(months) // 2,
                                         len(months) // 2)

            limit = account["base_limit"] * (1 + 0.002 * on_book)
            util = _clamp(account["base_util"] + 0.20 * personal
                          + 0.03 * season + account["wobble"]
                          + _jitter(account["account_id"], month, 0.05),
                          0.01, 1.22)
            balance = limit * min(util, 1.15)
            ead = balance if account["product"] != "Credit Card" else (
                balance + max(0.0, limit - balance) * 0.45)

            score = _clamp(customer["base_score"] - 190 * personal
                           + 9 * season
                           + _jitter(account["customer_id"], month, 26.0),
                           300, 900)
            band = _band(score)
            prior_score = previous_score.get(account["customer_id"], score)

            # The roll-rate process. Entry, then cure or roll, then
            # charge-off. Stress moves the rates; it does not set the state.
            account_id = account["account_id"]
            prior_dpd = arrears.get(account_id, 0)
            pressure = max(0.0, personal)
            draw = _unit(account_id, month)
            fragility = account["fragility"]
            if prior_dpd <= 0:
                hazard = (ENTRY_HAZARD[account["product"]]
                          * (0.25 + 3.1 * fragility)
                          * (1.0 + 2.6 * pressure)
                          * _entry_multiple(account, customer, month))
                dpd = (_entry_dpd(account, customer, month,
                                  _unit(account_id + "entry", month))
                       if draw < hazard else 0)
            else:
                depth = min(1.0, prior_dpd / float(CHARGE_OFF_DPD))
                cure = (CURE_RATE[account["product"]]
                        * (1.25 - 0.85 * fragility)
                        * (1.0 - 0.72 * depth)
                        / (1.0 + 1.4 * pressure)
                        # A SEASONAL MISS IS MADE UP. Without this the
                        # Ramadan entries roll on at the ordinary rate and
                        # the shape is a step rather than a bump, which is
                        # a different finding and the wrong one.
                        * (_season_cure(month) if prior_dpd < 60 else 1.0))
                dpd = 0 if draw < cure else prior_dpd + 30
            was = streak.get(account_id, 0)
            streak[account_id] = was + 1 if dpd > 0 else 0
            arrears[account_id] = dpd

            # Staging from OBSERVABLE triggers, not from the stress index.
            #
            # Reading Stage 2 off a smooth index put forty-eight per cent of
            # the card book in Stage 2 while five per cent of it was actually
            # in arrears -- a book no bank would recognise, and one where
            # "what drove the Stage 2 increase?" has no answer an analyst
            # could act on. The triggers below are the ones a retail SICR
            # policy actually uses: thirty days past due, an absolute score
            # floor below the lowest band boundary, and a material fall from
            # the score the account was written at.
            score_fall = customer["base_score"] - score
            if dpd >= 90:
                stage = 3
            elif dpd >= 30 or score < 520 or score_fall > 105:
                stage = 2
            else:
                stage = 1
            lgd = _clamp(account["base_lgd"] + 0.10 * personal
                         - (0.12 if account["secured_flag"] else 0.0),
                         0.05, 0.92)
            if stage == 3:
                # An account ninety days past due has defaulted. Pricing it
                # off a score-driven PD of a few per cent put the Stage 3
                # coverage of this book at twelve per cent, which says the
                # bank expects to collect eighty-eight per cent of its
                # non-performing retail book. It does not.
                pd_pit = pd_life = 1.0
                ecl_12m = ecl_life = ead * lgd
            else:
                pd_pit = _clamp(0.006 * math.exp((900 - score) / 150)
                                + 0.02 * personal, 0.0004, 0.92)
                pd_life = _clamp(pd_pit * (2.2 + 1.8 * personal), pd_pit,
                                 0.98)
                ecl_12m = ead * pd_pit * lgd
                ecl_life = ead * pd_life * lgd
            ecl = ecl_12m if stage == 1 else ecl_life
            # Charge-off: the account is written down in THIS month's row and
            # reports no month after it. The write-off is a flow and the row
            # that carries it is the last one the account has.
            wrote_off = 0.0
            recovered = 0.0
            if dpd >= CHARGE_OFF_DPD:
                wrote_off = ead * WRITE_OFF_FRACTION
                recovered = wrote_off * RECOVERY_OF_WRITE_OFF[
                    account["secured_flag"]]
                charged_off.add(account_id)
                # A charged-off account is fully provided at the point it
                # leaves: carrying a lifetime ECL below the amount written
                # off would say the book expected to keep it.
                ecl = max(ecl, wrote_off - recovered)
                ecl_life = max(ecl_life, ecl)
            cured = 1 if (was > 0 and dpd == 0) else 0

            row = {
                **gov,
                "account_id": account["account_id"],
                "customer_id": account["customer_id"],
                "reporting_month": month, "product": account["product"],
                "sub_product": account["sub_product"],
                "secured_flag": account["secured_flag"],
                "origination_month": account["origination_month"],
                "origination_channel": account["origination_channel"],
                "vintage_year": account["vintage_year"],
                "months_on_book": on_book,
                "customer_segment": customer["customer_segment"],
                "employment_type": customer["employment_type"],
                "region": customer["region"],
                "limit_sar_mn": round(limit, 5),
                "balance_sar_mn": round(balance, 5),
                "ead_sar_mn": round(ead, 5),
                "utilisation_pct": round(util * 100, 3),
                "stage": stage, "sicr_flag": 1 if stage >= 2 else 0,
                "default_flag": 1 if stage == 3 else 0,
                "dpd_days": dpd, "delinquency_bucket": _bucket(dpd),
                "delinquency_bucket_fine": _fine_bucket(dpd),
                "pd_pit_12m": round(pd_pit, 6),
                "pd_lifetime": round(pd_life, 6),
                "lgd_pct": round(lgd * 100, 3),
                "ecl_12m_sar_mn": round(ecl_12m, 6),
                "ecl_lifetime_sar_mn": round(ecl_life, 6),
                "ecl_sar_mn": round(ecl, 6),
                "write_off_sar_mn": round(wrote_off, 6),
                "recovery_sar_mn": round(recovered, 6),
                "cure_flag": cured,
                "behaviour_score": round(score, 2), "score_band": band,
            }
            account_rows.append(row)
            per_customer.setdefault(account["customer_id"], []).append(row)

            prior_util = previous_util.get(account["account_id"], util)
            prior_balance = previous_balance.get(account["account_id"],
                                                 balance)
            previous_util[account["account_id"]] = util
            previous_balance[account["account_id"]] = balance
            behaviour_rows.append({
                **gov,
                "account_id": account["account_id"],
                "customer_id": account["customer_id"],
                "reporting_month": month, "product": account["product"],
                "sub_product": account["sub_product"],
                "utilisation_pct": round(util * 100, 3),
                "utilisation_change_pp": round((util - prior_util) * 100, 3),
                "payment_ratio_pct": round(
                    _clamp(38 - 26 * personal + 4 * season, 0.0, 100.0), 3),
                "missed_payments_12m": int(max(0, round(personal * 5.2))),
                "delinquency_streak_months": streak[account["account_id"]],
                "balance_growth_pct": round(
                    (balance - prior_balance) / max(prior_balance, 1e-6)
                    * 100, 3),
                "cash_advance_ratio_pct": round(
                    _clamp(3 + 22 * personal, 0.0, 60.0), 3)
                if account["product"] == "Credit Card" else 0.0,
                "overlimit_flag": 1 if util > 1.0 else 0,
                "inflow_change_pct": round(-9.5 * personal + 2.5 * season, 3),
                "bureau_inquiries_6m": int(max(0, round(personal * 4.4))),
                "repayment_behaviour_score": round(
                    _clamp(82 - 55 * personal, 2, 99), 2),
            })

            if account["secured_flag"]:
                drift = -0.10 if account["product"] == "Mortgage" else -0.22
                value = (limit * (1.32 if account["product"] == "Mortgage"
                                  else 1.18)
                         * (1 + drift * (on_book / 60.0)))
                collateral_rows.append({
                    **gov,
                    "account_id": account["account_id"],
                    "customer_id": account["customer_id"],
                    "reporting_month": month, "product": account["product"],
                    "collateral_type": ("Residential Property"
                                        if account["product"] == "Mortgage"
                                        else "Motor Vehicle"),
                    "collateral_value_sar_mn": round(value, 5),
                    "ltv_pct": round(balance / max(value, 1e-6) * 100, 3),
                    "collateral_coverage_pct": round(
                        value / max(ead, 1e-6) * 100, 3),
                    "valuation_age_months": (on_book % 18) + 1,
                })

        for customer_id, rows in per_customer.items():
            customer = by_id[customer_id]
            # THE MEAN THAT COST A RELEASE. Its inputs are already rounded
            # to two places, so it lands on a half-cent tie constantly and
            # `round(score, 2)` below is decided by the last bit of the
            # sum -- which the builtin changed in CPython 3.12.
            score = exact_mean(r["behaviour_score"] for r in rows)
            prior = previous_score.get(customer_id, score)
            previous_score[customer_id] = score
            band, prior_band = _band(score), _band(prior)
            customer_rows.append({
                **gov,
                "customer_id": customer_id, "reporting_month": month,
                "customer_segment": customer["customer_segment"],
                "employment_type": customer["employment_type"],
                "region": customer["region"],
                "tenure_months": customer["base_tenure"] + m_index,
                "accounts_held": len(rows),
                "behaviour_score": round(score, 2),
                "behaviour_score_previous": round(prior, 2),
                "behaviour_score_change": round(score - prior, 2),
                "score_band": band, "score_band_previous": prior_band,
                "score_migration": ("DETERIORATED" if score < prior - 2
                                    else "IMPROVED" if score > prior + 2
                                    else "STABLE"),
                "total_ead_sar_mn": round(
                    exact_total(r["ead_sar_mn"] for r in rows), 5),
                "total_ecl_sar_mn": round(
                    exact_total(r["ecl_sar_mn"] for r in rows), 6),
                "worst_stage": max(r["stage"] for r in rows),
                "worst_dpd_days": max(r["dpd_days"] for r in rows),
            })

    frames = {
        "retail_customer_month": pd.DataFrame(customer_rows),
        "retail_account_month": pd.DataFrame(account_rows),
        "retail_behaviour_month": pd.DataFrame(behaviour_rows),
        "retail_collateral_month": pd.DataFrame(collateral_rows),
    }
    return lake.Build(
        domain_id=dom.RETAIL, release_id=release_id, periods=months,
        frames=frames,
        counts={"customers": len(customers), "accounts": len(accounts),
                "products": len(PRODUCTS),
                "sub_products": whole_total(
                    len(v) for v in SUB_PRODUCTS.values()),
                "employment_types": len(EMPLOYMENT_TYPES),
                "regions": len(REGIONS)},
        notes={"score_bands": list(BANDS),
               "stressed_products": [p for p, v in PRODUCT_STRESS.items()
                                     if v > 0.3],
               "improving_products": [p for p, v in PRODUCT_STRESS.items()
                                      if v < 0],
               "product_launched_in_window": {
                   "product": NEW_PRODUCT, "first_month": NEW_PRODUCT_FROM},
               "employment_types": [e[0] for e in EMPLOYMENT_TYPES],
               "origination_channels": [c[0] for c in CHANNELS]})


__all__ = ["BANDS", "CHANNELS", "CUSTOMERS", "EMPLOYMENT_TYPES",
           "NEW_PRODUCT", "NEW_PRODUCT_FROM", "PRODUCTS",
           "PRODUCT_STRESS", "SUB_PRODUCTS", "build"]
