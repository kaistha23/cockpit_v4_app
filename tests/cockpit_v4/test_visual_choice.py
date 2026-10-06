"""MODEL MOCK · REAL DATABASE/RUNNER · REPRODUCTION.

A chart because a chart helps, not because there were rows.

The defect this module exists for
---------------------------------
"Show me the customers behind this" came back with one bar per borrower --
a wall of bars with a label column nobody could align, above a table that
said the same thing better. The chart validator had one question: do these
columns exist in the artifact? They did, so it published.

Existence is not usefulness. Two counts decide whether a picture can say
anything at all, and both are properties of the RESULT rather than of the
question:

    one point    is not a comparison
    ninety points is not one either

Between them, a chart. A twelve-sector ranking passes. A ten-borrower top-N
passes, because ten points is a readable comparison whatever the rows are
called -- this does not guess at intent from column names or grain, which
would be a different and much worse rule.
"""

from __future__ import annotations

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import finalization as fin
from backend.cockpit_v4 import states as st

QUESTION = "Show the exposure behind this."

#: Every borrower and facility, one row each: a detail list, not a ranking.
DETAIL_SQL = """
SELECT facility_id,
       SUM(ead_reported) AS ead_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = ?
GROUP BY facility_id
ORDER BY ead_reported_sar_mn DESC
"""

DETAIL_FIELDS = ["cockpit_facility_quarter.ead_reported",
                 "cockpit_facility_quarter.facility_id"]


def _run(drive, release_id, *, sql, fields, grain, chart_label, limit=""):
    quarter = oracles.latest_quarter(release_id)

    def answer(messages):
        from test_orchestration_recovery import _execution_result

        step = _execution_result(messages)["steps"][0]
        measure = next(c for c in step["columns"] if c.endswith("_mn"))
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="Here is what the evidence shows.",
                  tables=[{"title": "Result", "artifact_id":
                           step["artifact_id"], "columns": step["columns"]}],
                  charts=[{"kind": "bar", "artifact_id": step["artifact_id"],
                           "title": "Result", "x_column": chart_label,
                           "y_columns": [measure],
                           "unit": "SAR million"}]))])

    code = sql.replace("?", f"'{quarter}'")
    if limit:
        code = code.rstrip() + f"\n{limit}"
    return drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            code, purpose="Result", grain=grain, units="SAR million",
            subquestions=["result"], fields=fields, quarter=quarter)]),
        answer])


def test_a_sector_ranking_keeps_its_chart(drive, release_id):
    """§28, §51. Twelve categories ranked: exactly what a bar chart is for."""
    outcome, _, _ = _run(drive, release_id, sql=EAD_SQL, fields=EAD_FIELDS,
                         grain="sector", chart_label="sector_name")
    assert outcome.state == st.COMPLETED, outcome.message
    charts = outcome.response["charts"]
    assert charts, "a ranked sector comparison lost its chart"
    assert fin.MIN_CHART_POINTS <= len(charts[0]["points"]) <= \
        fin.MAX_CHART_POINTS


def test_a_facility_detail_list_does_not_get_a_chart(drive, release_id):
    """§29, §51. The live defect: one bar per borrower, above a table."""
    outcome, _, _ = _run(drive, release_id, sql=DETAIL_SQL,
                         fields=DETAIL_FIELDS, grain="facility",
                         chart_label="facility_id")
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["charts"] == [], (
        "a detail list was published as a chart")
    # The table is still there. Dropping the picture does not drop the answer.
    assert outcome.response["tables"], "the table went with the chart"
    warnings = outcome.response["validation"]["warnings"]
    assert any("points" in w for w in warnings), warnings


def test_a_top_ten_of_the_same_list_keeps_its_chart(drive, release_id):
    """§51. Ten borrowers IS a readable comparison. The rule counts points.

    This is what stops the shape rule from becoming "entity lists never get
    charts", which would be a guess about what the rows MEAN rather than a
    fact about how many of them there are.
    """
    outcome, _, _ = _run(drive, release_id, sql=DETAIL_SQL,
                         fields=DETAIL_FIELDS, grain="facility",
                         chart_label="facility_id", limit="LIMIT 10")
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["charts"], (
        "a ten-row ranking was refused a chart")


def test_a_single_row_result_does_not_get_a_chart(drive, release_id):
    """§51. One bar is not a comparison; the sentence already said it."""
    outcome, _, _ = _run(drive, release_id, sql=DETAIL_SQL,
                         fields=DETAIL_FIELDS, grain="facility",
                         chart_label="facility_id", limit="LIMIT 1")
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["charts"] == []


def test_dropping_a_chart_costs_no_extra_model_call(drive, release_id):
    """An unhelpful picture is a warning, never a correction round."""
    outcome, provider, _ = _run(drive, release_id, sql=DETAIL_SQL,
                                fields=DETAIL_FIELDS, grain="facility",
                                chart_label="facility_id")
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2
