"""
What each book contains. One registry, two domains, no shared assumptions.

Why this exists rather than reusing V3's
----------------------------------------
V3's catalogue is a module of constants: `QUERYABLE_RELATIONS` and a field
registry hard-bound to the corporate book, reached through static methods. It
is correct for what it is and V4 used it unchanged for a corporate-only
runtime. It cannot describe a second book, and teaching it to would mean
editing V3 -- which is not on the table and would not be right anyway: a
retail account is not a corporate facility with different words on it.

So the schema is DATA here. A relation is a name, a grain, a period column
and a list of fields; a field is a name, a type, a unit and a sentence saying
what it means. Both books are described the same way and neither can see the
other's entries, because `RELATIONS` is keyed by domain and every lookup
takes the domain as its first argument.

Units are the display policy's vocabulary
-----------------------------------------
`unit` is not decoration. `display.classify` reads it to decide that an
amount shows no decimals and a probability shows two, so `rcy` and
`probability_0_1` here are the same words that module already understands.
A field whose unit is spelled inventively is a field whose figures get
published wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import domains as dom


class UnknownRelation(LookupError):
    """A relation this domain does not have. Named with what it does have."""


class UnknownField(LookupError):
    """A column this relation does not define."""


#: EVERY UNIT THIS CATALOGUE USES GETS A STATED VERDICT.
#:
#: `Field.additive` used to name four units on each side and return
#: "not_additive" for everything else, so a unit it had never heard of was
#: indistinguishable from one deliberately judged non-additive. Three of the
#: eleven units actually in use reached their answer that way, and one of
#: them was wrong: `notches` is a signed count of rating grades, `display`
#: already classes it with `count`, and published prose already totals it
#: ("a net movement of +7 notches"), yet the catalogue said it could not be
#: added. `test_field_units.py` asserts this pair covers the whole
#: catalogue, so the next inventively-spelled unit fails a test instead of
#: quietly becoming something nobody may sum.
ADDITIVE_UNITS: frozenset[str] = frozenset({
    "rcy",        # an amount in the release's reporting currency
    "count",      # a number of things
    "days",       # already additive before this; left as it was
    "months",     # likewise
    "notches",    # rating grades moved -- signed, and summed in reports
})

#: Stated, not inferred. `''` is every string and categorical column: a
#: sector name is not a quantity. `percentage points` is a difference of two
#: percentages and shares their arithmetic. `years` is a tenor -- averaged
#: across facilities, never totalled.
NOT_ADDITIVE_UNITS: frozenset[str] = frozenset({
    "", "percent", "percentage points", "probability_0_1", "fraction_0_1",
    "fraction", "share", "rate_0_1", "percentage", "pct", "times", "ratio",
    "multiple", "x", "index", "rank", "years",
})


@dataclass(frozen=True)
class Field:
    name: str
    dtype: str
    unit: str
    description: str
    group: str = ""
    #: How this column may be added up. A share cannot be summed across
    #: segments and a stage cannot be averaged, and a tool that offers either
    #: is offering a wrong number.
    aggregation: str = ""
    #: What a person calls this column. Authored where the column's own name
    #: does not read as English -- `ead_sar_mn` is "Exposure at default",
    #: `score_band` is "Behavioural score band" -- and derived from the name
    #: where it does. A schema browser that shows only `pd_ttc_12m` is a
    #: schema browser for the person who already knows the schema.
    business_label: str = ""

    @property
    def definition(self) -> str:
        """The sentence a catalogue consumer reads. Same text, its name."""
        return self.description

    @property
    def label(self) -> str:
        if self.business_label:
            return self.business_label
        return self.name.replace("_", " ").strip().capitalize()

    @property
    def additive(self) -> str:
        """Whether this column's values may be added up.

        Read from the unit, with an author's explicit `aggregation` winning.
        Both verdicts are STATED above rather than one being a fallthrough,
        so "nobody has classified this unit" and "this unit may not be
        summed" stop being the same answer.
        """
        if self.aggregation:
            return self.aggregation
        if self.unit in ADDITIVE_UNITS:
            return "additive"
        # Unstated units land here with the safe verdict. `test_field_units`
        # is what stops one staying unstated.
        return "not_additive"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "type": self.dtype,
                "unit": self.unit, "description": self.description,
                "group": self.group, "aggregation": self.additive}


@dataclass(frozen=True)
class Relation:
    name: str
    grain: str
    period_column: str
    key_columns: tuple[str, ...]
    description: str
    fields: tuple[Field, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def field(self, column: str) -> Field:
        wanted = str(column or "").strip()
        for spec in self.fields:
            if spec.name == wanted:
                return spec
        raise UnknownField(
            f"Column {column!r} is not defined in {self.name}. It holds: "
            f"{', '.join(self.columns)}.")

    def to_dict(self) -> dict[str, Any]:
        return {"relation": self.name, "grain": self.grain,
                "period_column": self.period_column,
                "key_columns": list(self.key_columns),
                "description": self.description,
                "fields": [f.to_dict() for f in self.fields]}


def field_from_dict(payload: dict[str, Any]) -> Field:
    """Rebuild a field from the form a RELEASE published it in.

    The inverse of `Field.to_dict()`, and the reason a published release can
    describe itself.

    `to_dict` writes the RESOLVED `label` and `aggregation`. Both are
    properties over a raw slot -- `label` falls back to the column name
    tidied up, `additive` falls back to the unit -- so a round trip has to
    decide what to do with a resolved value whose origin it cannot see. The
    two are treated differently, because the two raw slots are read
    differently:

    `label` goes back verbatim. Nothing reads `business_label` except
    `label` itself, so storing the published label reproduces exactly what
    the release displayed, which is what a historical release is for. It is
    optional: the three oldest manifests predate the key, and a release that
    recorded none gets the derived label -- again what it was showing.

    `aggregation` goes back only where it OVERRULES the unit. The raw slot
    is a claim that an author overruled the unit, and `semantics.py`
    publishes it to the analyst on that basis; filling it with a verdict the
    unit already implies states something nobody said. It also costs: doing
    that put a redundant `aggregation` on all thirty canonical measures and
    994 bytes on every action request, over the payload bound.

    Either way the rebuilt field renders identically -- same `label`, same
    `additive` -- which is the property that matters for reading history.
    """
    name = str(payload["name"])
    unit = str(payload.get("unit") or "")
    published_aggregation = str(payload.get("aggregation") or "")
    derived_aggregation = ("additive" if unit in ADDITIVE_UNITS
                           else "not_additive")
    return Field(
        name=name,
        dtype=str(payload.get("type") or payload.get("dtype") or ""),
        unit=unit,
        description=str(payload.get("description")
                        or payload.get("definition") or ""),
        group=str(payload.get("group") or ""),
        aggregation=("" if published_aggregation == derived_aggregation
                     else published_aggregation),
        business_label=str(payload.get("label") or ""))


def relation_from_dict(payload: dict[str, Any]) -> Relation:
    """Rebuild a relation from the form a RELEASE published it in.

    `Relation.to_dict()` records all six of its fields, so this round trip
    is lossless. The derived `columns` property and `field()` lookup come
    back with the rebuilt field list.
    """
    return Relation(
        name=str(payload.get("relation") or payload.get("name") or ""),
        grain=str(payload.get("grain") or ""),
        period_column=str(payload.get("period_column") or ""),
        key_columns=tuple(str(c) for c in (payload.get("key_columns") or ())),
        description=str(payload.get("description") or ""),
        fields=tuple(field_from_dict(f) for f in (payload.get("fields") or ())))


def _f(name, dtype, unit, description, group="", label="", aggregation=""):
    return Field(name, dtype, unit, description, group, aggregation, label)


#: Columns every relation in every domain carries. They are the isolation:
#: the session filters on them when it materializes the table, so a query
#: cannot reach another tenant, release or domain by forgetting a WHERE.
GOVERNANCE_FIELDS: tuple[Field, ...] = (
    _f("tenant_id", "string", "", "The tenant this row belongs to.",
       "Governance"),
    _f("dataset_release_id", "string", "",
       "The immutable release this row was published in.", "Governance"),
    _f("domain_id", "string", "",
       "The analytical domain this row belongs to.", "Governance"),
    _f("reporting_currency", "string", "",
       "The currency every amount in this row is denominated in.",
       "Governance"),
)

# ---- corporate ---------------------------------------------------------

#: The CORPORATE book reports quarterly and the RETAIL book monthly.
#:
#: Deliberate, and not a detail. A corporate credit file is reviewed on the
#: financial, rating and IFRS 9 cycle its borrowers actually report on, and
#: that cycle is quarterly; retail monitoring is behavioural and monthly.
#: Every relation below names its own period column, and every consumer reads
#: `Relation.period_column` rather than assuming one.
PERIOD_COLUMNS: dict[str, str] = {
    dom.CORPORATE: "reporting_quarter",
    dom.RETAIL: "reporting_month",
}

FREQUENCIES: dict[str, str] = {
    dom.CORPORATE: "quarterly",
    dom.RETAIL: "monthly",
}


def period_column(domain_id: str) -> str:
    """The column this domain records its reporting period in."""
    return PERIOD_COLUMNS[dom.parse(domain_id)]


def frequency(domain_id: str) -> str:
    return FREQUENCIES[dom.parse(domain_id)]


#: What a period IS in each book, for the words a payload writes.
PERIOD_NOUNS: dict[str, str] = {
    dom.CORPORATE: "quarter",
    dom.RETAIL: "month",
}


def period_noun(domain_id: str) -> str:
    return PERIOD_NOUNS[dom.parse(domain_id)]


def period_keys(domain_id: str, reporting: str,
                comparison: str = "") -> dict[str, str]:
    """The period fields any payload from this book carries.

    `reporting_period` is the truth and `period_noun` says what kind of
    period it is. The older `reporting_quarter` / `reporting_month` pair is
    still served for readers that predate the two calendars, but ONLY the
    one this book actually reports in is filled: a payload that fills both
    says the Corporate book has months, and a reader that indexes the wrong
    key then gets a value that looks right. An empty string is a visible
    mistake; a quarter under a month's name is an invisible one.

    This lives in `schema` because the calendar does: one place decides
    which column a book keeps its periods in, what to call a period, and
    what keys a payload spells it under.
    """
    domain_id = dom.parse(domain_id)
    noun = PERIOD_NOUNS[domain_id]
    body = {
        "reporting_period": reporting,
        "comparison_period": comparison,
        "period_noun": noun,
        "comparison_basis": f"previous {noun}",
        "reporting_quarter": "", "comparison_quarter": "",
        "reporting_month": "", "comparison_month": "",
    }
    body[f"reporting_{noun}"] = reporting
    body[f"comparison_{noun}"] = comparison
    return body


_CORP_BORROWER = Relation(
    name="corp_borrower_quarter",
    grain="one row per borrower per reporting quarter",
    period_column="reporting_quarter",
    key_columns=("borrower_id", "reporting_quarter"),
    description=("The obligor as at a quarter end: who it is, how it is "
                 "segmented, how it is rated and what its financials say."),
    fields=GOVERNANCE_FIELDS + (
        _f("borrower_id", "string", "", "Stable obligor identifier.",
           "Identity", label="Borrower id"),
        _f("borrower_name", "string", "", "Fictional obligor name.",
           "Identity", label="Borrower"),
        _f("group_id", "string", "", "Parent group identifier.", "Identity",
           label="Group id"),
        _f("group_name", "string", "", "Fictional parent group name.",
           "Identity", label="Parent group"),
        _f("reporting_quarter", "string", "",
           "Reporting quarter as YYYYQn.", "Period",
           label="Reporting quarter"),
        _f("sector", "string", "", "Economic sector.", "Segmentation",
           label="Sector"),
        _f("sub_sector", "string", "", "Sector sub-classification.",
           "Segmentation", label="Sub-sector"),
        _f("region", "string", "", "Saudi region of the primary operation.",
           "Segmentation", label="Region"),
        _f("relationship_tier", "string", "",
           "Relationship management tier: Strategic, Core or "
           "Transactional.", "Segmentation", label="Relationship segment"),
        _f("rating_current", "string", "",
           "Internal rating this quarter, on the 19-grade scale.", "Rating",
           label="Current rating"),
        _f("rating_previous", "string", "",
           "Internal rating the previous quarter.", "Rating",
           label="Prior rating"),
        _f("rating_at_origination", "string", "",
           "The internal rating the exposure was written at.", "Rating",
           label="Rating at origination"),
        _f("rating_notches_moved", "integer", "notches",
           "Notches moved since last quarter. Negative is a downgrade.",
           "Rating", label="Notches moved this quarter"),
        _f("rating_notches_from_origination", "integer", "notches",
           "Notches moved since origination. Negative is a downgrade.",
           "Rating", label="Notches moved since origination"),
        _f("rating_migration", "string", "",
           "UPGRADE, STABLE or DOWNGRADE this quarter.", "Rating",
           label="Rating migration"),
        _f("rating_outlook", "string", "",
           "Positive, Stable or Negative.", "Rating", label="Outlook"),
        _f("rating_driver", "string", "",
           "The variable that moved the rating most this quarter: "
           "Leverage, Debt service, Profitability, Liquidity, Qualitative "
           "or None.", "Rating", label="Primary rating driver"),
        _f("pd_ttc_12m", "float", "probability_0_1",
           "Through-the-cycle 12-month probability of default implied by "
           "the rating.", "Rating", label="Through-the-cycle 12-month PD"),
        _f("revenue_sar_mn", "float", "rcy", "Trailing twelve-month revenue.",
           "Financials", label="Revenue"),
        _f("ebitda_sar_mn", "float", "rcy",
           "Trailing twelve-month EBITDA.", "Financials", label="EBITDA"),
        _f("ebit_sar_mn", "float", "rcy",
           "Trailing twelve-month operating profit.", "Financials",
           label="EBIT"),
        _f("operating_cash_flow_sar_mn", "float", "rcy",
           "Trailing twelve-month cash generated by operations.",
           "Financials", label="Operating cash flow"),
        _f("total_debt_sar_mn", "float", "rcy", "Total debt outstanding.",
           "Financials", label="Total debt"),
        _f("cash_sar_mn", "float", "rcy", "Cash and equivalents.",
           "Financials", label="Cash"),
        _f("net_debt_sar_mn", "float", "rcy", "Total debt less cash.",
           "Financials", label="Net debt"),
        _f("leverage_x", "float", "times", "Net debt to EBITDA.", "Ratios",
           label="Leverage"),
        _f("dscr_x", "float", "times", "Debt service coverage ratio.",
           "Ratios", label="DSCR"),
        _f("interest_cover_x", "float", "times",
           "EBITDA to interest expense.", "Ratios", label="Interest cover"),
        _f("current_ratio_x", "float", "times",
           "Current assets to current liabilities.", "Ratios",
           label="Current ratio"),
        _f("quick_ratio_x", "float", "times",
           "Liquid assets to current liabilities.", "Ratios",
           label="Quick ratio"),
        _f("ebitda_margin_pct", "float", "percent",
           "EBITDA as a percentage of revenue.", "Ratios",
           label="EBITDA margin"),
        _f("return_on_assets_pct", "float", "percent",
           "Operating profit as a percentage of total assets.", "Ratios",
           label="Return on assets"),
        _f("cash_conversion_pct", "float", "percent",
           "Operating cash flow as a percentage of EBITDA.", "Ratios",
           label="Cash conversion"),
        _f("qualitative_score", "float", "index",
           "Analyst qualitative assessment, 0 (weak) to 100 (strong).",
           "Qualitative", label="Qualitative score"),
        _f("watchlist_flag", "integer", "count",
           "1 when the obligor is on the credit watch list this quarter.",
           "Credit status", label="On watch list"),
        _f("watchlist_reason", "string", "",
           "Why it is on the watch list, empty when it is not.",
           "Credit status", label="Watch-list reason"),
        _f("restructured_flag", "integer", "count",
           "1 when the obligor's facilities have been restructured.",
           "Credit status", label="Restructured"),
        _f("quarters_on_watchlist", "integer", "count",
           "Consecutive quarters on the watch list.", "Credit status",
           label="Quarters on watch list"),
    ))

_CORP_FACILITY = Relation(
    name="corp_facility_quarter",
    grain="one row per facility per reporting quarter",
    period_column="reporting_quarter",
    key_columns=("facility_id", "reporting_quarter"),
    description=("The exposure as at a quarter end: limits, drawings, "
                 "IFRS 9 stage, PD, LGD, EAD and ECL."),
    fields=GOVERNANCE_FIELDS + (
        _f("facility_id", "string", "", "Stable facility identifier.",
           "Identity", label="Facility id"),
        _f("borrower_id", "string", "", "The obligor this facility is to.",
           "Identity", label="Borrower id"),
        _f("borrower_name", "string", "", "The obligor's name, carried so a "
           "facility question does not need the borrower relation.",
           "Identity", label="Borrower"),
        _f("reporting_quarter", "string", "",
           "Reporting quarter as YYYYQn.", "Period",
           label="Reporting quarter"),
        _f("product_type", "string", "",
           "The lending product: term_loan, working_capital, "
           "revolving_credit, trade_finance, project_finance, overdraft or "
           "asset_finance. Also called the facility type.", "Segmentation",
           label="Product type"),
        _f("facility_class", "string", "",
           "funded or non_funded: whether drawing it puts cash out.",
           "Segmentation", label="Facility class"),
        _f("sector", "string", "", "The obligor's sector, carried for "
           "single-relation sector analysis.", "Segmentation",
           label="Sector"),
        _f("sub_sector", "string", "", "The obligor's sub-sector.",
           "Segmentation", label="Sub-sector"),
        _f("region", "string", "", "The obligor's region.", "Segmentation",
           label="Region"),
        _f("relationship_tier", "string", "",
           "The obligor's relationship segment.", "Segmentation",
           label="Relationship segment"),
        _f("limit_sar_mn", "float", "rcy", "Sanctioned limit.", "Exposure",
           label="Limit"),
        _f("drawn_sar_mn", "float", "rcy", "Drawn balance.", "Exposure",
           label="Drawn"),
        _f("undrawn_sar_mn", "float", "rcy", "Undrawn commitment.",
           "Exposure", label="Undrawn"),
        _f("utilisation_pct", "float", "percent",
           "Drawn as a percentage of limit.", "Exposure",
           label="Utilisation"),
        _f("ead_sar_mn", "float", "rcy",
           "Exposure at default: drawn plus the credit-converted undrawn.",
           "Exposure", label="Exposure at default"),
        _f("stage", "integer", "count", "IFRS 9 stage: 1, 2 or 3.", "IFRS 9",
           label="IFRS 9 stage"),
        _f("sicr_flag", "integer", "count",
           "1 when a significant increase in credit risk is recognised.",
           "IFRS 9", label="SICR"),
        _f("default_flag", "integer", "count",
           "1 when the facility is in default.", "IFRS 9", label="Default"),
        _f("dpd_days", "integer", "days", "Days past due.", "Delinquency",
           label="Days past due"),
        _f("pd_pit_12m", "float", "probability_0_1",
           "Point-in-time 12-month probability of default.", "IFRS 9",
           label="Point-in-time 12-month PD"),
        _f("pd_lifetime", "float", "probability_0_1",
           "Lifetime probability of default.", "IFRS 9",
           label="Lifetime PD"),
        _f("lgd_pct", "float", "percent", "Loss given default.", "IFRS 9",
           label="LGD"),
        _f("ecl_12m_sar_mn", "float", "rcy", "Twelve-month expected credit "
           "loss.", "IFRS 9", label="12-month ECL"),
        _f("ecl_lifetime_sar_mn", "float", "rcy",
           "Lifetime expected credit loss.", "IFRS 9", label="Lifetime ECL"),
        _f("ecl_sar_mn", "float", "rcy",
           "Recognised ECL: the 12-month figure in Stage 1 and the lifetime "
           "figure in Stages 2 and 3.", "IFRS 9", label="Recognised ECL"),
        _f("ecl_coverage_pct", "float", "percent",
           "Recognised ECL as a percentage of EAD.", "IFRS 9",
           label="ECL coverage"),
        _f("write_off_sar_mn", "float", "rcy",
           "Amount written off this quarter.", "IFRS 9", label="Write-off"),
        _f("recovery_sar_mn", "float", "rcy",
           "Amount recovered this quarter.", "IFRS 9", label="Recovery"),
        _f("cure_flag", "integer", "count",
           "1 when the facility cured out of default or delinquency this "
           "quarter.", "Delinquency", label="Cured this quarter"),
        _f("past_due_flag", "integer", "count",
           "1 when any amount is past due.", "Delinquency",
           label="Past due"),
        _f("quarters_in_stage", "integer", "count",
           "Consecutive quarters at the current stage.", "IFRS 9",
           label="Quarters in stage"),
        _f("origination_quarter", "string", "",
           "The quarter the facility was written, as YYYYQn.", "Vintage",
           label="Origination quarter"),
    ))

_CORP_COLLATERAL = Relation(
    name="corp_collateral_quarter",
    grain="one row per collateral item per facility per reporting quarter",
    period_column="reporting_quarter",
    key_columns=("collateral_id", "reporting_quarter"),
    description="Security held against a facility, and what it is worth.",
    fields=GOVERNANCE_FIELDS + (
        _f("collateral_id", "string", "", "Stable collateral identifier.",
           "Identity", label="Collateral id"),
        _f("facility_id", "string", "", "The facility this secures.",
           "Identity", label="Facility id"),
        _f("borrower_id", "string", "", "The obligor.", "Identity",
           label="Borrower id"),
        _f("reporting_quarter", "string", "",
           "Reporting quarter as YYYYQn.", "Period",
           label="Reporting quarter"),
        _f("collateral_type", "string", "",
           "Real Estate, Plant and Equipment, Receivables, Cash Deposit or "
           "Corporate Guarantee.", "Collateral", label="Collateral type"),
        _f("sector", "string", "", "The obligor's sector.", "Segmentation",
           label="Sector"),
        _f("market_value_sar_mn", "float", "rcy", "Appraised market value.",
           "Collateral", label="Market value"),
        _f("haircut_pct", "float", "percent",
           "Regulatory haircut applied to the market value.", "Collateral",
           label="Haircut"),
        _f("allocated_value_sar_mn", "float", "rcy",
           "Market value after haircut, allocated to this facility.",
           "Collateral", label="Allocated value"),
        _f("coverage_pct", "float", "percent",
           "Allocated value as a percentage of the facility's EAD.",
           "Collateral", label="Collateral coverage"),
        _f("ltv_pct", "float", "percent",
           "EAD as a percentage of market value.", "Collateral",
           label="Loan to value"),
        _f("valuation_age_quarters", "integer", "count",
           "Quarters since the last appraisal.", "Collateral",
           label="Quarters since valuation"),
    ))

_CORP_COVENANT = Relation(
    name="corp_covenant_quarter",
    grain="one row per covenant test per facility per reporting quarter",
    period_column="reporting_quarter",
    key_columns=("covenant_id", "reporting_quarter"),
    description="Covenant tests, their thresholds and what was observed.",
    fields=GOVERNANCE_FIELDS + (
        _f("covenant_id", "string", "", "Stable covenant identifier.",
           "Identity", label="Covenant id"),
        _f("facility_id", "string", "", "The facility this tests.",
           "Identity", label="Facility id"),
        _f("borrower_id", "string", "", "The obligor.", "Identity",
           label="Borrower id"),
        _f("reporting_quarter", "string", "",
           "Reporting quarter as YYYYQn.", "Period",
           label="Reporting quarter"),
        _f("sector", "string", "", "The obligor's sector.", "Segmentation",
           label="Sector"),
        _f("covenant_type", "string", "",
           "Leverage, DSCR, Interest Cover, Current Ratio or Minimum "
           "EBITDA.", "Covenants", label="Covenant type"),
        _f("threshold_value", "float", "times",
           "The level the covenant requires.", "Covenants",
           label="Threshold"),
        _f("observed_value", "float", "times",
           "The level actually observed this quarter.", "Covenants",
           label="Observed"),
        _f("headroom_pct", "float", "percent",
           "Headroom against the threshold. Negative is a breach.",
           "Covenants", label="Headroom"),
        _f("test_status", "string", "", "PASS, WATCH or BREACH.",
           "Covenants", label="Test status"),
        _f("breach_flag", "integer", "count", "1 when the test is breached.",
           "Covenants", label="Breached"),
        _f("waiver_flag", "integer", "count",
           "1 when a waiver has been granted for this breach.", "Covenants",
           label="Waived"),
        _f("waiver_status", "string", "",
           "GRANTED, REQUESTED or NONE.", "Covenants", label="Waiver status"),
        _f("waiver_quarter", "string", "",
           "The quarter the waiver was granted, empty when there is none.",
           "Covenants", label="Waiver quarter"),
        _f("consecutive_breaches", "integer", "count",
           "Consecutive quarters this test has been breached.", "Covenants",
           label="Consecutive breaches"),
    ))

# ---- retail ------------------------------------------------------------

_RETAIL_CUSTOMER = Relation(
    name="retail_customer_month",
    grain="one row per retail customer per reporting month",
    period_column="reporting_month",
    key_columns=("customer_id", "reporting_month"),
    description=("The retail customer as at a month end: segment, tenure, "
                 "behaviour score and their worst account position."),
    fields=GOVERNANCE_FIELDS + (
        _f("customer_id", "string", "", "Stable customer identifier.",
           "Identity", label="Customer id"),
        _f("reporting_month", "string", "", "Reporting month as YYYY-MM.",
           "Period"),
        _f("customer_segment", "string", "",
           "Mass, Affluent, Private or Payroll.", "Segmentation"),
        _f("employment_type", "string", "",
           "Salaried-Government, Salaried-Private, Self-employed or "
           "Non-salaried.", "Segmentation"),
        _f("region", "string", "", "Saudi region of residence.",
           "Segmentation"),
        _f("tenure_months", "integer", "months",
           "Months since the relationship opened.", "Segmentation"),
        _f("accounts_held", "integer", "count",
           "Number of open accounts this month.", "Segmentation"),
        _f("behaviour_score", "float", "index",
           "Behavioural score this month, 300 (weak) to 900 (strong).",
           "Behaviour score", label="Behavioural score"),
        _f("behaviour_score_previous", "float", "index",
           "Behavioural score the previous month.", "Behaviour score"),
        _f("behaviour_score_change", "float", "index",
           "Change since last month. Negative is deterioration.",
           "Behaviour score"),
        _f("score_band", "string", "",
           "Band of the current behavioural score: A (best) to E (worst). "
           "Also called the behavioural score band.",
           "Behaviour score", label="Behavioural score band"),
        _f("score_band_previous", "string", "",
           "Band of the previous month's behavioural score.",
           "Behaviour score", label="Prior behavioural score band"),
        _f("score_migration", "string", "",
           "IMPROVED, STABLE or DETERIORATED.", "Behaviour score",
           label="Behavioural score migration"),
        _f("total_ead_sar_mn", "float", "rcy",
           "Exposure at default across the customer's accounts.",
           "Exposure"),
        _f("total_ecl_sar_mn", "float", "rcy",
           "Recognised ECL across the customer's accounts.", "IFRS 9"),
        _f("worst_stage", "integer", "count",
           "The worst IFRS 9 stage across the customer's accounts.",
           "IFRS 9"),
        _f("worst_dpd_days", "integer", "days",
           "The worst days past due across the customer's accounts.",
           "Delinquency"),
    ))

_RETAIL_ACCOUNT = Relation(
    name="retail_account_month",
    grain="one row per retail account per reporting month",
    period_column="reporting_month",
    key_columns=("account_id", "reporting_month"),
    description=("The retail account as at a month end: product, balances, "
                 "IFRS 9 position and delinquency."),
    fields=GOVERNANCE_FIELDS + (
        _f("account_id", "string", "", "Stable account identifier.",
           "Identity", label="Account id"),
        _f("customer_id", "string", "", "The customer who holds it.",
           "Identity"),
        _f("reporting_month", "string", "", "Reporting month as YYYY-MM.",
           "Period"),
        _f("product", "string", "",
           "Mortgage, Personal Finance, Auto Finance, Credit Card or Buy "
           "Now Pay Later. Buy Now Pay Later was launched inside this "
           "window and has no rows before its first month.",
           "Product"),
        _f("sub_product", "string", "",
           "What the product was sold as: Fixed Rate or Variable Rate "
           "mortgages; Salary Advance, Consumer Durable or Debt "
           "Consolidation personal finance; New or Used vehicle finance; "
           "Classic, Gold or Signature cards; Instalment 3M or 6M BNPL.",
           "Product"),
        _f("secured_flag", "integer", "count",
           "1 when the product is secured.", "Product"),
        _f("origination_month", "string", "",
           "The month the account was opened, as YYYY-MM.", "Vintage"),
        _f("origination_channel", "string", "",
           "Where the account was written: Branch, Digital or Partner.",
           "Origination"),
        _f("vintage_year", "integer", "count",
           "The calendar year of origination.", "Vintage"),
        _f("months_on_book", "integer", "months",
           "Months since origination. The comparison a vintage analysis "
           "makes is at EQUAL months on book, not in calendar time.",
           "Vintage"),
        _f("customer_segment", "string", "", "The customer's segment.",
           "Segmentation"),
        _f("employment_type", "string", "",
           "How the customer is paid: Salaried-Government, "
           "Salaried-Private, Self-employed or Non-salaried. The strongest "
           "single lever in Saudi retail credit policy.", "Segmentation"),
        _f("region", "string", "", "The customer's region.", "Segmentation"),
        _f("limit_sar_mn", "float", "rcy",
           "Sanctioned limit or original advance.", "Exposure"),
        _f("balance_sar_mn", "float", "rcy", "Outstanding balance.",
           "Exposure"),
        _f("ead_sar_mn", "float", "rcy", "Exposure at default.", "Exposure",
           label="Exposure at default"),
        _f("utilisation_pct", "float", "percent",
           "Balance as a percentage of limit.", "Exposure"),
        _f("stage", "integer", "count", "IFRS 9 stage: 1, 2 or 3.", "IFRS 9"),
        _f("sicr_flag", "integer", "count",
           "1 when a significant increase in credit risk is recognised.",
           "IFRS 9"),
        _f("default_flag", "integer", "count",
           "1 when the account is in default.", "IFRS 9"),
        _f("dpd_days", "integer", "days", "Days past due.", "Delinquency",
           label="Days past due"),
        _f("delinquency_bucket", "string", "",
           "Current, 1-29, 30-59, 60-89 or 90+ days past due.",
           "Delinquency"),
        _f("delinquency_bucket_fine", "string", "",
           "The same arrears at collections grain: Current, 1-9, 10-19, "
           "20-29, 30-59, 60-89, 90-179 or 180+. Use it to say WHERE "
           "inside 1-29 a movement sits -- a book entering at 20-29 is a "
           "different problem from one entering at 1-9 -- and to separate "
           "90-179 from the 180+ tail.", "Delinquency"),
        _f("pd_pit_12m", "float", "probability_0_1",
           "Point-in-time 12-month probability of default.", "IFRS 9",
           label="Point-in-time 12-month PD"),
        _f("pd_lifetime", "float", "probability_0_1",
           "Lifetime probability of default.", "IFRS 9",
           label="Lifetime PD"),
        _f("lgd_pct", "float", "percent", "Loss given default.", "IFRS 9",
           label="LGD"),
        _f("ecl_12m_sar_mn", "float", "rcy",
           "Twelve-month expected credit loss.", "IFRS 9"),
        _f("ecl_lifetime_sar_mn", "float", "rcy",
           "Lifetime expected credit loss.", "IFRS 9"),
        _f("ecl_sar_mn", "float", "rcy",
           "Recognised ECL: the 12-month figure in Stage 1 and the lifetime "
           "figure in Stages 2 and 3.", "IFRS 9", label="Recognised ECL"),
        _f("write_off_sar_mn", "float", "rcy",
           "Amount written off this month.", "IFRS 9"),
        _f("recovery_sar_mn", "float", "rcy",
           "Amount recovered this month.", "IFRS 9"),
        _f("cure_flag", "integer", "count",
           "1 when the account cured from delinquency this month.",
           "Delinquency"),
        _f("behaviour_score", "float", "index",
           "The customer's behavioural score, carried for single-relation "
           "score analysis.", "Behaviour score"),
        _f("score_band", "string", "",
           "The customer's behavioural score band.", "Behaviour score",
           label="Behavioural score band"),
    ))

_RETAIL_BEHAVIOUR = Relation(
    name="retail_behaviour_month",
    grain="one row per retail account per reporting month",
    period_column="reporting_month",
    key_columns=("account_id", "reporting_month"),
    description=("The behavioural variables the score is built from: how the "
                 "account is actually being used and repaid."),
    fields=GOVERNANCE_FIELDS + (
        _f("account_id", "string", "", "The account.", "Identity"),
        _f("customer_id", "string", "", "The customer.", "Identity"),
        _f("reporting_month", "string", "", "Reporting month as YYYY-MM.",
           "Period"),
        _f("product", "string", "", "The account's product.", "Product"),
        _f("sub_product", "string", "", "The account's sub-product.",
           "Product"),
        _f("utilisation_pct", "float", "percent",
           "Balance as a percentage of limit.", "Behaviour variables"),
        _f("utilisation_change_pp", "float", "percentage points",
           "Change in utilisation since last month.",
           "Behaviour variables"),
        _f("payment_ratio_pct", "float", "percent",
           "Amount paid this month as a percentage of the amount due.",
           "Behaviour variables"),
        _f("missed_payments_12m", "integer", "count",
           "Payments missed in the last twelve months.",
           "Behaviour variables"),
        _f("delinquency_streak_months", "integer", "months",
           "Consecutive months past due.", "Behaviour variables"),
        _f("balance_growth_pct", "float", "percent",
           "Change in balance since last month.", "Behaviour variables"),
        _f("cash_advance_ratio_pct", "float", "percent",
           "Cash advances as a percentage of spend. Cards only; zero "
           "elsewhere.", "Behaviour variables"),
        _f("overlimit_flag", "integer", "count",
           "1 when the balance exceeded the limit this month.",
           "Behaviour variables"),
        _f("inflow_change_pct", "float", "percent",
           "Change in credited salary or other inflow since last month.",
           "Behaviour variables"),
        _f("bureau_inquiries_6m", "integer", "count",
           "Credit bureau inquiries in the last six months.",
           "Behaviour variables"),
        _f("repayment_behaviour_score", "float", "index",
           "Repayment component of the behavioural score, 0 to 100.",
           "Behaviour variables"),
    ))

_RETAIL_COLLATERAL = Relation(
    name="retail_collateral_month",
    grain="one row per secured retail account per reporting month",
    period_column="reporting_month",
    key_columns=("account_id", "reporting_month"),
    description=("Security on secured retail lending. Unsecured products do "
                 "not appear here at all rather than appearing with zeroes."),
    fields=GOVERNANCE_FIELDS + (
        _f("account_id", "string", "", "The account.", "Identity"),
        _f("customer_id", "string", "", "The customer.", "Identity"),
        _f("reporting_month", "string", "", "Reporting month as YYYY-MM.",
           "Period"),
        _f("product", "string", "", "Mortgage or Auto Finance.", "Product"),
        _f("collateral_type", "string", "", "Residential Property or "
           "Motor Vehicle.", "Collateral"),
        _f("collateral_value_sar_mn", "float", "rcy",
           "Appraised value of the security.", "Collateral"),
        _f("ltv_pct", "float", "percent",
           "Balance as a percentage of collateral value.", "Collateral",
           label="Loan to value"),
        _f("collateral_coverage_pct", "float", "percent",
           "Collateral value as a percentage of EAD.", "Collateral"),
        _f("valuation_age_months", "integer", "months",
           "Months since the last valuation.", "Collateral"),
    ))


RELATIONS: dict[str, tuple[Relation, ...]] = {
    dom.CORPORATE: (_CORP_BORROWER, _CORP_FACILITY, _CORP_COLLATERAL,
                    _CORP_COVENANT),
    dom.RETAIL: (_RETAIL_CUSTOMER, _RETAIL_ACCOUNT, _RETAIL_BEHAVIOUR,
                 _RETAIL_COLLATERAL),
}


def relations(domain_id: str) -> tuple[Relation, ...]:
    return RELATIONS[dom.parse(domain_id)]


def relation_names(domain_id: str) -> tuple[str, ...]:
    return tuple(r.name for r in relations(domain_id))


def relation(domain_id: str, name: str) -> Relation:
    wanted = str(name or "").strip().lower()
    for spec in relations(domain_id):
        if spec.name == wanted:
            return spec
    raise UnknownRelation(
        f"{name!r} is not a relation of the {dom.LABELS[dom.parse(domain_id)]} "
        f"domain. Its relations are: "
        f"{', '.join(relation_names(domain_id))}.")


def field(domain_id: str, relation_name: str, column: str) -> Field:
    return relation(domain_id, relation_name).field(column)


def domain_of_relation(name: str) -> str:
    """Which book owns this relation name. Used to refuse a cross-domain read.

    Relation names are deliberately prefixed per domain, so this is a lookup
    rather than a guess -- and a name belonging to neither book raises rather
    than defaulting to one.
    """
    wanted = str(name or "").strip().lower()
    for domain_id, specs in RELATIONS.items():
        if any(spec.name == wanted for spec in specs):
            return domain_id
    raise UnknownRelation(f"{name!r} is not a relation of any domain.")


__all__ = ["FREQUENCIES", "Field", "GOVERNANCE_FIELDS", "PERIOD_COLUMNS",
           "PERIOD_NOUNS", "RELATIONS", "Relation", "UnknownField",
           "UnknownRelation", "domain_of_relation", "field",
           "field_from_dict", "frequency", "relation_from_dict",
           "period_column", "period_keys", "period_noun", "relation",
           "relation_names", "relations"]
