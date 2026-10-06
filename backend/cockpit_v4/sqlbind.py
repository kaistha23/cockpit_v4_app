"""
Proving a query bindable before saying it was validated.

The defect this exists for
--------------------------
A live run showed "Query validated", then failed "at the bind check". Both
statements were true and the order was wrong: validation checked the SQL's
structure and that its relations were authorized, announced success, and only
then — inside the executor — asked DuckDB whether the query resolved at all.

The binder failure itself had a specific cause. `execute_analysis` publishes a
`parameters` object on every step, so a model following the schema may write
`WHERE reporting_quarter = ?` and supply the value there. Nothing carried that
object to the engine. DuckDB then saw a placeholder with no value and refused:

    Invalid Input Error: Values were not provided for the following prepared
    statement parameters: 1

which is a bind failure caused by a contract the application advertised and
did not honour. Both halves are fixed here: parameters reach the engine, and
the bind is proven at validation time so the announcement is true.

Why EXPLAIN
-----------
`EXPLAIN` runs the parser, the binder and the optimizer and produces a plan.
It resolves every relation, column, alias, function, GROUP BY position and
ORDER BY term, and it executes nothing — an EXPLAIN of an unguarded
cross join over the whole book returns in about three milliseconds. It is a
real proof of bindability at no analytical cost, which is what section 6 asks
for.

What this module will never do
------------------------------
Repair. A bind failure returns the exact sanitized DuckDB diagnostic — the
exception type and the binder's own message — to the analyst, which is the
only repair mechanism V4 has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: DuckDB names the placeholder style by its syntax. Both are legal in a
#: submitted step, and each needs a different Python argument.
_POSITIONAL = re.compile(r"(?<![:$@\w])\?")
_NAMED = re.compile(r"\$[A-Za-z_]\w*")

#: Paths and connection strings never reach a model-facing message.
_PARQUET_CALL = re.compile(r"read_parquet\('[^']*'\)")
_PATH = re.compile(r"/[^\s'\"]+/[^\s'\"]+\.parquet")


class BindFailure(Exception):
    """The query did not bind. Carries what an operator needs and no more."""

    def __init__(self, message: str, *, exception_type: str = "",
                 raw: str = "", unresolved: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.exception_type = exception_type
        self.raw = raw
        self.unresolved = unresolved

    def detail(self) -> dict[str, Any]:
        return {"phase": "bind",
                "duckdb_exception_type": self.exception_type,
                "binder_message": self.message,
                "unresolved_name": self.unresolved}


@dataclass
class Placeholders:
    """What the SQL asks for, and what the step supplied."""

    positional: int = 0
    named: tuple[str, ...] = ()
    supplied: dict[str, Any] = field(default_factory=dict)

    @property
    def any_required(self) -> bool:
        return bool(self.positional or self.named)


def placeholders(sql: str, parameters: dict[str, Any] | None
                 ) -> Placeholders:
    text = str(sql or "")
    return Placeholders(
        positional=len(_POSITIONAL.findall(text)),
        named=tuple(sorted({m[1:] for m in _NAMED.findall(text)})),
        supplied=dict(parameters or {}))


def parameter_argument(sql: str, parameters: dict[str, Any] | None
                       ) -> list[Any] | dict[str, Any] | None:
    """The argument DuckDB wants, or None when the query takes no parameters.

    Positional `?` placeholders want a LIST in order; DuckDB numbers them from
    1, so a step supplying `{"1": "2026Q2"}` is ordered by that number rather
    than by the accident of dictionary order. Named `$name` placeholders want
    the mapping as it stands.

    Raises `BindFailure` when the supplied parameters cannot satisfy the SQL,
    because that is a bind problem and it is better named here than as a
    DuckDB message the analyst has to decode.
    """
    found = placeholders(sql, parameters)
    supplied = found.supplied

    if not found.any_required:
        if supplied:
            raise BindFailure(
                f"the step supplied {len(supplied)} parameter(s) "
                f"({sorted(supplied)}) but the SQL contains no placeholder to "
                f"bind them to. Either reference them with ? or $name, or "
                f"send no parameters.",
                exception_type="ParameterMismatch")
        return None

    if found.positional and found.named:
        raise BindFailure(
            f"the SQL mixes {found.positional} positional placeholder(s) with "
            f"named placeholder(s) {list(found.named)}. DuckDB binds one "
            f"style per statement; use either ? or $name throughout.",
            exception_type="ParameterMismatch")

    if found.named:
        missing = [name for name in found.named if name not in supplied]
        if missing:
            raise BindFailure(
                f"the SQL references {missing} and the step's parameters "
                f"supply {sorted(supplied)}. Every named placeholder needs a "
                f"value; nothing was substituted.",
                exception_type="ParameterMismatch")
        return {name: supplied[name] for name in found.named}

    # Positional. Keys may be "1".."n" or "$1".."$n"; anything else is not an
    # ordering this can honour, and guessing one would be choosing which value
    # lands in which filter.
    ordered: list[Any] = []
    normalised: dict[int, Any] = {}
    for key, value in supplied.items():
        text = str(key).lstrip("$?")
        if not text.isdigit():
            raise BindFailure(
                f"the SQL uses {found.positional} positional placeholder(s), "
                f"so the step's parameters must be numbered (\"1\", \"2\", …) "
                f"in the order they appear. Got {sorted(supplied)}.",
                exception_type="ParameterMismatch")
        normalised[int(text)] = value
    for index in range(1, found.positional + 1):
        if index not in normalised:
            raise BindFailure(
                f"the SQL uses {found.positional} positional placeholder(s) "
                f"and parameter {index} was not supplied. DuckDB refuses a "
                f"statement with an unbound placeholder; nothing was "
                f"substituted.",
                exception_type="ParameterMismatch")
        ordered.append(normalised[index])
    extra = sorted(k for k in normalised if k > found.positional)
    if extra:
        raise BindFailure(
            f"the SQL uses {found.positional} positional placeholder(s) but "
            f"parameters {extra} were also supplied.",
            exception_type="ParameterMismatch")
    return ordered


#: DuckDB lines that propose a DIFFERENT name. Dropped: nothing in V4 offers
#: a substitute for a name that did not resolve (V4-AT-048). Lines that say
#: how to make the SAME query valid -- "Either add it to the GROUP BY list,
#: or use ANY_VALUE(...)" -- are kept: they name no other column, and losing
#: them made the analyst repeat the same GROUP BY error until its
#: submissions ran out.
_SUBSTITUTE_HINT = re.compile(r"^\s*(Candidate bindings|Did you mean)\b", re.I)


def _sanitize(raw: str) -> str:
    cleaned = _PARQUET_CALL.sub("the authorized view", raw)
    cleaned = _PATH.sub("the authorized view", cleaned)
    kept: list[str] = []
    for line in cleaned.strip().splitlines():
        if line.strip().startswith("LINE "):
            break  # the query echo and its caret follow; the model has the query
        if not line.strip() or _SUBSTITUTE_HINT.match(line):
            continue
        kept.append(line.strip())
    return " ".join(kept)[:600]


def sanitize(raw: str) -> str:
    """Public form of `_sanitize`, for runtime errors raised outside binding."""
    return _sanitize(raw)


_MISSING_COLUMN = re.compile(r'Referenced column "?([^"\s]+)"? not found', re.I)
_MISSING_TABLE = re.compile(r"Table with name ([^\s]+) does not exist", re.I)


def prove_bindable(sql: str, session: Any, *,
                   parameters: dict[str, Any] | None = None) -> None:
    """Bind the query against the real views. Executes nothing.

    Raises `BindFailure` with DuckDB's own diagnostic when it does not bind.
    """
    argument = parameter_argument(sql, parameters)
    connection = getattr(session, "connection", None)
    if connection is None:
        raise BindFailure(
            "no analytical session is open, so the query could not be bound.",
            exception_type="SessionUnavailable")
    try:
        if argument is None:
            connection.execute(f"EXPLAIN {sql}")
        else:
            connection.execute(f"EXPLAIN {sql}", argument)
    except Exception as exc:  # noqa: BLE001
        raw = str(exc)
        message = _sanitize(raw) or f"{type(exc).__name__}"
        column = _MISSING_COLUMN.search(raw)
        table = _MISSING_TABLE.search(raw)
        unresolved = ""
        if column:
            unresolved = column.group(1).split(".")[-1]
        elif table:
            unresolved = table.group(1).strip('"').split(".")[-1]
        raise BindFailure(message, exception_type=type(exc).__name__,
                          raw=raw, unresolved=unresolved) from exc


__all__ = ["BindFailure", "Placeholders", "parameter_argument",
           "placeholders", "prove_bindable"]
