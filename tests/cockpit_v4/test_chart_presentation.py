"""
The charts reach the reader, in the form that was asked for.

UNIT · REPRODUCTION · MODEL MOCK · REAL DATABASE/RUNNER. No paid provider
call.

The defect this exists for
--------------------------
Four consecutive live answers came back as a table under the yellow banner
"The analysis ran. The written explanation did not." One of them was a reply
to a reader who had asked, in those words, for a LINE CHART. No chart was
drawn in any of the four.

Five independent causes, each sufficient on its own:

  1. `_check_ordering` fired on any of `top|largest|...|most` -- most of the
     vocabulary of a delinquency write-up -- and then demanded the charted
     measure be monotonic across the artifact's rows, FOR EVERY KIND. A
     trend is ordered by period, so a line that rises and falls failed, and
     failed identically on the one correction the run can afford. Its own
     docstring said the check was about "a chart of ranked bars".
  2. `result_only_response` hard-coded `"charts": []` and was never handed
     the rejected answer, so the rescue channel that keeps the rows threw
     the charts away.
  3. `choose()` in the browser used `.find()`, so a three-chart answer
     rendered one, behind a chart/table toggle.
  4. the chart allowance was 2 (3 on deep) and the schema said
     `maxItems: 3`, so "one chart per product" was not expressible.
  5. `analyst.md` contained no occurrence of "chart", "graph" or "plot".
     The reader's own words were in the conversation and nothing said they
     were there to be honoured.

What is pinned here:

  a sequence is not a rank         -> a non-monotonic line publishes
  a rank still is                  -> an unordered bar is still refused
  a warning is not a rejection     -> a chart past the cap cannot hard-fail
  the rescue keeps the pictures    -> result-only republishes the charts
  the correction keeps them too    -> the packet says to send them again
  the policy is on the right turn  -> the answer turn carries it, the
                                      action turn (which is measured
                                      against a byte bound) does not
"""

from __future__ import annotations

import json

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call  # noqa: F401
from test_orchestration_recovery import _ead_call, _execution_result

from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.config import (ANALYTICAL_DEEP_LIMITS,
                                       ANALYTICAL_STANDARD_LIMITS,
                                       STANDARD_LIMITS)
from backend.cockpit_v4.contracts import parse_final
from backend.cockpit_v4.finalization import (COMPOSITION_KINDS,
                                             RANKING_KINDS, SEQUENCE_KINDS,
                                             SHAPE_KINDS, CHART_KINDS,
                                             Finalizer, correction_packet)

#: A book's delinquency rate over five periods: up, up, down, up. Nothing
#: about it is monotonic, and nothing about it should be.
TREND = [{"period": "P1", "dpd_30_plus_pct": 2.1},
         {"period": "P2", "dpd_30_plus_pct": 2.6},
         {"period": "P3", "dpd_30_plus_pct": 3.4},
         {"period": "P4", "dpd_30_plus_pct": 2.9},
         {"period": "P5", "dpd_30_plus_pct": 3.8}]

#: The sentence a risk write-up actually contains. Every one of these words
#: is in `_SUPERLATIVE`.
NARRATIVE = ("Delinquency is at its highest in the latest period, and the "
             "worst of the movement is in the most recent month.")


def _answer(charts, narrative: str = NARRATIVE):
    return parse_final(final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                             narrative=narrative, charts=list(charts)))


def _chart(kind: str, *, artifact_id: str, title: str = "Delinquency",
           column: str = "dpd_30_plus_pct"):
    return {"kind": kind, "title": title, "artifact_id": artifact_id,
            "x_column": "period", "y_columns": [column], "unit": "percent"}


@pytest.fixture
def stored(store_db):
    def _put(rows, columns):
        return store_db.put_artifact(
            run_id="r-presentation", tenant_id="t", kind="result",
            release_id="rel", scope={"step_id": "s1"},
            columns=list(columns), rows=list(rows))
    return _put


@pytest.fixture
def ordering(store_db, stored):
    """`_check_ordering` over one stored, deliberately unordered result."""
    artifact_id = stored(TREND, ["period", "dpd_30_plus_pct"])

    def _check(kind: str, *, limits=STANDARD_LIMITS, count: int = 1,
               narrative: str = NARRATIVE):
        finalizer = Finalizer(store=store_db, tenant_id="t", release_id="rel",
                              limits=limits, run_artifacts={artifact_id})
        charts = [_chart(kind, artifact_id=artifact_id, title=f"Chart {i}")
                  for i in range(count)]
        return finalizer._check_ordering(_answer(charts, narrative))

    return _check


# ---- a sequence is not a ranking ---------------------------------------

@pytest.mark.parametrize(
    "kind", sorted(SEQUENCE_KINDS | COMPOSITION_KINDS | SHAPE_KINDS))
def test_a_form_that_asserts_no_ranking_is_not_asked_to_be_ordered(
        ordering, kind):
    """THE live defect.

    A line, an area, a histogram, a box, a grid -- none of them says "this
    is the largest". Only the narrative said that, and the narrative was
    talking about the rows, which the reader can also see.
    """
    assert ordering(kind) == []


def test_a_ranking_chart_over_an_unordered_result_is_still_refused(
        ordering):
    """The check that was worth having, still having it.

    A bar chart IS a ranking claim: the reader reads the order of the bars
    as the order of the measure. A query that forgot its ORDER BY under a
    sentence about "the largest" is the specific error this catches, and no
    other check sees it.
    """
    problems = ordering("bar")
    assert len(problems) == 1
    assert "not ordered by it" in problems[0]


@pytest.mark.parametrize("kind", sorted(RANKING_KINDS))
def test_every_ranking_form_is_held_to_the_same_rule(ordering, kind):
    assert ordering(kind), f"{kind} asserts a rank and was not checked"


def test_the_refusal_names_the_chart_it_is_about(ordering):
    """A correction the analyst can act on.

    "The answer claims a ranking" over a six-chart answer does not say
    WHICH one to fix. It names the chart, the column and the artifact, and
    offers the third remedy the code now allows -- draw it as a sequence.
    """
    problem = ordering("bar", count=3)[0]
    assert "chart 1" in problem and "'Chart 0'" in problem
    assert "dpd_30_plus_pct" in problem
    assert "sequence" in problem


def test_a_chart_past_the_allowance_cannot_reject_the_answer(ordering):
    """A warning is not a rejection.

    Charts past the limit are DROPPED with a note, not refused. Judging a
    chart nobody will see, and failing the whole answer over it, is the
    same kind of error as failing a line for not being sorted.
    """
    over = STANDARD_LIMITS.charts + 2
    assert len(ordering("bar", count=over)) == STANDARD_LIMITS.charts


def test_a_narrative_with_no_ranking_language_is_never_checked(ordering):
    assert ordering("bar", narrative="Delinquency moved over the period.") \
        == []


# ---- the allowance -----------------------------------------------------

def test_one_chart_per_product_is_expressible():
    """Four products and a DPD trend for each is four charts.

    The allowance was 2. "Multiple product x DPD line charts" was not a
    presentation the contract could carry, whatever the analyst intended.
    """
    assert ANALYTICAL_STANDARD_LIMITS.charts >= 6
    assert ANALYTICAL_DEEP_LIMITS.charts >= 8


def test_the_schema_allows_what_the_budget_allows():
    from backend.cockpit_v4 import contracts as contracts_mod

    schema = json.loads(
        (contracts_mod.CONTRACTS_DIR / "finalize_response.schema.json")
        .read_text(encoding="utf-8"))
    charts = schema["properties"]["charts"]
    assert charts["maxItems"] >= ANALYTICAL_DEEP_LIMITS.charts


def test_the_vocabulary_is_one_list_in_two_places():
    """The enum the analyst is offered and the kinds the server draws."""
    from backend.cockpit_v4 import contracts as contracts_mod

    shared = json.loads(
        (contracts_mod.CONTRACTS_DIR / "shared_defs.schema.json")
        .read_text(encoding="utf-8"))
    enum = shared["$defs"]["Chart"]["properties"]["kind"]["enum"]
    assert sorted(enum) == sorted(CHART_KINDS)


# ---- the policy is carried on the turn that can act on it --------------

def test_the_answer_turn_is_told_how_to_present_the_result():
    blocks = ctx_mod.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"},
         {"type": "text", "text": json.dumps({"catalog_index": []})},
         {"type": "text", "text": json.dumps({"pinned_scope": {}})}])
    text = " ".join(str(b.get("text") or "") for b in blocks)
    assert "PRESENTATION" in text
    assert "line chart" in text
    # The reader's own words are the instruction when they gave one.
    assert "named a form" in text


def test_the_action_turn_does_not_pay_for_it():
    """The instruction is carried on EVERY action attempt and is measured
    against a byte bound. An action turn cannot send a chart, so chart
    policy on it is bytes spent on a turn that cannot use them -- and the
    seeded bound had 88 bytes of headroom when this was written."""
    instruction = ctx_mod.PROMPT_PATH.read_text(encoding="utf-8")
    assert "chart" not in instruction.lower()


def test_the_presentation_block_survives_a_packet_with_nothing_to_replace():
    """No catalogue block is not a reason to lose the policy.

    The count is exact rather than `>=` so that a block appearing by
    accident is as visible as one going missing. It moved from 2 to 3 when
    `ANALYTICAL_ANSWER` was added beside the chart policy -- WHAT the answer
    must be, then HOW to present it -- and PRESENTATION is still last.
    """
    blocks = ctx_mod.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"}])
    assert len(blocks) == 3
    assert "THE ANSWER" in str(blocks[-2]["text"])
    assert "PRESENTATION" in str(blocks[-1]["text"])


def test_an_empty_context_is_left_alone():
    assert ctx_mod.finalization_system([]) == []


# ---- a correction replaces the whole answer ----------------------------

def test_the_correction_packet_says_to_send_the_charts_again(
        store_db, stored):
    """The charts were not mentioned, so a corrected answer arrived with
    none: the reader lost every picture by getting a better sentence."""
    artifact_id = stored(TREND, ["period", "dpd_30_plus_pct"])
    finalizer = Finalizer(store=store_db, tenant_id="t", release_id="rel",
                          limits=STANDARD_LIMITS, run_artifacts={artifact_id})
    answer = _answer([_chart("line", artifact_id=artifact_id,
                             title="Delinquency trend"),
                      _chart("bar", artifact_id=artifact_id,
                             title="By product")])
    report = finalizer.validate(answer, executed=True)
    packet = correction_packet(
        answer, report, store=store_db, tenant_id="t",
        run_artifacts={artifact_id})

    assert "resend" in json.dumps(packet["charts"]).lower()
    sent = packet["charts"]["you_sent"]
    assert [c["title"] for c in sent] == ["Delinquency trend", "By product"]
    assert [c["kind"] for c in sent] == ["line", "bar"]
    # The points are NOT echoed: the analyst has them, and this packet is
    # carried on a turn whose whole problem was running out of room.
    assert "points" not in json.dumps(packet["charts"])


# ---- the rescue channel keeps the pictures -----------------------------

def _bad_answer_with_a_chart(messages):
    """A final answer whose evidence does not resolve, carrying a chart.

    The narrative is refused three times over. The chart is independent of
    it -- `_check_chart` reads the artifact, not the prose -- so there is
    no reason for the reader to lose it.
    """
    step = _execution_result(messages)["steps"][0]
    artifact = step["artifact_id"]
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative="Total exposure is {{claim.total}}.",
              numeric_claims=[{
                  "claim_id": "total", "decimal_value": "1.00",
                  "unit": "amount", "display_precision": 2,
                  "evidence": {"artifact_id": artifact,
                               "row_key": "all sectors",
                               "column_id": "ead_reported_sar_mn"}}],
              charts=[{"kind": "bar", "title": "EAD by sector",
                       "artifact_id": artifact, "x_column": "sector_name",
                       "y_columns": ["ead_reported_sar_mn"],
                       "unit": "SAR million"}]))],
        output_tokens=400)


def test_a_rejected_narrative_does_not_take_the_charts_with_it(
        drive, store_db, release_id):
    quarter = oracles.latest_quarter(release_id)
    outcome, _provider, _record = drive(
        "What is total exposure at default by sector in the latest quarter?",
        [ScriptedResult(tool_calls=[_ead_call(quarter)]),
         _bad_answer_with_a_chart, _bad_answer_with_a_chart,
         _bad_answer_with_a_chart])

    assert outcome.error_code == st.ANSWER_VALIDATION
    published = outcome.response or {}
    assert published.get("result_only") is True
    assert published["charts"], "the chart the analyst drew over real rows"
    chart = published["charts"][0]
    assert chart["kind"] == "bar"
    assert chart["points"], "rendered from the artifact, not from the prose"
    # What failed still failed: no number the analyst supplied is published.
    assert published["numeric_claims"] == []
