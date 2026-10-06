"""How long this turn is allowed to take, decided BEFORE the first call.

The defect this exists for
--------------------------
A live thread, seeded from an attention card, had already run one analysis.
The reader asked a second question:

    "How is risk building in Information Technology?"

The run failed with DEADLINE_EXPIRED at sixty seconds. Sixty is the PRODUCT
HELP allowance. The analysis allowance is a hundred and twenty, and the run
never reached it, because the widening happened only after the analyst had
declared `DATA_ANALYSIS` -- which takes a provider generation, which on a
large book is most of a minute. The turn was killed by the clock it was
supposed to have left behind before it started.

Widening on the declaration is right and stays. What was missing is that the
declaration is not the first thing that is knowable about a turn. A thread
opened from an attention card is about a number. A thread whose last turn ran
SQL is about a number. A question naming a measure and a sector is about a
number. None of that needs a model.

What this does
--------------
It reads the question, the thread it was asked in and the mode the reader
chose, and names ONE policy family before anything is spent:

    product_help.standard    60s   $1.00
    data_analysis.standard  120s   $1.50
    data_analysis.deep      240s   $3.00

The classification is deliberately asymmetric. Starting an analytical turn on
the help allowance kills it; starting a help turn on the analysis allowance
costs nothing, because an allowance is a CEILING and a Product Help answer
that takes eight seconds still takes eight seconds. So where the evidence
points both ways, this says analysis.

It never narrows. `Ledger.adopt` only ever widens, and the declaration path
in `orchestration` is unchanged: a turn this module reads as help, whose
analyst then declares an analysis, still widens on the declaration exactly as
before. This only removes the case where that widening arrives too late.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import DEEP
from backend.cockpit_v4 import config as config_mod

PRODUCT_HELP_STANDARD = "product_help.standard"
PRODUCT_HELP_DEEP = "product_help.deep"
DATA_ANALYSIS_STANDARD = "data_analysis.standard"
DATA_ANALYSIS_DEEP = "data_analysis.deep"

#: The three families the diagnostics must name, in the order they widen.
NAMED_FAMILIES = (PRODUCT_HELP_STANDARD, DATA_ANALYSIS_STANDARD,
                  DATA_ANALYSIS_DEEP)

#: Every family, including the one a reader reaches by asking a product
#: question in Deep mode. Listed because it exists, not because it is
#: interesting.
FAMILIES = (PRODUCT_HELP_STANDARD, PRODUCT_HELP_DEEP,
            DATA_ANALYSIS_STANDARD, DATA_ANALYSIS_DEEP)


def limits_for_family(family: str) -> Any:
    """The allowance this family carries, with the limits profile applied.

    Goes through `config.effective_limits` so `policy()`, the deadline the
    route stamps and the diagnostics all show what a run will really get.
    """
    return config_mod.effective_limits({
        PRODUCT_HELP_STANDARD: "standard",
        PRODUCT_HELP_DEEP: "deep",
        DATA_ANALYSIS_STANDARD: "analytical_standard",
        DATA_ANALYSIS_DEEP: "analytical_deep",
    }[family])


def policy() -> list[dict[str, Any]]:
    """What the diagnostics publish: every family and what it allows.

    A reader looking at a run that stopped on time should be able to see the
    whole ladder without reading the source, and an operator comparing two
    deployments should be able to see that they agree.
    """
    out = []
    for family in FAMILIES:
        limits = limits_for_family(family)
        kind, _, tier = family.partition(".")
        out.append({
            "family": family,
            "query_mode": ("DATA_ANALYSIS" if kind == "data_analysis"
                           else "PRODUCT_HELP"),
            "tier": tier,
            "deadline_seconds": limits.deadline_seconds,
            "spend_ceiling_usd": limits.spend_ceiling_usd,
            "analysis_rounds": limits.analysis_rounds,
            "execution_submissions": limits.execution_submissions,
            "finalization_reserve_seconds": limits.finalization_reserve_seconds,
            "named": family in NAMED_FAMILIES,
        })
    return out


@dataclass(frozen=True)
class Verdict:
    """One turn's envelope, and the reason for it."""

    family: str
    analytical: bool
    reason: str
    signals: tuple[str, ...]

    @property
    def limits(self) -> Any:
        return limits_for_family(self.family)

    def to_dict(self) -> dict[str, Any]:
        limits = self.limits
        return {"family": self.family, "analytical": self.analytical,
                "reason": self.reason, "signals": list(self.signals),
                "deadline_seconds": limits.deadline_seconds,
                "spend_ceiling_usd": limits.spend_ceiling_usd}


# ---- the lexical signals ------------------------------------------------

#: Words that only appear in a question about the book's numbers. Kept short
#: on purpose: this is not intent classification, it is one bit, and the
#: thread context below carries most of the weight.
_MEASURE = (
    r"ecl", r"expected credit loss", r"ead", r"exposure at default",
    r"exposure", r"provision(?:s|ing)?", r"coverage", r"stage [123]",
    r"staging", r"sicr", r"impairment", r"gross carrying", r"drawn",
    r"limit utilisation", r"utilization", r"pd\b", r"lgd\b", r"eir\b",
    r"delinquen\w*", r"arrears", r"npl", r"default rate", r"write[- ]?off",
    r"cure rate", r"roll[- ]?rate", r"vintage", r"covenant", r"collateral",
    r"ltv\b", r"dscr", r"watchlist", r"watch list", r"rating", r"downgrade",
    r"upgrade", r"migration", r"concentration", r"portfolio", r"book",
    r"balance", r"facilit(?:y|ies)", r"borrower", r"obligor", r"customer",
    r"account",
)
_ANALYTICAL_VERB = (
    r"how much", r"how many", r"what is the", r"what are the", r"break ?down",
    r"broken down", r"by sector", r"by product", r"by segment", r"by region",
    r"by stage", r"top \d+", r"bottom \d+", r"largest", r"smallest",
    r"highest", r"lowest", r"worst", r"best", r"trend", r"trending",
    r"compare", r"comparison", r"versus", r"\bvs\b", r"movement", r"change",
    r"increase", r"decrease", r"deteriorat\w*", r"improv\w*", r"driv\w*",
    r"building", r"growing", r"rising", r"falling", r"share of",
    r"percentage of", r"total", r"average", r"median", r"distribution",
    r"chart", r"table", r"export",
)
_PERIOD = (
    r"\b20\d\d-\d\d\b", r"\b20\d\dq[1-4]\b", r"latest month",
    r"last month", r"this month", r"month on month", r"year on year",
    r"\bmom\b", r"\byoy\b", r"since \w+", r"over the last \w+",
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec",
)
#: Questions about the PRODUCT. Present so that "what can you do" in a thread
#: that once ran an analysis is not charged the analysis allowance for a
#: two-sentence answer -- but only when nothing analytical is also present.
_PRODUCT = (
    r"who are you", r"what are you", r"what can you do", r"what do you do",
    r"how do you work", r"how does .{0,20}work", r"what is creditprobe",
    r"creditprobe", r"cockpit", r"help me", r"^help\b", r"your capabilit\w*",
    r"what data do you have", r"which books", r"what books",
)


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def _mode_is_deep(mode: Any) -> bool:
    return str(mode or "").strip().lower() == DEEP


def classify(*, question: str, mode: Any = "standard",
             seeded: bool = False,
             prior_turns: list[dict[str, Any]] | None = None,
             catalog: Any = None) -> Verdict:
    """Which envelope this turn starts in, decided from what is knowable.

    `seeded` means the thread was opened from an attention card. `prior_turns`
    are this thread's completed turns, newest last, in `RunStore.recent_turns`
    shape.
    """
    text = " " + re.sub(r"\s+", " ", str(question or "")).strip().lower() + " "
    deep = _mode_is_deep(mode)
    signals: list[str] = []

    if seeded:
        signals.append("thread_opened_from_an_attention_card")
    for turn in list(prior_turns or []):
        answer = turn.get("answer") or {}
        intent = answer.get("intent") or {}
        if str(intent.get("query_mode") or "") == "DATA_ANALYSIS" \
                or answer.get("executed") or answer.get("evidence_bound"):
            signals.append("an_earlier_turn_in_this_thread_ran_an_analysis")
            break

    measures = _hits(text, _MEASURE)
    verbs = _hits(text, _ANALYTICAL_VERB)
    periods = _hits(text, _PERIOD)
    if measures:
        signals.append("the_question_names_a_measure")
    if verbs:
        signals.append("the_question_asks_for_a_figure_or_a_comparison")
    if periods:
        signals.append("the_question_names_a_period")
    if catalog is not None and _names_a_relation(text, catalog):
        signals.append("the_question_names_a_relation_of_this_book")

    product = _hits(text, _PRODUCT)

    # A pure product question is a product question however analytical the
    # rest of the thread was: "what can you do?" does not become a
    # hundred-and-twenty-second turn because the previous one ran SQL.
    if product and not (measures or verbs or periods):
        return Verdict(
            family=PRODUCT_HELP_DEEP if deep else PRODUCT_HELP_STANDARD,
            analytical=False,
            reason=("The question asks about CreditProbe itself and names no "
                    "measure, figure or period."),
            signals=("the_question_asks_about_the_product",))

    if signals:
        return Verdict(
            family=DATA_ANALYSIS_DEEP if deep else DATA_ANALYSIS_STANDARD,
            analytical=True,
            reason=_reason(signals),
            signals=tuple(dict.fromkeys(signals)))

    # Nothing points either way. The tight allowance, and the declaration
    # path widens it the moment the analyst says otherwise -- which is the
    # behaviour every turn had before this module existed.
    return Verdict(
        family=PRODUCT_HELP_DEEP if deep else PRODUCT_HELP_STANDARD,
        analytical=False,
        reason=("Nothing in the question or the thread says this is a "
                "question about the book's numbers. The allowance widens on "
                "the analyst's declaration if it is."),
        signals=())


_REASONS = {
    "thread_opened_from_an_attention_card":
        "this conversation was opened from an attention card, so it is "
        "about a figure the dashboard already showed",
    "an_earlier_turn_in_this_thread_ran_an_analysis":
        "an earlier turn in this conversation ran an analysis",
    "the_question_names_a_measure":
        "the question names a measure this book records",
    "the_question_asks_for_a_figure_or_a_comparison":
        "the question asks for a figure or a comparison",
    "the_question_names_a_period":
        "the question names a period",
    "the_question_names_a_relation_of_this_book":
        "the question names a relation of this book",
}


def _reason(signals: list[str]) -> str:
    named = [_REASONS[s] for s in dict.fromkeys(signals) if s in _REASONS]
    if not named:
        return "This turn reads as a question about the book's numbers."
    if len(named) == 1:
        body = named[0]
    else:
        body = ", ".join(named[:-1]) + f" and {named[-1]}"
    return f"Read as a question about the book's numbers because {body}."


def _names_a_relation(text: str, catalog: Any) -> bool:
    try:
        relations = catalog.relations()
    except Exception:  # noqa: BLE001 - a catalogue that cannot be read
        return False
    for relation in relations:
        words = [w for w in re.split(r"[_\W]+", str(relation).lower())
                 if len(w) > 4]
        if words and all(w in text for w in words):
            return True
    return False


def for_request(*, store: Any, thread_id: str, question: str,
                mode: Any = "standard", catalog: Any = None) -> Verdict:
    """The verdict for one accepted request, read from the durable store.

    Called at ACCEPT time to stamp the run's deadline and again by the worker
    to open its ledger. Both read the same thread and the same question, so
    both reach the same family; nothing is persisted that could disagree with
    itself.
    """
    seeded = False
    turns: list[dict[str, Any]] = []
    try:
        seeded = bool(store.thread_context(thread_id))
    except Exception:  # noqa: BLE001 - an unreadable thread is not seeded
        seeded = False
    try:
        turns = list(store.recent_turns(thread_id, 3) or [])
    except Exception:  # noqa: BLE001
        turns = []
    return classify(question=question, mode=mode, seeded=seeded,
                    prior_turns=turns, catalog=catalog)


__all__ = ["DATA_ANALYSIS_DEEP", "DATA_ANALYSIS_STANDARD", "FAMILIES",
           "NAMED_FAMILIES", "PRODUCT_HELP_DEEP", "PRODUCT_HELP_STANDARD",
           "Verdict", "classify", "for_request", "limits_for_family",
           "policy"]
