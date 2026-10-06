"""REAL DATABASE · MODEL MOCK · REPRODUCTION. The two Mac CALL_LIMIT runs.

What happened on the Mac
------------------------
Two ordinary Corporate questions were asked against a correct backend --
`v4-saudi-corporate-20q-v3`, quarterly, 20 periods, latest 2026Q2 -- and
both produced the same screen:

    Request accepted
    Understanding the request              ~80s
    (response cut off before the stage completed; asking again)
    one structure-regeneration attempt used
    CALL_LIMIT

No query ran. No answer was published. The questions were:

  1. "What is driving Stage 2 and ECL growth?"
  2. "What drove the change in Recognised ECL for Construction in 2026Q2?"
     -- asked in a thread seeded from the Construction ECL card.

These replays use the REAL shape of that failure: the first action turn ends
at its output allowance with nothing complete in it, which is what
`stop_reason: "max_tokens"` means and what `OutputTruncated` is. They are
not a happy-path stub with a retry bolted on.

Two different things are asserted, and both matter:

  * given ONE bad action turn, the run now recovers, executes and publishes
    -- because the retry is a turn that cannot answer in prose, carries no
    answer contract, and has time left to make;
  * given TWO bad action turns, the run still stops at CALL_LIMIT -- bounded
    is the point, and a run that retried forever would be a worse failure
    than the one being fixed.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.provider import OutputTruncated

from test_domain_execution import drive_domain, make_domain_run  # noqa: F401

STAGE2 = "What is driving Stage 2 and ECL growth?"
CONSTRUCTION = ("What drove the change in Recognised ECL for Construction "
                "in 2026Q2?")


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


# ---- the provider output that actually came back -----------------------

def truncated_action() -> OutputTruncated:
    """One action turn that reached its allowance with nothing complete.

    This is the Mac failure verbatim: the turn was served, it was billed,
    its stop reason was `max_tokens`, and half a tool call is not a tool
    call. Nothing from it runs and it does not enter history.
    """
    return OutputTruncated(
        "the response reached its 3,072-token output allowance",
        limit=config_mod.ANALYTICAL_STANDARD_LIMITS.action_output_tokens)


def prose_instead_of_an_action() -> ScriptedResult:
    """The other shape the live run produced: an essay, and no tool call."""
    return ScriptedResult(
        text="Stage 2 exposure has risen across several sectors this "
             "quarter, and the ECL movement behind it is concentrated in "
             "Construction and Real Estate. To establish this properly I "
             "would look at ...",
        stop_reason="end_turn")


def ecl_by_sector(quarter: str, call_id: str = "tu-ecl"):
    sql = ("SELECT sector, SUM(ecl_sar_mn) AS ecl_sar_mn, "
           "SUM(ead_sar_mn) AS ead_sar_mn "
           "FROM corp_facility_quarter "
           f"WHERE reporting_quarter = '{quarter}' "
           "GROUP BY sector ORDER BY ecl_sar_mn DESC")
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="ECL and EAD by sector",
                         rationale="direct read"),
        "objective": "ECL and EAD by sector",
        "subquestions": ["Where is ECL building?"],
        "scope": {"reporting_quarters": [quarter], "filters": {}},
        "metadata_receipt_ids": [],
        "fields_required": ["ecl_sar_mn", "ead_sar_mn"],
        "expected_output_grain": "one row per sector",
        "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                   "parameters": {}, "purpose": "ECL and EAD by sector",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def answer_from_result(messages):
    """A valid answer built from whatever the execution actually returned."""
    from test_orchestration_recovery import _execution_result

    body = _execution_result(messages)
    step = body["steps"][0]
    artifact = step["artifact_id"]
    # Bind to the row the question NAMES when the result holds one ("... in
    # Real Estate?"), as a faithful analyst would. Finalization refuses an
    # answer whose only claims sit on another entity's row.
    # The FIRST user message is the request; later user turns carry tool
    # results, which name every row. Only its request line counts: the rest
    # is context (an investigation packet names other segments too).
    first = next((str(m.get("content")) for m in messages
                  if m.get("role") == "user"), "")
    asked = first.split("unmodified):", 1)[-1].strip().split("\n\n", 1)[0].lower()
    index = next((i for i, row in enumerate(step["preview"])
                  if any(isinstance(v, str) and len(v) > 2 and v.lower() in asked
                         for v in row.values())), 0)
    cell = step["preview"][index]
    column = next(c for c in step["columns"]
                  if isinstance(cell[c], (int, float)))
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="Recognised ECL is concentrated where exposure is. "
                        "The largest sector position is {{claim.top}}.",
              coverage=[{"subquestion": "Where is ECL building?",
                         "status": "answered",
                         "evidence_refs": [
                             {"artifact_id": artifact, "row_key": f"r{index}",
                              "column_id": column}]}],
              numeric_claims=[{
                  "claim_id": "top",
                  "decimal_value": str(cell[column]),
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact, "row_key": f"r{index}",
                               "column_id": column}}],
              tables=[{"title": "ECL by sector", "artifact_id": artifact,
                       "columns": list(cell)}]))],
        output_tokens=400)


def latest_quarter() -> str:
    return resolver.scope_for(dom.CORPORATE).latest_period


def seeded_construction_run(store_db):
    """A thread opened from the real Construction ECL card."""
    att.clear_cache()
    scope = resolver.scope_for(dom.CORPORATE)
    session = cat.open_session(catalog=cat.build(domain_id=dom.CORPORATE))
    feed = att.compute(session=session, scope=scope)
    card = next((c for c in feed["ecl_highlights"] + feed[
        "segments_requiring_attention"]
        if "Construction" in str(c.get("headline") or "")), None)
    if card is None:
        pytest.skip("this release produced no Construction ECL card")

    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=scope.domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    store_db.set_thread_context(
        thread_id, tenant_id=lake.DEFAULT_TENANT, kind="attention_item",
        body=card)
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT,
        principal_id="u1", question=CONSTRUCTION, mode="standard",
        release_id=scope.release_id, domain_id=scope.domain_id,
        release_fingerprint=scope.release_fingerprint,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")
    return record, card


def catalog_calls(provider) -> int:
    """How many times this run went looking for metadata it was given."""
    seen = 0
    for sent in provider.sent:
        for message in sent["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("name") == "inspect_catalog"):
                        seen += 1
    return seen


# ---- §15. MAC-A: "What is driving Stage 2 and ECL growth?" -------------

def test_stage2_and_ecl_growth_publishes_after_a_truncated_action(
        drive_domain, store_db):
    """The exact Mac failure, run to publication.

    PASS means: the recovery succeeded, a query was prepared, validated and
    executed, an answer published, and no CALL_LIMIT or DEADLINE_EXPIRED.
    """
    quarter = latest_quarter()
    outcome, provider, record = drive_domain(dom.CORPORATE, STAGE2, [
        truncated_action(),
        ScriptedResult(tool_calls=[ecl_by_sector(quarter)]),
        answer_from_result])

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert outcome.response["executed"] is True
    assert outcome.response["narrative"]

    report = outcome.call_report
    # §14: one action, one recovery, one answer. Never more.
    assert report["generations"] == 3, [c["purpose"] for c in
                                        report["calls"]]
    actions = [c for c in report["calls"] if c["phase"] == "action"]
    assert len(actions) == 2, "no more than two action-generation attempts"
    assert catalog_calls(provider) == 0, (
        "the governed measures for Stage 2 and ECL are already in the "
        "opening packet; a catalogue lookup for them is a round trip for "
        "something the run was handed")


def test_a_prose_answer_to_an_action_turn_also_recovers(drive_domain,
                                                        store_db):
    """The other live shape: an essay where an action belonged."""
    quarter = latest_quarter()
    outcome, _, _ = drive_domain(dom.CORPORATE, STAGE2, [
        prose_instead_of_an_action(),
        ScriptedResult(tool_calls=[ecl_by_sector(quarter)]),
        answer_from_result])
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True


# ---- §16. MAC-B: the seeded Construction investigation -----------------

def test_the_seeded_construction_investigation_publishes(drive_domain,
                                                         store_db):
    """PASS means the packet was loaded, no broad discovery, SQL ran."""
    record, card = seeded_construction_run(store_db)
    quarter = latest_quarter()
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, CONSTRUCTION,
        [truncated_action(),
         ScriptedResult(tool_calls=[ecl_by_sector(quarter)]),
         answer_from_result],
        record=record)

    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.error_code not in (st.CALL_LIMIT, st.DEADLINE_EXPIRED)
    assert outcome.response["executed"] is True

    # The seed reached the analyst, resolved, on the FIRST action.
    opening = provider.first_input_text()
    assert "ANALYSIS PACKET" in opening
    assert "Construction" in opening
    assert card["reporting_period"] in opening
    assert catalog_calls(provider) == 0, (
        "a seeded thread already holds the relation, the segment value and "
        "both periods; the live run read the catalogue twice and died")


def test_a_seeded_thread_is_told_its_metadata_is_already_sufficient(
        store_db):
    """§7. Deterministic, server-side, and not a choice of method."""
    from backend.cockpit_v4 import context as ctx_mod

    record, card = seeded_construction_run(store_db)
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    packet = ctx_mod.build(
        question=CONSTRUCTION, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), investigation=card,
        session=book.session, analytical=True)

    readiness = json.loads(packet.system_blocks[-1]["text"])[
        "analysis_readiness"]
    assert readiness["sufficient"] is True
    assert readiness["normal_first_action"] == "execute_analysis"
    assert readiness["seed"]["packet_present"] is True
    assert not readiness["seed"]["missing"]
    # It says the metadata is there. It does not say what to do with it.
    blob = json.dumps(readiness).lower()
    for method in ("group by", "sum(", "compare", "decomposition"):
        assert method not in blob, (
            f"the readiness check is prescribing a method ({method!r}); "
            f"which analysis to run is the analyst's")


def test_an_unrecognised_question_is_not_told_to_skip_discovery(store_db):
    """The check has to be capable of saying no, or it says nothing."""
    from backend.cockpit_v4 import semantics as sem

    book = arun.for_domain(dom.CORPORATE)
    readiness = sem.readiness(
        book.catalog, "What is the gizmo ratio of the frobnicator?")
    assert readiness["sufficient"] is False
    assert readiness["normal_first_action"] == "inspect_catalog"


# ---- §14. bounded is the point ----------------------------------------

def test_bad_actions_stop_once_there_is_nothing_left_to_change(
        drive_domain, store_db):
    """No infinite retry, and no retry that repeats the same question.

    A re-ask is granted while the REQUEST can still change: the surface
    narrows to the state's one legal tool, and a truncation raises the
    output allowance once because a truncation is evidence that the
    allowance was the binding constraint. When neither can change again,
    the run stops -- and names what it actually spent.
    """
    outcome, provider, _ = drive_domain(dom.CORPORATE, STAGE2, [
        truncated_action(), truncated_action(), truncated_action()])

    assert outcome.state == st.FAILED
    # NOT a call limit. See `states.ACTION_FORMAT_EXHAUSTED`: generations,
    # provider attempts, time and money were all left. What ran out was the
    # re-ask for a complete action.
    assert outcome.error_code == st.ACTION_FORMAT_EXHAUSTED
    actions = [c for c in outcome.call_report["calls"]
               if c["phase"] == "action"]
    assert len(actions) == 3, (
        f"{len(actions)} action attempts: a run that keeps asking is a "
        f"worse failure than the one being fixed")
    # Each attempt asked something the last one did not.
    allowances = [c["output_allowance"]["granted"] for c in actions]
    assert allowances[1] > allowances[0], (
        "the re-ask after a truncation must change the one thing the "
        "evidence points at")
    assert allowances[2] == allowances[1], "and then stop changing it"


def test_the_reader_is_told_the_action_was_cut_off_not_that_it_failed(
        drive_domain, store_db):
    """§18. The panel says what happened, in the stage it happened in."""
    quarter = latest_quarter()
    outcome, _, record = drive_domain(dom.CORPORATE, STAGE2, [
        truncated_action(),
        ScriptedResult(tool_calls=[ecl_by_sector(quarter)]),
        answer_from_result])
    assert outcome.state == st.COMPLETED, outcome.message

    events = store_db.events_since(record.run_id, 0, 1000)
    retries = [e for e in events if e.event_type == ev.RETRY_REQUESTED]
    assert retries, "the truncated action left no trace"
    first = retries[0]
    assert first.stage == "understanding"
    assert "cut off" in (first.public_message or "").lower()
    # And the run then went on through the real stages.
    stages = [e.stage for e in events]
    for stage in ("preparing", "executing", "publishing"):
        assert stage in stages, f"the panel never showed {stage}"
