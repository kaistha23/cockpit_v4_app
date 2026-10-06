"""A count is an integer, however its unit is spelled.

UNIT. No model call, no paid provider call.

CHECK COUNT FORMATTING
----------------------
The first-pass evidence contains

    21,918.00 facility records
     1,949.00 facility records
       170.00 facility records

Tested at da974348 BEFORE changing anything, as instructed: it was NOT
already fixed by the H-LIVE-04 unit work, which governs operations that
CHANGE a unit and says nothing about how one is spelled. `classify` was an
exact-string lookup, `"facility records"` is not a key, and UNKNOWN carries
two decimal places.

A unit no table spells is now read from its WORDS: every word that names a
class must name the same class, and then the phrase is that class. The
vocabulary gained only the CARDINALITY nouns -- record, entry, observation,
item -- and not the entity singulars, because `borrower_id` is an
identifier and not a tally of anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import display as disp


@pytest.mark.parametrize("unit", [
    "facility records", "borrower records", "facility count",
    "loan accounts", "account records", "observations", "records",
    "data items", "customer accounts", "days past due",
])
def test_a_counting_phrase_is_a_count(unit):
    assert disp.classify(unit) == disp.COUNT
    assert disp.decimals(unit) == 0


@pytest.mark.parametrize("value", ["21918", "1949", "170", "21918.4"])
def test_the_live_figures_render_as_integers(value):
    shown = disp.format_value(Decimal(value), "facility records")
    assert ".00" not in shown
    assert shown.endswith(" facility records")
    assert "," in shown or len(value.split(".")[0]) <= 3


def test_the_exact_live_string_is_gone():
    assert disp.format_value(Decimal("21918"), "facility records") == \
        "21,918 facility records"


@pytest.mark.parametrize("unit,expected", [
    ("SAR million", disp.MONETARY_AMOUNT),
    ("percent", disp.PERCENTAGE),
    ("percentage points", disp.PERCENTAGE_POINT),
    ("average utilisation percent", disp.PERCENTAGE),
    # WORDS THAT DISAGREE stay UNKNOWN. Guessing between a share and a
    # headcount is how a percentage becomes a tally.
    ("percentage of borrowers", disp.UNKNOWN),
    ("ratio of accounts", disp.UNKNOWN),
    # And a phrase whose words nothing names is still nothing. A phrase
    # where exactly ONE word is known takes that word's class, which is
    # the whole point: "gizmo index" is an index.
    ("widgets", disp.UNKNOWN),
    ("gizmo index", disp.INTEGER),
    ("gizmo per frobnicator", disp.UNKNOWN),
])
def test_it_reads_the_phrase_without_guessing(unit, expected):
    assert disp.classify(unit) == expected


def test_an_identifier_is_not_a_count():
    """The entity singulars deliberately did NOT join the count vocabulary.

    `borrower_id` names a borrower and tallies nothing, and reading it as a
    count would give a text identifier a numeric display class.
    """
    assert disp.classify("borrower_id") == disp.UNKNOWN
    assert disp.classify("facility_id") == disp.UNKNOWN
    assert disp.entity_of("borrower_id") == "borrower"


def test_a_single_word_is_still_an_exact_lookup():
    """Phrase reading applies to PHRASES. One unknown word stays unknown
    rather than being matched against anything."""
    assert disp.classify("facilitys") == disp.UNKNOWN
    assert disp.classify("recordings") == disp.UNKNOWN


def test_substrings_are_not_matched():
    """`accounts` must not be found inside `accounting`."""
    assert disp.classify("accounting basis") == disp.UNKNOWN
    assert disp.classify("percentile band") == disp.UNKNOWN
