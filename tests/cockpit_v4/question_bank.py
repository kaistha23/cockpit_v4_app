"""The analytical question banks: forty per book, with independent oracles.

§42-§45. Each entry is one question a credit officer would actually ask, the
SQL that answers it in this book, and an ORACLE that recomputes the same
figure with pandas over the published Parquet.

Independent means what it says. Nothing here imports the catalogue, opens the
DuckDB session, or reuses a helper the product uses to build SQL. An oracle
that calls the code under test proves the code is self-consistent, which is
exactly what a wrong answer also is.

The two banks are deliberately not translations of each other. A corporate
book is asked about sectors, ratings, covenants and collateral; a retail book
about products, vintages, behavioural bands and delinquency buckets. Asking
each the other's questions would prove only that both can group by a column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from backend.cockpit_v4 import domains as dom

from . import domain_oracles as oracle

#: How many rows a "top N" question returns. Small enough to read, large
#: enough that the ordering is doing work.
TOP_N = 10


@dataclass(frozen=True)
class Case:
    """One question, the query that answers it, and the truth."""

    case_id: str
    domain_id: str
    question: str
    sql: str
    fields: tuple[str, ...]
    grain: str
    units: str
    #: The figures this must produce, recomputed with pandas.
    oracle: Callable[[], dict[str, float] | float]
    #: The column the answer is keyed by, or "" for a single figure.
    key: str = ""
    #: The column holding the figure.
    value: str = ""
    tolerance: float = 0.01
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---- helpers, in pandas, over the parquet -------------------------------

def _corp(relation: str) -> pd.DataFrame:
    return oracle.frame(dom.CORPORATE, relation)


def _retail(relation: str) -> pd.DataFrame:
    return oracle.frame(dom.RETAIL, relation)


def _at(frame: pd.DataFrame, domain_id: str, period: str) -> pd.DataFrame:
    return frame[frame[oracle.period_column(domain_id)] == period]


def _sum_by(frame: pd.DataFrame, key: str, value: str) -> dict[str, float]:
    return {str(k): float(v)
            for k, v in frame.groupby(key)[value].sum().items()}


def _mean_by(frame: pd.DataFrame, key: str, value: str) -> dict[str, float]:
    return {str(k): float(v)
            for k, v in frame.groupby(key)[value].mean().items()}


def _count_by(frame: pd.DataFrame, key: str,
              column: str) -> dict[str, float]:
    return {str(k): float(v)
            for k, v in frame.groupby(key)[column].nunique().items()}


def _share_by(frame: pd.DataFrame, key: str, value: str,
              mask: pd.Series) -> dict[str, float]:
    whole = frame.groupby(key)[value].sum()
    part = frame[mask].groupby(key)[value].sum().reindex(whole.index).fillna(0)
    return {str(k): float(part[k] / whole[k]) if whole[k] else 0.0
            for k in whole.index}


def _flag(frame: pd.DataFrame, column: str) -> pd.Series:
    """A boolean mask from a flag column.

    The Parquet stores these as int64 and DuckDB reads a non-zero integer as
    true, so `frame[frame["breach_flag"]]` would select COLUMNS rather than
    rows -- silently, until the integers happen not to be column names.
    """
    return frame[column].astype(int) == 1


def _top(body: dict[str, float], n: int = TOP_N) -> dict[str, float]:
    ordered = sorted(body.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ordered[:n])


# ---- the Corporate bank -------------------------------------------------

def corporate_cases() -> tuple[Case, ...]:
    latest = oracle.latest_period(dom.CORPORATE)
    previous = oracle.previous_period(dom.CORPORATE)
    year_ago = oracle.year_ago(dom.CORPORATE)
    facility = "corp_facility_quarter"
    borrower = "corp_borrower_quarter"
    collateral = "corp_collateral_quarter"
    covenant = "corp_covenant_quarter"
    q = "reporting_quarter"

    def at(relation: str, period: str = latest) -> pd.DataFrame:
        return _at(_corp(relation), dom.CORPORATE, period)

    def case(case_id: str, question: str, sql: str, *, fields, grain,
             units, oracle_fn, key="", value="", tolerance=0.01,
             notes="", tags=()) -> Case:
        return Case(case_id=case_id, domain_id=dom.CORPORATE,
                    question=question, sql=sql, fields=tuple(fields),
                    grain=grain, units=units, oracle=oracle_fn, key=key,
                    value=value, tolerance=tolerance, notes=notes,
                    tags=tuple(tags))

    return (
        case("C01", "What is exposure at default by sector this quarter?",
             f"SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn FROM {facility} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn", f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "sector", "ead_sar_mn"),
             tags=("exposure", "sector")),

        case("C02", "What is recognised ECL by sector?",
             f"SELECT sector, SUM(ecl_sar_mn) AS ecl_sar_mn FROM {facility} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ecl_sar_mn", f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="ecl_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "sector", "ecl_sar_mn"),
             tags=("ifrs9", "sector")),

        case("C03", "What is ECL by product type?",
             f"SELECT product_type, SUM(ecl_sar_mn) AS ecl_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ecl_sar_mn", f"{facility}.product_type"],
             grain="product_type", units="SAR million", key="product_type",
             value="ecl_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "product_type",
                                       "ecl_sar_mn"),
             notes="§38: a product is not a sector.",
             tags=("ifrs9", "product")),

        case("C04", "What share of exposure is in stage 2, by sector?",
             f"SELECT sector, SUM(CASE WHEN stage = 2 THEN ead_sar_mn "
             f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn", f"{facility}.stage",
                     f"{facility}.sector"],
             grain="sector", units="percent", key="sector",
             value="stage2_share", tolerance=1e-6,
             oracle_fn=lambda: _share_by(
                 at(facility), "sector", "ead_sar_mn",
                 at(facility)["stage"] == 2),
             tags=("ifrs9", "sector")),

        case("C05", "What is ECL coverage of exposure, by sector?",
             f"SELECT sector, SUM(ecl_sar_mn) / NULLIF(SUM(ead_sar_mn), 0) "
             f"AS coverage FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ecl_sar_mn", f"{facility}.ead_sar_mn",
                     f"{facility}.sector"],
             grain="sector", units="percent", key="sector", value="coverage",
             tolerance=1e-6,
             oracle_fn=lambda: {
                 k: float(v) for k, v in
                 (at(facility).groupby("sector")["ecl_sar_mn"].sum()
                  / at(facility).groupby("sector")["ead_sar_mn"].sum()
                  ).items()},
             tags=("ifrs9", "sector")),

        case("C06", "What is exposure by region?",
             f"SELECT region, SUM(ead_sar_mn) AS ead_sar_mn FROM {facility} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn", f"{facility}.region"],
             grain="region", units="SAR million", key="region",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "region", "ead_sar_mn"),
             tags=("exposure", "region")),

        case("C07", "What is exposure by relationship segment?",
             f"SELECT relationship_tier, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn",
                     f"{facility}.relationship_tier"],
             grain="relationship_tier", units="SAR million",
             key="relationship_tier", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "relationship_tier",
                                       "ead_sar_mn"),
             tags=("exposure", "segmentation")),

        case("C08", "How many borrowers are in each sector?",
             f"SELECT sector, COUNT(DISTINCT borrower_id) AS borrowers "
             f"FROM {borrower} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.borrower_id", f"{borrower}.sector"],
             grain="sector", units="borrowers", key="sector",
             value="borrowers",
             oracle_fn=lambda: _count_by(at(borrower), "sector",
                                         "borrower_id"),
             tags=("identity", "sector")),

        case("C09", "What is exposure by internal rating?",
             f"SELECT b.rating_current, SUM(f.ead_sar_mn) AS ead_sar_mn "
             f"FROM {facility} f JOIN {borrower} b "
             f"ON b.borrower_id = f.borrower_id AND b.{q} = f.{q} "
             f"WHERE f.{q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn",
                     f"{borrower}.rating_current"],
             grain="rating_current", units="SAR million",
             key="rating_current", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(
                 at(facility).merge(
                     at(borrower)[["borrower_id", "rating_current"]],
                     on="borrower_id"),
                 "rating_current", "ead_sar_mn"),
             tags=("rating", "exposure")),

        case("C10", "Which parent groups hold the most exposure?",
             f"SELECT b.group_name, SUM(f.ead_sar_mn) AS ead_sar_mn "
             f"FROM {facility} f JOIN {borrower} b "
             f"ON b.borrower_id = f.borrower_id AND b.{q} = f.{q} "
             f"WHERE f.{q} = '{latest}' GROUP BY 1 "
             f"ORDER BY ead_sar_mn DESC, b.group_name LIMIT {TOP_N}",
             fields=[f"{facility}.ead_sar_mn", f"{borrower}.group_name"],
             grain="group", units="SAR million", key="group_name",
             value="ead_sar_mn",
             oracle_fn=lambda: _top(_sum_by(
                 at(facility).merge(
                     at(borrower)[["borrower_id", "group_name"]],
                     on="borrower_id"),
                 "group_name", "ead_sar_mn")),
             tags=("concentration",)),

        case("C11", "How many borrowers were downgraded, by sector?",
             f"SELECT sector, COUNT(DISTINCT borrower_id) AS borrowers "
             f"FROM {borrower} WHERE {q} = '{latest}' "
             f"AND rating_migration = 'DOWNGRADE' GROUP BY 1",
             fields=[f"{borrower}.rating_migration",
                     f"{borrower}.borrower_id", f"{borrower}.sector"],
             grain="sector", units="borrowers", key="sector",
             value="borrowers",
             oracle_fn=lambda: _count_by(
                 at(borrower)[at(borrower)["rating_migration"]
                              == "DOWNGRADE"],
                 "sector", "borrower_id"),
             tags=("rating", "migration")),

        case("C12", "How many covenants are in breach, by covenant type?",
             f"SELECT covenant_type, COUNT(*) AS breaches FROM {covenant} "
             f"WHERE {q} = '{latest}' AND breach_flag GROUP BY 1",
             fields=[f"{covenant}.breach_flag",
                     f"{covenant}.covenant_type"],
             grain="covenant_type", units="covenants", key="covenant_type",
             value="breaches",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(covenant)[_flag(at(covenant), "breach_flag")]
                 .groupby("covenant_type").size().items()},
             tags=("covenants",)),

        case("C13", "How much exposure sits behind a covenant breach?",
             f"SELECT SUM(f.ead_sar_mn) AS ead_sar_mn FROM {facility} f "
             f"WHERE f.{q} = '{latest}' AND f.facility_id IN ("
             f"SELECT DISTINCT facility_id FROM {covenant} "
             f"WHERE {q} = '{latest}' AND breach_flag)",
             fields=[f"{facility}.ead_sar_mn", f"{covenant}.breach_flag"],
             grain="portfolio", units="SAR million", value="ead_sar_mn",
             oracle_fn=lambda: float(at(facility)[
                 at(facility)["facility_id"].isin(
                     at(covenant)[_flag(at(covenant), "breach_flag")][
                         "facility_id"].unique())]["ead_sar_mn"].sum()),
             tags=("covenants", "exposure")),

        case("C14", "What is collateral cover by collateral type?",
             f"SELECT collateral_type, "
             f"SUM(allocated_value_sar_mn) AS allocated_value_sar_mn "
             f"FROM {collateral} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{collateral}.allocated_value_sar_mn",
                     f"{collateral}.collateral_type"],
             grain="collateral_type", units="SAR million",
             key="collateral_type", value="allocated_value_sar_mn",
             oracle_fn=lambda: _sum_by(at(collateral), "collateral_type",
                                       "allocated_value_sar_mn"),
             tags=("collateral",)),

        case("C15", "What is average utilisation by product type?",
             f"SELECT product_type, AVG(utilisation_pct) AS utilisation_pct "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.utilisation_pct",
                     f"{facility}.product_type"],
             grain="product_type", units="percent", key="product_type",
             value="utilisation_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(facility), "product_type",
                                        "utilisation_pct"),
             tags=("exposure", "product")),

        case("C16", "How did total exposure move over the latest quarter?",
             f"SELECT SUM(CASE WHEN {q} = '{latest}' THEN ead_sar_mn "
             f"ELSE 0 END) - SUM(CASE WHEN {q} = '{previous}' "
             f"THEN ead_sar_mn ELSE 0 END) AS ead_movement_sar_mn "
             f"FROM {facility} WHERE {q} IN ('{latest}', '{previous}')",
             fields=[f"{facility}.ead_sar_mn"],
             grain="portfolio", units="SAR million",
             value="ead_movement_sar_mn",
             oracle_fn=lambda: float(
                 at(facility, latest)["ead_sar_mn"].sum()
                 - at(facility, previous)["ead_sar_mn"].sum()),
             notes="§4: quarter on quarter, not month on month.",
             tags=("movement",)),

        case("C17", "How did ECL move year on year?",
             f"SELECT SUM(CASE WHEN {q} = '{latest}' THEN ecl_sar_mn "
             f"ELSE 0 END) - SUM(CASE WHEN {q} = '{year_ago}' "
             f"THEN ecl_sar_mn ELSE 0 END) AS ecl_movement_sar_mn "
             f"FROM {facility} WHERE {q} IN ('{latest}', '{year_ago}')",
             fields=[f"{facility}.ecl_sar_mn"],
             grain="portfolio", units="SAR million",
             value="ecl_movement_sar_mn",
             oracle_fn=lambda: float(
                 at(facility, latest)["ecl_sar_mn"].sum()
                 - at(facility, year_ago)["ecl_sar_mn"].sum()),
             notes="§4: four quarters back, not thirteen slots.",
             tags=("movement", "yoy")),

        case("C18", "What share of exposure is in stage 3?",
             f"SELECT SUM(CASE WHEN stage = 3 THEN ead_sar_mn ELSE 0 END) "
             f"/ NULLIF(SUM(ead_sar_mn), 0) AS stage3_share "
             f"FROM {facility} WHERE {q} = '{latest}'",
             fields=[f"{facility}.ead_sar_mn", f"{facility}.stage"],
             grain="portfolio", units="percent", value="stage3_share",
             tolerance=1e-6,
             oracle_fn=lambda: float(
                 at(facility)[at(facility)["stage"] == 3]["ead_sar_mn"].sum()
                 / at(facility)["ead_sar_mn"].sum()),
             tags=("ifrs9",)),

        case("C19", "How much exposure is on the watchlist, by sector?",
             f"SELECT b.sector, SUM(f.ead_sar_mn) AS ead_sar_mn "
             f"FROM {facility} f JOIN {borrower} b "
             f"ON b.borrower_id = f.borrower_id AND b.{q} = f.{q} "
             f"WHERE f.{q} = '{latest}' AND b.watchlist_flag GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn", f"{borrower}.watchlist_flag"],
             grain="sector", units="SAR million", key="sector",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(
                 at(facility).merge(
                     at(borrower)[_flag(at(borrower), "watchlist_flag")][
                         ["borrower_id"]], on="borrower_id"),
                 "sector", "ead_sar_mn"),
             tags=("watchlist", "sector")),

        case("C20", "What have we written off this quarter, by sector?",
             f"SELECT sector, SUM(write_off_sar_mn) AS write_off_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.write_off_sar_mn", f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="write_off_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "sector",
                                       "write_off_sar_mn"),
             tags=("ifrs9", "loss")),

        case("C21", "What have we recovered this quarter, by sector?",
             f"SELECT sector, SUM(recovery_sar_mn) AS recovery_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.recovery_sar_mn", f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="recovery_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "sector",
                                       "recovery_sar_mn"),
             tags=("ifrs9", "loss")),

        case("C22", "How many facilities have cured, by sector?",
             # Over the WINDOW, not the latest quarter. Cures in this book
             # are concentrated in 2024Q4-2025Q2 -- an authored recovery
             # followed by a deterioration -- and a question pinned to the
             # latest quarter would return nothing and look like a defect
             # in the query rather than a fact about the book.
             f"SELECT sector, COUNT(DISTINCT facility_id) AS facilities "
             f"FROM {facility} WHERE cure_flag = 1 GROUP BY 1",
             fields=[f"{facility}.cure_flag", f"{facility}.facility_id",
                     f"{facility}.sector"],
             grain="sector", units="facilities", key="sector",
             value="facilities",
             oracle_fn=lambda: _count_by(
                 _corp(facility)[_flag(_corp(facility), "cure_flag")],
                 "sector", "facility_id"),
             tags=("ifrs9", "cure")),

        case("C23", "How much of the book is funded?",
             f"SELECT facility_class, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn",
                     f"{facility}.facility_class"],
             grain="facility_class", units="SAR million",
             key="facility_class", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "facility_class",
                                       "ead_sar_mn"),
             tags=("exposure", "product")),

        case("C24", "Which sub-sectors carry the most ECL?",
             f"SELECT sub_sector, SUM(ecl_sar_mn) AS ecl_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1 "
             f"ORDER BY ecl_sar_mn DESC, sub_sector LIMIT {TOP_N}",
             fields=[f"{facility}.ecl_sar_mn", f"{facility}.sub_sector"],
             grain="sub_sector", units="SAR million", key="sub_sector",
             value="ecl_sar_mn",
             oracle_fn=lambda: _top(_sum_by(at(facility), "sub_sector",
                                            "ecl_sar_mn")),
             tags=("ifrs9", "sector")),

        case("C25", "What is average leverage by sector?",
             f"SELECT sector, AVG(leverage_x) AS leverage_x "
             f"FROM {borrower} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.leverage_x", f"{borrower}.sector"],
             grain="sector", units="times", key="sector", value="leverage_x",
             tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(borrower), "sector", "leverage_x"),
             tags=("financials", "sector")),

        case("C26", "What is average debt service cover by sector?",
             f"SELECT sector, AVG(dscr_x) AS dscr_x FROM {borrower} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.dscr_x", f"{borrower}.sector"],
             grain="sector", units="times", key="sector", value="dscr_x",
             tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(borrower), "sector", "dscr_x"),
             tags=("financials", "sector")),

        case("C27", "What is average EBITDA margin by sector?",
             f"SELECT sector, AVG(ebitda_margin_pct) AS ebitda_margin_pct "
             f"FROM {borrower} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.ebitda_margin_pct", f"{borrower}.sector"],
             grain="sector", units="percent", key="sector",
             value="ebitda_margin_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(borrower), "sector",
                                        "ebitda_margin_pct"),
             tags=("financials", "sector")),

        case("C28", "What drives the ratings that moved?",
             f"SELECT rating_driver, COUNT(DISTINCT borrower_id) AS borrowers "
             f"FROM {borrower} WHERE {q} = '{latest}' "
             f"AND rating_migration <> 'STABLE' GROUP BY 1",
             fields=[f"{borrower}.rating_driver",
                     f"{borrower}.rating_migration"],
             grain="rating_driver", units="borrowers", key="rating_driver",
             value="borrowers",
             oracle_fn=lambda: _count_by(
                 at(borrower)[at(borrower)["rating_migration"] != "STABLE"],
                 "rating_driver", "borrower_id"),
             tags=("rating", "driver")),

        case("C29", "How far have ratings drifted from origination?",
             f"SELECT sector, AVG(rating_notches_from_origination) AS notches "
             f"FROM {borrower} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.rating_notches_from_origination",
                     f"{borrower}.sector"],
             grain="sector", units="notches", key="sector", value="notches",
             tolerance=1e-6,
             oracle_fn=lambda: _mean_by(
                 at(borrower), "sector", "rating_notches_from_origination"),
             tags=("rating", "history")),

        case("C30", "What is loss given default by product type?",
             f"SELECT product_type, AVG(lgd_pct) AS lgd_pct FROM {facility} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.lgd_pct", f"{facility}.product_type"],
             grain="product_type", units="percent", key="product_type",
             value="lgd_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(facility), "product_type",
                                        "lgd_pct"),
             tags=("ifrs9", "product")),

        case("C31", "What is average LTV by collateral type?",
             f"SELECT collateral_type, AVG(ltv_pct) AS ltv_pct "
             f"FROM {collateral} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{collateral}.ltv_pct",
                     f"{collateral}.collateral_type"],
             grain="collateral_type", units="percent", key="collateral_type",
             value="ltv_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(collateral), "collateral_type",
                                        "ltv_pct"),
             tags=("collateral",)),

        case("C32", "Which collateral valuations are the oldest?",
             # The column cycles one to four quarters, so "older than two
             # years" is unreachable in this release and a question asking
             # it returns nothing. Asking for the oldest band is the same
             # governance question against data that has an answer.
             f"SELECT collateral_type, COUNT(*) AS stale FROM {collateral} "
             f"WHERE {q} = '{latest}' AND valuation_age_quarters >= 4 "
             f"GROUP BY 1",
             fields=[f"{collateral}.valuation_age_quarters",
                     f"{collateral}.collateral_type"],
             grain="collateral_type", units="valuations",
             key="collateral_type", value="stale",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(collateral)[at(collateral)["valuation_age_quarters"] >= 4]
                 .groupby("collateral_type").size().items()},
             tags=("collateral", "governance")),

        case("C33", "How many covenants have been waived?",
             f"SELECT waiver_status, COUNT(*) AS covenants FROM {covenant} "
             f"WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{covenant}.waiver_status"],
             grain="waiver_status", units="covenants", key="waiver_status",
             value="covenants",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(covenant).groupby("waiver_status").size().items()},
             tags=("covenants",)),

        case("C34", "Which covenants have breached repeatedly?",
             f"SELECT covenant_type, COUNT(*) AS covenants FROM {covenant} "
             f"WHERE {q} = '{latest}' AND consecutive_breaches >= 2 "
             f"GROUP BY 1",
             fields=[f"{covenant}.consecutive_breaches",
                     f"{covenant}.covenant_type"],
             grain="covenant_type", units="covenants", key="covenant_type",
             value="covenants",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(covenant)[at(covenant)["consecutive_breaches"] >= 2]
                 .groupby("covenant_type").size().items()},
             tags=("covenants", "persistence")),

        case("C35", "How long have exposures been in their stage?",
             f"SELECT stage, AVG(quarters_in_stage) AS quarters "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.quarters_in_stage", f"{facility}.stage"],
             grain="stage", units="quarters", key="stage", value="quarters",
             tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(facility), "stage",
                                        "quarters_in_stage"),
             tags=("ifrs9", "persistence")),

        case("C36", "What is undrawn commitment by sector?",
             f"SELECT sector, SUM(undrawn_sar_mn) AS undrawn_sar_mn "
             f"FROM {facility} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{facility}.undrawn_sar_mn", f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="undrawn_sar_mn",
             oracle_fn=lambda: _sum_by(at(facility), "sector",
                                       "undrawn_sar_mn"),
             tags=("exposure", "sector")),

        case("C37", "How many facilities are past due, by sector?",
             f"SELECT sector, COUNT(DISTINCT facility_id) AS facilities "
             f"FROM {facility} WHERE {q} = '{latest}' AND dpd_days > 0 "
             f"GROUP BY 1",
             fields=[f"{facility}.dpd_days", f"{facility}.facility_id",
                     f"{facility}.sector"],
             grain="sector", units="facilities", key="sector",
             value="facilities",
             oracle_fn=lambda: _count_by(
                 at(facility)[at(facility)["dpd_days"] > 0], "sector",
                 "facility_id"),
             tags=("delinquency", "sector")),

        case("C38", "What is 12-month PD by rating?",
             f"SELECT rating_current, AVG(pd_ttc_12m) AS pd_ttc_12m "
             f"FROM {borrower} WHERE {q} = '{latest}' GROUP BY 1",
             fields=[f"{borrower}.pd_ttc_12m",
                     f"{borrower}.rating_current"],
             grain="rating_current", units="percent", key="rating_current",
             value="pd_ttc_12m", tolerance=1e-9,
             oracle_fn=lambda: _mean_by(at(borrower), "rating_current",
                                        "pd_ttc_12m"),
             tags=("rating", "pd")),

        case("C39", "How many borrowers have been restructured?",
             f"SELECT sector, COUNT(DISTINCT borrower_id) AS borrowers "
             f"FROM {borrower} WHERE {q} = '{latest}' AND restructured_flag "
             f"GROUP BY 1",
             fields=[f"{borrower}.restructured_flag",
                     f"{borrower}.borrower_id", f"{borrower}.sector"],
             grain="sector", units="borrowers", key="sector",
             value="borrowers",
             oracle_fn=lambda: _count_by(
                 at(borrower)[_flag(at(borrower), "restructured_flag")], "sector",
                 "borrower_id"),
             tags=("forbearance",)),

        case("C40", "How much of project finance sits in each sector?",
             f"SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn FROM {facility} "
             f"WHERE {q} = '{latest}' AND product_type = 'project_finance' "
             f"GROUP BY 1",
             fields=[f"{facility}.ead_sar_mn", f"{facility}.product_type",
                     f"{facility}.sector"],
             grain="sector", units="SAR million", key="sector",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(
                 at(facility)[at(facility)["product_type"]
                              == "project_finance"], "sector", "ead_sar_mn"),
             notes="§38, §39: the product is a FILTER, the sector is the cut.",
             tags=("product", "crosscut")),
    )


# ---- the Retail bank ----------------------------------------------------

def retail_cases() -> tuple[Case, ...]:
    latest = oracle.latest_period(dom.RETAIL)
    previous = oracle.previous_period(dom.RETAIL)
    year_ago = oracle.year_ago(dom.RETAIL)
    account = "retail_account_month"
    customer = "retail_customer_month"
    behaviour = "retail_behaviour_month"
    collateral = "retail_collateral_month"
    m = "reporting_month"

    def at(relation: str, period: str = latest) -> pd.DataFrame:
        return _at(_retail(relation), dom.RETAIL, period)

    def case(case_id: str, question: str, sql: str, *, fields, grain,
             units, oracle_fn, key="", value="", tolerance=0.01,
             notes="", tags=()) -> Case:
        return Case(case_id=case_id, domain_id=dom.RETAIL,
                    question=question, sql=sql, fields=tuple(fields),
                    grain=grain, units=units, oracle=oracle_fn, key=key,
                    value=value, tolerance=tolerance, notes=notes,
                    tags=tuple(tags))

    return (
        case("R01", "What is exposure at default by product this month?",
             f"SELECT product, SUM(ead_sar_mn) AS ead_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "product", "ead_sar_mn"),
             tags=("exposure", "product")),

        case("R02", "What is recognised ECL by product?",
             f"SELECT product, SUM(ecl_sar_mn) AS ecl_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ecl_sar_mn", f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="ecl_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "product", "ecl_sar_mn"),
             tags=("ifrs9", "product")),

        case("R03", "What is ECL by behavioural score band?",
             f"SELECT score_band, SUM(ecl_sar_mn) AS ecl_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ecl_sar_mn", f"{account}.score_band"],
             grain="score_band", units="SAR million", key="score_band",
             value="ecl_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "score_band",
                                       "ecl_sar_mn"),
             tags=("ifrs9", "behaviour")),

        case("R04", "What share of exposure is in stage 2, by product?",
             f"SELECT product, SUM(CASE WHEN stage = 2 THEN ead_sar_mn "
             f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.stage",
                     f"{account}.product"],
             grain="product", units="percent", key="product",
             value="stage2_share", tolerance=1e-6,
             oracle_fn=lambda: _share_by(at(account), "product",
                                         "ead_sar_mn",
                                         at(account)["stage"] == 2),
             tags=("ifrs9", "product")),

        case("R05", "What is exposure by delinquency bucket?",
             f"SELECT delinquency_bucket, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn",
                     f"{account}.delinquency_bucket"],
             grain="delinquency_bucket", units="SAR million",
             key="delinquency_bucket", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "delinquency_bucket",
                                       "ead_sar_mn"),
             tags=("delinquency",)),

        case("R06", "What is exposure by origination vintage?",
             f"SELECT CAST(vintage_year AS VARCHAR) AS vintage_year, "
             f"SUM(ead_sar_mn) AS ead_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.vintage_year"],
             grain="vintage_year", units="SAR million", key="vintage_year",
             value="ead_sar_mn",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(account).groupby("vintage_year")["ead_sar_mn"]
                 .sum().items()},
             tags=("vintage",)),

        case("R07", "What is exposure by customer segment?",
             f"SELECT customer_segment, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn",
                     f"{account}.customer_segment"],
             grain="customer_segment", units="SAR million",
             key="customer_segment", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "customer_segment",
                                       "ead_sar_mn"),
             tags=("exposure", "segmentation")),

        case("R08", "What is exposure by region?",
             f"SELECT region, SUM(ead_sar_mn) AS ead_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.region"],
             grain="region", units="SAR million", key="region",
             value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "region", "ead_sar_mn"),
             tags=("exposure", "region")),

        case("R09", "How many accounts cured this month, by product?",
             f"SELECT product, COUNT(DISTINCT account_id) AS accounts "
             f"FROM {account} WHERE {m} = '{latest}' AND cure_flag GROUP BY 1",
             fields=[f"{account}.cure_flag", f"{account}.account_id",
                     f"{account}.product"],
             grain="product", units="accounts", key="product",
             value="accounts",
             oracle_fn=lambda: _count_by(
                 at(account)[_flag(at(account), "cure_flag")], "product",
                 "account_id"),
             tags=("cure",)),

        case("R10", "How many customers' scores deteriorated?",
             f"SELECT customer_segment, COUNT(DISTINCT customer_id) "
             f"AS customers FROM {customer} WHERE {m} = '{latest}' "
             f"AND score_migration = 'DETERIORATED' GROUP BY 1",
             fields=[f"{customer}.score_migration",
                     f"{customer}.customer_id",
                     f"{customer}.customer_segment"],
             grain="customer_segment", units="customers",
             key="customer_segment", value="customers",
             oracle_fn=lambda: _count_by(
                 at(customer)[at(customer)["score_migration"]
                              == "DETERIORATED"],
                 "customer_segment", "customer_id"),
             tags=("behaviour", "migration")),

        case("R11", "Which customers carry the most exposure?",
             f"SELECT customer_id, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1 "
             f"ORDER BY ead_sar_mn DESC, customer_id LIMIT {TOP_N}",
             fields=[f"{account}.ead_sar_mn", f"{account}.customer_id"],
             grain="customer", units="SAR million", key="customer_id",
             value="ead_sar_mn",
             oracle_fn=lambda: _top(_sum_by(at(account), "customer_id",
                                            "ead_sar_mn")),
             tags=("concentration",)),

        case("R12", "How much of the book is secured?",
             f"SELECT CAST(secured_flag AS VARCHAR) AS secured_flag, "
             f"SUM(ead_sar_mn) AS ead_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.secured_flag"],
             grain="secured_flag", units="SAR million", key="secured_flag",
             value="ead_sar_mn",
             oracle_fn=lambda: {
                 str(k).lower(): float(v) for k, v in
                 at(account).groupby("secured_flag")["ead_sar_mn"]
                 .sum().items()},
             tags=("collateral", "exposure")),

        case("R13", "How did total exposure move over the latest month?",
             f"SELECT SUM(CASE WHEN {m} = '{latest}' THEN ead_sar_mn "
             f"ELSE 0 END) - SUM(CASE WHEN {m} = '{previous}' "
             f"THEN ead_sar_mn ELSE 0 END) AS ead_movement_sar_mn "
             f"FROM {account} WHERE {m} IN ('{latest}', '{previous}')",
             fields=[f"{account}.ead_sar_mn"],
             grain="portfolio", units="SAR million",
             value="ead_movement_sar_mn",
             oracle_fn=lambda: float(
                 at(account, latest)["ead_sar_mn"].sum()
                 - at(account, previous)["ead_sar_mn"].sum()),
             tags=("movement",)),

        case("R14", "How did ECL move year on year?",
             f"SELECT SUM(CASE WHEN {m} = '{latest}' THEN ecl_sar_mn "
             f"ELSE 0 END) - SUM(CASE WHEN {m} = '{year_ago}' "
             f"THEN ecl_sar_mn ELSE 0 END) AS ecl_movement_sar_mn "
             f"FROM {account} WHERE {m} IN ('{latest}', '{year_ago}')",
             fields=[f"{account}.ecl_sar_mn"],
             grain="portfolio", units="SAR million",
             value="ecl_movement_sar_mn",
             oracle_fn=lambda: float(
                 at(account, latest)["ecl_sar_mn"].sum()
                 - at(account, year_ago)["ecl_sar_mn"].sum()),
             notes="§3: twelve months back.",
             tags=("movement", "yoy")),

        case("R15", "What is ECL coverage of exposure?",
             f"SELECT SUM(ecl_sar_mn) / NULLIF(SUM(ead_sar_mn), 0) "
             f"AS coverage FROM {account} WHERE {m} = '{latest}'",
             fields=[f"{account}.ecl_sar_mn", f"{account}.ead_sar_mn"],
             grain="portfolio", units="percent", value="coverage",
             tolerance=1e-9,
             oracle_fn=lambda: float(at(account)["ecl_sar_mn"].sum()
                                     / at(account)["ead_sar_mn"].sum()),
             tags=("ifrs9",)),

        case("R16", "What share of exposure is past due, by product?",
             f"SELECT product, SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn "
             f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS dpd_share "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.dpd_days", f"{account}.ead_sar_mn",
                     f"{account}.product"],
             grain="product", units="percent", key="product",
             value="dpd_share", tolerance=1e-6,
             oracle_fn=lambda: _share_by(at(account), "product",
                                         "ead_sar_mn",
                                         at(account)["dpd_days"] > 0),
             tags=("delinquency", "product")),

        case("R17", "What is average utilisation by product?",
             f"SELECT product, AVG(utilisation_pct) AS utilisation_pct "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.utilisation_pct", f"{account}.product"],
             grain="product", units="percent", key="product",
             value="utilisation_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(account), "product",
                                        "utilisation_pct"),
             tags=("behaviour", "product")),

        case("R18", "What have we written off this month, by product?",
             f"SELECT product, SUM(write_off_sar_mn) AS write_off_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.write_off_sar_mn", f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="write_off_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "product",
                                       "write_off_sar_mn"),
             tags=("loss",)),

        case("R19", "What have we recovered this month, by product?",
             f"SELECT product, SUM(recovery_sar_mn) AS recovery_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.recovery_sar_mn", f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="recovery_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "product",
                                       "recovery_sar_mn"),
             tags=("loss",)),

        case("R20", "What is loss given default by product?",
             f"SELECT product, AVG(lgd_pct) AS lgd_pct FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.lgd_pct", f"{account}.product"],
             grain="product", units="percent", key="product",
             value="lgd_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(account), "product", "lgd_pct"),
             tags=("ifrs9", "product")),

        case("R21", "What is 12-month PD by score band?",
             f"SELECT score_band, AVG(pd_pit_12m) AS pd_pit_12m "
             f"FROM {account} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.pd_pit_12m", f"{account}.score_band"],
             grain="score_band", units="percent", key="score_band",
             value="pd_pit_12m", tolerance=1e-9,
             oracle_fn=lambda: _mean_by(at(account), "score_band",
                                        "pd_pit_12m"),
             tags=("behaviour", "pd")),

        case("R22", "How does stage 2 vary with months on book?",
             f"SELECT CAST(months_on_book // 12 AS VARCHAR) AS years_on_book, "
             f"SUM(CASE WHEN stage = 2 THEN ead_sar_mn ELSE 0 END) "
             f"/ NULLIF(SUM(ead_sar_mn), 0) AS stage2_share FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.months_on_book", f"{account}.stage",
                     f"{account}.ead_sar_mn"],
             grain="years_on_book", units="percent", key="years_on_book",
             value="stage2_share", tolerance=1e-6,
             oracle_fn=lambda: _seasoning(at(account)),
             tags=("vintage", "seasoning")),

        case("R23", "What is average payment ratio by product?",
             f"SELECT product, AVG(payment_ratio_pct) AS payment_ratio_pct "
             f"FROM {behaviour} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{behaviour}.payment_ratio_pct",
                     f"{behaviour}.product"],
             grain="product", units="percent", key="product",
             value="payment_ratio_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(behaviour), "product",
                                        "payment_ratio_pct"),
             tags=("behaviour",)),

        case("R24", "How many accounts missed a payment in the last year?",
             f"SELECT product, COUNT(DISTINCT account_id) AS accounts "
             f"FROM {behaviour} WHERE {m} = '{latest}' "
             f"AND missed_payments_12m > 0 GROUP BY 1",
             fields=[f"{behaviour}.missed_payments_12m",
                     f"{behaviour}.account_id", f"{behaviour}.product"],
             grain="product", units="accounts", key="product",
             value="accounts",
             oracle_fn=lambda: _count_by(
                 at(behaviour)[at(behaviour)["missed_payments_12m"] > 0],
                 "product", "account_id"),
             tags=("behaviour", "delinquency")),

        case("R25", "How many accounts are over their limit?",
             f"SELECT product, COUNT(DISTINCT account_id) AS accounts "
             f"FROM {behaviour} WHERE {m} = '{latest}' AND overlimit_flag "
             f"GROUP BY 1",
             fields=[f"{behaviour}.overlimit_flag",
                     f"{behaviour}.account_id", f"{behaviour}.product"],
             grain="product", units="accounts", key="product",
             value="accounts",
             oracle_fn=lambda: _count_by(
                 at(behaviour)[_flag(at(behaviour), "overlimit_flag")], "product",
                 "account_id"),
             tags=("behaviour",)),

        case("R26", "What is average cash advance ratio by product?",
             f"SELECT product, AVG(cash_advance_ratio_pct) AS ratio "
             f"FROM {behaviour} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{behaviour}.cash_advance_ratio_pct",
                     f"{behaviour}.product"],
             grain="product", units="percent", key="product", value="ratio",
             tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(behaviour), "product",
                                        "cash_advance_ratio_pct"),
             tags=("behaviour",)),

        case("R27", "How many bureau enquiries have customers made?",
             f"SELECT product, AVG(bureau_inquiries_6m) AS inquiries "
             f"FROM {behaviour} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{behaviour}.bureau_inquiries_6m",
                     f"{behaviour}.product"],
             grain="product", units="enquiries", key="product",
             value="inquiries", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(behaviour), "product",
                                        "bureau_inquiries_6m"),
             tags=("behaviour", "bureau")),

        case("R28", "What is average LTV by collateral type?",
             f"SELECT collateral_type, AVG(ltv_pct) AS ltv_pct "
             f"FROM {collateral} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{collateral}.ltv_pct",
                     f"{collateral}.collateral_type"],
             grain="collateral_type", units="percent",
             key="collateral_type", value="ltv_pct", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(collateral), "collateral_type",
                                        "ltv_pct"),
             tags=("collateral",)),

        case("R29", "What is the value of collateral held, by type?",
             f"SELECT collateral_type, "
             f"SUM(collateral_value_sar_mn) AS collateral_value_sar_mn "
             f"FROM {collateral} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{collateral}.collateral_value_sar_mn",
                     f"{collateral}.collateral_type"],
             grain="collateral_type", units="SAR million",
             key="collateral_type", value="collateral_value_sar_mn",
             oracle_fn=lambda: _sum_by(at(collateral), "collateral_type",
                                       "collateral_value_sar_mn"),
             tags=("collateral",)),

        case("R30", "How many customers are in each score band?",
             f"SELECT score_band, COUNT(DISTINCT customer_id) AS customers "
             f"FROM {customer} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{customer}.score_band", f"{customer}.customer_id"],
             grain="score_band", units="customers", key="score_band",
             value="customers",
             oracle_fn=lambda: _count_by(at(customer), "score_band",
                                         "customer_id"),
             tags=("behaviour",)),

        case("R31", "What is average tenure by customer segment?",
             f"SELECT customer_segment, AVG(tenure_months) AS tenure_months "
             f"FROM {customer} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{customer}.tenure_months",
                     f"{customer}.customer_segment"],
             grain="customer_segment", units="months", key="customer_segment",
             value="tenure_months", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(customer), "customer_segment",
                                        "tenure_months"),
             tags=("identity",)),

        case("R32", "How many products does each segment hold?",
             f"SELECT customer_segment, AVG(accounts_held) AS accounts_held "
             f"FROM {customer} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{customer}.accounts_held",
                     f"{customer}.customer_segment"],
             grain="customer_segment", units="accounts",
             key="customer_segment", value="accounts_held", tolerance=1e-6,
             oracle_fn=lambda: _mean_by(at(customer), "customer_segment",
                                        "accounts_held"),
             tags=("identity", "cross-sell")),

        case("R33", "What is the worst stage each segment reaches?",
             f"SELECT customer_segment, MAX(worst_stage) AS worst_stage "
             f"FROM {customer} WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{customer}.worst_stage",
                     f"{customer}.customer_segment"],
             grain="customer_segment", units="stage", key="customer_segment",
             value="worst_stage",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 at(customer).groupby("customer_segment")["worst_stage"]
                 .max().items()},
             tags=("ifrs9",)),

        case("R34", "What is exposure by product and region?",
             f"SELECT product || ' / ' || region AS product_region, "
             f"SUM(ead_sar_mn) AS ead_sar_mn FROM {account} "
             f"WHERE {m} = '{latest}' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.product",
                     f"{account}.region"],
             grain="product_region", units="SAR million",
             key="product_region", value="ead_sar_mn",
             oracle_fn=lambda: {
                 f"{p} / {r}": float(v) for (p, r), v in
                 at(account).groupby(["product", "region"])["ead_sar_mn"]
                 .sum().items()},
             tags=("crosscut",)),

        case("R35", "How much credit card exposure is past due?",
             f"SELECT delinquency_bucket, SUM(ead_sar_mn) AS ead_sar_mn "
             f"FROM {account} WHERE {m} = '{latest}' "
             f"AND product = 'Credit Card' GROUP BY 1",
             fields=[f"{account}.ead_sar_mn", f"{account}.product",
                     f"{account}.delinquency_bucket"],
             grain="delinquency_bucket", units="SAR million",
             key="delinquency_bucket", value="ead_sar_mn",
             oracle_fn=lambda: _sum_by(
                 at(account)[at(account)["product"] == "Credit Card"],
                 "delinquency_bucket", "ead_sar_mn"),
             notes="§39: the product is a filter, the bucket is the cut.",
             tags=("crosscut", "delinquency")),

        case("R36", "How many accounts are in default, by product?",
             f"SELECT product, COUNT(DISTINCT account_id) AS accounts "
             f"FROM {account} WHERE {m} = '{latest}' AND default_flag "
             f"GROUP BY 1",
             fields=[f"{account}.default_flag", f"{account}.account_id",
                     f"{account}.product"],
             grain="product", units="accounts", key="product",
             value="accounts",
             oracle_fn=lambda: _count_by(
                 at(account)[_flag(at(account), "default_flag")], "product",
                 "account_id"),
             tags=("delinquency",)),

        case("R37", "What is limit headroom by product?",
             f"SELECT product, SUM(limit_sar_mn) - SUM(balance_sar_mn) "
             f"AS headroom_sar_mn FROM {account} WHERE {m} = '{latest}' "
             f"GROUP BY 1",
             fields=[f"{account}.limit_sar_mn", f"{account}.balance_sar_mn",
                     f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="headroom_sar_mn",
             oracle_fn=lambda: {
                 str(k): float(v) for k, v in
                 (at(account).groupby("product")["limit_sar_mn"].sum()
                  - at(account).groupby("product")["balance_sar_mn"].sum()
                  ).items()},
             tags=("exposure", "product")),

        case("R38", "How many accounts flagged SICR, by product?",
             f"SELECT product, COUNT(DISTINCT account_id) AS accounts "
             f"FROM {account} WHERE {m} = '{latest}' AND sicr_flag GROUP BY 1",
             fields=[f"{account}.sicr_flag", f"{account}.account_id",
                     f"{account}.product"],
             grain="product", units="accounts", key="product",
             value="accounts",
             oracle_fn=lambda: _count_by(
                 at(account)[_flag(at(account), "sicr_flag")], "product",
                 "account_id"),
             tags=("ifrs9",)),

        case("R39", "What is lifetime ECL by product?",
             f"SELECT product, SUM(ecl_lifetime_sar_mn) "
             f"AS ecl_lifetime_sar_mn FROM {account} WHERE {m} = '{latest}' "
             f"GROUP BY 1",
             fields=[f"{account}.ecl_lifetime_sar_mn",
                     f"{account}.product"],
             grain="product", units="SAR million", key="product",
             value="ecl_lifetime_sar_mn",
             oracle_fn=lambda: _sum_by(at(account), "product",
                                       "ecl_lifetime_sar_mn"),
             tags=("ifrs9", "product")),

        case("R40", "How has the 2024 vintage performed by product?",
             f"SELECT product, SUM(CASE WHEN stage >= 2 THEN ead_sar_mn "
             f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
             f"FROM {account} WHERE {m} = '{latest}' AND vintage_year = 2024 "
             f"GROUP BY 1",
             fields=[f"{account}.vintage_year", f"{account}.stage",
                     f"{account}.ead_sar_mn", f"{account}.product"],
             grain="product", units="percent", key="product",
             value="stage2_share", tolerance=1e-6,
             oracle_fn=lambda: _share_by(
                 at(account)[at(account)["vintage_year"] == 2024],
                 "product", "ead_sar_mn",
                 at(account)[at(account)["vintage_year"] == 2024]["stage"]
                 >= 2),
             notes="§10: the authored 2024 vintage story.",
             tags=("vintage", "crosscut")),
    )


def _seasoning(frame: pd.DataFrame) -> dict[str, float]:
    """Stage-2 share by whole years on book, in pandas.

    DuckDB's `months_on_book / 12` on an integer column truncates; this
    mirrors that explicitly rather than relying on pandas matching it by
    accident.
    """
    years = (frame["months_on_book"] // 12).astype(int)
    whole = frame.groupby(years)["ead_sar_mn"].sum()
    part = (frame[frame["stage"] == 2].groupby(years)["ead_sar_mn"].sum()
            .reindex(whole.index).fillna(0))
    return {str(k): float(part[k] / whole[k]) if whole[k] else 0.0
            for k in whole.index}


def all_cases() -> tuple[Case, ...]:
    return corporate_cases() + retail_cases()


__all__ = ["Case", "TOP_N", "all_cases", "corporate_cases", "retail_cases"]
