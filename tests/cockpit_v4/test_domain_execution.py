"""MODEL MOCK · REAL DATABASE/RUNNER · INDEPENDENT ORACLE.

Two books, executed. The round this replaces refused Retail questions rather
than letting one run against corporate relations; these are the regressions
that make the refusal unnecessary.

What is scripted and what is not
--------------------------------
The model is scripted, so nothing here claims anything about answer quality.
Everything else is real: the run is persisted with its domain and release, the
worker resolves the book from THAT record, a real DuckDB session is
materialized from that book's own parquet, the submitted SQL runs in it, and
every published figure is checked against a pandas oracle that never touches
the catalogue, the session or the product's SQL.

The isolation cases are the point of the file. A corporate run that names a
retail relation must be refused by name; a retail run must never see a
corporate relation; and the two runs must not be able to reach each other's
numbers through a cache, a session or a release header.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

TOLERANCE = 1e-6

#: How many rows a "which are the biggest" question asks for.
#:
#: The Corporate book holds three thousand borrowers and sixteen hundred
#: groups. A query with no LIMIT against it returns a result whose PREVIEW is
#: capped, and comparing a capped preview against a complete oracle finds a
#: difference that is the cap and calls it an error. A top-N question asks
#: for a top N.
TOP_N = 25


def top_n(values: dict[str, float], n: int = TOP_N) -> dict[str, float]:
    """The oracle, sliced exactly as the query slices it."""
    return dict(sorted(values.items(),
                       key=lambda kv: (-float(kv[1]), kv[0]))[:n])


@pytest.fixture(scope="module", autouse=True)
def _both_books_published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


# ---- driving one run in one book ---------------------------------------

def make_domain_run(store_db, domain_id: str, question: str, *,
                    tenant: str = lake.DEFAULT_TENANT,
                    principal: str = "u1", mode: str = "standard"):
    """A run persisted exactly as the API persists one, book and all."""
    from backend.cockpit_v4 import domain_resolver as resolver

    scope = resolver.scope_for(domain_id, tenant_id=tenant)
    thread_id = store_db.create_thread(
        tenant_id=tenant, principal_id=principal, domain_id=scope.domain_id,
        release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id=tenant, principal_id=principal,
        question=question, mode=mode, release_id=scope.release_id,
        domain_id=scope.domain_id,
        release_fingerprint=scope.release_fingerprint,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")
    return record


@pytest.fixture
def drive_domain(store_db, runtime):
    """Run one scripted conversation, in one book, through the real worker."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    def _drive(domain_id: str, question: str, script, *,
               tenant: str = lake.DEFAULT_TENANT, record=None):
        provider = ScriptedProvider(script)
        runtime.provider = provider
        record = record or make_domain_run(store_db, domain_id, question,
                                           tenant=tenant)
        outcome = Worker(store=store_db, runtime=runtime).execute(record)
        return outcome, provider, record
    return _drive


def execute_call(sql: str, *, purpose: str, grain: str, units: str,
                 subquestions, fields, month: str, call_id="tu-x",
                 step_id="s1"):
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT", understood=purpose,
                         rationale="direct read"),
        "objective": purpose, "subquestions": list(subquestions),
        "scope": {"reporting_months": [month], "filters": {}},
        "metadata_receipt_ids": [], "fields_required": list(fields),
        "expected_output_grain": grain, "expected_units": units,
        "steps": [{"step_id": step_id, "language": "sql", "code": sql,
                   "parameters": {}, "purpose": purpose,
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, call_id)


def answer_from(messages, *, narrative: str, subquestion: str,
                seen: list | None = None):
    """An answer built from whatever the execution actually returned."""
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    artifact = step["artifact_id"]
    if seen is not None:
        seen.append(artifact)
    row = step["preview"][0]
    column = next(c for c in step["columns"]
                  if isinstance(row[c], (int, float)))
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative=narrative + " The figure is {{claim.v}}.",
              coverage=[{"subquestion": subquestion, "status": "answered",
                         "evidence_refs": [{"artifact_id": artifact,
                                            "row_key": "r0",
                                            "column_id": column}]}],
              numeric_claims=[{
                  "claim_id": "v", "decimal_value": str(row[column]),
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact, "row_key": "r0",
                               "column_id": column}}],
              tables=[{"title": subquestion, "artifact_id": artifact,
                       "columns": list(row)}]))])


def rows_of(store_db, record, artifact_id: str):
    """Every row of the run's result artifact, read back from the store.

    Read from the artifact rather than the preview, so the check is against
    what was stored for the reader and not against a truncated echo of it.
    """
    run = store_db.get_run(record.run_id)
    assert run.state == st.COMPLETED, run.error_code
    stored = store_db.get_artifact(artifact_id, tenant_id=record.tenant_id)
    assert stored is not None, "the result artifact was not readable"
    return stored


def run_case(drive_domain, store_db, *, domain_id: str, question: str,
             sql: str, fields, purpose: str, grain: str, units: str,
             month: str):
    seen: list[str] = []
    outcome, provider, record = drive_domain(domain_id, question, [
        ScriptedResult(tool_calls=[execute_call(
            sql, purpose=purpose, grain=grain, units=units,
            subquestions=[purpose], fields=fields, month=month)]),
        lambda m: answer_from(m, narrative=purpose, subquestion=purpose,
                              seen=seen)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    return Case(outcome=outcome, provider=provider, record=record,
                artifact_id=seen[0], rows=rows_of(store_db, record, seen[0]))


@dataclass
class Case:
    """What one executed case produced, for the assertions that follow."""

    outcome: Any
    provider: Any
    record: Any
    artifact_id: str
    rows: dict


def keyed(payload, key: str, value: str) -> dict[str, float]:
    return {str(r[key]): float(r[value]) for r in payload["rows"]}


# ---- C01-C05: the Corporate book ---------------------------------------

def test_c01_corporate_ead_by_sector(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="What is exposure at default by sector this month?",
        sql=f"SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn "
            f"FROM corp_facility_quarter WHERE reporting_quarter = '{month}' "
            f"GROUP BY sector ORDER BY ead_sar_mn DESC",
        fields=["corp_facility_quarter.ead_sar_mn",
                "corp_facility_quarter.sector"],
        purpose="EAD by sector", grain="sector", units="SAR million",
        month=month)
    produced = keyed(case.rows, "sector", "ead_sar_mn")
    expected = oracle.corp_ead_by_sector(month)
    assert set(produced) == set(expected)
    for sector, value in expected.items():
        assert produced[sector] == pytest.approx(value, abs=TOLERANCE)


def test_c02_corporate_stage_two_share_by_sector(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="What share of exposure is in stage 2 or worse, by sector?",
        sql=f"SELECT sector, SUM(CASE WHEN stage >= 2 THEN ead_sar_mn "
            f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
            f"FROM corp_facility_quarter WHERE reporting_quarter = '{month}' "
            f"GROUP BY sector ORDER BY stage2_share DESC",
        fields=["corp_facility_quarter.ead_sar_mn",
                "corp_facility_quarter.stage", "corp_facility_quarter.sector"],
        purpose="Stage 2+ share of EAD by sector", grain="sector",
        units="percent", month=month)
    produced = keyed(case.rows, "sector", "stage2_share")
    for sector, value in oracle.corp_stage2_share_by_sector(month).items():
        assert produced[sector] == pytest.approx(value, abs=TOLERANCE)


def test_c03_corporate_ecl_by_product_type(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="What is recognised ECL by facility type?",
        sql=f"SELECT product_type, SUM(ecl_sar_mn) AS ecl_sar_mn "
            f"FROM corp_facility_quarter WHERE reporting_quarter = '{month}' "
            f"GROUP BY product_type ORDER BY ecl_sar_mn DESC",
        fields=["corp_facility_quarter.ecl_sar_mn",
                "corp_facility_quarter.product_type"],
        purpose="ECL by facility type", grain="facility type",
        units="SAR million", month=month)
    produced = keyed(case.rows, "product_type",
                     "ecl_sar_mn")
    for key, value in oracle.corp_ecl_by_product_type(month).items():
        assert produced[key] == pytest.approx(value, abs=TOLERANCE)


def test_c04_corporate_quarter_on_quarter_exposure_movement(drive_domain,
                                                            store_db):
    latest = oracle.latest_period(dom.CORPORATE)
    previous = oracle.previous_period(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="How did total exposure move over the latest quarter?",
        sql=f"SELECT SUM(CASE WHEN reporting_quarter = '{latest}' "
            f"THEN ead_sar_mn ELSE 0 END) "
            f"- SUM(CASE WHEN reporting_quarter = '{previous}' "
            f"THEN ead_sar_mn ELSE 0 END) AS ead_movement_sar_mn "
            f"FROM corp_facility_quarter "
            f"WHERE reporting_quarter IN ('{latest}', '{previous}')",
        fields=["corp_facility_quarter.ead_sar_mn"],
        purpose="EAD movement over the latest quarter", grain="portfolio",
        units="SAR million", month=latest)
    payload = case.rows
    produced = float(payload["rows"][0]["ead_movement_sar_mn"])
    assert produced == pytest.approx(
        oracle.corp_ead_movement(latest, previous), abs=1e-4)


def test_c05_corporate_exposure_by_borrower_does_not_multiply(drive_domain,
                                                              store_db):
    """The join case. EAD is summed at facility grain, then named."""
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="Which borrowers hold the most exposure?",
        sql=f"WITH by_borrower AS ("
            f"  SELECT borrower_id, SUM(ead_sar_mn) AS ead_sar_mn"
            f"  FROM corp_facility_quarter WHERE reporting_quarter = '{month}'"
            f"  GROUP BY borrower_id) "
            f"SELECT b.borrower_name, t.ead_sar_mn FROM by_borrower t "
            f"JOIN corp_borrower_quarter b ON b.borrower_id = t.borrower_id "
            f"AND b.reporting_quarter = '{month}' "
            # "The MOST exposure" is a top-N question, and this book holds
            # three thousand borrowers. Asking for all of them and reading
            # the first hundred rows of the preview would compare a truncated
            # answer against a complete oracle and call the difference a
            # defect. The query says what it wants.
            f"ORDER BY t.ead_sar_mn DESC, b.borrower_name LIMIT {TOP_N}",
        fields=["corp_facility_quarter.ead_sar_mn",
                "corp_borrower_quarter.borrower_name"],
        purpose="EAD by borrower", grain="borrower", units="SAR million",
        month=month)
    produced = keyed(case.rows, "borrower_name",
                     "ead_sar_mn")
    expected = top_n(oracle.corp_ead_by_borrower(month))
    assert set(produced) == set(expected)
    for name, value in expected.items():
        assert produced[name] == pytest.approx(value, abs=TOLERANCE)


# ---- R01-R06: the Retail book ------------------------------------------

def test_r01_retail_ead_by_product(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="What is exposure at default by product this month?",
        sql=f"SELECT product, SUM(ead_sar_mn) AS ead_sar_mn "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY product ORDER BY ead_sar_mn DESC",
        fields=["retail_account_month.ead_sar_mn",
                "retail_account_month.product"],
        purpose="EAD by product", grain="product", units="SAR million",
        month=month)
    produced = keyed(case.rows, "product", "ead_sar_mn")
    expected = oracle.retail_ead_by_product(month)
    assert set(produced) == set(expected)
    for product, value in expected.items():
        assert produced[product] == pytest.approx(value, abs=1e-4)


def test_r02_retail_stage_two_share_by_product(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="What share of retail exposure is in stage 2 or worse?",
        sql=f"SELECT product, SUM(CASE WHEN stage >= 2 THEN ead_sar_mn "
            f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY product ORDER BY stage2_share DESC",
        fields=["retail_account_month.ead_sar_mn",
                "retail_account_month.stage",
                "retail_account_month.product"],
        purpose="Stage 2+ share of EAD by product", grain="product",
        units="percent", month=month)
    produced = keyed(case.rows, "product", "stage2_share")
    for product, value in oracle.retail_stage2_share_by_product(month).items():
        assert produced[product] == pytest.approx(value, abs=TOLERANCE)


def test_r03_retail_ecl_by_score_band(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="What is ECL by behaviour score band?",
        sql=f"SELECT score_band, SUM(ecl_sar_mn) AS ecl_sar_mn "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY score_band ORDER BY ecl_sar_mn DESC",
        fields=["retail_account_month.ecl_sar_mn",
                "retail_account_month.score_band"],
        purpose="ECL by score band", grain="score band",
        units="SAR million", month=month)
    produced = keyed(case.rows, "score_band", "ecl_sar_mn")
    for band, value in oracle.retail_ecl_by_score_band(month).items():
        assert produced[band] == pytest.approx(value, abs=1e-4)


def test_r04_retail_delinquent_share_by_product(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How much retail exposure is past due, by product?",
        sql=f"SELECT product, SUM(CASE WHEN dpd_days > 0 THEN ead_sar_mn "
            f"ELSE 0 END) / NULLIF(SUM(ead_sar_mn), 0) AS past_due_share "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY product ORDER BY past_due_share DESC",
        fields=["retail_account_month.ead_sar_mn",
                "retail_account_month.dpd_days",
                "retail_account_month.product"],
        purpose="Past-due share of EAD by product", grain="product",
        units="percent", month=month)
    produced = keyed(case.rows, "product", "past_due_share")
    for product, value in \
            oracle.retail_delinquent_share_by_product(month).items():
        assert produced[product] == pytest.approx(value, abs=TOLERANCE)


def test_r05_retail_write_offs_by_product(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="What was written off this month, by product?",
        sql=f"SELECT product, SUM(write_off_sar_mn) AS write_off_sar_mn "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY product ORDER BY write_off_sar_mn DESC",
        fields=["retail_account_month.write_off_sar_mn",
                "retail_account_month.product"],
        purpose="Write-offs by product", grain="product",
        units="SAR million", month=month)
    produced = keyed(case.rows, "product",
                     "write_off_sar_mn")
    for product, value in oracle.retail_write_off_by_product(month).items():
        assert produced[product] == pytest.approx(value, abs=1e-4)


def test_r06_retail_exposure_by_origination_vintage(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How is exposure spread across origination vintages?",
        sql=f"SELECT CAST(vintage_year AS VARCHAR) AS vintage_year, "
            f"SUM(ead_sar_mn) AS ead_sar_mn "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY vintage_year ORDER BY vintage_year",
        fields=["retail_account_month.ead_sar_mn",
                "retail_account_month.vintage_year"],
        purpose="EAD by origination vintage", grain="vintage year",
        units="SAR million", month=month)
    produced = keyed(case.rows, "vintage_year", "ead_sar_mn")
    expected = oracle.retail_ead_by_vintage(month)
    assert set(produced) == set(expected)
    for vintage, value in expected.items():
        assert produced[vintage] == pytest.approx(value, abs=1e-4)


# ---- the isolation cases -----------------------------------------------

CROSS = {
    dom.CORPORATE: ("retail_account_month", "retail"),
    dom.RETAIL: ("corp_facility_quarter", "corporate"),
}


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_run_naming_the_other_books_relation_is_refused_by_name(
        drive_domain, store_db, domain_id):
    """The defect the whole domain model exists to prevent.

    Not "returned no rows" and not "unknown table": refused, with the owning
    book named, before anything ran.
    """
    relation, owner = CROSS[domain_id]
    month = oracle.latest_month(domain_id)
    outcome, _provider, record = drive_domain(
        domain_id, "What is exposure in the other book?", [
            ScriptedResult(tool_calls=[execute_call(
                f"SELECT SUM(ead_sar_mn) AS ead_sar_mn FROM {relation} "
                f"WHERE {oracle.period_column(owner)} = '{month}'",
                purpose="Exposure", grain="portfolio", units="SAR million",
                subquestions=["Exposure"], fields=[f"{relation}.ead_sar_mn"],
                month=month)]),
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                      disposition="unsupported",
                      narrative=("That relation belongs to the other book "
                                 "and was not read.")),
                "tu-2")])])

    assert outcome.state in (st.UNSUPPORTED, st.COMPLETED, st.PARTIAL)
    assert not outcome.response.get("executed"), (
        "a query naming the other book's relation must not report as "
        "executed")
    events = store_db.events_since(record.run_id, 0)
    text = json.dumps([e.to_dict() if hasattr(e, "to_dict") else e
                       for e in events], default=str)
    assert dom.LABELS[owner] in text, (
        "the refusal must name the book the relation belongs to, and this "
        f"run said: {text[-2000:]}")
    assert relation not in json.dumps(
        store_db.get_run(record.run_id).budget, default=str)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_session_holds_one_books_relations_and_no_others(domain_id):
    book = arun.for_domain(domain_id)
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    theirs = set(arun.for_domain(other).catalog.relations())
    mine = set(book.session.relations)
    assert mine == set(book.catalog.relations())
    assert not (mine & theirs)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_starting_context_names_only_this_books_relations(drive_domain,
                                                              domain_id):
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    theirs = arun.for_domain(other).catalog.relations()
    _outcome, provider, _record = drive_domain(
        domain_id, "What can you tell me about this portfolio?", [
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent(), disposition="answer",
                      narrative="hello"))])])
    sent = provider.first_input_text()
    for relation in arun.for_domain(domain_id).catalog.relations():
        assert relation in sent, f"{relation} is missing from the packet"
    for relation in theirs:
        assert relation not in sent, (
            f"{relation} belongs to the other book and reached the packet")
    assert dom.LABELS[domain_id] in sent


def test_the_packet_says_which_book_and_which_fingerprint(drive_domain):
    _o, provider, _r = drive_domain(dom.RETAIL, "What is this?", [
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent(), disposition="answer", narrative="hi"))])])
    sent = provider.first_input_text()
    book = arun.for_domain(dom.RETAIL)
    assert book.release_id in sent
    assert book.release_fingerprint[:16] in sent
    assert "reporting_months" in sent, (
        "a monthly book must not describe its periods as quarters")


# ---- cache contamination (five orderings) -------------------------------

ORDERINGS = [
    (dom.CORPORATE, dom.RETAIL),
    (dom.RETAIL, dom.CORPORATE),
    (dom.CORPORATE, dom.RETAIL, dom.CORPORATE),
    (dom.RETAIL, dom.CORPORATE, dom.RETAIL),
    (dom.CORPORATE, dom.CORPORATE, dom.RETAIL, dom.RETAIL),
]


@pytest.mark.parametrize("ordering", ORDERINGS,
                         ids=["cr", "rc", "crc", "rcr", "ccrr"])
def test_no_ordering_lets_one_book_be_served_from_the_others_cache(
        drive_domain, store_db, ordering):
    """Whichever book was opened first must not decide what the next reads."""
    arun.reset()
    for domain_id in ordering:
        month = oracle.latest_month(domain_id)
        if domain_id == dom.CORPORATE:
            relation, dimension = "corp_facility_quarter", "sector"
            expected = oracle.corp_ead_by_sector(month)
        else:
            relation, dimension = "retail_account_month", "product"
            expected = oracle.retail_ead_by_product(month)
        case = run_case(
            drive_domain, store_db, domain_id=domain_id,
            question=f"EAD by {dimension}?",
            sql=f"SELECT {dimension}, SUM(ead_sar_mn) AS ead_sar_mn "
                f"FROM {relation} "
                f"WHERE {oracle.period_column(domain_id)} = '{month}' "
                f"GROUP BY {dimension}",
            fields=[f"{relation}.ead_sar_mn", f"{relation}.{dimension}"],
            purpose=f"EAD by {dimension}", grain=dimension,
            units="SAR million", month=month)
        produced = keyed(case.rows, dimension, "ead_sar_mn")
        assert set(produced) == set(expected), (
            f"{domain_id} returned the wrong book's segments in ordering "
            f"{ordering}")
        for key, value in expected.items():
            assert produced[key] == pytest.approx(value, abs=1e-4)
        assert case.rows["release_id"] == dom.DEFAULT_RELEASES[domain_id]
        assert case.rows["scope"]["domain_id"] == domain_id


# ---- the run's own record decides --------------------------------------

def test_a_rebuilt_release_refuses_the_run_rather_than_answering_it(
        drive_domain, store_db):
    """The fingerprint is the control, not the release name."""
    record = make_domain_run(store_db, dom.RETAIL, "What is EAD?")
    stale = record.__class__(**{**record.__dict__,
                                "release_fingerprint": "0" * 64})
    outcome, provider, _r = drive_domain(
        dom.RETAIL, "What is EAD?", [], record=stale)
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.DATA_UNAVAILABLE
    assert "rebuilt" in outcome.message
    assert provider.sent == [], "a refused book must cost no model call"


def test_a_label_that_disagrees_with_the_release_is_refused(drive_domain,
                                                            store_db):
    record = make_domain_run(store_db, dom.RETAIL, "What is EAD?")
    mislabelled = record.__class__(
        **{**record.__dict__, "domain_id": dom.CORPORATE})
    outcome, provider, _r = drive_domain(
        dom.RETAIL, "What is EAD?", [], record=mislabelled)
    assert outcome.state == st.FAILED
    assert outcome.error_code == st.DATA_UNAVAILABLE
    assert "disagree" in outcome.message
    assert provider.sent == []


def test_a_follow_up_reads_the_same_book_as_the_question_before_it(
        drive_domain, store_db):
    """Two runs, one thread. The second must not re-derive its book."""
    first = make_domain_run(store_db, dom.RETAIL, "What is EAD by product?")
    month = oracle.latest_month(dom.RETAIL)
    sql = (f"SELECT product, SUM(ead_sar_mn) AS ead_sar_mn "
           f"FROM retail_account_month WHERE reporting_month = '{month}' "
           f"GROUP BY product")
    for _ in range(2):
        record, _created = store_db.accept_run(
            thread_id=first.thread_id, tenant_id=first.tenant_id,
            principal_id=first.principal_id, question="And now?",
            mode="standard", release_id=first.release_id,
            domain_id=first.domain_id,
            release_fingerprint=first.release_fingerprint,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="testsha", deadline_at="")
        case = run_case(
            drive_domain, store_db, domain_id=dom.RETAIL,
            question="And now?", sql=sql,
            fields=["retail_account_month.ead_sar_mn",
                    "retail_account_month.product"],
            purpose="EAD by product", grain="product", units="SAR million",
            month=month)
        assert case.rows["scope"]["domain_id"] == dom.RETAIL
        assert case.rows["release_id"] == dom.DEFAULT_RELEASES[dom.RETAIL]
        assert set(keyed(case.rows, "product", "ead_sar_mn")) == set(
            oracle.retail_ead_by_product(month))
        del record


# ---- seeded investigations carry the book with them ---------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_seeded_thread_executes_in_the_book_the_card_came_from(
        drive_domain, store_db, domain_id):
    """§13, §14. Investigate Further opens a thread, not a question.

    The seed names a segment, a period and a movement that exist in ONE
    release. A run in that thread must reach that release and no other, and
    the case file it is handed must describe the same book.
    """
    from backend.cockpit_v4 import attention_v2 as att
    from backend.cockpit_v4 import domain_resolver as resolver

    scope = resolver.scope_for(domain_id)
    book = arun.for_domain(domain_id)
    feed = att.compute(session=book.session, scope=scope)
    item = feed["segments_requiring_attention"][0]

    thread_id = store_db.create_thread(
        tenant_id=lake.DEFAULT_TENANT, principal_id="u1",
        domain_id=domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    store_db.set_thread_context(
        thread_id, tenant_id=lake.DEFAULT_TENANT, kind="attention_item",
        body={"item_id": item["item_id"], "domain_id": domain_id,
              "release_id": scope.release_id,
              "release_fingerprint": scope.release_fingerprint,
              "segment": item["segment"], "metric": item["metric"],
              "headline": item["headline"],
              "reporting_period": item["reporting_period"],
              "comparison_period": item["comparison_period"]})
    record, _created = store_db.accept_run(
        thread_id=thread_id, tenant_id=lake.DEFAULT_TENANT,
        principal_id="u1", question="Show me what is behind this.",
        mode="standard", release_id=scope.release_id, domain_id=domain_id,
        release_fingerprint=scope.release_fingerprint, ui_filters={},
        idempotency_key="", body_digest="", startup_sha="testsha",
        deadline_at="")

    _outcome, provider, _record = drive_domain(
        domain_id, "Show me what is behind this.", [
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent(), disposition="answer",
                      narrative="looking"))])], record=record)

    sent = provider.first_input_text()
    assert "ACTIVE INVESTIGATION" in sent
    assert item["segment"] in sent
    assert scope.release_id in sent
    assert item["reporting_period"] in sent
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    assert dom.DEFAULT_RELEASES[other] not in sent
    for relation in arun.for_domain(other).catalog.relations():
        assert relation not in sent


# ---- C06-C12: the rest of the Corporate bank ---------------------------

def test_c06_corporate_ecl_coverage_by_sector(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="What is ECL coverage by sector?",
        sql=f"SELECT sector, SUM(ecl_sar_mn) / NULLIF(SUM(ead_sar_mn), 0) "
            f"AS ecl_coverage FROM corp_facility_quarter "
            f"WHERE reporting_quarter = '{month}' GROUP BY sector "
            f"ORDER BY ecl_coverage DESC",
        fields=["corp_facility_quarter.ecl_sar_mn",
                "corp_facility_quarter.ead_sar_mn",
                "corp_facility_quarter.sector"],
        purpose="ECL coverage by sector", grain="sector", units="percent",
        month=month)
    produced = keyed(case.rows, "sector", "ecl_coverage")
    for sector, value in oracle.corp_coverage_by_sector(month).items():
        assert produced[sector] == pytest.approx(value, abs=TOLERANCE)


def test_c07_corporate_downgrades_this_month(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="Which borrowers were downgraded this month?",
        sql=f"SELECT borrower_name, -rating_notches_moved AS notches_down, "
            f"rating_previous, rating_current FROM corp_borrower_quarter "
            f"WHERE reporting_quarter = '{month}' AND rating_notches_moved < 0 "
            f"ORDER BY notches_down DESC, borrower_name LIMIT {TOP_N}",
        fields=["corp_borrower_quarter.borrower_name",
                "corp_borrower_quarter.rating_notches_moved",
                "corp_borrower_quarter.rating_current"],
        purpose="Borrowers downgraded this month", grain="borrower",
        units="notches", month=month)
    produced = {str(r["borrower_name"]): int(r["notches_down"])
                for r in case.rows["rows"]}
    every = oracle.corp_downgrades(month)
    assert produced == dict(sorted(every.items(),
                                   key=lambda kv: (-kv[1], kv[0]))[:TOP_N])
    # And the book really does have downgrades to find.
    assert len(every) >= 1


def test_c08_corporate_covenant_breaches_and_the_exposure_behind_them(
        drive_domain, store_db):
    """The de-duplication case. Three breaches on one facility are one
    exposure, and summing across the join would count it three times."""
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="Which covenants are in breach, and how much exposure sits "
                 "behind them?",
        sql=f"WITH breached AS ("
            f"  SELECT DISTINCT facility_id FROM corp_covenant_quarter"
            f"  WHERE reporting_quarter = '{month}' AND breach_flag = 1) "
            f"SELECT COUNT(*) AS facilities, "
            f"SUM(f.ead_sar_mn) AS ead_sar_mn "
            f"FROM corp_facility_quarter f JOIN breached b "
            f"ON b.facility_id = f.facility_id "
            f"WHERE f.reporting_quarter = '{month}'",
        fields=["corp_covenant_quarter.breach_flag",
                "corp_covenant_quarter.facility_id",
                "corp_facility_quarter.ead_sar_mn"],
        purpose="Exposure behind breached covenants", grain="portfolio",
        units="SAR million", month=month)
    row = case.rows["rows"][0]
    assert float(row["ead_sar_mn"]) == pytest.approx(
        oracle.corp_exposure_behind_breaches(month), abs=1e-4)
    assert sum(oracle.corp_breaches_by_type(month).values()) >= int(
        row["facilities"]), (
        "a facility may carry more than one breached covenant")


def test_c09_corporate_collateral_cover_by_type(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="How well is each collateral type covering its exposure?",
        # The de-duplication is DECLARED. A facility pledging two assets of
        # the same type is one exposure, so the exposure side counts each
        # facility once per type; the grain diagnostic stands down because
        # the author said so in the query rather than in a comment.
        sql=f"WITH pledged AS ("
            f"  SELECT collateral_type,"
            f"         SUM(allocated_value_sar_mn) AS allocated"
            f"  FROM corp_collateral_quarter"
            f"  WHERE reporting_quarter = '{month}' GROUP BY collateral_type), "
            f"secured AS ("
            f"  SELECT d.collateral_type, SUM(f.ead_sar_mn) AS ead"
            f"  FROM (SELECT DISTINCT collateral_type, facility_id"
            f"        FROM corp_collateral_quarter"
            f"        WHERE reporting_quarter = '{month}') d"
            f"  JOIN corp_facility_quarter f ON f.facility_id = d.facility_id"
            f"  AND f.reporting_quarter = '{month}'"
            f"  GROUP BY d.collateral_type) "
            f"SELECT p.collateral_type, "
            f"p.allocated / NULLIF(s.ead, 0) AS cover "
            f"FROM pledged p JOIN secured s "
            f"ON s.collateral_type = p.collateral_type ORDER BY cover",
        fields=["corp_collateral_quarter.allocated_value_sar_mn",
                "corp_collateral_quarter.collateral_type",
                "corp_facility_quarter.ead_sar_mn"],
        purpose="Collateral cover by type", grain="collateral type",
        units="percent", month=month)
    produced = keyed(case.rows, "collateral_type", "cover")
    for kind, value in oracle.corp_collateral_cover_by_type(month).items():
        assert produced[kind] == pytest.approx(value, abs=1e-6)


def test_c10_corporate_stage_migration_between_two_months(drive_domain,
                                                          store_db):
    latest = oracle.latest_month(dom.CORPORATE)
    previous = oracle.previous_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="How much exposure moved to a worse stage this month?",
        sql=f"WITH now AS (SELECT facility_id, stage, ead_sar_mn "
            f"FROM corp_facility_quarter WHERE reporting_quarter = '{latest}'), "
            f"before AS (SELECT facility_id, stage FROM corp_facility_quarter "
            f"WHERE reporting_quarter = '{previous}') "
            f"SELECT SUM(CASE WHEN n.stage > b.stage THEN n.ead_sar_mn "
            f"ELSE 0 END) AS deteriorated, "
            f"SUM(CASE WHEN n.stage < b.stage THEN n.ead_sar_mn "
            f"ELSE 0 END) AS improved "
            f"FROM now n JOIN before b ON b.facility_id = n.facility_id",
        fields=["corp_facility_quarter.stage",
                "corp_facility_quarter.ead_sar_mn"],
        purpose="Stage migration over the latest month", grain="portfolio",
        units="SAR million", month=latest)
    row = case.rows["rows"][0]
    expected = oracle.corp_stage_migration(latest, previous)
    assert float(row["deteriorated"]) == pytest.approx(
        expected["deteriorated"], abs=1e-4)
    assert float(row["improved"]) == pytest.approx(expected["improved"],
                                                   abs=1e-4)


def test_c11_corporate_group_concentration(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="Which parent groups hold the most exposure?",
        sql=f"SELECT b.group_name, SUM(f.ead_sar_mn) AS ead_sar_mn "
            f"FROM corp_facility_quarter f "
            f"JOIN corp_borrower_quarter b ON b.borrower_id = f.borrower_id "
            f"AND b.reporting_quarter = f.reporting_quarter "
            f"WHERE f.reporting_quarter = '{month}' "
            f"GROUP BY b.group_name "
            f"ORDER BY ead_sar_mn DESC, b.group_name LIMIT {TOP_N}",
        fields=["corp_facility_quarter.ead_sar_mn",
                "corp_borrower_quarter.group_name"],
        purpose="Group exposure concentration", grain="group",
        units="SAR million", month=month)
    produced = keyed(case.rows, "group_name", "ead_sar_mn")
    expected = top_n(oracle.corp_ead_by_group(month))
    assert set(produced) == set(expected)
    for name, value in expected.items():
        assert produced[name] == pytest.approx(value, abs=1e-4)


def test_c12_corporate_utilisation_by_product_type(drive_domain, store_db):
    month = oracle.latest_month(dom.CORPORATE)
    case = run_case(
        drive_domain, store_db, domain_id=dom.CORPORATE,
        question="How drawn is each facility type?",
        sql=f"SELECT product_type, "
            f"SUM(drawn_sar_mn) / NULLIF(SUM(limit_sar_mn), 0) AS utilisation "
            f"FROM corp_facility_quarter WHERE reporting_quarter = '{month}' "
            f"GROUP BY product_type ORDER BY utilisation DESC",
        fields=["corp_facility_quarter.drawn_sar_mn",
                "corp_facility_quarter.limit_sar_mn",
                "corp_facility_quarter.product_type"],
        purpose="Utilisation by facility type", grain="facility type",
        units="percent", month=month)
    produced = keyed(case.rows, "product_type", "utilisation")
    for kind, value in oracle.corp_utilisation_by_type(month).items():
        assert produced[kind] == pytest.approx(value, abs=TOLERANCE)


# ---- R07-R12: the rest of the Retail bank ------------------------------

def test_r07_retail_exposure_by_delinquency_bucket(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How is exposure spread across delinquency buckets?",
        sql=f"SELECT delinquency_bucket, SUM(ead_sar_mn) AS ead_sar_mn "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY delinquency_bucket ORDER BY delinquency_bucket",
        fields=["retail_account_month.ead_sar_mn",
                "retail_account_month.delinquency_bucket"],
        purpose="EAD by delinquency bucket", grain="bucket",
        units="SAR million", month=month)
    produced = keyed(case.rows, "delinquency_bucket", "ead_sar_mn")
    expected = oracle.retail_ead_by_bucket(month)
    assert set(produced) == set(expected)
    for bucket, value in expected.items():
        assert produced[bucket] == pytest.approx(value, abs=1e-4)
    assert "90+" in produced and produced["90+"] > 0, (
        "a retail book with no ninety-day population answers every arrears "
        "question with approximately nothing")


def test_r08_retail_cures_by_product(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How many accounts cured this month, by product?",
        sql=f"SELECT product, SUM(cure_flag) AS cures "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY product ORDER BY cures DESC",
        fields=["retail_account_month.cure_flag",
                "retail_account_month.product"],
        purpose="Cures by product", grain="product", units="count",
        month=month)
    produced = {str(r["product"]): int(r["cures"]) for r in case.rows["rows"]}
    expected = oracle.retail_cures_by_product(month)
    for product, value in expected.items():
        assert produced[product] == value


def test_r09_retail_behaviour_score_migration(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How many customers moved score band this month?",
        sql=f"SELECT score_migration, COUNT(*) AS customers "
            f"FROM retail_customer_month WHERE reporting_month = '{month}' "
            f"GROUP BY score_migration ORDER BY customers DESC",
        fields=["retail_customer_month.score_migration",
                "retail_customer_month.customer_id"],
        purpose="Behaviour score migration", grain="migration",
        units="count", month=month)
    produced = {str(r["score_migration"]): int(r["customers"])
                for r in case.rows["rows"]}
    assert produced == oracle.retail_band_migration(month)


def test_r10_retail_customer_concentration(drive_domain, store_db):
    """The roll-up is READ, not recomputed across the join."""
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="Which customers hold the most exposure?",
        sql=f"SELECT customer_id, total_ead_sar_mn "
            f"FROM retail_customer_month WHERE reporting_month = '{month}' "
            f"ORDER BY total_ead_sar_mn DESC LIMIT 25",
        fields=["retail_customer_month.total_ead_sar_mn",
                "retail_customer_month.customer_id"],
        purpose="Largest retail customers", grain="customer",
        units="SAR million", month=month)
    produced = keyed(case.rows, "customer_id", "total_ead_sar_mn")
    expected = oracle.retail_ead_by_customer(month)
    top = sorted(expected, key=lambda k: -expected[k])[:25]
    assert set(produced) == set(top)
    for customer in top:
        assert produced[customer] == pytest.approx(expected[customer],
                                                   abs=1e-6)


def test_r11_retail_secured_and_unsecured(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="How does secured lending compare with unsecured?",
        sql=f"SELECT CASE WHEN secured_flag = 1 THEN 'secured' "
            f"ELSE 'unsecured' END AS security, "
            f"SUM(ead_sar_mn) AS ead_sar_mn, SUM(ecl_sar_mn) AS ecl_sar_mn, "
            f"SUM(ecl_sar_mn) / NULLIF(SUM(ead_sar_mn), 0) AS coverage, "
            f"COUNT(*) AS accounts "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY security ORDER BY security",
        fields=["retail_account_month.secured_flag",
                "retail_account_month.ead_sar_mn",
                "retail_account_month.ecl_sar_mn"],
        purpose="Secured against unsecured", grain="security",
        units="SAR million", month=month)
    produced = {str(r["security"]): r for r in case.rows["rows"]}
    expected = oracle.retail_secured_split(month)
    assert set(produced) == set(expected)
    for key, values in expected.items():
        assert float(produced[key]["ead_sar_mn"]) == pytest.approx(
            values["ead"], abs=1e-4)
        assert float(produced[key]["coverage"]) == pytest.approx(
            values["coverage"], abs=TOLERANCE)
        assert int(produced[key]["accounts"]) == int(values["accounts"])
    assert expected["secured"]["coverage"] < expected["unsecured"]["coverage"], (
        "security that does not reduce loss given default is not security")


def test_r12_retail_seasoning_curve(drive_domain, store_db):
    month = oracle.latest_month(dom.RETAIL)
    case = run_case(
        drive_domain, store_db, domain_id=dom.RETAIL,
        question="Does Stage 2 exposure rise with months on book?",
        sql=f"SELECT CASE WHEN months_on_book < 12 THEN '0-11' "
            f"WHEN months_on_book < 24 THEN '12-23' "
            f"WHEN months_on_book < 36 THEN '24-35' ELSE '36+' END "
            f"AS seasoning, "
            f"SUM(CASE WHEN stage >= 2 THEN ead_sar_mn ELSE 0 END) "
            f"/ NULLIF(SUM(ead_sar_mn), 0) AS stage2_share "
            f"FROM retail_account_month WHERE reporting_month = '{month}' "
            f"GROUP BY seasoning ORDER BY seasoning",
        fields=["retail_account_month.months_on_book",
                "retail_account_month.stage",
                "retail_account_month.ead_sar_mn"],
        purpose="Stage 2 share by seasoning", grain="seasoning band",
        units="percent", month=month)
    produced = keyed(case.rows, "seasoning", "stage2_share")
    expected = oracle.retail_stage2_by_months_on_book(month)
    assert set(produced) == set(expected)
    for band, value in expected.items():
        assert produced[band] == pytest.approx(value, abs=TOLERANCE)


def test_the_grain_diagnostic_refuses_an_undeclared_repetition(drive_domain,
                                                               store_db):
    """§7. A demonstrable repetition trap is a refusal, in the right book.

    C09 above passes because its de-duplication is declared. This is the same
    question written the way that silently double-counts, and it must not
    return a number. The refusal has to name THIS book's relations and THIS
    book's join, which is exactly what V3's diagnostic could not do -- it
    read a hard-coded list of corporate quarterly join pairs and would have
    found nothing here at all.
    """
    month = oracle.latest_month(dom.CORPORATE)
    outcome, provider, _record = drive_domain(
        dom.CORPORATE, "How well is collateral covering exposure?", [
            ScriptedResult(tool_calls=[execute_call(
                f"SELECT c.collateral_type, "
                f"SUM(c.allocated_value_sar_mn) / "
                f"NULLIF(SUM(f.ead_sar_mn), 0) AS cover "
                f"FROM corp_collateral_quarter c "
                f"JOIN corp_facility_quarter f ON f.facility_id = c.facility_id "
                f"AND f.reporting_quarter = c.reporting_quarter "
                f"WHERE c.reporting_quarter = '{month}' "
                f"GROUP BY c.collateral_type",
                purpose="Collateral cover", grain="collateral type",
                units="percent", subquestions=["Collateral cover"],
                fields=["corp_collateral_quarter.allocated_value_sar_mn",
                        "corp_facility_quarter.ead_sar_mn"],
                month=month)]),
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                      disposition="unsupported",
                      narrative=("That join repeats the exposure, so no "
                                 "figure was published.")),
                "tu-2")])])

    assert not outcome.response.get("executed")
    # What the ANALYST was told, read out of the bytes that would have gone
    # to the provider. The refusal is a fact about this book's join, so it
    # has to name this book's relations and say why.
    told = provider.last_input_text()
    assert "corp_collateral_quarter" in told and "corp_facility_quarter" in told
    assert "count the same amount more than once" in told
    assert "Security is held against a facility" in told
    assert "retail_" not in told, (
        "a refusal in the Corporate book must not name a Retail relation")
