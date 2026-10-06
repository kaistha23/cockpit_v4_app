"""
Every unit this catalogue uses has a stated verdict.

NO MODEL · UNIT. No paid provider call.

`Field.additive` named four units on each side and returned "not_additive"
for everything else, so a unit it had never heard of was indistinguishable
from one deliberately judged non-additive. Three of the eleven units the
catalogue actually uses reached their answer that way, and one of them was
wrong: `notches` is a signed count of rating grades, `display` classes it
with `count`, published prose already totals it -- "a net movement of +7
notches" -- and the catalogue said it could not be added.

`schema.py`'s own docstring is where this test comes from: *"A field whose
unit is spelled inventively is a field whose figures get published wrong."*
A fallthrough is how that stays true silently. The pair of explicit sets is
the fix, and this file is what stops the next unit slipping past them.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4 import sql as v4_sql
from backend.cockpit_v4.schema import (ADDITIVE_UNITS, NOT_ADDITIVE_UNITS,
                                       Field)


def catalogue_units() -> dict[str, list[str]]:
    """Every unit string on every field of every relation, both books."""
    found: dict[str, list[str]] = {}
    for relations in schema_mod.RELATIONS.values():
        for relation in relations:
            for field in relation.fields:
                found.setdefault(field.unit, []).append(
                    f"{relation.name}.{field.name}")
    return found


def test_no_unit_in_this_catalogue_reaches_its_verdict_by_fallthrough():
    """THE point of the file.

    A unit in neither set still answers "not_additive", because refusing to
    add an unknown quantity is the safe half of the mistake. What it cannot
    do is stay unknown: this is the test that makes somebody decide.
    """
    stated = ADDITIVE_UNITS | NOT_ADDITIVE_UNITS
    unstated = {unit: where for unit, where in catalogue_units().items()
                if unit not in stated}
    assert not unstated, (
        f"units with no stated verdict: "
        f"{ {u: w[:2] for u, w in unstated.items()} }")


def test_no_unit_is_claimed_to_be_both():
    assert not (ADDITIVE_UNITS & NOT_ADDITIVE_UNITS)


def test_rating_notches_may_be_added_up():
    """The one verdict that was wrong.

    A notch is one step on the nineteen-grade internal scale. Two of them
    are in the corporate book -- movement this quarter, and movement since
    origination -- and both are signed integers that a report sums across a
    portfolio to say which way the book moved.
    """
    assert "notches" in ADDITIVE_UNITS
    moved = Field(name="rating_notches_moved", dtype="integer",
                  unit="notches", description="Notches moved this quarter.")
    assert moved.additive == "additive"
    assert moved.to_dict()["aggregation"] == "additive"


def test_a_notch_is_classified_the_same_way_twice():
    """`display` and `schema` had disagreed about it.

    `display.CATALOG_UNITS` puts `notches` with `count`, `days` and
    `months`; `Field.additive` put it with percentages and ratios. Two
    modules describing the same column in incompatible terms is how a
    reader gets a total nobody meant to offer.
    """
    for unit in ("count", "days", "months", "notches"):
        assert disp.CATALOG_UNITS[unit] == disp.COUNT, unit
        assert unit in ADDITIVE_UNITS, unit


def test_the_double_count_check_can_actually_see_a_notch():
    """Two gates, and one without the other changes nothing.

    `additive_measures` requires BOTH `Field.additive == "additive"` AND
    membership of `sql._ADDITIVE_UNITS`, so classifying notches correctly
    in the schema while leaving the join check blind to them would have
    been a fix that fixed nothing.
    """
    assert "notches" in v4_sql._ADDITIVE_UNITS
    # A catalogue with no published shape, which falls back to the domain's
    # current schema -- the right reading here, because the question is
    # about how `schema.py` classifies a unit, not about any release.
    catalog = cat.Catalog(
        domain_id="corporate", dataset_release_id="unit-test",
        release_fingerprint="0" * 64,
        calendar=cat.Calendar(slots=("2026Q2",), frequency="quarterly"))
    measures = v4_sql.additive_measures(catalog, "corp_borrower_quarter")
    assert "rating_notches_moved" in measures, measures


@pytest.mark.parametrize("unit", sorted(NOT_ADDITIVE_UNITS))
def test_nothing_that_may_not_be_summed_became_summable(unit):
    """Including `''`, which is every string and categorical column: a
    sector name is not a quantity. A tenor in `years` is averaged across
    facilities, never totalled."""
    assert Field(name="f", dtype="d", unit=unit,
                 description="").additive == "not_additive"


def test_an_author_may_still_overrule_the_unit():
    """`aggregation` is the escape hatch and it stays open. A column whose
    unit says additive but whose meaning does not can say so."""
    field = Field(name="f", dtype="d", unit="rcy", description="",
                  aggregation="not_additive")
    assert field.additive == "not_additive"
