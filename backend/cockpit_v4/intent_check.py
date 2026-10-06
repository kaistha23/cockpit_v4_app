"""Does the governed plan answer the question that was ASKED?

WHY this exists. The plan compiler guarantees that a plan is valid, governed
SQL. It cannot guarantee that the plan is the one the reader wanted, and the
local model quietly submits plans that are valid and wrong:

  * "Average ECL for Region Riyadh in 2026Q1" arrived as period "latest"
    (2026Q2) -- a well-formed answer about a different quarter.
  * "... for Riyadh" arrived with `region` as a GROUP BY dimension and no
    filter, so every region came back and the answer was read as Riyadh's.
  * "How many facilities that were Stage 1 or 2 in Q4 2023 migrated to Stage 3
    by Q1 2026?" arrived as a single latest-month plan; a plan cannot express
    a period-to-period transition at all.

Each is a silent wrong answer, so the check is deliberately HIGH PRECISION:
it only fires on facts the server already holds (periods spelled in the
question, entity matches the resolver marked exact) and it errs towards
saying nothing. Rules 1 and 2 REFUSE (the analyst gets the exact correction
and a bounded plan retry); rule 3 only WARNS, because "average" in English is
looser than a metric label; rule 4 (operation_dropped) is SHADOW: recorded,
never refused, until its precision is measured.

Modes, from COCKPIT_V4_INTENT_CHECK: `enforce` (default) refuses, `shadow`
records what it would have refused without refusing, `off` does nothing.
Anything unrecognised is `enforce`, never silently off.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import plan_compiler as pc
from backend.cockpit_v4 import values as values_mod
from backend.cockpit_v4.contracts import Rejection

REFUSE = "refuse"
WARN = "warn"
MODES = ("off", "shadow", "enforce")
ENV = "COCKPIT_V4_INTENT_CHECK"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "refuse" | "warn"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "detail": dict(self.detail)}


def mode() -> str:
    value = os.environ.get(ENV, "").strip().lower()
    return value if value in MODES else "enforce"


# ------------------------------------------------------------ question periods

_Q_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"\b(\d{4})\s*[-/]?\s*q([1-4])\b"), lambda m: (int(m[1]), int(m[2]), None)),
    (re.compile(r"\bq([1-4])\s*[-/,]?\s*(?:of\s+)?(?:fy\s*)?(\d{4})\b"),
     lambda m: (int(m[2]), int(m[1]), None)),
    (re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(\d{4})\b"),
     lambda m: (int(m[2]), _Q_ORD[m[1]], None)),
    (re.compile(r"(?<![\d-])(\d{4})-(0?[1-9]|1[0-2])(?![\d-])"),
     lambda m: (int(m[1]), None, int(m[2]))),
    (re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s+(\d{4})\b"),
     lambda m: (int(m[2]), None, pc._MONTHS[m[1]])),
)


def named_periods(question: str) -> list[tuple[int, int | None, int | None]]:
    """(year, quarter, month) for every period the question spells, in order."""
    text = str(question or "").lower()
    found: list[tuple[int, tuple[int, int | None, int | None]]] = []
    for pattern, build in _PERIOD_PATTERNS:
        for m in pattern.finditer(text):
            found.append((m.start(), build(m)))
            # Blank the span so a later pattern cannot read it a second way
            # ("Q1 2026" must not also surface as a bare year-month).
            text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    seen: list[tuple[int, int | None, int | None]] = []
    for _, p in sorted(found, key=lambda item: item[0]):
        if p not in seen:
            seen.append(p)
    return seen


def _label(p: tuple[int, int | None, int | None]) -> str:
    y, q, mo = p
    return f"{y}Q{q}" if q else f"{y}-{mo:02d}"


_TRANSITION_STRONG = re.compile(
    r"\b(migrat\w*|moved|move[sd]? (?:in|out|from|to)|transition\w*|roll(?:ed|s)? (?:in|out)|"
    r"cured|became|become)\b")
_TRANSITION_WEAK = re.compile(r"\bfrom\b.+\bto\b|\bbetween\b.+\band\b")
_CHANGE_WORDS = re.compile(r"\b(change|growth|grew|increase\w*|decrease\w*|trend|difference|delta|"
                           r"evolution|movement in)\b")
_ENTITY_STATE = re.compile(r"\b(stage|bucket|status|default\w*|facilities|accounts|borrowers|"
                           r"customers|exposures)\b")


def _is_transition(question: str) -> bool:
    q = question.lower()
    if _TRANSITION_STRONG.search(q):
        return True
    return bool(_TRANSITION_WEAK.search(q) and _ENTITY_STATE.search(q)
                and not _CHANGE_WORDS.search(q))


# ------------------------------------------------------------------ rule 1

def _plan_period_covers(p: dict[str, Any], expected: set[str], measures: list[str],
                        book: pc.Book) -> bool | None:
    """True/False, or None when the plan's periods cannot be judged."""
    period = p["period"]
    kind = period["kind"]
    if kind == "all":
        return True
    if kind == "explicit":
        values = set(period["values"])
        if expected <= values:
            return True
        # A comparison anchored on one explicit period also reads an earlier one.
        return bool(p["compare"] != "none" and expected & values)
    relation = book.metrics[measures[0]].relation
    held = book.populated.get(relation, ())
    if not held:
        return None
    year = pc._YEAR.get(book.domain_id, 4)
    reach: set[str] = set()
    if kind == "latest":
        reach.add(held[-1])
        if p["compare"] == "prior" and len(held) > 1:
            reach.add(held[-2])
        if p["compare"] == "year_ago" and len(held) > year:
            reach.add(held[-1 - year])
    elif kind == "prior" and len(held) > 1:
        reach.add(held[-2])
    elif kind == "year_ago" and len(held) > year:
        reach.add(held[-1 - year])
    elif kind == "last_n":
        reach.update(held[-period["n"]:])
    return expected <= reach


def _expected(named: tuple[int, int | None, int | None], measures: list[str],
              book: pc.Book) -> set[str] | None:
    try:
        values, _ = pc._explicit_periods([_label(named)], measures, book)
    except Rejection:
        return None
    return set(values)


def _rule_period(p: dict[str, Any], question: str, book: pc.Book) -> list[Finding]:
    named = named_periods(question)
    if not named:
        return []
    out: list[Finding] = []
    labels = [_label(n) for n in named]
    if len(named) >= 2 and _is_transition(question):
        out.append(Finding(
            "period_transition", REFUSE,
            "The question follows entities from one named period to another "
            f"({', '.join(labels)}), and a governed plan cannot express a period-to-period "
            "transition. Use execute_analysis `steps` with SQL that joins the two periods "
            "on the entity key (one CTE per period, then join), not `plan`.",
            {"periods": labels}))
        return out
    expected: set[str] = set()
    for n in named:
        got = _expected(n, p["measures"], book)
        if got is None:
            return []  # cannot map a period for these measures: say nothing
        expected |= got
    covered = _plan_period_covers(p, expected, p["measures"], book)
    if covered is False:
        want = sorted(labels)
        out.append(Finding(
            "period_named", REFUSE,
            f"The question names {', '.join(want)} but the plan's period is "
            f"{p['period']['kind']!r}, which answers a different period. Send "
            f"\"period\": {{\"kind\": \"explicit\", \"values\": {json.dumps(want)}}} (and remove "
            "period.n / compare unless the question asks for them).",
            {"named": want, "plan_period": p["period"]["kind"]}))
    return out


# ------------------------------------------------------------------ rule 2

def _comparison_words(fname: str) -> re.Pattern[str]:
    f = re.escape(fname.replace("_", " "))
    return re.compile(
        rf"\b(?:by|per|each|every|across|among)\s+(?:all\s+|the\s+)?{f}s?\b|"
        rf"\bother\s+{f}s?\b|\bcompare[sd]?\b|\bcompared\s+(?:with|to)\b|\bvs\.?\b|\bversus\b|"
        r"\brelative\s+to\b|\brest\s+of\b|\brank(?:ed|ing|s)?\b")


def _entity_field(entry: dict[str, Any]) -> str:
    return str(entry.get("field", "")).split(".")[-1].strip().lower()


def _same(a: Any, b: Any) -> bool:
    return values_mod.normalize(a) == values_mod.normalize(b)


def _rule_scope(p: dict[str, Any], question: str, envelope: Any, book: pc.Book) -> list[Finding]:
    entries = [dict(e) for e in (getattr(envelope, "resolved_entities", ()) or ())
               if isinstance(e, dict)]
    by_field: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_field.setdefault(_entity_field(e), []).append(e)
    asked = values_mod.normalize(question)
    out: list[Finding] = []
    for fname, group in by_field.items():
        if fname not in book.fields:  # (c) the plan cannot filter it
            continue
        distinct = {values_mod.normalize(e.get("value", "")) for e in group}
        if len(distinct) >= 2:  # (a) a comparison
            continue
        entry = group[0]
        value = str(entry.get("value", ""))
        if "exact" in entry:
            if not entry["exact"]:
                continue
        elif f" {values_mod.normalize(value)} " not in f" {asked} ":
            continue
        if _comparison_words(fname).search(question.lower()):  # (b)
            continue
        if any(_same(v, value) for v in p["filters"].get(fname, ())):
            continue
        shown = ", ".join(p["filters"].get(fname, ())) or "no filter"
        out.append(Finding(
            "entity_scope", REFUSE,
            f"The question is about {fname} = {value!r}, but the plan has {shown} on {fname} "
            "(grouping by a field returns every value, it does not select one). Add "
            f"\"filters\": [{{\"field\": \"{fname}\", \"values\": [\"{value}\"]}}]"
            + (f" and drop {fname} from dimensions unless the reader wants the others too."
               if fname in p["dimensions"] else "."),
            {"field": fname, "value": value, "plan_filter": list(p["filters"].get(fname, ()))}))
    return out


# ------------------------------------------------------------------ rule 3

_WANTS_AVG = re.compile(r"\b(average|mean|avg)\b")
_WANTS_SUM = re.compile(r"\b(total|sum)\b")


def _is_avg(metric: pc.Metric) -> bool:
    return metric.sql.strip().upper().startswith("AVG(") or "average" in metric.label.lower()


def _is_sum(metric: pc.Metric) -> bool:
    return metric.sql.strip().upper().startswith("SUM(") or metric.label.startswith("Total")


def _rule_aggregation(p: dict[str, Any], question: str, book: pc.Book) -> list[Finding]:
    q = question.lower()
    metrics = [book.metrics[m] for m in p["measures"]]
    avg, tot = bool(_WANTS_AVG.search(q)), bool(_WANTS_SUM.search(q))
    if avg and tot:
        return []
    if avg and all(_is_sum(m) and not _is_avg(m) for m in metrics):
        return [Finding("aggregation_word", WARN,
                        "The question says average but the measure(s) "
                        f"{', '.join(p['measures'])} are totals; the answer is a sum, not a mean.",
                        {"measures": list(p["measures"])})]
    if tot and any(_is_avg(m) for m in metrics):
        return [Finding("aggregation_word", WARN,
                        "The question says total but a chosen measure is a simple average.",
                        {"measures": list(p["measures"])})]
    return []


# ------------------------------------------- rule 4: operation_dropped (SHADOW)
#
# WHY. A plan can be valid, cover every named period and still drop what the
# question asked to compute. Run b8aa21b3 ("Which sub-sectors share top 10, that
# had the largest absolute and relative increase in Stage 3 EAD between 2025Q2
# and 2026Q2?") sent compare "none", no limit and the default sort: 136 rows of
# raw totals, with no change, no ranking and no overlap.
#
# One table instead of one rule per incident: `requested_operations` reads a
# small fixed vocabulary from the question, and each operation has one test of
# whether a plan (or SQL) produces it. A new corner case is usually a new row
# here plus a golden, not new code. Every finding is SHADOW until
# scripts/cockpit_v4/shadow_precision.py measures it at >= 95% precision.

SHADOW = "shadow"
OPERATIONS = ("change", "pct_change", "rank_change", "top_n", "intersect")
_RELATIVE = re.compile(r"\b(relative|percent(?:age)?|pct|growth rate|rate of (?:change|growth))\b|%")
_RANKING = re.compile(r"\b(top|largest|highest|biggest|greatest|most|lowest|smallest|least|fewest)\b")
_FALLING = re.compile(r"\b(decreas\w*|fell|fall\w*|drop\w*|declin\w*|reduc\w*|shr[aui]nk\w*)\b")
_NUMBER_WORDS = {"three": 3, "five": 5, "ten": 10, "twenty": 20}
_TOP_N = re.compile(
    r"\btop[\s-]+(\d{1,2}|three|five|ten|twenty)\b|"
    r"\b(?:largest|highest|biggest|greatest|lowest|smallest)\s+(\d{1,2}|three|five|ten|twenty)\b|"
    r"\b(\d{1,2}|three|five|ten|twenty)\s+(?:largest|highest|biggest|greatest|lowest|smallest)\b")
# "Which 10 sub-sectors had the largest ..." -- a count, read only beside a ranking word.
_COUNTED = re.compile(r"\b(?:which|what|the|list|show|name|give)\s+(?:me\s+)?(\d{1,2}|three|five|ten|twenty)\s+[a-z]")
# "share" only as "share the top N" / "share top 10", never "share of portfolio".
_OVERLAP = re.compile(r"\bshare[sd]?\s+(?:a\s+|the\s+)?(?:top|place|spot)|\bin\s+both\b|\bboth\s+(?:top|lists?|rankings?)\b|"
                      r"\bin\s+common\b|\boverlap\w*\b|\bintersect\w*\b|\bcommon\s+to\b")
_TWO_RANKINGS = re.compile(r"\b(absolute)\b.*\b(relative|percent\w*)\b|\b(relative|percent\w*)\b.*\b(absolute)\b")


def requested_operations(question: str) -> dict[str, Any]:
    """{operation: detail} for what the question asks to COMPUTE.

    Deliberately narrow: `change` needs a change word AND two named periods,
    so "how did ECL change this quarter" (one implicit period) says nothing.
    """
    q = str(question or "").lower()
    ops: dict[str, Any] = {}
    named = named_periods(q)
    change = bool(_CHANGE_WORDS.search(q)) and len(named) >= 2
    if change:
        ops["change"] = [_label(n) for n in named]
        if _RELATIVE.search(q):
            ops["pct_change"] = True
        if _RANKING.search(q):
            ops["rank_change"] = True
    m = _TOP_N.search(q) or (_RANKING.search(q) and _COUNTED.search(q))
    if m:
        raw = next(g for g in m.groups() if g)
        ops["top_n"] = int(_NUMBER_WORDS.get(raw, raw))
        if _OVERLAP.search(q) and (_TWO_RANKINGS.search(q) or " both " in f" {q} "):
            ops["intersect"] = True
    return ops


def _op_finding(op: str, message: str, **detail: Any) -> Finding:
    return Finding("operation_dropped", SHADOW, message, {"operation": op, **detail})


def _rule_operations(p: dict[str, Any], question: str) -> list[Finding]:
    ops = requested_operations(question)
    out: list[Finding] = []
    if "intersect" in ops:
        # No plan can intersect two rankings; say only this, the rest follows.
        return [_op_finding(
            "intersect",
            "The question asks which groups appear in two rankings at once (an overlap), "
            "which a governed plan cannot express. Use execute_analysis `steps`: one CTE "
            "with both measures, one CTE per ranking (ORDER BY ... LIMIT N), then JOIN them.",
            top_n=ops.get("top_n"))]
    if "change" in ops and p["compare"] == "none":
        periods = ops["change"]
        out.append(_op_finding(
            "change",
            f"The question asks for a change between {', '.join(periods)} but the plan has "
            "compare 'none', so it returns raw totals per period and no change. Send "
            f"\"period\": {{\"kind\": \"explicit\", \"values\": {json.dumps(sorted(periods)[:2])}}}, "
            "\"compare\": \"between\".", periods=periods))
    elif "rank_change" in ops and p["sort"] not in pc.CHANGE_SORTS:
        q = question.lower()
        base = "pct_change" if "pct_change" in ops and "absolute" not in q else "change"
        want = f"{base}_{'asc' if _FALLING.search(q) else 'desc'}"
        out.append(_op_finding(
            "rank_change",
            "The question ranks by the size of the change, but the plan sorts by "
            f"{p['sort']!r}, which ranks the current level. Send \"sort\": \"{want}\".",
            sort=p["sort"]))
    if "top_n" in ops and p["limit"] != ops["top_n"]:
        out.append(_op_finding(
            "top_n",
            f"The question asks for the top {ops['top_n']} but the plan's limit is "
            f"{p['limit'] or 'unset'}. Send \"limit\": {ops['top_n']}.",
            n=ops["top_n"], limit=p["limit"]))
    return out


# ------------------------------------------------------------------- public

def check_plan(plan: dict[str, Any], *, question: str, envelope: Any,
               book: pc.Book) -> list[Finding]:
    """Findings for a raw plan. An invalid plan raises the compiler's Rejection."""
    normalised = pc.normalize(plan, book)
    q = str(question or "")
    if not q.strip():
        return []
    return [*_rule_period(normalised, q, book),
            *_rule_scope(normalised, q, envelope, book),
            *_rule_aggregation(normalised, q, book),
            *_rule_operations(normalised, q)]


def refusal(findings: list[Finding]) -> Rejection | None:
    """The Rejection for the refuse findings, intent message first, or None."""
    refused = [f for f in findings if f.severity == REFUSE]
    if not refused:
        return None
    text = " ".join(f"Intent check: {f.message}" for f in refused)
    return Rejection(pc.PLAN_INVALID, text + " Nothing was executed.", field_path="plan",
                     detail={"intent_findings": [f.as_dict() for f in refused]})


def gate(plan: Any, *, question: str, envelope: Any, book: pc.Book,
         run_mode: str | None = None) -> tuple[list[Finding], Rejection | None]:
    """(findings to record without refusing, a Rejection to raise or None).

    enforce: refuse findings become the Rejection; warnings and SHADOW-severity
    findings are returned for the record. shadow: nothing is refused; every
    finding is returned for the record.
    """
    chosen = run_mode or mode()
    if chosen == "off" or not isinstance(plan, dict):
        return [], None
    findings = check_plan(plan, question=question, envelope=envelope, book=book)
    if chosen == "shadow":
        return findings, None
    return [f for f in findings if f.severity != REFUSE], refusal(findings)


# ------------------------------------------------------- raw SQL (SHADOW ONLY)
#
# WHY. About one run in eight skips the governed plan and writes SQL, so none
# of the plan rules above can see it. These rules read DuckDB's parse tree of
# the analyst's statement and record what looks like a wrong answer. They are
# SHADOW-ONLY: they never refuse, because a refusal of valid SQL is worse than
# the miss. scripts/cockpit_v4/shadow_precision.py measures precision over
# past runs; the bar for promotion to refusal is 95% (artifacts_eval/gates.json).

_PERIOD_COLUMN = re.compile(r"reporting_(?:quarter|month)|(?:^|_)period(?:$|_)|^quarter$|^month$")


def _walk(node: Any):
    """Every dict in a parse tree, parents before children."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _columns_in(node: Any) -> set[str]:
    out: set[str] = set()
    for d in _walk(node):
        names = d.get("column_names")
        if d.get("class") == "COLUMN_REF" and names:
            out.add(str(names[-1]).lower())
    return out


def _string_constants(doc: Any) -> list[str]:
    out: list[str] = []
    for d in _walk(doc):
        if d.get("class") == "CONSTANT":
            v = d.get("value") or {}
            if isinstance(v, dict) and not v.get("is_null") \
                    and str((v.get("type") or {}).get("id", "")).upper() == "VARCHAR":
                out.append(str(v.get("value", "")))
    return out


def _filter_columns(doc: Any) -> set[str]:
    """Columns that SELECT rows: WHERE, HAVING, QUALIFY and JOIN conditions.

    GROUP BY is deliberately not here: grouping by a field returns every
    value, it does not select one (the live "for Riyadh" failure).
    """
    out: set[str] = set()
    for d in _walk(doc):
        for key in ("where_clause", "having", "qualify", "condition"):
            if isinstance(d.get(key), dict):
                out |= _columns_in(d[key])
    return out


def _period_literals(named: list[tuple[int, int | None, int | None]]) -> set[str]:
    wanted: set[str] = set()
    for y, q, mo in named:
        wanted.add(_label((y, q, mo)).lower())
        if q:
            wanted.update(f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1))
    return wanted


def _constrains_period_indirectly(doc: Any) -> bool:
    """MAX/MIN subquery on a period column, or LIMIT/OFFSET used to pick one."""
    for d in _walk(doc):
        if d.get("class") == "COMPARISON":
            for a, b in ((d.get("left"), d.get("right")), (d.get("right"), d.get("left"))):
                if (isinstance(a, dict) and a.get("class") == "COLUMN_REF"
                        and _PERIOD_COLUMN.search(str((a.get("column_names") or [""])[-1]).lower())
                        and any(x.get("class") == "FUNCTION"
                                and str(x.get("function_name", "")).lower() in ("max", "min")
                                for x in _walk(b)) and any(
                                    x.get("class") == "SUBQUERY" for x in _walk(b))):
                    return True
        if d.get("class") == "SUBQUERY":
            for m in _walk(d.get("subquery")):
                if m.get("type") == "LIMIT_MODIFIER":
                    return True
        if d.get("type") == "LIMIT_MODIFIER" and d.get("offset"):
            return True
    return False


def _sql_period(doc: Any, question: str) -> list[Finding]:
    named = named_periods(question)
    if not named or any(w in c.lower() for c in _string_constants(doc)
                        for w in _period_literals(named)):
        return []
    if not _constrains_period_indirectly(doc):
        return []
    labels = [_label(n) for n in named]
    return [Finding(
        "sql_period", SHADOW,
        f"The question names {', '.join(labels)} but the SQL never states it and picks its "
        "period with MAX()/LIMIT/OFFSET, which answers whichever period is latest.",
        {"named": labels})]


def _sql_entity(doc: Any, question: str, envelope: Any) -> list[Finding]:
    entries = [dict(e) for e in (getattr(envelope, "resolved_entities", ()) or ())
               if isinstance(e, dict)]
    by_field: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_field.setdefault(_entity_field(e), []).append(e)
    constants = [values_mod.normalize(c) for c in _string_constants(doc)]
    filtered = _filter_columns(doc)
    out: list[Finding] = []
    for fname, group in by_field.items():
        if not fname or len({values_mod.normalize(e.get("value", "")) for e in group}) >= 2:
            continue
        entry = group[0]
        value = str(entry.get("value", ""))
        if entry.get("exact") is not True or not value.strip():
            continue
        if _comparison_words(fname).search(question.lower()):
            continue
        if fname in filtered or values_mod.normalize(value) in constants:
            continue
        out.append(Finding(
            "sql_entity", SHADOW,
            f"The question is about {fname} = {value!r}, but the SQL neither filters {fname} "
            "nor mentions that value.", {"field": fname, "value": value}))
    return out


def _sql_ratio_sum(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for d in _walk(doc):
        if d.get("class") in ("FUNCTION", "WINDOW") \
                and str(d.get("function_name", "")).lower() in ("sum", "fsum", "total"):
            for col in sorted(_columns_in(d.get("children"))):
                if col.endswith("_pct") or col.startswith(("pd_", "lgd")):
                    out.append(Finding(
                        "sql_ratio_sum", SHADOW,
                        f"SUM({col}) adds a ratio; ratios are not additive. Use a weighted "
                        "average (or the governed ratio) instead.", {"column": col}))
    return out


def _sql_operations(doc: Any, question: str) -> list[Finding]:
    """The SQL side of rule 4: what the statement computes, from its parse tree."""
    ops = requested_operations(question)
    if not ops:
        return []
    nodes = list(_walk(doc))
    subtracts = any(d.get("class") == "FUNCTION" and d.get("function_name") == "-"
                    for d in nodes)
    limits = sum(1 for d in nodes if d.get("type") == "LIMIT_MODIFIER")
    windows = sum(1 for d in nodes if d.get("class") == "WINDOW" and str(
        d.get("function_name", "")).lower() in ("rank", "row_number", "dense_rank", "ntile"))
    intersects = any(d.get("setop_type") == "INTERSECT" for d in nodes)
    out: list[Finding] = []

    def dropped(op: str, message: str) -> None:
        out.append(Finding("sql_operation_dropped", SHADOW, message, {"operation": op}))

    if "change" in ops and not subtracts:
        dropped("change", "The question asks for a change between "
                f"{', '.join(ops['change'])} but the SQL subtracts nothing.")
    if "top_n" in ops and not (limits or windows):
        dropped("top_n", f"The question asks for the top {ops['top_n']} but the SQL has no "
                "LIMIT and no rank/row_number.")
    if "intersect" in ops and not intersects and limits + windows < 2:
        dropped("intersect", "The question asks for the overlap of two rankings but the SQL "
                "ranks at most once.")
    return out


def check_sql(sql: str, *, question: str, envelope: Any = None,
              session: Any = None) -> list[Finding]:
    """SHADOW findings for analyst-written SQL; [] when anything cannot be judged.

    Never raises: an unparseable statement is the SQL validator's business, and
    a bug here must not cost anybody an answer. `session` is accepted for
    symmetry with the validators and is not read.
    """
    try:
        from backend.cockpit_v4 import sql as v4_sql

        q = str(question or "")
        if not str(sql or "").strip():
            return []
        doc = v4_sql._parse(str(sql))
        if doc.get("error"):
            return []
        findings: list[Finding] = []
        if q.strip():
            findings += _sql_period(doc, q)
            findings += _sql_entity(doc, q, envelope)
            findings += _sql_operations(doc, q)
        findings += _sql_ratio_sum(doc)
        return findings
    except Exception:  # noqa: BLE001 - shadow checks must never break a run
        return []


__all__ = ["Finding", "check_plan", "check_sql", "gate", "mode", "named_periods", "refusal",
           "requested_operations"]
