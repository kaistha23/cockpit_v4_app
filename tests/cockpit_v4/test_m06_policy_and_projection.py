"""M06: a policy definition is not a licence to choose the measure.

MODEL MOCK · REAL DATABASE/RUNNER · REAL POLICY PACK. No paid provider call.

The defect
----------
The paid retest ran M06 turn 1:

    Which sectors have the largest exposure?

That question names no clause, no limit and no threshold. `exposure` has
three governed readings in this book -- `ead_sar_mn`, `limit_sar_mn`,
`drawn_sar_mn` -- and they rank the sectors differently, which is what makes
it the matrix's proven blocking ambiguity (L19). The run was supposed to
ask. It did not: it settled on EAD and answered, and the reader got one of
three possible orderings with nothing saying which.

The mechanism, reproduced offline in `test_the_clarification_turn_is_not_
told_its_question_is_invented`:

  * `policy_blocks` attaches the SYNOPSIS on every turn that can publish,
    which it must -- H-LIVE-05 exists because a turn without it stated that
    this book records no single-name limit when CP-1.1 does.
  * The synopsis carries CP-1.1 in full, including "Exposure is measured as
    EAD, funded and unfunded together", because that is the rule CP-1.1's
    own breach test runs on.
  * `POLICY_RULE` then said, unconditionally, that a term a clause settles
    is settled and that treating it as open "invents an ambiguity the policy
    has already closed".

So the one turn whose entire job was to put the question back to the reader
was handed a rule telling it the question was invented.

The rule, and why it is not about exposure
------------------------------------------
A policy definition settles terminology FOR THE TEST IT DEFINES. The reader
has invoked that test when the question reaches that clause; they have not
when the question is an ordinary data-analysis question that never mentions
it. The server already decides which is which, deterministically and before
any model call: `cp.retrieve` returns the clauses the question names, in the
reader's own words. When it returns none, the settling sentence is not sent
at all.

Nothing here keys on "exposure", on CP-1.1, on sectors, or on the Corporate
book -- `test_the_rule_is_not_written_around_one_clause` and the Retail
cases hold that.
"""

from __future__ import annotations

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain  # noqa: F401

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import credit_policy as cp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import semantics as sem

#: The three turns the approved paid retest ran. Not four: the chain was
#: authored with a second narrowing that was never executed.
M06 = ["Which sectors have the largest exposure?",
       "Exposure at default",
       "Just the top five by that measure"]

#: M07, the control: turn 2 changes the subject instead of answering.
M07_TURN_2 = "What is total ECL this quarter?"

#: The question that DOES invoke CP-1.1, and must keep its answer (L16).
L16 = "Which sectors are above the single-name limit?"

#: The sentence at issue, quoted from the block that carries it.
SETTLES = "FOR THIS TEST"

#: CP-1.1's own definition, which must keep reaching every publishing turn.
CP11_DEFINITION = "funded and unfunded together"

THE_OPTIONS = ["Exposure at default", "Approved limit", "Drawn balance"]

SQL = ("SELECT b.sector AS sector, SUM(f.ead_sar_mn) AS ead_sar_mn "
       "FROM corp_facility_quarter f JOIN corp_borrower_quarter b "
       "ON f.borrower_id = b.borrower_id AND f.quarter = b.quarter "
       "WHERE f.quarter = '2026Q2' GROUP BY b.sector ORDER BY 2 DESC")


@pytest.fixture(autouse=True)
def _clean():
    arun.reset()
    yield
    arun.reset()


def _submits_declaring_the_ambiguity():
    return ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="rank sectors by exposure",
                         ambiguities=["exposure reads as EAD, as the "
                                      "approved limit or as drawn balance, "
                                      "and the three rank sectors "
                                      "differently"]),
        "objective": "rank sectors by exposure",
        "subquestions": ["which sector is largest"],
        "scope": {"reporting_periods": ["2026Q2"], "filters": {}},
        "metadata_receipt_ids": [],
        "fields_required": ["corp_facility_quarter.ead_sar_mn"],
        "expected_output_grain": "sector", "expected_units": "amount",
        "steps": [{"step_id": "s1", "language": "sql", "code": SQL,
                   "parameters": {}, "purpose": "rank",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""})])


def _asks_the_reader():
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT",
                      understood="rank sectors by exposure"),
        disposition="clarification",
        narrative="Exposure has three governed readings in this book.",
        clarification_question="Which exposure measure did you mean?",
        clarification_options=list(THE_OPTIONS)))])


def _system_text(sent) -> str:
    return " ".join(str(b.get("text") or "") for b in sent["system"])


def _turn(ordinal: int, question: str, **answer) -> dict:
    body = {"disposition": "answer", "narrative": "…"}
    body.update(answer)
    return {"turn_id": f"t-{ordinal}", "ordinal": ordinal,
            "question": question, "answer": body}


def _clarification_turn() -> dict:
    return _turn(1, M06[0], disposition="clarification",
                 narrative="Exposure has three governed readings.",
                 clarification_question="Which exposure measure did you "
                                        "mean?",
                 clarification_options=list(THE_OPTIONS))


def packet_for(question: str, turns: list[dict]):
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    return ctx_mod.build(
        question=question, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), recent_turns=turns,
        session=book.session, analytical=True)


# ---- A. the reproduction, and the repair -------------------------------

def test_a_turn_one_is_a_real_ambiguity_before_any_model_is_asked():
    """The server's own verdict, so the rest is not an opinion."""
    book = arun.for_domain(dom.CORPORATE)
    ready = sem.readiness(book.catalog, M06[0])
    assert ready["sufficient"] is False
    fields = {f for m in ready["terms_still_needing_a_decision"]
              for f in m["candidate_fields"]}
    assert {"ead_sar_mn", "limit_sar_mn", "drawn_sar_mn"} <= fields
    # And the question invokes no clause at all.
    assert cp.retrieve(dom.CORPORATE, question=M06[0])["clauses"] == []


def test_the_clarification_turn_is_not_told_its_question_is_invented(
        drive_domain):  # noqa: F811
    """THE DEFECT. Drive the real worker to NEEDS_CLARIFICATION and read
    the system context that turn was actually sent."""
    _outcome, provider, _record = drive_domain(
        dom.CORPORATE, M06[0],
        [_submits_declaring_the_ambiguity(), _asks_the_reader()])
    assert len(provider.sent) == 2
    asking_turn = _system_text(provider.sent[-1])
    assert SETTLES not in asking_turn
    assert "invents an ambiguity" not in asking_turn


# ---- B. L16 is not collateral damage -----------------------------------

def test_a_question_that_asks_to_apply_the_clause_still_settles_the_term():
    blocks = ctx_mod.policy_blocks(domain_id=dom.CORPORATE, question=L16)
    blob = " ".join(b["text"] for b in blocks)
    assert SETTLES in blob
    assert "CP-1.1" in blob
    # L16 is answerable and must not acquire a round trip.
    book = arun.for_domain(dom.CORPORATE)
    assert sem.readiness(book.catalog, L16)["sufficient"] is True


# ---- C. the condition is retrieval, not wording ------------------------

@pytest.mark.parametrize("question,invoked", [
    ("Which sectors have the largest exposure?", False),
    ("Just the top five by that measure", False),
    ("Exposure at default", False),
    ("What is total ECL this quarter?", False),
    ("Which sectors are above the single-name limit?", True),
    ("Which borrowers breach the single obligor limit?", True),
    ("What does CP-1.1 say?", True),
])
def test_the_settling_sentence_rides_only_on_an_invoked_clause(
        question, invoked):
    blocks = ctx_mod.policy_blocks(domain_id=dom.CORPORATE,
                                   question=question)
    blob = " ".join(b["text"] for b in blocks)
    reached = bool(cp.retrieve(dom.CORPORATE, question=question)["clauses"])
    assert reached is invoked, question
    assert (SETTLES in blob) is invoked, question


# ---- C2. the guard on retrieval getting generous -----------------------

#: A question that uses a policy word in passing and DOES reach a clause,
#: while the server still cannot decide the measure. Retrieval matches on
#: the reader's own topic words -- which is what lets "single name" reach
#: CP-1.1 without a clause id -- and the same generosity reaches CP-1.2
#: from this. The settling sentence must not go out.
GENEROUS = "Show me exposure concentration"

OPEN_NOTE = "put it to the reader rather than letting a rule"


def _undecided(question: str) -> list[str]:
    book = arun.for_domain(dom.CORPORATE)
    return [t["term"] for t in
            (sem.readiness(book.catalog, question)
             .get("terms_still_needing_a_decision") or ())]


def test_a_clause_the_question_grazed_does_not_close_an_open_reading():
    assert cp.retrieve(dom.CORPORATE, question=GENEROUS)["clauses"]
    assert _undecided(GENEROUS) == ["exposure"]
    blocks = ctx_mod.policy_blocks(domain_id=dom.CORPORATE,
                                   question=GENEROUS,
                                   undecided=_undecided(GENEROUS))
    blob = " ".join(b["text"] for b in blocks)
    # The clauses are still attached -- what changes is how to read them.
    assert "CP-1.2" in blob
    assert SETTLES not in blob
    assert OPEN_NOTE in blob
    # And the analyst is told WHICH term, not just that something is open.
    assert "they do not decide which measure the question means: exposure" \
        in blob.lower()


def test_the_guard_lifts_once_the_server_has_nothing_open():
    """The same question, with the server holding no undecided term, gets
    the settling sentence. The condition is the open reading, not the
    wording."""
    blocks = ctx_mod.policy_blocks(domain_id=dom.CORPORATE,
                                   question=GENEROUS, undecided=())
    blob = " ".join(b["text"] for b in blocks)
    assert SETTLES in blob
    assert OPEN_NOTE not in blob


def test_l16_has_nothing_open_so_it_is_unaffected_by_the_guard():
    assert _undecided(L16) == []
    blocks = ctx_mod.policy_blocks(domain_id=dom.CORPORATE, question=L16,
                                   undecided=_undecided(L16))
    blob = " ".join(b["text"] for b in blocks)
    assert SETTLES in blob
    assert OPEN_NOTE not in blob


def test_the_run_passes_the_servers_own_verdict_and_not_its_own():
    """`_undecided_terms` reads `semantics.readiness`, which the server
    computed before any model call. Nothing here is the analyst's opinion."""
    from backend.cockpit_v4 import orchestration as orch

    class _Run:
        readiness = {"sufficient": False,
                     "terms_still_needing_a_decision": [
                         {"term": "exposure", "candidate_fields": ["a", "b"]},
                         {"term": ""},
                     ]}
        _undecided_terms = orch.Orchestrator._undecided_terms

    assert _Run()._undecided_terms() == ["exposure"]
    class _Empty:
        readiness: dict = {}
        _undecided_terms = orch.Orchestrator._undecided_terms
    assert _Empty()._undecided_terms() == []


# ---- D. H-LIVE-05 is preserved -----------------------------------------

def test_the_synopsis_still_reaches_the_turn_that_cannot_use_the_clause(
        drive_domain):  # noqa: F811
    """The pack is still there. What was removed is one sentence about how
    to read it, not the policy itself -- a turn with no policy is the
    defect H-LIVE-05 exists for."""
    _outcome, provider, _record = drive_domain(
        dom.CORPORATE, M06[0],
        [_submits_declaring_the_ambiguity(), _asks_the_reader()])
    asking_turn = _system_text(provider.sent[-1])
    assert CP11_DEFINITION in asking_turn
    assert "single_obligor_ead_sar_mn" in asking_turn
    assert "Never say this book records no policy" in asking_turn


def test_the_standing_rule_says_what_a_definition_does_and_does_not_do():
    rule = ctx_mod.POLICY_RULE
    assert "governs THAT CLAUSE'S OWN TEST" in rule
    assert "the reader chooses, not the pack" in rule


# ---- E, F. the clarification round trip, on M06's own words ------------

def test_turn_two_is_projected_as_the_answer_to_turn_one():
    packet = packet_for(M06[1], [_clarification_turn()])
    prior = packet.payload["recent_turns"][-1]
    assert prior["you_asked"] == "Which exposure measure did you mean?"
    assert prior["you_offered"] == THE_OPTIONS
    assert "ANSWERING it" in prior["note"]
    # And the three words are one of the offered choices verbatim, which is
    # what makes the projection decisive rather than suggestive.
    assert M06[1] in prior["you_offered"]


def test_turn_three_still_sees_the_clarification_and_the_answer_to_it():
    turns = [_clarification_turn(),
             _turn(2, M06[1], narrative="Exposure at default, by sector.")]
    packet = packet_for(M06[2], turns)
    history = packet.payload["recent_turns"]
    assert [h["ordinal"] for h in history] == [1, 2]
    assert history[0]["you_asked"]
    assert history[0]["you_offered"] == THE_OPTIONS
    # The chosen reading is readable from the chain, not from a flag the
    # server invented: the question of turn 2 IS the choice.
    assert history[1]["question"] == M06[1]
    assert "you_asked" not in history[1]


# ---- G. the control: a reply that is not a reply -----------------------

def test_the_projection_leaves_room_for_a_new_question():
    packet = packet_for(M07_TURN_2, [_clarification_turn()])
    prior = packet.payload["recent_turns"][-1]
    assert "If it plainly asks something else, it is a new question." in (
        prior["note"])
    assert M07_TURN_2 not in prior["you_offered"]


# ---- H. none of this is about exposure, CP-1.1 or one book -------------

def test_the_rule_is_not_written_around_one_clause():
    import re

    for text in (ctx_mod.POLICY_RULE, ctx_mod.POLICY_SETTLES_THE_TERM):
        lowered = text.lower()
        for token in ("cp-1.1", "rp-4.1", "ead", "exposure", "sector",
                      "corporate", "retail", "obligor", "concentration"):
            assert not re.search(rf"\b{re.escape(token)}\b", lowered), (
                token, text)


@pytest.mark.parametrize("question,invoked", [
    ("Which products have the largest balances?", False),
    ("When does the collections ladder start?", True),
])
def test_the_same_rule_holds_in_the_other_book(question, invoked):
    blocks = ctx_mod.policy_blocks(domain_id=dom.RETAIL, question=question)
    blob = " ".join(b["text"] for b in blocks)
    assert blob, "the synopsis reaches every publishing turn in both books"
    assert (SETTLES in blob) is invoked, question


# ---- I. the matrix says three turns ------------------------------------

@pytest.fixture(scope="module")
def uat():
    import importlib.util
    import pathlib
    import sys

    harness = (pathlib.Path(__file__).resolve().parents[2]
               / "scripts" / "cockpit_v4" / "live_uat.py")
    spec = importlib.util.spec_from_file_location("live_uat", harness)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_uat"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("live_uat", None)


def test_m06_is_three_turns_and_its_note_says_so(uat):
    journey = {j.jid: j for j in uat.matrix()}["M06"]
    assert journey.turns == M06
    assert "Turn 3 must inherit" in journey.notes
    assert "Turns 3 and 4" not in journey.notes
    assert "fourth" not in journey.notes
