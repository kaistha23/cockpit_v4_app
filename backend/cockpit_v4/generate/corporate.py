"""
A Saudi corporate credit book, twenty QUARTERS of it, with things to find.

Why quarters
------------
A corporate credit file is reviewed on the cycle its obligors actually report
on. Audited and reviewed financials, internal rating actions, covenant tests
and the IFRS 9 stage that follows them all land quarterly; a monthly
corporate book invents twelve observations a year that no credit committee
ever saw. The retail book next door is monthly for the opposite reason:
retail risk is behavioural, and behaviour is a monthly signal.

So this book publishes `reporting_quarter`, its relations are named for it,
and nothing in it pretends a month is available.

What this is not
----------------
It is not noise. A synthetic book whose every metric wanders randomly is
useless for the thing it exists to support: an analyst asking "what is
deteriorating and why" and getting an answer that holds together. So the
dynamics here are authored -- a few sectors under real stress, a downgrade
cluster that shows up in ratings before it shows up in stage, covenant
headroom eroding where leverage is rising, collateral values softening in
one sector and firming in another -- and the rest of the book is stable or
quietly improving, because a portfolio where everything worsens at once is
its own kind of unbelievable.

Determinism
-----------
Every number comes from a seeded generator and a pure function of
(entity, quarter). Two builds of the same release id produce byte-identical
parquet and therefore the same fingerprint, which is what makes the
fingerprint worth checking.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4.generate import quarter_range, quarters_between
from backend.cockpit_v4.generate.totals import exact_total, whole_total

SEED = 20260803

#: Releases this generator no longer produces.
#:
#: The `-20m-` releases are published, fingerprinted books of a MONTHLY
#: corporate portfolio. This generator now writes a quarterly one, so asking
#: it for those ids would hand back different bytes under a name that already
#: means something else -- which is the one thing an immutable release id
#: exists to prevent. Refused by name rather than silently obliged.
FROZEN_RELEASES: frozenset[str] = frozenset({
    "v4-saudi-corporate-20m-v1", "v4-saudi-corporate-20m-v2",
    # -20q-v3 is the book before the planted patterns and the larger
    # population. Published, readable, and not rebuildable from here.
    "v4-saudi-corporate-20q-v3"})


class FrozenRelease(ValueError):
    """A published release this generator must not rebuild."""


# ---- the population ----------------------------------------------------

#: Fictional Saudi and GCC-style names. No real institution is described.
BORROWERS: tuple[tuple[str, str, str, str], ...] = (
    # (name, sector, sub_sector, region)
    ("Al Noor Infrastructure", "Construction", "Civil Infrastructure", "Riyadh"),
    ("Tuwaiq Construction", "Construction", "Building Contracting", "Riyadh"),
    ("Najd Manufacturing", "Manufacturing", "Industrial Products", "Qassim"),
    ("Rawabi Industrial Works", "Manufacturing", "Metal Fabrication", "Eastern Province"),
    ("Eastern Petrochemicals", "Chemicals", "Petrochemicals", "Eastern Province"),
    ("Jubail Specialty Chemicals", "Chemicals", "Specialty Chemicals", "Eastern Province"),
    ("Red Sea Logistics", "Transport and Logistics", "Freight", "Makkah"),
    ("Hijaz Freight Services", "Transport and Logistics", "Land Transport", "Madinah"),
    ("Gulf Horizon Trading", "Wholesale Trade", "General Trading", "Riyadh"),
    ("Al Waha Healthcare", "Healthcare", "Hospitals", "Riyadh"),
    ("Kingdom Technology Services", "Information Technology", "IT Services", "Riyadh"),
    ("Sadara Digital Systems", "Information Technology", "Software", "Eastern Province"),
    ("Asir Agri Processing", "Agriculture and Agri-processing", "Food Processing", "Asir"),
    ("Yanbu Power Partners", "Power and Utilities", "Generation", "Madinah"),
    ("Tabuk Renewables", "Power and Utilities", "Renewables", "Tabuk"),
    ("Al Rawdah Real Estate", "Real Estate", "Commercial Property", "Riyadh"),
    ("Jeddah Waterfront Development", "Real Estate", "Mixed Use", "Makkah"),
    ("Hail Retail Group", "Retail Trade", "Department Stores", "Hail"),
    ("Arabian Metals and Mining", "Metals and Mining", "Mining", "Northern Borders"),
    ("Al Fanar Hospitality", "Hospitality", "Hotels", "Makkah"),
    ("Saudi Fibre Networks", "Telecommunications", "Fixed Networks", "Riyadh"),
    ("Najm Mobile Infrastructure", "Telecommunications", "Towers", "Eastern Province"),
)

#: The book is not twenty names.
#:
#: It was, and the consequence showed in every answer: fourteen sectors over
#: twenty borrowers meant "EAD by sector" returned fourteen rows of which
#: most held a single obligor, so "sector" and "borrower" were the same
#: dimension wearing two names, and every quarter-on-quarter movement was one
#: company's news. A wholesale book a credit officer would recognise has
#: several names in each sector, a spread of sizes, and enough of them that a
#: sector aggregate says something about the sector.
_TAIL_PREFIXES: tuple[str, ...] = (
    "Al Faisaliah", "Najd", "Hijaz", "Tuwaiq", "Dhahran", "Qassim",
    "Al Khobar", "Unayzah", "Al Jouf", "Wadi Hanifah", "Rabigh", "Al Ahsa",
    "Sudair", "Al Kharj", "Buraydah", "Taif", "Najran", "Jazan",
    "Yanbu", "Khamis Mushait", "Al Bahah", "Arar", "Sakaka", "Turaif",
)

#: The middle of a corporate name. Empty is included deliberately -- plenty
#: of real companies are two words -- and the rest are the ordinary
#: descriptors a trading name carries.
_TAIL_MIDDLES: tuple[str, ...] = (
    "", "National", "Arabian", "United", "First", "Modern", "Advanced",
    "Prime", "Gulf", "Central", "Peninsula", "Integrated", "Premier",
    "Continental", "Eastern", "Western",
)

#: sector -> (sub-sectors, trade words, home regions)
_TAIL_SECTORS: dict[str, tuple[tuple[str, ...], tuple[str, ...],
                               tuple[str, ...]]] = {
    "Construction": (("Civil Infrastructure", "Building Contracting",
                      "Roads and Bridges", "Marine Works",
                      "Electromechanical", "Site Preparation"),
                     ("Contracting", "Engineering", "Civil Works"),
                     ("Riyadh", "Makkah", "Eastern Province", "Qassim")),
    "Real Estate": (("Commercial Property", "Mixed Use", "Residential",
                     "Industrial Parks", "Retail Property",
                     "Land Development"),
                    ("Properties", "Estates", "Development"),
                    ("Riyadh", "Makkah", "Madinah", "Eastern Province")),
    "Hospitality": (("Hotels", "Serviced Apartments", "Catering", "Resorts",
                     "Travel Services"),
                    ("Hospitality", "Hotels", "Resorts"),
                    ("Makkah", "Madinah", "Riyadh", "Tabuk")),
    "Retail Trade": (("Department Stores", "Grocery", "Specialty Retail",
                      "Electronics Retail", "Fashion Retail"),
                     ("Retail", "Stores", "Markets"),
                     ("Riyadh", "Hail", "Asir", "Eastern Province")),
    "Transport and Logistics": (("Freight", "Land Transport", "Warehousing",
                                 "Port Services", "Cold Chain", "Courier"),
                                ("Logistics", "Transport", "Shipping"),
                                ("Makkah", "Madinah", "Eastern Province",
                                 "Riyadh")),
    "Manufacturing": (("Industrial Products", "Metal Fabrication",
                       "Packaging", "Building Materials", "Plastics",
                       "Machinery"),
                      ("Industries", "Manufacturing", "Works"),
                      ("Qassim", "Eastern Province", "Riyadh", "Asir")),
    "Wholesale Trade": (("General Trading", "Food Distribution",
                         "Equipment Distribution", "Pharma Distribution",
                         "Building Supplies"),
                        ("Trading", "Distribution", "Supplies"),
                        ("Riyadh", "Eastern Province", "Makkah")),
    "Agriculture and Agri-processing": (("Food Processing", "Dairy",
                                         "Poultry", "Grain Handling",
                                         "Aquaculture", "Greenhouse"),
                                        ("Agri", "Farms", "Foods"),
                                        ("Asir", "Qassim", "Hail",
                                         "Northern Borders")),
    "Metals and Mining": (("Mining", "Smelting", "Aggregates",
                           "Steel Products", "Industrial Minerals"),
                          ("Minerals", "Metals", "Mining"),
                          ("Northern Borders", "Eastern Province", "Hail")),
    "Healthcare": (("Hospitals", "Clinics", "Pharmaceuticals", "Diagnostics",
                    "Medical Devices"),
                   ("Medical", "Healthcare", "Care"),
                   ("Riyadh", "Makkah", "Eastern Province")),
    "Chemicals": (("Petrochemicals", "Specialty Chemicals", "Fertilisers",
                   "Industrial Gases", "Polymers"),
                  ("Chemicals", "Petrochemicals", "Industries"),
                  ("Eastern Province", "Madinah", "Riyadh")),
    "Information Technology": (("IT Services", "Software", "Data Centres",
                                "Systems Integration", "Managed Services"),
                               ("Technologies", "Digital", "Systems"),
                               ("Riyadh", "Eastern Province", "Makkah")),
    "Power and Utilities": (("Generation", "Renewables", "Water",
                             "Transmission", "Waste to Energy"),
                            ("Energy", "Power", "Utilities"),
                            ("Madinah", "Tabuk", "Eastern Province",
                             "Najran")),
    "Telecommunications": (("Fixed Networks", "Towers", "Data Services",
                            "Satellite", "Managed Connectivity"),
                           ("Telecom", "Communications", "Networks"),
                           ("Riyadh", "Eastern Province", "Makkah")),
}

#: Extra names per sector, at wholesale scale. The weights keep the sectors
#: carrying an authored story populous enough that a sector finding is a
#: finding about a sector and not about one company.
#: Raised by about half. Six sub-sectors inside a sector, three relationship
#: tiers, eight products and twenty quarters is a lot of cells, and a
#: sub-sector finding that rests on forty names is an anecdote with a
#: p-value. `Building Contracting` carries a planted story and needs a
#: population to carry it in.
_TAIL_COUNTS: dict[str, int] = {
    "Construction": 620, "Real Estate": 540, "Hospitality": 380,
    "Retail Trade": 380, "Transport and Logistics": 400,
    "Manufacturing": 480, "Wholesale Trade": 340,
    "Agriculture and Agri-processing": 320, "Metals and Mining": 280,
    "Healthcare": 340, "Chemicals": 380, "Information Technology": 350,
    "Power and Utilities": 340, "Telecommunications": 240,
}


def _combinations(words: tuple[str, ...]) -> list[str]:
    """Every name this sector's tables can spell, in a fixed order.

    Enumerated rather than sampled. Sampling needed a collision loop, and the
    bump depended on how many earlier names happened to collide -- so a name
    changed when a count changed. Enumeration gives the same name for the
    same (sector, index) whatever else the book does.
    """
    out: list[str] = []
    for word in words:
        for middle in _TAIL_MIDDLES:
            for prefix in _TAIL_PREFIXES:
                out.append(" ".join(p for p in (prefix, middle, word) if p))
    return out


def _tail() -> tuple[tuple[str, str, str, str], ...]:
    """The generated part of the book. A pure function of the tables above."""
    taken = {name for name, *_ in BORROWERS}
    out: list[tuple[str, str, str, str]] = []
    for sector in sorted(_TAIL_COUNTS):
        subs, words, regions = _TAIL_SECTORS[sector]
        names = _combinations(words)
        cursor = 0
        for index in range(_TAIL_COUNTS[sector]):
            while True:
                candidate = names[cursor % len(names)]
                if cursor >= len(names):
                    candidate = f"{candidate} {cursor // len(names) + 1}"
                cursor += 1
                if candidate not in taken:
                    break
            taken.add(candidate)
            seed = _stable(f"{sector}|{index}", 10_000)
            out.append((candidate, sector,
                        subs[(seed // 3 + index) % len(subs)],
                        regions[(seed // 11 + index) % len(regions)]))
    return tuple(out)


def obligors() -> tuple[tuple[str, str, str, str], ...]:
    """Every name in the book: the authored anchors and the generated tail."""
    return BORROWERS + _tail()


GROUPS: dict[str, str] = {
    "Construction": "Tuwaiq Holding",
    "Chemicals": "Eastern Industrial Group",
    "Transport and Logistics": "Red Sea Holding",
    "Information Technology": "Kingdom Digital Group",
    "Power and Utilities": "Arabian Energy Group",
    "Real Estate": "Al Rawdah Group",
    "Telecommunications": "Peninsula Communications Group",
}

# ---- the products ------------------------------------------------------

#: Product types, as the book's own identifiers.
#:
#: `snake_case`, because these are governed values of a governed field and a
#: governed value that is sometimes "Project Finance" and sometimes "project
#: finance" is two values. The reader never has to type them this way --
#: `values.resolve` reads "project finance", "Project-Finance" and "prject
#: finance" as this one identifier -- and the export, the chart label and the
#: table header all show the reader's form.
PRODUCT_TYPES: tuple[str, ...] = (
    "term_loan", "working_capital", "revolving_credit", "trade_finance",
    "project_finance", "overdraft", "asset_finance",
    # Weight 0. Supply chain finance is not drawn from this distribution --
    # it is booked explicitly from `NEW_PRODUCT_FROM_QUARTER`, so no
    # facility of it exists before the quarter the bank started writing it.
    "supply_chain_finance")

#: How often each product appears, out of 100. No mainstream product may be
#: three names: a reader asking "and within project finance?" must get a
#: population, not an anecdote.
PRODUCT_WEIGHTS: tuple[int, ...] = (24, 19, 15, 14, 12, 9, 7, 0)

#: Which products put cash out when drawn. A guarantee or a letter of credit
#: is an exposure without a disbursement, and a book that cannot tell the
#: difference cannot answer "how much of this is funded?".
FACILITY_CLASS: dict[str, str] = {
    "term_loan": "funded", "working_capital": "funded",
    "revolving_credit": "funded", "overdraft": "funded",
    "asset_finance": "funded", "project_finance": "funded",
    "trade_finance": "non_funded", "supply_chain_finance": "non_funded",
}

#: Sector concentration BY PRODUCT. Project finance sits in infrastructure,
#: power and real estate; trade finance sits in trading and manufacturing.
#: A book where every product is spread evenly over every sector has no
#: concentration to find.
PRODUCT_SECTOR_BIAS: dict[str, dict[str, float]] = {
    "project_finance": {"Power and Utilities": 3.6, "Construction": 3.0,
                        "Real Estate": 2.6, "Transport and Logistics": 2.0,
                        "Telecommunications": 1.8, "Metals and Mining": 1.5},
    "trade_finance": {"Wholesale Trade": 3.4, "Retail Trade": 2.2,
                      "Manufacturing": 2.0, "Chemicals": 1.8,
                      "Agriculture and Agri-processing": 1.6},
    "asset_finance": {"Transport and Logistics": 3.0, "Construction": 2.2,
                      "Manufacturing": 1.8, "Healthcare": 1.4},
    "working_capital": {"Manufacturing": 1.6, "Wholesale Trade": 1.6,
                        "Retail Trade": 1.4,
                        "Agriculture and Agri-processing": 1.4},
    "revolving_credit": {"Information Technology": 1.6, "Healthcare": 1.4,
                         "Telecommunications": 1.4},
    "overdraft": {"Retail Trade": 1.6, "Hospitality": 1.5,
                  "Wholesale Trade": 1.4},
    "term_loan": {},
}

COLLATERAL_TYPES = ("Real Estate", "Plant and Equipment", "Receivables",
                    "Cash Deposit", "Corporate Guarantee")
COVENANT_TYPES = ("Leverage", "DSCR", "Interest Cover", "Current Ratio",
                  "Minimum EBITDA")
TIERS = ("Strategic", "Core", "Transactional")

# ---- the rating scale --------------------------------------------------

#: Nineteen grades, best to worst, then the default state.
RATINGS: tuple[str, ...] = (
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C")
DEFAULT_GRADE = "D"

#: The through-the-cycle PD of each grade, as a master scale.
#:
#: Calibrated rather than convenient. Pricing the whole book off
#: `0.004 * exp(grade / 3.4)` put the AVERAGE Stage 1 twelve-month PD at nine
#: and a half per cent -- a CCC number applied to a performing
#: investment-grade book. The curve below is the shape a rating master scale
#: actually has: roughly a doubling every grade and a half, from one basis
#: point at AAA to the high teens at C.
PD_ANCHOR = 0.00012
PD_DECAY = 2.55

#: How hard the sector stories push, and how hard each name's own story does.
#: Both are tuned against the book they produce rather than chosen: at 1.0
#: the stressed sectors finished with every obligor on the watch list and
#: forty-five per cent of the book in Stage 2, which is a portfolio in
#: resolution rather than one under pressure.
STRESS_SCALE = 0.62
COHORT_SCALE = 0.55

#: The grade at which the book watches an exposure as a backstop, and the
#: grade at which it is credit-impaired. The primary trigger is RELATIVE -- a
#: downgrade since origination -- because that is what a significant increase
#: in credit risk means.
WATCH_GRADE = RATINGS.index("CCC")
IMPAIRED_GRADE = RATINGS.index("CC")

#: Notches of downgrade since origination that trigger a significant
#: increase in credit risk on their own. Two is the common policy.
SICR_NOTCHES = 2

#: How the book was written: the grade distribution at origination. A real
#: book is written across the scale, with most of it around BBB/BB and a
#: short tail either side.
ORIGINATION_GRADES: tuple[int, ...] = tuple(range(2, 18))
ORIGINATION_WEIGHTS: tuple[int, ...] = (
    1, 2, 3, 5, 8, 11, 13, 13, 12, 10, 8, 6, 4, 3, 2, 1)

#: Debt service coverage below which a borrower starts missing payments, and
#: above which arrears cure. Between the two the arrears freeze: a borrower
#: that has stopped deteriorating has not yet repaid.
ARREARS_DSCR = 1.00
CURE_DSCR = 1.20

#: Days past due at which a facility is charged off, and how much of the
#: exposure goes when it is.
CHARGE_OFF_DPD = 360
WRITE_OFF_FRACTION = 0.65

#: The authored sector stories. A negative figure is an improving sector,
#: and the book has both.
SECTOR_STRESS: dict[str, float] = {
    "Construction": 1.00,
    "Real Estate": 0.85,
    "Hospitality": 0.55,
    "Retail Trade": 0.40,
    "Transport and Logistics": 0.20,
    "Manufacturing": 0.15,
    "Wholesale Trade": 0.10,
    "Agriculture and Agri-processing": 0.05,
    "Metals and Mining": 0.00,
    "Telecommunications": -0.05,
    "Healthcare": -0.10,
    "Chemicals": -0.20,
    "Information Technology": -0.30,
    "Power and Utilities": -0.35,
}

# ---- the planted patterns ------------------------------------------------
#
# Sector stress above shapes the book. What follows is deliberate structure
# inside it: findings a corporate credit committee would be expected to
# reach, each by a different cut, and each recovered by a direct SQL oracle
# in `tests/cockpit_v4/domain_oracles.py`.

#: C7. A SUB-SECTOR THAT GOES BEFORE ITS RATINGS DO.
#:
#: Building Contracting deteriorates from 2025Q4 in the two things a credit
#: officer can see early -- debt service and covenant headroom -- while the
#: internal grade, which is sticky and reviewed on a cycle, follows two
#: quarters later. Construction as a whole barely moves, because the other
#: five sub-sectors do not, so the finding is only reachable at sub-sector
#: grain.
#:
#: This is the early-warning question the product exists to answer, and a
#: book where rating, DSCR and covenant all turn in the same quarter cannot
#: pose it.
EARLY_SUB_SECTOR = "Building Contracting"
EARLY_FROM_QUARTER = "2025Q4"
EARLY_DSCR_DROP = 0.78
EARLY_HEADROOM_DROP = 18.0

#: How many quarters the internal grade lags the cash-flow signal.
EARLY_RATING_LAG = 2

#: C8. A CONCENTRATION THAT IS ONLY VISIBLE AFTER GROUPING.
#:
#: No single borrower in Tuwaiq Holding is near the single-obligor limit.
#: Their aggregate crosses it in 2026Q1, and an exposure report by BORROWER
#: shows nothing at all. Grouping is the whole finding.
#: A SMALL, NAMED group -- seven obligors across four sectors, related by
#: ownership and not by what they do. `GROUPS` above maps a whole SECTOR to
#: a holding name, which makes those "groups" sector-sized and a
#: concentration inside one indistinguishable from the sector moving. This
#: one is the real shape of the exposure a single-obligor limit is written
#: against: a handful of connected names, each modest, adding up.
CONCENTRATION_GROUP = "Al Faisaliah Industrial Group"
CONCENTRATION_MEMBERS: tuple[str, ...] = (
    "Al Noor Infrastructure", "Tuwaiq Construction", "Najd Manufacturing",
    "Rawabi Industrial Works", "Gulf Horizon Trading",
    "Al Rawdah Real Estate", "Jeddah Waterfront Development")
CONCENTRATION_FROM_QUARTER = "2026Q1"
CONCENTRATION_RAMP = 3.4

#: C10. A PRODUCT TYPE THAT DID NOT EXIST A YEAR AGO.
#:
#: Supply chain finance, written from 2025Q4 and growing fast. Same shape as
#: the retail BNPL book and the same question: when losses rise, is it the
#: book or is it the thing the bank started doing last year?
NEW_PRODUCT_TYPE = "supply_chain_finance"
NEW_PRODUCT_FROM_QUARTER = "2025Q4"
NEW_PRODUCT_STRESS = 0.95

#: Collateral values follow their own cycle. Real estate softens through the
#: window while industrial security firms up -- so "coverage fell" and "ECL
#: rose" are separable findings rather than the same finding twice.
COLLATERAL_DRIFT: dict[str, float] = {
    "Real Estate": -0.18, "Plant and Equipment": 0.06,
    "Receivables": -0.04, "Cash Deposit": 0.0,
    "Corporate Guarantee": -0.02,
}

# ---- the cohorts -------------------------------------------------------

#: What happens to a name over the twenty quarters, beyond what its sector
#: does.
#:
#: A book whose only story is "these three sectors got worse" answers one
#: question. A credit reader asks others: which names improved while their
#: sector deteriorated, which were already weak and stayed weak, which went
#: late and then recovered, which defaulted. Each name is assigned one of
#: these, deterministically from its own name.
COHORTS: tuple[str, ...] = ("stable", "improver", "early_deterioration",
                            "severe_deterioration", "cure", "default")
COHORT_WEIGHTS: tuple[int, ...] = (60, 15, 13, 6, 4, 2)


def cohort_of(name: str) -> str:
    """Which story this name is in. A pure function of the name."""
    # AN INTEGER MODULUS, and it has to stay one. `_stable` takes the
    # remainder against this, so a float here would reassign every name in
    # the book to a different story and fail nothing on the way out.
    draw = _stable(f"cohort|{name}", whole_total(COHORT_WEIGHTS))
    running = 0
    for cohort, weight in zip(COHORTS, COHORT_WEIGHTS):
        running += weight
        if draw < running:
            return cohort
    return COHORTS[0]


def cohort_shape(cohort: str, ramp: float) -> float:
    """How much stress this cohort's own story adds at this point.

    Zero at the start of the window for every cohort: a book where a name is
    already mid-story in the first quarter has no "when did this begin?".
    """
    if cohort == "improver":
        return -0.70 * ramp
    if cohort == "early_deterioration":
        # Nothing for the first half, then it turns. This is the cohort the
        # attention feed is FOR: a name that was fine until recently.
        return 0.70 * _clamp((ramp - 0.45) / 0.55, 0.0, 1.0)
    if cohort == "severe_deterioration":
        return 0.65 * ramp
    if cohort == "default":
        return 1.20 * ramp
    if cohort == "cure":
        # Up to a peak around two thirds of the way through, then back. The
        # arrears, the stage and the rating follow it down, which is what a
        # cure is.
        return 2.00 * math.sin(math.pi * _clamp(ramp * 1.45, 0.0, 1.0))
    return 0.0


def cohort_drift(cohort: str, ramp: float) -> float:
    """The cohort's story, with the book's average story taken out.

    Cohorts REDISTRIBUTE risk; they do not add it. Added raw, two fifths of
    the names were in a cohort that only ever pushes upward and the
    portfolio's ECL coverage doubled without any sector having a new story.
    The mean is subtracted so the aggregate stays the book it was and the
    DISPERSION is what changed.
    """
    return cohort_shape(cohort, ramp) - _cohort_mean(ramp)


def _cohort_mean(ramp: float) -> float:
    total = whole_total(COHORT_WEIGHTS)
    return exact_total(cohort_shape(c, ramp) * w
                       for c, w in zip(COHORTS, COHORT_WEIGHTS)) / total


# ---- helpers -----------------------------------------------------------

def grade_pd(grade: int) -> float:
    """Twelve-month PD for a rating grade, from the master scale."""
    return _clamp(PD_ANCHOR * math.exp(grade / PD_DECAY), 0.0001, 0.40)


def _jitter(key: str, period: str, spread: float) -> float:
    """A per-(entity, quarter) wobble that is stable across builds.

    Hashed rather than drawn, so it is a pure function of who and when: two
    builds fingerprint identically and adding a borrower does not reshuffle
    everyone else's history.
    """
    digest = hashlib.sha256(f"corp|{key}|{period}".encode("utf-8")).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return (unit - 0.5) * 2 * spread


def _stable(text: str, modulus: int) -> int:
    """A repeatable integer from a string.

    NOT `hash()`. Python randomises string hashing per process, so a build on
    Monday and a build on Tuesday produced different group ids and different
    waiver quarters from the same inputs -- and therefore different parquet
    bytes and a different fingerprint. A fingerprint that changes when
    nothing changed is a fingerprint nobody can check anything against.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _ramp(index: int, total: int) -> float:
    """0 at the start of the window, 1 at the end, easing in the middle."""
    if total <= 1:
        return 1.0
    x = index / (total - 1)
    # Eases in and keeps going. Smoothstep was the first shape here and its
    # slope falls to zero at BOTH ends, so the last few quarters of the
    # window barely moved -- which made every quarter-on-quarter attention
    # card tiny and left the feed with two findings on a book that had
    # plainly changed.
    return x ** 1.55


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _before_window(first: str, back: int) -> str:
    """`back` quarters before the first quarter of the window."""
    year, quarter = int(first[:4]), int(first[-1])
    total = year * 4 + (quarter - 1) - (back + 1)
    return f"{total // 4:04d}Q{total % 4 + 1}"


def _pick(options: tuple[str, ...], weights: tuple[int, ...],
          draw: int) -> str:
    running = 0
    for option, weight in zip(options, weights):
        running += weight
        if draw < running:
            return option
    return options[-1]


def _group_of(name: str, sector: str) -> str:
    """The ownership group a borrower belongs to.

    `CONCENTRATION_MEMBERS` are named first, because they are related by
    who owns them rather than by what they do -- which is exactly why a
    limit written against a borrower does not catch them.
    """
    if name in CONCENTRATION_MEMBERS:
        return CONCENTRATION_GROUP
    return GROUPS.get(sector, f"{name} Holding")


def _product_for(sector: str, key: str) -> str:
    """Which product this facility is, biased by the obligor's sector.

    A product with weight ZERO is not in the draw at all. `max(1, ...)`
    below used to be applied to every product, which meant a zero weight
    still produced a weight of one and a product the bank had not started
    writing yet turned up in 2021. The floor is for a product that is IN
    the distribution and heavily biased away from this sector.
    """
    options = tuple(p for p, w in zip(PRODUCT_TYPES, PRODUCT_WEIGHTS) if w > 0)
    weights = tuple(
        max(1, int(round(base * PRODUCT_SECTOR_BIAS.get(product, {})
                         .get(sector, 1.0))))
        for product, base in zip(PRODUCT_TYPES, PRODUCT_WEIGHTS) if base > 0)
    # Integer weights, and an integer modulus for the same reason as
    # `cohort_of` above: a float here would repick every facility's product.
    return _pick(options, weights, _stable(key, whole_total(weights)))


def _migration(moved: int) -> str:
    if moved > 0:
        return "UPGRADE"
    if moved < 0:
        return "DOWNGRADE"
    return "STABLE"


def _driver(leverage: float, dscr: float, margin: float,
            quick: float, moved: int) -> str:
    """Which variable moved the rating most. Named, not inferred by a reader
    from four columns that all moved a little."""
    if moved == 0:
        return "None"
    scores = {
        "Leverage": leverage / 6.0,
        "Debt service": max(0.0, 2.4 - dscr) / 2.4,
        "Profitability": max(0.0, 0.22 - margin) / 0.22,
        "Liquidity": max(0.0, 1.4 - quick) / 1.4,
    }
    top = max(scores, key=lambda k: scores[k])
    return top if scores[top] > 0.15 else "Qualitative"


def build(release_id: str = "",
          tenant_id: str = lake.DEFAULT_TENANT) -> lake.Build:
    import pandas as pd

    release_id = release_id or dom.DEFAULT_RELEASES[dom.CORPORATE]
    if release_id in FROZEN_RELEASES:
        raise FrozenRelease(
            f"{release_id} is published and immutable, and this generator no "
            f"longer produces it: that release is a MONTHLY corporate book "
            f"and this one is quarterly. The current Corporate release is "
            f"{dom.DEFAULT_RELEASES[dom.CORPORATE]}.")

    quarters = quarter_range()
    rng = random.Random(SEED)

    borrowers: list[dict[str, Any]] = []
    facilities: list[dict[str, Any]] = []
    book = obligors()
    for index, (name, sector, sub_sector, region) in enumerate(book):
        borrower_id = f"CB{index + 1:05d}"
        borrowers.append({
            "borrower_id": borrower_id, "borrower_name": name,
            "group_id": f"CG{_stable(_group_of(name, sector), 9000) + 1000}",
            "group_name": _group_of(name, sector),
            "sector": sector, "sub_sector": sub_sector, "region": region,
            "relationship_tier": TIERS[index % len(TIERS)],
            "base_rating": rng.choices(ORIGINATION_GRADES,
                                       weights=ORIGINATION_WEIGHTS, k=1)[0],
            "base_revenue": round(rng.uniform(900, 7200), 1),
            "base_margin": rng.uniform(0.09, 0.28),
            "base_leverage": rng.uniform(1.4, 4.2),
            "quality": rng.uniform(0.0, 1.0),
            "cohort": cohort_of(name),
        })
        # Two to five facilities, so the book is a book of exposures rather
        # than a book of obligors with one line each.
        for slot in range(2 + index % 4):
            facility_id = f"CF{index + 1:05d}{slot + 1}"
            product = _product_for(sector, f"{facility_id}|product")
            facilities.append({
                "facility_id": facility_id,
                "borrower_id": borrower_id,
                "product_type": product,
                "facility_class": FACILITY_CLASS[product],
                "base_limit": round(rng.uniform(140, 1450), 1),
                "base_util": rng.uniform(0.42, 0.93),
                "ccf": rng.uniform(0.2, 0.5),
                "lgd": rng.uniform(0.28, 0.55),
                "collateral_type": COLLATERAL_TYPES[(index + slot)
                                                    % len(COLLATERAL_TYPES)],
                "base_collateral": rng.uniform(0.55, 1.35),
                "covenant_type": COVENANT_TYPES[(index + slot * 2)
                                                % len(COVENANT_TYPES)],
                "wobble": rng.uniform(-0.03, 0.03),
                # BEFORE the window. Every facility in this book is
                # present for all twenty quarters, so originating one
                # inside the window would publish rows for quarters the
                # facility did not yet exist in -- which the release gates
                # catch, correctly, as a facility reporting before it was
                # written.
                "origination_quarter": _before_window(
                    quarters[0], _stable(f"{facility_id}|origination", 16)),
            })

    # C10. THE PRODUCT THE BANK STARTED DOING LAST YEAR.
    #
    # Supply chain finance, written from 2025Q4 onward. Every other facility
    # in this book predates the window, which is why the loop below has to
    # learn to skip: a facility originated INSIDE the window reports no row
    # for the quarters before it existed, and a book where every product has
    # been there for five years cannot be asked what is new.
    new_from = quarters.index(NEW_PRODUCT_FROM_QUARTER)
    for offset in range(int(len(book) * 0.55)):
        index = (offset * 13 + 7) % len(book)
        borrower_id = f"CB{index + 1:05d}"
        facility_id = f"CFN{offset + 1:05d}"
        facilities.append({
            "facility_id": facility_id,
            "borrower_id": borrower_id,
            "product_type": NEW_PRODUCT_TYPE,
            "facility_class": FACILITY_CLASS[NEW_PRODUCT_TYPE],
            "base_limit": round(rng.uniform(60, 420), 1),
            "base_util": rng.uniform(0.55, 0.98),
            "ccf": rng.uniform(0.3, 0.6),
            "lgd": rng.uniform(0.38, 0.68),
            "collateral_type": "Receivables",
            "base_collateral": rng.uniform(0.25, 0.70),
            "covenant_type": "Current Ratio",
            "wobble": rng.uniform(-0.03, 0.03),
            "origination_quarter": quarters[
                min(new_from + _stable(f"{facility_id}|open", 4),
                    len(quarters) - 1)],
        })

    by_id = {b["borrower_id"]: b for b in borrowers}
    gov = {"tenant_id": tenant_id, "dataset_release_id": release_id,
           "domain_id": dom.CORPORATE, "reporting_currency": lake.CURRENCY}

    borrower_rows: list[dict[str, Any]] = []
    facility_rows: list[dict[str, Any]] = []
    collateral_rows: list[dict[str, Any]] = []
    covenant_rows: list[dict[str, Any]] = []
    previous_rating: dict[str, int] = {}
    stage_since: dict[str, tuple[int, int]] = {}
    watch_since: dict[str, int] = {}
    breach_streak: dict[str, int] = {}
    #: Each borrower's financial position in the first quarter of the window.
    origination: dict[str, dict[str, float]] = {}
    #: Days past due carried forward per facility, driven by the borrower's
    #: own debt service coverage rather than by a threshold on a stress
    #: index. "Why is this facility in arrears?" is then a question the book
    #: can answer from a column it publishes.
    arrears: dict[str, int] = {}

    early_index = quarters.index(EARLY_FROM_QUARTER)
    concentration_index = quarters.index(CONCENTRATION_FROM_QUARTER)

    for q_index, quarter in enumerate(quarters):
        ramp = _ramp(q_index, len(quarters))
        season = math.sin((q_index % 4) / 4 * 2 * math.pi)
        #: What the borrower loop worked out, for the facility loop to read.
        state: dict[str, dict[str, Any]] = {}

        for borrower in borrowers:
            bid = borrower["borrower_id"]
            stress = (SECTOR_STRESS.get(borrower["sector"], 0.0) * ramp
                      * STRESS_SCALE)
            # A borrower's own quality softens or sharpens its sector's story.
            personal = stress * (0.55 + 0.9 * (1 - borrower["quality"]))
            # And its own story runs on top of its sector's. An improver in
            # Construction improves; a name in a quiet sector can still be
            # the one that defaults.
            personal += cohort_drift(borrower["cohort"], ramp) * COHORT_SCALE

            revenue = borrower["base_revenue"] * (
                1 + 0.045 * season - 0.16 * personal + 0.035 * q_index / 4)
            margin = _clamp(borrower["base_margin"] - 0.05 * personal,
                            0.01, 0.42)
            ebitda = revenue * margin
            ebit = ebitda * _clamp(0.72 - 0.06 * personal, 0.35, 0.92)
            debt = borrower["base_revenue"] * borrower["base_leverage"] * 0.38
            debt *= 1 + 0.10 * personal
            cash = max(8.0, ebitda * (0.42 - 0.18 * personal))
            net_debt = max(0.0, debt - cash)
            leverage = net_debt / max(ebitda, 1.0)
            dscr = _clamp(2.35 - 0.95 * personal - 0.05 * leverage, 0.55, 4.2)
            interest_cover = _clamp(5.4 - 2.2 * personal - 0.22 * leverage,
                                    0.7, 12.0)

            # C7. WHAT A CREDIT OFFICER SEES BEFORE THE RATING MOVES.
            #
            # Applied to debt service and interest cover only, and NOT to
            # `personal`, which is what drives the rating. So Building
            # Contracting's DSCR falls and its covenants tighten from
            # 2025Q4 while its grades stand still; the grades follow two
            # quarters later, when `personal` picks the same cohort up
            # below. That lag is the finding, and a book where all three
            # turn in the same quarter cannot be used to test for it.
            early = (borrower["sub_sector"] == EARLY_SUB_SECTOR
                     and q_index >= early_index)
            if early:
                lead = min(1.0, (q_index - early_index + 1) / 3.0)
                dscr = _clamp(dscr - EARLY_DSCR_DROP * lead, 0.55, 4.2)
                interest_cover = _clamp(
                    interest_cover - 1.7 * lead, 0.7, 12.0)
                if q_index >= early_index + EARLY_RATING_LAG:
                    personal += 0.55 * min(
                        1.0, (q_index - early_index
                              - EARLY_RATING_LAG + 1) / 3.0)
            current_ratio = _clamp(1.65 - 0.5 * personal, 0.55, 3.1)
            quick_ratio = _clamp(current_ratio - 0.35 - 0.1 * personal,
                                 0.25, 2.6)
            operating_cash = ebitda * _clamp(0.78 - 0.22 * personal,
                                             0.20, 0.98)

            # Unevenly, and stickily.
            #
            # Unevenly because a sector whose obligors all downgrade together
            # is a sector where "which names moved?" has no answer: every
            # borrower carries a permanent idiosyncratic offset, so the weak
            # names go first, some hold on, and a healthy sector still has a
            # name or two on the watch list.
            #
            # Stickily because a rating is a decision, not a reading. Letting
            # it follow a quarterly wobble put names into Stage 2 and out
            # again quarter after quarter, which is not a credit cycle. A
            # downgrade lands at once; a recovery takes a notch a quarter.
            idiosyncratic = _jitter(bid, "origination", 1.7)
            notches = max(0, int(round(4.2 * personal + idiosyncratic
                                       + _jitter(bid, quarter, 0.3))))
            target = min(len(RATINGS) - 1, borrower["base_rating"] + notches)
            prior = previous_rating.get(bid, target)
            grade = max(target, prior - 1)
            previous_rating[bid] = grade
            moved = prior - grade  # negative is a downgrade
            from_origination = borrower["base_rating"] - grade

            watching = int(grade >= WATCH_GRADE
                           or (grade - borrower["base_rating"])
                           >= SICR_NOTCHES
                           or dscr < CURE_DSCR)
            watch_since[bid] = (watch_since.get(bid, 0) + 1) if watching else 0

            state[bid] = {
                "personal": personal, "grade": grade, "dscr": dscr,
                "leverage": leverage, "interest_cover": interest_cover,
                "current_ratio": current_ratio, "ebitda": ebitda,
                "notches_from_origination": grade - borrower["base_rating"],
            }
            # The book as it was WRITTEN. Covenant thresholds are set against
            # it, because a covenant is a promise made at origination about
            # how far a borrower may drift from where it started.
            origination.setdefault(bid, dict(state[bid]))

            borrower_rows.append({
                **gov,
                "borrower_id": bid, "borrower_name": borrower["borrower_name"],
                "group_id": borrower["group_id"],
                "group_name": borrower["group_name"],
                "reporting_quarter": quarter, "sector": borrower["sector"],
                "sub_sector": borrower["sub_sector"],
                "region": borrower["region"],
                "relationship_tier": borrower["relationship_tier"],
                "rating_current": RATINGS[grade],
                "rating_previous": RATINGS[prior],
                "rating_at_origination": RATINGS[borrower["base_rating"]],
                "rating_notches_moved": moved,
                "rating_notches_from_origination": from_origination,
                "rating_migration": _migration(moved),
                "rating_outlook": ("Negative" if personal > 0.35
                                   else "Positive" if personal < -0.12
                                   else "Stable"),
                "rating_driver": _driver(leverage, dscr, margin, quick_ratio,
                                         moved),
                "pd_ttc_12m": round(grade_pd(grade), 6),
                "revenue_sar_mn": round(revenue, 1),
                "ebitda_sar_mn": round(ebitda, 1),
                "ebit_sar_mn": round(ebit, 1),
                "operating_cash_flow_sar_mn": round(operating_cash, 1),
                "total_debt_sar_mn": round(debt, 1),
                "cash_sar_mn": round(cash, 1),
                "net_debt_sar_mn": round(net_debt, 1),
                "leverage_x": round(leverage, 3),
                "dscr_x": round(dscr, 3),
                "interest_cover_x": round(interest_cover, 3),
                "current_ratio_x": round(current_ratio, 3),
                "quick_ratio_x": round(quick_ratio, 3),
                "ebitda_margin_pct": round(margin * 100, 3),
                "return_on_assets_pct": round(
                    _clamp(ebit / max(debt + cash, 1.0) * 100, -8.0, 34.0),
                    3),
                "cash_conversion_pct": round(
                    operating_cash / max(ebitda, 1.0) * 100, 3),
                "qualitative_score": round(
                    _clamp(78 - 34 * personal + 4 * season, 12, 97), 2),
                "watchlist_flag": watching,
                "watchlist_reason": ("" if not watching else
                                     "Rating downgraded since origination"
                                     if (grade - borrower["base_rating"])
                                     >= SICR_NOTCHES else
                                     "Debt service below policy"
                                     if dscr < CURE_DSCR else
                                     "Rating below watch grade"),
                "restructured_flag": int(
                    watch_since.get(bid, 0) >= 4 and dscr < ARREARS_DSCR),
                "quarters_on_watchlist": watch_since.get(bid, 0),
            })

        for facility in facilities:
            if quarters_between(facility["origination_quarter"], quarter) < 0:
                continue  # not written yet: no row rather than a zero row
            borrower = by_id[facility["borrower_id"]]
            bid = borrower["borrower_id"]
            here = state[bid]
            personal = here["personal"]
            grade = here["grade"]
            fid = facility["facility_id"]

            limit = facility["base_limit"] * (1 + 0.012 * q_index)
            # C8. A LIMIT THAT IS ONLY BREACHED IN AGGREGATE.
            #
            # Every name in the group stays comfortably inside the
            # single-obligor limit; what crosses it is their total. An
            # exposure report ordered by borrower shows nothing, and the
            # finding only exists once the rows are grouped -- which is the
            # whole point of carrying `group_name` beside `borrower_name`.
            if (borrower["group_name"] == CONCENTRATION_GROUP
                    and q_index >= concentration_index):
                limit *= 1 + (CONCENTRATION_RAMP - 1) * min(
                    1.0, (q_index - concentration_index + 1) / 2.0)
            util = _clamp(facility["base_util"] + 0.12 * personal
                          + 0.02 * season + facility["wobble"], 0.05, 1.0)
            drawn = limit * util
            undrawn = max(0.0, limit - drawn)
            ead = drawn + undrawn * facility["ccf"]

            # Arrears follow debt service, not a threshold on a stress index.
            # A borrower that cannot cover its debt service starts missing
            # payments and rolls a quarter deeper each quarter; one that
            # recovers cures; one in between is frozen where it is.
            prior_dpd = arrears.get(fid, 0)
            dscr = here["dscr"]
            # C10. A NEW PRODUCT UNDERWRITTEN WITHOUT PERFORMANCE HISTORY.
            #
            # Supply chain finance is written against receivables, on
            # limits set from a programme rather than from the obligor, and
            # its cut-offs have never been tested through a cycle. So the
            # SAME borrower defaults on it before it defaults on its term
            # loan: this facility's debt-service test is read against a
            # tighter threshold than the book's.
            #
            # Applied per FACILITY rather than to the obligor, which is the
            # whole finding. "Which names are deteriorating?" returns the
            # usual suspects; "which PRODUCT is deteriorating?" returns the
            # one the bank started writing three quarters ago.
            if facility["product_type"] == NEW_PRODUCT_TYPE:
                dscr -= NEW_PRODUCT_STRESS * min(
                    1.0, quarters_between(facility["origination_quarter"],
                                          quarter) / 2.0 + 0.5)
            if dscr < ARREARS_DSCR:
                dpd = min(CHARGE_OFF_DPD, prior_dpd + 90 if prior_dpd else 30)
            elif dscr < CURE_DSCR and prior_dpd:
                dpd = prior_dpd
            else:
                dpd = 0
            cured = int(prior_dpd >= 90 and dpd == 0)
            arrears[fid] = dpd

            # Staging from what the book OBSERVES: arrears, the grade itself,
            # and the downgrade since origination.
            if dpd >= 90 or grade >= IMPAIRED_GRADE:
                stage = 3
            elif (dpd >= 30 or grade >= WATCH_GRADE
                  or here["notches_from_origination"] >= SICR_NOTCHES):
                stage = 2
            else:
                stage = 1
            sicr = 1 if stage >= 2 else 0
            default_flag = 1 if stage == 3 else 0

            lgd = _clamp(facility["lgd"] + 0.09 * personal, 0.12, 0.85)
            if stage == 3:
                # A defaulted exposure has already defaulted. Publishing a
                # point-in-time PD of eight per cent against it is not a
                # calibration choice -- it is a contradiction of the flag in
                # the next column.
                pd_pit = pd_life = 1.0
                ecl_12m = ecl_life = ead * lgd
            else:
                pd_pit = _clamp(grade_pd(grade) * (1 + 0.35 * personal),
                                0.0001, 0.45)
                pd_life = _clamp(pd_pit * (2.4 + 1.6 * personal), pd_pit,
                                 0.97)
                ecl_12m = ead * pd_pit * lgd
                ecl_life = ead * pd_life * lgd
            ecl = ecl_12m if stage == 1 else ecl_life

            written_off = (ead * WRITE_OFF_FRACTION
                           if dpd >= CHARGE_OFF_DPD else 0.0)
            recovered = (written_off * 0.22 if written_off else
                         ead * lgd * 0.08 if cured else 0.0)

            seen, since = stage_since.get(fid, (stage, 0))
            since = since + 1 if seen == stage else 1
            stage_since[fid] = (stage, since)

            facility_rows.append({
                **gov,
                "facility_id": fid, "borrower_id": bid,
                "borrower_name": borrower["borrower_name"],
                "reporting_quarter": quarter,
                "product_type": facility["product_type"],
                "facility_class": facility["facility_class"],
                "sector": borrower["sector"],
                "sub_sector": borrower["sub_sector"],
                "region": borrower["region"],
                "relationship_tier": borrower["relationship_tier"],
                "limit_sar_mn": round(limit, 2),
                "drawn_sar_mn": round(drawn, 2),
                "undrawn_sar_mn": round(undrawn, 2),
                "utilisation_pct": round(util * 100, 3),
                "ead_sar_mn": round(ead, 2),
                "stage": stage, "sicr_flag": sicr,
                "default_flag": default_flag, "dpd_days": dpd,
                "pd_pit_12m": round(pd_pit, 6),
                "pd_lifetime": round(pd_life, 6),
                "lgd_pct": round(lgd * 100, 3),
                "ecl_12m_sar_mn": round(ecl_12m, 4),
                "ecl_lifetime_sar_mn": round(ecl_life, 4),
                "ecl_sar_mn": round(ecl, 4),
                "ecl_coverage_pct": round(ecl / max(ead, 0.01) * 100, 4),
                "write_off_sar_mn": round(written_off, 4),
                "recovery_sar_mn": round(recovered, 4),
                "cure_flag": cured,
                "past_due_flag": 1 if dpd > 0 else 0,
                "quarters_in_stage": since,
                "origination_quarter": facility["origination_quarter"],
            })

            drift = COLLATERAL_DRIFT.get(facility["collateral_type"], 0.0)
            market = (ead * facility["base_collateral"]
                      * (1 + drift * ramp + 0.015 * season))
            haircut = {"Real Estate": 0.25, "Plant and Equipment": 0.35,
                       "Receivables": 0.30, "Cash Deposit": 0.0,
                       "Corporate Guarantee": 0.40}[
                           facility["collateral_type"]]
            allocated = market * (1 - haircut)
            collateral_rows.append({
                **gov,
                "collateral_id": f"CC{fid[2:]}",
                "facility_id": fid, "borrower_id": bid,
                "reporting_quarter": quarter,
                "collateral_type": facility["collateral_type"],
                "sector": borrower["sector"],
                "market_value_sar_mn": round(market, 2),
                "haircut_pct": round(haircut * 100, 2),
                "allocated_value_sar_mn": round(allocated, 2),
                "coverage_pct": round(allocated / max(ead, 0.01) * 100, 3),
                "ltv_pct": round(ead / max(market, 0.01) * 100, 3),
                "valuation_age_quarters": (q_index % 4) + 1,
            })

            kind = facility["covenant_type"]
            # A covenant tests the figure the book PUBLISHES, against a
            # threshold set from where the borrower started. Testing a
            # privately recomputed ratio against a constant that was the same
            # for every borrower meant a reader comparing `dscr_x` on the
            # borrower row with `observed_value` on its DSCR covenant found
            # two different numbers for one ratio.
            start = origination[bid]
            observed, threshold = {
                "Leverage": (round(here["leverage"], 3),
                             round(max(3.0, start["leverage"] * 1.35), 3)),
                "DSCR": (round(here["dscr"], 3),
                         round(max(1.05, start["dscr"] * 0.72), 3)),
                "Interest Cover": (round(here["interest_cover"], 3),
                                   round(max(1.75,
                                             start["interest_cover"] * 0.62),
                                         3)),
                "Current Ratio": (round(here["current_ratio"], 3),
                                  round(max(0.95,
                                            start["current_ratio"] * 0.82),
                                        3)),
                "Minimum EBITDA": (round(here["ebitda"], 2),
                                   round(start["ebitda"] * 0.78, 2)),
            }[kind]
            # Leverage is a ceiling; everything else here is a floor.
            if kind == "Leverage":
                headroom = (threshold - observed) / max(threshold, 0.01) * 100
            else:
                headroom = (observed - threshold) / max(threshold, 0.01) * 100
            breach = 1 if headroom < 0 else 0
            status = "BREACH" if breach else ("WATCH" if headroom < 10
                                              else "PASS")
            covenant_id = f"CV{fid[2:]}"
            breach_streak[covenant_id] = (
                breach_streak.get(covenant_id, 0) + 1 if breach else 0)
            waived = bool(breach and (q_index + _stable(kind, 97)) % 3 == 0)
            requested = bool(breach and not waived
                             and breach_streak[covenant_id] >= 2)
            covenant_rows.append({
                **gov,
                "covenant_id": covenant_id,
                "facility_id": fid, "borrower_id": bid,
                "reporting_quarter": quarter,
                "sector": borrower["sector"],
                "covenant_type": kind,
                "threshold_value": round(float(threshold), 3),
                "observed_value": round(float(observed), 3),
                "headroom_pct": round(headroom, 3),
                "test_status": status, "breach_flag": breach,
                "waiver_flag": int(waived),
                "waiver_status": ("GRANTED" if waived else
                                  "REQUESTED" if requested else "NONE"),
                "waiver_quarter": quarter if waived else "",
                "consecutive_breaches": breach_streak[covenant_id],
            })

    frames = {
        "corp_borrower_quarter": pd.DataFrame(borrower_rows),
        "corp_facility_quarter": pd.DataFrame(facility_rows),
        "corp_collateral_quarter": pd.DataFrame(collateral_rows),
        "corp_covenant_quarter": pd.DataFrame(covenant_rows),
    }
    products = {p: whole_total(1 for f in facilities
                               if f["product_type"] == p)
                for p in PRODUCT_TYPES}
    return lake.Build(
        domain_id=dom.CORPORATE, release_id=release_id, periods=quarters,
        frames=frames,
        counts={"borrowers": len(borrowers), "facilities": len(facilities),
                "groups": len({b["group_name"] for b in borrowers}),
                "sectors": len({b["sector"] for b in borrowers}),
                "sub_sectors": len({b["sub_sector"] for b in borrowers}),
                "product_types": len(PRODUCT_TYPES),
                "regions": len({b["region"] for b in borrowers})},
        notes={"rating_scale": list(RATINGS) + [DEFAULT_GRADE],
               "stressed_sectors": [s for s, v in SECTOR_STRESS.items()
                                    if v > 0.3],
               "improving_sectors": [s for s, v in SECTOR_STRESS.items()
                                     if v < 0],
               "product_types": list(PRODUCT_TYPES),
               "facilities_by_product": products,
               # The stories the book was written with. Here rather than in a
               # column, because a real book does not publish a field saying
               # which of its borrowers were authored to default.
               "cohorts": {c: whole_total(1 for b in borrowers
                                          if b["cohort"] == c)
                           for c in COHORTS}})


__all__ = ["BORROWERS", "COHORTS", "FROZEN_RELEASES", "FrozenRelease",
           "PRODUCT_TYPES", "RATINGS", "SECTOR_STRESS", "build",
           "cohort_drift", "cohort_of", "cohort_shape", "grade_pd",
           "obligors"]
