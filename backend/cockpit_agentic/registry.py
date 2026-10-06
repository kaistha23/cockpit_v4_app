"""
Which functionality owns what. Specification section 6.1.

Descriptions come from the application, not from a model
--------------------------------------------------------
Every entry below was checked against `frontend/src/lib/navigation.ts` and the
backend packages at the base commit. Section 6.1 requires the registry to carry
the ACTUAL UI label and a VERIFIED navigation route, and `verify_routes` asserts
that against the file rather than trusting this docstring.

Two places where the specification's names and the product's differ, and the
product wins:

* **Credit Scoring has no module.** There is no `/credit-scoring` route and no
  workflow that assigns a borrower a new score. `backend/scorecard/` builds and
  fits scorecards and `/scorecard-validation` validates them; neither
  originates a score for a borrower. Section 6.1 anticipates exactly this and
  says to keep the ownership exclusion and state that the correct workflow is
  unavailable -- so the entry is present, `enabled=False`, with no route and an
  honest reason. Cockpit still refuses to generate a score; it just cannot
  promise a screen that would.
* **What-if Analysis ships as "Stress Testing".** The route is `/stress` and
  the label a user sees is "Stress Testing". Referring someone to "What-if
  Analysis" would send them looking for a menu item that is not there.

Ownership is about the ACTION, not the vocabulary
-------------------------------------------------
"Show the stored rating" and "calculate a new rating" name the same noun and
belong to different functionalities. The `examples` and `counterexamples` on
each entry are pairs like that, because a score cannot separate them and a
boundary drawn on keywords would put both in the same place.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.cockpit_agentic import DOMAIN, REGISTRY_VERSION
from backend.cockpit_agentic.contracts import FunctionalityRegistryEntry

COCKPIT = "cockpit"
EWS = "ews"
CREDIT_SCORING = "credit_scoring"
SCORECARD_VALIDATION = "scorecard_validation"
WHAT_IF = "what_if"
LENSES = "lenses"

#: The six the gate scores. Every one of them, every time -- section 6.2.
FUNCTIONALITY_IDS: tuple[str, ...] = (COCKPIT, EWS, CREDIT_SCORING,
                                      SCORECARD_VALIDATION, WHAT_IF, LENSES)

ENTRIES: tuple[FunctionalityRegistryEntry, ...] = (
    FunctionalityRegistryEntry(
        functionality_id=COCKPIT,
        ui_label="Cockpit",
        description=(
            "Ask a question in plain language about the bank's stored "
            "twenty-quarter corporate credit dataset. Queries, compares and "
            "explains what was recorded."),
        owns=(
            "Querying, comparing and explaining data already stored in the "
            "twenty-quarter corporate domain",
            "Historical IFRS 9 results, ECL, PD, LGD, EAD and stage as they "
            "were recorded",
            "Stored risk ratings and their recorded history and reasons",
            "The forty financial ratios and the statements behind them",
            "Covenant tests, breaches, waivers and headroom as recorded",
            "Collateral valuations, haircuts and allocations as recorded",
            "Recorded macroeconomic vintages and their forecast horizons",
        ),
        excludes=(
            "Generating a new credit score or rating",
            "Producing early-warning alerts, signals or watchlist priorities",
            "Running a new shock, stress or hypothetical parameter change",
            "Validating or recalibrating a scoring model",
            "Interpreting documents or reports",
            "Any data outside the twenty-quarter corporate domain",
        ),
        supported_actions=("query", "compare", "aggregate", "explain",
                           "describe_coverage", "trend"),
        examples=(
            "Show this borrower's stored rating history over the last eight "
            "quarters.",
            "Compare DSCR and recorded covenant breaches over four quarters.",
            "Compare the two recorded quarterly ECL values and explain the "
            "drivers the evidence supports.",
            "Compare the already-stored baseline and downside IFRS 9 outputs.",
            "Which sectors have the highest stage 2 exposure this quarter?",
        ),
        counterexamples=(
            "Assign this borrower a new credit score.",
            "Why did its early-warning alert score increase?",
            "Increase PD by 20% and recalculate ECL.",
            "Validate the scorecard's discrimination and calibration.",
            "Summarise the attached credit memo.",
        ),
        data_domain=DOMAIN,
        enabled=True,
        route="/",
    ),
    FunctionalityRegistryEntry(
        functionality_id=EWS,
        ui_label="Early Warning",
        description=(
            "Forward Risk Signal and Early Warning Signals: a factor-based "
            "estimate of the chance a facility moves to a worse IFRS 9 stage "
            "next quarter, and thirty-four governed conditions the book is "
            "watched for, borrower by borrower."),
        owns=(
            "Early-warning alerts, signals and scores",
            "The drivers of an early-warning score",
            "Watchlist prioritisation and the early-warning investigation "
            "workflow",
        ),
        excludes=(
            "Reporting stored historical data, which is Cockpit's",
        ),
        supported_actions=("score", "prioritise", "investigate_alert",
                           "explain_signal"),
        examples=(
            "Why did this borrower's early-warning score increase?",
            "Which borrowers should be on the watchlist this quarter?",
            "What drove the forward risk signal for this facility?",
        ),
        counterexamples=(
            "Compare this borrower's DSCR over four quarters -- a historical "
            "comparison is not an early-warning request merely because it "
            "could inform risk monitoring.",
        ),
        data_domain="early_warning",
        enabled=True,
        route="/early-warning",
    ),
    FunctionalityRegistryEntry(
        functionality_id=CREDIT_SCORING,
        ui_label="Credit Scoring",
        description=(
            "Calculating, assigning or updating a borrower's credit score or "
            "rating."),
        owns=(
            "Calculating a new credit score or rating for a borrower",
            "Updating or overriding an assigned score",
            "Operating a credit-scoring workflow",
        ),
        excludes=(
            "Reading a stored rating, which is Cockpit's",
            "Validating an existing scorecard, which belongs to Scorecard "
            "Validation and is a different job",
        ),
        supported_actions=("score_borrower", "assign_rating"),
        examples=(
            "Assign this borrower a credit score.",
            "What rating would this borrower get on current financials?",
            "Re-score the portfolio on the updated model.",
        ),
        counterexamples=(
            "Show the borrower's stored rating -- that is Cockpit reading "
            "recorded data.",
        ),
        data_domain="",
        enabled=False,
        unavailable_reason=(
            "This deployment has no credit-scoring workflow. Scorecards are "
            "built and fitted in Analysis Studio and monitored in Scorecard "
            "Validation, but neither assigns a borrower a new score. Cockpit "
            "cannot generate one either: it reads stored ratings only."),
    ),
    FunctionalityRegistryEntry(
        functionality_id=SCORECARD_VALIDATION,
        ui_label="Scorecard Validation",
        description=(
            "Governed monitoring and validation of the retail application and "
            "behavioural scorecards: discrimination, calibration, stability, "
            "variable diagnostics and implementation, each against an approved "
            "limit that says where it came from."),
        owns=(
            "Assessing or testing an existing scorecard or scoring model",
            "Discrimination, calibration, stability and variable diagnostics",
        ),
        excludes=(
            "Originating a new score -- this module validates scorecards, it "
            "does not run them for a borrower",
            "Reporting stored ratings, which is Cockpit's",
        ),
        supported_actions=("validate_model", "test_discrimination",
                           "test_calibration", "test_stability"),
        examples=(
            "Validate the scorecard's discrimination and calibration.",
            "Has the application scorecard's population stability drifted?",
        ),
        counterexamples=(
            "Assign this borrower a score -- validation is not origination.",
        ),
        data_domain="scorecard",
        enabled=True,
        route="/scorecard-validation",
    ),
    FunctionalityRegistryEntry(
        functionality_id=WHAT_IF,
        # The label a user actually sees. Referring them to "What-if Analysis"
        # would send them looking for a menu item that is not there.
        ui_label="Stress Testing",
        description=(
            "Named, versioned management scenarios applied to the portfolio, "
            "with comparison. This is where a new shock or hypothetical "
            "parameter change is run."),
        owns=(
            "New user-requested shocks and hypothetical parameter changes",
            "Alternative and stress scenarios and their simulated "
            "consequences",
            "Comparing a simulated outcome against the base",
        ),
        excludes=(
            "Comparing already-stored historical results, which is Cockpit's",
            "Comparing stored IFRS 9 scenario outputs that the source already "
            "produced -- that is reading, not simulating",
        ),
        supported_actions=("apply_scenario", "shock_parameter",
                           "simulate", "compare_scenarios"),
        examples=(
            "Increase PD by 20% and recalculate ECL.",
            "What happens to the book under a 300 basis point rate shock?",
            "Stress the Real Estate portfolio.",
        ),
        counterexamples=(
            "Compare the stored baseline and downside IFRS 9 outputs -- those "
            "were already computed and recorded, so reading them is Cockpit.",
        ),
        data_domain="stress",
        enabled=True,
        route="/stress",
    ),
    FunctionalityRegistryEntry(
        functionality_id=LENSES,
        ui_label="Lenses",
        description=(
            "Live dashboards you build by describing them. Each tile is a "
            "certified analysis with its own Trace."),
        owns=(
            "The Lenses dashboard workflow and its published views",
            "Building or reading a Lens",
        ),
        excludes=(
            "Answering an analytical question about the Cockpit domain, which "
            "is Cockpit's",
        ),
        supported_actions=("build_lens", "open_lens", "publish_lens"),
        examples=(
            "Build me a dashboard of sector exposure and stage migration.",
            "Open the CRO lens.",
        ),
        counterexamples=(
            "What is total stage 2 exposure this quarter? -- that is a "
            "question, not a dashboard.",
        ),
        data_domain="lenses",
        enabled=True,
        route="/lenses",
    ),
)

BY_ID: dict[str, FunctionalityRegistryEntry] = {
    e.functionality_id: e for e in ENTRIES}

assert set(BY_ID) == set(FUNCTIONALITY_IDS)


def entry(functionality_id: str) -> FunctionalityRegistryEntry:
    try:
        return BY_ID[str(functionality_id)]
    except KeyError:
        raise LookupError(
            f"{functionality_id!r} is not a registered functionality. The "
            f"registry holds: {', '.join(FUNCTIONALITY_IDS)}.") from None


def grounding_facts() -> tuple[str, ...]:
    """Everything the registry actually documents, flattened.

    Section 10: a statement about how CreditProbe works must come from here.
    Where a product-specific mechanism is not in this tuple, the honest answer
    is that it cannot be verified from the configured product information --
    not a description of how such a system usually works, which is the shape
    an invented answer takes.
    """
    facts: list[str] = []
    for item in ENTRIES:
        facts.append(item.description)
        facts.extend(item.owns)
        facts.extend(item.excludes)
        facts.extend(item.supported_actions)
        facts.extend(item.examples)
        facts.extend(item.counterexamples)
        facts.append(item.ui_label)
    return tuple(facts)


def compact() -> dict[str, Any]:
    """What goes to Opus. Descriptions only -- never another module's data.

    Section 9.6: functionality descriptions are metadata, not a gateway. This
    payload contains no row, field, search result or tool from any domain but
    Cockpit's.
    """
    return {
        "registry_version": REGISTRY_VERSION,
        "functionalities": [e.compact() for e in ENTRIES],
        "rule": (
            "Score every entry independently from 0 to 100. These are "
            "suitability scores, not probabilities, and they do not sum to "
            "100. Proceed with Cockpit ONLY if Cockpit is the unique highest "
            "scorer AND the requested action is inside its ownership. A tie, "
            "an unresolved ambiguity, or a requested action that appears in "
            "another functionality's 'owns' list means clarify or refer -- a "
            "score never overrides an explicit out-of-scope action."),
        "coverage_rule": (
            "Do NOT refer an otherwise in-scope question elsewhere merely "
            "because the selected quarter has no data. That is a Cockpit "
            "data-coverage limitation and you should report it as one."),
        "referral_rule": (
            "A referral executes no SQL and no Python, fetches nothing from "
            "the other module, and starts nothing. Offer up to three "
            "alternative questions that Cockpit genuinely CAN answer from the "
            "fields and periods in this catalogue, each naming the fields it "
            "needs. Do not rename an excluded task as a Cockpit one: an "
            "excluded 'risk score' does not become permissible as a Cockpit "
            "'risk index'. If fewer than three genuine alternatives exist, "
            "give fewer; for a wholly unrelated request, give none."),
        "mixed_scope_rule": (
            "If part of the request belongs elsewhere, say which part, and "
            "offer an explicit Cockpit-only reformulation of the rest. Do not "
            "silently drop the excluded part and report the whole question "
            "answered."),
    }


# ------------------------------------------------------- route verification

_NAV = Path(__file__).resolve().parents[2] / "frontend/src/lib/navigation.ts"


def verify_routes() -> dict[str, Any]:
    """Check every enabled entry's route against the application.

    A referral with a dead link is worse than a referral with none: it sends
    someone to a 404 with the bank's confidence behind it. So the routes are
    asserted against `navigation.ts` rather than asserted in prose.
    """
    if not _NAV.exists():
        return {"verified": False,
                "reason": f"{_NAV} is not present in this checkout"}
    source = _NAV.read_text()
    declared = set(re.findall(r'href:\s*"([^"]+)"', source))
    labels = dict(re.findall(
        r'href:\s*"([^"]+)",\s*\n\s*label:\s*"([^"]+)"', source))
    results: dict[str, Any] = {}
    for item in ENTRIES:
        if not item.enabled:
            results[item.functionality_id] = {
                "enabled": False, "route": None,
                "route_exists": None,
                "unavailable_reason": item.unavailable_reason}
            continue
        results[item.functionality_id] = {
            "enabled": True,
            "route": item.route,
            "route_exists": item.route in declared,
            "declared_label": labels.get(item.route, ""),
            "registry_label": item.ui_label,
            "label_matches": labels.get(item.route, "") == item.ui_label,
        }
    return {
        "verified": all(r.get("route_exists", True) is not False
                        for r in results.values()),
        "navigation_file": str(_NAV.relative_to(_NAV.parents[3])),
        "results": results,
    }


__all__ = ["BY_ID", "COCKPIT", "CREDIT_SCORING", "ENTRIES", "EWS",
           "FUNCTIONALITY_IDS", "LENSES", "SCORECARD_VALIDATION", "WHAT_IF",
           "compact", "entry", "verify_routes"]
