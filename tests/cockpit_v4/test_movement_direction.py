"""A movement statement is checked against its own two figures.

MODEL MOCK · REAL DATABASE/RUNNER · REAL SQL.

CLOSURE-02
----------
The first live UAT's M04 final turn said past-due exposure had gone

    "from 8.08% of exposure to 8.42%"

and then that

    "it has actually eased slightly".

8.42 is larger than 8.08. The numeric evidence was right and the
interpretation of it was backwards, and a reader taking the sentence at
face value reads a portfolio improving while it deteriorates.

The check added for it reads NOTHING about the analysis. It makes no
judgement about whether a move is material, what caused it, or whether
easing would be good news. It takes one sentence naming a start and an end
figure THROUGH THE CLAIMS THE SERVER COMPUTED, and refuses it when the
direction word says the opposite of the arithmetic. Interpretation stays
the model's; arithmetic stays the server's.

These tests prove the mechanics and the correction path. What live Opus
writes is not established here.
"""

from __future__ import annotations

import json

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain, execute_call  # noqa: F401

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import states as st

QUESTION = "How did past-due exposure compare with a quarter ago?"

FIELDS = ["corp_facility_quarter.ead_sar_mn",
          "corp_facility_quarter.past_due_flag"]

#: One row, two periods, so a movement is a comparison of two cells of the
#: same result in the same unit -- which is the shape this check is about.
SQL = """
SELECT
  100 * SUM(CASE WHEN past_due_flag AND reporting_quarter = '{now}'
                 THEN ead_sar_mn ELSE 0 END)
      / SUM(CASE WHEN reporting_quarter = '{now}'
                 THEN ead_sar_mn ELSE 0 END)   AS share_now_pct,
  100 * SUM(CASE WHEN past_due_flag AND reporting_quarter = '{prior}'
                 THEN ead_sar_mn ELSE 0 END)
      / SUM(CASE WHEN reporting_quarter = '{prior}'
                 THEN ead_sar_mn ELSE 0 END)   AS share_prior_pct
FROM corp_facility_quarter
WHERE reporting_quarter IN ('{now}', '{prior}')
"""


def _periods():
    quarters = oracle.periods(dom.CORPORATE)
    return quarters[-1], quarters[-2]


def _submission():
    now, prior = _periods()
    return execute_call(
        SQL.format(now=now, prior=prior), purpose="Past-due share by quarter",
        grain="portfolio", units="percent",
        subquestions=["past-due share now and a quarter ago"],
        fields=FIELDS, month=now)


def _artifact_id(messages) -> str:
    from test_entity_count_evidence import _artifact_id as scan

    return scan(messages)


def _claims(artifact):
    def cell(column):
        return {"artifact_id": artifact, "row_key": "r0",
                "column_id": column}
    return [
        {"claim_id": "prior", "unit": "percent",
         "evidence": cell("share_prior_pct")},
        {"claim_id": "now", "unit": "percent",
         "evidence": cell("share_now_pct")},
    ]


def _answer(narrative: str):
    def build(messages):
        # A CORRECTION turn reads the finalizer's rejection, not the
        # execution result, so the last message is not the place to look on
        # every turn.
        artifact = _artifact_id(messages)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative=narrative,
                  numeric_claims=_claims(artifact)))])
    return build


@pytest.fixture(scope="module")
def direction():
    """Which way the book actually moved, computed independently."""
    import pandas as pd  # noqa: F401

    now, prior = _periods()
    frame = oracle.frame(dom.CORPORATE, "corp_facility_quarter")

    def share(period):
        slice_ = frame[frame["reporting_quarter"] == period]
        due = slice_[slice_["past_due_flag"].astype(bool)]
        return 100 * due["ead_sar_mn"].sum() / slice_["ead_sar_mn"].sum()

    return share(prior), share(now)


def test_the_book_really_does_move_between_those_two_quarters(direction):
    prior, now = direction
    assert prior != now, "a test of direction needs a direction"


#: The live sentence, in the live structure.
_ROSE = ("Past-due exposure moved from {{claim.prior}} of exposure to "
         "{{claim.now}}, so it has risen.")
_EASED = ("Past-due exposure moved from {{claim.prior}} of exposure to "
          "{{claim.now}}, so it has actually eased slightly.")


def test_a_sentence_that_contradicts_its_own_figures_is_refused(
        drive_domain, direction):  # noqa: F811
    prior, now = direction
    wrong, right = (_EASED, _ROSE) if now > prior else (_ROSE, _EASED)

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(wrong), _answer(right)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 3, (
        "the contradicting sentence must have been refused and re-asked")


def test_the_refusal_quotes_the_two_figures_and_the_word(
        drive_domain, direction):  # noqa: F811
    prior, now = direction
    wrong, right = (_EASED, _ROSE) if now > prior else (_ROSE, _EASED)

    _outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer(wrong), _answer(right)])

    sent = json.dumps(provider.sent[2]["messages"], default=str)
    assert "which went" in sent
    assert "describe the movement they show" in sent


def test_the_correct_direction_publishes_in_one_answer(drive_domain,  # noqa: F811
                                                       direction):
    prior, now = direction
    right = _ROSE if now > prior else _EASED

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]), _answer(right)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "no refusal: the words match the figures"


# ---- everything it must stay silent about -----------------------------

@pytest.mark.parametrize("narrative", [
    # No direction word at all.
    "Past-due exposure moved from {{claim.prior}} to {{claim.now}}.",
    # Both senses in one sentence: two statements, and this cannot tell
    # which word belongs to which.
    ("Past-due exposure moved from {{claim.prior}} to {{claim.now}} while "
     "coverage rose and utilisation fell."),
    # Not the from/to structure. "B, up from A" reverses the order and is
    # deliberately left alone.
    "Past-due exposure is {{claim.now}}, higher than {{claim.prior}}.",
    # One claim only: nothing to compare.
    "Past-due exposure is {{claim.now}}, and it has eased. {{claim.prior}}",
])
def test_it_is_silent_where_it_cannot_prove_a_contradiction(
        drive_domain, narrative):  # noqa: F811
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]), _answer(narrative)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, (
        f"refused a sentence it cannot prove wrong: {narrative!r}")


def test_a_judgement_word_is_not_a_direction_word(drive_domain, direction):  # noqa: F811
    """"deteriorated" is a rise for ECL and a fall for coverage. The same
    word means opposite arithmetic on two measures, so it is not here."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]),
         _answer("Past-due exposure moved from {{claim.prior}} to "
                 "{{claim.now}}, which has deteriorated.")])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2


# ---- the mutation check ------------------------------------------------

def test_that_guard_fails_when_the_movement_check_is_disabled(
        drive_domain, direction, monkeypatch):  # noqa: F811
    """Disable it and the backwards sentence publishes, as it did live."""
    from backend.cockpit_v4.finalization import Finalizer

    monkeypatch.setattr(Finalizer, "_check_movement", lambda self, final: [])
    prior, now = direction
    wrong = _EASED if now > prior else _ROSE

    outcome, provider, _ = drive_domain(
        dom.CORPORATE, QUESTION,
        [ScriptedResult(tool_calls=[_submission()]), _answer(wrong)])

    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, "nothing refused it"
    assert ("eased" in outcome.response["narrative"]
            or "risen" in outcome.response["narrative"])
