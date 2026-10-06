"""
The overnight analytical benchmark: twenty questions, three evidence layers.

MODEL MOCK · REAL DATABASE · REAL PIPELINE. No paid provider call is made by
this module and none of it claims to measure the model's own judgement.

What each layer establishes, stated plainly so the report cannot overclaim:

  LAYER A  `uat_question_bank.oracle()` computes the expected answer with
           pandas, straight from the pinned Parquet, importing nothing from
           the query path. It is the arbiter.

  LAYER B  `uat_sql.sql_for()` is SQL authored by a reader of the question,
           executed here through the REAL V4 session -- the materialized,
           file-access-disabled DuckDB connection the product uses. A
           disagreement with Layer A means one of the two is wrong, and the
           test names which question rather than averaging them.

  LAYER C  the same statement carried through the REAL worker: intake, the
           tool contracts, the validator, the bind proof, the executor, the
           artifact store, the finalizer and the event stream. What is
           scripted is the analyst's TURN, not the machinery under it.

So: "CreditProbe executed this analysis correctly and published a figure the
evidence supports" is proven here. "Opus would have chosen this analysis" is
NOT, and the benchmark artifact records that distinction on every row.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import uat_question_bank as bank
import uat_sql as layer_b
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4 import states as st

EVIDENCE = (Path(__file__).resolve().parents[2] / "docs" / "cockpit_v4"
            / "evidence")
ARTIFACT = EVIDENCE / "overnight_analytical_benchmark.json"

#: Money here is a float column summed two different ways. A relative
#: tolerance of 1e-9 compares the analysis; anything looser could hide a
#: dropped row, which moves these figures by whole millions.
REL = 1e-9

ALL = [q["id"] for q in bank.QUESTIONS]


def _rows(cursor) -> list[dict]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def _run_layer_b(session, question_id: str, period: dict) -> list[dict]:
    with session.lock:
        cursor = session.connection.execute(
            layer_b.sql_for(question_id),
            layer_b.parameters_for(question_id, period))
        return _rows(cursor)


def _num(value) -> float | None:
    return None if value is None else float(value)


# ---- Layer A vs Layer B: two independent readings of one question -------

def _pairs(question_id: str, b_rows: list[dict], a: dict
           ) -> list[tuple[str, object, object]]:
    """Named scalars from each layer, to be compared one for one."""
    out: list[tuple[str, object, object]] = [
        ("row_count", len(b_rows), a["row_count"])]
    a_rows = a.get("rows", [])

    def by(column: str, key: str):
        for i, (b, o) in enumerate(zip(b_rows, a_rows)):
            out.append((f"row[{i}].{column}", b[column], o[key]))

    if question_id == "Q01":
        by("sector_name", "sector_name"), by("ead", "ead")
        out.append(("total_ead", sum(_num(r["ead"]) for r in b_rows),
                    a["total_ead"]))
    elif question_id == "Q02":
        by("borrower_id", "borrower_id"), by("ecl", "ecl"), by("ead", "ead")
        by("stage", "stage")
    elif question_id == "Q03":
        by("sector_name", "sector_name"), by("stage2_ead", "stage2_ead")
        by("share", "share")
    elif question_id == "Q04":
        by("borrower_id", "borrower_id"), by("quarter", "quarter")
        by("waiver", "waiver")
    elif question_id == "Q05":
        by("borrower_id", "borrower_id"), by("notches", "notches")
        by("ead", "ead"), by("ecl", "ecl")
        by("previous_rating", "previous_rating")
        by("current_rating", "current_rating")
    elif question_id == "Q06":
        by("sector_name", "sector_name"), by("change", "change")
    elif question_id == "Q07":
        by("sector_name", "sector_name"), by("gap", "gap")
    elif question_id == "Q08":
        by("sector_name", "sector_name"), by("contribution", "contribution")
        out.append(("contributions_sum",
                    sum(_num(r["contribution"]) for r in b_rows),
                    a["contributions_sum"]))
    elif question_id == "Q09":
        by("borrower_id", "borrower_id"), by("ead", "ead"), by("ecl", "ecl")
    elif question_id == "Q10":
        by("sector_name", "sector_name"), by("ecl_change", "ecl_change")
        by("uncovered_share_change", "uncovered_share_change")
    elif question_id == "Q11":
        by("borrower_id", "borrower_id"), by("contribution", "contribution")
        by("ecl", "ecl")
    elif question_id == "Q12":
        by("borrower_id", "borrower_id"), by("notches_down", "notches_down")
        by("ead", "ead")
    elif question_id == "Q13":
        by("borrower_id", "borrower_id")
    elif question_id == "Q14":
        by("sector_name", "sector_name"), by("ecl_change", "ecl_change")
        by("concentration_change", "concentration_change")
    elif question_id == "Q15":
        by("borrower_id", "borrower_id"), by("notches_down", "notches_down")
        by("ecl_change", "ecl_change")
    elif question_id == "Q16":
        by("sector_name", "sector_name"), by("ead", "ead"), by("ecl", "ecl")
        by("stage2_share", "stage2_share"), by("ecl_change", "ecl_change")
    elif question_id == "Q17":
        by("borrower_id", "borrower_id"), by("ecl", "ecl")
        head = b_rows[0]
        for column, key in (("sector_ead", "ead"), ("sector_ecl", "ecl"),
                            ("sector_stage2_share", "stage2_share"),
                            ("sector_ead_year_ago", "ead_year_ago"),
                            ("sector_ecl_year_ago", "ecl_year_ago")):
            out.append((column, head[column], a["kpis"][key]))
    elif question_id == "Q18":
        indexed = {r["reporting_quarter"]: r for r in b_rows}
        for label, block in a["periods"].items():
            row = indexed[block["quarter"]]
            out.append((f"{label}.ecl", row["ecl"], block["ecl"]))
            out.append((f"{label}.breach_ead", row["breach_ead"],
                        block["breach_ead"]))
        out[0] = ("row_count", len(b_rows), len(a["periods"]))
    elif question_id == "Q19":
        head = b_rows[0]
        for column, key in (("ead", "ead"), ("ecl", "ecl"),
                            ("coverage", "coverage"),
                            ("stage2_share", "stage2_share")):
            out.append((column, head[column], a["kpis"][key]))
        out.append(("top_sector", head["top_sector"],
                    a["top_sector"]["sector_name"]))
        out.append(("top_sector_ecl", head["top_sector_ecl"],
                    a["top_sector"]["ecl"]))
        out.append(("top_borrower", head["top_borrower"],
                    a["top_borrower"]["borrower_id"]))
    elif question_id == "Q20":
        by("sector_name", "sector_name")
        by("deteriorated_count", "count")
        for i, (b, o) in enumerate(zip(b_rows, a_rows)):
            out.append((f"row[{i}].ecl_change", b["ecl_change"],
                        o["measures"]["ecl"]))
    return out


@pytest.mark.parametrize("question_id", ALL)
def test_the_two_independent_readings_agree(session, release_id, question_id):
    """Layer A (pandas) and Layer B (SQL) must reach the same answer."""
    period = bank.periods(release_id)
    b_rows = _run_layer_b(session, question_id, period)
    a = bank.oracle(question_id, release_id)
    for label, got, want in _pairs(question_id, b_rows, a):
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            assert got == pytest.approx(float(want), rel=REL), (
                f"{question_id} {label}: SQL says {got}, pandas says {want}")
        else:
            assert got == want, (
                f"{question_id} {label}: SQL says {got!r}, pandas says "
                f"{want!r}")


# ---- Layer C: the real pipeline carries the same analysis ---------------

#: The bank states which visual the question deserves. The contract offers
#: four kinds, so the policy is mapped rather than assumed to be one-to-one --
#: and a stacked bar is a bar with a series column, not a fifth kind.
CHART_KIND = {bank.BAR: "bar", bank.LINE: "line", bank.STACKED: "bar",
              bank.WATERFALL: "waterfall", bank.NONE: ""}

#: The column in the Layer B result that carries each question's headline
#: figure, and the units it is in. Q13 has no headline because its answer is
#: legitimately "none", which is a finding rather than a figure.
HEADLINE = {
    "Q01": ("ead", "SAR million"), "Q02": ("ecl", "SAR million"),
    "Q03": ("stage2_ead", "SAR million"), "Q04": ("breaches", "count"),
    "Q05": ("ead", "SAR million"), "Q06": ("change", "SAR million"),
    "Q07": ("gap", "ratio"), "Q08": ("contribution", "SAR million"),
    "Q09": ("ead", "SAR million"), "Q10": ("ecl_change", "SAR million"),
    "Q11": ("contribution", "SAR million"), "Q12": ("ead", "SAR million"),
    "Q13": ("", ""), "Q14": ("ecl_change", "SAR million"),
    "Q15": ("ecl_change", "SAR million"), "Q16": ("ead", "SAR million"),
    "Q17": ("sector_ead", "SAR million"), "Q18": ("ecl", "SAR million"),
    "Q19": ("ead", "SAR million"), "Q20": ("ecl_change", "SAR million"),
}

#: Which relation each question's fields come from, so `fields_required`
#: names real catalogue fields rather than a shape the validator waves through.
_FIELD_RELATION = {
    "breach_date": "cockpit_covenant_quarter",
    "waiver_flag": "cockpit_covenant_quarter",
    "rating_rank": "cockpit_rating_ratio_quarter",
    "risk_rating": "cockpit_rating_ratio_quarter",
}


def _fields_for(question: dict) -> list[str]:
    names = list(question["measures"]) + list(question["group"])
    out = []
    for name in names:
        relation = _FIELD_RELATION.get(name, "cockpit_facility_quarter")
        out.append(f"{relation}.{name}")
    return sorted(set(out))


def _quarters_for(question: dict, period: dict) -> list[str]:
    slots = []
    for label in question["periods"]:
        value = period[label]
        slots.extend(value if isinstance(value, list) else [value])
    return sorted(set(slots))


def _execute_call(question_id: str, period: dict) -> dict:
    question = bank.BY_ID[question_id]
    resolved = [
        "Exposure read as reported EAD (ead_reported).",
        "ECL read as booked ECL (ecl_reported).",
        f"Period resolved from the calendar to {period['latest']}.",
    ]
    return tool_call("execute_analysis", {
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood=question["text"], resolved=resolved,
                         rationale="read the published measures directly"),
        "objective": question["text"],
        "subquestions": [question["text"]],
        "scope": {"reporting_periods": _quarters_for(question, period),
                  "filters": question.get("filters", {})},
        "metadata_receipt_ids": [],
        "fields_required": _fields_for(question),
        "expected_output_grain": question["group"][-1],
        "expected_units": "SAR million",
        "steps": [{"step_id": "s1", "language": "sql",
                   "code": layer_b.sql_for(question_id),
                   "parameters": layer_b.parameters_for(question_id, period),
                   "purpose": question["text"],
                   "input_artifact_ids": [], "depends_on_step_ids": []}],
        "repair_of_submission_id": ""}, f"tu-{question_id}")


def _finalizer(question_id: str, period: dict):
    """Build the answer from what actually came back, never from the oracle."""
    question = bank.BY_ID[question_id]
    column, unit = HEADLINE[question_id]

    def turn(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        artifact = step["artifact_id"]
        preview = step["preview"]
        group = question["group"][-1]
        key_column = next(
            (c for c in (group, "sector_name", "borrower_id",
                         "reporting_quarter") if preview and c in preview[0]),
            "")

        if not preview or not column:
            return ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                    understood=question["text"]),
                      narrative="No borrower meets all three conditions in "
                                "this quarter. That is the finding, not a "
                                "gap in the data.",
                      coverage=[{"subquestion": question["text"],
                                 "status": "answered", "evidence_refs": [
                                     {"artifact_id": artifact, "row_key": "0",
                                      "column_id": ""}]}],
                      tables=[{"title": question["text"][:80],
                               "artifact_id": artifact,
                               # The artifact's REAL columns. Naming a
                               # column it does not have renders an empty
                               # column, which reads as missing data rather
                               # than as an empty result.
                               "columns": list(preview[0]) if preview
                               else list(step["columns"])}]))],
                output_tokens=320)

        row_key = (f"{key_column}={preview[0][key_column]}" if key_column
                   else "0")
        value = preview[0][column]
        charts = []
        kind = CHART_KIND[question["chart"]]
        if kind and key_column:
            charts = [{"kind": kind, "title": question["text"][:80],
                       "artifact_id": artifact, "x_column": key_column,
                       "y_columns": [column], "unit": unit}]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood=question["text"]),
                  narrative=(f"For {period['latest']}, the leading line is "
                             f"{{{{claim.headline}}}} {unit}. "
                             f"{question['insight'][0]}."),
                  coverage=[{"subquestion": question["text"],
                             "status": "answered",
                             "evidence_refs": [
                                 {"artifact_id": artifact,
                                  "row_key": row_key,
                                  "column_id": column}]}],
                  numeric_claims=[{
                      "claim_id": "headline",
                      "decimal_value": repr(float(value)),
                      "unit": unit,
                      # Precision comes from the UNIT, never a blanket 2: a
                      # count has no decimal places and the policy refuses
                      # one that claims otherwise.
                      "display_precision": prec.default_precision(unit),
                      "evidence": {"artifact_id": artifact,
                                   "row_key": row_key,
                                   "column_id": column}}],
                  tables=[{"title": question["text"][:80],
                           "artifact_id": artifact,
                           "columns": list(preview[0])}],
                  charts=charts))],
            output_tokens=420)

    return turn


_RESULTS: dict[str, dict] = {}


@pytest.mark.parametrize("question_id", ALL)
def test_the_pipeline_publishes_the_answer_the_oracle_expects(
        question_id, drive, store_db, session, release_id):
    """Layer C. Real validator, real bind proof, real DuckDB, real finalizer."""
    period = bank.periods(release_id)
    question = bank.BY_ID[question_id]
    started = time.monotonic()
    outcome, provider, record = drive(
        question["text"],
        [ScriptedResult(tool_calls=[_execute_call(question_id, period)]),
         _finalizer(question_id, period)],
        mode="standard")
    elapsed_ms = int((time.monotonic() - started) * 1000)

    assert outcome.state == st.COMPLETED, (
        f"{question_id} did not complete: {outcome.message}")
    body = outcome.response
    assert body["executed"] is True, f"{question_id} published without SQL"
    assert "{{claim." not in body["narrative"], (
        f"{question_id} published an unrendered placeholder")

    # The stored artifact, row for row, against the pandas oracle.
    a = bank.oracle(question_id, release_id)
    stored_id = (body["tables"][0]["artifact_id"] if body["tables"] else "")
    stored = store_db.get_artifact(stored_id, tenant_id="demo-tenant")
    for label, got, want in _pairs(question_id, stored["rows"], a):
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            assert got == pytest.approx(float(want), rel=REL), (
                f"{question_id} {label}: the pipeline published {got}, the "
                f"oracle expects {want}")
        else:
            assert got == want, (
                f"{question_id} {label}: the pipeline published {got!r}, the "
                f"oracle expects {want!r}")

    # The declared visual matches the policy the bank states for it.
    kind = CHART_KIND[question["chart"]]
    if kind and a["row_count"]:
        assert body["charts"], (
            f"{question_id} warrants a {question['chart']}: "
            f"{question['chart_reason']}")
        assert body["charts"][0]["kind"] == kind
    elif not a["row_count"]:
        assert not body["charts"], (
            f"{question_id} returned no rows; there is nothing to chart")
    else:
        assert not body["charts"], (
            f"{question_id} warrants no chart: {question['chart_reason']}")

    events = [e.event_type for e in store_db.events_since(record.run_id)]
    assert ev.ANSWER_READY in events

    _RESULTS[question_id] = {
        "question_id": question_id, "category": question["category"],
        "question": question["text"], "mode": question["mode"],
        "terminal_state": outcome.state,
        "executed": body["executed"],
        "generations": len(provider.sent),
        "row_count": len(stored["rows"]),
        "oracle_row_count": a["row_count"],
        "rows_match_oracle": True,
        "chart_policy": question["chart"],
        "chart_published": (body["charts"][0]["kind"] if body["charts"]
                            else None),
        "chart_reason": question["chart_reason"],
        "numeric_claims": len(body["numeric_claims"]),
        "wall_ms": elapsed_ms,
    }


# ---- the answer-quality rubric -----------------------------------------

#: The brief's weights. The ones that are properties of what the PRODUCT
#: published are scored from the run; the ones that are properties of the
#: model's own judgement cannot be scored while the analyst turn is
#: scripted. Those are carried as `model_dependent` and left unscored rather
#: than awarded to a mock, because a rubric that grades the harness is worse
#: than no rubric.
#:
#: WHICH IS WHAT "visualization_judgement" WAS DOING. It was one row, worth
#: 10, marked not model-dependent, and its test read "a chart is published
#: exactly when the question warrants one". But the scripted analyst reads
#: `question["chart"]` out of the bank and emits that form, and the mark
#: then checks the published chart against the same field. The only way it
#: can fail is if the PIPELINE drops or mangles a chart the analyst
#: declared. That is worth marking and it is not judgement, and calling it
#: judgement put 10 of 80 points behind a name that claimed more than the
#: run had shown -- the exact failure the paragraph above says to avoid.
#:
#: So the 10 is split in half, between the part a mock can prove and the
#: part only a live analyst can. Splitting evenly is a reading of the
#: brief's single weight rather than a measurement, and it is named as one.
RUBRIC = [
    {"dimension": "numerical_correctness", "weight": 40,
     "model_dependent": False,
     "test": "every published figure reconciles to the pandas oracle"},
    {"dimension": "answers_what_was_asked", "weight": 20,
     "model_dependent": False,
     "test": "coverage names each subquestion and resolves to evidence"},
    {"dimension": "insight_and_interpretation", "weight": 15,
     "model_dependent": True,
     "test": "requires a live analyst turn; not scored against a mock"},
    {"dimension": "evidence_and_traceability", "weight": 10,
     "model_dependent": False,
     "test": "each claim's artifact, row and column resolve to the stored "
             "cell it quotes"},
    {"dimension": "visual_fidelity", "weight": 5,
     "model_dependent": False,
     "test": "the form the analyst declared is the form published, and a "
             "question warranting none publishes none"},
    {"dimension": "visualization_judgement", "weight": 5,
     "model_dependent": True,
     "test": "deciding whether this result warrants a chart at all is the "
             "analyst's call; a scripted turn was handed the answer"},
    {"dimension": "clarity_of_writing", "weight": 5,
     "model_dependent": True,
     "test": "requires a live analyst turn; not scored against a mock"},
]

SCORABLE = sum(r["weight"] for r in RUBRIC if not r["model_dependent"])


def test_the_rubric_still_adds_up_to_the_brief() -> None:
    assert sum(r["weight"] for r in RUBRIC) == 100
    assert SCORABLE == 75
    assert sorted(r["dimension"] for r in RUBRIC) == sorted(
        {r["dimension"] for r in RUBRIC}), "a dimension is listed twice"


def test_only_what_a_mock_can_prove_carries_a_mark() -> None:
    """The invariant the split was for.

    Every dimension this run SCORES must be one the run can establish
    without the model's judgement, and every dimension that needs a live
    analyst must carry no mark at all. A dimension that is both -- marked,
    and model-dependent -- is a mark awarded to the harness.
    """
    scored = {r["dimension"] for r in RUBRIC if not r["model_dependent"]}
    unscored = {r["dimension"] for r in RUBRIC if r["model_dependent"]}
    assert scored & unscored == set()
    assert "visualization_judgement" in unscored, (
        "choosing whether a result warrants a chart is the analyst's call; "
        "a scripted turn that was handed the form cannot demonstrate it")
    assert "visual_fidelity" in scored, (
        "that the declared form survives the pipeline IS provable here and "
        "should not have been dropped with the judgement claim")


def test_every_scored_dimension_is_one_the_scorecard_actually_marks() -> None:
    """A dimension in the rubric with no mark behind it inflates the
    denominator and makes the score unreachable; one marked but absent from
    the rubric is a mark nobody declared."""
    import inspect

    body = inspect.getsource(_score)
    for row in RUBRIC:
        marked = f'marks["{row["dimension"]}"]' in body
        assert marked is not row["model_dependent"], (
            f"{row['dimension']}: model_dependent={row['model_dependent']} "
            f"but {'is' if marked else 'is not'} marked in the scorecard")


def _score(question_id: str, body: dict, stored: dict, question: dict, *,
           rows: int = -1) -> dict:
    marks: dict[str, int] = {}

    marks["numerical_correctness"] = 40  # asserted above, row for row

    subquestions = {c["subquestion"] for c in body["coverage"]}
    answered = [c for c in body["coverage"] if c["status"] == "answered"]
    marks["answers_what_was_asked"] = (
        20 if subquestions and len(answered) == len(body["coverage"]) else 0)

    resolvable = True
    index = {str(i): row for i, row in enumerate(stored["rows"])}
    for row in stored["rows"]:
        for column, value in row.items():
            index[f"{column}={value}"] = row
    for claim in body["numeric_claims"]:
        cell = index.get(claim["evidence"]["row_key"])
        resolvable = resolvable and cell is not None and (
            claim["evidence"]["column_id"] in cell)
    marks["evidence_and_traceability"] = 10 if resolvable else 0

    # A question that returned no rows warrants no chart, whatever its
    # standing policy says: there is nothing to plot, and charting an empty
    # result would be the wrong judgement rather than the right one.
    kind = "" if rows == 0 else CHART_KIND[question["chart"]]
    published = body["charts"][0]["kind"] if body["charts"] else ""
    marks["visual_fidelity"] = 5 if published == kind else 0

    return {"marks": marks, "scored": sum(marks.values()),
            "scorable_total": SCORABLE,
            "not_scored": [r["dimension"] for r in RUBRIC
                           if r["model_dependent"]]}


@pytest.mark.parametrize("question_id", ALL)
def test_the_published_answer_scores_full_marks_on_what_can_be_measured(
        question_id, drive, store_db, release_id):
    period = bank.periods(release_id)
    question = bank.BY_ID[question_id]
    outcome, _, _ = drive(
        question["text"],
        [ScriptedResult(tool_calls=[_execute_call(question_id, period)]),
         _finalizer(question_id, period)])
    assert outcome.state == st.COMPLETED, outcome.message
    body = outcome.response
    stored = store_db.get_artifact(body["tables"][0]["artifact_id"],
                                   tenant_id="demo-tenant")
    card = _score(question_id, body, stored, question,
                  rows=bank.oracle(question_id, release_id)["row_count"])
    assert card["scored"] == SCORABLE, (
        f"{question_id} lost marks: "
        f"{[k for k, v in card['marks'].items() if v == 0]}")
    _RESULTS.setdefault(question_id, {})["rubric"] = card


# ---- what the product refuses, which is the other half of quality ------

def test_a_figure_the_artifact_does_not_hold_is_not_published(drive,
                                                              release_id):
    """Rubric dimension 1 is enforced, not merely measured."""
    period = bank.periods(release_id)

    def overstated(messages):
        body = json.loads(messages[-1]["content"][0]["content"])
        artifact = body["steps"][0]["artifact_id"]
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative="Total exposure is {{claim.x}} SAR million.",
                  numeric_claims=[{
                      "claim_id": "x", "decimal_value": "99999999.99",
                      "unit": "SAR million", "display_precision": 2,
                      "evidence": {"artifact_id": artifact,
                                   "row_key": "0",
                                   "column_id": "ead"}}]))])

    def stand_down(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="partial_answer",
                  narrative="The figure could not be tied to the evidence.",
                  limitations=["the claim did not match the artifact"]))])

    outcome, provider, _ = drive(
        bank.BY_ID["Q01"]["text"],
        [ScriptedResult(tool_calls=[_execute_call("Q01", period)]),
         overstated, stand_down])
    assert outcome.state == st.PARTIAL
    assert len(provider.sent) == 3, (
        "the overstated figure must be sent back to the analyst, not "
        "published")


def test_an_unbindable_query_is_refused_before_validation_is_announced(
        drive, release_id):
    """Rubric dimension 4: 'validated' may not precede the bind proof."""
    period = bank.periods(release_id)
    call = _execute_call("Q01", period)
    call["input"]["steps"][0]["code"] = (
        "SELECT sector_name, SUM(no_such_column) AS ead "
        "FROM cockpit_facility_quarter WHERE reporting_quarter = $q "
        "GROUP BY 1")

    def stand_down(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  disposition="unsupported",
                  narrative="That column is not in the release.",
                  limitations=["no_such_column does not exist"]))])

    outcome, provider, _ = drive(
        bank.BY_ID["Q01"]["text"],
        [ScriptedResult(tool_calls=[call]), stand_down])
    reply = json.dumps(provider.sent[-1]["messages"], default=str)
    assert "no_such_column" in reply, (
        "the analyst must receive the binder's own diagnostic")
    assert outcome.state in (st.COMPLETED, st.PARTIAL, st.UNSUPPORTED)


# ---- the benchmark artifact --------------------------------------------

def test_zz_write_the_benchmark_artifact(release_id):
    """Ordered last by name so it records what the other tests established."""
    if len(_RESULTS) < len(ALL):
        pytest.skip("the benchmark artifact is written by a full run of this "
                    "module")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release_id": release_id,
        "periods": bank.periods(release_id),
        "evidence_class": "MODEL MOCK · REAL DATABASE · REAL PIPELINE",
        "paid_provider_calls": 0,
        "what_this_proves": (
            "CreditProbe V4 validated, bound, executed, stored and published "
            "each of these twenty analyses, and every published figure "
            "matches an independent pandas oracle computed from the pinned "
            "release."),
        "what_this_does_not_prove": (
            "that the analyst model would choose these analyses, write this "
            "SQL, or write prose of this quality. The analyst turn is "
            "scripted in this module and the two model-dependent rubric "
            "dimensions are left unscored for that reason."),
        "rubric": RUBRIC,
        "scorable_total": SCORABLE,
        "questions": [_RESULTS[q] for q in ALL],
    }
    ARTIFACT.write_text(json.dumps(payload, indent=2) + "\n")
    assert ARTIFACT.exists()
