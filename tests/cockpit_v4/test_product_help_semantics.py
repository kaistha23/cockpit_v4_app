"""
Product Help: scope, semantic equivalence, and the one-generation policy.

Evidence: UNIT for the coverage and retrieval rules, MODEL MOCK for the runs.
The scripted analyst lets us check what CreditProbe PUT IN FRONT of the model
and how many generations the run cost -- which is exactly what the live
`run-53dfe2c6f10c480bb7b2d033273a6427` spent two of. It says nothing about the
wording Opus chooses.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import product_knowledge as pk
from backend.cockpit_v4.contracts import (TOOL_EXECUTE, TOOL_FINALIZE,
                                          TOOL_INSPECT, TOOL_NAMES,
                                          TOOL_PRODUCT, provider_tools)
from conftest import ScriptedResult, final, intent, tool_call

# ---- the two scopes -----------------------------------------------------

BROAD = [
    "Who are you?",
    "What are you?",
    "What is CreditProbe?",
    "Tell me about CreditProbe.",
    "What is CreditProbe AI?",
    "What does CreditProbe do?",
    "What problem does CreditProbe solve?",
    "Why would a CRO use CreditProbe?",
    "What can CreditProbe do?",
    "What are the seven main functionalities?",
    "Explain CreditProbe at a high level.",
]

COCKPIT = [
    "What is Cockpit?",
    "What is Cockpit in CreditProbe?",
    "What does Cockpit do?",
    "Why should I use Cockpit?",
    "Explain CreditProbe Cockpit.",
    "What problem does Cockpit solve?",
]

DEEP = [
    "Explain TAC.",
    "What are the four EWS layers?",
    "How does Playbook work?",
    "How do Cockpit and What-If differ?",
    "How does AI Project Planner track dependencies?",
    "What is Graph Data?",
    "How does Scorecard Validation work?",
    "Explain External Intelligence.",
]


@pytest.mark.parametrize("question", BROAD)
def test_a_broad_product_question_is_one_generation(question):
    verdict = pk.coverage(question)
    assert verdict["level"] == pk.COVERAGE_SYNOPSIS, question
    assert verdict["deep_topics_named"] == []


@pytest.mark.parametrize("question", COCKPIT)
def test_a_cockpit_question_is_scoped_to_cockpit(question):
    verdict = pk.coverage(question)
    assert verdict["level"] == pk.COVERAGE_RETRIEVAL, question
    assert "cockpit" in verdict["deep_topics_named"]


@pytest.mark.parametrize("question", DEEP)
def test_a_specific_question_still_retrieves(question):
    verdict = pk.coverage(question)
    assert verdict["level"] == pk.COVERAGE_RETRIEVAL, question
    assert verdict["deep_topics_named"]


def test_similar_intent_does_not_mean_the_same_grounding():
    """"Who are you?" and "What is Cockpit?" must not be the same answer."""
    broad = pk.synopsis()
    cockpit = pk.retrieve(query="What is Cockpit of CreditProbe?")
    section = next(s for s in cockpit["sections"] if s["topic"] == "cockpit")

    # The Cockpit answer has material the broad one does not: what Cockpit
    # owns in detail, its boundary, and where it stops.
    assert section["boundary"]
    assert section["three_beats"]
    assert section["example_questions"]
    broad_text = str(broad)
    assert section["boundary"] not in broad_text
    # And the broad synopsis still situates Cockpit, so a Cockpit answer can
    # place it inside the product without a second retrieval.
    assert any(m["name"] == "Cockpit" for m in broad["seven_functionalities"])


def test_a_cockpit_question_retrieves_its_boundaries_against_the_neighbours():
    sections = pk.retrieve(query="What problem does Cockpit solve?")["sections"]
    titles = " ".join(str(s.get("title", "")) for s in sections)
    assert "Cockpit" in titles
    relationships = [s for s in sections if s["topic"] == "relationships"]
    assert relationships, "a Cockpit answer can say where Cockpit ends"


# ---- what must never leak ----------------------------------------------

@pytest.mark.parametrize("question", BROAD + COCKPIT + DEEP)
def test_no_product_answer_can_reach_the_superseded_architecture(question):
    payload = str(pk.retrieve(query=question)) + str(pk.synopsis())
    lowered = payload.lower()
    for forbidden in ("multi-agent", "multi agent", "orchestrator agent",
                      "agent swarm"):
        assert forbidden not in lowered, f"{question}: leaked {forbidden}"


def test_the_grounding_carries_no_release_ids_or_route_names():
    payload = str(pk.synopsis())
    for forbidden in ("v4-uat-", "/api/v1/", "cockpit_facility_quarter",
                      "dataset_release_id"):
        assert forbidden not in payload, forbidden


# ---- the tool policy ----------------------------------------------------

def test_withholding_removes_only_the_product_tool():
    full = [t["name"] for t in provider_tools()]
    assert set(full) == set(TOOL_NAMES)
    trimmed = [t["name"] for t in provider_tools(withhold=(TOOL_PRODUCT,))]
    assert TOOL_PRODUCT not in trimmed
    assert set(full) - set(trimmed) == {TOOL_PRODUCT}


def test_the_run_can_never_lose_its_way_to_terminate():
    names = [t["name"] for t in provider_tools(withhold=(TOOL_FINALIZE,))]
    assert TOOL_FINALIZE in names


def test_an_unknown_tool_name_in_withhold_is_refused():
    with pytest.raises(ValueError):
        provider_tools(withhold=("inspect_the_vibes",))


def test_who_are_you_is_offered_four_tools_and_costs_one_generation(
        drive, store_db):
    script = [ScriptedResult(tool_calls=[tool_call("finalize_response", {
        **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
        **final(disposition="answer",
                narrative="# CreditProbe AI\n\nAn intelligent credit "
                          "investigation layer for risk teams.")})])]
    outcome, provider, record = drive("Who are you?", script)

    assert outcome.state == "COMPLETED"
    assert len(provider.sent) == 1, "one generation, no retrieval round trip"
    offered = [t["name"] for t in provider.sent[0]["tools"]]
    assert TOOL_PRODUCT not in offered
    # THIS USED TO ASSERT `offered == [TOOL_FINALIZE]`, on the reasoning that
    # "a product question cannot execute or inspect anything". True of a
    # question that IS one -- and the surface is chosen before anybody knows
    # which kind it is. `envelope.classify` reads the words; the first live
    # UAT sent it "wat is the toatl expsoure at defalt by secter this qtr",
    # it read no measure, and the analyst -- which understood the sentence
    # perfectly -- had `finalize_response` alone and no way to reach the
    # book. Neither `classify` nor `sem.readiness` can tell that question
    # from this one: both resolve nothing for both.
    #
    # So `execute_analysis` is on the request and nothing is required.
    # What this test protects is unchanged and still asserted: ONE
    # generation, no retrieval round trip, and the product tool off the
    # first action.
    assert TOOL_EXECUTE in offered, (
        "the book must stay reachable, because the surface was chosen "
        "before anyone knew this was a product question")
    assert TOOL_INSPECT not in offered, (
        "the catalogue is not the way out: a run that read it would still "
        "be on the product-help clock, which is the defect envelope.py "
        "exists for. Submitting SQL is the declaration that widens it.")
    assert set(offered) == {TOOL_EXECUTE, TOOL_FINALIZE}

    run = store_db.get_run(record.run_id)
    assert run.budget["generation_attempts"][0] == 1


def test_a_cockpit_question_is_offered_the_product_tool_from_the_start(drive):
    def second(messages):
        return ScriptedResult(tool_calls=[tool_call("finalize_response", {
            **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
            **final(disposition="answer",
                    narrative="## Cockpit\n\nIt interrogates the recorded "
                              "book.")})])

    script = [ScriptedResult(tool_calls=[tool_call(
        "inspect_product_knowledge", {
            **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
            "query": "What is Cockpit?", "topics": ["cockpit"],
            "detail": "standard"})]), second]
    outcome, provider, _record = drive("What is Cockpit of CreditProbe?",
                                       script)

    assert outcome.state == "COMPLETED"
    offered = [t["name"] for t in provider.sent[0]["tools"]]
    assert TOOL_PRODUCT in offered, "a named module needs the retrieval tool"


def test_a_withheld_tool_comes_back_for_the_second_action(drive):
    """The economy is first-action only. It can never strand a run."""
    def second(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "inspect_product_knowledge", {
                **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
                "query": "tac", "topics": ["tac"], "detail": "standard"})])

    def third(messages):
        return ScriptedResult(tool_calls=[tool_call("finalize_response", {
            **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
            **final(disposition="answer", narrative="TAC is a classifier.")})])

    # A first action that is NOT a finalization, on a question the policy
    # judged synopsis-covered.
    script = [ScriptedResult(tool_calls=[tool_call("inspect_catalog", {
        **intent(mode="DATA_ANALYSIS", owner="COCKPIT"),
        "query": "exposure", "relation_ids": [], "field_ids": [],
        "detail": ["discovery"], "reporting_periods": [], "sample_rows": 0,
        "cursor": ""})]), second, third]

    outcome, provider, _record = drive("Who are you?", script)
    assert outcome.state == "COMPLETED"
    first_offer = [t["name"] for t in provider.sent[0]["tools"]]
    second_offer = [t["name"] for t in provider.sent[1]["tools"]]
    assert TOOL_PRODUCT not in first_offer
    assert TOOL_PRODUCT in second_offer, (
        "the economy is first-action only; it may never strand a run that "
        "turns out to need the deeper pack")
    # It comes back on the SECOND ACTION. It does not come back on a
    # recovery: a re-ask after a malformed action is never a broader
    # question than the one that failed.
    assert set(second_offer) == {TOOL_PRODUCT, TOOL_EXECUTE, TOOL_FINALIZE}


def test_the_context_tells_the_model_which_policy_applies(drive):
    script = [ScriptedResult(tool_calls=[tool_call("finalize_response", {
        **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
        **final(disposition="answer", narrative="Hello.")})])]
    _outcome, provider, _record = drive("Who are you?", script)
    sent = provider.first_input_text()
    assert "product_knowledge_coverage" in sent
    assert pk.COVERAGE_SYNOPSIS in sent


def test_the_instruction_states_the_scope_rule():
    from backend.cockpit_v4 import context as context_mod

    instruction = context_mod.analyst_instruction()
    assert "Answer it in\nthis one action" in instruction
    assert "not the same answer" in instruction


# The four product-answer outlines moved out of `analyst.md` and onto the
# turn that writes a product answer. `analyst.md` is carried on EVERY action
# attempt and measured against a payload bound, and an action turn -- which
# is choosing what to run -- can write no product answer at all, so the run
# paid for them on every attempt and could act on them on at most one. These
# three tests hold the move honest: the outlines still exist, they reach the
# product turn, and they no longer ride on the action turn.

OUTLINES = ("a_broad_product_question", "a_single_module_question",
            "a_module_question_about_cockpit", "a_narrow_concept_question")


def test_the_four_product_shapes_all_still_exist():
    from backend.cockpit_v4 import context as context_mod

    for key in OUTLINES:
        assert key in context_mod.PRODUCT_ANSWER, key
        assert len(context_mod.PRODUCT_ANSWER[key]) > 60, key
    # The substance, not just the keys: the arc, the boundary and the
    # instruction not to brochure a one-word concept.
    blob = " ".join(context_mod.PRODUCT_ANSWER.values())
    for phrase in ("Detect, Diagnose, Decide, Drive Alignment",
                   "the governance boundary",
                   "where Cockpit ends and Early Warning or What-If begins",
                   "Do not produce a brochure for it",
                   "Do not walk through every section"):
        assert phrase in blob, phrase


def test_the_outlines_reach_the_turn_that_writes_a_product_answer(drive):
    script = [ScriptedResult(tool_calls=[tool_call("finalize_response", {
        **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
        **final(disposition="answer", narrative="Hello.")})])]
    _outcome, provider, _record = drive("Who are you?", script)
    blob = " ".join(block["text"] for block in provider.sent[0]["system"])
    for key in OUTLINES:
        assert key in blob, key


def test_the_outlines_do_not_ride_on_an_analytical_action_turn(drive):
    script = [ScriptedResult(tool_calls=[tool_call("finalize_response", {
        **intent(), **final(disposition="answer", narrative="ok")})])]
    _outcome, provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        script)
    blob = " ".join(block["text"] for block in provider.sent[0]["system"])
    for key in OUTLINES:
        assert key not in blob, key
    from backend.cockpit_v4 import context as context_mod

    # And they are not smuggled back in through the instruction file, which
    # is the thing that was paying for them.
    instruction = context_mod.analyst_instruction()
    assert "wants: one strong line of positioning" not in instruction
    assert "Do not produce a brochure for it" not in instruction
