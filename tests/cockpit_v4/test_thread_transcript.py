"""MODEL MOCK · REAL DATABASE/RUNNER · REAL HTTP · UNIT.

A conversation a reader can scroll, reopen and rename.

The product problem this round exists for: V4 could answer, but it did not
feel like a conversation. One question, one answer, and the reader was still
looking at a dashboard. A transcript is the difference, and a transcript that
renders only its last few exchanges is not one -- it is a window, and a reader
scrolling up to find what they asked half an hour ago finds nothing.

Two sizes on purpose. `recent_turns` is what the ANALYST reads and stays
small, because a twenty-turn conversation must not be sent to a model in
full. `thread_turns` is what a READER reads and is complete.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st

P = "/api/v1/cockpit-v4"


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": r.headers.get("X-Test-User", "u1"),
                       "tenant": r.headers.get("X-Test-Tenant",
                                               "demo-tenant")},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def _thread_with(store_db, questions, tenant="demo-tenant"):
    thread_id = store_db.create_thread(tenant_id=tenant, principal_id="u1")
    for index, question in enumerate(questions):
        store_db.append_turn(
            thread_id=thread_id, run_id=f"run-{index}", question=question,
            answer={"narrative": f"Answer {index}", "disposition": "answer",
                    "tables": [], "charts": [],
                    "suggested_questions": [{"question": "And next?"}]})
    return thread_id


# ---- the transcript ----------------------------------------------------

def test_a_thread_renders_every_turn_not_a_window(client, store_db):
    questions = [f"Question {n}?" for n in range(12)]
    thread_id = _thread_with(store_db, questions)

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["turn_count"] == 12
    assert [t["question"] for t in body["turns"]] == questions, (
        "the transcript must be complete and in order")


def test_a_turn_carries_what_it_takes_to_render_it(client, store_db):
    thread_id = _thread_with(store_db, ["Total EAD by sector?"])
    turn = client.get(f"{P}/threads/{thread_id}").json()["turns"][0]
    for key in ("turn_id", "run_id", "ordinal", "question", "answer",
                "created_at"):
        assert key in turn, key
    assert turn["run_id"] == "run-0", (
        "without the run id there is no trace to open from a past turn")
    assert turn["answer"]["narrative"] == "Answer 0"


def test_the_analyst_still_reads_only_a_few_turns(store_db):
    """The two sizes are different on purpose."""
    thread_id = _thread_with(store_db, [f"Q{n}?" for n in range(12)])
    assert len(store_db.recent_turns(thread_id, 3)) == 3
    assert len(store_db.thread_turns(thread_id)) == 12


def test_the_transcript_says_what_its_numbers_mean(client, store_db):
    """THE BOOK THIS THREAD IS HELD IN, not the process's configured one.

    This assertion used to read the configured release out of the fixture,
    and passed because the header was built from the same place. It was
    wrong twice over: a thread's answers are computed against the release
    its DOMAIN publishes, and on a deployment whose configured release is
    not published at all the transcript named a release that does not exist
    there. A live trace reported `v4-saudi-20q-v1` for a run accepted
    against `v4-saudi-corporate-20q-v3`.
    """
    from backend.cockpit_v4 import domains as dom_mod

    thread_id = _thread_with(store_db, ["Total EAD?"])
    body = client.get(f"{P}/threads/{thread_id}").json()
    expected = dom_mod.DEFAULT_RELEASES[body["domain_id"]]
    assert body["release"]["release_id"] == expected
    assert body["release"]["reporting_currency"] == "SAR"
    assert len(body["release"]["release_fingerprint"]) == 64


@pytest.mark.parametrize("domain_id", ["corporate", "retail"])
def test_the_transcript_never_names_two_different_books(client, store_db,
                                                        domain_id):
    """One payload, one answer to "which release?".

    `release_id` came from the thread's PIN and `release` was built from the
    process's configuration, so a single response could state two different
    releases and a reader had no way to tell which one the numbers came
    from. Pinned explicitly, and in both books, because the configured
    release can coincide with one of them by accident.
    """
    from backend.cockpit_v4 import domains as dom_mod

    pinned = dom_mod.DEFAULT_RELEASES[domain_id]
    thread_id = store_db.create_thread(
        tenant_id="demo-tenant", principal_id="u1",
        domain_id=domain_id, release_id=pinned)
    store_db.append_turn(thread_id=thread_id, run_id="run-0",
                         question="Total EAD?",
                         answer={"narrative": "A", "disposition": "answer",
                                 "tables": [], "charts": [],
                                 "suggested_questions": []})

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["release_id"] == pinned
    assert body["release"]["release_id"] == pinned, (
        f"the transcript names {body['release_id']} in one field and "
        f"{body['release']['release_id']} in another")


def test_a_trace_names_the_release_its_run_was_accepted_in(client, store_db,
                                                           runtime):
    """And not whichever release this process happens to be pointed at.

    The live case exactly: a run accepted against
    `v4-saudi-corporate-20q-v3` whose trace reported `v4-saudi-20q-v1`, the
    configured release -- which on that machine was not published at all.
    """
    from backend.cockpit_v4 import domains as dom_mod

    accepted = dom_mod.DEFAULT_RELEASES[dom_mod.CORPORATE]
    assert accepted != str(runtime.cfg.release_id), (
        "this test needs the configured and accepted releases to differ")

    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question="Total EAD?", mode="standard", release_id=accepted,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")

    body = client.get(f"{P}/runs/{record.run_id}/trace").json()
    assert body["release"]["release_id"] == accepted
    assert body["release"]["release_id"] != str(runtime.cfg.release_id)


def test_a_trace_of_an_unopenable_book_states_the_id_and_claims_nothing(
        client, make_run, runtime):
    """A release that belongs to neither book opens nothing.

    The honest answer is the id the run was accepted against and no
    currency, scale or fingerprint -- those would be read from a book that
    was never opened. Not the configured release, and not silence either:
    a reader asking "which release?" of a run that HAS one deserves it.
    """
    record = make_run("Total EAD?")
    body = client.get(f"{P}/runs/{record.run_id}/trace").json()
    assert body["release"] == {"release_id": str(runtime.cfg.release_id)}


# ---- the title ---------------------------------------------------------

def test_the_first_question_names_the_thread(client, store_db):
    """No model call. The thread is about what was asked in it."""
    thread_id = _thread_with(store_db, ["What is total EAD by sector?",
                                        "And a year ago?"])
    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["title"] == "What is total EAD by sector?"


def test_a_very_long_question_does_not_become_a_very_long_title(store_db):
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.append_turn(thread_id=thread_id, run_id="r", answer={},
                         question="word " * 200)
    assert len(store_db.thread_title(thread_id)) <= 200


def test_renaming_a_thread_keeps_the_new_name(client, store_db):
    thread_id = _thread_with(store_db, ["What is total EAD by sector?"])
    response = client.post(f"{P}/threads/{thread_id}/title",
                           json={"title": "Sector exposure review"})
    assert response.status_code == 200
    assert response.json()["title"] == "Sector exposure review"
    assert client.get(f"{P}/threads/{thread_id}").json()["title"] == \
        "Sector exposure review"


def test_a_rename_does_not_get_overwritten_by_the_next_turn(client,
                                                            store_db):
    thread_id = _thread_with(store_db, ["First question?"])
    client.post(f"{P}/threads/{thread_id}/title", json={"title": "My review"})
    store_db.append_turn(thread_id=thread_id, run_id="r2", answer={},
                         question="Second question?")
    assert store_db.thread_title(thread_id) == "My review"


# ---- §53: a URL is not an authorization --------------------------------

def test_another_tenant_cannot_read_a_thread(client, store_db):
    thread_id = _thread_with(store_db, ["Total EAD?"])
    response = client.get(f"{P}/threads/{thread_id}",
                          headers={"X-Test-Tenant": "other-bank"})
    assert response.status_code == 404, (
        "and 404 rather than 403: it must not confirm the thread exists")


def test_another_tenant_cannot_rename_a_thread(client, store_db):
    thread_id = _thread_with(store_db, ["Total EAD?"])
    response = client.post(f"{P}/threads/{thread_id}/title",
                           json={"title": "Mine now"},
                           headers={"X-Test-Tenant": "other-bank"})
    assert response.status_code == 404
    assert store_db.thread_title(thread_id) == "Total EAD?"


# ---- §36: what "continue where you left off" lists ---------------------

def test_a_reopenable_thread_is_listed_by_what_was_asked_in_it(store_db):
    thread_id = _thread_with(store_db, ["Which sectors carry the most ECL?",
                                        "And the borrowers behind them?"])
    rows = store_db.recent_threads(tenant_id="demo-tenant", principal_id="u1")
    row = next(r for r in rows if r["thread_id"] == thread_id)
    assert row["title"] == "Which sectors carry the most ECL?"
    assert row["turns"] == 2
    assert row["last_activity_at"]


def test_a_renamed_thread_is_listed_under_its_new_name(store_db):
    thread_id = _thread_with(store_db, ["Which sectors carry the most ECL?"])
    store_db.set_thread_title(thread_id, tenant_id="demo-tenant",
                              title="ECL concentration")
    rows = store_db.recent_threads(tenant_id="demo-tenant", principal_id="u1")
    row = next(r for r in rows if r["thread_id"] == thread_id)
    assert row["title"] == "ECL concentration"


# ---- the schema can be added to without losing a database --------------

def test_a_database_written_before_titles_existed_still_opens(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` does not alter an existing table."""
    import sqlite3

    from backend.cockpit_v4.run_store import RunStore

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE threads (thread_id TEXT PRIMARY KEY, "
                 "tenant_id TEXT NOT NULL, principal_id TEXT NOT NULL, "
                 "created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO threads VALUES ('th-old', 'demo-tenant', "
                 "'u1', '2026-09-01T00:00:00Z')")
    conn.commit()
    conn.close()

    store = RunStore(path)
    assert store.thread_title("th-old") == ""
    assert store.set_thread_title("th-old", tenant_id="demo-tenant",
                                  title="Recovered")
    assert store.thread_title("th-old") == "Recovered"


# ---- §23: every answer offers its next questions ----------------------

def test_an_answer_carries_its_own_suggested_follow_ups(client, store_db):
    """They ride in the final response. §50: no extra generation for them."""
    thread_id = _thread_with(store_db, ["Total EAD by sector?"])
    turn = client.get(f"{P}/threads/{thread_id}").json()["turns"][0]
    assert turn["answer"]["suggested_questions"], (
        "a reader with no next question has to invent one")


# ---- §19: a rendered visual is bound to its release -------------------

def test_a_rendered_table_travels_with_the_release_that_made_it(
        drive, store_db, release_id):
    """§19: a visualization is an artifact of ONE release, not a picture."""
    import json as _json

    import oracles
    from conftest import ScriptedResult, final, intent, tool_call
    from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
    from test_orchestration_recovery import _execution_result
    from test_vertical_slice import _execute_call

    from backend.cockpit_v4 import release as rel
    from backend.cockpit_v4 import states as st

    quarter = oracles.latest_quarter(release_id)

    def _answer(messages):
        step = _execution_result(messages)["steps"][0]
        column = next(c for c in step["columns"] if c != "sector_name")
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="The book totals {{claim.total}}.",
                  numeric_claims=[{
                      "claim_id": "total", "unit": "SAR million",
                      "derivation": {"operation": "sum", "operands": [
                          {"artifact_id": step["artifact_id"],
                           "column_id": column,
                           "row_ids": list(step["row_ids"])}]}}],
                  tables=[{"title": "EAD by sector",
                           "artifact_id": step["artifact_id"],
                           "columns": ["sector_name", column]}],
                  charts=[{"kind": "bar", "title": "EAD by sector",
                           "artifact_id": step["artifact_id"],
                           "x_column": "sector_name",
                           "y_columns": [column],
                           "unit": "SAR million"}]))])

    outcome, _, record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]), _answer])
    assert outcome.state == st.COMPLETED, outcome.message

    body = client_free_body = outcome.response
    table = body["tables"][0]
    chart = body["charts"][0]

    # The visual names the artifact it was rendered from, and the answer
    # carries the release header, so a chart read back months later says
    # which book and which bytes produced it.
    assert table["artifact_id"] and chart["artifact_id"]
    assert table["rendered_by"] == "creditprobe"
    assert chart["rendered_by"] == "creditprobe"
    assert body["release"]["release_id"] == release_id
    assert body["release"]["release_fingerprint"] == rel.fingerprint(
        release_id)
    assert body["release"]["reporting_currency"] == "SAR"

    # And the figures a reader sees are the server's, at the server's
    # precision -- the frontend is handed both forms and picks one.
    row = table["rows"][0]
    column = next(c for c in table["columns"] if c != "sector_name")
    assert str(row["display"][column]).startswith("SAR ")
    assert row["canonical"][column] != row["display"][column]


def test_a_seeded_investigation_thread_is_named_after_its_case(client):
    """A thread that is about something says so before anybody types.

    `Investigate Further` opens a thread already carrying a case. Listing it
    as "New conversation" beside four others named the same thing makes the
    list useless, and the name is not a thing that needs inventing: the
    attention item's headline is already written and already on the card the
    reader just clicked.
    """
    feed = client.get(f"{P}/attention").json()
    item = feed["segments_requiring_attention"][0]

    opened = client.post(
        f"{P}/attention/{item['item_id']}/investigate")
    assert opened.status_code == 201, opened.text
    thread_id = opened.json()["thread_id"]

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["title"] == item["headline"][:120]
    assert body["turns"] == []


def test_a_thread_is_named_as_soon_as_it_has_a_question(client):
    """§23. Not when the answer lands -- when the question is asked.

    The title used to be written by `append_turn`, which runs when a turn
    SETTLES. So a reader watched "New conversation" for the whole time the
    analysis was working, and a thread whose run failed kept that name for
    good. The title is the question; nothing has to finish for it to be true.
    """
    started = client.post(f"{P}/runs", json={
        "question": "What is driving Stage 2 and ECL growth?",
        "mode": "standard"})
    assert started.status_code == 202, started.text
    thread_id = started.json()["thread_id"]

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["title"] == "What is driving Stage 2 and ECL growth?"
    # And it is there before any turn has been recorded.
    assert body["turns"] == []


def test_a_second_question_does_not_rename_the_thread(client):
    """The conversation is named after what opened it, not its latest turn."""
    first = client.post(f"{P}/runs", json={
        "question": "ECL decomposition", "mode": "standard"})
    thread_id = first.json()["thread_id"]
    client.post(f"{P}/runs", json={
        "question": "and by sector?", "mode": "standard",
        "thread_id": thread_id})
    assert client.get(f"{P}/threads/{thread_id}").json()["title"] == (
        "ECL decomposition")


def test_the_turn_is_written_before_the_run_is_marked_terminal(
        drive, store_db, release_id):
    """§53. A terminal run whose thread is still empty is a race, not a flake.

    The transcript row used to be written AFTER `update_state(terminal=True)`.
    The ordering was observable: a reader who clicked back the instant the
    answer appeared reached the landing page before the turn existed, and
    "Continue where you left off" -- which lists threads that HAVE a turn --
    did not list the conversation they had just held. It appeared a moment
    later, which is worse than never, because nobody is looking by then.

    This watches the ordering itself rather than timing it, so it cannot pass
    by being lucky.
    """
    from conftest import ScriptedResult, final, intent, tool_call

    seen: dict = {}
    original = type(store_db).update_state

    def watched(self, run_id, **kwargs):
        if kwargs.get("terminal") and kwargs.get("final_response") is not None:
            record = self.get_run(run_id)
            seen[run_id] = [t["run_id"]
                            for t in self.thread_turns(record.thread_id)]
        return original(self, run_id, **kwargs)

    type(store_db).update_state = watched
    try:
        outcome, _, record = drive("Who are you?", [
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                      narrative="CreditProbe Cockpit analyses a credit "
                                "book."))])])
    finally:
        type(store_db).update_state = original

    assert outcome.state == st.COMPLETED, outcome.message
    assert record.run_id in seen, "the run never settled with a response"
    assert record.run_id in seen[record.run_id], (
        "the run was marked terminal while its thread was still empty")


def test_one_run_is_one_turn_however_often_it_settles(drive, store_db,
                                                      release_id):
    """Writing the turn early is only safe if writing it twice cannot."""
    from conftest import ScriptedResult, final, intent, tool_call

    outcome, _, record = drive("Who are you?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                  narrative="CreditProbe Cockpit analyses a credit book."))])])
    assert outcome.state == st.COMPLETED, outcome.message

    settled = store_db.get_run(record.run_id)
    first = store_db.append_turn(
        thread_id=settled.thread_id, run_id=record.run_id,
        question=settled.question, answer=settled.final_response or {})
    second = store_db.append_turn(
        thread_id=settled.thread_id, run_id=record.run_id,
        question=settled.question, answer=settled.final_response or {})
    assert first == second, "a second settle wrote a second copy of the turn"
    assert len(store_db.thread_turns(settled.thread_id)) == 1
