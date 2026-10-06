"""
An independent implementation of the attention ranking, in pandas.

Independent means: it reads the published Parquet files directly with pandas
and re-implements the documented method from the specification in
`backend/cockpit_v4/attention.py`'s module docstring. It does not import the
ranking engine, does not open the DuckDB session, does not execute the
engine's SQL, and does not call `compute`, `select`, `_candidate` or any
other production function.

That is the point. An oracle that calls the code under test proves the code
is self-consistent, which is exactly what a wrong ranking also is. This one
can disagree — and while it was being written it did, on the covenant
observation gap, which is how that defect was found.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

MATERIAL_FRACTION = 0.0005
MIN_SECTOR_FRACTION = 0.005
FULL_MONEY_FRACTION = 0.02
MIN_FACILITIES = 2
CONFIDENT_FACILITIES = 5

#: id -> (family, direction, kind, full_move, observation_base, min_obs)
SPECS: dict[str, tuple[str, int, str, float, str, float]] = {
    "stage2_share": ("stage_migration", 1, "share", 0.10, "", 0.0),
    "stage3_share": ("stage_migration", 1, "share", 0.05, "", 0.0),
    "ecl_coverage": ("ecl", 1, "ratio", 0.01, "", 0.0),
    "ecl_amount": ("ecl", 1, "amount", 0.0, "", 0.0),
    "weighted_pd": ("credit_quality", 1, "ratio", 0.02, "pd_base", 0.5),
    "rating_rank": ("credit_quality", 1, "ratio", 1.0, "rating_base", 0.5),
    "uncovered_share": ("collateral", 1, "share", 0.10, "", 0.0),
    "covenant_breach_share": ("covenant", 1, "share", 0.10, "covenant_base",
                              0.2),
    "past_due_share": ("arrears", 1, "share", 0.10, "", 0.0),
    "concentration_share": ("concentration", 1, "share", 0.03, "", 0.0),
}


def _frame(release_id: str, relation: str) -> pd.DataFrame:
    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
    from backend.cockpit_agentic import store

    return pd.read_parquet(store.relation_path(release_id, relation))


def aggregates(release_id: str) -> pd.DataFrame:
    """Sector x quarter aggregates, built with pandas only."""
    facility = _frame(release_id, "cockpit_facility_quarter")
    facility = facility[facility["sector_name"].notna()]

    rows = []
    for (quarter, sector), group in facility.groupby(
            ["reporting_quarter", "sector_name"], observed=True):
        pd_rows = group[group["pd_pit_12m"].notna()]
        rows.append({
            "reporting_quarter": str(quarter), "sector_name": str(sector),
            "facilities": int(len(group)),
            "ead": float(group["ead_reported"].sum()),
            "ecl": float(group["ecl_reported"].sum()),
            "stage2_ead": float(
                group.loc[group["ifrs9_stage"] == 2, "ead_reported"].sum()),
            "stage3_ead": float(
                group.loc[group["ifrs9_stage"] == 3, "ead_reported"].sum()),
            "pd_base": float(pd_rows["ead_reported"].sum())
            if len(pd_rows) else None,
            "pd_weight": float(
                (pd_rows["ead_reported"] * pd_rows["pd_pit_12m"]).sum())
            if len(pd_rows) else None,
            "uncovered_ead": float(group.loc[
                group["collateral_coverage_ratio"].notna()
                & (group["collateral_coverage_ratio"] < 1),
                "ead_reported"].sum()),
            "past_due_ead": float(group.loc[
                group["days_past_due"].notna() & (group["days_past_due"] > 0),
                "ead_reported"].sum()),
        })
    frame = pd.DataFrame(rows)

    borrower = facility.groupby(
        ["reporting_quarter", "borrower_id", "sector_name"],
        observed=True)["ead_reported"].sum().reset_index(name="ead")

    rating = _frame(release_id, "cockpit_rating_ratio_quarter")[
        ["reporting_quarter", "borrower_id", "rating_rank"]]
    joined = borrower.merge(rating, on=["reporting_quarter", "borrower_id"],
                            how="left")
    rated = joined[joined["rating_rank"].notna()].copy()
    rated["weight"] = rated["ead"] * rated["rating_rank"]
    rating_agg = rated.groupby(["reporting_quarter", "sector_name"],
                               observed=True).agg(
        rating_base=("ead", "sum"), rating_weight=("weight", "sum")
    ).reset_index()

    covenant = _frame(release_id, "cockpit_covenant_quarter")
    observed = covenant[covenant["headroom_value"].notna()
                        | covenant["breach_date"].notna()].copy()
    observed["breached"] = (
        observed["breach_date"].notna()
        | (observed["headroom_value"].notna()
           & (observed["headroom_value"] < 0))).astype(int)
    per_borrower = observed.groupby(
        ["reporting_quarter", "borrower_id"], observed=True)["breached"].max(
        ).reset_index()
    cov = borrower.merge(per_borrower, on=["reporting_quarter", "borrower_id"],
                         how="inner")
    cov["breach_ead"] = cov["ead"] * cov["breached"]
    covenant_agg = cov.groupby(["reporting_quarter", "sector_name"],
                               observed=True).agg(
        covenant_base=("ead", "sum"), breach_ead=("breach_ead", "sum")
    ).reset_index()

    for extra in (rating_agg, covenant_agg):
        frame = frame.merge(extra, on=["reporting_quarter", "sector_name"],
                            how="left")
    return frame


def _value(spec_id: str, row: pd.Series, book_ead: float) -> float | None:
    numerator = {
        "stage2_share": row.get("stage2_ead"),
        "stage3_share": row.get("stage3_ead"),
        "ecl_coverage": row.get("ecl"), "ecl_amount": row.get("ecl"),
        "weighted_pd": row.get("pd_weight"),
        "rating_rank": row.get("rating_weight"),
        "uncovered_share": row.get("uncovered_ead"),
        "covenant_breach_share": row.get("breach_ead"),
        "past_due_share": row.get("past_due_ead"),
        "concentration_share": row.get("ead"),
    }[spec_id]
    denominator = {
        "stage2_share": row.get("ead"), "stage3_share": row.get("ead"),
        "ecl_coverage": row.get("ead"), "ecl_amount": None,
        "weighted_pd": row.get("pd_base"),
        "rating_rank": row.get("rating_base"),
        "uncovered_share": row.get("ead"),
        "covenant_breach_share": row.get("covenant_base"),
        "past_due_share": row.get("ead"),
        "concentration_share": book_ead,
    }[spec_id]
    if numerator is None or pd.isna(numerator):
        return None
    if SPECS[spec_id][2] == "amount":
        return float(numerator)
    if denominator is None or pd.isna(denominator) or float(denominator) == 0:
        return None
    return float(numerator) / float(denominator)


def candidates(release_id: str) -> list[dict[str, Any]]:
    frame = aggregates(release_id)
    quarters = sorted(frame["reporting_quarter"].unique())
    latest = quarters[-1]
    bases = []
    if len(quarters) >= 2:
        bases.append(("prior_quarter", quarters[-2]))
    if len(quarters) >= 5:
        bases.append(("prior_year", quarters[-5]))

    def book(quarter: str) -> float:
        return float(frame.loc[frame["reporting_quarter"] == quarter,
                               "ead"].sum())

    book_new = book(latest)
    out: list[dict[str, Any]] = []
    for basis, comparison in bases:
        book_old = book(comparison) or book_new
        for sector in sorted(frame["sector_name"].unique()):
            new = frame[(frame["reporting_quarter"] == latest)
                        & (frame["sector_name"] == sector)]
            old = frame[(frame["reporting_quarter"] == comparison)
                        & (frame["sector_name"] == sector)]
            if new.empty or old.empty:
                continue
            new_row, old_row = new.iloc[0], old.iloc[0]
            ead_new, ead_old = float(new_row["ead"]), float(old_row["ead"])
            for spec_id, (family, direction, kind, full_move, obs,
                          min_obs) in SPECS.items():
                value_new = _value(spec_id, new_row, book_new)
                value_old = _value(spec_id, old_row, book_old)
                if value_new is None or value_old is None:
                    continue
                adverse = (value_new - value_old) * direction
                if adverse <= 0:
                    continue
                if obs:
                    shares = []
                    for row, ead in ((old_row, ead_old), (new_row, ead_new)):
                        base = row.get(obs)
                        shares.append(None if base is None or pd.isna(base)
                                      or ead == 0 else float(base) / ead)
                    if any(s is None or s < min_obs for s in shares):
                        continue
                exposure = adverse if kind == "amount" else adverse * ead_new
                if min(int(old_row["facilities"]),
                       int(new_row["facilities"])) < MIN_FACILITIES:
                    continue
                if min(ead_old, ead_new) < (MIN_SECTOR_FRACTION
                                            * max(book_new, book_old)):
                    continue
                if exposure < MATERIAL_FRACTION * book_new:
                    continue
                reference = (FULL_MONEY_FRACTION * book_new
                             if kind == "amount" else full_move)
                full_money = FULL_MONEY_FRACTION * book_new
                relative = adverse / (adverse + reference)
                money = exposure / (exposure + full_money)
                confidence = min(1.0,
                                 min(int(old_row["facilities"]),
                                     int(new_row["facilities"]))
                                 / CONFIDENT_FACILITIES)
                out.append({
                    "metric": spec_id, "family": family, "segment": sector,
                    "basis": basis, "quarter": latest,
                    "comparison": comparison, "value_old": value_old,
                    "value_new": value_new, "adverse": adverse,
                    "exposure_at_risk": exposure,
                    "score": round(100.0 * (0.5 * relative + 0.5 * money)
                                   * confidence, 4)})
    return out


def top_segments(release_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """The documented selection, re-implemented: dedup, then four passes."""
    def key(c: dict[str, Any]) -> tuple:
        return (-c["score"], -c["exposure_at_risk"], -c["adverse"],
                c["segment"], c["metric"], c["basis"])

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in sorted(candidates(release_id), key=key):
        best.setdefault((candidate["segment"], candidate["family"]),
                        candidate)

    remaining = sorted(best.values(), key=key)
    chosen: list[dict[str, Any]] = []
    segments: set[str] = set()
    families: set[str] = set()

    def take(accept) -> None:
        for candidate in list(remaining):
            if len(chosen) >= limit:
                return
            if not accept(candidate):
                continue
            chosen.append(candidate)
            remaining.remove(candidate)
            segments.add(candidate["segment"])
            families.add(candidate["family"])

    take(lambda c: c["segment"] not in segments and c["family"] not in families)
    take(lambda c: c["segment"] not in segments)
    take(lambda c: c["family"] not in families)
    take(lambda c: True)
    return chosen[:limit]


def ecl_by_sector(release_id: str, quarter: str) -> dict[str, float]:
    frame = aggregates(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    return {str(r["sector_name"]): float(r["ecl"])
            for _, r in slice_.iterrows()}


def book_totals(release_id: str, quarter: str) -> dict[str, float]:
    frame = aggregates(release_id)
    slice_ = frame[frame["reporting_quarter"] == quarter]
    return {"ead": float(slice_["ead"].sum()),
            "ecl": float(slice_["ecl"].sum()),
            "stage2_ead": float(slice_["stage2_ead"].sum())}
