"""
What a correct answer to a four-step analysis actually weighs.

The live thread `th-48fdeffe125f489592f627e307852e31` was cut off writing
one of these. The question the incident turned on was whether the allowance
was merely tight or arithmetically impossible, and the only way to settle it
is to build the object and measure it.

So this builds one: five sub-questions over one dimension in one period,
answered the way the contract requires -- every figure bound to executed
evidence through a claim, the narrative referencing those claims, a table
per result, charts over the two that rank, plus coverage, limitations and
follow-ups. Nothing here is padding; strip any of it and the answer stops
satisfying `finalize_response`.

Used by `test_answer_allowance.py` to pin the sizing, and by
`scripts/cockpit_v4/answer_size_evidence.py` to record it.
"""

from __future__ import annotations

from typing import Any

#: Rows per result. `preview_rows` in every Limits set.
ROWS = 100

SECTORS = ["Construction", "Real Estate", "Manufacturing",
           "Wholesale and Retail", "Transport and Storage", "Utilities",
           "Mining and Quarrying", "Professional Services",
           "Accommodation and Food", "Agriculture"]

STEPS = [
    ("s1", "ECL and EAD by sector", ["sector", "ecl_sar_mn", "ead_sar_mn"]),
    ("s2", "Stage 3 exposure by sector",
     ["sector", "stage3_ead_sar_mn", "stage3_ratio"]),
    ("s3", "Covenant breaches by sector",
     ["sector", "breached_facilities", "breached_ead_sar_mn"]),
    ("s4", "Rating movement by sector",
     ["sector", "notches_moved", "downgraded_ead_sar_mn"]),
]

MONEY_UNIT = "SAR million"


def row_ids(count: int = ROWS) -> list[str]:
    from backend.cockpit_v4 import derivation as deriv

    return [deriv.row_id_for(i) for i in range(count)]


def preview(columns: list[str], count: int = ROWS) -> list[dict[str, Any]]:
    """Rows shaped like a real sector breakdown: a label and some money."""
    out = []
    for i in range(count):
        row: dict[str, Any] = {
            columns[0]: f"{SECTORS[i % len(SECTORS)]} segment {i:03d}"}
        for j, column in enumerate(columns[1:]):
            row[column] = round(118_728.4213 / (i + 1) * (j + 1), 4)
        out.append(row)
    return out


def artifact_id(step_id: str) -> str:
    return f"art-{step_id}-9f2c41ab"


def four_step_answer(*, shorthand: bool = True) -> dict[str, Any]:
    """The `finalize_response` body a correct four-step answer carries.

    Sixteen claims, four per result: a total and a share (both derived over
    the real rows) and the top two values (direct, one cell each). That is
    the shape of every ranked breakdown a credit reader asks for, and the
    derivations are where the weight is.

    Both spellings are buildable, and the difference between them is the
    whole point. With `shorthand=False` a `sum` over 100 rows names 100 row
    ids, twelve times over, which is how the object came to be two-thirds
    row ids and to overrun the allowance the live thread was cut off at.
    That form is kept -- not as history but as evidence: the regression is
    only pinned if the object that overran can still be built and measured.
    """
    ids = row_ids()
    whole = (lambda art, column: {
        "artifact_id": art, "column_id": column, "rows": "all"}) if shorthand \
        else (lambda art, column: {
            "artifact_id": art, "column_id": column, "row_ids": ids})
    claims: list[dict[str, Any]] = []
    for n, (step_id, _purpose, columns) in enumerate(STEPS):
        art, value_column = artifact_id(step_id), columns[-1]
        claims.append({
            "claim_id": f"total_{n}", "unit": MONEY_UNIT,
            "derivation": {"operation": "sum", "operands": [
                whole(art, value_column)]}})
        claims.append({
            "claim_id": f"share_{n}", "unit": "percent",
            "derivation": {"operation": "percentage", "operands": [
                {"artifact_id": art, "column_id": value_column,
                 "row_ids": ids[:4]},
                whole(art, value_column)]}})
        for rank, label in ((0, "top"), (1, "second")):
            claims.append({
                "claim_id": f"{label}_{n}", "unit": MONEY_UNIT,
                "evidence": {"artifact_id": art, "row_key": ids[rank],
                             "column_id": value_column}})

    narrative = (
        "Expected credit loss across the corporate book stands at "
        "{{claim.total_0}} against exposure at default of "
        "{{claim.total_1}}, and the concentration is the story: the four "
        "largest sectors carry {{claim.share_0}} of the loss, led by "
        "{{claim.top_0}} in Construction with Real Estate behind it at "
        "{{claim.second_0}}.\n\n"
        "Stage 3 exposure is {{claim.total_1}}, or {{claim.share_1}} of "
        "the book. That ratio is not evenly spread — Construction alone "
        "accounts for {{claim.top_1}} of it and the next sector down is "
        "{{claim.second_1}}, so the head is heavy and the tail is short."
        "\n\n"
        "Covenant breaches cover {{claim.total_2}} of exposure, "
        "{{claim.share_2}} of the book, concentrated in the same names: "
        "{{claim.top_2}} in the leading sector against {{claim.second_2}} "
        "in the next. Where breaches and Stage 3 overlap the exposure is "
        "already provisioned; where they do not, they are the "
        "forward-looking signal.\n\n"
        "Rating movement runs the same way. {{claim.total_3}} of exposure "
        "sat behind a downgrade this quarter, {{claim.share_3}} of the "
        "book, with {{claim.top_3}} in the worst-affected sector. Read "
        "together, the four views point at one concentration rather than "
        "four separate ones.")

    return {
        "intent": {
            "query_mode": "DATA_ANALYSIS", "owner": "CREDIT_RISK",
            "understood_request": (
                "Expected credit loss, exposure at default, Stage 3 "
                "exposure, covenant breaches and rating movement by sector "
                "for the latest reporting quarter, with the concentration "
                "called out."),
            "response_language": "en", "blocking_ambiguities": None,
            "resolved_assumptions": [
                "Latest reporting quarter means the most recent quarter "
                "present in the pinned release.",
                "Sector means the borrower's reported sector, not the "
                "facility's."],
            "canonical_mappings": None, "excluded_parts": None,
            "public_rationale": (
                "Five measures over one dimension in one period; each runs "
                "as its own query and the answer reads them together.")},
        "disposition": "answer", "narrative": narrative,
        "coverage": [{"subquestion": purpose, "status": "answered"}
                     for _, purpose, _ in STEPS]
        + [{"subquestion": "Where is the concentration?",
            "status": "answered"}],
        "numeric_claims": claims,
        "evidence_refs": [artifact_id(s) for s, _, _ in STEPS],
        "tables": [{"artifact_id": artifact_id(s), "title": p,
                    "columns": c} for s, p, c in STEPS],
        "charts": [
            {"artifact_id": artifact_id("s1"), "kind": "bar",
             "title": "ECL by sector", "category_column": "sector",
             "value_column": "ecl_sar_mn"},
            {"artifact_id": artifact_id("s2"), "kind": "bar",
             "title": "Stage 3 exposure by sector",
             "category_column": "sector",
             "value_column": "stage3_ead_sar_mn"}],
        "limitations": [
            "Covenant breach counts are as recorded at quarter end and do "
            "not reflect waivers granted after the reporting date.",
            "Rating movement is measured in notches over one quarter and "
            "does not distinguish a downgrade within investment grade from "
            "one that crosses into speculative grade."],
        "suggested_questions": [
            {"question": "How has ECL in Construction moved over the last "
                         "four quarters?", "kind": "drill_down"},
            {"question": "Which borrowers drive the Stage 3 exposure in "
                         "Real Estate?", "kind": "drill_down"},
            {"question": "What is the overlap between covenant breaches "
                         "and Stage 3 classification?", "kind": "related"},
            {"question": "How does the sector concentration compare with "
                         "the Retail book?", "kind": "related"}],
        "clarification_question": "", "clarification_options": [],
        "referral_owner": "", "referral_reason": ""}


def four_step_tool_result() -> dict[str, Any]:
    """The `execute_analysis` result the answer turn has to read first."""
    from backend.cockpit_v4.execute_tool import claim_guide

    ids = row_ids()
    steps = []
    for step_id, purpose, columns in STEPS:
        art = artifact_id(step_id)
        units = {c: (MONEY_UNIT if c.endswith("_sar_mn") else "")
                 for c in columns}
        steps.append({
            "step_id": step_id, "status": "ok", "language": "sql",
            "purpose": purpose,
            "executed_code_digest": "sha256:" + "a1b2c3d4" * 8,
            "elapsed_ms": 41, "columns": columns, "row_count": ROWS,
            "preview": preview(columns), "row_ids": ids,
            "preview_truncated": False, "artifact_id": art,
            "column_units": units,
            "how_to_cite_these_numbers": claim_guide(
                art, columns, ids, ROWS, MONEY_UNIT)})
    return {"submission_id": "sub-1", "status": "ok",
            "validated_whole_batch_before_running": True, "steps": steps}
