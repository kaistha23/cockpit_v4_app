"""An analysis PLAN, compiled to SQL steps by code. For models that write SQL poorly.

Why this exists
---------------
Local models on the Cockpit failed far more on writing SQL than on deciding
what to compute: GROUP BY mistakes, columns read from the wrong relation, an
earlier step's output used as a table. The sibling slm_v1 project measured the
same thing and settled it the same way: the model fills a small plan whose
every name comes from an enumerated list, and code writes the SQL.

What the plan can say
---------------------
    {"measures":   ["ecl", "ead_sar_mn"],          # governed metric ids (enum)
     "dimensions": ["sector"],                      # governed fields (enum)
     "filters":    [{"field": "stage", "values": ["2"]}],
     "period":     {"kind": "latest" | "prior" | "year_ago" | "last_n" | "all"
                            | "explicit",
                    "n": 4,
                    "values": ["2026Q1"]},          # explicit: 1..12 named periods
     "compare":    "none" | "prior" | "year_ago" | "between",
     "sort":       "auto" | "measure_desc" | "measure_asc" | "dimension"
                   | "change_desc" | "change_asc" | "pct_change_desc" | "pct_change_asc",
     "limit":      0}

What it guarantees
------------------
- Only governed names: a measure, dimension or filter value the book does not
  hold is refused with the names it does hold. Nothing is substituted.
- No joins. Measures are grouped by the relation that holds them and each
  group is ONE self-contained step over ONE relation, so nothing can fan out.
- The output is an ordinary `execute_analysis` step list. Validation,
  execution, artifacts, budgets and evidence rules are exactly the ones a
  hand-written submission meets; this module only writes the SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4.contracts import Rejection

PLAN_INVALID = "PLAN_INVALID"
PERIOD_KINDS = ("latest", "prior", "year_ago", "last_n", "all", "explicit")
MAX_PERIOD_VALUES = 12

#: Columns that are FLOWS: an amount that happened DURING the month, so three
#: months add up to a quarter. Every other measure is a STOCK (a balance, an
#: allowance, a count, an average): a quarter is its quarter-END month, and
#: adding months together would be a number with no meaning.
FLOW_COLUMNS = frozenset({"write_off_sar_mn", "recovery_sar_mn"})
_FLOW = re.compile(r"\b(" + "|".join(sorted(FLOW_COLUMNS)) + r")\b")
#: "between" compares the two periods named in period.values (later vs earlier),
#: for questions such as "between 2025Q2 and 2026Q2" whose gap is not one step
#: or one year in the data.
COMPARES = ("none", "prior", "year_ago", "between")
#: The change sorts rank by `{m}_change` / `{m}_pct_change` and need a compare:
#: "largest increase" is a ranking of the movement, not of the current level.
CHANGE_SORTS = ("change_desc", "change_asc", "pct_change_desc", "pct_change_asc")
SORTS = ("auto", "measure_desc", "measure_asc", "dimension") + CHANGE_SORTS
MAX_MEASURES, MAX_DIMENSIONS, MAX_FILTERS, MAX_LIMIT = 4, 2, 4, 50

#: Periods per year, by the book's frequency, for a year-ago comparison.
_YEAR = {"corporate": 4, "retail": 12}
_KEYS = ("facility_id", "borrower_id", "account_id", "customer_id",
         "covenant_id", "collateral_id")
_AVERAGED = re.compile(r"(_pct|_x|_days|_score|^pd_|_months|_quarters)$|^pd_")


@dataclass(frozen=True)
class Metric:
    metric_id: str
    label: str
    relation: str
    sql: str          # an aggregate expression over the relation
    unit: str


@dataclass(frozen=True)
class Field:
    name: str
    relations: tuple[str, ...]
    values: tuple[str, ...]   # canonical values; empty = any value of the column
    numeric: bool


@dataclass(frozen=True)
class Book:
    domain_id: str
    metrics: dict[str, Metric]
    fields: dict[str, Field]
    period_column: dict[str, str]
    #: relation -> the periods it holds, ascending. Empty when they could not
    #: be read, in which case an explicit period is checked for format only.
    populated: dict[str, tuple[str, ...]] = field(default_factory=dict)


_CURRENCY_UNITS = {"rcy", "sar", "sar million", "sar_mn", "sar mn"}
_NEVER = re.compile(r"(_id|_version|_flag|_code|_count|_status)$|^stage$")


def _measure_kind(catalog: Any, relation: str, col: str) -> str:
    """'sum' for a currency amount, 'avg' for a numeric level, '' otherwise.

    Read from the catalogue's own field facts (dtype, unit, additivity), not
    guessed from the name: summing days past due or a probability is a number
    with no meaning, and an identifier is not a measure at all.
    """
    if _NEVER.search(col):
        return ""
    try:
        spec = catalog.resolve(relation, col)
    except Exception:  # noqa: BLE001
        return "sum" if col.endswith("_sar_mn") else ""
    dtype = str(getattr(spec, "dtype", "") or "").lower()
    if dtype not in ("float", "double", "integer", "int", "decimal", "number"):
        return ""
    unit = str(getattr(spec, "unit", "") or "").lower()
    additive = str(getattr(spec, "additive", "") or getattr(spec, "aggregation", "") or "").lower()
    if (unit in _CURRENCY_UNITS or col.endswith("_sar_mn")) and additive in ("additive", ""):
        return "sum"
    if unit in ("count",):
        return ""
    if additive in ("not_additive", "non_additive") or _AVERAGED.search(col) or unit in (
            "days", "months", "percent", "pct", "ratio", "x") or unit.startswith("probability"):
        return "avg"
    return ""


def _average_unit(catalog: Any, relation: str, col: str) -> str:
    """An average keeps its column's unit: the mean of a PD is a PD."""
    from backend.cockpit_v4 import display

    unit = display.unit_for_field(catalog, relation, col)
    return unit if display.classify(unit) != display.UNKNOWN else "average"


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _column_names(catalog: Any, relation: str) -> list[str]:
    return [c if isinstance(c, str) else c.name for c in catalog.columns(relation)]


def build_book(*, domain_id: str, catalog: Any, session: Any) -> Book:
    """The governed vocabulary a plan may use for this book."""
    from backend.cockpit_v4 import attention_v2 as att
    from backend.cockpit_v4 import values as values_mod

    relations = list(catalog.relations())
    columns = {r: _column_names(catalog, r) for r in relations}
    period_column = {r: next((c for c in columns[r] if c in ("reporting_quarter", "reporting_month")), "")
                     for r in relations}

    metrics: dict[str, Metric] = {}

    def add(metric: Metric) -> None:
        key = metric.metric_id
        if key in metrics and metrics[key].relation != metric.relation:
            key = f"{metric.metric_id}@{metric.relation}"
        metrics.setdefault(key, Metric(key, metric.label, metric.relation, metric.sql, metric.unit))

    families = att._corporate_families() if domain_id == "corporate" else att._retail_families()
    for f in families:
        if f.relation not in columns or getattr(f, "filter_expression", ""):
            continue
        percent = f.kind == "share" or f.unit == "percent"
        add(Metric(f.measure, f.measure_label, f.relation,
                   f"({f.expression}) * 100" if percent else f.expression,
                   "percent" if percent else f.unit))
    for relation in relations:
        for col in columns[relation]:
            kind = _measure_kind(catalog, relation, col)
            if kind == "sum":
                add(Metric(col, f"Total {col.removesuffix('_sar_mn').replace('_', ' ')}",
                           relation, f"SUM({col})", "SAR million"))
        for col in columns[relation]:
            if _measure_kind(catalog, relation, col) == "avg":
                add(Metric(f"avg_{col}", f"Simple average of {col.replace('_', ' ')} (unweighted)", relation, f"AVG({col})",
                           _average_unit(catalog, relation, col)))
        for col in columns[relation]:
            if col in _KEYS and relation.split("_")[1] in col:
                entity = col.removesuffix("_id")
                add(Metric(f"{entity}_count", f"Number of {entity}s", relation,
                           f"COUNT(DISTINCT {col})", "count"))

    fields: dict[str, Field] = {}
    try:
        governed = {dim.field_name: tuple(dim.values) for dim in
                    values_mod.dimensions(session=session, catalog=catalog).values()}
    except Exception:  # noqa: BLE001 - an older catalogue type: read the session
        governed = _categorical_from_session(session, relations, columns)
    for name, held in governed.items():
        holders = tuple(r for r in relations if name in columns[r])
        if holders:
            fields[name] = Field(name, holders, held, False)
    for relation in relations:
        for col in columns[relation]:
            if col == "stage" or col.endswith("_flag") or col in ("sector",) and col not in fields:
                holders = tuple(r for r in relations if col in columns[r])
                if col not in fields:
                    vals = ("1", "2", "3") if col == "stage" else (("0", "1") if col.endswith("_flag") else ())
                    fields[col] = Field(col, holders, vals, col == "stage" or col.endswith("_flag"))
    return Book(domain_id, metrics, fields, period_column,
                _populated_periods(session, period_column))


def _populated_periods(session: Any, period_column: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """The periods each relation holds, so a named period can be checked.

    Best effort: with no session, or a failing query, the relation maps to
    nothing and an explicit period is validated by its format alone.
    """
    connection = getattr(session, "connection", None)
    found: dict[str, tuple[str, ...]] = {}
    if connection is None:
        return found
    for relation, column in period_column.items():
        if not column:
            continue
        try:
            rows = connection.execute(
                f"SELECT DISTINCT {column} FROM {relation} WHERE {column} IS NOT NULL").fetchall()
            found[relation] = tuple(sorted(str(r[0]) for r in rows))
        except Exception:  # noqa: BLE001 - degrade to format-only validation
            continue
    return found


def _categorical_from_session(session: Any, relations: list[str],
                              columns: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    """Text columns with at most 80 distinct values, as `values` would govern them."""
    connection = getattr(session, "connection", None)
    found: dict[str, tuple[str, ...]] = {}
    if connection is None:
        return found
    for relation in relations:
        types = {row[0]: str(row[1]).upper() for row in
                 connection.execute(f"DESCRIBE {relation}").fetchall()}
        for col in columns[relation]:
            if col in found or not types.get(col, "").startswith("VARCHAR") or col.endswith("_id") \
                    or col in ("tenant_id", "dataset_release_id", "domain_id", "reporting_currency"):
                continue
            held = [r[0] for r in connection.execute(
                f"SELECT DISTINCT {col} FROM {relation} WHERE {col} IS NOT NULL LIMIT 81").fetchall()]
            if 1 < len(held) <= 80:
                found[col] = tuple(sorted(str(v) for v in held))
    return found


def plan_schema(book: Book) -> dict[str, Any]:
    """The `plan` property of execute_analysis: every name is an enum."""
    return {
        "type": "object",
        "description": (
            "Preferred over writing SQL. Name what to compute; CreditProbe writes "
            "and runs the SQL. Measures are governed metric ids; dimensions and "
            "filter fields are governed fields; filter values must be values "
            "those fields hold. A stage filter requires measures and dimensions "
            "available on the relation holding stage (for Corporate, facility-grain "
            "metrics such as EAD, ECL or facility_count; not borrower_count). To "
            "name a specific period use period.kind 'explicit' with period.values, "
            "e.g. ['2026Q1'] or ['2026-03']; accepted forms are 2026Q1, 'Q1 2026', "
            "2026-03 and 'Mar 2026'. 'latest' is only for the most recent period."),
        "properties": {
            "measures": {"type": "array", "minItems": 1, "maxItems": MAX_MEASURES,
                         "items": {"type": "string", "enum": sorted(book.metrics)}},
            "dimensions": {"type": "array", "maxItems": MAX_DIMENSIONS,
                           "items": {"type": "string", "enum": sorted(book.fields)}},
            "filters": {"type": "array", "maxItems": MAX_FILTERS, "items": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": sorted(book.fields)},
                               "values": {"type": "array", "minItems": 1,
                                          "items": {"type": "string"}}},
                "required": ["field", "values"], "additionalProperties": False}},
            "period": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": list(PERIOD_KINDS)},
                "n": {"type": "integer", "minimum": 2, "maximum": 24},
                "values": {"type": "array", "minItems": 1, "maxItems": MAX_PERIOD_VALUES,
                           "items": {"type": "string"},
                           "description": "With kind 'explicit': the periods to report, "
                                          "such as 2026Q1 or 2026-03."}},
                "required": ["kind"], "additionalProperties": False},
            "compare": {"type": "string", "enum": list(COMPARES),
                        "description": "prior / year_ago: the period against an earlier one "
                                       "(period 'latest' or one explicit value). between: two "
                                       "explicit period.values, the later against the earlier. "
                                       "Adds _prev, _change and _pct_change columns."},
            "sort": {"type": "string", "enum": list(SORTS),
                     "description": "change_* / pct_change_* rank by the movement (needs compare); "
                                    "with limit this is 'largest increase / decrease'."},
            "limit": {"type": "integer", "minimum": 0, "maximum": MAX_LIMIT},
        },
        "required": ["measures"],
        "additionalProperties": False,
    }


def metric_glossary(book: Book) -> str:
    """One line per metric, for the tool description: id, unit, relation."""
    return "; ".join(f"{m.metric_id} ({m.unit}, {m.label})" for m in
                     sorted(book.metrics.values(), key=lambda m: m.metric_id))


def sensitivity_schema() -> dict[str, Any]:
    """The narrow, auditable contract for an IFRS 9 PD sensitivity.

    This is intentionally not a general what-if language.  Its one supported
    transformation is the facility-level impact of an absolute Lifetime-PD
    shock, with EAD, LGD and overlay held fixed.
    """
    return {"type": "object", "additionalProperties": False,
            "required": ["driver", "shock", "population", "held_constant", "denominator"],
            "properties": {
                "driver": {"type": "string", "enum": ["pd_lifetime"]},
                "shock": {"type": "object", "additionalProperties": False,
                          "required": ["kind", "value"], "properties": {
                              "kind": {"type": "string", "enum": ["absolute_percentage_points"]},
                              "value": {"type": "number", "exclusiveMinimum": 0, "maximum": 100}}},
                "population": {"type": "object", "additionalProperties": False,
                               "required": ["sector", "stage", "reporting_quarter"], "properties": {
                                   "sector": {"type": "string"},
                                   "stage": {"type": "integer", "enum": [2]},
                                   "reporting_quarter": {"type": "string", "pattern": "^[0-9]{4}Q[1-4]$"}}},
                "held_constant": {"type": "array", "minItems": 3, "items": {"type": "string",
                                  "enum": ["lgd_pct", "ead_sar_mn", "macro_overlay"]}},
                "denominator": {"type": "string", "enum": ["sector_total_recognised_ecl"]}}}


# ------------------------------------------------------------------ compile

def _refuse(message: str, path: str = "plan") -> Rejection:
    return Rejection(PLAN_INVALID, message + " Nothing was executed.", field_path=path)


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def normalize(plan: Any, book: Book) -> dict[str, Any]:
    """slm_v1 `normalize_plan`: fix shapes, never meanings."""
    if not isinstance(plan, dict):
        raise _refuse("plan must be an object.")
    lowered = {k.lower(): k for k in book.metrics}
    measures = []
    for m in _as_list(plan.get("measures")):
        key = lowered.get(str(m).strip().lower())
        if key is None:
            raise _refuse(f"{m!r} is not a governed measure. Measures: {', '.join(sorted(book.metrics))}.",
                          "plan.measures")
        if key not in measures:
            measures.append(key)
    if not 1 <= len(measures) <= MAX_MEASURES:
        raise _refuse(f"plan.measures must name 1 to {MAX_MEASURES} measures.", "plan.measures")
    dims = []
    for d in _as_list(plan.get("dimensions")):
        name = str(d).strip().lower()
        if name not in book.fields:
            raise _refuse(f"{d!r} is not a governed field. Fields: {', '.join(sorted(book.fields))}.",
                          "plan.dimensions")
        if name not in dims:
            dims.append(name)
    if len(dims) > MAX_DIMENSIONS:
        raise _refuse(f"plan.dimensions may name at most {MAX_DIMENSIONS} fields.", "plan.dimensions")
    merged: dict[str, list[str]] = {}
    for f in _as_list(plan.get("filters")):
        if not isinstance(f, dict):
            raise _refuse("each filter must be an object with 'field' and 'values'.", "plan.filters")
        name = str(f.get("field", "")).strip().lower()
        if name not in book.fields:
            raise _refuse(f"{f.get('field')!r} is not a governed field. Fields: "
                          f"{', '.join(sorted(book.fields))}.", "plan.filters")
        merged.setdefault(name, [])
        for v in _as_list(f.get("values", f.get("value"))):
            merged[name].append(_canonical_value(book.fields[name], v))
    if len(merged) > MAX_FILTERS:
        raise _refuse(f"plan.filters may name at most {MAX_FILTERS} fields.", "plan.filters")
    period = plan.get("period") or {}
    if isinstance(period, str):
        period = {"kind": period}
    raw_values = _as_list(period.get("values", period.get("value")))
    kind = str(period.get("kind") or ("explicit" if raw_values else "latest")).lower()
    if kind not in PERIOD_KINDS:
        raise _refuse(f"plan.period.kind must be one of {', '.join(PERIOD_KINDS)}.", "plan.period")
    if raw_values and kind != "explicit":
        raise _refuse(f"plan.period.values names specific periods, so period.kind must be "
                      f"'explicit', not {kind!r}.", "plan.period")
    n = int(period.get("n") or (4 if kind == "last_n" else 0))
    compare = str(plan.get("compare") or "none").lower()
    compare = compare if compare in COMPARES else "none"
    values: list[str] = []
    aggregate = ""
    if kind == "explicit":
        values, aggregate = _explicit_periods(raw_values, measures, book)
    if compare == "between":
        if kind != "explicit" or len(values) != 2 or aggregate:
            raise _refuse("plan.compare 'between' compares two named periods: period.kind must be "
                          "'explicit' with exactly two values (not a quarter of summed flow "
                          "months).", "plan.compare")
    elif compare != "none" and kind not in ("latest", "explicit"):
        raise _refuse("plan.compare compares one period with an earlier one, so "
                      "period.kind must be 'latest' (or 'explicit' with one value) when compare "
                      "is used.", "plan.compare")
    if compare in ("prior", "year_ago") and kind == "explicit" and (len(values) != 1 or aggregate):
        raise _refuse("plan.compare needs exactly one explicit period (and not a quarter of "
                      "summed flow months); name one period or drop compare.", "plan.compare")
    sort = str(plan.get("sort") or "auto").lower()
    sort = sort if sort in SORTS else "auto"
    if sort in CHANGE_SORTS and compare == "none":
        raise _refuse(f"plan.sort {sort!r} ranks by a change, which needs plan.compare "
                      "(prior, year_ago or between).", "plan.sort")
    limit = max(0, min(int(plan.get("limit") or 0), MAX_LIMIT))
    return {"measures": measures, "dimensions": dims, "filters": merged,
            "period": {"kind": kind, "n": max(2, min(n, 24)) if kind == "last_n" else 0,
                       "values": values, "aggregate": aggregate},
            "compare": compare, "sort": sort, "limit": limit}


_MONTHS = {m: i for i, names in enumerate(
    ("jan january", "feb february", "mar march", "apr april", "may", "jun june", "jul july",
     "aug august", "sep sept september", "oct october", "nov november", "dec december"), start=1)
    for m in names.split()}


def _parse_period(text: Any) -> tuple[int, int | None, int | None]:
    """(year, quarter, month) from a quarter or a month in any common spelling."""
    t = str(text).strip().lower()
    if m := re.fullmatch(r"(\d{4})\s*[- ]?\s*q([1-4])", t):
        return int(m[1]), int(m[2]), None
    if m := re.fullmatch(r"q([1-4])\s*[- ]?\s*(\d{4})", t):
        return int(m[2]), int(m[1]), None
    if m := re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})", t):
        if 1 <= int(m[2]) <= 12:
            return int(m[1]), None, int(m[2])
    if (m := re.fullmatch(r"([a-z]{3,9})\.?[\s-]*(\d{4})", t)) and m[1] in _MONTHS:
        return int(m[2]), None, _MONTHS[m[1]]
    raise _refuse(f"{text!r} is not a period. Use 2026Q1 for a quarter or 2026-03 for a month.",
                  "plan.period.values")


def _available(periods: tuple[str, ...]) -> str:
    shown = periods if len(periods) <= 6 else periods[:3] + ("...",) + periods[-3:]
    return ", ".join(shown)


def _explicit_periods(raw: list[Any], measures: list[str], book: Book) -> tuple[list[str], str]:
    """(canonical sorted values, a 'sum of ...' label or ''), or a refusal.

    Why: the model may NAME a period but never has it replaced. Quarter books
    take 'YYYYQn', month books 'YYYY-MM'. A quarter asked of a month book is a
    stock at its quarter-END month, or, for flows only, the sum of its three
    months (interim convention until docs/cockpit_v4/TODO_FORMULAE_IFRS9.md
    decides); a book that cannot answer one way is refused, not approximated.
    """
    if not 1 <= len(raw) <= MAX_PERIOD_VALUES:
        raise _refuse(f"plan.period.values must name 1 to {MAX_PERIOD_VALUES} periods.", "plan.period.values")
    relations = list(dict.fromkeys(book.metrics[m].relation for m in measures))
    columns = {book.period_column.get(r, "") for r in relations}
    if "" in columns or len(columns) != 1:
        raise _refuse("The chosen measures do not share one reporting period, so a named period "
                      "cannot be applied; ask for them in separate plans.", "plan.period")
    monthly = columns == {"reporting_month"}
    flows = [bool(_FLOW.search(book.metrics[m].sql)) for m in measures]
    if any(flows) and not all(flows):
        raise _refuse("These measures mix flows (" + ", ".join(sorted(FLOW_COLUMNS)) + ") with "
                      "balances. A quarter is a quarter-end balance but a sum of months for flows; "
                      "split this into two plans.", "plan.measures")
    parsed = [_parse_period(v) for v in raw]
    aggregate = ""
    out: list[str] = []
    if not monthly:
        out = [f"{y}Q{q or (mo - 1) // 3 + 1}" for y, q, mo in parsed]
    else:
        quarters = [p for p in parsed if p[1]]
        if all(flows) and quarters:
            if len(parsed) > 1:
                raise _refuse("A quarter of flow measures is the sum of its three months, so it "
                              "cannot be combined with other periods; name one quarter, or name "
                              "months.", "plan.period.values")
            y, q, _ = quarters[0]
            out = [f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)]
            aggregate = f"sum of {out[0]}..{out[-1]}"
        else:
            out = [f"{y}-{(3 * q if q else mo):02d}" for y, q, mo in parsed]
    out = sorted(set(out))
    for relation in relations:
        held = book.populated.get(relation, ())
        missing = [v for v in out if held and v not in held]
        if missing:
            raise _refuse(f"{', '.join(missing)} is not a period {relation} holds. Periods held: "
                          f"{_available(held)}. Nothing was substituted.", "plan.period.values")
    return out, aggregate


def _canonical_value(field: Field, value: Any) -> str:
    from backend.cockpit_v4 import values as values_mod

    text = str(value).strip()
    if field.numeric:
        lowered = text.lower().removeprefix("stage").strip()
        lowered = {"true": "1", "yes": "1", "false": "0", "no": "0"}.get(lowered, lowered)
        if not field.values or lowered in field.values:
            return lowered
    else:
        wanted = values_mod.normalize(text)
        for canonical in field.values:
            if values_mod.normalize(canonical) == wanted:
                return canonical
    shown = ", ".join(field.values[:25]) + (" ..." if len(field.values) > 25 else "")
    raise _refuse(f"{text!r} is not a value of {field.name}. Its values are: {shown}.", "plan.filters")


def _by_relation(p: dict[str, Any], book: Book) -> dict[str, list[Metric]]:
    grouped: dict[str, list[Metric]] = {}
    for mid in p["measures"]:
        metric = book.metrics[mid]
        grouped.setdefault(metric.relation, []).append(metric)
    return grouped


@dataclass(frozen=True)
class OutputColumn:
    """One column a compiled step returns, and what it means."""

    name: str
    #: "dimension", "period", "value", "prior", "change" or "pct_change".
    role: str
    unit: str = ""
    label: str = ""


@dataclass(frozen=True)
class StepShape:
    """What a compiled step returns. Read by `answer_draft`, never by SQL."""

    columns: tuple[OutputColumn, ...]
    #: The plan set a LIMIT: the rows are the top N, so their sum is not a
    #: total of anything a reader would call a total.
    limited: bool


def _change_unit(unit: str) -> str:
    from backend.cockpit_v4 import display

    kind = display.classify(unit)
    if kind == display.PERCENTAGE:
        return "percentage points"
    # A movement of a fraction would print as a percentage of itself.
    return "" if kind == display.PROBABILITY else unit


def _period_in_rows(p: dict[str, Any]) -> bool:
    """One row per period: a trend, every period, or several named periods.

    A quarter of summed flow months is ONE figure, so it has no period column.
    """
    period = p["period"]
    return period["kind"] in ("last_n", "all") or (
        period["kind"] == "explicit" and len(period["values"]) > 1 and not period["aggregate"])


def output_columns(plan: Any, book: Book) -> dict[str, StepShape]:
    """step_id -> the columns `compile_plan` writes for that step, in order."""
    p = normalize(plan, book)
    shapes: dict[str, StepShape] = {}
    then = _then(p)
    for index, (relation, metrics) in enumerate(_by_relation(p, book).items(), start=1):
        cols: list[OutputColumn] = []
        if p["compare"] == "none" and _period_in_rows(p):
            cols.append(OutputColumn(book.period_column.get(relation, ""), "period"))
        cols += [OutputColumn(d, "dimension") for d in p["dimensions"]]
        for m in metrics:
            a = _alias(m)
            cols.append(OutputColumn(a, "value", m.unit, m.label))
            if p["compare"] != "none":
                cols += [OutputColumn(f"{a}_prev", "prior", m.unit, f"{m.label}, {then}"),
                         OutputColumn(f"{a}_change", "change", _change_unit(m.unit),
                                      f"Change in {m.label.lower()} since the {then}"),
                         OutputColumn(f"{a}_pct_change", "pct_change", "percent",
                                      f"Relative change in {m.label.lower()} since the {then}")]
        shapes[f"plan_{index}"] = StepShape(tuple(cols), bool(p["limit"]))
    return shapes


def compile_plan(plan: Any, book: Book) -> tuple[list[dict[str, Any]], str]:
    """(execute_analysis steps, a one-line objective). Raises Rejection."""
    p = normalize(plan, book)
    by_relation = _by_relation(p, book)
    steps = []
    for index, (relation, metrics) in enumerate(by_relation.items(), start=1):
        for name in p["dimensions"] + list(p["filters"]):
            if relation not in book.fields[name].relations:
                raise _refuse(
                    f"{name} is not recorded on {relation}, which holds "
                    f"{', '.join(m.metric_id for m in metrics)}. Relations holding {name}: "
                    f"{', '.join(book.fields[name].relations)}.", "plan")
        sql = _sql(relation, metrics, p, book)
        steps.append({"step_id": f"plan_{index}", "language": "sql", "code": sql,
                      "purpose": _purpose(metrics, p)})
    return steps, _purpose([m for ms in by_relation.values() for m in ms], p)


def compile_sensitivity(sensitivity: Any, book: Book) -> tuple[list[dict[str, Any]], str]:
    """Compile the approved Lifetime-PD +pp ECL sensitivity, never SQL from a model."""
    if book.domain_id != "corporate":
        raise _refuse("Lifetime-PD ECL sensitivity is currently available for the Corporate book only.",
                      "sensitivity")
    if not isinstance(sensitivity, dict):
        raise _refuse("sensitivity must be an object.", "sensitivity")
    if sensitivity.get("driver") != "pd_lifetime":
        raise _refuse("sensitivity.driver must be 'pd_lifetime', not 12-month PD.", "sensitivity.driver")
    shock = sensitivity.get("shock") or {}
    if shock.get("kind") != "absolute_percentage_points":
        raise _refuse("sensitivity.shock.kind must be 'absolute_percentage_points', not a relative multiplier.",
                      "sensitivity.shock.kind")
    try:
        pp = float(shock.get("value"))
    except (TypeError, ValueError):
        raise _refuse("sensitivity.shock.value must be a positive number of percentage points.",
                      "sensitivity.shock.value") from None
    if not 0 < pp <= 100:
        raise _refuse("sensitivity.shock.value must be greater than 0 and no more than 100 percentage points.",
                      "sensitivity.shock.value")
    population = sensitivity.get("population") or {}
    sector = str(population.get("sector") or "").strip()
    quarter = str(population.get("reporting_quarter") or "").strip()
    if not sector or not re.fullmatch(r"\d{4}Q[1-4]", quarter) or population.get("stage") != 2:
        raise _refuse("sensitivity.population requires a sector, Stage 2, and an explicit YYYYQn reporting quarter.",
                      "sensitivity.population")
    held = {str(x) for x in _as_list(sensitivity.get("held_constant"))}
    required = {"lgd_pct", "ead_sar_mn", "macro_overlay"}
    if held != required:
        raise _refuse("sensitivity.held_constant must name exactly lgd_pct, ead_sar_mn and macro_overlay.",
                      "sensitivity.held_constant")
    if sensitivity.get("denominator") != "sector_total_recognised_ecl":
        raise _refuse("sensitivity.denominator must be 'sector_total_recognised_ecl'.", "sensitivity.denominator")
    relation = next((r for r in book.fields.get("stage", Field("", (), (), False)).relations
                     if book.period_column.get(r)), "")
    if not relation and book.metrics.get("ead_reported"):
        relation = book.metrics["ead_reported"].relation
    if not relation or relation not in book.period_column:
        raise _refuse("No facility relation with Lifetime PD is available in this release.", "sensitivity")
    # +1 percentage point is +0.01 probability; lgd_pct is recorded as 0..100.
    delta = pp / 100.0
    where = f"reporting_quarter = {_q(quarter)} AND sector = {_q(sector)}"
    increase = f"SUM(CASE WHEN stage = 2 THEN ead_sar_mn * (lgd_pct / 100.0) * {delta:.12g} ELSE 0 END)"
    total = "SUM(ecl_sar_mn)"
    sql = (f"SELECT {increase} AS ecl_increase_sar_mn, {total} AS sector_total_ecl_sar_mn, "
           f"{increase} / NULLIF({total}, 0) * 100 AS ecl_increase_pct "
           f"FROM {relation} WHERE {where}")
    purpose = (f"Lifetime PD sensitivity: +{pp:g} percentage point(s) for Stage 2 {sector} "
               f"facilities in {quarter}; EAD, LGD and macro overlay held constant")
    return ([{"step_id": "sensitivity_1", "language": "sql", "code": sql,
              "purpose": purpose}], purpose)


def _purpose(metrics: list[Metric], p: dict[str, Any]) -> str:
    parts = [", ".join(m.label for m in metrics)]
    if p["dimensions"]:
        parts.append("by " + " and ".join(p["dimensions"]))
    if p["filters"]:
        parts.append("where " + "; ".join(f"{k} in {v}" for k, v in p["filters"].items()))
    if p["period"]["kind"] == "explicit":
        named = p["period"]["aggregate"] or ", ".join(p["period"]["values"])
        parts.append(f"for {named}")
    else:
        period = {"latest": "latest period", "prior": "prior period", "year_ago": "same period a year earlier",
                  "last_n": f"last {p['period']['n']} periods", "all": "all periods"}[p["period"]["kind"]]
        parts.append(f"for the {period}")
    if p["compare"] != "none":
        parts.append(f"compared with the {_then(p)}")
    return " ".join(parts)[:300]


def _then(p: dict[str, Any]) -> str:
    if p["compare"] == "between":
        return f"{min(p['period']['values'])} period"
    return "prior period" if p["compare"] == "prior" else "year-ago period"


def _period_value(relation: str, column: str, offset: int) -> str:
    if offset == 0:
        return f"(SELECT MAX({column}) FROM {relation})"
    return (f"(SELECT {column} FROM (SELECT DISTINCT {column} FROM {relation}) "
            f"ORDER BY {column} DESC LIMIT 1 OFFSET {offset})")


def _explicit_in(period_col: str, values: list[str]) -> str:
    return f"{period_col} IN ({', '.join(_q(v) for v in values)})"


def _compare_periods(relation: str, p: dict[str, Any], book: Book, offset: int) -> tuple[str, str]:
    """(cur, prev) literals for a comparison anchored on a named period.

    prev is the period `offset` positions earlier in what the relation holds,
    never a calendar guess: a gap in the data is refused, not papered over.
    """
    held = book.populated.get(relation, ())
    current = p["period"]["values"][0]
    if not held:
        raise _refuse(f"The periods {relation} holds could not be read, so a comparison with an "
                      f"earlier period than {current} cannot be made.", "plan.compare")
    index = held.index(current) if current in held else -1
    if index < offset:
        raise _refuse(f"No period {offset} earlier than {current} is held by {relation}. Periods "
                      f"held: {_available(held)}.", "plan.compare")
    return current, held[index - offset]


def _where(relation: str, p: dict[str, Any], book: Book, period_sql: str) -> str:
    clauses = [period_sql] if period_sql else []
    for name, values in p["filters"].items():
        field = book.fields[name]
        rendered = ", ".join(v if field.numeric else _q(v) for v in values)
        clauses.append(f"{name} IN ({rendered})")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _sql(relation: str, metrics: list[Metric], p: dict[str, Any], book: Book) -> str:
    period_col = book.period_column.get(relation, "")
    kind = p["period"]["kind"]
    dims = list(p["dimensions"])
    year = _YEAR.get(book.domain_id, 4)
    selects = [f"{m.sql} AS {_alias(m)}" for m in metrics]

    if p["compare"] != "none":
        if not period_col:
            raise _refuse(f"{relation} records no reporting period to compare.", "plan.compare")
        offset = {"prior": 1, "year_ago": year, "between": 1}[p["compare"]]
        if p["compare"] == "between":
            earlier, later = sorted(p["period"]["values"])
            literal = {0: later, offset: earlier}
        elif kind == "explicit":
            literal = dict(zip((0, offset), _compare_periods(relation, p, book, offset)))
        def side(name: str, off: int) -> str:
            at = _q(literal[off]) if kind == "explicit" else _period_value(relation, period_col, off)
            where = _where(relation, p, book, f"{period_col} = {at}")
            group = f" GROUP BY {', '.join(dims)}" if dims else ""
            return (f"{name} AS (SELECT {', '.join(dims + selects)} FROM {relation}{where}{group})")
        between = p["compare"] == "between"
        if between:
            # Two named periods are symmetric: a group held in only one of them
            # still moved (0 -> X is an increase of X), so keep both sides and
            # read a missing additive total as zero. Averages stay NULL.
            joined = (f"cur FULL JOIN prev USING ({', '.join(dims)})" if dims
                      else "cur CROSS JOIN prev")
            dim_cols = list(dims)
        else:
            joined = (f"cur LEFT JOIN prev USING ({', '.join(dims)})" if dims
                      else "cur CROSS JOIN prev")
            dim_cols = [f"cur.{d}" for d in dims]
        outs = list(dim_cols)

        def sides(m: Metric) -> tuple[str, str]:
            a = _alias(m)
            if between and _additive(m):
                return f"COALESCE(cur.{a}, 0)", f"COALESCE(prev.{a}, 0)"
            return f"cur.{a}", f"prev.{a}"

        for m in metrics:
            a = _alias(m)
            c, v = sides(m)
            if (c, v) == (f"cur.{a}", f"prev.{a}"):
                outs += [f"cur.{a}", f"prev.{a} AS {a}_prev"]
            else:
                outs += [f"{c} AS {a}", f"{v} AS {a}_prev"]
            outs += [f"{c} - {v} AS {a}_change",
                     f"({c} - {v}) / NULLIF(ABS({v}), 0) * 100 AS {a}_pct_change"]
        c, v = sides(metrics[0])
        order = _order(p, dims, [c], dim_cols, change=f"{c} - {v}",
                       pct=f"({c} - {v}) / NULLIF(ABS({v}), 0)")
        limit = f" LIMIT {p['limit']}" if p["limit"] else ""
        return (f"WITH {side('cur', 0)}, {side('prev', offset)} "
                f"SELECT {', '.join(outs)} FROM {joined}{order}{limit}")

    if kind in ("last_n", "all"):
        if not period_col:
            raise _refuse(f"{relation} records no reporting period.", "plan.period")
        dims = [period_col] + dims
        period_sql = "" if kind == "all" else (
            f"{period_col} IN (SELECT DISTINCT {period_col} FROM {relation} "
            f"ORDER BY {period_col} DESC LIMIT {p['period']['n']})")
    elif kind == "explicit":
        if not period_col:
            raise _refuse(f"{relation} records no reporting period.", "plan.period")
        if _period_in_rows(p):
            dims = [period_col] + dims
        period_sql = _explicit_in(period_col, p["period"]["values"])
    else:
        offset = {"latest": 0, "prior": 1, "year_ago": year}[kind]
        period_sql = f"{period_col} = {_period_value(relation, period_col, offset)}" if period_col else ""
    where = _where(relation, p, book, period_sql)
    group = f" GROUP BY {', '.join(dims)}" if dims else ""
    order = _order(p, dims, [_alias(metrics[0])], dims, period_first=_period_in_rows(p))
    limit = f" LIMIT {p['limit']}" if p["limit"] else ""
    return f"SELECT {', '.join(dims + selects)} FROM {relation}{where}{group}{order}{limit}"


def _additive(metric: Metric) -> bool:
    """A total or a count: a group with no rows holds zero of it."""
    return metric.sql.strip().upper().startswith(("SUM(", "COUNT("))


def _alias(metric: Metric) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", metric.metric_id.split("@")[0].lower())
    if metric.unit == "percent" and not base.endswith("_pct"):
        base += "_pct"
    return base


def _order(p: dict[str, Any], dims: list[str], measure: list[str], dim_cols: list[str],
           period_first: bool = False, change: str = "", pct: str = "") -> str:
    if not dim_cols:
        return ""
    sort = p["sort"]
    if sort in CHANGE_SORTS and change:
        direction = "ASC" if sort.endswith("_asc") else "DESC"
        # A movement from zero has no percentage: rank it last, never first.
        if sort.startswith("pct_"):
            return f" ORDER BY {pct} {direction} NULLS LAST, {', '.join(dim_cols)}"
        return f" ORDER BY {change} {direction} NULLS LAST, {', '.join(dim_cols)}"
    if sort == "dimension" or (sort == "auto" and period_first):
        return " ORDER BY " + ", ".join(dim_cols)
    direction = "ASC" if sort == "measure_asc" else "DESC"
    return f" ORDER BY {measure[0]} {direction}"
