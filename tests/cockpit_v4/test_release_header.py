"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

Which release this is, proved rather than declared.

Every analytical object in V4 carries a `release_id`. An id is a NAME. Two
releases can share one; a release can be rebuilt under the same id from
different bytes; and a stored artifact from yesterday's build satisfies
"same release_id" against today's perfectly. So objects carry a header whose
fingerprint is taken over the published bytes, and evidence validation
compares that.

D-001 recorded a suspicion that some test or launcher path rebuilds a
release. These tests settle it: the fingerprint is taken before and after
the work, and a publish attempt over a live release must fail rather than
silently replace it.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import release as rel

SAUDI = "v4-saudi-20q-v1"

#: Taken once, at import, before any test has run. Compared at the end.
_SESSION_FINGERPRINT: dict[str, str] = {
    SAUDI: rel.fingerprint(SAUDI),
    "v4-uat-20q-v1": rel.fingerprint("v4-uat-20q-v1"),
}


# ---- the default ------------------------------------------------------

def test_the_saudi_release_is_the_default(monkeypatch):
    """A runtime with nothing configured runs the Saudi demonstration."""
    from backend.cockpit_v4 import config as config_mod

    monkeypatch.delenv("COCKPIT_V4_RELEASE_ID", raising=False)
    cfg = config_mod.load()
    assert cfg.release_id == SAUDI
    assert "COCKPIT_V4_RELEASE_ID" not in cfg.missing, (
        "an unset release is no longer a missing setting; it is the default")


def test_an_explicit_release_is_never_overridden(monkeypatch):
    """A default that outranks an explicit choice is not a default."""
    from backend.cockpit_v4 import config as config_mod

    monkeypatch.setenv("COCKPIT_V4_RELEASE_ID", "v4-uat-20q-v1")
    assert config_mod.load().release_id == "v4-uat-20q-v1"


def test_a_missing_configured_release_is_an_error_not_a_fallback(
        v4_config, capability):
    """Silently loading a different release is the defect, not the cure."""
    from dataclasses import replace

    from backend.cockpit_v4.service import PreflightFailed, load_release

    with pytest.raises(PreflightFailed) as raised:
        load_release(replace(v4_config, release_id="v4-does-not-exist"))
    assert "Nothing was substituted" in str(raised.value)


# ---- the header -------------------------------------------------------

def test_the_header_states_what_the_numbers_mean(runtime, release_id):
    header = rel.header(release_id=release_id, catalog=runtime.catalog,
                        release_summary=runtime.release_summary)
    assert header.release_id == release_id
    assert len(header.release_fingerprint) == 64
    assert header.country == "Saudi Arabia"
    assert header.reporting_currency == "SAR"
    assert header.amount_scale == "million"
    assert header.money_unit == "SAR million"
    assert header.latest_populated_quarter
    assert header.not_client_data is True
    assert header.denominated is True
    assert header.unverified == ()


def test_a_release_that_does_not_say_is_not_guessed(release_id):
    """Fail closed. A currency is not something to infer from a country.

    Neither the manifest nor the catalogue built from it declares one, which
    is the honest shape of a release that simply does not say. The header
    reports what is missing instead of choosing a currency, and a caller that
    needs to denominate a figure refuses.
    """
    header = rel.header(release_id=release_id, catalog=None,
                        release_summary={})
    assert header.denominated is False
    assert set(header.unverified) == {"reporting_currency", "amount_scale",
                                      "country"}


def test_a_header_matches_only_the_same_bytes(runtime, release_id):
    header = rel.header(release_id=release_id, catalog=runtime.catalog,
                        release_summary=runtime.release_summary)
    assert header.matches(header.to_dict())

    stale = dict(header.to_dict())
    stale["release_fingerprint"] = "0" * 64
    assert not header.matches(stale), (
        "same id, different bytes: that is the case this exists for")

    unstamped = dict(header.to_dict())
    unstamped.pop("release_fingerprint")
    assert not header.matches(unstamped), (
        "an object with no fingerprint cannot be SHOWN to be this release")

    other = dict(header.to_dict())
    other["release_id"] = "v4-uat-20q-v1"
    assert not header.matches(other)


# ---- D-001: the published release is not rebuilt ----------------------

def test_the_fingerprint_is_stable_across_a_reload(release_id):
    rel.forget()
    first = rel.fingerprint(release_id)
    rel.forget()
    second = rel.fingerprint(release_id)
    assert first == second, (
        "hashing the same published bytes twice gave two answers")


def test_the_two_releases_do_not_share_a_fingerprint(release_id):
    assert rel.fingerprint(SAUDI) != rel.fingerprint("v4-uat-20q-v1")


def test_publishing_over_a_live_release_is_refused(release_id):
    """The D-001 suspicion, as an assertion.

    A release is immutable once published. If a seeder, a launcher or a test
    could overwrite one in place, every fingerprint in the system would be a
    statement about whenever it was last taken.
    """
    from backend.cockpit_agentic import store as v3_store

    before = rel.fingerprint(release_id)

    class _Release:
        """Enough of a release to reach the guard, and no further."""

        dataset_release_id = release_id
        frames: dict = {}
        calendar = None

    with pytest.raises(v3_store.UnsafeTarget) as raised:
        v3_store.write(_Release(), require_flag=False, overwrite=False)
    assert "immutable" in str(raised.value)

    rel.forget()
    assert rel.fingerprint(release_id) == before, (
        "the release changed while being asked whether it could change")


def test_the_suite_leaves_the_release_byte_identical(release_id,
                                                     request):
    """D-001, closed. Recorded at session end, compared to session start."""
    rel.forget()
    assert rel.fingerprint(release_id) == _SESSION_FINGERPRINT[release_id], (
        "the published release changed during this test session")
