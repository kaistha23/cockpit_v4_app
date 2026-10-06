"""
Validating arithmetic that is real but has no row of its own.

The defect this exists for
--------------------------
A live run asked for exposure at default by sector. Everything worked: the
request was understood, EAD resolved to `ead_reported`, the latest quarter
resolved from the calendar, the SQL bound, it executed, twelve rows came
back. Then publication was refused, twice:

    claim 'total_ead' names row 'all sectors', which is not in artifact ...
    claim 'top4_pct' names row 'top 4 sectors', which is not in artifact ...

The analyst had done nothing wrong. A total across twelve sectors is a real
number a credit officer needs, and so is the share carried by the largest
four -- but neither is a cell in the result, because the query grouped by
sector and did not also produce a total row. Faced with a validator that
would only accept a pointer to one physical cell, the only move left was to
invent a row name, and inventing it is what got the answer rejected.

The fix is not to trust the analyst's arithmetic. It is to let a claim SHOW
its arithmetic, over cells that really exist, and to recompute it here.

What this is not
----------------
Not an expression language. There is no parser, no `eval`, no Python in the
payload and no way to write a new operation. A derivation names one operation
from a closed set, and each operation knows its own arity and how it consumes
its operands. Everything a claim can say is one of the twelve shapes below,
and anything else is refused by name.

Nesting is bounded at exactly one level -- an operation over operands, where
an operand is a set of cells in one column of one artifact. That is enough
for every derived figure a Cockpit answer has needed (a total, a share, a
movement, a growth rate, a weighted average, a rank), and it keeps the
schema finite, which matters because the provider's tool dialect rejects the
recursive constructs a deeper tree would need.

Exactness
---------
Every value is `Decimal`, parsed from the artifact's own stored string form.
Floats are not used anywhere in this module: a book total that reads
20720.343518249818 in one place and 20720.34351824982 in another is a
reconciliation argument nobody should have to have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# ---- the closed operation set ------------------------------------------

IDENTITY = "identity"
SUM = "sum"
DIFFERENCE = "difference"
RATIO = "ratio"
PERCENTAGE = "percentage"
PERCENTAGE_CHANGE = "percentage_change"
SHARE_OF_TOTAL = "share_of_total"
WEIGHTED_AVERAGE = "weighted_average"
MIN = "min"
MAX = "max"
COUNT = "count"
RANK = "rank"
RESULT_ROWS = "result_rows"

#: operation -> (operand count, one-line meaning). The arity is part of the
#: contract: an operation given the wrong number of operands is refused
#: before any arithmetic happens.
OPERATIONS: dict[str, tuple[int, str]] = {
    IDENTITY: (1, "the single referenced cell, unchanged"),
    SUM: (1, "the sum of the referenced cells"),
    MIN: (1, "the smallest of the referenced cells"),
    MAX: (1, "the largest of the referenced cells"),
    COUNT: (1, "how many referenced cells hold a value"),
    DIFFERENCE: (2, "sum(first) - sum(second)"),
    RATIO: (2, "sum(first) / sum(second)"),
    PERCENTAGE: (2, "100 * sum(first) / sum(second)"),
    PERCENTAGE_CHANGE: (2,
                        "100 * (sum(first) - sum(second)) / sum(second), "
                        "where the first operand is the later period"),
    SHARE_OF_TOTAL: (2,
                     "sum(first) / sum(second), where the first operand's "
                     "rows must be a subset of the second's"),
    WEIGHTED_AVERAGE: (2,
                       "sum(value * weight) / sum(weight), over the same "
                       "rows in the same order"),
    RANK: (2, "the 1-based position of the first operand's single cell "
              "within the second operand's cells, largest first"),
    RESULT_ROWS: (1, "how many rows the referenced result PRODUCED, "
                     "published or not; send rows='all' and the column "
                     "that identifies what each row is"),
}

#: WHOSE UNIT IS THE RESULT'S UNIT.
#:
#: H-LIVE-04. A claim's unit may be read back onto the columns it was
#: computed from only where the arithmetic preserves the unit. `sum`,
#: `min`, `max` and `difference` of amounts in SAR million are amounts in
#: SAR million, and `identity` is the cell itself. Everything else changes
#: the unit: `percentage`, `ratio`, `share_of_total` and
#: `percentage_change` produce a proportion out of two amounts, `count`
#: produces a count out of anything, and `rank` produces a position.
#:
#: The live answer to "What is ECL coverage of exposure?" published
#: `balance_total` as 19,619.22% and `limit_total` as 35,932.22%, because
#: their only appearance in any claim was as the DENOMINATOR of a coverage
#: percentage and the renderer read the percentage's unit off the operand.
#: A ratio's denominator is not measured in percent.
UNIT_PRESERVING_OPERATIONS = frozenset({IDENTITY, SUM, MIN, MAX, DIFFERENCE})

#: Operations where the FIRST operand carries the result's unit and the
#: others do not. A weighted average is in the unit of its values; its
#: weights are whatever they weigh by.
FIRST_OPERAND_CARRIES_THE_UNIT = frozenset({WEIGHTED_AVERAGE})


#: Operations whose result is in a unit of its OWN, whatever its operands
#: are measured in. Declared rather than derived by subtraction, so a new
#: operation has to be decided about here and `test_units_across_kinds`
#: fails until it is.
UNIT_CHANGING_OPERATIONS = frozenset({
    RATIO, PERCENTAGE, PERCENTAGE_CHANGE, SHARE_OF_TOTAL, COUNT, RANK,
    RESULT_ROWS})


def operands_in_the_result_unit(derivation: "Derivation") -> tuple[CellSet, ...]:
    """The operands that are measured in the unit of this derivation.

    Empty for every operation that changes the unit, which is the honest
    answer: a column whose unit nothing can name is published as a plain
    number, and that is better than a denomination nobody computed.
    """
    if derivation.operation in UNIT_PRESERVING_OPERATIONS:
        return derivation.operands
    if derivation.operation in FIRST_OPERAND_CARRIES_THE_UNIT:
        return derivation.operands[:1]
    return ()


#: Operations whose result is a proportion of one (`ratio`, `share_of_total`)
#: and operations whose result is already multiplied by a hundred. Units are
#: checked against this, because "percent" and "percentage point" being
#: confused is how a 0.4 becomes a 40.
FRACTION_OPERATIONS = frozenset({RATIO, SHARE_OF_TOTAL})
PERCENT_OPERATIONS = frozenset({PERCENTAGE, PERCENTAGE_CHANGE})

#: Unit strings that mean "a proportion expressed out of one hundred".
PERCENT_UNITS = frozenset({"percent", "%", "pct", "percentage"})
#: Unit strings that mean "a proportion of one".
FRACTION_UNITS = frozenset({"ratio", "fraction", "share", "proportion", "x"})

#: A derived figure is compared against the recomputation at this relative
#: tolerance. It is not a licence to be approximately right: it absorbs the
#: last place of a decimal string the analyst rounded for display, and
#: nothing wider. A dropped row moves these figures by whole millions.
DEFAULT_TOLERANCE = Decimal("1e-9")

#: No derivation may reference more cells than this. A claim that needs more
#: is not a claim, it is a table.
MAX_REFS = 500


class DerivationError(Exception):
    """A derivation that cannot be recomputed, with the reason to send back."""


#: The one value `rows` may take. A word, not a wildcard: `*` and `all` and
#: `:` are all things `row_index_for` would happily try to match against a cell
#: value, and a token that can resolve to data is not a token.
ALL_ROWS = "all"


@dataclass(frozen=True)
class CellSet:
    """Cells in ONE column of ONE artifact.

    Named one of two ways, and never both. `row_ids` accepts the ids the
    result packet publishes (`r0`, `r1`, ...), a plain index, or a
    `column=value` key; all three resolve to the same rows, and the packet
    publishes the first because it is stable even when two rows share a
    label. `all_rows` means every row of the result, expanded HERE against
    the stored artifact.

    WHY THE SHORTHAND IS A SEPARATE FIELD, NOT A TOKEN IN `row_ids`.
    A four-step answer used to spend about 8.3 KB -- two thirds of its whole
    output -- reciting row ids back to the server, because a sum over a
    hundred rows had to name a hundred rows. But `row_index_for`'s last rule
    matches a key against any cell value in any row, so a sentinel inside
    `row_ids` would silently resolve to a data row that happened to hold
    that string. A separate field cannot collide with a value, and it leaves
    `row_ids` meaning exactly one thing: real ids. An invented label like
    "all sectors" is still refused, which is the guarantee a live run paid
    for.
    """

    artifact_id: str
    column_id: str
    row_ids: tuple[str, ...] = ()
    all_rows: bool = False

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"artifact_id": self.artifact_id,
                                "column_id": self.column_id}
        if self.all_rows:
            body["rows"] = ALL_ROWS
        else:
            body["row_ids"] = list(self.row_ids)
        return body


@dataclass(frozen=True)
class Derivation:
    operation: str
    operands: tuple[CellSet, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation,
                "operands": [o.to_dict() for o in self.operands]}

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(o.artifact_id for o in self.operands))


def parse(body: Any) -> Derivation:
    """Read a derivation from a model payload, refusing anything unsupported."""
    if not isinstance(body, dict):
        raise DerivationError("A derivation must be an object naming an "
                              "operation and its operands.")
    operation = str(body.get("operation") or "").strip()
    if operation not in OPERATIONS:
        raise DerivationError(
            f"{operation!r} is not a supported derivation. Supported "
            f"operations are: {', '.join(sorted(OPERATIONS))}.")
    raw = body.get("operands")
    if not isinstance(raw, list) or not raw:
        raise DerivationError(
            f"derivation {operation!r} needs an 'operands' list.")
    arity = OPERATIONS[operation][0]
    if len(raw) != arity:
        raise DerivationError(
            f"derivation {operation!r} takes {arity} operand"
            f"{'s' if arity > 1 else ''} ({OPERATIONS[operation][1]}), and "
            f"{len(raw)} were given.")
    operands = []
    total_refs = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DerivationError(
                f"operand {index} of {operation!r} must be an object naming "
                f"artifact_id, column_id and row_ids.")
        artifact_id = str(item.get("artifact_id") or "").strip()
        column_id = str(item.get("column_id") or "").strip()
        rows = item.get("row_ids")
        scope = item.get("rows")
        if not artifact_id or not column_id:
            raise DerivationError(
                f"operand {index} of {operation!r} must name both an "
                f"artifact_id and a column_id.")
        # EXACTLY ONE OF THE TWO FORMS, the way a claim carries exactly one
        # of `evidence` and `derivation`. The provider's tool dialect has no
        # way to say "one of these two" in a schema, so it is said in the
        # field descriptions and enforced here, where the message can name
        # the operand that broke it.
        wants_all = scope is not None
        names_rows = isinstance(rows, list) and bool(rows)
        if wants_all and names_rows:
            raise DerivationError(
                f"operand {index} of {operation!r} carries both 'rows' and "
                f"'row_ids'. Either it is every row of the result or it is "
                f"the rows you name; send whichever it is, not both.")
        if wants_all:
            if str(scope).strip().lower() != ALL_ROWS:
                raise DerivationError(
                    f"operand {index} of {operation!r} sets rows={scope!r}. "
                    f"The only value 'rows' takes is {ALL_ROWS!r}, meaning "
                    f"every row of that result. For some of the rows, name "
                    f"them in 'row_ids'.")
            # NOT counted against MAX_REFS here: `parse` has no artifact and
            # cannot know how many rows it is admitting. The bound is
            # enforced in `_compute`, once every operand is resolved.
            operands.append(CellSet(artifact_id, column_id, (), True))
            continue
        if not isinstance(rows, list) or not rows:
            raise DerivationError(
                f"operand {index} of {operation!r} names no cells. Send "
                f"rows={ALL_ROWS!r} for every row of that result, or name "
                f"the ones you mean in 'row_ids'.")
        row_ids = tuple(str(r) for r in rows)
        total_refs += len(row_ids)
        if total_refs > MAX_REFS:
            raise DerivationError(
                f"a single derivation may reference at most {MAX_REFS} "
                f"cells; this one references more. Publish it as a table.")
        operands.append(CellSet(artifact_id, column_id, row_ids))
    return Derivation(operation, tuple(operands))


# ---- resolving cells ---------------------------------------------------

_MISSING = object()


def row_id_for(index: int) -> str:
    """The published id of a result row. Positional, stable within a run."""
    return f"r{index}"


def row_index_for(row_id: str, rows: list[dict[str, Any]]) -> int:
    """Resolve a published row id, a bare index, or a `column=value` key.

    PUBLIC because the live-UAT reconciliation has to resolve a claim's
    `row_key` against a stored artifact the same way the finalizer did. A
    second implementation of these four rules in the harness would be a
    second answer to "which row is r3", and the one place that must never
    disagree is the one that decides whether a published figure matches the
    independent oracle.
    """
    key = str(row_id or "").strip()
    if not key:
        return -1
    if key.startswith("r") and key[1:].isdigit():
        index = int(key[1:])
        return index if 0 <= index < len(rows) else -1
    if key.isdigit():
        index = int(key)
        return index if 0 <= index < len(rows) else -1
    if "=" in key:
        name, _, wanted = key.partition("=")
        name, wanted = name.strip(), wanted.strip()
        for i, row in enumerate(rows):
            if str(row.get(name, "")) == wanted:
                return i
        return -1
    for i, row in enumerate(rows):
        if any(str(v) == key for v in row.values()):
            return i
    return -1


#: The private name this had for two rounds, kept so nothing that already
#: imported it breaks.
_index_of = row_index_for


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise DerivationError(
            f"{value!r} is not a number this can compute with.") from exc


@dataclass
class _Resolved:
    #: Empty for a resolution that did not need numbers. `indices` is
    #: always populated, so "how many cells" never depends on this.
    values: list[Decimal]
    indices: list[int]


def _incomplete(record: dict[str, Any], artifact_id: str, *,
                label: str) -> str:
    """Why "every row" may not be said about this result, if it may not.

    A SQL result past the preview cap is clipped BEFORE it is stored, and
    the engine already warns that a clipped table is not a complete
    aggregate. A Python result is stored whole but only its first rows carry
    published ids. So "every row" is honest in exactly one case -- the
    artifact holds every row the step produced, and every one of them was
    addressed -- and that is recorded on the artifact when it is written.

    Missing means no: an artifact stored before this was recorded cannot
    say how much of its result it holds, and a total over an unknown
    fraction is the failure this gate exists to prevent.
    """
    scope = record.get("scope") or {}
    if scope.get("complete") is True:
        return ""
    held = len(record.get("rows") or ())
    produced = scope.get("produced_rows")
    if produced and int(produced) > held:
        return (f"{label} says 'every row' of artifact {artifact_id!r}, but "
                f"that result produced {int(produced):,} rows and only "
                f"{held:,} of them were published. A total over part of a "
                f"result is not that result's total: narrow the query so "
                f"the whole result fits, or name in 'row_ids' the rows you "
                f"actually mean, or -- if what you want is HOW MANY ROWS "
                f"the result had -- use the 'result_rows' operation, which "
                f"reads the {int(produced):,} CreditProbe recorded when it "
                f"ran the query.")
    return (f"{label} says 'every row' of artifact {artifact_id!r}, which "
            f"does not record whether it holds its whole result. Name the "
            f"rows you mean in 'row_ids'.")

def _resolve(cells: CellSet, artifacts: dict[str, dict[str, Any]],
             *, label: str, numeric: bool = True) -> _Resolved:
    """Resolve a cell set against the stored artifact.

    `numeric` is False for `count`, whose documented meaning is "how many
    referenced cells hold a value" and which therefore does not need those
    values to BE numbers. Everything else is unchanged: the row must exist,
    it may not be named twice, a NULL is still refused, and the reference
    bound still applies.

    CLOSURE-01. This used to convert every cell to a Decimal whatever the
    operation was, so counting borrowers over `borrower_name` failed with
    "'Tuwaiq Gulf Energy' is not a number this can compute with". A
    borrower-grain result identifies its borrowers by NAME, so the one
    governed way to count them was refused -- which left an analyst that
    wanted to state a borrower count with nothing supported to state it
    with.
    """
    record = artifacts.get(cells.artifact_id)
    if record is None:
        raise DerivationError(
            f"{label} references artifact {cells.artifact_id!r}, which this "
            f"run did not produce or which is not available to you.")
    if cells.column_id not in record["columns"]:
        raise DerivationError(
            f"{label} names column {cells.column_id!r}, which is not in "
            f"artifact {cells.artifact_id!r}. Its columns are "
            f"{record['columns']}.")
    rows = record["rows"]
    # EXPANDED HERE, WHERE THE ARTIFACT IS. `parse` is artifact-blind, so a
    # shorthand travels as a flag and becomes ids at the last moment -- in
    # STORED ORDER, which `weighted_average` depends on: it pairs values
    # with weights by position and compares the two index lists for
    # equality, so two shorthands must expand identically and a shorthand
    # against an explicit list must expand the way the packet published.
    if cells.all_rows:
        refused = _incomplete(record, cells.artifact_id, label=label)
        if refused:
            raise DerivationError(refused)
        wanted: tuple[str, ...] = tuple(row_id_for(i) for i in range(len(rows)))
    else:
        wanted = cells.row_ids
    seen: set[int] = set()
    values: list[Decimal] = []
    indices: list[int] = []
    for row_id in wanted:
        index = row_index_for(row_id, rows)
        if index < 0:
            raise DerivationError(
                f"{label} names row {row_id!r}, which is not in artifact "
                f"{cells.artifact_id!r}. That artifact has "
                f"{len(rows)} rows, addressed r0 to r{len(rows) - 1}.")
        if index in seen:
            raise DerivationError(
                f"{label} references row {row_id!r} more than once. A cell "
                f"counted twice is not a sum.")
        seen.add(index)
        cell = rows[index].get(cells.column_id, _MISSING)
        if cell is _MISSING:
            raise DerivationError(
                f"{label} names column {cells.column_id!r}, which row "
                f"{row_id!r} does not hold.")
        if cell is None:
            raise DerivationError(
                f"{label} includes row {row_id!r}, whose "
                f"{cells.column_id!r} is NULL. A null is not zero: "
                + ("name the rows you mean in 'row_ids', leaving this one "
                   "out, and say so"
                   if cells.all_rows else
                   "exclude the row and say so")
                + ", or report the figure as unavailable.")
        if numeric:
            values.append(_decimal(cell))
        indices.append(index)
    return _Resolved(values, indices)


# ---- the operations ----------------------------------------------------

def _sum(resolved: _Resolved) -> Decimal:
    return sum(resolved.values, Decimal(0))


def _divide(numerator: Decimal, denominator: Decimal, *, what: str
            ) -> Decimal:
    if denominator == 0:
        raise DerivationError(
            f"{what} divides by zero. A zero base has no meaningful "
            f"percentage: say the base was zero rather than publishing a "
            f"number.")
    return numerator / denominator


def plain(value: Decimal) -> Decimal:
    """The same number, never in exponent notation.

    `Decimal` renders some exact results as "0E+12" or "1E+3", and the
    answer contract requires a plain decimal string -- rightly, because that
    value is quoted to a credit officer. A zero Stage 2 share came back as
    "0E+12" and a correct answer was refused for its formatting, which is
    the engine handing out a value its own contract will not accept. The
    number is unchanged; only its written form is.
    """
    return Decimal(format(value, "f"))


def compute(derivation: Derivation, artifacts: dict[str, dict[str, Any]], *,
            label: str = "this claim") -> Decimal:
    """Recompute a derived value from the stored artifacts. Exact, no floats.

    The result is always in plain form, so `str()` of it is a value the
    answer contract accepts.
    """
    return plain(_compute(derivation, artifacts, label=label))


def _result_rows(cells: CellSet, artifacts: dict[str, dict[str, Any]], *,
                 label: str) -> Decimal:
    """HOW MANY ROWS THE RESULT HAD, from the server's own record of it.

    A SQL result past the preview cap is clipped before it is stored, and
    `_incomplete` rightly refuses a total over the part that was kept. But
    the SIZE of the whole result is a fact CreditProbe measured when it ran
    the query and wrote onto the artifact, and until this existed there was
    no way for an answer to say it.

    The live UAT found that the hard way. Asked for average utilisation and
    facility count "for each borrower", the analyst produced a correct
    5,412-row result of which 100 were published, wanted to say how many
    borrowers that was, and had nowhere to get the number: `rows='all'` is
    refused because the artifact holds a prefix, and counting the published
    rows answers 100. Both permitted attempts went on discovering that, and
    the run fell back to publishing rows with no explanation.

    The figure is the SERVER'S. The analyst names the artifact and the
    column that says what a row is -- which is what keeps the entity check
    meaningful -- and CreditProbe supplies the count.
    """
    record = artifacts.get(cells.artifact_id)
    if record is None:
        raise DerivationError(
            f"{label} references artifact {cells.artifact_id!r}, which this "
            f"run did not produce or which is not available to you.")
    if cells.column_id not in record["columns"]:
        raise DerivationError(
            f"{label} names column {cells.column_id!r}, which is not in "
            f"artifact {cells.artifact_id!r}. Its columns are "
            f"{record['columns']}.")
    if not cells.all_rows:
        raise DerivationError(
            f"{label} uses 'result_rows', which counts the WHOLE result and "
            f"not a selection of it. Send rows='all'.")
    scope = record.get("scope") or {}
    produced = scope.get("produced_rows")
    if produced is None:
        if scope.get("complete") is True:
            return Decimal(len(record.get("rows") or ()))
        raise DerivationError(
            f"{label} asks how many rows artifact {cells.artifact_id!r} "
            f"produced, and that artifact does not record it.")
    return Decimal(int(produced))


def _compute(derivation: Derivation, artifacts: dict[str, dict[str, Any]], *,
             label: str) -> Decimal:
    operation = derivation.operation
    if operation == RESULT_ROWS:
        return _result_rows(derivation.operands[0], artifacts, label=label)
    resolved = [
        _resolve(cells, artifacts,
                 label=(f"{label} operand {i + 1}" if len(derivation.operands)
                        > 1 else label),
                 numeric=operation != COUNT)
        for i, cells in enumerate(derivation.operands)]

    # THE BOUND, WHERE THE COUNT IS FINALLY KNOWN. `parse` checks it too,
    # so a nine-hundred-id list is refused before the database is touched --
    # but `parse` cannot count a shorthand, and the shorthand is the form
    # that can expand without the analyst seeing how far. Both checks say
    # the same thing; this is the one that cannot be walked around.
    # `indices`, not `values`: a `count` resolves no numbers and a bound
    # read off an empty list is a bound that does not apply.
    referenced = sum(len(r.indices) for r in resolved)
    if referenced > MAX_REFS:
        raise DerivationError(
            f"{label} references {referenced:,} cells and a single "
            f"derivation may reference at most {MAX_REFS}. Publish it as a "
            f"table.")

    if operation == IDENTITY:
        if len(resolved[0].values) != 1:
            raise DerivationError(
                f"{label} uses 'identity', which names exactly one cell; "
                f"{len(resolved[0].values)} were given. Use 'sum' for more "
                f"than one.")
        return resolved[0].values[0]
    if operation == SUM:
        return _sum(resolved[0])
    if operation == MIN:
        return min(resolved[0].values)
    if operation == MAX:
        return max(resolved[0].values)
    if operation == COUNT:
        return Decimal(len(resolved[0].indices))
    if operation == DIFFERENCE:
        return _sum(resolved[0]) - _sum(resolved[1])
    if operation == RATIO:
        return _divide(_sum(resolved[0]), _sum(resolved[1]), what=label)
    if operation == PERCENTAGE:
        return Decimal(100) * _divide(_sum(resolved[0]), _sum(resolved[1]),
                                      what=label)
    if operation == PERCENTAGE_CHANGE:
        base = _sum(resolved[1])
        return Decimal(100) * _divide(_sum(resolved[0]) - base, base,
                                      what=label)
    if operation == SHARE_OF_TOTAL:
        part, whole = derivation.operands[0], derivation.operands[1]
        if part.artifact_id != whole.artifact_id:
            raise DerivationError(
                f"{label} takes a share of a total across two different "
                f"artifacts. A share must be a part of its own whole.")
        if not set(resolved[0].indices) <= set(resolved[1].indices):
            stray = sorted(set(resolved[0].indices) - set(resolved[1].indices))
            raise DerivationError(
                f"{label} is a share of a total whose numerator includes "
                f"rows the denominator does not: "
                f"{[row_id_for(i) for i in stray]}. The part must be inside "
                f"the whole.")
        return _divide(_sum(resolved[0]), _sum(resolved[1]), what=label)
    if operation == WEIGHTED_AVERAGE:
        values, weights = resolved[0], resolved[1]
        if values.indices != weights.indices:
            raise DerivationError(
                f"{label} is a weighted average whose values and weights "
                f"are not the same rows in the same order. Name the same "
                f"row_ids for both operands.")
        weighted = sum((v * w for v, w in zip(values.values, weights.values)),
                       Decimal(0))
        return _divide(weighted, _sum(weights), what=label)
    if operation == RANK:
        if len(resolved[0].values) != 1:
            raise DerivationError(
                f"{label} ranks one cell within a population; its first "
                f"operand names {len(resolved[0].values)} cells.")
        target = resolved[0].values[0]
        if not set(resolved[0].indices) <= set(resolved[1].indices):
            raise DerivationError(
                f"{label} ranks a row that is not in the population it is "
                f"ranked against.")
        return Decimal(sum(1 for v in resolved[1].values if v > target) + 1)
    raise DerivationError(f"{operation!r} is not a supported derivation.")


# ---- unit agreement ----------------------------------------------------

def unit_problem(derivation: Derivation, unit: str) -> str:
    """Catch the percent / percentage-point / fraction confusions."""
    lowered = str(unit or "").strip().lower()
    if derivation.operation in PERCENT_OPERATIONS:
        if lowered in FRACTION_UNITS:
            return (f"a {derivation.operation!r} derivation already "
                    f"multiplies by a hundred, so its unit cannot be "
                    f"{unit!r}. Use 'percent', or use 'ratio' as the "
                    f"operation.")
        if lowered in {"percentage point", "percentage points", "pp", "bps",
                       "basis points"}:
            return (f"a {derivation.operation!r} derivation produces a "
                    f"percentage, not a {unit}. A difference between two "
                    f"percentages is a percentage point; this is not that.")
    if derivation.operation in FRACTION_OPERATIONS and lowered in PERCENT_UNITS:
        return (f"a {derivation.operation!r} derivation produces a "
                f"proportion of one, so its unit cannot be {unit!r}. Use "
                f"'percentage' as the operation, or state the unit as a "
                f"ratio.")
    if derivation.operation in (COUNT, RESULT_ROWS) \
            and (lowered in PERCENT_UNITS or lowered in FRACTION_UNITS):
        return f"a count is not measured in {unit!r}."
    return ""


def describe() -> list[dict[str, Any]]:
    """The operation table, for the guide sent with every result packet."""
    return [{"operation": name, "operands": arity, "meaning": meaning}
            for name, (arity, meaning) in sorted(OPERATIONS.items())]


__all__ = [
    "COUNT", "CellSet", "DEFAULT_TOLERANCE", "DIFFERENCE", "Derivation",
    "DerivationError", "FRACTION_OPERATIONS", "IDENTITY", "MAX", "MAX_REFS",
    "MIN", "OPERATIONS", "PERCENTAGE", "PERCENTAGE_CHANGE",
    "PERCENT_OPERATIONS", "RANK", "RATIO", "SHARE_OF_TOTAL", "SUM",
    "WEIGHTED_AVERAGE", "compute", "describe", "parse", "plain",
    "row_id_for",
    "operands_in_the_result_unit",
    "row_index_for",
    "unit_problem"]
