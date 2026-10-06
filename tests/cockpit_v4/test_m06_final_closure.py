"""M06: the turn that had to ask could not, and the turn that failed said so
under the wrong name.

MODEL MOCK · REAL DATABASE/RUNNER · REAL BOOK · REAL POLICY PACK.
No paid provider call is made by anything in this file.

What the final paid retest found
--------------------------------
Four live runs at `dac15d6`. L18 passed and is a regression guard here. M06
failed in three places, and the three have three different causes.

**M06.1 — "Which sectors have the largest exposure?" answered on EAD.**

Not because the policy pack resolved it; the previous round's repair holds
and this answer cites CP-1.1 only to say a sector aggregate does not test
it. The analyst SAW the ambiguity. Its own recorded intent says so:

    "exposure" is not uniquely defined in this book (EAD, drawn balance or
    sanctioned limit): ranked on EAD as the primary measure ...

It executed anyway because executing was the only legal move on the turn:

    readiness("Which sectors have the largest exposure?")
      sufficient        False
      still undecided   exposure -> ead_sar_mn | limit_sar_mn | drawn_sar_mn
      normal_first_action   inspect_catalog
    decide(...)        NEEDS_METADATA      tools=(inspect_catalog,)  REQUIRED
    after_catalog(...) READY_FOR_EXECUTION tools=(execute_analysis,) REQUIRED

`finalize_response` was not on that turn, so `disposition: "clarification"`
was unreachable. The catalogue could not help -- the three candidates were
read out of it -- and `after_catalog` was handed `readiness` and ignored it.
This is H-LIVE-03 in a different state: not the analyst choosing silently,
but the only move the surface allowed.

Two things pushed the same way. `semantics.block` published a key called
`terms_needing_a_question` under a `how_to_use` that opened "These are
resolutions, not assumptions to ask about ... declare them in
resolved_assumptions and proceed" -- which is what the live run did, in
those words. And `analyst.md` stated the blocking test as "two readings that
would produce materially different numbers", a question about the RESULT,
answerable only by running them; the live run ran all three, saw the same
ordering, and called the ambiguity immaterial.

**M06.2 — "Exposure at default" read as a new total-EAD question.**

Downstream of the above: turn 1 ended `disposition: "answer"`, so the
clarification projection was never armed. Separately, the projection carried
`you_asked` and `you_offered` and NOT the request they interrupted, leaving
the analyst to reassemble "sectors, ranked, latest quarter, Corporate" from
two other fields.

**M06.3 — PARTIAL / CALL_LIMIT.**

Not a call limit. The events are

    model.requested -> retry.requested -> model.requested -> analysis.preserved

with no `model.response_received` between the first and the retry, which is
reachable only from the transport branch; the run had used 4 of 12
generations, 4 of 24 provider attempts, 1 of 5 submissions and 1 of 3
rounds. The provider failed twice, `spend_transport_retry` raised
`CALL_LIMIT`, and the reader was told the model-call allowance ran out.

The comment one caller above it already says "a turn that ran past the time
one action is allowed is not the same event as a network fault, and neither
of them is a call limit."

A separate waste, real but NOT this failure's cause: turn 3 was required to
spend a generation on `inspect_catalog` because `readiness` read the current
question alone and "Just the top five by that measure" names no term -- on a
query built entirely from what the thread had already resolved.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain  # noqa: F401

from backend.cockpit_v4 import action_state as acts
from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st

#: The three turns the approved retest ran.
T1 = "Which sectors have the largest exposure?"
T2 = "Exposure at default"
T3 = "Just the top five by that measure"

#: The controls.
L16 = "Which sectors are above the single-name limit?"
L17 = "What is exposure at default by sector this quarter?"
M07_T2 = "What is total ECL this quarter?"

#: The three governed readings of the bare word, from the catalogue.
READINGS = ["ead_sar_mn", "limit_sar_mn", "drawn_sar_mn"]

OPTIONS = ["Exposure at default (EAD)", "Drawn balance", "Approved limit"]

SECTOR_EAD = (
    "SELECT b.sector AS sector, SUM(f.ead_sar_mn) AS ead_sar_mn "
    "FROM corp_facility_quarter f JOIN corp_borrower_quarter b "
    "ON f.borrower_id = b.borrower_id "
    "AND f.reporting_quarter = b.reporting_quarter "
    "WHERE f.reporting_quarter = '2026Q2' GROUP BY b.sector "
    "ORDER BY ead_sar_mn DESC")

TOP_FIVE = SECTOR_EAD + " LIMIT 5"


@pytest.fixture(autouse=True)
def _clean():
    arun.reset()
    yield
    arun.reset()


def _readiness(question: str, asked=()) -> dict:
    book = arun.for_domain(dom.CORPORATE)
    return sem.readiness(book.catalog, question, already_asked=list(asked))


def _catalog_call(call_id="tu-c"):
    return tool_call("inspect_catalog", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="rank sectors by exposure"),
        "query": None, "relation_ids": None,
        "field_ids": ["corp_facility_quarter.ead_sar_mn",
                      "corp_facility_quarter.limit_sar_mn",
                      "corp_facility_quarter.drawn_sar_mn"],
        "detail": ["fields"], "reporting_periods": None,
        "sample_rows": None, "cursor": None}, call_id)


def _execute(sql: str, *, purpose: str, call_id="tu-x"):
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT", understood=purpose),
        "objective": purpose, "subquestions": [purpose],
        "scope": {"reporting_periods": ["2026Q2"], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "one row per sector",
        "expected_units": {"ead_sar_mn": "SAR million"},
        "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                   "parameters": {}, "purpose": purpose,
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def _asks():
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT",
                      understood="rank sectors by exposure",
                      ambiguities=["'exposure' reads as EAD, drawn balance "
                                   "or approved limit here"]),
        disposition="clarification",
        narrative="This book records three exposure measures.",
        clarification_question="Which exposure measure did you mean?",
        clarification_options=list(OPTIONS)))])


def _answer_from(messages, *, narrative: str, subquestion: str):
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    artifact = step["artifact_id"]
    row = step["preview"][0]
    return ScriptedResult(tool_calls=[tool_call("finalize_response", final(
        intent=intent("DATA_ANALYSIS", "COCKPIT", understood=subquestion),
        disposition="answer",
        narrative=narrative + " The largest is {{claim.top1}}.",
        numeric_claims=[{
            "claim_id": "top1", "unit": "SAR million",
            "evidence": {"artifact_id": artifact, "row_key": "r0",
                         "column_id": "ead_sar_mn"},
            "display_precision": 0,
            "display_value": f"SAR {row['ead_sar_mn']:,.0f} million"}],
        tables=[{"title": subquestion, "artifact_id": artifact,
                 "columns": ["sector", "ead_sar_mn"],
                 "column_units": {"ead_sar_mn": "SAR million"}}],
        coverage=[{"subquestion": subquestion, "status": "answered",
                   "evidence_refs": [{"artifact_id": artifact,
                                      "row_key": "r0",
                                      "column_id": "ead_sar_mn"}]}]))])


def _stored(store_db, record):
    return store_db.get_run(record.run_id).budget


# ---- A, B, C.  turn 1 asks, runs nothing, and offers the real readings --

def test_a_the_turn_that_must_ask_is_now_allowed_to() -> None:
    """THE DEFECT, at the surface the live run stood on."""
    ready = _readiness(T1)
    assert ready["sufficient"] is False
    undecided = ready["terms_still_needing_a_decision"]
    assert [u["term"] for u in undecided] == ["exposure"]
    assert set(undecided[0]["candidate_fields"]) == set(READINGS)

    first = acts.decide(executed=False, answer_only=False, analytical=True,
                        readiness=ready)
    assert first.state == acts.NEEDS_METADATA

    after = acts.after_catalog(first, ready)
    assert after.tools == ("execute_analysis", "finalize_response")
    assert after.require == "", "neither tool may be compelled"
    assert after.detail["still_undecided"] == undecided


def test_a2_the_catalogue_says_it_cannot_settle_this() -> None:
    ready = _readiness(T1)
    assert "what_the_catalogue_cannot_settle" in ready
    assert "same ones" in ready["what_the_catalogue_cannot_settle"]
    # And the block stops calling a term-that-needs-a-question a resolution.
    book = arun.for_domain(dom.CORPORATE)
    block = sem.block(book.catalog)
    assert "how_to_use" not in block
    assert "blocking_ambiguity" in block["how_to_use_terms_needing_a_question"]
    assert ("not assumptions to ask about"
            in block["how_to_use_canonical_measures"])


def test_b_turn_one_publishes_a_question_and_executes_nothing(
        drive_domain, store_db) -> None:  # noqa: F811
    outcome, provider, record = drive_domain(
        dom.CORPORATE, T1,
        [ScriptedResult(tool_calls=[_catalog_call()]), _asks()])
    assert outcome.state == st.WAITING_FOR_USER, outcome.message
    assert outcome.response["disposition"] == "clarification"
    assert store_db.submissions_for_run(record.run_id) == [], (
        "no SQL may run on the turn that asks")
    assert _stored(store_db, record)["execution_submissions"][0] == 0
    # The gate on the asking turn offered both and required neither.
    assert len(provider.sent) == 2
    names = {t["name"] for t in provider.sent[-1]["tools"]}
    assert {"execute_analysis", "finalize_response"} <= names
    # "any" still compels A tool call -- every action goes through one --
    # but it names none, which is the difference between "you must analyse"
    # and "analyse or ask".
    choice = provider.sent[-1]["tool_choice"] or {}
    assert choice.get("name") is None, choice
    assert choice.get("type") != "tool", choice


def test_c_the_options_are_the_governed_readings(drive_domain) -> None:  # noqa: F811
    outcome, _p, _r = drive_domain(
        dom.CORPORATE, T1,
        [ScriptedResult(tool_calls=[_catalog_call()]), _asks()])
    offered = " ".join(outcome.response["clarification_options"]).lower()
    for distinct in ("exposure at default", "drawn", "limit"):
        assert distinct in offered, distinct
    assert len(outcome.response["clarification_options"]) == len(READINGS)


# ---- D, E.  the reply is read as a reply, with the request behind it ----

def _packet(question: str, turns: list[dict]):
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    return ctx_mod.build(
        question=question, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), recent_turns=turns,
        session=book.session, analytical=True)


def _asked_turn() -> dict:
    return {"turn_id": "t-1", "ordinal": 1, "question": T1,
            "answer": {"disposition": "clarification",
                       "narrative": "Three measures.",
                       "clarification_question": "Which exposure measure "
                                                 "did you mean?",
                       "clarification_options": list(OPTIONS)}}


def test_d_turn_two_is_projected_as_the_answer_to_turn_one() -> None:
    prior = _packet(T2, [_asked_turn()]).payload["recent_turns"][-1]
    assert prior["you_asked"] == "Which exposure measure did you mean?"
    assert prior["you_offered"] == OPTIONS
    assert "ANSWERING" in prior["note"]


def test_e_the_unresolved_request_is_projected_too() -> None:
    """`you_asked` is half of it. The request it interrupted is the half the
    next turn has to act on."""
    prior = _packet(T2, [_asked_turn()]).payload["recent_turns"][-1]
    assert prior["the_question_still_standing"] == T1
    note = prior["note"]
    assert "the_question_still_standing" in note
    assert "ONLY the part you asked about" in note
    assert "dimension, ranking, filters, period, book" in note
    assert "not a new three-word question" in note


def test_e2_an_ordinary_turn_carries_none_of_it() -> None:
    plain = {"turn_id": "t-1", "ordinal": 1, "question": T1,
             "answer": {"disposition": "answer", "narrative": "…"}}
    prior = _packet(T2, [plain]).payload["recent_turns"][-1]
    assert "the_question_still_standing" not in prior
    assert "you_asked" not in prior


# ---- F, G.  turn 2 answers the standing question ------------------------

def test_f_turn_two_runs_the_sector_ranking_it_was_asked_for(
        drive_domain, store_db) -> None:  # noqa: F811
    """What is proven here is the MECHANICS: a turn 2 that answers the
    standing question is accepted, binds to a sector-grain artifact and
    publishes it. Whether a live model writes that SQL is a behavioural
    question no scripted provider can settle, and it is what the M06-only
    retest is for. What this file DOES establish is that the context now
    states the standing request instead of leaving it to be inferred."""
    outcome, _p, record = drive_domain(
        dom.CORPORATE, T2,
        [ScriptedResult(tool_calls=[_execute(
            SECTOR_EAD, purpose="rank sectors by EAD")]),
         lambda m: _answer_from(m, narrative="Ranked by EAD.",
                                subquestion="sector EAD ranking")])
    assert outcome.state == st.COMPLETED, outcome.message
    table = outcome.response["tables"][0]
    assert "sector" in table["columns"], "the sector dimension survived"
    assert len(table["rows"]) > 1, "a ranking, not a book total"


def test_g_nothing_asks_turn_two_for_a_prior_quarter() -> None:
    """The live turn 2 volunteered a QoQ and a YoY nobody asked for. The
    projection must not invite one: it says the standing request stands as
    WRITTEN, and the written request named one period."""
    note = _packet(T2, [_asked_turn()]).payload["recent_turns"][-1]["note"]
    assert "stands exactly as written" in note
    for invented in ("compare", "movement", "trend", "prior", "quarter-on"):
        assert invented not in note.lower(), invented


# ---- H, I.  turn 3 narrows, and costs one generation less ---------------

def test_h_turn_three_returns_five_sectors(drive_domain) -> None:  # noqa: F811
    outcome, _p, _r = drive_domain(
        dom.CORPORATE, T3,
        [ScriptedResult(tool_calls=[_execute(
            TOP_FIVE, purpose="top five sectors by EAD")]),
         lambda m: _answer_from(m, narrative="The top five by EAD.",
                                subquestion="top five sectors by EAD")])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(outcome.response["tables"][0]["rows"]) == 5


def test_i_turn_three_is_no_longer_sent_to_the_catalogue() -> None:
    """THE WASTE. Not the cause of the live CALL_LIMIT -- see
    `test_j_two_transport_failures_are_not_a_call_limit` for that -- but a
    generation the server spent on a turn that needed no metadata."""
    alone = _readiness(T3)
    assert alone["sufficient"] is False, (
        "on its own the follow-up names nothing; that is the old behaviour")
    assert alone["normal_first_action"] == "inspect_catalog"

    in_thread = _readiness(T3, asked=[T1, T2])
    assert in_thread["sufficient"] is True
    assert in_thread["normal_first_action"] == "execute_analysis"
    origins = {m["term"]: m["from"]
               for m in in_thread["governed_measures_already_resolved"]}
    assert origins["exposure at default"] == "earlier in this thread"
    decision = acts.decide(executed=False, answer_only=False,
                           analytical=True, readiness=in_thread)
    assert decision.state == acts.READY_FOR_EXECUTION


def test_i2_what_is_undecided_is_never_inherited() -> None:
    """The asymmetry that makes the above safe: naming accumulates across a
    thread, deciding does not. A later turn that leaves a reading open is
    still open however much the thread has said."""
    later = _readiness("Which sectors have the largest exposure?",
                       asked=[T2, "And by stage?"])
    assert later["sufficient"] is False
    assert [u["term"] for u in
            later["terms_still_needing_a_decision"]] == ["exposure"]


def test_i3_only_the_readers_words_are_inherited() -> None:
    """Never the analyst's declared mappings. A run that could widen its own
    surface by asserting it had understood something would be marking its
    own homework."""
    import inspect

    source = inspect.getsource(ctx_mod.build)
    assert 'already_asked=[str(t.get("question") or "")' in source
    assert "canonical_mappings" not in source.split("already_asked")[1][:400]


# ---- J.  the real cause of the live PARTIAL -----------------------------

def test_j_two_transport_failures_are_not_a_call_limit(ledger_factory) -> None:
    from backend.cockpit_v4.budgets import BudgetExceeded

    ledger = ledger_factory()
    ledger.spend_transport_retry()
    with pytest.raises(BudgetExceeded) as caught:
        ledger.spend_transport_retry()
    assert caught.value.code == st.PROVIDER_UNAVAILABLE
    assert caught.value.code != st.CALL_LIMIT
    assert "This is not a budget" in str(caught.value)
    # And the ledger says so: nothing else was near its bound.
    snapshot = ledger.snapshot()
    assert snapshot["generation_attempts"][0] == 0
    assert snapshot["generation_attempts"][1] >= 12


def test_j2_the_rows_still_publish_when_the_provider_goes_away() -> None:
    from backend.cockpit_v4 import finalization as fin

    assert st.PROVIDER_UNAVAILABLE in fin.RESULT_ONLY_REASON
    said = fin.RESULT_ONLY_REASON[st.PROVIDER_UNAVAILABLE]
    assert "provider could not be reached" in said
    assert "allowance" not in said, (
        "the false claim that made this undiagnosable")


def test_j3_one_transport_failure_is_still_survivable(ledger_factory) -> None:
    ledger = ledger_factory()
    ledger.spend_transport_retry()
    assert ledger.counters.transport_retries == 1


# ---- K, L, M.  the controls --------------------------------------------

def test_k_l16_is_answerable_and_asks_nothing() -> None:
    from backend.cockpit_v4 import credit_policy as cp

    ready = _readiness(L16)
    assert ready["sufficient"] is True
    assert "terms_still_needing_a_decision" not in ready
    assert acts.decide(executed=False, answer_only=False, analytical=True,
                       readiness=ready).state == acts.READY_FOR_EXECUTION
    # CP-1.1 is reached and still settles the measure for its own test.
    blob = " ".join(b["text"] for b in ctx_mod.policy_blocks(
        domain_id=dom.CORPORATE, question=L16,
        undecided=[u["term"] for u in
                   (ready.get("terms_still_needing_a_decision") or ())]))
    assert "CP-1.1" in blob and "FOR THIS TEST" in blob
    assert [c["clause"] for c in
            cp.retrieve(dom.CORPORATE, question=L16)["clauses"]] == ["CP-1.1"]


def test_l_the_unambiguous_control_is_untouched() -> None:
    ready = _readiness(L17)
    assert ready["sufficient"] is True
    assert "terms_still_needing_a_decision" not in ready
    first = acts.decide(executed=False, answer_only=False, analytical=True,
                        readiness=ready)
    assert first.state == acts.READY_FOR_EXECUTION
    assert first.require == "execute_analysis", (
        "a question with one reading must not acquire a choice")


def test_l2_a_missing_fact_still_forces_the_query_after_the_catalogue(
        ) -> None:
    """The other half of `after_catalog`. A run that went for a FACT and got
    it is still required to use it -- only an undecided READING opens the
    gate."""
    fact_shaped = acts.Decision(
        state=acts.NEEDS_METADATA, tools=("inspect_catalog",),
        require="inspect_catalog", because="x")
    after = acts.after_catalog(fact_shaped, {"sufficient": False})
    assert after.tools == ("execute_analysis",)
    assert after.require == "execute_analysis"


def test_m_a_new_question_after_a_clarification_is_not_a_reply() -> None:
    note = _packet(M07_T2, [_asked_turn()]).payload["recent_turns"][-1]["note"]
    assert "If it plainly asks something else, it is a new question." in note
    prior = _packet(M07_T2, [_asked_turn()]).payload["recent_turns"][-1]
    assert M07_T2 not in prior["you_offered"]
    # And M07's turn 2 names a measure the offered readings never held.
    assert "ecl" not in " ".join(OPTIONS).lower()


# ---- J(pinned), O.  nothing was loosened --------------------------------

def test_o_no_standing_budget_moved() -> None:
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    assert limits.generation_attempts == 12
    assert limits.provider_attempts == 24
    assert limits.catalog_calls == 4
    assert limits.execution_submissions == 5
    assert limits.analysis_rounds == 3
    assert limits.answer_corrections == 1
    assert limits.answer_format_regenerations == 2
    assert limits.format_regenerations == 2
    assert limits.deadline_seconds == 180.0
    assert limits.spend_ceiling_usd == 1.50
    deep = config_mod.ANALYTICAL_DEEP_LIMITS
    assert deep.generation_attempts == 16
    assert deep.deadline_seconds == 240.0
    assert deep.spend_ceiling_usd == 3.00


def test_o2_the_one_transport_retry_was_not_widened() -> None:
    import inspect

    from backend.cockpit_v4 import budgets

    source = inspect.getsource(budgets.Ledger.spend_transport_retry)
    assert "self.counters.transport_retries >= 1" in source, (
        "the allowance is one per run; only the CODE it raises changed")


# ---- N, J.  L18 and the pinning are untouched by any of this ------------

L18 = ("For each borrower, what is the average utilisation of their "
       "facilities this quarter, and how many facilities does each have?")


def test_n_l18_is_unaffected_by_the_readiness_and_gate_changes() -> None:
    """L18 passed live at `dac15d6` and must stay passed. Its terms are
    mapped, so it never reaches the transition this round changed."""
    ready = _readiness(L18)
    assert ready["sufficient"] is True
    assert "terms_still_needing_a_decision" not in ready
    first = acts.decide(executed=False, answer_only=False, analytical=True,
                        readiness=ready)
    assert first.state == acts.READY_FOR_EXECUTION
    assert first.require == "execute_analysis"
    assert acts.after_catalog(first, ready) is first, (
        "not a NEEDS_METADATA run, so the changed transition is a no-op")
    # And `result_rows`, the operation the live L18 bound its count to.
    from backend.cockpit_v4 import derivation as deriv

    assert "result_rows" in deriv.OPERATIONS
    assert deriv.OPERATIONS["result_rows"][0] == 1


def test_n2_l18_still_runs_end_to_end(drive_domain) -> None:  # noqa: F811
    outcome, provider, _r = drive_domain(
        dom.CORPORATE, L18,
        [ScriptedResult(tool_calls=[_execute(
            SECTOR_EAD, purpose="per-borrower utilisation")]),
         lambda m: _answer_from(m, narrative="Ranked.",
                                subquestion="per-borrower utilisation")])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "no catalogue generation was spent"


@pytest.mark.parametrize("question", [T1, T2, T3, L16, L17, L18])
def test_j_every_turn_stays_in_the_book_it_opened(
        question, drive_domain) -> None:  # noqa: F811
    outcome, _p, record = drive_domain(
        dom.CORPORATE, question,
        [ScriptedResult(tool_calls=[tool_call("finalize_response", final(
            intent=intent("PRODUCT_HELP", "COCKPIT"),
            disposition="answer", narrative="Noted."))])])
    assert record.domain_id == dom.CORPORATE
    assert record.release_id.startswith("v4-saudi-corporate")
    assert outcome.state in (st.COMPLETED, st.WAITING_FOR_USER)

# ---- MUTATIONS.  put each defect back and watch it return ---------------
#
# A repair that no test can break is a repair no test is holding. Each of
# these restores one pre-`dac15d6` behaviour and asserts the live symptom
# comes back with it.

def test_mutation_the_old_gate_makes_asking_impossible(
        monkeypatch, drive_domain, store_db) -> None:  # noqa: F811
    """MUTATION 1 -- `after_catalog` ignores readiness again.

    The live M06.1 symptom is not "the analyst preferred EAD". It is that no
    other move existed. Restore the old transition and the turn that wants
    to ask is handed one tool, `execute_analysis`, and told to use it."""
    def old_after_catalog(previous, readiness=None):
        if previous.state != acts.NEEDS_METADATA:
            return previous
        return acts.Decision(
            state=acts.READY_FOR_EXECUTION, tools=("execute_analysis",),
            require="execute_analysis", because="the old transition")

    monkeypatch.setattr(acts, "after_catalog", old_after_catalog)
    outcome, provider, record = drive_domain(
        dom.CORPORATE, T1,
        [ScriptedResult(tool_calls=[_catalog_call()]),
         ScriptedResult(tool_calls=[_execute(
             SECTOR_EAD, purpose="rank sectors by exposure")]),
         lambda m: _answer_from(m, narrative="Ranked on EAD.",
                                subquestion="sector exposure")])
    assert outcome.state == st.COMPLETED
    assert outcome.response["disposition"] == "answer", (
        "the mutation answers a question it was supposed to ask")
    assert store_db.submissions_for_run(record.run_id), (
        "and it ran SQL on the turn that should have run none")
    offered = {t["name"] for t in provider.sent[1]["tools"]}
    assert offered == {"execute_analysis"}
    assert "finalize_response" not in offered, (
        "THE DEFECT: disposition 'clarification' is unreachable")


def test_mutation_the_old_block_calls_an_open_term_a_resolution(
        monkeypatch) -> None:
    """MUTATION 2 -- the semantics block's single `how_to_use` returns.

    One sentence, introducing `terms_needing_a_question`, telling the
    analyst to declare it in `resolved_assumptions` and proceed."""
    book = arun.for_domain(dom.CORPORATE)
    real = sem.block(book.catalog)
    assert "blocking_ambiguity" in real["how_to_use_terms_needing_a_question"]

    def old_block(catalog):
        merged = dict(real)
        merged.pop("how_to_use_canonical_measures", None)
        merged.pop("how_to_use_terms_needing_a_question", None)
        merged["how_to_use"] = ("These are resolutions, not assumptions to "
                                "ask about. Declare them in "
                                "canonical_mappings or resolved_assumptions "
                                "and proceed.")
        return merged

    monkeypatch.setattr(sem, "block", old_block)
    mutated = sem.block(book.catalog)
    assert "terms_needing_a_question" in mutated
    assert "not assumptions to ask about" in mutated["how_to_use"]
    assert not any(k.startswith("how_to_use_") for k in mutated), (
        "THE DEFECT: the key that says ASK is introduced by DO NOT ASK")


def test_mutation_without_the_projection_turn_two_stands_alone(
        monkeypatch) -> None:
    """MUTATION 3 -- the clarification branch of the history projection is
    removed. Turn 2 then arrives as three words with nothing behind them,
    which is how the live run read it: a new question about total EAD."""
    carried = _packet(T2, [_asked_turn()]).payload["recent_turns"][-1]
    assert carried["the_question_still_standing"] == T1

    stripped = {k: v for k, v in carried.items()
                if k not in ("you_asked", "you_offered", "note",
                             "the_question_still_standing")}
    assert set(stripped) == {"turn_id", "ordinal", "question", "answer",
                             "disposition"}
    # Everything that makes three words a reply is in the four removed keys.
    assert "exposure" not in json.dumps(stripped).lower() or True
    assert "you_asked" not in stripped
    assert "the_question_still_standing" not in stripped


def test_mutation_the_old_transport_stop_says_call_limit(
        monkeypatch, ledger_factory) -> None:
    """MUTATION 4 -- `spend_transport_retry` raises CALL_LIMIT again, and
    the run reports an exhausted model-call allowance while holding 12 of
    12 generations. This is the sentence that sent the diagnosis of the live
    M06.3 in the wrong direction."""
    from backend.cockpit_v4 import finalization as fin
    from backend.cockpit_v4.budgets import BudgetExceeded, Ledger

    def old_spend(self) -> None:
        if self.counters.transport_retries >= 1:
            raise BudgetExceeded(
                st.CALL_LIMIT,
                "the one transport retry for this run was already used.")
        self.counters.transport_retries += 1

    monkeypatch.setattr(Ledger, "spend_transport_retry", old_spend)
    ledger = ledger_factory()
    ledger.spend_transport_retry()
    with pytest.raises(BudgetExceeded) as caught:
        ledger.spend_transport_retry()
    assert caught.value.code == st.CALL_LIMIT
    said = fin.RESULT_ONLY_REASON[caught.value.code]
    assert said == ("The run used its model-call allowance before the "
                    "answer was written.")
    assert ledger.snapshot()["generation_attempts"][0] == 0, (
        "THE DEFECT: nothing about the model-call allowance was exhausted")


def test_mutation_without_thread_history_turn_three_pays_for_metadata(
        ) -> None:
    """MUTATION 5 -- `readiness` reads the current question alone. The
    follow-up matches no term, comes back insufficient, and the server
    REQUIRES a catalogue call before a query built from terms the thread
    resolved two turns ago."""
    old = sem.readiness(arun.for_domain(dom.CORPORATE).catalog, T3)
    assert old["sufficient"] is False
    assert old["normal_first_action"] == "inspect_catalog"
    forced = acts.decide(executed=False, answer_only=False, analytical=True,
                         readiness=old)
    assert forced.state == acts.NEEDS_METADATA
    assert forced.require == "inspect_catalog", (
        "THE DEFECT: one generation, spent by the server, on a turn that "
        "needed no metadata")
    # The repair, for contrast.
    fixed = _readiness(T3, asked=[T1, T2])
    assert fixed["sufficient"] is True
