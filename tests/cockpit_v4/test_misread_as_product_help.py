"""A question the classifier misreads must still be able to reach the book.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

These tests prove MECHANICS AND PROMPT PROPAGATION only. What a live
claude-opus-5 does with the surface below is not established here and needs
an explicitly approved paid retest.

H-LIVE-03 and H-LIVE-05: one root cause
---------------------------------------
`envelope.classify` decides, from the question's words and before any model
call, whether a turn is analytical. `action_state.decide` then uses that bit
to choose the tool surface, and on a non-analytical turn whose product
coverage is "synopsis" it offered `finalize_response` ALONE and REQUIRED it.

Two live questions landed there:

    "wat is the toatl expsoure at defalt by secter this qtr"
        -- no measure in the lexicon survives those typos

    "Which sectors are above the single-name limit?"
        -- names a policy concept, not a measure

The first is the whole of H-LIVE-03. The live analyst understood it exactly:
exposure at default by sector for 2026Q2, mapped to
`corp_facility_quarter.ead_sar_mn` and `corp_borrower_quarter.sector`, no
blocking ambiguity, "every term in the question resolves cleanly". Then it
had one tool on the request and nothing that could run a query, so it
published PRODUCT_HELP / unsupported and offered to proceed if the reader
said "go ahead". Not a failure of autonomy: the only move the surface
allowed.

The second is H-LIVE-05. On top of the same dead end, the policy pack was
attached by `context.finalization_system`, which `orchestration` applies only
when the action state is RESULT_READY -- so that turn published a
user-facing answer with NO policy context at all, and said "There is no
recorded single-name policy limit in the Corporate Credit book". CP-1.1
exists, sets `single_obligor_ead_sar_mn` to 25000, and its keyword map holds
"single name" for exactly this sentence.

Both fixes are structural and add no lexicon:

  * `execute_analysis` is offered on the non-analytical surface and nothing
    is required. Submitting SQL already IS the declaration -- `_do_execute`
    adopts the analytical allowance and escalates the envelope before it
    parses a field -- and withholding the tool is what made that
    documented widening unreachable.
  * the policy pack reaches every turn that can call `finalize_response`,
    not only one that executed.

H-LIVE-06
---------
A later live turn wrote "well inside the CP-1.1 single obligor limit of SAR
25,000 million". The figure is right and the answer showed no retrieval,
because there was none: the threshold reached that turn inside the policy
synopsis the server attaches. The published answer now carries the receipt
for that -- which pack, which version, which clauses -- and an answer that
presents a threshold as a limit while naming no clause is refused.
"""

from __future__ import annotations

import json

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import drive_domain, execute_call  # noqa: F401

from backend.cockpit_v4 import action_state as acts
from backend.cockpit_v4 import credit_policy as cp
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import envelope as env
from backend.cockpit_v4 import product_knowledge as pk
from backend.cockpit_v4 import states as st

#: The live sentence, and four more that are messy in different ways. Not one
#: of them is ambiguous: each names a measure, a dimension and a period that
#: a reader would resolve without asking.
MESSY = (
    "wat is the toatl expsoure at defalt by secter this qtr",
    "show me stge 2 exposre by sectr for this quater",
    "totl expsure at defalt by industy this qtr",
    "give me the totl exposre at defualt by sectr this quartr",
    "coverge of expsoure by secter ths qtr",
    "stge two expsure by secter this qtr",
)

#: The other half of the picture, measured rather than assumed: a typo the
#: MEASURE survives is classified correctly, because the lexicon matched
#: "ecl" or "ead" whatever happened to the words around it. So the misread
#: is not "typos break the classifier", it is "typos in the measure break
#: the classifier" -- and no amount of tuning a lexicon fixes the general
#: case, which is why the fix is the surface and not the words.
MESSY_BUT_STILL_READ_CORRECTLY = (
    "ecl by sectr latst quartr pls",
    "whats the ttl ead by sector thsi qtr",
    "eclcoverage by sector this qtr?",
)

POLICY_QUESTION = "Which sectors are above the single-name limit?"

EAD_BY_SECTOR = """
SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn
FROM corp_facility_quarter
WHERE reporting_quarter = '{quarter}'
GROUP BY sector
ORDER BY ead_sar_mn DESC
"""

FIELDS = ["corp_facility_quarter.ead_sar_mn",
          "corp_facility_quarter.sector"]


def _surface(question: str) -> acts.Decision:
    """The surface the product would put this question on, first action."""
    verdict = env.classify(question=question)
    withheld = (pk.coverage(question)["level"] == pk.COVERAGE_SYNOPSIS
                or verdict.analytical)
    return acts.decide(executed=False, answer_only=False,
                       analytical=verdict.analytical, readiness={},
                       product_tool_withheld=withheld)


# ---- the reproduction, at the layer that decides ----------------------

@pytest.mark.parametrize("question", MESSY)
def test_the_classifier_reads_a_messy_data_question_as_product_help(question):
    """The premise, measured. This is the misread the surface must survive.

    Nothing here is being fixed: the classifier is a BUDGET decision made
    from words alone and it is allowed to be wrong. What must not happen is
    that being wrong ends the turn.
    """
    verdict = env.classify(question=question)
    assert verdict.analytical is False
    assert verdict.family == env.PRODUCT_HELP_STANDARD


@pytest.mark.parametrize("question", MESSY_BUT_STILL_READ_CORRECTLY)
def test_a_typo_the_measure_survives_is_classified_correctly(question):
    """The control. The misread is narrower than "typos break it"."""
    assert env.classify(question=question).analytical is True


def test_the_policy_question_is_read_the_same_way():
    assert env.classify(question=POLICY_QUESTION).analytical is False


@pytest.mark.parametrize("question", MESSY + (POLICY_QUESTION,))
def test_that_surface_can_still_reach_the_book(question):
    decision = _surface(question)
    assert decision.state == acts.PRODUCT_HELP
    assert "execute_analysis" in acts.offered(decision)
    assert decision.require == "", (
        "requiring finalize_response is what made this a dead end: the "
        "analyst could not run anything whatever it understood")


def test_nothing_about_the_question_text_is_consulted_to_open_the_door():
    """The fix is structural. It is the same surface for every
    non-analytical question, including a genuine product one."""
    for question in (*MESSY, POLICY_QUESTION, "Who are you?",
                     "What problem does CreditProbe solve?"):
        decision = _surface(question)
        assert decision.state == acts.PRODUCT_HELP, question
        assert "execute_analysis" in acts.offered(decision), question


def test_the_one_generation_product_help_economy_is_intact():
    """`inspect_product_knowledge` is still off the first action of a broad
    product question. That economy was never the required tool."""
    decision = _surface("Who are you?")
    assert "inspect_product_knowledge" not in acts.offered(decision)
    assert pk.coverage("Who are you?")["level"] == pk.COVERAGE_SYNOPSIS


def test_a_second_action_gets_the_product_tool_back():
    decision = acts.decide(executed=False, answer_only=False,
                           analytical=False, readiness={},
                           product_tool_withheld=False)
    assert "inspect_product_knowledge" in acts.offered(decision)
    assert "execute_analysis" in acts.offered(decision)


def test_a_recovery_never_widens_the_surface():
    """A re-ask after a malformed action must not be a broader question."""
    decision = _surface(MESSY[0])
    narrowed = decision.narrow()
    assert narrowed.recovering is True
    assert narrowed.tools == decision.tools


# ---- the mechanism end to end -----------------------------------------

def _answer_from(messages, *, narrative: str):
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative=narrative,
              numeric_claims=[{
                  "claim_id": "top", "unit": "SAR million",
                  "evidence": {"artifact_id": step["artifact_id"],
                               "row_key": "r0",
                               "column_id": "ead_sar_mn"}}]))])


@pytest.fixture
def misread_run(drive_domain):  # noqa: F811
    """A run the classifier reads as product help, whose analyst runs SQL."""
    quarter = oracle.latest_period(dom.CORPORATE)
    sql = EAD_BY_SECTOR.format(quarter=quarter)
    outcome, provider, record = drive_domain(
        dom.CORPORATE, MESSY[0], [
            ScriptedResult(tool_calls=[execute_call(
                sql, purpose="EAD by sector", grain="sector",
                units="SAR million", subquestions=["EAD by sector"],
                fields=FIELDS, month=quarter)]),
            lambda m: _answer_from(
                m, narrative="Construction carries the most, at {{claim.top}}.")
        ])
    return outcome, provider, record


def test_a_misread_run_can_execute_and_publish(misread_run):
    outcome, _, _ = misread_run
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["executed"] is True
    assert outcome.response["disposition"] == "answer"
    assert outcome.response["numeric_claims"]


def test_the_first_action_actually_offered_execute_analysis(misread_run):
    """Propagation: not the state machine's opinion, the request's bytes."""
    _, provider, _ = misread_run
    offered = {t["name"] for t in (provider.sent[0]["tools"] or ())}
    assert "execute_analysis" in offered
    assert provider.sent[0]["tool_choice"] == {
        "type": "any", "disable_parallel_tool_use": True}, (
        "nothing is required on this surface, so the turn may choose")


def test_submitting_sql_widened_the_run_to_a_data_analysis(misread_run):
    """The declaration the old surface made unreachable."""
    outcome, _, _ = misread_run
    assert outcome.response["intent"]["query_mode"] == "DATA_ANALYSIS"


def test_the_widened_run_got_the_analytical_allowance(misread_run):
    from backend.cockpit_v4 import config as config_mod

    _, _, record = misread_run
    ceiling = config_mod.ANALYTICAL_STANDARD_LIMITS.spend_ceiling_usd
    assert ceiling > config_mod.STANDARD_LIMITS.spend_ceiling_usd
    # The answer turn ran under the analytical deadline, not the sixty
    # seconds a product-help turn gets.
    assert config_mod.ANALYTICAL_STANDARD_LIMITS.deadline_seconds > \
        config_mod.STANDARD_LIMITS.deadline_seconds


# ---- H-LIVE-05: the policy reaches a publishing product-help turn -----

def test_the_policy_question_reaches_cp_1_1_deterministically():
    """The retrieval was never broken. Nothing consumed it on that turn."""
    found = cp.retrieve(dom.CORPORATE, question=POLICY_QUESTION)
    assert [c["clause"] for c in found["clauses"]] == ["CP-1.1"]
    assert found["clauses"][0]["thresholds"] == {
        "single_obligor_ead_sar_mn": 25000}


def test_a_product_help_publishing_turn_is_given_this_books_policy(
        drive_domain):  # noqa: F811
    """The fix. The turn publishes an answer, so it gets the pack."""
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, POLICY_QUESTION, [
            ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("PRODUCT_HELP", "COCKPIT"),
                      disposition="answer",
                      narrative="The single obligor limit is CP-1.1."))])])
    assert outcome.state == st.COMPLETED, outcome.message

    system = json.dumps(provider.sent[0]["system"], default=str)
    assert "Corporate Credit Policy" in system
    assert "CP-1.1" in system
    assert "single_obligor_ead_sar_mn" in system
    assert "Retail Credit Policy" not in system


def test_an_action_turn_still_gets_no_policy(drive_domain):  # noqa: F811
    """It holds no number, so a threshold in front of it compares nothing."""
    quarter = oracle.latest_period(dom.CORPORATE)
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, "What is exposure at default by sector this quarter?",
        [ScriptedResult(tool_calls=[execute_call(
            EAD_BY_SECTOR.format(quarter=quarter), purpose="EAD by sector",
            grain="sector", units="SAR million",
            subquestions=["EAD by sector"], fields=FIELDS, month=quarter)]),
         lambda m: _answer_from(m, narrative="Sector exposure: {{claim.top}}.")
         ])
    assert outcome.state == st.COMPLETED, outcome.message
    action = json.dumps(provider.sent[0]["system"], default=str)
    assert "Credit Policy" not in action
    answer = json.dumps(provider.sent[1]["system"], default=str)
    assert "Corporate Credit Policy" in answer


# ---- H-LIVE-06: the provenance of a policy number ---------------------

def _policy_answer(narrative: str):
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("PRODUCT_HELP", "COCKPIT"),
              disposition="answer", narrative=narrative))])


def test_a_published_answer_carries_the_policy_receipt(drive_domain):  # noqa: F811
    outcome, _, _ = drive_domain(
        dom.CORPORATE, POLICY_QUESTION,
        [_policy_answer("CP-1.1 caps single obligor exposure at SAR 25,000 "
                        "million of EAD, funded and unfunded together.")])
    assert outcome.state == st.COMPLETED, outcome.message

    receipt = outcome.response["policy_context"]
    assert receipt["book"] == dom.CORPORATE
    assert receipt["pack_version"] == cp.version()
    assert receipt["clauses_attached"] == ["CP-1.1"]
    assert receipt["thresholds_in_context"]["CP-1.1"] == {
        "single_obligor_ead_sar_mn": 25000}
    assert outcome.response["policy_citations"] == ["CP-1.1"]


def test_a_threshold_presented_as_a_limit_with_no_clause_is_refused(
        drive_domain):  # noqa: F811
    """H-LIVE-06. Correct is not the same as governed.

    The live narrative cited CP-1.1 and is fine. This is the case that was
    only ever a warning: the same figure, presented as a limit, with no
    clause named anywhere in the answer.
    """
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, POLICY_QUESTION,
        [_policy_answer("The group is well inside the single obligor limit "
                        "of SAR 25,000 million."),
         _policy_answer("CP-1.1 sets the single obligor limit at SAR 25,000 "
                        "million of EAD, and the group is inside it.")])
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2, (
        "the uncited answer must have been refused and re-asked; one "
        "generation means it was published")
    assert outcome.response["policy_citations"] == ["CP-1.1"], (
        "the corrected answer is the one that names the clause")
    assert "25,000 million" in outcome.response["narrative"]


def test_the_refusal_says_what_to_do_about_it(drive_domain):  # noqa: F811
    outcome, provider, _ = drive_domain(
        dom.CORPORATE, POLICY_QUESTION,
        [_policy_answer("The group is well inside the single obligor limit "
                        "of SAR 25,000 million."),
         _policy_answer("CP-1.1 sets it at SAR 25,000 million.")])
    assert outcome.state == st.COMPLETED, outcome.message
    sent = json.dumps(provider.sent[1]["messages"], default=str)
    assert "names no clause" in sent
    assert "25,000" in sent


def test_an_ordinary_analytical_sentence_is_not_a_policy_citation(
        drive_domain):  # noqa: F811
    """Narrow by construction. A credit write-up says 'limit' constantly.

    `approved_limit` utilisation, a headroom figure, a cap on a chart axis:
    none of those is a policy threshold, and a check that refused them
    would refuse correct analysis for using ordinary English.
    """
    outcome, _, _ = drive_domain(
        dom.CORPORATE, POLICY_QUESTION,
        [_policy_answer("Utilisation against the approved limit is 62 per "
                        "cent, and the largest single exposure is SAR 4,120 "
                        "million.")])
    assert outcome.state == st.COMPLETED, outcome.message
    assert outcome.response["policy_citations"] == []


def test_a_retail_answer_never_receives_a_corporate_clause(drive_domain):  # noqa: F811
    outcome, provider, _ = drive_domain(
        dom.RETAIL, "Which collections action applies at 20-29 days?",
        [_policy_answer("The collections ladder applies from 20 days.")])
    assert outcome.state == st.COMPLETED, outcome.message
    system = json.dumps(provider.sent[0]["system"], default=str)
    assert "Retail Credit Policy" in system
    assert "CP-" not in system
    assert outcome.response["policy_context"]["book"] == dom.RETAIL
