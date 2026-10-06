"""The revised live-UAT expectations, proven against the live runtime.

UNIT · REAL RELEASE · REAL POLICY PACK. No model call, no paid provider call.

The first live UAT invalidated three matrix entries, and the replacements
are not opinions. Each one is checked here against the catalogue this book
actually publishes and the policy pack this book actually holds, so a later
round cannot quietly reintroduce an expectation the data contradicts.

L16 -- was "true clarification", is not
    CP-1.1 reads "Total exposure to a single obligor may not exceed SAR
    25,000 million. Exposure is measured as EAD, funded and unfunded
    together." The clause closes the ambiguity the expectation rested on,
    `cp.retrieve` returns it for that exact sentence, and `sem.readiness`
    calls the question sufficient. So it is answerable, and asking is the
    failure L17 exists to catch.

L19 -- the replacement true clarification
    "Which sectors have the largest exposure?" leaves `exposure` undecided
    between three governed fields that produce three different rankings,
    and no clause of this book reaches it.

M05 -- retired, not failed
    Turn 2 ("Exposure at default") does not answer the clarification turn 1
    actually asked, which was about the breach test. Invalid design for
    clarification-answer projection, in either direction. M06 replaces it
    and M07 is its control.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import domain_oracles as oracle
import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import credit_policy as cp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import semantics as sem

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")

L16_QUESTION = "Which sectors are above the single-name limit?"
L19_QUESTION = "Which sectors have the largest exposure?"


@pytest.fixture(scope="module")
def uat():
    spec = importlib.util.spec_from_file_location("live_uat", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_uat"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("live_uat", None)


@pytest.fixture(scope="module")
def index(uat):
    return {j.jid: j for j in uat.matrix()}


@pytest.fixture(scope="module")
def corporate():
    return arun.for_domain(dom.CORPORATE)


# ---- L16 is answerable ------------------------------------------------

def test_the_policy_settles_the_measure_l16_was_believed_to_leave_open():
    clause = cp.clause(dom.CORPORATE, "CP-1.1")
    assert clause is not None
    assert "measured as EAD" in clause["rule"]
    assert clause["thresholds"] == {"single_obligor_ead_sar_mn": 25000}


def test_l16_reaches_that_clause_by_the_readers_own_words():
    found = cp.retrieve(dom.CORPORATE, question=L16_QUESTION)
    assert [c["clause"] for c in found["clauses"]] == ["CP-1.1"]


def test_the_runtime_calls_l16_sufficient(corporate):
    readiness = sem.readiness(corporate.catalog, L16_QUESTION)
    assert readiness["sufficient"] is True
    terms = {m["term"] for m in
             (readiness.get("governed_measures_already_resolved") or ())}
    assert {"limit", "sector"} <= terms
    assert not readiness.get("terms_still_needing_a_decision")


def test_l16_no_longer_expects_a_clarification(index):
    assert index["L16"].expect_clarification == "no"
    assert "CP-1.1" in index["L16"].notes


def test_the_book_supports_the_breach_test_and_nothing_breaches_it():
    """The honest answer is "none", and it is worth checking it is."""
    frame = oracle.frame(dom.CORPORATE, "corp_facility_quarter")
    period = oracle.latest_period(dom.CORPORATE)
    latest = frame[frame["reporting_quarter"] == period]
    by_borrower = latest.groupby("borrower_id")["ead_sar_mn"].sum()

    assert len(by_borrower) > 1000, "a borrower-grain test needs borrowers"
    limit = cp.clause(dom.CORPORATE,
                      "CP-1.1")["thresholds"]["single_obligor_ead_sar_mn"]
    assert by_borrower.max() < limit, (
        "if the book gains a breaching obligor this expectation changes: "
        "L16's answer becomes a list of sectors rather than 'none'")
    assert by_borrower.max() > limit / 2, (
        "and a limit nothing comes close to would make L16 a test of "
        "nothing; the largest obligor must at least be the same order")


# ---- L19 is a real ambiguity ------------------------------------------

def test_l19_leaves_the_measure_undecided(corporate):
    readiness = sem.readiness(corporate.catalog, L19_QUESTION)
    assert readiness["sufficient"] is False
    needs = {m["term"]: m["candidate_fields"]
             for m in readiness["terms_still_needing_a_decision"]}
    assert "exposure" in needs
    assert set(needs["exposure"]) == {"ead_sar_mn", "limit_sar_mn",
                                      "drawn_sar_mn"}


def test_every_candidate_reading_of_l19_really_exists(corporate):
    columns = set(corporate.catalog.columns("corp_facility_quarter"))
    assert {"ead_sar_mn", "limit_sar_mn", "drawn_sar_mn"} <= columns


def test_the_three_readings_rank_the_sectors_differently():
    """An ambiguity that changed no answer would not be blocking."""
    frame = oracle.frame(dom.CORPORATE, "corp_facility_quarter")
    period = oracle.latest_period(dom.CORPORATE)
    latest = frame[frame["reporting_quarter"] == period]
    orders = {
        column: list(latest.groupby("sector")[column].sum()
                     .sort_values(ascending=False).index)
        for column in ("ead_sar_mn", "limit_sar_mn", "drawn_sar_mn")}
    assert len({tuple(v) for v in orders.values()}) > 1, (
        f"the three readings produce the same ranking: {orders}")


def test_no_clause_of_this_book_reaches_l19():
    assert cp.retrieve(dom.CORPORATE, question=L19_QUESTION)["clauses"] == []


def test_l19_is_in_the_matrix_as_the_clarification_case(index):
    assert index["L19"].expect_clarification == "yes"
    assert index["L19"].turns == [L19_QUESTION]
    assert index["L19"].oracle is None, (
        "a question the analyst must refuse to answer has no figure to "
        "reconcile")


# ---- M05 retired, M06 and M07 replace it -----------------------------

def test_m05_is_retired_with_the_reason_recorded(index):
    journey = index["M05"]
    assert journey.retired
    assert "does not answer the clarification" in journey.retired
    assert journey.turns[0] == L16_QUESTION, (
        "kept exactly as it ran, so the executed first pass still maps")


def test_a_retired_journey_is_read_and_not_graded(uat, index):
    turns = [{"state": "COMPLETED", "disposition": "answer",
              "clarification_question": "", "chart_count": 0,
              "intent": {}, "executed": True}]
    graded = uat.grade(index["M05"], turns)
    assert graded["expectations"]["checked"] is False
    assert graded["expectations"]["retired"] == index["M05"].retired
    # It is still READ: the observable behaviour is all there.
    assert graded["dispositions"] == ["answer"]
    assert graded["every_turn_executed"] is True


def test_m06_turn_two_answers_one_of_the_offered_readings(index):
    journey = index["M06"]
    assert journey.retired == ""
    assert journey.turns[0] == L19_QUESTION
    assert journey.turns[1] == "Exposure at default"
    assert journey.expect_clarification == "yes"
    # And "exposure at default" is one of the governed readings the
    # clarification is about, which is what makes turn 2 an ANSWER.
    book = arun.for_domain(dom.CORPORATE)
    needs = sem.readiness(book.catalog, L19_QUESTION)[
        "terms_still_needing_a_decision"]
    fields = {f for m in needs for f in m["candidate_fields"]}
    assert "ead_sar_mn" in fields


def test_m07_turn_two_is_not_one_of_the_offered_readings(index):
    journey = index["M07"]
    assert journey.turns[0] == L19_QUESTION
    assert journey.turns[1] == "What is total ECL this quarter?"
    assert "ecl" not in L19_QUESTION.lower(), (
        "the control only works if turn 2 names a different measure")


def test_the_two_chains_are_a_matched_pair(index):
    """One proves projection happens; the other proves it is not blind."""
    assert index["M06"].turns[0] == index["M07"].turns[0]
    assert index["M06"].turns[1] != index["M07"].turns[1]


# ---- the first pass stays mappable -----------------------------------

def test_every_first_pass_journey_is_still_in_the_matrix(uat):
    """The executed queue must remain readable after this revision."""
    from test_live_uat_rejudge import APPROVED_QUEUE

    index = {j.jid: j for j in uat.matrix()}
    for jid in APPROVED_QUEUE:
        assert jid in index, f"{jid} ran and was removed from the matrix"


def test_the_first_pass_questions_are_unchanged(uat):
    """A revised EXPECTATION is not a revised question. Changing the text
    would make the paid runs unmappable."""
    from test_live_uat_rejudge import APPROVED_QUEUE

    index = {j.jid: j for j in uat.matrix()}
    assert index["L16"].turns == [L16_QUESTION]
    assert index["M05"].turns[1] == "Exposure at default"
    assert len(index["M05"].turns) == 4
    assert sum(len(index[j].turns) for j in APPROVED_QUEUE) == 28
