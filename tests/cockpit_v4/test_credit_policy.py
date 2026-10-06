"""
A recommendation names the clause it changes.

UNIT · REAL DATABASE/RUNNER · MODEL MOCK. No paid provider call.

Why this exists
---------------
A finding is not an action. "Credit Card - Gold held by non-salaried
customers is entering arrears at 20-29 days" is a fact about the book; what
a credit committee needs next is which rule allowed it and what changing
that rule would do. That step is where a model is most dangerous and least
checkable: policy prose reads as authoritative whether or not the rule it
describes exists, whether or not the analyst knows what the rule currently
says, and whether or not the clause it names is the one that governs the
exposure. A wrong number can be recomputed. A wrong rule cannot.

So the policy is a PACK -- clause-numbered, with thresholds, owners, review
dates and the levers each clause can carry -- and:

  * it is retrieved, never remembered: the clauses arrive bounded, and the
    analyst writes the answer;
  * the two books never cross, because a Corporate limit quoted in a Retail
    answer is a citation that looks checked and is not;
  * a narrative that proposes a policy change and cites no clause of this
    book is refused, the same way a portfolio number with no evidence is;
  * and the retrieval happens on the ANSWER turn, server-side. It was a
    TOOL first, and the tool could not be called: the state that holds a
    result requires `finalize_response`, so a companion offered beside it
    is a schema every action turn pays for and no turn can reach. The
    reversal is recorded here rather than quietly dropped, because "it is
    offered" and "it can be used" are not the same claim and only tests of
    the second kind are worth anything.

What is deliberately NOT here: any test that a particular recommendation is
good. CreditProbe retrieves and checks the citation. Whether tightening a
DBR cap is the right answer is the committee's judgement and not a thing
software can hold an opinion about.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import action_state as ast
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import credit_policy as cp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4.config import STANDARD_LIMITS
from backend.cockpit_v4.contracts import parse_final
from backend.cockpit_v4.finalization import Finalizer

from conftest import final, intent  # noqa: F401


# ---- the pack ----------------------------------------------------------

def test_both_books_have_a_policy_and_it_says_it_is_not_real():
    for book in dom.DOMAIN_IDS:
        assert cp.clauses(book), book
    assert cp.pack()["not_client_data"] is True
    # A synthetic policy that did not say so is a document somebody could
    # mistake for their own bank's.
    assert "NOT any institution's actual policy" in cp.pack()["note"]


def test_every_clause_carries_what_makes_it_citable():
    """An id, a rule, an owner and a review date.

    A clause with no owner is a rule nobody can be asked to change, and a
    clause with no review date is one nobody knows the age of. Both are
    what make a citation worth more than a sentence.
    """
    for book in dom.DOMAIN_IDS:
        for clause in cp.clauses(book):
            for key in ("clause", "title", "rule", "owner", "last_reviewed"):
                assert clause.get(key), (book, clause.get("clause"), key)
            assert clause["clause"].startswith(
                "RP-" if book == "retail" else "CP-")


def test_a_clause_offers_levers_rather_than_a_recommendation():
    """`levers` says what the clause CAN change, not what it should.

    The difference matters: a list of what a rule is able to do is a fact
    about the rule, and a list of what it ought to do is an opinion the
    pack has no standing to hold.
    """
    with_levers = [c for c in cp.clauses("retail") if c.get("levers")]
    assert len(with_levers) >= 10
    for clause in with_levers:
        assert all(isinstance(lever, str) for lever in clause["levers"])


# ---- retrieval ---------------------------------------------------------

def test_a_clause_id_is_the_most_precise_way_in():
    got = cp.retrieve("corporate", clause_ids=("CP-1.2",))
    assert got["matched_by"] == "clause id"
    assert [c["clause"] for c in got["clauses"]] == ["CP-1.2"]
    assert got["clauses"][0]["thresholds"][
        "connected_group_ead_sar_mn"] == 50000


def test_a_topic_returns_THE_CLAUSE_rather_than_its_whole_section():
    """Clause grain, and phrases rather than words.

    Section grain fired on a third of an ordinary question bank: "exposure
    at default by sector" pulled the entire rating and watchlist section
    because that section listed the word "default". A policy attachment
    that arrives on every other question teaches the analyst to stop
    reading it.
    """
    got = cp.retrieve(
        "corporate", question="is the group over its single obligor limit?")
    assert got["matched_by"] == "topic"
    assert [c["clause"] for c in got["clauses"]] == ["CP-1.1"]

    quiet = cp.retrieve(
        "corporate", question="What is exposure at default by sector?")
    assert quiet["clauses"] == [], (
        "an ordinary portfolio question names no policy clause")


def test_a_section_id_still_returns_the_whole_section():
    got = cp.retrieve("corporate", question="what does CP-1 cover?")
    assert got["matched_by"] == "section"
    assert {c["clause"] for c in got["clauses"]} == {"CP-1.1", "CP-1.2",
                                                     "CP-1.3"}


def test_a_retrieval_is_bounded():
    """Unbounded retrieval is the pack by another name."""
    got = cp.retrieve("retail", question=" ".join(
        f"{c['clause']}" for c in cp.clauses("retail")))
    assert len(got["clauses"]) <= cp.MAX_CLAUSES


def test_a_question_matching_nothing_says_so_rather_than_guessing():
    got = cp.retrieve("retail", question="what is the weather")
    assert got["clauses"] == []
    assert got["sections"], "and says what there IS to ask for"


# ---- the books do not cross --------------------------------------------

def test_a_retail_retrieval_cannot_reach_a_corporate_clause():
    got = cp.retrieve("retail", question="what does CP-1.2 say?")
    assert got["clauses"] == []


def test_a_corporate_retrieval_cannot_reach_a_retail_clause():
    got = cp.retrieve("corporate", question="what does RP-4.1 say?")
    assert got["clauses"] == []


def test_a_topic_in_both_books_returns_only_this_books_section():
    """"Staging" is a section in both packs and means different things."""
    retail = cp.retrieve("retail", question="how does staging work here?")
    corporate = cp.retrieve("corporate", question="how does staging work?")
    assert all(c["clause"].startswith("RP-") for c in retail["clauses"])
    assert all(c["clause"].startswith("CP-") for c in corporate["clauses"])


def test_an_unknown_book_is_named_rather_than_defaulted():
    with pytest.raises(cp.UnknownBook):
        cp.retrieve("wholesale", question="limits")


# ---- the citation rule -------------------------------------------------

def _validated(narrative: str, *, domain_id: str, store):
    finalizer = Finalizer(store=store, tenant_id="t", release_id="rel",
                          limits=STANDARD_LIMITS, domain_id=domain_id)
    answer = parse_final(final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                               narrative=narrative))
    return finalizer.validate(answer, executed=False)


def test_a_policy_change_with_no_clause_is_refused(store_db):
    report = _validated(
        "The non-salaried card book is deteriorating. The policy should be "
        "tightened for that segment.",
        domain_id="retail", store=store_db)
    assert any("cites no clause" in p for p in report.problems), \
        report.problems


def test_the_same_change_publishes_once_it_cites_the_clause(store_db):
    report = _validated(
        "The non-salaried card book is deteriorating. RP-2.2 sets the card "
        "application score at 640 with a 40 point uplift for non-salaried "
        "applicants; raising that uplift to 80 would have excluded most of "
        "this cohort at origination.",
        domain_id="retail", store=store_db)
    assert not [p for p in report.problems if "clause" in p], report.problems


def test_the_other_books_clause_is_refused_even_with_a_citation(
        store_db):
    """A citation that looks checked and is not.

    Worse than citing nothing: a reader who sees a clause number assumes
    somebody looked it up, and CP-1.2 does not govern a retail account.
    """
    report = _validated(
        "Retail concentration is rising. CP-1.2 caps connected group "
        "exposure and the policy should be tightened accordingly.",
        domain_id="retail", store=store_db)
    assert any("does not govern this book" in p for p in report.problems), \
        report.problems


def test_ordinary_analysis_is_never_caught_by_this(store_db):
    """THE FAILURE MODE THIS ROUND ALREADY PAID FOR ONCE.

    `_check_ordering` fired on "highest", "worst" and "most" and refused
    two live answers for using the ordinary vocabulary of a delinquency
    write-up. A policy check that fired on "recommend" or "tighten" alone
    would do the same thing again, so it needs the word POLICY as well.
    """
    for narrative in (
        "Delinquency in the card book rose to its highest in the window, "
        "and the worst of the movement is in the most recent month.",
        "Coverage should be increased to reflect the new bucket.",
        "We recommend reviewing the Gold cohort before the next committee.",
        "The largest sector by exposure is Construction, and it is the one "
        "to watch.",
    ):
        report = _validated(narrative, domain_id="retail", store=store_db)
        assert not [p for p in report.problems if "polic" in p], (
            narrative, report.problems)


def test_a_run_with_no_book_is_not_asked_to_cite_anything(store_db):
    """A product question has no credit policy to quote."""
    report = _validated(
        "CreditProbe does not set policy; the policy should be changed by "
        "the committee that owns it.",
        domain_id="", store=store_db)
    assert not [p for p in report.problems if "polic" in p], report.problems


# ---- where the policy reaches the analyst ------------------------------

def test_the_policy_is_not_a_tool_the_run_can_be_offered():
    """RECORDED REVERSAL.

    `inspect_credit_policy` existed, was registered in the contract, and
    was placed on `RESULT_READY` as a companion to `finalize_response`.
    `RESULT_READY` REQUIRES finalize -- `provider_tools` is called with
    that one name forced -- so the policy tool was a schema on every action
    payload that no turn could ever call. It is gone, and the clauses
    arrive in the answer turn's context instead.
    """
    assert "inspect_credit_policy" not in contracts_mod.TOOL_NAMES
    assert not (contracts_mod.CONTRACTS_DIR
                / "inspect_credit_policy.schema.json").exists()


def test_the_turn_that_holds_a_result_is_the_turn_that_answers():
    """Which is why the context is the right place to put the policy."""
    holding = ast.decide(executed=True, answer_only=False, analytical=True,
                         readiness={})
    assert holding.state == ast.RESULT_READY
    assert holding.require == contracts_mod.TOOL_FINALIZE


# ---- the synopsis ------------------------------------------------------

def _answer_turn(domain_id: str, question: str = "") -> str:
    blocks = ctx_mod.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"},
         {"type": "text", "text": json.dumps({"pinned_scope": {}})}],
        domain_id=domain_id, question=question)
    return " ".join(str(b.get("text") or "") for b in blocks)


def test_the_answer_turn_carries_this_books_policy_synopsis():
    text = _answer_turn("corporate")
    assert "Corporate Credit Policy" in text
    assert "CP-1.1" in text
    assert "Retail Credit Policy" not in text
    assert "RP-" not in text


def test_a_policy_question_arrives_with_its_clauses_already_attached():
    """The analyst cannot fail to have asked, because it did not have to."""
    text = _answer_turn(
        "corporate", "Is this connected group over its exposure limit?")
    assert "CP-1.2" in text
    assert "connected_group_ead_sar_mn" in text
    assert "50000" in text


def test_a_question_naming_no_policy_topic_carries_only_the_synopsis():
    """A retrieval that always fires is the pack attached by another route."""
    text = _answer_turn("corporate", "What is EAD by sector this period?")
    assert "Corporate Credit Policy" in text
    assert "CP-4.1" not in text and "haircut" not in text.lower()


def test_an_ordinary_analytical_question_attaches_no_clause_at_all():
    """Measured, not assumed: against the question bank this fires on
    about one question in ten, and on the ten it should."""
    import re

    from pathlib import Path

    bank = Path(__file__).with_name("question_bank.py").read_text()
    asked = re.findall(
        r'"((?:What|How|Which|Show|Compare|Is|Are|Why|List|Give)'
        r'[^"]{15,140})"', bank)
    assert len(asked) >= 50, "the bank moved; re-point this"
    for book in dom.DOMAIN_IDS:
        fired = sum(1 for q in asked
                    if cp.retrieve(book, question=q)["clauses"])
        assert fired / len(asked) < 0.25, (book, fired, len(asked))


def test_the_retail_answer_turn_never_carries_a_corporate_clause():
    text = _answer_turn(
        "retail", "Which collections action applies at 20-29 days?")
    assert "RP-4.1" in text
    assert "CP-" not in text


def test_the_action_turn_carries_no_policy_at_all():
    """The instruction is carried on every attempt and is bounded."""
    instruction = ctx_mod.PROMPT_PATH.read_text(encoding="utf-8")
    assert "RP-" not in instruction and "CP-" not in instruction


def test_a_turn_with_no_book_gets_no_policy_block():
    blocks = ctx_mod.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"}], domain_id="")
    text = " ".join(str(b.get("text") or "") for b in blocks)
    assert "Credit Policy" not in text


def test_the_synopsis_is_small_enough_to_carry():
    """Two kilobytes, against a pack of twenty-three."""
    for book in dom.DOMAIN_IDS:
        size = len(json.dumps(cp.synopsis(book), ensure_ascii=False))
        assert size < 3_000, (book, size)
    assert cp.PACK_PATH.stat().st_size > 15_000, (
        "the pack is meant to be far larger than the synopsis of it")


# ---- the thresholds line up with the book ------------------------------

def test_the_group_limit_is_one_the_book_can_actually_cross():
    """A policy whose limits no exposure approaches is decoration.

    `test_planted_patterns` puts a connected group's aggregate over this
    limit in 2026Q1 while every single name stays inside the obligor one.
    If either threshold drifts away from the book, the question "are we
    within our limits?" stops having an answer either way.
    """
    import domain_oracles as oracles

    single = cp.clause("corporate", "CP-1.1")["thresholds"][
        "single_obligor_ead_sar_mn"]
    group_limit = cp.clause("corporate", "CP-1.2")["thresholds"][
        "connected_group_ead_sar_mn"]

    facilities = oracles.frame(dom.CORPORATE, "corp_facility_quarter")
    borrowers = oracles.frame(dom.CORPORATE, "corp_borrower_quarter")
    keys = borrowers[["borrower_id", "reporting_quarter",
                      "group_name"]].drop_duplicates()
    joined = facilities.merge(keys, on=["borrower_id", "reporting_quarter"],
                              how="left")
    last = joined[joined["reporting_quarter"] == "2026Q2"]

    by_name = last.groupby("borrower_name")["ead_sar_mn"].sum()
    by_group = last.groupby("group_name")["ead_sar_mn"].sum()
    assert by_name.max() < single, (
        "no single obligor should be over CP-1.1, or the concentration "
        "pattern is reachable without grouping")
    assert (by_group > group_limit).any(), (
        "no group is over CP-1.2, so the policy question has no answer")
