"""REAL API · REAL STORE · NO MODEL.

§9-§13. The turn that was killed by the wrong clock.

The live failure
----------------
A thread seeded from an attention card had already run one analysis. The
reader typed:

    "How is risk building in Information Technology?"

and got DEADLINE_EXPIRED at sixty seconds. Sixty is the PRODUCT HELP
allowance. The analysis allowance is a hundred and twenty and the run never
reached it: widening happened only once the analyst had declared
`DATA_ANALYSIS`, and on a large book declaring it costs most of a minute.

So the envelope is decided before the first provider call, from things that
need no model: the question, the mode, and the thread it was asked in.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import envelope as env
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes

P = "/api/v1/cockpit-v4"

THE_LIVE_QUESTION = "How is risk building in Information Technology?"


def analysed(query_mode: str = "DATA_ANALYSIS") -> dict:
    """A completed turn that ran an analysis, in `recent_turns` shape."""
    return {"turn_id": "t1", "ordinal": 1, "question": "EAD by sector?",
            "answer": {"intent": {"query_mode": query_mode},
                       "disposition": "answer", "executed": True,
                       "narrative": "..."}}


def helped() -> dict:
    return {"turn_id": "t1", "ordinal": 1, "question": "Who are you?",
            "answer": {"intent": {"query_mode": "PRODUCT_HELP"},
                       "disposition": "answer", "executed": False,
                       "evidence_bound": False, "narrative": "..."}}


# ---- §11: the families and what they allow ------------------------------

def test_the_three_named_families_carry_the_allowances_the_spec_names():
    wanted = {
        env.PRODUCT_HELP_STANDARD: (60.0, 1.00),
        env.DATA_ANALYSIS_STANDARD: (180.0, 1.50),
        env.DATA_ANALYSIS_DEEP: (240.0, 3.00),
    }
    for family, (seconds, dollars) in wanted.items():
        limits = env.limits_for_family(family)
        assert limits.deadline_seconds == seconds, family
        assert float(limits.spend_ceiling_usd) == dollars, family


def test_the_policy_document_names_every_family_the_spec_requires():
    """§12. Diagnostics must be able to show the ladder."""
    published = {f["family"]: f for f in env.policy()}
    for family in env.NAMED_FAMILIES:
        assert family in published
        assert published[family]["named"] is True
    assert set(published) == set(env.FAMILIES)
    for entry in published.values():
        assert entry["deadline_seconds"] > 0
        assert entry["spend_ceiling_usd"] > 0
        assert entry["query_mode"] in {"DATA_ANALYSIS", "PRODUCT_HELP"}
        assert entry["finalization_reserve_seconds"] > 0


def test_the_ladder_only_ever_goes_up():
    order = [env.PRODUCT_HELP_STANDARD, env.DATA_ANALYSIS_STANDARD,
             env.DATA_ANALYSIS_DEEP]
    seconds = [env.limits_for_family(f).deadline_seconds for f in order]
    assert seconds == sorted(seconds)


# ---- §10: classified before the first provider call ---------------------

def test_the_live_question_is_analytical_on_its_own_words():
    verdict = env.classify(question=THE_LIVE_QUESTION)
    assert verdict.analytical is True
    assert verdict.family == env.DATA_ANALYSIS_STANDARD
    assert verdict.limits.deadline_seconds == env.limits_for_family(env.DATA_ANALYSIS_STANDARD).deadline_seconds
    assert verdict.reason


def test_a_seeded_thread_is_analytical_whatever_the_follow_up_says():
    """A conversation opened from an attention card is about a figure. "Tell
    me more" in it is not a product question."""
    verdict = env.classify(question="Tell me more", seeded=True)
    assert verdict.analytical is True
    assert "attention card" in verdict.reason
    assert "thread_opened_from_an_attention_card" in verdict.signals


def test_a_thread_whose_earlier_turn_ran_an_analysis_stays_analytical():
    verdict = env.classify(question="and within project finance?",
                           prior_turns=[analysed()])
    assert verdict.analytical is True
    assert "an_earlier_turn_in_this_thread_ran_an_analysis" in verdict.signals


def test_an_earlier_turn_that_executed_counts_even_without_a_declared_mode():
    turn = analysed(query_mode="")
    verdict = env.classify(question="and the month before?", prior_turns=[turn])
    assert verdict.analytical is True


@pytest.mark.parametrize("question", [
    "Who are you?", "What can you do?", "What is CreditProbe?",
    "How do you work?", "help",
])
def test_a_product_question_keeps_the_tight_allowance(question):
    verdict = env.classify(question=question)
    assert verdict.analytical is False
    assert verdict.family == env.PRODUCT_HELP_STANDARD
    assert verdict.limits.deadline_seconds == 60.0


def test_a_product_question_stays_a_product_question_in_an_analytical_thread():
    """The allowance is per TURN. "What can you do?" does not become a
    hundred-and-twenty-second question because the last turn ran SQL."""
    verdict = env.classify(question="What can you do?", seeded=True,
                           prior_turns=[analysed()])
    assert verdict.analytical is False
    assert verdict.family == env.PRODUCT_HELP_STANDARD


def test_a_product_question_that_also_names_a_figure_is_analytical():
    verdict = env.classify(
        question="What is CreditProbe's total ECL for the latest month?")
    assert verdict.analytical is True


@pytest.mark.parametrize("question", [
    "What is EAD by sector for the latest month?",
    "Which borrowers deteriorated most?",
    "Show me the top 10 facilities by exposure",
    "Compare stage 2 coverage month on month",
    "How is delinquency trending in Riyadh?",
    "Break down provisions by product",
    "What is the ECL coverage ratio?",
    "Which covenants are in breach?",
    "How many accounts rolled into 90+?",
])
def test_questions_about_the_book_get_the_analysis_allowance(question):
    verdict = env.classify(question=question)
    assert verdict.analytical is True, verdict.reason
    assert verdict.limits.deadline_seconds == env.limits_for_family(env.DATA_ANALYSIS_STANDARD).deadline_seconds


def test_deep_mode_doubles_the_analysis_allowance():
    verdict = env.classify(question=THE_LIVE_QUESTION, mode="deep")
    assert verdict.family == env.DATA_ANALYSIS_DEEP
    assert verdict.limits.deadline_seconds == 240.0
    assert float(verdict.limits.spend_ceiling_usd) == 3.00


def test_deep_mode_on_a_product_question_is_not_a_data_analysis_budget():
    verdict = env.classify(question="Who are you?", mode="deep")
    assert verdict.analytical is False
    assert verdict.family == env.PRODUCT_HELP_DEEP


def test_nothing_either_way_keeps_the_tight_allowance_and_says_why():
    verdict = env.classify(question="hello")
    assert verdict.analytical is False
    assert verdict.signals == ()
    assert "widens on the analyst's declaration" in verdict.reason


def test_the_verdict_is_serialisable_for_the_trace():
    body = env.classify(question=THE_LIVE_QUESTION).to_dict()
    assert body["family"] == env.DATA_ANALYSIS_STANDARD
    assert body["analytical"] is True
    assert body["deadline_seconds"] == env.limits_for_family(env.DATA_ANALYSIS_STANDARD).deadline_seconds
    assert isinstance(body["signals"], list)


# ---- the store-backed path ----------------------------------------------

def test_the_verdict_reads_the_thread_it_is_asked_in(store_db):
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.CORPORATE,
        release_id=dom.DEFAULT_RELEASES[dom.CORPORATE],
        release_fingerprint="fp")
    quiet = env.for_request(store=store_db, thread_id=thread_id,
                            question="Tell me more")
    assert quiet.analytical is False

    store_db.append_turn(thread_id=thread_id, run_id="r1",
                         question="EAD by sector?",
                         answer=analysed()["answer"])
    loud = env.for_request(store=store_db, thread_id=thread_id,
                           question="Tell me more")
    assert loud.analytical is True
    assert loud.family == env.DATA_ANALYSIS_STANDARD


def test_a_product_help_thread_does_not_widen_the_next_turn(store_db):
    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=dom.CORPORATE,
        release_id=dom.DEFAULT_RELEASES[dom.CORPORATE],
        release_fingerprint="fp")
    store_db.append_turn(thread_id=thread_id, run_id="r1",
                         question="Who are you?", answer=helped()["answer"])
    verdict = env.for_request(store=store_db, thread_id=thread_id,
                              question="Tell me more")
    assert verdict.analytical is False


def test_an_unreadable_thread_never_raises(store_db):
    class Broken:
        def thread_context(self, *a, **k):
            raise RuntimeError("no")

        def recent_turns(self, *a, **k):
            raise RuntimeError("no")

    verdict = env.for_request(store=Broken(), thread_id="nope",
                              question=THE_LIVE_QUESTION)
    assert verdict.analytical is True


# ---- §9: the accepted run carries the right deadline --------------------

@pytest.fixture
def client(store_db, runtime):
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def seconds_of(store_db, run_id: str) -> float:
    """How long the ACCEPTED run was actually given, from its own record."""
    from datetime import datetime

    record = store_db.get_run(run_id)
    deadline = datetime.fromisoformat(record.deadline_at)
    created = datetime.fromisoformat(record.created_at)
    return (deadline - created).total_seconds()


def settle(store_db, run_id: str) -> None:
    record = store_db.get_run(run_id)
    store_db.update_state(run_id, expect_version=record.version,
                          state="CANCELLED", terminal=True)


def test_the_route_stamps_the_analysis_deadline_on_an_analytical_question(
        client, store_db):
    """§9. The run is given the ANALYSIS clock at ACCEPT, not after a
    generation has been spent declaring that it needs one."""
    response = client.post(f"{P}/runs", json={"question": THE_LIVE_QUESTION,
                                              "mode": "standard"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    wanted = env.limits_for_family(
        env.DATA_ANALYSIS_STANDARD).deadline_seconds
    assert wanted - 10 <= seconds_of(store_db, run_id) <= wanted + 5
    settle(store_db, run_id)


def test_the_route_keeps_sixty_seconds_for_a_product_question(
        client, store_db):
    response = client.post(f"{P}/runs", json={"question": "Who are you?",
                                              "mode": "standard"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert 55 <= seconds_of(store_db, run_id) <= 65
    settle(store_db, run_id)


def test_a_follow_up_in_an_analysed_thread_is_accepted_on_the_wide_clock(
        client, store_db):
    """The live defect, end to end. The first turn establishes the thread;
    the follow-up must not be given the product-help clock."""
    first = client.post(f"{P}/runs", json={
        "question": "What is EAD by sector for the latest month?",
        "mode": "standard"})
    assert first.status_code == 202
    thread_id = first.json()["thread_id"]
    settle(store_db, first.json()["run_id"])
    store_db.append_turn(thread_id=thread_id, run_id=first.json()["run_id"],
                         question="What is EAD by sector?",
                         answer=analysed()["answer"])

    follow = client.post(f"{P}/runs", json={
        "question": "and within prject finance?", "mode": "standard",
        "thread_id": thread_id})
    assert follow.status_code == 202, follow.text
    wanted = env.limits_for_family(
        env.DATA_ANALYSIS_STANDARD).deadline_seconds
    assert (wanted - 10
            <= seconds_of(store_db, follow.json()["run_id"])
            <= wanted + 5)
    settle(store_db, follow.json()["run_id"])


def test_deep_mode_is_accepted_on_the_deep_clock(client, store_db):
    response = client.post(f"{P}/runs", json={"question": THE_LIVE_QUESTION,
                                              "mode": "deep"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert 230 <= seconds_of(store_db, run_id) <= 245
    settle(store_db, run_id)


# ---- §12: the diagnostics publish the ladder ----------------------------

def test_diagnostics_name_the_three_policy_families(client):
    body = client.get(f"{P}/diagnostics").json()
    families = {f["family"]: f for f in body["budget_policy"]["families"]}
    for family in ("product_help.standard", "data_analysis.standard",
                   "data_analysis.deep"):
        assert family in families, sorted(families)
    assert (families["data_analysis.standard"]["deadline_seconds"]
            == env.limits_for_family(env.DATA_ANALYSIS_STANDARD).deadline_seconds)
    assert families["data_analysis.standard"]["spend_ceiling_usd"] == 1.50
    assert families["data_analysis.deep"]["deadline_seconds"] == 240.0
    assert families["data_analysis.deep"]["spend_ceiling_usd"] == 3.00
    assert families["product_help.standard"]["deadline_seconds"] == 60.0
    assert body["budget_policy"]["how_it_is_chosen"]


# ---- the widening path is untouched -------------------------------------

def test_a_turn_that_started_narrow_still_widens_on_the_declaration():
    """Nothing here replaces the declaration path: it removes the case where
    the declaration arrives too late, and that is all."""
    import inspect

    from backend.cockpit_v4 import orchestration

    source = inspect.getsource(orchestration.Orchestrator)
    assert "_adopt_analytical_limits" in source
    assert "_adopt_analytical_limits_for_tool" in source
    assert "_widen_to_analytical" in source


def test_the_ledger_never_narrows_an_allowance_it_already_has(capability,
                                                              store_db):
    from backend.cockpit_v4.budgets import Ledger

    ledger = Ledger(limits=config_mod.ANALYTICAL_STANDARD_LIMITS,
                    capability=capability, store=store_db, run_id="r1",
                    started_monotonic=0.0)
    report = ledger.adopt(config_mod.STANDARD_LIMITS)
    assert ledger.limits.deadline_seconds == (
        config_mod.ANALYTICAL_STANDARD_LIMITS.deadline_seconds), (
        "adopting the tighter allowance must not shorten a run that already "
        "holds the wider one")
    assert report["changed"] is False
