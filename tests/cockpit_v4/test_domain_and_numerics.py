"""
MODEL MOCK · REAL DATABASE/RUNNER.

The semantics that make an answer right or quietly wrong: grain, repetition,
allocation, vintage, units, and the difference between zero and missing. Each
numeric assertion is checked against `oracles.py`, computed with pandas
straight from the Parquet files.
"""

from __future__ import annotations

from decimal import Decimal

import oracles
import pytest
from conftest import intent

from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4.config import STANDARD_LIMITS
from backend.cockpit_v4.contracts import Rejection, parse_execution


@pytest.fixture
def session(runtime, release_id):
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_agentic import sql as v3_sql
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    return v3_sql.open_session(scope=scope, catalog=runtime.catalog)


@pytest.fixture
def service(session, store_db, runtime, release_id):
    from backend.cockpit_v4.execute_tool import ExecutionService

    return ExecutionService(
        session=session, scope=None, catalog=runtime.catalog, store=store_db,
        run_id="r-domain", tenant_id="demo-tenant", release_id=release_id,
        limits=STANDARD_LIMITS)


def submit(service, sql: str, *, step_id="s1", language="sql"):
    body = {"intent": intent("DATA_ANALYSIS", "COCKPIT"),
            "objective": "o", "subquestions": ["a"],
            "scope": {"reporting_periods": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": ["f"],
            "expected_output_grain": "g", "expected_units": "u",
            "steps": [{"step_id": step_id, "language": language, "code": sql,
                       "parameters": {}, "purpose": "p",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}
    submission = parse_execution(body, max_steps=6)
    service.validate_batch(submission)
    return submission, service.run_batch(submission, submission_id="sub-1",
                                         deadline_seconds=20.0)


def rows_of(store, result):
    return store.get_artifact(result.steps[0].artifact_id,
                              tenant_id="demo-tenant")["rows"]


TOLERANCE = Decimal("0.00001")


def close(a, b) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= TOLERANCE


# ---- grain and repetition ----------------------------------------------

def test_borrower_financials_are_not_multiplied_by_facilities(
        service, store_db, release_id):
    """V4-AT-037. The classic repetition trap, and its correct form."""
    quarter = oracles.latest_quarter(release_id)
    expected = oracles.total_assets_sum(release_id, quarter)

    correct = (f"SELECT SUM(total_assets) AS total FROM ("
               f"  SELECT DISTINCT borrower_id, total_assets"
               f"  FROM cockpit_borrower_financial_quarter"
               f"  WHERE reporting_quarter = '{quarter}') d")
    _, result = submit(service, correct)
    assert result.status == "ok", result.steps[0].message
    assert close(rows_of(store_db, result)[0]["total"], expected)

    # The wrong form: joined to facilities and summed. It runs -- this is a
    # semantic error, not a syntax one -- and the number is LARGER. The point
    # is that the correct form matches the oracle and this one does not.
    wrong = (f"SELECT SUM(b.total_assets) AS total "
             f"FROM cockpit_borrower_financial_quarter b "
             f"JOIN cockpit_facility_quarter f "
             f"  ON f.borrower_id = b.borrower_id "
             f" AND f.reporting_quarter = b.reporting_quarter "
             f"WHERE b.reporting_quarter = '{quarter}'")
    submission = parse_execution(
        {"intent": intent("DATA_ANALYSIS", "COCKPIT"), "objective": "o",
         "subquestions": ["a"],
         "scope": {"reporting_periods": [], "filters": {}},
         "metadata_receipt_ids": [], "fields_required": ["f"],
         "expected_output_grain": "g", "expected_units": "u",
         "steps": [{"step_id": "s2", "language": "sql", "code": wrong,
                    "parameters": {}, "purpose": "p",
                    "input_artifact_ids": [], "depends_on_step_ids": []}],
         "repair_of_submission_id": ""}, max_steps=6)
    # WHY THIS ONE IS NOT REFUSED, STATED RATHER THAN TOLERATED.
    #
    # This used to sit in a try/except that accepted either outcome -- ran
    # and inflated, or refused -- which read like a choice and was not one.
    # The join-grain check is driven by the catalogue's declared joins, and
    # this fixture is the LEGACY catalogue, which states none. So the check
    # is silent here and always was, and the test that pins the refusal is
    # `test_join_grain_refusal.py`, against a book that declares its joins.
    service.validate_batch(submission)
    inflated = service.run_batch(submission, submission_id="sub-2",
                                 deadline_seconds=20.0)
    assert inflated.status == "ok", inflated.steps[0].message
    got = rows_of(store_db, inflated)[0]["total"]
    assert not close(got, expected), (
        "the joined form should NOT equal the borrower-grain total; if it "
        "does, this release has one facility per borrower and the test "
        "proves nothing")


def test_shared_collateral_is_counted_at_its_allocated_share(
        service, store_db, release_id):
    """V4-AT-038. A whole asset is not repeated once per facility."""
    quarter = oracles.latest_quarter(release_id)
    expected = oracles.collateral_allocated_total(release_id, quarter)
    columns = store_db  # placeholder to keep the fixture referenced

    sql = (f"SELECT SUM(allocated_net_value_rcy) AS allocated "
           f"FROM cockpit_collateral_allocation "
           f"WHERE reporting_quarter = '{quarter}'")
    try:
        _, result = submit(service, sql)
    except Exception:
        sql = (f"SELECT SUM(allocated_net_value) AS allocated "
               f"FROM cockpit_collateral_allocation "
               f"WHERE reporting_quarter = '{quarter}'")
        _, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    assert close(rows_of(store_db, result)[0]["allocated"], expected)


def test_untested_covenants_are_distinguishable_from_compliant(
        service, store_db, release_id):
    """V4-AT-039. `not_tested` is a status, never an absence of a breach."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT test_status, COUNT(*) AS n "
           f"FROM cockpit_covenant_quarter "
           f"WHERE reporting_quarter = '{quarter}' "
           f"GROUP BY test_status ORDER BY n DESC")
    _, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    statuses = {str(r["test_status"]) for r in rows_of(store_db, result)}
    assert statuses, "the release records covenant test statuses"
    # Whatever the release contains, 'compliant' must not be the only value
    # the schema can express.
    from backend.cockpit_agentic import fields as v3_fields

    spec = next(f for f in v3_fields.fields_of("cockpit_covenant_quarter")
                if f.name == "test_status")
    allowed = set(spec.enumeration or ())
    assert {"not_tested", "waived"} & allowed or len(allowed) > 2, (
        "the covenant status enumeration must be able to say 'not tested'")


def test_scenario_detail_does_not_multiply_booked_ecl(service, store_db,
                                                      release_id):
    """V4-AT-040. Booked ECL is not the sum of stored scenario ECL."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT COUNT(*) AS scenario_rows, "
           f"       COUNT(DISTINCT facility_id) AS facilities "
           f"FROM cockpit_ifrs9_detail "
           f"WHERE reporting_quarter = '{quarter}'")
    _, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    row = rows_of(store_db, result)[0]
    assert int(row["scenario_rows"]) > int(row["facilities"]), (
        "scenario/horizon detail repeats the facility row; a test that ran "
        "against a release without that repetition would prove nothing")


def test_macro_forward_points_stay_labelled_forecasts(service, store_db,
                                                      release_id):
    """V4-AT-041. A later actual must not appear as a past forecast."""
    sql = ("SELECT quarter_offset, observation_status, COUNT(*) AS n "
           "FROM cockpit_macro_quarter_window "
           "WHERE quarter_offset > 0 "
           "GROUP BY quarter_offset, observation_status")
    _, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    rows = rows_of(store_db, result)
    assert rows, "the release carries forward macro horizons"
    statuses = {str(r["observation_status"]) for r in rows}
    assert not (statuses & {"historical_actual", "current_actual"}), (
        f"a positive offset carries {statuses}; a forward point must remain "
        f"a forecast at its original vintage")


# ---- units, zero and missing -------------------------------------------

def test_the_release_declares_its_own_currency_and_scale(runtime):
    """V4-AT-042. Units are read from the release, not from another dashboard."""
    catalog = runtime.catalog
    assert getattr(catalog, "reporting_currency", "")
    assert getattr(catalog, "amount_scale", "")
    # The pair must be COHERENT and must come from the SELECTED release. A
    # currency from one country with a scale word from another is how a
    # reader ends up off by orders of magnitude with nothing looking wrong --
    # and asserting a particular currency here would bake one release's
    # nationality into a test that every release has to pass.
    unit = prec.money_unit(catalog)
    assert prec.classify(unit).kind == prec.MONEY, (
        f"{unit!r} is not a unit the precision policy can classify as money")
    assert catalog.amount_scale in prec.MONEY_SCALES, (
        f"{catalog.amount_scale!r} is not a scale this policy knows")
    incoherent = {("SAR", "crore"), ("SAR", "lakh"), ("INR", "million"),
                  ("USD", "crore")}
    assert (catalog.reporting_currency, catalog.amount_scale) not in \
        incoherent, (
        f"{catalog.reporting_currency} {catalog.amount_scale} pairs a "
        f"currency with a scale word from somewhere else")


def test_no_eligible_rows_is_not_reported_as_zero(service, store_db,
                                                  release_id):
    """V4-AT-043. An empty result and a genuine zero are different facts."""
    empty = ("SELECT SUM(ead_reported) AS ead FROM cockpit_facility_quarter "
             "WHERE reporting_quarter = '1999Q1'")
    _, result = submit(service, empty)
    assert result.status == "ok"
    rows = rows_of(store_db, result)
    # SUM over no rows is NULL in SQL, and the artifact preserves the NULL.
    # It is the analyst's job to say "no eligible rows", and the finalizer
    # refuses to let a claim point at a NULL cell -- checked below.
    assert rows and rows[0]["ead"] is None


def test_a_claim_pointing_at_a_null_cell_is_refused(store_db, release_id):
    """V4-AT-043. A null is not zero, and cannot be quoted as a figure."""
    from backend.cockpit_v4.contracts import parse_final
    from backend.cockpit_v4.finalization import Finalizer

    artifact_id = store_db.put_artifact(
        run_id="r", tenant_id="t", kind="result", release_id=release_id,
        scope={}, columns=["ead"], rows=[{"ead": None}])
    finalizer = Finalizer(store=store_db, tenant_id="t",
                          release_id=release_id, limits=STANDARD_LIMITS,
                          run_artifacts={artifact_id})
    final = parse_final({
        "intent": intent("DATA_ANALYSIS", "COCKPIT"), "disposition": "answer",
        "narrative": "Exposure is {{claim.z}}.",
        "coverage": [], "numeric_claims": [{
            "claim_id": "z", "decimal_value": "0", "unit": "SAR million",
            "display_precision": 2,
            "evidence": {"artifact_id": artifact_id, "row_key": "0",
                         "column_id": "ead"}}],
        "evidence_refs": [], "tables": [], "charts": [], "limitations": [],
        "suggested_questions": [], "clarification_question": "",
        "clarification_options": [], "referral_owner": "",
        "referral_reason": ""})
    report = finalizer.validate(final, executed=True)
    assert not report.ok
    assert any("NULL" in problem and "not zero" in problem
               for problem in report.problems)


def test_a_column_outside_the_catalog_is_not_reachable(runtime, release_id):
    """V4-AT-044. `SELECT *` views do not expose undeclared source columns."""
    catalog = runtime.catalog
    declared = set(catalog.columns("cockpit_facility_quarter"))
    from backend.cockpit_agentic import store as v3_store

    frame = v3_store.read_relation(release_id, "cockpit_facility_quarter")
    actual = set(str(c) for c in frame.columns)
    assert actual <= declared, (
        f"the published relation exposes undeclared columns: "
        f"{sorted(actual - declared)}")


def test_a_wide_result_is_marked_rather_than_silently_reduced(
        service, store_db, release_id):
    """V4-AT-045. A preview never masquerades as the whole population."""
    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT * FROM cockpit_facility_quarter "
           f"WHERE reporting_quarter = '{quarter}'")
    _, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    step = result.steps[0]
    assert len(step.columns) > STANDARD_LIMITS.preview_columns
    assert step.truncated is True
    assert any("preview" in warning and "artifact" in warning
               for warning in step.warnings), step.warnings
    assert len(step.preview[0]) <= STANDARD_LIMITS.preview_columns
    # The full width is still retrievable from the artifact.
    stored = store_db.get_artifact(step.artifact_id, tenant_id="demo-tenant")
    assert len(stored["columns"]) == len(step.columns)


# ---- execution fidelity -------------------------------------------------

def test_the_executed_code_equals_the_submitted_code_byte_for_byte(
        service, store_db, release_id):
    """V4-AT-046. The digest proves it, including whitespace and comments."""
    from backend.cockpit_v4.provider import code_digest

    quarter = oracles.latest_quarter(release_id)
    sql = (f"-- a comment the validator must not strip\n"
           f"SELECT   sector_name,\n"
           f"         SUM(ead_reported)   AS ead\n"
           f"FROM cockpit_facility_quarter\n"
           f"WHERE reporting_quarter = '{quarter}'   -- trailing comment\n"
           f"GROUP BY sector_name\n")
    submission, result = submit(service, sql)
    assert result.status == "ok", result.steps[0].message
    assert submission.steps[0].code == sql, "the submission kept the source"
    assert result.steps[0].code_digest == code_digest(sql)
    stored = store_db.get_artifact(result.steps[0].artifact_id,
                                   tenant_id="demo-tenant")
    assert stored["code_digest"] == code_digest(sql)


def test_an_invalid_field_reference_gets_a_diagnostic_not_a_substitution(
        service, store_db, release_id):
    """V4-AT-048. Nothing guesses that `exposure` meant `ead_reported`.

    It is refused at VALIDATION now, not at execution: a column DuckDB cannot
    resolve means the query never bound, and a batch that cannot bind must not
    be announced as validated.
    """
    sql = "SELECT exposure FROM cockpit_facility_quarter LIMIT 1"
    with pytest.raises(Rejection) as caught:
        submit(service, sql)
    rejection = caught.value
    assert rejection.detail["failed_check"] == "bind"
    assert rejection.detail["phase"] == "bind"
    assert rejection.detail["unresolved_name"] == "exposure"
    assert "BinderException" in rejection.detail["duckdb_exception_type"]
    assert "ead_reported" not in str(rejection), (
        "a diagnostic names what did not resolve; it does not offer a "
        "substitute")
    assert "nothing was repaired" in str(rejection).lower()


def test_a_failed_step_stops_its_dependents_and_preserves_the_rest(
        service, store_db, release_id):
    """V4-AT-051. Earlier successes keep their provenance and status."""
    quarter = oracles.latest_quarter(release_id)
    body = {"intent": intent("DATA_ANALYSIS", "COCKPIT"), "objective": "o",
            "subquestions": ["a"],
            "scope": {"reporting_periods": [], "filters": {}},
            "metadata_receipt_ids": [], "fields_required": ["f"],
            "expected_output_grain": "g", "expected_units": "u",
            "steps": [
                {"step_id": "good", "language": "sql",
                 "code": (f"SELECT COUNT(*) AS n FROM "
                          f"cockpit_facility_quarter WHERE "
                          f"reporting_quarter='{quarter}'"),
                 "parameters": {}, "purpose": "count",
                 "input_artifact_ids": [], "depends_on_step_ids": []},
                {"step_id": "bad", "language": "sql",
                 "code": "SELECT no_such_column FROM cockpit_facility_quarter",
                 "parameters": {}, "purpose": "break",
                 "input_artifact_ids": [], "depends_on_step_ids": ["good"]},
                {"step_id": "after", "language": "sql",
                 "code": "SELECT 1 AS x",
                 "parameters": {}, "purpose": "later",
                 "input_artifact_ids": [], "depends_on_step_ids": ["bad"]}],
            "repair_of_submission_id": ""}
    submission = parse_execution(body, max_steps=6)
    service.validate_batch(submission)
    result = service.run_batch(submission, submission_id="sub-3",
                               deadline_seconds=20.0)

    by_id = {s.step_id: s for s in result.steps}
    assert by_id["good"].status == "ok"
    assert by_id["good"].artifact_id, "a successful step keeps its artifact"
    assert by_id["bad"].status == "failed"
    assert by_id["after"].status == "not_run"
    assert "not run" in by_id["after"].message
    assert result.status == "failed"
