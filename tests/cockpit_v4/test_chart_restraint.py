"""A chart the analyst can justify, and the permission not to draw one.

UNIT · REPRODUCTION. No model call, no database.

Two defects, opposite in direction, one cause
---------------------------------------------
`PRESENTATION` and the `charts` contract were both written after four live
answers arrived as bare tables, one of them to a reader who had asked for a
line chart in those words. Everything written then says SEND A CHART, six
different ways. Nothing said when not to.

So the only failure the guidance could still produce was the opposite one:
a single-bar chart under a sentence that already gave the number, a pie of
two slices, a line through three points -- decoration around an answer that
was finished without it. That is not a cosmetic complaint. An analyst who
charts whatever can be charted is an analyst who is not deciding anything,
and a reader can tell.

The second defect was smaller and stranger. `why_this_chart` -- the field
where the analyst states the reason -- was sent by two test fixtures and had
never existed in the contract. `$defs.Chart` is `additionalProperties:
false`, so the payload those fixtures exercised was one a real provider
would have refused. They passed because they build the answer directly and
never meet the wire schema. A fixture that proves a shape the provider
rejects is worse than no fixture: it reports coverage of a path that cannot
run.

Both are fixed here, and both are held by a mutation check -- the property
removed from the schema, the clause removed from the block -- because a
guard nobody has watched fail is a guard whose pattern may simply be wrong.
"""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import governance as gov

#: A chart exactly as the contract requires it, with nothing optional.
MINIMAL = {"kind": "bar", "title": "Exposure by sector",
           "artifact_id": "art-1", "x_column": "sector",
           "y_columns": ["exposure"], "unit": "SAR"}


def wire_schema() -> dict:
    """The `finalize_response` schema AS THE PROVIDER RECEIVES IT.

    `$ref` is resolved away before the schema goes on the wire, so a test
    that validates against the file with its refs intact is validating a
    document the provider never sees. `_inline` is the product's own
    flattening and is therefore the only honest copy to check.
    """
    return contracts_mod._inline(
        contracts_mod._load("finalize_response.schema.json"),
        contracts_mod._defs())


def answer_with(chart: dict) -> dict:
    return {"narrative": "n", "disposition": "answer", "charts": [chart]}


# ---- the rationale is a field the analyst is allowed to send -----------

def test_a_chart_may_carry_the_reason_it_exists() -> None:
    jsonschema.validate(
        answer_with(dict(MINIMAL, why_this_chart="Ranked comparison.")),
        wire_schema())


def test_the_reason_is_optional_and_a_chart_without_one_is_still_legal() -> None:
    """The point is the deciding, not the sentence. An analyst who leaves it
    out has not broken the contract."""
    jsonschema.validate(answer_with(MINIMAL), wire_schema())


def test_the_reason_is_one_line_and_not_a_second_narrative() -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            answer_with(dict(MINIMAL, why_this_chart="x" * 201)),
            wire_schema())


def test_the_chart_object_is_still_a_closed_world() -> None:
    """Adding one field must not have opened the door to any field."""
    with pytest.raises(jsonschema.ValidationError) as caught:
        jsonschema.validate(answer_with(dict(MINIMAL, nonsense="x")),
                            wire_schema())
    assert "nonsense" in str(caught.value)


@pytest.mark.parametrize("module", ["test_governance_record.py",
                                    "test_export.py"])
def test_the_fixtures_that_send_a_rationale_send_a_legal_payload(
        module: str) -> None:
    """The specific reason this file exists.

    Both modules build a chart carrying `why_this_chart` and assert on what
    the product does with it. Until the property was added to the contract,
    the shape they exercised was one the provider would have rejected
    outright, so whatever they proved, they did not prove it about a chart
    a live run could produce.
    """
    from pathlib import Path

    source = (Path(__file__).parent / module).read_text(encoding="utf-8")
    assert "why_this_chart" in source, (
        f"{module} no longer sends a rationale; this guard now checks "
        f"nothing and should be removed or re-pointed")
    jsonschema.validate(
        answer_with(dict(MINIMAL, why_this_chart="Ranked comparison.")),
        wire_schema())


def test_removing_the_property_makes_that_fixture_illegal_again() -> None:
    """§16 mutation check. If the property were quietly dropped from the
    contract, the test above must go red -- otherwise it is passing on
    something other than the property being there."""
    mutated = copy.deepcopy(wire_schema())
    chart = mutated["properties"]["charts"]["items"]
    assert chart.get("additionalProperties") is False
    del chart["properties"]["why_this_chart"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            answer_with(dict(MINIMAL, why_this_chart="Ranked comparison.")),
            mutated)


# ---- the answer turn is told when NOT to draw one ----------------------

def presentation_text() -> str:
    blocks = ctx_mod.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"}])
    return " ".join(str(b.get("text") or "") for b in blocks)


def test_the_analyst_is_told_that_some_results_have_no_shape() -> None:
    text = presentation_text()
    assert "a_chart_is_not_always_the_answer" in text
    assert "send no chart" in text


def test_the_restraint_reaches_the_turn_that_decides() -> None:
    """Not the action turn: it holds no result and cannot send a chart, and
    `test_the_action_turn_does_not_pay_for_it` measures its bytes. The
    restraint belongs where the rows are already in hand."""
    assert "chart" not in ctx_mod.PROMPT_PATH.read_text(
        encoding="utf-8").lower()
    assert "a_chart_is_not_always_the_answer" in presentation_text()


def test_the_analyst_is_told_where_to_put_the_reason() -> None:
    text = presentation_text()
    assert "why_this_chart" in text
    assert "governance record" in text


def test_an_explicit_request_still_wins() -> None:
    """Restraint is not a licence to ignore a reader who asked for a form.
    Both clauses are present and the request one is unchanged."""
    text = presentation_text()
    assert "an_explicit_request_wins" in text
    assert "line chart" in text


def test_the_restraint_clause_is_a_clause_and_not_a_comment() -> None:
    """§16 mutation check, from the other side: a clause written in a
    `#` comment beside the block would satisfy a grep of the SOURCE and
    reach the analyst never. This reads the block that is serialised."""
    assert "a_chart_is_not_always_the_answer" in ctx_mod.PRESENTATION
    assert "say_why_in_one_line" in ctx_mod.PRESENTATION
    payload = json.loads(json.dumps(ctx_mod.PRESENTATION, ensure_ascii=False))
    assert "send no chart" in payload["a_chart_is_not_always_the_answer"]


# ---- the record carries the reason, and never invents one --------------

def test_the_governance_record_publishes_the_analysts_reason() -> None:
    answer = {"charts": [dict(MINIMAL, points=[{}, {}],
                              x_axis={"label": "Sector"},
                              y_axis={"label": "Exposure, SAR"},
                              why_this_chart="Concentration across sectors.")]}
    [published] = gov._published_from(answer, "art-1")
    assert published["kind"] == "chart"
    assert published["why_this_chart"] == "Concentration across sectors."


def test_a_chart_with_no_reason_is_recorded_as_having_none() -> None:
    """The record must not supply a justification the analyst did not give.
    An empty string reads as "not stated"; a manufactured sentence would
    read as the analyst's reasoning and be evidence of nothing."""
    answer = {"charts": [dict(MINIMAL, points=[])]}
    [published] = gov._published_from(answer, "art-1")
    assert published["why_this_chart"] == ""


def test_a_reason_on_a_different_artifact_is_not_borrowed() -> None:
    answer = {"charts": [
        dict(MINIMAL, artifact_id="art-other", points=[],
             why_this_chart="Belongs to the other chart."),
        dict(MINIMAL, points=[])]}
    [published] = gov._published_from(answer, "art-1")
    assert published["why_this_chart"] == ""
