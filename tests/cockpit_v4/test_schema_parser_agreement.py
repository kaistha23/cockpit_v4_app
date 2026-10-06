"""UNIT · NO MODEL. The tool schemas the model reads agree with the parser.

The defect this guards
----------------------
The schemas, the prompt and the parser had drifted apart. A model that
followed the schema to the letter was refused by the parser: it was offered
`disposition: "partial"` (the parser accepts `partial_answer`),
`artifact_kind: "completed_turn"` (the parser accepts `thread_turn`), eight
steps in a Standard run (the parser allows six), and was told the evidence
field is `row_id` (the parser requires `row_key`). Claude worked around the
contradictions; a weaker model spent its submissions on them.

The parser is the source of truth. These tests fail the moment a schema
offers something it refuses, or requires something it does not.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import contracts as c
from conftest import intent

PROMPT = pathlib.Path(c.__file__).parent / "prompts" / "analyst.md"


@pytest.fixture
def carried():
    return c.parse_intent(intent("DATA_ANALYSIS", "COCKPIT"))


def _tool(name, **kw):
    return next(t for t in c.provider_tools(**kw) if t["name"] == name)["input_schema"]


def _minimal(schema, defs):
    """A payload holding ONLY what the schema marks required, typed per schema."""
    out = {}
    for key in schema.get("required", []):
        prop = schema["properties"][key]
        if "$ref" in prop:
            prop = defs[prop["$ref"].rsplit("/", 1)[-1]]
        kind = prop.get("type")
        kind = kind[0] if isinstance(kind, list) else kind
        if "enum" in prop:
            out[key] = prop["enum"][0]
        elif kind == "array":
            item = prop.get("items", {})
            out[key] = [_minimal(item, defs)] if item.get("type") == "object" else ["x"]
        elif kind == "object":
            out[key] = _minimal(prop, defs) if prop.get("properties") else {}
        elif kind in ("integer", "number"):
            out[key] = 1
        else:
            out[key] = "SELECT 1" if key == "code" else ("sql" if key == "language" else "x")
    return out


def test_every_disposition_the_schema_offers_is_one_the_parser_accepts():
    offered = _tool("finalize_response")["properties"]["disposition"]["enum"]
    assert set(offered) == set(c.DISPOSITIONS)


def test_read_artifact_offers_and_requires_exactly_what_the_parser_reads(carried):
    schema = _tool("read_artifact")
    assert set(schema["properties"]["artifact_kind"]["enum"]) == set(c.ARTIFACT_KINDS)
    assert {"artifact_id", "artifact_kind"} <= set(schema["required"])
    for kind in schema["properties"]["artifact_kind"]["enum"]:
        assert c.parse_artifact({"artifact_id": "a", "artifact_kind": kind},
                                carried=carried).artifact_kind == kind


@pytest.mark.parametrize("limits", [config_mod.STANDARD_LIMITS, config_mod.DEEP_LIMITS])
def test_the_step_limit_offered_is_the_modes_own(limits, carried):
    schema = _tool("execute_analysis", max_steps=limits.steps_per_batch)
    assert schema["properties"]["steps"]["maxItems"] == limits.steps_per_batch
    step = {"step_id": "s", "language": "sql", "code": "SELECT 1", "purpose": "p"}
    too_many = [dict(step, step_id=f"s{i}") for i in range(limits.steps_per_batch + 1)]
    with pytest.raises(c.Rejection):
        c.parse_execution({"objective": "o", "steps": too_many},
                          max_steps=limits.steps_per_batch, carried=carried)


def test_a_payload_with_only_the_schemas_required_fields_parses(carried):
    """Nothing the schema calls optional is something the parser requires."""
    defs = c._defs()
    execute = _tool("execute_analysis", max_steps=6)
    submission = c.parse_execution(_minimal(execute, defs), max_steps=6, carried=carried)
    assert submission.steps and submission.subquestions
    c.parse_catalog(_minimal(_tool("inspect_catalog"), defs), carried=carried)
    c.parse_artifact(_minimal(_tool("read_artifact"), defs), carried=carried)


def test_the_step_schema_requires_what_the_parser_requires_and_no_more(carried):
    required = set(c._defs()["Step"]["required"])
    assert required == {"step_id", "language", "code", "purpose"}
    for missing in sorted(required):
        step = {"step_id": "s", "language": "sql", "code": "SELECT 1", "purpose": "p"}
        step.pop(missing)
        with pytest.raises(c.Rejection):
            c.parse_execution({"objective": "o", "steps": [step]}, max_steps=6, carried=carried)


def test_the_prompt_names_the_evidence_field_the_parser_reads():
    text = PROMPT.read_text(encoding="utf-8")
    assert "`row_key`" in text
    assert "the `row_id`" not in text


def test_a_failed_step_after_a_result_can_be_corrected_not_only_answered():
    from backend.cockpit_v4 import action_state as acts
    repair = acts.decide(executed=True, answer_only=False, analytical=True,
                         readiness={}, repair_open=True)
    assert repair.state == acts.RESULT_READY
    assert set(acts.offered(repair)) >= {c.TOOL_EXECUTE, c.TOOL_FINALIZE}
    assert repair.require == ""
    settled = acts.decide(executed=True, answer_only=False, analytical=True, readiness={})
    assert c.TOOL_EXECUTE not in acts.offered(settled)       # unchanged when nothing failed
    closing = acts.decide(executed=True, answer_only=True, analytical=True,
                          readiness={}, repair_open=True)
    assert c.TOOL_EXECUTE not in acts.offered(closing)       # answer-only stays answer-only


def test_the_prompt_says_steps_are_self_contained():
    assert "self-contained" in PROMPT.read_text(encoding="utf-8")
