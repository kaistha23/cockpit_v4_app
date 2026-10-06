"""REAL DATABASE · NO MODEL · INDEPENDENT ORACLE.

§23, §24, §25. The stage profile and the ECL decomposition, in both books.

Every figure is recomputed here from the published Parquet with pandas: a
different engine, a different code path, and no shared helper with the module
under test. The decomposition is checked twice over -- each component against
its own independent recomputation, and the whole set against the movement it
claims to explain.
"""

from __future__ import annotations

import domain_oracles as oracle
import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import ecl as ecl_mod
from backend.cockpit_v4 import lake

TOLERANCE = 1e-6

EXPOSURE = {dom.CORPORATE: ("corp_facility_quarter", "facility_id"),
            dom.RETAIL: ("retail_account_month", "account_id")}


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    ecl_mod.clear_cache()
    yield
    arun.reset()
    ecl_mod.clear_cache()


@pytest.fixture(scope="module")
def books():
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        book = arun.for_domain(domain_id)
        scope = resolver.scope_for(domain_id)
        out[domain_id] = (book, scope)
    return out


def _months(domain_id: str) -> tuple[str, str]:
    """This book's two latest periods: quarters in Corporate, months in
    Retail. The name is historical; the values are the book's own."""
    return oracle.latest_period(domain_id), oracle.previous_period(domain_id)


def _frame(domain_id: str, month: str):
    relation, _key = EXPOSURE[domain_id]
    data = oracle.frame(domain_id, relation)
    return data[data[oracle.period_column(domain_id)] == month]


# ---- §23: the stage profile --------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_stage_profile_matches_an_independent_oracle(books, domain_id):
    book, scope = books[domain_id]
    month, comparison = _months(domain_id)
    profile = ecl_mod.stage_profile(session=book.session, scope=scope,
                                    month=month, comparison=comparison)
    rows = _frame(domain_id, month)
    prior = _frame(domain_id, comparison)

    assert profile["total_ead"] == pytest.approx(float(rows["ead_sar_mn"]
                                                       .sum()), abs=1e-4)
    assert profile["total_ecl"] == pytest.approx(float(rows["ecl_sar_mn"]
                                                       .sum()), abs=1e-4)
    assert profile["coverage"] == pytest.approx(
        float(rows["ecl_sar_mn"].sum()) / float(rows["ead_sar_mn"].sum()),
        abs=TOLERANCE)
    assert profile["ecl_movement"] == pytest.approx(
        float(rows["ecl_sar_mn"].sum()) - float(prior["ecl_sar_mn"].sum()),
        abs=1e-4)

    by_stage = {int(s["stage"]): s for s in profile["stages"]}
    for stage in ecl_mod.STAGES:
        group = rows[rows["stage"] == stage]
        entry = by_stage[stage]
        assert entry["ead"] == pytest.approx(float(group["ead_sar_mn"].sum()),
                                             abs=1e-4)
        assert entry["ecl"] == pytest.approx(float(group["ecl_sar_mn"].sum()),
                                             abs=1e-4)
        assert entry["exposures"] == len(group)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_stages_add_up_to_the_book(books, domain_id):
    book, scope = books[domain_id]
    profile = ecl_mod.stage_profile(session=book.session, scope=scope)
    assert sum(s["ead"] for s in profile["stages"]) == pytest.approx(
        profile["total_ead"], abs=1e-6)
    assert sum(s["ecl"] for s in profile["stages"]) == pytest.approx(
        profile["total_ecl"], abs=1e-6)
    assert sum(s["share_of_ead"] for s in profile["stages"]) == \
        pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_profile_reads_as_a_credit_book(books, domain_id):
    """The shape a credit reader checks first, stated as a regression.

    Not aesthetics. A book whose Stage 3 coverage is twelve per cent is
    saying the bank expects to collect eighty-eight per cent of its
    non-performing exposure, and a Stage 1 twelve-month PD of nine per cent
    is a CCC number applied to a performing book. Both were true of this
    generator and both made every derived figure quietly wrong.
    """
    book, scope = books[domain_id]
    profile = ecl_mod.stage_profile(session=book.session, scope=scope)
    by_stage = {int(s["stage"]): s for s in profile["stages"]}

    assert 0.0 < by_stage[1]["coverage"] < 0.02, (
        "Stage 1 carries a twelve-month expected loss on a performing book")
    assert by_stage[1]["coverage"] < by_stage[2]["coverage"] \
        < by_stage[3]["coverage"], (
        "coverage must rise with stage; it is the whole point of staging")
    assert by_stage[3]["coverage"] > 0.30, (
        "a defaulted exposure is not mostly collectable")
    assert 0.0 < profile["coverage"] < 0.12
    assert by_stage[1]["share_of_ead"] > 0.5, (
        "most of a performing book is performing")
    for stage in ecl_mod.STAGES:
        assert by_stage[stage]["exposures"] > 0, (
            f"stage {stage} is empty, so every question about it answers "
            f"'approximately nothing'")


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_defaulted_exposure_carries_a_probability_of_one(domain_id):
    """Publishing a PD of eight per cent beside a default flag of 1."""
    rows = _frame(domain_id, oracle.latest_month(domain_id))
    defaulted = rows[rows["stage"] == 3]
    assert len(defaulted) > 0
    assert (defaulted["pd_pit_12m"] == 1.0).all()
    assert (defaulted["pd_lifetime"] == 1.0).all()
    assert (defaulted["default_flag"] == 1).all()
    performing = rows[rows["stage"] == 1]
    assert performing["pd_pit_12m"].max() < 0.5


# ---- §24, §25: the decomposition ---------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_components_sum_to_the_movement_exactly(books, domain_id):
    book, scope = books[domain_id]
    body = ecl_mod.decompose(session=book.session, scope=scope).to_dict()
    total = sum(c["amount"] for c in body["components"])
    assert total == pytest.approx(body["movement"], abs=1e-6)
    assert body["reconciles"] is True
    assert abs(body["residual"]) < 1e-6
    assert body["model_calls"] == 0


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_movement_is_the_two_months_it_names(books, domain_id):
    book, scope = books[domain_id]
    month, comparison = _months(domain_id)
    body = ecl_mod.decompose(session=book.session, scope=scope).to_dict()
    assert body["reporting_period"] == month
    assert body["comparison_period"] == comparison
    assert body["period_noun"] == ("quarter" if domain_id == dom.CORPORATE
                                   else "month")
    assert body["opening"] == pytest.approx(
        float(_frame(domain_id, comparison)["ecl_sar_mn"].sum()), abs=1e-4)
    assert body["closing"] == pytest.approx(
        float(_frame(domain_id, month)["ecl_sar_mn"].sum()), abs=1e-4)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_every_component_matches_its_own_pandas_oracle(books, domain_id):
    """Each group recomputed independently, in pandas, from the parquet."""
    book, scope = books[domain_id]
    month, comparison = _months(domain_id)
    _relation, key = EXPOSURE[domain_id]
    now = _frame(domain_id, month).set_index(key)
    before = _frame(domain_id, comparison).set_index(key)

    opened = now.index.difference(before.index)
    closed = before.index.difference(now.index)
    kept = now.index.intersection(before.index)
    migrated = [k for k in kept if now.loc[k, "stage"] != before.loc[k,
                                                                    "stage"]]
    stayed = [k for k in kept if now.loc[k, "stage"] == before.loc[k,
                                                                  "stage"]]

    expected = {
        "new": float(now.loc[opened, "ecl_sar_mn"].sum()),
        "closed": -float(before.loc[closed, "ecl_sar_mn"].sum()),
        "stage_migration": float(now.loc[migrated, "ecl_sar_mn"].sum())
        - float(before.loc[migrated, "ecl_sar_mn"].sum()),
    }
    exposure_effect = 0.0
    for k in stayed:
        base_ead = float(before.loc[k, "ead_sar_mn"])
        coverage = (float(before.loc[k, "ecl_sar_mn"]) / base_ead
                    if base_ead else 0.0)
        exposure_effect += (float(now.loc[k, "ead_sar_mn"]) - base_ead) \
            * coverage
    expected["exposure_change"] = exposure_effect
    expected["risk_change"] = (
        float(now.loc[stayed, "ecl_sar_mn"].sum())
        - float(before.loc[stayed, "ecl_sar_mn"].sum()) - exposure_effect)

    produced = {c["component_id"]: c["amount"]
                for c in ecl_mod.decompose(session=book.session,
                                           scope=scope).to_dict()["components"]}
    assert set(produced) == set(expected)
    for name, value in expected.items():
        assert produced[name] == pytest.approx(value, abs=1e-4), name


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_groups_partition_the_book(books, domain_id):
    """New, closed and retained must not overlap or leave anything out."""
    month, comparison = _months(domain_id)
    _relation, key = EXPOSURE[domain_id]
    now = set(_frame(domain_id, month)[key])
    before = set(_frame(domain_id, comparison)[key])
    book, scope = books[domain_id]
    counts = {c["component_id"]: c["exposures"]
              for c in ecl_mod.decompose(session=book.session,
                                         scope=scope).to_dict()["components"]}
    assert counts["new"] == len(now - before)
    assert counts["closed"] == len(before - now)
    assert counts["stage_migration"] + counts["exposure_change"] == \
        len(now & before), (
        "every retained exposure is in exactly one of the two retained "
        "groups")
    assert counts["exposure_change"] == counts["risk_change"], (
        "the exposure and risk components describe the SAME population")


# ---- isolation ---------------------------------------------------------

def test_one_books_panel_cannot_be_computed_from_the_others_session(books):
    corporate, corporate_scope = books[dom.CORPORATE]
    _retail, retail_scope = books[dom.RETAIL]
    with pytest.raises(ecl_mod.EclUnavailable) as raised:
        ecl_mod.stage_profile(session=corporate.session, scope=retail_scope)
    assert "Retail" in str(raised.value)
    with pytest.raises(ecl_mod.EclUnavailable):
        ecl_mod.decompose(session=corporate.session, scope=retail_scope)
    # And the right pairing still works.
    assert ecl_mod.stage_profile(session=corporate.session,
                                 scope=corporate_scope)["domain_id"] == \
        dom.CORPORATE


def test_the_cache_cannot_serve_one_book_from_the_others_entry(books):
    ecl_mod.clear_cache()
    seen = {}
    for domain_id in dom.DOMAIN_IDS:
        book, scope = books[domain_id]
        seen[domain_id] = ecl_mod.cached(session=book.session, scope=scope,
                                         tenant_id=lake.DEFAULT_TENANT)
    for domain_id in dom.DOMAIN_IDS:
        book, scope = books[domain_id]
        again = ecl_mod.cached(session=book.session, scope=scope,
                               tenant_id=lake.DEFAULT_TENANT)
        assert again is seen[domain_id]
        assert again["profile"]["domain_id"] == domain_id
        assert again["profile"]["release_id"] == \
            dom.DEFAULT_RELEASES[domain_id]
    assert seen[dom.CORPORATE]["profile"]["total_ecl"] != \
        seen[dom.RETAIL]["profile"]["total_ecl"]


# ---- the published strings ---------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_server_publishes_every_number_as_a_string(books, domain_id):
    """§26. The page never formats a figure; it renders what it was given."""
    book, scope = books[domain_id]
    body = ecl_mod.cached(session=book.session, scope=scope,
                          tenant_id=lake.DEFAULT_TENANT, refresh=True)
    profile = body["profile"]
    assert profile["display_total_ead"].startswith("SAR ")
    assert profile["display_total_ead"].endswith(" million")
    assert "." not in profile["display_total_ead"].split(" ")[1], (
        "a monetary amount is published at zero decimal places")
    assert profile["display_coverage"].endswith("%")
    assert len(profile["display_coverage"].split(".")[1]) == 3, (
        "a percentage is published at two decimal places, then the % sign")
    for stage in profile["stages"]:
        assert stage["display_ecl"].startswith("SAR ")
        assert stage["display_share_of_ead"].endswith("%")
    for component in body["decomposition"]["components"]:
        assert component["display_amount"].startswith("SAR ")
        assert component["explanation"]
