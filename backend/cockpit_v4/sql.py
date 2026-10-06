"""
Validating and executing analyst-authored SQL against ONE domain's book.

Why V4 has its own
------------------
V3's executor is correct and V4 used it directly for a long time. It cannot
serve two books, and not because of a missing parameter: three of its controls
are corporate constants compiled into the module.

    * `_build_session` filters every table by a module-level `DOMAIN`.
    * `multiplication_risk` reads a hard-coded list of corporate join pairs
      and corporate additive measure names, so against `retail_account_month`
      it finds no risk -- not "no risk", but "nothing it knows about". A
      diagnostic that silently stops firing is worse than one that is absent.
    * Every refusal message says "its own twenty-quarter corporate domain
      only" and then lists the relations. Said to a reader in the Retail book
      it is simply false.

So this module is the V4 execution surface, and its facts come from the
`Catalog` of the session it was handed: the relations, the grains, the join
keys and the additive measures are that domain's, read at call time. V3 is not
modified and not imported here.

What it does NOT do
-------------------
It never repairs. It validates, executes, diagnoses and enforces limits, and
when something is wrong it returns the facts -- the engine's own message, the
columns that DO exist, the join that multiplies, the relations that are
readable -- and the analyst writes the next candidate. `execute` runs the SQL
exactly as given.

The boundary
------------
Unchanged from V3, because it was right: the session materializes only the
authorized relations of ONE domain, already filtered to tenant, release and
domain, and then sets `enable_external_access = false` and
`lock_configuration = true` before any authored SQL is admitted. That work
lives in `catalog.open_session`. Everything here runs inside that box.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod

#: Rows handed to the analyst. The full result stays in a bounded artifact; a
#: clipped table is reported as clipped and never as a complete aggregate.
MAX_MODEL_ROWS = 200

#: Rows a step may materialize at all.
MAX_RESULT_ROWS = 100_000

SYNTAX_ERROR = "SYNTAX_ERROR"
UNRESOLVED_FIELD = "UNRESOLVED_FIELD"
UNRESOLVED_RELATION = "UNRESOLVED_RELATION"
TYPE_MISMATCH = "TYPE_MISMATCH"
OUT_OF_SCOPE_ACCESS = "OUT_OF_SCOPE_ACCESS"
CROSS_DOMAIN_ACCESS = "CROSS_DOMAIN_ACCESS"
PERMISSION_DENIED = "PERMISSION_DENIED"
UNSAFE_OPERATION = "UNSAFE_OPERATION"
RUNTIME_ERROR = "RUNTIME_ERROR"
RESOURCE_LIMIT = "RESOURCE_LIMIT"
JOIN_MULTIPLICITY_RISK = "JOIN_MULTIPLICITY_RISK"


class SqlRejected(Exception):
    """A query this layer refuses. Carries the category and the facts."""

    def __init__(self, category: str, message: str, *,
                 unresolved: str = "", relation: str = "",
                 domain_id: str = "", detail: str = "",
                 facts: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.unresolved = unresolved
        self.relation = relation
        self.domain_id = domain_id
        #: Prose, for the person reading the trace.
        self.detail = detail
        #: The same statement as DATA, for everything that is not a person:
        #: the two relations, their grains, the key and the measures. The
        #: prose is assembled by f-string and will be re-worded; a test that
        #: has to regex an English sentence to learn which measure was at
        #: risk is a test that passes when the sentence is wrong.
        self.facts = dict(facts or {})


# ------------------------------------------------------------- 1. structure

_FORBIDDEN_CALLS = re.compile(
    r"\b(read_parquet|read_csv|read_csv_auto|read_json|read_json_auto|"
    r"read_blob|read_text|parquet_scan|csv_scan|glob|sniff_csv|"
    r"install|load_extension|attach|detach|copy_from|"
    r"duckdb_settings|duckdb_extensions|duckdb_databases|"
    r"pg_read_binary_file|lo_import|shell|system)\s*\(", re.I)

_FORBIDDEN_KEYWORDS = re.compile(
    r"^\s*(insert|update|delete|merge|create|drop|alter|truncate|grant|"
    r"revoke|attach|detach|install|load|copy|export|import|set|reset|call|"
    r"pragma|checkpoint|vacuum|begin|commit|rollback)\b", re.I)


def check_structure(sql: str) -> str:
    """One SELECT, and nothing else. Returns the normalized statement."""
    text = str(sql or "").strip()
    if not text:
        raise SqlRejected(SYNTAX_ERROR, "The submitted SQL is empty.")
    try:
        statements = duckdb.extract_statements(text)
    except Exception as exc:  # noqa: BLE001
        raise SqlRejected(SYNTAX_ERROR,
                          f"The SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise SqlRejected(
            UNSAFE_OPERATION,
            f"{len(statements)} statements were submitted. Exactly one SELECT "
            f"is permitted; multi-statement submissions are refused.")
    kind = getattr(statements[0], "type", None)
    if kind is not None and kind != duckdb.StatementType.SELECT:
        raise SqlRejected(
            UNSAFE_OPERATION,
            f"A {str(kind).split('.')[-1]} statement was submitted. The "
            f"Cockpit executes SELECT statements only: no writes, no schema "
            f"changes, no configuration changes and no data movement.")
    if _FORBIDDEN_KEYWORDS.match(text):
        raise SqlRejected(UNSAFE_OPERATION,
                          "Only a SELECT statement is permitted here.")
    found = _FORBIDDEN_CALLS.search(text)
    if found:
        raise SqlRejected(
            UNSAFE_OPERATION,
            f"{found.group(1)} is not available to the Cockpit. Query the "
            f"authorized views; there is no file, network or extension access "
            f"from here.")
    return text


# --------------------------------------------------- 2. domain authorization

_TABLE_REFERENCE = re.compile(
    r"\b(?:from|join)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)

_CTE_NAME = re.compile(
    r"(?:\bwith\b|,)\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+as\s*\(",
    re.IGNORECASE)

_DERIVED_ALIAS = re.compile(
    r"\)\s*(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def local_names(sql: str) -> set[str]:
    """Names the query defines itself: CTEs and derived-table aliases."""
    names = {m.lower() for m in _CTE_NAME.findall(str(sql))}
    names |= {m.lower() for m in _DERIVED_ALIAS.findall(str(sql))}
    return names


def referenced_relations(sql: str) -> set[str]:
    """Table names the query reads, minus the ones it defines for itself."""
    return {m.lower() for m in _TABLE_REFERENCE.findall(str(sql))} \
        - local_names(sql)


def _domain_of(session: Any) -> str:
    """Which book this session reads. Empty for the legacy catalogue.

    Read off the session, never a module constant. Empty is not an error: the
    legacy quarterly catalogue has no domain id and its messages simply do not
    name one.
    """
    return str(getattr(getattr(session, "catalog", None), "domain_id", "")
               or "")


def _label(domain_id: str) -> str:
    return dom.LABELS.get(domain_id, "Cockpit")


def authorize(sql: str, session: Any, *,
              also_allowed: tuple[str, ...] = ()) -> None:
    """Refuse a query that names a table this book does not contain.

    A relation belonging to the OTHER book is refused as exactly that, naming
    its owner. "Unknown relation" would send an analyst hunting for a typo in
    a name that is spelled perfectly, and -- worse -- it would leave a reader
    with the impression that the other book does not exist, rather than that
    this thread is not reading it.
    """
    domain_id = _domain_of(session)
    authorized = {r.lower() for r in session.relations}
    permitted = authorized | {str(a).lower() for a in also_allowed}
    for name in sorted(referenced_relations(sql)):
        if name in permitted:
            continue
        try:
            owner = schema_mod.domain_of_relation(name)
        except schema_mod.UnknownRelation:
            owner = ""
        if owner and domain_id and owner != domain_id:
            raise SqlRejected(
                CROSS_DOMAIN_ACCESS,
                f"{name!r} is a relation of the {_label(owner)} book and "
                f"this analysis is pinned to "
                f"{_label(domain_id)}. Nothing was substituted "
                f"and no {dom.SHORT_LABELS.get(domain_id, 'other')} table "
                f"was read in its place. A question about the other book "
                f"belongs in a "
                f"thread opened in that book. The relations readable here "
                f"are: {', '.join(sorted(authorized))}.",
                relation=name, domain_id=owner)
        raise SqlRejected(
            OUT_OF_SCOPE_ACCESS,
            f"{name!r} is not readable from this analysis. The readable "
            f"relations of the {_label(domain_id)} book are: "
            f"{', '.join(sorted(authorized))}.",
            relation=name, domain_id=domain_id)


# ------------------------------------------------------- 3. join multiplicity

#: Aggregates that a repeated row corrupts. `count` is included because a
#: fan-out inflates a count exactly as it inflates a total; `min`/`max` are
#: not, because repetition does not move an extreme.
_SUMMING = frozenset({"sum", "avg", "mean", "total", "count",
                      "sum_no_overflow", "fsum"})

#: One parser, reused. Parsing needs no catalogue and no data, so this is a
#: bare in-memory connection: the analyst's text is passed as a PARAMETER and
#: never concatenated, never executed, and it never touches the session that
#: holds the book.
_PARSER_LOCK = threading.Lock()
_PARSER: Any = None


def _parse(sql: str) -> dict[str, Any]:
    """The statement's parse tree, as DuckDB itself reads it.

    THE LOCK COVERS THE FETCH, not just the connect. A DuckDB connection
    carries ONE pending result, so `execute` and `fetchone` are two steps
    another thread can get between -- and two runs validating at the same
    moment would then read each other's parse trees, refusing, or failing
    to refuse, on the wrong query. Parsing takes about a millisecond, so
    serializing it costs nothing worth measuring.
    """
    global _PARSER
    with _PARSER_LOCK:
        if _PARSER is None:
            _PARSER = duckdb.connect()
        raw = _PARSER.execute("SELECT json_serialize_sql(?)",
                              [str(sql)]).fetchone()[0]
    return json.loads(raw)


@dataclass(frozen=True)
class _Scope:
    """One query scope: what it reads FROM, and what it aggregates.

    A scope is the unit the multiplicity question is actually about. Two
    relations only repeat each other's rows if they are joined TO EACH OTHER
    in the same scope; a relation read inside a scalar subquery is a separate
    result that is substituted in, and it repeats nothing.
    """

    tables: frozenset[str]
    #: (function name, is DISTINCT, the column names it reads)
    aggregates: tuple[tuple[str, bool, frozenset[str]], ...]


#: The node classes that aggregate. `WINDOW` is here because a windowed
#: total is a total: `SUM(x) OVER (PARTITION BY ...)` adds up the same
#: repeated rows, and reading only `FUNCTION` would have quietly stopped
#: refusing a case the text check did refuse.
_AGGREGATING = frozenset({"FUNCTION", "WINDOW"})


def _column_names(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for name in (node.get("column_names") or []):
            out.add(str(name).lower())
        for value in node.values():
            _column_names(value, out)
    elif isinstance(node, list):
        for value in node:
            _column_names(value, out)


def _arguments(part: dict[str, Any]) -> frozenset[str]:
    """The columns an aggregate ADDS UP, and not the ones it is framed by.

    `children` holds the arguments for both a plain aggregate and a window
    aggregate. A window's `partitions` and `orders` are read, not summed, so
    a measure appearing only there is not at risk and must not be reported
    as if it were.
    """
    columns: set[str] = set()
    _column_names(part.get("children"), columns)
    return frozenset(columns)


def _scopes(sql: str) -> tuple[_Scope, ...]:
    """Every query scope in this statement, from DuckDB's own parser.

    THE DEFECT THIS REPLACES. The check used to decide which relations a
    query joined by asking whether their NAMES appeared anywhere in the
    text: `name in lowered`, over the whole flattened statement. That is
    not a join graph, and a live run proved it -- a four-step analysis whose
    fourth step read two relations through independent scalar subqueries,
    with no JOIN keyword anywhere in it, was refused for "aggregating across
    the join between corp_covenant_quarter and corp_facility_quarter". The
    refusal quoted a join key the query never mentioned, because the key was
    read out of the catalogue rather than out of the query. The figures were
    correct to the last decimal.

    It cut the other way too: the escape hatch looked for the word
    "distinct" anywhere in the text, so a query that genuinely did
    double-count was excused by a comment that happened to contain it.

    Parsing costs about a millisecond and answers both.
    """
    doc = _parse(sql)
    if doc.get("error"):
        raise SqlRejected(
            SYNTAX_ERROR,
            f"The SQL could not be parsed for the grain check: "
            f"{doc.get('error_message') or 'unknown parse error'}")

    found: list[_Scope] = []

    def select_node(node: dict[str, Any]) -> None:
        tables: set[str] = set()
        aggregates: list[tuple[str, bool, frozenset[str]]] = []

        def from_table(part: Any) -> None:
            if not isinstance(part, dict):
                return
            kind = part.get("type")
            if kind == "BASE_TABLE":
                tables.add(str(part.get("table_name") or "").lower())
            elif kind == "JOIN":
                from_table(part.get("left"))
                from_table(part.get("right"))
            else:
                # A SUBQUERY, a derived table, a table function: its own
                # scope, and its rows are not this scope's rows.
                descend(part)

        def expression(part: Any) -> None:
            if isinstance(part, dict):
                if part.get("type") == "SELECT_NODE":
                    select_node(part)
                    return
                if part.get("class") in _AGGREGATING:
                    aggregates.append((
                        str(part.get("function_name") or "").lower(),
                        bool(part.get("distinct")), _arguments(part)))
                for key, value in part.items():
                    if key != "from_table":
                        expression(value)
            elif isinstance(part, list):
                for value in part:
                    expression(value)

        from_table(node.get("from_table"))
        for key, value in node.items():
            if key != "from_table":
                expression(value)
        found.append(_Scope(frozenset(tables), tuple(aggregates)))

    def descend(part: Any) -> None:
        if isinstance(part, dict):
            if part.get("type") == "SELECT_NODE":
                select_node(part)
                return
            for value in part.values():
                descend(value)
        elif isinstance(part, list):
            for value in part:
                descend(value)

    descend(doc)
    return tuple(found)


#: Units whose values are added up, and therefore double-counted by a join
#: that repeats the row carrying them. `notches` belongs here for the same
#: reason `count` does: a rating movement is a signed integer number of
#: grade steps, it is summed across a book -- "a net movement of +7
#: notches" -- and a join that repeats the borrower row inflates that total
#: exactly as it inflates an exposure. `days` and `months` are deliberately
#: out: a tenor repeated is wrong arithmetic, but nobody reports a total of
#: it.
_ADDITIVE_UNITS = ("rcy", "count", "notches")


def additive_measures(catalog: Any, relation: str) -> tuple[str, ...]:
    """The measures of this relation that a repeated row would double-count.

    Read off the CATALOGUE, so the answer is about the release the session
    actually opened. Keyed on the domain it was the current schema's list,
    which on a superseded release names measures that release does not have
    and misses ones it does -- and because the failure is swallowed below,
    the fan-out guard would have quietly weakened rather than complained.
    """
    try:
        spec = catalog.spec(relation)
    except Exception:  # noqa: BLE001 - a relation this release never had
        return ()
    return tuple(f.name for f in spec.fields
                 if f.additive == "additive" and f.unit in _ADDITIVE_UNITS)


def multiplication_risk(sql: str, session: Any) -> SqlRejected | None:
    """Whether this query aggregates an additive measure across a join that
    repeats it.

    A REPORT, not a correction. It names the grains, the join and the measure
    at risk, and stops: which de-duplication is right depends on what is being
    asked, and choosing one here would be choosing the analysis.

    The join pairs and the additive measures are read from the session's own
    catalogue, so the diagnostic is as alive in the Retail book as in the
    Corporate one. WHICH relations are joined, and whether the aggregate
    de-duplicates, are read from `_scopes` -- the query's own parse -- and
    never from its text; see that function for what the text scan cost.
    """
    named = {r.lower() for r in session.relations}
    domain_id = _domain_of(session)
    joins = getattr(session.catalog, "joins", None)
    if not domain_id or not callable(joins):
        # The legacy catalogue does not state its joins as data, so there is
        # nothing here to check them against. Silence, not a false all-clear:
        # `execute_tool` still runs V3's own diagnostic on a V3 session.
        return None

    here = _scopes(sql)
    for join in joins():
        many, one = str(join["left"]), str(join["right"])
        if many.lower() not in named or one.lower() not in named:
            continue
        # A JOIN THE CATALOGUE SAYS REPEATS NOTHING REFUSES NOTHING.
        #
        # `retail_behaviour_month -> retail_account_month` is declared "one
        # behaviour row to one account", and its own note says "this join
        # repeats nothing" -- yet every pair here was being read as many-to
        # -one, so summing an account's exposure across it was refused. The
        # fact was already stated in the catalogue; this check simply was
        # not reading it.
        if str(join.get("cardinality", "")).lower().startswith("one "):
            continue
        # The COARSER side is the one repeated by the join, so its additive
        # measures are the ones a total would count more than once.
        measures = additive_measures(session.catalog, one)
        if not measures:
            continue
        for scope in here:
            # BOTH RELATIONS, JOINED TO EACH OTHER, IN THIS SCOPE. A relation
            # read through a scalar subquery is in a scope of its own and
            # repeats nothing, which is the whole of the live false positive.
            if not {many.lower(), one.lower()} <= scope.tables:
                continue
            at_risk = sorted({
                m for m in measures
                for name, is_distinct, columns in scope.aggregates
                # An explicit DISTINCT is the author saying they know the
                # rows repeat and have taken the repetition out. Read off
                # the aggregate itself, not off the word appearing
                # somewhere in the text -- which a comment used to satisfy.
                if name in _SUMMING and not is_distinct and m in columns})
            if not at_risk:
                continue
            try:
                # The grain THIS RELEASE published, for the same reason.
                many_grain = session.catalog.spec(many).grain
                one_grain = session.catalog.spec(one).grain
            except Exception:  # noqa: BLE001
                many_grain = one_grain = "unknown"
            return SqlRejected(
                JOIN_MULTIPLICITY_RISK,
                f"This query aggregates {', '.join(at_risk)} across the join "
                f"between {many} and {one}, which repeats every {one} row "
                f"once per matching {many} row: {join.get('note', '')} The "
                f"total would count the same amount more than once.",
                relation=one, domain_id=domain_id,
                detail=(f"{many} grain: {many_grain}. {one} grain: "
                        f"{one_grain}. "
                        f"Join key: {', '.join(join.get('on', ()))}. "
                        f"Measures at risk: {', '.join(at_risk)}. "
                        f"CreditProbe does not choose the de-duplication: "
                        f"aggregating one side before joining, counting "
                        f"distinct keys, or a window function are all valid "
                        f"and only the question decides which."),
                facts={"many_relation": many, "many_grain": many_grain,
                       "one_relation": one, "one_grain": one_grain,
                       "join_key": list(join.get("on", ())),
                       "measures_at_risk": at_risk,
                       "joined_in_one_scope": sorted(scope.tables)})
    return None


# ------------------------------------------------------------- 4. diagnosis

_MISSING_COLUMN = re.compile(
    r'Referenced column "?([^"\s]+)"? not found', re.I)
_MISSING_TABLE = re.compile(
    r'Table with name ([^\s]+) does not exist', re.I)
_MISSING_CATALOG = re.compile(
    r'(?:Catalog Error|Binder Error).*?"?([A-Za-z_][A-Za-z0-9_.]*)"?\s+'
    r'does not exist', re.I)


def classify(error: Exception, session: Any) -> SqlRejected:
    """Turn an engine error into a category and a message this book can own.

    Sanitized: it names the query's own identifiers and THIS domain's
    relations, never a filesystem path, a connection string, or the other
    book's tables.
    """
    domain_id = _domain_of(session)
    label = _label(domain_id)
    readable = ", ".join(sorted(session.relations))
    raw = str(error)
    sanitized = re.sub(r"read_parquet\('[^']*'\)", "the authorized view", raw)
    sanitized = re.sub(r"/[^\s'\"]+/[^\s'\"]+\.parquet", "the authorized view",
                       sanitized)
    first = sanitized.strip().splitlines()[0][:400]

    column = _MISSING_COLUMN.search(raw)
    if column:
        name = column.group(1).split(".")[-1]
        return SqlRejected(UNRESOLVED_FIELD, first, unresolved=name,
                           domain_id=domain_id)
    table = _MISSING_TABLE.search(raw) or _MISSING_CATALOG.search(raw)
    if table:
        name = table.group(1).strip('"').split(".")[-1]
        if name.lower() not in {r.lower() for r in session.relations}:
            try:
                owner = schema_mod.domain_of_relation(name.lower())
            except schema_mod.UnknownRelation:
                owner = ""
            if owner and domain_id and owner != domain_id:
                return SqlRejected(
                    CROSS_DOMAIN_ACCESS,
                    f"{name!r} belongs to the {_label(owner)} book and is "
                    f"not present in this {label} analysis. The relations "
                    f"here are: {readable}.",
                    relation=name, domain_id=owner)
            return SqlRejected(
                OUT_OF_SCOPE_ACCESS,
                f"{name!r} is not readable from this analysis. The {label} "
                f"book reads: {readable}.",
                relation=name, domain_id=domain_id)
        return SqlRejected(UNRESOLVED_RELATION, first, relation=name,
                           domain_id=domain_id)
    if isinstance(error, duckdb.PermissionException):
        return SqlRejected(
            UNSAFE_OPERATION,
            "That operation reaches outside the Cockpit's data domain. There "
            "is no file, network or extension access from this session.",
            domain_id=domain_id)
    if isinstance(error, (duckdb.ConversionException,
                          duckdb.InvalidTypeException)):
        return SqlRejected(TYPE_MISMATCH, first,
                           domain_id=domain_id)
    if isinstance(error, duckdb.ParserException):
        return SqlRejected(SYNTAX_ERROR, first, domain_id=domain_id)
    if isinstance(error, duckdb.OutOfMemoryException):
        return SqlRejected(RESOURCE_LIMIT, first, domain_id=domain_id)
    return SqlRejected(RUNTIME_ERROR, first, domain_id=domain_id)


# ------------------------------------------------------------- 5. execution

@dataclass
class SqlResult:
    columns: list[dict[str, str]]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)
    #: Which book produced these rows. Stamped here so an artifact cannot be
    #: attributed to the wrong domain further downstream.
    domain_id: str = ""
    dataset_release_id: str = ""


def execute(sql: str, session: Any, *, deadline_seconds: float,
            max_rows: int = MAX_MODEL_ROWS,
            parameters: Any = None) -> SqlResult:
    """Run the query exactly as submitted, under a wall-clock deadline.

    The deadline is enforced by interrupting the connection from a watchdog
    thread, because a frontend timeout is not cancellation: the engine keeps
    working and the resource is still spent.
    """
    started = time.monotonic()
    timed_out = threading.Event()
    finished = threading.Event()

    def watchdog() -> None:
        if not finished.wait(max(0.05, deadline_seconds)):
            timed_out.set()
            try:
                session.connection.interrupt()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        if parameters is None:
            cursor = session.connection.execute(sql)
        else:
            cursor = session.connection.execute(sql, parameters)
        frame = cursor.fetch_df()
    except Exception as exc:  # noqa: BLE001
        finished.set()
        if timed_out.is_set():
            raise SqlRejected(
                RESOURCE_LIMIT,
                f"The query was cancelled after {deadline_seconds:.0f} "
                f"seconds. A row limit does not make a query cheap: it may "
                f"scan or sort the whole book before returning a few rows."
            ) from exc
        raise classify(exc, session) from exc
    finally:
        finished.set()

    elapsed = time.monotonic() - started
    total = int(len(frame))
    warnings: list[str] = []
    if total > MAX_RESULT_ROWS:
        raise SqlRejected(
            RESOURCE_LIMIT,
            f"The query produced {total} rows against a {MAX_RESULT_ROWS} "
            f"limit. Aggregate on the server side.")
    shown = frame.head(max_rows)
    truncated = total > max_rows
    if truncated:
        warnings.append(
            f"{total} rows were produced and the first {max_rows} are shown. "
            f"This is a CLIPPED table, not a complete aggregate: do not read "
            f"a total off it.")
    columns = [{"name": str(name), "type": str(dtype)}
               for name, dtype in zip(frame.columns, frame.dtypes)]
    return SqlResult(
        columns=columns,
        rows=shown.replace({float("nan"): None}).to_dict(orient="records"),
        row_count=total, truncated=truncated,
        elapsed_seconds=round(elapsed, 4), warnings=warnings,
        domain_id=_domain_of(session),
        dataset_release_id=str(getattr(
            getattr(session, "catalog", None), "dataset_release_id", "")))


# ------------------------------------------------- 6. packet diagnostics

def filter_values(relation: str, column: str, session: Any, *,
                  limit: int = 25) -> list[str]:
    """The values that actually exist for a filter column, in this book."""
    try:
        name = session.catalog.require_relation(relation)
        session.catalog.resolve(name, column)
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = session.connection.execute(
            f'SELECT DISTINCT "{column}" AS v FROM "{name}" '
            f'WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT {int(limit)}'
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [str(r[0]) for r in rows]


def sample_rows(relation: str, session: Any, *, limit: int = 10,
                columns: list[str] | None = None) -> dict[str, Any]:
    """Up to ten reproducible preview rows, ordered by this relation's key.

    Labelled with what it is: rows illustrate shape. They do not establish a
    total and they do not establish a missing rate.
    """
    name = session.catalog.require_relation(relation)
    spec = session.catalog.spec(name)
    keys = [c for c in (list(spec.key_columns) + [spec.period_column])
            if c and c in spec.columns]
    projection = ", ".join(f'"{c}"' for c in (columns or [])) or "*"
    order = ", ".join(f'"{c}"' for c in keys) or "1"
    frame = session.connection.execute(
        f'SELECT {projection} FROM "{name}" ORDER BY {order} '
        f"LIMIT {int(limit)}").fetch_df()
    return {
        "relation": name,
        "domain_id": _domain_of(session),
        "ordering": keys or ["(engine order)"],
        "columns_shown": (columns or list(frame.columns)),
        "rows": frame.replace({float("nan"): None}).to_dict(orient="records"),
        "limitation": ("Up to ten rows, ordered by the key above. They "
                       "illustrate the SHAPE of the relation. They do not "
                       "establish a total, a distribution or a missing rate."),
    }


__all__ = ["CROSS_DOMAIN_ACCESS", "JOIN_MULTIPLICITY_RISK", "MAX_MODEL_ROWS",
           "MAX_RESULT_ROWS", "OUT_OF_SCOPE_ACCESS", "PERMISSION_DENIED",
           "RESOURCE_LIMIT", "RUNTIME_ERROR", "SYNTAX_ERROR", "SqlRejected",
           "SqlResult", "TYPE_MISMATCH", "UNRESOLVED_FIELD",
           "UNRESOLVED_RELATION", "UNSAFE_OPERATION", "additive_measures",
           "authorize", "check_structure", "classify", "execute",
           "filter_values", "local_names", "multiplication_risk",
           "referenced_relations", "sample_rows"]
