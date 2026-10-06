"""REAL DATABASE · REAL WORKER · MODEL MOCK · INDEPENDENT ORACLES.

§42-§45. Forty Corporate questions and forty Retail ones, every one of them
driven end to end through the real V4 worker and checked against a figure
recomputed with pandas over the published Parquet.

Why a BANK rather than more examples
------------------------------------
Every previous round was handed a failing question and fixed it. The
instruction for this one is explicit: a fix that makes only the reported
example pass is not acceptable. A bank is the difference -- eighty questions
that a reader would actually ask, spread across every subject area both books
publish, so a contract that is wrong is wrong HERE rather than on a Mac.

The oracles are in `question_bank.py` and import nothing from the analytical
path. Each one reads the Parquet with pandas and computes the figure from the
column definitions. An oracle that called the code under test would prove the
code is self-consistent, which is exactly what a wrong answer also is.
"""

from __future__ import annotations

import json

import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain  # noqa: F401
from test_domain_execution import execute_call, make_domain_run  # noqa: F401

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import states as st

from . import domain_oracles as oracle
from . import question_bank as bank


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


CASES = bank.all_cases()
IDS = [case.case_id for case in CASES]


def _answer(case: bank.Case):
    """Finalize from the rows the execution actually returned."""
    def build(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        row = step["preview"][0]
        row_key = (f"{case.key}={row[case.key]}" if case.key else "0")
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="answer",
                  narrative=f"{case.question} {{{{claim.figure}}}}",
                  numeric_claims=[{
                      "claim_id": "figure", "unit": case.units,
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": row_key,
                                   "column_id": case.value}}]))])
    return build


def run_case(drive, store_db, case: bank.Case):
    """Drive one question through the real worker and read the ARTIFACT.

    The artifact rather than the preview: the preview is a truncated echo
    for the analyst, and checking against it would check that the truncation
    is self-consistent.
    """
    period = oracle.latest_period(case.domain_id)
    seen: list[str] = []

    def answer(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        seen.append(body["steps"][0]["artifact_id"])
        return _answer(case)(messages)

    outcome, _provider, record = drive(
        case.domain_id, case.question,
        [ScriptedResult(tool_calls=[execute_call(
            case.sql, purpose=case.question, grain=case.grain,
            units=case.units, subquestions=[case.question],
            fields=list(case.fields), month=period)]),
         answer])
    assert outcome.state == st.COMPLETED, (case.case_id, outcome.message)
    stored = store_db.get_artifact(seen[0], tenant_id=record.tenant_id)
    assert stored is not None, case.case_id
    return outcome, record, stored


def keyed(rows: list[dict], key: str, value: str) -> dict[str, float]:
    return {str(row[key]): float(row[value]) for row in rows}


# ---- the bank, one test per question ------------------------------------

@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_answer_matches_an_independent_pandas_oracle(drive_domain,  # noqa: F811
                                                         store_db, case):
    """The figure CreditProbe produced is the figure in the Parquet."""
    _outcome, _record, body = run_case(drive_domain, store_db, case)
    rows = body["rows"]
    assert rows, case.case_id
    expected = case.oracle()

    if not case.key:
        assert len(rows) == 1, (case.case_id, len(rows))
        produced = float(rows[0][case.value])
        assert abs(produced - float(expected)) <= case.tolerance, (
            case.case_id, produced, expected)
        return

    produced = keyed(rows, case.key, case.value)
    assert set(produced) == set(expected), (
        case.case_id, sorted(set(produced) ^ set(expected)))
    for name, figure in expected.items():
        assert abs(produced[name] - float(figure)) <= case.tolerance, (
            case.case_id, name, produced[name], figure)


# ---- what must hold for EVERY question in the bank ----------------------

@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_answer_is_stamped_with_the_book_it_came_from(drive_domain,  # noqa: F811
                                                            store_db, case):
    """§17, §18. A figure without its release is a figure nobody can
    re-derive a quarter later."""
    _outcome, record, _body = run_case(drive_domain, store_db, case)
    stored = store_db.get_run(record.run_id)
    assert stored.domain_id == case.domain_id
    assert stored.release_id == dom.DEFAULT_RELEASES[case.domain_id]
    assert stored.release_fingerprint == lake.fingerprint(
        dom.DEFAULT_RELEASES[case.domain_id])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_no_question_reads_the_other_books_relations(case):
    """§17. Checked on the SQL itself, before anything runs: a query that
    names the other book's table is refused, but a bank entry that does so
    is a bank entry that was never testing this book."""
    other = next(d for d in dom.DOMAIN_IDS if d != case.domain_id)
    for relation in schema_mod.relation_names(other):
        assert relation not in case.sql, (case.case_id, relation)
    for field_id in case.fields:
        relation = field_id.split(".", 1)[0]
        assert relation in schema_mod.relation_names(case.domain_id), (
            case.case_id, field_id)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_question_names_this_books_calendar(case):
    """§2, §3. A Corporate question filters on `reporting_quarter` and a
    Retail one on `reporting_month`, and never the other way."""
    mine = schema_mod.period_column(case.domain_id)
    other = ("reporting_month" if mine == "reporting_quarter"
             else "reporting_quarter")
    assert other not in case.sql, (case.case_id, other)


# ---- the bank is the size the round asks for ----------------------------

def test_the_banks_are_large_enough_to_be_a_matrix():
    """§42, §43. Forty each, and forty DIFFERENT questions: a bank of forty
    spellings of one question proves what one question proves."""
    corporate = bank.corporate_cases()
    retail = bank.retail_cases()
    assert len(corporate) >= 40, len(corporate)
    assert len(retail) >= 40, len(retail)
    assert len({c.question for c in corporate}) == len(corporate)
    assert len({c.question for c in retail}) == len(retail)
    assert len({c.sql for c in CASES}) == len(CASES)


def test_the_banks_are_not_translations_of_each_other():
    """A corporate book is asked about sectors, ratings, covenants and
    collateral; a retail book about products, vintages, behavioural bands
    and buckets. Asking each the other's questions would prove only that
    both can group by a column."""
    corporate = {c.grain for c in bank.corporate_cases()}
    retail = {c.grain for c in bank.retail_cases()}
    shared = corporate & retail
    # A handful of grains are honestly common: every book has a whole-book
    # figure, both are regional, and both take collateral. Everything else
    # must differ, or the two banks are one bank asked twice.
    assert shared <= {"portfolio", "region", "collateral_type"}, sorted(shared)
    assert len(shared) <= 3, sorted(shared)
    assert "sector" in corporate and "sector" not in retail
    assert "score_band" in retail and "score_band" not in corporate


def test_the_banks_cover_every_subject_area_the_books_publish():
    """§7, §11, §42. A bank that never asks about covenants is a bank that
    cannot notice the covenant relation is broken."""
    for domain_id, cases, required in (
            (dom.CORPORATE, bank.corporate_cases(),
             {"exposure", "ifrs9", "rating", "covenants", "collateral",
              "financials", "sector", "concentration", "movement"}),
            (dom.RETAIL, bank.retail_cases(),
             {"exposure", "ifrs9", "behaviour", "delinquency", "vintage",
              "collateral", "concentration", "movement"})):
        tagged = {tag for case in cases for tag in case.tags}
        assert required <= tagged, (domain_id, sorted(required - tagged))


def test_every_relation_in_both_books_is_read_by_some_question():
    """A relation nothing asks about is a relation nothing checks."""
    for domain_id, cases in ((dom.CORPORATE, bank.corporate_cases()),
                             (dom.RETAIL, bank.retail_cases())):
        read = {field.split(".", 1)[0]
                for case in cases for field in case.fields}
        for relation in schema_mod.relation_names(domain_id):
            assert relation in read, (domain_id, relation)
