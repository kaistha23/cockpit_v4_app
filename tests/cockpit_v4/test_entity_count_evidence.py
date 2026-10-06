"""A count of one thing is not a count of another.

MODEL MOCK · REAL DATABASE/RUNNER · REAL SQL.

CLOSURE-01
----------
The first live UAT asked L18:

    "For each borrower, what is the average utilisation of their facilities
     this quarter, and how many facilities does each have?"

The query was right and the result was right: one row per borrower, 5,412 of
them, and the published limitation said so. The narrative said

    "2 borrowers carry facilities in 2026Q2"

from a claim whose unit was `borrowers`, whose operation was `identity`, and
whose cell was `facility_count` at row r0. The first borrower held two
facilities. A per-row count of FACILITIES was published as a portfolio count
of BORROWERS.

Nothing caught it, and `da974348`'s unit fix does not either -- that fix
governs operations which CHANGE the unit, and `identity` preserves it. Both
values are COUNT class, so every unit check agreed. What no check asked was
WHAT IS BEING COUNTED.

`display.entity_of` answers that from the words, and the finalizer refuses a
claim whose unit names one entity while its evidence column names another.
Narrow by construction: silent when the unit names no entity, silent when
the column names none, and silent when a phrase names two.
"""

from __future__ import annotations

import json

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain, execute_call  # noqa: F401

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import states as st

QUESTION = ("For each borrower, what is the average utilisation of their "
            "facilities this quarter, and how many facilities does each "
            "have?")

FIELDS = ["corp_facility_quarter.borrower_name",
          "corp_facility_quarter.utilisation_pct",
          "corp_facility_quarter.facility_id"]

SQL = """
SELECT borrower_name,
       AVG(utilisation_pct) AS avg_utilisation_pct,
       COUNT(*) AS facility_count
FROM corp_facility_quarter
WHERE reporting_quarter = '{quarter}'
GROUP BY borrower_name
ORDER BY facility_count DESC
"""


def _submission():
    quarter = oracle.latest_period(dom.CORPORATE)
    return execute_call(
        SQL.format(quarter=quarter), purpose="Utilisation by borrower",
        grain="borrower", units="percent",
        subquestions=["average utilisation and facility count per borrower"],
        fields=FIELDS, month=quarter)


def _artifact_id(messages) -> str:
    """The stored result, found wherever it is in the conversation.

    A CORRECTION turn reads the finalizer's rejection, not the execution
    result, so the last message is not the place to look on every turn.
    """
    for message in reversed(messages):
        for part in message.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            try:
                body = json.loads(part["content"])
            except (TypeError, ValueError, KeyError):
                continue
            for step in (body.get("steps") or []):
                if step.get("artifact_id"):
                    return str(step["artifact_id"])
            for stored in (body.get("authorized_artifacts") or []):
                if stored.get("artifact_id"):
                    return str(stored["artifact_id"])
    raise AssertionError("no stored artifact anywhere in the conversation")


def _answer(narrative: str, claims):
    def build(messages):
        artifact = _artifact_id(messages)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative=narrative,
                  numeric_claims=claims(artifact)))])
    return build


#: THE LIVE CLAIM, exactly as it was written.
def _the_live_claim(artifact):
    return [{"claim_id": "borrowers", "unit": "borrowers",
             "evidence": {"artifact_id": artifact, "row_key": "r0",
                          "column_id": "facility_count"}}]


# ---- what the unit layer does and does not see ------------------------

def test_both_values_are_counts_so_no_unit_check_could_have_caught_it():
    """Why `da974348` does not block any part of this."""
    assert disp.classify("borrowers") == disp.COUNT
    assert disp.classify("facility_count") == disp.COUNT
    # And `identity` preserves its operand's unit, correctly.
    from backend.cockpit_v4 import derivation as deriv

    parsed = deriv.parse({"operation": "identity", "operands": [
        {"artifact_id": "a", "column_id": "facility_count",
         "row_ids": ["r0"]}]})
    assert len(deriv.operands_in_the_result_unit(parsed)) == 1


def test_the_unit_and_the_column_name_different_entities():
    assert disp.entity_of("borrowers") == "borrower"
    assert disp.entity_of("facility_count") == "facility"


@pytest.mark.parametrize("text", ["n", "total", "avg_utilisation_pct",
                                  "SAR million", "percent",
                                  "facilities per borrower"])
def test_a_phrase_that_names_no_single_entity_is_proof_of_nothing(text):
    """The check must be silent wherever it cannot prove a mismatch."""
    assert disp.entity_of(text) == ""


# ---- the reproduction --------------------------------------------------

#: The rows the result actually published. A `count` over "every row" of
#: this result is REFUSED by an older and equally correct rule -- the
#: artifact holds the first hundred of 5,412 -- which is why the corrected
#: answers below either name the rows they mean or do not state the figure
#: at all. Both are answers requirement 5 allows.
PUBLISHED_ROWS = [f"r{i}" for i in range(10)]


def _counting_borrowers(artifact):
    return [{"claim_id": "borrowers", "unit": "borrowers",
             "derivation": {"operation": "count", "operands": [
                 {"artifact_id": artifact, "column_id": "borrower_name",
                  "row_ids": PUBLISHED_ROWS}]}}]


def _no_count_at_all(artifact):
    return [{"claim_id": "util", "unit": "percent",
             "evidence": {"artifact_id": artifact, "row_key": "r0",
                          "column_id": "avg_utilisation_pct"}}]


def test_a_facility_count_cannot_be_published_as_a_borrower_count(
        drive_domain):  # noqa: F811
    """The live claim is refused, and an answer that omits it publishes."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("{{claim.borrowers}} carry facilities in 2026Q2.",
                 _the_live_claim),
         _answer("The busiest borrower runs at {{claim.util}}.",
                 _no_count_at_all)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3, (
        "the first answer must have been refused and re-asked; two "
        "generations means the facility count was published")
    assert "borrowers carry facilities" not in outcome.response["narrative"]


def test_the_refusal_says_what_governed_evidence_would_look_like(
        drive_domain):  # noqa: F811
    _outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("{{claim.borrowers}} carry facilities.", _the_live_claim),
         _answer("Utilisation is {{claim.util}}.", _no_count_at_all)])

    sent = json.dumps(provider.sent[2]["messages"], default=str)
    assert "one thing per facility" in sent
    assert "'count' derivation" in sent
    assert "do not state it" in sent


def test_counting_the_right_entity_publishes(drive_domain):  # noqa: F811
    """Requirement 5's supported path: a row-count over a column that
    identifies the entity, across rows the answer names."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("The ten busiest are {{claim.borrowers}}.",
                 _counting_borrowers)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "no refusal: the entities agree"
    assert "10 borrowers" in outcome.response["narrative"], \
        outcome.response["narrative"]


def test_a_derivation_over_the_wrong_column_is_refused_too(
        drive_domain):  # noqa: F811
    """The rule is about the arithmetic's columns, not only direct cells."""
    def wrong(artifact):
        return [{"claim_id": "borrowers", "unit": "borrowers",
                 "derivation": {"operation": "sum", "operands": [
                     {"artifact_id": artifact, "column_id": "facility_count",
                      "row_ids": PUBLISHED_ROWS}]}}]

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("{{claim.borrowers}} in the book.", wrong),
         _answer("The ten busiest are {{claim.borrowers}}.",
                 _counting_borrowers)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3, (
        "summing facility_count is a facility total, not a borrower count")
    assert "10 borrowers" in outcome.response["narrative"]


def test_the_matching_entity_publishes_without_complaint(drive_domain):  # noqa: F811
    """The control. A claim whose unit and column agree is not touched."""
    def facilities(artifact):
        return [{"claim_id": "facilities", "unit": "facilities",
                 "evidence": {"artifact_id": artifact, "row_key": "r0",
                              "column_id": "facility_count"}}]

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("The largest borrower holds {{claim.facilities}}.",
                 facilities)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "one action, one answer, no refusal"
    assert "facilities" in outcome.response["narrative"]


def test_a_money_claim_on_the_same_result_is_not_touched(drive_domain):  # noqa: F811
    """A unit naming no entity makes no assertion this can check."""
    def utilisation(artifact):
        return [{"claim_id": "util", "unit": "percent",
                 "evidence": {"artifact_id": artifact, "row_key": "r0",
                              "column_id": "avg_utilisation_pct"}}]

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("Utilisation is {{claim.util}}.", utilisation)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2


# ---- the mutation check ------------------------------------------------

def test_that_guard_fails_when_the_entity_check_is_disabled(drive_domain,  # noqa: F811
                                                            monkeypatch):
    """Remove the rule and `facility_count` = 2 becomes "2 borrowers"."""
    from backend.cockpit_v4.finalization import Finalizer

    monkeypatch.setattr(Finalizer, "_wrong_entity",
                        staticmethod(lambda claim, columns: ""))
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("{{claim.borrowers}} carry facilities in 2026Q2.",
                 _the_live_claim)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "nothing refused it"
    # The live defect, reproduced exactly: a per-row facility count
    # published as the borrower count.
    assert outcome.response["narrative"].startswith("6 borrowers"), (
        outcome.response["narrative"])
