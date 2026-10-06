"""
Real-world input: misspelling, telegraphic English, long paragraphs, and
other languages.

Evidence: UNIT for the normalisation and coverage rules, MODEL MOCK for the
runs -- the analyst is scripted, so these establish that the PIPELINE treats
every phrasing identically and preserves what must survive. They establish
nothing about what Opus writes; that is the live UAT's job and it is labelled
as such.

The thing under test is a boundary, not a model. CreditProbe may normalise
Unicode and whitespace and nothing else, so a misspelled, telegraphic or
mixed-language question must reach the analyst byte-identical apart from
that, must route through the same ownership and the same fields, and must
produce the same number.
"""

from __future__ import annotations

import json

import pytest

import oracles
from backend.cockpit_v4 import product_knowledge as pk
from backend.cockpit_v4.intake import normalize_question
from conftest import ScriptedResult, intent, tool_call

# ---- A. misspelling -----------------------------------------------------

MISSPELLED = [
    ("who are yu", "Who are you?"),
    ("wat is credit porbe", "What is CreditProbe?"),
    ("what is cockpit of creditporbe", "What is Cockpit of CreditProbe?"),
    ("show ead latest quater by secter",
     "Show EAD by sector for the latest quarter"),
    ("decompoze ecl movment", "Decompose the ECL movement"),
]


@pytest.mark.parametrize("typed,_clean", MISSPELLED)
def test_a_misspelled_question_reaches_the_analyst_unchanged(typed, _clean):
    result = normalize_question(typed)
    assert result.text == typed
    assert result.applied == []
    assert not result.changed


@pytest.mark.parametrize("typed,clean", MISSPELLED)
def test_a_misspelling_does_not_change_the_product_knowledge_policy(
        typed, clean):
    """The tool policy must not depend on how well the user spells."""
    assert pk.coverage(typed)["level"] == pk.coverage(clean)["level"]


def test_nothing_in_the_pipeline_corrects_spelling():
    """The correction is the analyst's job, and only the analyst's."""
    import backend.cockpit_v4.intake as intake

    source = intake.__doc__ or ""
    assert "no spelling correction" in source.lower()
    for typed, clean in MISSPELLED:
        assert normalize_question(typed).text != clean


# ---- B. telegraphic English --------------------------------------------

TELEGRAPHIC = [
    "latest qtr construction stage 2 change show",
    "which sector biggest ecl increase latest year",
    "data first customer rating downgrade ecl tell me",
]


@pytest.mark.parametrize("typed", TELEGRAPHIC)
def test_telegraphic_english_survives_intake(typed):
    result = normalize_question(typed)
    assert result.text == typed
    assert pk.coverage(typed)["level"] == pk.COVERAGE_SYNOPSIS


# ---- C. a long paragraph with the question at the end -------------------

PARAGRAPH = (
    "We had the quarterly credit committee yesterday and the construction "
    "book came up again. Acme Infra Ltd was downgraded in 2026Q1, their "
    "DSCR fell to 0.85x and the collateral revaluation knocked 12.5% off "
    "the plant and machinery values. Someone claimed ECL for that sector "
    "was up about SAR 45.75 million but nobody had the number.\n\n"
    "So: what is the ECL by sector for the latest quarter?"
)


def test_a_long_paragraph_keeps_its_shape_and_every_figure():
    result = normalize_question(PARAGRAPH)
    for token in ("Acme Infra Ltd", "2026Q1", "0.85x", "12.5%",
                  "SAR 45.75 million", "DSCR"):
        assert token in result.text, token
    # The paragraph break the user typed is structure, not whitespace noise.
    assert "\n\n" in result.text
    assert result.text.strip().endswith(
        "what is the ECL by sector for the latest quarter?")


def test_collapsing_whitespace_never_merges_the_context_into_the_question():
    messy = "Context line one.\n\n\n\n\nAnd the question?"
    assert normalize_question(messy).text == "Context line one.\n\nAnd the question?"


# ---- D, E, F. other languages ------------------------------------------

#: (label, question, clean English equivalent, expected coverage level)
LANGUAGES = [
    ("english", "What is CreditProbe?", pk.COVERAGE_SYNOPSIS),
    ("english_misspelled", "wat is credit porbe", pk.COVERAGE_SYNOPSIS),
    ("hinglish", "CreditProbe kya karta hai?", pk.COVERAGE_SYNOPSIS),
    ("hindi", "CreditProbe क्या करता है?", pk.COVERAGE_SYNOPSIS),
    ("arabic", "ما هو CreditProbe وماذا يفعل؟", pk.COVERAGE_SYNOPSIS),
    ("bengali", "CreditProbe কী করে?", pk.COVERAGE_SYNOPSIS),
]


@pytest.mark.parametrize("label,question,level", LANGUAGES)
def test_a_product_question_in_any_language_gets_the_same_tool_policy(
        label, question, level):
    verdict = pk.coverage(question)
    assert verdict["level"] == level, f"{label}: {verdict}"
    assert verdict["deep_topics_named"] == []


COCKPIT_IN_MANY_LANGUAGES = [
    ("english", "What is Cockpit of CreditProbe?"),
    ("english_misspelled", "wat is cockpit of creditporbe"),
    ("hinglish", "CreditProbe ka cockpit kya karta hai?"),
    ("hindi", "CreditProbe का Cockpit क्या करता है?"),
    ("arabic", "ما هو Cockpit في CreditProbe؟"),
]


@pytest.mark.parametrize("label,question", COCKPIT_IN_MANY_LANGUAGES)
def test_a_cockpit_question_retrieves_cockpit_in_any_language(label, question):
    """Module names are proper nouns; they survive translation and typos."""
    verdict = pk.coverage(question)
    assert verdict["level"] == pk.COVERAGE_RETRIEVAL, f"{label}: {verdict}"
    assert "cockpit" in verdict["deep_topics_named"], label
    sections = pk.retrieve(query=question)["sections"]
    assert any(s["topic"] == "cockpit" for s in sections), label


@pytest.mark.parametrize("label,question", [
    ("hindi", "मुझे latest quarter का ECL by sector दिखाओ"),
    ("hinglish", "construction ka stage 2 exposure last year kitna badha"),
    ("arabic", "أرني ECL حسب القطاع لآخر ربع سنة"),
    ("bengali", "সর্বশেষ ত্রৈমাসিকে সেক্টর অনুযায়ী ECL দেখান"),
])
def test_a_data_question_in_another_language_is_carried_unchanged(
        label, question):
    result = normalize_question(question)
    assert result.text == question, label
    assert result.applied == [], label


def test_a_mixed_script_question_keeps_its_entities_and_periods():
    question = ("Acme Steel Ltd ka 2026Q2 mein ECL kitna tha? "
                "पिछले साल 12.5% बढ़ा था।")
    result = normalize_question(question)
    for token in ("Acme Steel Ltd", "2026Q2", "12.5%", "ECL"):
        assert token in result.text, token


def test_bidi_controls_are_removed_and_the_words_are_not():
    question = "ما هو ‮CreditProbe‬؟"
    result = normalize_question(question)
    assert "removed_bidi_controls" in result.applied
    assert "‮" not in result.text and "‬" not in result.text
    assert "CreditProbe" in result.text
    assert "ما هو" in result.text


# ---- the same data question, five ways, through the real pipeline -------

EAD_SQL = """
SELECT sector_name,
       SUM(ead_reported) AS ead_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = '{quarter}'
GROUP BY sector_name
ORDER BY ead_reported_sar_mn DESC
"""

EAD_VARIANTS = [
    ("clean_english", "Show latest-quarter EAD by sector."),
    ("misspelled", "show ead latest quater by secter"),
    ("telegraphic", "latest qtr ead by sector show"),
    ("hinglish", "latest quarter ka EAD sector wise dikhao"),
    ("hindi", "मुझे latest quarter का EAD by sector दिखाओ"),
    ("arabic", "أرني EAD حسب القطاع لآخر ربع سنة"),
]


def _ead_script(quarter: str, top_sector: str):
    """Submit the analysis, then finalize from whatever came back."""
    submit = ScriptedResult(tool_calls=[tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="reported EAD by sector, latest quarter"),
        "objective": "Reported EAD by sector for the latest quarter",
        "subquestions": ["EAD by sector"],
        "scope": {"reporting_periods": [quarter], "filters": {}},
        "metadata_receipt_ids": [],
        "fields_required": ["cockpit_facility_quarter.ead_reported",
                            "cockpit_facility_quarter.sector_name"],
        "expected_output_grain": "sector", "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": EAD_SQL.format(quarter=quarter), "parameters": {},
                   "purpose": "EAD by sector",
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, "tu-exec")])

    def finish(messages):
        from conftest import final

        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        cell = next(r["ead_reported_sar_mn"] for r in step["preview"]
                    if r["sector_name"] == top_sector)
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood="reported EAD by sector"),
                  narrative="The largest sector is {{claim.top}}.",
                  coverage=[{"subquestion": "EAD by sector",
                             "status": "answered",
                             "evidence_refs": [{
                                 "artifact_id": step["artifact_id"],
                                 "row_key": f"sector_name={top_sector}",
                                 "column_id": "ead_reported_sar_mn"}]}],
                  numeric_claims=[{
                      "claim_id": "top",
                      "decimal_value": repr(float(cell)),
                      "unit": "SAR million", "display_precision": 2,
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": f"sector_name={top_sector}",
                                   "column_id": "ead_reported_sar_mn"}}]),
            "tu-final")])

    return [submit, finish]


@pytest.mark.parametrize("label,question", EAD_VARIANTS)
def test_every_phrasing_reaches_the_same_number(label, question, drive,
                                                store_db, release_id):
    """MODEL MOCK. The analyst is scripted identically for every variant.

    What this establishes is that nothing between the user and the analyst,
    and nothing between the analyst and the data, behaves differently because
    the question was misspelled, telegraphic or written in another script.
    """
    quarter = oracles.latest_quarter(release_id)
    expected = oracles.ead_by_sector(release_id, quarter)
    top_sector = max(expected, key=lambda k: expected[k])

    outcome, provider, record = drive(
        question, _ead_script(quarter, top_sector))

    assert outcome.state == "COMPLETED", f"{label}: {outcome}"
    # The analyst saw the user's own words.
    sent = str(provider.sent[0]["messages"][0]["content"])
    assert question in sent, f"{label}: the original wording did not survive"

    body = outcome.response
    artifact_id = body["numeric_claims"][0]["evidence"]["artifact_id"]
    rows = store_db.get_artifact(artifact_id,
                                 tenant_id=record.tenant_id)["rows"]
    actual = {str(r["sector_name"]): float(r["ead_reported_sar_mn"])
              for r in rows}
    assert set(actual) == set(expected), label
    for sector, value in expected.items():
        assert actual[sector] == pytest.approx(float(value), rel=1e-9), sector


def test_the_analyst_is_told_to_read_through_bad_spelling():
    """The rule lives in the runtime instruction, and it is checked here.

    A prompt is not a proof, and this test does not pretend otherwise: it
    asserts the instruction SAYS it, so the rule cannot be deleted silently.
    Whether Opus follows it is the live UAT's question.
    """
    from backend.cockpit_v4 import context as context_mod

    instruction = context_mod.analyst_instruction().lower()
    assert "poor spelling is not ambiguity" in instruction
    assert "answer in the language the user wrote in" in instruction
    assert "never translate a borrower name" in instruction
