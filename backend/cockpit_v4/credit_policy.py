"""
The bank's credit policy: a synopsis always, the clauses on request.

Why not attach the policy
-------------------------
The pack is thirty clauses across two books, about 23KB of JSON. Attaching
it to every question would put the whole rule book in front of a request
about Stage 2 exposure -- the same mistake `product_knowledge` exists to
avoid, in a different costume. So two layers:

  * a compact SYNOPSIS -- which book's policy is in force, its sections,
    and the handful of thresholds a portfolio question actually collides
    with (the concentration limits, the DBR caps, the SICR triggers, the
    write-off points). Enough to notice that a number has crossed a line.

  * `retrieve`, which returns the clauses a specific question needs, in
    full, with their thresholds and the levers they offer.

Both are carried on the ANSWER turn, by `context.finalization_system`. Not
on an action turn, which holds no number yet and so has nothing to compare
against a threshold; and not through a tool.

WHY NOT A TOOL. It was one first, and the tool could not be called. The
state that holds a result REQUIRES `finalize_response`, so a tool offered
beside it is a schema every action turn pays for and no turn can reach.
Retrieval here needs no judgement either -- the clause ids and the topics
are in the reader's own words, exactly as `values.resolve` reads a category
value -- so the server does it and the analyst cannot fail to have asked.

THE BOOKS DO NOT CROSS. A Retail thread never receives a Corporate clause
and a Corporate thread never receives a Retail one, because a policy
recommendation quoting the wrong book's rule is worse than no
recommendation: it is a governance answer that sounds right.

What this is for
----------------
A finding is not an action. "Credit Card - Gold held by non-salaried
customers is entering arrears at 20-29 days" is a fact about the book; what
a credit committee needs next is which rule allowed it and what changing
that rule would do. That step is only safe if the rule is QUOTED rather
than remembered, which is why `recommendation` below refuses a policy
action that cites no clause -- the same discipline as a numeric claim with
no evidence behind it.

CreditProbe retrieves. The analyst decides. Nothing here writes a
recommendation or ranks one clause above another.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PACK_PATH = Path(__file__).resolve().parent / "credit_policy.json"

#: The most clauses one retrieval may return. A question that matches more
#: than this is a question that has not been narrowed, and returning forty
#: clauses is attaching the pack by another route.
MAX_CLAUSES = 8

#: The separator between the book letters and the number, as anything a
#: reader might put there: a hyphen, a space, nothing, or the typographic
#: dash a word processor made of the hyphen in the document they copied it
#: out of. Someone quoting a clause id at the product is the likeliest way
#: one arrives, and the dash they paste is not the one they typed.
_ID_GAP = r"[-\s\u2010-\u2015]?"

#: A clause id as the pack writes it: `RP-1.1`, `CP-4.2`.
CLAUSE_ID = re.compile(rf"\b([RC]P){_ID_GAP}(\d+)\.(\d+)\b", re.IGNORECASE)

#: A section id: `RP-1`, `CP-4`.
SECTION_ID = re.compile(rf"\b([RC]P){_ID_GAP}(\d+)\b(?!\.)", re.IGNORECASE)


class UnknownBook(ValueError):
    """A policy book that does not exist. Named, never coerced."""


@lru_cache(maxsize=1)
def pack() -> dict[str, Any]:
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def version() -> str:
    return str(pack().get("pack_version", "unknown"))


def _book(book_id: str) -> dict[str, Any]:
    books = pack()["books"]
    if book_id not in books:
        raise UnknownBook(
            f"{book_id!r} has no credit policy in this pack. The books are "
            f"{', '.join(sorted(books))}.")
    return books[book_id]


def clauses(book_id: str) -> list[dict[str, Any]]:
    """Every clause of one book, flattened, each carrying its section."""
    out: list[dict[str, Any]] = []
    for section in _book(book_id)["sections"]:
        for clause in section["clauses"]:
            out.append({**clause, "section": section["section"],
                        "section_title": section["title"],
                        "book_id": book_id})
    return out


def clause(book_id: str, clause_id: str) -> dict[str, Any] | None:
    wanted = clause_id.strip().upper().replace(" ", "-")
    return next((c for c in clauses(book_id)
                 if c["clause"].upper() == wanted), None)


# ---- the always-present synopsis ---------------------------------------

#: The clauses a PORTFOLIO question runs into, per book. Deliberately short:
#: this is what makes the analyst notice that a number has crossed a line,
#: not what lets it write the policy change.
_HEADLINE: dict[str, tuple[str, ...]] = {
    "retail": ("RP-1.1", "RP-2.2", "RP-4.1", "RP-5.1"),
    "corporate": ("CP-1.1", "CP-1.2", "CP-2.1", "CP-3.1"),
}


def synopsis(book_id: str) -> dict[str, Any]:
    """The compact policy facts carried on an analytical turn.

    Substantive enough to recognise a breach, small enough to carry: the
    sections by name, and the four clauses a portfolio number is most
    likely to collide with, with their thresholds.
    """
    book = _book(book_id)
    by_id = {c["clause"]: c for c in clauses(book_id)}
    return {
        "policy": book["title"],
        "pack_version": version(),
        "book": book_id,
        "approved_by": book["approved_by"],
        "next_review": book["next_review"],
        "not_client_data": True,
        "sections": [f"{s['section']} {s['title']}"
                     for s in book["sections"]],
        "thresholds_a_portfolio_number_meets": [
            {"clause": cid, "title": by_id[cid]["title"],
             "rule": by_id[cid]["rule"],
             **({"thresholds": by_id[cid]["thresholds"]}
                if by_id[cid].get("thresholds") else {})}
            for cid in _HEADLINE.get(book_id, ()) if cid in by_id],
        "more_detail": (
            "Clauses this question names are attached in full below, with "
            "their thresholds and the levers each one offers. Nothing is "
            "attached when the question names no policy topic."),
        "citation_rule": (
            "A policy action names the clause it changes, quotes the rule "
            "in force, and states the proposed change. A recommendation "
            "citing no clause is not publishable."),
    }


# ---- retrieval ----------------------------------------------------------

#: Words that point at ONE CLAUSE. Deterministic, inspectable, and extended
#: when the pack gains a clause rather than guessed at by a model.
#:
#: PER CLAUSE, NOT PER SECTION, and the words are phrases rather than terms.
#: Both were the other way first and the retrieval fired on a third of an
#: ordinary question bank: "What is exposure at default by sector?" pulled
#: the whole rating and watchlist section because CP-3 listed the word
#: "default", and "What is average LTV by collateral type?" pulled five
#: clauses of retail product limits because RP-2 listed "ltv". A policy
#: attachment that arrives on every other question is the pack attached by
#: another route, and it teaches the analyst to stop reading it.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Retail
    "RP-1.1": ("dbr", "debt burden", "affordability", "instalment burden"),
    "RP-1.2": ("salary transfer", "salary assignment"),
    "RP-1.3": ("non-salaried income", "self-employed income",
               "income verification", "bank statement"),
    "RP-2.1": ("card limit", "credit limit", "limit increase",
               "limit ceiling"),
    "RP-2.2": ("cut-off", "cutoff", "application score", "score floor",
               "minimum score"),
    "RP-2.3": ("maximum tenor", "tenor cap", "tenor"),
    "RP-2.4": ("ltv", "loan to value", "loan-to-value"),
    "RP-2.5": ("override", "exception approval", "policy exception"),
    "RP-3.1": ("new product", "pilot", "launch", "bnpl", "buy now pay later",
               "buy now"),
    "RP-3.2": ("partner channel", "digital channel", "origination channel",
               "first payment default", "partner"),
    "RP-3.3": ("vintage", "months on book", "cohort performance"),
    "RP-4.1": ("collection", "collections", "arrears band", "dpd band",
               "days past due", "sub-bucket", "delinquency bucket",
               "field visit", "contact strategy"),
    "RP-4.2": ("restructure", "forbearance", "reschedul"),
    "RP-4.3": ("deferral", "payment holiday", "ramadan", "seasonal"),
    "RP-4.4": ("write-off", "write off", "charge-off", "charge off"),
    "RP-5.1": ("sicr", "significant increase", "staging trigger",
               "stage 2 trigger", "moves to stage 2"),
    "RP-5.2": ("definition of default", "default definition", "cure period",
               "90 days past due"),
    # Corporate
    # "single name" is what a credit officer writes for this clause more
    # often than "single obligor", which is what the clause is titled. A
    # limit nobody can find by its working name is a limit that gets
    # breached.
    "CP-1.1": ("single obligor", "obligor limit", "large exposure",
               "single name", "single borrower"),
    "CP-1.2": ("group limit", "connected", "group exposure",
               "related parties", "concentration"),
    # "industry" and "sector" are the same word in this conversation.
    "CP-1.3": ("sector limit", "sector cap", "sub-sector limit",
               "sector concentration", "industry limit",
               "industry concentration"),
    "CP-2.1": ("covenant package", "dscr", "debt service", "leverage "
               "covenant", "interest cover", "covenant"),
    "CP-2.2": ("breach", "waiver", "remediation"),
    "CP-2.3": ("debt service below", "below policy", "utp",
               "unlikeliness to pay"),
    "CP-3.1": ("watchlist", "watch list", "early warning"),
    "CP-3.2": ("rating review", "rating action", "out of cycle",
               "review frequency", "notch"),
    "CP-3.3": ("sicr", "significant increase", "staging trigger",
               "stage 2 trigger"),
    "CP-4.1": ("haircut", "collateral value", "recognised collateral",
               "security value"),
    "CP-4.2": ("revaluation", "valuation age", "ltv", "loan to value",
               "top up", "top-up"),
    "CP-5.1": ("new product", "pilot", "product approval"),
    "CP-5.2": ("supply chain", "scf", "receivables finance", "dilution",
               "programme limit", "anchor"),
}


#: Every separator a reader might put between two words, as one space.
#:
#: The matcher was a raw substring test over the lowercased question, so
#: "single-name limit" and "single name limit" were two different questions
#: and only one of them reached CP-1.1. The map had already begun paying for
#: this by hand -- "write-off" listed beside "write off", "cut-off" beside
#: "cutoff", "loan-to-value" beside "loan to value" -- which is the tell:
#: three entries standing in for a missing normalisation, and still 75 of
#: the map's own keywords failing when their own punctuation was flipped
#: back at it. "cut off", the third spelling, matched nothing at all.
#:
#: The en- and em-dash range is in here because a question pasted out of a
#: policy document or an email carries typographic dashes, and a clause that
#: cannot be found by someone quoting it is the worst case of the lot.
_SEPARATORS = re.compile(r"[\s\-\u2010-\u2015_/]+")


def flatten(text: str) -> str:
    """Lowercase, with every run of separators reduced to one space."""
    return _SEPARATORS.sub(" ", (text or "").lower()).strip()


def clauses_for(book_id: str, question: str) -> list[str]:
    """Which clauses of THIS book's policy the question names."""
    text = flatten(question)
    prefix = "RP" if book_id == "retail" else "CP"
    return [cid for cid, words in _KEYWORDS.items()
            if cid.startswith(prefix)
            and any(flatten(word) in text for word in words)]


def sections_for(book_id: str, question: str) -> list[str]:
    """The sections those clauses sit in. Kept for callers that want the
    coarser answer; retrieval works at clause grain."""
    return sorted({cid.split(".")[0]
                   for cid in clauses_for(book_id, question)})


def named_clauses(question: str) -> list[str]:
    """Clause ids written out in the question, normalised."""
    return [f"{m.group(1).upper()}-{m.group(2)}.{m.group(3)}"
            for m in CLAUSE_ID.finditer(question or "")]


def named_sections(question: str) -> list[str]:
    return [f"{m.group(1).upper()}-{m.group(2)}"
            for m in SECTION_ID.finditer(question or "")]


def retrieve(book_id: str, *, question: str = "",
             clause_ids: tuple[str, ...] = (),
             topic: str = "") -> dict[str, Any]:
    """The clauses this question needs, in full, and nothing else.

    Three ways in, in order of precision: an explicit clause id, a section
    id, and finally a keyword match over the question. Whichever is used,
    the result is bounded at `MAX_CLAUSES` -- an unbounded retrieval is the
    pack by another name.
    """
    book = _book(book_id)
    everything = clauses(book_id)
    wanted: list[dict[str, Any]] = []
    how = ""

    asked = [c.upper().replace(" ", "-") for c in clause_ids]
    asked += named_clauses(question) + named_clauses(topic)
    seen: set[str] = set()
    for cid in asked:
        # A clause id THIS book does not hold resolves to nothing, which is
        # how a Retail thread asking for `CP-1.2` comes back empty rather
        # than reaching across.
        found = clause(book_id, cid)
        if found is not None and found["clause"] not in seen:
            wanted.append(found)
            seen.add(found["clause"])
    if wanted:
        how = "clause id"

    if not wanted:
        sections = [x for x in named_sections(question) + named_sections(topic)
                    if x.startswith("RP" if book_id == "retail" else "CP")]
        if sections:
            wanted = [c for c in everything if c["section"] in sections]
            how = "section"

    if not wanted:
        matched = clauses_for(book_id, f"{question} {topic}")
        if matched:
            wanted = [c for c in everything if c["clause"] in matched]
            how = "topic"

    if not wanted:
        return {
            "book": book_id, "policy": book["title"],
            "pack_version": version(), "clauses": [], "matched_by": "",
            "note": ("No clause of this book's policy matches. Name a "
                     "clause id, a section id, or a policy topic -- "
                     "concentration, covenants, collections, staging, "
                     "collateral, cut-offs, a new product."),
            "sections": [f"{s['section']} {s['title']}"
                         for s in book["sections"]],
        }

    clipped = len(wanted) > MAX_CLAUSES
    return {
        "book": book_id, "policy": book["title"],
        "pack_version": version(),
        "approved_by": book["approved_by"],
        "next_review": book["next_review"],
        "not_client_data": True,
        "matched_by": how,
        "clauses": wanted[:MAX_CLAUSES],
        "clipped": clipped,
        "citation_rule": (
            "Quote the clause id and the rule as written when proposing a "
            "change to it. The `levers` on each clause are the changes that "
            "clause can carry; they are not a recommendation."),
    }


# ---- a recommendation has to cite --------------------------------------

def citations(text: str, book_id: str) -> list[str]:
    """Clause ids in a piece of prose that this book actually contains.

    A clause id the pack does not hold is not a citation. Neither is the
    OTHER book's clause: `CP-1.2` quoted in a Retail answer is a rule that
    does not apply to the exposure being discussed, which is a worse error
    than quoting nothing.
    """
    held = {c["clause"] for c in clauses(book_id)}
    seen: list[str] = []
    for cid in named_clauses(text):
        if cid in held and cid not in seen:
            seen.append(cid)
    return seen


def foreign_citations(text: str, book_id: str) -> list[str]:
    """Clause ids from the OTHER book. Always a mistake, never a warning."""
    other = "corporate" if book_id == "retail" else "retail"
    held = {c["clause"] for c in clauses(other)}
    return [cid for cid in named_clauses(text) if cid in held]


__all__ = ["CLAUSE_ID", "MAX_CLAUSES", "PACK_PATH", "UnknownBook",
           "citations", "clause", "clauses", "clauses_for",
           "foreign_citations", "named_clauses", "named_sections", "pack",
           "retrieve", "sections_for", "synopsis", "version"]
