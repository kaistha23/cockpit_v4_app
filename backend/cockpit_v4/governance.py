"""How a question became a number, assembled from what was already stored.

The gap this fills
------------------
A reader pressed "Trace" and got the process panel: a list of stages with
ticks against them. It said the analysis had run. It did not say what was
understood from the question, which governed values the reader's own words
resolved to, what query was written, whether a query was refused, which
relations were read, or how the figure in the second paragraph reached the
page from the row it came out of.

Every one of those was ALREADY ON FILE. The exact SQL of every submission,
byte for byte, including the ones the binder refused and which therefore
never ran. The bind proof and the checks each step passed. The intent
envelope with its resolved entities and open questions. The row id of
every table cell and every chart point. The release id and the fingerprint
of the bytes. None of it had a route.

So this module reads, and does not compute. It opens no model, runs no
query and recalculates no figure; it assembles stored records into the
order a person reads them.

What it will not do
-------------------
Claim row-level provenance it does not have. Lineage here stops at the
result set: an artifact row can be traced to the step and the query that
produced it, and NOT to the source rows that were aggregated into it. The
record says so at that boundary, in the payload, rather than laying out a
waterfall that implies a link nobody can follow.

Who may see the SQL
-------------------
The query is the bank's logic. It is shown to an administrator and
withheld from everyone else -- with the step, its purpose, its checks and
its outcome all still shown, because the point of a governance record is
that a reader can see the controls worked without being handed the
internals.
"""

from __future__ import annotations

from typing import Any

from backend.cockpit_v4 import events as ev

#: The role that may read the queries. Everything else in the record is
#: shown to every reader who can open the run.
SQL_ROLE = "administrator"

#: What stands where the SQL would be, for a reader without that role.
SQL_WITHHELD = ("The query itself is shown to administrators. Everything "
                "else about this step -- its purpose, the checks it passed, "
                "the relations it read and the rows it produced -- is here.")


def may_read_sql(who: dict[str, Any]) -> bool:
    """Whether this reader sees the queries.

    Case-folded: a principal carrying "Administrator" and one carrying
    "administrator" are the same person, and a governance record that
    turned on the capitalisation of a role name would be a bug nobody
    could reproduce.
    """
    roles = who.get("roles") or ()
    if isinstance(roles, str):
        roles = (roles,)
    return any(str(role).strip().lower() == SQL_ROLE for role in roles)


def _detail(details: dict[str, dict[str, Any]], ref: str) -> dict[str, Any]:
    return details.get(str(ref or ""), {}) or {}


def question_block(record: Any, events: list[Any],
                   details: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The question, as typed and as stored.

    NOT "how your English was corrected". `intake.py` states the policy in
    its own header and this record repeats it rather than implying
    otherwise: CreditProbe does not rewrite a reader's sentence. What
    normalisation does is mechanical -- Unicode composition, zero-width and
    bidi removal, whitespace folding -- and it is reported as exactly that.

    A question nothing changed carries no report, because there is nothing
    to report. Inventing one would suggest the text had been edited.
    """
    asked = str(getattr(record, "question", "") or "")
    for event in events:
        if event.event_type != ev.RUN_ACCEPTED or event.operation != "normalize":
            continue
        body = _detail(details, event.detail_ref)
        report = body.get("normalization") or {}
        if not report:
            continue
        return {
            "asked": str(body.get("original") or asked),
            "analysed": asked,
            "changed": bool(report.get("changed")),
            "normalisation": report,
            "policy": ("Mechanical text normalisation only. The wording of "
                       "the question is never rewritten, and no spelling "
                       "correction is applied to the sentence."),
        }
    return {"asked": asked, "analysed": asked, "changed": False,
            "normalisation": {}, "policy": ("The question was stored exactly "
                                            "as it was typed.")}


def interpretation_block(events: list[Any],
                         details: dict[str, dict[str, Any]],
                         answer: dict[str, Any]) -> dict[str, Any]:
    """What was understood, and what the reader's own words resolved to.

    The envelope is recovered from the FIRST `intent.validated` detail,
    which is the only place it is written whole. Every later one carries
    the flat nine-field intent, and `final_response["intent"]` is that flat
    form too -- so a record built from the answer alone would be missing
    the domain, the fingerprint, the period semantics and every value
    resolution.
    """
    envelope: dict[str, Any] = {}
    for event in events:
        if event.event_type != ev.INTENT_VALIDATED:
            continue
        body = _detail(details, event.detail_ref)
        if body.get("intent_id"):
            envelope = body
            break
    flat = dict((answer or {}).get("intent") or {})

    def field(name: str) -> Any:
        return envelope.get(name) or flat.get(name) or ""

    resolutions = []
    for entry in (envelope.get("resolved_entities") or ()):
        if not isinstance(entry, dict):
            continue
        resolutions.append({
            # The phrase the reader typed, kept beside the governed value
            # it resolved to. THIS is the correction that actually happens
            # -- "prject finance" to the catalogue's "Project Finance" --
            # and it is a value lookup, not a spelling fix on the sentence.
            "you_typed": str(entry.get("term") or ""),
            "resolved_to": str(entry.get("value") or ""),
            "on_field": str(entry.get("field") or ""),
            "relation": str(entry.get("relation") or ""),
            "exact": bool(entry.get("exact")),
            "recorded_as": str(entry.get("record_as") or ""),
        })

    return {
        "intent_id": str(envelope.get("intent_id") or ""),
        "understood_request": str(field("understood_request")),
        "query_mode": str(field("query_mode")),
        "owner": str(field("owner")),
        "response_language": str(field("response_language") or "en"),
        "domain_id": str(envelope.get("domain_id") or ""),
        "domain_label": str(envelope.get("domain_label") or ""),
        "period": envelope.get("period") or {},
        "canonical_mappings": list(field("canonical_mappings") or ()),
        "resolved_assumptions": list(field("resolved_assumptions") or ()),
        "blocking_ambiguities": list(field("blocking_ambiguities") or ()),
        "excluded_parts": list(field("excluded_parts") or ()),
        "open_questions": list(envelope.get("open_questions") or ()),
        "value_resolutions": resolutions,
        "rationale": str(field("public_rationale")),
    }


def _step_events(events: list[Any],
                 details: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What the run recorded about each step, keyed by step id."""
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        body = _detail(details, event.detail_ref)
        step_id = str(body.get("step_id") or "")
        if not step_id:
            continue
        seen = out.setdefault(step_id, {})
        if event.event_type == ev.TOOL_FAILED:
            seen["failed"] = {
                "phase": str(body.get("phase") or body.get("stage") or ""),
                "executed": bool(body.get("executed")),
                "failed_check": str(body.get("failed_check") or ""),
                "error_code": str(body.get("error_code") or ""),
                "message": str(body.get("message") or ""),
            }
        elif event.event_type == ev.TOOL_COMPLETED:
            seen["completed"] = {"rows": body.get("produced_rows"),
                                 "artifact_id": str(body.get("artifact_id") or "")}
    return out


def submissions_block(submissions: list[dict[str, Any]],
                      events: list[Any],
                      details: dict[str, dict[str, Any]],
                      artifacts: dict[str, dict[str, Any]],
                      *, show_sql: bool) -> list[dict[str, Any]]:
    """Every batch of code this run submitted, and what became of it.

    A REFUSED SUBMISSION IS A FIRST-CLASS ENTRY. It carries the same
    fields as one that ran -- its purpose, its language, its exact code --
    with `ran: false` and the reason it was stopped. Leaving it out would
    produce a record in which the controls appear never to have been
    tested.
    """
    per_step = _step_events(events, details)
    bind_by_ordinal: dict[int, dict[str, Any]] = {}
    refusal_by_ordinal: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.event_type == ev.TOOL_VALIDATED:
            body = _detail(details, event.detail_ref)
            if body.get("bound_now") is not None or body.get(
                    "bound_at_run_time") is not None:
                bind_by_ordinal[int(event.submission or 0)] = {
                    "bound_now": list(body.get("bound_now") or ()),
                    "bound_at_run_time": list(
                        body.get("bound_at_run_time") or ()),
                }
        elif (event.event_type == ev.TOOL_FAILED
              and str(event.status or "") == ev.STATUS_REJECTED):
            body = _detail(details, event.detail_ref)
            refusal_by_ordinal[int(event.submission or 0)] = {
                "stage": str(body.get("stage") or ""),
                "error_code": str(body.get("error_code") or ""),
                "field": str(body.get("field") or ""),
                "message": str(body.get("message") or ""),
            }

    out: list[dict[str, Any]] = []
    for submission in submissions:
        payload = submission.get("payload") or {}
        ordinal = int(submission.get("ordinal") or 0)
        status = str(submission.get("status") or "")
        refused = status == "rejected"
        steps: list[dict[str, Any]] = []
        for step in (payload.get("steps") or ()):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or "")
            seen = per_step.get(step_id, {})
            artifact = artifacts.get(
                str((seen.get("completed") or {}).get("artifact_id") or ""), {})
            scope = (artifact.get("scope") or {}) if artifact else {}
            steps.append({
                "step_id": step_id,
                "purpose": str(step.get("purpose") or ""),
                "language": str(step.get("language") or ""),
                # Byte for byte as submitted. Never reformatted: a query
                # shown prettier than it ran is a query that was not shown.
                "code": (str(step.get("code") or "") if show_sql
                         else SQL_WITHHELD),
                "code_shown": show_sql,
                "parameters": step.get("parameters") or {},
                "depends_on": list(step.get("depends_on_step_ids") or ()),
                "input_artifact_ids": list(step.get("input_artifact_ids") or ()),
                "artifact_id": artifact.get("artifact_id", ""),
                "rows_out": artifact.get("row_count"),
                "columns_out": list(artifact.get("columns") or ()),
                "code_digest": str(artifact.get("code_digest") or ""),
                # WHAT THE QUERY READ, not what it was allowed to read.
                "relations_read": list(scope.get("referenced_relations") or ()),
                "relations_authorized": list(scope.get("relations") or ()),
                "complete": scope.get("complete"),
                "failed": seen.get("failed"),
            })
        out.append({
            "ordinal": ordinal,
            "submission_id": str(submission.get("submission_id") or ""),
            "round": int(submission.get("round") or 0),
            "status": status,
            "ran": not refused,
            "refusal": refusal_by_ordinal.get(ordinal) if refused else None,
            "objective": str(payload.get("objective") or ""),
            "subquestions": list(payload.get("subquestions") or ()),
            "fields_required": list(payload.get("fields_required") or ()),
            "expected_grain": str(payload.get("expected_output_grain") or ""),
            "expected_units": payload.get("expected_units") or {},
            "repair_of": str(payload.get("repair_of_submission_id") or ""),
            "bind_proof": bind_by_ordinal.get(ordinal) or {},
            "failed_step": str(submission.get("failed_step") or ""),
            "steps": steps,
            "created_at": str(submission.get("created_at") or ""),
        })
    return out


#: Said at the boundary of what the lineage can support, in the payload,
#: so the panel states it rather than implying a link it does not have.
LINEAGE_LIMIT = (
    "Provenance stops at the result set. Every published figure is traced "
    "to a stored row of a stored result, and that result to the query and "
    "the release that produced it. The individual source rows that were "
    "aggregated into it are not retained, so a total cannot be opened up "
    "into the exposures behind it.")


def waterfall_block(answer: dict[str, Any],
                    artifacts: dict[str, dict[str, Any]],
                    release: dict[str, Any]) -> dict[str, Any]:
    """Release to relation to step to artifact to figure, in that order.

    Every rung is a stored fact. The chain is assembled here and nowhere
    computed: a chart point already carries its row id, a table cell
    already carries its row id, and a narrative number already carries
    either the cell it came from or the arithmetic the server recomputed.
    """
    rows: list[dict[str, Any]] = []
    for artifact_id, artifact in artifacts.items():
        scope = artifact.get("scope") or {}
        rows.append({
            "artifact_id": artifact_id,
            "step_id": str(scope.get("step_id") or ""),
            "language": str(scope.get("language") or "sql"),
            "relations_read": list(scope.get("referenced_relations") or ()),
            "release_id": str(artifact.get("release_id") or ""),
            "release_fingerprint": str(scope.get("release_fingerprint") or ""),
            "columns": list(artifact.get("columns") or ()),
            "rows": artifact.get("row_count"),
            "complete": scope.get("complete"),
            "code_digest": str(artifact.get("code_digest") or ""),
            "published_as": _published_from(answer, artifact_id),
        })
    rows.sort(key=lambda row: (row["step_id"], row["artifact_id"]))

    claims = []
    for claim in ((answer or {}).get("numeric_claims") or ()):
        if not isinstance(claim, dict):
            continue
        evidence = claim.get("evidence") or {}
        claims.append({
            "claim_id": str(claim.get("claim_id") or ""),
            "published": str(claim.get("display") or claim.get("value") or ""),
            "unit": str(claim.get("unit") or ""),
            # One or the other, never both: a figure is either read off a
            # stored cell or computed from operands the server re-ran.
            # `row_key`, which is what `EvidenceRef` calls it. The published
            # table and chart call the same handle `row_id`, and the record
            # keeps the reference's own name so a reader matching this
            # against the stored answer is looking at the same word.
            "from_cell": ({"artifact_id": str(evidence.get("artifact_id") or ""),
                           "row_key": str(evidence.get("row_key") or ""),
                           "column_id": str(evidence.get("column_id") or "")}
                          if evidence.get("artifact_id") else None),
            "from_arithmetic": claim.get("derivation") or None,
        })

    return {"release": release, "artifacts": rows, "claims": claims,
            "limit": LINEAGE_LIMIT}


def _published_from(answer: dict[str, Any], artifact_id: str) -> list[dict[str, Any]]:
    """Which tables and charts a reader saw this artifact as."""
    out: list[dict[str, Any]] = []
    for table in ((answer or {}).get("tables") or ()):
        if isinstance(table, dict) and table.get("artifact_id") == artifact_id:
            out.append({"kind": "table", "title": str(table.get("title") or ""),
                        "rows_shown": len(table.get("rows") or ())})
    for index, chart in enumerate((answer or {}).get("charts") or ()):
        if isinstance(chart, dict) and chart.get("artifact_id") == artifact_id:
            out.append({"kind": "chart", "index": index,
                        "form": str(chart.get("kind") or ""),
                        "title": str(chart.get("title") or ""),
                        "points": len(chart.get("points") or ()),
                        "x_axis": (chart.get("x_axis") or {}).get("label", ""),
                        "y_axis": (chart.get("y_axis") or {}).get("label", ""),
                        # Why this FORM rather than the table beside it, in
                        # the analyst's own words. Optional in the contract,
                        # so it is read as a string and an absent rationale
                        # shows as an absent rationale -- the record never
                        # invents a justification the analyst did not give.
                        "why_this_chart": str(
                            chart.get("why_this_chart") or "")})
    return out
