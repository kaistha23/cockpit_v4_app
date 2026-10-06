"""
Product knowledge: a small synopsis always, the detail on request.

Why not attach the pack
-----------------------
The reviewed pack is ~9,000 tokens. Attaching it to every question would put
the entire product deck in front of a request about Stage 2 exposure, which is
the V3 mistake in a new costume: paying for the whole domain because the
question mentioned the product's name.

So two layers:

  * a compact SYNOPSIS in the starting context — positioning, the seven
    modules in one line each, the arc, and the boundaries. Enough to answer
    "Who are you?" or "What is CreditProbe?" well, in ONE generation.

  * `inspect_product_knowledge`, which returns the sections a specific
    question needs — TAC, the four layers, one module in full, or how two
    modules differ.

CreditProbe retrieves. Opus writes. Nothing here turns a retrieved section
into a canned answer: the sections are facts with slide provenance, and the
analyst decides what to say with them.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PACK_PATH = Path(__file__).resolve().parent / "product_knowledge.json"

#: Retrieval topics the analyst can name. Anything else falls back to a
#: keyword match over the pack.
TOPICS: tuple[str, ...] = (
    "positioning", "audience", "arc", "modules", "cockpit", "early_warning",
    "tac", "layers", "what_if", "scorecard_validation", "playbook", "lenses",
    "planner", "supporting", "relationships", "boundaries", "governance",
    "examples")

MAX_SECTIONS = 6

#: Topics the always-on synopsis already carries in full enough shape to
#: answer from. Positioning, the arc, who it is for and the problem, the seven
#: functionalities with their one-liners and ownership, and the boundaries.
SYNOPSIS_TOPICS: frozenset[str] = frozenset({
    "positioning", "arc", "audience", "modules", "boundaries", "governance"})

#: Everything else. Naming one of these is what makes a question worth a
#: retrieval: a module in full, TAC, the four layers, how two modules differ,
#: what a supporting capability actually does, or a worked example.
DEEP_TOPICS: frozenset[str] = frozenset(TOPICS) - SYNOPSIS_TOPICS


@lru_cache(maxsize=1)
def pack() -> dict[str, Any]:
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def version() -> str:
    return str(pack().get("pack_version", "unknown"))


def _module(module_id: str) -> dict[str, Any] | None:
    return next((m for m in pack()["modules"] if m["id"] == module_id), None)


# ---- the always-present synopsis ---------------------------------------

def synopsis() -> dict[str, Any]:
    """The compact product facts carried in every Cockpit request.

    Deliberately substantive rather than a stub: a question like "Who are
    you?" must be answerable well from this alone, in one generation. It is
    still an order of magnitude smaller than the pack.
    """
    doc = pack()
    positioning = doc["positioning"]
    audience = doc["audience"]
    return {
        "product": "CreditProbe AI",
        "pack_version": doc["pack_version"],
        "headline": positioning["headline"],
        "distinction": positioning["distinction"],
        "arc": [f"{stage} — {positioning['arc_questions'][stage]}"
                for stage in positioning["arc"]],
        "arc_note": positioning["arc_note"],
        "written_for": audience["roles"],
        "the_problem": audience["the_real_problem"],
        "questions_users_bring": audience["questions_a_cro_asks"],
        "seven_functionalities": [
            {"name": m["name"], "does": m["one_line"], "owns": m["owns"]}
            for m in doc["modules"]],
        "supporting_capabilities": [
            c["name"] for c in doc["supporting_capabilities"]],
        "never_claim": doc["boundaries"]["never_claim"],
        "demo_data": doc["boundaries"]["demo_data"],
        "more_detail": (
            "Call inspect_product_knowledge for a module in full, for TAC, "
            "for the four Early Warning intelligence layers, for how two "
            "modules differ, or for worked examples with their slide "
            "references."),
    }


# ---- coverage: is a retrieval worth a generation? ----------------------

#: What `coverage()` returns in `level`.
COVERAGE_SYNOPSIS = "synopsis"
COVERAGE_RETRIEVAL = "retrieval"


def coverage(question: str) -> dict[str, Any]:
    """Does the always-on synopsis already cover this question?

    This is the whole of the one-generation policy, and it is deliberately
    NEGATIVE: a question needs `inspect_product_knowledge` only when it NAMES
    a topic whose detail the synopsis does not carry. Nothing here has to
    recognise a broad product question, which is what makes it survive "who r
    u", "CreditProbe kya karta hai?" and "ما هو CreditProbe" without a
    language model, a translation step or a list of phrasings to maintain.

    The deep topics are proper nouns — Cockpit, TAC, Playbook, Lenses,
    What-If, Scorecard Validation, the four layers — and those survive
    translation and misspelling far better than the question around them.

    Getting it wrong costs nothing that is not already being paid: the tool
    is restored for every action after the first, so the worst case is the
    two generations a product question costs today.
    """
    named = [t for t in topics_for(question) if t in DEEP_TOPICS]
    if named:
        return {
            "level": COVERAGE_RETRIEVAL,
            "deep_topics_named": named,
            "reason": ("The question names product detail the synopsis does "
                       "not carry."),
        }
    return {
        "level": COVERAGE_SYNOPSIS,
        "deep_topics_named": [],
        "reason": ("No product topic beyond what the synopsis in this "
                   "context already carries."),
    }


# ---- retrieval ----------------------------------------------------------

def _module_section(module: dict[str, Any], *, full: bool) -> dict[str, Any]:
    section = {
        "topic": module["id"],
        "title": module["name"],
        "slide": module["slide"],
        "one_line": module["one_line"],
        "three_beats": module["three_beats"],
        "four_d": module["four_d"],
        "why_it_matters": module["why_it_matters"],
        "produces": module.get("produces", []),
        "owns": module["owns"],
        "boundary": module["boundary"],
        "example_questions": module["example_questions"],
    }
    if not full:
        return section
    for key in ("how_it_works", "core_principle", "question_types",
                "brings_together", "tracks", "typical_measures",
                "monitoring_flow", "does_not_own", "worked_example",
                "also_slides"):
        if module.get(key):
            section[key] = module[key]
    return section


def _relationship_sections(ids: set[str]) -> list[dict[str, Any]]:
    names = {m["id"]: m["name"] for m in pack()["modules"]}
    out = []
    for rel in pack()["relationships"]:
        between = set(rel["between"])
        # Return a relationship only when the question touches BOTH sides.
        if len(ids & between) >= 2 or (ids & between and len(ids) == 1
                                       and len(between) == 2):
            out.append({
                "topic": "relationships",
                "title": " and ".join(names.get(x, x) for x in rel["between"]),
                "difference": rel.get("difference", ""),
                "together": rel.get("together", ""),
            })
    return out


#: Words that point at a topic. Deterministic, inspectable, and easy to
#: extend when the deck gains a section.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cockpit": ("cockpit",),
    "early_warning": ("early warning", "ews", "deterioration", "alert",
                      "escalation"),
    "tac": ("tac", "trigger", "accelerator", "classifier"),
    # The four layers, by the names the deck gives them. "External
    # intelligence" is a layer, not a supporting capability, and a question
    # naming one must retrieve the section that defines it.
    "layers": ("intelligence layer", "four layer", "layer 1", "layer 2",
               "layer 3", "layer 4", "behavioural intelligence",
               "external intelligence", "credit and financial fundamentals",
               "graph and relationship intelligence", "relationship "
               "intelligence"),
    "what_if": ("what-if", "what if", "scenario", "stress", "shock",
                "downside", "simulate"),
    "scorecard_validation": ("scorecard", "validation", "model health",
                             "ks", "calibration", "discrimination"),
    "playbook": ("playbook", "committee", "pack", "decision question"),
    "lenses": ("lens", "lenses", "dashboard", "kpi", "reporting"),
    "planner": ("planner", "project", "workstream", "dependency", "milestone",
                "blocker", "overdue"),
    "supporting": ("data builder", "graph data", "borrower 360",
                   "root-cause", "root cause", "action matrix",
                   "escalation matrix", "transportable", "workflow"),
    "arc": ("detect", "diagnose", "decide", "drive alignment", "journey"),
    "audience": ("cro", "credit officer", "head of credit", "problem",
                 "why would", "who is it for"),
    "boundaries": ("automatic", "autonomous", "approve", "replace",
                   "decide by itself", "demo data", "real data", "synthetic"),
    "positioning": ("what is creditprobe", "who are you", "what can you do",
                    "overview", "seven"),
}

_MODULE_TOPICS = {"cockpit", "early_warning", "what_if",
                  "scorecard_validation", "playbook", "lenses", "planner"}


@lru_cache(maxsize=None)
def _keyword_pattern(word: str) -> re.Pattern[str]:
    """A keyword matcher that respects word boundaries.

    Plain substring matching pulled "kpi" out of the middle of "cockpit" and
    answered a question about Cockpit and What-If with a section on Lenses.
    A retrieval layer that quietly adds an unrelated module is worse than one
    that returns nothing: the analyst writes about it.

    The boundary is on the LEFT only, so "layer" still matches "layers" and
    "automatic" still matches "automatically". Requiring a boundary on both
    sides made the plural forms — which is how people actually ask — miss.
    """
    return re.compile(rf"\b{re.escape(word.strip())}")


def topics_for(query: str) -> list[str]:
    """Which sections a question is asking about. Deterministic."""
    text = re.sub(r"\s+", " ", (query or "").lower()).strip()
    found: list[str] = []
    for topic, words in _KEYWORDS.items():
        if any(_keyword_pattern(word).search(text) for word in words):
            found.append(topic)
    return found


def retrieve(*, query: str = "", topics: tuple[str, ...] = (),
             detail: str = "standard") -> dict[str, Any]:
    """Return the requested product-knowledge sections, with provenance."""
    doc = pack()
    wanted: list[str] = [t for t in topics if t in TOPICS]
    if not wanted:
        wanted = topics_for(query)
    if not wanted:
        wanted = ["positioning", "modules"]

    full = detail == "full"
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(section: dict[str, Any]) -> None:
        key = f"{section['topic']}:{section.get('title', '')}"
        if key not in seen:
            seen.add(key)
            sections.append(section)

    module_ids = {t for t in wanted if t in _MODULE_TOPICS}

    for topic in wanted:
        if topic in _MODULE_TOPICS:
            module = _module(topic)
            if module:
                add(_module_section(module, full=full))
        elif topic == "tac":
            early = _module("early_warning")
            if early and early.get("tac"):
                add({"topic": "tac", "title": "TAC",
                     "slide": early["tac"]["slide"], **early["tac"]})
                module_ids.add("early_warning")
        elif topic == "layers":
            early = _module("early_warning")
            if early and early.get("layers"):
                add({"topic": "layers",
                     "title": "The four Early Warning intelligence layers",
                     "slide": early["layers"]["slide"],
                     **early["layers"]})
                module_ids.add("early_warning")
        elif topic in ("positioning", "arc"):
            add({"topic": "positioning", "title": "Positioning",
                 "slide": doc["positioning"]["slide"],
                 **doc["positioning"]})
        elif topic == "audience":
            add({"topic": "audience",
                 "title": "Who it is for, and the problem",
                 "slide": doc["audience"]["slide"], **doc["audience"]})
        elif topic == "modules":
            add({"topic": "modules", "title": "The seven functionalities",
                 "slide": 3,
                 "items": [{"name": m["name"], "slide": m["slide"],
                            "one_line": m["one_line"],
                            "three_beats": m["three_beats"],
                            "owns": m["owns"]}
                           for m in doc["modules"]]})
        elif topic == "supporting":
            add({"topic": "supporting",
                 "title": "Supporting capabilities", "slide": 2,
                 "note": ("Platform capabilities from the deck. Not counted "
                          "among the seven main functionalities."),
                 "items": doc["supporting_capabilities"]})
        elif topic in ("boundaries", "governance"):
            add({"topic": "boundaries",
                 "title": "Boundaries and governance",
                 **doc["boundaries"]})
        elif topic == "relationships":
            module_ids |= _MODULE_TOPICS
        elif topic == "examples":
            for module in doc["modules"]:
                if module.get("worked_example"):
                    add({"topic": "examples",
                         "title": f"{module['name']} worked example",
                         "slide": module["slide"],
                         **module["worked_example"]})

    for section in _relationship_sections(module_ids):
        add(section)

    truncated = len(sections) > MAX_SECTIONS
    return {
        "status": "ok",
        "pack_version": doc["pack_version"],
        "source": doc["source"]["document"],
        "topics_returned": wanted,
        "sections": sections[:MAX_SECTIONS],
        **({"omitted_sections": len(sections) - MAX_SECTIONS,
            "omitted_note": ("Ask for fewer topics, or name the one you "
                             "need. Nothing was abbreviated.")}
           if truncated else {}),
        "usage_note": (
            "These are product facts with slide provenance. Figures inside a "
            "worked example are illustrations of the product, never current "
            "portfolio values — for anything about this book, execute an "
            "analysis."),
        "current_architecture_note": (
            "The deck's architecture slide describes a SUPERSEDED design and "
            "is deliberately not retrievable. Do not describe CreditProbe's "
            "current runtime from it."),
    }


__all__ = ["COVERAGE_RETRIEVAL", "COVERAGE_SYNOPSIS", "DEEP_TOPICS",
           "MAX_SECTIONS", "PACK_PATH", "SYNOPSIS_TOPICS", "TOPICS",
           "coverage", "pack", "retrieve", "synopsis", "topics_for",
           "version"]
