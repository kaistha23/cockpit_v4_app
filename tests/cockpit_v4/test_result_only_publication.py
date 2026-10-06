"""
The result is published even when the write-up is not.

MODEL MOCK · REAL DATABASE/RUNNER · REAL API. No paid provider call.

Thread `th-48fdeffe125f489592f627e307852e31`, on the Mac: four steps were
validated and bound, three stored their results, the answer turn was cut off
at its output allowance twice, and the run stopped as
ANSWER_FORMAT_EXHAUSTED. What the reader saw was a red box reading

    This request stopped
    Reason: ANSWER_FORMAT_EXHAUSTED

and nothing else. The rows the run had computed -- and been paid for -- were
never shown, could not be exported, and were gone from the transcript after a
reload.

The rows were never lost. They were in the artifact store the whole time.
What was missing was an ADDRESS: the only channel a result reached a reader
through was the written answer object, and failing to produce that object is
exactly what had happened.

So the object is now built by the server instead, from the stored artifacts,
with a server-written caveat and NO numeric claims -- no number reaches the
reader that the analyst supplied, because the analyst supplied nothing. The
run still fails, and still fails for the same reason. What changes is that
the reader keeps their numbers.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call  # noqa: F401
from test_orchestration_recovery import _ead_call

from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import OutputTruncated

P = "/api/v1/cockpit-v4"


def _cut_off() -> OutputTruncated:
    """What the provider raises when `stop_reason` is `max_tokens`."""
    return OutputTruncated("cut off", limit=4096)


@pytest.fixture
def stopped(drive, store_db, release_id):
    """A run that executed its query and could never write the answer.

    Two truncated answer turns exhaust the answer-format allowance. This is
    the live sequence, minus the action-phase truncation that is irrelevant
    to what happens afterwards.
    """
    quarter = oracles.latest_quarter(release_id)
    outcome, provider, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _cut_off(), _cut_off(), _cut_off()])
    assert outcome.error_code == st.ANSWER_FORMAT_EXHAUSTED, outcome.message
    return outcome, record, store_db


# ---- the run itself ----------------------------------------------------

def test_a_run_that_computed_rows_does_not_stop_with_nothing(stopped):
    """The whole defect, in one assertion: there is a response."""
    outcome, _record, _store = stopped
    assert outcome.response, (
        "the analysis executed and its rows were stored; a terminal stop "
        "that returns no response leaves the reader with a red box")
    assert outcome.response["executed"] is True
    assert outcome.response["result_only"] is True
    assert outcome.response["disposition"] == "partial_answer"


def test_the_stop_is_partial_because_verified_evidence_exists(stopped):
    """FAILED reads as "the query failed". The query did not fail."""
    outcome, _record, _store = stopped
    assert outcome.state == st.PARTIAL, outcome.message
    # The code is unchanged -- the run really did stop for this reason.
    assert outcome.error_code == st.ANSWER_FORMAT_EXHAUSTED
    # A partial stop is not an operator incident; it must not mint a
    # reference that tells the reader to go and find somebody.
    assert outcome.error_id == ""


def test_the_published_rows_are_the_artifacts_own_rows(stopped):
    """Every figure was computed and stored. None was written."""
    outcome, _record, store = stopped
    tables = outcome.response["tables"]
    assert len(tables) == 1, tables
    table = tables[0]
    assert table["rows"], "a table with no rows publishes nothing"
    assert table["rendered_by"] == "creditprobe"

    stored = store.get_artifact(table["artifact_id"],
                                tenant_id=lake.DEFAULT_TENANT)
    assert len(table["rows"]) == len(stored["rows"])
    # And the canonical values are the stored values, cell for cell.
    for published, kept in zip(table["rows"], stored["rows"]):
        for column, value in published["canonical"].items():
            assert kept[column] == value, (column, kept[column], value)


def test_no_number_reaches_the_reader_that_the_analyst_supplied(stopped):
    """The analyst wrote nothing, so there is nothing of theirs to check."""
    outcome, _record, _store = stopped
    assert outcome.response["numeric_claims"] == []
    assert outcome.response["evidence_bound"] is False
    # And the narrative is the server's, not a fragment of a cut-off answer.
    assert "{{claim." not in outcome.response["narrative"]


def test_the_caveat_blames_the_explanation_and_not_the_analysis(stopped):
    """An honest caveat. The analysis ran; the write-up did not.

    Saying "the analysis failed" over rows that were computed correctly
    would be the same defect wearing the opposite sign.
    """
    outcome, _record, _store = stopped
    narrative = outcome.response["narrative"].lower()
    assert "the analysis ran" in narrative
    assert "could not write" in narrative
    limitations = " ".join(outcome.response["limitations"]).lower()
    assert "written answer" in limitations
    assert outcome.response["result_only_reason"]


def test_the_table_is_titled_by_the_step_that_produced_it(stopped):
    """A table headed "Result" is a table a reader has to decode."""
    outcome, _record, _store = stopped
    assert outcome.response["tables"][0]["title"] == "EAD by sector"


# ---- the exchange survives a reload ------------------------------------

def test_the_exchange_is_written_into_the_transcript(stopped):
    """Without a turn the whole exchange vanishes on refresh."""
    outcome, record, store = stopped
    turns = store.thread_turns(record.thread_id)
    assert len(turns) == 1, turns
    answer = turns[0]["answer"]
    if isinstance(answer, str):
        answer = json.loads(answer)
    assert answer["result_only"] is True
    assert answer["tables"][0]["rows"]


def test_the_run_record_serves_the_response_back(stopped):
    """`/runs/{id}` used to serve `final_response: null`."""
    _outcome, record, store = stopped
    stored = store.get_run(record.run_id)
    assert stored.final_response, "the response must survive the process"
    assert stored.final_response["result_only"] is True


# ---- and it can be taken out of the building ---------------------------

@pytest.fixture
def api(stopped, runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cockpit_v4 import routes

    _outcome, _record, store = stopped
    app = FastAPI()
    routes.install(store=store, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def test_the_preserved_result_is_exportable(api, stopped):
    """`_export_context` 409'd on an empty `final_response`."""
    outcome, record, _store = stopped
    analysis = api.get(f"{P}/runs/{record.run_id}/export")
    assert analysis.status_code == 200, analysis.text
    assert "## Lineage" in analysis.text

    artifact_id = outcome.response["tables"][0]["artifact_id"]
    table = api.get(
        f"{P}/runs/{record.run_id}/artifacts/{artifact_id}/export")
    assert table.status_code == 200, table.text
    assert table.headers["content-type"].startswith("text/csv")


def test_the_run_route_carries_the_response_to_the_browser(api, stopped):
    _outcome, record, _store = stopped
    body = api.get(f"{P}/runs/{record.run_id}").json()
    assert body["state"] == st.PARTIAL
    assert body["error_code"] == st.ANSWER_FORMAT_EXHAUSTED
    assert body["final_response"] is not None
    assert body["final_response"]["result_only"] is True
    assert body["final_response"]["tables"][0]["rows"]


# ---- the bound still holds ---------------------------------------------

def test_a_run_that_executed_nothing_still_stops_with_nothing(
        drive, release_id):
    """A result-only response must never be invented out of an empty run.

    Publishing a caveat with no rows under it would be a red box with extra
    steps, and a PARTIAL state over no evidence at all would be a lie.
    """
    outcome, _provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [_cut_off(), _cut_off(), _cut_off(), _cut_off()])
    assert outcome.state == st.FAILED
    assert outcome.response is None


def test_a_stop_never_settles_as_failed_while_carrying_a_result(
        drive, release_id):
    """One set of codes, read twice.

    The state promotion and the result-only channel are driven by the SAME
    map, so a run can never settle as FAILED while holding a published
    result (a red box over rows, which is this whole defect) nor as PARTIAL
    with nothing to show for it (a partial answer that is not an answer).
    """
    from backend.cockpit_v4 import orchestration as orch

    assert orch._RESULT_ONLY_REASON, "the map is the promotion list"
    for code, reason in orch._RESULT_ONLY_REASON.items():
        assert code in st.ERROR_CODES, code
        assert reason.strip().endswith("."), (code, reason)


def test_the_supervisor_and_the_orchestrator_read_one_table(release_id):
    """Two components publish this way, and must agree on when and why.

    The orchestrator publishes when its own budget check stops the run;
    the SUPERVISOR publishes when it settles a run whose worker is blocked
    on a socket. Two tables of the same sentences are two tables that can
    disagree, and the reader would then be told different things about the
    same failure depending on which component happened to win a race.
    """
    from backend.cockpit_v4 import finalization as fin
    from backend.cockpit_v4 import orchestration as orch
    from backend.cockpit_v4 import supervisor as sup

    assert orch._RESULT_ONLY_REASON is fin.RESULT_ONLY_REASON
    assert sup._RESULT_REASON is fin.RESULT_ONLY_REASON


def test_a_rejected_answer_publishes_its_rows_and_none_of_its_prose(
        drive, release_id):
    """Recorded reversal.

    This test previously asserted the opposite -- `FAILED`, `response is
    None` -- and was named "the one neighbouring failure mode this must
    not have moved". That was a scope boundary for the round that built
    this channel, not a finding about what a reader should see.

    It does not survive contact with one. The query ran, the rows are
    stored and correct, and the only thing that failed is the write-up.
    The stated ground for excluding it -- that the analyst DID write an
    answer -- argues for discarding that answer, which this channel does:
    it publishes no narrative and no claims, only server-rendered tables
    under a server-written caveat. The reader was being shown a red box
    over their own correct result.
    """
    from test_orchestration_recovery import _bad_answer

    quarter = oracles.latest_quarter(release_id)
    outcome, _provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _bad_answer, _bad_answer, _bad_answer])
    assert outcome.error_code == st.ANSWER_VALIDATION
    assert outcome.state == st.PARTIAL

    published = outcome.response or {}
    assert published.get("result_only") is True
    assert published["tables"], "the rows the query returned"
    # What failed, failed closed.
    assert published["numeric_claims"] == []
    assert published["evidence_bound"] is False
    assert "could not be reconciled" in " ".join(published["limitations"])


def test_a_successful_answer_is_not_overwritten_by_a_later_stop(
        drive, store_db, release_id):
    """A published answer wins. The result-only channel is a fallback."""
    from test_orchestration_recovery import _good_answer

    quarter = oracles.latest_quarter(release_id)
    outcome, _provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]), _good_answer])
    assert outcome.state == st.COMPLETED, outcome.message
    assert not outcome.response.get("result_only")
    assert outcome.response["numeric_claims"], (
        "a real answer keeps its claims")
