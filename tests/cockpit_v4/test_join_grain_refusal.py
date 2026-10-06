"""
A refusal that says it was a refusal.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

A step stopped by the join-grain check or by a missing Python sandbox used
to be reported as a query that RAN and failed: `phase: "runtime"`,
`executed: true`, and a reader told "<purpose> failed while running" about a
batch in which the engine was never asked for anything. Two lines did it --
a two-valued `phase` switch that called everything-not-bind a runtime
failure, and an `executed` exclusion tuple that named three checks out of
nine.

Underneath was a placement bug. `multiplication_risk` is a judgement about
the query TEXT -- a catalogue read and, now, a parse, no engine, no
parameters, no earlier artifact -- yet it ran inside `_run_sql`, so the
fan-out refusal fired after the step budget was spent and after the run had
publicly announced the very query it refuses. Three of the four branches
around it could not execute, and the fourth turned a broken diagnostic into
a clean bill of health.

Then a live run showed the judgement itself was wrong. The check decided
which relations a query joined by asking whether their NAMES appeared
anywhere in the flattened text, and read the join key out of the CATALOGUE
rather than out of the query. A four-step Corporate analysis whose fourth
step reached two relations through independent correlated scalar
subqueries -- no JOIN token anywhere in it -- was refused for "aggregating
across the join between corp_covenant_quarter and corp_facility_quarter",
quoting a key the query never mentions. Its figures were correct.

The same text scan failed in the other direction: the escape hatch looked
for the word "distinct" anywhere in the statement, so a query that really
did double-count was excused by a COMMENT containing it.

Both are gone. The check now reads DuckDB's own parse of the statement, so
"joined" means joined to each other in one query scope and "de-duplicated"
means the aggregate itself carries DISTINCT.

What is pinned here:

  the rung is derived, once        -> phase and executed cannot disagree
  the check runs before anything   -> refused above TOOL_VALIDATED
  the refusal carries its evidence -> facts, not a sentence to regex
  a one-to-one join repeats nothing -> and is no longer refused
  a scalar subquery repeats nothing -> the live submission, verbatim
  a comment does not de-duplicate  -> the aggregate does
  a query that really ran          -> still says so

Recorded, not fixed: a join inside a CTE whose RAW rows are selected out and
summed in the outer query is not refused -- the outer aggregate's operand is
the CTE alias, and the joining scope aggregates nothing. The text check
allowed this too, so it is a standing gap and not a regression.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, intent, tool_call

from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import execute_tool as xt
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.contracts import Rejection

#: A total of a BORROWER measure across the facility join. Every facility of
#: a borrower repeats that borrower's debt, so the total counts it once per
#: facility. Verified against the catalogue: `total_debt_sar_mn` is `rcy`,
#: and the pair is declared "many facility rows to one borrower row".
CORPORATE_FAN_OUT = (
    "SELECT b.sector, SUM(b.total_debt_sar_mn) AS debt "
    "FROM corp_facility_quarter f "
    "JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id "
    "AND b.reporting_quarter = f.reporting_quarter "
    "GROUP BY 1")

#: The catalogue declares this pair "one behaviour row to one account" and
#: says in as many words that it repeats nothing. It was refused anyway,
#: because every declared join was read as many-to-one.
RETAIL_ONE_TO_ONE = (
    "SELECT SUM(a.ead_sar_mn) AS ead "
    "FROM retail_behaviour_month b "
    "JOIN retail_account_month a ON a.account_id = b.account_id "
    "AND a.reporting_month = b.reporting_month")

#: THE EXACT SUBMISSION THAT WAS WRONGLY REFUSED, character for character
#: from the stored submission record of the live run. Not re-indented, not
#: re-spelled, not shortened: a rewritten equivalent would prove that some
#: query like it is accepted, which is not the claim.
#:
#: It contains no JOIN. `corp_covenant_quarter` and `corp_borrower_quarter`
#: are each read by an independent correlated scalar subquery, whose result
#: is one value substituted into the row -- it repeats nothing. The check
#: refused it anyway, because both names appear in the text.
LIVE_SUBMISSION_S4 = """SELECT f.reporting_quarter,
       SUM(f.sicr_flag) AS sicr_flags,
       SUM(f.default_flag) AS default_flags,
       AVG(f.pd_pit_12m) AS avg_pd,
       SUM(CASE WHEN f.dpd_days>30 THEN 1 ELSE 0 END) AS facs_dpd30,
       (SELECT COUNT(*)
          FROM corp_covenant_quarter c
         WHERE c.reporting_quarter=f.reporting_quarter
           AND c.breach_flag=1) AS covenant_breaches,
       (SELECT COUNT(*)
          FROM corp_borrower_quarter b
         WHERE b.reporting_quarter=f.reporting_quarter
           AND b.rating_migration='DOWNGRADE') AS downgrades
FROM corp_facility_quarter f
WHERE f.reporting_quarter IN ('2026Q1','2026Q2')
GROUP BY f.reporting_quarter
ORDER BY f.reporting_quarter;"""

#: The same query with both subqueries removed. If the subqueries had
#: multiplied facility rows, the facility figures would differ between the
#: two. They do not, and that is the positive proof the refusal was false.
LIVE_SUBMISSION_S4_WITHOUT_SUBQUERIES = """SELECT f.reporting_quarter,
       SUM(f.sicr_flag) AS sicr_flags,
       SUM(f.default_flag) AS default_flags,
       AVG(f.pd_pit_12m) AS avg_pd,
       SUM(CASE WHEN f.dpd_days>30 THEN 1 ELSE 0 END) AS facs_dpd30
FROM corp_facility_quarter f
WHERE f.reporting_quarter IN ('2026Q1','2026Q2')
GROUP BY f.reporting_quarter
ORDER BY f.reporting_quarter;"""

#: The fan-out, unchanged, with the word "distinct" in a COMMENT. Nothing is
#: de-duplicated; the old escape hatch searched the statement for the word.
FAN_OUT_WITH_DISTINCT_IN_A_COMMENT = (
    "-- one row per borrower, distinct by construction\n" + CORPORATE_FAN_OUT)

#: The fan-out with DISTINCT on the aggregate itself. This one really does
#: take the repetition out, and is the case the hatch was written for.
FAN_OUT_WITH_A_DISTINCT_AGGREGATE = (
    "SELECT b.sector, SUM(DISTINCT b.total_debt_sar_mn) AS debt "
    "FROM corp_facility_quarter f "
    "JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id "
    "GROUP BY 1")

#: Binds, and then fails in the engine: the cast is legal to plan and
#: impossible to evaluate. The no-regression case.
REALLY_RUNS_AND_FAILS = (
    "SELECT CAST(borrower_id AS INTEGER) AS n FROM corp_borrower_quarter "
    "LIMIT 1")


@pytest.fixture(scope="module", autouse=True)
def _both_books_published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")


@pytest.fixture
def drive_domain(store_db, runtime):
    """Run one scripted conversation, in one BOOK, through the real worker.

    `drive` is not usable here: it builds on the legacy catalogue, which
    states no joins, so the check under test is silent on it.
    """
    from conftest import ScriptedProvider
    from test_domain_execution import make_domain_run

    from backend.cockpit_v4.worker import Worker

    def _drive(domain_id: str, question: str, script):
        provider = ScriptedProvider(script)
        runtime.provider = provider
        record = make_domain_run(store_db, domain_id, question)
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
        return outcome, provider, record
    return _drive


@pytest.fixture
def book():
    """An execution service over one real book, with its declared joins.

    NOT the `service` fixture. That one is built on the legacy catalogue,
    which states no joins at all, so `multiplication_risk` is silent on it
    both before and after this change -- a test written against it would
    pass without exercising anything.
    """
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService

    def _open(domain_id: str, store=None):
        release_id = dom.DEFAULT_RELEASES[domain_id]
        catalog = cat.build(domain_id=domain_id,
                            tenant_id=lake.DEFAULT_TENANT,
                            release_id=release_id)
        return ExecutionService(
            session=cat.open_session(catalog=catalog), scope=None,
            catalog=catalog, store=store, run_id="r-grain",
            tenant_id=lake.DEFAULT_TENANT, release_id=release_id,
            limits=STANDARD_LIMITS)
    return _open


def submission(*steps):
    """One `execute_analysis` submission, parsed as the run parses it."""
    from backend.cockpit_v4.contracts import parse_execution

    return parse_execution({
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="totals by sector"),
        "objective": "totals by sector", "subquestions": ["totals"],
        "scope": {"reporting_months": [], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": list(steps), "repair_of_submission_id": ""}, max_steps=4)


def step(code: str, *, step_id: str = "s1", depends_on=()):
    return {"step_id": step_id, "language": "sql", "code": code,
            "parameters": {}, "purpose": f"{step_id} purpose",
            "input_artifact_ids": [], "depends_on_step_ids": list(depends_on)}


# ---- the rung, derived once ---------------------------------------------

def test_no_check_this_module_names_is_missing_from_the_phase_table():
    """The guard against the next `join_grain`.

    `join_grain` and `sandbox` were not classified wrongly on purpose; they
    were simply absent from a two-valued switch, and absence defaulted to
    "this query ran". A table that must be exhaustive turns the next
    omission into a failing test instead of a false trace.
    """
    named = {value for name, value in vars(xt).items()
             if name.startswith("CHECK_") and isinstance(value, str)}
    assert named, "no CHECK_* constants found; this test has lost its target"
    missing = named - set(xt.PHASE_OF_CHECK)
    assert not missing, f"checks with no rung: {sorted(missing)}"


@pytest.mark.parametrize("check", sorted(xt.REFUSED_BEFORE_EXECUTION))
def test_every_check_that_refuses_before_execution_says_nothing_ran(check):
    """`executed` is not a second judgement -- it is the rung, read again."""
    published = xt.StepResult(
        step_id="s1", status="failed", language="sql", code_digest="d",
        purpose="p", error_code=st.SQL_VALIDATION, failed_check=check,
        message="refused").to_dict()
    assert published["executed"] is False, (
        f"{check} refused before anything ran and reported that it ran")
    assert published["phase"] != xt.PHASE_RUNTIME


def test_each_refusal_is_on_the_rung_it_actually_reached():
    """Three rungs, and they are not interchangeable.

    A reader told "refused before it ran" and a reader told "did not bind"
    have been told different things, and an operator reading the trace of a
    step the batch never reached should not see either.
    """
    def rung(check: str) -> str:
        return xt.StepResult(step_id="s", status="failed", language="sql",
                             code_digest="d", purpose="p",
                             failed_check=check).to_dict()["phase"]

    assert rung(xt.CHECK_BIND) == xt.PHASE_BIND
    assert rung(xt.CHECK_GRAIN) == xt.PHASE_CHECK
    assert rung(xt.CHECK_SANDBOX) == xt.PHASE_CHECK
    assert rung(xt.CHECK_DOMAIN) == xt.PHASE_CHECK
    assert rung(xt.CHECK_DEPENDENCY) == xt.PHASE_NOT_STARTED
    assert rung(xt.CHECK_BATCH) == xt.PHASE_NOT_STARTED
    assert rung(xt.CHECK_RUNTIME) == xt.PHASE_RUNTIME


def test_a_check_this_module_has_never_heard_of_is_not_evidence_it_ran():
    """Fail closed. An unrecognised name is the one case where guessing
    "runtime" publishes a claim nobody made."""
    assert xt.phase_of("something_invented_later") == xt.PHASE_CHECK
    assert xt.phase_of("") == xt.PHASE_CHECK


# ---- the check runs before anything -------------------------------------

def test_a_fan_out_total_is_refused_by_validation_not_by_the_runner(book):
    """It is a judgement about the TEXT, so it needs no engine.

    It used to wait until `_run_sql`, which is after the step budget is
    spent and after the run has announced the query as validated.
    """
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(CORPORATE_FAN_OUT)))
    assert caught.value.code == st.SQL_VALIDATION
    assert caught.value.field_path == "steps.s1.code"


def test_the_refusal_names_the_join_both_grains_and_the_measure(book):
    """As DATA, not as a sentence to regex.

    The prose is assembled by f-string and will be re-worded; a test that
    reads the measure out of an English sentence passes when the sentence
    is wrong.
    """
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(CORPORATE_FAN_OUT)))
    detail = caught.value.detail

    assert detail["failed_check"] == xt.CHECK_GRAIN
    assert detail["phase"] == xt.PHASE_CHECK
    assert detail["many_relation"] == "corp_facility_quarter"
    assert detail["one_relation"] == "corp_borrower_quarter"
    assert detail["join_key"] == ["borrower_id", "reporting_quarter"]
    assert "total_debt_sar_mn" in detail["measures_at_risk"]
    for grain in ("many_grain", "one_grain"):
        assert detail[grain] and detail[grain] != "unknown", grain
    # A report, not a correction: which de-duplication is right depends on
    # what is being asked, and choosing one here would be choosing the
    # analysis.
    assert "does not choose" in detail["explanation"]


def test_an_explicit_de_duplication_is_not_refused(book):
    """False positives are the expensive failure.

    A query that de-duplicates the repeated side is correct, and refusing
    correct SQL costs the analyst a whole submission to discover that the
    check was wrong.
    """
    service = book(dom.CORPORATE)
    deduplicated = (
        "WITH one_row AS (SELECT DISTINCT borrower_id, reporting_quarter, "
        "sector, total_debt_sar_mn FROM corp_borrower_quarter) "
        "SELECT sector, SUM(total_debt_sar_mn) AS debt FROM one_row "
        "GROUP BY 1")
    service.validate_batch(submission(step(deduplicated)))


def test_a_join_the_catalogue_says_repeats_nothing_is_not_refused(book):
    """`retail_behaviour_month -> retail_account_month` is declared "one
    behaviour row to one account", and its own note says the join repeats
    nothing -- yet every declared pair was read as many-to-one, so summing
    an account's exposure across it was refused. The fact was already in the
    catalogue; the check was not reading it."""
    service = book(dom.RETAIL)
    service.validate_batch(submission(RETAIL_ONE_TO_ONE and
                                      step(RETAIL_ONE_TO_ONE)))


def test_a_many_to_one_retail_join_is_still_refused(book):
    """The cardinality skip is narrow. A customer total repeated once per
    account is the same trap in the other book."""
    service = book(dom.RETAIL)
    fan_out = ("SELECT SUM(c.total_ead_sar_mn) AS ead "
               "FROM retail_account_month a "
               "JOIN retail_customer_month c ON c.customer_id = a.customer_id "
               "AND c.reporting_month = a.reporting_month")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(fan_out)))
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN


# ---- deferred steps -----------------------------------------------------

def test_a_deferred_step_is_grain_checked_before_anything_runs(book):
    """The check needs no bound parameters and no earlier artifact, so it
    applies to a step that declares a dependency exactly as it applies to
    one that does not. Ordered after the bind proof it would not have: that
    one is skipped for deferred steps, and the refusal would then depend on
    whether a step happened to declare a dependency."""
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(
            step("SELECT 1 AS one", step_id="s1"),
            step(CORPORATE_FAN_OUT, step_id="s2", depends_on=["s1"])))
    assert caught.value.field_path == "steps.s2.code"
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN


def test_a_deferred_step_is_not_refused_merely_for_being_deferred(book):
    """Guards the over-refusal. A dependency is not a fan-out."""
    service = book(dom.CORPORATE)
    report = service.validate_batch(submission(
        step("SELECT 1 AS one", step_id="s1"),
        step("SELECT 2 AS two", step_id="s2", depends_on=["s1"])))
    assert report["bound_at_run_time"] == ["s2"]
    assert report["bound_now"] == ["s1"]


# ---- a step that really ran ---------------------------------------------

def test_a_step_that_really_ran_and_failed_still_says_it_ran(book, store_db):
    """The no-regression half. Narrowing what counts as "executed" must not
    swallow the case where the engine genuinely did the work."""
    service = book(dom.CORPORATE, store=store_db)
    batch = service.run_batch(submission(step(REALLY_RUNS_AND_FAILS)),
                              submission_id="sub-1", deadline_seconds=20.0)
    published = batch.steps[0].to_dict()
    assert published["phase"] == xt.PHASE_RUNTIME
    assert published["executed"] is True
    assert published["failed_check"] == xt.CHECK_RUNTIME


def test_a_step_the_batch_never_reached_does_not_report_that_it_executed(
        book, store_db):
    """`not_started` is its own rung because "a check refused it" would be a
    second untrue thing to say about a step nothing ever looked at."""
    service = book(dom.CORPORATE, store=store_db)
    batch = service.run_batch(
        submission(step(REALLY_RUNS_AND_FAILS, step_id="s1"),
                   step("SELECT 1 AS one", step_id="s2")),
        submission_id="sub-2", deadline_seconds=20.0)
    later = batch.steps[1].to_dict()
    assert later["status"] == "not_run"
    assert later["executed"] is False, (
        "a step the batch stopped before reaching reported that it ran")
    assert later["phase"] == xt.PHASE_NOT_STARTED


def test_a_python_step_with_no_runner_reports_that_nothing_ran(book):
    """Sandbox refusals are the other half of the same defect: V4 reports
    Python UNAVAILABLE rather than quietly evaluating it, and then said the
    step had executed."""
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(
            {"step_id": "s1", "language": "python", "code": "x = 1",
             "parameters": {}, "purpose": "p", "input_artifact_ids": [],
             "depends_on_step_ids": []}))
    assert caught.value.code == st.PYTHON_UNAVAILABLE
    assert caught.value.detail["failed_check"] == xt.CHECK_SANDBOX
    assert xt.phase_of(xt.CHECK_SANDBOX) == xt.PHASE_CHECK


# ---- what the run, the reader and the analyst are told ------------------

def _events(store, run_id):
    return list(store.events_since(run_id, 0))


def _grain_run(drive_domain):
    """One real run whose only submission is the fan-out."""
    return drive_domain(
        dom.CORPORATE, "What is total debt by sector?",
        [ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT",
                             understood="total debt by sector"),
            "objective": "total debt by sector",
            "subquestions": ["total debt by sector"],
            "scope": {"reporting_months": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": [],
            "expected_output_grain": "sector",
            "expected_units": "SAR million",
            "steps": [step(CORPORATE_FAN_OUT)],
            "repair_of_submission_id": ""}, "tu-1")])])


def test_the_run_never_says_it_validated_the_query_it_refused(drive_domain,
                                                              store_db):
    """ORDER, not presence.

    The refusal used to fire inside the runner, which is after
    `tool.validated` has already told the reader the query was checked and
    bound. Announcing a query as validated and then refusing it is two true
    statements in the wrong order, which is the same defect the bind proof
    was added to fix.
    """
    _outcome, _provider, record = _grain_run(drive_domain)
    kinds = [e.event_type for e in _events(store_db, record.run_id)]
    assert ev.TOOL_VALIDATED not in kinds, (
        "the run announced the query as validated and then refused it")
    stages = {e.stage for e in _events(store_db, record.run_id)}
    assert "executing" not in stages, (
        f"a refused submission reached the executing stage: {sorted(stages)}")


def test_the_refused_batch_stores_no_result(drive_domain, store_db):
    """Refusing above the runner means no artifact survives -- which is
    `validate_batch`'s own contract: a batch whose second step is wrong
    should not have run its first. Read from the artifacts table, not from
    the run's own report of itself."""
    _outcome, _provider, record = _grain_run(drive_domain)
    stored = store_db._connect().execute(
        "SELECT COUNT(*) AS n FROM artifacts WHERE run_id=?",
        (record.run_id,)).fetchone()
    assert stored["n"] == 0, (
        "a refused submission left a result behind; nothing ran")


def test_the_reader_is_told_it_was_refused_not_that_it_failed_while_running(
        drive_domain, store_db):
    """The sentence a person actually sees."""
    outcome, _provider, record = _grain_run(drive_domain)
    said = " ".join(e.public_message or ""
                    for e in _events(store_db, record.run_id))
    assert "refused before anything ran" in said, said
    assert "failed while running" not in said
    assert "Query validated and bound" not in said
    assert outcome.state in (st.FAILED, st.PARTIAL), outcome.state


def test_the_analyst_is_told_the_query_was_refused_not_that_it_ran(
        drive_domain):
    """The tool_result is what the analyst repairs from. Told `executed:
    true` about a batch that ran nothing, the honest repair is the wrong
    one."""
    _outcome, provider, _record = _grain_run(drive_domain)
    results = [m for m in provider.sent[-1]["messages"]
               if m.get("role") == "user"]
    body = json.dumps([m.get("content") for m in results])
    assert '"executed": true' not in body.replace('"executed":true',
                                                  '"executed": true')
    assert xt.CHECK_GRAIN in body


# ---- what validation claims ---------------------------------------------

def test_the_validation_message_does_not_claim_the_declared_grain_was_proven(
        drive_domain, store_db):
    """"Query validated and bound: N step(s), X grain, Y units" claimed
    three things and had earned one. The grain and the units are the
    analyst's own declared strings, echoed and never checked against a
    result nobody had yet seen."""
    month_sql = ("SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn "
                 "FROM corp_facility_quarter GROUP BY 1")
    _outcome, _provider, record = drive_domain(
        dom.CORPORATE, "What is exposure by sector?",
        [ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT",
                             understood="exposure by sector"),
            "objective": "exposure by sector",
            "subquestions": ["exposure by sector"],
            "scope": {"reporting_months": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": [],
            "expected_output_grain": "sector",
            "expected_units": "SAR million",
            "steps": [step(month_sql)],
            "repair_of_submission_id": ""}, "tu-1")])])

    validated = [e for e in _events(store_db, record.run_id)
                 if e.event_type == ev.TOOL_VALIDATED]
    assert validated, "the query was never announced as checked"
    said = validated[0].public_message or ""
    assert "executing nothing" in said, said
    assert "declared them and are not proven here" in said, said
    assert "Query validated and bound" not in said

    body = (store_db.get_detail(validated[0].detail_ref) or {}).get("body")
    assert body, "the operator record is missing"
    checks = body["checks_passed"]
    assert checks["join_grain"], "the grain check is unstated"
    # Two scanners key on the substring "authorization" -- the store's
    # redaction and the evidence-trace generator -- so a description that
    # spelled the check constant came back "[redacted]" or was refused
    # publication outright.
    assert "[redacted]" not in str(checks), (
        "a check description was mistaken for a credential")
    not_proven = " ".join(body["not_proven"])
    assert "grain and units" in not_proven
    assert "never checked" in not_proven


# ---- the join graph is parsed, not spelled ------------------------------

def test_a_subquery_that_repeats_nothing_is_not_refused(book):
    """Was a recorded limitation; is now a fixed defect.

    The check needed only two relation names somewhere in the text and an
    aggregate over an additive measure, so a subquery that repeats nothing
    was refused for a join it does not contain. `corp_facility_quarter` here
    supplies a membership test and contributes no rows to the total.
    """
    service = book(dom.CORPORATE)
    repeats_nothing = (
        "SELECT SUM(b.total_debt_sar_mn) AS debt FROM corp_borrower_quarter b "
        "WHERE b.borrower_id IN (SELECT borrower_id FROM "
        "corp_facility_quarter)")
    service.validate_batch(submission(step(repeats_nothing)))


def test_the_exact_live_submission_is_no_longer_refused(book):
    """The run that produced this change, replayed verbatim.

    Not an authored equivalent: the constant is the stored submission body,
    character for character. It was refused for "aggregating across the join
    between corp_covenant_quarter and corp_facility_quarter", and the join
    key the refusal quoted -- facility_id, reporting_quarter -- appears
    nowhere in it. Nor does the token JOIN.
    """
    assert "join" not in LIVE_SUBMISSION_S4.lower(), (
        "the fixture has been edited; it must contain no join")
    service = book(dom.CORPORATE)
    service.validate_batch(submission(step(LIVE_SUBMISSION_S4)))


def test_the_live_submission_executes_and_multiplies_nothing(book):
    """Unrefused is not the claim. The claim is that it was CORRECT.

    A check that stops refusing a query has proved nothing about the query.
    So: run it, run it again with both subqueries removed, and compare the
    facility figures. A subquery that fanned facility rows out would move
    every one of them.
    """
    from backend.cockpit_v4 import sql as v4_sql

    service = book(dom.CORPORATE)
    service.validate_batch(submission(step(LIVE_SUBMISSION_S4)))
    full = v4_sql.execute(LIVE_SUBMISSION_S4, service.session,
                          deadline_seconds=60.0)
    control = v4_sql.execute(LIVE_SUBMISSION_S4_WITHOUT_SUBQUERIES,
                             service.session, deadline_seconds=60.0)

    assert full.row_count == 2, full.rows
    facility_columns = ("sicr_flags", "default_flags", "avg_pd", "facs_dpd30")
    for row, bare in zip(full.rows, control.rows, strict=True):
        assert row["reporting_quarter"] == bare["reporting_quarter"]
        for column in facility_columns:
            assert row[column] == bare[column], (
                f"{column} moved when the subqueries were removed: "
                f"{row[column]} vs {bare[column]} -- the subqueries DO "
                f"multiply facility rows and the refusal was right")
        # And the subqueries returned something, so the comparison above is
        # not vacuously true of a query that read nothing.
        assert row["covenant_breaches"] > 0
        assert row["downgrades"] > 0


def test_the_word_distinct_in_a_comment_does_not_de_duplicate_anything(book):
    """The hatch, closed from the other side.

    The escape from a fan-out refusal used to be the word "distinct"
    anywhere in the statement. A comment satisfied it, and a genuinely
    double-counted total was published. DISTINCT is now read off the
    aggregate node, where it is either present or not.
    """
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(
            submission(step(FAN_OUT_WITH_DISTINCT_IN_A_COMMENT)))
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN


def test_a_distinct_aggregate_is_still_taken_at_its_word(book):
    """And the hatch still opens for the case it was written for."""
    service = book(dom.CORPORATE)
    service.validate_batch(
        submission(step(FAN_OUT_WITH_A_DISTINCT_AGGREGATE)))


def test_the_refusal_names_the_scope_it_judged(book):
    """Which scope, not merely which relations.

    A statement has several scopes and the judgement is about one of them.
    Reporting the tables that were joined TO EACH OTHER is what lets a
    reader check the refusal instead of believing it.
    """
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(CORPORATE_FAN_OUT)))
    assert caught.value.detail["joined_in_one_scope"] == [
        "corp_borrower_quarter", "corp_facility_quarter"]


def test_a_comma_join_is_a_join(book):
    """No JOIN keyword, the same cross product, the same double count."""
    service = book(dom.CORPORATE)
    comma = ("SELECT b.sector, SUM(b.total_debt_sar_mn) AS debt "
             "FROM corp_facility_quarter f, corp_borrower_quarter b "
             "WHERE b.borrower_id = f.borrower_id GROUP BY 1")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(comma)))
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN


def test_a_count_over_a_fan_out_is_refused_like_a_total(book):
    """A repeated row inflates a count exactly as it inflates a sum."""
    service = book(dom.CORPORATE)
    counted = ("SELECT COUNT(b.total_debt_sar_mn) AS n "
               "FROM corp_facility_quarter f "
               "JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(counted)))
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN


def test_two_runs_parsing_at_once_do_not_read_each_other_s_query(book):
    """One connection, one pending result.

    The parse is `execute` then `fetchone` on a shared connection. Without a
    lock around BOTH, two runs validating at the same moment can pick up
    each other's parse tree -- and a refusal or an all-clear then belongs to
    somebody else's query. Verdicts under concurrency must be the verdicts
    under isolation, every time.
    """
    import threading

    service = book(dom.CORPORATE)
    wrong: list[tuple[str, bool]] = []

    def hammer(code: str, refused: bool) -> None:
        for _ in range(40):
            try:
                service.validate_batch(submission(step(code)))
                seen = False
            except Rejection:
                seen = True
            if seen != refused:
                wrong.append((code[:24], seen))

    threads = [threading.Thread(target=hammer, args=args)
               for args in [(LIVE_SUBMISSION_S4, False),
                            (CORPORATE_FAN_OUT, True)] * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not wrong, f"verdicts crossed between queries: {wrong[:4]}"


def test_a_windowed_total_is_a_total(book):
    """`SUM(x) OVER (...)` adds up the same repeated rows.

    Reading only plain aggregates out of the parse would have quietly
    stopped refusing a case the text scan did refuse -- a fix that trades
    one false negative for another is not a fix.
    """
    service = book(dom.CORPORATE)
    windowed = (
        "SELECT f.facility_id, "
        "SUM(b.total_debt_sar_mn) OVER (PARTITION BY b.sector) AS debt "
        "FROM corp_facility_quarter f "
        "JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id")
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(windowed)))
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN
    assert "total_debt_sar_mn" in caught.value.detail["measures_at_risk"]


def test_a_measure_a_window_is_only_framed_BY_is_not_at_risk(book):
    """Partitioned by, not summed. Nothing is added up more than once."""
    service = book(dom.CORPORATE)
    framed = (
        "SELECT COUNT(f.facility_id) OVER (PARTITION BY b.total_debt_sar_mn) "
        "AS n FROM corp_facility_quarter f "
        "JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id")
    service.validate_batch(submission(step(framed)))


def test_the_relations_are_read_from_the_parse_and_not_from_a_comment(book):
    """A name in a comment is not a table this query reads."""
    service = book(dom.CORPORATE)
    mentioned_only = (
        "-- compare against corp_facility_quarter in a later step\n"
        "SELECT sector, SUM(total_debt_sar_mn) AS debt "
        "FROM corp_borrower_quarter GROUP BY 1")
    service.validate_batch(submission(step(mentioned_only)))


# ---- the repair: refused, corrected, completed --------------------------

#: The same total, de-duplicated. Every facility of a borrower repeats that
#: borrower's debt, so the correction takes the borrower side on its own --
#: one of the three de-duplications the refusal itself names.
CORPORATE_DEDUPLICATED = (
    "SELECT sector, SUM(total_debt_sar_mn) AS debt "
    "FROM corp_borrower_quarter GROUP BY 1 ORDER BY 2 DESC")


def _submission(code: str, *, step_id: str = "s1", call_id: str = "tu-1",
                objective: str = "total debt by sector"):
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT", understood=objective),
        "objective": objective, "subquestions": [objective],
        "scope": {"reporting_months": [], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": [],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": [step(code, step_id=step_id)],
        "repair_of_submission_id": ""}, call_id)


def _diagnostic(messages) -> dict:
    """The tool_result body the repairing turn actually read.

    Not the event, not the store -- the bytes handed to the model. A
    refusal the analyst cannot act on is a refusal that ends the run.
    """
    return json.loads(messages[-1]["content"][0]["content"])


def _repair_run(drive_domain, seen: dict):
    """Fan-out refused, corrected, answered. The shape of a working repair."""
    from test_domain_execution import answer_from

    def repair(messages):
        seen["diagnostic"] = _diagnostic(messages)
        return ScriptedResult(tool_calls=[
            _submission(CORPORATE_DEDUPLICATED, step_id="s2",
                        call_id="tu-2")])

    def finish(messages):
        return answer_from(messages, narrative="Total debt by sector.",
                           subquestion="total debt by sector")

    return drive_domain(
        dom.CORPORATE, "What is total debt by sector?",
        [ScriptedResult(tool_calls=[_submission(CORPORATE_FAN_OUT)]),
         repair, finish])


def test_a_refused_fan_out_is_repaired_by_the_model_and_the_run_completes(
        drive_domain, store_db):
    """THE point of refusing early rather than late.

    A fan-out caught at validation costs a submission and nothing else --
    no step budget, no round, no artifact to reconcile. So the analyst can
    simply write the query again, and the run finishes the question it was
    asked. This is the grain twin of the bind repair pinned in
    `test_protocol_and_bounds.py`, and it is what "repairable" has to mean:
    not that an error was returned, but that the run reached COMPLETED.
    """
    seen: dict = {}
    outcome, _provider, _record = _repair_run(drive_domain, seen)
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    assert outcome.response["numeric_claims"], "the answer published a figure"
    assert seen["diagnostic"], "the repairing turn read no diagnostic"


def test_the_diagnostic_the_repairing_turn_read_carries_its_evidence(
        drive_domain, store_db):
    """What is in the analyst's hands when it writes the correction.

    Asserted from the tool_result body, because that is the only thing the
    model sees. An event nobody reads and a store nobody queries are not
    how a repair gets authored.
    """
    seen: dict = {}
    outcome, _provider, _record = _repair_run(drive_domain, seen)
    assert outcome.state == st.COMPLETED, outcome.message
    body = seen["diagnostic"]

    assert body["status"] == "rejected"
    assert body["error_code"] == st.SQL_VALIDATION
    assert body["field"] == "steps.s1.code"
    detail = body["detail"]
    assert detail["failed_check"] == xt.CHECK_GRAIN
    assert detail["phase"] == xt.PHASE_CHECK
    # The facts, as data. A repair that has to regex an English sentence to
    # learn which measure was doubled is a repair written from a guess.
    assert detail["many_relation"] == "corp_facility_quarter"
    assert detail["one_relation"] == "corp_borrower_quarter"
    assert detail["join_key"] == ["borrower_id", "reporting_quarter"]
    assert "total_debt_sar_mn" in detail["measures_at_risk"]
    # And the three de-duplications that would be valid, so the correction
    # is a choice rather than a search.
    assert "aggregating one side before joining" in detail["explanation"]
    # What the runtime path has always said, and the validation path did not.
    assert "Author the corrected code yourself" in detail["note"]
    assert "no step budget was spent" in detail["note"]
    # How much room is left to do it in.
    assert body["budgets_remaining"]["execution_submissions"] == [1, 5]
    assert body["budgets_remaining"]["steps_attempted"] == [0, 12]


def test_both_the_refused_and_the_corrected_sql_came_from_the_model(
        drive_domain, store_db):
    """§25, restated for grain. CreditProbe supplies a diagnostic and
    nothing more: it never rewrites a query, drops a join or substitutes a
    de-duplication it thinks the analyst meant."""
    seen: dict = {}
    outcome, _provider, record = _repair_run(drive_domain, seen)
    assert outcome.state == st.COMPLETED, outcome.message

    rows = store_db._connect().execute(
        "SELECT status, payload FROM submissions WHERE run_id=? "
        "ORDER BY ordinal", (record.run_id,)).fetchall()
    codes = "\n".join(r["payload"] for r in rows)
    assert CORPORATE_FAN_OUT in codes, "the refused query is not on file"
    assert CORPORATE_DEDUPLICATED in codes, "the correction is not on file"
    assert any(r["status"] == "rejected" for r in rows)
    assert any(r["status"] == "ok" for r in rows), rows[-1]["status"]


def test_an_identical_resubmission_is_refused_again_and_the_run_lives(
        drive_domain, store_db):
    """A refusal is about THIS submission, never about the run.

    Worth pinning for a reason that is easy to get wrong: the rejected
    submission IS filed with a real no-progress key, but that is not what
    stops the second attempt. `no_progress_check` sits after
    `validate_batch`, so the identical query is refused again by the same
    check and never reaches it. Determinism stops it, not bookkeeping --
    and either way the analyst is still free to write something different.
    """
    from test_domain_execution import answer_from

    def finish(messages):
        return answer_from(messages, narrative="Total debt by sector.",
                           subquestion="total debt by sector")

    outcome, _provider, record = drive_domain(
        dom.CORPORATE, "What is total debt by sector?",
        [ScriptedResult(tool_calls=[_submission(CORPORATE_FAN_OUT)]),
         ScriptedResult(tool_calls=[
             _submission(CORPORATE_FAN_OUT, call_id="tu-2")]),
         ScriptedResult(tool_calls=[
             _submission(CORPORATE_DEDUPLICATED, step_id="s2",
                         call_id="tu-3")]),
         finish])
    assert outcome.state == st.COMPLETED, outcome.message

    spent = store_db.get_run(record.run_id).budget
    assert spent["execution_submissions"][0] == 3, (
        "each refusal costs a submission slot; that is what bounds the loop")
    assert spent["steps_attempted"][0] == 1, (
        "only the corrected submission ever reached a step")


# ---- refused before any step executes -----------------------------------

#: A four-step plan whose first three are ordinary and whose last fans out.
#:
#: AUTHORED, not replayed. The live Mac plan is not in this repository --
#: no SQL, no digest -- so this proves the STRUCTURAL claim and says so:
#: a plan is refused whole, and a good step early in it does not run
#: because a bad step later in it exists.
FOUR_STEP_PLAN = [
    ("s1", "SELECT sector, SUM(ead_sar_mn) AS ead "
           "FROM corp_facility_quarter GROUP BY 1"),
    ("s2", "SELECT sector, SUM(ecl_sar_mn) AS ecl "
           "FROM corp_facility_quarter GROUP BY 1"),
    ("s3", "SELECT stage, COUNT(*) AS facilities "
           "FROM corp_facility_quarter GROUP BY 1"),
    ("s4", CORPORATE_FAN_OUT),
]


def _four_step_run(drive_domain):
    steps = [step(code, step_id=step_id) for step_id, code in FOUR_STEP_PLAN]
    return drive_domain(
        dom.CORPORATE, "Exposure, loss, stage mix and debt by sector",
        [ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT",
                             understood="four views of the corporate book"),
            "objective": "four views of the corporate book",
            "subquestions": [code for _, code in FOUR_STEP_PLAN],
            "scope": {"reporting_months": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": [],
            "expected_output_grain": "sector",
            "expected_units": "SAR million",
            "steps": steps, "repair_of_submission_id": ""}, "tu-1")])])


def test_a_plan_whose_last_step_fans_out_runs_none_of_the_first_three(
        drive_domain, store_db):
    """`validate_batch`'s own contract, at four steps.

    "A batch whose fourth step names a table that does not exist should not
    have run its first three: a partial execution leaves artifacts an
    analyst may reasonably think are complete." The grain check is now one
    of the checks that sentence is about. Three of these four steps are
    perfectly good and none of them ran.

    AUTHORED SQL. This is the shape of the failure, not a replay of a
    particular one.
    """
    _outcome, _provider, record = _four_step_run(drive_domain)

    stored = store_db._connect().execute(
        "SELECT COUNT(*) AS n FROM artifacts WHERE run_id=?",
        (record.run_id,)).fetchone()
    assert stored["n"] == 0, (
        "a good step ran inside a plan that was refused as a whole")

    events = _events(store_db, record.run_id)
    assert ev.TOOL_VALIDATED not in [e.event_type for e in events]
    assert "executing" not in {e.stage for e in events}


def test_the_refusal_names_the_step_that_fans_out_and_not_the_first_one(
        drive_domain, store_db):
    """Four steps, one fault. An analyst told "steps.s1.code" would rewrite
    a query that is correct."""
    _outcome, _provider, record = _four_step_run(drive_domain)
    failed = [e for e in _events(store_db, record.run_id)
              if e.event_type == ev.TOOL_FAILED and e.stage == "validating"]
    assert failed, "no validation refusal was recorded"
    body = (store_db.get_detail(failed[0].detail_ref) or {}).get("body") or {}
    assert body["field"] == "steps.s4.code", body.get("field")
    assert body["failed_check"] == xt.CHECK_GRAIN
    # The whole plan is on file, not only the step that broke it.
    assert [s["step_id"] for s in body["steps"]] == ["s1", "s2", "s3", "s4"]


def test_a_refused_plan_spends_a_submission_and_no_step_budget(
        drive_domain, store_db):
    """The mechanical content of "before any step executes".

    `spend_steps` and `open_round` both sit after the re-raise, so a
    refused plan leaves them untouched. That is what makes a repair
    affordable: four steps were proposed and the run still has all twelve.
    """
    _outcome, _provider, record = _four_step_run(drive_domain)
    spent = store_db.get_run(record.run_id).budget

    assert spent["execution_submissions"][0] == 1, (
        "a refused submission is still a submission")
    assert spent["steps_attempted"][0] == 0, (
        "the step budget was charged for steps that never ran")
    assert spent["analysis_rounds"][0] == 0, (
        "a refused plan opened an analysis round")


# ---- a broken check is not a clean bill of health -----------------------

def test_a_grain_check_that_cannot_run_is_not_an_all_clear(book,
                                                           monkeypatch):
    """The arm that used to read `except Exception: risk = None`.

    A diagnostic that cannot complete must not return "no risk found" --
    that is the one answer a check may never give, because it is
    indistinguishable from the answer that lets a double-counted total
    through. It is an operator's problem and is reported as one, so the
    reader is pointed at somebody who can fix it rather than asked to
    rewrite SQL that may be perfectly correct.
    """
    from backend.cockpit_v4 import sql as v4_sql

    def broken(*_args, **_kwargs):
        raise RuntimeError("the catalogue join table could not be read")

    monkeypatch.setattr(v4_sql, "multiplication_risk", broken)
    service = book(dom.CORPORATE)
    with pytest.raises(Rejection) as caught:
        service.validate_batch(submission(step(CORPORATE_DEDUPLICATED)))

    assert caught.value.code == st.INTERNAL_ERROR, (
        "a broken check is not the analyst's fault and is not their fix")
    assert caught.value.detail["check_completed"] is False
    assert caught.value.detail["failed_check"] == xt.CHECK_GRAIN
    said = caught.value.message
    assert "was not run" in said and "not modified" in said, said
    assert "could not be completed" in said, said
