"""
Analytical execution: what may block it, what must bind, and what it costs.

Evidence: REAL DATABASE for every number — the queries run against the pinned
release through the real DuckDB session — and MODEL MOCK for the runs, which
use the scripted provider. No paid provider call is made in this module.

Three defects are pinned here, each from a live run:

  * a declared resolution ("exposure read as reported EAD") refused the
    analysis it made possible;
  * "Query validated" was announced before DuckDB had been asked whether the
    query bound at all, and `steps[].parameters` never reached the engine;
  * a Standard analytical run ran out of a sixty-second deadline and a
    one-dollar ceiling that were set for a product question.
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import Rejection, parse_execution
from backend.cockpit_v4.sqlbind import (BindFailure, parameter_argument,
                                        prove_bindable)


def _submission(sql: str, *, blocking=(), resolved=(), mappings=(),
                parameters=None, step_id="s1"):
    return parse_execution({
        "intent": intent("DATA_ANALYSIS", "COCKPIT", ambiguities=list(blocking),
                         resolved=list(resolved), mappings=list(mappings)),
        "objective": "o", "subquestions": ["a"],
        "scope": {"reporting_periods": [], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": ["f"],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": [{"step_id": step_id, "language": "sql", "code": sql,
                   "parameters": dict(parameters or {}), "purpose": "p",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, max_steps=6)


# ---- 1. a resolution is not an ambiguity --------------------------------

RESOLUTIONS = [
    "Exposure read as reported EAD (ead_reported), not gross carrying amount.",
    "Period not specified: using the latest populated quarter 2026Q2 against "
    "2026Q1.",
    "ECL read as booked ECL (ecl_reported).",
]


@pytest.mark.parametrize("line", RESOLUTIONS)
def test_a_declared_resolution_does_not_block_execution(service, line):
    """The exact sentences a live run was refused for."""
    submission = _submission(
        "SELECT 1 AS n", resolved=[line], mappings=[line])
    assert submission.intent.may_execute
    service.validate_batch(submission)


def test_a_genuine_blocking_ambiguity_still_refuses(service):
    submission = _submission(
        "SELECT 1 AS n",
        blocking=["'exposure' could be ead_reported, gross_carrying_amount "
                  "or drawn_balance"])
    assert not submission.intent.may_execute
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    assert "BLOCKING ambiguity" in str(caught.value)
    assert "resolved_assumptions" in str(caught.value)


def test_the_three_lists_are_carried_separately(service):
    submission = _submission("SELECT 1 AS n", resolved=["a"], mappings=["b"])
    body = submission.intent.to_dict()
    assert body["resolved_assumptions"] == ["a"]
    assert body["canonical_mappings"] == ["b"]
    assert body["blocking_ambiguities"] == []


# ---- 2. canonical semantics --------------------------------------------

def test_every_canonical_mapping_names_a_field_the_release_has(runtime):
    columns = {relation: set(runtime.catalog.columns(relation))
               for relation in runtime.catalog.relations()}
    for mapping in sem.measures(runtime.catalog):
        assert mapping["field"] in columns.get(mapping["relation"], set()), \
            f"{mapping['term']} -> {mapping['relation']}.{mapping['field']}"


def test_exposure_at_default_is_never_ambiguous():
    for question in ("What is total exposure at default by sector in the "
                     "latest quarter?",
                     "show total exposure at default by sector",
                     "EAD by sector latest quarter"):
        assert sem.ambiguous_terms_in(question) == [], question


def test_the_bare_word_exposure_is_the_one_that_needs_a_question():
    found = sem.ambiguous_terms_in(
        "Which sectors had ECL increase faster than exposure this quarter?")
    assert [f["term"] for f in found] == ["exposure"]
    assert set(found[0]["candidate_fields"]) == {
        "ead_reported", "gross_carrying_amount", "drawn_balance"}


def test_period_resolution_is_deterministic_from_the_calendar(runtime,
                                                              release_id):
    periods = sem.periods(runtime.catalog)
    slots = oracles.quarters(release_id)
    assert periods["latest_quarter"] == slots[-1]
    assert periods["prior_quarter"] == slots[-2]
    assert periods["same_quarter_last_year"] == slots[-5]
    assert "latest POPULATED reporting quarter" in periods["rule"]


def test_the_semantics_block_reaches_the_model(drive):
    script = [ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent(), disposition="answer", narrative="hi"))])]
    _outcome, provider, _record = drive("What is EAD by sector?", script)
    sent = provider.first_input_text()
    assert "cockpit_semantics" in sent
    assert "ead_reported" in sent
    assert "latest POPULATED reporting quarter" in sent


# ---- 3. binding --------------------------------------------------------

def test_a_placeholder_with_no_parameter_is_refused_at_validation(service):
    """The live failure, reproduced: EXPLAIN and EXECUTE both refuse it."""
    submission = _submission(
        "SELECT sector_name FROM cockpit_facility_quarter "
        "WHERE reporting_quarter = ? GROUP BY 1")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    detail = caught.value.detail
    assert detail["failed_check"] == "bind"
    assert detail["phase"] == "bind"
    assert "parameter 1 was not supplied" in caught.value.message


def test_a_placeholder_with_its_parameter_binds_and_runs(service,
                                                         release_id):
    quarter = oracles.latest_quarter(release_id)
    submission = _submission(
        "SELECT sector_name, SUM(ead_reported) AS ead FROM "
        "cockpit_facility_quarter WHERE reporting_quarter = ? GROUP BY 1",
        parameters={"1": quarter})
    report = service.validate_batch(submission)
    assert report["bound_now"] == ["s1"]
    result = service.run_batch(submission, submission_id="sub-1",
                               deadline_seconds=20.0)
    assert result.status == "ok", result.steps[0].message
    expected = oracles.ead_by_sector(release_id, quarter)
    rows = {r["sector_name"]: float(r["ead"]) for r in result.steps[0].preview}
    assert set(rows) == set(expected)
    for sector, value in expected.items():
        assert rows[sector] == pytest.approx(float(value), rel=1e-9)


def test_named_parameters_bind_too(service, release_id):
    quarter = oracles.latest_quarter(release_id)
    submission = _submission(
        "SELECT COUNT(*) AS n FROM cockpit_facility_quarter "
        "WHERE reporting_quarter = $q",
        parameters={"q": quarter})
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="sub-1",
                               deadline_seconds=20.0)
    assert result.status == "ok"


def test_parameters_with_no_placeholder_are_refused(service):
    submission = _submission("SELECT 1 AS n", parameters={"1": "x"})
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    assert "no placeholder" in caught.value.message


def test_an_unresolvable_column_never_reaches_execution(service):
    submission = _submission(
        "SELECT exposure FROM cockpit_facility_quarter LIMIT 1")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    assert caught.value.detail["unresolved_name"] == "exposure"
    assert "BinderException" in caught.value.detail["duckdb_exception_type"]


@pytest.mark.parametrize("sql,unresolved", [
    ("SELECT sector_name FROM cockpit_facility_quarter GROUP BY 7", ""),
    ("SELECT sector_name, SUM(ead_reported) FROM cockpit_facility_quarter "
     "ORDER BY nope", "nope"),
    ("SELECT SUM(no_such) FROM cockpit_facility_quarter", "no_such"),
])
def test_the_binder_catches_what_structure_checks_cannot(service, sql,
                                                         unresolved):
    submission = _submission(sql)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission)
    assert caught.value.detail["failed_check"] == "bind"
    if unresolved:
        assert caught.value.detail["unresolved_name"] == unresolved


def test_binding_executes_nothing(service, release_id):
    """An EXPLAIN of an unguarded cross join is instant. Running it is not."""
    import time

    started = time.monotonic()
    prove_bindable(
        "SELECT a.borrower_id, b.borrower_id AS other "
        "FROM cockpit_facility_quarter a, cockpit_facility_quarter b",
        service.session)
    assert time.monotonic() - started < 2.0


def test_the_parameter_argument_shape_is_what_duckdb_wants():
    assert parameter_argument("SELECT 1", None) is None
    assert parameter_argument("SELECT ?", {"1": "a"}) == ["a"]
    assert parameter_argument("SELECT ?, ?", {"2": "b", "1": "a"}) == ["a", "b"]
    assert parameter_argument("SELECT $x", {"x": 1}) == {"x": 1}
    with pytest.raises(BindFailure):
        parameter_argument("SELECT ?, $x", {"1": "a", "x": 1})
    with pytest.raises(BindFailure):
        parameter_argument("SELECT ?", {"quarter": "2026Q2"})


# ---- 4. the four questions, against independent oracles ----------------

EAD_SQL = ("SELECT sector_name, SUM(ead_reported) AS ead_sar_mn "
           "FROM cockpit_facility_quarter WHERE reporting_quarter = ? "
           "GROUP BY 1 ORDER BY 2 DESC")

ECL_TOP5_SQL = ("SELECT sector_name, SUM(ecl_reported) AS ecl_sar_mn "
                "FROM cockpit_facility_quarter WHERE reporting_quarter = ? "
                "GROUP BY 1 ORDER BY 2 DESC, 1 ASC LIMIT 5")

STAGE2_YOY_SQL = """
WITH now AS (
  SELECT sector_name, SUM(ead_reported) AS s2
  FROM cockpit_facility_quarter
  WHERE reporting_quarter = $now AND ifrs9_stage = 2 GROUP BY 1),
then_ AS (
  SELECT sector_name, SUM(ead_reported) AS s2
  FROM cockpit_facility_quarter
  WHERE reporting_quarter = $then AND ifrs9_stage = 2 GROUP BY 1)
SELECT COALESCE(n.sector_name, t.sector_name) AS sector_name,
       COALESCE(t.s2, 0) AS prior,
       COALESCE(n.s2, 0) AS current,
       COALESCE(n.s2, 0) - COALESCE(t.s2, 0) AS change,
       CASE WHEN t.sector_name IS NULL THEN 'entered'
            WHEN n.sector_name IS NULL THEN 'exited'
            ELSE 'present' END AS status
FROM now n FULL OUTER JOIN then_ t ON n.sector_name = t.sector_name
ORDER BY change DESC, 1 ASC
"""


def test_question_a_total_ead_by_sector_matches_the_oracle(service,
                                                           release_id):
    quarter = oracles.latest_quarter(release_id)
    submission = _submission(EAD_SQL, parameters={"1": quarter},
                             mappings=["exposure at default = ead_reported"],
                             resolved=[f"latest populated quarter {quarter}"])
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="s", 
                               deadline_seconds=20.0)
    assert result.status == "ok"
    expected = oracles.ead_by_sector(release_id, quarter)
    rows = {r["sector_name"]: float(r["ead_sar_mn"])
            for r in result.steps[0].preview}
    assert set(rows) == set(expected)
    for sector, value in expected.items():
        # The oracle rounds to six places; asserting past its own precision
        # would compare the engine against the rounding.
        assert rows[sector] == pytest.approx(float(value), rel=1e-6)


def test_question_b_top_five_sectors_by_ecl_match_the_oracle(service,
                                                             release_id):
    quarter = oracles.latest_quarter(release_id)
    submission = _submission(ECL_TOP5_SQL, parameters={"1": quarter},
                             mappings=["ECL = ecl_reported (booked)"])
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="s",
                               deadline_seconds=20.0)
    assert result.status == "ok"
    expected = oracles.top_sectors_by_ecl(release_id, quarter, n=5)
    rows = [(r["sector_name"], float(r["ecl_sar_mn"]))
            for r in result.steps[0].preview]
    assert [s for s, _ in rows] == [s for s, _ in expected]
    for (_, got), (_, want) in zip(rows, expected):
        assert got == pytest.approx(float(want), rel=1e-9)


def test_question_c_stage2_year_change_loses_no_sector(service, release_id):
    expected = oracles.stage2_year_change(release_id)
    submission = _submission(
        STAGE2_YOY_SQL,
        parameters={"now": expected["current_quarter"],
                    "then": expected["prior_quarter"]},
        resolved=[f"latest year = {expected['current_quarter']} vs "
                  f"{expected['prior_quarter']}"])
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="s",
                               deadline_seconds=20.0)
    assert result.status == "ok"
    rows = {r["sector_name"]: r for r in result.steps[0].preview}

    present = {r["sector"] for r in expected["by_sector"]}
    assert set(rows) == present, (
        "a FULL OUTER JOIN must keep every sector in either quarter; an "
        "inner join here deletes exactly the movements being asked about")
    for row in expected["by_sector"]:
        got = rows[row["sector"]]
        assert float(got["change"]) == pytest.approx(float(row["change"]),
                                                     rel=1e-6)
        assert got["status"] == row["status"]
    entered = [s for s, r in rows.items() if r["status"] == "entered"]
    assert sorted(entered) == sorted(expected["entered"])


def test_question_d_ecl_faster_than_exposure_matches_the_oracle(service,
                                                                release_id):
    expected = oracles.ecl_vs_exposure_growth(release_id)
    sql = """
    WITH n AS (SELECT sector_name, SUM(ecl_reported) AS ecl,
                      SUM(ead_reported) AS ead
               FROM cockpit_facility_quarter WHERE reporting_quarter = $now
               GROUP BY 1),
         p AS (SELECT sector_name, SUM(ecl_reported) AS ecl,
                      SUM(ead_reported) AS ead
               FROM cockpit_facility_quarter WHERE reporting_quarter = $prev
               GROUP BY 1)
    SELECT COALESCE(n.sector_name, p.sector_name) AS sector_name,
           (n.ecl - p.ecl) / NULLIF(p.ecl, 0) AS ecl_growth,
           (n.ead - p.ead) / NULLIF(p.ead, 0) AS ead_growth
    FROM n FULL OUTER JOIN p ON n.sector_name = p.sector_name
    WHERE p.ecl IS NOT NULL AND n.ecl IS NOT NULL AND p.ecl <> 0
      AND p.ead <> 0
      AND (n.ecl - p.ecl) / p.ecl > (n.ead - p.ead) / p.ead
    ORDER BY (n.ecl - p.ecl) / p.ecl - (n.ead - p.ead) / p.ead DESC, 1 ASC
    """
    submission = _submission(
        sql, parameters={"now": expected["quarter"],
                         "prev": expected["comparison"]},
        blocking=[])
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="s",
                               deadline_seconds=20.0)
    assert result.status == "ok"
    rows = [(r["sector_name"], float(r["ecl_growth"]), float(r["ead_growth"]))
            for r in result.steps[0].preview]
    assert [s for s, _, _ in rows] == [s for s, _, _ in expected["faster"]]
    for (_, eg, ag), (_, want_e, want_a) in zip(rows, expected["faster"]):
        assert eg == pytest.approx(float(want_e), rel=1e-6)
        assert ag == pytest.approx(float(want_a), rel=1e-6)


# ---- 5. budgets, deadlines and the answer reserve ----------------------

def _declares_analysis(_messages):
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              disposition="partial_answer",
              narrative="Nothing to report yet."))])


def test_an_analytical_question_runs_on_the_analysis_allowance(drive,
                                                               store_db):
    """§9. A question that names a measure is on the analysis clock from its
    first second, not once the analyst has spent a generation saying so."""
    allowance = config_mod.ANALYTICAL_STANDARD_LIMITS
    _outcome, _provider, record = drive("EAD by sector", [_declares_analysis])
    budget = store_db.get_run(record.run_id).budget
    # READ FROM THE CONSTANT, not repeated as a literal. The claim is that
    # the run is on the ANALYSIS allowance; which number that is belongs to
    # `test_the_analytical_limits_are_deliberate_values` and nowhere else.
    assert budget["deadline_seconds"] == allowance.deadline_seconds
    assert budget["spend_ceiling_usd"] == allowance.spend_ceiling_usd
    messages = [e.public_message
                for e in store_db.events_since(record.run_id)]
    wanted = (f"Allowance: {allowance.deadline_seconds:.0f}s, "
              f"${allowance.spend_ceiling_usd:.2f}")
    assert any(wanted in m for m in messages), messages


def test_a_declared_analysis_still_widens_a_run_that_started_narrow(
        drive, store_db):
    """The widening path is the fallback, and it is still load-bearing: a
    question the envelope could not read as analytical, whose analyst then
    declares one, gets the analysis allowance on the declaration."""
    allowance = config_mod.ANALYTICAL_STANDARD_LIMITS
    _outcome, _provider, record = drive("Tell me more", [_declares_analysis])
    budget = store_db.get_run(record.run_id).budget
    assert budget["deadline_seconds"] == allowance.deadline_seconds
    assert budget["spend_ceiling_usd"] == allowance.spend_ceiling_usd
    events = store_db.events_since(record.run_id)
    messages = [e.public_message for e in events]
    wanted = (f"Analysis allowance: {allowance.deadline_seconds:.0f}s, "
              f"${allowance.spend_ceiling_usd:.2f}")
    assert any(wanted in m for m in messages), (
        f"the run must widen on the declaration; messages were {messages}")
    assert "budget" in [e.operation for e in events]


def test_a_product_question_keeps_the_tight_allowance(drive, store_db):
    script = [ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent(), disposition="answer", narrative="hi"))])]
    _outcome, _provider, record = drive("Who are you?", script)
    budget = store_db.get_run(record.run_id).budget
    assert budget["deadline_seconds"] == 60.0
    assert budget["spend_ceiling_usd"] == 1.0


def test_the_stored_watchdog_deadline_moves_with_the_allowance(drive,
                                                               store_db):
    before = None

    def submit(_messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="partial_answer", narrative="x"))])

    _outcome, _provider, record = drive("EAD by sector", [submit])
    stored = store_db.get_run(record.run_id)
    # The supervisor settles on `deadline_at`; widening the ledger alone
    # would leave a 120-second analysis killed by a 60-second watchdog.
    assert stored.deadline_at


def test_the_analytical_limits_are_deliberate_values():
    standard = config_mod.analytical_limits_for("standard")
    deep = config_mod.analytical_limits_for("deep")
    # THE ONE PLACE THE NUMBER IS WRITTEN DOWN. 120 was measured against a
    # run that writes one query and one answer; two live runs spent 25s, 21s
    # and 52s on three generations before the answer correction had even
    # started, and both were reaped mid-sentence.
    assert (standard.deadline_seconds, standard.spend_ceiling_usd) == (
        180.0, 1.50)
    assert (deep.deadline_seconds, deep.spend_ceiling_usd) == (240.0, 3.00)
    # Nothing else moved: this is a time and money change, not a quiet
    # loosening of how much work a run may do.
    base = config_mod.STANDARD_LIMITS
    for field in ("execution_submissions", "analysis_rounds",
                  "generation_attempts", "catalog_calls", "total_steps",
                  "answer_corrections", "reserved_output_tokens"):
        assert getattr(standard, field) == getattr(base, field), field


def _prespend_leaving(ledger, *, input_tokens: int, output_tokens: int
                      ) -> float:
    """What to reserve up front so exactly `output_tokens` stay affordable.

    Derived from the capability's own price card rather than typed in. The
    literals here used to be tuned by hand against a $15/$75 fixture, so
    correcting that fixture to the model CreditProbe actually serves broke
    a test about affordability arithmetic for reasons that had nothing to
    do with affordability arithmetic.
    """
    price = ledger.capability.price
    fixed = price.cost(input_tokens=input_tokens, output_tokens=0)
    per_token = price.cost(input_tokens=0, output_tokens=1_000_000) / 1e6
    return ledger.limits.spend_ceiling_usd - fixed - output_tokens * per_token


def test_the_response_allowance_shrinks_before_the_run_fails(ledger_factory):
    """A shorter answer is affordable; refusing outright was the defect."""
    ledger = ledger_factory(spend_ceiling_usd=1.0)
    wanted = config_mod.STANDARD_LIMITS.reserved_output_tokens
    # Leave room for half the answer: enough to be worth writing, not
    # enough to write the whole of it.
    ledger.store.reserve(
        run_id=ledger.run_id, purpose="earlier",
        reserved_usd=_prespend_leaving(ledger, input_tokens=12_000,
                                       output_tokens=wanted // 2))
    affordable = ledger.affordable_output_tokens(input_tokens=12_000,
                                                 wanted=wanted)
    assert 0 < affordable < wanted
    assert affordable >= ledger.MIN_RESPONSE_TOKENS


def test_a_budget_that_cannot_buy_any_answer_still_fails_closed(
        ledger_factory):
    ledger = ledger_factory(spend_ceiling_usd=1.0)
    wanted = config_mod.STANDARD_LIMITS.reserved_output_tokens
    # Room for half the floor is no room at all: below MIN_RESPONSE_TOKENS
    # there is no answer worth paying for, and the run must say so rather
    # than buy a stub.
    ledger.store.reserve(
        run_id=ledger.run_id, purpose="earlier",
        reserved_usd=_prespend_leaving(
            ledger, input_tokens=12_000,
            output_tokens=ledger.MIN_RESPONSE_TOKENS // 2))
    assert ledger.affordable_output_tokens(
        input_tokens=12_000, wanted=wanted) < ledger.MIN_RESPONSE_TOKENS


def test_the_first_analytical_failure_survives_a_later_terminal_stop(
        drive, store_db):
    """Binder failure, then a cost stop. The terminal code is not the story."""
    broken = ("SELECT sector_name, SUM(no_such_column) AS x FROM "
              "cockpit_facility_quarter GROUP BY 1")

    def first(_messages):
        return ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT"),
            "objective": "o", "subquestions": ["a"],
            "scope": {"reporting_periods": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": ["f"],
            "expected_output_grain": "sector", "expected_units": "u",
            "steps": [{"step_id": "s1", "language": "sql", "code": broken,
                       "parameters": {}, "purpose": "p",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""})])

    def then_run_out(_messages):
        raise TimeoutError("no more budget")

    outcome, _provider, record = drive("EAD by sector", [first, then_run_out])
    assert outcome.state in (st.FAILED, st.EXPIRED, st.PARTIAL)
    messages = [e.public_message
                for e in store_db.events_since(record.run_id)]
    assert any("did not bind and was not run" in m for m in messages), messages
