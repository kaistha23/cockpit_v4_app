"""
Validating and executing Opus-authored SQL. Specification sections 7.6 and 10.1.

What this module does, and the one thing it never does
------------------------------------------------------
It validates, executes, diagnoses and enforces limits. It does NOT repair.
Section 7.6A makes Opus the sole owner of query authorship, so when a query
fails, this module returns facts -- the exact error, the fields that DO exist,
the valid join keys, the available filter values -- and Opus writes the next
candidate. There is no code path here that edits, completes or substitutes a
query, and `execute` takes the SQL exactly as given.

The boundary is a real one
--------------------------
A keyword blocklist is not a sandbox: a SELECT can call functions with effects,
read files and open sockets. So the connection is built to have nothing else in
it. The authorized relations are MATERIALIZED into the session -- one table per
allowlisted relation, projected to the declared columns and already filtered to
the authenticated tenant and the pinned release. Then, before any model-authored
SQL is admitted:

    SET enable_external_access = false   -- no file, no network, no extension
    SET lock_configuration = true        -- and the model cannot turn it back on

After that the session's entire universe is those tables. A query naming
`read_parquet`, `read_csv_auto`, `COPY ... TO`, `ATTACH`, `INSTALL` or another
schema fails inside the engine, not merely at a regex.

Materialized rather than lazy, and why it had to be
---------------------------------------------------
Lazy views over `read_parquet` were the obvious design and they do not work:
disabling file access disables the views too, since they re-read the file on
every query. The alternative, DuckDB's `allowed_directories` path allowlist,
was measured and is NOT a boundary -- with it set and the configuration locked,
`read_csv_auto('/etc/passwd')`, `COPY ... TO '/tmp/x.csv'` and `ATTACH` all
still succeeded. Materializing is what actually closes every one of them, so
that is what this does.

It costs about 1.2 seconds and 250 MB for the demonstration release, which is
too much to pay inside a 60-second request budget, so a session is cached per
tenant and release and reused. That is sound because a release is immutable and
the tenant filter is baked into the tables: the cached session cannot serve a
different tenant or a different release, by construction rather than by check.

Three layers, in order
----------------------
1. **Structure.** Exactly one statement, and it must be a SELECT. Multi-
   statement injection and every DDL/DML form are refused before the engine
   sees them.
2. **Binding.** `EXPLAIN` binds the query against the real views WITHOUT
   executing it, so an unresolved column or relation is diagnosed exactly, with
   the engine's own message, before any work is done.
3. **Execution.** Under a wall-clock deadline enforced by interrupting the
   connection, with a bounded row count.

A result-row limit alone does not make a query cheap -- it may sort the whole
book to return ten rows -- so the deadline, not the LIMIT, is the control.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import duckdb

from backend.cockpit_agentic import DOMAIN
from backend.cockpit_agentic import catalog as catalog_mod
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic import scope as scope_mod
from backend.cockpit_agentic import store
from backend.cockpit_agentic.contracts import (INVALID_FILTER_VALUE,
                                               JOIN_MULTIPLICITY_RISK,
                                               OUT_OF_SCOPE_ACCESS,
                                               PERMISSION_DENIED,
                                               RESOURCE_LIMIT, RUNTIME_ERROR,
                                               SYNTAX_ERROR, TYPE_MISMATCH,
                                               UNRESOLVED_FIELD,
                                               UNRESOLVED_RELATION,
                                               UNSAFE_OPERATION)

#: Rows returned to the model. The full result stays in a bounded artifact; a
#: clipped table is reported as clipped and never as a complete aggregate.
MAX_MODEL_ROWS = 200

#: Rows a step may materialize at all.
MAX_RESULT_ROWS = 100_000


class SqlRejected(Exception):
    """A query the validator refuses. Carries the section 8.2 category."""

    def __init__(self, category: str, message: str, *,
                 unresolved: str = "", relation: str = "",
                 detail: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.unresolved = unresolved
        self.relation = relation
        self.detail = detail


# ------------------------------------------------------------- 1. structure

#: Operations that are never permitted, whatever they are wrapped in. The
#: statement-type check below is the real control; this catches the forms
#: DuckDB parses as a SELECT that nonetheless reach outside.
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
    except Exception as e:                                  # noqa: BLE001
        raise SqlRejected(SYNTAX_ERROR,
                          f"The SQL could not be parsed: {e}") from e
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
        raise SqlRejected(
            UNSAFE_OPERATION,
            "Only a SELECT statement is permitted here.")
    found = _FORBIDDEN_CALLS.search(text)
    if found:
        raise SqlRejected(
            UNSAFE_OPERATION,
            f"{found.group(1)} is not available to the Cockpit. Query the "
            f"authorized views; there is no file, network or extension access "
            f"from here.")
    return text


# ------------------------------------------------- 2. the sandboxed session

@dataclass
class Session:
    """One tenant's connection to one pinned release.

    Reused across requests, and serialized by its own lock. Section 9.4 sets
    one active analytical execution per thread and at most two per user, so
    serializing here matches the documented concurrency policy rather than
    quietly exceeding it -- and it keeps `interrupt` meaning what it says,
    since an interrupt reaches the whole connection.
    """

    connection: Any
    scope: scope_mod.Scope
    catalog: catalog_mod.Catalog
    relations: tuple[str, ...]
    lock: threading.RLock = field(default_factory=threading.RLock)
    built_seconds: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.scope.tenant_id, self.scope.dataset_release_id)

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:                                   # noqa: BLE001
            pass


def _build_session(*, scope: scope_mod.Scope,
                   catalog: catalog_mod.Catalog) -> Session:
    """Materialize the authorized relations, then shut the door behind them.

    Order is the whole security argument: the tables are created while file
    access still works, and file access is disabled and the configuration
    locked BEFORE any model-authored SQL is admitted.
    """
    started = time.monotonic()
    # A tenant the release does not contain would materialize ten empty tables
    # and every query would return zero rows -- which reads as "the portfolio
    # is empty" rather than "you are looking at the wrong release". Say it.
    try:
        tenants = store.read_manifest(scope.dataset_release_id).get("tenants")
    except store.ReleaseNotFound:
        tenants = None
    if tenants and scope.tenant_id not in tenants:
        raise SqlRejected(
            PERMISSION_DENIED,
            f"Release {scope.dataset_release_id!r} holds no data for tenant "
            f"{scope.tenant_id!r}. This is an access or configuration "
            f"mismatch, not an empty portfolio.")

    connection = duckdb.connect(database=":memory:")
    connection.execute("SET threads TO 2")
    connection.execute("SET memory_limit = '512MB'")

    built: list[str] = []
    for relation in sorted(scope.relations):
        try:
            path = store.relation_path(scope.dataset_release_id, relation)
        except store.ReleaseNotFound:
            continue
        if relation == catalog_mod.MACRO_PIVOT:
            columns = (["reporting_quarter", "country_or_region", "scenario_id"]
                       + [s.name for s in F.MACRO_PIVOT_FIELDS])
            predicate = ""
        else:
            columns = [s.name for s in F.fields_of(relation)]
            # The tenant, release and domain filters are part of the TABLE, not
            # something the model has to remember to write. Section 10.1: do
            # not rely on Opus including the correct WHERE tenant_id.
            predicate = (
                f" WHERE tenant_id = '{scope.tenant_id}'"
                f" AND dataset_release_id = '{scope.dataset_release_id}'"
                f" AND domain_id = '{DOMAIN}'")
        projection = ", ".join(f'"{c}"' for c in columns)
        connection.execute(
            f'CREATE TABLE "{relation}" AS SELECT {projection} '
            f"FROM read_parquet('{path}'){predicate}")
        built.append(relation)

    # From here the session can reach nothing but those tables.
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return Session(connection=connection, scope=scope, catalog=catalog,
                   relations=tuple(built),
                   built_seconds=round(time.monotonic() - started, 3))


#: Cached sessions, keyed by tenant and release. Bounded: a deployment serving
#: many tenants must not accumulate one materialized copy per tenant for ever.
_SESSIONS: "OrderedDict[tuple[str, str], Session]" = OrderedDict()
_SESSIONS_LOCK = threading.RLock()
MAX_CACHED_SESSIONS = 4


def open_session(*, scope: scope_mod.Scope, catalog: catalog_mod.Catalog,
                 reuse: bool = True) -> Session:
    """A session for this tenant and release, built or reused.

    The cache key carries the tenant AND the release, so a cached session can
    never serve either a different tenant or a different release -- the two
    isolation properties that matter, held by the key rather than by a check
    someone has to remember.
    """
    key = (scope.tenant_id, scope.dataset_release_id)
    if not reuse:
        return _build_session(scope=scope, catalog=catalog)
    with _SESSIONS_LOCK:
        cached = _SESSIONS.get(key)
        if cached is not None:
            _SESSIONS.move_to_end(key)
            return cached
    session = _build_session(scope=scope, catalog=catalog)
    with _SESSIONS_LOCK:
        _SESSIONS[key] = session
        _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > MAX_CACHED_SESSIONS:
            _key, evicted = _SESSIONS.popitem(last=False)
            evicted.close()
    return session


def clear_sessions() -> None:
    with _SESSIONS_LOCK:
        while _SESSIONS:
            _key, session = _SESSIONS.popitem()
            session.close()


# --------------------------------------------------------------- 3. binding

_MISSING_COLUMN = re.compile(
    r'Referenced column "?([^"\s]+)"? not found', re.I)
_MISSING_TABLE = re.compile(
    r'Table with name ([^\s]+) does not exist', re.I)
_MISSING_CATALOG = re.compile(
    r'(?:Catalog Error|Binder Error).*?"?([A-Za-z_][A-Za-z0-9_.]*)"?\s+does not exist',
    re.I)


def _classify(error: Exception, session: Session) -> SqlRejected:
    """Turn an engine error into a section 8.2 category and a clean message.

    The message is sanitized: it names the query's own identifiers and this
    domain's relations, never a filesystem path, a connection string or a
    schema belonging to another module -- section 10.3.
    """
    raw = str(error)
    sanitized = re.sub(r"read_parquet\('[^']*'\)", "the authorized view", raw)
    sanitized = re.sub(r"/[^\s'\"]+/[^\s'\"]+\.parquet", "the authorized view",
                       sanitized)
    first = sanitized.strip().splitlines()[0][:400]

    column = _MISSING_COLUMN.search(raw)
    if column:
        return SqlRejected(UNRESOLVED_FIELD, first,
                           unresolved=column.group(1).split(".")[-1])
    table = _MISSING_TABLE.search(raw) or _MISSING_CATALOG.search(raw)
    if table:
        name = table.group(1).strip('"').split(".")[-1]
        if name not in session.scope.relations:
            return SqlRejected(
                OUT_OF_SCOPE_ACCESS,
                f"{name!r} is not readable from the Cockpit. The Cockpit reads "
                f"its own twenty-quarter corporate domain only: "
                f"{', '.join(sorted(session.scope.relations))}.",
                relation=name)
        return SqlRejected(UNRESOLVED_RELATION, first, relation=name)
    if isinstance(error, duckdb.PermissionException):
        return SqlRejected(
            UNSAFE_OPERATION,
            "That operation reaches outside the Cockpit's data domain. There "
            "is no file, network or extension access from this session.")
    if isinstance(error, (duckdb.ConversionException,
                          duckdb.InvalidTypeException)):
        return SqlRejected(TYPE_MISMATCH, first)
    if isinstance(error, duckdb.ParserException):
        return SqlRejected(SYNTAX_ERROR, first)
    if isinstance(error, duckdb.BinderException):
        return SqlRejected(UNRESOLVED_FIELD, first)
    if isinstance(error, duckdb.OutOfMemoryException):
        return SqlRejected(RESOURCE_LIMIT, first)
    return SqlRejected(RUNTIME_ERROR, first)


#: Which relations multiply which, and what gets multiplied. Section 19.
#:
#: These are facts about the data, not opinions about the analysis. One
#: facility can be secured by several collateral assets and bound by several
#: covenants, so a join between them repeats the facility's exposure once per
#: matching row -- and a SUM over that join is silently wrong in a way the
#: engine cannot see and the reader cannot check.
#:
#: The pairs are directional and specific rather than a blanket "joins are
#: risky", because a diagnostic that fires on every join is one an author
#: learns to ignore.
MULTIPLYING_JOINS: tuple[tuple[str, str, str], ...] = (
    ("cockpit_facility_quarter", "cockpit_collateral_allocation",
     "one facility position can have several collateral allocations, so the "
     "facility's exposure and ECL repeat once per allocation"),
    ("cockpit_facility_quarter", "cockpit_covenant_quarter",
     "one facility's borrower can be bound by several covenants, so the "
     "facility's exposure repeats once per covenant test"),
    ("cockpit_facility_quarter", "cockpit_borrower_financial_quarter",
     "one borrower can hold several facilities, so joining the statement to "
     "facilities repeats the statement once per facility"),
    ("cockpit_facility_quarter", "cockpit_qualitative_quarter",
     "there are twenty qualitative answers per borrower-quarter, so the "
     "facility's measures repeat twenty times"),
    ("cockpit_borrower_financial_quarter", "cockpit_qualitative_quarter",
     "twenty qualitative answers per borrower-quarter multiply every "
     "statement line by twenty"),
    ("cockpit_collateral_quarter", "cockpit_collateral_allocation",
     "an asset shared across facilities appears once in the asset relation "
     "and once per facility in the allocation, so summing gross value across "
     "the join double counts it"),
    ("cockpit_facility_quarter", "cockpit_ifrs9_detail",
     "the IFRS 9 detail is per run, scenario and horizon index, so a facility "
     "row repeats once per scenario-horizon cell"),
    ("cockpit_facility_quarter", "cockpit_macro_quarter_window",
     "the macro window holds twenty offsets per anchor, so every facility row "
     "repeats twenty times"),
)

#: What must not be summed across a multiplying join without a de-duplication.
_ADDITIVE = ("gross_carrying_amount", "drawn_balance", "undrawn_balance",
             "approved_limit", "ecl_reported", "ecl_12m_reported",
             "ecl_lifetime_reported", "ecl_modelled", "ecl_overlay",
             "ead_reported", "gross_market_value", "net_realizable_value",
             "total_assets", "revenue", "ebitda", "net_profit")

_AGGREGATE = re.compile(r"\b(sum|avg|mean|total)\s*\(", re.I)
_DISTINCTING = re.compile(r"\b(distinct|group\s+by|qualify|row_number|"
                          r"partition\s+by)\b", re.I)


def multiplication_risk(sql: str, session: Session) -> SqlRejected | None:
    """Whether this query aggregates an additive measure across a join that
    repeats it. Section 19.

    A REPORT, not a correction. It names the grains, the multiplicity and the
    measure at risk, and stops: which de-duplication is right depends on what
    is being asked, and choosing one here would be choosing the analysis.

    Silent about a join that already de-duplicates, because a diagnostic that
    fires on correct work is one an author learns to route around.
    """
    lowered = " ".join(str(sql).lower().split())
    if not _AGGREGATE.search(lowered):
        return None
    named = [r for r in session.relations if r in lowered]
    if len(named) < 2:
        return None

    for left, right, why in MULTIPLYING_JOINS:
        if left not in named or right not in named:
            continue
        measures = [m for m in _ADDITIVE
                    if re.search(rf"\b(sum|avg|mean|total)\s*\(\s*"
                                 rf"(distinct\s+)?[\w.]*{m}\b", lowered)]
        if not measures:
            continue
        # An explicit de-duplication is the author saying they know. Take
        # their word for it: the alternative is refusing correct queries.
        if _DISTINCTING.search(lowered) and "distinct" in lowered:
            continue
        return SqlRejected(
            JOIN_MULTIPLICITY_RISK,
            f"This query aggregates {', '.join(measures)} across a join "
            f"between {left} and {right}, which multiplies rows: {why}. The "
            f"total would count the same amount more than once.",
            relation=left,
            detail=(f"{left} grain: {F.GRAIN.get(left, 'unknown')}. "
                    f"{right} grain: {F.GRAIN.get(right, 'unknown')}. "
                    f"Measures at risk: {', '.join(measures)}. "
                    f"CreditProbe does not choose the de-duplication: "
                    f"aggregating one side before joining, counting distinct "
                    f"keys, or a window function are all valid and only the "
                    f"question decides which."))
    return None


def bind(sql: str, session: Session) -> None:
    """Resolve the query against the real views WITHOUT running it, then check
    it does not silently multiply a measure."""
    try:
        session.connection.execute(f"EXPLAIN {sql}")
    except Exception as e:                                  # noqa: BLE001
        raise _classify(e, session) from e
    risk = multiplication_risk(sql, session)
    if risk is not None:
        raise risk


# ------------------------------------------------------------- 4. execution

@dataclass
class SqlResult:
    columns: list[dict[str, str]]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_seconds: float
    warnings: list[str]


def execute(sql: str, session: Session, *, deadline_seconds: float,
            max_rows: int = MAX_MODEL_ROWS) -> SqlResult:
    """Run the query exactly as submitted, under a wall-clock deadline.

    The deadline is enforced by interrupting the connection from a watchdog
    thread, because a frontend timeout is not cancellation: the engine keeps
    working and the resource is still spent.
    """
    started = time.monotonic()
    timed_out = threading.Event()

    def watchdog() -> None:
        if not finished.wait(max(0.05, deadline_seconds)):
            timed_out.set()
            try:
                session.connection.interrupt()
            except Exception:                               # noqa: BLE001
                pass

    finished = threading.Event()
    guard = threading.Thread(target=watchdog, daemon=True)
    guard.start()
    try:
        cursor = session.connection.execute(sql)
        frame = cursor.fetch_df()
    except Exception as e:                                  # noqa: BLE001
        finished.set()
        if timed_out.is_set():
            raise SqlRejected(
                RESOURCE_LIMIT,
                f"The query was cancelled after {deadline_seconds:.0f} "
                f"seconds. A row limit does not make a query cheap: it may "
                f"scan or sort the whole book before returning a few rows.")
        raise _classify(e, session) from e
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
            f"This is a CLIPPED table, not a complete aggregate: do not read a "
            f"total off it.")
    columns = [{"name": str(name), "type": str(dtype)}
               for name, dtype in zip(frame.columns, frame.dtypes)]
    return SqlResult(
        columns=columns,
        rows=shown.replace({float("nan"): None}).to_dict(orient="records"),
        row_count=total, truncated=truncated,
        elapsed_seconds=round(elapsed, 4), warnings=warnings)


# ------------------------------------------------- diagnostics for the packet

def filter_values(relation: str, column: str, session: Session, *,
                  limit: int = 25) -> list[str]:
    """The values that actually exist for a filter column.

    Section 7.6: filter syntax being valid does not guarantee matching values
    exist, and an unsupported value returns the available permitted choices
    rather than secretly substituting another.
    """
    try:
        scope_mod.permit(relation, session.scope)
        session.catalog.resolve(relation, column)
    except Exception:                                       # noqa: BLE001
        return []
    try:
        rows = session.connection.execute(
            f'SELECT DISTINCT "{column}" AS v FROM "{relation}" '
            f'WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT {int(limit)}'
        ).fetchall()
    except Exception:                                       # noqa: BLE001
        return []
    return [str(r[0]) for r in rows]


def sample_rows(relation: str, session: Session, *, limit: int = 10,
                columns: list[str] | None = None) -> dict[str, Any]:
    """Up to ten reproducible, permission-filtered preview rows.

    Ordered explicitly so the sample is the same on every build, and labelled
    with what it is: rows illustrate shape. They do not establish a total and
    they do not establish a missing rate -- the profiler does that, from the
    whole release.
    """
    scope_mod.permit(relation, session.scope)
    keys = [c for c in ("reporting_quarter", "facility_id", "position_id",
                        "borrower_id", "collateral_id", "covenant_id",
                        "question_id", "factor_id", "quarter_offset",
                        "scenario_id")
            if F.find(relation, c) is not None]
    projection = ", ".join(f'"{c}"' for c in (columns or [])) or "*"
    order = ", ".join(f'"{c}"' for c in keys) or "1"
    frame = session.connection.execute(
        f'SELECT {projection} FROM "{relation}" ORDER BY {order} '
        f"LIMIT {int(limit)}").fetch_df()
    return {
        "relation": relation,
        "ordering": keys or ["(engine order)"],
        "columns_shown": (columns or list(frame.columns)),
        "rows": frame.replace({float("nan"): None}).to_dict(orient="records"),
        "limitation": ("Up to ten rows, ordered by the key above. They "
                       "illustrate the SHAPE of the relation. They do not "
                       "establish a total, a distribution or a missing rate: "
                       "the coverage profile does that, from every row."),
    }


__all__ = ["MAX_MODEL_ROWS", "MAX_RESULT_ROWS", "Session", "SqlRejected",
           "SqlResult", "bind", "check_structure", "execute", "filter_values",
           "open_session", "sample_rows"]
