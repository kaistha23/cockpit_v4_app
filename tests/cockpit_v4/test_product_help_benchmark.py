"""
UNIT (deterministic) · MODEL MOCK where a run is exercised.

The Product Help benchmark from the build instruction, thirty questions.

What this proves and what it does not
------------------------------------
DETERMINISTIC: that each question retrieves the right product-knowledge
sections, that the facts behind an answer come from the ingested deck, that
the superseded architecture slide cannot leak into an answer, and that the
boundaries are stated where they belong.

NOT PROVEN HERE: the prose quality of a real answer. That needs a real
provider and a human reading it. Those cases are listed in
`REQUIRES_REAL_PROVIDER` and are explicitly NOT RUN.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import product_knowledge as pk
from backend.cockpit_v4 import states as st

#: The benchmark. `expect` names sections the question must retrieve.
BENCHMARK: list[dict] = [
    {"q": "Who are you?", "expect": ["positioning"]},
    {"q": "What is CreditProbe?", "expect": ["positioning"]},
    {"q": "What problem does CreditProbe solve?", "expect": ["audience"]},
    {"q": "Why would a CRO use CreditProbe?", "expect": ["audience"]},
    {"q": "What can CreditProbe do for a senior credit officer?",
     "expect": ["audience"]},
    {"q": "What does Cockpit do?", "expect": ["cockpit"]},
    {"q": "What is Early Warning?", "expect": ["early_warning"]},
    {"q": "Explain TAC.", "expect": ["tac"]},
    {"q": "What are the four Early Warning intelligence layers?",
     "expect": ["layers"]},
    {"q": "What does What-If do?", "expect": ["what_if"]},
    {"q": "What does Scorecard Validation do?",
     "expect": ["scorecard_validation"]},
    {"q": "What does Playbook do?", "expect": ["playbook"]},
    {"q": "What are Lenses?", "expect": ["lenses"]},
    {"q": "What does AI Project Planner do?", "expect": ["planner"]},
    {"q": "How do Cockpit and Early Warning differ?",
     "expect": ["cockpit", "early_warning"]},
    {"q": "How do Early Warning and What-If differ?",
     "expect": ["early_warning", "what_if"]},
    {"q": "How do Cockpit and What-If differ?",
     "expect": ["cockpit", "what_if"]},
    {"q": "How do Cockpit, What-If and Playbook work together?",
     "expect": ["cockpit", "what_if", "playbook"]},
    {"q": "How does Early Warning connect to Playbook?",
     "expect": ["early_warning", "playbook"]},
    {"q": "Can CreditProbe make credit decisions automatically?",
     "expect": ["boundaries"]},
    {"q": "Does CreditProbe replace a credit officer?",
     "expect": ["boundaries"]},
    {"q": "Can CreditProbe approve a limit?", "expect": ["boundaries"]},
    {"q": "Is the demo data real?", "expect": ["boundaries"]},
    {"q": "What are the seven main functionalities?",
     "expect": ["positioning"]},
    {"q": "What is the Detect, Diagnose, Decide, Drive Alignment idea?",
     "expect": ["arc"]},
    {"q": "What is Data Builder?", "expect": ["supporting"]},
    {"q": "What is Borrower 360?", "expect": ["supporting"]},
    {"q": "What is Graph Data?", "expect": ["supporting"]},
    {"q": "How does CreditProbe help committee preparation?",
     "expect": ["playbook"]},
    {"q": "What should I use if I want to stress the construction portfolio?",
     "expect": ["what_if"]},
]

#: Answer QUALITY for these needs a real provider and a human reading it.
#: Listed so the gap is visible rather than implied.
REQUIRES_REAL_PROVIDER = [case["q"] for case in BENCHMARK]


def test_the_benchmark_has_the_thirty_cases_the_instruction_names():
    assert len(BENCHMARK) == 30
    assert len({case["q"] for case in BENCHMARK}) == 30


@pytest.mark.parametrize("case", BENCHMARK, ids=[c["q"][:44] for c in BENCHMARK])
def test_each_question_retrieves_the_sections_it_needs(case):
    result = pk.retrieve(query=case["q"])
    returned = set(result["topics_returned"])
    missing = [t for t in case["expect"] if t not in returned]
    assert not missing, (
        f"{case['q']!r} retrieved {sorted(returned)} and needs {missing}")
    assert result["sections"], "a retrieval that returns nothing is a miss"


@pytest.mark.parametrize("case", BENCHMARK, ids=[c["q"][:44] for c in BENCHMARK])
def test_no_question_can_reach_the_superseded_architecture(case):
    """The deck's slide 14 must never describe the current runtime."""
    body = json.dumps(pk.retrieve(query=case["q"], detail="full")).lower()
    for leak in ("sonnet", "qwen", "mistral", "ownership gate",
                 "analysis plan", "thread-summary", "pass 1", "pass 2"):
        assert leak not in body, (
            f"{case['q']!r} retrieved text containing {leak!r} — the "
            f"superseded architecture must not be reachable")


def test_the_pack_records_the_architecture_slide_as_historical():
    historical = pk.pack()["historical_architecture"]
    assert historical["status"] == "HISTORICAL_ARCHITECTURE"
    assert historical["label"] == "NOT_CURRENT_V4_ARCHITECTURE"
    assert historical["applies_to_current_runtime"] is False
    assert historical["slide"] == 14
    for banned in ("Sonnet preprocessing passes",
                   "a separate ownership gate call",
                   "a separate analysis-plan call",
                   "a mandatory summary-after-answer flow"):
        assert banned in historical["must_not_reintroduce"]


def test_the_synopsis_does_not_carry_the_old_architecture():
    body = json.dumps(pk.synopsis()).lower()
    for leak in ("sonnet", "qwen", "ownership gate", "analysis plan"):
        assert leak not in body


# ---- facts that must match the ingested deck ---------------------------

def test_the_seven_functionalities_are_the_deck_s_seven():
    names = [m["name"] for m in pk.pack()["modules"]]
    assert names == ["Cockpit", "Early Warning Analysis", "What-If Analysis",
                     "Scorecard Validation", "Playbook", "Lenses",
                     "AI Project Planner"]


def test_the_arc_is_the_deck_s_arc():
    positioning = pk.pack()["positioning"]
    assert positioning["arc"] == ["Detect", "Diagnose", "Decide",
                                  "Drive Alignment"]
    assert positioning["arc_questions"]["Detect"] == "Where is risk building?"
    assert "not a rule assigning one module per stage" in positioning["arc_note"]


def test_tac_is_trigger_accelerator_classifier():
    tac = next(m for m in pk.pack()["modules"]
               if m["id"] == "early_warning")["tac"]
    assert tac["T"]["name"] == "Trigger"
    assert tac["A"]["name"] == "Accelerator"
    assert tac["C"]["name"] == "Classifier"
    assert "magnitude" in tac["A"]["dimensions"]
    assert "corroboration" in tac["A"]["dimensions"]


def test_the_four_layers_are_the_deck_s_four():
    layers = next(m for m in pk.pack()["modules"]
                  if m["id"] == "early_warning")["layers"]["items"]
    assert [x["layer"] for x in layers] == [1, 2, 3, 4]
    assert layers[0]["name"] == "Internal behavioural intelligence"
    assert layers[1]["name"] == "Credit and financial fundamentals"
    assert layers[2]["name"] == "External intelligence"
    assert layers[3]["name"] == "Graph and relationship intelligence"


def test_the_supporting_capabilities_are_not_called_main_functionalities():
    supporting = {c["name"] for c in pk.pack()["supporting_capabilities"]}
    main = {m["name"] for m in pk.pack()["modules"]}
    assert not supporting & main
    for expected in ("Data Builder", "Graph Data", "Borrower 360",
                     "Root-Cause Investigation", "Action Matrix",
                     "Escalation Matrix", "Transportable Investigation",
                     "Workflow"):
        assert expected in supporting
    body = json.dumps(pk.retrieve(topics=("supporting",)))
    assert "Not counted among the seven main functionalities" in body


def test_every_module_records_the_slide_it_came_from():
    for module in pk.pack()["modules"]:
        assert isinstance(module["slide"], int) and module["slide"] >= 2


def test_the_source_deck_is_recorded_with_its_digest():
    source = pk.pack()["source"]
    assert source["document"].endswith(".pdf")
    assert len(source["sha256"]) == 64
    assert source["pages"] == 14


# ---- boundaries --------------------------------------------------------

def test_the_forbidden_claims_are_recorded():
    never = pk.pack()["boundaries"]["never_claim"]
    for claim in ("autonomous credit approval",
                  "replacement of the credit officer",
                  "guaranteed early detection",
                  "guaranteed prediction of default",
                  "autonomous model approval",
                  "autonomous committee approval"):
        assert claim in never


@pytest.mark.parametrize("question", [
    "Can CreditProbe make credit decisions automatically?",
    "Does CreditProbe replace a credit officer?",
    "Can CreditProbe approve a limit?",
])
def test_a_boundary_question_retrieves_the_boundary(question):
    body = json.dumps(pk.retrieve(query=question))
    assert "never_claim" in body
    assert "autonomous credit approval" in body


def test_the_demo_data_answer_is_available_and_honest():
    body = json.dumps(pk.retrieve(query="Is the demo data real?"))
    assert "synthetic demonstration data" in body
    assert "never current portfolio values" in body


def test_every_worked_example_is_labelled_as_a_deck_example():
    """A deck figure must never be presentable as this book's number."""
    for module in pk.pack()["modules"]:
        example = module.get("worked_example")
        if example:
            assert example.get("label") == "deck example, not live data", (
                f"{module['name']} has an unlabelled worked example")
    body = json.dumps(pk.retrieve(topics=("examples",), detail="full"))
    assert "never current portfolio values" in body


def test_module_ownership_does_not_grant_data_access():
    cockpit = next(m for m in pk.pack()["modules"] if m["id"] == "cockpit")
    assert "historical and recorded corporate credit analysis" in cockpit["owns"]
    assert "Early Warning" in cockpit["does_not_own"]
    assert "What-If" in cockpit["does_not_own"]


# ---- the retrieval stays small -----------------------------------------

def test_the_synopsis_is_small_enough_to_carry_on_every_request():
    size = len(json.dumps(pk.synopsis()))
    assert size < 5_000, (
        f"the always-present synopsis is {size} characters; the whole point "
        f"is that the deck is NOT attached to every question")


def test_a_retrieval_is_bounded_and_says_what_it_omitted():
    result = pk.retrieve(topics=tuple(pk.TOPICS))
    assert len(result["sections"]) <= pk.MAX_SECTIONS
    if result.get("omitted_sections"):
        assert "Nothing was abbreviated" in result["omitted_note"]


def test_the_whole_pack_is_never_returned_by_one_call():
    whole = len(json.dumps(pk.pack()))
    biggest = max(
        len(json.dumps(pk.retrieve(query=case["q"], detail="full")))
        for case in BENCHMARK)
    assert biggest < whole / 2, (
        f"the largest retrieval is {biggest} of a {whole}-character pack; "
        f"retrieval must be selective")


# ---- the one run this file exercises -----------------------------------

def test_product_help_reaches_the_knowledge_tool_without_touching_data(drive):
    """MODEL MOCK. The tool is available to a product-help intent."""
    args = {"intent": intent("PRODUCT_HELP", "COCKPIT",
                             understood="what Early Warning does"),
            "query": "What does Early Warning do?", "topics": None,
            "detail": None}

    seen: dict = {}

    def answer(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        seen["result"] = body
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                  narrative="## Early Warning\n\nIt identifies emerging "
                            "deterioration."), "tu-2")])

    outcome, provider, record = drive("What does Early Warning do?", [
        ScriptedResult(tool_calls=[tool_call(
            "inspect_product_knowledge", args, "tu-1")]),
        answer])

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is False, (
        "product help must not touch the portfolio")
    assert seen["result"]["status"] == "ok"
    assert "early_warning" in seen["result"]["topics_returned"]
    assert seen["result"]["pack_version"] == pk.version()
