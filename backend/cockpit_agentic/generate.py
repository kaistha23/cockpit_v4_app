"""
The twenty-quarter synthetic demonstration release. Specification section 5.

Deterministic from one master seed. Nothing here describes a real borrower, a
real bank's book or a real economy, and no parameter in it is calibrated,
validated or approved for any purpose.

Coherent, not independently random
----------------------------------
Section 5 asks for known numerical fixtures and coherent relationships rather
than independent random columns, and the difference matters: a book whose
ratings, PDs, statements, covenants and collateral move independently cannot
support a genuine analytical question, and an analysis of it would look
plausible while meaning nothing. So one latent quality process per borrower
drives everything downstream:

    macro cycle + borrower drift  ->  latent quality
    latent quality               ->  rating, and the PIT PD around it
    rating alone                 ->  the TTC PD (through the cycle: no macro)
    hazard curve over the actual remaining maturity -> lifetime PD
    term structure x LGD x EAD x discount, per scenario, weighted -> ECL
    the same quality process      ->  statement trajectory -> the forty ratios
    those ratios vs contractual thresholds -> covenant status and headroom

That last chain is what makes a covenant breach in this data mean something:
it happens because the borrower's DSCR actually fell, not because a breach
flag was drawn from a distribution.

What is deliberately imperfect
------------------------------
Real books have gaps, and a demo without them teaches the wrong lesson. This
release carries missing valuations, carried-forward annual statements that are
not new quarterly observations, unanswered qualitative questions, covenants
not yet due, facilities originating and closing part-way through the window,
collateral shared across facilities, and macro forecasts whose vintages
disagree with the actuals that later arrived.

This module is not on the answer path. It writes Parquet; the runtime reads
it. No model calls it and it computes nothing at query time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Sequence

import numpy as np
import pandas as pd

from backend.cockpit_agentic import DATA_VERSION, DOMAIN, NOT_CLIENT_DATA, ORIGIN
from backend.cockpit_agentic import calendar as cal
from backend.cockpit_agentic import fields as F

MASTER_SEED = 20260908

REPORTING_CURRENCY = "INR"
AMOUNT_SCALE = "crore"
GEOGRAPHY = "IN"

#: Section 5's target scale, configurable for a development machine.
DEFAULT_BORROWERS = 250
DEFAULT_FACILITIES = 600

TENANT = "demo-tenant"

SECTORS: tuple[tuple[str, str], ...] = (
    ("C25", "Manufacturing"), ("F41", "Construction"),
    ("G46", "Wholesale Trade"), ("L68", "Real Estate"),
    ("H49", "Transport and Logistics"), ("D35", "Power and Utilities"),
    ("J62", "Information Technology"), ("I55", "Hospitality"),
    ("A01", "Agriculture and Agri-processing"), ("C20", "Chemicals"),
    ("B07", "Metals and Mining"), ("G47", "Retail Trade"),
)

PRODUCTS: tuple[str, ...] = ("term_loan", "working_capital", "revolving_credit",
                             "project_finance", "trade_finance")
PORTFOLIOS: tuple[str, ...] = ("large_corporate", "mid_corporate", "sme")

_NAME_HEAD = ("Aravali", "Bhavani", "Chandrika", "Deccan", "Eastwind",
              "Falcon", "Ganga", "Himalaya", "Indus", "Jamuna", "Kaveri",
              "Lotus", "Meghna", "Narmada", "Orion", "Pallava", "Quartz",
              "Ratna", "Sahyadri", "Tapti", "Ujjain", "Vindhya", "Wardha",
              "Yamuna", "Zenith", "Konkan", "Malabar", "Nilgiri")
_NAME_TAIL = ("Industries", "Enterprises", "Infratech", "Logistics", "Retail",
              "Chemicals", "Steel", "Power", "Textiles", "Foods", "Systems",
              "Estates", "Minerals", "Agro", "Motors", "Cements")


def _rng(*parts: Any) -> np.random.Generator:
    """A generator whose stream depends only on its labels.

    Naming the stream rather than sharing one sequence is what makes the data
    reproducible under change: adding a borrower does not shift every other
    borrower's numbers, so a numerical fixture written today still holds
    tomorrow.
    """
    blob = "|".join(str(p) for p in (MASTER_SEED, *parts)).encode()
    seed = int.from_bytes(hashlib.blake2b(blob, digest_size=8).digest(), "big")
    return np.random.default_rng(seed)


def _pick(rng: np.random.Generator, options: Sequence[Any]) -> Any:
    return options[int(rng.integers(0, len(options)))]


# --------------------------------------------------------------- population

@dataclass
class Borrower:
    borrower_id: str
    name: str
    group_id: str
    sector_code: str
    sector_name: str
    country_code: str
    portfolio_id: str
    #: Latent credit quality at the first slot, on a standard normal scale.
    #: Higher is stronger. Never published: it is the generator's cause, and
    #: publishing it would let an analysis "explain" the book by reading the
    #: answer key.
    quality0: float
    #: How strongly this borrower's fortunes track the macro cycle.
    macro_beta: float
    #: Idiosyncratic drift per quarter.
    drift: float
    scale: float
    statement_frequency: str      # quarterly | semiannual | annual
    fiscal_year_end_month: int


@dataclass
class Facility:
    facility_id: str
    position_id: str
    borrower_id: str
    product_type: str
    origination_index: int
    maturity_index: int
    limit: float
    utilisation: float
    lgd_base: float


@dataclass
class Asset:
    collateral_id: str
    borrower_id: str
    collateral_type: str
    base_value: float
    currency: str
    #: Facilities this asset secures. More than one is the shared-collateral
    #: case the join warnings exist for.
    secures: list[str] = field(default_factory=list)
    valuation_gap_quarters: tuple[int, ...] = ()


def build_population(*, borrowers: int, facilities: int, slots: int
                     ) -> tuple[list[Borrower], list[Facility], list[Asset]]:
    rng = _rng("population", borrowers, facilities)
    out_b: list[Borrower] = []
    for i in range(borrowers):
        r = _rng("borrower", i)
        code, name = _pick(r, SECTORS)
        out_b.append(Borrower(
            borrower_id=f"BRW{i + 1:04d}",
            name=f"{_pick(r, _NAME_HEAD)} {_pick(r, _NAME_TAIL)}",
            # Roughly one borrower in six belongs to a group.
            group_id=(f"GRP{int(r.integers(1, 40)):03d}"
                      if r.random() < 0.17 else ""),
            sector_code=code, sector_name=name, country_code=GEOGRAPHY,
            portfolio_id=_pick(r, PORTFOLIOS),
            quality0=float(r.normal(0.15, 0.95)),
            macro_beta=float(np.clip(r.normal(0.55, 0.30), 0.05, 1.30)),
            drift=float(r.normal(-0.012, 0.055)),
            scale=float(np.exp(r.normal(6.0, 0.85))),
            statement_frequency=str(r.choice(
                ["quarterly", "semiannual", "annual"], p=[0.45, 0.30, 0.25])),
            fiscal_year_end_month=int(r.choice([3, 12], p=[0.75, 0.25]))))

    out_f: list[Facility] = []
    n = 0
    while len(out_f) < facilities:
        b = out_b[n % len(out_b)]
        k = len([f for f in out_f if f.borrower_id == b.borrower_id])
        r = _rng("facility", b.borrower_id, k)
        # Most facilities span the window; some originate inside it and some
        # close inside it. A facility present for six of twenty quarters has
        # six rows, and that is coverage rather than missing data.
        if r.random() < 0.22:
            origination = int(r.integers(1, slots - 3))
        else:
            origination = -int(r.integers(1, 24))       # before the window
        tenor = int(r.integers(8, 40))
        maturity = origination + tenor
        facility_id = f"FAC{len(out_f) + 1:05d}"
        # A minority of facilities carry independently measured tranches, which
        # is why position_id is part of the atomic key.
        positions = 2 if r.random() < 0.12 else 1
        for p in range(positions):
            out_f.append(Facility(
                facility_id=facility_id,
                position_id=f"P{p + 1}",
                borrower_id=b.borrower_id,
                product_type=_pick(r, PRODUCTS),
                origination_index=origination,
                maturity_index=maturity,
                limit=float(b.scale * r.uniform(0.25, 1.6) / positions),
                utilisation=float(np.clip(r.beta(5, 2.2), 0.05, 0.995)),
                lgd_base=float(np.clip(r.normal(0.42, 0.13), 0.08, 0.90))))
        n += 1

    out_a: list[Asset] = []
    by_borrower: dict[str, list[str]] = {}
    for f in out_f:
        by_borrower.setdefault(f.borrower_id, []).append(
            f"{f.facility_id}:{f.position_id}")
    for i, b in enumerate(out_b):
        r = _rng("assets", b.borrower_id)
        keys = by_borrower.get(b.borrower_id, [])
        if not keys:
            continue
        for j in range(int(r.integers(1, 5))):
            kind = _pick(r, F.COLLATERAL_TYPES)
            # A third of assets secure more than one facility of the same
            # borrower. Summing their whole value across facilities is the
            # double count the catalog warns about.
            share = keys if (len(keys) > 1 and r.random() < 0.33) else [
                _pick(r, keys)]
            out_a.append(Asset(
                collateral_id=f"COL{len(out_a) + 1:05d}",
                borrower_id=b.borrower_id, collateral_type=kind,
                base_value=float(b.scale * r.uniform(0.15, 1.1)),
                currency=("USD" if r.random() < 0.09 else REPORTING_CURRENCY),
                secures=list(share),
                # Some valuations are simply missing in some quarters.
                valuation_gap_quarters=tuple(
                    int(x) for x in r.choice(range(slots),
                                             size=int(r.integers(0, 3)),
                                             replace=False))))
    return out_b, out_f, out_a


# -------------------------------------------------------------- macro paths

#: Long-run level and quarterly volatility for each factor. Demonstration
#: assumptions, not estimates of anything.
MACRO_SHAPE: dict[str, tuple[float, float, float]] = {
    # factor_id: (long-run mean, mean reversion speed, shock sd)
    "real_gdp_growth_yoy": (6.2, 0.30, 1.05),
    "cpi_inflation_yoy": (5.0, 0.26, 0.85),
    "unemployment_rate": (7.4, 0.20, 0.42),
    "policy_interest_rate": (6.0, 0.18, 0.36),
    "interbank_rate_3m": (6.6, 0.20, 0.40),
    "sovereign_bond_yield_10y": (7.1, 0.16, 0.34),
    "fx_lcy_per_usd": (83.0, 0.10, 1.30),
    "benchmark_oil_price": (78.0, 0.18, 7.50),
    "commercial_property_price_index": (112.0, 0.12, 2.60),
    "private_sector_credit_growth_yoy": (11.5, 0.24, 1.60),
}

#: Which way a factor pushes credit quality. Negative means a rise is bad.
MACRO_CREDIT_SIGN: dict[str, float] = {
    "real_gdp_growth_yoy": +1.0,
    "cpi_inflation_yoy": -0.35,
    "unemployment_rate": -0.85,
    "policy_interest_rate": -0.55,
    "interbank_rate_3m": -0.45,
    "sovereign_bond_yield_10y": -0.30,
    "fx_lcy_per_usd": -0.20,
    "benchmark_oil_price": -0.25,
    "commercial_property_price_index": +0.40,
    "private_sector_credit_growth_yoy": +0.30,
}

SCENARIO_SHIFT: dict[str, float] = {"baseline": 0.0, "upside": +0.9,
                                    "downside": -1.4}
SCENARIO_WEIGHT: dict[str, float] = {"baseline": 0.50, "upside": 0.20,
                                     "downside": 0.30}


def _actual_path(calendar: cal.Calendar) -> dict[str, dict[str, float]]:
    """The realised macro history, on the union of macro target quarters.

    Built once over the whole target span so that the value a later anchor
    reports as an actual is the SAME number an earlier anchor forecast toward
    -- the forecast error is then real rather than manufactured.
    """
    targets = calendar.macro_targets()
    out: dict[str, dict[str, float]] = {}
    for factor_id, _meaning, _unit in F.MACRO_FACTORS:
        mean, speed, sd = MACRO_SHAPE[factor_id]
        r = _rng("macro-actual", factor_id)
        level = mean + r.normal(0, sd)
        series: dict[str, float] = {}
        for target in targets:
            level += speed * (mean - level) + r.normal(0, sd)
            series[target] = float(level)
        out[factor_id] = series
    return out


def _cycle(actual: dict[str, dict[str, float]], quarter: str) -> float:
    """One standardized credit-cycle score for a quarter.

    Positive is a benign environment. This is what borrower quality tracks, and
    it is the reason a downturn in this book shows up in ratings, PDs,
    covenant headroom and ECL at the same time rather than in one of them.
    """
    total = 0.0
    for factor_id, _m, _u in F.MACRO_FACTORS:
        mean, _speed, sd = MACRO_SHAPE[factor_id]
        z = (actual[factor_id][quarter] - mean) / max(sd, 1e-9)
        total += MACRO_CREDIT_SIGN[factor_id] * z
    return float(total / len(F.MACRO_FACTORS))


def macro_rows(calendar: cal.Calendar,
               actual: dict[str, dict[str, float]]) -> pd.DataFrame:
    """One row per anchor, factor, geography, scenario and offset.

    Twenty offsets per anchor. A negative or zero offset is an observation; a
    positive one is a forecast made at that anchor, from that anchor's vintage,
    and it does NOT become an actual when the quarter later arrives.
    """
    rows: list[dict[str, Any]] = []
    for anchor in calendar.slots:
        anchor_end = cal.parse(anchor).end_date
        cutoff = anchor_end + timedelta(days=30)
        vintage = f"{anchor}-V1"
        for factor_id, _meaning, unit in F.MACRO_FACTORS:
            mean, speed, sd = MACRO_SHAPE[factor_id]
            for scenario in F.MACRO_SCENARIOS:
                r = _rng("macro-forecast", anchor, factor_id, scenario)
                shift = SCENARIO_SHIFT[scenario]
                level = actual[factor_id][anchor]
                for offset, target in calendar.macro_window(anchor):
                    target_end = cal.parse(target).end_date
                    if offset <= 0:
                        value = actual[factor_id][target]
                        status = ("current_actual" if offset == 0
                                  else "historical_actual")
                        # A scenario does not rewrite history: the past is the
                        # past in every scenario.
                        published = target_end + timedelta(days=40)
                    else:
                        # Forecast: mean-revert from the anchor toward a
                        # scenario-shifted long run, with vintage noise that
                        # widens with the horizon.
                        goal = mean + shift * sd
                        level += speed * (goal - level) + r.normal(
                            0, sd * 0.45 * min(offset, 6) / 6.0)
                        value = float(level)
                        status = "forecast"
                        published = cutoff
                    if offset == 0 and status == "current_actual":
                        # The current quarter may not be published yet.
                        rr = _rng("macro-nowcast", anchor, factor_id)
                        if rr.random() < 0.45:
                            status = "nowcast"
                            published = cutoff
                    rows.append({
                        "tenant_id": TENANT, "domain_id": DOMAIN,
                        "dataset_release_id": calendar.dataset_release_id,
                        "quarter_end_date": anchor_end,
                        "data_cutoff_at": pd.Timestamp(cutoff),
                        "source_system": "demo_macro_service",
                        "source_record_id": f"{factor_id}/{anchor}/{target}",
                        "source_period_start": cal.parse(target).start_date,
                        "source_period_end": target_end,
                        "source_published_at": pd.Timestamp(published),
                        "source_available_at": pd.Timestamp(
                            published + timedelta(days=2)),
                        "source_version": DATA_VERSION,
                        "mapping_version": DATA_VERSION,
                        "ingested_at": pd.Timestamp(cutoff),
                        "value_origin": ("source_forecast"
                                         if offset > 0 else "actual"),
                        "missing_reason": "",
                        "currency_code": ("USD" if factor_id
                                          == "benchmark_oil_price"
                                          else REPORTING_CURRENCY),
                        "reporting_currency": REPORTING_CURRENCY,
                        "fx_to_reporting_currency": 1.0,
                        "amount_scale": "unit",
                        "record_status": "available",
                        "observation_age_days": int(
                            (anchor_end - target_end).days),
                        "provenance_id":
                            f"mac:{factor_id}:{anchor}:{target}:{scenario}",
                        "factor_id": factor_id,
                        "reporting_quarter": anchor,
                        "macro_target_quarter": target,
                        "quarter_offset": int(offset),
                        "country_or_region": GEOGRAPHY,
                        "scenario_id": scenario,
                        "value": round(float(value), 4),
                        "unit": unit,
                        "index_base_period": (
                            "2019Q1=100"
                            if factor_id == "commercial_property_price_index"
                            else ""),
                        "frequency": ("monthly" if factor_id in (
                            "cpi_inflation_yoy", "unemployment_rate",
                            "fx_lcy_per_usd", "benchmark_oil_price")
                            else "quarterly"),
                        "quarter_aggregation_method": (
                            "average" if factor_id in (
                                "cpi_inflation_yoy", "unemployment_rate")
                            else "end_of_period" if factor_id in (
                                "fx_lcy_per_usd", "benchmark_oil_price")
                            else "native"),
                        "observation_status": status,
                        "forecast_vintage": vintage,
                        "published_at": pd.Timestamp(published),
                        "available_at": pd.Timestamp(
                            published + timedelta(days=2)),
                        "source_name": "demo_macro_service",
                        "source_reference": f"{factor_id}/{vintage}",
                    })
    return pd.DataFrame(rows)


def macro_pivot(window: pd.DataFrame) -> pd.DataFrame:
    """The convenience pivot: one row per anchor, geography and scenario, with
    two hundred value cells. Two hundred CELLS, not two hundred factors."""
    frame = window.copy()
    frame["_col"] = [
        f"{fid}_{'current' if off == 0 else ('lag%d' % -off if off < 0 else 'lead%d' % off)}"
        for fid, off in zip(frame["factor_id"], frame["quarter_offset"])]
    wide = frame.pivot_table(
        index=["reporting_quarter", "country_or_region", "scenario_id"],
        columns="_col", values="value", aggfunc="first").reset_index()
    wide.columns.name = None
    expected = [s.name for s in F.MACRO_PIVOT_FIELDS]
    for column in expected:
        if column not in wide.columns:
            wide[column] = np.nan
    return wide[["reporting_quarter", "country_or_region", "scenario_id"]
                + expected]


# ------------------------------------------------------ borrower trajectory

def quality_path(borrower: Borrower, calendar: cal.Calendar,
                 cycle: dict[str, float]) -> dict[str, float]:
    """Latent credit quality per reporting quarter. Higher is stronger.

    Never published. It is the generator's cause; exposing it would let an
    analysis read the answer key rather than infer it from the evidence.
    """
    r = _rng("quality", borrower.borrower_id)
    level = borrower.quality0
    out: dict[str, float] = {}
    for slot in calendar.slots:
        level += (borrower.drift
                  + borrower.macro_beta * 0.42 * cycle[slot]
                  + float(r.normal(0, 0.30))
                  - 0.10 * level)          # mild reversion, so nothing runs off
        out[slot] = float(level)
    return out


def rating_of(quality: float) -> str:
    """Map latent quality to one of the nineteen grades.

    Monotone by construction, so a borrower whose quality falls cannot be shown
    with an improving rating -- which is what makes a rating migration in this
    book a real signal rather than noise.
    """
    # quality about +2.2 is AAA, about -2.4 is C.
    span = 4.6
    position = (2.2 - float(quality)) / span * 18.0
    rank = int(np.clip(round(position), 0, 18))
    return F.RATING_SCALE[rank]


def pd_pit_12m_of(quality: float, cycle_now: float) -> float:
    """Point-in-time 12-month PD. Responds to the cycle: that is what PIT means."""
    score = -1.15 * float(quality) - 0.55 * float(cycle_now) - 2.55
    return float(np.clip(1.0 / (1.0 + np.exp(-score)), 0.0004, 0.97))


def pd_ttc_12m_of(quality: float) -> float:
    """Through-the-cycle 12-month PD. Deliberately does NOT see the cycle.

    Section 4.2: TTC parameters are not automatic substitutes for PIT inputs,
    and this data makes the difference visible rather than asserting it.
    """
    score = -1.15 * float(quality) - 2.55
    return float(np.clip(1.0 / (1.0 + np.exp(-score)), 0.0004, 0.97))


def hazard_curve(pd_12m: float, horizons: int) -> np.ndarray:
    """Marginal (conditional) default probabilities per year, decaying.

    Returns the per-horizon MARGINAL hazard. The cumulative PD is one minus
    the product of survivals across it, which is emphatically NOT the annual
    PD times the number of years -- the distinction section 4.2 insists on and
    validate_data.py asserts.
    """
    base = float(np.clip(pd_12m, 1e-6, 0.97))
    # A weak credit's hazard falls after the first year (survivors are the
    # stronger ones); a strong credit's rises slightly toward its mean.
    slope = -0.16 if base > 0.05 else 0.07
    out = np.array([base * ((1.0 + slope) ** h) for h in range(horizons)])
    return np.clip(out, 1e-6, 0.97)


def cumulative_pd(marginal: np.ndarray) -> np.ndarray:
    survival = np.cumprod(1.0 - marginal)
    return 1.0 - survival


# ------------------------------------------------------ financial statements

def statement_path(borrower: Borrower, calendar: cal.Calendar,
                   quality: dict[str, float]) -> dict[str, dict[str, float]]:
    """A coherent balance sheet and income statement per reporting quarter.

    Coherent means the accounting identities hold: current plus non-current
    assets equal total assets, liabilities plus equity equal assets, and the
    income statement rolls from revenue to net profit through the components
    published beside it. A ratio computed from these agrees with the ratio
    field published alongside, which is what lets a test check both.
    """
    out: dict[str, dict[str, float]] = {}
    r = _rng("statements", borrower.borrower_id)
    revenue = borrower.scale * float(r.uniform(0.55, 1.6))
    assets = borrower.scale * float(r.uniform(1.1, 2.6))
    leverage0 = float(np.clip(r.normal(0.55, 0.13), 0.18, 0.86))
    margin0 = float(np.clip(r.normal(0.135, 0.05), -0.04, 0.34))

    for slot in calendar.slots:
        q = quality[slot]
        growth = 1.0 + 0.018 + 0.030 * q + float(r.normal(0, 0.028))
        revenue = max(revenue * growth, borrower.scale * 0.08)
        assets = max(assets * (1.0 + 0.012 + 0.020 * q
                               + float(r.normal(0, 0.020))),
                     borrower.scale * 0.15)
        margin = float(np.clip(margin0 + 0.045 * q + r.normal(0, 0.017),
                               -0.22, 0.40))
        leverage = float(np.clip(leverage0 - 0.035 * q + r.normal(0, 0.012),
                                 0.10, 0.95))

        # ---- balance sheet, built to add up -------------------------
        current_assets = assets * float(np.clip(r.normal(0.46, 0.07), 0.20, 0.72))
        noncurrent_assets = assets - current_assets
        cash = current_assets * float(np.clip(r.normal(0.16, 0.06), 0.02, 0.42))
        restricted_cash = cash * float(np.clip(r.normal(0.11, 0.06), 0.0, 0.40))
        sti = current_assets * float(np.clip(r.normal(0.06, 0.03), 0.0, 0.20))
        receivables_net = current_assets * float(
            np.clip(r.normal(0.34, 0.07), 0.08, 0.60))
        allowance = receivables_net * float(np.clip(r.normal(0.045, 0.02),
                                                    0.0, 0.15))
        receivables_gross = receivables_net + allowance
        inventory = current_assets * float(np.clip(r.normal(0.27, 0.07),
                                                   0.02, 0.55))
        prepayments = current_assets * 0.04
        other_current = max(current_assets - cash - sti - receivables_net
                            - inventory - prepayments, 0.0)

        ppe_net = noncurrent_assets * float(np.clip(r.normal(0.68, 0.10),
                                                    0.25, 0.92))
        accumulated_depreciation = ppe_net * float(np.clip(r.normal(0.55, 0.15),
                                                           0.05, 1.3))
        ppe_gross = ppe_net + accumulated_depreciation
        goodwill = noncurrent_assets * float(np.clip(r.normal(0.07, 0.05),
                                                     0.0, 0.25))
        intangibles = noncurrent_assets * float(np.clip(r.normal(0.05, 0.03),
                                                        0.0, 0.18))
        lt_investments = noncurrent_assets * 0.06
        other_noncurrent = max(noncurrent_assets - ppe_net - goodwill
                               - intangibles - lt_investments, 0.0)

        total_liabilities = assets * leverage
        equity = assets - total_liabilities
        current_liabilities = total_liabilities * float(
            np.clip(r.normal(0.52, 0.09), 0.20, 0.82))
        noncurrent_liabilities = total_liabilities - current_liabilities

        st_borrowings = current_liabilities * float(
            np.clip(r.normal(0.34, 0.08), 0.05, 0.62))
        cpltd = current_liabilities * float(np.clip(r.normal(0.14, 0.05),
                                                    0.01, 0.30))
        trade_payables = current_liabilities * float(
            np.clip(r.normal(0.30, 0.07), 0.05, 0.55))
        interest_payable = current_liabilities * 0.03
        tax_payable = current_liabilities * 0.05
        accrued = current_liabilities * 0.05
        other_cl = max(current_liabilities - st_borrowings - cpltd
                       - trade_payables - interest_payable - tax_payable
                       - accrued, 0.0)

        long_term_debt = noncurrent_liabilities * float(
            np.clip(r.normal(0.62, 0.10), 0.20, 0.88))
        lease_nc = noncurrent_liabilities * float(np.clip(r.normal(0.10, 0.05),
                                                          0.0, 0.28))
        lease_c = current_liabilities * 0.04
        deferred_tax = noncurrent_liabilities * 0.07
        provisions_nc = noncurrent_liabilities * 0.05
        other_ncl = max(noncurrent_liabilities - long_term_debt - lease_nc
                        - deferred_tax - provisions_nc, 0.0)

        share_capital = equity * float(np.clip(r.normal(0.30, 0.10), 0.05, 0.70))
        reserves = equity * float(np.clip(r.normal(0.18, 0.07), 0.0, 0.45))
        nci = equity * float(np.clip(r.normal(0.04, 0.03), 0.0, 0.15))
        retained = equity - share_capital - reserves - nci

        # ---- income statement, rolled through ----------------------
        cogs = revenue * float(np.clip(r.normal(0.68, 0.06), 0.35, 0.90))
        gross_profit = revenue - cogs
        staff = revenue * float(np.clip(r.normal(0.075, 0.02), 0.01, 0.20))
        selling = revenue * float(np.clip(r.normal(0.035, 0.012), 0.0, 0.10))
        admin = revenue * float(np.clip(r.normal(0.042, 0.014), 0.0, 0.12))
        rnd = revenue * float(np.clip(r.normal(0.008, 0.006), 0.0, 0.05))
        lease_rent = revenue * 0.011
        other_opex = revenue * float(np.clip(r.normal(0.017, 0.008), 0.0, 0.06))
        depreciation = ppe_gross * float(np.clip(r.normal(0.021, 0.005),
                                                 0.004, 0.05))
        amortization = intangibles * 0.06
        other_operating_income = revenue * float(np.clip(r.normal(0.008, 0.005),
                                                         0.0, 0.03))
        total_opex = (staff + selling + admin + rnd + lease_rent + other_opex
                      + depreciation + amortization)
        ebitda = (gross_profit + other_operating_income - staff - selling
                  - admin - rnd - lease_rent - other_opex)
        ebit = ebitda - depreciation - amortization

        total_debt = st_borrowings + cpltd + long_term_debt
        rate = float(np.clip(r.normal(0.093, 0.016), 0.045, 0.185))
        interest_expense = total_debt * rate / 4.0
        interest_income = (cash + sti) * 0.045 / 4.0
        fx = revenue * float(r.normal(0.0, 0.004))
        exceptional_income = (revenue * 0.006 if r.random() < 0.10 else 0.0)
        exceptional_expenses = (revenue * 0.011 if r.random() < 0.14 else 0.0)
        other_nonop = revenue * 0.003
        pbt = (ebit + interest_income - interest_expense + fx
               + exceptional_income - exceptional_expenses + other_nonop)
        tax = max(pbt, 0.0) * 0.25
        net_profit = pbt - tax
        # Keep the published margin close to the intended one without breaking
        # the roll-forward: the intended margin drives revenue growth, and the
        # realised one is whatever the components produce.
        _ = margin

        ocf = ebitda * float(np.clip(r.normal(0.78, 0.16), 0.10, 1.30))
        capex = revenue * float(np.clip(r.normal(0.052, 0.025), 0.004, 0.16))
        fcf = ocf - capex
        principal_due = cpltd
        interest_due = interest_expense
        debt_service = principal_due + interest_due
        cfads = ebitda * float(np.clip(r.normal(0.86, 0.10), 0.30, 1.15))

        out[slot] = {
            "revenue": revenue, "cost_of_goods_sold": cogs,
            "gross_profit": gross_profit, "staff_costs": staff,
            "selling_distribution_expenses": selling,
            "administrative_expenses": admin,
            "research_development_expenses": rnd,
            "lease_rent_expense": lease_rent,
            "depreciation_expense": depreciation,
            "amortization_expense": amortization,
            "other_operating_expenses": other_opex,
            "total_operating_expenses": total_opex,
            "other_operating_income": other_operating_income,
            "ebitda": ebitda, "ebit": ebit,
            "interest_income": interest_income,
            "interest_expense": interest_expense,
            "net_finance_cost": interest_expense - interest_income,
            "foreign_exchange_gain_loss": fx,
            "exceptional_income": exceptional_income,
            "exceptional_expenses": exceptional_expenses,
            "other_nonoperating_income": other_nonop,
            "profit_before_tax": pbt, "tax_expense": tax,
            "net_profit": net_profit,
            "net_profit_attributable_to_owners": net_profit * (
                1.0 - (nci / equity if equity > 0 else 0.0)),
            "dividends_declared": max(net_profit, 0.0) * 0.18,
            "domestic_revenue": revenue * 0.78,
            "export_revenue": revenue * 0.22,
            "credit_sales": revenue * float(np.clip(r.normal(0.82, 0.09),
                                                    0.30, 1.0)),
            "sales_returns": revenue * 0.012,
            "sales_discounts": revenue * 0.008,
            "cash_and_cash_equivalents": cash,
            "restricted_cash": restricted_cash,
            "short_term_investments": sti,
            "trade_receivables_gross": receivables_gross,
            "receivables_loss_allowance": allowance,
            "trade_receivables_net": receivables_net,
            "inventory": inventory, "prepayments": prepayments,
            "other_current_assets": other_current,
            "current_assets": current_assets,
            "ppe_gross": ppe_gross,
            "accumulated_depreciation": accumulated_depreciation,
            "ppe_net": ppe_net, "goodwill": goodwill,
            "other_intangible_assets": intangibles,
            "long_term_investments": lt_investments,
            "other_noncurrent_assets": other_noncurrent,
            "noncurrent_assets": noncurrent_assets,
            "total_assets": assets,
            "trade_payables": trade_payables,
            "short_term_borrowings": st_borrowings,
            "current_portion_long_term_debt": cpltd,
            "interest_payable": interest_payable, "tax_payable": tax_payable,
            "accrued_expenses": accrued,
            "other_current_liabilities": other_cl,
            "current_liabilities": current_liabilities,
            "long_term_debt": long_term_debt,
            "lease_liabilities_current": lease_c,
            "lease_liabilities_noncurrent": lease_nc,
            "deferred_tax_liabilities": deferred_tax,
            "provisions_noncurrent": provisions_nc,
            "other_noncurrent_liabilities": other_ncl,
            "noncurrent_liabilities": noncurrent_liabilities,
            "total_liabilities": total_liabilities,
            "share_capital": share_capital, "retained_earnings": retained,
            "reserves": reserves, "noncontrolling_interests": nci,
            "shareholders_equity": equity,
            "tangible_net_worth": equity - goodwill - intangibles,
            "working_capital": current_assets - current_liabilities,
            "liquid_assets": cash - restricted_cash + sti,
            "total_debt": total_debt,
            "net_debt": total_debt - (cash - restricted_cash),
            "capital_employed": assets - current_liabilities,
            "operating_cash_flow": ocf, "capital_expenditure": capex,
            "free_cash_flow": fcf,
            "cash_available_for_debt_service": cfads,
            "scheduled_principal_due": principal_due,
            "interest_due_for_debt_service": interest_due,
            "debt_service_due": debt_service,
            "credit_purchases": cogs * float(np.clip(r.normal(0.74, 0.10),
                                                     0.25, 1.0)),
        }
    return out


# ----------------------------------------------------------- the forty ratios

def _safe(numerator: float | None, denominator: float | None
          ) -> tuple[float | None, str]:
    """Divide, or say why not.

    Section 4.6: division by zero yields a FLAGGED UNAVAILABLE result, never
    infinity and never zero. A negative denominator keeps its number and gets a
    warning rather than being hidden.
    """
    if numerator is None or denominator is None:
        return None, "unavailable"
    if denominator == 0:
        return None, "invalid_denominator"
    value = float(numerator) / float(denominator)
    if denominator < 0:
        return value, "invalid_denominator"
    return value, "derived"


def ratios_for(current: dict[str, float], opening: dict[str, float] | None,
               period_days: int) -> dict[str, Any]:
    """The forty ratios for one borrower-quarter, with a status on each.

    `opening` is the TRUE opening balance sheet where one is available. Where
    it is not, an average-denominator ratio is unavailable rather than computed
    from an invented prior quarter -- section 4.6 is explicit that "average"
    means genuine beginning and ending balances.
    """
    c, out = current, {}

    def put(name: str, value: float | None, status: str) -> None:
        out[name] = value
        out[f"{name}_status"] = status

    def avg(key: str) -> float | None:
        if opening is None:
            return None
        return (float(c[key]) + float(opening[key])) / 2.0

    eligible_cash = c["cash_and_cash_equivalents"] - c["restricted_cash"]
    cl = c["current_liabilities"]

    put("current_ratio", *_safe(c["current_assets"], cl))
    put("quick_ratio", *_safe(eligible_cash + c["short_term_investments"]
                              + c["trade_receivables_net"], cl))
    put("cash_ratio", *_safe(eligible_cash + c["short_term_investments"], cl))
    # The bank's own liquidity ratio. Here it is a genuinely different
    # definition, not an alias: liquid assets over short-term debt.
    put("liquidity_ratio", *_safe(
        c["liquid_assets"],
        c["short_term_borrowings"] + c["current_portion_long_term_debt"]))
    put("operating_cash_flow_to_current_liabilities",
        *_safe(c["operating_cash_flow"], cl))
    put("working_capital_to_total_assets",
        *_safe(c["working_capital"], c["total_assets"]))
    put("liquid_assets_to_total_assets",
        *_safe(c["liquid_assets"], c["total_assets"]))
    put("dscr", *_safe(c["cash_available_for_debt_service"],
                       c["debt_service_due"]))
    put("interest_coverage_ratio", *_safe(c["ebit"], c["interest_expense"]))
    put("ebitda_interest_coverage", *_safe(c["ebitda"], c["interest_expense"]))
    put("fixed_charge_coverage_ratio",
        *_safe(c["ebitda"] + c["lease_rent_expense"],
               c["interest_expense"] + c["lease_rent_expense"]
               + c["scheduled_principal_due"]))
    put("operating_cash_flow_to_debt",
        *_safe(c["operating_cash_flow"], c["total_debt"]))
    put("free_cash_flow_to_debt_service",
        *_safe(c["free_cash_flow"], c["debt_service_due"]))
    # A negative EBITDA makes these economically uninterpretable. The number is
    # kept with a warning rather than hidden.
    for name, num in (("net_debt_to_ebitda", c["net_debt"]),
                      ("debt_to_ebitda", c["total_debt"])):
        value, status = _safe(num, c["ebitda"])
        if c["ebitda"] < 0:
            status = "invalid_denominator"
        put(name, value, status)
    put("debt_to_equity", *_safe(c["total_debt"], c["shareholders_equity"]))
    put("liabilities_to_assets",
        *_safe(c["total_liabilities"], c["total_assets"]))
    put("equity_to_assets",
        *_safe(c["shareholders_equity"], c["total_assets"]))
    put("long_term_debt_to_capital",
        *_safe(c["long_term_debt"],
               c["long_term_debt"] + c["shareholders_equity"]))
    put("tangible_net_worth_to_debt",
        *_safe(c["tangible_net_worth"], c["total_debt"]))
    put("gross_profit_margin", *_safe(c["gross_profit"], c["revenue"]))
    put("ebitda_margin", *_safe(c["ebitda"], c["revenue"]))
    put("operating_profit_margin", *_safe(c["ebit"], c["revenue"]))
    put("net_profit_margin", *_safe(c["net_profit"], c["revenue"]))
    put("return_on_assets", *_safe(c["net_profit"], avg("total_assets")))
    put("return_on_equity", *_safe(c["net_profit"],
                                   avg("shareholders_equity")))
    put("return_on_capital_employed", *_safe(c["ebit"],
                                             avg("capital_employed")))
    put("operating_cash_flow_margin",
        *_safe(c["operating_cash_flow"], c["revenue"]))
    put("free_cash_flow_margin", *_safe(c["free_cash_flow"], c["revenue"]))
    put("total_asset_turnover", *_safe(c["revenue"], avg("total_assets")))
    put("fixed_asset_turnover", *_safe(c["revenue"], avg("ppe_net")))
    put("working_capital_turnover", *_safe(c["revenue"],
                                           avg("working_capital")))
    put("inventory_turnover", *_safe(c["cost_of_goods_sold"], avg("inventory")))
    put("receivables_turnover", *_safe(c["credit_sales"],
                                       avg("trade_receivables_net")))
    put("payables_turnover", *_safe(c["credit_purchases"],
                                    avg("trade_payables")))

    def days(numerator_key: str, flow_key: str) -> tuple[float | None, str]:
        average = avg(numerator_key)
        value, status = _safe(average, c[flow_key])
        return ((value * period_days) if value is not None else None), status

    put("receivables_days", *days("trade_receivables_net", "credit_sales"))
    put("inventory_days", *days("inventory", "cost_of_goods_sold"))
    put("payables_days", *days("trade_payables", "credit_purchases"))
    parts = [out["receivables_days"], out["inventory_days"],
             out["payables_days"]]
    if all(p is not None for p in parts):
        put("cash_conversion_cycle_days", parts[0] + parts[1] - parts[2],
            "derived")
    else:
        put("cash_conversion_cycle_days", None, "unavailable")
    put("capex_to_operating_cash_flow",
        *_safe(c["capital_expenditure"], c["operating_cash_flow"]))
    return out


# ---------------------------------------------------- IFRS 9 measurement

def measure_ecl(*, pd_pit_12m: float, pd_lifetime: float, lgd: float,
                ead: float, remaining_quarters: int, eir: float,
                stage: int, scenario: str) -> dict[str, Any]:
    """Stored IFRS 9 outputs for one position, run and scenario.

    A genuine term structure: marginal hazards, survival, an amortising EAD
    path and discounting at the effective rate. Stage 1 measures twelve months;
    stages 2 and 3 measure lifetime. The result is the SUM of the horizon
    shortfalls, so an analysis can reconcile it -- and a reconstruction that
    reaches for a single PD x LGD x EAD product will NOT reproduce it, which is
    the point section 4.2 makes about forcing that formula.
    """
    shift = {"baseline": 1.0, "upside": 0.72, "downside": 1.55}[scenario]
    years = max(1, int(np.ceil(remaining_quarters / 4.0)))
    horizon_years = 1 if stage == 1 else years
    marginal = hazard_curve(min(pd_pit_12m * shift, 0.97), horizon_years)
    survival = np.concatenate([[1.0], np.cumprod(1.0 - marginal)[:-1]])
    lgd_s = float(np.clip(lgd * (0.92 if scenario == "upside" else
                                 1.14 if scenario == "downside" else 1.0),
                          0.02, 0.98))
    rows: list[dict[str, Any]] = []
    total = 0.0
    for h in range(horizon_years):
        # EAD amortises toward maturity.
        ead_h = float(ead) * max(0.0, 1.0 - 0.9 * h / max(years, 1))
        discount = 1.0 / ((1.0 + max(eir, 0.001)) ** (h + 0.5))
        shortfall = float(marginal[h]) * float(survival[h]) * lgd_s * ead_h * discount
        total += shortfall
        rows.append({
            "term_horizon_index": h,
            "term_pd_marginal": float(marginal[h]),
            "term_survival": float(survival[h]),
            "term_lgd": lgd_s, "term_ead": ead_h,
            "term_discount_factor": float(discount),
            "term_expected_shortfall": float(shortfall),
        })
    cumulative = cumulative_pd(marginal)
    for h, row in enumerate(rows):
        row["term_pd_cumulative"] = float(cumulative[h])
    return {
        "ecl": float(total), "rows": rows, "lgd": lgd_s,
        "pd_12m": float(marginal[0]),
        "pd_lifetime": float(cumulative[-1]),
        "horizon_years": horizon_years,
    }


def stage_of(*, pd_now: float, pd_origination: float, days_past_due: int,
             defaulted: bool) -> tuple[int, bool, str]:
    """Stored stage, SICR flag and the recorded reason.

    A demonstration policy, applied once when the data is generated. It is NOT
    a runtime rule: Cockpit reads ifrs9_stage and never assigns one.
    """
    if defaulted or days_past_due >= 90:
        return 3, True, "default or 90+ days past due"
    relative = pd_now / max(pd_origination, 1e-6)
    if relative >= 2.0 and (pd_now - pd_origination) >= 0.005:
        return 2, True, ("PIT 12-month PD at least doubled since origination "
                         "and rose by at least 50 basis points")
    if days_past_due >= 30:
        return 2, True, "30+ days past due"
    return 1, False, ""


# ------------------------------------------------------------- assembly

QUALITATIVE_BY_QUALITY = {
    # quality threshold (upper bound) -> most likely grade index
    -1.2: 0, -0.4: 1, 0.4: 2, 1.2: 3,
}


def _qualitative_grade(rng: np.random.Generator, quality: float) -> str:
    base = 4
    for threshold, index in sorted(QUALITATIVE_BY_QUALITY.items()):
        if quality < threshold:
            base = index
            break
    jitter = int(rng.integers(-1, 2))
    return F.QUALITATIVE_GRADES[int(np.clip(base + jitter, 0, 4))]


def _statement_period(borrower: Borrower, slot: str, position: int
                      ) -> tuple[str, int, bool, bool]:
    """(period basis, period days, is a new observation, audited).

    A borrower reporting annually has ONE new statement a year; the other three
    quarters carry it forward. Section 3.2: a carried-forward annual statement
    is not a newly observed quarterly statement, and this is where that becomes
    true of the data rather than only of the documentation.
    """
    quarter = cal.parse(slot).quarter
    fy_quarter = 1 if borrower.fiscal_year_end_month == 3 else 4
    if borrower.statement_frequency == "quarterly":
        return "quarter", 91, True, False
    if borrower.statement_frequency == "semiannual":
        fresh = quarter in (fy_quarter, (fy_quarter + 2 - 1) % 4 + 1)
        return ("year_to_date" if fresh else "year_to_date", 182, fresh,
                quarter == fy_quarter)
    fresh = quarter == fy_quarter
    return "annual", 365, fresh, fresh


def build_release(*, dataset_release_id: str = "demo-20q-v1",
                  last_quarter: str = "2026Q2",
                  borrowers: int = DEFAULT_BORROWERS,
                  facilities: int = DEFAULT_FACILITIES,
                  populated: Sequence[str] | None = None
                  ) -> "Release":
    """Build the whole twenty-quarter release. Deterministic."""
    calendar = cal.Calendar.ending(
        last_quarter, dataset_release_id=dataset_release_id,
        populated=tuple(populated) if populated else None)
    actual = _actual_path(calendar)
    cycle = {slot: _cycle(actual, slot) for slot in calendar.slots}
    people, positions, assets = build_population(
        borrowers=borrowers, facilities=facilities, slots=len(calendar))

    quality = {b.borrower_id: quality_path(b, calendar, cycle) for b in people}
    statements = {b.borrower_id: statement_path(b, calendar, quality[b.borrower_id])
                  for b in people}
    by_id = {b.borrower_id: b for b in people}

    facility_rows: list[dict[str, Any]] = []
    ifrs9_rows: list[dict[str, Any]] = []
    financial_rows: list[dict[str, Any]] = []
    rating_rows: list[dict[str, Any]] = []
    qualitative_rows: list[dict[str, Any]] = []
    collateral_rows: list[dict[str, Any]] = []
    allocation_rows: list[dict[str, Any]] = []
    covenant_rows: list[dict[str, Any]] = []
    calendar_rows: list[dict[str, Any]] = []

    #: The origination baselines, set the first time a position is observed.
    origination_pd: dict[str, tuple[float, float, float, float]] = {}
    #: Facility keys an asset is allocated to, per quarter, for the summaries.
    allocations_by_position: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def keys(slot: str) -> dict[str, Any]:
        end = cal.parse(slot).end_date
        return {
            "tenant_id": TENANT, "domain_id": DOMAIN,
            "dataset_release_id": dataset_release_id,
            "reporting_quarter": slot,
            "quarter_end_date": end,
            "data_cutoff_at": pd.Timestamp(end + timedelta(days=45)),
            "source_system": "cockpit_demo_generator",
            "source_version": DATA_VERSION, "mapping_version": DATA_VERSION,
            "ingested_at": pd.Timestamp(end + timedelta(days=46)),
            "currency_code": REPORTING_CURRENCY,
            "reporting_currency": REPORTING_CURRENCY,
            "fx_to_reporting_currency": 1.0,
            "amount_scale": AMOUNT_SCALE,
            "record_status": "available",
        }

    for index, slot in enumerate(calendar.slots):
        if slot not in calendar.populated:
            calendar_rows.append({
                **keys(slot), "slot_index": index, "is_populated": False,
                "facility_row_count": 0, "borrower_row_count": 0,
                "coverage_note": ("This slot was created by the twenty-quarter "
                                  "calendar but carries no observations in "
                                  "this release."),
                "value_origin": "synthetic_demo", "missing_reason":
                    "outside_coverage", "provenance_id": f"cal:{slot}",
                "source_record_id": slot, "observation_age_days": 0,
            })
            continue

        base = keys(slot)
        end = cal.parse(slot).end_date
        cutoff = base["data_cutoff_at"]

        # ---- collateral assets, at ASSET grain ----------------------
        asset_value: dict[str, dict[str, float]] = {}
        for asset in assets:
            r = _rng("valuation", asset.collateral_id, slot)
            missing = index in asset.valuation_gap_quarters
            drift = 1.0 + 0.006 + 0.02 * cycle[slot] + float(r.normal(0, 0.03))
            gross = asset.base_value * (drift ** (index + 1))
            fx = 83.0 if asset.currency == "USD" else 1.0
            gross_rcy = gross * fx
            eligible = gross_rcy * float(np.clip(r.normal(0.94, 0.05), 0.5, 1.0))
            market_h = float(np.clip(r.normal(0.12, 0.06), 0.0, 0.45))
            liquidity_h = float(np.clip(r.normal(0.06, 0.03), 0.0, 0.28))
            fx_h = 0.08 if asset.currency != REPORTING_CURRENCY else 0.0
            legal_h = float(np.clip(r.normal(0.03, 0.02), 0.0, 0.15))
            # The source's own total is NOT the sum of the components: they
            # overlap, and the combination method says so.
            total_h = float(np.clip(
                1.0 - (1 - market_h) * (1 - liquidity_h) * (1 - fx_h)
                * (1 - legal_h), 0.0, 0.95))
            haircut_amount = eligible * total_h
            net = eligible - haircut_amount
            valuation_date = end - timedelta(days=int(r.integers(20, 420)))
            expiry = valuation_date + timedelta(days=365)
            asset_value[asset.collateral_id] = {
                "gross_rcy": 0.0 if missing else gross_rcy,
                "eligible": 0.0 if missing else eligible,
                "net": 0.0 if missing else net,
                "haircut": total_h, "haircut_amount":
                    0.0 if missing else haircut_amount,
                "overdue": float(expiry < end),
                "missing": float(missing),
            }
            collateral_rows.append({
                **base,
                "collateral_id": asset.collateral_id,
                "collateral_type": asset.collateral_type,
                "collateral_description":
                    f"{asset.collateral_type.replace('_', ' ')} pledged by "
                    f"{by_id[asset.borrower_id].name}",
                "collateral_owner_reference": asset.borrower_id,
                "valuation_date": None if missing else valuation_date,
                "valuation_available_at":
                    None if missing else pd.Timestamp(
                        valuation_date + timedelta(days=12)),
                "valuation_method": ("market_comparable"
                                     if asset.collateral_type.endswith("property")
                                     else "book_value"),
                "valuation_source": "demo_valuer",
                "collateral_currency": asset.currency,
                "gross_market_value": None if missing else gross,
                "gross_market_value_rcy": None if missing else gross_rcy,
                "eligible_value_before_haircut": None if missing else eligible,
                "market_haircut": None if missing else market_h,
                "liquidity_haircut": None if missing else liquidity_h,
                "fx_haircut": None if missing else fx_h,
                "legal_haircut": None if missing else legal_h,
                "total_haircut": None if missing else total_h,
                "haircut_combination_method": "multiplicative",
                "haircut_policy_version": "demo-haircut-v1",
                "haircut_amount": None if missing else haircut_amount,
                "haircut_base": "eligible_value_before_haircut",
                "net_realizable_value": None if missing else net,
                "valuation_expiry_date": None if missing else expiry,
                "valuation_overdue_flag": (None if missing else bool(expiry < end)),
                "valuation_status": "unvalued" if missing else (
                    "expired" if expiry < end else "valued"),
                "source_record_id": asset.collateral_id,
                "source_period_start": None if missing else valuation_date,
                "source_period_end": None if missing else valuation_date,
                "source_published_at": None if missing else pd.Timestamp(
                    valuation_date + timedelta(days=10)),
                "source_available_at": None if missing else pd.Timestamp(
                    valuation_date + timedelta(days=12)),
                "value_origin": "synthetic_demo",
                "missing_reason": "not_collected" if missing else "",
                "provenance_id": f"col:{asset.collateral_id}:{slot}",
                "observation_age_days": (
                    None if missing else int((end - valuation_date).days)),
            })

        # ---- allocations, at the LINK grain -------------------------
        allocations_by_position.clear()
        for asset in assets:
            value = asset_value[asset.collateral_id]
            live = [k for k in asset.secures]
            if not live:
                continue
            share = 1.0 / len(live)
            for k in live:
                facility_id, position_id = k.split(":")
                allocated_gross = value["gross_rcy"] * share
                allocated_net = value["net"] * share
                row = {
                    **base,
                    "allocation_id": f"ALC{asset.collateral_id}-{facility_id}-{position_id}",
                    "collateral_id": asset.collateral_id,
                    "facility_id": facility_id, "position_id": position_id,
                    "allocation_share": share,
                    "allocated_gross_value_rcy": allocated_gross,
                    "allocated_net_value_rcy": allocated_net,
                    "lien_rank": 1 if len(live) == 1 else 2,
                    "secured_amount": allocated_net,
                    "allocation_status": "source",
                    "source_record_id": asset.collateral_id,
                    "value_origin": "synthetic_demo", "missing_reason": "",
                    "provenance_id": f"alc:{asset.collateral_id}:{k}:{slot}",
                    "observation_age_days": 0,
                }
                allocation_rows.append(row)
                allocations_by_position.setdefault(
                    (facility_id, position_id, slot), []).append(
                        {"type": asset.collateral_type, **value,
                         "allocated_gross": allocated_gross,
                         "allocated_net": allocated_net})

        # ---- borrower statements, ratings, qualitative ---------------
        live_borrowers: set[str] = set()
        for position in positions:
            if position.origination_index <= index < position.maturity_index:
                live_borrowers.add(position.borrower_id)

        for borrower_id in sorted(live_borrowers):
            borrower = by_id[borrower_id]
            current = statements[borrower_id][slot]
            previous_slot = calendar.previous(slot)
            opening = (statements[borrower_id][previous_slot]
                       if previous_slot and previous_slot in calendar.populated
                       else None)
            basis, days_in_period, fresh, audited = _statement_period(
                borrower, slot, index)
            # A carried-forward statement reports the LAST OBSERVED figures and
            # says so, rather than presenting stale numbers as new.
            source_slot = slot
            if not fresh:
                for back in range(index - 1, -1, -1):
                    candidate = calendar.slots[back]
                    b2, _d, f2, _a = _statement_period(borrower, candidate, back)
                    if f2 and candidate in calendar.populated:
                        source_slot = candidate
                        break
            reported = statements[borrower_id][source_slot]
            source_end = cal.parse(source_slot).end_date
            available = cal.available_by(source_end, audited=audited)
            age = int((end - source_end).days)

            financial_rows.append({
                **base,
                "borrower_id": borrower_id,
                "statement_scope": ("consolidated" if borrower.group_id
                                    else "standalone"),
                "statement_id": f"FS-{borrower_id}-{source_slot}",
                "statement_version": "1",
                "statement_period_basis": basis,
                "statement_period_days": days_in_period,
                "audited_flag": bool(audited),
                "audit_opinion": ("unqualified" if audited else ""),
                "equity_scope": "including_nci",
                "tangible_net_worth_basis": "equity less goodwill and other "
                                            "intangible assets",
                "liquid_assets_basis": "unrestricted cash plus short-term "
                                       "investments",
                "debt_lease_treatment": "leases_excluded",
                "fcf_basis": "operating cash flow less capital expenditure",
                "dscr_basis": "cash available for debt service over scheduled "
                              "principal plus interest for the same period",
                "operating_expense_basis": "staff, selling, administrative, "
                                           "R&D, lease, other, depreciation "
                                           "and amortization",
                "ebitda_basis": "gross profit plus other operating income less "
                                "cash operating expenses",
                "financial_input_coverage": (
                    "" if fresh else
                    "carried forward from " + source_slot),
                "source_record_id": f"FS-{borrower_id}-{source_slot}",
                "source_period_start": source_end - timedelta(
                    days=days_in_period),
                "source_period_end": source_end,
                "source_published_at": pd.Timestamp(available),
                "source_available_at": pd.Timestamp(available),
                "value_origin": "synthetic_demo" if fresh else "carried_forward",
                "missing_reason": "" if fresh else "no_prior_observation"
                if source_slot == slot else "",
                "provenance_id": f"fs:{borrower_id}:{source_slot}",
                "observation_age_days": age,
                **{k: float(v) for k, v in reported.items()},
                **{f"opening_{k}": (float(opening[k]) if opening else None)
                   for k in ("total_assets", "shareholders_equity", "inventory",
                             "trade_receivables_net", "trade_payables",
                             "ppe_net", "working_capital", "capital_employed")},
            })

            grade = rating_of(quality[borrower_id][slot])
            previous_grade = (rating_of(quality[borrower_id][previous_slot])
                              if previous_slot in calendar.populated
                              and previous_slot else "")
            rr = _rng("rating", borrower_id, slot)
            observed = rr.random() > 0.10
            ratio_values = ratios_for(reported, opening, days_in_period)
            rating_rows.append({
                **base,
                "borrower_id": borrower_id,
                "rating_basis": "internal_final",
                "risk_rating": grade if observed else previous_grade or grade,
                "rating_rank": F.RATING_RANK[
                    grade if observed else previous_grade or grade],
                "rating_scale_id": "cockpit-19-grade",
                "rating_scale_version": "1.0",
                "rating_effective_date": end - timedelta(
                    days=int(rr.integers(5, 180))),
                "rating_review_date": end + timedelta(days=180),
                "rating_previous_recorded": previous_grade,
                "rating_at_origination": rating_of(
                    quality[borrower_id][calendar.slots[0]]),
                "rating_outlook": str(rr.choice(
                    ["positive", "stable", "negative", "developing"],
                    p=[0.14, 0.60, 0.20, 0.06])),
                "rating_reason_recorded": (
                    "annual review" if observed else ""),
                "rating_override_flag": bool(rr.random() < 0.06),
                "rating_override_reason": ("committee override recorded"
                                           if rr.random() < 0.06 else ""),
                "rating_source": "internal_committee",
                "rating_approver_reference": "REDACTED",
                "rating_status": "observed" if observed else "carried_forward",
                "rating_missing_reason": "",
                "ratio_definition_id": "cockpit-40-ratio-v1",
                "ratio_period_basis": basis,
                "ratio_period_days": days_in_period,
                "quick_ratio_basis": "unrestricted cash, short-term "
                                     "investments and net trade receivables",
                "liquidity_ratio_basis": "liquid assets over short-term "
                                         "borrowings plus current portion of "
                                         "long-term debt",
                "fixed_charge_coverage_basis": "EBITDA plus lease rent over "
                                               "interest plus lease rent plus "
                                               "scheduled principal",
                "receivables_turnover_basis": "credit_sales",
                "payables_turnover_basis": "credit_purchases",
                "source_record_id": f"RT-{borrower_id}-{slot}",
                "source_period_start": source_end - timedelta(
                    days=days_in_period),
                "source_period_end": source_end,
                "source_published_at": pd.Timestamp(available),
                "source_available_at": pd.Timestamp(available),
                "value_origin": "derived",
                "missing_reason": "",
                "provenance_id": f"rt:{borrower_id}:{slot}",
                "observation_age_days": age,
                **ratio_values,
                # The source's own figure, where it differs from the derived
                # one. Only supplied for the ratios a bank typically publishes.
                **{f"{name}_source_value": (
                    (ratio_values[name] * float(
                        _rng("srcval", borrower_id, slot, name).normal(1.0, 0.02)))
                    if ratio_values.get(name) is not None else None)
                   for name in ("dscr", "current_ratio", "interest_coverage_ratio",
                                "net_debt_to_ebitda", "debt_to_equity",
                                "liquidity_ratio")},
            })

            for question_id, answer_field, question_text in F.QUALITATIVE_QUESTIONS:
                qr = _rng("qual", borrower_id, slot, question_id)
                answered = qr.random() > 0.07
                # Assessments are refreshed annually; the other quarters carry
                # the last answer forward.
                is_review_quarter = cal.parse(slot).quarter == 1
                qualitative_rows.append({
                    **base,
                    "borrower_id": borrower_id,
                    "question_id": question_id,
                    "question_text": question_text,
                    "answer_value": (
                        _qualitative_grade(qr, quality[borrower_id][slot])
                        if answered else None),
                    "answer_text": "",
                    "answer_version": "1",
                    "answer_status": ("observed" if answered and is_review_quarter
                                      else "carried_forward" if answered
                                      else "not_answered"),
                    "assessor_reference": "REDACTED",
                    "assessment_date": (end - timedelta(days=30)
                                        if answered else None),
                    "source_record_id": f"QL-{borrower_id}-{slot}-{question_id}",
                    "value_origin": "synthetic_demo",
                    "missing_reason": "" if answered else "not_collected",
                    "provenance_id": f"ql:{borrower_id}:{slot}:{question_id}",
                    "observation_age_days": 30 if answered else None,
                })

        # ---- facility positions -------------------------------------
        for position in positions:
            if not (position.origination_index <= index < position.maturity_index):
                continue
            borrower = by_id[position.borrower_id]
            key = f"{position.facility_id}:{position.position_id}"
            r = _rng("facility-state", key, slot)
            q = quality[position.borrower_id][slot]

            drawn = position.limit * float(np.clip(
                position.utilisation + r.normal(0, 0.04), 0.02, 1.0))
            undrawn = max(position.limit - drawn, 0.0)
            accrued = drawn * 0.012
            gross_carrying = drawn + accrued
            ccf = float(np.clip(r.normal(0.42, 0.10), 0.05, 0.95))
            ead = drawn + undrawn * ccf
            eir = float(np.clip(r.normal(0.094, 0.016), 0.04, 0.19)) / 4.0

            pd_pit = pd_pit_12m_of(q, cycle[slot])
            pd_ttc = pd_ttc_12m_of(q)
            remaining_q = max(position.maturity_index - index, 1)
            years = max(1, int(np.ceil(remaining_q / 4.0)))
            pd_pit_life = float(cumulative_pd(hazard_curve(pd_pit, years))[-1])
            pd_ttc_life = float(cumulative_pd(hazard_curve(pd_ttc, years))[-1])
            if key not in origination_pd:
                origination_pd[key] = (pd_pit, pd_pit_life, pd_ttc, pd_ttc_life)
            o_pit, o_pit_life, o_ttc, o_ttc_life = origination_pd[key]

            dpd = int(max(0, r.poisson(2.0) * (3 if q < -0.9 else 1)
                          - (2 if q > 0.5 else 0)))
            defaulted = bool(q < -2.0 and r.random() < 0.35)
            stage, sicr, sicr_reason = stage_of(
                pd_now=pd_pit, pd_origination=o_pit, days_past_due=dpd,
                defaulted=defaulted)
            lgd_pit = float(np.clip(position.lgd_base - 0.05 * cycle[slot]
                                    + r.normal(0, 0.03), 0.05, 0.95))
            lgd_ttc = float(np.clip(position.lgd_base, 0.05, 0.95))

            weighted_ecl = 0.0
            twelve_month_ecl = 0.0
            lifetime_ecl = 0.0
            for scenario in F.MACRO_SCENARIOS:
                measured = measure_ecl(
                    pd_pit_12m=pd_pit, pd_lifetime=pd_pit_life, lgd=lgd_pit,
                    ead=ead, remaining_quarters=remaining_q, eir=eir,
                    stage=stage, scenario=scenario)
                weight = SCENARIO_WEIGHT[scenario]
                weighted_ecl += weight * measured["ecl"]
                twelve = measure_ecl(
                    pd_pit_12m=pd_pit, pd_lifetime=pd_pit_life, lgd=lgd_pit,
                    ead=ead, remaining_quarters=remaining_q, eir=eir,
                    stage=1, scenario=scenario)
                life = measure_ecl(
                    pd_pit_12m=pd_pit, pd_lifetime=pd_pit_life, lgd=lgd_pit,
                    ead=ead, remaining_quarters=remaining_q, eir=eir,
                    stage=2, scenario=scenario)
                twelve_month_ecl += weight * twelve["ecl"]
                lifetime_ecl += weight * life["ecl"]
                for term in measured["rows"]:
                    ifrs9_rows.append({
                        **base,
                        "facility_id": position.facility_id,
                        "position_id": position.position_id,
                        "ifrs9_run_id": f"RUN-{slot}",
                        "scenario_id": scenario,
                        "scenario_name": scenario.capitalize(),
                        "scenario_weight": weight,
                        "scenario_ecl": measured["ecl"],
                        "scenario_pd_pit_12m": measured["pd_12m"],
                        "scenario_pd_pit_lifetime": measured["pd_lifetime"],
                        "scenario_lgd": measured["lgd"],
                        "scenario_ead": ead,
                        "term_horizon_end_date": end + timedelta(
                            days=365 * (term["term_horizon_index"] + 1)),
                        "source_record_id":
                            f"IF9-{key}-{slot}-{scenario}-"
                            f"{term['term_horizon_index']}",
                        "value_origin": "synthetic_demo", "missing_reason": "",
                        "provenance_id": f"if9:{key}:{slot}:{scenario}",
                        "observation_age_days": 0,
                        **term,
                    })
            overlay = weighted_ecl * (0.06 if r.random() < 0.30 else 0.0)

            # collateral roll-up for this position, from the allocation rows
            linked = allocations_by_position.get(
                (position.facility_id, position.position_id, slot), [])
            summary: dict[str, Any] = {}
            for kind in F.COLLATERAL_TYPES:
                mine = [a for a in linked if a["type"] == kind]
                valued = [a for a in mine if not a["missing"]]
                weights = sum(a["eligible"] for a in valued)
                summary[f"{kind}_asset_count"] = len(mine)
                summary[f"{kind}_gross_value_rcy"] = sum(
                    a["gross_rcy"] for a in mine)
                summary[f"{kind}_allocated_gross_value_rcy"] = sum(
                    a["allocated_gross"] for a in mine)
                summary[f"{kind}_haircut_weighted"] = (
                    sum(a["haircut"] * a["eligible"] for a in valued) / weights
                    if weights > 0 else None)
                summary[f"{kind}_haircut_amount_rcy"] = sum(
                    a["haircut_amount"] for a in mine)
                summary[f"{kind}_net_value_rcy"] = sum(a["net"] for a in mine)
                summary[f"{kind}_allocated_net_value_rcy"] = sum(
                    a["allocated_net"] for a in mine)
                summary[f"{kind}_valuation_missing_rate"] = (
                    sum(a["missing"] for a in mine) / len(mine) if mine else None)
                summary[f"{kind}_overdue_valuation_count"] = int(sum(
                    a["overdue"] for a in mine))
            allocated_net_total = sum(a["allocated_net"] for a in linked)

            facility_rows.append({
                **base,
                "facility_id": position.facility_id,
                "position_id": position.position_id,
                "borrower_id": position.borrower_id,
                "borrower_name": borrower.name,
                "borrower_group_id": borrower.group_id,
                "sector_code": borrower.sector_code,
                "sector_name": borrower.sector_name,
                "country_code": borrower.country_code,
                "portfolio_id": borrower.portfolio_id,
                "product_type": position.product_type,
                "facility_status": ("defaulted" if defaulted else "active"),
                "origination_date": cal.parse(
                    calendar.slots[max(position.origination_index, 0)]
                    if position.origination_index >= 0
                    else calendar.slots[0]).end_date + timedelta(
                        days=(0 if position.origination_index >= 0
                              else 90 * position.origination_index)),
                "maturity_date": end + timedelta(days=91 * remaining_q),
                "remaining_maturity_months": remaining_q * 3.0,
                "approved_limit": position.limit,
                "drawn_balance": drawn, "undrawn_balance": undrawn,
                "gross_carrying_amount": gross_carrying,
                "accrued_interest": accrued,
                "accrued_interest_in_balance": True,
                "ead_reported": ead, "ead_pit": ead,
                "ead_ttc": None, "ccf_pit": ccf, "ccf_ttc": None,
                "pd_pit_12m": pd_pit, "pd_pit_lifetime": pd_pit_life,
                "pd_ttc_12m": pd_ttc, "pd_ttc_lifetime": pd_ttc_life,
                "pd_pit_12m_at_origination": o_pit,
                "pd_pit_lifetime_at_origination": o_pit_life,
                "pd_ttc_12m_at_origination": o_ttc,
                "pd_ttc_lifetime_at_origination": o_ttc_life,
                "pd_lifetime_horizon_months": remaining_q * 3.0,
                "pd_definition_id": "demo-pd-v1",
                "pd_parameter_version": "1.0",
                "lgd_pit": lgd_pit, "lgd_ttc": lgd_ttc, "lgd_downturn": None,
                "lgd_definition_id": "demo-lgd-v1",
                "ead_definition_id": "demo-ead-v1",
                "ifrs9_stage": stage,
                "stage_reason_recorded": sicr_reason,
                "sicr_flag": sicr, "sicr_reason_recorded": sicr_reason,
                "default_flag": defaulted,
                "default_date": end if defaulted else None,
                "days_past_due": dpd,
                "effective_interest_rate": eir * 4.0,
                "ecl_12m_reported": twelve_month_ecl,
                "ecl_lifetime_reported": lifetime_ecl,
                "ecl_reported": weighted_ecl + overlay,
                "ecl_modelled": weighted_ecl,
                "ecl_overlay": overlay,
                "ecl_coverage_ratio": ((weighted_ecl + overlay) / gross_carrying
                                       if gross_carrying else None),
                "ecl_coverage_denominator": "gross_carrying_amount",
                "ifrs9_run_id": f"RUN-{slot}",
                "ifrs9_model_version": "demo-ifrs9-v1",
                "ifrs9_input_coverage_status": "reconstructable",
                "collateral_total_gross_value_rcy": sum(
                    a["gross_rcy"] for a in linked),
                "collateral_total_allocated_gross_value_rcy": sum(
                    a["allocated_gross"] for a in linked),
                "collateral_total_allocated_net_value_rcy": allocated_net_total,
                "collateral_haircut_weighting_base":
                    "eligible_value_before_haircut",
                "collateral_coverage_ratio": (allocated_net_total / ead
                                              if ead else None),
                "collateral_coverage_denominator": "ead_reported",
                "allocation_coverage_status": "all_allocated",
                "source_record_id": key,
                "source_period_start": end - timedelta(days=91),
                "source_period_end": end,
                "source_published_at": cutoff,
                "source_available_at": cutoff,
                "value_origin": "synthetic_demo", "missing_reason": "",
                "provenance_id": f"fq:{key}:{slot}",
                "observation_age_days": 0,
                **summary,
            })

        # ---- covenants ----------------------------------------------
        for borrower_id in sorted(live_borrowers):
            borrower = by_id[borrower_id]
            rating_row = rating_rows[-1]
            ratio_row = next(row for row in reversed(rating_rows)
                             if row["borrower_id"] == borrower_id
                             and row["reporting_quarter"] == slot)
            cr = _rng("covenants", borrower_id)
            terms = [
                ("dscr", ">=", float(np.clip(cr.normal(1.25, 0.12), 1.0, 1.7)),
                 "times"),
                ("net_debt_to_ebitda", "<=",
                 float(np.clip(cr.normal(3.5, 0.7), 1.8, 6.0)), "times"),
                ("current_ratio", ">=",
                 float(np.clip(cr.normal(1.15, 0.12), 0.8, 1.7)), "times"),
                ("interest_coverage_ratio", ">=",
                 float(np.clip(cr.normal(2.4, 0.6), 1.1, 4.5)), "times"),
            ]
            facility_for = next(
                (p for p in positions if p.borrower_id == borrower_id
                 and p.origination_index <= index < p.maturity_index), None)
            for n, (metric, operator, threshold, unit) in enumerate(terms):
                tr = _rng("covenant-test", borrower_id, slot, metric)
                if tr.random() < 0.25:
                    continue                      # not every borrower has all four
                observed = ratio_row.get(metric)
                status_of_ratio = ratio_row.get(f"{metric}_status")
                due = cal.parse(slot).quarter in (2, 4)
                borrower_wide = n < 2
                if observed is None or status_of_ratio in (
                        "unavailable", "invalid_denominator"):
                    status, headroom = "unavailable", None
                elif not due:
                    status, headroom = "not_tested", None
                else:
                    if operator == ">=":
                        headroom = float(observed) - threshold
                    else:
                        headroom = threshold - float(observed)
                    status = "compliant" if headroom >= 0 else "breached"
                waived = bool(status == "breached" and tr.random() < 0.35)
                if waived:
                    status = "waived"
                covenant_rows.append({
                    **base,
                    "covenant_id": f"CV-{borrower_id}-{metric}",
                    "borrower_id": borrower_id,
                    "facility_id": (None if borrower_wide else
                                    (facility_for.facility_id
                                     if facility_for else None)),
                    "binding_scope": "borrower" if borrower_wide else "facility",
                    "covenant_name": metric.replace("_", " ").title(),
                    "covenant_type": "financial",
                    "metric_name": metric,
                    "metric_definition_id": "cockpit-40-ratio-v1",
                    "contract_reference": f"FA-{borrower_id}-2021",
                    "effective_date": cal.parse(calendar.slots[0]).end_date,
                    "expiry_date": cal.parse(calendar.slots[-1]).end_date
                    + timedelta(days=730),
                    "test_frequency": "semiannual",
                    "test_due_date": end,
                    "test_period_start": end - timedelta(days=182),
                    "test_period_end": end,
                    "comparison_operator": operator,
                    "threshold_value": threshold,
                    "threshold_lower": None, "threshold_upper": None,
                    "threshold_unit": unit,
                    # An observation is published only where the test was
                    # actually performed. A covenant whose input ratio was
                    # unusable carries no value at all: publishing the number
                    # beside "unavailable" invites it to be read as the test.
                    "observed_value": (
                        float(observed)
                        if status in ("compliant", "breached", "waived")
                        else None),
                    "observed_text": "",
                    "test_status": status,
                    "headroom_value": headroom,
                    "headroom_unit": unit,
                    "breach_date": end if status == "breached" else None,
                    "breach_reason_recorded": (
                        "covenant metric below contractual threshold"
                        if status == "breached" else ""),
                    "waiver_flag": waived,
                    "waiver_date": end if waived else None,
                    "waiver_expiry_date": (end + timedelta(days=180)
                                           if waived else None),
                    "cure_deadline": (end + timedelta(days=60)
                                      if status == "breached" else None),
                    "cure_status": ("in_progress" if status == "breached"
                                    else "not_required"),
                    "evidence_reference": f"CT-{borrower_id}-{slot}-{metric}",
                    "test_version": "1",
                    "test_missing_reason": (
                        "test not due this quarter" if status == "not_tested"
                        else "input ratio unavailable"
                        if status == "unavailable" else ""),
                    "source_record_id": f"CV-{borrower_id}-{metric}-{slot}",
                    "source_period_start": end - timedelta(days=182),
                    "source_period_end": end,
                    "source_published_at": cutoff,
                    "source_available_at": cutoff,
                    "value_origin": "synthetic_demo", "missing_reason": "",
                    "provenance_id": f"cv:{borrower_id}:{metric}:{slot}",
                    "observation_age_days": 0,
                })

        calendar_rows.append({
            **keys(slot), "slot_index": index, "is_populated": True,
            "facility_row_count": sum(
                1 for row in facility_rows if row["reporting_quarter"] == slot),
            "borrower_row_count": len(live_borrowers),
            "coverage_note": "",
            "value_origin": "synthetic_demo", "missing_reason": "",
            "provenance_id": f"cal:{slot}", "source_record_id": slot,
            "observation_age_days": 0,
        })

    window = macro_rows(calendar, actual)
    return Release(
        dataset_release_id=dataset_release_id,
        calendar=calendar,
        frames={
            F.CALENDAR: pd.DataFrame(calendar_rows),
            F.FACILITY_QUARTER: pd.DataFrame(facility_rows),
            F.IFRS9_DETAIL: pd.DataFrame(ifrs9_rows),
            F.BORROWER_FINANCIAL: pd.DataFrame(financial_rows),
            F.RATING_RATIO: pd.DataFrame(rating_rows),
            F.QUALITATIVE: pd.DataFrame(qualitative_rows),
            F.COLLATERAL: pd.DataFrame(collateral_rows),
            F.COLLATERAL_ALLOCATION: pd.DataFrame(allocation_rows),
            F.COVENANT: pd.DataFrame(covenant_rows),
            F.MACRO_WINDOW: window,
            "cockpit_macro_pivot": macro_pivot(window),
        })


@dataclass
class Release:
    """One built release, in memory."""

    dataset_release_id: str
    calendar: cal.Calendar
    frames: dict[str, pd.DataFrame]

    def summary(self) -> dict[str, Any]:
        return {
            "dataset_release_id": self.dataset_release_id,
            "domain_id": DOMAIN,
            "origin": ORIGIN,
            "data_version": DATA_VERSION,
            "not_client_data": NOT_CLIENT_DATA,
            "reporting_quarters": list(self.calendar.slots),
            "populated_quarters": list(self.calendar.populated),
            "missing_quarters": list(self.calendar.missing),
            "reporting_currency": REPORTING_CURRENCY,
            "amount_scale": AMOUNT_SCALE,
            "rows": {name: int(len(frame))
                     for name, frame in self.frames.items()},
            "columns": {name: int(frame.shape[1])
                        for name, frame in self.frames.items()},
        }


__all__ = ["AMOUNT_SCALE", "Asset", "Borrower", "DEFAULT_BORROWERS",
           "DEFAULT_FACILITIES", "Facility", "GEOGRAPHY", "MASTER_SEED",
           "REPORTING_CURRENCY", "Release", "SCENARIO_WEIGHT", "TENANT",
           "build_population", "build_release", "cumulative_pd",
           "hazard_curve", "macro_pivot", "macro_rows", "measure_ecl",
           "pd_pit_12m_of", "pd_ttc_12m_of", "quality_path", "rating_of",
           "ratios_for", "stage_of", "statement_path"]


# --------------------------------------------------------- schema conformance

def conform(release: Release) -> dict[str, Any]:
    """Make every frame carry exactly its declared columns, and report the gap.

    Two directions, and they are not symmetric:

    * A DECLARED column the generator did not populate is ADDED as null. The
      catalog promises it and a query naming it must bind rather than fail with
      an unresolved column; its availability says it is unpopulated. Section 4:
      schema existence is not population, and the honest form of that is a
      column that exists and is empty, not a column that is missing.
    * An UNDECLARED column is REMOVED. The allowlist is the dictionary, and a
      column the catalog does not declare must not be reachable through a view
      -- section 3.4.
    """
    report: dict[str, Any] = {"added_as_null": {}, "removed_undeclared": {}}
    for relation, frame in release.frames.items():
        declared = [s.name for s in F.fields_of(relation)]
        if relation == "cockpit_macro_pivot":
            declared = (["reporting_quarter", "country_or_region",
                         "scenario_id"]
                        + [s.name for s in F.MACRO_PIVOT_FIELDS])
        present = list(frame.columns)
        missing = [c for c in declared if c not in present]
        extra = [c for c in present if c not in declared]
        for column in missing:
            frame[column] = None
        if extra:
            frame = frame.drop(columns=extra)
        release.frames[relation] = frame[declared]
        if missing:
            report["added_as_null"][relation] = missing
        if extra:
            report["removed_undeclared"][relation] = extra
    return report
