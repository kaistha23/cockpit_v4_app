"""
UNIT. No model, no database, no runner.

The model-facing schema and the server validator must agree MECHANICALLY.

The live defect: the first product-help run answered correctly in 16.6
seconds and was refused because `clarification_question` was `null` — a
representation the schema it was given explicitly permits. CreditProbe then
forced a second full generation: 2 calls, 29.4 seconds and ~$0.162 for a
question that needs one call.

The fix is not prompt wording. It is that every absence the schema advertises
is an absence the validator accepts, and that every field the validator
demands is a field the schema requires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import jsonschema

from backend.cockpit_v4 import contracts as c
from backend.cockpit_v4.contracts import parse_catalog

SCHEMAS = Path(c.CONTRACTS_DIR)

#: The payload recorded from the live run, with the null that cost a
#: generation. Kept whole rather than reduced to the one field, because the
#: point is that a model filling in "nothing" everywhere is behaving
#: correctly.
#:
#: It still carries the OLD nested `intent`, deliberately. That field is no
#: longer a property of any tool schema (§24), but a payload that sends one
#: anyway is a live model behaving the way a live model has behaved, and the
#: parser must keep taking it rather than refusing a finished answer over a
#: field it stopped asking for.
LIVE_FINALIZE_PAYLOAD = {
    "intent": {
        "query_mode": "PRODUCT_HELP",
        "owner": "COCKPIT",
        "understood_request": "The user is asking who I am.",
        "response_language": "en",
        "blocking_ambiguities": None,
        "resolved_assumptions": None,
        "canonical_mappings": None,
        "excluded_parts": None,
        "public_rationale": "Answering from product metadata.",
    },
    "disposition": "answer",
    "narrative": "CreditProbe is an intelligent credit-investigation layer.",
    "coverage": None,
    "numeric_claims": None,
    "evidence_refs": None,
    "tables": None,
    "charts": None,
    "limitations": None,
    "suggested_questions": None,
    "clarification_question": None,
    "clarification_options": None,
    "referral_owner": None,
    "referral_reason": None,
}


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


#: What the RUN owns before any tool is called. §24: the server authors this
#: once, so every parse below has one to merge into -- which is exactly the
#: state the orchestrator is in from its first second.
CARRIED = c.Intent(
    query_mode="PRODUCT_HELP", owner="COCKPIT", understood_request="",
    response_language="en", blocking_ambiguities=(), resolved_assumptions=(),
    canonical_mappings=(), excluded_parts=(), public_rationale="")


#: The same, for a run whose turn IS analytical. Masked sample rows are only
#: available to one of these, which is a property of the RUN and not of the
#: request -- so it is the carried envelope that decides, not a field the
#: caller fills in.
ANALYSIS = c.Intent(
    query_mode="DATA_ANALYSIS", owner="COCKPIT", understood_request="",
    response_language="en", blocking_ambiguities=(), resolved_assumptions=(),
    canonical_mappings=(), excluded_parts=(), public_rationale="")


def _accepts_null(node: dict) -> bool:
    declared = node.get("type")
    if isinstance(declared, list):
        return "null" in declared
    return declared == "null"


# ---- the recorded defect ------------------------------------------------

def test_the_payload_that_cost_a_generation_is_accepted():
    final = c.parse_final(LIVE_FINALIZE_PAYLOAD, carried=CARRIED)
    assert final.disposition == "answer"
    assert final.clarification_question == ""
    assert final.clarification_options == ()
    assert final.referral_owner == ""
    assert final.coverage == ()
    assert final.numeric_claims == ()
    assert final.intent.blocking_ambiguities == ()
    assert final.intent.resolved_assumptions == ()
    assert final.intent.canonical_mappings == ()


@pytest.mark.parametrize("spelling", [None, "", "absent"])
def test_the_three_spellings_of_absence_store_identically(spelling):
    """null, "" and a missing key mean the same thing and are stored as one."""
    payload = dict(LIVE_FINALIZE_PAYLOAD)
    if spelling == "absent":
        payload.pop("clarification_question")
    else:
        payload["clarification_question"] = spelling
    assert c.parse_final(payload, carried=CARRIED).clarification_question == ""


def test_the_schema_advertises_every_absence_the_validator_accepts():
    """Agreement in the direction that caused the defect."""
    schema = _schema("finalize_response.schema.json")["properties"]
    for field in ("clarification_question", "clarification_options",
                  "referral_owner", "referral_reason", "coverage",
                  "numeric_claims", "evidence_refs", "tables", "charts",
                  "limitations", "suggested_questions"):
        assert _accepts_null(schema[field]), (
            f"the validator accepts null for {field}; the schema must say so, "
            f"or the model is being told something the server does not mean")


def test_the_validator_accepts_every_absence_the_schema_advertises():
    """Agreement in the other direction, checked field by field."""
    schema = _schema("finalize_response.schema.json")["properties"]
    nullable = [f for f, node in schema.items() if _accepts_null(node)]
    assert nullable, "this test is meaningless if nothing is nullable"
    for field in nullable:
        payload = {**LIVE_FINALIZE_PAYLOAD, field: None}
        c.parse_final(payload, carried=CARRIED)  # must not raise


@pytest.mark.parametrize("tool,schema_name,payload", [
    ("inspect_catalog", "inspect_catalog.schema.json", {
        "query": None, "relation_ids": None, "field_ids": None,
        "detail": None, "reporting_periods": None, "sample_rows": None,
        "cursor": None}),
    ("read_artifact", "read_artifact.schema.json", {
        "artifact_id": "art-1", "artifact_kind": "result", "columns": None,
        "offset": None, "limit": None, "cursor": None}),
])
def test_the_other_tools_accept_absence_too(tool, schema_name, payload):
    """The same mismatch would cost the same on any tool.

    No `intent` in these payloads: §24 removed it from every tool schema, so
    a payload carrying one would now fail the schema it is being checked
    against. The run's intent is carried instead.
    """
    parse = {"inspect_catalog": c.parse_catalog,
             "read_artifact": c.parse_artifact}[tool]
    parse(payload, carried=CARRIED)
    schema = _schema(schema_name)["properties"]
    assert "intent" not in schema, (
        f"{tool} must not ask the analyst to restate the run's intent")
    for field, value in payload.items():
        if value is None:
            assert _accepts_null(schema[field]), (
                f"{tool}.{field} accepts null and the schema does not say so")


def test_an_execution_submission_accepts_its_optional_absences():
    payload = {
        "objective": "count rows", "subquestions": ["how many"],
        "scope": None, "metadata_receipt_ids": None, "fields_required": None,
        "expected_output_grain": "release",
        "expected_units": {"n": "rows"},
        "steps": [{"step_id": "s1", "language": "sql", "code": "SELECT 1",
                   "parameters": None, "purpose": "count",
                   "input_artifact_ids": None, "depends_on_step_ids": None}],
        "repair_of_submission_id": None,
    }
    submission = c.parse_execution(
        payload, max_steps=6,
        carried=c.Intent(query_mode="DATA_ANALYSIS", owner="COCKPIT",
                         understood_request="", response_language="en",
                         blocking_ambiguities=(), resolved_assumptions=(),
                         canonical_mappings=(), excluded_parts=(),
                         public_rationale=""))
    assert submission.scope == {}
    assert submission.steps[0].parameters == {}
    assert submission.repair_of_submission_id == ""


# ---- expected_units: the schema said object, the validator said string ----

def test_expected_units_honours_the_published_schema():
    """A model that FOLLOWED the schema would have been refused every time."""
    schema = _schema("execute_analysis.schema.json")["properties"]
    assert schema["expected_units"]["type"] == "object", (
        "the published contract is a column -> unit mapping")
    units = c._parse_units({"ead_sar_mn": "SAR million", "n": "rows"})
    assert units == {"ead_sar_mn": "SAR million", "n": "rows"}
    assert c.units_display(units) == "ead_sar_mn: SAR million, n: rows"


def test_a_single_unit_string_is_accepted_as_shorthand():
    assert c._parse_units("SAR million") == {"": "SAR million"}
    assert c.units_display({"": "SAR million"}) == "SAR million"


@pytest.mark.parametrize("bad", [None, "", "   ", {}, {"x": None}, 5, []])
def test_expected_units_still_fails_closed_on_nonsense(bad):
    with pytest.raises(c.Rejection) as excinfo:
        c._parse_units(bad)
    assert excinfo.value.field_path.startswith("expected_units")


# ---- what must STILL fail closed ---------------------------------------

@pytest.mark.parametrize("field", ["disposition", "narrative"])
def test_a_mandatory_field_is_not_weakened(field):
    """Absence is accepted where it means something. This is not that."""
    for absent in (None, "", ):
        payload = {**LIVE_FINALIZE_PAYLOAD, field: absent}
        with pytest.raises(c.Rejection):
            c.parse_final(payload, carried=CARRIED)


def test_the_mandatory_fields_are_not_nullable_in_the_schema():
    schema = _schema("finalize_response.schema.json")["properties"]
    for field in ("disposition", "narrative"):
        assert not _accepts_null(schema[field]), (
            f"{field} carries substance; null is not a legal spelling of it")


def test_a_genuinely_malformed_response_still_fails_closed():
    """Normalization is representation only. Substance is still checked."""
    cases = [
        ({"disposition": "invented_disposition"}, "disposition"),
        ({"narrative": 42}, "narrative"),
        ({"coverage": [{"subquestion": "q", "status": "made_up",
                        "evidence_refs": []}]}, "status"),
        ({"numeric_claims": [{"claim_id": "a", "decimal_value": "not a number",
                              "unit": "x",
                              "evidence": {"artifact_id": "art-1",
                                           "row_key": "0",
                                           "column_id": "c"}}]},
         "decimal_value"),
        ({"numeric_claims": [{"claim_id": "a", "decimal_value": "1.0",
                              "unit": "x", "evidence": None}]}, "evidence"),
    ]
    for override, expected_field in cases:
        payload = {**LIVE_FINALIZE_PAYLOAD, **override}
        with pytest.raises(c.Rejection) as excinfo:
            c.parse_final(payload, carried=CARRIED)
        assert expected_field in (excinfo.value.field_path
                                  or excinfo.value.message), (
            f"{override} should be refused because of {expected_field}")


def test_a_clarification_must_still_carry_its_question():
    payload = {**LIVE_FINALIZE_PAYLOAD, "disposition": "clarification"}
    with pytest.raises(c.Rejection) as excinfo:
        c.parse_final(payload, carried=CARRIED)
    assert excinfo.value.field_path == "clarification_question"


def test_a_referral_must_still_name_a_real_owner():
    for owner in (None, "", "NOT_A_MODULE"):
        payload = {**LIVE_FINALIZE_PAYLOAD, "disposition": "referral",
                   "referral_owner": owner}
        with pytest.raises(c.Rejection) as excinfo:
            c.parse_final(payload, carried=CARRIED)
        assert excinfo.value.field_path == "referral_owner"


def test_a_scalar_is_still_never_expanded_into_characters():
    """Accepting absence is not the same as accepting a wrong shape."""
    payload = {**LIVE_FINALIZE_PAYLOAD, "limitations": "a single limitation"}
    with pytest.raises(c.Rejection) as excinfo:
        c.parse_final(payload, carried=CARRIED)
    assert "array of strings" in excinfo.value.message
    assert "A single string is not an array" in excinfo.value.message


def test_every_nullable_field_still_rejects_a_wrong_type():
    for field, wrong in (("clarification_question", 42),
                         ("clarification_options", "a string"),
                         ("coverage", "a string"),
                         ("numeric_claims", 7)):
        payload = {**LIVE_FINALIZE_PAYLOAD, field: wrong}
        with pytest.raises(c.Rejection):
            c.parse_final(payload, carried=CARRIED)


# ---- inspect_catalog: sample_rows and detail cannot disagree -----------

def _catalog_payload(**body):
    """A catalogue request exactly as the published schema allows one.

    No `intent`: §24 took it off every tool, and this payload is validated
    against that schema, so carrying one would make the test assert the
    opposite of the contract.
    """
    payload = {
        "query": None, "relation_ids": None, "field_ids": None,
        "detail": None, "reporting_periods": None, "sample_rows": None,
        "cursor": None,
    }
    payload.update(body)
    return payload


@pytest.mark.parametrize("body", [
    {"detail": ["fields"], "sample_rows": 3},
    {"detail": ["samples"]},
    {"detail": ["samples"], "sample_rows": 0},
    {"detail": ["samples"], "sample_rows": 10},
    {"detail": ["fields", "samples"], "sample_rows": 1},
    {"sample_rows": 2},
])
def test_every_sample_request_the_schema_allows_is_accepted(body):
    """A live run was refused for a call its own schema called valid.

    "sample_rows requires 'samples' in detail" was a parser rule the published
    schema did not state. The two forms are the same request and neither is
    refused for lacking the other.
    """
    payload = _catalog_payload(**body)
    # The INLINED schema, which is what the provider is actually sent.
    published = next(tool["input_schema"] for tool in c.provider_tools()
                     if tool["name"] == "inspect_catalog")
    jsonschema.validate(payload, published)
    parsed = parse_catalog(payload, carried=ANALYSIS)
    assert "samples" in parsed.detail
    assert parsed.sample_rows >= 1


def test_the_schema_says_the_two_forms_mean_the_same_thing():
    schema = next(tool["input_schema"] for tool in c.provider_tools()
                  if tool["name"] == "inspect_catalog")
    described = (schema["properties"]["sample_rows"]["description"]
                 + schema["properties"]["detail"]["description"]
                 + json.dumps(schema.get("allOf", [])))
    assert "mean the same thing" in described
    assert "rejected" in described or "Neither form is rejected" in described
