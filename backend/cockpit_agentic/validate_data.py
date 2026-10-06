"""
Integrity gates over a built release. Specification section 14.1.

Every check answers a question an analyst could get wrong if the data were
wrong, and each one names the trap it is guarding. A release that fails any of
these is not published: a demonstration that quietly double counts shared
collateral or presents an annual statement as four quarterly observations
teaches the wrong thing more effectively than no demonstration at all.

These are DATA checks. None of them is a runtime analytical template, and
nothing in the answer path calls this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backend.cockpit_agentic import DOMAIN, ORIGIN
from backend.cockpit_agentic import calendar as cal
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic.generate import SCENARIO_WEIGHT, Release

TOLERANCE = 1e-6


@dataclass(frozen=True)
class Check:
    check_id: str
    description: str
    passed: bool
    detail: str = ""
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "description": self.description,
                "passed": self.passed, "detail": self.detail,
                "observed": self.observed}


def _ok(check_id: str, description: str, observed: Any = None) -> Check:
    return Check(check_id, description, True, observed=observed)


def _fail(check_id: str, description: str, detail: str,
          observed: Any = None) -> Check:
    return Check(check_id, description, False, detail=detail, observed=observed)


# ---- calendar and coverage ------------------------------------------------

def check_twenty_slots(r: Release) -> list[Check]:
    frame = r.frames[F.CALENDAR]
    if len(r.calendar) != 20:
        return [_fail("calendar.twenty", "Exactly twenty reporting slots",
                      f"{len(r.calendar)} slots")]
    if len(frame) != 20:
        return [_fail("calendar.twenty", "Exactly twenty reporting slots",
                      f"the calendar relation has {len(frame)} rows")]
    return [_ok("calendar.twenty", "Exactly twenty reporting slots", 20)]


def check_populated_is_honest(r: Release) -> list[Check]:
    """Twenty slots created is not twenty quarters observed."""
    frame = r.frames[F.CALENDAR]
    claimed = set(frame.loc[frame["is_populated"] == True, "reporting_quarter"])
    facility = r.frames[F.FACILITY_QUARTER]
    actual = set(facility["reporting_quarter"].unique())
    if claimed != actual:
        return [_fail("calendar.populated_honest",
                      "A slot is marked populated only if it has rows",
                      f"claimed {sorted(claimed - actual)} with no rows; "
                      f"{sorted(actual - claimed)} have rows but are not "
                      f"marked")]
    empty = frame.loc[frame["is_populated"] == False]
    if len(empty) and empty["coverage_note"].fillna("").eq("").any():
        return [_fail("calendar.populated_honest",
                      "An empty slot says why", "a missing slot has no note")]
    return [_ok("calendar.populated_honest",
                "Populated slots match the rows that exist", len(actual))]


def check_no_quarter_outside_the_window(r: Release) -> list[Check]:
    """No relation may reach a twenty-first reporting quarter."""
    bad: dict[str, list[str]] = {}
    for name, frame in r.frames.items():
        if "reporting_quarter" not in frame.columns:
            continue
        outside = sorted(set(frame["reporting_quarter"].dropna().unique())
                         - set(r.calendar.slots))
        if outside:
            bad[name] = outside
    if bad:
        return [_fail("calendar.no_outside_quarter",
                      "No relation carries a quarter outside the twenty",
                      str(bad))]
    return [_ok("calendar.no_outside_quarter",
                "Every reporting_quarter is one of the twenty")]


# ---- the second time axis -------------------------------------------------

def check_macro_offsets(r: Release) -> list[Check]:
    frame = r.frames[F.MACRO_WINDOW]
    offsets = sorted(int(o) for o in frame["quarter_offset"].unique())
    if offsets != list(range(-4, 16)):
        return [_fail("macro.offsets", "Offsets run -4 through +15",
                      f"found {offsets}")]
    counts = frame.groupby(
        ["reporting_quarter", "factor_id", "country_or_region", "scenario_id"]
    ).size()
    if not (counts == 20).all():
        return [_fail("macro.offsets", "Twenty offsets per anchor, factor, "
                      "geography and scenario",
                      f"counts range {counts.min()}-{counts.max()}")]
    return [_ok("macro.offsets", "Ten factors x twenty offsets per anchor",
                int(len(frame)))]


def check_macro_window_does_not_add_reporting_quarters(r: Release) -> list[Check]:
    """The macro targets extend past the calendar; the calendar does not."""
    frame = r.frames[F.MACRO_WINDOW]
    targets = set(frame["macro_target_quarter"].unique())
    beyond = targets - set(r.calendar.slots)
    if not beyond:
        return [_fail("macro.second_axis",
                      "Macro targets extend beyond the reporting calendar",
                      "no target lies outside the twenty, so the window is "
                      "not a second axis at all")]
    facility_quarters = set(r.frames[F.FACILITY_QUARTER]["reporting_quarter"])
    leaked = facility_quarters & beyond
    if leaked:
        return [_fail("macro.second_axis",
                      "The macro horizon adds no facility reporting quarter",
                      f"{sorted(leaked)} appear as facility quarters")]
    return [_ok("macro.second_axis",
                "Macro targets extend beyond the calendar without extending it",
                len(beyond))]


def check_no_future_actuals(r: Release) -> list[Check]:
    """A forward value is a forecast. Never an observed future actual."""
    frame = r.frames[F.MACRO_WINDOW]
    bad = frame[(frame["quarter_offset"] > 0)
                & frame["observation_status"].isin(
                    ["historical_actual", "current_actual"])]
    if len(bad):
        return [_fail("macro.no_future_actual",
                      "No forward value is labelled an actual",
                      f"{len(bad)} forward rows claim actual status")]
    backward = frame[(frame["quarter_offset"] < 0)
                     & (frame["observation_status"] == "forecast")]
    if len(backward):
        return [_fail("macro.no_future_actual",
                      "No past value is labelled a forecast",
                      f"{len(backward)} past rows claim forecast status")]
    return [_ok("macro.no_future_actual",
                "Forward values are forecasts and past values are actuals")]


def check_macro_vintages(r: Release) -> list[Check]:
    """A later actual must not overwrite an earlier forecast.

    The same target quarter, seen from an earlier anchor as a forecast and a
    later one as an actual, must differ -- otherwise the forecast was written
    with hindsight.
    """
    frame = r.frames[F.MACRO_WINDOW]
    baseline = frame[frame["scenario_id"] == "baseline"]
    forecasts = baseline[baseline["quarter_offset"] > 0]
    actuals = baseline[baseline["quarter_offset"] <= 0][
        ["factor_id", "macro_target_quarter", "value"]].drop_duplicates(
        ["factor_id", "macro_target_quarter"])
    merged = forecasts.merge(actuals, on=["factor_id", "macro_target_quarter"],
                             suffixes=("_forecast", "_actual"))
    if merged.empty:
        return [_fail("macro.vintages",
                      "Forecasts are comparable with the actuals that arrived",
                      "no target is both forecast and later observed")]
    identical = merged[np.isclose(merged["value_forecast"],
                                  merged["value_actual"], atol=1e-9)]
    share = len(identical) / len(merged)
    if share > 0.02:
        return [_fail("macro.vintages",
                      "A forecast is not the actual that later arrived",
                      f"{share:.1%} of forecasts equal the realised value "
                      f"exactly, which means hindsight leaked into the vintage")]
    return [_ok("macro.vintages",
                "Forecasts differ from the actuals that later arrived",
                round(1 - share, 4))]


# ---- keys, grain and fan-out ----------------------------------------------

KEYS: dict[str, list[str]] = {
    F.FACILITY_QUARTER: ["reporting_quarter", "facility_id", "position_id"],
    F.BORROWER_FINANCIAL: ["reporting_quarter", "borrower_id",
                           "statement_scope"],
    F.RATING_RATIO: ["reporting_quarter", "borrower_id", "rating_basis"],
    F.QUALITATIVE: ["reporting_quarter", "borrower_id", "question_id"],
    F.COLLATERAL: ["reporting_quarter", "collateral_id"],
    F.COLLATERAL_ALLOCATION: ["reporting_quarter", "collateral_id",
                              "facility_id", "position_id"],
    F.COVENANT: ["reporting_quarter", "covenant_id", "test_version"],
    F.CALENDAR: ["reporting_quarter"],
}


def check_key_uniqueness(r: Release) -> list[Check]:
    out: list[Check] = []
    for relation, key in KEYS.items():
        frame = r.frames[relation]
        duplicates = int(frame.duplicated(subset=key).sum())
        if duplicates:
            out.append(_fail(f"grain.{relation}",
                             f"{relation} is unique on {key}",
                             f"{duplicates} duplicate rows"))
        else:
            out.append(_ok(f"grain.{relation}",
                           f"{relation} is unique on {key}", len(frame)))
    return out


def check_borrower_statement_not_multiplied(r: Release) -> list[Check]:
    """A borrower with four facilities has ONE balance sheet."""
    financial = r.frames[F.BORROWER_FINANCIAL]
    facility = r.frames[F.FACILITY_QUARTER]
    per = financial.groupby(["reporting_quarter", "borrower_id"]).size()
    if (per > 1).any():
        return [_fail("grain.borrower_statement",
                      "One statement per borrower-quarter",
                      f"{int((per > 1).sum())} borrower-quarters have more")]
    multi = facility.groupby(["reporting_quarter", "borrower_id"]).size()
    if not (multi > 1).any():
        return [_fail("grain.borrower_statement",
                      "The book contains borrowers with several facilities",
                      "no borrower has more than one facility, so the "
                      "repetition trap is not demonstrated")]
    joined = facility.merge(financial[["reporting_quarter", "borrower_id",
                                       "total_assets"]],
                            on=["reporting_quarter", "borrower_id"],
                            how="left")
    inflated = float(joined["total_assets"].sum())
    honest = float(financial["total_assets"].sum())
    if inflated <= honest * 1.05:
        return [_fail("grain.borrower_statement",
                      "The repetition is material enough to matter",
                      "joining statements to facilities barely changes the "
                      "total, so the trap would not be visible")]
    return [_ok("grain.borrower_statement",
                "One statement per borrower-quarter, and joining it to "
                "facilities visibly inflates it",
                {"honest_total_assets": round(honest, 2),
                 "inflated_by_facility_join": round(inflated, 2)})]


def check_shared_collateral_is_counted_once(r: Release) -> list[Check]:
    assets = r.frames[F.COLLATERAL]
    allocation = r.frames[F.COLLATERAL_ALLOCATION]
    shared = allocation.groupby(["reporting_quarter", "collateral_id"]).size()
    if not (shared > 1).any():
        return [_fail("grain.shared_collateral",
                      "Some assets secure more than one facility",
                      "no shared collateral exists, so the double-count trap "
                      "is not demonstrated")]
    quarter = r.calendar.latest_populated()
    asset_total = float(assets.loc[assets["reporting_quarter"] == quarter,
                                   "gross_market_value_rcy"].sum(min_count=0))
    allocated = float(allocation.loc[
        allocation["reporting_quarter"] == quarter,
        "allocated_gross_value_rcy"].sum())
    naive = float(allocation.loc[allocation["reporting_quarter"] == quarter]
                  .merge(assets.loc[assets["reporting_quarter"] == quarter,
                                    ["collateral_id", "gross_market_value_rcy"]],
                         on="collateral_id")["gross_market_value_rcy"].sum())
    if allocated > asset_total * 1.0001:
        return [_fail("grain.shared_collateral",
                      "Allocated value never exceeds the assets it comes from",
                      f"allocated {allocated:.2f} > assets {asset_total:.2f}")]
    if naive <= allocated * 1.02:
        return [_fail("grain.shared_collateral",
                      "The naive join visibly double counts",
                      "summing whole-asset value across allocations barely "
                      "differs from the allocated sum")]
    return [_ok("grain.shared_collateral",
                "Allocated value sums to the asset value; the naive join "
                "double counts",
                {"asset_grain_total": round(asset_total, 2),
                 "allocated_total": round(allocated, 2),
                 "naive_join_total": round(naive, 2)})]


def check_allocation_shares(r: Release) -> list[Check]:
    allocation = r.frames[F.COLLATERAL_ALLOCATION]
    shares = allocation.groupby(
        ["reporting_quarter", "collateral_id"])["allocation_share"].sum()
    over = shares[shares > 1.0 + TOLERANCE]
    if len(over):
        return [_fail("collateral.shares",
                      "Allocation shares for one asset sum to at most one",
                      f"{len(over)} assets exceed it")]
    return [_ok("collateral.shares",
                "Allocation shares sum to at most one per asset")]


def check_no_double_haircut(r: Release) -> list[Check]:
    """A net value never receives a haircut twice."""
    assets = r.frames[F.COLLATERAL]
    valued = assets[assets["valuation_status"] == "valued"].copy()
    expected = (valued["eligible_value_before_haircut"]
                - valued["haircut_amount"])
    residual = (valued["net_realizable_value"] - expected).abs()
    worst = float(residual.max()) if len(residual) else 0.0
    if worst > TOLERANCE * max(1.0, float(valued["net_realizable_value"].abs().max())):
        return [_fail("collateral.single_haircut",
                      "Net value is the eligible value less the haircut, once",
                      f"largest residual {worst:.6f}")]
    return [_ok("collateral.single_haircut",
                "Net value equals eligible less haircut, applied once")]


def check_haircut_components_are_not_blindly_summed(r: Release) -> list[Check]:
    """The source total is not the sum of overlapping components."""
    assets = r.frames[F.COLLATERAL]
    valued = assets[assets["valuation_status"] == "valued"]
    if valued.empty:
        return [_fail("collateral.haircut_combination", "Haircuts exist",
                      "no valued asset")]
    naive = (valued["market_haircut"] + valued["liquidity_haircut"]
             + valued["fx_haircut"] + valued["legal_haircut"])
    same = np.isclose(naive, valued["total_haircut"], atol=1e-9).mean()
    if same > 0.02:
        return [_fail("collateral.haircut_combination",
                      "The total haircut is not the naive sum of components",
                      f"{same:.1%} of assets have total == sum, which would "
                      f"teach that summing overlapping haircuts is correct")]
    method = set(valued["haircut_combination_method"].unique())
    if method - {"multiplicative", "additive", "maximum", "source_supplied",
                 "unknown"}:
        return [_fail("collateral.haircut_combination",
                      "The combination method is declared", str(method))]
    return [_ok("collateral.haircut_combination",
                "The total haircut differs from the naive component sum, and "
                "the method is declared", sorted(method))]


# ---- IFRS 9 and PD semantics ----------------------------------------------

def check_probability_bounds(r: Release) -> list[Check]:
    out: list[Check] = []
    checks = [
        (F.FACILITY_QUARTER, ["pd_pit_12m", "pd_pit_lifetime", "pd_ttc_12m",
                              "pd_ttc_lifetime", "lgd_pit", "lgd_ttc",
                              "ccf_pit"]),
        (F.IFRS9_DETAIL, ["scenario_pd_pit_12m", "scenario_pd_pit_lifetime",
                          "scenario_lgd", "scenario_weight",
                          "term_pd_marginal", "term_pd_cumulative",
                          "term_survival"]),
    ]
    for relation, columns in checks:
        frame = r.frames[relation]
        for column in columns:
            series = pd.to_numeric(frame[column], errors="coerce").dropna()
            if series.empty:
                continue
            if series.lt(0).any() or series.gt(1).any():
                out.append(_fail(f"prob.{relation}.{column}",
                                 f"{column} lies in [0, 1]",
                                 f"range {series.min():.4f}-{series.max():.4f}"))
            else:
                out.append(_ok(f"prob.{relation}.{column}",
                               f"{column} lies in [0, 1]"))
    return out


def check_lifetime_is_not_annual_times_years(r: Release) -> list[Check]:
    """The distinction section 4.2 insists on, asserted against the data."""
    frame = r.frames[F.FACILITY_QUARTER]
    live = frame[(frame["pd_lifetime_horizon_months"] > 15)
                 & frame["pd_pit_lifetime"].notna()].copy()
    if live.empty:
        return [_fail("pd.lifetime_semantics",
                      "Multi-year positions exist", "none found")]
    years = np.ceil(live["pd_lifetime_horizon_months"] / 12.0)
    naive = (live["pd_pit_12m"] * years).clip(upper=1.0)
    close = np.isclose(live["pd_pit_lifetime"], naive, rtol=0.02).mean()
    if close > 0.05:
        return [_fail("pd.lifetime_semantics",
                      "Lifetime PD is a survival curve, not the annual PD "
                      "times the number of years",
                      f"{close:.1%} of positions match the naive product")]
    # ...and it must still exceed the twelve-month figure.
    if (live["pd_pit_lifetime"] < live["pd_pit_12m"] - TOLERANCE).any():
        return [_fail("pd.lifetime_semantics",
                      "Cumulative lifetime PD is at least the 12-month PD",
                      "some lifetime PD is lower")]
    return [_ok("pd.lifetime_semantics",
                "Lifetime PD is a cumulative survival measure, distinct from "
                "annual PD times years", round(1 - close, 4))]


def check_pit_and_ttc_differ(r: Release) -> list[Check]:
    """TTC is not a substitute for PIT: it does not see the cycle."""
    frame = r.frames[F.FACILITY_QUARTER]
    same = np.isclose(frame["pd_pit_12m"], frame["pd_ttc_12m"],
                      rtol=1e-4).mean()
    if same > 0.05:
        return [_fail("pd.pit_vs_ttc", "PIT and TTC PD are distinct series",
                      f"{same:.1%} of rows have them equal")]
    # And the difference must track the cycle rather than being noise.
    by_quarter = frame.groupby("reporting_quarter").apply(
        lambda g: float((g["pd_pit_12m"] - g["pd_ttc_12m"]).mean()),
        include_groups=False)
    if by_quarter.std() < 1e-5:
        return [_fail("pd.pit_vs_ttc",
                      "The PIT-TTC gap moves with the cycle",
                      "the gap is constant across quarters, so PIT is not "
                      "point-in-time in any meaningful sense")]
    return [_ok("pd.pit_vs_ttc",
                "PIT and TTC differ, and their gap moves across quarters",
                round(float(by_quarter.std()), 6))]


def check_curve_identities(r: Release) -> list[Check]:
    """Survival, marginal and cumulative PD are mutually consistent."""
    frame = r.frames[F.IFRS9_DETAIL].sort_values(
        ["facility_id", "position_id", "reporting_quarter", "scenario_id",
         "term_horizon_index"])
    worst = 0.0
    for _key, group in frame.groupby(
            ["facility_id", "position_id", "reporting_quarter", "scenario_id"],
            sort=False):
        marginal = group["term_pd_marginal"].to_numpy(dtype=float)
        survival = group["term_survival"].to_numpy(dtype=float)
        cumulative = group["term_pd_cumulative"].to_numpy(dtype=float)
        expected_survival = np.concatenate(
            [[1.0], np.cumprod(1.0 - marginal)[:-1]])
        expected_cumulative = 1.0 - np.cumprod(1.0 - marginal)
        worst = max(worst,
                    float(np.abs(survival - expected_survival).max()),
                    float(np.abs(cumulative - expected_cumulative).max()))
        if worst > 1e-9:
            break
    if worst > 1e-9:
        return [_fail("ifrs9.curve_identities",
                      "Survival and cumulative PD follow from the marginals",
                      f"largest deviation {worst:.3e}")]
    return [_ok("ifrs9.curve_identities",
                "Survival and cumulative PD reconcile with the marginals")]


def check_ecl_reconciles_to_its_horizons(r: Release) -> list[Check]:
    """A scenario ECL is the sum of its discounted horizon shortfalls."""
    detail = r.frames[F.IFRS9_DETAIL]
    summed = detail.groupby(
        ["facility_id", "position_id", "reporting_quarter", "scenario_id"]
    ).agg(shortfall=("term_expected_shortfall", "sum"),
          stated=("scenario_ecl", "first")).reset_index()
    residual = (summed["shortfall"] - summed["stated"]).abs()
    scale = summed["stated"].abs().clip(lower=1.0)
    worst = float((residual / scale).max())
    if worst > 1e-6:
        return [_fail("ifrs9.ecl_reconciles",
                      "Scenario ECL equals the sum of its horizon shortfalls",
                      f"largest relative residual {worst:.3e}")]
    return [_ok("ifrs9.ecl_reconciles",
                "Scenario ECL reconciles to its horizon shortfalls",
                int(len(summed)))]


def check_scenario_weights(r: Release) -> list[Check]:
    detail = r.frames[F.IFRS9_DETAIL]
    weights = detail.drop_duplicates(
        ["facility_id", "position_id", "reporting_quarter", "scenario_id"]
    ).groupby(["facility_id", "position_id",
               "reporting_quarter"])["scenario_weight"].sum()
    off = weights[(weights - 1.0).abs() > TOLERANCE]
    if len(off):
        return [_fail("ifrs9.weights", "Scenario weights sum to one",
                      f"{len(off)} positions do not")]
    return [_ok("ifrs9.weights", "Scenario weights sum to one",
                dict(SCENARIO_WEIGHT))]


def check_booked_ecl_is_not_a_scenario_sum(r: Release) -> list[Check]:
    """Summing scenario ECL is not the booked figure."""
    facility = r.frames[F.FACILITY_QUARTER]
    detail = r.frames[F.IFRS9_DETAIL]
    per = detail.drop_duplicates(
        ["facility_id", "position_id", "reporting_quarter", "scenario_id"])
    naive = per.groupby(["facility_id", "position_id",
                         "reporting_quarter"])["scenario_ecl"].sum()
    booked = facility.set_index(
        ["facility_id", "position_id", "reporting_quarter"])["ecl_reported"]
    joined = pd.concat([naive.rename("naive"), booked.rename("booked")],
                       axis=1).dropna()
    if joined.empty:
        return [_fail("ifrs9.scenario_sum", "Scenario detail joins to the book",
                      "no overlap")]
    close = np.isclose(joined["naive"], joined["booked"], rtol=0.01).mean()
    if close > 0.05:
        return [_fail("ifrs9.scenario_sum",
                      "The booked ECL is weighted, not the sum of scenarios",
                      f"{close:.1%} of positions have them equal")]
    return [_ok("ifrs9.scenario_sum",
                "Summing scenario ECL overstates the booked figure, as it "
                "should", round(float((joined['naive'] / joined['booked']).median()), 3))]


def check_a_single_pd_lgd_ead_product_does_not_reproduce_ecl(r: Release
                                                             ) -> list[Check]:
    """Section 4.2: the runtime must not force PD x LGD x EAD as THE
    reconstruction of every reported ECL. The data has to make that true."""
    frame = r.frames[F.FACILITY_QUARTER]
    live = frame[frame["ecl_reported"] > 0]
    if live.empty:
        return [_fail("ifrs9.no_forced_formula", "Positions carry ECL",
                      "none")]
    product = live["pd_pit_12m"] * live["lgd_pit"] * live["ead_reported"]
    close = np.isclose(product, live["ecl_reported"], rtol=0.05).mean()
    if close > 0.10:
        return [_fail("ifrs9.no_forced_formula",
                      "The reported ECL is a term-structure measurement, not a "
                      "single-period product",
                      f"{close:.1%} of positions are reproduced by "
                      f"PD x LGD x EAD, which would teach that the shortcut is "
                      f"the answer")]
    return [_ok("ifrs9.no_forced_formula",
                "PD x LGD x EAD does not reproduce the reported ECL",
                round(1 - close, 4))]


def check_stage_and_default_are_separate_from_the_rating(r: Release
                                                         ) -> list[Check]:
    facility = r.frames[F.FACILITY_QUARTER]
    rating = r.frames[F.RATING_RATIO]
    joined = facility.merge(rating[["reporting_quarter", "borrower_id",
                                    "risk_rating", "rating_rank"]],
                            on=["reporting_quarter", "borrower_id"], how="left")
    c_grade = joined[joined["risk_rating"] == "C"]
    if len(c_grade) and bool(c_grade["default_flag"].all()):
        return [_fail("rating.default_separate",
                      "Grade C is not mechanically default",
                      "every C-rated position is flagged defaulted")]
    defaulted = joined[joined["default_flag"] == True]
    if len(defaulted) and set(defaulted["risk_rating"].dropna()) == {"C"}:
        return [_fail("rating.default_separate",
                      "Default is not confined to grade C",
                      "only C-rated positions ever default")]
    return [_ok("rating.default_separate",
                "default_flag and grade C are distinct",
                {"c_rated": int(len(c_grade)),
                 "defaulted": int(len(defaulted))})]


# ---- ratings, ratios, statements ------------------------------------------

def check_nineteen_grades(r: Release) -> list[Check]:
    rating = r.frames[F.RATING_RATIO]
    present = set(rating["risk_rating"].dropna().unique())
    unknown = present - set(F.RATING_SCALE)
    if unknown:
        return [_fail("rating.scale", "Ratings come from the nineteen grades",
                      f"unknown grades {sorted(unknown)}")]
    ranks = rating.dropna(subset=["risk_rating"])
    mismatched = ranks[ranks["rating_rank"]
                       != ranks["risk_rating"].map(F.RATING_RANK)]
    if len(mismatched):
        return [_fail("rating.scale", "rating_rank matches risk_rating",
                      f"{len(mismatched)} rows disagree")]
    coverage = len(present) / 19.0
    return [_ok("rating.scale",
                "Ratings are on the nineteen-grade scale and the rank agrees",
                {"grades_used": len(present), "coverage": round(coverage, 2)})]


def check_balance_sheet_adds_up(r: Release) -> list[Check]:
    frame = r.frames[F.BORROWER_FINANCIAL]
    identities = [
        ("assets = liabilities + equity", frame["total_assets"],
         frame["total_liabilities"] + frame["shareholders_equity"]),
        ("current + non-current = total assets",
         frame["total_assets"],
         frame["current_assets"] + frame["noncurrent_assets"]),
        ("current + non-current = total liabilities",
         frame["total_liabilities"],
         frame["current_liabilities"] + frame["noncurrent_liabilities"]),
        ("gross profit = revenue - cost of sales", frame["gross_profit"],
         frame["revenue"] - frame["cost_of_goods_sold"]),
        ("working capital = current assets - current liabilities",
         frame["working_capital"],
         frame["current_assets"] - frame["current_liabilities"]),
    ]
    out: list[Check] = []
    for name, left, right in identities:
        residual = (left - right).abs()
        scale = left.abs().clip(lower=1.0)
        worst = float((residual / scale).max())
        if worst > 1e-9:
            out.append(_fail(f"statement.{name}", name,
                             f"largest relative residual {worst:.3e}"))
        else:
            out.append(_ok(f"statement.{name}", name))
    return out


def check_carried_forward_is_not_a_new_observation(r: Release) -> list[Check]:
    """Section 3.2: a carried-forward annual statement is not a newly observed
    quarterly one."""
    frame = r.frames[F.BORROWER_FINANCIAL]
    carried = frame[frame["value_origin"] == "carried_forward"]
    if carried.empty:
        return [_fail("statement.carried_forward",
                      "Some statements are carried forward",
                      "every statement is a fresh observation, which no real "
                      "book looks like")]
    if (carried["observation_age_days"] <= 95).all():
        return [_fail("statement.carried_forward",
                      "A carried-forward statement is visibly stale",
                      "no carried-forward statement is older than a quarter")]
    annual = frame[frame["statement_period_basis"] == "annual"]
    if len(annual) and (annual["statement_period_days"] != 365).any():
        return [_fail("statement.carried_forward",
                      "An annual statement declares a 365-day period",
                      "some annual statements claim a quarterly period")]
    return [_ok("statement.carried_forward",
                "Carried-forward statements are labelled and visibly aged",
                {"carried_forward_rows": int(len(carried)),
                 "max_age_days": int(carried["observation_age_days"].max())})]


def check_ratio_arithmetic(r: Release) -> list[Check]:
    """A published ratio agrees with the statement it came from."""
    rating = r.frames[F.RATING_RATIO]
    financial = r.frames[F.BORROWER_FINANCIAL]
    joined = rating.merge(financial, on=["reporting_quarter", "borrower_id"],
                          suffixes=("", "_fs"))
    out: list[Check] = []
    cases = [
        ("current_ratio", joined["current_assets"], joined["current_liabilities"]),
        ("debt_to_equity", joined["total_debt"], joined["shareholders_equity"]),
        ("ebitda_margin", joined["ebitda"], joined["revenue"]),
        ("dscr", joined["cash_available_for_debt_service"],
         joined["debt_service_due"]),
        ("liabilities_to_assets", joined["total_liabilities"],
         joined["total_assets"]),
    ]
    for name, numerator, denominator in cases:
        usable = joined[(joined[f"{name}_status"] == "derived")
                        & denominator.ne(0)]
        if usable.empty:
            out.append(_fail(f"ratio.{name}", f"{name} is checkable",
                             "no derived rows"))
            continue
        expected = (numerator / denominator)[usable.index]
        residual = (usable[name] - expected).abs()
        scale = expected.abs().clip(lower=1e-6)
        worst = float((residual / scale).max())
        if worst > 1e-9:
            out.append(_fail(f"ratio.{name}",
                             f"{name} agrees with its statement inputs",
                             f"largest relative residual {worst:.3e}"))
        else:
            out.append(_ok(f"ratio.{name}",
                           f"{name} agrees with its statement inputs"))
    return out


def check_zero_denominator_is_flagged_not_infinite(r: Release) -> list[Check]:
    rating = r.frames[F.RATING_RATIO]
    out: list[Check] = []
    for name in F.RATIO_NAMES:
        series = pd.to_numeric(rating[name], errors="coerce")
        if np.isinf(series.to_numpy(dtype=float, na_value=np.nan)).any():
            out.append(_fail("ratio.no_infinity",
                             "No ratio is infinite", f"{name} contains inf"))
            return out
    flagged = rating[[f"{n}_status" for n in F.RATIO_NAMES]].isin(
        ["invalid_denominator", "unavailable"]).any().any()
    out.append(_ok("ratio.no_infinity",
                   "No ratio is infinite; unusable ones carry a status",
                   {"any_flagged": bool(flagged)}))
    return out


def check_average_denominators_need_a_true_opening_balance(r: Release
                                                           ) -> list[Check]:
    """Section 4.6: "average" means genuine opening and closing balances, not
    an invented prior quarter."""
    rating = r.frames[F.RATING_RATIO]
    first = r.calendar.slots[0]
    firsts = rating[rating["reporting_quarter"] == first]
    if firsts.empty:
        return [_ok("ratio.opening_balance",
                    "The first slot is unpopulated, so nothing to check")]
    unavailable = (firsts["return_on_assets_status"] == "unavailable").mean()
    if unavailable < 0.99:
        return [_fail("ratio.opening_balance",
                      "An average-denominator ratio is unavailable in the "
                      "first quarter, where there is no true opening balance",
                      f"only {unavailable:.1%} are unavailable, so a prior "
                      f"quarter was invented")]
    return [_ok("ratio.opening_balance",
                "Average-denominator ratios are unavailable in the first slot "
                "rather than computed from an invented opening balance")]


# ---- covenants and qualitative --------------------------------------------

def check_covenant_headroom_direction(r: Release) -> list[Check]:
    frame = r.frames[F.COVENANT]
    tested = frame[frame["test_status"].isin(["compliant", "breached"])
                   & frame["headroom_value"].notna()].copy()
    if tested.empty:
        return [_fail("covenant.headroom", "Covenants are tested", "none")]
    wrong = tested[((tested["test_status"] == "compliant")
                    & (tested["headroom_value"] < -TOLERANCE))
                   | ((tested["test_status"] == "breached")
                      & (tested["headroom_value"] >= 0))]
    if len(wrong):
        return [_fail("covenant.headroom",
                      "Positive headroom means compliant, under the actual "
                      "comparator", f"{len(wrong)} rows disagree")]
    ge = tested[tested["comparison_operator"] == ">="]
    le = tested[tested["comparison_operator"] == "<="]
    for label, subset, sign in (("greater-or-equal", ge, +1),
                                ("less-or-equal", le, -1)):
        if subset.empty:
            continue
        expected = sign * (subset["observed_value"]
                           - subset["threshold_value"])
        if not np.allclose(subset["headroom_value"], expected, atol=1e-9):
            return [_fail("covenant.headroom",
                          f"Headroom follows the {label} comparator",
                          "the sign convention is wrong")]
    return [_ok("covenant.headroom",
                "Headroom follows the contractual comparator, and its sign "
                "matches the status", int(len(tested)))]


def check_untested_is_not_compliant(r: Release) -> list[Check]:
    frame = r.frames[F.COVENANT]
    untested = frame[frame["test_status"].isin(
        ["not_tested", "overdue", "unavailable"])]
    if untested.empty:
        return [_fail("covenant.untested",
                      "Some covenants are not tested this quarter",
                      "every covenant is tested every quarter, which no real "
                      "book looks like")]
    if untested["observed_value"].notna().any():
        return [_fail("covenant.untested",
                      "An untested covenant carries no observation",
                      "some untested rows carry a value")]
    if untested["test_missing_reason"].fillna("").eq("").any():
        return [_fail("covenant.untested", "An untested covenant says why",
                      "some untested rows give no reason")]
    return [_ok("covenant.untested",
                "Untested covenants carry no observation and say why",
                int(len(untested)))]


def check_borrower_wide_covenants_exist(r: Release) -> list[Check]:
    frame = r.frames[F.COVENANT]
    scopes = set(frame["binding_scope"].unique())
    if scopes != {"borrower", "facility"}:
        return [_fail("covenant.scope",
                      "Both borrower-wide and facility-specific covenants "
                      "exist", f"found {sorted(scopes)}")]
    borrower_wide = frame[frame["binding_scope"] == "borrower"]
    if borrower_wide["facility_id"].notna().any():
        return [_fail("covenant.scope",
                      "A borrower-wide covenant carries no facility",
                      "some do")]
    return [_ok("covenant.scope",
                "Borrower-wide and facility-specific covenants are "
                "distinguished", {"borrower_wide": int(len(borrower_wide))})]


def check_breaches_and_waivers_exist(r: Release) -> list[Check]:
    frame = r.frames[F.COVENANT]
    statuses = frame["test_status"].value_counts().to_dict()
    for needed in ("compliant", "breached", "waived"):
        if statuses.get(needed, 0) < 1:
            return [_fail("covenant.outcomes",
                          "The book contains breaches and waivers",
                          f"no covenant is {needed}")]
    return [_ok("covenant.outcomes",
                "Compliant, breached and waived outcomes all occur",
                {k: int(v) for k, v in statuses.items()})]


def check_twenty_questions_per_borrower_quarter(r: Release) -> list[Check]:
    frame = r.frames[F.QUALITATIVE]
    per = frame.groupby(["reporting_quarter", "borrower_id"]).size()
    if not (per == 20).all():
        return [_fail("qualitative.twenty",
                      "Twenty question rows per borrower-quarter",
                      f"counts range {per.min()}-{per.max()}")]
    unanswered = frame[frame["answer_status"] == "not_answered"]
    if unanswered.empty:
        return [_fail("qualitative.twenty",
                      "Some questions are unanswered",
                      "every question is answered everywhere, which no real "
                      "assessment looks like")]
    if unanswered["answer_value"].notna().any():
        return [_fail("qualitative.twenty",
                      "An unanswered question carries no value", "some do")]
    return [_ok("qualitative.twenty",
                "Twenty questions per borrower-quarter, some unanswered",
                {"rows": int(len(frame)),
                 "unanswered": int(len(unanswered))})]


# ---- labelling ------------------------------------------------------------

def check_synthetic_labelling(r: Release) -> list[Check]:
    out: list[Check] = []
    for name, frame in r.frames.items():
        if "domain_id" not in frame.columns:
            continue
        domains = set(frame["domain_id"].dropna().unique())
        if domains != {DOMAIN}:
            out.append(_fail(f"label.{name}",
                             f"{name} carries only the {DOMAIN} domain",
                             f"found {sorted(domains)}"))
        else:
            out.append(_ok(f"label.{name}", f"{name} is {DOMAIN} only"))
        origins = set(frame["value_origin"].dropna().unique())
        unknown = origins - set(
            ("actual", "source_forecast", "derived", "carried_forward",
             "synthetic_demo"))
        if unknown:
            out.append(_fail(f"origin.{name}",
                             f"{name} declares a known value_origin",
                             f"found {sorted(unknown)}"))
    out.append(_ok("label.origin", "The release is labelled synthetic", ORIGIN))
    return out


CHECKS = (
    check_twenty_slots, check_populated_is_honest,
    check_no_quarter_outside_the_window,
    check_macro_offsets, check_macro_window_does_not_add_reporting_quarters,
    check_no_future_actuals, check_macro_vintages,
    check_key_uniqueness, check_borrower_statement_not_multiplied,
    check_shared_collateral_is_counted_once, check_allocation_shares,
    check_no_double_haircut, check_haircut_components_are_not_blindly_summed,
    check_probability_bounds, check_lifetime_is_not_annual_times_years,
    check_pit_and_ttc_differ, check_curve_identities,
    check_ecl_reconciles_to_its_horizons, check_scenario_weights,
    check_booked_ecl_is_not_a_scenario_sum,
    check_a_single_pd_lgd_ead_product_does_not_reproduce_ecl,
    check_stage_and_default_are_separate_from_the_rating,
    check_nineteen_grades, check_balance_sheet_adds_up,
    check_carried_forward_is_not_a_new_observation, check_ratio_arithmetic,
    check_zero_denominator_is_flagged_not_infinite,
    check_average_denominators_need_a_true_opening_balance,
    check_covenant_headroom_direction, check_untested_is_not_compliant,
    check_borrower_wide_covenants_exist, check_breaches_and_waivers_exist,
    check_twenty_questions_per_borrower_quarter, check_synthetic_labelling,
)


def validate(release: Release) -> dict[str, Any]:
    checks: list[Check] = []
    for check in CHECKS:
        try:
            checks.extend(check(release))
        except Exception as e:                       # noqa: BLE001
            checks.append(_fail(check.__name__, check.__doc__ or check.__name__,
                                f"the check itself failed: {e!r}"))
    failed = [c for c in checks if not c.passed]
    return {
        "dataset_release_id": release.dataset_release_id,
        "passed": not failed,
        "checks_run": len(checks),
        "checks_failed": len(failed),
        "failures": [c.to_dict() for c in failed],
        "results": [c.to_dict() for c in checks],
    }


__all__ = ["CHECKS", "Check", "validate"]
