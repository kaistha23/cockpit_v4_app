"""REAL DATABASE · MODEL MOCK · UNIT. One legal transition at a time.

§15, §16, §17, §18. CreditProbe knows the orchestration STATE; Opus owns
the analytical CONTENT. These assert the first half and, just as
deliberately, that the second half is untouched: nothing here decides a
relation, a measure, an aggregation, a filter or a comparison.

The control case matters as much as the happy ones. A gate that only ever
says "execute" is not a gate, it is a hard-coded first action -- so there
is a question below whose governed metadata genuinely is not in the packet,
and it must get the catalogue.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import action_state as acts
from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import contracts as c
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st

import test_mac_action_replay as mac
from test_action_matrices import (CORPORATE_QUESTIONS, RETAIL_QUESTIONS,
                                  _one_analysis, _query_from_packet)
from test_domain_execution import drive_domain, make_domain_run  # noqa: F401

RETAIL_DELINQUENCY = "Where is delinquency building?"


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


def surfaces(provider) -> list[set[str]]:
    return [{t["name"] for t in sent["tools"]} for sent in provider.sent]


def choices(provider) -> list:
    return [sent["tool_choice"] for sent in provider.sent]


# ---- §15. an execution-ready turn is offered execute_analysis, alone ---

@pytest.mark.parametrize("domain_id,question", [
    (dom.CORPORATE, mac.STAGE2),
    (dom.RETAIL, RETAIL_DELINQUENCY),
])
def test_an_execution_ready_turn_can_only_execute(drive_domain, domain_id,
                                                  question):
    outcome, provider, _ = drive_domain(
        domain_id, question, _one_analysis(domain_id, question))
    assert outcome.state == st.COMPLETED, outcome.message

    first = surfaces(provider)[0]
    assert first == {"execute_analysis"}, first
    assert "inspect_product_knowledge" not in first
    assert "inspect_catalog" not in first
    assert "finalize_response" not in first, (
        "nothing has been executed, and every number in a V4 answer is "
        "bound to a result that ran")
    assert provider.sent[0]["tool_choice"] == {
        "type": "tool", "name": "execute_analysis",
        "disable_parallel_tool_use": True}


@pytest.mark.parametrize("domain_id,question", [
    (dom.CORPORATE, mac.STAGE2),
    (dom.RETAIL, RETAIL_DELINQUENCY),
])
def test_after_execution_only_the_answer_surface_appears(drive_domain,
                                                         domain_id,
                                                         question):
    outcome, provider, _ = drive_domain(
        domain_id, question, _one_analysis(domain_id, question))
    assert outcome.state == st.COMPLETED, outcome.message

    last = surfaces(provider)[-1]
    assert last <= {"finalize_response", "read_artifact"}
    assert "finalize_response" in last
    assert provider.sent[-1]["tool_choice"]["name"] == "finalize_response"


def test_the_gate_decides_the_transition_and_not_the_analysis():
    """The line this round must not cross.

    `action_state` may say "execute". It may not say WHAT to execute, and
    there is no code in it that could: no SQL, no relation, no column, no
    aggregation, no period arithmetic. Checked against the CODE -- the
    docstrings are free to discuss all of it, and do.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(acts.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    code = ast.unparse(tree).lower()

    for forbidden in ("select ", "group by", "sum(", " where ",
                      "reporting_quarter", "reporting_month", "sar",
                      "ead_", "ecl_", "order by"):
        assert forbidden not in code, (
            f"action_state's code mentions {forbidden!r}; choosing the "
            f"analysis is the analyst's")


# ---- §16. the seeded investigation executes directly ------------------

def test_the_seeded_construction_investigation_executes_first(drive_domain,
                                                              store_db):
    record, card = mac.seeded_construction_run(store_db)
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, mac.CONSTRUCTION,
        [ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)]),
         mac.answer_from_result],
        record=record)

    assert outcome.state == st.COMPLETED, outcome.message
    assert surfaces(provider)[0] == {"execute_analysis"}
    assert mac.catalog_calls(provider) == 0
    assert not any("inspect_product_knowledge" in s
                   for s in surfaces(provider)), (
        "a seeded investigation never needs the product pack")
    assert outcome.call_report["generations"] == 2


# ---- §17. the control: metadata that genuinely is not there -----------

UNRESOLVED = ("What is the gizmo ratio of the frobnicator, by widget "
              "class?")


def test_a_question_the_packet_cannot_answer_gets_the_catalogue():
    book = arun.for_domain(dom.CORPORATE)
    readiness = sem.readiness(book.catalog, UNRESOLVED)
    assert readiness["sufficient"] is False

    decision = acts.decide(executed=False, answer_only=False,
                           analytical=True, readiness=readiness)
    assert decision.state == acts.NEEDS_METADATA
    assert decision.tools == ("inspect_catalog",)
    assert decision.require == "inspect_catalog"


def test_the_catalogue_is_a_state_a_run_passes_through(drive_domain,
                                                       store_db):
    """It reads what it said was missing, and then it uses it.

    Without this, NEEDS_METADATA is a state a run can sit in -- which is
    the live Construction thread that read the catalogue twice and answered
    nothing.
    """
    quarter = mac.latest_quarter()
    catalog_call = tool_call("inspect_catalog", {
        "query": "gizmo ratio", "relation_ids": [], "field_ids": [],
        "detail": ["discovery"], "reporting_periods": [], "sample_rows": 0,
        "cursor": ""}, "tu-cat")
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, UNRESOLVED,
        [ScriptedResult(tool_calls=[catalog_call]),
         ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)]),
         mac.answer_from_result])

    assert outcome.state == st.COMPLETED, outcome.message
    offered = surfaces(provider)
    assert offered[0] == {"inspect_catalog"}, (
        "a question whose metadata is missing opens on the catalogue")
    assert offered[1] == {"execute_analysis"}, (
        "having read it, the next legal transition is to use it")
    assert offered[2] <= {"finalize_response", "read_artifact"}


# ---- §18. the matrix, measured ----------------------------------------

MATRIX = ([(dom.CORPORATE, q) for q in CORPORATE_QUESTIONS]
          + [(dom.RETAIL, q) for q in RETAIL_QUESTIONS])


@pytest.mark.parametrize("domain_id,question", MATRIX)
def test_every_matrix_question_converges(drive_domain, domain_id, question):
    """First legal action, provider calls, catalogue calls, publication."""
    outcome, provider, _ = drive_domain(
        domain_id, question, _one_analysis(domain_id, question))

    assert outcome.state == st.COMPLETED, f"{question!r}: {outcome.message}"
    assert outcome.response["executed"] is True
    assert outcome.response.get("narrative"), "executing is not publishing"
    assert surfaces(provider)[0] == {"execute_analysis"}
    assert mac.catalog_calls(provider) == 0
    assert outcome.call_report["generations"] == 2, (
        f"{question!r} took {outcome.call_report['generations']} "
        f"generations; the target is one action and one answer")


def test_the_matrix_measurements_are_recorded(drive_domain, tmp_path):
    rows = []
    for domain_id, question in MATRIX:
        outcome, provider, _ = drive_domain(
            domain_id, question, _one_analysis(domain_id, question))
        rows.append({
            "book": domain_id, "question": question,
            "first_legal_action": sorted(surfaces(provider)[0])[0],
            "provider_calls": outcome.call_report["generations"],
            "catalogue_calls": mac.catalog_calls(provider),
            "executions": 1 if outcome.response["executed"] else 0,
            "published": bool(outcome.response.get("narrative")),
        })
    (tmp_path / "matrix.json").write_text(json.dumps(rows, indent=1))
    assert all(r["published"] for r in rows)
    assert all(r["catalogue_calls"] == 0 for r in rows)
    assert all(r["first_legal_action"] == "execute_analysis" for r in rows)
    for row in rows:
        print(f"{row['book'][:4]} {row['question'][:46]:<48} "
              f"first={row['first_legal_action']:<17} "
              f"calls={row['provider_calls']} "
              f"catalogue={row['catalogue_calls']}")


# ---- §20. five repetitions of each named replay -----------------------

@pytest.mark.parametrize("run", range(1, 6))
def test_the_stage2_replay_five_times(drive_domain, store_db, run):
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_domain(dom.CORPORATE, mac.STAGE2, [
        mac.truncated_action(),
        ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)]),
        mac.answer_from_result])
    assert outcome.state == st.COMPLETED, f"run {run}: {outcome.message}"
    assert outcome.error_code not in (st.CALL_LIMIT,
                                      st.ACTION_FORMAT_EXHAUSTED)
    assert surfaces(provider)[1] <= surfaces(provider)[0]


@pytest.mark.parametrize("run", range(1, 6))
def test_the_construction_replay_five_times(drive_domain, store_db, run):
    record, _ = mac.seeded_construction_run(store_db)
    quarter = mac.latest_quarter()
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, mac.CONSTRUCTION,
        [mac.truncated_action(),
         ScriptedResult(tool_calls=[mac.ecl_by_sector(quarter)]),
         mac.answer_from_result], record=record)
    assert outcome.state == st.COMPLETED, f"run {run}: {outcome.message}"
    assert mac.catalog_calls(provider) == 0


@pytest.mark.parametrize("run", range(1, 6))
def test_the_retail_delinquency_replay_five_times(drive_domain, store_db,
                                                  run):
    outcome, provider, _ = drive_domain(
        dom.RETAIL, RETAIL_DELINQUENCY,
        [mac.truncated_action()]
        + _one_analysis(dom.RETAIL, RETAIL_DELINQUENCY))
    assert outcome.state == st.COMPLETED, f"run {run}: {outcome.message}"
    assert outcome.response["executed"] is True
    assert surfaces(provider)[0] == {"execute_analysis"}
