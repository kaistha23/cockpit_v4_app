"""
MODEL MOCK · UNIT.

Two things that are easy to claim and easy to get wrong: that a question in
another language survives the trip to the analyst intact, and that this suite
never presents a fixture as a live measurement.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st

TESTS = Path(__file__).resolve().parent

#: Human-reviewed fixtures. Each carries a number, an entity and an exclusion,
#: because those are the three things a preprocessing pass used to lose. The
#: `expected` column is what a competent human reader takes the request to
#: mean -- it is the review, written down, not a model output.
MULTILINGUAL = [
    {
        "language": "Hindi",
        "question": "2026Q2 में Stage 2 exposure कितना है, Real Estate को "
                    "छोड़कर?",
        "must_survive": ["2026Q2", "Stage 2", "Real Estate"],
        "expected": ("Stage 2 exposure for reporting quarter 2026Q2, "
                     "EXCLUDING the Real Estate sector."),
    },
    {
        "language": "Bengali",
        "question": "2025Q4 এ Manufacturing ছাড়া কোন সেক্টরে EAD সবচেয়ে বেশি?",
        "must_survive": ["2025Q4", "Manufacturing", "EAD"],
        "expected": ("Which sector has the highest EAD in 2025Q4, EXCLUDING "
                     "Manufacturing."),
    },
    {
        "language": "Arabic",
        "question": "ما هو إجمالي التعرض في 2026Q1 باستثناء قطاع العقارات؟",
        "must_survive": ["2026Q1"],
        "expected": ("Total exposure in 2026Q1, EXCLUDING the real-estate "
                     "sector. 'Exposure' is ambiguous and must be clarified "
                     "or sourced from the dictionary."),
    },
    {
        "language": "Hinglish",
        "question": "Latest quarter mein Stage 2 exposure kitna badha hai, "
                    "IT sector chhod ke?",
        "must_survive": ["Stage 2", "IT"],
        "expected": ("How much Stage 2 exposure increased in the latest "
                     "quarter, EXCLUDING the IT sector."),
    },
]


@pytest.mark.parametrize("fixture", MULTILINGUAL,
                         ids=[f["language"] for f in MULTILINGUAL])
def test_the_original_question_reaches_the_analyst_intact(drive, fixture):
    """V4-AT-030. No mandatory translation call, and nothing rewritten.

    The assertion is deliberately on the BYTES that would have gone to the
    provider. V3 sent a Sonnet-normalised paraphrase and kept the original
    only for display, which is how a negation or an exclusion goes missing
    without anything in the trace saying so.
    """
    outcome, provider, _ = drive(fixture["question"], [
        ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT",
                          understood=fixture["expected"],
                          language=fixture["language"],
                          ambiguities=["which exposure measure"],
                          excluded=[]),
            disposition="clarification",
            narrative="One question first.",
            clarification_question="Which exposure measure do you mean?"))])])

    sent = provider.first_input_text()
    assert fixture["question"] in sent, (
        f"the {fixture['language']} question did not reach the analyst "
        f"unmodified")
    for token in fixture["must_survive"]:
        assert token in sent, (
            f"{token!r} — a number, entity or exclusion — was lost")
    assert outcome.state == st.WAITING_FOR_USER
    assert len(provider.sent) == 1, "no translation round trip"


def test_no_test_in_this_suite_claims_a_live_provider_measurement():
    """V4-AT-100. Fixtures, local estimates and oracles stay distinguishable.

    Every test module must declare its evidence label in its docstring, and
    none of them may claim REAL PROVIDER — because no paid run was authorized
    in this environment. If a live suite is added later, it declares REAL
    PROVIDER and this test's exclusion list is what has to be updated
    deliberately.
    """
    allowed = {"MODEL MOCK", "REAL DATABASE/RUNNER", "REAL DATABASE",
               "REAL HTTP", "REAL SOURCE", "REAL PROCESS", "UNIT",
               "REPRODUCTION", "REAL RUNNER", "REAL SSE",
               # A suite with no provider in it AT ALL -- not even a scripted
               # one. The data-plane and dashboard suites are this: real
               # parquet, real DuckDB, and nothing that could be mistaken for
               # evidence about a model. It is a stronger claim than MODEL
               # MOCK and it needed its own word.
               "NO MODEL",
               "INDEPENDENT ORACLE",
               "in-process ASGI client"}
    for path in sorted(TESTS.glob("test_*.py")):
        header = path.read_text(encoding="utf-8")[:900]
        assert "REAL PROVIDER" not in header, (
            f"{path.name} claims a real provider; no paid run was authorized")
        declared = [label for label in allowed if label in header]
        assert declared, (
            f"{path.name} declares no evidence label. A reader cannot tell "
            f"what its passing result actually establishes.")


def test_the_numerical_oracles_are_independent_of_the_code_under_test():
    """V4-AT-100. An oracle that reuses the pipeline proves self-consistency.

    Self-consistency is exactly what a wrong answer also has, so the oracle
    module must not import the catalog, the session, the validator or the
    orchestration.
    """
    source = (TESTS / "oracles.py").read_text(encoding="utf-8")
    for forbidden in ("cockpit_v4", "duckdb", "sql", "catalog",
                      "execute_tool", "orchestration"):
        assert f"import {forbidden}" not in source
        assert f"from backend.cockpit_v4" not in source
    # It is allowed exactly one thing from V3: where the Parquet files live.
    assert "from backend.cockpit_agentic import store" in source
    assert "read_parquet" in source


def test_the_acceptance_inventory_records_how_each_case_was_checked():
    """V4-AT-099, V4-AT-100. Evidence labels, and no unearned green."""
    inventory = json.loads(
        (TESTS.parent.parent / "docs" / "cockpit_v4"
         / "ACCEPTANCE_CASES.json").read_text(encoding="utf-8"))
    assert inventory["count"] == 100
    for case in inventory["cases"]:
        evidence = case["evidence"]
        assert evidence["status"] in ("COVERED", "NOT RUN")
        assert evidence["real_provider"].startswith("NOT RUN"), (
            f"{case['id']} claims real-provider evidence, which this build "
            f"does not have")
        if evidence["status"] == "COVERED":
            assert evidence["tests"], f"{case['id']} is covered by nothing"


def test_the_regression_baseline_is_recorded_with_its_command():
    """V4-AT-099. A matched baseline, not an assumed one."""
    report = (TESTS.parent.parent / "docs" / "cockpit_v4"
              / "REGRESSION_REPORT.md")
    assert report.exists(), "the regression report is a deliverable"
    text = report.read_text(encoding="utf-8")
    for required in ("pytest", "tests collected", "failures", "errors",
                     "skipped"):
        assert required in text, f"the report must record {required}"
