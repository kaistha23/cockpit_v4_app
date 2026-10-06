"""REAL DATABASE · MODEL MOCK. A planned result's figures are claimed by the server.

The answer turn for a planned submission receives an `answer_kit`: every
figure already bound to its cell (or to arithmetic over cells), under a
readable placeholder. The model writes prose around the placeholders and may
send no `numeric_claims` at all. These tests pin that the kit describes the
compiled SQL's real columns, that totals and shares appear only where they
mean something, that an answer built only from placeholders passes the SAME
finalizer as any other, and that a wrong placeholder is still refused.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from backend.cockpit_v4 import answer_draft as ad
from backend.cockpit_v4 import plan_compiler as pc
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.execute_tool import StepResult
from backend.cockpit_v4.model_capabilities import ModelTraits
from conftest import ScriptedResult, final, intent, tool_call


@pytest.fixture
def book(session, runtime):
    return pc.build_book(domain_id="corporate", catalog=runtime.catalog, session=session)


def _step(session, plan, book) -> tuple[StepResult, pc.StepShape]:
    steps, objective = pc.compile_plan(plan, book)
    cursor = session.connection.execute(steps[0]["code"])
    columns = [d[0] for d in cursor.description]
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    result = StepResult(step_id="plan_1", status="ok", language="sql", code_digest="d",
                        purpose=objective, columns=columns, row_count=len(rows),
                        preview=rows, row_ids=[f"r{i}" for i in range(len(rows))],
                        artifact_id="art-1")
    return result, pc.output_columns(plan, book)["plan_1"]


@pytest.mark.parametrize("plan", [
    {"measures": ["ead_reported"], "dimensions": ["sector_name"]},
    {"measures": ["ead_reported"], "dimensions": ["sector_name"], "compare": "prior"},
    {"measures": ["ead_reported"], "period": {"kind": "last_n", "n": 3}},
    {"measures": ["ead_reported"]},
])
def test_the_shape_names_exactly_the_columns_the_compiled_sql_returns(session, book, plan):
    result, shape = _step(session, plan, book)
    assert [c.name for c in shape.columns] == result.columns


def test_every_cell_is_drafted_with_a_readable_id_and_a_total_and_shares(session, book):
    result, shape = _step(session, {"measures": ["ead_reported"], "dimensions": ["sector_name"]}, book)
    drafted = ad.draft(result, shape, money_unit="SAR million")
    ids = {d.claim.claim_id for d in drafted}
    first = result.preview[0]["sector_name"]
    assert f"ead_reported.{ad._slug(first)}" in ids
    assert "ead_reported.total" in ids
    assert f"ead_reported.{ad._slug(first)}.share" in ids
    assert all(re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", i) for i in ids)


@pytest.mark.parametrize("plan", [
    {"measures": ["ead_reported"], "dimensions": ["sector_name"], "limit": 3},
    {"measures": ["ead_reported"], "period": {"kind": "last_n", "n": 3}},
])
def test_no_total_over_a_top_n_or_over_periods(session, book, plan):
    result, shape = _step(session, plan, book)
    roles = {d.role for d in ad.draft(result, shape, money_unit="SAR million")}
    assert "value" in roles and not roles & {"total", "share"}


def test_a_comparison_drafts_the_prior_value_and_the_change(session, book):
    result, shape = _step(session, {"measures": ["ead_reported"], "compare": "prior"}, book)
    drafted = ad.draft(result, shape, money_unit="SAR million")
    assert {d.claim.claim_id for d in drafted} >= {
        "ead_reported", "ead_reported_prev", "ead_reported_change", "ead_reported_pct_change"}
    line = ad.headline(drafted, result.purpose)
    assert "{{claim.ead_reported_prev}}" in line and "{{claim.ead_reported_change}}" in line


def test_the_headline_uses_only_drafted_placeholders(session, book):
    result, shape = _step(session, {"measures": ["ead_reported"], "dimensions": ["sector_name"]}, book)
    drafted = ad.draft(result, shape, money_unit="SAR million")
    used = set(re.findall(r"\{\{claim\.([^}]+)\}\}", ad.headline(drafted, result.purpose)))
    assert used and used <= {d.claim.claim_id for d in drafted}
    assert not re.search(r"\d{3,}", re.sub(r"\{\{[^}]+\}\}", "", ad.headline(drafted, "")))


def test_a_null_cell_is_never_drafted(session, book):
    result, shape = _step(session, {"measures": ["ead_reported"], "dimensions": ["sector_name"]}, book)
    result.preview[0]["ead_reported"] = None
    drafted = ad.draft(result, shape, money_unit="SAR million")
    assert all(d.claim.evidence.row_key != "r0" for d in drafted)
    assert not any(d.role in ("total", "share") for d in drafted)


# ---- end to end: the same finalizer, the same rules ------------------------

def _plan_call(plan):
    return tool_call("execute_analysis", {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
                                          "plan": plan}, "tu-p")


def _kit(messages):
    body = json.loads(messages[-1]["content"][0]["content"])
    return body["steps"][0]


def _answer(narrative_for):
    def respond(messages):
        step = _kit(messages)
        return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT"), narrative=narrative_for(step["answer_kit"]),
            numeric_claims=[]), "tu-f")])
    return respond


def test_an_answer_of_placeholders_alone_is_published_with_checked_figures(drive, runtime, capability):
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    seen = {}

    def narrative(kit):
        seen["kit"] = kit
        share = next(f for f in kit["figures"] if f["placeholder"].endswith(".share}}"))
        return f"{kit['headline']} The largest sector carries {share['placeholder']} of it."

    outcome, _, _ = drive("EAD by sector this quarter", [
        ScriptedResult(tool_calls=[_plan_call({"measures": ["ead_reported"], "dimensions": ["sector_name"]})]),
        _answer(narrative)])
    assert outcome.state == st.COMPLETED, outcome.message
    published = outcome.response
    assert "{{" not in published["narrative"]
    shown = {c["claim_id"]: c.get("display_value") for c in published["numeric_claims"]}
    assert "ead_reported.total" in shown and all(shown.values())
    by_placeholder = {f["placeholder"]: f["value"] for f in seen["kit"]["figures"]}
    assert shown["ead_reported.total"] == by_placeholder["{{claim.ead_reported.total}}"]
    assert published["evidence_bound"] is True


def test_the_kit_replaces_the_generic_citation_guide(drive, runtime, capability):
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    seen = {}

    def narrative(kit):
        return kit["headline"]

    def capture(messages):
        seen["step"] = _kit(messages)
        return _answer(narrative)(messages)

    outcome, _, _ = drive("EAD by sector this quarter", [
        ScriptedResult(tool_calls=[_plan_call({"measures": ["ead_reported"], "dimensions": ["sector_name"]})]),
        capture])
    assert outcome.state == st.COMPLETED, outcome.message
    assert "answer_kit" in seen["step"] and "how_to_cite_these_numbers" not in seen["step"]


def test_a_placeholder_that_was_not_drafted_is_still_refused(drive, runtime, capability):
    runtime.capability = dataclasses.replace(capability, traits=ModelTraits(plan_compiler=True))
    seen = {}

    def correction(messages):
        seen["error"] = messages[-1]["content"][0]["content"]
        return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("DATA_ANALYSIS", "COCKPIT"),
            narrative="EAD was {{claim.ead_reported.total}}."), "tu-f2")])

    outcome, _, _ = drive("EAD by sector this quarter", [
        ScriptedResult(tool_calls=[_plan_call({"measures": ["ead_reported"], "dimensions": ["sector_name"]})]),
        _answer(lambda kit: "EAD was {{claim.ead_total}}."),
        correction])
    assert "ead_total" in seen["error"] and "ready_figures" in seen["error"]
    assert outcome.state == st.COMPLETED, outcome.message


def test_a_model_without_the_trait_never_receives_a_kit(drive, runtime):
    from test_catalog_convergence import EAD_SQL, execute_call, finish
    import oracles
    seen = {}

    def capture(messages):
        seen["body"] = messages[-1]["content"][0]["content"]
        return finish(messages)

    quarter = oracles.latest_quarter(runtime.cfg.release_id)
    outcome, _, _ = drive("EAD by sector this quarter",
                          [ScriptedResult(tool_calls=[execute_call(EAD_SQL, {"1": quarter})]), capture])
    assert outcome.state == st.COMPLETED, outcome.message
    assert "answer_kit" not in seen["body"]
