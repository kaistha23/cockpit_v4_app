"""A clause a reader can find by the name they call it.

UNIT · REPRODUCTION. No model call, no database.

The defect
----------
`clauses_for` lowercased the question and asked whether each keyword was a
substring of it. So "single-name limit" and "single name limit" were two
different questions, and a hyphen decided whether a credit officer asking
about the largest exposure limit in the book got CP-1.1 or got nothing.

The map had already started paying for this by hand. "write-off" is listed
beside "write off". "cut-off" beside "cutoff". "loan-to-value" beside "loan
to value". Three entries standing in for a normalisation that was not
there -- and incomplete even so: "cut off", the third spelling, matched
nothing at all.

The measured size of it: of the map's own keywords, 75 failed when their own
punctuation was flipped back at them. Not phrases someone might invent --
the product's own vocabulary, asked in the other spelling.

The fix is one function. `flatten` lowercases and reduces every run of
separators -- spaces, ASCII hyphens, the en- and em-dash range, underscores,
slashes -- to a single space, and both sides of the comparison go through
it. The dash range is in there because a question pasted out of a policy PDF
or an email carries typographic dashes, and a clause that cannot be found by
someone quoting it is the worst case of the lot.

What is NOT claimed here
------------------------
That retrieval is good. This is a keyword map, deliberately: the analyst is
the judgement in this system, and a server that guessed at policy relevance
would be making the analyst's call for it. What is asserted is that the map
does not lose to punctuation, and that a phrase in it can be found in the
spellings a reader actually writes.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import credit_policy as cp

BOOKS = ("corporate", "retail")


def book_of(clause_id: str) -> str:
    return "retail" if clause_id.startswith("RP") else "corporate"


def flip(word: str) -> str:
    """The other spelling: a hyphenated phrase spaced, a spaced one
    hyphenated."""
    return word.replace("-", " ") if "-" in word else word.replace(" ", "-")


PAIRS = [(cid, word) for cid, words in cp._KEYWORDS.items() for word in words
         if flip(word) != word]


def test_there_is_something_to_check() -> None:
    """A guard built from a map that turned out to be single words would
    pass while checking nothing."""
    assert len(PAIRS) > 50


@pytest.mark.parametrize("clause_id,word", PAIRS,
                         ids=[f"{c}:{w}" for c, w in PAIRS])
def test_every_keyword_survives_its_own_punctuation_being_flipped(
        clause_id: str, word: str) -> None:
    """The whole defect, measured over the product's own vocabulary.

    This is the assertion that makes the fix structural rather than tuned:
    it is derived from the map, so adding one synonym cannot satisfy it and
    removing the normaliser cannot survive it.
    """
    assert clause_id in cp.clauses_for(book_of(clause_id), flip(word)), (
        f"{clause_id} is found by {word!r} and not by {flip(word)!r}; a "
        f"reader's hyphen decides whether they reach the clause")


@pytest.mark.parametrize("book", BOOKS)
def test_a_question_pasted_out_of_a_document_still_finds_its_clause(
        book: str) -> None:
    """An en-dash and an em-dash are what a word processor makes of a
    hyphen, and a quoted clause is the likeliest way one arrives."""
    for dash in ("‐", "‑", "–", "—"):
        spelled = "loan" + dash + "to" + dash + "value"
        assert cp.clauses_for(book, f"the {spelled} cap"), (
            f"{spelled!r} finds nothing in the {book} book")


DASHES = ["-", "\u2010", "\u2011", "\u2013", "\u2014", " ", ""]


@pytest.mark.parametrize("dash", DASHES, ids=[repr(d) for d in DASHES])
def test_a_clause_id_quoted_out_of_a_document_resolves(dash: str) -> None:
    """The same argument as the keyword matcher, one route over.

    Someone quoting a clause id at the product is the likeliest way one
    arrives, and the dash they paste is not the dash they typed: a word
    processor turns `CP-1.2` into `CP\u20111.2`. The id pattern allowed a
    hyphen, a space or nothing, and nothing typographic.
    """
    assert cp.named_clauses(f"what does CP{dash}1.2 say") == ["CP-1.2"]


@pytest.mark.parametrize("dash", DASHES, ids=[repr(d) for d in DASHES])
def test_a_section_id_quoted_out_of_a_document_resolves(dash: str) -> None:
    assert cp.named_sections(f"the CP{dash}2 section") == ["CP-2"]


@pytest.mark.parametrize("book", BOOKS)
def test_every_clause_is_reachable_by_its_own_title(book: str) -> None:
    """The minimum bar, and derived from the pack rather than from me: a
    clause the pack itself titles "Sector concentration" must come back
    when someone asks about sector concentration.

    RP-5.2 is titled "Default", one word that means a dozen things in a
    credit conversation, and its keywords are the specific phrases -- the
    definition of default, the cure period. Matching the bare word would
    put the default-definition clause in front of every question about a
    defaulted facility. That exclusion is a decision, so it is named.
    """
    unreachable = [c["clause"] for c in cp.clauses(book)
                   if c["clause"] not in cp.clauses_for(book, c["title"])]
    assert unreachable == ([] if book == "corporate" else ["RP-5.2"])


# ---- the vocabulary that was simply missing ----------------------------

#: Phrasings a credit officer writes, and the clause each one is about.
#:
#: Separate from the normalisation above, and honestly so: no amount of
#: punctuation handling turns "single name" into "single obligor". These
#: are standard names for the clause they point at, and the clause was
#: unreachable by them.
VOCABULARY = [
    ("corporate", "Which sectors are above the single-name limit?", "CP-1.1"),
    ("corporate", "our single name concentration", "CP-1.1"),
    ("corporate", "what is the single borrower cap", "CP-1.1"),
    ("corporate", "industry limits for real estate", "CP-1.3"),
    ("corporate", "industry concentration against the cap", "CP-1.3"),
]


@pytest.mark.parametrize("book,question,clause_id", VOCABULARY)
def test_the_clause_is_found_by_the_name_the_reader_uses(
        book: str, question: str, clause_id: str) -> None:
    assert clause_id in cp.clauses_for(book, question), question


@pytest.mark.parametrize("book,question,clause_id", VOCABULARY)
def test_and_the_full_retrieval_carries_it_through(
        book: str, question: str, clause_id: str) -> None:
    """`clauses_for` is not the product. `retrieve` is what the answer turn
    actually receives, and a match that never reaches it is a match nobody
    benefits from."""
    got = cp.retrieve(book, question=question)
    assert [c for c in got["clauses"] if c["clause"] == clause_id], (
        f"{question!r} matched {clause_id} but retrieval returned "
        f"{[c['clause'] for c in got['clauses']]}")


# ---- §16 mutation checks -----------------------------------------------

def test_without_the_normaliser_the_flip_check_would_fail() -> None:
    """The guard above must be failing for the reason claimed.

    This reproduces the OLD matcher -- raw substring over the lowercased
    question -- and asserts it loses on the map's own vocabulary. If this
    ever passes, `flatten` is not what is doing the work and the parametrised
    test above is passing for some other reason.
    """
    def old(book_id: str, question: str) -> list[str]:
        text = (question or "").lower()
        prefix = "RP" if book_id == "retail" else "CP"
        return [cid for cid, words in cp._KEYWORDS.items()
                if cid.startswith(prefix)
                and any(word in text for word in words)]

    lost = [(cid, word) for cid, word in PAIRS
            if cid not in old(book_of(cid), flip(word))]
    assert len(lost) >= 70, (
        f"only {len(lost)} keywords fail under the old matcher; the "
        f"defect this suite is about is not the defect being measured")


def test_flatten_does_not_join_words_that_were_separate() -> None:
    """The one way normalising could do harm: collapsing a separator to
    NOTHING would make "co-operate" and "cooperate" the same, and more
    importantly would let "top up" match inside "stop upstream"."""
    assert cp.flatten("loan-to-value") == "loan to value"
    assert cp.flatten("LOAN  TO\tVALUE") == "loan to value"
    assert cp.flatten("write—off") == "write off"
    assert cp.flatten("topup") != cp.flatten("top up")


def test_a_question_that_names_no_policy_topic_still_matches_nothing() -> None:
    """Normalisation must widen spellings, not widen matching. A question
    with no policy in it returns no clauses, and the retrieval says how to
    ask rather than handing over the pack."""
    for book in BOOKS:
        assert cp.clauses_for(book, "EAD by sector for the last quarter") == []
        got = cp.retrieve(book, question="what is the total exposure")
        assert got["clauses"] == []
        assert got["note"]
