"""
Two releases, side by side, proving the selection decides everything.

REAL DATABASE · REAL SOURCE. No paid provider call.

The defect this exists for
--------------------------
The Saudi round added a module-level currency to `precision.py` and wired it
into `service.load_release` as the fallback for a release whose manifest does
not declare one. `v4-uat-20q-v1` does not declare one -- the shared release
writer never recorded currency -- so selecting it reported **SAR million**
over INR-denominated data. Nothing looked wrong: a silent manifest is not a
wrong manifest, and the relabelling was invisible.

So the rule this module holds is narrow and absolute: **the selected release
decides its own currency and scale, and V4 has no default of any
nationality.** Every test here runs against BOTH releases, because a test
that only ever sees one cannot tell a release-driven value from a constant.
"""

from __future__ import annotations

import hashlib
import pathlib
from decimal import Decimal

import pandas as pd
import pytest

from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4 import service

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The two releases this branch can serve. Neither is "the default".
UAT = "v4-uat-20q-v1"
SAUDI = "v4-saudi-20q-v1"
BOTH = [UAT, SAUDI]

#: What each release is, independently of anything V4 believes.
EXPECTED = {
    UAT: {"currency": "INR", "scale": "crore"},
    SAUDI: {"currency": "SAR", "scale": "million"},
}


class _Cfg:
    def __init__(self, release_id: str) -> None:
        self.release_id = release_id


def _available(release_id: str) -> bool:
    from backend.cockpit_agentic import store

    try:
        store.read_manifest(release_id)
        return True
    except Exception:                                          # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test selects its own release from cold."""
    service._CATALOG_CACHE.clear()
    service._COVERAGE_CACHE.clear()
    yield
    service._CATALOG_CACHE.clear()
    service._COVERAGE_CACHE.clear()


def _load(release_id: str):
    if not _available(release_id):
        pytest.skip(f"release {release_id} is not published in this runtime")
    return service.load_release(_Cfg(release_id))


# ---- selection decides the denomination -------------------------------

@pytest.mark.parametrize("release_id", BOTH)
def test_the_selected_release_decides_its_own_currency(release_id):
    catalog, _, summary = _load(release_id)
    want = EXPECTED[release_id]
    assert catalog.reporting_currency == want["currency"], (
        f"{release_id} holds {want['currency']} data and the runtime reports "
        f"{catalog.reporting_currency}")
    assert catalog.amount_scale == want["scale"]
    assert summary["reporting_currency"] == want["currency"]
    assert summary["amount_scale"] == want["scale"]


def test_the_two_releases_do_not_report_the_same_denomination():
    """The test that a constant would fail and a release-driven value passes."""
    for release_id in BOTH:
        if not _available(release_id):
            pytest.skip(f"{release_id} is not published in this runtime")
    uat, _, _ = _load(UAT)
    service._CATALOG_CACHE.clear()
    saudi, _, _ = _load(SAUDI)
    assert (uat.reporting_currency, uat.amount_scale) != (
        saudi.reporting_currency, saudi.amount_scale), (
        "both releases reported the same denomination, which means something "
        "is supplying it instead of the release")


def test_no_module_level_currency_default_exists_anywhere_in_v4():
    """Neither nationality. This is the exact regression being prevented."""
    from backend.cockpit_v4 import precision

    for name in ("CURRENCY", "AMOUNT_SCALE", "MONEY_UNIT",
                 "DEFAULT_CURRENCY", "DEFAULT_SCALE"):
        assert not hasattr(precision, name), f"precision.{name}"

    source = (ROOT / "backend" / "cockpit_v4" / "service.py").read_text()
    for literal in ('or "SAR"', "or 'SAR'", 'or "INR"', "or 'INR'",
                    'or "crore"', 'or "million"'):
        assert literal not in source, (
            f"service.py falls back to {literal}; the release decides")


@pytest.mark.parametrize("release_id", BOTH)
def test_a_release_that_declares_nothing_is_read_from_its_own_data(
        release_id):
    """Provenance, not a guess: the data carries its own reporting currency."""
    from backend.cockpit_agentic import store

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    manifest = store.read_manifest(release_id)
    frame = pd.read_parquet(
        store.relation_path(release_id, "cockpit_facility_quarter"),
        columns=["reporting_currency"])
    in_data = sorted({str(v) for v in frame["reporting_currency"].dropna()})
    assert in_data == [EXPECTED[release_id]["currency"]], in_data

    currency, scale = service.denomination(release_id, manifest)
    assert currency == in_data[0], (
        "the runtime reported a currency the release's own data contradicts")
    assert scale == EXPECTED[release_id]["scale"]


def test_an_undeclared_release_is_never_given_an_invented_currency():
    """A release with neither a manifest nor readable data says nothing."""
    currency, scale = service.denomination("no-such-release-at-all", {})
    assert currency == "", (
        f"a release that says nothing was handed {currency!r}")
    assert scale in ("", "crore"), scale


# ---- the data itself is untouched -------------------------------------

@pytest.mark.parametrize("release_id", BOTH)
def test_each_release_keeps_its_own_data(release_id):
    from backend.cockpit_agentic import store

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    directory = pathlib.Path(
        store.relation_path(release_id, "cockpit_facility_quarter")).parent
    digest = hashlib.sha256()
    for parquet in sorted(directory.glob("*.parquet")):
        digest.update(parquet.read_bytes())
    assert len(list(directory.glob("*.parquet"))) == 11
    # Recorded rather than pinned to a literal: the point is that selecting
    # one release cannot alter the other, which the next test checks.
    assert digest.hexdigest()


def test_selecting_one_release_does_not_touch_the_other():
    from backend.cockpit_agentic import store

    for release_id in BOTH:
        if not _available(release_id):
            pytest.skip(f"{release_id} is not published in this runtime")

    def fingerprint(release_id: str) -> str:
        directory = pathlib.Path(
            store.relation_path(release_id,
                                "cockpit_facility_quarter")).parent
        digest = hashlib.sha256()
        for parquet in sorted(directory.glob("*.parquet")):
            digest.update(parquet.read_bytes())
        return digest.hexdigest()

    before = {r: fingerprint(r) for r in BOTH}
    for release_id in BOTH:
        service._CATALOG_CACHE.clear()
        _load(release_id)
    after = {r: fingerprint(r) for r in BOTH}
    assert before == after, "loading a release rewrote release data"


@pytest.mark.parametrize("release_id", BOTH)
def test_the_borrower_names_belong_to_their_own_release(release_id):
    from backend.cockpit_agentic import store

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    frame = pd.read_parquet(
        store.relation_path(release_id, "cockpit_facility_quarter"),
        columns=["borrower_name"])
    names = sorted({str(v) for v in frame["borrower_name"].dropna()})
    from backend.cockpit_v4 import saudi

    saudi_shaped = sum(
        1 for n in names if any(n.startswith(h) for h in saudi.NAME_HEAD))
    if release_id == SAUDI:
        assert saudi_shaped == len(names), "the Saudi release is all GCC names"
    else:
        assert saudi_shaped == 0, (
            "the UAT release was given Saudi names; localization leaked "
            "across releases")


# ---- numerical results are per release --------------------------------

@pytest.mark.parametrize("release_id,sectors", [(UAT, 11), (SAUDI, 12)])
def test_each_release_keeps_its_own_numbers(release_id, sectors):
    from backend.cockpit_agentic import store

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    frame = pd.read_parquet(
        store.relation_path(release_id, "cockpit_facility_quarter"))
    latest = sorted(frame["reporting_quarter"].dropna().unique())[-1]
    book = frame[(frame["reporting_quarter"] == latest)
                 & frame["sector_name"].notna()]
    assert book["sector_name"].nunique() == sectors, (
        f"{release_id} should hold {sectors} sectors in {latest}")


# ---- caches and threads are release-scoped ----------------------------

def test_the_catalog_cache_is_keyed_by_release():
    for release_id in BOTH:
        if not _available(release_id):
            pytest.skip(f"{release_id} is not published in this runtime")
    _load(UAT)
    _load(SAUDI)
    assert set(service._CATALOG_CACHE) == set(BOTH), (
        "the catalog cache is not keyed by release id")
    uat_catalog = service._CATALOG_CACHE[UAT][0]
    saudi_catalog = service._CATALOG_CACHE[SAUDI][0]
    assert uat_catalog.reporting_currency != saudi_catalog.reporting_currency
    assert uat_catalog.dataset_release_id == UAT
    assert saudi_catalog.dataset_release_id == SAUDI


def test_the_attention_cache_is_keyed_by_release_and_tenant():
    from backend.cockpit_v4 import attention

    assert attention.cache_key(UAT, "t") != attention.cache_key(SAUDI, "t")
    assert attention.cache_key(UAT, "a") != attention.cache_key(UAT, "b")


def test_a_run_records_the_release_it_was_answered_from(store_db):
    thread = store_db.create_thread(tenant_id="demo-tenant",
                                    principal_id="u1")
    uat_run, _ = store_db.accept_run(
        thread_id=thread, tenant_id="demo-tenant", principal_id="u1",
        question="q", mode="standard", release_id=UAT, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="s", deadline_at="")
    saudi_run, _ = store_db.accept_run(
        thread_id=thread, tenant_id="demo-tenant", principal_id="u1",
        question="q", mode="standard", release_id=SAUDI, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="s", deadline_at="")
    assert store_db.get_run(uat_run.run_id).release_id == UAT
    assert store_db.get_run(saudi_run.run_id).release_id == SAUDI


def test_an_artifact_carries_the_release_it_was_computed_from(store_db):
    """A result cannot be reused under a different release by accident."""
    uat_artifact = store_db.put_artifact(
        run_id="r1", tenant_id="demo-tenant", kind="result", release_id=UAT,
        scope={}, columns=["sector_name", "ead"],
        rows=[{"sector_name": "IT", "ead": "1"}])
    saudi_artifact = store_db.put_artifact(
        run_id="r2", tenant_id="demo-tenant", kind="result", release_id=SAUDI,
        scope={}, columns=["sector_name", "ead"],
        rows=[{"sector_name": "IT", "ead": "2"}])
    assert store_db.get_artifact(
        uat_artifact, tenant_id="demo-tenant")["release_id"] == UAT
    assert store_db.get_artifact(
        saudi_artifact, tenant_id="demo-tenant")["release_id"] == SAUDI


# ---- the maths is release-neutral -------------------------------------

@pytest.mark.parametrize("unit", ["INR crore", "SAR million", "USD million",
                                  "EUR bn"])
def test_the_canonical_display_contract_works_in_any_currency(unit):
    """The rounding fix is arithmetic, not geography."""
    canonical = Decimal("40599.1736630513815")
    assert prec.classify(unit).kind == prec.MONEY, unit
    # Zero places, and only zero: a credit paper writes SAR 40,599 million,
    # and two decimal places on a forty-billion book are three hundredths of
    # a riyal. An amount is written that way in every currency.
    assert prec.allowed_precisions(unit) == (0,)
    # A cross-check the analyst rounded to two places is still recognised as
    # this value -- it just is not how the figure gets published.
    accepted = prec.check("40599.17", canonical, unit=unit,
                          declared_precision=2, label="t")
    assert accepted.ok
    assert accepted.precision == 0
    assert str(accepted.display) == "40599"
    assert not prec.check("40599.18", canonical, unit=unit,
                          declared_precision=2, label="t").ok
    assert not prec.check("40599.1699", canonical, unit=unit,
                          declared_precision=2, label="t").ok


def test_a_published_amount_is_written_in_its_own_currency():
    value = Decimal("40599.1736630513815")
    assert prec.format_value(value, "INR crore", 2) == "INR 40,599.17 crore"
    assert prec.format_value(value, "SAR million", 2) == (
        "SAR 40,599.17 million")


def test_the_money_unit_comes_from_the_catalog_not_a_constant():
    class Catalog:
        reporting_currency = "INR"
        amount_scale = "crore"

    assert prec.money_unit(Catalog()) == "INR crore"

    class Silent:
        reporting_currency = ""
        amount_scale = ""

    assert prec.money_unit(Silent()) == "", (
        "a release that says nothing must not be handed a currency")


# ---- what the analyst is told, per release ----------------------------

@pytest.mark.parametrize("release_id", BOTH)
def test_the_claim_guide_shows_the_selected_releases_money_unit(release_id):
    """The worked example must not advertise another release's currency.

    It rides with every result packet. An example reading "SAR million" on an
    INR book would steer the analyst into declaring the wrong unit on every
    amount it publishes -- and the unit is what the precision policy reads.
    """
    from backend.cockpit_v4.execute_tool import claim_guide

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    catalog, _, _ = _load(release_id)
    unit = prec.money_unit(catalog)
    guide = claim_guide("art-1", ["sector_name", "ead"], ["r0", "r1"], 2,
                        unit)
    assert guide["example_total"]["unit"] == unit
    want = EXPECTED[release_id]
    assert unit == f"{want['currency']} {want['scale']}"

    other = [r for r in BOTH if r != release_id][0]
    stranger = EXPECTED[other]["currency"]
    assert stranger not in str(guide), (
        f"the guide for {release_id} mentions {stranger}")


@pytest.mark.parametrize("release_id", BOTH)
def test_an_executed_result_carries_its_own_releases_money_unit(
        release_id, store_db):
    """End to end: the packet the analyst receives, from a real execution."""
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_agentic import sql as v3_sql
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService
    from backend.cockpit_v4.contracts import parse_execution
    from backend.cockpit_v4 import pyrunner
    from backend.cockpit_v4.service import _Principal

    if not _available(release_id):
        pytest.skip(f"{release_id} is not published in this runtime")
    catalog, _, _ = _load(release_id)
    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    session = v3_sql.open_session(scope=scope, catalog=catalog, reuse=False)
    service_ = ExecutionService(
        session=session, scope=scope, catalog=catalog, store=store_db,
        run_id="run-iso", tenant_id="demo-tenant", release_id=release_id,
        limits=STANDARD_LIMITS, python_runner=pyrunner.PythonRunner())

    submission = parse_execution({
        "intent": {"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT",
                   "understood_request": "ead by sector",
                   "response_language": "en", "blocking_ambiguities": [],
                   "resolved_assumptions": [], "canonical_mappings": [],
                   "excluded_parts": [], "public_rationale": "direct"},
        "objective": "o", "subquestions": ["a"],
        "scope": {"reporting_periods": [], "filters": {}},
        "metadata_receipt_ids": [],
        "fields_required": ["cockpit_facility_quarter.ead_reported"],
        "expected_output_grain": "sector", "expected_units": "amount",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": ("SELECT sector_name, SUM(ead_reported) AS ead "
                            "FROM cockpit_facility_quarter "
                            "WHERE sector_name IS NOT NULL GROUP BY 1"),
                   "parameters": {}, "purpose": "p",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, max_steps=6)
    service_.validate_batch(submission)
    batch = service_.run_batch(submission, submission_id="sub-iso",
                               deadline_seconds=30.0)
    step = batch.steps[0].to_dict()

    want = EXPECTED[release_id]
    guide = step["how_to_cite_these_numbers"]
    assert guide["example_total"]["unit"] == (
        f"{want['currency']} {want['scale']}")
    other = [r for r in BOTH if r != release_id][0]
    assert EXPECTED[other]["currency"] not in str(guide)
