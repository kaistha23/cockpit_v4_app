"""
MODEL MOCK · REAL DATABASE/RUNNER.

The three working checkpoints, end to end through the real worker, the real
store, the real event stream and -- for the two analytical cases -- the real
DuckDB session against the published release. Only the model is scripted.

Every numeric assertion is checked against `oracles.py`, which computes the
expected answer with pandas straight from the Parquet files and never touches
the code path under test.
"""

from __future__ import annotations

from decimal import Decimal

import oracles

#: Money in this release is a float column, so an exact decimal comparison
#: between two independent aggregations tests IEEE-754 formatting rather than
#: the analysis. One paisa on a million-scale figure is well below any tolerance
#: that could hide a real error (a dropped sector moves these by thousands).
TOLERANCE = Decimal("0.00001")


def close(a, b) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= TOLERANCE
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st


# ---- checkpoint 1: "Who are you?" -------------------------------------

def test_who_are_you_finishes_in_one_generation(drive, store_db):
    """V4-AT-007. One call, no metadata, no SQL, no summary, no Sonnet."""
    outcome, provider, record = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT",
                                understood="who this assistant is")))])])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 1, (
        "product help must not need a second generation")

    events = store_db.events_since(record.run_id)
    types = [e.event_type for e in events]
    assert ev.ANSWER_READY in types
    assert not [t for t in types if t.startswith("tool.started")], (
        "no tool execution belongs on the help path")

    body = store_db.get_run(record.run_id).final_response
    assert body["disposition"] == "answer"
    assert body["executed"] is False


def test_help_does_not_receive_the_whole_catalogue(drive):
    """V4-AT-031. The 991-field dictionary must not be in a help prompt."""
    outcome, provider, _ = drive(
        "Who are you?",
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response", final())])])
    sent = provider.first_input_text()

    # The index names relations; it must NOT carry field definitions.
    assert "cockpit_facility_quarter" in sent, "the index should be present"
    for definition_only_field in ("pd_ttc_lifetime_at_origination",
                                  "haircut_combination_method",
                                  "cash_conversion_cycle_days"):
        assert definition_only_field not in sent, (
            f"{definition_only_field} is a field DEFINITION and must be "
            f"fetched through inspect_catalog, not attached to every prompt")


def test_original_wording_reaches_the_model_unmodified(drive):
    """V4-AT-018. No preprocessing pass rewrites the user's question."""
    question = ("2026Q2 में Stage 2 exposure कितना बढ़ा, Real Estate को "
                "छोड़कर?")
    outcome, provider, _ = drive(
        question,
        [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                language="hi",
                                ambiguities=["which exposure measure"]),
                  disposition="clarification",
                  clarification_question="Which exposure measure?"))])])
    assert question in provider.first_input_text(), (
        "the original wording, numbers, script and exclusion must reach the "
        "analyst unmodified")
    assert outcome.state == st.WAITING_FOR_USER


# ---- checkpoint 2: EAD by sector ---------------------------------------

EAD_SQL = """
SELECT sector_name,
       SUM(ead_reported) AS ead_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = ?
GROUP BY sector_name
ORDER BY ead_reported_sar_mn DESC
"""


def _execute_call(sql: str, *, purpose: str, grain: str, units: str,
                  subquestions, fields, quarter: str, call_id="tu-x",
                  step_id="s1"):
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood=purpose, rationale="direct read"),
        "objective": purpose, "subquestions": list(subquestions),
        "scope": {"reporting_periods": [quarter], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": list(fields),
        "expected_output_grain": grain, "expected_units": units,
        "steps": [{"step_id": step_id, "language": "sql",
                   "code": sql.replace("?", f"'{quarter}'"),
                   "parameters": {}, "purpose": purpose,
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def test_ead_by_sector_matches_an_independent_oracle(drive, store_db,
                                                     release_id):
    """V4-AT-035. Real SQL, real data, checked against pandas."""
    quarter = oracles.latest_quarter(release_id)
    expected = oracles.ead_by_sector(release_id, quarter)
    top_sector = max(expected, key=lambda k: expected[k])

    def second_turn(messages):
        # The artifact id is only known after the execution, so the final
        # response is built from what actually came back.
        import json

        last = messages[-1]["content"][0]["content"]
        body = json.loads(last)
        step = body["steps"][0]
        artifact = step["artifact_id"]
        # The claim carries the EXACT stored value. A rounded restatement is
        # refused by the finalizer, which is the behaviour under test
        # elsewhere -- here the analyst quotes what the evidence holds and
        # sets display_precision for how it should read.
        cell = next(r["ead_reported_sar_mn"] for r in step["preview"]
                    if r["sector_name"] == top_sector)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood="EAD by sector, latest quarter"),
                  narrative=(f"In {quarter}, {top_sector} carries the "
                             f"largest reported exposure at "
                             f"{{{{claim.top_ead}}}}."),
                  coverage=[{"subquestion": "EAD by sector for the latest "
                                            "quarter",
                             "status": "answered",
                             "evidence_refs": [
                                 {"artifact_id": artifact,
                                  "row_key": f"sector_name={top_sector}",
                                  "column_id": "ead_reported_sar_mn"}]}],
                  numeric_claims=[{
                      "claim_id": "top_ead",
                      "decimal_value": repr(float(cell)),
                      "unit": "SAR million",
                      "display_precision": 2,
                      "evidence": {
                          "artifact_id": artifact,
                          "row_key": f"sector_name={top_sector}",
                          "column_id": "ead_reported_sar_mn"}}],
                  tables=[{"title": "Reported EAD by sector",
                           "artifact_id": artifact,
                           "columns": ["sector_name",
                                       "ead_reported_sar_mn"]}]))],
            output_tokens=400)

    outcome, provider, record = drive(
        "What is the EAD by sector for the latest quarter?",
        [ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector for the latest quarter",
            grain="sector", units="SAR million",
            subquestions=["EAD by sector for the latest quarter"],
            fields=["cockpit_facility_quarter.ead_reported",
                    "cockpit_facility_quarter.sector_name"],
            quarter=quarter)]),
         second_turn])

    assert outcome.state == st.COMPLETED, outcome.message
    body = outcome.response
    assert body["executed"] is True

    # The published narrative carries the RENDERED value, not a placeholder.
    assert "{{claim." not in body["narrative"]
    assert top_sector in body["narrative"]

    # And the stored artifact matches the independent oracle, row for row.
    artifact_id = body["numeric_claims"][0]["evidence"]["artifact_id"]
    stored = store_db.get_artifact(artifact_id, tenant_id="demo-tenant")
    produced = {str(r["sector_name"]): r["ead_reported_sar_mn"]
                for r in stored["rows"]}
    assert set(produced) == set(expected), (
        "the executed SQL returned a different set of sectors than the oracle")
    for sector, value in expected.items():
        assert close(produced[sector], value), (
            f"{sector}: SQL produced {produced[sector]} and the independent "
            f"oracle expects {value}")


def test_a_claim_that_does_not_match_the_artifact_is_refused(drive,
                                                             release_id):
    """V4-AT-093. A number that the evidence does not support is not published."""
    quarter = oracles.latest_quarter(release_id)

    def wrong_number(messages):
        import json

        body = json.loads(messages[-1]["content"][0]["content"])
        artifact = body["steps"][0]["artifact_id"]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="Exposure is {{claim.x}}.",
                  numeric_claims=[{
                      "claim_id": "x", "decimal_value": "999999.99",
                      "unit": "SAR million", "display_precision": 2,
                      "evidence": {"artifact_id": artifact, "row_key": "0",
                                   "column_id": "ead_reported_sar_mn"}}]))],
            )

    def give_up(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="partial_answer",
                  narrative="The figure could not be bound to the evidence.",
                  limitations=["the numeric claim did not match"]))])

    outcome, provider, _ = drive(
        "EAD by sector?",
        [ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=["cockpit_facility_quarter.ead_reported"],
            quarter=quarter)]),
         wrong_number, give_up])

    # Three generations: execute, rejected answer, corrected answer.
    assert len(provider.sent) == 3
    assert outcome.state == st.PARTIAL


# ---- checkpoint 3: Stage-2 change over the latest year -----------------

STAGE2_SQL = """
WITH stage2 AS (
  SELECT reporting_quarter, sector_name,
         SUM(ead_reported) AS stage2_ead_sar_mn
  FROM cockpit_facility_quarter
  WHERE ifrs9_stage = 2
    AND reporting_quarter IN (?, ??)
  GROUP BY reporting_quarter, sector_name
),
sectors AS (
  SELECT DISTINCT sector_name FROM stage2
)
SELECT s.sector_name,
       COALESCE(p.stage2_ead_sar_mn, 0) AS prior_stage2_ead_sar_mn,
       COALESCE(c.stage2_ead_sar_mn, 0) AS current_stage2_ead_sar_mn,
       COALESCE(c.stage2_ead_sar_mn, 0)
         - COALESCE(p.stage2_ead_sar_mn, 0) AS change_sar_mn,
       CASE WHEN p.stage2_ead_sar_mn IS NULL THEN 'entered'
            WHEN c.stage2_ead_sar_mn IS NULL THEN 'exited'
            ELSE 'present' END AS movement
FROM sectors s
LEFT JOIN stage2 p ON p.sector_name = s.sector_name
                  AND p.reporting_quarter = ?
LEFT JOIN stage2 c ON c.sector_name = s.sector_name
                  AND c.reporting_quarter = ??
ORDER BY change_sar_mn DESC
"""


def test_stage2_year_change_handles_entering_and_exiting_sectors(
        drive, store_db, release_id):
    """V4-AT-036. A full-outer comparison, checked against the oracle.

    The oracle deliberately includes sectors that ENTERED or EXITED stage 2
    over the year. An inner join drops them and the total then disagrees --
    which is exactly the silent error this case exists to catch.
    """
    expected = oracles.stage2_year_change(release_id)
    prior, current = expected["prior_quarter"], expected["current_quarter"]
    sql = STAGE2_SQL.replace("??", f"'{current}'").replace("?", f"'{prior}'")

    def finish(messages):
        import json

        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        artifact = step["artifact_id"]
        lead = step["preview"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood="stage 2 change over four "
                                           "quarters"),
                  narrative=(f"Stage 2 exposure moved by "
                             f"{{{{claim.total}}}} between {prior} and "
                             f"{current}."),
                  coverage=[{"subquestion": "stage 2 change over the year",
                             "status": "answered",
                             "evidence_refs": [{"artifact_id": artifact,
                                                "row_key": "0",
                                                "column_id": "change_sar_mn"}]}],
                  numeric_claims=[{
                      "claim_id": "total",
                      "decimal_value": repr(float(lead["change_sar_mn"])),
                      "unit": "SAR million", "display_precision": 2,
                      "evidence": {"artifact_id": artifact,
                                   "row_key": f"sector_name="
                                              f"{lead['sector_name']}",
                                   "column_id": "change_sar_mn"}}],
                  limitations=[
                      "sectors that entered or exited stage 2 are marked in "
                      "the movement column"]))])

    outcome, provider, record = drive(
        "How has Stage 2 exposure changed versus four quarters earlier, "
        "by sector?",
        [ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT",
                             understood="stage 2 change by sector"),
            "objective": "Stage 2 exposure now versus four quarters earlier",
            "subquestions": ["stage 2 exposure change by sector over four "
                             "quarters"],
            "scope": {"reporting_periods": [prior, current], "filters": {}},
            "metadata_receipt_ids": [],
            "fields_required": ["cockpit_facility_quarter.ifrs9_stage",
                                "cockpit_facility_quarter.ead_reported"],
            "expected_output_grain": "sector",
            "expected_units": "SAR million",
            "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                       "parameters": {}, "purpose": "stage 2 comparison",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""})]),
         finish])

    assert outcome.state == st.COMPLETED, outcome.message
    artifact_id = outcome.response["numeric_claims"][0]["evidence"][
        "artifact_id"]
    stored = store_db.get_artifact(artifact_id, tenant_id="demo-tenant")

    produced = {str(r["sector_name"]): r for r in stored["rows"]}
    for row in expected["by_sector"]:
        got = produced.get(row["sector"])
        assert got is not None, (
            f"{row['sector']} is in the oracle and missing from the result; "
            f"an inner join would do exactly this")
        assert close(got["change_sar_mn"], row["change"]), (
            f"{row['sector']}: SQL produced {got['change_sar_mn']} and the "
            f"independent oracle expects {row['change']}")
        assert got["movement"] == row["status"]

    # And the sectors that entered are actually present and marked.
    for sector in expected["entered"]:
        assert produced[sector]["movement"] == "entered"
        assert float(produced[sector]["prior_stage2_ead_sar_mn"]) == 0.0

    total = sum(Decimal(str(r["change_sar_mn"])) for r in stored["rows"])
    assert close(total, expected["total_change"]), (
        "the per-sector changes do not add up to the independently computed "
        "total change")
