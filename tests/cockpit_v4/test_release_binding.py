"""MODEL MOCK · REAL DATABASE/RUNNER · UNIT.

An artifact from one release is not evidence for another.

Every analytical object carries a release id, and an id is a name. Two builds
of one id share the name and differ in every number, so "same release_id" is
satisfied perfectly by a stored artifact from before a rebuild. What they
cannot share is a fingerprint of the bytes, so that is what evidence
validation compares.

§6 lists the objects that must be release-bound and says to hard-test it.
§32 says a seeded investigation must never reach the release it did not come
from.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_orchestration_recovery import _execution_result
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import release as rel
from backend.cockpit_v4 import states as st

SAUDI = "v4-saudi-20q-v1"
OTHER = "v4-uat-20q-v1"
QUESTION = "What is total exposure at default by sector in the latest quarter?"


def _answer(messages):
    step = _execution_result(messages)["steps"][0]
    column = next(c for c in step["columns"] if c != "sector_name")
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="The book totals {{claim.total_ead}}.",
              numeric_claims=[{
                  "claim_id": "total_ead", "unit": "SAR million",
                  "derivation": {"operation": "sum", "operands": [
                      {"artifact_id": step["artifact_id"],
                       "column_id": column,
                       "row_ids": list(step["row_ids"])}]}}]))])


def _run(drive, release_id):
    quarter = oracles.latest_quarter(release_id)
    return drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        _answer])


# ---- §27: the header travels with the answer ---------------------------

def test_a_published_answer_says_what_its_numbers_mean(drive, release_id):
    outcome, _, _ = _run(drive, release_id)
    assert outcome.state == st.COMPLETED, outcome.message

    header = outcome.response["release"]
    assert header["release_id"] == release_id
    assert header["release_fingerprint"] == rel.fingerprint(release_id)
    assert header["country"] == "Saudi Arabia"
    assert header["reporting_currency"] == "SAR"
    assert header["amount_scale"] == "million"
    assert header["not_client_data"] is True


def test_an_artifact_records_the_bytes_it_was_computed_from(drive,
                                                            store_db,
                                                            release_id):
    outcome, _, record = _run(drive, release_id)
    assert outcome.state == st.COMPLETED, outcome.message

    claim = outcome.response["numeric_claims"][0]
    artifact_id = claim["derivation"]["operands"][0]["artifact_id"]
    stored = store_db.get_artifact(artifact_id, tenant_id=record.tenant_id)
    assert stored["release_id"] == release_id
    assert stored["scope"]["release_fingerprint"] == rel.fingerprint(
        release_id)


# ---- §6: contamination is refused --------------------------------------

def _finalizer_for(runtime, store_db, release_id, tenant="demo-tenant"):
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.finalization import Finalizer

    return Finalizer(
        store=store_db, tenant_id=tenant, release_id=release_id,
        limits=STANDARD_LIMITS,
        header=rel.header(release_id=release_id, catalog=runtime.catalog,
                          release_summary=runtime.release_summary))


def test_an_artifact_from_another_release_is_refused(runtime, store_db,
                                                     release_id):
    foreign = store_db.put_artifact(
        run_id="r-foreign", tenant_id="demo-tenant", kind="result",
        release_id=OTHER, scope={"release_fingerprint": rel.fingerprint(OTHER)},
        columns=["sector_name", "ead"],
        rows=[{"sector_name": "Manufacturing", "ead": 1.0}])

    finalizer = _finalizer_for(runtime, store_db, release_id)
    problem = finalizer.release_problem(foreign)
    assert problem, "an artifact from another release passed as evidence"
    assert OTHER in problem and release_id in problem
    assert "not evidence for another" in problem


def test_an_artifact_from_another_build_of_the_same_id_is_refused(
        runtime, store_db, release_id):
    """The case an id cannot catch. Same name, different numbers."""
    stale = store_db.put_artifact(
        run_id="r-stale", tenant_id="demo-tenant", kind="result",
        release_id=release_id,
        scope={"release_fingerprint": "0" * 64},
        columns=["sector_name", "ead"],
        rows=[{"sector_name": "Manufacturing", "ead": 1.0}])

    finalizer = _finalizer_for(runtime, store_db, release_id)
    problem = finalizer.release_problem(stale)
    assert problem
    assert "different build" in problem


def test_an_artifact_from_this_build_is_accepted(runtime, store_db,
                                                 release_id):
    mine = store_db.put_artifact(
        run_id="r-mine", tenant_id="demo-tenant", kind="result",
        release_id=release_id,
        scope={"release_fingerprint": rel.fingerprint(release_id)},
        columns=["sector_name", "ead"],
        rows=[{"sector_name": "Manufacturing", "ead": 1.0}])

    finalizer = _finalizer_for(runtime, store_db, release_id)
    assert finalizer.release_problem(mine) == ""


def test_a_claim_on_a_foreign_artifact_fails_the_answer(drive, store_db,
                                                        release_id):
    """End to end, through the real validator."""
    foreign = store_db.put_artifact(
        run_id="r-foreign", tenant_id="demo-tenant", kind="result",
        release_id=OTHER,
        scope={"release_fingerprint": rel.fingerprint(OTHER)},
        columns=["sector_name", "ead"],
        rows=[{"sector_name": "Manufacturing", "ead": 1.0}])

    def _cite_the_foreigner(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="The book totals {{claim.total_ead}}.",
                  numeric_claims=[{
                      "claim_id": "total_ead", "unit": "SAR million",
                      "evidence": {"artifact_id": foreign,
                                   "row_key": "r0", "column_id": "ead"}}]))])

    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        _cite_the_foreigner, _cite_the_foreigner])
    assert outcome.state != st.COMPLETED
    assert outcome.error_code == st.ANSWER_VALIDATION, outcome.message


# ---- §31/§32: the dashboard and the threads it seeds -------------------

def test_the_attention_cache_is_keyed_by_the_releases_bytes():
    """A rebuilt release must not be served the old book's dashboard."""
    from backend.cockpit_v4 import attention

    key = attention.cache_key(SAUDI, "demo-tenant")
    assert key[0] == SAUDI and key[1] == "demo-tenant"
    assert key[2] == rel.fingerprint(SAUDI)
    assert attention.cache_key(SAUDI, "demo-tenant") != \
        attention.cache_key(OTHER, "demo-tenant")
    assert attention.cache_key(SAUDI, "demo-tenant") != \
        attention.cache_key(SAUDI, "other-bank")


def test_a_seeded_thread_answers_from_the_release_it_was_seeded_from(
        drive, store_db, release_id):
    """§32. A follow-up must never reach the release the card did not use."""
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.set_thread_context(
        thread_id, tenant_id="demo-tenant", kind="attention_item", body={
            "segment": "Manufacturing", "release_id": release_id,
            "reporting_quarter": "2026Q2", "comparison_quarter": "2026Q1",
            "metric": "covenant_breach_share",
            "headline": "Manufacturing: more exposure sits under a breach"})
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question=QUESTION, mode="standard", release_id=release_id,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")

    quarter = oracles.latest_quarter(release_id)
    outcome, _, _ = drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        _answer], record=record)

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["release"]["release_id"] == release_id
    assert outcome.response["release"]["release_fingerprint"] == \
        rel.fingerprint(release_id)
    assert store_db.get_run(record.run_id).release_id == release_id
