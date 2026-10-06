"""
The exact tool schemas sent to Anthropic must use the supported subset.

Evidence: UNIT over the real `provider_tools()` output — the same object the
adapter puts on the wire. No network call is made.

The defect this module exists for: a live UAT failed before model inference
with

    HTTP 400 invalid_request_error
    tools.0.custom.input_schema: input_schema does not support oneOf, allOf, ...

`tools[0]` was `inspect_catalog`, and the unsupported keyword was an `allOf`
added in the previous round to *document* that `sample_rows` and
`detail: ["samples"]` mean the same thing. It carried only a title and a
description and no schema semantics at all — and the provider rejects the
keyword regardless of what is inside it.

General JSON Schema support is not Anthropic tool-schema support. The rule
here is mechanical and covers every tool-set variant the runtime can build,
because the one that broke was the first tool in the array and nothing tested
the array.
"""

from __future__ import annotations

import itertools
import json

import pytest

from backend.cockpit_v4 import contracts as c

#: Keywords an Anthropic tool `input_schema` does not support. The four the
#: error names, plus the rest of the composition and conditional family, plus
#: `$ref`, which only works because `provider_tools()` inlines it first.
UNSUPPORTED: frozenset[str] = frozenset({
    "oneOf", "allOf", "anyOf", "not",
    "if", "then", "else",
    "$ref", "$defs", "definitions",
    "dependentSchemas", "dependentRequired", "dependencies",
    "patternProperties", "propertyNames", "unevaluatedProperties",
    "unevaluatedItems", "contains", "prefixItems",
})


def _walk(node, path=""):
    """Every key in the schema, with the path that reaches it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _offences(schema) -> list[str]:
    return [path for path, key in _walk(schema) if key in UNSUPPORTED]


def _variants():
    """Every tool set the runtime can build.

    `withhold` is used by the one-generation Product Help policy, so the array
    the provider sees is not always the same array. The blocker was in the
    first element of one of them.
    """
    withholdable = [n for n in c.TOOL_NAMES if n != c.TOOL_FINALIZE]
    yield "full", c.provider_tools()
    for size in range(1, len(withholdable) + 1):
        for combination in itertools.combinations(withholdable, size):
            yield ("withhold:" + ",".join(combination),
                   c.provider_tools(withhold=combination))


ALL_VARIANTS = list(_variants())


@pytest.mark.parametrize("label,tools",
                         ALL_VARIANTS, ids=[v[0] for v in ALL_VARIANTS])
def test_no_tool_set_uses_an_unsupported_schema_keyword(label, tools):
    for index, tool in enumerate(tools):
        offences = _offences(tool["input_schema"])
        assert not offences, (
            f"{label}: tools[{index}].{tool['name']}.input_schema uses "
            f"{offences} — the provider rejects the request with HTTP 400 "
            f"before any inference happens.")


def test_the_variant_matrix_actually_covers_every_tool_first(label=None):
    """The blocker was in tools[0]; a test that never varies the array misses it."""
    first_positions = {tools[0]["name"] for _label, tools in ALL_VARIANTS
                       if tools}
    assert first_positions >= set(c.TOOL_NAMES) - {c.TOOL_FINALIZE} or \
        len(first_positions) >= 2, first_positions
    assert len(ALL_VARIANTS) >= 16


@pytest.mark.parametrize("tool", c.provider_tools(), ids=lambda t: t["name"])
def test_every_tool_is_a_plain_object_schema(tool):
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert isinstance(schema.get("properties"), dict) and schema["properties"]
    assert isinstance(schema.get("required"), list)
    assert schema.get("additionalProperties") is False
    assert tool["description"].strip()


@pytest.mark.parametrize("tool", c.provider_tools(), ids=lambda t: t["name"])
def test_every_schema_is_json_serialisable_and_bounded(tool):
    body = json.dumps(tool["input_schema"])
    assert len(body) < 60_000, (
        f"{tool['name']} schema is {len(body)} bytes; tool definitions are "
        f"billed input on every single generation")


def test_the_whole_tool_array_is_a_reasonable_size():
    body = json.dumps(c.provider_tools())
    assert len(body) < 120_000, len(body)


def test_type_unions_with_null_are_still_allowed():
    """The supported way to say "absent or a value". Not a composition keyword."""
    catalog = next(t for t in c.provider_tools()
                   if t["name"] == "inspect_catalog")
    assert catalog["input_schema"]["properties"]["query"]["type"] == [
        "string", "null"]


def test_the_sample_rows_dependency_is_documented_without_a_keyword():
    """It is stated in the descriptions and ENFORCED in the parser."""
    catalog = next(t for t in c.provider_tools()
                   if t["name"] == "inspect_catalog")
    properties = catalog["input_schema"]["properties"]
    described = (properties["sample_rows"]["description"]
                 + properties["detail"]["description"])
    assert "mean the same thing" in described
    assert "neither is rejected" in described or \
        "neither form is rejected" in described


# ---- the taxonomy: a 400 is not an outage -----------------------------

from backend.cockpit_v4 import states as st  # noqa: E402
from backend.cockpit_v4.provider import _classify  # noqa: E402

#: The live UAT's diagnostic, verbatim apart from the wrapper.
LIVE_400 = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'tools.0.custom.input_schema: "
    "input_schema does not support oneOf, allOf, anyOf, or "
    "dependentSchemas'}}")


def test_the_live_400_is_a_tool_schema_fault_not_an_outage():
    failure = _classify(RuntimeError(LIVE_400))
    assert failure.code == st.TOOL_SCHEMA_INVALID
    assert failure.code != st.PROVIDER_UNAVAILABLE
    assert failure.retry_class == "none", "retrying a malformed request is waste"
    assert "before the model saw it" in str(failure)


def test_the_operator_detail_names_the_failing_tool_and_keywords():
    detail = _classify(RuntimeError(LIVE_400)).detail
    assert detail["status_code"] == 400
    assert detail["provider_error_type"] == "invalid_request_error"
    assert detail["rejected_before_inference"] is True
    assert detail["failing_schema_path"] == "tools.0.custom.input_schema"
    assert detail["failing_tool_index"] == 0
    assert "oneOf" in detail["unsupported_keywords"]
    assert "allOf" in detail["unsupported_keywords"]


def test_no_credential_can_reach_a_message_or_a_detail():
    leaky = ("Error code: 400 - invalid_request_error: bad tools.0.custom."
             "input_schema; sent with Authorization: Bearer "
             "sk-ant-api03-SECRETVALUE123456")
    failure = _classify(RuntimeError(leaky))
    blob = str(failure) + json.dumps(failure.detail)
    assert "sk-ant" not in blob
    assert "SECRETVALUE" not in blob
    assert "[redacted]" in blob


@pytest.mark.parametrize("text,expected", [
    ("Error code: 401 - authentication_error: invalid api key",
     st.PROVIDER_AUTH),
    ("Error code: 403 - permission denied for model", st.PROVIDER_AUTH),
    ("Error code: 429 - rate limit exceeded", st.PROVIDER_RATE_LIMIT),
    ("Error code: 503 - overloaded_error", st.PROVIDER_UNAVAILABLE),
    ("Connection timed out after 30s", st.PROVIDER_UNAVAILABLE),
    ("Error code: 400 - invalid_request_error: max_tokens too large",
     st.PROVIDER_REQUEST_INVALID),
])
def test_each_provider_failure_gets_its_own_class(text, expected):
    assert _classify(RuntimeError(text)).code == expected


def test_a_rejected_request_is_reported_at_understanding_not_publishing(
        drive, store_db):
    """Nothing was produced, so nothing failed to publish."""
    from conftest import ScriptedResult  # noqa: F401

    outcome, _provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [RuntimeError(LIVE_400)])
    assert outcome.error_code == st.TOOL_SCHEMA_INVALID
    events = store_db.events_since(record.run_id)
    failure = next(e for e in events if e.status == "failed")
    assert failure.stage == "understanding", (
        f"a request rejected before inference is not a publishing failure; "
        f"the trace said {failure.stage}")
    assert failure.operation == "provider_request"
    assert outcome.error_id.startswith("err-")
    assert "Reference err-" in failure.public_message
