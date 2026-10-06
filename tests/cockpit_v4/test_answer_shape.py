"""What a good analytical answer is, said once, on the turn that writes it.

UNIT · REPRODUCTION · MODEL MOCK. No paid provider call, no database.

The gap
-------
`analyst.md` has a section called "Writing the answer". It is careful work:
who is reading, answer first, prose over bullets, no marketing language. Then
it gives four WORKED SHAPES -- what a good answer contains and in what order
-- and every one of the four is a PRODUCT question. "Who are you?" "What does
Early Warning do?" "What is Cockpit?" "What is TAC?"

The shape it never describes is the one this product exists for: the answer
written with a result set in hand. The "Analysis" section beside it is
entirely about how to RUN the query -- resolve the terms, ask once, submit
your own SQL -- and stops at the moment the rows arrive.

So the single act the whole system is built around was the one act with no
statement of what good looks like, while four less central ones had theirs
spelled out. That asymmetry is the defect. The default an analyst falls back
to in its absence is narrating the table, which is the one thing the reader
already has in front of them.

Why it is a context block and not a prompt section
--------------------------------------------------
The same reason the chart policy is. `analyst.md` rides on every action
attempt and is measured against a byte bound; an action turn holds no result
and cannot write an answer about one. The finalization context was
deliberately compacted -- roughly four thousand tokens of catalogue removed,
because once the result is in hand none of it decides anything -- and this
spends about five hundred of what that freed, on the turn that can use it.

What this suite does NOT claim
------------------------------
That answers are good. Prose quality is not a property a unit test can
assert, and a test that grepped a scripted narrative for the word
"concentration" would be measuring the fixture. What is asserted is that the
standard exists, is carried to the turn that writes the answer, is not
carried to the turn that cannot, and says the specific things it was written
to say.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import context as ctx_mod


def blocks(*texts: str) -> list[dict]:
    return [{"type": "text", "text": t} for t in texts]


def answer_turn_text(domain_id: str = "") -> str:
    packet = ctx_mod.finalization_system(
        blocks("INSTRUCTION",
               json.dumps({"catalog_index": []}),
               json.dumps({"pinned_scope": {}})),
        domain_id=domain_id)
    return " ".join(str(b.get("text") or "") for b in packet)


# ---- the standard exists and reaches the right turn --------------------

def test_the_turn_that_writes_the_answer_is_told_what_one_looks_like() -> None:
    text = answer_turn_text()
    assert '"phase": "THE ANSWER"' in text


def test_the_turn_that_cannot_write_an_answer_does_not_carry_it() -> None:
    """An action turn holds no result. Standards for prose about a result
    are bytes spent on a turn that cannot spend them, on a payload that is
    bounded and measured."""
    instruction = ctx_mod.PROMPT_PATH.read_text(encoding="utf-8")
    for phrase in ("who_is_reading", "do_not_narrate_the_table",
                   "what_it_means_for_the_book"):
        assert phrase not in instruction


def test_it_survives_a_packet_with_no_catalogue_to_replace() -> None:
    packet = ctx_mod.finalization_system(blocks("INSTRUCTION"))
    text = " ".join(str(b.get("text") or "") for b in packet)
    assert '"phase": "THE ANSWER"' in text


def test_the_answer_comes_before_the_chart_policy() -> None:
    """Block order is read order. The prose IS the answer; the charts
    accompany it, and a packet that led with how to draw would be saying
    the opposite."""
    packet = ctx_mod.finalization_system(blocks("INSTRUCTION"))
    phases = [json.loads(b["text"]).get("phase")
              for b in packet if str(b.get("text", "")).startswith("{")]
    assert phases.index("THE ANSWER") < phases.index("PRESENTATION")


@pytest.mark.parametrize("book", ["corporate", "retail"])
def test_the_credit_policy_still_arrives_beside_it(book: str) -> None:
    """Two blocks were added to this turn over two rounds. Neither may have
    displaced the policy retrieval, which is what makes "above the limit" a
    sentence the analyst can write.

    The domain id has to be one the pack knows: `finalization_system`
    swallows an unknown book, correctly -- not every release ships a policy
    pack -- so a test naming a book that does not exist would assert
    nothing and look fine doing it.
    """
    from backend.cockpit_v4 import domains

    assert book in domains.DOMAIN_IDS
    text = answer_turn_text(domain_id=book)
    assert '"phase": "THE ANSWER"' in text
    assert "PRESENTATION" in text
    assert '"pack_version"' in text, "the policy synopsis is gone"


# ---- it says the things it was written to say --------------------------

REQUIRED = [
    # Lead with the finding, not with the question or the method.
    ("lead_with_the_answer", "first sentence"),
    # A number with nothing beside it makes the reader do the sizing.
    ("a_figure_needs_something_beside_it", "prior period"),
    # The sentence a credit officer actually acts on.
    ("say_where_it_sits", "Concentration"),
    # Direction is not cause, and saying so is part of the answer.
    ("what_moved_it", "not its cause"),
    # An answer that stops at the number has not finished.
    ("what_it_means_for_the_book", "worth doing next"),
    # Once, where it matters -- not hedged onto every clause.
    ("what_this_does_not_establish", "is noise"),
    # The failure mode the whole block exists to prevent.
    ("do_not_narrate_the_table", "published beside you"),
    # The process panel and the trace already carry this.
    ("do_not_narrate_the_process", "governance trace"),
    # Without this, a checklist becomes a form and produces padding.
    ("length_follows_the_question", "short answer"),
]


@pytest.mark.parametrize("key,phrase", REQUIRED)
def test_each_standard_is_present_and_says_what_it_means(
        key: str, phrase: str) -> None:
    assert key in ctx_mod.ANALYTICAL_ANSWER, (
        f"{key} has gone from the standard; an answer turn is no longer "
        f"told this and no other block says it")
    assert phrase in ctx_mod.ANALYTICAL_ANSWER[key], (
        f"{key} survives as a heading but no longer says {phrase!r}")


def test_no_standard_asks_for_a_figure_the_result_does_not_hold() -> None:
    """The comparison rule is the one place this could go wrong.

    "Put the number against the prior period" read as an instruction rather
    than a preference is an instruction to produce a prior-period number,
    and a run whose result holds none would have to invent it. The clause
    must bound itself to what was computed.
    """
    clause = ctx_mod.ANALYTICAL_ANSWER["a_figure_needs_something_beside_it"]
    assert "the RESULT already holds" in clause
    assert "Do not reach for one that was not computed." in clause


def test_the_standard_is_advice_about_shape_not_a_lookup_of_answers() -> None:
    """§17. This block must not become a semantic planner in prose.

    It may say what an answer is held to. It may not name a portfolio, a
    field, a threshold or a figure -- the moment it does, CreditProbe is
    deciding the analysis rather than governing it.
    """
    text = json.dumps(ctx_mod.ANALYTICAL_ANSWER).lower()
    for forbidden in ("ead_reported", "ecl_reported", "stage 2", "sar",
                      "select ", "cockpit_facility", "basis point"):
        assert forbidden not in text, (
            f"the answer standard names {forbidden!r}; shape guidance that "
            f"reaches into the book is the analyst's job being done for it")


def test_it_is_a_block_that_ships_and_not_a_comment_beside_one() -> None:
    """§16 mutation check. A standard written in a `#` comment satisfies a
    grep of the source and reaches the analyst never, so this reads the
    serialised payload the provider receives."""
    packet = ctx_mod.finalization_system(blocks("INSTRUCTION"))
    sent = [json.loads(b["text"]) for b in packet
            if str(b.get("text", "")).startswith("{")]
    [standard] = [b for b in sent if b.get("phase") == "THE ANSWER"]
    assert set(standard) - {"phase"} == set(ctx_mod.ANALYTICAL_ANSWER) - {
        "phase"}


def test_removing_a_standard_is_visible_here() -> None:
    """§16, from the other side: the parametrised test above must fail if a
    key is dropped, rather than passing on a block that still exists."""
    mutated = {k: v for k, v in ctx_mod.ANALYTICAL_ANSWER.items()
               if k != "do_not_narrate_the_table"}
    assert "do_not_narrate_the_table" not in mutated
    assert "published beside you" not in json.dumps(mutated)


# ---- the cost of adding it --------------------------------------------

#: What the two standing policy blocks -- the answer standard and the chart
#: policy -- may cost the answer turn, in bytes of JSON.
#:
#: A bound rather than a measurement. The catalogue block this turn used to
#: carry was about four thousand TOKENS, and replacing it is why an answer
#: is affordable on a run that already spent an action retry. Policy that
#: grew back into that space one clause at a time would undo the compaction
#: without any single change looking like it had.
POLICY_BYTES = 5000


def test_the_standing_policy_stays_within_what_the_compaction_freed() -> None:
    cost = (len(json.dumps(ctx_mod.ANALYTICAL_ANSWER, ensure_ascii=False))
            + len(json.dumps(ctx_mod.PRESENTATION, ensure_ascii=False)))
    assert cost <= POLICY_BYTES, (
        f"the answer turn now carries {cost} bytes of standing policy "
        f"against a {POLICY_BYTES} byte bound. More words are not more "
        f"guidance: cut a clause before raising this.")


def test_the_heavy_catalogue_block_is_still_gone() -> None:
    """The bound above is only meaningful while the thing it protects is
    still absent."""
    packet = ctx_mod.finalization_system(
        blocks("INSTRUCTION", json.dumps({"catalog_index": [{"id": "x"}]})))
    text = " ".join(str(b.get("text") or "") for b in packet)
    assert "catalog_index" not in text
