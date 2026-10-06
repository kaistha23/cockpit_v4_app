"""MODEL MOCK · REAL DATABASE/RUNNER · REAL SOURCE · UNIT.

The Cockpit V4 demonstration is Saudi, everywhere a reader looks.

This is a product rule, not a cosmetic one: the demonstration book is a Saudi
corporate portfolio in SAR million, and an Indian money label anywhere in the
V4 experience is a defect. The rule is enforced at the surface a reader
actually reaches -- the default release, the data in it, the answers built
from it, the dashboards, the product knowledge and the evidence artefacts --
rather than by a string replacement that would leave the runtime free to
report something else.

The two halves that matter separately:

  * NO Indian money label reaches a reader. INR, crore, lakh, the rupee sign.
  * The release SAYS what it is. Country, currency and scale come from the
    release's own metadata, so an undeclared release fails closed instead of
    being handed a nationality.
"""

from __future__ import annotations

import json
import pathlib
import re

import oracles
import pandas as pd
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_orchestration_recovery import _execution_result
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import release as rel
from backend.cockpit_v4 import states as st

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORBIDDEN = ("INR", "crore", "lakh", "₹", "Rs.", "Rs ")

#: Where a reader of the V4 demonstration actually looks.
SURFACE = (
    ROOT / "frontend" / "src" / "components" / "cockpit-v4",
    ROOT / "backend" / "cockpit_v4" / "prompts",
    ROOT / "backend" / "cockpit_v4" / "product_knowledge.json",
    ROOT / "docs" / "cockpit_v4" / "evidence",
)


def _offenders(text: str) -> list[str]:
    return [w for w in FORBIDDEN if w in text]


# ---- §2, §28: the default is Saudi -------------------------------------

def test_the_default_release_is_the_saudi_one():
    assert rel.DEFAULT_RELEASE_ID == "v4-saudi-20q-v1"


def test_the_release_declares_saudi_arabia_sar_and_million(runtime,
                                                           release_id):
    header = rel.header(release_id=release_id, catalog=runtime.catalog,
                        release_summary=runtime.release_summary)
    assert header.country == "Saudi Arabia"
    assert header.reporting_currency == "SAR"
    assert header.amount_scale == "million"
    assert header.denominated


# ---- §3: the data is Saudi --------------------------------------------

def _facility(release_id: str) -> pd.DataFrame:
    from backend.cockpit_agentic import store

    return pd.read_parquet(
        store.relation_path(release_id, "cockpit_facility_quarter"),
        columns=["sector_name", "borrower_name", "currency_code",
                 "country_code"])


def test_every_amount_in_the_book_is_in_riyals(release_id):
    frame = _facility(release_id)
    assert sorted(frame["currency_code"].dropna().unique()) == ["SAR"]


def test_every_borrower_sits_in_saudi_arabia(release_id):
    frame = _facility(release_id)
    assert sorted(frame["country_code"].dropna().unique()) == ["SA"]


def test_the_borrowers_are_saudi_and_obviously_fictional(release_id):
    """Invented names in a Saudi register, and no real company among them."""
    frame = _facility(release_id)
    names = sorted(frame["borrower_name"].dropna().unique())
    assert len(names) > 20

    from backend.cockpit_v4 import saudi

    for name in names:
        head, _, tail = name.partition(" ")
        assert any(name.startswith(h) for h in saudi.NAME_HEAD), name
        assert any(name.endswith(t) for t in saudi.NAME_TAIL), name


def test_the_sectors_are_a_saudi_corporate_book(release_id):
    """Construction, real estate, petrochemicals, utilities, logistics.

    The taxonomy is the shared generator's and is not V4's to change -- this
    asserts that what it produces IS appropriate, not that V4 rewrote it.
    """
    sectors = set(_facility(release_id)["sector_name"].dropna().unique())
    for expected in ("Construction", "Real Estate", "Manufacturing",
                     "Chemicals", "Power and Utilities",
                     "Transport and Logistics", "Wholesale Trade",
                     "Retail Trade", "Information Technology",
                     "Hospitality"):
        assert expected in sectors, f"{expected} is missing from the book"


def test_no_indian_money_label_is_anywhere_in_the_book(release_id):
    from backend.cockpit_agentic import store
    from backend.cockpit_v4 import saudi

    frames = {}
    for relation in ("cockpit_facility_quarter", "cockpit_covenant_quarter",
                     "cockpit_rating_ratio_quarter"):
        frames[relation] = pd.read_parquet(
            store.relation_path(release_id, relation))
    audit = saudi.audit(frames)
    assert audit["leaks"] == [], audit["leaks"]
    assert audit["currencies"] == ["SAR"]


# ---- §30: nothing a reader sees says rupees ----------------------------

@pytest.mark.parametrize("path", [p for p in SURFACE])
def test_no_indian_money_label_reaches_the_v4_surface(path):
    if not path.exists():
        pytest.skip(f"{path} is not present in this checkout")
    files = [path] if path.is_file() else [
        f for f in path.rglob("*")
        if f.is_file() and f.suffix in (".ts", ".tsx", ".json", ".md",
                                        ".mjs", ".txt")]
    offenders = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for word in _offenders(text):
            offenders.append(f"{file.relative_to(ROOT)}: {word}")
    assert offenders == [], "\n".join(offenders)


def test_a_published_answer_says_riyals_and_nothing_else(drive, release_id):
    quarter = oracles.latest_quarter(release_id)

    def _answer(messages):
        step = _execution_result(messages)["steps"][0]
        column = next(c for c in step["columns"] if c != "sector_name")
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="The book totals {{claim.total_ead}}.",
                  numeric_claims=[{
                      "claim_id": "total_ead", "unit": "SAR million",
                      "derivation": {"operation": "sum", "operands": [
                          {"artifact_id": step["artifact_id"],
                           "column_id": column,
                           "row_ids": list(step["row_ids"])}]}}],
                  tables=[{"title": "EAD by sector",
                           "artifact_id": step["artifact_id"],
                           "columns": ["sector_name", column]}]))])

    outcome, _, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]), _answer])

    assert outcome.state == st.COMPLETED, outcome.message
    published = json.dumps(outcome.response, default=str)
    assert _offenders(published) == []
    assert "SAR" in outcome.response["narrative"]


def test_the_attention_dashboard_is_saudi(runtime, session, release_id):
    """§31: the cards a reader opens the product on."""
    from backend.cockpit_v4 import attention
    from backend.cockpit_v4 import display as disp

    attention.clear_cache()
    feed = attention.compute(
        session=session, release_id=release_id,
        quarters=[str(q) for q in runtime.catalog.calendar.populated],
        currency=disp.money_unit(runtime.catalog))

    published = json.dumps(feed, default=str)
    assert _offenders(published) == []
    assert feed["release_id"] == release_id
    assert feed["segments_requiring_attention"], "the dashboard is empty"
    from backend.cockpit_v4 import saudi

    for card in feed["segments_requiring_attention"]:
        assert "SAR" in json.dumps(card, default=str) or card["metric"], card
    for card in feed.get("ecl_highlights", []):
        for name in (card.get("borrower_name"), ):
            if name:
                assert any(name.startswith(h) for h in saudi.NAME_HEAD), name


# ---- §5: an undeclared release is not given a nationality --------------

def test_a_release_that_declares_nothing_fails_closed():
    header = rel.header(release_id="v4-saudi-20q-v1", catalog=None,
                        release_summary={})
    assert not header.denominated
    assert "reporting_currency" in header.unverified
    assert header.reporting_currency == "", (
        "an undeclared release must not be handed a currency of any "
        "nationality, Saudi included")
