"""MODEL MOCK · REAL DATABASE/RUNNER · REPRODUCTION.

`intent must be an object`, and the run that dies of it.

The defect this module exists for
---------------------------------
Live analytical questions -- "What is driving Stage 2 and ECL growth?",
"ecl decomposition" -- ended with that sentence on the user's screen. Two
separate faults, chained:

1. `intent` was REQUIRED on all five tools. It is a nine-field object and the
   analyst had to restate it verbatim on every catalogue read, every artifact
   read, every execution and the final answer -- four or five times per run,
   for a declaration that does not change between them. A field restated for
   no reason is a field that will eventually be sent wrong, and the answer to
   that is not a sterner instruction; it is to stop asking.

2. When the restatement did come back malformed, the run was ALSO still on
   the Product Help allowance -- sixty seconds -- because the allowance was
   widened inside `_record_intent`, which never ran. So a format slip on a
   field nobody needed twice spent the recovery budget and then the clock,
   and the run died with the parser's sentence as its public message.

What is fixed here
------------------
`intent` is authored once and carried. The server already holds it; a later
call that omits it inherits the run's, and one that restates it may refine
it. The analyst still owns the intent -- what it no longer owns is the
obligation to retype it.

An `intent` that arrives as a JSON STRING of the object is accepted, because
that is a real provider behaviour and re-deriving an object from it is
mechanical.

And the analytical allowance is adopted when an ANALYTICAL TOOL is called,
not when a field parses. Calling `execute_analysis` is itself the
declaration; nothing about the budget needs the model to say it twice.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import contracts as c
from backend.cockpit_v4 import states as st

QUESTION = "What is driving Stage 2 and ECL growth?"


def _execute_args(quarter: str) -> dict:
    """The execute_analysis arguments, as a plain dict this module can bend."""
    return dict(_execute_call(
        EAD_SQL, purpose="Reported EAD by sector", grain="sector",
        units="SAR million", subquestions=["EAD by sector"],
        fields=EAD_FIELDS, quarter=quarter)["input"])


def _answer(messages):
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="Stage 2 and ECL both rose this quarter."))])


# ---- 1. the parser, on its own -----------------------------------------

def test_intent_is_authored_once_and_carried(release_id):
    """§5. A later call inherits the run's intent instead of retyping it."""
    declared = c.parse_intent(intent("DATA_ANALYSIS", "COCKPIT"))
    carried = c.parse_intent(None, carried=declared)
    assert carried.to_dict() == declared.to_dict()
    # Absent and empty both mean "the same as before".
    assert c.parse_intent({}, carried=declared).to_dict() == declared.to_dict()


def test_an_intent_restated_differently_still_wins(release_id):
    """The analyst may refine its reading; it is not frozen at turn one."""
    first = c.parse_intent(intent("DATA_ANALYSIS", "COCKPIT",
                                  understood="EAD by sector"))
    second = c.parse_intent(intent("DATA_ANALYSIS", "COCKPIT",
                                   understood="EAD by sector and by stage"),
                            carried=first)
    assert second.understood_request == "EAD by sector and by stage"


def test_an_intent_sent_as_a_json_string_is_read_not_refused():
    """A real provider behaviour, and a mechanical thing to undo."""
    body = intent("DATA_ANALYSIS", "COCKPIT")
    parsed = c.parse_intent(json.dumps(body))
    assert parsed.query_mode == "DATA_ANALYSIS"
    assert parsed.owner == "COCKPIT"


def test_a_first_call_with_no_intent_at_all_says_what_to_send():
    """Nothing to carry, so it is asked for -- once, and usefully."""
    with pytest.raises(c.Rejection) as caught:
        c.parse_intent(None)
    message = caught.value.message
    assert "query_mode" in message and "owner" in message, message
    assert "must be an object." != message, (
        "the analyst was told what was wrong and not what to do")


def test_something_that_is_not_an_intent_is_still_refused():
    """Postel at the boundary is not the absence of a boundary."""
    for junk in ("Stage 2 growth", 42, [1, 2], json.dumps([1, 2])):
        with pytest.raises(c.Rejection):
            c.parse_intent(junk)


# ---- 2. the schema and the parser agree --------------------------------

def test_every_tool_schema_says_exactly_what_the_parser_requires():
    """§5, and then §24. What is advertised is what is enforced.

    The earlier round made `intent` optional rather than required, which
    stopped it being demanded and left it being sent -- and a field that is
    still sent can still arrive as a string. It did, twice, on live Mac
    runs. So the field is gone from every tool: the run owns an intent
    envelope from its first second and nothing asks the analyst to restate
    it. The parser still ACCEPTS one (see the tolerance tests above),
    because tolerating what a model sends is not the same as asking for it.
    """
    for tool in c.provider_tools():
        schema = tool["input_schema"]
        assert "intent" not in schema.get("required", []), (
            f"{tool['name']} still demands a restated intent")
        assert "intent" not in schema["properties"], (
            f"{tool['name']} still offers an intent to restate")
        # Nor anywhere inside it, which is where it would reappear if
        # somebody "kept it for compatibility".
        assert "intent" not in json.dumps(schema), tool["name"]

    by_name = {t["name"]: t["input_schema"] for t in c.provider_tools()}
    finalize = by_name["finalize_response"]
    for field in c.FLAT_INTENT_FIELDS:
        assert field in finalize["properties"], (
            f"the answer must still be able to state {field}")
        assert field not in finalize["required"], (
            f"{field} is answer content, not a precondition")


#: NOT narrowed, and deliberately.
#:
#: The wire schema spells an optional field `"type": ["array", "null"]` and
#: marks it required, which is the "always required, explicitly nullable"
#: pattern. Replacing that with a single type looked like sensible hardening
#: against a constrained decoder -- until it was checked: unions are not on
#: the provider's unsupported list, Product Help runs fine today with them,
#: and nothing here can show they contributed to the malformed intent.
#:
#: Changing the model-facing contract on a hunch is how the next round gets
#: an unexplained regression. What IS established is that `intent` was
#: restated four or five times a run for no reason, and that the analytical
#: allowance waited on that restatement parsing. Those are what changed.


def test_a_null_is_still_accepted_from_the_model():
    """The schema stops advertising null; the parser keeps accepting it.

    Narrowing what we ASK for must not narrow what we tolerate. A model that
    sends `"blocking_ambiguities": null` is saying there are none, and
    refusing that would be a new way to fail for no gain.
    """
    body = intent("DATA_ANALYSIS", "COCKPIT")
    body["blocking_ambiguities"] = None
    body["resolved_assumptions"] = None
    parsed = c.parse_intent(body)
    assert parsed.blocking_ambiguities == ()
    assert parsed.resolved_assumptions == ()


# ---- 3. the run, end to end --------------------------------------------

def test_an_analysis_publishes_when_only_the_first_call_declares_intent(
        drive, release_id):
    """A. The restatement is gone and the run does not notice."""
    quarter = oracles.latest_quarter(release_id)
    args = _execute_args(quarter)

    def without_intent(messages):
        body = dict(args)
        body.pop("intent")
        return ScriptedResult(tool_calls=[
            tool_call("execute_analysis", body, "tu-x")])

    def answer_without_intent(messages):
        from test_orchestration_recovery import _execution_result

        step = _execution_result(messages)["steps"][0]
        body = final(narrative="Reported EAD by sector, latest quarter.",
                     tables=[{"title": "Reported EAD by sector",
                              "artifact_id": step["artifact_id"],
                              "columns": step["columns"]}])
        body.pop("intent")
        return ScriptedResult(tool_calls=[
            tool_call("finalize_response", body)])

    outcome, provider, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog",
            {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
             "query": "ead by sector", "relation_ids": [], "field_ids": [],
             "detail": [], "reporting_periods": [], "sample_rows": 0,
             "cursor": ""})]),
        without_intent, answer_without_intent])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3, (
        "a carried intent must not cost a correction round")


def _analysis_turns(quarter: str):
    """One execution and one answer, the way a real analytical run goes."""
    def answer(messages):
        from test_orchestration_recovery import _execution_result

        step = _execution_result(messages)["steps"][0]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="Reported EAD by sector, latest quarter.",
                  tables=[{"title": "Reported EAD by sector",
                           "artifact_id": step["artifact_id"],
                           "columns": step["columns"]}]))])

    return [ScriptedResult(tool_calls=[
        tool_call("execute_analysis", _execute_args(quarter), "tu-x")]),
        answer]


def test_a_data_analysis_run_gets_the_analytical_allowance(drive, store_db,
                                                           release_id):
    """§8, B. The tool is the declaration. The clock widens on the tool."""
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(QUESTION, _analysis_turns(quarter))

    assert outcome.state == st.COMPLETED, outcome.message
    budget = store_db.get_run(record.run_id).budget
    analytical = config_mod.analytical_limits_for(record.mode)
    assert budget["deadline_seconds"] == analytical.deadline_seconds, (
        f"an executed analysis ran on a "
        f"{budget['deadline_seconds']}s allowance; the analytical allowance "
        f"is {analytical.deadline_seconds}s")


def test_the_allowance_widens_before_the_query_runs(drive, store_db,
                                                    release_id):
    """§8. Classified BEFORE the expensive loop, not after it.

    The sequence that matters: the execution is REQUESTED, the allowance
    widens, and only then does the query spend the clock. It used to widen
    from the parsed `intent` -- steps later, and never at all when that field
    came back malformed.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(QUESTION, _analysis_turns(quarter))
    assert outcome.state == st.COMPLETED, outcome.message

    events = store_db.events_since(record.run_id)
    order = [e.operation for e in events]
    # The allowance is in force BEFORE the query is even requested. It used
    # to be adopted from the parsed `intent` -- steps later, and never at all
    # when that field came back malformed -- and is now decided at intake by
    # `envelope.classify`, with the declaration path left as the fallback for
    # a question that reads as neither.
    assert "allowance" in order, (
        f"no allowance was ever stated; operations were {sorted(set(order))}")
    allowance_at = order.index("allowance")
    requested_at = order.index("execute_analysis")
    assert allowance_at < requested_at, (
        f"allowance at {allowance_at}, execution requested at "
        f"{requested_at}. Operations: {order}")
    stated = events[allowance_at].public_message
    wanted = config_mod.ANALYTICAL_STANDARD_LIMITS
    assert (f"{wanted.deadline_seconds:.0f}s" in stated
            and f"${wanted.spend_ceiling_usd:.2f}" in stated), stated
    assert (store_db.get_run(record.run_id).budget["deadline_seconds"]
            == wanted.deadline_seconds)
    # And none of it waited on a parsed intent.
    assert allowance_at < order.index("intent"), (
        "the allowance still waits on a parsed intent")


def test_product_help_keeps_its_tight_allowance(drive, store_db,
                                                release_id):
    """§8. Nothing here was solved by widening every deadline."""
    outcome, _, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                  narrative="CreditProbe Cockpit analyses a credit book."))])])
    assert outcome.state == st.COMPLETED, outcome.message
    budget = store_db.get_run(record.run_id).budget
    analytical = config_mod.analytical_limits_for(record.mode)
    assert budget["deadline_seconds"] < analytical.deadline_seconds
