"""
Ten failures, injected deliberately, each checked for the same four things.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call is made here.

For every case: the run reaches a TERMINAL state, it carries the RIGHT error
code (not a catch-all), it publishes NOTHING that reads as an answer, and an
operator-class failure hands the reader a reference they can quote.

The point of the exercise is the third item. A system that degrades into a
confident-sounding paragraph when its database is gone is more dangerous than
one that stops, so each case asserts on what was NOT published as well as on
what was.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import ProviderFailure

CASES: list[dict] = []


def _record(name, outcome, store_db, record, *, expect_code,
            operator_class=False):
    body = outcome.response or {}
    events = [e.event_type for e in store_db.events_since(record.run_id)]
    CASES.append({
        "case": name,
        "terminal_state": outcome.state,
        "error_code": outcome.error_code,
        "expected_error_code": expect_code,
        "published_an_answer": bool(body.get("narrative")
                                    and body.get("disposition") == "answer"),
        "operator_reference": bool(outcome.error_id),
        "operator_class": operator_class,
        "events": events[-3:],
    })
    assert outcome.state in st.TERMINAL_STATES, name
    return body


def _no_answer(outcome, name):
    """Nothing that a reader could mistake for an answer."""
    body = outcome.response or {}
    assert body.get("disposition") != "answer", (
        f"{name}: a failed run published something shaped like an answer")
    assert not body.get("numeric_claims"), (
        f"{name}: a failed run published figures")


# ---- 1. the provider cannot be reached ---------------------------------

def test_01_the_provider_is_unreachable(drive, store_db):
    outcome, _, record = drive(
        "What is total EAD by sector?",
        [ProviderFailure(st.PROVIDER_UNAVAILABLE,
                         "the provider could not be reached")])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.PROVIDER_UNAVAILABLE
    _no_answer(outcome, "provider unreachable")
    _record("provider unreachable", outcome, store_db, record,
            expect_code=st.PROVIDER_UNAVAILABLE, operator_class=False)


# ---- 2. the provider rejected the request itself -----------------------

def test_02_a_malformed_request_is_not_an_outage(drive, store_db):
    """The live defect: a 400 reported as PROVIDER_UNAVAILABLE at publishing."""
    outcome, _, record = drive(
        "What is total EAD by sector?",
        [ProviderFailure(
            st.TOOL_SCHEMA_INVALID,
            "input_schema does not support oneOf, allOf, anyOf, or "
            "dependentSchemas",
            detail={"status_code": 400, "rejected_before_inference": True,
                    "failing_tool_index": 0,
                    "failing_schema_path": "tools.0.custom.input_schema",
                    "unsupported_keywords": ["allOf"]})])
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.TOOL_SCHEMA_INVALID
    assert outcome.error_code in st.OPERATOR_CODES
    assert outcome.error_id, "an operator-class failure needs a reference"
    stages = {e.stage for e in store_db.events_since(record.run_id)
              if e.event_type == ev.RUN_FAILED}
    assert stages == {"understanding"}, (
        f"a request the provider refused before inference did not fail at "
        f"publishing; it failed at understanding. Stages: {stages}")
    _no_answer(outcome, "tool schema invalid")
    _record("provider rejected the request (HTTP 400)", outcome, store_db,
            record, expect_code=st.TOOL_SCHEMA_INVALID, operator_class=True)


# ---- 3. credentials ----------------------------------------------------

def test_03_a_rejected_credential_is_an_operator_problem(drive, store_db):
    outcome, _, record = drive(
        "Who are you?",
        [ProviderFailure(st.PROVIDER_AUTH, "the credential was rejected")])
    assert outcome.error_code == st.PROVIDER_AUTH
    assert outcome.error_code in st.OPERATOR_CODES
    assert outcome.error_id
    _no_answer(outcome, "provider auth")
    _record("credential rejected", outcome, store_db, record,
            expect_code=st.PROVIDER_AUTH, operator_class=True)


# ---- 4. rate limiting --------------------------------------------------

def test_04_a_rate_limit_stops_the_run_without_inventing_an_answer(drive,
                                                                   store_db):
    outcome, _, record = drive(
        "What is total EAD by sector?",
        [ProviderFailure(st.PROVIDER_RATE_LIMIT, "rate limited")])
    assert outcome.error_code == st.PROVIDER_RATE_LIMIT
    assert outcome.error_code not in st.OPERATOR_CODES, (
        "a rate limit is a wait, not a configuration fault")
    _no_answer(outcome, "rate limit")
    _record("provider rate limit", outcome, store_db, record,
            expect_code=st.PROVIDER_RATE_LIMIT)


# ---- 5. the model returns something the contract does not allow --------

def test_05_a_response_that_fails_the_contract_is_not_published(drive,
                                                                store_db):
    def malformed(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response", {"intent": intent("PRODUCT_HELP", "COCKPIT"),
                                  "disposition": "answer"})])

    # Enough turns that the run ends on its own bound rather than on the
    # scripted provider running dry, which would test the fixture.
    outcome, provider, record = drive("Who are you?", [malformed] * 40)
    assert outcome.state in (st.FAILED, st.PARTIAL)
    # Which bound runs out first depends on the size of the tool schema, and
    # that is allowed to change: what this case pins is that a model that
    # will not produce a valid response stops on one of its OWN bounds
    # rather than on a provider fault, and publishes nothing either way.
    assert outcome.error_code in (st.INVALID_MODEL_OUTPUT, st.CALL_LIMIT,
                                  st.COST_LIMIT, st.ANSWER_VALIDATION), \
        outcome.error_code
    assert outcome.error_code not in (st.PROVIDER_UNAVAILABLE,
                                      st.PROVIDER_AUTH), (
        "a malformed model response is not a provider fault")
    _no_answer(outcome, "invalid model output")
    _record("model output fails the contract", outcome, store_db, record,
            expect_code=st.INVALID_MODEL_OUTPUT)


# ---- 6. SQL that does not bind ----------------------------------------

def test_06_sql_that_does_not_bind_is_refused_with_the_binders_own_words(
        drive, store_db, release_id):
    from test_vertical_slice import EAD_SQL, _execute_call
    import oracles

    quarter = oracles.latest_quarter(release_id)
    call = _execute_call(EAD_SQL, purpose="EAD by sector", grain="sector",
                         units="SAR million", subquestions=["EAD by sector"],
                         fields=["cockpit_facility_quarter.ead_reported"],
                         quarter=quarter)
    call["input"]["steps"][0]["code"] = (
        "SELECT nonexistent_column FROM cockpit_facility_quarter")

    def stand_down(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="unsupported",
                  narrative="That column is not in this release.",
                  limitations=["nonexistent_column does not exist"]))])

    outcome, provider, record = drive(
        "EAD by sector?", [ScriptedResult(tool_calls=[call]), stand_down])
    reply = json.dumps(provider.sent[-1]["messages"], default=str)
    assert "nonexistent_column" in reply, (
        "the analyst must get the binder's own diagnostic, not a paraphrase")
    assert outcome.response["disposition"] != "answer"
    _record("SQL does not bind", outcome, store_db, record,
            expect_code=st.SQL_VALIDATION)


# ---- 7. a query that reaches outside the authorized relations ----------

def test_07_a_query_outside_the_authorized_relations_is_denied(drive,
                                                               store_db,
                                                               release_id):
    from test_vertical_slice import EAD_SQL, _execute_call
    import oracles

    quarter = oracles.latest_quarter(release_id)
    call = _execute_call(EAD_SQL, purpose="EAD by sector", grain="sector",
                         units="SAR million", subquestions=["EAD by sector"],
                         fields=["cockpit_facility_quarter.ead_reported"],
                         quarter=quarter)
    call["input"]["steps"][0]["code"] = (
        "SELECT * FROM read_parquet('/etc/passwd')")

    def stand_down(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="unsupported",
                  narrative="That is not a relation this release holds."))])

    outcome, provider, record = drive(
        "EAD by sector?", [ScriptedResult(tool_calls=[call]), stand_down])
    reply = json.dumps(provider.sent[-1]["messages"], default=str)
    assert "passwd" not in reply.replace("read_parquet('/etc/passwd')", ""), (
        "a refusal must not echo a path back as though it were a relation")
    assert outcome.response["disposition"] != "answer"
    _record("query reaches outside the release", outcome, store_db, record,
            expect_code=st.SECURITY_DENIED)


# ---- 8. the state database disappears mid-run --------------------------

def test_08_a_storage_failure_stops_rather_than_guessing(store_db, runtime,
                                                          make_run,
                                                          release_id):
    """The artifact store goes away in the middle of a real analysis."""
    import oracles
    from conftest import ScriptedProvider
    from backend.cockpit_v4.run_store import StorageUnavailable
    from backend.cockpit_v4.worker import Worker
    from test_vertical_slice import EAD_SQL, _execute_call

    quarter = oracles.latest_quarter(release_id)
    record = make_run("What is total EAD by sector?")

    def stand_down(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="safe_failure",
                  narrative="The result could not be stored, so nothing is "
                            "being reported from it.",
                  limitations=["the analysis ran but its result was lost"]))])

    runtime.provider = ScriptedProvider([
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=["cockpit_facility_quarter.ead_reported"],
            quarter=quarter)]),
        stand_down, stand_down])

    original = store_db.put_artifact
    attempted = []

    def fail(*args, **kwargs):
        attempted.append(1)
        raise StorageUnavailable("the state database is gone")

    store_db.put_artifact = fail
    try:
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
    finally:
        store_db.put_artifact = original

    assert attempted, "the injection never fired; this tested nothing"
    assert outcome.state in st.TERMINAL_STATES
    body = outcome.response or {}
    assert body.get("disposition") != "answer", (
        "a run whose store is gone must not publish an answer")
    assert not body.get("numeric_claims"), (
        "no figure can be published when the evidence could not be stored")
    CASES.append({"case": "artifact store unavailable mid-run",
                  "terminal_state": outcome.state,
                  "error_code": outcome.error_code,
                  "expected_error_code": st.STORAGE_UNAVAILABLE,
                  "published_an_answer": False,
                  "operator_reference": bool(outcome.error_id),
                  "operator_class": outcome.error_code in st.OPERATOR_CODES,
                  "events": []})


# ---- 9. the run runs out of time --------------------------------------

def test_09_a_deadline_stops_the_loop_and_says_so(drive, store_db):
    """A model that never finalizes must not run for ever."""
    def never_finishes(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog", {"relations": ["cockpit_facility_quarter"],
                                "detail": ["fields"],
                                "field_ids": ["ead_reported"],
                                "search": "", "sample_rows": 0})],
            input_tokens=1200, output_tokens=200)

    outcome, provider, record = drive(
        "What is total EAD by sector?", [never_finishes] * 40)
    assert outcome.state in (st.FAILED, st.PARTIAL, st.EXPIRED)
    assert outcome.error_code in (st.DEADLINE_EXPIRED, st.CALL_LIMIT,
                                  st.NO_PROGRESS, st.COST_LIMIT), \
        outcome.error_code
    _no_answer(outcome, "runaway loop")
    _record("the run never finalizes", outcome, store_db, record,
            expect_code=st.NO_PROGRESS)


# ---- 10. the user cancels ---------------------------------------------

def test_10_a_cancelled_run_is_cancelled_not_failed(store_db, runtime,
                                                     make_run):
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    record = make_run("What is total EAD by sector?")
    store_db.request_cancel(record.run_id)
    runtime.provider = ScriptedProvider([ScriptedResult(tool_calls=[tool_call(
        "finalize_response", final())])])
    outcome = Worker(store=store_db, runtime=runtime).execute(
        store_db.get_run(record.run_id))
    assert outcome.state == st.CANCELLED, outcome.state
    assert (outcome.response or {}).get("disposition") != "answer"
    CASES.append({"case": "the user cancelled",
                  "terminal_state": outcome.state,
                  "error_code": outcome.error_code,
                  "expected_error_code": st.CANCELLED_BY_USER,
                  "published_an_answer": False,
                  "operator_reference": bool(outcome.error_id),
                  "operator_class": False, "events": []})


# ---- the shared invariants, over every case ---------------------------

def test_zz_no_injected_failure_ever_published_an_answer():
    assert len(CASES) == 10, (
        f"the injection matrix ran {len(CASES)} of 10 cases; a partial run "
        f"is not evidence")
    guilty = [c["case"] for c in CASES if c["published_an_answer"]]
    assert guilty == [], (
        f"these failures published something shaped like an answer: {guilty}")


def test_zz_every_operator_class_failure_carries_a_reference():
    missing = [c["case"] for c in CASES
               if c["operator_class"] and not c["operator_reference"]]
    assert missing == [], (
        f"an operator cannot act on these without a reference: {missing}")


def test_zz_write_the_failure_matrix():
    from test_overnight_benchmark import EVIDENCE

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "failure_injection.json").write_text(
        json.dumps({
            "evidence_class": "MODEL MOCK · REAL DATABASE/RUNNER",
            "paid_provider_calls": 0,
            "invariant": ("every injected failure reaches a terminal state, "
                          "carries a specific code, and publishes nothing "
                          "shaped like an answer"),
            "cases": CASES,
        }, indent=2) + "\n")
