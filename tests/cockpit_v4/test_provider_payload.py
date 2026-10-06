"""
The exact request that would go to Anthropic, snapshotted and asserted.

Evidence: MODEL MOCK. The scripted provider records the whole assembled
request — system blocks, messages, tools, model id, max_tokens — which is
byte-for-byte what the real adapter would send. No network call is made and
no credential is read.

This exists because the live blocker was in the request, not in the model:
the run failed before inference and nothing in the test suite had ever looked
at the payload as a whole.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import contracts as c

QUESTIONS = {
    "ead_by_sector":
        "What is total exposure at default by sector in the latest quarter?",
    "who_are_you": "Who are you?",
    "explain_tac": "Explain TAC.",
}

UNSUPPORTED = {"oneOf", "allOf", "anyOf", "not", "if", "then", "else",
               "$ref", "$defs", "dependentSchemas", "dependentRequired",
               "patternProperties", "propertyNames", "contains",
               "prefixItems", "unevaluatedProperties"}

SECRETISH = ("sk-ant", "api_key", "authorization", "bearer",
             "anthropic_api_key", "password", "secret")


@pytest.fixture
def payload(drive):
    def _capture(question: str) -> dict:
        script = [ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent(), disposition="answer", narrative="ok"))])]
        _outcome, provider, _record = drive(question, script)
        return provider.sent[0]
    return _capture


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_request_names_the_one_configured_model(key, payload, runtime):
    sent = payload(QUESTIONS[key])
    assert sent["model"] == runtime.capability.model_id
    assert sent["model"], "no default, no 'latest', no inherited AI_MODEL"


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_tools_are_provider_compatible(key, payload):
    sent = payload(QUESTIONS[key])
    blob = json.dumps(sent["tools"])
    for keyword in UNSUPPORTED:
        assert f'"{keyword}"' not in blob, (
            f"{key}: the request carries {keyword}, which the provider "
            f"rejects with HTTP 400 before inference")
    for tool in sent["tools"]:
        assert tool["input_schema"]["type"] == "object"
        assert tool["input_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_system_context_is_present_and_ordered(key, payload):
    sent = payload(QUESTIONS[key])
    blocks = sent["system"]
    assert isinstance(blocks, list) and len(blocks) >= 3
    # Stable prefix first so a cache write is reusable; volatile budget last.
    assert "CreditProbe Cockpit's analyst" in blocks[0]["text"]
    assert "cockpit_semantics" in blocks[1]["text"]
    # The volatile block closes the CARRIED context -- it sits behind the
    # whole stable prefix and there is exactly one of it. Guidance that is
    # true only of THIS turn is appended behind it (`finalization_system`,
    # `policy_blocks`, `product_blocks`), so "last in the list" and "last of
    # the context" are not the same assertion; this is the one that holds.
    volatile = [i for i, block in enumerate(blocks)
                if "pinned_scope" in str(block.get("text") or "")]
    assert volatile == [2], [str(b.get("text"))[:60] for b in blocks]
    if key == "ead_by_sector":
        # An action turn is choosing what to run. It can publish nothing,
        # so nothing turn-scoped is appended and the volatile block is last.
        assert len(blocks) == 3, len(blocks)
    else:
        # A PRODUCT_HELP turn CAN publish, so it carries the shape a product
        # answer wants -- the outlines an action turn could never act on.
        assert "a_narrow_concept_question" in blocks[-1]["text"]
    # The product pack rides on a PRODUCT question and not on an analytical
    # one: about five kilobytes describing what CreditProbe is, handed to a
    # turn that was asked what a book did. `inspect_product_knowledge` is
    # already withheld from that turn for the same reason, and both come
    # back on the second action.
    if key == "ead_by_sector":
        assert "creditprobe" not in blocks[1]["text"]
    else:
        assert "creditprobe" in blocks[1]["text"]


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_question_reaches_the_model_unmodified(key, payload):
    sent = payload(QUESTIONS[key])
    assert QUESTIONS[key] in str(sent["messages"][0]["content"])


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_no_credential_is_anywhere_in_the_request(key, payload):
    sent = payload(QUESTIONS[key])
    blob = json.dumps(sent, default=str).lower()
    for marker in SECRETISH:
        assert marker not in blob, f"{key}: {marker} appears in the request"


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_no_v3_or_sonnet_tool_is_offered(key, payload):
    sent = payload(QUESTIONS[key])
    names = {tool["name"] for tool in sent["tools"]}
    assert names <= set(c.TOOL_NAMES), names
    # Scoped to what is OFFERED and to route-shaped strings. The word
    # "investigation" appears legitimately in the product prose — "CreditProbe
    # accelerates the investigation" — and banning the word would be banning
    # the product's own vocabulary.
    tools_blob = json.dumps(sent["tools"], default=str).lower()
    for forbidden in ("sonnet", "haiku", "agentic_officer", "route_question",
                      "plan_analysis", "investigations"):
        assert forbidden not in tools_blob, f"{key}: {forbidden} in tools"
    whole = json.dumps(sent, default=str).lower()
    for route in ("/api/v1/investigations", "/api/v1/agentic/officer",
                  "claude-3", "claude-sonnet", "claude-haiku"):
        assert route not in whole, f"{key}: {route}"


def test_a_data_question_carries_no_catalogue_dump(payload):
    """991 field definitions is what the loop was made of."""
    sent = payload(QUESTIONS["ead_by_sector"])
    blob = json.dumps({"system": sent["system"],
                       "messages": sent["messages"]}, default=str)
    assert len(blob) < 90_000, f"assembled context is {len(blob)} bytes"
    # The mapped fields are there; the whole catalogue is not.
    assert "ead_reported" in blob and "sector_name" in blob
    assert blob.count("cockpit_facility_quarter.") < 60


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_request_fits_the_model(key, payload, runtime):
    sent = payload(QUESTIONS[key])
    blob = json.dumps({"system": sent["system"], "messages": sent["messages"],
                       "tools": sent["tools"]}, default=str)
    estimate = len(blob) / 2.2
    assert estimate + sent["max_tokens"] < runtime.capability.context_tokens


def test_a_broad_product_question_is_offered_only_what_it_can_use(payload):
    """The synopsis already covers it, so the retrieval tool is not on the
    first action: one generation.

    It used to assert `names == [TOOL_FINALIZE]`, on the reasoning that a
    product question cannot execute anything. True of a question that IS
    one -- and the surface is chosen from the question's WORDS before
    anybody knows which kind it is. The first live UAT sent a typo-heavy
    data question that matched no measure, and the analyst, which had read
    it correctly, was left with one tool and no way to reach the book.
    `execute_analysis` is therefore on the request; `inspect_catalog` is
    not, because submitting SQL is the declaration that widens the
    allowance and reading the catalogue is not.
    """
    sent = payload(QUESTIONS["who_are_you"])
    names = [tool["name"] for tool in sent["tools"]]
    assert c.TOOL_PRODUCT not in names
    assert c.TOOL_INSPECT not in names
    assert set(names) == {c.TOOL_EXECUTE, c.TOOL_FINALIZE}


def test_a_named_deep_topic_is_offered_the_retrieval_tool(payload):
    """It names detail the synopsis does not carry, so it may go and read
    it. The catalogue is still not on the request."""
    sent = payload(QUESTIONS["explain_tac"])
    names = [tool["name"] for tool in sent["tools"]]
    assert c.TOOL_PRODUCT in names
    assert set(names) == {c.TOOL_PRODUCT, c.TOOL_EXECUTE, c.TOOL_FINALIZE}
    assert c.TOOL_INSPECT not in names


def test_the_sanitized_payload_is_written_as_evidence(payload, tmp_path):
    """A reviewable artifact, with nothing sensitive in it."""
    from pathlib import Path

    out = {}
    for key, question in sorted(QUESTIONS.items()):
        sent = payload(question)
        out[key] = {
            "question": question,
            "model": sent["model"],
            "max_tokens": sent["max_tokens"],
            "system_blocks": len(sent["system"]),
            "system_bytes": len(json.dumps(sent["system"], default=str)),
            "message_bytes": len(json.dumps(sent["messages"], default=str)),
            "tools": [{"name": t["name"],
                       "schema_bytes": len(json.dumps(t["input_schema"]))}
                      for t in sent["tools"]],
            "unsupported_schema_keywords": [],
        }
    evidence = Path("docs/cockpit_v4/evidence/provider_payload.json")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(
        {"label": "MODEL MOCK — the assembled request, never sent",
         "payloads": out}, indent=2) + "\n", encoding="utf-8")
    assert evidence.exists()
