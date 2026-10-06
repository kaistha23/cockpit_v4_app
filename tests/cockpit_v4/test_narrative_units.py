"""UNIT · REPRODUCTION. No model call.

The unit is written twice, or not at all.

The defect this exists for
--------------------------
A published Retail answer read, verbatim:

    The 90+ book is SAR SAR 78 million million across 446 accounts
    accounts, and it is entirely Stage 3.
    ... an overall coverage of 48.31%%.
    90+ EAD is up 10.23%% on July and 82.38%% on 2025-08.

Nothing was computed wrongly. `format_value` writes the unit INTO the
string it returns -- `SAR 78 million`, `48.31%`, `446 accounts` -- and the
analyst, writing prose, wrote the unit as well. Two correct components,
one unreadable sentence.

It survived because nothing looked for it. `_BARE_NUMBER` strips the
placeholders and then hunts for digits, so `SAR {{claim.x}} million` is
clean by that test; the prompt forbade typing NUMBERS and said nothing
about units; and every fixture in the suite used a bare, unit-free
placeholder, so no test could ever have seen it. The frontend already knew
the hazard and guarded the claims LIST against it
(`claim-display.ts`) -- the narrative had no equivalent.

What is pinned here:

  the live strings, repaired      -> the four forms that reached a reader
  prose that was right, untouched -> no repair where none is due
  a mention is not a duplicate    -> "millions of riyals" survives
  the repair is recorded          -> warned, not hidden
  a mismatched unit is left alone -> that is a different fault
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4.finalization import substitute

#: (narrative, claim values, claim units, expected published prose)
LIVE_FORMS = [
    pytest.param(
        "The 90+ book is SAR {{claim.ead}} million across {{claim.n}} "
        "accounts, and it is entirely Stage 3.",
        {"ead": "SAR 78 million", "n": "446 accounts"},
        {"ead": "SAR million", "n": "accounts"},
        "The 90+ book is SAR 78 million across 446 accounts, and it is "
        "entirely Stage 3.",
        id="money-prefix-scale-and-count"),
    pytest.param(
        "an overall coverage of {{claim.cov}}%.",
        {"cov": "48.31%"}, {"cov": "percent"},
        "an overall coverage of 48.31%.",
        id="percent"),
    pytest.param(
        "90+ EAD is up {{claim.g}}% on July and {{claim.h}}% on 2025-08.",
        {"g": "10.23%", "h": "82.38%"},
        {"g": "percent", "h": "percent"},
        "90+ EAD is up 10.23% on July and 82.38% on 2025-08.",
        id="two-percents-one-sentence"),
    pytest.param(
        "ECL against it is SAR {{claim.ecl}} million.",
        {"ecl": "SAR 38 million"}, {"ecl": "SAR million"},
        "ECL against it is SAR 38 million.",
        id="money-again"),
]


@pytest.mark.parametrize("narrative,values,units,expected", LIVE_FORMS)
def test_the_unit_reaches_the_reader_once(narrative, values, units, expected):
    """The four forms that were actually published, repaired."""
    rendered, notes = substitute(narrative, values, units)
    assert rendered == expected
    assert notes, "a repair happened and was not recorded"


@pytest.mark.parametrize("narrative,values,units,expected", LIVE_FORMS)
def test_no_doubled_token_survives(narrative, values, units, expected):
    """The reader-visible statement, independent of the exact wording."""
    rendered, _ = substitute(narrative, values, units)
    for doubled in ("SAR SAR", "%%", "million million", "accounts accounts",
                    "USD USD", "billion billion"):
        assert doubled not in rendered, doubled


def test_prose_that_was_already_right_is_not_touched():
    """False repairs are the expensive failure.

    A narrative that uses the placeholder correctly must come through
    byte-identical, and must not report a repair it did not make.
    """
    narrative = ("Total EAD is {{claim.ead}} across {{claim.n}}, and "
                 "coverage is {{claim.cov}}.")
    values = {"ead": "SAR 78 million", "n": "446 accounts",
              "cov": "48.31%"}
    units = {"ead": "SAR million", "n": "accounts", "cov": "percent"}
    rendered, notes = substitute(narrative, values, units)
    assert rendered == ("Total EAD is SAR 78 million across 446 accounts, "
                        "and coverage is 48.31%.")
    assert notes == []


def test_a_unit_mentioned_elsewhere_in_the_sentence_survives():
    """Only the token TOUCHING the placeholder is a duplicate.

    "balances are in millions of riyals" is the analyst telling the reader
    what the book is denominated in, which is useful and is not a second
    copy of anything.
    """
    narrative = ("Millions of riyals sit in this bucket; the book is SAR "
                 "{{claim.ead}} million, a million-riyal story.")
    rendered, _ = substitute(narrative, {"ead": "SAR 78 million"},
                             {"ead": "SAR million"})
    assert rendered == ("Millions of riyals sit in this bucket; the book is "
                        "SAR 78 million, a million-riyal story.")


def test_a_short_scale_the_release_spells_is_still_a_duplicate():
    """`format_value` writes "billion"; the release says "bn".

    The analyst reads the release and writes what it says. Matching only
    the expanded word would leave `USD 3 billion bn` on the screen.
    """
    rendered, notes = substitute("USD {{claim.x}} bn of exposure.",
                                 {"x": "USD 3 billion"}, {"x": "USD bn"})
    assert rendered == "USD 3 billion of exposure."
    assert len(notes) == 2


def test_a_unit_that_does_not_match_the_claim_is_left_visible():
    """Not every adjacent word is a duplicate.

    `SAR {{claim.p}} million` whose claim is a PERCENTAGE is a claim
    carrying the wrong unit. Eating the `SAR` would hide that; it is a
    different fault and it is not this function's to paper over.
    """
    rendered, notes = substitute("SAR {{claim.p}} million of it.",
                                 {"p": "48.31%"}, {"p": "percent"})
    assert rendered == "SAR 48.31% million of it."
    assert notes == []


def test_an_unresolved_placeholder_is_left_alone_with_its_prose():
    """A claim that never rendered keeps its placeholder AND its units.

    Stripping around a placeholder that is still a placeholder would
    corrupt the text the re-ask is about to be shown.
    """
    rendered, notes = substitute("SAR {{claim.missing}} million.",
                                 {}, {"missing": "SAR million"})
    assert rendered == "SAR {{claim.missing}} million."
    assert notes == []


# ---- the affixes are derived, not listed --------------------------------

@pytest.mark.parametrize("unit,prefix,suffix", [
    ("SAR million", "sar", "million"),
    ("USD bn", "usd", "billion"),
    ("SAR", "sar", ""),
    ("percent", "", "%"),
    ("probability", "", "%"),
    ("accounts", "", "accounts"),
    ("percentage points", "", "pp"),
    ("ratio", "", "x"),
    ("count", "", ""),
])
def test_the_affixes_are_what_format_value_actually_writes(unit, prefix,
                                                           suffix):
    """The guard reads `format_value`; it does not restate it.

    A second hand-maintained table of units would drift from the first one
    the day somebody changed a rendering. This asserts the derivation
    agrees with the function it derives from, for every unit class.
    """
    prefixes, suffixes = disp.written_affixes(unit)
    assert (prefixes[0] if prefixes else "") == prefix
    assert (suffixes[0] if suffixes else "") == suffix

    rendered = disp.format_value(Decimal(0), unit)
    for token in prefixes:
        assert rendered.lower().startswith(token)
    if suffix:
        assert rendered.lower().rstrip(".").endswith(suffix)
