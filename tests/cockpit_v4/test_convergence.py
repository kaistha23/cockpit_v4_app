"""REAL DATABASE · MODEL MOCK · REAL WORKER · BOTH BOOKS.

§28-§32. A simple question converges in one action turn and one answer turn,
and the two recovery budgets are separate.

What went wrong live
--------------------
Runs died at their deadline having answered nothing. The response was more
time -- 60s, then 120s -- and more time is not convergence: a run that needs
four catalogue calls to answer "what is EAD by sector" will spend whatever it
is given. §31 says it directly: the real fix is convergence, not only time.

So the bounds checked here are on WORK rather than on seconds:

    a simple question costs ONE action turn and ONE answer turn;
    it makes AT MOST one catalogue call, and none at all when the packet
      already carries the semantics it would have asked for;
    the tool result an action reads back is SMALL, because a result the
      analyst has to page through is a second action turn waiting to happen;
    the two structure-recovery budgets are SEPARATE, so a malformed action
      cannot consume the one allowance the answer has.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain, execute_call  # noqa: F401
from test_domain_execution import make_domain_run  # noqa: F401

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import states as st

from . import domain_oracles as oracle


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


#: One ordinary question per book, of the kind §32 calls simple: a measure,
#: a segment and a period, all of which the packet already resolves.
SIMPLE = {
    dom.CORPORATE: ("What is EAD by sector for the latest quarter?",
                    "corp_facility_quarter", "sector"),
    dom.RETAIL: ("What is EAD by product for the latest month?",
                 "retail_account_month", "product"),
}


def straight_through(domain_id: str):
    """The script a converged run follows: execute, then answer."""
    question, relation, dimension = SIMPLE[domain_id]
    period = oracle.latest_period(domain_id)
    sql = (f"SELECT {dimension}, SUM(ead_sar_mn) AS ead_sar_mn "
           f"FROM {relation} "
           f"WHERE {schema_mod.period_column(domain_id)} = '{period}' "
           f"GROUP BY {dimension} ORDER BY ead_sar_mn DESC")

    def answer(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        row = step["preview"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="answer",
                  narrative="The largest is {{claim.top}}.",
                  numeric_claims=[{
                      "claim_id": "top", "unit": "SAR million",
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": f"{dimension}={row[dimension]}",
                                   "column_id": "ead_sar_mn"}}]))])

    return question, [
        ScriptedResult(tool_calls=[execute_call(
            sql, purpose=f"EAD by {dimension}", grain=dimension,
            units="SAR million", subquestions=[f"EAD by {dimension}"],
            fields=[f"{relation}.ead_sar_mn", f"{relation}.{dimension}"],
            month=period)]),
        answer]


# ---- §32: one action turn, one answer turn ------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_simple_question_costs_one_action_and_one_answer(drive_domain,  # noqa: F811
                                                           domain_id):
    question, script = straight_through(domain_id)
    outcome, _provider, _record = drive_domain(domain_id, question, script)
    assert outcome.state == st.COMPLETED, outcome.message

    report = outcome.call_report
    assert report["generations"] == 2, report["by_purpose"]
    by_phase = report["by_phase"]
    assert by_phase.get("action") == 1, by_phase
    assert by_phase.get("answer") == 1, by_phase


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_simple_question_needs_no_catalogue_call_at_all(drive_domain,  # noqa: F811
                                                          domain_id):
    """§32. Zero, because the packet already carries the semantics a
    catalogue call would have gone looking for: the measure, the segment
    column, the relation and the period are all resolved before the first
    generation."""
    question, script = straight_through(domain_id)
    outcome, _provider, _record = drive_domain(domain_id, question, script)
    purposes = outcome.call_report["by_purpose"]
    assert "CATALOG" not in json.dumps(purposes).upper(), purposes


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_run_finishes_far_inside_its_allowance(drive_domain, domain_id):  # noqa: F811
    """§31. Time is a backstop, not the mechanism. A converged run should
    not be anywhere near its deadline, and a run that only just fits is one
    regression away from not fitting."""
    question, script = straight_through(domain_id)
    outcome, _provider, _record = drive_domain(domain_id, question, script)
    report = outcome.call_report
    assert report["elapsed_ms"] < report["deadline_seconds"] * 1000 / 2, (
        f"{report['elapsed_ms']}ms of a "
        f"{report['deadline_seconds']}s allowance")


# ---- §30: the action reads back something small -------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_an_action_result_is_small_enough_to_read_in_one_turn(drive_domain,  # noqa: F811
                                                              domain_id):
    """A result the analyst has to page through is a second action turn
    waiting to happen. The preview is bounded by row and by column, and the
    whole tool result stays inside the soft input budget."""
    captured: list[dict] = []
    question, script = straight_through(domain_id)

    def watch(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        captured.append(body)
        return script[1](messages)

    outcome, _provider, _record = drive_domain(
        domain_id, question, [script[0], watch])
    assert outcome.state == st.COMPLETED, outcome.message
    assert captured, "no tool result was read back"

    limits = config_mod.analytical_limits_for("standard")
    body = captured[0]
    size = len(json.dumps(body, ensure_ascii=False))
    # A rough token budget: four characters to a token is the usual
    # approximation, and the point is the ORDER of magnitude -- a result
    # that eats the soft input budget leaves no room for the question.
    assert size // 4 < limits.soft_input_tokens // 2, size
    for step in body["steps"]:
        assert len(step["preview"]) <= limits.preview_rows
        assert len(step["columns"]) <= limits.preview_columns


# ---- §29: the two recovery budgets are separate -------------------------

def _malformed_action(_messages):
    """An action turn whose structure the server cannot parse."""
    return ScriptedResult(tool_calls=[tool_call(
        "execute_analysis", {"objective": "", "steps": []})])


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_malformed_action_does_not_spend_the_answers_allowance(
        drive_domain, domain_id):  # noqa: F811
    """§29. The two are separate budgets, and this is the case that proves
    it: an action that had to be re-asked must leave the answer with its own
    one correction intact, or a single malformed tool call costs the run its
    ability to recover the answer it already has."""
    question, script = straight_through(domain_id)
    outcome, _provider, _record = drive_domain(
        domain_id, question, [_malformed_action] + script)
    assert outcome.state == st.COMPLETED, outcome.message
    # The action was re-asked; the answer still went through on its first
    # attempt, which is what "separate" means.
    report = outcome.call_report
    assert report["by_phase"].get("answer") == 1, report["by_phase"]


def test_the_budgets_are_declared_separately_and_neither_is_zero():
    """A shared counter would make §29 unenforceable however the run goes."""
    for mode in ("standard", "deep"):
        for limits in (config_mod.limits_for(mode),
                       config_mod.analytical_limits_for(mode)):
            assert limits.format_regenerations >= 1
            assert limits.answer_format_regenerations >= 1
            assert limits.answer_corrections >= 1


def test_the_analytical_allowances_are_the_ones_the_round_specifies():
    """§31. The analysis allowance, standard and deep."""
    assert config_mod.analytical_limits_for(
        "standard").deadline_seconds == (
        config_mod.ANALYTICAL_STANDARD_LIMITS.deadline_seconds)
    assert config_mod.analytical_limits_for("deep").deadline_seconds == 240.0
    # And the tight product-help allowance is unchanged: widening everything
    # would be the opposite of the point.
    assert config_mod.limits_for("standard").deadline_seconds == 60.0


# ---- §31: finalize from the result, not by reopening planning -----------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_answer_turn_is_offered_the_answer_tool(drive_domain,  # noqa: F811
                                                    domain_id):
    """§31. Once there is a valid result, the next turn's job is to WRITE
    the answer. A turn still holding the full action tool set is a turn
    invited to plan again, and planning again on a finished analysis is how
    a run spends its reserve and finalizes nothing."""
    seen: list[list[str]] = []
    question, script = straight_through(domain_id)

    def watch(messages):
        return script[1](messages)

    outcome, provider, _record = drive_domain(
        domain_id, question, [script[0], watch])
    assert outcome.state == st.COMPLETED, outcome.message

    for call in outcome.call_report["calls"]:
        if call.get("phase") == "answer":
            seen.append(sorted(call.get("tools_offered") or []))
    assert seen, "no answer turn was recorded"
    for offered in seen:
        assert "finalize_response" in offered
        assert "inspect_product_knowledge" not in offered, (
            "a finished analysis is not a product question")
