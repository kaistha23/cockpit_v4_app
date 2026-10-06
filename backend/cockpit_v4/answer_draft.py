"""The numbers of a PLANNED result, claimed by the server before the answer.

Why this exists
---------------
A compiled plan leaves nothing to guess about its result: CreditProbe wrote
the SQL, so it knows which column is a dimension, which is a measure, and the
unit of each. Asking the model to rebuild that knowledge as `numeric_claims`
-- artifact id, row key, column id, unit, for every figure -- was where local
models lost the most answers: a missing `row_key`, a column id from another
step, a share typed by hand that no cell held.

So for a planned step the server drafts the claims itself, one per cell plus
a total and a share where that arithmetic means something, each with a
readable id (`ecl_sar_mn.stage_2`). The model writes the narrative around
`{{claim.<id>}}` placeholders and may omit `numeric_claims` entirely; the
finalizer attaches the drafted claim for every placeholder the narrative
uses.

What this does NOT change
-------------------------
A drafted claim is an ordinary `NumericClaim`. It is checked by the same
`Finalizer.validate` as one the model wrote -- the cell is re-read, a total
or share is recomputed from the stored artifact -- and a claim the narrative
never uses is never attached. The narrative's words stay the model's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import display
from backend.cockpit_v4.contracts import EvidenceRef, NumericClaim

#: Rows drafted per step. A planned result is ordered by its measure, so
#: these are the rows an answer talks about; a figure further down can still
#: be cited by hand.
MAX_ROWS = 12
#: Claims offered per step. The kit is re-read by the answer turn, and a
#: local model's context is the scarce thing.
MAX_CLAIMS = 60
_MAX_ID = 64
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Drafted:
    claim: NumericClaim
    #: What the figure is, in words: "Total ecl, stage 2".
    means: str
    #: The figure as CreditProbe will publish it, so the model can reason
    #: about which is largest without typing any of them.
    shown: str
    column: str = ""
    role: str = ""
    row: str = ""

    @property
    def placeholder(self) -> str:
        return "{{claim.%s}}" % self.claim.claim_id

    def to_dict(self) -> dict[str, Any]:
        return {"placeholder": self.placeholder, "means": self.means,
                "value": self.shown}


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _slug(text: Any, limit: int = 24) -> str:
    return _SLUG.sub("_", str(text).lower()).strip("_")[:limit].strip("_")


def _row_key(row: dict[str, Any], dims: list[Any]) -> str:
    """`stage_2`, `real_estate`, `2026q2`, or `real_estate.stage_2`."""
    parts = []
    for d in dims:
        value = row.get(d.name)
        piece = _slug(value)
        if not piece:
            return ""
        # A bare number names nothing: "2" is a stage, a flag or a count.
        if piece.isdigit() and d.role != "period":
            piece = f"{_slug(d.name, 16)}_{piece}"
        parts.append(piece)
    return ".".join(parts)


def _row_words(row: dict[str, Any], dims: list[Any]) -> str:
    return ", ".join(
        f"{d.name.replace('_', ' ')} {row.get(d.name)}"
        if str(row.get(d.name)).isdigit() else str(row.get(d.name))
        for d in dims)


def _unit(unit: str, money_unit: str) -> str:
    if unit == "average":
        return ""
    if money_unit and display.classify(unit) == display.MONETARY_AMOUNT:
        return money_unit
    return unit


def _shown(value: Decimal, unit: str) -> str:
    return display.format_value(value, unit)


class _Ids:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, base: str, fallback: str) -> str:
        candidate = base if base and len(base) <= _MAX_ID else fallback[:_MAX_ID]
        claim_id, n = candidate, 2
        while claim_id in self.used:
            suffix = f"_{n}"
            claim_id, n = candidate[:_MAX_ID - len(suffix)] + suffix, n + 1
        self.used.add(claim_id)
        return claim_id


def draft(result: Any, shape: Any, *, money_unit: str = "") -> list[Drafted]:
    """Claims for one successful planned step. Empty when nothing is safe.

    `result` is the step's `StepResult`; `shape` is its
    `plan_compiler.StepShape`. Only cells that exist and hold a number are
    drafted, so every drafted claim is one the finalizer will accept.
    """
    if getattr(result, "status", "") != "ok" or not result.artifact_id or not result.preview:
        return []
    columns = [c for c in shape.columns if c.name]
    dims = [c for c in columns if c.role in ("dimension", "period")]
    measures = [c for c in columns if c.role not in ("dimension", "period")
                and c.name in result.columns
                # A movement with no unit is a movement of a fraction, and
                # would print as a percentage of itself.
                and not (c.role == "change" and not c.unit)]
    rows = list(zip(result.row_ids, result.preview))
    shown_rows = rows[:MAX_ROWS]
    keys = [_row_key(row, dims) for _, row in shown_rows]
    if dims and (len(set(keys)) != len(keys) or "" in keys):
        keys = [row_id for row_id, _ in shown_rows]
    ids = _Ids()
    out: list[Drafted] = []

    for col in measures:
        unit = _unit(col.unit, money_unit)
        for (row_id, row), key in zip(shown_rows, keys):
            value = _number(row.get(col.name))
            if value is None:
                continue
            claim_id = ids.take(f"{col.name}.{key}" if dims else col.name,
                                f"{col.name[:50]}.{row_id}")
            out.append(Drafted(
                NumericClaim(claim_id=claim_id, decimal_value="", unit=unit,
                             evidence=EvidenceRef(result.artifact_id, row_id, col.name)),
                means=col.label + (f", {_row_words(row, dims)}" if dims else ""),
                shown=_shown(value, unit), column=col.name, role=col.role,
                row=_row_words(row, dims)))
        out += _total_and_shares(result, col, unit, dims, rows, keys, shape, ids)
    return out[:MAX_CLAIMS]


def _total_and_shares(result: Any, col: Any, unit: str, dims: list[Any],
                      rows: list[tuple[str, dict[str, Any]]], keys: list[str],
                      shape: Any, ids: _Ids) -> list[Drafted]:
    """A total and each row's share of it -- only where both mean something.

    Money only: a sum of distinct borrower counts across stages counts a
    borrower once per stage. Never over periods: last quarter's exposure
    plus this quarter's is not an exposure. Never over a LIMIT: the top five
    sectors' sum is not the book's. And only over a complete result, because
    the finalizer refuses a total over a clipped one.
    """
    if (col.role != "value" or not dims or any(d.role == "period" for d in dims)
            or shape.limited or len(rows) < 2
            or display.classify(unit) != display.MONETARY_AMOUNT
            or result.truncated or len(rows) != result.row_count):
        return []
    values = [_number(row.get(col.name)) for _, row in rows]
    if any(v is None or v < 0 for v in values):
        return []
    total = sum(values, Decimal(0))
    every = {"artifact_id": result.artifact_id, "column_id": col.name, "rows": deriv.ALL_ROWS}
    label = col.label.removeprefix("Total ")
    label = label[:1].lower() + label[1:]
    out = [Drafted(
        NumericClaim(claim_id=ids.take(f"{col.name}.total", f"{col.name[:50]}.total"),
                     decimal_value="", unit=unit, evidence=EvidenceRef("", "", ""),
                     derivation={"operation": deriv.SUM, "operands": [dict(every)]}),
        means=f"Sum of {label} over all {len(rows)} rows of this result",
        shown=_shown(total, unit), column=col.name, role="total")]
    if total <= 0:
        return out
    for (row_id, row), key in list(zip(rows, keys))[:MAX_ROWS]:
        share = _number(row.get(col.name)) / total * 100
        out.append(Drafted(
            NumericClaim(claim_id=ids.take(f"{col.name}.{key}.share", f"{col.name[:44]}.{row_id}.share"),
                         decimal_value="", unit="percent", evidence=EvidenceRef("", "", ""),
                         derivation={"operation": deriv.PERCENTAGE, "operands": [
                             {"artifact_id": result.artifact_id, "column_id": col.name,
                              "row_ids": [row_id]}, dict(every)]}),
            means=f"{_row_words(row, dims)}'s share of the total {label}",
            shown=_shown(share, "percent"), column=col.name, role="share",
            row=_row_words(row, dims)))
    return out


def headline(drafted: list[Drafted], purpose: str) -> str:
    """One sentence an answer can open with, built only from placeholders."""
    if not drafted:
        return ""
    lead = (purpose[:1].upper() + purpose[1:]).replace("_", " ") if purpose else drafted[0].means
    if not drafted[0].row:
        # No dimensions: one row, and the first figure of each kind is the
        # first measure's.
        of: dict[str, Drafted] = {}
        for d in drafted:
            of.setdefault(d.role, d)
        if "prior" in of and "change" in of:
            return (f"{lead}: {of['value'].placeholder}, against {of['prior'].placeholder} "
                    f"in the earlier period, a change of {of['change'].placeholder}.")
        return f"{lead}: {of['value'].placeholder}." if "value" in of else ""
    first = drafted[0].column
    rows = [d for d in drafted if d.column == first and d.role == "value"][:5]
    if not rows:
        return ""
    change = {d.row: d for d in drafted if d.column == f"{first}_change"}
    sentence = f"{lead}: " + "; ".join(
        f"{d.row} {d.placeholder}"
        + (f" (change {change[d.row].placeholder})" if d.row in change else "")
        for d in rows) + "."
    total = next((d for d in drafted if d.column == first and d.role == "total"), None)
    return sentence + (f" Total {total.placeholder}." if total else "")


KIT_RULES = (
    "CreditProbe has already bound every figure below to this result. Write "
    "the narrative with these placeholders, copied exactly, wherever a figure "
    "belongs -- for example \"Stage 2 holds {{claim.<id>}}\". You do not need "
    "to send numeric_claims for them: each placeholder the narrative uses is "
    "attached and checked for you. Do not type any figure yourself; a number "
    "not listed here can still be claimed the usual way. 'headline' is a "
    "ready first sentence you may use or adapt.")


def kit(drafted: list[Drafted], purpose: str) -> dict[str, Any]:
    return {"rules": KIT_RULES, "headline": headline(drafted, purpose),
            "figures": [d.to_dict() for d in drafted]}


__all__ = ["Drafted", "KIT_RULES", "MAX_CLAIMS", "MAX_ROWS", "draft",
           "headline", "kit"]
