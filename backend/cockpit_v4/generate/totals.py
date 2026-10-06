"""Adding numbers up, named by which kind of number they are.

Why this module exists
----------------------
`v4-saudi-retail-20m-v4` was not reproducible. The same commit, the same
seed and the same lake produced two different books:

    Python 3.10 / 3.11   content digest 65524d5dd04d2d2e
    Python 3.12 / 3.13   content digest 9d444d6969e5e860

One line did it:

    score = sum(r["behaviour_score"] for r in rows) / len(rows)

CPython 3.12 changed the builtin `sum` to use Neumaier compensated
summation for floats (gh-100425); 3.10 and 3.11 accumulate naively. The two
return different doubles. Ordinarily that is invisible -- the difference is
a few parts in 10^16 -- but the summands here are themselves `round(_, 2)`
values, so the mean lands EXACTLY on a half-cent tie over and over, and
`round(score, 2)` then falls one way or the other on the last bit of the
sum. Three columns of one relation moved. Row counts, entity counts and
field counts did not, so every manifest still looked right.

The fix is not "use fsum everywhere". `math.fsum` returns a float, and two
of the sums in these generators feed `_stable(text, modulus)` as its
INTEGER modulus; handing that a float silently reassigns every cohort and
every product type in the Corporate book. Adding up money and counting
things are different operations and the difference matters, so they have
different names here and the generators say which one they mean.

`tests/cockpit_v4/test_generator_determinism.py` fails the build if a
generator calls the builtin `sum` again.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def exact_total(values: Iterable[float]) -> float:
    """Add up floats, identically on every interpreter.

    `math.fsum` is correctly rounded by an exact algorithm -- it keeps the
    partial sums rather than estimating a correction -- so its answer is
    the nearest double to the true total on every conforming build and
    every version. That is the property the builtin lacks and the only
    reason to prefer it here; it is not about being more accurate, it is
    about being the SAME.
    """
    return math.fsum(values)


def exact_mean(values: Iterable[float]) -> float:
    """The arithmetic mean of floats, identically on every interpreter.

    Named separately from `exact_total` because the hazard belongs to the
    mean. A total of money is rounded to a scale far coarser than the noise
    and absorbs it; a mean of values that were themselves rounded to two
    places lands on a rounding tie constantly, and then an invisible
    difference decides a visible digit. That is what happened here, and a
    reader who sees `exact_mean` is being told which of the two they are
    looking at.

    Raises on an empty input rather than returning 0.0: a mean of nothing
    is not zero, and a book that quietly records it as zero is a book with
    a wrong number in it.
    """
    items = list(values)
    if not items:
        raise ValueError("exact_mean of no values: a mean of nothing is not "
                         "zero, and the caller has to decide what it is.")
    return math.fsum(items) / len(items)


def whole_total(values: Iterable[int]) -> int:
    """Add up integers, and stay an integer.

    The builtin is right for this and always has been: `sum` over ints is
    exact arithmetic with no float path and no version history. This
    function exists so that a generator can say "these are counts" in code
    rather than in a comment, and so the test that forbids the bare builtin
    has something to allow.

    It refuses floats on purpose. `sum(COHORT_WEIGHTS)` and `sum(weights)`
    are the integer MODULUS of `_stable`, and a float modulus would reassign
    every cohort and product in the Corporate book without failing anything.
    Better to raise here than to publish that.
    """
    items = list(values)
    wrong = [v for v in items if not isinstance(v, int) or isinstance(v, bool)]
    if wrong:
        raise TypeError(
            f"whole_total is for counts and integer weights; got "
            f"{wrong[:3]!r}. Floats belong in exact_total, whose answer "
            f"does not depend on the interpreter.")
    return sum(items)


__all__ = ["exact_mean", "exact_total", "whole_total"]
