"""REAL DATABASE · NO MODEL · UNIT.

Every sentence a reader sees is on the calendar of the book it describes.

The Corporate book reports QUARTERS and the Retail book reports MONTHS, and
the dashboard already knew that: the attention heading read
`Reporting quarter Q2 2026`. What did not know it was the prose AROUND the
numbers -- the drawer's review steps, the driver statements, the ECL card's
measure label, and the openers a seeded thread offers -- all of which named
one calendar for both books.

These tests do not check that the word is "quarter". They check that it is
the word the BOOK uses, which is what keeps them true the day a book's
frequency changes.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import schema as schema_mod

#: The other book's period word, for a given book.
FOREIGN = {"quarter": "month", "month": "quarter"}


@pytest.fixture(scope="module")
def feeds():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        scope = resolver.scope_for(domain_id)
        session = cat.open_session(catalog=cat.build(domain_id=domain_id))
        out[domain_id] = (att.compute(session=session, scope=scope), scope)
    return out


def _prose(item: dict) -> list[str]:
    """Only what a READER is shown. Relation names are not prose.

    `corp_facility_quarter` is a table, not a sentence: a test that swept
    the whole payload for the word would flag the schema and would have to
    be weakened until it caught nothing.
    """
    out = [str(item.get("headline") or ""),
           str(item.get("what_changed") or ""),
           str(item.get("why_it_appeared") or ""),
           str(item.get("one_line") or ""),
           str(item.get("why") or ""),
           str(item.get("metric_label") or "")]
    out += [str(line) for line in (item.get("what_to_review_next") or [])]
    out += [str(d.get("statement") or "")
            for d in (item.get("possible_drivers") or [])]
    out += [str(n.get("label") or "") for n in (item.get("key_numbers") or [])]
    drill = item.get("drilldown") or {}
    out.append(str(drill.get("note") or ""))
    out += [str(q.get("question") or "")
            for q in (drill.get("suggested_questions") or [])]
    return [text for text in out if text]


# ---- the dashboard -----------------------------------------------------

def test_no_card_on_either_dashboard_names_the_other_calendar(feeds):
    """The live defect, on the server side of it.

    A corporate drawer told the reader to check "what the same segment's
    coverage did over the same month", about a book with no months in it.
    """
    for domain_id, (feed, scope) in feeds.items():
        foreign = FOREIGN[scope.period_noun]
        cards = (feed["segments_requiring_attention"]
                 + feed["ecl_highlights"])
        assert cards, f"{domain_id} produced no cards to check"
        for card in cards:
            for text in _prose(card):
                assert foreign not in text.lower(), (
                    f"{domain_id} ({scope.period_noun}ly) says "
                    f"{foreign!r}: {text!r}")


def test_every_review_step_is_written_in_the_book_s_own_period(feeds):
    """`what_to_review_next` is the drawer's list, rendered verbatim."""
    for domain_id, (feed, scope) in feeds.items():
        steps = [line
                 for card in feed["segments_requiring_attention"]
                 for line in card["what_to_review_next"]]
        assert steps, f"{domain_id} offered no review steps"
        assert not any("{period}" in line for line in steps), (
            "a template reached the reader unfilled")
        spoken = [line for line in steps if "period" in line.lower()
                  or "month" in line.lower() or "quarter" in line.lower()]
        for line in spoken:
            assert scope.period_noun in line.lower(), line


def test_the_ecl_card_measures_the_book_s_own_period(feeds):
    """"ECL added this month" was on the corporate dashboard."""
    for domain_id, (feed, scope) in feeds.items():
        added = [str(card.get("metric_label") or "")
                 for card in feed["ecl_highlights"]
                 if card.get("metric") == "ecl_increase"]
        assert added, f"{domain_id} has no ECL-added card"
        for measure in added:
            assert measure == f"ECL added this {scope.period_noun}"


def test_the_two_dashboards_do_not_share_a_period_word(feeds):
    """Corporate quarters, Retail months -- established from the release."""
    assert feeds[dom.CORPORATE][1].period_noun == "quarter"
    assert feeds[dom.RETAIL][1].period_noun == "month"


# ---- the review steps, without a database ------------------------------

def test_review_steps_fill_from_the_noun_they_are_given():
    quarterly = att._review_next("stage2_share", "quarter")
    monthly = att._review_next("stage2_share", "month")
    assert any("same quarter" in line for line in quarterly)
    assert any("same month" in line for line in monthly)
    assert not any("month" in line for line in quarterly)
    assert not any("quarter" in line for line in monthly)


def test_an_unknown_measure_still_gets_the_book_s_period():
    default = att._review_next("no_such_measure", "quarter")
    assert default, "a card with an unfamiliar measure got no review steps"
    assert not any("month" in line for line in default)
    assert not any("{period}" in line for line in default)


def test_no_review_template_states_a_calendar_of_its_own():
    """The guarantee behind the tests above."""
    for steps in list(att.REVIEW_NEXT.values()) + [att.DEFAULT_REVIEW]:
        for line in steps:
            assert "month" not in line and "quarter" not in line, line


# ---- the openers a thread offers ---------------------------------------

def test_generic_openers_are_asked_in_the_book_s_own_period():
    """§31. A chip is the question the reader actually sends.

    "How has ECL coverage moved over the last twelve months?" on a book
    that publishes quarters is a window three years shorter than the words
    on the chip.
    """
    for domain_id in dom.DOMAIN_IDS:
        noun = schema_mod.period_noun(domain_id)
        questions = [q["question"]
                     for q in routes._opening_questions(None, [], domain_id)]
        assert questions, f"{domain_id} offered no openers"
        for question in questions:
            assert "{" not in question, f"unfilled template: {question}"
            assert FOREIGN[noun] not in question.lower(), question
        assert any(noun in q.lower() for q in questions), (
            f"{domain_id} openers never name its own period")


def test_a_seeded_thread_opens_on_the_card_s_own_period():
    """The seed carries the period; the openers must not re-derive it."""
    seeded = {"body": {"reporting_period": "2026Q2", "drilldown": {}}}
    questions = [q["question"]
                 for q in routes._opening_questions(seeded, [], "corporate")]
    assert any("2026Q2" in q for q in questions)
    assert not any("month" in q.lower() for q in questions)


def test_no_opener_template_states_a_calendar_of_its_own():
    for templates in routes._GENERIC_OPENERS.values():
        for template in templates:
            assert "month" not in template and "quarter" not in template, (
                template)
