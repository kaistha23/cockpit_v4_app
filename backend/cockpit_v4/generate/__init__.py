"""V4-native synthetic book generators. One module per analytical domain."""

from __future__ import annotations

MONTHS = 20
LAST_MONTH = "2026-08"


def month_range(last: str = LAST_MONTH, count: int = MONTHS) -> tuple[str, ...]:
    """The `count` completed months ending at `last`, oldest first.

    Completed, deliberately. A part-month at the end of a book reads as a
    collapse in every month-on-month comparison drawn against it, and no
    amount of footnoting stops a reader seeing the cliff.
    """
    year, month = (int(p) for p in last.split("-"))
    out: list[str] = []
    for back in range(count - 1, -1, -1):
        total = year * 12 + (month - 1) - back
        out.append(f"{total // 12:04d}-{total % 12 + 1:02d}")
    return tuple(out)


def months_between(earlier: str, later: str) -> int:
    ey, em = (int(p) for p in earlier.split("-"))
    ly, lm = (int(p) for p in later.split("-"))
    return (ly * 12 + lm) - (ey * 12 + em)


#: The CORPORATE calendar: twenty completed quarters ending at the quarter
#: the demonstration treats as current.
QUARTERS = 20
LAST_QUARTER = "2026Q2"


def quarter_range(last: str = LAST_QUARTER,
                  count: int = QUARTERS) -> tuple[str, ...]:
    """The `count` completed quarters ending at `last`, oldest first."""
    year, quarter = int(last[:4]), int(last[-1])
    out: list[str] = []
    for back in range(count - 1, -1, -1):
        total = year * 4 + (quarter - 1) - back
        out.append(f"{total // 4:04d}Q{total % 4 + 1}")
    return tuple(out)


def quarters_between(earlier: str, later: str) -> int:
    ey, eq = int(earlier[:4]), int(earlier[-1])
    ly, lq = int(later[:4]), int(later[-1])
    return (ly * 4 + lq) - (ey * 4 + eq)


def quarter_of(month: str) -> str:
    """The quarter a YYYY-MM month falls in."""
    year, mon = (int(p) for p in month.split("-"))
    return f"{year:04d}Q{(mon - 1) // 3 + 1}"


__all__ = ["LAST_MONTH", "LAST_QUARTER", "MONTHS", "QUARTERS",
           "month_range", "months_between", "quarter_of", "quarter_range",
           "quarters_between"]
