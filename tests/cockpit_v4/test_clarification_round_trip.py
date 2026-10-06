"""The half of a clarification that comes back.

UNIT · REPRODUCTION · REAL DATABASE. No model call.

The defect
----------
A turn can end by putting one question back to the reader instead of
guessing -- `disposition: "clarification"`, the question in
`clarification_question`, the choices in `clarification_options`, rendered
as buttons. That half works, and `test_clarification_turn.py` holds it.

The reader clicks one. What the NEXT turn receives as its question is the
option's text:

    Symmetric allocation

Three words, arriving alone. The analyst's history block carried, per turn,
the question asked, the narrative, and the disposition. Not the
clarification question. So what the analyst saw was that it had asked
SOMETHING, and a short phrase it had to work backwards from, with the thing
that would have made it obvious sitting in the stored answer unread.

The round trip completed only when the analyst had happened to repeat its
own question inside the narrative as well as putting it in the field built
for it, and nothing requires that. A clarification whose answer cannot be
matched to its question is worse than not having asked: the reader has
spent a turn and the run is no better informed.

The fix is a projection, not a mechanism. `recent_turns` already returns the
whole stored answer; the history block simply stopped short of the two keys
that matter here.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import lake

THE_QUESTION = "Sequential or symmetric attribution?"
THE_OPTIONS = ["Sequential (PD, then LGD, then EAD)",
               "Symmetric allocation"]


def a_clarification_turn(**over) -> dict:
    """One stored turn that ended by asking."""
    answer = {
        "disposition": "clarification",
        "narrative": "I need one decision before I can rank these.",
        "clarification_question": THE_QUESTION,
        "clarification_options": list(THE_OPTIONS),
    }
    answer.update(over.pop("answer", {}))
    turn = {"turn_id": "t-1", "ordinal": 1,
            "question": "compare PD, LGD & CCF and tell me what dominates",
            "answer": answer}
    turn.update(over)
    return turn


@pytest.fixture(autouse=True)
def _clean():
    arun.reset()
    yield
    arun.reset()


def packet_for(question: str, turns: list[dict]):
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    return ctx_mod.build(
        question=question, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), recent_turns=turns,
        session=book.session, analytical=True)


def history_of(packet) -> list[dict]:
    return packet.payload["recent_turns"]


# ---- the round trip ----------------------------------------------------

def test_the_next_turn_is_told_what_question_it_asked() -> None:
    packet = packet_for("Symmetric allocation", [a_clarification_turn()])
    [turn] = history_of(packet)
    assert turn["you_asked"] == THE_QUESTION


def test_it_is_also_told_what_it_offered() -> None:
    """The reply is usually one of the choices word for word. Seeing the
    list is what makes matching it unmistakable rather than probable."""
    packet = packet_for("Symmetric allocation", [a_clarification_turn()])
    [turn] = history_of(packet)
    assert turn["you_offered"] == THE_OPTIONS


def test_the_reply_is_named_as_a_reply() -> None:
    packet = packet_for("Symmetric allocation", [a_clarification_turn()])
    [turn] = history_of(packet)
    assert "ANSWERING" in turn["note"]
    assert "rather than asking again" in turn["note"]


def test_but_the_reader_is_allowed_to_change_the_subject() -> None:
    """An instruction to read the next question as the answer, full stop,
    would misread a reader who asked something else entirely. The note has
    to leave that open, and saying so is the difference between guidance
    and a rule that is sometimes wrong."""
    packet = packet_for("actually, ECL by stage please",
                        [a_clarification_turn()])
    [turn] = history_of(packet)
    assert "it is a new question" in turn["note"]


# ---- it costs nothing on a turn that is not one ------------------------

def test_an_ordinary_turn_carries_none_of_this() -> None:
    ordinary = {"turn_id": "t-1", "ordinal": 1, "question": "EAD by sector",
                "answer": {"disposition": "answer", "narrative": "n"}}
    packet = packet_for("and by stage?", [ordinary])
    [turn] = history_of(packet)
    assert set(turn) == {"turn_id", "ordinal", "question", "answer",
                         "disposition"}


def test_a_clarification_with_no_options_carries_no_empty_list() -> None:
    """An empty `you_offered` is a list the analyst has to read to discover
    it says nothing."""
    turn = a_clarification_turn(answer={"clarification_options": []})
    packet = packet_for("symmetric", [turn])
    [seen] = history_of(packet)
    assert "you_offered" not in seen
    assert seen["you_asked"] == THE_QUESTION


def test_the_options_are_bounded() -> None:
    """The block rides on every turn of the rest of the thread."""
    turn = a_clarification_turn(
        answer={"clarification_options": [f"choice {n}" for n in range(30)]})
    [seen] = history_of(packet_for("choice 3", [turn]))
    assert len(seen["you_offered"]) == ctx_mod.CLARIFICATION_OPTIONS


def test_a_turn_that_asked_but_recorded_no_question_says_so_emptily() -> None:
    """Defensive: the field is optional in the contract, so a clarification
    without one must not raise here and must not invent a question."""
    turn = a_clarification_turn(answer={"clarification_question": None})
    [seen] = history_of(packet_for("symmetric", [turn]))
    assert seen["you_asked"] == ""


# ---- and it survives the round trip through the store ------------------

def test_the_keys_are_actually_in_what_the_store_gives_back(
        store_db) -> None:
    """The projection is only half the claim.

    `recent_turns` reads the answer back out of SQLite as JSON. If the
    clarification keys were not being persisted, the block above would be
    reading fields that are always absent in production and every test
    above would still pass, because they hand the turn in by hand.
    """
    thread_id = store_db.create_thread(
        tenant_id="t1", principal_id="u1",
        domain_id=dom.CORPORATE, release_id="r1")
    store_db.append_turn(
        thread_id=thread_id, run_id="run-1",
        question="compare PD, LGD & CCF",
        answer=a_clarification_turn()["answer"])
    [back] = store_db.recent_turns(thread_id, 3)
    assert back["answer"]["clarification_question"] == THE_QUESTION
    assert back["answer"]["clarification_options"] == THE_OPTIONS


def test_the_published_answer_is_what_carries_them() -> None:
    """The last link, named rather than assumed.

    The chain is: `FinalResponse.to_dict()` -> `outcome.response` ->
    `append_turn` -> `recent_turns` -> this history block. The store test
    above hands in an answer by hand, so on its own it would pass over a
    contract that had stopped emitting these keys. This asserts the source.
    """
    from backend.cockpit_v4 import contracts as contracts_mod
    import inspect

    emitted = inspect.getsource(contracts_mod.FinalResponse.to_dict)
    assert '"clarification_question"' in emitted
    assert '"clarification_options"' in emitted


# ---- §16 mutation check ------------------------------------------------

def test_the_old_projection_would_lose_the_question() -> None:
    """Reproduces the block as it was: the four keys it carried, and
    nothing else. If this ever finds the question, the fix is not what is
    doing the work."""
    turn = a_clarification_turn()
    prior = turn["answer"]
    old = {"turn_id": turn["turn_id"], "ordinal": turn["ordinal"],
           "question": turn["question"],
           "answer": prior.get("narrative", "")[:1500],
           "disposition": prior.get("disposition", "")}
    assert THE_QUESTION not in json.dumps(old), (
        "the old projection carried the clarification question after all; "
        "this suite is about a defect that was not there")
