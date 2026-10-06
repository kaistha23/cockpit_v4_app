"""
Which CATEGORY did the reader mean? Resolved without a model call.

Two different questions, and they were being answered by the same mechanism
-------------------------------------------------------------------------
SCHEMA RESOLUTION asks "which field?" — is it `product_type`, `facility_type`,
`product`? The catalogue answers that, and it is already good at it.

VALUE RESOLUTION asks "which category value?" — is "prject finance" the thing
the book calls `project_finance`? Nothing answered that, so a live run spent
generation after generation asking the catalogue for `product_name`,
`product_category`, `facility_type`, `product`, `product_code` — inventing
field names because the field it already had did not seem to contain the
value the user typed.

The reader does not know the book's underscores or its capitalisation. They
should not have to.

What this does
--------------
For every GOVERNED categorical field of low cardinality, it builds a bounded
index of the values that actually exist, keyed by a normalised form and by a
small set of authored aliases. Then:

  "project finance", "Project Finance", "project-finance", "PROJECT_FINANCE"
      → project_finance, exactly, with no fuzzy matching at all.

  "prject finance", "projet finance", "project finence"
      → project_finance, when the near match is UNIQUELY strong.

  "finance"
      → refused, with the close matches named, because it could be Project
        Finance, Personal Finance or Auto Finance and guessing between them
        is choosing the analysis.

What it deliberately does not do
--------------------------------
It does not resolve a value it is not sure about, it does not index a
high-cardinality field (there is no useful "did you mean" across four
thousand borrower names), and it never invents a value that is not in the
book. A resolution is recorded as a `canonical_mapping` and a
`resolved_assumption`, so the trace shows what was read and what it was taken
to mean.
"""

from __future__ import annotations

import difflib
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import schema as schema_mod

#: A field with more distinct values than this is not a dimension a reader
#: names by hand, and "did you mean" across it is noise. Borrower names,
#: account ids and months are all above it on purpose.
MAX_CARDINALITY = 80

#: How close a non-exact match must be before it is taken, and how much
#: closer than the runner-up. Both are needed: a strong match that is only
#: barely stronger than another strong match is an ambiguity, not a typo.
MIN_SIMILARITY = 0.82
MIN_MARGIN = 0.08

#: Candidates shown to the reader when the match is not unique.
MAX_ALTERNATIVES = 4


@dataclass(frozen=True)
class Resolution:
    """One phrase, taken to mean one category value of one field."""

    raw: str
    field_name: str
    value: str
    exact: bool
    similarity: float
    relation: str = ""

    @property
    def display(self) -> str:
        return pretty(self.value)

    def as_mapping(self) -> dict[str, str]:
        """The canonical mapping a run records for this resolution."""
        return {"term": self.raw, "field": f"{self.relation}.{self.field_name}"
                if self.relation else self.field_name,
                "value": self.value}

    def as_assumption(self) -> str:
        if self.exact:
            return (f"Read {self.raw!r} as {self.field_name} = "
                    f"{self.value!r}.")
        return (f"Interpreted {self.raw!r} as {self.display} "
                f"({self.field_name} = {self.value!r}).")


@dataclass(frozen=True)
class Ambiguity:
    """A phrase that could be more than one real value. Asked, never guessed."""

    raw: str
    candidates: tuple[Resolution, ...]

    @property
    def question(self) -> str:
        names = [c.display for c in self.candidates]
        if len(names) == 2:
            joined = f"{names[0]} or {names[1]}"
        else:
            joined = ", ".join(names[:-1]) + f" or {names[-1]}"
        return f"Did you mean {joined}?"


@dataclass(frozen=True)
class Dimension:
    """One governed categorical field and the values it actually holds."""

    relation: str
    field_name: str
    values: tuple[str, ...]
    #: normalised form -> canonical value. Carries authored aliases too.
    lookup: dict[str, str] = field(default_factory=dict)
    #: the subset of `lookup` keys that are real values rather than aliases.
    own: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {"relation": self.relation, "field": self.field_name,
                "values": list(self.values), "count": len(self.values)}


# ---- normalisation -----------------------------------------------------

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"[\s_\-]+")


def normalize(text: str) -> str:
    """Case, spaces, underscores, hyphens and punctuation, all made one form.

    `project finance`, `Project Finance`, `project_finance` and
    `project-finance` are the same request written four ways, and refusing
    three of them is refusing the reader's own language.
    """
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _PUNCT.sub(" ", folded.lower())
    return _SPACE.sub(" ", folded).strip()


def pretty(value: str) -> str:
    """The reader's form of a canonical value: `project_finance` → Project
    Finance. A value that is already written for people is left alone."""
    text = str(value or "")
    if "_" not in text and text != text.lower():
        return text
    return " ".join(part.capitalize() for part in _SPACE.split(text) if part)


#: Authored aliases, per canonical value. Short forms a reader really types.
#: Deliberately small: an alias table is a promise that the word means one
#: thing, and a long one is a list of promises nobody checked.
ALIASES: dict[str, tuple[str, ...]] = {
    "project_finance": ("project fin", "pf lending", "project lending"),
    "working_capital": ("wc", "working cap"),
    "revolving_credit": ("revolver", "rcf"),
    "trade_finance": ("trade fin", "tf"),
    "term_loan": ("term lending", "tl"),
    "asset_finance": ("asset lending",),
    "Information Technology": ("it", "info tech", "tech", "technology"),
    "Agriculture and Agri-processing": ("agri", "agriculture"),
    "Transport and Logistics": ("logistics", "transport"),
    "Metals and Mining": ("mining", "metals"),
    "Power and Utilities": ("utilities", "power"),
    "Real Estate": ("property", "re"),
    "Credit Card": ("credit cards", "cards", "card"),
    "Personal Finance": ("personal loan", "personal loans", "pl"),
    "Auto Finance": ("auto", "car finance", "motor finance"),
    "Mortgage": ("mortgages", "home loan", "home loans"),
    "Buy Now Pay Later": ("bnpl", "buy now pay later", "pay later",
                          "instalment"),
    "supply_chain_finance": ("scf", "supply chain", "supplier finance"),
    # Sub-products a reader names by their short form. "Gold" alone is
    # ambiguous across books, which is why it is not here: `Credit Card -
    # Gold` is reached by naming the product too.
    "Debt Consolidation": ("consolidation", "debt consol"),
    "Salary Advance": ("salary advance", "advance"),
    "Used Vehicle": ("used car", "used cars", "second hand"),
    "New Vehicle": ("new car", "new cars"),
    # Employment types. The hyphen is the canonical form and nobody types it.
    "Salaried-Government": ("government", "govt", "public sector",
                            "salaried government"),
    "Salaried-Private": ("private sector", "salaried private"),
    "Non-salaried": ("non salaried", "nonsalaried", "no salary transfer"),
    "Self-employed": ("self employed", "selfemployed"),
}


# ---- building the index ------------------------------------------------

_CACHE: dict[tuple[str, ...], dict[str, Dimension]] = {}
_LOCK = threading.RLock()


def dimensions(*, session: Any, catalog: Any = None) -> dict[str, Dimension]:
    """Every governed categorical field of this book, with its real values.

    Read from the session, so the values are the ones the release actually
    holds -- not a list somebody wrote down beside it. Cached per release
    fingerprint, because a release is immutable and a rebuilt one is a
    different release.
    """
    catalog = catalog if catalog is not None else session.catalog
    key = (str(getattr(catalog, "tenant_id", "")),
           str(getattr(catalog, "domain_id", "")),
           str(getattr(catalog, "dataset_release_id", "")),
           str(getattr(catalog, "release_fingerprint", "")))
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit

    found: dict[str, Dimension] = {}
    for relation in catalog.relations():
        spec = catalog.spec(relation)
        for column in spec.fields:
            if not _is_categorical(column):
                continue
            values = _distinct(session, relation, column.name)
            if not values or len(values) > MAX_CARDINALITY:
                continue
            # First relation wins: `sector` on the borrower table and on the
            # facility table are the same dimension, and offering it twice
            # would make every resolution ambiguous with itself.
            if column.name in found:
                continue
            found[column.name] = Dimension(
                relation=relation, field_name=column.name,
                values=values, lookup=_lookup(values),
                own=_own(values))
    with _LOCK:
        _CACHE[key] = found
        while len(_CACHE) > 6:
            _CACHE.pop(next(iter(_CACHE)))
    return found


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


#: Columns that are identifiers, periods or governance rather than dimensions
#: a reader names. Excluded by NAME because excluding them by cardinality
#: would still index a small tenant list or a two-value release id.
#: `_previous` is excluded on purpose: `rating_previous` holds exactly the
#: values `rating_current` holds, so indexing it would make every rating an
#: ambiguity with itself while adding no value the reader could name.
#:
#: The period suffixes come from the CALENDARS rather than a literal list.
#: They were written out as `_month` when there was one calendar, and the
#: moment the Corporate book started reporting quarters its
#: `reporting_quarter`, `origination_quarter` and `waiver_quarter` columns
#: all became "governed dimensions" a reader could supposedly name -- so
#: `2021Q3` was offered as a category and the bounded enumeration carried
#: sixty dates it had no business carrying. A calendar is not a dimension in
#: any book, so the rule is derived from the set of calendars there are.
_PERIOD_SUFFIXES = tuple(sorted(set(schema_mod.PERIOD_NOUNS.values())))

_NOT_A_DIMENSION = re.compile(
    r"(_id|_date|_name|_previous|"
    + "|".join(f"_{noun}" for noun in _PERIOD_SUFFIXES)
    + r")$|^(tenant_id|dataset_release_id|domain_id|reporting_currency)$")


def _is_categorical(column: Any) -> bool:
    if str(getattr(column, "dtype", "")) != "string":
        return False
    return not _NOT_A_DIMENSION.search(str(column.name))


def _distinct(session: Any, relation: str, column: str) -> tuple[str, ...]:
    try:
        rows = session.connection.execute(
            f'SELECT DISTINCT "{column}" AS v FROM "{relation}" '
            f'WHERE "{column}" IS NOT NULL AND "{column}" <> \'\' '
            f'ORDER BY 1 LIMIT {MAX_CARDINALITY + 1}').fetchall()
    except Exception:  # noqa: BLE001 - an unreadable column is not a dimension
        return ()
    return tuple(str(r[0]) for r in rows)


def _aliases_for(value: str) -> tuple[str, ...]:
    """The authored short forms for this value, matched on its NORMALISED
    spelling.

    Keyed that way because a release may spell a governed value
    `project_finance` or `Project Finance` and the alias table is about the
    THING, not the typography. Keying it literally meant an alias table
    written against one spelling went silently dead when the release changed
    to the other.
    """
    wanted = normalize(value)
    for key, aliases in ALIASES.items():
        if normalize(key) == wanted:
            return aliases
    return ()


def _lookup(values: tuple[str, ...]) -> dict[str, str]:
    table: dict[str, str] = {}
    for value in values:
        table[normalize(value)] = value
        table[normalize(pretty(value))] = value
        for alias in _aliases_for(value):
            table.setdefault(normalize(alias), value)
    return table


def _own(values: tuple[str, ...]) -> frozenset[str]:
    """The normalised forms that ARE a value of this field, alias-free.

    An authored alias is a convenience. A value the book actually holds is a
    fact, and where the two collide the fact wins: `Mining` is a real
    sub-sector of this book, so "mining" means that and not the `Metals and
    Mining` sector the alias table also offers.
    """
    forms: set[str] = set()
    for value in values:
        forms.add(normalize(value))
        forms.add(normalize(pretty(value)))
    return frozenset(forms)


# ---- resolving ---------------------------------------------------------

def resolve(phrase: str, *, index: dict[str, Dimension],
            field_name: str = "") -> Resolution | Ambiguity | None:
    """What the reader meant, or an honest question, or nothing.

    `field_name` narrows the search to one dimension when the question
    already named it. Without it, every governed dimension is searched, and a
    phrase matching values in two different dimensions is an ambiguity like
    any other.
    """
    wanted = normalize(phrase)
    if not wanted:
        return None
    searched = ({field_name: index[field_name]} if field_name in index
                else index)

    exact = [Resolution(raw=str(phrase), field_name=dim.field_name,
                        value=dim.lookup[wanted], exact=True, similarity=1.0,
                        relation=dim.relation)
             for dim in searched.values() if wanted in dim.lookup]
    # A real value of the book outranks an authored alias for another value.
    owned = [r for r in exact
             if wanted in searched[r.field_name].own]
    if owned:
        exact = owned
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # The same spelling in two dimensions. Real, and not guessable:
        # "Riyadh" is a region on two relations of the same book.
        unique = {r.value for r in exact}
        if len(unique) == 1:
            return exact[0]
        return Ambiguity(raw=str(phrase),
                         candidates=tuple(exact[:MAX_ALTERNATIVES]))

    scored: list[Resolution] = []
    for dim in searched.values():
        for key, value in dim.lookup.items():
            ratio = _similarity(wanted, key)
            if ratio >= MIN_SIMILARITY:
                scored.append(Resolution(
                    raw=str(phrase), field_name=dim.field_name, value=value,
                    exact=False, similarity=round(ratio, 4),
                    relation=dim.relation))
    if not scored:
        return _contained(wanted, str(phrase), searched)
    scored.sort(key=lambda r: (-r.similarity, r.value))
    best = scored[0]
    rivals = [r for r in scored if r.value != best.value]
    if rivals and best.similarity - rivals[0].similarity < MIN_MARGIN:
        seen: dict[str, Resolution] = {}
        for candidate in scored:
            seen.setdefault(candidate.value, candidate)
        return Ambiguity(raw=str(phrase),
                         candidates=tuple(list(seen.values())
                                          [:MAX_ALTERNATIVES]))
    return best


def _contained(wanted: str, raw: str, searched: dict[str, Dimension]
               ) -> Ambiguity | None:
    """The half-named value: "finance" when the book holds four of them.

    A bare word that is a whole token of several real values is not a near
    miss and not a typo -- it is a reader naming a family. Silently dropping
    it is how "within project finance" became five catalogue calls, and
    picking one of the four would be choosing the analysis. So it is asked
    about, by name, and only when there is genuinely more than one answer: a
    single containment is left alone for schema resolution to handle, because
    guessing which word was omitted is still guessing.
    """
    tokens = set(wanted.split())
    if not tokens or tokens <= STOPWORDS:
        return None
    hits: dict[str, Resolution] = {}
    for dim in searched.values():
        for key in dim.own:
            if tokens < set(key.split()):
                value = dim.lookup[key]
                hits.setdefault(value, Resolution(
                    raw=raw, field_name=dim.field_name, value=value,
                    exact=False, similarity=0.0, relation=dim.relation))
    if len(hits) < 2:
        return None
    return Ambiguity(raw=raw, candidates=tuple(sorted(
        hits.values(), key=lambda r: r.value)[:MAX_ALTERNATIVES]))


def _similarity(left: str, right: str) -> float:
    """Sequence similarity, with a token-overlap floor.

    `difflib` alone scores "prject finance" against "personal finance" higher
    than is comfortable, so a shared whole token lifts a candidate and a
    completely disjoint token set caps it.
    """
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if left_tokens & right_tokens:
        ratio = max(ratio, 0.5 + 0.5 * len(left_tokens & right_tokens)
                    / max(len(left_tokens | right_tokens), 1))
    elif ratio < 0.9:
        ratio *= 0.9
    return ratio


#: Words that carry no category. A window is trimmed of them at both ends
#: before it is resolved, and a window made only of them is not a phrase at
#: all -- otherwise "and" matches every value with "and" in its name, which
#: is four of this book's sectors.
STOPWORDS = frozenset((
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "by", "for",
    "to", "from", "with", "within", "into", "per", "across", "about",
    "how", "what", "which", "who", "why", "when", "where", "is", "are",
    "was", "were", "be", "being", "been", "do", "does", "did", "show",
    "give", "list", "tell", "me", "us", "my", "our", "it", "its", "this",
    "that", "these", "those", "please", "now", "also", "just", "only",
    "break", "split", "down", "up", "out", "over", "under", "vs", "versus",
))


def _trim(words: list[str]) -> list[str]:
    """The window with its leading and trailing filler removed."""
    left, right = 0, len(words)
    while left < right and words[left] in STOPWORDS:
        left += 1
    while right > left and words[right - 1] in STOPWORDS:
        right -= 1
    return words[left:right]


def phrases(question: str, *, index: dict[str, Dimension],
            max_words: int = 4) -> list[Resolution | Ambiguity]:
    """Every category value this question appears to name.

    Windows of up to four words, longest first, so "project finance" is tried
    before "finance" and the longer, unambiguous reading wins. Each window is
    trimmed of filler first, so "within prject finance" is read as the two
    words that carry the category and the resolution records those two --
    not the preposition the reader happened to put in front of them.
    """
    words = normalize(question).split()
    found: list[Resolution | Ambiguity] = []
    taken: set[int] = set()
    # EXACT first, across every window size, before any near match is
    # considered anywhere in the question. Otherwise "how are credit cards
    # doing" resolves on the three-word window and records the reader as
    # having said "credit cards doing", because a near match on a longer
    # window outscored the exact match sitting inside it.
    for want_exact in (True, False):
        for size in range(min(max_words, len(words)), 0, -1):
            for start in range(0, len(words) - size + 1):
                span = set(range(start, start + size))
                if span & taken:
                    continue
                window = words[start:start + size]
                if _trim(window) != window:
                    # A shorter window covers the same ground without the
                    # filler, and it is reached on a later pass.
                    continue
                if all(word in STOPWORDS for word in window):
                    continue
                outcome = resolve(" ".join(window), index=index)
                if outcome is None:
                    continue
                hit = isinstance(outcome, Resolution) and outcome.exact
                if hit is not want_exact:
                    continue
                if not want_exact and isinstance(outcome, Resolution) \
                        and size == 1 and len(window[0]) <= 4:
                    # A single short word is not enough evidence for a typo
                    # correction: "auto" is an alias and "aut" is a mistake
                    # nobody can be sure about.
                    continue
                if isinstance(outcome, Ambiguity) and _dropped_a_word(
                        words, start, size, index):
                    # The reader wrote MORE than this window, the longer
                    # phrase matched nothing, and this window is ambiguous
                    # only because a word was dropped.
                    #
                    # "and within project finance?" asked of the Retail book
                    # resolved `finance` and asked "did you mean Auto Finance
                    # or Personal Finance?" -- a question about something the
                    # reader did not say, built by discarding the word that
                    # made their phrase unambiguous. The Retail book has no
                    # project finance, and saying so is the honest answer.
                    continue
                found.append(outcome)
                taken |= span
    return found


def _dropped_a_word(words: list[str], start: int, size: int,
                    index: dict[str, Dimension]) -> bool:
    """Whether this window is ambiguous only because a neighbour was cut.

    True when the reader wrote an adjacent non-filler word that this window
    excludes, and the longer phrase including it matches nothing at all. The
    longer phrase is what they said; that it matches nothing is a fact about
    this book, not an ambiguity in their wording.
    """
    for left, right in ((start - 1, start + size), (start, start + size + 1)):
        if left < 0 or right > len(words):
            continue
        if right - left <= size:
            continue
        wider = words[left:right]
        if _trim(wider) != wider:
            continue
        if all(word in STOPWORDS for word in wider):
            continue
        if resolve(" ".join(wider), index=index) is None:
            return True
    return False


def block(index: dict[str, Dimension]) -> dict[str, Any]:
    """The bounded enumeration metadata carried in the starting context.

    Only the dimensions small enough to enumerate. A reader's question names
    a sector or a product, never one of four thousand borrower ids, and
    dumping those would be spending the context on the one thing it cannot
    help with.
    """
    return {
        "note": ("The values these governed dimensions actually hold. A "
                 "question naming one of them does not need a catalogue "
                 "call, and a spelling or spacing variant of one resolves to "
                 "it without a question."),
        "dimensions": [dim.to_dict() for dim in
                       sorted(index.values(), key=lambda d: d.field_name)],
    }


__all__ = ["ALIASES", "Ambiguity", "Dimension", "MAX_ALTERNATIVES",
           "MAX_CARDINALITY", "MIN_MARGIN", "MIN_SIMILARITY", "Resolution",
           "STOPWORDS", "block", "clear_cache", "dimensions", "normalize",
           "phrases", "pretty", "resolve"]
