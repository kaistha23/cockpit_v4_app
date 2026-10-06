"""
Where the ECL is, and what moved it. Computed, never narrated.

Two questions, both asked of every book
---------------------------------------
1. THE PROFILE. How much exposure sits in each IFRS 9 stage this month, how
   much loss is carried against it, and what the coverage is. With the same
   figures for the month before, so the movement is a fact rather than an
   impression.

2. THE DECOMPOSITION. Total ECL moved by some amount between two months.
   This says how much of that move each identifiable cause contributed, and
   the causes sum to the move EXACTLY -- not approximately, and not with a
   residual quietly labelled "other".

The decomposition, stated so a reader can check it
--------------------------------------------------
Every exposure (a facility in the Corporate book, an account in Retail) is in
exactly one of three groups between the two months, so the groups partition
the change and cannot double-count it:

  NEW         present this month, absent last month      + ECL_now
  CLOSED      present last month, absent this month      - ECL_before
  RETAINED    present in both                            ECL_now - ECL_before

The retained group is then split, again exactly:

  STAGE MIGRATION    stage changed          the whole of its ECL change
  EXPOSURE CHANGE    stage unchanged        ΔEAD x coverage_before
  RISK CHANGE        stage unchanged        the remainder

"Risk change" is a remainder by construction and is labelled as one: it is
what is left after the exposure movement is taken out at last month's
coverage, which is PD, LGD and any overlay together. Splitting it further
would require model parameters this book does not publish, and inventing that
split is how a decomposition becomes a story.

Why it is not a model call
--------------------------
Every number here is an aggregate of published columns. A language model
cannot make it more accurate and can make it wrong, so the analyst is never
asked. The analyst's job starts where this ends: explaining a movement this
has already measured.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from decimal import Decimal

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod


def _money(value: float, unit: str) -> str:
    """The published string for an amount. The server owns it, not the page."""
    return disp.format_value(Decimal(str(round(float(value), 10))), unit)


def _share(value: float) -> str:
    """A 0-1 fraction, published as a percentage at the governed precision."""
    return disp.format_value(Decimal(str(round(float(value) * 100, 10))),
                             "percent")


def _points(value: float) -> str:
    return disp.format_value(Decimal(str(round(float(value) * 100, 10))),
                             "percentage points")

#: Which relation carries the stage, the exposure and the loss, per book, and
#: what one row of it IS.
EXPOSURE_GRAIN: dict[str, dict[str, str]] = {
    dom.CORPORATE: {"relation": "corp_facility_quarter", "key": "facility_id",
                    "noun": "facility", "plural": "facilities",
                    "period": "reporting_quarter", "period_noun": "quarter"},
    dom.RETAIL: {"relation": "retail_account_month", "key": "account_id",
                 "noun": "account", "plural": "accounts",
                 "period": "reporting_month", "period_noun": "month"},
}

STAGES = (1, 2, 3)

#: A movement smaller than this share of the book is reported as flat. Not a
#: rounding rule: a hundredth of a percent of a portfolio is noise, and
#: calling it a rise trains a reader to ignore the ones that are not.
MATERIAL_SHARE = 0.0005


class EclUnavailable(RuntimeError):
    """The figures could not be computed. Said, never estimated."""


@dataclass(frozen=True)
class Component:
    """One named contributor to a movement, with what it is made of."""

    component_id: str
    label: str
    amount: float
    exposures: int
    explanation: str

    def to_dict(self, unit: str) -> dict[str, Any]:
        return {
            "component_id": self.component_id, "label": self.label,
            "amount": self.amount,
            "display_amount": _money(self.amount, unit),
            "exposures": self.exposures,
            "explanation": self.explanation,
        }


@dataclass
class Decomposition:
    """A movement and the components that sum to it."""

    domain_id: str
    release_id: str
    release_fingerprint: str
    reporting_month: str
    comparison_month: str
    money_unit: str
    opening: float
    closing: float
    components: list[Component] = field(default_factory=list)

    @property
    def movement(self) -> float:
        return self.closing - self.opening

    @property
    def residual(self) -> float:
        """What the components fail to explain. Zero, by construction."""
        return self.movement - sum(c.amount for c in self.components)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "domain_label": dom.LABELS[self.domain_id],
            "release_id": self.release_id,
            "release_fingerprint": self.release_fingerprint,
            **schema_mod.period_keys(self.domain_id, self.reporting_month,
                                     self.comparison_month),
            "money_unit": self.money_unit,
            "opening": self.opening,
            "closing": self.closing,
            "movement": self.movement,
            "display_opening": _money(self.opening, self.money_unit),
            "display_closing": _money(self.closing, self.money_unit),
            "display_movement": _money(self.movement, self.money_unit),
            "components": [c.to_dict(self.money_unit)
                           for c in self.components],
            "residual": self.residual,
            "reconciles": abs(self.residual) < 1e-6,
            "method": (
                "Every exposure is NEW, CLOSED or RETAINED between the two "
                "months, so the three groups partition the change. Retained "
                "exposures are split again: one whose stage changed "
                "contributes its whole ECL change to stage migration; one "
                "whose stage did not is split into its exposure movement "
                "priced at last month's coverage, and the remainder, which "
                "is the change in PD, LGD and overlay together. The "
                "components sum to the movement exactly; the residual is "
                "published so a reader can see that they do."),
            "model_calls": 0,
        }


def _rows(session: Any, sql: str) -> list[dict[str, Any]]:
    cursor = session.connection.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _grain(domain_id: str) -> dict[str, str]:
    try:
        return EXPOSURE_GRAIN[domain_id]
    except KeyError as exc:
        raise EclUnavailable(
            f"No exposure relation is recorded for domain "
            f"{domain_id!r}.") from exc


# ---- 1. the profile ----------------------------------------------------

def stage_profile(*, session: Any, scope: Any, month: str = "",
                  comparison: str = "") -> dict[str, Any]:
    """EAD, ECL, coverage and count by IFRS 9 stage, this month and last."""
    if session.domain_id != scope.domain_id:
        raise EclUnavailable(
            f"A {dom.LABELS[scope.domain_id]} profile cannot be computed "
            f"from a {dom.LABELS[session.domain_id]} session.")
    grain = _grain(scope.domain_id)
    month = month or scope.latest_period
    comparison = comparison or scope.previous_period
    now = _stage_rows(session, grain, month)
    before = _stage_rows(session, grain, comparison)

    unit = scope.money_unit
    total_ead = sum(r["ead"] for r in now.values())
    total_ecl = sum(r["ecl"] for r in now.values())
    prior_ead = sum(r["ead"] for r in before.values())
    prior_ecl = sum(r["ecl"] for r in before.values())

    stages = []
    for stage in STAGES:
        this = now.get(stage, {"ead": 0.0, "ecl": 0.0, "count": 0})
        last = before.get(stage, {"ead": 0.0, "ecl": 0.0, "count": 0})
        share = this["ead"] / total_ead if total_ead else 0.0
        prior_share = last["ead"] / prior_ead if prior_ead else 0.0
        coverage = this["ecl"] / this["ead"] if this["ead"] else 0.0
        prior_coverage = last["ecl"] / last["ead"] if last["ead"] else 0.0
        stages.append({
            "stage": stage,
            "label": f"Stage {stage}",
            "ead": this["ead"], "ecl": this["ecl"],
            "display_ead": _money(this["ead"], unit),
            "display_ecl": _money(this["ecl"], unit),
            f"{grain['plural']}": this["count"],
            "exposures": this["count"],
            "share_of_ead": share,
            "display_share_of_ead": _share(share),
            "coverage": coverage,
            "display_coverage": _share(coverage),
            "ead_movement": this["ead"] - last["ead"],
            "ecl_movement": this["ecl"] - last["ecl"],
            "display_ead_movement": _money(this["ead"] - last["ead"],
                                                unit),
            "display_ecl_movement": _money(this["ecl"] - last["ecl"],
                                                unit),
            "share_movement_pp": share - prior_share,
            "coverage_movement_pp": coverage - prior_coverage,
        })

    coverage = total_ecl / total_ead if total_ead else 0.0
    prior_total_coverage = prior_ecl / prior_ead if prior_ead else 0.0
    return {
        "domain_id": scope.domain_id,
        "domain_label": dom.LABELS[scope.domain_id],
        "release_id": scope.release_id,
        "release_fingerprint": scope.release_fingerprint,
        **schema_mod.period_keys(scope.domain_id, month, comparison),
        "money_unit": unit,
        "exposure_grain": grain["noun"],
        "relation": grain["relation"],
        "total_ead": total_ead, "total_ecl": total_ecl,
        "display_total_ead": _money(total_ead, unit),
        "display_total_ecl": _money(total_ecl, unit),
        "coverage": coverage,
        "display_coverage": _share(coverage),
        "coverage_movement_pp": coverage - prior_total_coverage,
        "ead_movement": total_ead - prior_ead,
        "ecl_movement": total_ecl - prior_ecl,
        "display_ead_movement": _money(total_ead - prior_ead, unit),
        "display_ecl_movement": _money(total_ecl - prior_ecl, unit),
        "material": abs(total_ecl - prior_ecl) > MATERIAL_SHARE * max(
            total_ecl, 1e-9),
        "stages": stages,
        "model_calls": 0,
        "note": ("Stage is as recorded in the book, not derived from days "
                 "past due. Coverage is ECL over EAD at the same grain; a "
                 "stage with no exposure reports zero coverage rather than "
                 "an undefined ratio."),
    }


def _stage_rows(session: Any, grain: dict[str, str],
                month: str) -> dict[int, dict[str, float]]:
    rows = _rows(session, f"""
        SELECT stage,
               SUM(ead_sar_mn) AS ead,
               SUM(ecl_sar_mn) AS ecl,
               COUNT(*) AS n
        FROM {grain['relation']}
        WHERE {grain["period"]} = '{month}'
        GROUP BY stage
    """)
    return {int(r["stage"]): {"ead": float(r["ead"] or 0.0),
                             "ecl": float(r["ecl"] or 0.0),
                             "count": int(r["n"] or 0)}
            for r in rows}


# ---- 2. the decomposition ----------------------------------------------

def decompose(*, session: Any, scope: Any, month: str = "",
              comparison: str = "") -> Decomposition:
    """Attribute the ECL movement between two months. Exactly."""
    if session.domain_id != scope.domain_id:
        raise EclUnavailable(
            f"A {dom.LABELS[scope.domain_id]} decomposition cannot be "
            f"computed from a {dom.LABELS[session.domain_id]} session.")
    grain = _grain(scope.domain_id)
    month = month or scope.latest_period
    comparison = comparison or scope.previous_period
    key, relation = grain["key"], grain["relation"]
    period = grain["period"]

    rows = _rows(session, f"""
        WITH now AS (
            SELECT {key} AS id, stage, ead_sar_mn AS ead, ecl_sar_mn AS ecl
            FROM {relation} WHERE {period} = '{month}'),
        before AS (
            SELECT {key} AS id, stage, ead_sar_mn AS ead, ecl_sar_mn AS ecl
            FROM {relation} WHERE {period} = '{comparison}')
        SELECT
            CASE
                WHEN before.id IS NULL THEN 'new'
                WHEN now.id IS NULL THEN 'closed'
                WHEN now.stage <> before.stage THEN 'stage_migration'
                ELSE 'retained'
            END AS bucket,
            COUNT(*) AS n,
            SUM(COALESCE(now.ecl, 0) - COALESCE(before.ecl, 0)) AS d_ecl,
            SUM(CASE WHEN before.id IS NOT NULL AND now.id IS NOT NULL
                          AND now.stage = before.stage
                     THEN (now.ead - before.ead)
                          * CASE WHEN before.ead = 0 THEN 0
                                 ELSE before.ecl / before.ead END
                     ELSE 0 END) AS d_exposure
        FROM now FULL OUTER JOIN before ON now.id = before.id
        GROUP BY 1
    """)
    buckets = {str(r["bucket"]): r for r in rows}

    def amount(name: str, column: str = "d_ecl") -> float:
        row = buckets.get(name)
        return float(row[column] or 0.0) if row else 0.0

    def count(name: str) -> int:
        row = buckets.get(name)
        return int(row["n"] or 0) if row else 0

    exposure_effect = amount("retained", "d_exposure")
    retained_total = amount("retained")
    noun, plural = grain["noun"], grain["plural"]
    components = [
        Component(
            "new", f"New {plural}", amount("new"), count("new"),
            f"ECL carried by {count('new')} {plural} present in {month} and "
            f"not in {comparison}."),
        Component(
            "closed", f"Closed {plural}", amount("closed"), count("closed"),
            f"ECL released by {count('closed')} {plural} present in "
            f"{comparison} and not in {month}. Closure, repayment and "
            f"write-off all leave the book this way and are not separated "
            f"here."),
        Component(
            "stage_migration", "Stage migration",
            amount("stage_migration"), count("stage_migration"),
            f"The whole ECL change of {count('stage_migration')} {plural} "
            f"whose IFRS 9 stage changed. A move to Stage 2 or 3 switches "
            f"the measurement basis to lifetime, so the change is "
            f"attributed to the migration rather than split."),
        Component(
            "exposure_change", "Exposure change", exposure_effect,
            count("retained"),
            f"The EAD movement of {plural} that stayed in the same stage, "
            f"priced at last month's coverage for each {noun}. This is what "
            f"the book would have cost if only the exposure had changed."),
        Component(
            "risk_change", "Risk and overlay change",
            retained_total - exposure_effect, count("retained"),
            "What is left for the same-stage exposures after the exposure "
            "movement is taken out: PD, LGD and overlay together. It is a "
            "remainder and is reported as one -- this book does not publish "
            "the parameters that would split it further."),
    ]

    return Decomposition(
        domain_id=scope.domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint,
        reporting_month=month, comparison_month=comparison,
        money_unit=scope.money_unit,
        opening=_total_ecl(session, relation, comparison, period),
        closing=_total_ecl(session, relation, month, period),
        components=components)


def _total_ecl(session: Any, relation: str, month: str,
               period: str = "reporting_month") -> float:
    rows = _rows(session, f"SELECT SUM(ecl_sar_mn) AS ecl FROM {relation} "
                          f"WHERE {period} = '{month}'")
    return float(rows[0]["ecl"] or 0.0) if rows else 0.0


# ---- caching -----------------------------------------------------------

_CACHE: dict[tuple[str, ...], dict[str, Any]] = {}
_LOCK = threading.RLock()


def cached(*, session: Any, scope: Any, tenant_id: str,
           refresh: bool = False) -> dict[str, Any]:
    """The profile and the decomposition for one book, computed once.

    Keyed by tenant, domain, release AND fingerprint, so a rebuilt release
    cannot be served from the old build's entry and one book can never be
    served from the other's.
    """
    key = (tenant_id, scope.domain_id, scope.release_id,
           scope.release_fingerprint, scope.latest_period)
    if not refresh:
        with _LOCK:
            hit = _CACHE.get(key)
        if hit is not None:
            return hit
    body = {
        "profile": stage_profile(session=session, scope=scope),
        "decomposition": decompose(session=session, scope=scope).to_dict(),
    }
    with _LOCK:
        _CACHE[key] = body
        while len(_CACHE) > 8:
            _CACHE.pop(next(iter(_CACHE)))
    return body


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["Component", "Decomposition", "EXPOSURE_GRAIN", "EclUnavailable",
           "MATERIAL_SHARE", "STAGES", "cached", "clear_cache", "decompose",
           "stage_profile"]
