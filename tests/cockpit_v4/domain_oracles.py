"""
Independent pandas oracles for the two books.

Independent means: these read the published Parquet directly with pandas and
compute the figure from the column definitions, in a different engine and a
different code path. Nothing here imports the catalogue, opens the DuckDB
session, or runs any SQL the product generates. An oracle that calls the code
under test proves the code is self-consistent, which is exactly what a wrong
answer also is.
"""

from __future__ import annotations

import pandas as pd

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake


def frame(domain_id: str, relation: str) -> pd.DataFrame:
    return pd.read_parquet(
        lake.relation_path(dom.DEFAULT_RELEASES[domain_id], relation))


#: What each book calls its period column, and what one exposure row IS.
#: Read here rather than imported from the product, because an oracle that
#: asks the code under test which column to read is not independent of it.
PERIOD_COLUMN: dict[str, str] = {dom.CORPORATE: "reporting_quarter",
                                 dom.RETAIL: "reporting_month"}
EXPOSURE: dict[str, str] = {dom.CORPORATE: "corp_facility_quarter",
                            dom.RETAIL: "retail_account_month"}


def period_column(domain_id: str) -> str:
    return PERIOD_COLUMN[domain_id]


def periods(domain_id: str, relation: str = "") -> list[str]:
    """Every period this book publishes, read from the parquet itself.

    The relation defaults to the book's exposure table -- the one every
    period appears in -- so a caller that only wants "the calendar" does not
    have to know which table to ask.
    """
    return sorted(
        frame(domain_id, relation or EXPOSURE[domain_id])[
            PERIOD_COLUMN[domain_id]].unique())


def months(domain_id: str, relation: str = "") -> list[str]:
    """The older name. Same list; the Corporate book's are quarters."""
    return periods(domain_id, relation)


def latest_month(domain_id: str) -> str:
    return periods(domain_id, EXPOSURE[domain_id])[-1]


def previous_month(domain_id: str) -> str:
    return periods(domain_id, EXPOSURE[domain_id])[-2]


def latest_period(domain_id: str) -> str:
    return latest_month(domain_id)


def previous_period(domain_id: str) -> str:
    return previous_month(domain_id)


def year_ago(domain_id: str) -> str:
    """The same period a year earlier: four quarters or twelve months."""
    slots = periods(domain_id, EXPOSURE[domain_id])
    step = 4 if domain_id == dom.CORPORATE else 12
    return slots[-(step + 1)]


def _month(domain_id: str, relation: str, month: str) -> pd.DataFrame:
    data = frame(domain_id, relation)
    return data[data[PERIOD_COLUMN[domain_id]] == month]


# ---- shared shapes -----------------------------------------------------

def sum_by(domain_id: str, relation: str, month: str, dimension: str,
           measure: str) -> dict[str, float]:
    rows = _month(domain_id, relation, month)
    return {str(k): float(v) for k, v in
            rows.groupby(dimension, observed=True)[measure].sum().items()}


def total(domain_id: str, relation: str, month: str,
          measure: str) -> float:
    return float(_month(domain_id, relation, month)[measure].sum())


def share_by(domain_id: str, relation: str, month: str, dimension: str, *,
             measure: str, condition) -> dict[str, float]:
    """The measure meeting a row condition, over the measure. Per dimension."""
    rows = _month(domain_id, relation, month)
    out: dict[str, float] = {}
    for key, group in rows.groupby(dimension, observed=True):
        base = float(group[measure].sum())
        if base <= 0:
            continue
        hit = float(group.loc[condition(group), measure].sum())
        out[str(key)] = hit / base
    return out


# ---- corporate ---------------------------------------------------------

def corp_ead_by_sector(month: str) -> dict[str, float]:
    return sum_by(dom.CORPORATE, "corp_facility_quarter", month, "sector",
                  "ead_sar_mn")


def corp_stage2_share_by_sector(month: str) -> dict[str, float]:
    return share_by(dom.CORPORATE, "corp_facility_quarter", month, "sector",
                    measure="ead_sar_mn", condition=lambda g: g["stage"] >= 2)


def corp_ecl_by_product_type(month: str) -> dict[str, float]:
    return sum_by(dom.CORPORATE, "corp_facility_quarter", month,
                  "product_type", "ecl_sar_mn")


def corp_ead_movement(latest: str, previous: str) -> float:
    return (total(dom.CORPORATE, "corp_facility_quarter", latest, "ead_sar_mn")
            - total(dom.CORPORATE, "corp_facility_quarter", previous,
                    "ead_sar_mn"))


def corp_ead_by_borrower(month: str) -> dict[str, float]:
    """Facility EAD summed to the borrower, then named.

    The borrower relation is joined for its NAME only. Its own money columns
    are never summed across this join: one borrower holds several facilities,
    so revenue would be counted once per facility.
    """
    facilities = _month(dom.CORPORATE, "corp_facility_quarter", month)
    borrowers = _month(dom.CORPORATE, "corp_borrower_quarter", month)
    names = dict(zip(borrowers["borrower_id"], borrowers["borrower_name"]))
    summed = facilities.groupby("borrower_id", observed=True)[
        "ead_sar_mn"].sum()
    return {str(names.get(k, k)): float(v) for k, v in summed.items()}


# ---- retail ------------------------------------------------------------

def retail_ead_by_product(month: str) -> dict[str, float]:
    return sum_by(dom.RETAIL, "retail_account_month", month, "product",
                  "ead_sar_mn")


def retail_stage2_share_by_product(month: str) -> dict[str, float]:
    return share_by(dom.RETAIL, "retail_account_month", month, "product",
                    measure="ead_sar_mn", condition=lambda g: g["stage"] >= 2)


def retail_ecl_by_score_band(month: str) -> dict[str, float]:
    return sum_by(dom.RETAIL, "retail_account_month", month, "score_band",
                  "ecl_sar_mn")


def retail_delinquent_share_by_product(month: str) -> dict[str, float]:
    return share_by(dom.RETAIL, "retail_account_month", month, "product",
                    measure="ead_sar_mn",
                    condition=lambda g: g["dpd_days"] > 0)


def retail_write_off_by_product(month: str) -> dict[str, float]:
    return sum_by(dom.RETAIL, "retail_account_month", month, "product",
                  "write_off_sar_mn")


def retail_ead_by_vintage(month: str) -> dict[str, float]:
    rows = _month(dom.RETAIL, "retail_account_month", month)
    return {str(int(k)): float(v) for k, v in
            rows.groupby("vintage_year", observed=True)["ead_sar_mn"]
            .sum().items()}


def retail_ecl_coverage(month: str) -> float:
    rows = _month(dom.RETAIL, "retail_account_month", month)
    return float(rows["ecl_sar_mn"].sum()) / float(rows["ead_sar_mn"].sum())


__all__ = ["EXPOSURE", "PERIOD_COLUMN", "corp_ead_by_borrower",
           "corp_ead_by_sector",
           "corp_ead_movement",
           "corp_ecl_by_product_type",
           "corp_stage2_share_by_sector", "frame", "latest_month",
           "latest_period", "months", "period_column", "periods",
           "previous_period", "year_ago",
           "previous_month", "retail_delinquent_share_by_product",
           "retail_ead_by_product", "retail_ead_by_vintage",
           "retail_ecl_by_score_band", "retail_ecl_coverage",
           "retail_stage2_share_by_product", "retail_write_off_by_product",
           "share_by", "sum_by", "total"]


# ---- corporate, the second six -----------------------------------------

def corp_coverage_by_sector(month: str) -> dict[str, float]:
    rows = _month(dom.CORPORATE, "corp_facility_quarter", month)
    out: dict[str, float] = {}
    for key, group in rows.groupby("sector", observed=True):
        ead = float(group["ead_sar_mn"].sum())
        if ead > 0:
            out[str(key)] = float(group["ecl_sar_mn"].sum()) / ead
    return out


def corp_downgrades(month: str) -> dict[str, int]:
    """Borrowers whose rating moved DOWN this month, by how many notches.

    `rating_notches_moved` is recorded as negative for a downgrade, so the
    oracle reads the sign off the column rather than recomputing it from the
    two grades -- which is a different figure the moment either is null.
    """
    rows = _month(dom.CORPORATE, "corp_borrower_quarter", month)
    moved = rows[rows["rating_notches_moved"] < 0]
    return {str(r["borrower_name"]): int(-r["rating_notches_moved"])
            for _i, r in moved.iterrows()}


def corp_breaches_by_type(month: str) -> dict[str, int]:
    rows = _month(dom.CORPORATE, "corp_covenant_quarter", month)
    breached = rows[rows["breach_flag"] == 1]
    return {str(k): int(len(g)) for k, g in
            breached.groupby("covenant_type", observed=True)}


def corp_exposure_behind_breaches(month: str) -> float:
    """EAD of the facilities with at least one breached covenant.

    De-duplicated on purpose: a facility with three breached covenants is one
    exposure, and summing across the join would count it three times.
    """
    covenants = _month(dom.CORPORATE, "corp_covenant_quarter", month)
    breached = set(covenants.loc[covenants["breach_flag"] == 1,
                                 "facility_id"])
    facilities = _month(dom.CORPORATE, "corp_facility_quarter", month)
    return float(facilities[facilities["facility_id"].isin(breached)]
                 ["ead_sar_mn"].sum())


def corp_collateral_cover_by_type(month: str) -> dict[str, float]:
    """Allocated collateral over the EAD of the facility it stands against."""
    collateral = _month(dom.CORPORATE, "corp_collateral_quarter", month)
    facilities = _month(dom.CORPORATE, "corp_facility_quarter", month)
    ead = dict(zip(facilities["facility_id"], facilities["ead_sar_mn"]))
    out: dict[str, float] = {}
    for key, group in collateral.groupby("collateral_type", observed=True):
        secured = sum(float(ead.get(f, 0.0))
                      for f in group["facility_id"].unique())
        if secured > 0:
            out[str(key)] = float(
                group["allocated_value_sar_mn"].sum()) / secured
    return out


def corp_stage_migration(latest: str, previous: str) -> dict[str, float]:
    """EAD that crossed a stage boundary between the two months."""
    now = _month(dom.CORPORATE, "corp_facility_quarter", latest).set_index(
        "facility_id")
    before = _month(dom.CORPORATE, "corp_facility_quarter",
                    previous).set_index("facility_id")
    kept = now.index.intersection(before.index)
    worse = [k for k in kept if now.loc[k, "stage"] > before.loc[k, "stage"]]
    better = [k for k in kept if now.loc[k, "stage"] < before.loc[k, "stage"]]
    return {"deteriorated": float(now.loc[worse, "ead_sar_mn"].sum()),
            "improved": float(now.loc[better, "ead_sar_mn"].sum())}


def corp_ead_by_group(month: str) -> dict[str, float]:
    facilities = _month(dom.CORPORATE, "corp_facility_quarter", month)
    borrowers = _month(dom.CORPORATE, "corp_borrower_quarter", month)
    group = dict(zip(borrowers["borrower_id"], borrowers["group_name"]))
    out: dict[str, float] = {}
    for _i, row in facilities.iterrows():
        name = str(group.get(row["borrower_id"], ""))
        out[name] = out.get(name, 0.0) + float(row["ead_sar_mn"])
    return out


def corp_utilisation_by_type(month: str) -> dict[str, float]:
    """Drawn over limit, at the PRODUCT level rather than an average of
    ratios."""
    rows = _month(dom.CORPORATE, "corp_facility_quarter", month)
    out: dict[str, float] = {}
    for key, group in rows.groupby("product_type", observed=True):
        limit = float(group["limit_sar_mn"].sum())
        if limit > 0:
            out[str(key)] = float(group["drawn_sar_mn"].sum()) / limit
    return out


# ---- retail, the second six --------------------------------------------

def retail_ead_by_bucket(month: str) -> dict[str, float]:
    return sum_by(dom.RETAIL, "retail_account_month", month,
                  "delinquency_bucket", "ead_sar_mn")


def retail_cures_by_product(month: str) -> dict[str, int]:
    rows = _month(dom.RETAIL, "retail_account_month", month)
    cured = rows[rows["cure_flag"] == 1]
    return {str(k): int(len(g))
            for k, g in cured.groupby("product", observed=True)}


def retail_band_migration(month: str) -> dict[str, int]:
    rows = _month(dom.RETAIL, "retail_customer_month", month)
    return {str(k): int(len(g))
            for k, g in rows.groupby("score_migration", observed=True)}


def retail_ead_by_customer(month: str) -> dict[str, float]:
    """Customer exposure, read from the customer relation's own roll-up."""
    rows = _month(dom.RETAIL, "retail_customer_month", month)
    return {str(r["customer_id"]): float(r["total_ead_sar_mn"])
            for _i, r in rows.iterrows()}


def retail_secured_split(month: str) -> dict[str, dict[str, float]]:
    rows = _month(dom.RETAIL, "retail_account_month", month)
    out: dict[str, dict[str, float]] = {}
    for flag, group in rows.groupby("secured_flag", observed=True):
        ead = float(group["ead_sar_mn"].sum())
        out["secured" if int(flag) == 1 else "unsecured"] = {
            "ead": ead, "ecl": float(group["ecl_sar_mn"].sum()),
            "coverage": (float(group["ecl_sar_mn"].sum()) / ead
                         if ead else 0.0),
            "accounts": float(len(group)),
        }
    return out


def retail_stage2_by_months_on_book(month: str) -> dict[str, float]:
    """Stage 2+ share of EAD by seasoning band."""
    rows = _month(dom.RETAIL, "retail_account_month", month).copy()

    def band(value: int) -> str:
        if value < 12:
            return "0-11"
        if value < 24:
            return "12-23"
        if value < 36:
            return "24-35"
        return "36+"

    rows["seasoning"] = rows["months_on_book"].map(band)
    out: dict[str, float] = {}
    for key, group in rows.groupby("seasoning", observed=True):
        ead = float(group["ead_sar_mn"].sum())
        if ead > 0:
            out[str(key)] = float(
                group.loc[group["stage"] >= 2, "ead_sar_mn"].sum()) / ead
    return out
