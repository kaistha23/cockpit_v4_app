"""An ordered distribution has a shape, and a single figure does not.

UNIT · SCRIPTED. No model call, no paid provider call.

CLOSURE-04
----------
The first live UAT's L04 asked "What is exposure by delinquency bucket?".
The answer returned a correct table and no chart, and the matrix expects
one.

Read against the guidance the answer turn was given, that is not
surprising. It asks "does this result have a shape?" and answers with a
list: a ranking, a movement, a concentration, a migration, a spread. An
ordered distribution is none of them. A delinquency mix is not a ranking --
the order is the bucket's, not the measure's -- it is not a movement, and
it is not a concentration unless one bucket happens to dominate. So the
honest reading of the instruction was that this result had no shape.

The guidance now names that kind of shape, generally: a measure spread
across bands that have a natural ORDER, where the weight ALONG the order is
the finding. Delinquency buckets are one example of it and are not
mentioned as a rule. The restraint rule is untouched, and these tests
assert it is: one figure still has no shape and still gets no chart.

These prove the instruction and the surface. What live Opus draws needs the
paid retest.
"""

from __future__ import annotations

import json

from backend.cockpit_v4 import context as ctx


def _presentation() -> str:
    blocks = ctx.finalization_system(
        [{"type": "text", "text": "INSTRUCTION"},
         {"type": "text", "text": json.dumps({"pinned_scope": {}})}],
        domain_id="corporate", question="What is exposure by bucket?")
    return " ".join(str(b.get("text") or "") for b in blocks)


def test_the_answer_turn_is_told_an_ordered_distribution_has_a_shape():
    text = _presentation()
    assert "an_ordered_distribution_has_a_shape" in text
    assert "natural ORDER" in text


def test_the_guidance_names_a_class_of_shape_not_a_question():
    """Not an L04 rule. It lists several ordered band types and none of
    them is the question that was asked."""
    body = ctx.PRESENTATION["an_ordered_distribution_has_a_shape"]
    for band in ("delinquency buckets", "rating grades", "IFRS 9 stages",
                 "tenor bands", "vintages", "score bands"):
        assert band in body, band
    assert "exposure by delinquency bucket" not in body.lower()


def test_it_says_which_form_an_ordered_distribution_takes():
    """The order is the axis, so it is not a ranking by size -- which is
    the mistake the existing bar-chart rule warns about."""
    body = ctx.PRESENTATION["an_ordered_distribution_has_a_shape"]
    assert "not as a ranking by size" in body
    assert "sequence" in body and "composition" in body


def test_the_restraint_rule_is_untouched():
    """L12 is a single coverage ratio. One figure has no shape, and the
    sentence that says so must still be there, word for word."""
    body = ctx.PRESENTATION["a_chart_is_not_always_the_answer"]
    assert "One figure has none: state it and send no chart." in body
    assert "Two or three values a reader compares at a glance" in body


def test_the_two_rules_do_not_contradict_each_other():
    """One says draw an ordered spread; the other says do not draw a small
    table. The word that reconciles them is SEVERAL."""
    ordered = ctx.PRESENTATION["an_ordered_distribution_has_a_shape"]
    restraint = ctx.PRESENTATION["a_chart_is_not_always_the_answer"]
    assert "Several such bands" in ordered
    assert "Two or three values" in restraint


def test_the_guidance_is_not_carried_on_an_action_turn():
    """It costs the turn that chooses what to run nothing at all."""
    instruction = ctx.PROMPT_PATH.read_text(encoding="utf-8")
    assert "an_ordered_distribution_has_a_shape" not in instruction
    assert "delinquency buckets" not in instruction


def test_every_form_it_names_is_a_form_the_product_draws():
    from backend.cockpit_v4.finalization import (COMPOSITION_KINDS,
                                                 SEQUENCE_KINDS)

    assert SEQUENCE_KINDS and COMPOSITION_KINDS, (
        "the guidance points at sequence and composition forms; they must "
        "exist")
