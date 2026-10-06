"""
The V4 demonstration book, as a Saudi corporate portfolio.

Why this is a V4 module and not a change to the generator
---------------------------------------------------------
The synthetic data comes from the V3 corporate_cockpit generator, which V4
uses unchanged on purpose: the domain is preserved and only the orchestration
differs. That generator is shared, so localizing V4 by editing it would
change V3's releases too. Everything here therefore operates on a release
AFTER it is built, and nothing in this file is imported by V3.

Why nothing is FX-converted
---------------------------
Multiplying synthetic rupees by an exchange rate would manufacture economic
meaning that was never there -- a portfolio that looks like it was translated
from somewhere else, with an implied rate a reader could ask about and nobody
could defend. The amounts are fictional to begin with, so they are simply
READ as Saudi amounts: the same numbers, denominated in SAR million, which is
a credible scale for a mid-size Saudi corporate book (the demonstration book
totals about SAR 20.7 billion of exposure).

The release stays fictional and says so. `not_client_data` is carried through
untouched.

Determinism
-----------
A borrower's name is a pure function of its id, so reseeding the same release
produces byte-identical data. The generator's own reproducibility guarantee
is not weakened by localizing on top of it.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd

#: What the V4 book is denominated in, in one place.
CURRENCY = "SAR"
AMOUNT_SCALE = "million"
GEOGRAPHY = "SA"
GEOGRAPHY_NAME = "Saudi Arabia"

#: The currency codes the generator emits, and what each becomes here. A
#: foreign-currency facility stays foreign -- a book with no FX exposure at
#: all would be a less useful demonstration, and USD is as plausible in
#: Riyadh as anywhere.
CURRENCY_MAP = {"INR": CURRENCY}

#: Fictional corporate names in a Saudi and wider GCC register. Every one is
#: invented. None is intended to resemble a real company, and the pairing is
#: deliberately generic -- a place or a descriptor with a line of business --
#: so that no combination reads as a particular firm.
NAME_HEAD: tuple[str, ...] = (
    "Al Noor", "Riyadh", "Najd", "Red Sea", "Eastern Province", "Al Faisal",
    "Arabian Gulf", "Desert Gate", "Jeddah", "Dammam", "Al Rabia",
    "Hail Valley", "Qassim", "Tabuk", "Al Waha", "Gulf Horizon",
    "Al Khobar", "Yanbu", "Jubail", "Asir Highland", "Al Madina",
    "Northern Frontier", "Al Kharj", "Rub Al Khali", "Sharqiyah",
    "Al Buraida", "Taif", "Al Ahsa",
)
NAME_TAIL: tuple[str, ...] = (
    "Industrial Company", "Contracting", "Holding", "Logistics",
    "Trading Company", "Petrochemical Industries", "Steel Works",
    "Power Company", "Real Estate Development", "Food Industries",
    "Technology Services", "Properties", "Mining Company",
    "Agricultural Company", "Motors", "Cement Company",
)


def borrower_name(borrower_id: str) -> str:
    """A stable fictional name for a borrower id.

    Derived by hash rather than by counter so that adding a borrower does not
    rename every other one, which is the same property the generator's own
    named random streams give it.
    """
    digest = hashlib.sha256(f"v4-saudi::{borrower_id}".encode()).digest()
    head = NAME_HEAD[int.from_bytes(digest[:4], "big") % len(NAME_HEAD)]
    tail = NAME_TAIL[int.from_bytes(digest[4:8], "big") % len(NAME_TAIL)]
    return f"{head} {tail}"


def _rename_borrowers(frame: pd.DataFrame) -> pd.DataFrame:
    if "borrower_name" not in frame.columns:
        return frame
    if "borrower_id" not in frame.columns:
        return frame
    frame = frame.copy()
    unique = {str(b): borrower_name(str(b))
              for b in frame["borrower_id"].dropna().unique()}
    frame["borrower_name"] = frame["borrower_id"].map(
        lambda b: unique.get(str(b), ""))
    return frame


def _restate_currency(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in ("currency_code", "reporting_currency",
                           "collateral_currency") if c in frame.columns]
    if not columns:
        return frame
    frame = frame.copy()
    for column in columns:
        frame[column] = frame[column].map(
            lambda v: CURRENCY_MAP.get(str(v), v) if pd.notna(v) else v)
    return frame


def _restate_geography(frame: pd.DataFrame) -> pd.DataFrame:
    for column in ("country_code",):
        if column in frame.columns:
            frame = frame.copy()
            frame[column] = frame[column].map(
                lambda v: GEOGRAPHY if str(v) == "IN" else v)
    for column in ("country_or_region",):
        if column in frame.columns:
            frame = frame.copy()
            frame[column] = frame[column].map(
                lambda v: GEOGRAPHY_NAME if str(v) in ("IN", "India") else v)
    return frame


def localize(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Read a built release as a Saudi book. No amount is changed."""
    out: dict[str, pd.DataFrame] = {}
    for relation, frame in frames.items():
        frame = _rename_borrowers(frame)
        frame = _restate_currency(frame)
        frame = _restate_geography(frame)
        out[relation] = frame
    return out


def manifest_overrides() -> dict[str, Any]:
    """What the published manifest must say about money and place.

    `service.load_release` reads these and falls back to the generator's own
    values when they are absent, which is how a V4 runtime was reporting INR
    crore for a Saudi demonstration.
    """
    return {
        "reporting_currency": CURRENCY,
        "amount_scale": AMOUNT_SCALE,
        "geography": GEOGRAPHY,
        "geography_name": GEOGRAPHY_NAME,
        "localization": "cockpit_v4.saudi",
        "amounts_converted": False,
        "localization_note": (
            "Synthetic amounts are READ as Saudi amounts in SAR million. No "
            "FX conversion was applied: applying a rate to fictional figures "
            "would manufacture economic meaning that was never in them."),
    }


def audit(frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """What a localized release should contain, and what it must not."""
    leaks: list[str] = []
    names: set[str] = set()
    currencies: set[str] = set()
    countries: set[str] = set()
    for relation, frame in frames.items():
        if "borrower_name" in frame.columns:
            names.update(str(v) for v in frame["borrower_name"].dropna())
        for column in ("currency_code", "reporting_currency",
                       "collateral_currency"):
            if column in frame.columns:
                currencies.update(str(v) for v in frame[column].dropna())
        for column in ("country_code", "country_or_region"):
            if column in frame.columns:
                countries.update(str(v) for v in frame[column].dropna())
        for column in frame.columns:
            if frame[column].dtype == object:
                text = " ".join(
                    str(v) for v in frame[column].dropna().unique()[:2000])
                for word in ("INR", "crore", "₹"):
                    if word in text:
                        leaks.append(f"{relation}.{column} contains {word!r}")
    return {"borrower_names": sorted(names), "currencies": sorted(currencies),
            "countries": sorted(countries), "leaks": sorted(set(leaks))}


__all__ = ["AMOUNT_SCALE", "CURRENCY", "CURRENCY_MAP", "GEOGRAPHY",
           "GEOGRAPHY_NAME", "NAME_HEAD", "NAME_TAIL", "audit",
           "borrower_name", "localize", "manifest_overrides"]
