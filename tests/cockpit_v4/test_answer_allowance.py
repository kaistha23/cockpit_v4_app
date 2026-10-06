"""
The answer turn could not fit its own answer.

MODEL MOCK · REAL DATABASE/RUNNER · REAL API. No paid provider call.

Thread `th-48fdeffe125f489592f627e307852e31` executed its query and then was
cut off writing the answer about it, twice, and stopped as
`ANSWER_FORMAT_EXHAUSTED`. Stage 1 published the rows anyway. This is the
other half: why the answer could not be written, and why asking again could
not have helped.

It was arithmetic, not judgement. A correct answer to a four-step analysis --
sixteen claims, a narrative that references them, four tables and two charts
-- serializes to about 12.6 KB, which the run's own token estimator puts at
~5,700 tokens. The allowance was 4,096. The object did not overrun a generous
budget; it could not be emitted at any effort setting, and the re-ask was the
identical request at the identical size with "keep it compact" appended.

Four things were wrong and all four are asserted here:

  the allowance was too small          -> raised, and measured
  the re-ask was the same request      -> ceiling + lower effort
  one truncation spent the only re-ask -> two, and they are not shared
  the first attempt could eat the clock -> bounded while a successor exists
"""

from __future__ import annotations

import pytest
from conftest import ScriptedResult
from test_orchestration_recovery import _ead_call, _good_answer

import oracles
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import OutputTruncated

ANALYTICAL = (config_mod.ANALYTICAL_STANDARD_LIMITS,
              config_mod.ANALYTICAL_DEEP_LIMITS)
ALL_LIMITS = (config_mod.STANDARD_LIMITS, config_mod.DEEP_LIMITS) + ANALYTICAL


def _cut_off(limit: int = 4_096) -> OutputTruncated:
    return OutputTruncated("cut off", limit=limit)


def _answer_calls(provider):
    """Every call the run made with the answer's tool surface.

    Identified by purpose rather than by position: a run that recovers makes
    a different number of action calls, and a test that counted them would
    be asserting the shape of the failure rather than the shape of the fix.
    """
    return [c for c in provider.sent
            if c["purpose"] in ("FINAL_ANSWER", "ANSWER_FORMAT_RECOVERY")]


# ---- the allowance ------------------------------------------------------

def test_a_truncated_answer_is_asked_again_with_more_room(
        drive, release_id):
    """THE fix. The re-ask must differ from the request that failed."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    first, retry = _answer_calls(provider)[:2]
    assert first["max_tokens"] == limits.reserved_output_tokens
    assert retry["max_tokens"] == limits.answer_output_ceiling
    assert retry["max_tokens"] > first["max_tokens"], (
        "the same allowance that overran cannot be the one re-asked at")


def test_the_re_ask_thinks_less_so_the_answer_can_be_longer(
        drive, release_id):
    """Thinking is spent from the same allowance as the answer.

    A truncated turn is one where the two competed and the object lost. By
    the re-ask the analysis has run and the figures are chosen; what is left
    is serialising a decision already taken.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    first, retry = _answer_calls(provider)[:2]
    assert first["output_config"] == {"effort": limits.answer_effort}
    assert retry["output_config"] == {
        "effort": limits.answer_recovery_effort}
    assert retry["output_config"] != first["output_config"]


def test_the_re_ask_is_told_what_changed_and_what_not_to_do(
        drive, release_id):
    """"Keep it compact" asked the model to solve the wrong problem.

    The object that overran was the right size for the analysis. Told to
    shrink, the honest thing for an analyst to do is drop claims or rows --
    a worse answer, not a shorter one.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    said = str(_answer_calls(provider)[1]["messages"][-1]).lower()
    assert "larger" in said, "the re-ask must say the allowance moved"
    assert "do not drop" in said
    assert "keep it compact" not in said


def test_the_escalation_goes_up_once_and_then_stops(drive, release_id):
    """One-way and bounded. A raised allowance is not an unbounded one."""
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _cut_off(), _cut_off(), _good_answer])
    assert outcome.state == st.PARTIAL, outcome.message
    assert outcome.error_code == st.ANSWER_FORMAT_EXHAUSTED

    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    allowances = [c["max_tokens"] for c in _answer_calls(provider)]
    assert allowances[0] == limits.reserved_output_tokens
    assert allowances[1] == limits.answer_output_ceiling
    assert allowances[1:] == allowances[1:2] * len(allowances[1:]), (
        f"the ceiling must be a ceiling: {allowances}")


# ---- the recovery budget ------------------------------------------------

def test_a_truncated_answer_no_longer_disarms_a_malformed_one(
        drive, release_id):
    """Three failure modes share the answer-side counter.

    A truncation, a turn with no tool call, and an illegal batch all spend
    from it. At a budget of one, whichever fired first disarmed the other
    two -- so a run that was cut off once and then came back without a tool
    call had nothing left, although each failure had happened exactly once.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(),                              # truncated
         ScriptedResult(text="Here is what I found."),   # no tool call
         _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message


def test_the_answer_budget_is_still_bounded(drive, release_id):
    """Two is not unlimited."""
    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _cut_off(), _cut_off(), _good_answer])
    assert outcome.error_code == st.ANSWER_FORMAT_EXHAUSTED


# ---- the clock ----------------------------------------------------------

def test_an_answer_with_a_successor_may_not_eat_the_clock(ledger_factory):
    """The live shape: 43s + 47s of 120s, and 7.8s left for the third."""
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    ledger = ledger_factory(deadline_seconds=limits.deadline_seconds,
                            answer_call_seconds=limits.answer_call_seconds,
                            answer_format_regenerations=(
                                limits.answer_format_regenerations))
    assert ledger.answer_reask_remains()
    assert ledger.call_timeout_seconds(phase="answer") <= (
        limits.answer_call_seconds)


def test_the_last_answer_attempt_keeps_the_whole_remainder(ledger_factory):
    """Bounding the last attempt would throw away work already paid for.

    So the bound is a question about the future -- can CreditProbe ask
    again? -- and not about the phase.
    """
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    ledger = ledger_factory(deadline_seconds=limits.deadline_seconds,
                            answer_call_seconds=limits.answer_call_seconds,
                            answer_format_regenerations=1)
    ledger.spend_format_recovery(phase="answer")
    assert not ledger.answer_reask_remains()
    timeout = ledger.call_timeout_seconds(phase="answer")
    assert timeout > limits.answer_call_seconds, (
        "the final attempt is the one that must not be cut short")
    assert timeout <= ledger.remaining_seconds


def test_an_answer_is_never_bounded_to_an_action_window(ledger_factory):
    """Pinned: the answer turn is the long one, in every configuration."""
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    ledger = ledger_factory(deadline_seconds=limits.deadline_seconds,
                            answer_call_seconds=limits.answer_call_seconds)
    assert (ledger.call_timeout_seconds(phase="answer")
            > ledger.call_timeout_seconds(phase="action"))


@pytest.mark.parametrize("limits", ANALYTICAL,
                         ids=lambda lim: lim.mode)
def test_an_action_an_answer_and_a_re_ask_fit_inside_the_deadline(limits):
    """The arithmetic, as an assertion rather than a hope.

    One action, one bounded answer, and enough left for the re-ask the
    answer budget promises. The live run failed this: nothing bounded the
    first answer, so the second had 7.8 seconds.
    """
    assert (limits.action_call_seconds + limits.answer_call_seconds
            + limits.min_call_seconds <= limits.deadline_seconds), (
        f"{limits.mode}: no room for the re-ask the budget promises")
    assert limits.answer_call_seconds > limits.action_call_seconds, (
        "the answer is the long turn; bounding it to an action window is "
        "what this bound exists NOT to do")


# ---- the sizing, and the ceiling nobody was checking ---------------------

@pytest.mark.parametrize("limits", ALL_LIMITS, ids=lambda lim: lim.mode)
def test_the_answer_allowance_is_bigger_than_the_action_allowance(limits):
    assert limits.action_output_tokens < limits.reserved_output_tokens
    assert limits.reserved_output_tokens < limits.answer_output_ceiling
    assert limits.action_output_ceiling <= limits.reserved_output_tokens


@pytest.mark.parametrize("limits", ALL_LIMITS, ids=lambda lim: lim.mode)
def test_no_allowance_exceeds_what_the_model_will_emit(limits, capability):
    """The tripwire that did not exist.

    `provider.py` clamps the request to `capability.max_output_tokens` with
    a silent `min()` -- no validation, no event, no call-report field that
    distinguishes "I asked for 12,288" from "the model allows 8,192". So a
    ceiling above the model's own limit would be configured, reported in the
    trace, and never once sent, and every test of the escalation would pass
    while exercising nothing.
    """
    for field in ("reserved_output_tokens", "answer_output_ceiling",
                  "action_output_tokens", "action_output_ceiling"):
        assert getattr(limits, field) <= capability.max_output_tokens, (
            f"{limits.mode}.{field} would be silently clamped")


def _tokens(obj) -> int:
    """The run's own estimator: `provider.count_input`, len/2.2."""
    import json

    return int(len(json.dumps(obj, ensure_ascii=False, default=str)) / 2.2) + 1


def test_the_answer_allowance_covers_a_real_four_step_answer():
    """Measured, not asserted from taste.

    A four-step analysis answered properly -- sixteen claims, a narrative
    that references them, four tables, two charts, coverage, limitations and
    follow-ups -- is what the live thread was trying to emit.

    BOTH SPELLINGS, because the regression and the fix are different facts.
    The long form is the object that overran: every operand naming every row
    id, twelve times over. It still overruns 4,096, and it has to, or the
    incident this file exists for stops being reproducible. The shorthand
    form is the same answer saying "every row" instead of reciting them, and
    what it establishes is that the truncation was never about how much
    there was to say.
    """
    from answer_object import four_step_answer

    long_form = _tokens(four_step_answer(shorthand=False))
    short = _tokens(four_step_answer())
    base = config_mod.ANALYTICAL_STANDARD_LIMITS.reserved_output_tokens

    assert long_form > 4_096, (
        f"the long form needs ~{long_form:,} tokens and no longer overruns "
        f"the OLD 4,096 allowance; the regression this pins has changed "
        f"shape, so re-measure before relaxing the bound")
    assert base > long_form, (
        f"a correct answer needs ~{long_form:,} tokens and the allowance is "
        f"{base:,}; the object cannot be emitted at any effort setting")
    assert short < long_form, "the shorthand did not make the object smaller"
    assert short < 4_096, (
        f"the same answer, saying 'every row' rather than listing them, "
        f"needs ~{short:,} tokens -- it was supposed to fit inside the "
        f"allowance the live run overran")


def test_the_row_ids_were_two_thirds_of_what_the_answer_had_to_write():
    """Where the weight actually was.

    Sixteen claims over four results, and eight of their operands spanned a
    whole hundred-row result -- four totals and four share denominators.
    Each of those named a hundred ids, so the analyst spent most of its
    output reciting the result back to the server that had just sent it.
    """
    import json

    from answer_object import four_step_answer

    long_form = four_step_answer(shorthand=False)
    claims = len(json.dumps(long_form["numeric_claims"], ensure_ascii=False))
    whole = len(json.dumps(long_form, ensure_ascii=False))
    assert claims / whole > 0.6, (
        f"numeric_claims are {claims / whole:.0%} of the long-form answer; "
        f"the measurement this round was sized from said two thirds")


# ---- the trace says what the call was actually granted -------------------

def _model_responses(store, run_id):
    """Every `model.response_received` event, with its detail body."""
    out = []
    for event in store.events_since(run_id):
        if event.event_type != "model.response_received":
            continue
        record = store.get_detail(event.detail_ref) if event.detail_ref \
            else None
        out.append((event, (record or {}).get("body") or {}))
    return out


def test_the_trace_records_what_the_call_was_granted_not_only_wanted(
        drive, store_db, release_id):
    """`model.requested` is emitted BEFORE the call, so it can only say
    what was wanted. `affordable_output_tokens` then cuts that to fit the
    cost ceiling, and until now the only record was one field on a
    call-report row nothing reads -- so a truncation at a reduced allowance
    looked like the model overrunning a generous one.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, _, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    responses = _model_responses(store_db, record.run_id)
    assert responses, "no model response was traced at all"
    for _event, body in responses:
        granted = body.get("output_allowance")
        assert granted, "the trace must say what the call was granted"
        assert set(granted) >= {"wanted", "granted", "reduced"}
        assert granted["granted"] <= granted["wanted"]
        assert granted["reduced"] is (granted["granted"] < granted["wanted"])


def test_a_reduced_answer_allowance_is_said_out_loud(drive, release_id):
    """The reader is owed the reason, and "the cost ceiling decided how
    long this could be" is a different fact from "the model wrote too
    much"."""
    from backend.cockpit_v4.orchestration import Orchestrator

    said = Orchestrator._allowance_message
    full = type("A", (), {"response_allowance": {
        "wanted": 8_192, "granted": 8_192, "reduced": False}})()
    cut = type("A", (), {"response_allowance": {
        "wanted": 8_192, "granted": 3_100, "reduced": True}})()

    assert said(type("O", (), {"analyst": full})(),
                True) == "Response received."
    # Not mentioned on an action turn: length is not what a reader
    # experiences there, and every action turn would say it.
    assert said(type("O", (), {"analyst": cut})(),
                False) == "Response received."
    message = said(type("O", (), {"analyst": cut})(), True)
    assert "3,100" in message and "8,192" in message
    assert "cost ceiling" in message


# ---- the number the re-ask promises -------------------------------------

def _truncation_events(store, run_id):
    """Every `retry.requested` raised by a truncation, with its detail."""
    out = []
    for event in store.events_since(run_id):
        if event.event_type != "retry.requested":
            continue
        record = store.get_detail(event.detail_ref) if event.detail_ref \
            else None
        body = (record or {}).get("body") or {}
        if body.get("overran"):
            out.append((event, body))
    return out


def test_the_re_ask_names_the_allowance_the_next_call_actually_gets(
        drive, store_db, release_id):
    """A promise the next call keeps.

    The event and the prompt both quote a number. The allowance is chosen
    by `answering` -- was this the turn that writes the answer -- while the
    RECOVERY COUNTER is charged by `executed`, because the cost of a
    truncation is the work already done behind it. The two agree on every
    ordinary turn and diverge on an action issued after a query has run.
    Quoting the counter's predicate there would name the answer ceiling to
    a turn that will be given the action's.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    events = _truncation_events(store_db, record.run_id)
    assert len(events) == 1, "expected exactly one truncation"
    event, body = events[0]
    assert body["answering"] is True
    promised = body["next_allowance"]
    assert promised, "the trace must say what the next attempt is allowed"

    retry = _answer_calls(provider)[1]
    assert retry["max_tokens"] == promised, (
        f"the re-ask was promised {promised:,} tokens and sent "
        f"{retry['max_tokens']:,}")
    assert f"{promised:,}" in (event.public_message or "")
    assert f"{promised:,}" in str(retry["messages"][-1])


def test_a_truncated_action_is_not_promised_the_answers_allowance(
        drive, store_db, release_id):
    """The same promise on the other side of the ladder.

    An action cut off before anything ran is re-asked at the ACTION
    ceiling, and its message must not offer a reader -- or the model -- the
    answer's number.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [_cut_off(3_072), ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message

    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    events = _truncation_events(store_db, record.run_id)
    assert len(events) == 1, "expected exactly one truncation"
    event, body = events[0]
    assert body["answering"] is False
    assert body["next_allowance"] == limits.action_output_ceiling
    assert body["next_allowance"] < limits.answer_output_ceiling
    assert f"{limits.answer_output_ceiling:,}" not in (
        event.public_message or ""), (
        "an action re-ask was told about the answer's allowance")

    actions = [c for c in provider.sent if c not in _answer_calls(provider)]
    assert actions[1]["max_tokens"] == limits.action_output_ceiling
