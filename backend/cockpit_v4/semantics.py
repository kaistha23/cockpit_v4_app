"""
Canonical Cockpit semantics: what a term means when it has one meaning.

The defect this exists for
--------------------------
A live run recorded these, honestly, as ambiguities:

    "Exposure read as reported EAD (ead_reported), not gross carrying amount."
    "Period not specified: using the latest populated quarter 2026Q2 against
     2026Q1."
    "ECL read as booked ECL (ecl_reported)."

Every one is a correct resolution, written down for the reader. None of them
is a question. But `Intent.may_execute` required an EMPTY ambiguities list, so
recording a resolution blocked the analysis that the resolution made possible.
Careful behaviour was penalised, and the only way to run was to say nothing.

So the three things get three names:

  * a BLOCKING AMBIGUITY is a term with two defensible readings that would
    produce materially different numbers. It stops execution and is worth one
    targeted question.
  * a RESOLVED ASSUMPTION is a choice that has been made and is being
    declared. It belongs in the trace and in the answer's own words. It does
    not stop anything.
  * a CANONICAL MAPPING is a term this domain already defines. It is not even
    a choice.

What is here, and what is not
-----------------------------
Only mappings the catalogue supports. Nothing in this module invents a meaning,
and every entry names the field it resolves to so a reader can check it against
`inspect_catalog`. Where the catalogue genuinely offers two readings -- the
bare word "exposure" is the real case -- there is no canonical mapping and the
term is listed as one that needs a question.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

#: term -> (relation, field, what it means, why this and not the other)
#: Every field here exists in `cockpit_facility_quarter` in the pinned
#: release. A term whose field is absent from a release is dropped from the
#: mapping rather than offered and then failing to bind.
_LEGACY_MEASURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("exposure at default", "cockpit_facility_quarter", "ead_reported",
     "Reported exposure at default.",
     "EAD has one recorded meaning in this domain. `ead_pit` and `ead_ttc` "
     "are model parameters, not the reported figure."),
    ("ead", "cockpit_facility_quarter", "ead_reported",
     "Reported exposure at default.",
     "The same field. `ead_pit` and `ead_ttc` are parameters."),
    ("ecl", "cockpit_facility_quarter", "ecl_reported",
     "Booked expected credit loss as reported.",
     "`ecl_modelled` is the model's output before overlay and "
     "`ecl_overlay` is the adjustment; the booked figure is what the book "
     "carries."),
    ("expected credit loss", "cockpit_facility_quarter", "ecl_reported",
     "Booked expected credit loss as reported.",
     "As for ECL."),
    ("12-month ecl", "cockpit_facility_quarter", "ecl_12m_reported",
     "Reported 12-month ECL.", "Named explicitly by the question."),
    ("lifetime ecl", "cockpit_facility_quarter", "ecl_lifetime_reported",
     "Reported lifetime ECL.", "Named explicitly by the question."),
    ("ecl coverage", "cockpit_facility_quarter", "ecl_coverage_ratio",
     "Reported ECL coverage ratio.",
     "The recorded ratio. Computing ECL/EAD instead is a different figure "
     "and must be described as one."),
    ("gross carrying amount", "cockpit_facility_quarter",
     "gross_carrying_amount", "Gross carrying amount as recorded.",
     "A balance-sheet measure, distinct from EAD."),
    ("drawn balance", "cockpit_facility_quarter", "drawn_balance",
     "Drawn balance.", "Distinct from both EAD and gross carrying amount."),
    ("stage", "cockpit_facility_quarter", "ifrs9_stage",
     "IFRS 9 stage, recorded as 1, 2 or 3.",
     "The recorded stage. `sicr_flag` is the trigger, not the stage."),
    ("stage 1", "cockpit_facility_quarter", "ifrs9_stage",
     "ifrs9_stage = 1.", "Performing, 12-month ECL."),
    ("stage 2", "cockpit_facility_quarter", "ifrs9_stage",
     "ifrs9_stage = 2.",
     "Significant increase in credit risk, lifetime ECL."),
    ("stage 3", "cockpit_facility_quarter", "ifrs9_stage",
     "ifrs9_stage = 3.", "Credit-impaired, lifetime ECL."),
    ("pd", "cockpit_facility_quarter", "pd_pit_12m",
     "Point-in-time 12-month probability of default.",
     "The point-in-time 12-month PD is the reporting default. "
     "`pd_pit_lifetime`, `pd_ttc_12m` and the at-origination variants are "
     "different measures and must be named."),
    ("lgd", "cockpit_facility_quarter", "lgd_pit",
     "Point-in-time loss given default.",
     "`lgd_ttc` and `lgd_downturn` are different measures and must be "
     "named."),
    ("sector", "cockpit_facility_quarter", "sector_name",
     "Recorded sector name. `sector_code` is the same dimension by code.",
     "The only segment dimension in this release. There is no subsegment."),
    ("segment", "cockpit_facility_quarter", "sector_name",
     "Sector, the only segment dimension this release records.",
     "No subsegment level exists; do not imply one."),
    ("borrower", "cockpit_facility_quarter", "borrower_id",
     "Borrower, identified by borrower_id with borrower_name for display.",
     "One borrower may hold several facilities: a facility-grain sum "
     "repeats nothing, but a borrower-grain measure needs an explicit "
     "aggregation."),
    ("customer", "cockpit_facility_quarter", "borrower_id",
     "The same as borrower in this domain.", "One recorded counterparty."),
    ("facility", "cockpit_facility_quarter", "facility_id",
     "Facility, the grain of cockpit_facility_quarter.",
     "One row per facility per reporting quarter."),
    ("days past due", "cockpit_facility_quarter", "days_past_due",
     "Days past due at the reporting date.", "Recorded, not derived."),
    ("collateral coverage", "cockpit_facility_quarter",
     "collateral_coverage_ratio", "Recorded collateral coverage ratio.",
     "Allocated net collateral value over the coverage denominator, as "
     "recorded."),
    ("rating", "cockpit_rating_ratio_quarter", "risk_rating",
     "Internal risk rating, with rating_rank as its ordinal.",
     "Borrower grain. A lower rank is a stronger grade."),
)

#: The Corporate book of the dual-domain Cockpit. Wholesale: a named obligor
#: holds facilities, security is pledged against a facility, and a covenant
#: tests one. "Customer" therefore means BORROWER here, and it means something
#: else one book over -- which is exactly why this table is per domain rather
#: than one dictionary with a domain column.
_CORPORATE_MEASURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("exposure at default", "corp_facility_quarter", "ead_sar_mn",
     "Exposure at default, in SAR million.",
     "EAD has one recorded meaning in this book. `limit_sar_mn` is the "
     "committed limit and `drawn_sar_mn` is the drawn balance; neither is "
     "EAD."),
    ("ead", "corp_facility_quarter", "ead_sar_mn",
     "Exposure at default, in SAR million.", "The same field."),
    ("ecl", "corp_facility_quarter", "ecl_sar_mn",
     "Recognised expected credit loss, in SAR million.",
     "The recognised figure. `ecl_12m_sar_mn` and `ecl_lifetime_sar_mn` are "
     "the two stage bases; the recognised number is the one the book "
     "carries."),
    ("expected credit loss", "corp_facility_quarter", "ecl_sar_mn",
     "Recognised expected credit loss, in SAR million.", "As for ECL."),
    ("12-month ecl", "corp_facility_quarter", "ecl_12m_sar_mn",
     "12-month ECL, in SAR million.", "Named explicitly by the question."),
    ("lifetime ecl", "corp_facility_quarter", "ecl_lifetime_sar_mn",
     "Lifetime ECL, in SAR million.", "Named explicitly by the question."),
    ("stage", "corp_facility_quarter", "stage",
     "IFRS 9 stage, recorded as 1, 2 or 3.",
     "The recorded stage. `sicr_flag` is the trigger, not the stage."),
    ("stage 1", "corp_facility_quarter", "stage", "stage = 1.",
     "Performing, 12-month ECL."),
    ("stage 2", "corp_facility_quarter", "stage", "stage = 2.",
     "Significant increase in credit risk, lifetime ECL."),
    ("stage 3", "corp_facility_quarter", "stage", "stage = 3.",
     "Credit-impaired, lifetime ECL."),
    ("sicr", "corp_facility_quarter", "sicr_flag",
     "Significant-increase-in-credit-risk trigger, 0 or 1.",
     "The trigger. The stage it produces is `stage`."),
    ("default", "corp_facility_quarter", "default_flag",
     "Default marker, 0 or 1.", "Recorded, not derived from DPD."),
    ("pd", "corp_facility_quarter", "pd_pit_12m",
     "Point-in-time 12-month probability of default.",
     "`pd_lifetime` is a different measure and must be named. The borrower "
     "relation also records `pd_ttc_12m`, which is through-the-cycle."),
    ("lgd", "corp_facility_quarter", "lgd_pct",
     "Loss given default, as a percentage.",
     "Recorded at facility grain, in percent rather than a 0-1 fraction."),
    ("days past due", "corp_facility_quarter", "dpd_days",
     "Days past due at the reporting date.", "Recorded, not derived."),
    ("utilisation", "corp_facility_quarter", "utilisation_pct",
     "Drawn as a percentage of limit.",
     "Recorded. Computing drawn/limit instead is the same idea but a "
     "different number where either side is null."),
    ("limit", "corp_facility_quarter", "limit_sar_mn",
     "Committed limit, in SAR million.", "Not EAD and not drawn."),
    ("drawn", "corp_facility_quarter", "drawn_sar_mn",
     "Drawn balance, in SAR million.", "Not EAD."),
    ("facility", "corp_facility_quarter", "facility_id",
     "Facility, the grain of corp_facility_quarter.",
     "One row per facility per reporting month."),
    ("facility type", "corp_facility_quarter", "facility_type",
     "Recorded facility type.", "A product-like dimension for wholesale."),
    ("sector", "corp_borrower_quarter", "sector",
     "Recorded sector. `sub_sector` is the level below it.",
     "The primary segment dimension of this book."),
    ("segment", "corp_borrower_quarter", "sector",
     "Sector, this book's primary segment dimension.",
     "`relationship_tier` and `region` are the other two."),
    ("region", "corp_borrower_quarter", "region", "Recorded region.",
     "Also carried on corp_facility_quarter for facility-grain work."),
    ("borrower", "corp_borrower_quarter", "borrower_id",
     "Borrower, identified by borrower_id with borrower_name for display.",
     "One borrower holds several facilities: a facility-grain sum repeats "
     "nothing, but a borrower-grain measure needs an explicit "
     "aggregation."),
    ("customer", "corp_borrower_quarter", "borrower_id",
     "In the Corporate book a customer IS the borrower.",
     "This is a wholesale book. There is no separate retail customer here; "
     "that is the other book."),
    ("obligor", "corp_borrower_quarter", "borrower_id", "The borrower.",
     "One recorded counterparty."),
    ("group", "corp_borrower_quarter", "group_id",
     "Parent group, with group_name for display.",
     "Several borrowers may share one group: a group total needs an "
     "explicit aggregation."),
    ("rating", "corp_borrower_quarter", "rating_current",
     "Current internal rating, with rating_previous for the move.",
     "`rating_notches_moved` is the recorded movement; do not recompute it "
     "from the two grades unless the question asks for that."),
    ("leverage", "corp_borrower_quarter", "leverage_x",
     "Net debt to EBITDA, in times.", "Recorded, not derived."),
    ("dscr", "corp_borrower_quarter", "dscr_x",
     "Debt service coverage ratio, in times.", "Recorded."),
    ("interest cover", "corp_borrower_quarter", "interest_cover_x",
     "Interest coverage, in times.", "Recorded."),
    ("ebitda", "corp_borrower_quarter", "ebitda_sar_mn",
     "EBITDA, in SAR million.", "Borrower grain, not facility grain."),
    ("revenue", "corp_borrower_quarter", "revenue_sar_mn",
     "Revenue, in SAR million.", "Borrower grain."),
    ("collateral", "corp_collateral_quarter", "allocated_value_sar_mn",
     "Collateral value allocated to the facility, in SAR million.",
     "`market_value_sar_mn` is before the haircut; the allocated value is "
     "what supports the facility."),
    ("collateral coverage", "corp_collateral_quarter", "coverage_pct",
     "Recorded collateral coverage, as a percentage.",
     "Collateral item grain: a facility with two items has two rows."),
    ("ltv", "corp_collateral_quarter", "ltv_pct",
     "Loan to value, as a percentage.", "Collateral item grain."),
    ("covenant", "corp_covenant_quarter", "covenant_id",
     "Covenant test, the grain of corp_covenant_quarter.",
     "One row per covenant test per facility per month."),
    ("covenant breach", "corp_covenant_quarter", "breach_flag",
     "Breach marker, 0 or 1, with test_status in words.",
     "`waiver_flag` records that a breach was waived; it does not undo the "
     "breach."),
    ("headroom", "corp_covenant_quarter", "headroom_pct",
     "Covenant headroom, as a percentage.",
     "Negative headroom is a breach."),
)

#: The Retail book. A customer holds accounts; an account has a product, a
#: vintage and a behaviour score. Nothing here is a borrower and nothing here
#: has a covenant.
_RETAIL_MEASURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("exposure at default", "retail_account_month", "ead_sar_mn",
     "Exposure at default, in SAR million.",
     "`limit_sar_mn` is the limit and `balance_sar_mn` the balance; neither "
     "is EAD. `retail_customer_month.total_ead_sar_mn` is the SAME exposure "
     "already summed to the customer."),
    ("ead", "retail_account_month", "ead_sar_mn",
     "Exposure at default, in SAR million.", "The same field."),
    ("ecl", "retail_account_month", "ecl_sar_mn",
     "Recognised expected credit loss, in SAR million.",
     "`ecl_12m_sar_mn` and `ecl_lifetime_sar_mn` are the two stage bases. "
     "`retail_customer_month.total_ecl_sar_mn` is the same ECL summed to "
     "the customer: adding both relations double-counts it."),
    ("expected credit loss", "retail_account_month", "ecl_sar_mn",
     "Recognised expected credit loss, in SAR million.", "As for ECL."),
    ("12-month ecl", "retail_account_month", "ecl_12m_sar_mn",
     "12-month ECL, in SAR million.", "Named explicitly by the question."),
    ("lifetime ecl", "retail_account_month", "ecl_lifetime_sar_mn",
     "Lifetime ECL, in SAR million.", "Named explicitly by the question."),
    ("stage", "retail_account_month", "stage",
     "IFRS 9 stage, recorded as 1, 2 or 3.",
     "Account grain. `retail_customer_month.worst_stage` is the worst stage "
     "across that customer's accounts, which is a different statement."),
    ("stage 1", "retail_account_month", "stage", "stage = 1.",
     "Performing, 12-month ECL."),
    ("stage 2", "retail_account_month", "stage", "stage = 2.",
     "Significant increase in credit risk, lifetime ECL."),
    ("stage 3", "retail_account_month", "stage", "stage = 3.",
     "Credit-impaired, lifetime ECL."),
    ("sicr", "retail_account_month", "sicr_flag",
     "Significant-increase-in-credit-risk trigger, 0 or 1.",
     "The trigger, not the stage."),
    ("default", "retail_account_month", "default_flag",
     "Default marker, 0 or 1.", "Recorded."),
    ("pd", "retail_account_month", "pd_pit_12m",
     "Point-in-time 12-month probability of default.",
     "`pd_lifetime` is a different measure and must be named."),
    ("lgd", "retail_account_month", "lgd_pct",
     "Loss given default, as a percentage.", "Account grain."),
    ("days past due", "retail_account_month", "dpd_days",
     "Days past due at the reporting date.",
     "`delinquency_bucket` is the recorded banding of it."),
    ("delinquency", "retail_account_month", "delinquency_bucket",
     "Recorded delinquency bucket.",
     "The banding the book uses. `dpd_days` is the underlying count, and "
     "`delinquency_bucket_fine` splits the first thirty days into 1-9, "
     "10-19 and 20-29 and the tail into 90-179 and 180+."),
    ("sub-bucket", "retail_account_month", "delinquency_bucket_fine",
     "Arrears at collections grain.",
     "Use it to say WHERE inside 1-29 a movement sits. It nests exactly "
     "inside `delinquency_bucket`: summing the fine bands of a coarse one "
     "reproduces it."),
    ("sub-product", "retail_account_month", "sub_product",
     "What the product was sold as.",
     "Nested inside `product`. A sub-product name is only unambiguous with "
     "its product: `Gold` is a card, and `New Vehicle` is auto finance."),
    ("employment", "retail_account_month", "employment_type",
     "How the customer is paid.",
     "Salaried-Government, Salaried-Private, Self-employed or "
     "Non-salaried. Also carried on retail_customer_month."),
    ("channel", "retail_account_month", "origination_channel",
     "Where the account was written: Branch, Digital or Partner.",
     "An origination attribute: it does not change over the account's "
     "life."),
    ("write-off", "retail_account_month", "write_off_sar_mn",
     "Amount written off in the month, in SAR million.",
     "A flow, not a balance: summing it across months is a cumulative "
     "write-off, and summing it across accounts within one month is that "
     "month's write-off."),
    ("recovery", "retail_account_month", "recovery_sar_mn",
     "Amount recovered in the month, in SAR million.", "A flow, as above."),
    ("cure", "retail_account_month", "cure_flag",
     "Cure marker, 0 or 1, for an account that returned to performing.",
     "Recorded in the month the cure happened."),
    ("utilisation", "retail_account_month", "utilisation_pct",
     "Balance as a percentage of limit.",
     "Recorded. `retail_behaviour_month.utilisation_change_pp` is its "
     "movement, in percentage points."),
    ("limit", "retail_account_month", "limit_sar_mn",
     "Credit limit, in SAR million.", "Not EAD."),
    ("balance", "retail_account_month", "balance_sar_mn",
     "Outstanding balance, in SAR million.", "Not EAD."),
    ("product", "retail_account_month", "product",
     "Recorded product: the primary segment dimension of this book.",
     "This book's equivalent of the Corporate sector. There is no sector "
     "here."),
    ("segment", "retail_account_month", "product",
     "Product, this book's primary segment dimension.",
     "`customer_segment`, `region`, `score_band` and `vintage_year` are the "
     "others."),
    ("customer segment", "retail_customer_month", "customer_segment",
     "Recorded customer segment.",
     "Also carried on the account relation for account-grain work."),
    ("region", "retail_account_month", "region", "Recorded region.",
     "Also carried on retail_customer_month."),
    ("vintage", "retail_account_month", "vintage_year",
     "Origination vintage year, with origination_month for the exact date.",
     "`months_on_book` is the elapsed time since origination."),
    ("account", "retail_account_month", "account_id",
     "Account, the grain of retail_account_month.",
     "One row per account per reporting month."),
    ("customer", "retail_customer_month", "customer_id",
     "Retail customer, the grain of retail_customer_month.",
     "One customer holds several accounts: an account-grain sum repeats "
     "nothing, but a customer-grain measure needs an explicit aggregation. "
     "There is no borrower in this book."),
    ("behaviour score", "retail_customer_month", "behaviour_score",
     "Behaviour score, with behaviour_score_previous for the move.",
     "`behaviour_score_change` is the recorded movement and "
     "`score_migration` the banded description of it."),
    ("score band", "retail_customer_month", "score_band",
     "Recorded behaviour score band.",
     "`score_band_previous` is last month's band."),
    ("tenure", "retail_customer_month", "tenure_months",
     "Months the customer has been on book.",
     "Customer grain. `months_on_book` is the account equivalent."),
    ("payment ratio", "retail_behaviour_month", "payment_ratio_pct",
     "Payment as a percentage of the amount due.", "Account grain."),
    ("missed payments", "retail_behaviour_month", "missed_payments_12m",
     "Missed payments in the last twelve months.", "Recorded, not derived."),
    ("collateral", "retail_collateral_month", "collateral_value_sar_mn",
     "Collateral value, in SAR million.",
     "SECURED accounts only. An unsecured account has no row here, so an "
     "inner join silently drops it."),
    ("ltv", "retail_collateral_month", "ltv_pct",
     "Loan to value, as a percentage.", "Secured accounts only."),
    ("collateral coverage", "retail_collateral_month",
     "collateral_coverage_pct", "Recorded collateral coverage.",
     "Secured accounts only."),
)

#: term -> candidate fields, per book. A term is listed only where the
#: catalogue genuinely records two readings that produce different numbers.
_AMBIGUOUS_BY_DOMAIN: dict[str, dict[str, tuple[str, ...]]] = {
    "": {"exposure": ("ead_reported", "gross_carrying_amount",
                      "drawn_balance")},
    "corporate": {"exposure": ("ead_sar_mn", "limit_sar_mn",
                               "drawn_sar_mn")},
    "retail": {"exposure": ("ead_sar_mn", "limit_sar_mn",
                            "balance_sar_mn")},
}

#: METHODS with more than one defensible reading, as opposed to TERMS. The
#: catalogue can never say so -- every field involved maps cleanly -- so this
#: is read off the question's own words and nothing else. Deliberately narrow:
#: a method is only worth a question when the choice changes the numbers AND
#: the question has not already picked one. "What is driving Stage 2 and ECL
#: growth?" matches neither entry: it names no PD/LGD/CCF/EAD factor and asks
#: for no average, so it runs the default decomposition and states it.
_AMBIGUOUS_METHODS: dict[str, dict[str, Any]] = {
    "attribution_order": {
        "options": ("Sequential attribution (PD, then LGD, then EAD)",
                    "Symmetric attribution (order-independent)"),
        "why": ("Sequential attribution lets the order decide which of PD, "
                "LGD and CCF ranks first; a symmetric one does not. Same "
                "rows, different ranking."),
        # Two or more distinct factors, an attribution-ish verb, AND an ECL
        # change as the thing being attributed (or "dominant", which is a
        # ranking of the factors in itself). "What is driving PD and LGD
        # up?" attributes nothing to ECL and is left to run.
        "all_of": (re.compile(r"\b(pd|lgd|ccf|ead)\b"),
                   re.compile(r"\b(attribut\w*|decompos\w*|dominan\w*|"
                              r"contribut\w*|waterfall|bridge|driv\w*)\b"),
                   re.compile(r"\becl\b.*\b(change|movement|growth|"
                              r"increase|rise|up|driv\w*)\b|"
                              r"\b(change|movement|growth|increase|rise) "
                              r"(in|of) (the )?(\w+ )?ecl\b|"
                              r"\bdominan\w*")),
        "unless": re.compile(r"\b(sequential|symmetric|shapley)\b"),
        "distinct_first": 2,
    },
    "average_weighting": {
        "options": ("EAD-weighted average", "Simple average"),
        "why": ("An average of a ratio over facilities is a different number "
                "weighted by exposure than unweighted."),
        "all_of": (re.compile(r"\b(average|mean|avg)\b"),
                   re.compile(r"\b(pd|lgd|ccf|ratio|rate|coverage|dscr|ltv|"
                              r"score|percent\w*)\b")),
        "unless": re.compile(r"\b(weighted|unweighted|simple)\b"),
        "distinct_first": 0,
    },
}


def ambiguous_methods_in(question: str) -> list[dict[str, Any]]:
    """Methods this question leaves open, read off the QUESTION text only.

    Same shape and same spirit as `ambiguous_terms_in`, for a choice the
    catalogue does not record. The same two entries apply to both books, so
    no catalogue is taken. Never reads the analyst's own declarations: a run
    must not be able to widen what counts as ambiguous by asserting it. A
    question that has already picked the method ("EAD-weighted", "symmetric")
    has nothing left open, so naming one switches the entry off.
    """
    text = re.sub(r"\s+", " ", (question or "").lower())
    out: list[dict[str, Any]] = []
    for method, spec in _AMBIGUOUS_METHODS.items():
        if spec["unless"].search(text):
            continue
        if not all(rx.search(text) for rx in spec["all_of"]):
            continue
        distinct = {m.group(1) for m in spec["all_of"][0].finditer(text)}
        if len(distinct) < max(1, spec["distinct_first"]):
            continue
        out.append({"method": method, "options": list(spec["options"]),
                    "why": spec["why"]})
    return out


def non_latin(question: str) -> bool:
    """True when the question holds a letter outside the Latin script.

    `ambiguous_terms_in` and the method rules match English words, so a
    question written in another script can hold an ambiguous term the server
    cannot see. The server then cannot call a question back unnecessary,
    and `clarify_gate` stays out of the way. Multilingual aliases for the
    ambiguous terms are the longer-term fix.
    """
    for ch in str(question or ""):
        if ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            return True
    return False


#: Terms with more than one defensible reading in the legacy catalogue.
AMBIGUOUS_TERMS: dict[str, tuple[str, ...]] = _AMBIGUOUS_BY_DOMAIN[""]

_MEASURES_BY_DOMAIN: dict[
    str, tuple[tuple[str, str, str, str, str], ...]] = {
    "": _LEGACY_MEASURES,
    "corporate": _CORPORATE_MEASURES,
    "retail": _RETAIL_MEASURES,
}


def domain_of(catalog: Any) -> str:
    """Which book this catalogue is. Empty means the legacy one.

    Read off the catalogue that was handed in, never off a module constant:
    a process serves both books at once and the answer differs per run.
    """
    return str(getattr(catalog, "domain_id", "") or "")


def measure_table(catalog: Any) -> tuple[tuple[str, str, str, str, str], ...]:
    """The canonical term table of THIS book."""
    return _MEASURES_BY_DOMAIN.get(domain_of(catalog), _LEGACY_MEASURES)


def ambiguous_terms(catalog: Any = None) -> dict[str, tuple[str, ...]]:
    """The terms worth a question in THIS book."""
    return _AMBIGUOUS_BY_DOMAIN.get(domain_of(catalog), AMBIGUOUS_TERMS)

#: Period vocabulary, by the frequency the book actually reports on. A
#: monthly book asked about "last quarter" is being asked something its
#: calendar does not define, and answering with a month would be substituting.
_PERIOD_PHRASES: dict[str, dict[str, str]] = {
    "quarterly": {
        "latest quarter": "latest_period",
        "latest reporting quarter": "latest_period",
        "most recent quarter": "latest_period",
        "this quarter": "latest_period",
        "current quarter": "latest_period",
        "previous quarter": "prior_period",
        "prior quarter": "prior_period",
        "last quarter": "prior_period",
        "quarter on quarter": "period_on_period",
        "quarter-on-quarter": "period_on_period",
        "over the latest quarter": "period_on_period",
        "latest-quarter change": "period_on_period",
    },
    "monthly": {
        "latest month": "latest_period",
        "latest reporting month": "latest_period",
        "most recent month": "latest_period",
        "this month": "latest_period",
        "current month": "latest_period",
        "previous month": "prior_period",
        "prior month": "prior_period",
        "last month": "prior_period",
        "month on month": "period_on_period",
        "month-on-month": "period_on_period",
        "over the latest month": "period_on_period",
        "latest-month change": "period_on_period",
    },
}

#: Phrases that mean the same thing whatever the reporting frequency.
_COMMON_PERIOD_PHRASES: dict[str, str] = {
    "latest period": "latest_period",
    "latest reporting period": "latest_period",
    "most recent period": "latest_period",
    "previous period": "prior_period",
    "prior period": "prior_period",
    "latest year": "year_on_year",
    "over the latest year": "year_on_year",
    "last year": "year_on_year",
    "year on year": "year_on_year",
    "year-on-year": "year_on_year",
    "past year": "year_on_year",
}


def measures(catalog: Any = None) -> list[dict[str, str]]:
    """The canonical measure mappings this release can actually honour."""
    out: list[dict[str, str]] = []
    for term, relation, field, meaning, note in measure_table(catalog):
        if catalog is not None:
            try:
                if field not in set(catalog.columns(relation)):
                    continue
            except Exception:  # noqa: BLE001
                continue
        out.append({"term": term, "relation": relation, "field": field,
                    "means": meaning, "note": note})
    return out


#: Relation-level facts for the LEGACY catalogue, which does not carry them.
#: A V4 catalogue states its own grain, period column and keys, and
#: `_relation_facts` reads them from it rather than from this table.
_RELATION_FACTS: dict[str, dict[str, str]] = {
    "cockpit_facility_quarter": {
        "grain": "one row per facility per reporting quarter",
        "period_field": "reporting_quarter",
        "key": "facility_id",
        "borrower_key": "borrower_id"},
    "cockpit_rating_ratio_quarter": {
        "grain": "one row per borrower per reporting quarter",
        "period_field": "reporting_quarter",
        "key": "borrower_id",
        "borrower_key": "borrower_id"},
    "cockpit_covenant_quarter": {
        "grain": "one row per covenant per borrower per reporting quarter",
        "period_field": "reporting_quarter",
        "key": "covenant_id",
        "borrower_key": "borrower_id"},
    "cockpit_borrower_financial_quarter": {
        "grain": "one row per borrower per reporting quarter",
        "period_field": "reporting_quarter",
        "key": "borrower_id",
        "borrower_key": "borrower_id"},
    "cockpit_collateral_quarter": {
        "grain": "one row per collateral item per reporting quarter",
        "period_field": "reporting_quarter",
        "key": "collateral_id",
        "borrower_key": ""},
}


def _field_facts(
    relation: str,
    column: str,
    spec: Any,
    catalog: Any,
) -> dict[str, Any]:
    """Schema facts for one column, read straight off the catalogue.

    The mechanical half of a field-packet entry: what the column IS -- its
    type, unit, how it aggregates, the values it may take, the grain and key
    of the relation it sits in. Not which question it answers.

    Callers with a canonical mapping overwrite `means` with the term's
    recorded meaning; callers seeding an investigation keep the catalogue's
    own definition, because a covenant column has no canonical mapping and
    inventing one here would be this module deciding the analysis.
    """
    entry: dict[str, Any] = {
        "field_id": f"{relation}.{column}",
        "relation": relation,
        "column": column,
    }
    if spec is not None:
        definition = str(getattr(spec, "definition", "") or "")
        if definition:
            entry["means"] = definition
        entry["dtype"] = getattr(spec, "dtype", "")
        unit = getattr(spec, "unit", "")
        if unit:
            entry["unit"] = unit
        aggregation = getattr(spec, "aggregation", "")
        if aggregation:
            entry["aggregation"] = aggregation
        enumeration = tuple(getattr(spec, "enumeration", ()) or ())
        if enumeration:
            entry["allowed_values"] = list(enumeration)
        if getattr(spec, "currency_scoped", False):
            entry["currency"] = getattr(catalog, "reporting_currency", "")
            entry["amount_scale"] = getattr(catalog, "amount_scale", "")
    facts = _relation_facts(catalog, relation)
    entry.update({k: v for k, v in facts.items() if v})
    return entry


def _relation_facts(catalog: Any, relation: str) -> dict[str, str]:
    """Grain, period column and keys, from the catalogue that owns them.

    A catalogue that states these is asked; only one that does not falls back
    to the static table. That is what lets one function serve two books whose
    relations share no name.
    """
    getter = getattr(catalog, "spec", None)
    if callable(getter):
        try:
            spec = getter(relation)
        except Exception:  # noqa: BLE001
            spec = None
        if spec is not None:
            keys = tuple(getattr(spec, "key_columns", ()) or ())
            columns = set(getattr(spec, "columns", ()) or ())
            owner = next((c for c in ("borrower_id", "customer_id")
                          if c in columns), "")
            facts = {
                "grain": str(getattr(spec, "grain", "") or ""),
                "period_field": str(getattr(spec, "period_column", "") or ""),
                "key": keys[0] if keys else "",
                "key_columns": ", ".join(keys),
            }
            if owner:
                facts["counterparty_key"] = owner
            return facts
    return dict(_RELATION_FACTS.get(relation, {}))


#: The fields a seeded investigation needs BEYOND the canonical measures,
#: keyed by the attention indicator the card was built on.
#:
#: Mechanical, not analytical. Every column here is one the card's own
#: server-authored SQL reads to compute that indicator, plus the columns that
#: identify a row at its relation's grain. CreditProbe already knows them
#: because it already ran that SQL. Nothing here says how to aggregate them,
#: which quarter to compare, or what the answer is.
#:
#: Most indicators map to an empty tuple, and that is the finding rather than
#: an omission: they are computed from `cockpit_facility_quarter` columns that
#: `field_packet` already carries -- `ead_reported`, `ecl_reported`,
#: `ifrs9_stage`, `pd_pit_12m`, `days_past_due`, `collateral_coverage_ratio`.
#: A seeded thread on those needs no extra schema at all.
#:
#: The two that are not empty are the two whose measure lives outside the
#: facility relation. This exists because a live seeded covenant run called
#: `inspect_catalog` for `cockpit_covenant_quarter` and got back all
#: fifty-nine of the columns the release carries for it -- the whole relation,
#: because no covenant column is among the canonical measures and asking for
#: the relation was the only way to ask.
SEED_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    # SQL_COVENANT reads borrower_id, reporting_quarter, headroom_value and
    # breach_date. The rest identify the obligation a breach belongs to, so
    # "which covenants are in breach" resolves at the covenant grain instead
    # of returning borrower ids with nothing to name them by.
    "covenant_breach_share": (
        ("cockpit_covenant_quarter", "covenant_id"),
        ("cockpit_covenant_quarter", "borrower_id"),
        ("cockpit_covenant_quarter", "facility_id"),
        ("cockpit_covenant_quarter", "covenant_name"),
        ("cockpit_covenant_quarter", "covenant_type"),
        ("cockpit_covenant_quarter", "metric_name"),
        ("cockpit_covenant_quarter", "comparison_operator"),
        ("cockpit_covenant_quarter", "threshold_value"),
        ("cockpit_covenant_quarter", "observed_value"),
        ("cockpit_covenant_quarter", "test_status"),
        ("cockpit_covenant_quarter", "headroom_value"),
        ("cockpit_covenant_quarter", "headroom_unit"),
        ("cockpit_covenant_quarter", "breach_date"),
        ("cockpit_covenant_quarter", "waiver_flag"),
    ),
    # SQL_RATING reads borrower_id and rating_rank, and the relation is at
    # borrower x quarter x rating_basis grain, so the basis has to come with
    # it or a follow-up silently sums one borrower once per basis.
    "rating_rank": (
        ("cockpit_rating_ratio_quarter", "borrower_id"),
        ("cockpit_rating_ratio_quarter", "rating_basis"),
        ("cockpit_rating_ratio_quarter", "risk_rating"),
        ("cockpit_rating_ratio_quarter", "rating_rank"),
    ),
    "stage2_share": (),
    "stage3_share": (),
    "ecl_coverage": (),
    "ecl_amount": (),
    "weighted_pd": (),
    "uncovered_share": (),
    "past_due_share": (),
    "concentration_share": (),
}

#: The most field definitions a seeded case file may carry. Not a guess: the
#: widest entry above is the covenant one at fourteen, and the bound exists so
#: that widening it is a decision someone makes here rather than something a
#: relation dump does by accident.
MAX_SEED_FIELDS = 16

#: The same idea for the dual-domain books, keyed by domain and then by the
#: attention indicator the card was built on.
#:
#: Almost every V4 indicator is computed from columns of the book's own
#: headline relation -- `ead_sar_mn`, `ecl_sar_mn`, `stage`, `dpd_days` --
#: which `field_packet` already carries, so the honest entry is an empty
#: tuple. The Corporate collateral card is the exception: its measure lives
#: in `corp_collateral_quarter`, one relation over, and a seeded thread on it
#: would otherwise have to ask for the whole relation to name a single field.
SEED_FIELDS_BY_DOMAIN: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "corporate": {
        "ltv": (
            ("corp_collateral_quarter", "collateral_id"),
            ("corp_collateral_quarter", "facility_id"),
            ("corp_collateral_quarter", "borrower_id"),
            ("corp_collateral_quarter", "collateral_type"),
            ("corp_collateral_quarter", "market_value_sar_mn"),
            ("corp_collateral_quarter", "haircut_pct"),
            ("corp_collateral_quarter", "allocated_value_sar_mn"),
            ("corp_collateral_quarter", "coverage_pct"),
            ("corp_collateral_quarter", "ltv_pct"),
            ("corp_collateral_quarter", "valuation_age_months"),
        ),
        "stage2_share": (),
        "ecl": (),
        "ecl_coverage": (),
        "past_due_share": (),
    },
    "retail": {
        "stage2_share": (),
        "ecl": (),
        "dpd_share": (),
    },
}


def seed_field_packet(catalog: Any, metric: str) -> list[dict[str, Any]]:
    """Schema facts for the fields THIS seeded investigation turns on.

    Bounded by construction: only the columns the card's own indicator is
    computed from, and only those the release actually carries. An indicator
    with no entry returns nothing rather than falling back to a relation dump,
    because "nothing extra" is the correct answer for every indicator whose
    measure the canonical packet already covers.
    """
    book = SEED_FIELDS_BY_DOMAIN.get(domain_of(catalog))
    table = SEED_FIELDS if book is None else book
    wanted = table.get(str(metric or "").strip(), ())
    packet: list[dict[str, Any]] = []
    for relation, column in wanted:
        try:
            spec = catalog.resolve(relation, column)
        except Exception:  # noqa: BLE001
            continue
        if spec is None:
            continue
        packet.append(_field_facts(relation, column, spec, catalog))
    return packet[:MAX_SEED_FIELDS]


def field_packet(catalog: Any) -> list[dict[str, Any]]:
    """Mechanical schema facts for the canonically mapped fields.

    Enough to WRITE a query without exploring the catalogue: the relation, the
    column, what it means, its type and unit, the grain of its relation, the
    column that carries the reporting period, and the key to join on. Read
    straight off the catalogue, so nothing here is a definition someone
    invented.

    What it deliberately is not: a method. It does not say which measure
    answers a question, how to aggregate it, which quarter to pick, or what
    the answer is. Choosing those is the analysis, and the analyst does that.

    This exists because a live run spent three generations and its whole
    deadline calling `inspect_catalog` for "what is total exposure at default
    by sector in the latest quarter?" -- a question whose every term the
    server had already resolved. It knew the fields and did not say what type
    they were.
    """
    seen: dict[str, dict[str, Any]] = {}
    packet: list[dict[str, Any]] = []
    for mapping in measures(catalog):
        relation, column = mapping["relation"], mapping["field"]
        field_id = f"{relation}.{column}"
        if field_id in seen:
            # A second term for the SAME column is an alias, and dropping it
            # silently lost real information: "segment" resolves to `sector`
            # in one book and `product` in the other, and an analyst reading
            # a packet that names only `sector` has not been told that.
            entry = seen[field_id]
            entry.setdefault("also_known_as", []).append(mapping["term"])
            if mapping["note"] not in entry.get("alias_notes", []):
                entry.setdefault("alias_notes", []).append(mapping["note"])
            continue
        try:
            spec = catalog.resolve(relation, column)
        except Exception:  # noqa: BLE001
            spec = None
        entry: dict[str, Any] = {"term": mapping["term"]}
        entry.update(_field_facts(relation, column, spec, catalog))
        entry["means"] = mapping["means"]
        seen[field_id] = entry
        packet.append(entry)
    return packet


def frequency(catalog: Any) -> str:
    """How often this book reports. Read off its own calendar."""
    calendar = getattr(catalog, "calendar", None)
    value = str(getattr(calendar, "frequency", "") or "").strip().lower()
    return value or "quarterly"


def period_noun(catalog: Any) -> str:
    return {"monthly": "month", "quarterly": "quarter"}.get(
        frequency(catalog), "period")


def populated_periods(catalog: Any) -> list[str]:
    """The periods that actually hold rows, oldest first."""
    calendar = getattr(catalog, "calendar", None)
    return [str(q) for q in (getattr(calendar, "populated", ()) or ())]


def populated_quarters(catalog: Any) -> list[str]:
    """Kept for the quarterly legacy catalogue. Same list, older name."""
    return populated_periods(catalog)


def periods(catalog: Any) -> dict[str, Any]:
    """Deterministic period resolution against THIS book's own calendar.

    "Latest" is the latest POPULATED period, not the latest slot the calendar
    defines: a period with no rows is not a reporting period, and silently
    comparing against one produces a movement that is entirely an artefact of
    coverage.

    The vocabulary follows the frequency. A monthly book says month, a
    quarterly book says quarter, and the year-ago comparison steps back by
    the right number of slots for each.
    """
    slots = populated_periods(catalog)
    noun = period_noun(catalog)
    freq = frequency(catalog)
    per_year = {"monthly": 12, "quarterly": 4}.get(freq, 4)
    if not slots:
        return {"reporting_frequency": freq, "period_noun": noun,
                "populated_periods": [], "resolution": {}}
    latest = slots[-1]
    prior = slots[-2] if len(slots) >= 2 else ""
    year_ago = slots[-(per_year + 1)] if len(slots) >= per_year + 1 else ""
    body: dict[str, Any] = {
        "reporting_frequency": freq,
        "period_noun": noun,
        "period_column": _period_column(catalog),
        "populated_periods": slots,
        "latest_period": latest,
        "prior_period": prior,
        f"same_{noun}_last_year": year_ago,
        "resolution": {
            f"latest {noun}": latest,
            f"previous {noun}": prior,
            f"{noun} on {noun}": (f"{latest} vs {prior}" if prior else ""),
            "over the latest year": (f"{latest} vs {year_ago}"
                                     if year_ago else ""),
        },
        "rule": (
            f"'Latest {noun}' is the latest POPULATED reporting {noun}. A "
            f"latest-{noun} CHANGE is that {noun} against the previous "
            f"populated one. 'Over the latest year' is that {noun} against "
            f"the same {noun} one year earlier, which is {per_year} {noun}s "
            f"back. These are resolutions, not assumptions to ask about. "
            f"This book reports {freq}: a question asking for a period this "
            f"calendar does not define is a question to ask back, not one to "
            f"answer with the nearest thing."),
    }
    if freq == "quarterly":
        # The legacy quarterly vocabulary, for a reader and a packet that
        # were written against it.
        body["populated_quarters"] = slots
        body["latest_quarter"] = latest
        body["prior_quarter"] = prior
    return body


def _period_column(catalog: Any) -> str:
    """The column carrying the reporting period in this book."""
    getter = getattr(catalog, "spec", None)
    if callable(getter):
        for relation in (getattr(catalog, "relations", lambda: ())() or ()):
            try:
                column = str(getattr(getter(relation), "period_column", ""))
            except Exception:  # noqa: BLE001
                continue
            if column:
                return column
    return "reporting_quarter"


#: The tokens a prompt or a tool schema may carry, so that neither has to
#: hard-code a calendar. Substituted from the book the RUN is reading.
VOCABULARY_TOKENS = ("PERIOD", "PERIODS", "PERIOD_COLUMN", "PERIOD_FIELD",
                     "LATEST_PERIOD", "PRIOR_PERIOD", "FREQUENCY",
                     "PERIOD_EXAMPLE", "MAPPED_TERM_EXAMPLE")

#: What to say when there is no book to ask. Neutral, and deliberately not a
#: calendar: a prompt with no release open must not name one.
NEUTRAL_VOCABULARY: dict[str, str] = {
    "PERIOD": "period", "PERIODS": "periods",
    "PERIOD_COLUMN": "the reporting period column",
    "PERIOD_FIELD": "reporting_periods",
    "LATEST_PERIOD": "the latest populated period",
    "PRIOR_PERIOD": "the one before it",
    "FREQUENCY": "as this release reports",
    "PERIOD_EXAMPLE": ("period not specified: using the latest populated "
                       "period against the one before it"),
    "MAPPED_TERM_EXAMPLE": "EAD by segment for the latest period",
}


def vocabulary(catalog: Any = None) -> dict[str, str]:
    """The period words for THIS book, for a prompt or a tool schema.

    The defect this exists for
    --------------------------
    The analyst instruction and EVERY tool schema hard-coded a quarterly
    worked example -- "period not specified: using the latest populated
    quarter 2026Q2 against 2026Q1" -- and the execution schema named its
    field `reporting_quarters`. A live Corporate run against the MONTHLY book
    was therefore handed a catalogue that said monthly and a contract that
    said quarterly, and it believed the contract, because the contract is the
    thing it has to fill in. It replied "this book is recorded quarterly, not
    monthly" and offered 2026Q2.

    So the vocabulary comes from the book. There is no default calendar
    anywhere in the prompt or the schema.
    """
    if catalog is None:
        return dict(NEUTRAL_VOCABULARY)
    noun = period_noun(catalog)
    slots = populated_periods(catalog)
    latest = slots[-1] if slots else ""
    prior = slots[-2] if len(slots) >= 2 else ""
    column = _period_column(catalog)
    segment = ""
    for mapping in measures(catalog):
        if mapping["term"] == "segment":
            segment = mapping["field"]
            break
    return {
        "PERIOD": noun,
        "PERIODS": f"{noun}s",
        "PERIOD_COLUMN": column,
        "PERIOD_FIELD": f"reporting_{noun}s",
        "LATEST_PERIOD": latest or f"the latest populated {noun}",
        "PRIOR_PERIOD": prior or f"the {noun} before it",
        "FREQUENCY": frequency(catalog),
        "PERIOD_EXAMPLE": (
            f"period not specified: using the latest populated {noun} "
            f"{latest} against {prior}" if latest and prior else
            f"period not specified: using the latest populated {noun}"),
        "MAPPED_TERM_EXAMPLE": (
            f"EAD by {segment or 'segment'} for the latest {noun}"),
    }


def substitute(text: str, catalog: Any = None) -> str:
    """Replace every `{{TOKEN}}` with this book's word for it."""
    words = vocabulary(catalog)
    for token, value in words.items():
        text = text.replace("{{" + token + "}}", str(value))
    return text


def period_phrases(question: str, catalog: Any = None) -> list[str]:
    """Which period phrases the question actually used. Deterministic."""
    text = re.sub(r"\s+", " ", (question or "").lower())
    table = dict(_COMMON_PERIOD_PHRASES)
    table.update(_PERIOD_PHRASES.get(frequency(catalog), {}))
    found: list[str] = []
    for phrase, kind in table.items():
        if phrase in text and kind not in found:
            found.append(kind)
    return found


def ambiguous_terms_in(question: str,
                       catalog: Any = None) -> list[dict[str, Any]]:
    """Terms that genuinely need a question, if the user used one of them.

    "Exposure at default" contains the word "exposure" and is NOT ambiguous,
    so the longer canonical term is checked first and wins.
    """
    text = re.sub(r"\s+", " ", (question or "").lower())
    canonical = [term for term, *_ in measure_table(catalog) if term in text]
    out = []
    for term, options in ambiguous_terms(catalog).items():
        if not re.search(rf"\b{re.escape(term)}\b", text):
            continue
        if any(term in longer and longer != term for longer in canonical):
            continue
        out.append({"term": term, "candidate_fields": list(options)})
    return out


#: Terms every governed analytical question in either book leans on, over
#: and above the measures the catalogue maps. Matched as words, so "stage"
#: does not match "backstage".
_STRUCTURAL_TERMS: dict[str, tuple[str, ...]] = {
    "stage": ("stage", "stage 1", "stage 2", "stage 3", "staging"),
    "sector": ("sector", "industry"),
    "borrower": ("borrower", "obligor", "counterparty", "name"),
    "product": ("product",),
    "period": ("quarter", "month", "period", "latest", "this", "current"),
}


#: Spelling pairs the catalogue uses one side of and a reader uses either.
#: Not a stemmer and not a synonym list: two spellings of one word.
_SPELLINGS = (("behaviour", "behavior"), ("utilisation", "utilization"),
              ("recognised", "recognized"), ("analyse", "analyze"))


def _variants(phrase: str) -> list[str]:
    out = {phrase}
    for british, american in _SPELLINGS:
        for form in list(out):
            if british in form:
                out.add(form.replace(british, american))
            if american in form:
                out.add(form.replace(american, british))
    # "covenants" is the term "covenant"; "score bands" is "score band".
    # An optional plural, matched on the last word only, is the whole of the
    # morphology here -- a reader writes "which borrowers", the catalogue
    # says "borrower", and a check that called those different terms would
    # send a governed question round the catalogue for a word it already
    # holds.
    return sorted(out)


def _mentions(text: str, phrase: str) -> bool:
    for variant in _variants(phrase):
        pattern = (rf"(?<![a-z0-9]){re.escape(variant)}(?:e?s)?"
                   rf"(?![a-z0-9])")
        if re.search(pattern, text):
            return True
    return False


def readiness(catalog: Any, question: str, *,
              seed_packet: dict[str, Any] | None = None,
              value_resolution: dict[str, Any] | None = None,
              already_asked: Sequence[str] = ()) -> dict[str, Any]:
    """Whether the governed metadata already in this packet is enough.

    §7, §8. A DETERMINISTIC check, computed by the server before any model
    call, that says one thing: the facts needed to author a query for this
    question are already in front of you, or they are not.

    It does not choose the analysis. It names no aggregation, no filter, no
    comparison and no method -- which measure to report and how to compute
    it stay entirely the analyst's. What it removes is the reason a run had
    to go and look: a seeded Construction thread read the catalogue twice
    and died with CALL_LIMIT having been handed, in its own opening packet,
    the relation, the column, the segment value and both periods.

    `sufficient` is true when every governed term this question names is
    already resolved here. It is a statement about METADATA COVERAGE and
    nothing else, and `inspect_catalog` remains available either way.
    """
    text = " ".join(str(question or "").lower().split())
    # WHAT A FOLLOW-UP INHERITS FROM THE QUESTIONS BEFORE IT.
    #
    # `already_asked` is the reader's own earlier wording in this thread.
    # Measure matching runs over it too, because a thread that has said
    # "exposure at default" has named the measure and a follow-up that says
    # "just the top five by that measure" is not a question with no measure
    # in it -- it is the same measure, referred to.
    #
    # Without this, M06 turn 3 matched nothing, came back `sufficient:
    # false`, and was REQUIRED to call inspect_catalog before it could
    # author a query built entirely from terms the thread had already
    # resolved. One generation, spent by the server, on a turn that needed
    # no metadata.
    #
    # Deliberately asymmetric, and this is the whole safety of it: what has
    # been NAMED accumulates across the thread, but what is UNDECIDED is
    # read off the CURRENT question alone. Nothing a previous turn said can
    # settle a reading this one leaves open, and `sufficient` still requires
    # `not unmapped and not open_values`. It is also the reader's words
    # only -- never the analyst's declared mappings -- so a run cannot widen
    # its own surface by asserting it understood something.
    history = " ".join(str(q or "").lower() for q in already_asked)
    searchable = " ".join(x for x in (text, " ".join(history.split())) if x)
    measures_here = field_packet(catalog)
    matched: list[dict[str, str]] = []
    for entry in measures_here:
        names = [str(entry.get("term") or "")]
        names += [str(a) for a in (entry.get("also_known_as") or [])]
        hit = next((n for n in names if n and _mentions(searchable, n)), "")
        if hit:
            matched.append({"term": str(entry.get("term") or ""),
                            "matched_on": hit,
                            "field_id": str(entry.get("field_id") or ""),
                            "relation": str(entry.get("relation") or ""),
                            "from": ("this question" if _mentions(text, hit)
                                     else "earlier in this thread")})
    structural = sorted({name for name, phrases in _STRUCTURAL_TERMS.items()
                         if any(_mentions(text, w) for w in phrases)})

    unmapped = sorted(ambiguous_terms_in(question, catalog),
                      key=lambda t: t["term"])
    # `context._value_resolution` names these `recognised` and
    # `needs_a_question`; the shorter names are accepted too so this can be
    # called with either. A key that matches neither reads as "no values",
    # which is the safe reading: it can only make the check stricter.
    values = value_resolution or {}
    resolved_values = list(values.get("recognised")
                           or values.get("resolved") or [])
    open_values = list(values.get("needs_a_question")
                       or values.get("ambiguous") or [])

    seeded = bool(seed_packet)
    # A seeded thread is sufficient when the packet carries the four things
    # a query needs and could not otherwise know: which relation, which
    # period column, which period, and which value to filter on.
    seed_has = [key for key in ("relation", "period_column",
                                "reporting_period", "subject")
                if (seed_packet or {}).get(key)]
    seed_complete = seeded and len(seed_has) == 4

    # A governed VALUE is metadata too. "Why is risk building in Real
    # Estate?" names no measure at all -- it names a value of `sector`,
    # already resolved against the ones this release holds, which tells the
    # run the column and the filter. Requiring a measure as well would send
    # that question to the catalogue for something it was handed.
    sufficient = bool((seed_complete or matched or resolved_values)
                      and not unmapped and not open_values)
    body: dict[str, Any] = {
        "checked_by": "server, deterministically, before any model call",
        "sufficient": sufficient,
        "governed_measures_already_resolved": matched[:12],
        "structural_terms_recognised": structural,
        "periods_resolved": {
            "reporting": (seed_packet or {}).get("reporting_period", "")
            or (populated_periods(catalog) or [""])[-1],
            "comparison": (seed_packet or {}).get("comparison_period", ""),
            "period_column": _period_column(catalog),
        },
        "values_resolved": resolved_values,
    }
    if seeded:
        body["seed"] = {"packet_present": True,
                        "carries": seed_has,
                        "missing": [k for k in ("relation", "period_column",
                                                "reporting_period", "subject")
                                    if k not in seed_has]}
    if unmapped:
        body["terms_still_needing_a_decision"] = unmapped
    if open_values:
        body["values_still_ambiguous"] = open_values
    # WHAT THE SERVER KNOWS ABOUT WHETHER A QUESTION BACK IS EARNED.
    #
    # The clarification gate (`clarify_gate`) reads the same three lists, so
    # the analyst is shown exactly what the gate will weigh. When all three
    # are empty and the packet is sufficient, nothing here is the reader's
    # choice to make, and the run that asked anyway was only being nervous.
    methods = ambiguous_methods_in(question)
    # Only the non-empty entries are carried: this rides on every action
    # turn, whose size is bounded, and an empty list says nothing.
    basis = {key: value for key, value in (
        ("terms", [t.get("term") for t in unmapped]),
        ("values", open_values),
        ("methods", [m["method"] for m in methods])) if value}
    if basis:
        body["clarification_basis"] = basis
    if sufficient and not basis and not non_latin(question):
        body["clarification_not_needed"] = (
            "Every term maps: record choices in resolved_assumptions; "
            "'A and B' means both; a driver question runs the usual "
            "breakdown and states it.")
    body["normal_first_action"] = ("execute_analysis" if sufficient
                                   else "inspect_catalog")
    body["note"] = (
        "Every field fact needed to author this query is already in this "
        "packet, so the normal first action is execute_analysis. This says "
        "nothing about WHICH analysis: the measure, the aggregation, the "
        "comparison and the method are yours. inspect_catalog remains "
        "available if the question turns on a column that is genuinely not "
        "here -- name the field ids when you call it."
        if sufficient else
        "Something this question names is not resolved here, so a metadata "
        "lookup is reasonable before authoring the query. Ask for exactly "
        "the field ids you are missing.")
    # AND WHAT A CATALOGUE READ CANNOT FIX.
    #
    # The note above offers one remedy -- read the catalogue -- and for a
    # missing field fact that is the right one. For a term under
    # `terms_still_needing_a_decision` it is not a remedy at all: the
    # candidates were read FROM the catalogue, so reading it again returns
    # them unchanged. What is missing is not a fact about the book. It is
    # the reader's choice, and only the reader has it.
    if unmapped:
        body["what_the_catalogue_cannot_settle"] = (
            "Each term above maps to several materially different columns, "
            "read out of the catalogue -- so inspect_catalog returns the "
            "same ones. Unless this question picks one, or invokes a policy "
            "test that picks one, the choice is the reader's.")
    return body


def block(catalog: Any) -> dict[str, Any]:
    """The semantics block carried in the starting context."""
    return {
        "canonical_measures": field_packet(catalog),
        "periods": periods(catalog),
        "domain_id": domain_of(catalog),
        "terms_needing_a_question": {
            term: {"candidate_fields": list(options),
                   "why": ("The catalogue records all of these and they are "
                           "materially different figures. 'Exposure at "
                           "default' is NOT this case: it is EAD.")}
            for term, options in ambiguous_terms(catalog).items()},
        # THE TWO KEYS ABOVE ARE OPPOSITES AND THIS SENTENCE USED TO TREAT
        # THEM ALIKE.
        #
        # It opened "These are resolutions, not assumptions to ask about.
        # Declare them in canonical_mappings or resolved_assumptions and
        # proceed" -- written about `canonical_measures`, but standing as
        # the how_to_use for the whole block, `terms_needing_a_question`
        # included. So the one key whose entire purpose is to say "this word
        # needs a question" was introduced by a sentence telling the analyst
        # not to ask one, and to write it into `resolved_assumptions` and
        # carry on. The live M06 turn 1 did exactly that, in those words.
        "how_to_use_canonical_measures": (
            "These are resolutions, not assumptions to ask about. Declare "
            "them in canonical_mappings or resolved_assumptions and proceed. "
            "`canonical_measures` carries the relation, column, type, unit, "
            "grain, period column and join key for each mapped term: a "
            "question that uses only these terms can go straight to "
            "execute_analysis. Call inspect_catalog for a fact that is "
            "genuinely missing from here, naming the field ids you need."),
        "how_to_use_terms_needing_a_question": (
            "These are the opposite: several materially different columns "
            "for one word, and nothing in the book saying which was meant. "
            "inspect_catalog returns the same candidates -- it is where "
            "they came from. Unless the question itself picks one, or "
            "invokes a policy test that picks one, the choice is the "
            "reader's and this is a blocking_ambiguity: ask, with the "
            "candidates as clarification_options. Running one and saying "
            "which you ran is making the choice, not declaring it."),
    }


__all__ = ["AMBIGUOUS_TERMS", "ambiguous_methods_in", "MAX_SEED_FIELDS", "SEED_FIELDS",
           "SEED_FIELDS_BY_DOMAIN", "ambiguous_terms", "ambiguous_terms_in", "non_latin",
           "block", "domain_of", "readiness", "field_packet", "frequency", "measure_table",
           "measures", "period_noun", "period_phrases", "periods",
           "NEUTRAL_VOCABULARY", "VOCABULARY_TOKENS", "populated_periods",
           "populated_quarters", "seed_field_packet", "substitute",
           "vocabulary"]
