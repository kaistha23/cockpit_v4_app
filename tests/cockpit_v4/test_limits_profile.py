"""NO MODEL. The named limits profile: what it changes and what it must not.

The local oMLX model is slow (answer p95 76s), so `COCKPIT_V4_LIMITS_PROFILE=
local` widens the ANALYTICAL families' call windows, reserve and deadline.
These tests pin that the default is exactly the old constants, that the
profile touches nothing else, that a nonsense combination is refused, and
that a run upgraded to analytical widens every time field and narrows none.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import envelope as env
from backend.cockpit_v4.budgets import Ledger
from backend.cockpit_v4.config import (PROFILE_VAR, ConfigurationInvalid,
                                       effective_limits)

BASES = {"standard": config_mod.STANDARD_LIMITS,
         "deep": config_mod.DEEP_LIMITS,
         "analytical_standard": config_mod.ANALYTICAL_STANDARD_LIMITS,
         "analytical_deep": config_mod.ANALYTICAL_DEEP_LIMITS}
ANALYTICAL = ("analytical_standard", "analytical_deep")
TIME = ("deadline_seconds", "action_call_seconds", "answer_call_seconds",
        "finalization_reserve_seconds")


@pytest.mark.parametrize("value", [None, "", "default", "  DEFAULT "])
def test_default_is_todays_constants(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(PROFILE_VAR, value)
    for key, base in BASES.items():
        assert effective_limits(key) == base
    assert config_mod.active_profile() == "default"


def test_local_overrides_only_the_analytical_families(monkeypatch):
    monkeypatch.setenv(PROFILE_VAR, "local")
    for key in ("standard", "deep"):
        assert effective_limits(key) == BASES[key]
    for key in ANALYTICAL:
        got = effective_limits(key)
        assert (got.deadline_seconds, got.action_call_seconds,
                got.answer_call_seconds,
                got.finalization_reserve_seconds) == (300, 45, 100, 90)
        # Everything the profile does not name is untouched.
        assert replace(got, **{f: getattr(BASES[key], f) for f in TIME}
                       ) == BASES[key]


def test_unknown_profile_raises_naming_the_variable(monkeypatch):
    monkeypatch.setenv(PROFILE_VAR, "turbo")
    with pytest.raises(ConfigurationInvalid, match=PROFILE_VAR):
        effective_limits("standard")
    with pytest.raises(ConfigurationInvalid, match=PROFILE_VAR):
        config_mod.limits_for("standard")


@pytest.mark.parametrize("profile", sorted(config_mod.PROFILES))
@pytest.mark.parametrize("key", config_mod.FAMILY_KEYS)
def test_invariants_hold_for_every_profile_and_family(
        monkeypatch, profile, key):
    monkeypatch.setenv(PROFILE_VAR, profile)
    lim = effective_limits(key)
    margin = Ledger.SETTLEMENT_MARGIN_SECONDS
    assert margin == config_mod.SETTLEMENT_MARGIN_SECONDS == 2.0
    if key in ANALYTICAL:
        assert (2 * lim.action_call_seconds
                + lim.finalization_reserve_seconds + margin
                <= lim.deadline_seconds)
    assert lim.answer_call_seconds <= lim.deadline_seconds - lim.min_call_seconds


def test_invalid_override_combination_raises(monkeypatch):
    monkeypatch.setitem(
        config_mod.PROFILES, "broken",
        {"analytical_standard": {"deadline_seconds": 100.0,
                                 "action_call_seconds": 45.0,
                                 "finalization_reserve_seconds": 90.0}})
    monkeypatch.setenv(PROFILE_VAR, "broken")
    with pytest.raises(ConfigurationInvalid, match="deadline"):
        effective_limits("analytical_standard")
    # The same profile does not trip a family it does not touch.
    assert effective_limits("standard") == BASES["standard"]


def test_answer_window_longer_than_deadline_raises(monkeypatch):
    monkeypatch.setitem(
        config_mod.PROFILES, "broken",
        {"standard": {"answer_call_seconds": 59.0}})
    monkeypatch.setenv(PROFILE_VAR, "broken")
    with pytest.raises(ConfigurationInvalid, match="answer_call_seconds"):
        effective_limits("standard")


def test_env_is_read_at_call_time(monkeypatch):
    assert effective_limits("analytical_standard").deadline_seconds == 180.0
    monkeypatch.setenv(PROFILE_VAR, "local")
    assert effective_limits("analytical_standard").deadline_seconds == 300.0
    monkeypatch.delenv(PROFILE_VAR)
    assert effective_limits("analytical_standard").deadline_seconds == 180.0


def test_monkeypatched_constant_is_honoured(monkeypatch):
    """example_traces.py replaces STANDARD_LIMITS to force a short deadline."""
    short = replace(config_mod.STANDARD_LIMITS, deadline_seconds=0.5,
                    answer_call_seconds=0.1)
    monkeypatch.setattr(config_mod, "STANDARD_LIMITS", short)
    assert config_mod.limits_for("standard").deadline_seconds == 0.5


def test_envelope_and_policy_reflect_the_profile(monkeypatch):
    def deadlines():
        return {row["family"]: row["deadline_seconds"]
                for row in env.policy()}

    before = deadlines()
    assert before[env.DATA_ANALYSIS_STANDARD] == 180.0
    monkeypatch.setenv(PROFILE_VAR, "local")
    after = deadlines()
    assert after[env.DATA_ANALYSIS_STANDARD] == 300.0
    assert after[env.DATA_ANALYSIS_DEEP] == 300.0
    assert after[env.PRODUCT_HELP_STANDARD] == before[env.PRODUCT_HELP_STANDARD]
    assert (env.limits_for_family(env.DATA_ANALYSIS_DEEP).answer_call_seconds
            == 100.0)
    assert [r["finalization_reserve_seconds"] for r in env.policy()
            if r["family"] == env.DATA_ANALYSIS_STANDARD] == [90.0]


def test_adopt_widens_every_time_field(ledger_factory, monkeypatch):
    monkeypatch.setenv(PROFILE_VAR, "local")
    ledger = ledger_factory()
    target = config_mod.analytical_limits_for("standard")
    report = ledger.adopt(target)
    assert report["changed"]
    for name in TIME + ("spend_ceiling_usd",):
        assert report["after"][name] == getattr(target, name)
        assert report["before"][name] < report["after"][name]
        assert getattr(ledger.limits, name) == getattr(target, name)


def test_adopt_never_narrows(ledger_factory):
    ledger = ledger_factory(
        deadline_seconds=500.0, action_call_seconds=70.0,
        answer_call_seconds=200.0, finalization_reserve_seconds=120.0,
        spend_ceiling_usd=9.0)
    report = ledger.adopt(config_mod.ANALYTICAL_STANDARD_LIMITS)
    assert not report["changed"]
    assert report["before"] == report["after"]
    assert ledger.limits.deadline_seconds == 500.0
    assert ledger.limits.finalization_reserve_seconds == 120.0
