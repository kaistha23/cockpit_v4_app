"""
Twenty reporting slots, and the macro window's second time axis.
Specification section 3.1.

Two time axes, not one
----------------------
The twenty facility/borrower reporting snapshots are historical or current
observations of the book. They are the ONLY reporting periods Cockpit may
reach.

For EACH reporting snapshot t, the macro block has its own relative window of
twenty positions: t-4, t-3, t-2, t-1, t, t+1 ... t+15. That is a second axis,
not fifteen additional observed facility quarters. Every macro row therefore
carries both `reporting_quarter` (the anchor it was known at) and
`macro_target_quarter` (the period it describes), and the union of macro
targets across twenty anchors extends well past the reporting calendar without
extending the reporting calendar.

For the demo release ending 2026Q2 the reporting snapshots run 2021Q3 through
2026Q2. At the 2026Q2 anchor the macro window runs 2025Q2 through 2030Q1.
Those are demo dates, not a hard-coded production calendar: `Calendar.ending`
builds twenty slots ending wherever the release says.

What "twenty quarters" does not mean
------------------------------------
Twenty calendar slots created is not twenty quarters observed. A release must
say which slots are actually populated, and a facility originating part-way
through the window need not have twenty rows -- that is coverage, not
missingness. Both distinctions are carried in `Calendar`, not left to prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

#: Exactly twenty ordered, consecutive quarterly snapshot slots per release.
SLOTS = 20

#: The macro window, relative to each anchor. Twenty positions, four back and
#: fifteen forward, inclusive at both ends.
MACRO_MIN_OFFSET = -4
MACRO_MAX_OFFSET = 15
MACRO_OFFSETS: tuple[int, ...] = tuple(
    range(MACRO_MIN_OFFSET, MACRO_MAX_OFFSET + 1))
assert len(MACRO_OFFSETS) == 20

QUARTER_SEMANTICS = (
    "Calendar quarters. 2026Q2 means the three months ending 30 June 2026, and "
    "the snapshot is as at that date. Every published reporting quarter is a "
    "COMPLETED period; no partial quarter is published as an actual.")

MACRO_SEMANTICS = (
    "A macro row is identified by BOTH its anchor reporting_quarter and its "
    "macro_target_quarter. quarter_offset is target minus anchor, from -4 "
    "through +15. A positive offset is a forecast made at the anchor, never an "
    "observed future quarter, and it does not add a reporting period.")

_QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
_MONTH_END_DAY = {3: 31, 6: 30, 9: 30, 12: 31}
_LABEL = re.compile(r"^\s*(?:(\d{4})\s*[Qq]\s*([1-4])|[Qq]\s*([1-4])\s*[- ]?\s*(\d{4}))\s*$")


@dataclass(frozen=True, order=True)
class Quarter:
    """One calendar quarter. Ordered, so slots sort without special cases."""

    index: int                      # quarters since 2000Q1

    @staticmethod
    def of(year: int, quarter: int) -> "Quarter":
        if quarter not in (1, 2, 3, 4):
            raise ValueError(f"{quarter!r} is not a quarter of the year")
        return Quarter((int(year) - 2000) * 4 + (int(quarter) - 1))

    @property
    def year(self) -> int:
        return 2000 + self.index // 4

    @property
    def quarter(self) -> int:
        return self.index % 4 + 1

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"

    @property
    def end_date(self) -> date:
        month = _QUARTER_END_MONTH[self.quarter]
        return date(self.year, month, _MONTH_END_DAY[month])

    @property
    def start_date(self) -> date:
        return date(self.year, _QUARTER_END_MONTH[self.quarter] - 2, 1)

    @property
    def display(self) -> str:
        return f"Q{self.quarter} {self.year}"

    def shift(self, by: int) -> "Quarter":
        return Quarter(self.index + int(by))

    def __str__(self) -> str:       # pragma: no cover - convenience
        return self.label


def parse(label: str) -> Quarter:
    """Read `2026Q2`, `2026-Q2`, `Q2 2026` or `q2-2026`. Anything else raises."""
    m = _LABEL.match(str(label or "").replace("-Q", "Q").replace("-q", "q"))
    if not m:
        raise ValueError(f"{label!r} is not a quarter label")
    if m.group(1):
        return Quarter.of(int(m.group(1)), int(m.group(2)))
    return Quarter.of(int(m.group(4)), int(m.group(3)))


def label_of(q: Quarter | str) -> str:
    return q.label if isinstance(q, Quarter) else parse(q).label


def offset_between(anchor: str, target: str) -> int:
    """target - anchor, in quarters. The macro axis's coordinate."""
    return parse(target).index - parse(anchor).index


# ----------------------------------------------------------------- calendar

@dataclass(frozen=True)
class Calendar:
    """One release's reporting calendar: exactly twenty ordered slots.

    `populated` is what actually has rows. A release that created twenty slots
    and filled eight of them says so; section 3.1 forbids claiming twenty
    observed quarters merely because twenty slots exist.
    """

    dataset_release_id: str
    slots: tuple[str, ...]
    populated: tuple[str, ...] = ()
    data_cutoff: dict[str, str] = None      # type: ignore[assignment]

    def __post_init__(self) -> None:
        if len(self.slots) != SLOTS:
            raise ValueError(
                f"a Cockpit release has exactly {SLOTS} reporting slots; this "
                f"one has {len(self.slots)}")
        indices = [parse(s).index for s in self.slots]
        if indices != sorted(indices):
            raise ValueError("reporting slots must be in ascending order")
        if any(b - a != 1 for a, b in zip(indices, indices[1:])):
            raise ValueError("reporting slots must be consecutive quarters")
        object.__setattr__(self, "slots", tuple(label_of(s) for s in self.slots))
        pop = tuple(label_of(p) for p in (self.populated or ()))
        unknown = [p for p in pop if p not in self.slots]
        if unknown:
            raise ValueError(
                f"{unknown} are not reporting slots of this release")
        object.__setattr__(self, "populated", pop or self.slots)
        object.__setattr__(self, "data_cutoff", dict(self.data_cutoff or {}))

    # -- construction --------------------------------------------------

    @staticmethod
    def ending(last: str, *, dataset_release_id: str,
               populated: tuple[str, ...] | None = None) -> "Calendar":
        """Twenty consecutive slots ending at `last`. The release's calendar,
        never the wall clock."""
        end = parse(last)
        slots = tuple(end.shift(i - (SLOTS - 1)).label for i in range(SLOTS))
        return Calendar(dataset_release_id=dataset_release_id, slots=slots,
                        populated=populated or slots)

    # -- reporting axis ------------------------------------------------

    @property
    def first(self) -> str:
        return self.slots[0]

    @property
    def last(self) -> str:
        return self.slots[-1]

    @property
    def missing(self) -> tuple[str, ...]:
        """Slots with no rows. Named explicitly, never quietly omitted."""
        return tuple(s for s in self.slots if s not in self.populated)

    @property
    def fully_populated(self) -> bool:
        return not self.missing

    def __contains__(self, quarter: Any) -> bool:
        try:
            return label_of(quarter) in self.slots
        except ValueError:
            return False

    def __iter__(self) -> Iterator[str]:
        return iter(self.slots)

    def __len__(self) -> int:
        return len(self.slots)

    def position(self, quarter: str) -> int:
        """0-based slot position. Raises for anything outside the twenty."""
        label = label_of(quarter)
        try:
            return self.slots.index(label)
        except ValueError:
            raise OutsideCalendar(
                f"{label} is not one of this release's twenty reporting "
                f"quarters ({self.first} through {self.last}). Older snapshots "
                f"are not reachable from the Cockpit.") from None

    def require(self, quarter: str) -> str:
        self.position(quarter)
        return label_of(quarter)

    def previous(self, quarter: str) -> str | None:
        """The slot before, or None at the start. Never the earliest slot as a
        silent substitute."""
        i = self.position(quarter)
        return self.slots[i - 1] if i > 0 else None

    def latest_populated(self) -> str:
        for s in reversed(self.slots):
            if s in self.populated:
                return s
        raise OutsideCalendar(
            f"release {self.dataset_release_id!r} has no populated reporting "
            f"quarter")

    def end_date(self, quarter: str) -> date:
        return parse(self.require(quarter)).end_date

    # -- macro axis ----------------------------------------------------

    def macro_window(self, anchor: str) -> tuple[tuple[int, str], ...]:
        """The twenty (offset, target) pairs for one anchor.

        Targets may lie outside the reporting calendar in both directions. That
        is correct and is the whole point of the second axis.
        """
        base = parse(self.require(anchor))
        return tuple((off, base.shift(off).label) for off in MACRO_OFFSETS)

    def macro_targets(self) -> tuple[str, ...]:
        """Every distinct macro target across all twenty anchors, ordered.

        Its length is 20 + 19 = 39 for a twenty-slot calendar, and none of the
        extra periods is a reporting quarter.
        """
        seen: set[int] = set()
        for anchor in self.slots:
            for _off, target in self.macro_window(anchor):
                seen.add(parse(target).index)
        return tuple(Quarter(i).label for i in sorted(seen))

    def is_forecast(self, *, anchor: str, target: str) -> bool:
        """A macro value with a positive offset is a forecast made at the
        anchor. Section 3.1: label forward values as forecasts, never as actual
        future data."""
        return offset_between(anchor, target) > 0

    # -- reporting ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_release_id": self.dataset_release_id,
            "reporting_slots": list(self.slots),
            "slot_count": len(self.slots),
            "first_reporting_quarter": self.first,
            "last_reporting_quarter": self.last,
            "populated_quarters": list(self.populated),
            "missing_quarters": list(self.missing),
            "fully_populated": self.fully_populated,
            "quarter_end_dates": {s: parse(s).end_date.isoformat()
                                  for s in self.slots},
            "data_cutoff_at": dict(self.data_cutoff),
            "quarter_semantics": QUARTER_SEMANTICS,
            "macro_semantics": MACRO_SEMANTICS,
            "macro_offsets": list(MACRO_OFFSETS),
            "macro_target_quarters": list(self.macro_targets()),
            "note": ("Twenty reporting slots exist. Only the populated ones "
                     "carry observations; the missing ones are named above "
                     "rather than implied."),
        }

    def compact(self) -> dict[str, Any]:
        """The serialization that goes into the context packet."""
        return {
            "release": self.dataset_release_id,
            "reporting_quarters": list(self.slots),
            "populated": list(self.populated),
            "missing": list(self.missing),
            "macro_offsets": [MACRO_MIN_OFFSET, MACRO_MAX_OFFSET],
            "macro_target_range": [self.macro_targets()[0],
                                   self.macro_targets()[-1]],
            "semantics": QUARTER_SEMANTICS,
            "macro_semantics": MACRO_SEMANTICS,
        }


class OutsideCalendar(LookupError):
    """A period outside the release's twenty slots. Refused, not substituted."""


# ------------------------------------------------- statement availability

#: How long after a period end an audited annual statement is typically
#: available, and an unaudited interim one. Used to decide what was KNOWABLE at
#: a snapshot's cutoff -- section 3.2's point-in-time control.
ANNUAL_AVAILABILITY_LAG_DAYS = 120
INTERIM_AVAILABILITY_LAG_DAYS = 55


def available_by(period_end: date, *, audited: bool) -> date:
    from datetime import timedelta

    lag = (ANNUAL_AVAILABILITY_LAG_DAYS if audited
           else INTERIM_AVAILABILITY_LAG_DAYS)
    return period_end + timedelta(days=lag)


def knowable(available_at: date, as_at: date) -> bool:
    """Was this observation available by the snapshot's cutoff? A later actual
    must not overwrite an earlier forecast as though it was known then."""
    return available_at <= as_at


__all__ = ["ANNUAL_AVAILABILITY_LAG_DAYS", "Calendar",
           "INTERIM_AVAILABILITY_LAG_DAYS", "MACRO_MAX_OFFSET",
           "MACRO_MIN_OFFSET", "MACRO_OFFSETS", "MACRO_SEMANTICS",
           "OutsideCalendar", "QUARTER_SEMANTICS", "Quarter", "SLOTS",
           "available_by", "knowable", "label_of", "offset_between", "parse"]
