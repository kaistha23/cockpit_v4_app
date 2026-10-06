"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

Tables and charts: the analyst chooses them, CreditProbe fills them.

A published table already carried no values of its own -- it names an
artifact and the columns to show -- so it could never misreport a number.
What it could not do was WRITE one: a reader was served
3421.1736630513815 where a credit paper says SAR 3,421 million, and any
component that wanted the second form had to invent its own rounding.

So the answer now carries the rendered rows. Every value comes from the
stored artifact, formatted once by the display policy, with the canonical
value kept beside it because ordering, scale and any further arithmetic run
on that and never on a formatted string.
"""

from __future__ import annotations

import json
from decimal import Decimal

import oracles
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_orchestration_recovery import _execution_result
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import states as st

QUESTION = "What is total exposure at default by sector in the latest quarter?"


def _answer(messages):
    step = _execution_result(messages)["steps"][0]
    artifact = step["artifact_id"]
    column = next(c for c in step["columns"] if c != "sector_name")
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="The book totals {{claim.total_ead}}.",
              numeric_claims=[{
                  "claim_id": "total_ead", "unit": "SAR million",
                  "derivation": {"operation": "sum", "operands": [
                      {"artifact_id": artifact, "column_id": column,
                       "row_ids": list(step["row_ids"])}]}}],
              tables=[{"title": "EAD by sector", "artifact_id": artifact,
                       "columns": ["sector_name", column]}],
              charts=[{"kind": "bar", "title": "EAD by sector",
                       "artifact_id": artifact, "x_column": "sector_name",
                       "y_columns": [column], "unit": "SAR million"}]))])


def _run(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    return drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        _answer])


# ---- §22: tables -------------------------------------------------------

def test_a_published_table_carries_the_rows_the_server_rendered(drive,
                                                                release_id):
    outcome, _, _ = _run(drive, release_id)
    assert outcome.state == st.COMPLETED, outcome.message

    table = outcome.response["tables"][0]
    assert table["rendered_by"] == "creditprobe"
    assert table["row_count"] == 12, "the twelve real sectors"
    first = table["rows"][0]
    assert first["row_id"] == "r0"
    assert set(first) == {"row_id", "canonical", "display"}


def test_a_money_column_reads_as_money_and_keeps_its_canonical_value(
        drive, release_id):
    outcome, _, _ = _run(drive, release_id)
    table = outcome.response["tables"][0]
    column = next(c for c in table["columns"] if c != "sector_name")
    assert table["column_units"][column] == "SAR million"

    row = table["rows"][0]
    shown = row["display"][column]
    assert shown.startswith("SAR ") and shown.endswith(" million")
    assert "." not in shown.split("SAR ")[1].split(" million")[0], (
        f"a table amount was published with decimals: {shown}")
    # And the number the arithmetic runs on is untouched beside it.
    canonical = Decimal(str(row["canonical"][column]))
    assert canonical != Decimal(
        shown.split("SAR ")[1].split(" million")[0].replace(",", "")), (
        "full precision must survive into the row, not be replaced by the "
        "figure a reader sees")


def test_a_label_column_is_not_given_a_unit(drive, release_id):
    outcome, _, _ = _run(drive, release_id)
    table = outcome.response["tables"][0]
    assert "sector_name" not in table["column_units"]
    assert table["rows"][0]["display"]["sector_name"] == \
        table["rows"][0]["canonical"]["sector_name"]


def test_the_rows_keep_the_order_the_query_produced(drive, release_id):
    """Ordering is done at full precision, never on formatted strings.

    SAR 9,000 million sorts above SAR 40,599 million as text.
    """
    outcome, _, _ = _run(drive, release_id)
    table = outcome.response["tables"][0]
    column = next(c for c in table["columns"] if c != "sector_name")
    values = [Decimal(str(r["canonical"][column])) for r in table["rows"]]
    assert values == sorted(values, reverse=True)

    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    assert [r["canonical"]["sector_name"] for r in table["rows"]] == [
        k for k, _ in sorted(expected.items(), key=lambda kv: -kv[1])]


def test_the_table_values_match_an_independent_oracle(drive, release_id):
    """§36: the canonical layer and the display layer, both checked."""
    from backend.cockpit_v4 import display as disp

    outcome, _, _ = _run(drive, release_id)
    table = outcome.response["tables"][0]
    column = next(c for c in table["columns"] if c != "sector_name")
    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    for row in table["rows"]:
        sector = row["canonical"]["sector_name"]
        want = Decimal(str(expected[sector]))
        got = Decimal(str(row["canonical"][column]))
        assert abs(got - want) < Decimal("0.000001"), sector
        assert row["display"][column] == disp.format_value(
            want, "SAR million"), sector


# ---- §23: charts -------------------------------------------------------

def test_a_published_chart_carries_the_points_the_server_rendered(drive,
                                                                  release_id):
    outcome, _, _ = _run(drive, release_id)
    chart = outcome.response["charts"][0]
    column = next(iter(chart["series_units"]))
    assert chart["rendered_by"] == "creditprobe"
    assert chart["series_units"][column] == "SAR million"
    assert len(chart["points"]) == 12
    point = chart["points"][0]
    assert point["row_id"] == "r0"
    assert point["label"]
    assert point["display"][column].startswith("SAR ")
    assert isinstance(point["values"][column], (int, float))


def test_chart_ordering_uses_full_precision(drive, release_id):
    outcome, _, _ = _run(drive, release_id)
    chart = outcome.response["charts"][0]
    column = next(iter(chart["series_units"]))
    values = [Decimal(str(p["values"][column])) for p in chart["points"]]
    assert values == sorted(values, reverse=True)


# ---- the model contributes no numbers ----------------------------------

def test_the_model_has_no_way_to_put_a_number_in_a_table_or_chart():
    """Structural, not behavioural.

    A table spec names a title, an artifact and columns; a chart names its
    kind, axes and unit. Neither has a field for values, and both are
    closed. If either could carry them, a published table could disagree
    with the result it claims to show and nothing would catch it -- so the
    guarantee belongs in the schema rather than in a habit.
    """
    import pathlib

    schema = json.loads((pathlib.Path(__file__).resolve().parents[2]
                         / "backend" / "cockpit_v4" / "contracts"
                         / "shared_defs.schema.json").read_text())
    for name in ("Table", "Chart"):
        spec = schema["$defs"][name]
        assert spec["additionalProperties"] is False, name
        for field_name, field_spec in spec["properties"].items():
            kind = field_spec.get("type")
            if kind == "array":
                kind = field_spec.get("items", {}).get("type")
            assert kind == "string", (
                f"{name}.{field_name} is {kind!r}; only names and labels "
                f"may reach a published table from the model")
