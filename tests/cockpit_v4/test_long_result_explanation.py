"""An answer about a long result can say how long it was.

MODEL MOCK · REAL DATABASE/RUNNER · REAL SQL.

ISSUE 1 — L18, the paid retest
------------------------------
The entity-count defect is fixed: the retest's SQL was right on its first
submission and CreditProbe did NOT publish `facility_count` as a borrower
count. The run still ended

    state          PARTIAL
    error_code     ANSWER_VALIDATION
    evidence_bound false
    result_only    true

with both permitted explanation attempts refused and the safe result-only
fallback publishing the rows.

Reproduced here against the real worker on the retest's own SQL shape, and
the refusal says why:

    claim 'borrowers' says 'every row' of artifact '...', but that result
    produced 5,412 rows and only 100 of them were published.

That rule is right. The artifact holds a 100-row prefix of a 5,412-row
result, and a total over the part is not the total. What was missing is
anywhere else to go. The question asks about EACH BORROWER; an analyst
answering it wants to say how many borrowers there were; `rows='all'` is
refused, counting the published rows answers 100, and narrowing the query
would answer a different question. Both attempts went on discovering that.

The size of the whole result is not a mystery: CreditProbe measured it when
it ran the query and wrote `produced_rows` onto the artifact -- it is what
the refusal quotes back. There was simply no way for a claim to reach it.

`result_rows` is that way. The analyst names the artifact and the column
that says what a row is; CreditProbe supplies the count. The number is the
server's, and because the analyst still names a column, the entity check is
untouched: `result_rows` over `facility_count` called "borrowers" is
refused exactly as a `count` over it would be.
"""

from __future__ import annotations

import json

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain, execute_call  # noqa: F401
from test_entity_count_evidence import _artifact_id

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import states as st

QUESTION = ("For each borrower, what is the average utilisation of their "
            "facilities this quarter, and how many facilities does each "
            "have?")

FIELDS = ["corp_facility_quarter.utilisation_pct",
          "corp_facility_quarter.facility_id",
          "corp_facility_quarter.borrower_name",
          "corp_facility_quarter.ead_sar_mn"]

#: The retest's own SQL, in its own shape.
SQL = """
SELECT f.borrower_id,
       f.borrower_name,
       COUNT(DISTINCT f.facility_id) AS facility_count,
       AVG(f.utilisation_pct) AS avg_utilisation_pct,
       SUM(f.ead_sar_mn) AS ead_sar_mn
FROM corp_facility_quarter f
WHERE f.reporting_quarter = '{quarter}'
GROUP BY f.borrower_id, f.borrower_name
ORDER BY facility_count DESC
"""


def _submission():
    quarter = oracle.latest_period(dom.CORPORATE)
    return execute_call(
        SQL.format(quarter=quarter),
        purpose="Utilisation and facility count per borrower",
        grain="borrower", units="percent",
        subquestions=["utilisation and facility count per borrower"],
        fields=FIELDS, month=quarter)


COLUMNS = ["borrower_id", "borrower_name", "facility_count",
           "avg_utilisation_pct", "ead_sar_mn"]


def _answer(narrative: str, claims):
    def build(messages):
        artifact = _artifact_id(messages)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative=narrative, numeric_claims=claims(artifact),
                  tables=[{"title": "Utilisation by borrower",
                           "artifact_id": artifact,
                           "columns": COLUMNS}]))])
    return build


def _count_every_row(artifact):
    """What the analyst reached for, and what the artifact cannot give."""
    return [{"claim_id": "borrowers", "unit": "borrowers",
             "derivation": {"operation": "count", "operands": [
                 {"artifact_id": artifact, "column_id": "borrower_id",
                  "rows": "all"}]}}]


def _result_rows(artifact):
    return [{"claim_id": "borrowers", "unit": "borrowers",
             "derivation": {"operation": "result_rows", "operands": [
                 {"artifact_id": artifact, "column_id": "borrower_id",
                  "rows": "all"}]}},
            {"claim_id": "busiest", "unit": "facilities",
             "evidence": {"artifact_id": artifact, "row_key": "r0",
                          "column_id": "facility_count"}}]


def _wrong_entity(artifact):
    return [{"claim_id": "borrowers", "unit": "borrowers",
             "derivation": {"operation": "result_rows", "operands": [
                 {"artifact_id": artifact, "column_id": "facility_count",
                  "rows": "all"}]}}]


NARRATIVE = ("{{claim.borrowers}} hold facilities this quarter, and the "
             "busiest runs {{claim.busiest}}.")
SHORT = "{{claim.borrowers}} hold facilities this quarter."


# ---- the reproduction --------------------------------------------------

def test_the_result_is_longer_than_what_is_published(drive_domain):  # noqa: F811
    """The premise, measured: a prefix of a much longer result."""
    outcome, _, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(NARRATIVE, _result_rows)])
    assert outcome.state == st.COMPLETED, outcome.message
    table = outcome.response["tables"][0]
    assert table["row_count"] == 100
    assert "borrower_id" in table["columns"]


def test_every_row_of_a_clipped_result_is_still_refused(drive_domain):  # noqa: F811
    """Unchanged, and right. A total over the part is not the total."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(SHORT, _count_every_row),
         _answer(NARRATIVE, _result_rows)])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3, "the first answer must have been refused"
    sent = json.dumps(provider.sent[2]["messages"], default=str)
    assert "only 100 of them were published" in sent


def test_the_refusal_now_names_a_route_that_exists(drive_domain):  # noqa: F811
    """The correction contract: a refusal must leave somewhere to go."""
    _outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(SHORT, _count_every_row),
         _answer(NARRATIVE, _result_rows)])
    sent = json.dumps(provider.sent[2]["messages"], default=str)
    assert "'result_rows' operation" in sent
    assert "5,412" in sent, "and it quotes the number it would give"


def test_the_correction_publishes(drive_domain):  # noqa: F811
    """First explanation wrong, correction accepted, no result-only."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(SHORT, _count_every_row),
         _answer(NARRATIVE, _result_rows)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3
    assert outcome.response.get("result_only") is not True
    assert outcome.response["evidence_bound"] is True
    assert "5,412 borrowers" in outcome.response["narrative"], \
        outcome.response["narrative"]


def test_a_valid_explanation_publishes_first_time(drive_domain):  # noqa: F811
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(NARRATIVE, _result_rows)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "no correction needed"
    assert outcome.response["evidence_bound"] is True
    assert "5,412 borrowers" in outcome.response["narrative"]


# ---- the safeguard is untouched ---------------------------------------

def test_a_row_count_over_the_wrong_column_is_still_refused(drive_domain):  # noqa: F811
    """The whole point of CLOSURE-01, and a new operation must not be a
    way around it."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(SHORT, _wrong_entity),
         _answer(NARRATIVE, _result_rows)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3
    sent = json.dumps(provider.sent[2]["messages"], default=str)
    assert "one thing per facility" in sent


def test_a_second_unrecoverable_failure_still_falls_back_safely(
        drive_domain):  # noqa: F811
    """The safeguard the live run relied on. Two bad explanations must
    still publish the correct rows rather than nothing."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(SHORT, _count_every_row),
         _answer(SHORT, _wrong_entity)])

    assert outcome.state == st.PARTIAL, outcome.message
    assert outcome.response["result_only"] is True
    assert outcome.response["evidence_bound"] is False
    assert outcome.response["tables"][0]["row_count"] == 100, (
        "the rows are still published: the query was right")
    assert len(provider.sent) == 3, "one correction, not more"


def test_creditprobe_never_rewrote_the_analysts_sql(drive_domain,  # noqa: F811
                                                   store_db):
    """Whatever happens to the explanation, the code the run executed is
    the code the analyst sent, byte for byte."""
    call = _submission()
    authored = call["input"]["steps"][0]["code"]
    outcome, _provider, record = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[call]),
         _answer(SHORT, _count_every_row),
         _answer(SHORT, _wrong_entity)])
    assert outcome.state == st.PARTIAL

    submissions = store_db.submissions_for_run(record.run_id)
    assert submissions, "the submission is on file"
    recorded = submissions[0]["payload"]["steps"][0]["code"]
    assert recorded == authored, "CreditProbe changed the analyst's SQL"


# ---- the operation itself ---------------------------------------------

def test_result_rows_reads_the_servers_own_count():
    arts = {"a": {"columns": ["borrower_id"], "rows": [{"borrower_id": "b"}],
                  "scope": {"produced_rows": 5412, "complete": False}}}
    parsed = deriv.parse({"operation": "result_rows", "operands": [
        {"artifact_id": "a", "column_id": "borrower_id", "rows": "all"}]})
    assert deriv.compute(parsed, arts) == 5412


def test_a_complete_result_counts_what_it_holds():
    arts = {"a": {"columns": ["sector"],
                  "rows": [{"sector": "x"}, {"sector": "y"}],
                  "scope": {"complete": True}}}
    parsed = deriv.parse({"operation": "result_rows", "operands": [
        {"artifact_id": "a", "column_id": "sector", "rows": "all"}]})
    assert deriv.compute(parsed, arts) == 2


def test_it_refuses_a_selection_of_rows():
    arts = {"a": {"columns": ["sector"], "rows": [{"sector": "x"}],
                  "scope": {"produced_rows": 9, "complete": False}}}
    parsed = deriv.parse({"operation": "result_rows", "operands": [
        {"artifact_id": "a", "column_id": "sector", "row_ids": ["r0"]}]})
    with pytest.raises(deriv.DerivationError, match="rows='all'"):
        deriv.compute(parsed, arts)


def test_it_refuses_an_artifact_that_does_not_record_its_size():
    arts = {"a": {"columns": ["sector"], "rows": [{"sector": "x"}],
                  "scope": {}}}
    parsed = deriv.parse({"operation": "result_rows", "operands": [
        {"artifact_id": "a", "column_id": "sector", "rows": "all"}]})
    with pytest.raises(deriv.DerivationError, match="does not record it"):
        deriv.compute(parsed, arts)


def test_a_row_count_is_not_a_percentage():
    parsed = deriv.parse({"operation": "result_rows", "operands": [
        {"artifact_id": "a", "column_id": "sector", "rows": "all"}]})
    assert deriv.unit_problem(parsed, "percent")
    assert not deriv.unit_problem(parsed, "borrowers")


def test_the_operation_is_published_in_the_guide():
    """The analyst is told it exists, in the packet it reads."""
    names = {row["operation"] for row in deriv.describe()}
    assert "result_rows" in names
    entry = next(r for r in deriv.describe()
                 if r["operation"] == "result_rows")
    assert "PRODUCED" in entry["meaning"]
    assert entry["operands"] == 1
