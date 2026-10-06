"""
The Cockpit home feed: what deteriorated in the recorded book, and why.

What this is
------------
A deterministic analytical dashboard. It reads the pinned Cockpit V4 release
through the same read-only DuckDB session an analysis uses, computes a fixed
set of indicators at sector x quarter grain, scores them by a published
formula, and returns the top few with the numbers behind them.

What it is NOT
--------------
It is not Early Warning. Every indicator here is a movement in the RECORDED
book between two reporting quarters. Nothing live, nothing behavioural,
nothing predictive, and no EWS score is read, imported or imitated. The
Cockpit question is "what deteriorated in what we have recorded"; the Early
Warning question is "what is starting to go wrong now", and that belongs to
Early Warning.

It is also not a model call. No paid generation happens to render the home
page — not for the ranking, not for the headline, not for the explanation.
The model enters only when the user presses Investigate Further, and then it
is an ordinary V4 run with the item as its starting context.

The ranking, in full
--------------------
For each sector and each indicator, at the latest populated quarter Q, against
two comparison bases: the prior quarter, and the same quarter one year
earlier.

  1. value_old, value_new are computed from aggregates. A null on either side
     is NOT zero: the candidate is dropped and the reason recorded.
  2. adverse_delta = (value_new - value_old) * direction, where direction is
     +1 for indicators where up is worse and -1 where down is worse.
     adverse_delta <= 0 means no deterioration; the candidate is dropped.
  3. exposure_at_risk translates the movement into money, so a share, a ratio
     and an amount can be compared:
       - share/ratio indicators: adverse_delta * sector EAD at Q
       - amount indicators:      adverse_delta itself
  4. Materiality gates, all relative to the book so they travel to a bigger
     portfolio without being re-tuned:
       - exposure_at_risk  >= 5 bps of book EAD
       - sector EAD        >= 50 bps of book EAD in BOTH periods
       - facility count    >= 2 in both periods
     A sector that fails a gate is dropped with the gate named. This is what
     stops a two-facility sector producing a 100% "spike" off a tiny base.
  5. score = 100 * (0.5 * relative + 0.5 * money) * confidence
       relative   = min(1, adverse_delta / indicator.full_move)
       money      = min(1, exposure_at_risk / (2% of book EAD))
       confidence = min(1, min(facilities_old, facilities_new) / 5)
  6. De-duplication, so five cards are five issues:
       - one candidate per (sector, indicator family): the highest score wins,
         which is why "ECL up", "ECL coverage up" and "ECL outgrew exposure"
         cannot occupy three of the five slots for one sector;
       - then a round-robin over sectors, so five DIFFERENT sectors appear
         before any sector takes a second slot.
  7. Ties break on: score, then exposure_at_risk, then adverse_delta, then
     sector name, then indicator id. Every step is total and reproducible.

Fewer than five qualifying items is a legitimate outcome and is reported as
such. Padding the list with movements that failed a materiality gate would
make the feed worse than empty.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Gates and scales, expressed as fractions of the book's own EAD so the feed
#: behaves the same on a larger portfolio without being re-tuned.
MATERIAL_FRACTION = 0.0005        # 5 bps of book EAD
MIN_SECTOR_FRACTION = 0.005       # 50 bps of book EAD
FULL_MONEY_FRACTION = 0.02        # 2% of book EAD scores full on money
MIN_FACILITIES = 2
CONFIDENT_FACILITIES = 5
TOP_N = 5

FAMILY_STAGE = "stage_migration"
FAMILY_ECL = "ecl"
FAMILY_QUALITY = "credit_quality"
FAMILY_COLLATERAL = "collateral"
FAMILY_COVENANT = "covenant"
FAMILY_CONCENTRATION = "concentration"
FAMILY_ARREARS = "arrears"

#: What an item is ABOUT. The two dashboards are two questions, and the scope
#: is what separates them: "which segments deteriorated" is not the same
#: question as "what moved in ECL", and a book-wide observation or a single
#: borrower is not an answer to the first one.
SCOPE_SEGMENT = "segment"
SCOPE_PORTFOLIO = "portfolio"
SCOPE_BORROWER = "borrower"


class AttentionUnavailable(Exception):
    """The feed could not be computed. Carries a code and a plain reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Indicator:
    """One measurable movement, and everything needed to judge it."""

    id: str
    family: str
    label: str
    #: +1 when a rise is deterioration, -1 when a fall is.
    direction: int
    #: "share" and "ratio" are fractions of the sector; "amount" is money.
    kind: str
    #: The movement that scores full on relative severity.
    full_move: float
    unit: str
    headline: str
    explanation: str
    numerator: str = ""
    denominator: str = ""

    def value(self, row: dict[str, Any]) -> float | None:
        raise NotImplementedError


def _ratio(numerator: Any, denominator: Any) -> float | None:
    """A share, or None. A zero or missing denominator is never a zero share."""
    if numerator is None or denominator in (None, 0):
        return None
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return None
    if den == 0 or math.isnan(num) or math.isnan(den):
        return None
    return num / den


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


#: Every indicator: how its value is read out of the aggregate row, what a
#: full-severity move looks like, and how it is described to a credit officer.
INDICATORS: tuple[dict[str, Any], ...] = (
    {"id": "stage2_share", "family": FAMILY_STAGE,
     "label": "Stage 2 share of exposure", "direction": 1, "kind": "share",
     "full_move": 0.10, "unit": "share_of_sector_ead",
     "numerator": "stage2_ead", "denominator": "ead",
     "headline": "{sector}: Stage 2 share of exposure increased",
     "explanation": ("More of the sector's exposure now sits in Stage 2 than "
                     "in the comparison quarter, which means lifetime ECL is "
                     "being carried on a larger share of the book.")},
    {"id": "stage3_share", "family": FAMILY_STAGE,
     "label": "Stage 3 share of exposure", "direction": 1, "kind": "share",
     "full_move": 0.05, "unit": "share_of_sector_ead",
     "numerator": "stage3_ead", "denominator": "ead",
     "headline": "{sector}: Stage 3 exposure increased",
     "explanation": ("A larger share of the sector is credit-impaired than in "
                     "the comparison quarter.")},
    {"id": "ecl_coverage", "family": FAMILY_ECL,
     "label": "ECL coverage of exposure", "direction": 1, "kind": "ratio",
     "full_move": 0.01, "unit": "share_of_sector_ead",
     "numerator": "ecl", "denominator": "ead",
     "headline": "{sector}: ECL coverage increased",
     "explanation": ("The sector is carrying more expected credit loss per "
                     "unit of exposure than in the comparison quarter.")},
    {"id": "ecl_amount", "family": FAMILY_ECL,
     "label": "Reported ECL", "direction": 1, "kind": "amount",
     "full_move": 0.0, "unit": "reporting_currency",
     "numerator": "ecl", "denominator": "",
     "headline": "{sector}: reported ECL increased",
     "explanation": ("The absolute expected credit loss recorded against the "
                     "sector is higher than in the comparison quarter.")},
    {"id": "weighted_pd", "family": FAMILY_QUALITY,
     "label": "Exposure-weighted 12-month PD", "direction": 1, "kind": "ratio",
     "full_move": 0.02, "unit": "probability",
     "numerator": "pd_weight", "denominator": "pd_base",
     "observation_base": "pd_base", "min_observation": 0.5,
     "headline": "{sector}: exposure-weighted PD increased",
     "explanation": ("Weighted by exposure, the sector's 12-month "
                     "point-in-time probability of default is higher than in "
                     "the comparison quarter.")},
    {"id": "rating_rank", "family": FAMILY_QUALITY,
     "label": "Exposure-weighted rating rank", "direction": 1, "kind": "ratio",
     "full_move": 1.0, "unit": "rating_notches",
     "numerator": "rating_weight", "denominator": "rating_base",
     "observation_base": "rating_base", "min_observation": 0.5,
     "headline": "{sector}: internal ratings moved down",
     "explanation": ("Weighted by exposure, borrowers in this sector sit at a "
                     "weaker average internal rating than in the comparison "
                     "quarter. A higher rank is a weaker grade.")},
    {"id": "uncovered_share", "family": FAMILY_COLLATERAL,
     "label": "Exposure with collateral cover below 1x", "direction": 1,
     "kind": "share", "full_move": 0.10, "unit": "share_of_sector_ead",
     "numerator": "uncovered_ead", "denominator": "ead",
     "headline": "{sector}: less of the exposure is collateralised",
     "explanation": ("More of the sector's exposure now has recorded "
                     "collateral worth less than the exposure itself.")},
    {"id": "covenant_breach_share", "family": FAMILY_COVENANT,
     "label": "Exposure with a covenant breach", "direction": 1,
     "kind": "share", "full_move": 0.10, "unit": "share_of_sector_ead",
     "numerator": "breach_ead", "denominator": "covenant_base",
     "observation_base": "covenant_base", "min_observation": 0.2,
     "headline": "{sector}: more exposure sits under a covenant breach",
     "explanation": ("A larger share of the exposure with recorded covenants "
                     "is in breach or has negative headroom.")},
    {"id": "past_due_share", "family": FAMILY_ARREARS,
     "label": "Exposure past due", "direction": 1, "kind": "share",
     "full_move": 0.10, "unit": "share_of_sector_ead",
     "numerator": "past_due_ead", "denominator": "ead",
     "headline": "{sector}: more exposure is past due",
     "explanation": ("A larger share of the sector's exposure was past due at "
                     "the reporting date.")},
    {"id": "concentration_share", "family": FAMILY_CONCENTRATION,
     "label": "Share of total book exposure", "direction": 1, "kind": "share",
     "full_move": 0.03, "unit": "share_of_book_ead",
     "numerator": "ead", "denominator": "book_ead",
     "headline": "{sector}: concentration in the book increased",
     "explanation": ("The sector is a larger part of total exposure than in "
                     "the comparison quarter, so the same deterioration would "
                     "now cost more.")},
)

_BY_ID = {spec["id"]: spec for spec in INDICATORS}


# ---- the data layer -----------------------------------------------------

#: Server-authored, fixed, parameterised only by the three quarters. This is
#: CreditProbe computing a dashboard, not the analyst writing an analysis, so
#: the SQL is reviewable here rather than generated per request.
SQL_FACILITY = """
SELECT reporting_quarter,
       sector_name,
       COUNT(*)                                     AS facilities,
       COUNT(DISTINCT borrower_id)                  AS borrowers,
       SUM(ead_reported)                            AS ead,
       SUM(ecl_reported)                            AS ecl,
       SUM(CASE WHEN ifrs9_stage = 2 THEN ead_reported ELSE 0 END)
                                                    AS stage2_ead,
       SUM(CASE WHEN ifrs9_stage = 3 THEN ead_reported ELSE 0 END)
                                                    AS stage3_ead,
       SUM(CASE WHEN pd_pit_12m IS NOT NULL THEN ead_reported END)
                                                    AS pd_base,
       SUM(CASE WHEN pd_pit_12m IS NOT NULL
                THEN ead_reported * pd_pit_12m END) AS pd_weight,
       SUM(CASE WHEN collateral_coverage_ratio IS NOT NULL
                     AND collateral_coverage_ratio < 1
                THEN ead_reported ELSE 0 END)       AS uncovered_ead,
       SUM(CASE WHEN days_past_due IS NOT NULL AND days_past_due > 0
                THEN ead_reported ELSE 0 END)       AS past_due_ead
FROM cockpit_facility_quarter
WHERE reporting_quarter IN ({quarters})
  AND sector_name IS NOT NULL
GROUP BY 1, 2
"""

SQL_RATING = """
WITH borrower_ead AS (
  SELECT reporting_quarter, borrower_id, sector_name,
         SUM(ead_reported) AS ead
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ({quarters}) AND sector_name IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT b.reporting_quarter,
       b.sector_name,
       SUM(CASE WHEN r.rating_rank IS NOT NULL THEN b.ead END)
           AS rating_base,
       SUM(CASE WHEN r.rating_rank IS NOT NULL
                THEN b.ead * r.rating_rank END)     AS rating_weight
FROM borrower_ead b
LEFT JOIN cockpit_rating_ratio_quarter r
       ON r.borrower_id = b.borrower_id
      AND r.reporting_quarter = b.reporting_quarter
GROUP BY 1, 2
"""

SQL_COVENANT = """
WITH borrower_ead AS (
  SELECT reporting_quarter, borrower_id, sector_name,
         SUM(ead_reported) AS ead
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ({quarters}) AND sector_name IS NOT NULL
  GROUP BY 1, 2, 3
),
breaches AS (
  -- OBSERVED tests only. This release records covenant headroom in alternate
  -- quarters: a quarter with covenant rows but no headroom and no breach date
  -- is a quarter where the test was not observed, not a quarter with no
  -- breaches. Counting those rows in the base produced a 0% -> 48% "jump"
  -- across four sectors at once, which is what a data gap looks like when it
  -- is read as a movement.
  SELECT reporting_quarter, borrower_id,
         MAX(CASE WHEN breach_date IS NOT NULL
                    OR (headroom_value IS NOT NULL AND headroom_value < 0)
                  THEN 1 ELSE 0 END)                AS breached
  FROM cockpit_covenant_quarter
  WHERE reporting_quarter IN ({quarters})
    AND (headroom_value IS NOT NULL OR breach_date IS NOT NULL)
  GROUP BY 1, 2
)
SELECT b.reporting_quarter,
       b.sector_name,
       SUM(CASE WHEN x.borrower_id IS NOT NULL THEN b.ead END)
           AS covenant_base,
       SUM(CASE WHEN x.breached = 1 THEN b.ead ELSE 0 END)
           AS breach_ead
FROM borrower_ead b
LEFT JOIN breaches x
       ON x.borrower_id = b.borrower_id
      AND x.reporting_quarter = b.reporting_quarter
GROUP BY 1, 2
"""

SQL_TOP_BORROWERS = """
SELECT reporting_quarter, sector_name, borrower_id, borrower_name,
       SUM(ead_reported) AS ead,
       SUM(ecl_reported) AS ecl
FROM cockpit_facility_quarter
WHERE reporting_quarter = '{quarter}' AND sector_name IS NOT NULL
GROUP BY 1, 2, 3, 4
"""


def _quarter_list(quarters: list[str]) -> str:
    """Quarter labels for an IN list. Validated, never interpolated blind."""
    safe = []
    for q in quarters:
        text = str(q)
        if not text or len(text) > 12 or not text.replace("Q", "").isdigit():
            raise AttentionUnavailable(
                "ATTENTION_UNAVAILABLE",
                f"{text!r} is not a reporting-quarter label.")
        safe.append(f"'{text}'")
    return ", ".join(safe)


def _rows(session: Any, sql: str, *, deadline_seconds: float) -> list[dict]:
    from backend.cockpit_agentic import sql as v3_sql

    result = v3_sql.execute(sql, session, deadline_seconds=deadline_seconds)
    columns = list(getattr(result, "columns", ()) or ())
    out = []
    for row in getattr(result, "rows", ()) or ():
        out.append(dict(zip(columns, row)) if not isinstance(row, dict)
                   else dict(row))
    return out


def _merge(*sets: list[dict]) -> dict[tuple[str, str], dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in sets:
        for row in rows:
            key = (str(row.get("reporting_quarter")),
                   str(row.get("sector_name")))
            merged.setdefault(key, {"reporting_quarter": key[0],
                                    "sector_name": key[1]}).update(row)
    return merged


# ---- candidates and scoring --------------------------------------------

@dataclass
class Candidate:
    indicator: dict[str, Any]
    sector: str
    basis: str                      # "prior_quarter" | "prior_year"
    quarter: str
    comparison_quarter: str
    value_old: float
    value_new: float
    delta: float
    adverse_delta: float
    exposure_at_risk: float
    score: float
    relative: float
    money: float
    confidence: float
    facilities_old: int
    facilities_new: int
    sector_ead_new: float
    sector_ead_old: float
    numerator_old: float | None = None
    numerator_new: float | None = None
    denominator_old: float | None = None
    denominator_new: float | None = None

    @property
    def family(self) -> str:
        return str(self.indicator["family"])

    def item_id(self) -> str:
        raw = "|".join([self.sector, str(self.indicator["id"]), self.basis,
                        self.quarter, self.comparison_quarter])
        return "att-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _candidate(spec: dict[str, Any], sector: str, basis: str,
               old: dict[str, Any], new: dict[str, Any], *,
               book_ead_new: float, book_ead_old: float,
               ) -> tuple[Candidate | None, str]:
    """One indicator for one sector on one comparison basis.

    Returns the candidate, or None with the exact reason it was dropped. The
    reason is kept rather than discarded: "Construction did not appear" is a
    question the dashboard has to be able to answer.
    """
    num_key, den_key = spec.get("numerator", ""), spec.get("denominator", "")

    def read(row: dict[str, Any]) -> tuple[float | None, Any, Any]:
        numerator = row.get(num_key) if num_key else None
        if spec["kind"] == "amount":
            return _amount(numerator), numerator, None
        denominator = (book_ead_new if den_key == "book_ead" and row is new
                       else book_ead_old if den_key == "book_ead"
                       else row.get(den_key))
        return _ratio(numerator, denominator), numerator, denominator

    value_old, num_old, den_old = read(old)
    value_new, num_new, den_new = read(new)
    if value_old is None or value_new is None:
        return None, "missing_value"

    facilities_old = int(old.get("facilities") or 0)
    facilities_new = int(new.get("facilities") or 0)
    ead_old = ead_old_raw = _amount(old.get("ead")) or 0.0
    ead_new = ead_new_raw = _amount(new.get("ead")) or 0.0

    delta = value_new - value_old
    adverse = delta * int(spec["direction"])
    if adverse <= 0:
        return None, "no_deterioration"

    exposure_at_risk = (adverse if spec["kind"] == "amount"
                        else adverse * ead_new)

    observation = str(spec.get("observation_base") or "")
    if observation:
        # How much of the sector the measure was actually observed over. A
        # measure recorded for a tenth of the sector cannot say the sector
        # deteriorated, and treating an unobserved quarter as zero is how a
        # reporting gap becomes a headline.
        floor_share = float(spec.get("min_observation") or 0.0)
        for row, ead in ((old, ead_old_raw), (new, ead_new_raw)):
            share = _ratio(row.get(observation), ead)
            if share is None or share < floor_share:
                return None, "insufficient_observation"

    if min(facilities_old, facilities_new) < MIN_FACILITIES:
        return None, "below_minimum_facility_count"
    floor = MIN_SECTOR_FRACTION * max(book_ead_new, book_ead_old)
    if min(ead_old, ead_new) < floor:
        return None, "sector_below_minimum_exposure"
    if exposure_at_risk < MATERIAL_FRACTION * book_ead_new:
        return None, "below_materiality"

    full_move = (FULL_MONEY_FRACTION * book_ead_new
                 if spec["kind"] == "amount" else float(spec["full_move"]))
    full_money = FULL_MONEY_FRACTION * book_ead_new
    # x / (x + reference): 0 at no movement, 0.5 at the reference movement,
    # approaching 1 but never reaching it. A hard min(1, x/reference) made
    # every large movement score exactly 100, so the five biggest issues in
    # the book ranked on a tie-break instead of on size.
    relative = adverse / (adverse + full_move) if full_move > 0 else 0.0
    money = (exposure_at_risk / (exposure_at_risk + full_money)
             if full_money > 0 else 0.0)
    confidence = min(1.0, min(facilities_old, facilities_new)
                     / CONFIDENT_FACILITIES)
    score = round(100.0 * (0.5 * relative + 0.5 * money) * confidence, 4)

    return Candidate(
        indicator=spec, sector=sector, basis=basis,
        quarter=str(new["reporting_quarter"]),
        comparison_quarter=str(old["reporting_quarter"]),
        value_old=value_old, value_new=value_new, delta=delta,
        adverse_delta=adverse, exposure_at_risk=exposure_at_risk,
        score=score, relative=relative, money=money, confidence=confidence,
        facilities_old=facilities_old, facilities_new=facilities_new,
        sector_ead_new=ead_new, sector_ead_old=ead_old,
        numerator_old=_amount(num_old), numerator_new=_amount(num_new),
        denominator_old=_amount(den_old), denominator_new=_amount(den_new),
    ), ""


def _sort_key(c: Candidate) -> tuple:
    """Total and reproducible. Nothing here can depend on dict ordering."""
    return (-c.score, -c.exposure_at_risk, -c.adverse_delta, c.sector,
            str(c.indicator["id"]), c.basis)


def select(candidates: list[Candidate], *, limit: int = TOP_N
           ) -> list[Candidate]:
    """De-duplicate, then fill the list for breadth on both axes.

    Four passes, each walking the remaining candidates in score order:

      1. a segment not yet shown AND an issue type not yet shown;
      2. a segment not yet shown;
      3. an issue type not yet shown;
      4. whatever is left.

    Pass 1 is what stops five cards reading "more exposure under a covenant
    breach" in five different sectors when the book also has a stage
    migration and a ratings move worth seeing. Passes 2 to 4 mean breadth is
    a preference, never a reason to drop a real issue or to pad the list.
    """
    best: dict[tuple[str, str], Candidate] = {}
    for candidate in sorted(candidates, key=_sort_key):
        key = (candidate.sector, candidate.family)
        if key not in best:
            best[key] = candidate

    remaining = sorted(best.values(), key=_sort_key)
    chosen: list[Candidate] = []
    sectors: set[str] = set()
    families: set[str] = set()

    def take(accept) -> None:
        for candidate in list(remaining):
            if len(chosen) >= limit:
                return
            if not accept(candidate):
                continue
            chosen.append(candidate)
            remaining.remove(candidate)
            sectors.add(candidate.sector)
            families.add(candidate.family)

    take(lambda c: c.sector not in sectors and c.family not in families)
    take(lambda c: c.sector not in sectors)
    take(lambda c: c.family not in families)
    take(lambda c: True)
    return chosen[:limit]


# ---- presentation -------------------------------------------------------

def _fmt_amount(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}".strip()


def _fmt_value(value: float, unit: str, currency: str) -> str:
    if unit in ("share_of_sector_ead", "share_of_book_ead", "probability"):
        return f"{value * 100:.2f}%"
    if unit == "rating_notches":
        return f"{value:.2f} notches"
    return _fmt_amount(value, currency)


def _fmt_delta(value: float, unit: str, currency: str) -> str:
    if unit in ("share_of_sector_ead", "share_of_book_ead", "probability"):
        return f"{value * 100:+.2f} pp"
    if unit == "rating_notches":
        return f"{value:+.2f} notches"
    sign = "+" if value >= 0 else ""
    return f"{sign}{_fmt_amount(value, currency)}"


def _severity(score: float) -> str:
    """A label for the card. The score itself is always carried with it."""
    if score >= 50:
        return "high"
    if score >= 25:
        return "moderate"
    return "low"


_BASIS_LABEL = {"prior_quarter": "the previous quarter",
                "prior_year": "the same quarter a year earlier"}

#: What a credit officer would look at next, per family. These are navigation
#: prompts, not findings, and they are offered only where the pinned release
#: actually holds the level they name.
_REVIEW_NEXT = {
    FAMILY_STAGE: ["Which borrowers moved into Stage 2 and when",
                   "Whether this was migration or new exposure",
                   "The SICR reason recorded against those facilities"],
    FAMILY_ECL: ["The borrowers contributing most of the ECL increase",
                 "Whether ECL grew faster than exposure",
                 "The split between modelled ECL and overlay"],
    FAMILY_QUALITY: ["Which borrowers were downgraded, and from what",
                     "Whether PD moved on ratings or on model inputs",
                     "The financial ratios behind the weaker grades"],
    FAMILY_COLLATERAL: ["Which facilities lost cover, and on what collateral",
                        "Whether valuations aged or values fell",
                        "Collateral allocated across several facilities"],
    FAMILY_COVENANT: ["Which covenants are in breach and their cure status",
                      "Headroom trend on the binding covenants",
                      "Whether the breaches concentrate in a few borrowers"],
    FAMILY_ARREARS: ["The borrowers past due and by how many days",
                     "Whether arrears are recurring or one-off",
                     "Stage assignment against the days past due"],
    FAMILY_CONCENTRATION: ["The largest borrowers driving the share",
                           "Whether the share grew on new exposure or on "
                           "others shrinking",
                           "Exposure against any internal sector limit"],
}


def _item(candidate: Candidate, *, release_id: str, currency: str,
          section: str) -> dict[str, Any]:
    spec = candidate.indicator
    unit = str(spec["unit"])
    basis = _BASIS_LABEL.get(candidate.basis, candidate.basis)
    movement = _fmt_delta(candidate.delta, unit, currency)

    key_numbers = [
        {"label": f"{spec['label']}, {candidate.quarter}",
         "value": _fmt_value(candidate.value_new, unit, currency),
         "raw": candidate.value_new},
        {"label": f"{spec['label']}, {candidate.comparison_quarter}",
         "value": _fmt_value(candidate.value_old, unit, currency),
         "raw": candidate.value_old},
        {"label": "Movement", "value": movement, "raw": candidate.delta},
        {"label": f"Sector exposure, {candidate.quarter}",
         "value": _fmt_amount(candidate.sector_ead_new, currency),
         "raw": candidate.sector_ead_new},
        {"label": "Exposure implied by the movement",
         "value": _fmt_amount(candidate.exposure_at_risk, currency),
         "raw": candidate.exposure_at_risk},
    ]

    return {
        "item_id": candidate.item_id(),
        "section": section,
        # Segment-level by construction: every candidate is one sector's
        # movement between two quarters. Declared rather than inferred, so
        # the invariant below can be checked rather than assumed.
        "scope": SCOPE_SEGMENT,
        "headline": str(spec["headline"]).format(sector=candidate.sector),
        "one_line": (f"{_fmt_value(candidate.value_old, unit, currency)} → "
                     f"{_fmt_value(candidate.value_new, unit, currency)} "
                     f"({movement}) against {basis}."),
        "segment": candidate.sector,
        "segment_dimension": "sector_name",
        "metric": str(spec["id"]),
        "metric_label": str(spec["label"]),
        "family": candidate.family,
        "reporting_quarter": candidate.quarter,
        "comparison_quarter": candidate.comparison_quarter,
        "comparison_basis": candidate.basis,
        "movement": movement,
        "severity": _severity(candidate.score),
        "score": candidate.score,
        "why_it_appeared": (
            f"{spec['label']} for {candidate.sector} moved {movement} between "
            f"{candidate.comparison_quarter} and {candidate.quarter}. On "
            f"{candidate.quarter} exposure of "
            f"{_fmt_amount(candidate.sector_ead_new, currency)}, that "
            f"movement covers "
            f"{_fmt_amount(candidate.exposure_at_risk, currency)} — above the "
            f"materiality floor for this book — across "
            f"{candidate.facilities_new} facilities. It scored "
            f"{candidate.score:.1f} of 100 and ranked in the top "
            f"{TOP_N} for this release."),
        "what_changed": str(spec["explanation"]),
        "key_numbers": key_numbers,
        "possible_drivers": [],          # filled by `_drivers`
        "what_to_review_next": list(_REVIEW_NEXT.get(candidate.family, [])),
        "evidence": {
            "release_id": release_id,
            "reporting_quarter": candidate.quarter,
            "comparison_quarter": candidate.comparison_quarter,
            "segment_dimension": "sector_name",
            "segment": candidate.sector,
            "metric": str(spec["id"]),
            "unit": unit,
            "numerator_field": spec.get("numerator") or None,
            "denominator_field": spec.get("denominator") or None,
            "numerator_old": candidate.numerator_old,
            "numerator_new": candidate.numerator_new,
            "denominator_old": candidate.denominator_old,
            "denominator_new": candidate.denominator_new,
            "value_old": candidate.value_old,
            "value_new": candidate.value_new,
            "delta": candidate.delta,
            "exposure_at_risk": candidate.exposure_at_risk,
            "facilities_old": candidate.facilities_old,
            "facilities_new": candidate.facilities_new,
            "sector_ead_old": candidate.sector_ead_old,
            "sector_ead_new": candidate.sector_ead_new,
            "reporting_currency": currency,
            "ranking_reason": {
                "score": candidate.score,
                "relative_severity": round(candidate.relative, 6),
                "money_severity": round(candidate.money, 6),
                "base_confidence": round(candidate.confidence, 6),
                "formula": ("100 * (0.5*relative + 0.5*money) * confidence; "
                        "relative = d/(d+full_move); "
                        "money = e/(e+2% of book EAD)"),
            },
        },
        "evidence_url": f"/api/v1/cockpit-v4/attention/{candidate.item_id()}",
    }


def _drivers(candidate: Candidate, old: dict[str, Any], new: dict[str, Any],
             *, book_ead_new: float, book_ead_old: float, currency: str,
             borrowers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Movements recorded alongside this one, stated as co-movement.

    Nothing here establishes cause. Two measures moving in the same quarter is
    an association, and it is written as one: a dashboard that says "ECL rose
    BECAUSE ratings fell" has asserted a causal claim off a correlation of
    two aggregates, which is precisely the kind of confident wrong answer this
    build exists to avoid.
    """
    out: list[dict[str, Any]] = []
    for spec in INDICATORS:
        if spec["id"] == candidate.indicator["id"]:
            continue
        if spec["family"] == candidate.family:
            continue
        sibling, _reason = _candidate(
            spec, candidate.sector, candidate.basis, old, new,
            book_ead_new=book_ead_new, book_ead_old=book_ead_old)
        if sibling is None:
            continue
        out.append({
            "relationship": "coincides with",
            "statement": (f"{spec['label']} also moved "
                          f"{_fmt_delta(sibling.delta, str(spec['unit']), currency)} "
                          f"over the same period."),
            "metric": str(spec["id"]),
            "delta": sibling.delta,
            "strength": round(sibling.relative, 6),
        })
    out.sort(key=lambda d: (-float(d["strength"]), str(d["metric"])))

    top = [b for b in borrowers if str(b.get("sector_name")) ==
           candidate.sector]
    top.sort(key=lambda b: (-(_amount(b.get("ecl")) or 0.0),
                            str(b.get("borrower_name") or "")))
    if top:
        sector_ecl = sum((_amount(b.get("ecl")) or 0.0) for b in top)
        leader = top[0]
        share = _ratio(leader.get("ecl"), sector_ecl)
        if share is not None and share > 0:
            out.append({
                "relationship": "associated with",
                "statement": (
                    f"{leader.get('borrower_name') or leader.get('borrower_id')} "
                    f"carries {share * 100:.1f}% of the sector's reported ECL "
                    f"at {candidate.quarter} "
                    f"({_fmt_amount(_amount(leader.get('ecl')) or 0.0, currency)})."),
                "metric": "borrower_ecl_concentration",
                "delta": None,
                "strength": round(share, 6),
            })
    if not out:
        out.append({
            "relationship": "none recorded",
            "statement": ("No other recorded measure for this sector moved "
                          "adversely over the same period. The movement "
                          "stands on its own."),
            "metric": "", "delta": None, "strength": 0.0})
    return out[:4]


def _drilldown(borrowers: list[dict[str, Any]], sector: str
               ) -> dict[str, Any]:
    """What this release can actually be drilled into. No invented hierarchy.

    The pinned Cockpit release carries sector -> borrower -> facility. It
    carries no subsegment column, so no subsegment level is offered and the
    absence is stated rather than papered over with a plausible-looking one.
    """
    here = [b for b in borrowers if str(b.get("sector_name")) == sector]
    return {
        "available": ["borrower", "facility"],
        "unavailable": ["subsegment"],
        "note": ("This release records sector, borrower and facility. It has "
                 "no subsegment level, so there is no subsegment breakdown to "
                 "show — drill to borrowers instead."),
        "borrower_count": len({str(b.get("borrower_id")) for b in here}),
        "suggested_questions": [
            f"Which borrowers in {sector} are behind this movement?",
            f"Show the facilities in {sector} that moved, with their stage "
            f"and ECL.",
            f"Was this migration or new exposure in {sector}?",
        ],
    }


# ---- ECL highlights -----------------------------------------------------

def _highlight(*, item_id_seed: str, headline: str, one_line: str,
               segment: str, metric: str, metric_label: str, family: str,
               quarter: str, comparison: str, why: str, what_changed: str,
               key_numbers: list[dict[str, Any]], evidence: dict[str, Any],
               release_id: str, currency: str,
               scope: str = SCOPE_SEGMENT) -> dict[str, Any]:
    item_id = "att-" + hashlib.sha256(
        item_id_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "item_id": item_id,
        "section": "ecl_highlights",
        "scope": scope,
        "headline": headline,
        "one_line": one_line,
        "segment": segment,
        "segment_dimension": "sector_name" if segment != "Whole book"
                             else "portfolio",
        "metric": metric,
        "metric_label": metric_label,
        "family": family,
        "reporting_quarter": quarter,
        "comparison_quarter": comparison,
        "comparison_basis": "prior_quarter",
        "movement": one_line,
        "severity": "informational",
        "score": None,
        "why_it_appeared": why,
        "what_changed": what_changed,
        "key_numbers": key_numbers,
        "possible_drivers": [],
        "what_to_review_next": [
            "The borrowers contributing most of the movement",
            "Whether ECL moved on exposure, on staging, or on model inputs",
            "The same measure one quarter and one year earlier",
        ],
        "evidence": {"release_id": release_id, "reporting_quarter": quarter,
                     "comparison_quarter": comparison,
                     "reporting_currency": currency, **evidence},
        "evidence_url": f"/api/v1/cockpit-v4/attention/{item_id}",
    }


def _ecl_highlights(by_sector: dict[str, dict[str, dict[str, Any]]], *,
                    quarter: str, comparison: str, book_new: dict[str, float],
                    book_old: dict[str, float], borrowers: list[dict],
                    release_id: str, currency: str) -> list[dict[str, Any]]:
    """Five ECL developments, one per family, in a fixed priority order.

    Diversity is structural rather than a judgement call: each candidate
    belongs to a family, at most one per family is taken, and they are
    considered in a published order. That is what stops five cards all saying
    "ECL went up" about the same movement from five angles.
    """
    out: list[dict[str, Any]] = []

    moves = []
    for sector, periods in by_sector.items():
        new, old = periods.get(quarter), periods.get(comparison)
        if not new or not old:
            continue
        ecl_new, ecl_old = _amount(new.get("ecl")), _amount(old.get("ecl"))
        ead_new, ead_old = _amount(new.get("ead")), _amount(old.get("ead"))
        if ecl_new is None or ecl_old is None:
            continue
        cov_new, cov_old = _ratio(ecl_new, ead_new), _ratio(ecl_old, ead_old)
        moves.append({"sector": sector, "ecl_new": ecl_new, "ecl_old": ecl_old,
                      "delta": ecl_new - ecl_old, "ead_new": ead_new,
                      "cov_new": cov_new, "cov_old": cov_old,
                      "cov_delta": (None if cov_new is None or cov_old is None
                                    else cov_new - cov_old)})

    # 1. Largest ECL increase by sector.
    rising = sorted([m for m in moves if m["delta"] > 0],
                    key=lambda m: (-m["delta"], m["sector"]))
    if rising:
        m = rising[0]
        out.append(_highlight(
            item_id_seed=f"ecl_increase|{m['sector']}|{quarter}|{comparison}",
            headline=f"{m['sector']} had the largest ECL increase",
            one_line=(f"{_fmt_amount(m['ecl_old'], currency)} → "
                      f"{_fmt_amount(m['ecl_new'], currency)} "
                      f"({_fmt_delta(m['delta'], 'reporting_currency', currency)})."),
            segment=m["sector"], metric="ecl_increase_sector",
            metric_label="Reported ECL", family="ecl_move", quarter=quarter,
            comparison=comparison,
            why=(f"Of the sectors whose ECL rose between {comparison} and "
                 f"{quarter}, {m['sector']} rose by the most in absolute "
                 f"terms."),
            what_changed=("Reported expected credit loss against this sector "
                          "is higher than in the previous quarter."),
            key_numbers=[
                {"label": f"ECL {comparison}",
                 "value": _fmt_amount(m["ecl_old"], currency),
                 "raw": m["ecl_old"]},
                {"label": f"ECL {quarter}",
                 "value": _fmt_amount(m["ecl_new"], currency),
                 "raw": m["ecl_new"]},
                {"label": "Movement",
                 "value": _fmt_delta(m["delta"], "reporting_currency",
                                     currency),
                 "raw": m["delta"]}],
            evidence={"segment": m["sector"], "segment_dimension":
                      "sector_name", "metric": "ecl_reported",
                      "unit": "reporting_currency",
                      "numerator_field": "ecl_reported",
                      "denominator_field": None,
                      "value_old": m["ecl_old"], "value_new": m["ecl_new"],
                      "delta": m["delta"],
                      "ranking_reason": {"rule": "max(ecl_new - ecl_old) "
                                                 "over sectors where the "
                                                 "delta is positive"}},
            release_id=release_id, currency=currency))

    # 2. Largest ECL coverage increase.
    cov = sorted([m for m in moves
                  if m["cov_delta"] is not None and m["cov_delta"] > 0],
                 key=lambda m: (-m["cov_delta"], m["sector"]))
    if cov:
        m = cov[0]
        out.append(_highlight(
            item_id_seed=f"ecl_coverage|{m['sector']}|{quarter}|{comparison}",
            headline=f"{m['sector']} had the largest ECL coverage increase",
            one_line=(f"{m['cov_old'] * 100:.2f}% → {m['cov_new'] * 100:.2f}% "
                      f"of exposure ({m['cov_delta'] * 100:+.2f} pp)."),
            segment=m["sector"], metric="ecl_coverage_increase",
            metric_label="ECL coverage of exposure", family="coverage",
            quarter=quarter, comparison=comparison,
            why=(f"Coverage isolates the part of the ECL movement that is not "
                 f"explained by exposure changing. {m['sector']} moved the "
                 f"most on that measure."),
            what_changed=("More expected credit loss is carried per unit of "
                          "exposure than in the previous quarter."),
            key_numbers=[
                {"label": f"Coverage {comparison}",
                 "value": f"{m['cov_old'] * 100:.2f}%", "raw": m["cov_old"]},
                {"label": f"Coverage {quarter}",
                 "value": f"{m['cov_new'] * 100:.2f}%", "raw": m["cov_new"]},
                {"label": "Movement",
                 "value": f"{m['cov_delta'] * 100:+.2f} pp",
                 "raw": m["cov_delta"]},
                {"label": f"Sector exposure {quarter}",
                 "value": _fmt_amount(m["ead_new"] or 0.0, currency),
                 "raw": m["ead_new"]}],
            evidence={"segment": m["sector"], "segment_dimension":
                      "sector_name", "metric": "ecl_coverage",
                      "unit": "share_of_sector_ead",
                      "numerator_field": "ecl_reported",
                      "denominator_field": "ead_reported",
                      "value_old": m["cov_old"], "value_new": m["cov_new"],
                      "delta": m["cov_delta"],
                      "ranking_reason": {"rule": "max(ecl/ead delta) over "
                                                 "sectors with a positive "
                                                 "delta"}},
            release_id=release_id, currency=currency))

    # 3. Largest single contributor to book ECL.
    contributors = sorted(
        [m for m in moves if m["ecl_new"] > 0],
        key=lambda m: (-m["ecl_new"], m["sector"]))
    if contributors and book_new.get("ecl"):
        m = contributors[0]
        share = _ratio(m["ecl_new"], book_new["ecl"]) or 0.0
        out.append(_highlight(
            item_id_seed=f"ecl_contributor|{m['sector']}|{quarter}",
            headline=f"{m['sector']} carries the most ECL in the book",
            one_line=(f"{_fmt_amount(m['ecl_new'], currency)}, "
                      f"{share * 100:.1f}% of total reported ECL."),
            segment=m["sector"], metric="ecl_largest_contributor",
            metric_label="Share of total reported ECL", family="contribution",
            quarter=quarter, comparison=comparison,
            why=("Largest absolute ECL of any sector at the latest reporting "
                 "quarter."),
            what_changed=("This is a level, not a movement: where the book's "
                          "expected credit loss currently sits."),
            key_numbers=[
                {"label": f"Sector ECL {quarter}",
                 "value": _fmt_amount(m["ecl_new"], currency),
                 "raw": m["ecl_new"]},
                {"label": f"Book ECL {quarter}",
                 "value": _fmt_amount(book_new["ecl"], currency),
                 "raw": book_new["ecl"]},
                {"label": "Share of book ECL",
                 "value": f"{share * 100:.1f}%", "raw": share}],
            evidence={"segment": m["sector"], "segment_dimension":
                      "sector_name", "metric": "ecl_share_of_book",
                      "unit": "share_of_book_ecl",
                      "numerator_field": "ecl_reported",
                      "denominator_field": "ecl_reported (whole book)",
                      "value_old": None, "value_new": share,
                      "delta": None,
                      "numerator_new": m["ecl_new"],
                      "denominator_new": book_new["ecl"],
                      "ranking_reason": {"rule": "max(sector ECL) at the "
                                                 "latest quarter"}},
            release_id=release_id, currency=currency))

    # 4. Borrower concentration in ECL.
    if borrowers and book_new.get("ecl"):
        ranked = sorted(borrowers,
                        key=lambda b: (-(_amount(b.get("ecl")) or 0.0),
                                       str(b.get("borrower_name") or "")))
        top = ranked[0]
        top_ecl = _amount(top.get("ecl")) or 0.0
        share = _ratio(top_ecl, book_new["ecl"]) or 0.0
        name = str(top.get("borrower_name") or top.get("borrower_id") or "")
        out.append(_highlight(
            item_id_seed=f"ecl_borrower|{top.get('borrower_id')}|{quarter}",
            headline=f"{name} is the largest single ECL contributor",
            one_line=(f"{_fmt_amount(top_ecl, currency)}, {share * 100:.1f}% "
                      f"of total reported ECL, in "
                      f"{top.get('sector_name')}."),
            segment=str(top.get("sector_name") or ""),
            metric="ecl_borrower_concentration",
            metric_label="Borrower share of total ECL", family="borrower",
            scope=SCOPE_BORROWER,
            quarter=quarter, comparison=comparison,
            why=("Largest reported ECL of any single borrower at the latest "
                 "reporting quarter."),
            what_changed=("A concentration observation, not a movement: how "
                          "much of the book's ECL sits with one name."),
            key_numbers=[
                {"label": "Borrower", "value": name, "raw": None},
                {"label": f"Borrower ECL {quarter}",
                 "value": _fmt_amount(top_ecl, currency), "raw": top_ecl},
                {"label": "Share of book ECL",
                 "value": f"{share * 100:.1f}%", "raw": share}],
            evidence={"segment": str(top.get("sector_name") or ""),
                      "segment_dimension": "sector_name",
                      "borrower_id": str(top.get("borrower_id") or ""),
                      "metric": "borrower_ecl_share_of_book",
                      "unit": "share_of_book_ecl",
                      "numerator_field": "ecl_reported",
                      "denominator_field": "ecl_reported (whole book)",
                      "value_old": None, "value_new": share, "delta": None,
                      "numerator_new": top_ecl,
                      "denominator_new": book_new["ecl"],
                      "ranking_reason": {"rule": "max(borrower ECL) at the "
                                                 "latest quarter"}},
            release_id=release_id, currency=currency))

    # 5. How much of the book's ECL sits on Stage 2 exposure, and its move.
    if book_new.get("ead") and book_old.get("ead"):
        s2_new = _ratio(book_new.get("stage2_ead"), book_new["ead"])
        s2_old = _ratio(book_old.get("stage2_ead"), book_old["ead"])
        if s2_new is not None and s2_old is not None:
            delta = s2_new - s2_old
            direction = ("rose" if delta > 0 else
                         "fell" if delta < 0 else "was unchanged")
            out.append(_highlight(
                item_id_seed=f"stage_mix|book|{quarter}|{comparison}",
                headline=f"Stage 2 share of the book {direction}",
                one_line=(f"{s2_old * 100:.2f}% → {s2_new * 100:.2f}% of "
                          f"total exposure ({delta * 100:+.2f} pp)."),
                segment="Whole book", metric="stage2_share_of_book",
                metric_label="Stage 2 share of book exposure",
                family="stage_mix", scope=SCOPE_PORTFOLIO,
                quarter=quarter, comparison=comparison,
                why=("Stage mix is what moves lifetime ECL onto or off the "
                     "book, so it is reported whichever way it went."),
                what_changed=("The proportion of total exposure carrying "
                              "lifetime ECL changed."),
                key_numbers=[
                    {"label": f"Stage 2 share {comparison}",
                     "value": f"{s2_old * 100:.2f}%", "raw": s2_old},
                    {"label": f"Stage 2 share {quarter}",
                     "value": f"{s2_new * 100:.2f}%", "raw": s2_new},
                    {"label": "Movement",
                     "value": f"{delta * 100:+.2f} pp", "raw": delta}],
                evidence={"segment": "Whole book",
                          "segment_dimension": "portfolio",
                          "metric": "stage2_share_of_book",
                          "unit": "share_of_book_ead",
                          "numerator_field": "ead_reported where stage = 2",
                          "denominator_field": "ead_reported",
                          "value_old": s2_old, "value_new": s2_new,
                          "delta": delta,
                          "ranking_reason": {"rule": "always reported: the "
                                                     "book-level stage mix"}},
                release_id=release_id, currency=currency))

    # 6. A meaningful reduction, when one exists. Reported because a falling
    #    ECL is a development a credit officer wants to see and question.
    falling = sorted([m for m in moves if m["delta"] < 0],
                     key=lambda m: (m["delta"], m["sector"]))
    if falling:
        m = falling[0]
        out.append(_highlight(
            item_id_seed=f"ecl_reduction|{m['sector']}|{quarter}|{comparison}",
            headline=f"{m['sector']} had the largest ECL reduction",
            one_line=(f"{_fmt_amount(m['ecl_old'], currency)} → "
                      f"{_fmt_amount(m['ecl_new'], currency)} "
                      f"({_fmt_delta(m['delta'], 'reporting_currency', currency)})."),
            segment=m["sector"], metric="ecl_reduction_sector",
            metric_label="Reported ECL", family="improvement",
            quarter=quarter, comparison=comparison,
            why=("Largest absolute fall in reported ECL of any sector between "
                 "the two quarters."),
            what_changed=("Expected credit loss recorded against this sector "
                          "is lower than in the previous quarter."),
            key_numbers=[
                {"label": f"ECL {comparison}",
                 "value": _fmt_amount(m["ecl_old"], currency),
                 "raw": m["ecl_old"]},
                {"label": f"ECL {quarter}",
                 "value": _fmt_amount(m["ecl_new"], currency),
                 "raw": m["ecl_new"]},
                {"label": "Movement",
                 "value": _fmt_delta(m["delta"], "reporting_currency",
                                     currency),
                 "raw": m["delta"]}],
            evidence={"segment": m["sector"], "segment_dimension":
                      "sector_name", "metric": "ecl_reported",
                      "unit": "reporting_currency",
                      "numerator_field": "ecl_reported",
                      "denominator_field": None,
                      "value_old": m["ecl_old"], "value_new": m["ecl_new"],
                      "delta": m["delta"],
                      "ranking_reason": {"rule": "min(ecl_new - ecl_old) "
                                                 "over sectors where the "
                                                 "delta is negative"}},
            release_id=release_id, currency=currency))

    seen_families: set[str] = set()
    picked: list[dict[str, Any]] = []
    for item in out:
        if item["family"] in seen_families:
            continue
        seen_families.add(str(item["family"]))
        picked.append(item)
        if len(picked) >= TOP_N:
            break
    return picked


# ---- computing the feed -------------------------------------------------

def _prior_year(quarter: str, quarters: list[str]) -> str:
    """Same quarter one year earlier, if the calendar holds it."""
    if quarter not in quarters:
        return ""
    index = quarters.index(quarter)
    return quarters[index - 4] if index >= 4 else ""


def _prior_quarter(quarter: str, quarters: list[str]) -> str:
    if quarter not in quarters:
        return ""
    index = quarters.index(quarter)
    return quarters[index - 1] if index >= 1 else ""


def check_composition(segments: list[dict[str, Any]],
                      highlights: list[dict[str, Any]]) -> None:
    """Two dashboards, two questions, no overlap.

    Checked here rather than trusted. The composition defect that put ECL
    highlights and a single borrower into the segment list was a rendering
    decision, and an invariant that only lives in a component is one the next
    component can break again.

    "Which segments deteriorated" is not the same question as "what moved in
    ECL". A book-wide observation and a single borrower are answers to the
    second and not to the first, however interesting they are.
    """
    stray = [item.get("item_id") for item in segments
             if item.get("scope") != SCOPE_SEGMENT]
    if stray:
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            f"segments requiring attention holds segment-scoped items only; "
            f"{stray} are not.")
    shared = ({item.get("item_id") for item in segments}
              & {item.get("item_id") for item in highlights})
    if shared:
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            f"an item belongs to one dashboard, not both: {sorted(shared)}.")
    repeated = ({item.get("headline") for item in segments}
                & {item.get("headline") for item in highlights})
    if repeated:
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            f"two cards may not say the same sentence: {sorted(repeated)}.")


def compute(*, session: Any, release_id: str, quarters: list[str],
            currency: str, deadline_seconds: float = 20.0) -> dict[str, Any]:
    """Build the whole feed. Pure function of the release and the calendar."""
    started = time.monotonic()
    populated = [str(q) for q in quarters if q]
    if not populated:
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            "the pinned release reports no populated quarter.")

    latest = populated[-1]
    prior = _prior_quarter(latest, populated)
    year_ago = _prior_year(latest, populated)
    wanted = [q for q in (latest, prior, year_ago) if q]

    quarter_list = _quarter_list(wanted)
    facility_sql = SQL_FACILITY.format(quarters=quarter_list)
    rating_sql = SQL_RATING.format(quarters=quarter_list)
    covenant_sql = SQL_COVENANT.format(quarters=quarter_list)
    borrower_sql = SQL_TOP_BORROWERS.format(quarter=latest)

    try:
        facility = _rows(session, facility_sql,
                         deadline_seconds=deadline_seconds)
        rating = _rows(session, rating_sql, deadline_seconds=deadline_seconds)
        covenant = _rows(session, covenant_sql,
                         deadline_seconds=deadline_seconds)
        borrowers = _rows(session, borrower_sql,
                          deadline_seconds=deadline_seconds)
    except AttentionUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            f"the attention feed could not be computed from the pinned "
            f"release: {exc}") from exc

    merged = _merge(facility, rating, covenant)
    by_sector: dict[str, dict[str, dict[str, Any]]] = {}
    for (quarter, sector), row in merged.items():
        by_sector.setdefault(sector, {})[quarter] = row

    def book(quarter: str) -> dict[str, float]:
        totals = {"ead": 0.0, "ecl": 0.0, "stage2_ead": 0.0,
                  "stage3_ead": 0.0}
        for periods in by_sector.values():
            row = periods.get(quarter)
            if not row:
                continue
            for key in totals:
                totals[key] += _amount(row.get(key)) or 0.0
        return totals

    book_latest = book(latest)
    if book_latest["ead"] <= 0:
        raise AttentionUnavailable(
            "ATTENTION_UNAVAILABLE",
            f"the pinned release records no exposure at {latest}.")

    candidates: list[Candidate] = []
    skipped: list[dict[str, Any]] = []
    for basis, comparison in (("prior_quarter", prior),
                              ("prior_year", year_ago)):
        if not comparison:
            continue
        book_old = book(comparison)
        for sector, periods in sorted(by_sector.items()):
            new, old = periods.get(latest), periods.get(comparison)
            if not new or not old:
                skipped.append({"sector": sector, "basis": basis,
                                "reason": "sector_absent_in_one_period"})
                continue
            for spec in INDICATORS:
                candidate, reason = _candidate(
                    spec, sector, basis, old, new,
                    book_ead_new=book_latest["ead"],
                    book_ead_old=book_old["ead"] or book_latest["ead"])
                if candidate is None:
                    skipped.append({"sector": sector, "basis": basis,
                                    "metric": spec["id"], "reason": reason})
                else:
                    candidates.append(candidate)

    chosen = select(candidates, limit=TOP_N)
    segments = []
    for candidate in chosen:
        periods = by_sector[candidate.sector]
        item = _item(candidate, release_id=release_id, currency=currency,
                     section="segments_requiring_attention")
        item["possible_drivers"] = _drivers(
            candidate, periods[candidate.comparison_quarter],
            periods[candidate.quarter],
            book_ead_new=book_latest["ead"],
            book_ead_old=book(candidate.comparison_quarter)["ead"]
            or book_latest["ead"],
            currency=currency, borrowers=borrowers)
        item["drilldown"] = _drilldown(borrowers, candidate.sector)
        segments.append(item)

    highlights = _ecl_highlights(
        by_sector, quarter=latest, comparison=prior or latest,
        book_new=book_latest, book_old=book(prior) if prior else book_latest,
        borrowers=borrowers, release_id=release_id, currency=currency)
    for item in highlights:
        item["drilldown"] = _drilldown(borrowers, str(item["segment"]))

    check_composition(segments, highlights)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "release_id": release_id,
        "reporting_quarter": latest,
        "prior_quarter": prior,
        "prior_year_quarter": year_ago,
        "reporting_currency": currency,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"),
        "computed_ms": elapsed_ms,
        "model_calls": 0,
        "segments_requiring_attention": segments,
        "ecl_highlights": highlights,
        "segment_note": (
            "" if len(segments) >= TOP_N else
            f"{len(segments)} of {TOP_N} slots are filled. The remaining "
            f"movements in this release did not clear the materiality floor, "
            f"and nothing is shown that did not."),
        "ownership": {
            "functionality": "cockpit",
            "basis": "recorded_book",
            "note": ("Movements in the recorded book between two reporting "
                     "quarters. Not Early Warning: no live signal, no "
                     "behavioural score and no prediction is used here."),
        },
        "method": {
            "indicators": [
                {"id": s["id"], "family": s["family"], "label": s["label"],
                 "direction": "higher_is_worse" if s["direction"] > 0
                              else "lower_is_worse",
                 "unit": s["unit"], "full_move": s["full_move"]}
                for s in INDICATORS],
            "gates": {
                "materiality_fraction_of_book_ead": MATERIAL_FRACTION,
                "minimum_sector_fraction_of_book_ead": MIN_SECTOR_FRACTION,
                "minimum_facilities": MIN_FACILITIES,
                "confident_facilities": CONFIDENT_FACILITIES,
                "full_money_fraction_of_book_ead": FULL_MONEY_FRACTION},
            "formula": ("100 * (0.5*relative + 0.5*money) * confidence, "
                        "relative = d/(d+full_move), "
                        "money = e/(e+2% of book EAD)"),
            "observation_gates": {
                s["id"]: {"base": s["observation_base"],
                          "minimum_share_of_sector_ead": s["min_observation"]}
                for s in INDICATORS if s.get("observation_base")},
            "tie_break": ["score", "exposure_at_risk", "adverse_delta",
                          "segment", "metric", "comparison_basis"],
            "deduplication": ["one item per (segment, family)",
                              "then round-robin across segments"],
            "book_totals": {"latest": book_latest,
                            "comparison": book(prior) if prior else {}},
            "candidates_considered": len(candidates),
            "candidates_dropped": len(skipped),
            "sql": {"facility": facility_sql, "rating": rating_sql,
                    "covenant": covenant_sql, "borrower": borrower_sql},
        },
        "dropped": skipped,
    }


# ---- cache --------------------------------------------------------------

_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def cache_key(release_id: str, tenant_id: str) -> tuple[str, str, str]:
    """Release, tenant, and the FINGERPRINT of the release's bytes.

    The id and the tenant were not enough. A release rebuilt under the same
    id has the same key, so a dashboard computed from the old bytes would be
    served against the new ones -- a cache hit that is a statement about a
    book that no longer exists. The fingerprint is what the two builds
    cannot share, so a rebuild is simply a new key.
    """
    from backend.cockpit_v4 import release as release_mod

    return (str(release_id), str(tenant_id),
            release_mod.fingerprint(str(release_id)))


def cached(*, session: Any, release_id: str, tenant_id: str,
           quarters: list[str], currency: str, refresh: bool = False,
           deadline_seconds: float = 20.0) -> dict[str, Any]:
    """The feed, computed once per release build and tenant.

    Keyed by release, tenant and the release's own bytes, so a different
    release cannot be served from another one's entry, a tenant cannot see a
    feed built under someone else's authorization, and a rebuilt release
    cannot be served a dashboard computed from what it used to hold. A new
    build is a new key, which is the whole of the invalidation rule.
    """
    key = cache_key(release_id, tenant_id)
    if not refresh:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit is not None:
            return {**hit, "cached": True}

    feed = compute(session=session, release_id=release_id, quarters=quarters,
                   currency=currency, deadline_seconds=deadline_seconds)
    with _CACHE_LOCK:
        _CACHE[key] = feed
    return {**feed, "cached": False}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def find_item(feed: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    for section in ("segments_requiring_attention", "ecl_highlights"):
        for item in feed.get(section, ()):
            if str(item.get("item_id")) == item_id:
                return item
    return None


__all__ = ["AttentionUnavailable", "Candidate", "INDICATORS",
           "SCOPE_BORROWER", "SCOPE_PORTFOLIO", "SCOPE_SEGMENT", "TOP_N",
           "cache_key", "cached", "check_composition", "clear_cache",
           "compute", "find_item",
           "select"]
