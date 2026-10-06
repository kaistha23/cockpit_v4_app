"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

The three cases this round has to finish, measured rather than described.

Each one asserts the same four things, because each of them was a separate
live failure: the run PUBLISHES, it makes no more model calls than the work
needs, it makes no catalogue call it was already given the answer to, and the
SQL runs exactly once.

The model is scripted, so "the analyst did not call inspect_catalog" would be
true by construction and worth nothing. What the scripts cannot fake is
SUFFICIENCY: every field each query names is checked against the context the
run was actually handed, so a passing test means the analyst COULD have
written that query without asking, not merely that this script did not.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import states as st

# ---- what a case file / canonical packet must make available ------------


def _resolvable_fields(sent: str) -> set[str]:
    """Every `relation.column` the run was handed before its first call.

    Read out of the bytes that would have gone to the provider, so this is
    what the analyst could see -- not what a helper thinks it built.
    """
    flat = sent.replace('\\"', '"')
    found: set[str] = set()
    for token in flat.replace('"', " ").replace(",", " ").split():
        if token.count(".") == 1 and token.startswith("cockpit_"):
            found.add(token)
    return found


def _assert_no_unanswerable_field(provider, fields):
    available = _resolvable_fields(provider.first_input_text())
    missing = sorted(set(fields) - available)
    assert not missing, (
        f"the query names {missing}, which the starting context does not "
        f"resolve -- so this run only avoided a catalogue call because the "
        f"script did")


def _spend(store_db, record):
    return store_db.get_run(record.run_id).budget


def _final_from(messages, *, narrative, subquestion):
    """A valid answer built from whatever the execution actually returned."""
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    artifact = step["artifact_id"]
    row = step["preview"][0]
    column = next(c for c in step["columns"]
                  if isinstance(row[c], (int, float)))
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative=narrative + " The figure is {{claim.v}}.",
              coverage=[{"subquestion": subquestion, "status": "answered",
                         "evidence_refs": [{"artifact_id": artifact,
                                            "row_key": "r0",
                                            "column_id": column}]}],
              numeric_claims=[{
                  "claim_id": "v", "decimal_value": str(row[column]),
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact, "row_key": "r0",
                               "column_id": column}}],
              tables=[{"title": subquestion, "artifact_id": artifact,
                       "columns": list(row)}]))])


# ---- CASE A: the simplest analytical question there is ------------------

EAD_FIELDS = ["cockpit_facility_quarter.ead_reported",
              "cockpit_facility_quarter.sector_name"]

EAD_SQL = """
SELECT sector_name,
       SUM(ead_reported) AS ead_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = ?
GROUP BY sector_name
ORDER BY ead_reported_sar_mn DESC
"""


def test_case_a_simple_ead_publishes_in_two_generations(drive, store_db,
                                                        release_id):
    """One action, one answer, nothing in between.

    A live run spent three generations and its entire deadline calling
    `inspect_catalog` for a question whose every term the server had already
    resolved.
    """
    quarter = oracles.latest_quarter(release_id)
    question = ("What is total exposure at default by sector in the latest "
                "quarter?")
    outcome, provider, record = drive(question, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        lambda m: _final_from(m, narrative="Exposure by sector.",
                              subquestion="EAD by sector")])

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    assert "{{claim." not in outcome.response["narrative"]

    _assert_no_unanswerable_field(provider, EAD_FIELDS)
    spend = _spend(store_db, record)
    assert spend["generation_attempts"][0] == 2
    assert spend["catalog_calls"][0] == 0
    assert spend["execution_submissions"][0] == 1
    assert outcome.call_report["generations"] == 2


# ---- CASE B: the seeded Manufacturing covenant thread -------------------

COVENANT_FIELDS = ["cockpit_covenant_quarter.borrower_id",
                   "cockpit_covenant_quarter.breach_date",
                   "cockpit_covenant_quarter.headroom_value",
                   "cockpit_covenant_quarter.covenant_name"]

COVENANT_SQL = """
SELECT c.borrower_id,
       COUNT(*) AS covenants_tested,
       SUM(CASE WHEN c.breach_date IS NOT NULL
                  OR c.headroom_value < 0 THEN 1 ELSE 0 END) AS breaches
FROM cockpit_covenant_quarter c
WHERE c.reporting_quarter = ?
  AND (c.headroom_value IS NOT NULL OR c.breach_date IS NOT NULL)
GROUP BY c.borrower_id
HAVING breaches > 0
ORDER BY breaches DESC
"""


def _seed_manufacturing_covenant(store_db, release_id):
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.set_thread_context(
        thread_id, tenant_id="demo-tenant", kind="attention_item", body={
            "segment": "Manufacturing", "segment_dimension": "sector_name",
            "reporting_quarter": "2026Q2", "comparison_quarter": "2026Q1",
            "metric": "covenant_breach_share",
            "metric_label": "Exposure with a covenant breach",
            "headline": "Manufacturing: more exposure sits under a "
                        "covenant breach"})
    return thread_id


def _run_in_thread(store_db, thread_id, release_id, question):
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question=question, mode="standard", release_id=release_id,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")
    return record


def test_case_b_the_seeded_covenant_thread_answers_and_keeps_answering(
        drive, store_db, release_id):
    """The live seeded failure: query ran, seven rows came back, run expired.

    Two turns, because a seeded thread that loses its subject on the second
    question is not a thread. The second turn asserts the seed and the first
    turn's answer are both still in front of the analyst.
    """
    quarter = oracles.latest_quarter(release_id)
    thread_id = _seed_manufacturing_covenant(store_db, release_id)

    first_q = "Which borrowers are in breach and on how many covenants?"
    record = _run_in_thread(store_db, thread_id, release_id, first_q)
    outcome, provider, _ = drive(first_q, [
        ScriptedResult(tool_calls=[_execute_call(
            COVENANT_SQL, purpose="Borrowers in covenant breach",
            grain="borrower", units="count",
            subquestions=["Borrowers in breach"],
            fields=COVENANT_FIELDS, quarter=quarter)]),
        lambda m: _final_from(m, narrative="Borrowers in breach.",
                              subquestion="Borrowers in breach")],
        record=record)

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    _assert_no_unanswerable_field(provider, COVENANT_FIELDS)
    spend = _spend(store_db, record)
    assert spend["generation_attempts"][0] == 2
    assert spend["catalog_calls"][0] == 0
    assert spend["execution_submissions"][0] == 1

    # -- the follow-up, in the same thread --
    second_q = "And how much exposure do those borrowers carry?"
    record2 = _run_in_thread(store_db, thread_id, release_id, second_q)
    outcome2, provider2, _ = drive(second_q, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="EAD by sector", grain="sector",
            units="SAR million", subquestions=["Exposure of those borrowers"],
            fields=EAD_FIELDS, quarter=quarter)]),
        lambda m: _final_from(m, narrative="Their exposure.",
                              subquestion="Exposure of those borrowers")],
        record=record2)

    assert outcome2.state == st.COMPLETED, outcome2.message
    sent = provider2.first_input_text()
    assert "ACTIVE INVESTIGATION" in sent, (
        "the second turn lost the card that opened the thread")
    assert "Manufacturing" in sent and "2026Q2" in sent
    assert "CASE FILE FOR THIS INVESTIGATION" in sent, (
        "the case file is a property of the thread, not of its first turn")
    assert first_q in sent, "the previous question is what 'those' refers to"
    assert "Borrowers in breach" in sent


# ---- CASE C: the broad one ---------------------------------------------

RISK_SQL = """
SELECT sector_name,
       SUM(ead_reported)                                   AS ead,
       SUM(ecl_reported)                                   AS ecl,
       SUM(CASE WHEN ifrs9_stage = 2 THEN ead_reported ELSE 0 END) AS stage2,
       SUM(CASE WHEN days_past_due > 0 THEN ead_reported ELSE 0 END)
                                                           AS past_due
FROM cockpit_facility_quarter
WHERE reporting_quarter = ?
GROUP BY sector_name
ORDER BY ecl DESC
"""

RISK_FIELDS = ["cockpit_facility_quarter.ead_reported",
               "cockpit_facility_quarter.ecl_reported",
               "cockpit_facility_quarter.ifrs9_stage",
               "cockpit_facility_quarter.days_past_due",
               "cockpit_facility_quarter.sector_name"]


def test_case_c_a_broad_question_is_answered_not_refused(drive, store_db,
                                                         release_id):
    """"Why is risk building across the book?" is a real question.

    It names no measure, no sector and no quarter, and every one of those is
    resolvable: the canonical semantics say what risk measures this domain
    has, and the release's own calendar says which quarter is latest. A run
    that comes back asking which of them was meant has refused to work.
    """
    quarter = oracles.latest_quarter(release_id)
    question = "Why is risk building across the book?"
    outcome, provider, record = drive(question, [
        ScriptedResult(tool_calls=[_execute_call(
            RISK_SQL, purpose="Risk measures by sector", grain="sector",
            units="mixed",
            subquestions=["Where is risk building?"],
            fields=RISK_FIELDS, quarter=quarter)]),
        lambda m: _final_from(m, narrative="Risk is concentrated.",
                              subquestion="Where is risk building?")])

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    assert outcome.response["coverage"][0]["status"] == "answered"

    _assert_no_unanswerable_field(provider, RISK_FIELDS)
    sent = provider.first_input_text()
    assert quarter in sent, (
        "the release's latest populated quarter must be resolved for the "
        "analyst; 'latest' is not a question to ask back")
    spend = _spend(store_db, record)
    assert spend["generation_attempts"][0] == 2
    assert spend["catalog_calls"][0] == 0
    assert spend["execution_submissions"][0] == 1


# ---- the sufficiency check is not vacuous -------------------------------

def test_the_sufficiency_check_fails_on_a_field_the_context_lacks(
        drive, store_db, release_id):
    """Otherwise the three cases above assert nothing.

    `cockpit_collateral_quarter.net_realizable_value` is a real column in this
    release and is NOT in the canonical packet or in any case file, so a
    query naming it genuinely would have to ask the catalogue first.
    """
    quarter = oracles.latest_quarter(release_id)
    _, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
         lambda m: _final_from(m, narrative="Exposure by sector.",
                               subquestion="EAD by sector")])

    with pytest.raises(AssertionError, match="does not resolve"):
        _assert_no_unanswerable_field(
            provider, ["cockpit_collateral_quarter.net_realizable_value"])
