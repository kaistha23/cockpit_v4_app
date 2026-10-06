"""
What deserves attention in a book, decided the same way every time.

Deterministic, and zero model calls
-----------------------------------
This runs when a landing page loads. A page that costs a generation to
render is a page nobody can leave open, and a ranking that changes between
two loads of the same data is a ranking nobody can act on. So every number
here comes out of SQL over the pinned release and the ordering is a pure
function of those numbers.

Why it is domain-aware rather than domain-agnostic
--------------------------------------------------
A corporate book is watched by SECTOR: a sector's Stage 2 migration, its ECL
movement, its rating drift, its covenant headroom. A retail book is watched
by PRODUCT, SCORE BAND, DELINQUENCY BUCKET and VINTAGE, because the
individual account only matters once one of those moves. Pretending the two
are the same shape -- "segments" with "metrics" -- would produce a retail
page that talks about sectors it does not have.

So each domain declares its own families. The RANKING is shared, because
"material and moving" means the same thing in both books; what is ranked is
not.

Materiality and movement, together
----------------------------------
A segment that doubled its ECL on two accounts is noise. A segment holding a
fifth of the book that moved two per cent is not. The score multiplies a
normalised movement by the segment's share of the book, so neither alone
gets to the top, and a segment too small to matter is dropped before scoring
rather than ranked and buried.
"""

from __future__ import annotations

import re
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import values as val_mod
from backend.cockpit_v4 import schema as schema_mod

TOP_N = 5

#: A segment holding less than this share of the book is not a finding about
#: the book. It is still visible in a query; it is not worth a card.
MIN_SHARE = 0.01

#: Below this, a movement is indistinguishable from rounding.
MIN_MOVEMENT = 0.005

#: How many cards one lens may take. A book that moves moves every slice of
#: a dimension with it, and five cards from one dimension is one finding
#: with five headlines.
MAX_PER_DIMENSION = 2


class AttentionUnavailable(RuntimeError):
    """The feed could not be computed. Said plainly, never faked as empty."""


@dataclass(frozen=True)
class Family:
    """One way of watching a book: what to group by, and what to measure."""

    key: str
    label: str
    dimension: str
    dimension_label: str
    relation: str
    measure: str
    measure_label: str
    kind: str
    unit: str
    #: SQL fragment producing the measure, grouped by `dimension`.
    expression: str
    #: A weight the whole family carries: a Stage 2 migration is a louder
    #: signal than a utilisation drift even at the same magnitude.
    weight: float = 1.0
    direction: str = "up"
    #: The counterparty behind one row of this relation, so a card can say
    #: how many of them the segment holds. A book that cannot say "this
    #: sector has eleven borrowers" cannot offer a drill-down worth taking.
    #: How big this segment IS, for materiality. Exposure at default in
    #: almost every case -- but a relation that does not carry EAD needs its
    #: own answer rather than a crash or a silent zero.
    size_expression: str = "SUM(ead_sar_mn)"
    #: An extra WHERE this family needs, for a family that watches a subset
    #: -- covenant breaches, say -- rather than the whole relation.
    filter_expression: str = ""

    @property
    def period_column(self) -> str:
        """The column this family's relation records its period in.

        Read from the schema rather than assumed. The Corporate book reports
        quarterly and the Retail book monthly, and a feed that hard-codes
        `reporting_month` computes the Corporate dashboard from a column that
        does not exist.
        """
        return schema_mod.period_column(
            schema_mod.domain_of_relation(self.relation))


#: What a period IS in each book. `schema` owns the calendar; this is the
#: name the cards use for it, re-exported so callers keep importing one name.
PERIOD_NOUNS: dict[str, str] = dict(schema_mod.PERIOD_NOUNS)

#: Dimensions that are not columns. `portfolio` is the whole book taken at
#: once -- the highlight that answers "and overall?" -- and it is named here
#: so that "every dimension is a column of this book" stays checkable
#: without the check having to special-case it in three places.
SYNTHETIC_DIMENSIONS: frozenset[str] = frozenset({"portfolio"})


def _period_keys(domain_id: str, reporting: str,
                 comparison: str) -> dict[str, str]:
    """The period fields a card carries. See `schema.period_keys`."""
    return schema_mod.period_keys(domain_id, reporting, comparison)


def _corporate_families() -> tuple[Family, ...]:
    rel = "corp_facility_quarter"
    return (
        Family("sector_stage2", "Stage 2 exposure", "sector", "Sector", rel,
               "stage2_share", "Stage 2 share of EAD", "share", "percent",
               "SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.30),
        Family("sector_ecl", "ECL", "sector", "Sector", rel,
               "ecl", "Recognised ECL", "amount", "SAR million",
               "SUM(ecl_sar_mn)", weight=1.20),
        Family("sector_coverage", "ECL coverage", "sector", "Sector", rel,
               "ecl_coverage", "ECL as a share of EAD", "share", "percent",
               "SUM(ecl_sar_mn) / NULLIF(SUM(ead_sar_mn), 0)", weight=1.05),
        Family("sector_pastdue", "Past due", "sector", "Sector", rel,
               "past_due_share", "Past-due share of EAD", "share", "percent",
               "SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.10),
        Family("product_stage2", "Stage 2 exposure", "product_type",
               "Product type", rel,
               "stage2_share", "Stage 2 share of EAD", "share", "percent",
               "SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=0.95),
        Family("product_ecl", "ECL", "product_type", "Product type", rel,
               "ecl", "Recognised ECL", "amount", "SAR million",
               "SUM(ecl_sar_mn)", weight=0.92),
        Family("region_ecl", "ECL", "region", "Region", rel,
               "ecl", "Recognised ECL", "amount", "SAR million",
               "SUM(ecl_sar_mn)", weight=0.90),
        # §23: the book watches RATINGS before it watches stage, so the feed
        # has to as well. A downgrade is the earliest thing a corporate
        # credit officer acts on and it moves a quarter before the stage
        # does.
        Family("sector_downgrades", "Rating downgrades", "sector", "Sector",
               "corp_borrower_quarter",
               "downgrade_share", "Share of obligors downgraded this quarter",
               "share", "percent",
               "SUM(CASE WHEN rating_notches_moved < 0 THEN 1 ELSE 0 END) "
               "* 1.0 / NULLIF(COUNT(*), 0)", weight=1.25,
               size_expression="COUNT(*) * 1.0"),
        Family("sector_watchlist", "Watch list", "sector", "Sector",
               "corp_borrower_quarter",
               "watchlist_share", "Share of obligors on the watch list",
               "share", "percent",
               "SUM(watchlist_flag) * 1.0 / NULLIF(COUNT(*), 0)",
               weight=1.05, size_expression="COUNT(*) * 1.0"),
        # §23: covenants. A breach is a contractual event, and a sector where
        # they are accumulating is a sector where the documentation is about
        # to become the conversation.
        Family("sector_breaches", "Covenant breaches", "sector", "Sector",
               "corp_covenant_quarter",
               "breach_share", "Share of covenant tests breached", "share",
               "percent",
               "SUM(breach_flag) * 1.0 / NULLIF(COUNT(*), 0)", weight=1.20,
               size_expression="COUNT(*) * 1.0"),
        Family("covenant_breaches", "Covenant breaches", "covenant_type",
               "Covenant type", "corp_covenant_quarter",
               "breach_share", "Share of covenant tests breached", "share",
               "percent",
               "SUM(breach_flag) * 1.0 / NULLIF(COUNT(*), 0)", weight=1.00,
               size_expression="COUNT(*) * 1.0"),
        # §23: concentration. The largest obligor inside a segment, as a
        # share of that segment -- so "this sector is really three names" is
        # a finding the dashboard can make rather than one a reader has to
        # go looking for.
        Family("group_concentration", "Group concentration", "group_name",
               "Parent group", "corp_borrower_quarter",
               "group_share", "Share of the book's obligors in this group",
               "share", "percent",
               "COUNT(*) * 1.0 / NULLIF(SUM(COUNT(*)) OVER (), 0)",
               weight=0.85, size_expression="COUNT(*) * 1.0"),
        # "Collateral cover rose" was the label, and it read as good news:
        # the measure is LOAN TO VALUE, so a rise is security falling behind
        # the exposure it stands against. A card whose headline says the
        # opposite of what its number means is worse than no card.
        Family("sector_ltv", "Loan to value", "collateral_type",
               "Collateral type", "corp_collateral_quarter",
               "ltv", "Exposure as a share of pledged collateral value",
               "share", "percent",
               "SUM(ltv_pct) / NULLIF(COUNT(*) * 100.0, 0)", weight=1.00,
               size_expression="SUM(allocated_value_sar_mn)"),
    )


def _retail_families() -> tuple[Family, ...]:
    rel = "retail_account_month"
    return (
        Family("product_stage2", "Stage 2 exposure", "product", "Product",
               rel, "stage2_share", "Stage 2 share of EAD", "share",
               "percent",
               "SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.30),
        Family("product_ecl", "ECL", "product", "Product", rel,
               "ecl", "Recognised ECL", "amount", "SAR million",
               "SUM(ecl_sar_mn)", weight=1.20),
        Family("product_delinquency", "Delinquency", "product", "Product",
               rel, "dpd_share", "Share of EAD past due", "share", "percent",
               "SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.15),
        Family("band_stage2", "Stage 2 exposure", "score_band",
               "Behaviour score band", rel,
               "stage2_share", "Stage 2 share of EAD", "share", "percent",
               "SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.10),
        Family("vintage_stage2", "Stage 2 exposure", "vintage_year",
               "Origination vintage", rel,
               "stage2_share", "Stage 2 share of EAD", "share", "percent",
               "SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.00),
        Family("segment_ecl", "ECL", "customer_segment", "Customer segment",
               rel, "ecl", "Recognised ECL", "amount", "SAR million",
               "SUM(ecl_sar_mn)", weight=0.95),
        # §23: utilisation, which moves before delinquency does, and the
        # behavioural score, which moves before either.
        Family("product_utilisation", "Utilisation", "product", "Product",
               rel, "utilisation", "Average utilisation", "share", "percent",
               "SUM(utilisation_pct) / NULLIF(COUNT(*) * 100.0, 0)",
               weight=1.05),
        # Customer grain, so the lens has to be a CUSTOMER attribute: a
        # customer holds several products and `retail_customer_month` has no
        # product column to group by.
        Family("segment_score_decline", "Behavioural score deterioration",
               "customer_segment", "Customer segment",
               "retail_customer_month",
               "deteriorated_share",
               "Share of customers whose score deteriorated", "share",
               "percent",
               "SUM(CASE WHEN score_migration = 'DETERIORATED' THEN 1 "
               "ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)", weight=1.22,
               size_expression="SUM(total_ead_sar_mn)"),
        Family("region_delinquency", "Delinquency", "region", "Region", rel,
               "dpd_share", "Share of EAD past due", "share", "percent",
               "SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn ELSE 0 END) "
               "/ NULLIF(SUM(ead_sar_mn), 0)", weight=1.00),
    )


FAMILIES: dict[str, tuple[Family, ...]] = {
    dom.CORPORATE: _corporate_families(),
    dom.RETAIL: _retail_families(),
}


def _check_families() -> None:
    """Every family must name columns its relation actually has.

    A family whose dimension is not a column of its relation does not fail
    when it is written, when it is reviewed, or when the module is imported
    -- it fails inside DuckDB the first time the dashboard is computed, with
    a binder error, on the home page, for that book only. `product_score_
    decline` shipped grouping a CUSTOMER-grain relation by `product`, which
    a customer does not have, and the entire retail Attention feed raised.

    So the families are checked against the governed schema at import. A
    typo is then a failure to start, which is the cheapest kind.
    """
    for domain_id, families in FAMILIES.items():
        for family in families:
            if schema_mod.domain_of_relation(family.relation) != domain_id:
                raise ValueError(
                    f"attention family {family.key!r} is registered under "
                    f"{domain_id!r} but reads {family.relation!r}, which "
                    f"belongs to another book")
            relation = schema_mod.relation(domain_id, family.relation)
            columns = {f.name for f in relation.fields}
            if family.dimension not in columns:
                raise ValueError(
                    f"attention family {family.key!r} groups "
                    f"{family.relation!r} by {family.dimension!r}, which is "
                    f"not a column of it")
            referenced = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*",
                                        family.expression))
            if family.size_expression:
                referenced |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*",
                                             family.size_expression))
            unknown = {name for name in referenced
                       if name.islower() and "_" in name
                       and name not in columns and not name.startswith("null")}
            if unknown:
                raise ValueError(
                    f"attention family {family.key!r} references "
                    f"{sorted(unknown)} which {family.relation!r} does not "
                    f"have")


_check_families()

SECTION_LABELS: dict[str, str] = {
    dom.CORPORATE: "Segments requiring attention",
    dom.RETAIL: "Retail portfolio requiring attention",
}
HIGHLIGHT_LABELS: dict[str, str] = {
    dom.CORPORATE: "Latest-quarter ECL highlights",
    dom.RETAIL: "Latest-month retail ECL highlights",
}


@dataclass
class Candidate:
    family: Family
    segment: str
    now: float
    before: float
    ead_now: float
    ead_before: float
    share: float
    #: How many counterparties this segment holds this month.
    counterparties: int = 0
    score: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def movement(self) -> float:
        return self.now - self.before

    @property
    def relative(self) -> float:
        if self.before in (0, None):
            return 1.0 if self.now else 0.0
        return (self.now - self.before) / abs(self.before)


#: relation -> the column identifying the counterparty behind one row, and
#: the word a reader uses for it. A retail segment has customers; a corporate
#: one has borrowers; a collateral segment has facilities.
COUNTERPARTY: dict[str, str] = {
    "corp_facility_quarter": "borrower_id",
    "corp_borrower_quarter": "borrower_id",
    "corp_collateral_quarter": "facility_id",
    "corp_covenant_quarter": "facility_id",
    "retail_account_month": "customer_id",
    "retail_customer_month": "customer_id",
    "retail_behaviour_month": "customer_id",
    "retail_collateral_month": "customer_id",
}

COUNTERPARTY_LABEL: dict[str, str] = {
    "borrower_id": "borrowers", "customer_id": "customers",
    "facility_id": "facilities",
}

#: The finer dimension this book records under each segment dimension, where
#: one exists. Stating "there is no level below this" is a FACT about the
#: release, and inventing a subsegment is the failure it prevents.
FINER: dict[str, str] = {
    "sector": "sub_sector",
    "product": "",
    "region": "",
    "product_type": "",
    "collateral_type": "",
    "covenant_type": "",
    "group_name": "",
    "customer_segment": "",
    "score_band": "",
    "vintage_year": "",
}


def _query(session, sql: str) -> list[dict[str, Any]]:
    cursor = session.connection.execute(sql)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _measure(session, family: Family, month: str) -> dict[str, dict[str, float]]:
    counterparty = COUNTERPARTY.get(family.relation, "")
    counted = (f"COUNT(DISTINCT {counterparty})" if counterparty
               else "COUNT(*)")
    rows = _query(session, f"""
        SELECT CAST({family.dimension} AS VARCHAR) AS segment,
               {family.expression} AS value,
               {family.size_expression} AS ead,
               {counted} AS counterparties
        FROM {family.relation}
        WHERE {family.period_column} = '{month}'
        {f"AND {family.filter_expression}" if family.filter_expression else ""}
        GROUP BY 1
    """)
    return {str(r["segment"]): {"value": float(r["value"] or 0.0),
                                "ead": float(r["ead"] or 0.0),
                                "counterparties": int(r["counterparties"]
                                                      or 0)}
            for r in rows}


def _candidates(session, family: Family, *, month: str,
                comparison: str) -> list[Candidate]:
    now = _measure(session, family, month)
    before = _measure(session, family, comparison)
    total = sum(v["ead"] for v in now.values()) or 1.0
    #: What the whole book measures this month, for an amount family. The
    #: denominator that makes one segment's movement comparable to another's.
    book_total = sum(v["value"] for v in now.values()) or 1.0

    out: list[Candidate] = []
    for segment, current in now.items():
        prior = before.get(segment)
        if prior is None:
            continue  # a segment that did not exist last month is not a move
        share = current["ead"] / total
        if share < MIN_SHARE:
            continue
        candidate = Candidate(
            family=family, segment=segment,
            now=current["value"], before=prior["value"],
            ead_now=current["ead"], ead_before=prior["ead"], share=share,
            counterparties=int(current.get("counterparties") or 0))
        movement = candidate.movement
        if family.kind == "amount":
            # An AMOUNT is measured against the BOOK, not against itself.
            #
            # Normalising a sector's ECL movement by that sector's own prior
            # ECL is what put "Wholesale Trade: ECL rose to SAR 55 million"
            # at the top of a corporate page whose Construction book carries
            # SAR 800 million and had just added more than Wholesale holds.
            # A small segment's small move is large in its own terms and that
            # is exactly the reading a credit officer must not be given.
            #
            # So the score for an amount is its contribution to the whole
            # book's movement, and `share` is NOT applied on top of it: the
            # size is already in the numerator, and multiplying by it again
            # would rank by size squared.
            normalised = movement / book_total
            weighted = abs(normalised) * family.weight
        else:
            # A SHARE or RATIO is already size-free, so the segment's weight
            # in the book is what decides whether its move matters.
            normalised = movement
            weighted = abs(normalised) * share * family.weight
        if candidate.now <= candidate.before or abs(normalised) < MIN_MOVEMENT:
            continue
        # Material AND moving. Neither alone reaches the top.
        candidate.score = weighted
        out.append(candidate)
    return out


def _spread(ordered: list[Candidate], *, limit: int) -> list[Candidate]:
    """Round-robin across dimensions, then backfill by score."""
    by_dimension: dict[str, list[Candidate]] = {}
    for candidate in ordered:
        by_dimension.setdefault(candidate.family.dimension,
                                []).append(candidate)

    # Dimensions take their turn strongest-first. Alphabetical order was the
    # first version and it decided, by nothing but the letter C, that a
    # collateral card outranked the strongest sector finding in the book.
    order = sorted(by_dimension,
                   key=lambda d: (-by_dimension[d][0].score, d))

    taken: list[Candidate] = []
    seen: set[int] = set()
    # ONE CARD PER SEGMENT while there are segments left to name.
    #
    # Spreading across dimensions is not enough: two families on the SAME
    # dimension -- Stage 2 share and recognised ECL, say -- both top out on
    # the same sector in a book where that sector is genuinely the story, and
    # the reader gets five cards about four places. A dashboard answering
    # "where should I look" should name five places while five have something
    # to say.
    #
    # On a book too small or too quiet to offer five, the backfill below
    # still fills the section rather than showing four cards: a repeated
    # segment is worse than a missing one only when there was an alternative.
    segments: set[str] = set()
    for rounds in range(MAX_PER_DIMENSION):
        for dimension in order:
            queue = by_dimension[dimension]
            if len(queue) <= rounds or len(taken) >= limit:
                continue
            candidate = queue[rounds]
            if candidate.segment in segments:
                continue
            taken.append(candidate)
            seen.add(id(candidate))
            segments.add(candidate.segment)
    for candidate in ordered:
        if len(taken) >= limit:
            break
        if id(candidate) in seen or candidate.segment in segments:
            continue
        taken.append(candidate)
        seen.add(id(candidate))
        segments.add(candidate.segment)
    # Only now, and only to fill the section, may a segment appear twice.
    for candidate in ordered:
        if len(taken) >= limit:
            break
        if id(candidate) not in seen:
            taken.append(candidate)
            seen.add(id(candidate))
    return sorted(taken, key=lambda c: (-c.score, c.family.key, c.segment))


def _item_id(domain_id: str, family: str, segment: str, month: str) -> str:
    seed = f"{domain_id}|{family}|{segment}|{month}"
    return "att-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _shown(value: float, family: Family, currency: str, scale: str) -> str:
    if family.kind == "amount":
        return disp.format_value(_dec(value), f"{currency} {scale}")
    return disp.format_value(_dec(value * 100), "percent")


def _dec(value: float):
    from decimal import Decimal

    return Decimal(str(value))


#: What a credit officer checks next, by what moved. Deterministic, and
#: written as a REVIEW step rather than a conclusion: the card says what
#: changed, and these say where to look, never what it means.
#:
#: `{period}` is the book's OWN period noun, filled by `_review_next`. These
#: said "the same month" in both books, so a corporate drawer -- opened from
#: a card headed `Reporting quarter Q2 2026` -- told the reader to go and
#: check a month that book does not report.
REVIEW_NEXT: dict[str, tuple[str, ...]] = {
    "stage2_share": (
        "Which exposures crossed into Stage 2, and on which trigger.",
        "Whether the migration is concentrated in a few names or broad.",
        "What the same segment's coverage did over the same {period}."),
    "ecl": (
        "Whether the increase is new exposure, migration or higher loss "
        "rates.",
        "Which exposures contributed most of the movement.",
        "Whether coverage moved with it or the book simply grew."),
    "ecl_coverage": (
        "Whether coverage rose because ECL rose or because exposure fell.",
        "Which stage the change sits in.",
        "How this segment's coverage compares with the book."),
    "past_due_share": (
        "How far past due, and for how long.",
        "Whether the arrears are cured, rolling or newly entered.",
        "Whether the same exposures are already in Stage 2 or 3."),
    "dpd_share": (
        "Which delinquency buckets the movement sits in.",
        "Whether entry or cure rates changed.",
        "Whether the affected accounts share a vintage or a score band."),
    "ltv": (
        "Whether the exposure grew or the collateral value fell.",
        "How old the valuations behind it are.",
        "What is left uncovered after the haircut."),
}

DEFAULT_REVIEW = (
    "What moved underneath this measure over the same {period}.",
    "Whether the movement is concentrated or broad.",
    "Whether other measures on this segment moved with it.")


def _review_next(measure: str, noun: str) -> list[str]:
    """The review steps for this measure, written in this book's calendar."""
    return [step.format(period=noun)
            for step in REVIEW_NEXT.get(measure, DEFAULT_REVIEW)]


def _drivers(candidate: Candidate, everything: list[Candidate], *,
             noun: str = "period") -> list[dict[str, Any]]:
    """Other measures that moved on the SAME segment, as associations.

    Association, never cause: these are recorded alongside the movement, and
    the drawer says so under them. Deriving a cause here would be the card
    deciding the analysis, which is the analyst's job and the reason
    Investigate Further exists.
    """
    out: list[dict[str, Any]] = []
    for other in everything:
        if other is candidate:
            continue
        if (other.segment != candidate.segment
                or other.family.dimension != candidate.family.dimension):
            continue
        if other.family.measure == candidate.family.measure:
            continue
        out.append({
            "relationship": "recorded alongside",
            "statement": (f"{other.family.measure_label} on the same segment "
                          f"also rose over this {noun}."),
            "metric": other.family.measure,
            "delta": round(other.movement, 6),
            "strength": round(min(1.0, abs(other.movement) * 10), 4),
        })
    return sorted(out, key=lambda d: -d["strength"])[:3]


def _item(candidate: Candidate, *, scope: dom.DomainScope,
          month: str, comparison: str,
          everything: list[Candidate] | None = None) -> dict[str, Any]:
    family = candidate.family
    shown_now = _shown(candidate.now, family, scope.currency,
                       scope.amount_scale)
    shown_before = _shown(candidate.before, family, scope.currency,
                          scope.amount_scale)
    if family.kind == "amount":
        movement_text = (f"{disp.format_value(_dec(candidate.relative * 100), 'percent')}"
                         f" higher than {comparison}")
    else:
        movement_text = (
            f"{disp.format_value(_dec(candidate.movement * 100), 'percentage points')}"
            f" higher than {comparison}")
    return {
        "item_id": _item_id(scope.domain_id, family.key, candidate.segment,
                            month),
        "domain_id": scope.domain_id,
        "release_id": scope.release_id,
        "release_fingerprint": scope.release_fingerprint,
        "section": "attention",
        # WHAT this card is about. The page groups by it and the drawer
        # labels by it, and a card with no scope renders as a card about
        # nothing in particular.
        "scope": "segment",
        "family": family.key,
        # The family's label as written, not lower-cased: "ECL" is an
        # initialism and "ecl rose to" is how a card announces it was
        # assembled by string concatenation.
        # The reader's form in the prose, the book's own identifier in the
        # data. A governed value is spelled `asset_finance` in the release,
        # and a card headlined "asset_finance carries the most ECL" is the
        # database talking. `segment` stays canonical because the seed
        # filters on it and the drill-down query needs the real value.
        "headline": (f"{val_mod.pretty(candidate.segment)}: {family.label} "
                     f"rose to {shown_now}"),
        "segment": candidate.segment,
        "segment_label": val_mod.pretty(candidate.segment),
        "segment_dimension": family.dimension,
        "segment_dimension_label": family.dimension_label,
        "metric": family.measure,
        "metric_label": family.measure_label,
        **_period_keys(scope.domain_id, month, comparison),
        "what_changed": (f"{family.measure_label} was {shown_before} in "
                         f"{comparison} and is {shown_now} in {month}."),
        "why_it_appeared": (
            f"It moved {movement_text} on a segment holding "
            f"{disp.format_value(_dec(candidate.share * 100), 'percent')} of "
            f"the book's exposure at default."),
        "movement": {
            "from": candidate.before, "to": candidate.now,
            "from_display": shown_before, "to_display": shown_now,
            "unit": family.unit,
        },
        "key_numbers": [
            {"label": f"{family.measure_label}, {month}",
             "display": shown_now},
            {"label": f"{family.measure_label}, {comparison}",
             "display": shown_before},
            {"label": f"Exposure at default, {month}",
             "display": disp.format_value(
                 _dec(candidate.ead_now),
                 f"{scope.currency} {scope.amount_scale}")},
            {"label": "Share of book",
             "display": disp.format_value(_dec(candidate.share * 100),
                                          "percent")},
        ],
        "evidence": {"relation": family.relation,
                     "dimension": family.dimension,
                     "measure": family.measure},
        "evidence_url": "",
        # The drawer renders both of these. They were absent when the
        # per-domain engine replaced the quarterly one, and the drawer read
        # `item.possible_drivers.length` -- so clicking ANY card threw inside
        # the component and the error boundary took the whole Cockpit home
        # page with it. A dashboard whose cards cannot be opened is not a
        # dashboard.
        "possible_drivers": _drivers(candidate, everything or [],
                                     noun=scope.period_noun),
        "what_to_review_next": _review_next(family.measure,
                                            scope.period_noun),
        "drilldown": _drilldown(candidate, scope, month),
        "severity": ("high" if candidate.score > 0.02
                     else "medium" if candidate.score > 0.005 else "low"),
        "score": round(candidate.score, 6),
    }


def _drilldown(candidate: Candidate, scope: dom.DomainScope,
               month: str) -> dict[str, Any]:
    """Where a reader can go from this card, and where the book stops.

    The drawer prints this sentence. It printed "This segment has  borrowers
    at 2026-08" -- with a hole where the count belongs and the wrong noun for
    the Retail book -- because the per-domain engine emitted only the
    suggested questions and the component filled the rest from fields that
    were not there.

    Saying where the book STOPS is the other half. A card that offers a
    drill-down the release cannot serve is how an analyst ends up asking for
    a subsegment that does not exist.
    """
    family = candidate.family
    counterparty = COUNTERPARTY.get(family.relation, "")
    noun = COUNTERPARTY_LABEL.get(counterparty, "rows")
    finer = FINER.get(family.dimension, "")
    if finer:
        note = (f"{family.dimension_label} has a level below it in this "
                f"book: {finer}.")
    else:
        note = (f"There is no subsegment level below "
                f"{family.dimension_label.lower()} in this book, so a "
                f"finer cut is not available and none is implied.")
    return {
        "note": note,
        "entity_label": noun,
        "entity_count": candidate.counterparties,
        # The name the drawer has always used. Kept so a reader of the API
        # sees the same number under both names rather than one of them
        # empty.
        "borrower_count": candidate.counterparties,
        "available": ([finer] if finer else []) + [noun],
        "unavailable": [] if finer else ["subsegment"],
        # §19-§21. Which relation this finding was COMPUTED from, and which
        # of its columns the measure is computed out of. A thread seeded
        # from this card is handed both, so its first action can be the
        # query rather than a search for the table the card came from.
        "relation": family.relation,
        "period_column": family.period_column,
        "measure_fields": _measure_fields(family),
        "counterparty_column": counterparty,
        "finer_dimension": finer,
        "suggested_questions": _questions(candidate, scope, month),
    }


#: What the second axis is, per book: the dimension a reader cuts a finding
#: by when they want to know WHO inside it moved. Read from the release's own
#: relations, so a question offered here is a question the book can answer.
_CROSS: dict[str, tuple[tuple[str, str], ...]] = {
    dom.CORPORATE: (("product_type", "product type"),
                    ("region", "region"),
                    ("relationship_tier", "relationship segment")),
    dom.RETAIL: (("product", "product"), ("region", "region"),
                 ("customer_segment", "customer segment")),
}


#: SQL words that appear in a family's measure expression and are not
#: columns of the relation it reads.
_NOT_A_COLUMN = frozenset((
    "sum", "count", "avg", "min", "max", "case", "when", "then", "else",
    "end", "nullif", "coalesce", "cast", "as", "varchar", "decimal", "and",
    "or", "not", "null", "distinct", "over", "partition", "by",
))


def _measure_fields(family: Family) -> list[str]:
    """The columns a family's measure is actually computed from.

    Read out of the family's own SQL rather than from `measure`, which is a
    METRIC KEY -- `ecl`, `stage2_share`, `ltv` -- and not a column of
    anything. Offering `ecl` as a required field named a column no release
    holds, which is exactly the failure these questions exist to avoid.
    """
    found = re.findall(r"[a-z_][a-z0-9_]*", family.expression.lower())
    seen: list[str] = []
    for word in found:
        if word in _NOT_A_COLUMN or word in seen:
            continue
        seen.append(word)
    return seen


def _release_columns(scope: Any, relation: str) -> set[str]:
    """What THIS scope's release holds on that relation.

    Falls back to the domain's current schema only when the scope carries
    no catalogue, which is the shape some older callers pass.
    """
    catalog = getattr(scope, "catalog", None)
    if catalog is not None:
        try:
            return set(catalog.columns(relation))
        except Exception:  # noqa: BLE001 - not a relation of this release
            return set()
    try:
        return {f.name for f in schema_mod.relation(
            scope.domain_id, relation).fields}
    except Exception:  # noqa: BLE001
        return set()


def _questions(candidate: Candidate, scope: dom.DomainScope,
               month: str) -> list[dict[str, Any]]:
    """What to ask next about THIS finding. Deterministic; no model call.

    Five of them, and every one names a field or a period this release
    actually holds -- §31. A seeded thread shows these before its first
    message, so the reader opening a card is looking at five questions the
    book can answer rather than an empty box and a headline.

    Schema-aware means two things here. The DRILL-DOWN is offered only where
    the book has a level below the segment (`FINER`), because offering
    "break it down further" against a dimension with nothing under it is how
    an analyst ends up asking for a subsegment that does not exist. And the
    CROSS-CUT names a real second dimension of the same relation.
    """
    family = candidate.family
    segment = val_mod.pretty(candidate.segment)
    measure = family.measure_label or family.measure
    counterparty = COUNTERPARTY.get(family.relation, "")
    noun = COUNTERPARTY_LABEL.get(counterparty, "rows")
    finer = FINER.get(family.dimension, "")
    # A cross-cut has to be a column of THIS finding's relation. The
    # per-book list is the book's second axes in general, and the collateral
    # and covenant relations do not carry `product_type` or `region` -- so a
    # covenant-breach card was offering "Split Leverage by region", a chip
    # that could not be executed and that told the reader the book held
    # something it does not.
    # THE RELEASE'S columns, not the domain's current ones. The paragraph
    # above is right about why this filter exists and was reading the wrong
    # list to do it: on a superseded release it would offer a split by a
    # column added after that release was published.
    mine = _release_columns(scope, family.relation)
    cross = [(field, label) for field, label in _CROSS.get(scope.domain_id, ())
             if field != family.dimension and field in mine]

    out: list[dict[str, Any]] = [
        {"question": f"Show the {noun} behind {segment} in {month}.",
         "required_fields": [counterparty] if counterparty else [],
         "required_periods": [month], "required_quarters": [month],
         "kind": "drilldown"},
        {"question": (f"What drove the change in {measure} for {segment} "
                      f"in {month}?"),
         "required_fields": _measure_fields(family),
         "required_periods": [month], "required_quarters": [month],
         "kind": "diagnostic"},
    ]
    if finer and finer in mine:
        out.append({
            "question": f"Break {segment} down by {finer.replace('_', ' ')}.",
            "required_fields": [finer],
            "required_periods": [month], "required_quarters": [month],
            "kind": "drilldown"})
    if cross:
        field, label = cross[0]
        out.append({
            "question": f"Split {segment} by {label}.",
            "required_fields": [field],
            "required_periods": [month], "required_quarters": [month],
            "kind": "crosscut"})
    # §4. A trend question written in the book's OWN periods. "Over the last
    # twelve months" asked of a quarterly book is a question about three
    # years, and the reader who clicks it gets an answer to a question they
    # did not ask.
    noun_word = schema_mod.period_noun(scope.domain_id)
    span = 8 if noun_word == "quarter" else 12
    out.append({
        "question": (f"How has {measure} moved for {segment} over the last "
                     f"{span} {noun_word}s?"),
        "required_fields": _measure_fields(family),
        "required_periods": list(scope.last_periods(span)),
        "required_quarters": [], "kind": "trend"})
    if len(out) < 5 and len(cross) > 1:
        field, label = cross[1]
        out.insert(-1, {
            "question": f"Split {segment} by {label}.",
            "required_fields": [field],
            "required_periods": [month], "required_quarters": [month],
            "kind": "crosscut"})
    # Every question names the relation it would be asked of, the segment it
    # is about and the column that segment lives in. §22 requires each one to
    # be checkably executable before it is offered, and a question carrying
    # only prose cannot be checked against anything.
    stamp(out, relation=family.relation, dimension=family.dimension,
          segment=candidate.segment, period_column=family.period_column)
    return out[:5]


def stamp(questions: list[dict[str, Any]], *, relation: str, dimension: str,
          segment: str, period_column: str) -> None:
    """Give every suggested question the facts that make it CHECKABLE.

    §22 requires each offered question to be executable before it is
    offered, and a question carrying only prose cannot be checked against
    anything. So each one names the relation it would be asked of, the
    column its subject lives in, the value of that subject and the column
    the periods are kept in -- and `investigation.executable` binds all four
    against the release before the chip is shown.
    """
    for entry in questions:
        entry.setdefault("relation", relation)
        entry.setdefault("segment_dimension", dimension)
        entry.setdefault("segment", segment)
        entry.setdefault("period_column", period_column)


def _highlights(session, *, scope: dom.DomainScope, month: str,
                comparison: str) -> list[dict[str, Any]]:
    """The latest month's ECL, said four ways a reader actually asks for."""
    domain_id = scope.domain_id
    relation = ("corp_facility_quarter" if domain_id == dom.CORPORATE
                else "retail_account_month")
    dimension = "sector" if domain_id == dom.CORPORATE else "product"
    period = schema_mod.period_column(domain_id)
    money = f"{scope.currency} {scope.amount_scale}"

    rows = _query(session, f"""
        SELECT CAST({dimension} AS VARCHAR) AS segment,
               SUM(ecl_sar_mn) AS ecl,
               SUM(ead_sar_mn) AS ead
        FROM {relation} WHERE {period} = '{month}'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    prior = {r["segment"]: float(r["ecl"] or 0.0) for r in _query(session, f"""
        SELECT CAST({dimension} AS VARCHAR) AS segment,
               SUM(ecl_sar_mn) AS ecl
        FROM {relation} WHERE {period} = '{comparison}'
        GROUP BY 1
    """)}
    if not rows:
        return []

    total = sum(float(r["ecl"] or 0.0) for r in rows)
    total_ead = sum(float(r["ead"] or 0.0) for r in rows)
    largest = rows[0]
    moved = max(rows, key=lambda r: float(r["ecl"] or 0.0)
                - prior.get(r["segment"], 0.0))
    stage2 = _query(session, f"""
        SELECT SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) AS s2,
               SUM(ead_sar_mn) AS ead
        FROM {relation} WHERE {period} = '{month}'
    """)[0]

    def _card(key: str, headline: str, one_line: str, shown: str, *,
             segment: str = "", before: str = "",
             measure: str = "Recognised ECL",
             extra: tuple[dict[str, str], ...] = ()) -> dict:
        """One highlight, in the SAME envelope a segment card uses.

        It used to be a short dict with a headline and a display string, and
        the difference was not cosmetic: the drawer offers Investigate
        Further on every card, and the route that opens a seeded thread reads
        `segment`, `metric`, `what_changed`, `movement`, `key_numbers` and
        `evidence` off the item. Clicking Investigate on an ECL highlight
        therefore raised a KeyError and returned a 500 -- on the four cards a
        reader is most likely to click.
        """
        subject = val_mod.pretty(segment) if segment else scope.short_label
        return {
            "item_id": _item_id(scope.domain_id, f"ecl-{key}", segment,
                                month),
            "domain_id": scope.domain_id,
            "release_id": scope.release_id,
            "release_fingerprint": scope.release_fingerprint,
            "section": "ecl_highlight",
            "scope": "segment" if segment else "portfolio",
            "family": f"ecl-{key}",
            "headline": headline,
            "one_line": one_line,
            "display": shown,
            "segment": segment or subject,
            "segment_label": subject,
            "segment_dimension": dimension if segment else "portfolio",
            "segment_dimension_label": (
                "Sector" if dimension == "sector" and segment
                else "Product" if segment else "Whole book"),
            "metric": f"ecl_{key}",
            "metric_label": measure,
            **_period_keys(scope.domain_id, month, comparison),
            "what_changed": one_line,
            "why_it_appeared": (
                f"{measure} is one of the four figures this book reports on "
                f"its own ECL position for {month}."),
            "movement": {"from": None, "to": None,
                         "from_display": before, "to_display": shown,
                         "unit": "amount"},
            # At least two, always. A drawer that opens on a single figure
            # has nothing to compare and answers none of the questions it is
            # opened to answer.
            "key_numbers": [{"label": f"{measure}, {month}",
                             "display": shown}]
            + ([{"label": f"{measure}, {comparison}", "display": before}]
               if before else [])
            + list(extra or []),
            "evidence": {"relation": relation, "dimension": dimension,
                         "measure": "ecl_sar_mn"},
            "evidence_url": "",
            "possible_drivers": [],
            "what_to_review_next": _review_next("ecl", scope.period_noun),
            "drilldown": {
                "note": ("This is a book-level figure, so there is no "
                         "segment below it to open."),
                "entity_label": ("borrowers"
                                 if scope.domain_id == dom.CORPORATE
                                 else "customers"),
                "entity_count": 0, "borrower_count": 0,
                "available": [], "unavailable": ["subsegment"],
                # §31: five, and every one names a field or a period this
                # release holds. A thread seeded from an ECL highlight opens
                # on these, before anybody has typed anything into it.
                "suggested_questions": [
                {"question": (f"What drove {subject} ECL in {month}?"
                              if segment else
                              f"Which segments drove ECL in {month}?"),
                 "required_fields": ["ecl_sar_mn", dimension],
                 "required_periods": [month], "required_quarters": [month],
                 "kind": "drilldown"},
                {"question": (f"What is {subject} ECL coverage in {month}?"
                              if segment else
                              f"What is the book's ECL coverage in {month}?"),
                 "required_fields": ["ecl_sar_mn", "ead_sar_mn"],
                 "required_periods": [month], "required_quarters": [month],
                 "kind": "diagnostic"},
                {"question": (f"How does {subject} ECL split by stage in "
                              f"{month}?"),
                 "required_fields": ["ecl_sar_mn", "stage"],
                 "required_periods": [month], "required_quarters": [month],
                 "kind": "drilldown"},
                {"question": (f"Which {'borrowers' if scope.domain_id == dom.CORPORATE else 'customers'} "
                              f"carry the most of {subject} ECL in {month}?"),
                 "required_fields": [
                     "borrower_id" if scope.domain_id == dom.CORPORATE
                     else "customer_id", "ecl_sar_mn"],
                 "required_periods": [month], "required_quarters": [month],
                 "kind": "drilldown"},
                {"question": (
                    f"How has {subject} ECL moved over the last "
                    f"{8 if scope.domain_id == dom.CORPORATE else 12} "
                    f"{schema_mod.period_noun(scope.domain_id)}s?"),
                 "required_fields": ["ecl_sar_mn"],
                 "required_periods": list(scope.last_periods(
                     8 if scope.domain_id == dom.CORPORATE else 12)),
                 "required_quarters": [],
                 "kind": "trend"},
            ],
                # §19-§21. The same facts a segment card carries, so a
                # thread seeded from a highlight opens with the relation it
                # came from rather than having to look for it.
                "relation": relation,
                "period_column": schema_mod.period_column(scope.domain_id),
                "measure_fields": ["ecl_sar_mn", "ead_sar_mn"],
                "counterparty_column": (
                    "borrower_id" if scope.domain_id == dom.CORPORATE
                    else "customer_id"),
                "finer_dimension": "",
            },
            "severity": "medium",
            "score": 0.0,
        }

    def card(*args: Any, **kwargs: Any) -> dict:  # noqa: F811
        body = _card(*args, **kwargs)
        drill = body["drilldown"]
        stamp(drill["suggested_questions"], relation=drill["relation"],
              dimension=body["segment_dimension"],
              # A book-level highlight is about the whole book, so it pins
              # no segment: `executable` then checks the columns and the
              # periods and skips the subject check, which is right -- there
              # is no subject to check.
              segment=("" if body["scope"] == "portfolio"
                       else body["segment"]),
              period_column=drill["period_column"])
        return body

    increase = float(moved["ecl"] or 0.0) - prior.get(moved["segment"], 0.0)
    return [
        card("total", f"{scope.short_label} recognised ECL is "
                      f"{disp.format_value(_dec(total), money)}",
             f"Across the book in {month}, on exposure of "
             f"{disp.format_value(_dec(total_ead), money)}.",
             disp.format_value(_dec(total), money),
             before=disp.format_value(_dec(sum(prior.values())), money),
             extra=({"label": f"Exposure at default, {month}",
                     "display": disp.format_value(_dec(total_ead), money)},
                    {"label": f"Coverage, {month}",
                     "display": disp.format_value(
                         _dec(total / max(total_ead, 1e-9) * 100),
                         "percent")})),
        card("largest", f"{largest['segment']} carries the most ECL",
             f"{disp.format_value(_dec(float(largest['ecl'])), money)} of "
             f"the {disp.format_value(_dec(total), money)} recognised.",
             disp.format_value(_dec(float(largest["ecl"])), money),
             segment=str(largest["segment"]),
             before=disp.format_value(
                 _dec(prior.get(largest["segment"], 0.0)), money)),
        card("increase", f"{moved['segment']} added the most ECL",
             f"Up {disp.format_value(_dec(increase), money)} against "
             f"{comparison}.",
             disp.format_value(_dec(increase), money),
             segment=str(moved["segment"]),
             measure=f"ECL added this {scope.period_noun}",
             before=disp.format_value(
                 _dec(prior.get(moved["segment"], 0.0)), money)),
        # Named for its book. Both dashboards carried a card headlined
        # "Stage 2 and 3 exposure" and a reader with two tabs open could not
        # tell which portfolio they were looking at.
        card("stage2", f"{scope.short_label} Stage 2 and 3 exposure",
             f"{disp.format_value(_dec(float(stage2['s2'])), money)} of "
             f"{disp.format_value(_dec(float(stage2['ead'])), money)} sits "
             f"in Stage 2 or 3.",
             disp.format_value(
                 _dec(float(stage2["s2"]) / max(float(stage2["ead"]), 1e-9)
                      * 100), "percent"),
             measure="Stage 2 and 3 share of exposure",
             extra=({"label": f"Stage 2 and 3 exposure, {month}",
                     "display": disp.format_value(
                         _dec(float(stage2["s2"])), money)},
                    {"label": f"Exposure at default, {month}",
                     "display": disp.format_value(
                         _dec(float(stage2["ead"])), money)})),
    ]


def compute(*, session, scope: dom.DomainScope) -> dict[str, Any]:
    """The whole feed for one book. No model call, and no cross-domain read."""
    if session.domain_id != scope.domain_id:
        raise AttentionUnavailable(
            f"A {dom.LABELS[scope.domain_id]} feed was asked for from a "
            f"{dom.LABELS[session.domain_id]} session. Nothing was computed.")
    started = time.monotonic()
    month, comparison = scope.latest_period, scope.previous_period
    if not month or not comparison:
        raise AttentionUnavailable(
            f"Release {scope.release_id!r} does not have two months to "
            f"compare.")

    candidates: list[Candidate] = []
    for family in FAMILIES[scope.domain_id]:
        candidates.extend(_candidates(session, family, month=month,
                                      comparison=comparison))
    # One card per segment: the same sector arriving from three families is
    # one finding said three ways, and a page of it is a page of one story.
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        key = f"{candidate.family.dimension}|{candidate.segment}"
        if key not in best or candidate.score > best[key].score:
            best[key] = candidate
    ordered = sorted(best.values(),
                     key=lambda c: (-c.score, c.family.key, c.segment))

    # Then spread across DIMENSIONS before filling up from any one of them.
    #
    # When a whole book moves, every slice of one lens moves with it, and the
    # page filled up with four customer segments that had all risen by
    # roughly the same amount -- true, and four ways of reading one fact.
    # A flat cap fixed that and broke the other case: the corporate book has
    # two lenses worth showing, so capping at two per dimension left it with
    # two cards on a book that had plainly changed.
    #
    # So: take the best from each dimension in turn, and once every dimension
    # has had its offer, fill the rest by score. Diversity where the book
    # offers it, depth where it does not.
    ranked = _spread(ordered, limit=TOP_N)

    return {
        "domain_id": scope.domain_id,
        "domain_label": scope.label,
        "release_id": scope.release_id,
        "release_fingerprint": scope.release_fingerprint,
        **_period_keys(scope.domain_id, month, comparison),
        "reporting_currency": scope.currency,
        "amount_scale": scope.amount_scale,
        "attention_label": SECTION_LABELS[scope.domain_id],
        "highlights_label": HIGHLIGHT_LABELS[scope.domain_id],
        "segments_requiring_attention": [
            _item(c, scope=scope, month=month, comparison=comparison,
                  everything=candidates)
            for c in ranked[:TOP_N]],
        "ecl_highlights": _highlights(session, scope=scope, month=month,
                                      comparison=comparison),
        "model_calls": 0,
        "computed_ms": int((time.monotonic() - started) * 1000),
        # WHO computed this and on what basis. Carried because the page
        # prints it, and because a reader looking at a movement deserves to
        # know it is the recorded book and not a prediction. Dropping it when
        # the per-domain engine replaced the quarterly one took the whole
        # Home page down with a TypeError, which is a good argument for the
        # footnote being part of the contract rather than a nicety.
        "ownership": {
            "functionality": "cockpit",
            "basis": "recorded_book",
            "note": (f"Movements in the recorded {scope.label} book "
                     f"between two reporting "
                     f"{schema_mod.period_noun(scope.domain_id)}s. Not "
                     f"Early Warning: no live signal and no prediction is "
                     f"used here."),
        },
        "method_summary": {
            "candidates_considered": len(candidates),
            "families": [f.key for f in FAMILIES[scope.domain_id]],
            "minimum_share": MIN_SHARE,
            "minimum_movement": MIN_MOVEMENT,
            "max_per_dimension": MAX_PER_DIMENSION,
            "ranking": ("normalised movement x share of book x family "
                        "weight; one card per segment, at most "
                        f"{MAX_PER_DIMENSION} per dimension"),
        },
    }


_CACHE: dict[tuple, dict[str, Any]] = {}


def cached(*, session, scope: dom.DomainScope, tenant_id: str,
           refresh: bool = False) -> dict[str, Any]:
    """Keyed by tenant, domain, release AND fingerprint. §55."""
    key = scope.cache_key("attention", tenant_id)
    if not refresh and key in _CACHE:
        return {**_CACHE[key], "cached": True}
    feed = compute(session=session, scope=scope)
    _CACHE[key] = feed
    return {**feed, "cached": False}


def clear_cache() -> None:
    _CACHE.clear()


def find_item(feed: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    """Any card on the page, not only the movement ones.

    It searched `segments_requiring_attention` alone, so the four ECL
    highlight cards -- which sit on the same page, open the same drawer and
    offer the same Investigate Further -- were "no such attention item". A
    reader clicking the most prominent card on the Cockpit got a 404.
    """
    for section in ("segments_requiring_attention", "ecl_highlights"):
        for item in feed.get(section, []) or []:
            if item.get("item_id") == item_id:
                return item
    return None


__all__ = ["AttentionUnavailable", "Candidate", "FAMILIES",
           "HIGHLIGHT_LABELS", "MIN_MOVEMENT", "MIN_SHARE", "SECTION_LABELS",
           "MAX_PER_DIMENSION", "TOP_N", "cached", "clear_cache", "compute", "find_item"]
