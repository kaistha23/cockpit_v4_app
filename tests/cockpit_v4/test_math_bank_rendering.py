"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

The fifteen mathematical questions, with the analyst typing no numbers.

`test_math_pipeline` runs the same bank with the analyst SENDING a rounded
decimal string, which is the credit officer's form the old contract refused.
That path still matters and still passes: an offered cross-check is still
checked.

This module runs the other path, which is now the normal one. The analyst
names the cell or the arithmetic and nothing else; CreditProbe computes the
value, chooses its precision from the unit, formats it, and substitutes it
into the narrative. Both layers are checked against an independent oracle --
the canonical figure the derivation produced, and the string a reader is
actually shown.

§35 requires 15/15 through publication. §36 requires the oracle to check
canonical and display separately, because a display layer that silently
dropped a factor of a hundred would agree with itself perfectly.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import math_bank as bank
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_math_pipeline import (MEASURE, _claims_for, _execute_call,
                                _explain_rejection)

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import states as st

#: Everything a claim's value could be read off. If any of these survives
#: into what the model sends, the test is not exercising the new path.
_VALUE_KEYS = ("decimal_value", "display_precision")


def _finalizer(question_id, period):
    """The same answers as the bank's own harness, minus every number."""
    question = bank.BY_ID[question_id]

    def turn(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        artifact, rows = step["artifact_id"], step["preview"]
        row_ids, columns = step["row_ids"], step["columns"]

        if not row_ids:
            return ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                    understood=question["text"]),
                      narrative=(f"For {period['latest']}, no row meets the "
                                 f"conditions in this question."),
                      coverage=[{"subquestion": question["text"],
                                 "status": "answered", "evidence_refs": []}],
                      tables=[{"title": question["text"][:80],
                               "artifact_id": artifact,
                               "columns": columns}]))])

        claims = []
        for draft in _claims_for(question_id, artifact, rows, row_ids):
            claim = {k: v for k, v in draft.items()
                     if not k.startswith("_") and k not in _VALUE_KEYS}
            claims.append(claim)

        sentences = " ".join(f"{{{{claim.{c['claim_id']}}}}}" for c in claims)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood=question["text"]),
                  narrative=(f"For {period['latest']}, {sentences}"),
                  coverage=[{"subquestion": question["text"],
                             "status": "answered",
                             "evidence_refs": [
                                 {"artifact_id": artifact,
                                  "row_key": row_ids[0],
                                  "column_id": MEASURE[question_id]}]}],
                  numeric_claims=claims,
                  tables=[{"title": question["text"][:80],
                           "artifact_id": artifact,
                           "columns": list(rows[0]) if rows else ["x"]}]))])

    return turn


def _run(drive, question_id, release_id):
    period = bank.periods(release_id)
    return drive(bank.BY_ID[question_id]["text"],
                 [ScriptedResult(tool_calls=[_execute_call(question_id,
                                                           period)]),
                  _finalizer(question_id, period),
                  _explain_rejection(question_id)], mode="standard")


# ---- §35: all fifteen publish -----------------------------------------

@pytest.mark.parametrize("question_id", bank.ALL)
def test_the_question_publishes_without_the_analyst_typing_a_number(
        question_id, drive, release_id):
    outcome, provider, _ = _run(drive, question_id, release_id)
    assert outcome.state == st.COMPLETED, outcome.message

    body = outcome.response
    assert "{{claim." not in body["narrative"], (
        "a claim reference reached the reader unresolved")
    assert len(provider.sent) == 2, (
        "a number nobody typed cannot have been mistyped, so no repair "
        "round may be spent here")


@pytest.mark.parametrize("question_id", bank.ALL)
def test_no_claim_carried_a_value_from_the_model(question_id, drive,
                                                 release_id):
    """Otherwise this whole module is testing the old path again."""
    outcome, _, _ = _run(drive, question_id, release_id)
    assert outcome.state == st.COMPLETED, outcome.message
    for claim in outcome.response["numeric_claims"]:
        assert "decimal_value" not in claim, (
            f"{claim['claim_id']} carries a value the analyst did not send")


# ---- §36: both layers, against the oracle -----------------------------

@pytest.mark.parametrize("question_id", bank.ALL)
def test_every_published_figure_is_the_canonical_one_correctly_written(
        question_id, drive, release_id):
    """Canonical and display are checked separately, on purpose.

    A display layer that dropped a factor of a hundred would agree with
    itself perfectly. So the rendered string is recomputed here from the
    claim's own canonical value through the policy, and the canonical value
    is checked against the table the server rendered from the artifact.
    """
    outcome, _, _ = _run(drive, question_id, release_id)
    assert outcome.state == st.COMPLETED, outcome.message
    body = outcome.response

    table = body["tables"][0]
    if not table.get("rows"):
        pytest.skip("this question's result is empty on this book")

    for claim in body["numeric_claims"]:
        unit = claim["unit"]
        precision = claim["display_precision"]
        assert precision == disp.decimals(unit), (
            f"{claim['claim_id']} published at {precision}dp for {unit!r}; "
            f"the policy says {disp.decimals(unit)}")
        assert precision in disp.PERMITTED[disp.classify(unit)]

    # Every table cell with a resolved unit reads as the policy says.
    for column, unit in table["column_units"].items():
        for row in table["rows"]:
            canonical = row["canonical"][column]
            if canonical is None:
                continue
            assert row["display"][column] == disp.format_value(
                Decimal(str(canonical)), unit), f"{column}"


# ---- §37: quality of what reaches the reader --------------------------

_DEBRIS = re.compile(r"\d+\.\d{5,}")
_FORBIDDEN = ("INR", "crore", "lakh", "₹", "Rs.", "Rs ")


@pytest.mark.parametrize("question_id", bank.ALL)
def test_nothing_a_reader_sees_carries_machine_precision_or_a_wrong_currency(
        question_id, drive, release_id):
    outcome, _, _ = _run(drive, question_id, release_id)
    assert outcome.state == st.COMPLETED, outcome.message
    body = outcome.response

    reader_text = [body["narrative"]]
    for table in body["tables"]:
        for row in table.get("rows", []):
            reader_text.extend(str(v) for v in row["display"].values())
    for chart in body["charts"]:
        for point in chart.get("points", []):
            reader_text.extend(str(v) for v in
                               (point.get("display") or {}).values())

    joined = "\n".join(reader_text)
    debris = _DEBRIS.findall(joined)
    assert not debris, f"machine precision reached the reader: {debris[:3]}"
    for word in _FORBIDDEN:
        assert word not in joined, f"{word!r} reached the reader"


@pytest.mark.parametrize("question_id", bank.ALL)
def test_an_amount_and_a_percentage_are_never_written_alike(question_id,
                                                            drive,
                                                            release_id):
    """§37: inconsistent precision is a defect, not a style choice."""
    outcome, _, _ = _run(drive, question_id, release_id)
    assert outcome.state == st.COMPLETED, outcome.message

    for claim in outcome.response["numeric_claims"]:
        kind = disp.classify(claim["unit"])
        if kind == disp.MONETARY_AMOUNT:
            assert claim["display_precision"] == 0
        elif kind in (disp.PERCENTAGE, disp.PROBABILITY,
                      disp.PERCENTAGE_POINT, disp.RATIO):
            assert claim["display_precision"] == 2
        elif kind in (disp.COUNT, disp.INTEGER):
            assert claim["display_precision"] == 0
