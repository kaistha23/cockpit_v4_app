"""REAL DATABASE · MODEL MOCK · UNIT. What an ACTION turn is allowed to be.

The live failure this suite exists for
--------------------------------------
Two ordinary Corporate questions -- "What is driving Stage 2 and ECL
growth?" and a seeded Construction investigation -- each spent about eighty
seconds in "Understanding the request", used the one action-format recovery,
and stopped at CALL_LIMIT with no query executed.

Four things made that possible, and each one has assertions here:

1. `tool_choice` was a parameter `Analyst.ask` accepted and never sent, so a
   turn whose only legal outcome was a tool call was free to write an essay.
2. The action turn carried the FULL `finalize_response` contract -- nine
   kilobytes describing narrative, claims, tables, charts and follow-ups --
   which is an answer's schema handed to a turn that cannot answer.
3. The action turn was given the ANSWER's output allowance, so there was
   room for four thousand tokens of prose before anything had to be decided.
4. One provider call could block for the whole remaining deadline, so the
   first action could spend the budget the second action needed.

None of these is about what the model is good at. They are about what the
request permitted.
"""

from __future__ import annotations

import json
import time

import pytest

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.capability import Capability, PriceCard
from backend.cockpit_v4.provider import Analyst, ProviderFailure

import oracles
from conftest import ScriptedResult, final, intent, tool_call
from test_orchestration_recovery import _ead_call, _good_answer


# ---- §4. the action turn must be a tool call ---------------------------

def test_the_action_turn_requires_a_tool_call(drive, release_id):
    """The parameter that was accepted and dropped.

    A tool choice is the difference between a turn that MAY answer in prose
    and one that must take an action. Where the run's state leaves exactly
    one legal transition the choice NAMES that tool; where more than one is
    legal it is `any`. Claude Opus 5 accepts both; the models that do not
    are handled below.
    """
    outcome, provider, _ = drive("Who are you?", [ScriptedResult(
        tool_calls=[tool_call("finalize_response", final(
            intent=intent("PRODUCT_HELP", "COCKPIT"),
            narrative="CreditProbe is a credit-investigation layer."))])])
    assert outcome.state == st.COMPLETED, outcome.message
    choice = provider.sent[0]["tool_choice"]
    assert choice["type"] in ("any", "tool")
    assert choice.get("disable_parallel_tool_use") is True, (
        "one action per turn: a batch against a single-transition state is "
        "a batch the state machine would have to reject anyway")


def test_the_answer_turn_is_required_to_publish(drive, release_id):
    """Once a result exists, the only legal transition is to publish it.

    This used to be the one turn with no tool choice at all, on the reading
    that writing prose is what an answer is for. The prose still goes in the
    narrative; what the turn may not do is wander back into the analysis it
    has already paid for, so the required tool is `finalize_response` and
    nothing else is on the request.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        _ead_then_answer(quarter))
    assert outcome.state == st.COMPLETED, outcome.message
    action, answer = provider.sent[0], provider.sent[-1]
    assert action["tool_choice"]["name"] == "execute_analysis"
    assert answer["tool_choice"]["name"] == "finalize_response"
    assert {t["name"] for t in answer["tools"]} <= {"finalize_response",
                                                    "read_artifact"}


def test_a_model_that_refuses_forced_tool_use_still_runs(ledger_factory,
                                                         capability):
    """The narrowest alternative, when the mechanism is not available.

    Claude Fable 5.1 and Claude Mythos 5.1 answer 400 to forced tool use.
    Failing a run closed over a latency optimisation would be worse than the
    latency, so the parameter is dropped for the rest of the run and the
    attempt is made again once -- and the call report SAYS it happened,
    because a run that quietly stopped requiring tool calls is a run whose
    action turns can go back to writing essays.
    """
    sent: list[dict] = []

    class Fussy:
        def converse(self, **kwargs):
            sent.append(kwargs)
            if kwargs.get("tool_choice"):
                raise RuntimeError(
                    'Error code: 400 - {"type":"invalid_request_error",'
                    '"message":"tool_choice: type \\"tool\\" and \\"any\\" '
                    'are not supported for this model."}')
            return ScriptedResult(tool_calls=[
                {"id": "t1", "name": "finalize_response", "input": {}}])

    analyst = Analyst(provider=Fussy(), capability=capability,
                      ledger=ledger_factory(), system="s", tools=[])
    analyst.user("go")
    turn = analyst.ask(purpose="ANALYSIS_ACTION", max_output_tokens=512)

    assert turn.tool_calls, "the retry without tool_choice must be accepted"
    assert len(sent) == 2 and sent[0]["tool_choice"] and not sent[1].get(
        "tool_choice")
    assert analyst.forced_tool_use is False, (
        "a model that refused it once must not be asked again this run")
    report = analyst.call_report()["calls"][-1]
    assert "request_parameters_refused" in report
    assert report["tool_choice"] == "auto"


def test_an_unrelated_400_is_not_retried_without_the_parameter(
        ledger_factory, capability):
    """A rejected TOOL SCHEMA is a different fault with a different fix.

    Retrying it without `tool_choice` would hide a defect in what
    CreditProbe published behind a parameter that had nothing to do with it.
    """
    sent: list[dict] = []

    class Rejects:
        def converse(self, **kwargs):
            sent.append(kwargs)
            raise RuntimeError(
                'Error code: 400 - tools.2.custom.input_schema: does not '
                'support minimum')

    analyst = Analyst(provider=Rejects(), capability=capability,
                      ledger=ledger_factory(), system="s", tools=[])
    analyst.user("go")
    with pytest.raises(ProviderFailure) as raised:
        analyst.ask(purpose="ANALYSIS_ACTION", max_output_tokens=512)
    assert raised.value.code == st.TOOL_SCHEMA_INVALID
    assert len(sent) == 1, "one attempt; the schema is the fault"
    assert analyst.forced_tool_use is True


# ---- §3, §5. the action turn carries no answer contract ----------------

def test_an_analytical_action_is_not_offered_the_answer_contract():
    """§3. An action turn exists to choose and author the next action.

    It has executed nothing, and every number in a V4 answer is bound to a
    result that ran -- so narrative claims, evidence refs, tables, charts
    and follow-ups are not fields it could legally fill in. Publishing them
    cost about seven kilobytes on every action AND described, in detail, an
    answer-shaped response to a turn that was supposed to decide something.
    """
    full = {t["name"]: t for t in contracts_mod.provider_tools()}
    action = {t["name"]: t
              for t in contracts_mod.provider_tools(
                  stage="analytical_action")}
    answer_only = {"numeric_claims", "evidence_refs", "tables", "charts",
                   "coverage", "suggested_questions"}

    offered = set(action["finalize_response"]["input_schema"]["properties"])
    assert not (offered & answer_only), (
        f"the action turn is still offered {sorted(offered & answer_only)}")
    assert offered <= set(
        full["finalize_response"]["input_schema"]["properties"]), (
        "the action contract must be a SUBSET of the full one, never a "
        "shape the full contract would reject")


def test_the_action_finalize_can_only_stop_the_run():
    action = {t["name"]: t
              for t in contracts_mod.provider_tools(
                  stage="analytical_action")}
    enum = action["finalize_response"]["input_schema"][
        "properties"]["disposition"]["enum"]
    assert "answer" not in enum and "partial_answer" not in enum
    assert {"clarification", "referral", "unsupported"} <= set(enum)


def test_the_action_tool_payload_is_materially_smaller():
    """§5. Measured, not asserted."""
    def size(tools):
        return len(json.dumps(tools, ensure_ascii=False).encode())

    full = size(contracts_mod.provider_tools())
    action = size(contracts_mod.provider_tools(
        stage="analytical_action",
        withhold=(contracts_mod.TOOL_PRODUCT,)))
    assert action < full / 1.6, (
        f"the analytical action tool set is {action:,} bytes against "
        f"{full:,} for the full one; the saving is the point")


def test_no_tool_schema_asks_the_analyst_to_restate_the_run():
    """§5, §26.7. The run already owns intent, book, release and calendar."""
    for stage in ("", "analytical_action"):
        for tool in contracts_mod.provider_tools(stage=stage):
            blob = json.dumps(tool["input_schema"])
            assert '"intent"' not in blob, (
                f"{tool['name']} still asks for a nested intent object")
            for owned in ("release_id", "release_fingerprint", "tenant_id",
                          "domain_id"):
                assert f'"{owned}"' not in blob, (
                    f"{tool['name']} asks for {owned}, which the run owns")


# ---- §9. two phases, two output allowances -----------------------------

@pytest.mark.parametrize("limits", [config_mod.STANDARD_LIMITS,
                                    config_mod.ANALYTICAL_STANDARD_LIMITS,
                                    config_mod.DEEP_LIMITS,
                                    config_mod.ANALYTICAL_DEEP_LIMITS])
def test_an_action_is_allowed_less_output_than_an_answer(limits):
    assert limits.action_output_tokens < limits.reserved_output_tokens
    # And not so little that a model which thinks before it answers cannot
    # finish thinking: an allowance too tight buys a truncation every time.
    assert limits.action_output_tokens >= 2_048


def test_the_two_phases_ask_for_what_they_are_allowed(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        _ead_then_answer(quarter))
    assert outcome.state == st.COMPLETED, outcome.message
    assert provider.sent[0]["max_tokens"] == limits.action_output_tokens
    assert provider.sent[-1]["max_tokens"] == limits.reserved_output_tokens


# ---- §10. one action cannot eat the run --------------------------------

@pytest.mark.parametrize("limits", [config_mod.ANALYTICAL_STANDARD_LIMITS,
                                    config_mod.ANALYTICAL_DEEP_LIMITS])
def test_two_actions_and_an_answer_fit_inside_the_deadline(limits):
    """The arithmetic §10 asks for, as an assertion rather than a hope."""
    two_actions = 2 * limits.action_call_seconds
    assert two_actions + limits.finalization_reserve_seconds <= (
        limits.deadline_seconds), (
        f"{limits.mode}: two {limits.action_call_seconds:.0f}s actions and a "
        f"{limits.finalization_reserve_seconds:.0f}s answer do not fit in "
        f"{limits.deadline_seconds:.0f}s")
    # And no single action may take most of the run.
    assert limits.action_call_seconds <= limits.deadline_seconds / 2.5


def test_an_action_call_is_bounded_by_the_action_window(ledger_factory):
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    ledger = ledger_factory(deadline_seconds=limits.deadline_seconds,
                            action_call_seconds=limits.action_call_seconds)
    action = ledger.call_timeout_seconds(phase="action")
    answer = ledger.call_timeout_seconds(phase="answer")
    assert action <= limits.action_call_seconds
    assert answer > action, (
        "the ANSWER turn keeps the remainder: it is the last call the run "
        "makes and cutting it short throws away work already paid for")


def test_the_call_timeout_is_what_the_provider_is_actually_given(
        drive, release_id):
    outcome, provider, _ = drive("Who are you?", [ScriptedResult(
        tool_calls=[tool_call("finalize_response", final(
            intent=intent("PRODUCT_HELP", "COCKPIT"),
            narrative="CreditProbe is a credit-investigation layer."))])])
    assert outcome.state == st.COMPLETED, outcome.message
    assert 0 < provider.sent[0]["timeout"] <= (
        config_mod.STANDARD_LIMITS.action_call_seconds)


# ---- §13. the provider's own stop reason, recorded ---------------------

def test_every_generation_records_its_stop_reason(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        _ead_then_answer(quarter))
    assert outcome.state == st.COMPLETED, outcome.message
    for call in outcome.call_report["calls"]:
        assert call.get("stop_reason"), (
            f"generation {call['seq']} recorded no stop reason")
        assert "provider_ms" in call and "serialize_ms" in call
        assert "count_ms" in call


# ---- §12. prose AROUND a valid tool call is not a format failure -------

def test_a_tool_call_with_harmless_text_beside_it_is_accepted(drive,
                                                              release_id):
    """Recovery is for a response with no action in it, not for commentary.

    The tool call is complete and its arguments still go through the same
    validator; what is NOT spent is the one action recovery, on a turn that
    did exactly what was asked and said a sentence while doing it.
    """
    call = tool_call("finalize_response", final(
        intent=intent("PRODUCT_HELP", "COCKPIT"),
        narrative="CreditProbe is a credit-investigation layer."))
    turn = ScriptedResult(tool_calls=[call], text="Happy to help with that.")
    turn.assistant_blocks = (
        [{"type": "text", "text": "Happy to help with that."}]
        + list(turn.assistant_blocks))

    outcome, provider, _ = drive("Who are you?", [turn])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 1, (
        "prose beside a valid tool call must not cost a second generation")


# ---- helpers -----------------------------------------------------------

def _ead_then_answer(quarter: str):
    """One execution and one answer: the shape a simple analysis takes."""
    return [ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer]
