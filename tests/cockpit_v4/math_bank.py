"""
Fifteen mathematical questions, and the answer each one must produce.

LAYER A for the mathematical round. Every figure here is computed with
pandas straight from the pinned Parquet and nothing in this file imports the
query path, the catalog, the validator, the executor or the derivation
engine. Nothing is hard-coded: the numbers are recomputed from the release at
test time, so the bank travels to a different release and still means
something.

The bank exists to answer one question about each M-query: not "did the SQL
run" but "is the published answer, including every number it calculated on
top of the result, the right answer".
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pandas as pd

RELEASE = os.environ.get("COCKPIT_V4_TEST_RELEASE", "v4-saudi-20q-v1")

#: Chart policy, stated per question with a reason rather than applied by
#: rule. `NONE` is a decision, not an omission.
BAR, NONE = "horizontal_bar", "none"


def _frame(relation: str, release_id: str = "") -> pd.DataFrame:
    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
    from backend.cockpit_agentic import store

    return pd.read_parquet(
        store.relation_path(release_id or RELEASE, relation))


def quarters(release_id: str = "") -> list[str]:
    frame = _frame("cockpit_facility_quarter", release_id)
    return sorted(str(q) for q in frame["reporting_quarter"].dropna().unique())


def periods(release_id: str = "") -> dict[str, str]:
    slots = quarters(release_id)
    return {"latest": slots[-1], "prior": slots[-2], "year_ago": slots[-5]}


def _facility(release_id: str = "") -> pd.DataFrame:
    frame = _frame("cockpit_facility_quarter", release_id)
    return frame[frame["sector_name"].notna()]


def _at(quarter: str, release_id: str = "") -> pd.DataFrame:
    slice_ = _facility(release_id)
    return slice_[slice_["reporting_quarter"] == quarter]


def _sectors(quarter: str, release_id: str = "") -> pd.DataFrame:
    """Sector totals, ordered by EAD descending then name -- the answer's order."""
    rows = _at(quarter, release_id).groupby("sector_name", observed=True).agg(
        ead=("ead_reported", "sum"), ecl=("ecl_reported", "sum"),
        facilities=("facility_id", "nunique"),
        stage2_ead=("ead_reported",
                    lambda s: float(s[_at(quarter, release_id).loc[
                        s.index, "ifrs9_stage"] == 2].sum())),
    ).reset_index()
    return rows.sort_values(["ead", "sector_name"],
                            ascending=[False, True]).reset_index(drop=True)


def _borrowers(quarter: str, release_id: str = "") -> pd.DataFrame:
    rows = _at(quarter, release_id).groupby("borrower_id", observed=True).agg(
        sector_name=("sector_name", "first"),
        ead=("ead_reported", "sum"), ecl=("ecl_reported", "sum"),
        stage=("ifrs9_stage", "max")).reset_index()
    return rows.sort_values(["ecl", "borrower_id"],
                            ascending=[False, True]).reset_index(drop=True)


QUESTIONS: list[dict[str, Any]] = [
    {"id": "M01", "band": "simple",
     "text": "What is total exposure at default by sector in the latest "
             "quarter?",
     "chart": BAR,
     "chart_reason": "a ranked sector comparison reads faster as bars",
     "derived": ["total across sectors", "each sector's share"]},
    {"id": "M02", "band": "simple",
     "text": "Show ECL by sector in the latest quarter and rank the top five.",
     "chart": BAR, "chart_reason": "five ranked sectors on one measure",
     "derived": ["book ECL", "share carried by the top five"]},
    {"id": "M03", "band": "simple",
     "text": "Show Stage 2 EAD by sector and each sector's share of total "
             "Stage 2 EAD.",
     "chart": BAR, "chart_reason": "shares of one total compare as bars",
     "derived": ["total Stage 2 EAD", "per-sector share"]},
    {"id": "M04", "band": "simple",
     "text": "Show the top ten borrowers by ECL in the latest quarter.",
     "chart": BAR, "chart_reason": "ten named contributors rank usefully",
     "derived": ["ECL held by the ten", "their share of book ECL"]},
    {"id": "M05", "band": "simple",
     "text": "How much of total EAD is held by the five largest sectors?",
     "chart": NONE,
     "chart_reason": "the answer is one share; a chart of one number is "
                     "decoration",
     "derived": ["top-five EAD", "book EAD", "the share"]},
    {"id": "M06", "band": "movement",
     "text": "Which sectors saw the largest Stage 2 EAD increase over the "
             "latest year?",
     "chart": BAR, "chart_reason": "a ranked movement compares as bars",
     "derived": ["per-sector change", "total change"]},
    {"id": "M07", "band": "movement",
     "text": "Which sectors had ECL grow faster than EAD this quarter?",
     "chart": NONE,
     "chart_reason": "two growth rates per sector; the gap is the finding "
                     "and it belongs in a table",
     "derived": ["ECL growth", "EAD growth", "the gap"]},
    {"id": "M08", "band": "movement",
     "text": "Which sectors contributed most to the change in total ECL this "
             "quarter?",
     "chart": BAR,
     "chart_reason": "the parts sum exactly to the whole movement",
     "derived": ["per-sector contribution", "book delta"]},
    {"id": "M09", "band": "movement",
     "text": "Which borrowers had the largest ECL increase "
             "quarter-on-quarter?",
     "chart": BAR, "chart_reason": "named movers rank usefully",
     "derived": ["per-borrower increase"]},
    {"id": "M10", "band": "movement",
     "text": "Which borrowers were downgraded and moved into Stage 2 this "
             "quarter?",
     "chart": NONE,
     "chart_reason": "an intersection of two conditions is a short list, "
                     "possibly empty",
     "derived": ["EAD and ECL carried by the group"]},
    {"id": "M11", "band": "complex",
     "text": "Show portfolio EAD, ECL, ECL/EAD and Stage 2 share for the "
             "latest quarter and compare each with the previous quarter.",
     "chart": NONE,
     "chart_reason": "four KPIs with movements is a KPI table; a chart of "
                     "four scalars is decoration",
     "derived": ["each KPI", "each movement"]},
    {"id": "M12", "band": "complex",
     "text": "For Construction, compare EAD, ECL and Stage 2 share with a "
             "year ago.",
     "chart": NONE, "chart_reason": "three numbers and their movements",
     "derived": ["each KPI", "each movement"]},
    {"id": "M13", "band": "complex",
     "text": "Which sectors combine rising ECL and weakening collateral "
             "coverage?",
     "chart": NONE,
     "chart_reason": "two movements on different scales; a dual axis would "
                     "imply a relationship the evidence does not support",
     "derived": ["ECL change", "uncovered-share change"]},
    {"id": "M14", "band": "complex",
     "text": "What percentage of total ECL is carried by the ten largest "
             "borrowers?",
     "chart": NONE, "chart_reason": "the answer is one percentage",
     "derived": ["top-ten ECL", "book ECL", "the percentage"]},
    {"id": "M15", "band": "complex",
     "text": "Which sectors have the greatest concentration of ECL among "
             "their top three borrowers?",
     "chart": BAR, "chart_reason": "a ranked concentration per sector",
     "derived": ["top-three ECL per sector", "sector ECL", "the share"]},
]

BY_ID = {q["id"]: q for q in QUESTIONS}
ALL = [q["id"] for q in QUESTIONS]


# ---- the oracles -------------------------------------------------------

def _d(value) -> Decimal:
    return Decimal(str(value))


def _m01(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    rows = _sectors(period["latest"], release_id)
    total = float(rows["ead"].sum())
    return {"period": period["latest"],
            "rows": [{"sector_name": r.sector_name, "ead": float(r.ead),
                      "facilities": int(r.facilities)}
                     for r in rows.itertuples()],
            "row_count": int(len(rows)),
            "total_ead": total,
            "total_facilities": int(rows["facilities"].sum()),
            "shares": [float(r.ead) / total for r in rows.itertuples()],
            "top_sector": rows.iloc[0].sector_name,
            "top_share": float(rows.iloc[0].ead) / total,
            "top4_share": float(rows.head(4)["ead"].sum()) / total}


def _m02(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    rows = _sectors(period["latest"], release_id).sort_values(
        ["ecl", "sector_name"], ascending=[False, True]).reset_index(drop=True)
    book = float(rows["ecl"].sum())
    top5 = rows.head(5)
    return {"period": period["latest"],
            "rows": [{"sector_name": r.sector_name, "ecl": float(r.ecl)}
                     for r in rows.itertuples()],
            "row_count": int(len(rows)), "book_ecl": book,
            "top5": [r.sector_name for r in top5.itertuples()],
            "top5_ecl": float(top5["ecl"].sum()),
            "top5_share": float(top5["ecl"].sum()) / book}


def _m03(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    rows = _sectors(period["latest"], release_id)
    rows = rows[rows["stage2_ead"] > 0].sort_values(
        ["stage2_ead", "sector_name"],
        ascending=[False, True]).reset_index(drop=True)
    total = float(rows["stage2_ead"].sum())
    return {"period": period["latest"],
            "rows": [{"sector_name": r.sector_name,
                      "stage2_ead": float(r.stage2_ead)}
                     for r in rows.itertuples()],
            "row_count": int(len(rows)), "total_stage2_ead": total,
            "shares": [float(r.stage2_ead) / total for r in rows.itertuples()],
            "shares_sum": 1.0}


def _m04(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    everyone = _borrowers(period["latest"], release_id)
    top = everyone.head(10)
    book = float(everyone["ecl"].sum())
    return {"period": period["latest"],
            "rows": [{"borrower_id": r.borrower_id,
                      "sector_name": r.sector_name, "ead": float(r.ead),
                      "ecl": float(r.ecl), "stage": int(r.stage)}
                     for r in top.itertuples()],
            "row_count": int(len(top)), "book_ecl": book,
            "top10_ecl": float(top["ecl"].sum()),
            "top10_share": float(top["ecl"].sum()) / book}


def _m05(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    rows = _sectors(period["latest"], release_id)
    total = float(rows["ead"].sum())
    top5 = rows.head(5)
    return {"period": period["latest"],
            "rows": [{"sector_name": r.sector_name, "ead": float(r.ead)}
                     for r in rows.itertuples()],
            "row_count": int(len(rows)),
            "top5": [r.sector_name for r in top5.itertuples()],
            "top5_ead": float(top5["ead"].sum()), "total_ead": total,
            "top5_share": float(top5["ead"].sum()) / total}


def _m06(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _sectors(period["latest"], release_id).set_index("sector_name")
    then = _sectors(period["year_ago"], release_id).set_index("sector_name")
    rows = []
    for sector in sorted(set(now.index) | set(then.index)):
        current = float(now["stage2_ead"].get(sector, 0.0))
        prior = float(then["stage2_ead"].get(sector, 0.0))
        rows.append({"sector_name": sector, "current": current,
                     "prior": prior, "change": current - prior})
    rows.sort(key=lambda r: (-r["change"], r["sector_name"]))
    return {"period": period["latest"], "comparison": period["year_ago"],
            "rows": rows, "row_count": len(rows),
            "total_change": sum(r["change"] for r in rows)}


def _growth(new: float, old: float) -> float | None:
    return None if old == 0 else (new - old) / old


def _m07(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _sectors(period["latest"], release_id).set_index("sector_name")
    then = _sectors(period["prior"], release_id).set_index("sector_name")
    rows, undefined = [], []
    for sector in sorted(set(now.index) & set(then.index)):
        ecl_g = _growth(float(now["ecl"][sector]), float(then["ecl"][sector]))
        ead_g = _growth(float(now["ead"][sector]), float(then["ead"][sector]))
        if ecl_g is None or ead_g is None:
            undefined.append(sector)
            continue
        if ecl_g > ead_g:
            rows.append({"sector_name": sector, "ecl_growth": ecl_g,
                         "ead_growth": ead_g, "gap": ecl_g - ead_g})
    rows.sort(key=lambda r: (-r["gap"], r["sector_name"]))
    return {"period": period["latest"], "comparison": period["prior"],
            "rows": rows, "row_count": len(rows),
            "growth_undefined": undefined}


def _m08(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _sectors(period["latest"], release_id).set_index("sector_name")
    then = _sectors(period["prior"], release_id).set_index("sector_name")
    rows = []
    for sector in sorted(set(now.index) | set(then.index)):
        current = float(now["ecl"].get(sector, 0.0))
        prior = float(then["ecl"].get(sector, 0.0))
        rows.append({"sector_name": sector, "contribution": current - prior})
    rows.sort(key=lambda r: (-abs(r["contribution"]), r["sector_name"]))
    book_delta = float(now["ecl"].sum()) - float(then["ecl"].sum())
    return {"period": period["latest"], "comparison": period["prior"],
            "rows": rows, "row_count": len(rows), "book_delta": book_delta,
            "contributions_sum": sum(r["contribution"] for r in rows)}


def _m09(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _borrowers(period["latest"], release_id).set_index("borrower_id")
    then = _borrowers(period["prior"], release_id).set_index("borrower_id")
    rows = []
    for borrower in sorted(set(now.index) | set(then.index)):
        current = float(now["ecl"].get(borrower, 0.0))
        prior = float(then["ecl"].get(borrower, 0.0))
        if current - prior > 0:
            rows.append({"borrower_id": borrower, "increase": current - prior,
                         "ecl": current})
    rows.sort(key=lambda r: (-r["increase"], r["borrower_id"]))
    return {"period": period["latest"], "comparison": period["prior"],
            "rows": rows, "row_count": len(rows),
            "total_increase": sum(r["increase"] for r in rows)}


def _ratings(quarter: str, release_id: str) -> pd.DataFrame:
    frame = _frame("cockpit_rating_ratio_quarter", release_id)
    return frame[frame["reporting_quarter"] == quarter][
        ["borrower_id", "risk_rating", "rating_rank"]]


def _m10(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now_r = _ratings(period["latest"], release_id).set_index("borrower_id")
    then_r = _ratings(period["prior"], release_id).set_index("borrower_id")
    now = _borrowers(period["latest"], release_id).set_index("borrower_id")
    then = _borrowers(period["prior"], release_id).set_index("borrower_id")
    rows = []
    for borrower in sorted(set(now_r.index) & set(then_r.index)):
        if borrower not in now.index or borrower not in then.index:
            continue
        downgraded = (float(now_r["rating_rank"][borrower])
                      > float(then_r["rating_rank"][borrower]))
        entered = (int(now["stage"][borrower]) >= 2
                   and int(then["stage"][borrower]) < 2)
        if downgraded and entered:
            rows.append({"borrower_id": borrower,
                         "ead": float(now["ead"][borrower]),
                         "ecl": float(now["ecl"][borrower])})
    return {"period": period["latest"], "comparison": period["prior"],
            "rows": rows, "row_count": len(rows), "empty": not rows,
            "total_ead": sum(r["ead"] for r in rows),
            "total_ecl": sum(r["ecl"] for r in rows)}


def _book(quarter: str, release_id: str) -> dict[str, float]:
    rows = _sectors(quarter, release_id)
    ead, ecl = float(rows["ead"].sum()), float(rows["ecl"].sum())
    stage2 = float(rows["stage2_ead"].sum())
    return {"ead": ead, "ecl": ecl, "coverage": ecl / ead,
            "stage2_share": stage2 / ead}


def _m11(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _book(period["latest"], release_id)
    then = _book(period["prior"], release_id)
    return {"period": period["latest"], "comparison": period["prior"],
            "kpis": now, "kpis_prior": then,
            "changes": {k: now[k] - then[k] for k in now},
            "row_count": 1}


def _m12(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    sector = "Construction"
    now = _sectors(period["latest"], release_id).set_index("sector_name")
    then = _sectors(period["year_ago"], release_id).set_index("sector_name")
    def block(frame):
        return {"ead": float(frame["ead"][sector]),
                "ecl": float(frame["ecl"][sector]),
                "stage2_share": float(frame["stage2_ead"][sector])
                / float(frame["ead"][sector])}
    current, prior = block(now), block(then)
    return {"period": period["latest"], "comparison": period["year_ago"],
            "sector": sector, "kpis": current, "kpis_prior": prior,
            "changes": {k: current[k] - prior[k] for k in current},
            "row_count": 1}


def _uncovered(quarter: str, release_id: str) -> pd.Series:
    slice_ = _at(quarter, release_id)
    total = slice_.groupby("sector_name", observed=True)["ead_reported"].sum()
    low = slice_[slice_["collateral_coverage_ratio"].notna()
                 & (slice_["collateral_coverage_ratio"] < 1)].groupby(
        "sector_name", observed=True)["ead_reported"].sum()
    return low.reindex(total.index).fillna(0.0) / total


def _m13(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    now = _sectors(period["latest"], release_id).set_index("sector_name")
    then = _sectors(period["prior"], release_id).set_index("sector_name")
    now_u = _uncovered(period["latest"], release_id)
    then_u = _uncovered(period["prior"], release_id)
    rows = []
    for sector in sorted(set(now.index) & set(then.index)):
        ecl_change = float(now["ecl"][sector]) - float(then["ecl"][sector])
        cover_change = float(now_u.get(sector, 0.0)) - float(
            then_u.get(sector, 0.0))
        if ecl_change > 0 and cover_change > 0:
            rows.append({"sector_name": sector, "ecl_change": ecl_change,
                         "uncovered_share_change": cover_change})
    rows.sort(key=lambda r: (-r["ecl_change"], r["sector_name"]))
    return {"period": period["latest"], "comparison": period["prior"],
            "rows": rows, "row_count": len(rows)}


def _m14(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    everyone = _borrowers(period["latest"], release_id)
    top = everyone.head(10)
    book = float(everyone["ecl"].sum())
    return {"period": period["latest"],
            "rows": [{"borrower_id": r.borrower_id, "ecl": float(r.ecl)}
                     for r in top.itertuples()],
            "row_count": int(len(top)), "book_ecl": book,
            "top10_ecl": float(top["ecl"].sum()),
            "top10_percent": 100.0 * float(top["ecl"].sum()) / book}


def _m15(release_id: str) -> dict[str, Any]:
    period = periods(release_id)
    borrowers = _borrowers(period["latest"], release_id)
    rows = []
    for sector, group in borrowers.groupby("sector_name", observed=True):
        sector_ecl = float(group["ecl"].sum())
        if sector_ecl <= 0:
            continue
        top3 = group.sort_values(["ecl", "borrower_id"],
                                 ascending=[False, True]).head(3)
        rows.append({"sector_name": str(sector),
                     "top3_ecl": float(top3["ecl"].sum()),
                     "sector_ecl": sector_ecl,
                     "share": float(top3["ecl"].sum()) / sector_ecl,
                     "borrowers": [r.borrower_id for r in top3.itertuples()]})
    rows.sort(key=lambda r: (-r["share"], r["sector_name"]))
    return {"period": period["latest"], "rows": rows, "row_count": len(rows)}


_ORACLES = {f"M{i:02d}": globals()[f"_m{i:02d}"] for i in range(1, 16)}


def oracle(question_id: str, release_id: str = "") -> dict[str, Any]:
    return _ORACLES[question_id](release_id or RELEASE)


__all__ = ["ALL", "BAR", "BY_ID", "NONE", "QUESTIONS", "RELEASE", "oracle",
           "periods", "quarters"]
