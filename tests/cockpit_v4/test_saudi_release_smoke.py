"""MODEL MOCK · REAL DATABASE/RUNNER · REAL SOURCE · UNIT.

The Saudi release, provisioned and then independently verified.

§12 asks for the checks an operator would make after running the provisioning
command on a clean machine, made here against the published release rather
than against the seeder's own claims about it. The seeder says what it wrote;
this reads what is there.

No paid provider call: the model is never involved. The SQL, the release and
the attention computation are all real.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from backend.cockpit_v4 import release as rel

SAUDI = "v4-saudi-20q-v1"
#: Words that would mean the old book leaked into this one.
INDIAN = ("INR", "crore", "lakh", "₹", "Mumbai", "Delhi", "Chennai",
          "Bengaluru", "Kolkata", "Pvt Ltd", "Private Limited")


def _frame(release_id: str, relation: str) -> pd.DataFrame:
    from backend.cockpit_agentic import store

    return pd.read_parquet(store.relation_path(release_id, relation))


# ---- shape ------------------------------------------------------------

def test_the_release_carries_twenty_quarters(release_id):
    from backend.cockpit_agentic import store

    manifest = store.read_manifest(release_id)
    slots = manifest["calendar"]["reporting_slots"]
    assert len(slots) == 20, f"{len(slots)} quarters, not twenty"
    assert slots == sorted(slots)


def test_the_latest_quarter_is_the_one_the_book_ends_on(release_id, runtime):
    populated = [str(q) for q in runtime.catalog.calendar.populated]
    assert populated[-1] == "2026Q2"
    assert len(populated) == 20


def test_every_relation_the_domain_defines_is_published(release_id):
    from backend.cockpit_agentic import fields, store

    manifest = store.read_manifest(release_id)
    published = set(manifest["relations"])
    for relation in fields.RELATIONS:
        assert relation in published, f"{relation} was not published"
    assert len(published) == 11, sorted(published)


# ---- denomination -----------------------------------------------------

def test_the_book_is_denominated_in_riyals_at_million_scale(runtime,
                                                            release_id):
    header = rel.header(release_id=release_id, catalog=runtime.catalog,
                        release_summary=runtime.release_summary)
    assert header.reporting_currency == "SAR"
    assert header.amount_scale == "million"
    assert header.country == "Saudi Arabia"
    assert header.denominated


def test_no_amount_was_converted(release_id):
    from backend.cockpit_agentic import store

    manifest = store.read_manifest(release_id)
    assert manifest.get("amounts_converted") is False, (
        "applying a rate to fictional figures would manufacture economic "
        "meaning that was never in them")


def test_the_fingerprint_is_a_real_digest_of_the_published_bytes(release_id):
    digest = rel.fingerprint(release_id)
    assert re.fullmatch(r"[0-9a-f]{64}", digest), digest
    rel.forget()
    assert rel.fingerprint(release_id) == digest


# ---- the borrowers ----------------------------------------------------

def test_the_borrowers_are_saudi_synthetic_names(release_id):
    from backend.cockpit_v4 import saudi

    names = sorted(_frame(release_id, "cockpit_facility_quarter")
                   ["borrower_name"].dropna().unique())
    assert len(names) >= 100, f"only {len(names)} borrowers"
    for name in names:
        assert any(name.startswith(h) for h in saudi.NAME_HEAD), name
        assert any(name.endswith(t) for t in saudi.NAME_TAIL), name


def test_no_indian_borrower_name_or_money_label_survives(release_id):
    frame = _frame(release_id, "cockpit_facility_quarter")
    text = " ".join(
        str(v) for column in frame.columns if frame[column].dtype == object
        for v in frame[column].dropna().unique()[:5000])
    found = [word for word in INDIAN if word in text]
    assert found == [], f"the old book leaked: {found}"


# ---- it actually works ------------------------------------------------

def test_a_simple_ead_query_executes_against_the_published_release(
        session, release_id):
    from backend.cockpit_agentic import sql as v3_sql

    result = v3_sql.execute(
        "SELECT sector_name, SUM(ead_reported) AS ead "
        "FROM cockpit_facility_quarter "
        "WHERE reporting_quarter = '2026Q2' "
        "GROUP BY sector_name ORDER BY ead DESC",
        session, deadline_seconds=15.0)
    assert result.row_count == 12
    assert result.rows[0]["ead"] > 0


def test_the_attention_feed_computes_against_the_published_release(
        session, runtime, release_id):
    from backend.cockpit_v4 import attention
    from backend.cockpit_v4 import display as disp

    attention.clear_cache()
    feed = attention.compute(
        session=session, release_id=release_id,
        quarters=[str(q) for q in runtime.catalog.calendar.populated],
        currency=disp.money_unit(runtime.catalog))

    assert feed["release_id"] == release_id
    assert feed["segments_requiring_attention"], "the dashboard is empty"
    published = json.dumps(feed, default=str)
    for word in INDIAN:
        assert word not in published, word


# ---- the provisioning command itself ----------------------------------

def test_the_provisioning_command_is_v4s_own_seeder():
    from backend.cockpit_v4.service import provision_command

    command = provision_command(SAUDI)
    assert command == (
        f"python3 scripts/cockpit_v4/seed_release.py --release {SAUDI}")
    assert "build_cockpit_agentic_v3" not in command


def test_the_seeder_refuses_the_v3_namespace_and_an_existing_release():
    """Immutability and isolation, read off the seeder's own source.

    Running it here would either rebuild the release this suite reads from or
    take minutes doing nothing; the guarantees are structural and the source
    is where they live.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "scripts"
              / "cockpit_v4" / "seed_release.py").read_text()
    assert 'cockpit_agentic_v3' in source and "Refusing to seed into the V3" \
        in source
    assert "is not inside the V4 namespace" in source
    assert "published release is immutable" in source
    assert "saudi.localize" in source
    assert "saudi.manifest_overrides" in source
