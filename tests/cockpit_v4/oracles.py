"""
Independent numerical oracles.

These compute the expected answers with pandas, straight from the published
Parquet files -- NOT through the DuckDB session, the catalog, the validator
or anything else the run under test uses. That independence is the whole
point: a test that checks the pipeline against itself proves the pipeline is
self-consistent, which is exactly what a wrong answer also is.

They are oracles for tests. Nothing in the runtime imports this module, and
no production path may fall back to it.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pandas as pd


def _frame(release_id: str, relation: str) -> pd.DataFrame:
    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
    from backend.cockpit_agentic import store

    return pd.read_parquet(store.relation_path(release_id, relation))


def latest_quarter(release_id: str) -> str:
    frame = _frame(release_id, "cockpit_facility_quarter")
    return str(sorted(frame["reporting_quarter"].dropna().unique())[-1])


def quarters(release_id: str) -> list[str]:
    frame = _frame(release_id, "cockpit_facility_quarter")
    return sorted(str(q) for q in frame["reporting_quarter"].dropna().unique())


def ead_by_sector(release_id: str, quarter: str = "") -> dict[str, Decimal]:
    """Total reported EAD per sector for one reporting quarter.

    Facility-quarter grain: one row per facility per quarter, so a plain sum
    is correct here and no de-duplication is needed. That is a property of
    THIS relation, and the test asserts the runtime respected it rather than
    assuming it.
    """
    frame = _frame(release_id, "cockpit_facility_quarter")
    quarter = quarter or latest_quarter(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    grouped = slice_.groupby("sector_name")["ead_reported"].sum()
    return {str(k): Decimal(str(round(float(v), 6)))
            for k, v in grouped.items()}


def stage2_exposure(release_id: str, quarter: str) -> Decimal:
    """Total EAD in IFRS 9 stage 2 for one quarter."""
    frame = _frame(release_id, "cockpit_facility_quarter")
    slice_ = frame[(frame["reporting_quarter"] == quarter)
                   & (frame["ifrs9_stage"] == 2)]
    return Decimal(str(round(float(slice_["ead_reported"].sum()), 6)))


def stage2_by_sector(release_id: str, quarter: str) -> dict[str, Decimal]:
    frame = _frame(release_id, "cockpit_facility_quarter")
    slice_ = frame[(frame["reporting_quarter"] == quarter)
                   & (frame["ifrs9_stage"] == 2)]
    grouped = slice_.groupby("sector_name")["ead_reported"].sum()
    return {str(k): Decimal(str(round(float(v), 6)))
            for k, v in grouped.items()}


def stage2_year_change(release_id: str) -> dict[str, Any]:
    """Stage-2 exposure now versus four quarters earlier, per sector.

    Deliberately reports sectors that ENTERED and EXITED stage 2, because a
    plain inner join between the two quarters silently drops both -- and a
    sector whose stage-2 book went from zero to material is exactly the thing
    the question is asking about.
    """
    slots = quarters(release_id)
    if len(slots) < 5:
        raise AssertionError("the release has fewer than five quarters")
    current, prior = slots[-1], slots[-5]
    now = stage2_by_sector(release_id, current)
    then = stage2_by_sector(release_id, prior)
    sectors = sorted(set(now) | set(then))
    rows = []
    for sector in sectors:
        a, b = then.get(sector), now.get(sector)
        rows.append({
            "sector": sector,
            "prior": a, "current": b,
            "change": (b or Decimal(0)) - (a or Decimal(0)),
            "status": ("entered" if a is None else
                       "exited" if b is None else "present")})
    return {"current_quarter": current, "prior_quarter": prior,
            "total_current": stage2_exposure(release_id, current),
            "total_prior": stage2_exposure(release_id, prior),
            "total_change": (stage2_exposure(release_id, current)
                             - stage2_exposure(release_id, prior)),
            "by_sector": rows,
            "entered": [r["sector"] for r in rows if r["status"] == "entered"],
            "exited": [r["sector"] for r in rows if r["status"] == "exited"]}


def borrower_count(release_id: str, quarter: str = "") -> int:
    """Distinct borrowers. A borrower with three facilities is ONE borrower."""
    frame = _frame(release_id, "cockpit_facility_quarter")
    quarter = quarter or latest_quarter(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    return int(slice_["borrower_id"].nunique())


def total_assets_sum(release_id: str, quarter: str = "") -> Decimal:
    """Borrower-grain total assets, summed ONCE per borrower.

    The oracle for the repetition trap: joining this to the facility table
    and summing produces a larger number, and the larger number is wrong.
    """
    frame = _frame(release_id, "cockpit_borrower_financial_quarter")
    quarter = quarter or latest_quarter(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    deduped = slice_.drop_duplicates(subset=["borrower_id"])
    return Decimal(str(round(float(deduped["total_assets"].sum()), 6)))


def collateral_allocated_total(release_id: str, quarter: str = "") -> Decimal:
    """Allocated collateral value. A shared asset counts once per allocation
    at its ALLOCATED share, never once at its whole value per facility."""
    frame = _frame(release_id, "cockpit_collateral_allocation")
    quarter = quarter or latest_quarter(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    column = ("allocated_net_value_rcy" if "allocated_net_value_rcy"
              in slice_.columns else "allocated_net_value")
    return Decimal(str(round(float(slice_[column].sum()), 6)))


__all__ = ["borrower_count", "collateral_allocated_total", "ead_by_sector",
           "latest_quarter", "quarters", "stage2_by_sector",
           "stage2_exposure", "stage2_year_change", "total_assets_sum"]


def ecl_by_sector(release_id: str, quarter: str = "") -> dict[str, Decimal]:
    """Booked ECL per sector for one reporting quarter, facility grain."""
    frame = _frame(release_id, "cockpit_facility_quarter")
    quarter = quarter or latest_quarter(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    grouped = slice_.groupby("sector_name")["ecl_reported"].sum()
    # Deliberately NOT rounded. Rounding an oracle to six places and then
    # asserting agreement to nine is a test comparing the engine against the
    # rounding, not against the data.
    return {str(k): Decimal(repr(float(v))) for k, v in grouped.items()}


def top_sectors_by_ecl(release_id: str, quarter: str = "", n: int = 5
                       ) -> list[tuple[str, Decimal]]:
    """The n largest sectors by booked ECL, ties broken by sector name.

    The tie-break matters: two sectors with identical ECL must not rank in
    whatever order the engine happened to emit them.
    """
    values = ecl_by_sector(release_id, quarter)
    return sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def ecl_vs_exposure_growth(release_id: str, quarter: str = "",
                           comparison: str = "") -> dict[str, Any]:
    """Sectors whose ECL grew faster than their EAD, quarter on quarter.

    Outer-preserving: a sector present in only one of the two quarters is
    reported as entering or exiting rather than dropped, because an inner
    join here silently deletes exactly the movements that matter most.
    """
    all_quarters = quarters(release_id)
    quarter = quarter or all_quarters[-1]
    if not comparison:
        index = all_quarters.index(quarter)
        comparison = all_quarters[index - 1] if index >= 1 else ""
    new_ecl, old_ecl = ecl_by_sector(release_id, quarter), \
        ecl_by_sector(release_id, comparison)
    new_ead, old_ead = ead_by_sector(release_id, quarter), \
        ead_by_sector(release_id, comparison)

    faster, entered, exited, undefined = [], [], [], []
    for sector in sorted(set(new_ecl) | set(old_ecl)):
        if sector not in old_ecl:
            entered.append(sector)
            continue
        if sector not in new_ecl:
            exited.append(sector)
            continue
        if old_ecl[sector] == 0 or old_ead[sector] == 0:
            # A zero base has no growth RATE. It is not a 100% rise and it is
            # not a zero one; it is undefined, and saying so is the answer.
            undefined.append(sector)
            continue
        ecl_growth = (new_ecl[sector] - old_ecl[sector]) / old_ecl[sector]
        ead_growth = (new_ead[sector] - old_ead[sector]) / old_ead[sector]
        if ecl_growth > ead_growth:
            faster.append((sector, ecl_growth, ead_growth))
    faster.sort(key=lambda row: (-(row[1] - row[2]), row[0]))
    return {"quarter": quarter, "comparison": comparison,
            "faster": faster, "entered": entered, "exited": exited,
            "growth_undefined": undefined}
