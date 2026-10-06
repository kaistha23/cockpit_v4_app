"""
Everything a seeded investigation already knows, in one bounded packet.

The defect this exists for
--------------------------
A reader clicked the Construction card on the Corporate dashboard and the
thread it opened died with CALL_LIMIT. It had not answered anything. The
sequence was: read the catalogue, read the catalogue again, read product
knowledge, and run out of calls.

None of those calls could have told it anything the server did not already
know. CreditProbe COMPUTED that card. It knows the relation the finding came
from, the column the segment lives in, the exact governed value of the
segment, the measure's own columns, the period and the comparison period, and
which five questions the reader can ask next -- it wrote them. A thread that
has to go looking for any of that is looking for something it was handed.

So a seeded thread opens with an ANALYSIS PACKET: not a catalogue, not a
document, and not a method. It is the field facts behind one card, bounded by
construction and assembled without a model call, so the first action a seeded
run takes can be `execute_analysis`.

What it deliberately is not
---------------------------
It is not the answer, and it does not choose the analysis. Which measure to
report, how to aggregate it, which periods to compare and what the number
means are the analyst's, and nothing here decides any of them. It also does
not replace `inspect_catalog`: a question that turns on a column the packet
does not carry can still ask for one. The point is that the ORDINARY seeded
question -- "what drove this?", "who is behind it?", "split it by product" --
needs no lookup at all.
"""

from __future__ import annotations

import json
from typing import Any

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import values as val_mod

#: How many governed values of the segment's own dimension are listed. The
#: whole point of the packet is that it is small: a reader investigating
#: Construction needs to know the dimension is closed and what its neighbours
#: are called, not to receive seventy-five sub-sectors.
MAX_PEERS = 24

#: How many columns of the finding's relation are described in full. The
#: covenant relation has fifty-nine, and a packet that carried all of them
#: would be the relation dump this exists to avoid.
MAX_FIELDS = 18

#: Columns every finding needs regardless of its measure, because the
#: question after "what changed" is almost always "how big is it" or "how
#: much of it is impaired".
_ALWAYS = ("ead_sar_mn", "ecl_sar_mn", "stage")


def _field_facts(catalog: Any, relation: str, column: str
                 ) -> dict[str, Any] | None:
    try:
        spec = catalog.resolve(relation, column)
    except Exception:  # noqa: BLE001 - a column this book lacks is not a fact
        return None
    if spec is None:
        return None
    return {
        "field": f"{relation}.{column}",
        "column": column,
        "label": getattr(spec, "label", column),
        "dtype": spec.dtype,
        "unit": spec.unit,
        "adds_up": spec.additive == "additive",
        "means": spec.description,
    }


def _peers(session: Any, relation: str, column: str) -> list[str]:
    """The other values of the segment's own dimension, from the release."""
    try:
        rows = session.connection.execute(
            f'SELECT DISTINCT "{column}" AS v FROM "{relation}" '
            f'WHERE "{column}" IS NOT NULL AND "{column}" <> \'\' '
            f'ORDER BY 1 LIMIT {MAX_PEERS + 1}').fetchall()
    except Exception:  # noqa: BLE001
        return []
    seen = [str(row[0]) for row in rows]
    return seen if len(seen) <= MAX_PEERS else []


def analysis_packet(*, catalog: Any, session: Any, seed: dict[str, Any],
                    scope: Any = None) -> dict[str, Any]:
    """What this investigation can be answered from, without a lookup.

    Assembled from the card the reader clicked and the governed catalogue
    that produced it. No model call, no search, and nothing invented: every
    column named here is resolved against the catalogue and dropped if this
    book does not have it.
    """
    if not seed:
        return {}
    domain_id = str(seed.get("domain_id")
                    or getattr(catalog, "domain_id", "") or "")
    if not domain_id:
        return {}

    drill = dict(seed.get("drilldown") or {})
    questions = list(drill.get("suggested_questions") or [])
    relation = str(drill.get("relation")
                   or (questions[0].get("relation") if questions else "")
                   or "")
    if not relation:
        # Every family names its relation; a seed without one is a seed from
        # an older feed, and guessing which relation a finding came from is
        # exactly the kind of inference this packet exists to remove.
        return {}

    dimension = str(seed.get("segment_dimension") or "")
    segment = str(seed.get("segment") or "")
    period_column = schema_mod.period_column(domain_id)
    noun = schema_mod.period_noun(domain_id)

    # The measure's own columns first, then the columns every finding needs,
    # then the lenses the reader can cut this by. Ordered, de-duplicated and
    # capped, so the packet is the same size whatever the card was.
    # `portfolio` is a book-level card's dimension and is not a column of
    # anything; naming it here would put a field in the packet that the
    # catalogue then has to drop, which reads as a book missing a column it
    # was computed from.
    from backend.cockpit_v4 import attention_v2 as att_mod

    synthetic = set(att_mod.SYNTHETIC_DIMENSIONS)
    wanted: list[str] = []
    for column in (list(drill.get("measure_fields") or [])
                   + [f for q in questions
                      for f in (q.get("required_fields") or [])]
                   + list(_ALWAYS)
                   + [dimension, period_column]):
        column = str(column or "").strip()
        if column and column not in wanted and column not in synthetic:
            wanted.append(column)

    fields: list[dict[str, Any]] = []
    for column in wanted:
        facts = _field_facts(catalog, relation, column)
        if facts is not None:
            fields.append(facts)
        if len(fields) >= MAX_FIELDS:
            break

    spec = catalog.spec(relation)
    body: dict[str, Any] = {
        "what_this_is": (
            "The field facts behind the card this conversation was opened "
            "from, already resolved. You do not need to look any of this up. "
            "Which of these columns the answer needs, how to aggregate them "
            "and which periods to compare are yours to decide."),
        "book": dom.LABELS.get(domain_id, domain_id),
        "relation": relation,
        "grain": spec.grain,
        "key_columns": list(spec.key_columns),
        "period_column": period_column,
        "period_noun": noun,
        "reporting_period": str(seed.get("reporting_period") or ""),
        "comparison_period": str(seed.get("comparison_period") or ""),
        "fields": fields,
    }

    if dimension and segment and dimension not in synthetic:
        peers = _peers(session, relation, dimension)
        body["subject"] = {
            "column": dimension,
            # The value SQL filters on, and the words a reader reads. Both,
            # because the card shows the label and the query needs the value.
            "value": segment,
            "label": val_mod.pretty(segment),
            "filter": f"{dimension} = '{segment}'",
            "measure": str(seed.get("metric_label")
                           or seed.get("metric") or ""),
        }
        if peers:
            body["subject"]["other_values_of_this_column"] = peers

    if questions:
        body["questions_already_offered"] = [
            {"question": q.get("question", ""),
             "uses": list(q.get("required_fields") or []),
             "kind": q.get("kind", "")}
            for q in questions]
    return body


def unresolved(*, catalog: Any, packet: dict[str, Any]) -> list[str]:
    """Anything in this packet the catalogue cannot confirm.

    Used by the regression rather than at runtime: `analysis_packet` drops
    what it cannot resolve, so a non-empty list here means the DROPPING is
    what needs explaining -- a card computed from a column its own book does
    not admit to having.
    """
    problems: list[str] = []
    relation = str(packet.get("relation") or "")
    if not relation:
        return ["the packet names no relation"]
    for entry in packet.get("fields") or []:
        if _field_facts(catalog, relation, entry["column"]) is None:
            problems.append(f"{relation}.{entry['column']}")
    subject = packet.get("subject") or {}
    if subject and _field_facts(catalog, relation,
                                subject["column"]) is None:
        problems.append(f"{relation}.{subject['column']}")
    return problems


def executable(*, catalog: Any, session: Any, question: dict[str, Any],
               domain_id: str) -> tuple[bool, str]:
    """Whether a suggested question could actually be run.

    §22. A chip the reader clicks and that cannot be answered is worse than
    no chip: they have been told the book holds something it does not. So
    each one is checked against the catalogue AND bound against the engine
    before it is offered -- the columns exist on the relation named, the
    period column is this book's, and every period the question pins is one
    the release publishes.
    """
    relation = str(question.get("relation") or "")
    if not relation:
        return False, "the question names no relation"
    if schema_mod.domain_of_relation(relation) != domain_id:
        return False, f"{relation} belongs to another book"
    # THE CATALOGUE THIS FUNCTION WAS GIVEN. It took `catalog` as a keyword
    # argument and then asked `schema_mod` for the domain's current columns,
    # which is the one thing its own docstring says it does not do: on a
    # superseded release it would call a chip executable because today's
    # schema has the column, and the SQL would then fail at execution --
    # exactly the "told the book holds something it does not" this exists
    # to prevent.
    try:
        columns = set(catalog.columns(relation))
    except Exception:  # noqa: BLE001 - a relation this release never had
        return False, f"{relation} is not in this release"

    for column in question.get("required_fields") or []:
        if str(column) not in columns:
            return False, f"{relation} has no column {column}"

    period_column = schema_mod.period_column(domain_id)
    stated = str(question.get("period_column") or period_column)
    if stated != period_column:
        return False, (f"the question reports against {stated}, and this "
                       f"book keeps its periods in {period_column}")

    published = set()
    try:
        published = {str(row[0]) for row in session.connection.execute(
            f'SELECT DISTINCT "{period_column}" FROM "{relation}"').fetchall()}
    except Exception:  # noqa: BLE001
        return False, f"{relation} could not be read"
    for period in question.get("required_periods") or []:
        if str(period) not in published:
            return False, f"{relation} has no {period}"

    segment = str(question.get("segment") or "")
    dimension = str(question.get("segment_dimension") or "")
    if segment and dimension:
        if dimension not in columns:
            return False, f"{relation} has no column {dimension}"
        try:
            rows = session.connection.execute(
                f'SELECT 1 FROM "{relation}" WHERE "{dimension}" = ? '
                f'LIMIT 1', [segment]).fetchall()
        except Exception:  # noqa: BLE001
            return False, f"{relation}.{dimension} could not be read"
        if not rows:
            return False, (f"{relation} holds no {dimension} = {segment!r}, "
                           f"so this question is about nothing")
    return True, ""


def size(packet: dict[str, Any]) -> int:
    """The packet's cost in characters. Bounded by construction; measured
    here so the bound is a test rather than an intention."""
    return len(json.dumps(packet, ensure_ascii=False, default=str))


__all__ = ["MAX_FIELDS", "MAX_PEERS", "analysis_packet", "executable",
           "size", "unresolved"]
