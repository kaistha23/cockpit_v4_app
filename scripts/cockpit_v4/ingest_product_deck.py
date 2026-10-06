#!/usr/bin/env python3
"""
Ingest the CreditProbe functionality deck into the Product Knowledge Pack.

Run ONCE per deck version, during development. The deck is never parsed at
request time and never attached to a user question: this writes a reviewed,
versioned pack that the runtime retrieves from selectively.

Two artifacts, one source:
    backend/cockpit_v4/product_knowledge.json   machine-readable, retrieved
    docs/product_knowledge/creditprobe_product_knowledge.md   human review

The Markdown is GENERATED from the JSON so the two cannot drift. Review the
Markdown; correct the JSON.

Usage:
    python3 scripts/cockpit_v4/ingest_product_deck.py --deck <path-to-pdf>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

JSON_OUT = ROOT / "backend" / "cockpit_v4" / "product_knowledge.json"
MD_OUT = ROOT / "docs" / "product_knowledge" / "creditprobe_product_knowledge.md"

PACK_VERSION = "2026-09-11.1"


def build(deck_name: str, deck_sha: str, pages: int) -> dict:
    return {
        "pack_version": PACK_VERSION,
        "ingested_on": date.today().isoformat(),
        "source": {
            "document": deck_name,
            "kind": "CreditProbe AI Functionality Deck",
            "sha256": deck_sha,
            "pages": pages,
            "note": ("The authoritative product story. Where an older product "
                     "document disagrees with this deck, this deck wins."),
        },
        "positioning": {
            "slide": 2,
            "headline": "An intelligent credit investigation layer for risk teams.",
            "tagline": "From risk signals to risk action.",
            "distinction": (
                "It does not answer questions about the credit book. It "
                "investigates it."),
            "arc": ["Detect", "Diagnose", "Decide", "Drive Alignment"],
            "arc_questions": {
                "Detect": "Where is risk building?",
                "Diagnose": "Why is it happening?",
                "Decide": "What should we do about it?",
                "Drive Alignment": "How do we align everyone around it?",
            },
            "plain_arc": ("See the risk → understand the why → choose the "
                          "response → mobilise the action"),
            "arc_note": (
                "A shared operating story across modules, not a rule "
                "assigning one module per stage."),
        },
        "audience": {
            "slide": 2,
            "roles": ["CRO", "Head of Credit", "senior credit officer",
                      "portfolio-risk leader", "model-risk leader"],
            "the_real_problem": (
                "The difficulty is rarely a shortage of information. It is "
                "that the evidence is fragmented, and connecting it takes "
                "longer than the decision allows."),
            "fragmented_across": [
                "exposures", "ECL and IFRS 9 staging", "PD, LGD and EAD",
                "borrower behaviour", "ratings", "financial statements",
                "financial ratios", "covenants", "collateral",
                "early-warning signals", "external events", "relationships",
                "stress assumptions", "model-validation evidence",
                "committee material", "actions and owners",
                "project dependencies"],
            "consequences": [
                "deterioration is identified too late",
                "too much time goes into assembling evidence rather than judging it",
                "signal is hard to separate from noise",
                "root cause stays unclear",
                "portfolio and borrower evidence sit apart",
                "emerging risk is disconnected from structural credit quality",
                "stress work is disconnected from current deterioration",
                "committee packs are assembled by hand",
                "actions and owners drift away from the evidence behind them",
                "dependencies and overdue work hide across plans and meetings"],
            "questions_a_cro_asks": [
                "Where is risk building?",
                "Why is it happening?",
                "Which borrowers matter?",
                "What happens under a downside scenario?",
                "What should the committee discuss?",
                "Who needs to act?"],
        },
        "modules": _modules(),
        "supporting_capabilities": _supporting(),
        "relationships": _relationships(),
        "boundaries": {
            "slides": [4, 8, 9, 10, 11, 12, 13],
            "never_claim": [
                "autonomous credit approval",
                "replacement of the credit officer",
                "guaranteed early detection",
                "guaranteed prediction of default",
                "autonomous model approval",
                "autonomous committee approval",
                "unrestricted access to every possible data source"],
            "governance": [
                "Human accountability and approval remain explicit.",
                "Limit changes are proposals subject to the bank's approval, "
                "escalated through the configured escalation matrix rather "
                "than decided autonomously. (slide 8)",
                "Reviewers approve any model change. (slide 10)",
                "Playbook organises material and drafts narrative; it does "
                "not invent committee approval. (slide 11)",
                "Users approve material changes to ownership, dates and the "
                "plan. (slide 13)",
                "Cockpit explains returned evidence; it does not invent the "
                "underlying financial calculation. (slide 4)"],
            "demo_data": (
                "This environment uses synthetic demonstration data, not a "
                "real bank portfolio. Figures in the product deck are "
                "illustrative examples of the product, never current "
                "portfolio values."),
        },
        "historical_architecture": {
            "slide": 14,
            "status": "HISTORICAL_ARCHITECTURE",
            "applies_to_current_runtime": False,
            "label": "NOT_CURRENT_V4_ARCHITECTURE",
            "summary": (
                "Slide 14 of the deck describes an earlier Cockpit request "
                "lifecycle: two Sonnet/Qwen preprocessing passes, a separate "
                "ownership-gate call, a separate analysis-plan call, a "
                "sufficiency-review call, a separate final-answer call, an "
                "answer-rewrite and a thread-summary call after delivery."),
            "why_excluded": (
                "The current Cockpit V4 runtime is a single continuing Opus "
                "analyst with governed tools. It has no Sonnet preprocessing, "
                "no separate ownership-gate call, no planning essay, no "
                "mandatory separate final-answer call and no "
                "summary-before-answer step. This section is retained for "
                "provenance only and must never be described as how the "
                "product works today."),
            "must_not_reintroduce": [
                "Sonnet preprocessing passes",
                "a separate ownership gate call",
                "full-context architecture",
                "a separate analysis-plan call",
                "a mandatory summary-after-answer flow"],
        },
    }


def _modules() -> list[dict]:
    return [
        {
            "id": "cockpit", "name": "Cockpit", "slide": 4,
            "one_line": "Interrogate the recorded credit book: what moved, why, and who to look at next.",
            "three_beats": ["Ask and spot changes", "Explain drivers",
                            "Prioritize follow-ups"],
            "four_d": ["Spot movement (EAD/ECL change)",
                       "Explain drivers (bridge + sector)",
                       "Prioritise reviews (rank borrowers)",
                       "Share / assign (follow-up owners)"],
            "why_it_matters": (
                "A senior credit officer can see that ECL moved without being "
                "able to say what moved it, which borrowers are behind it, or "
                "which of them is worth a review this week."),
            "how_it_works": (
                "Cockpit classifies the question, chooses the execution path "
                "it needs, and explains the evidence it gets back. Four "
                "question types: a definition and product help answer "
                "directly; a data operation runs SQL; an analysis runs SQL "
                "and Python together."),
            "question_types": [
                {"type": "Definition", "path": "plain response",
                 "example": "What is IFRS 9 staging?"},
                {"type": "Product help", "path": "plain response",
                 "example": "How do I run a What-If scenario?"},
                {"type": "Data operation", "path": "SQL",
                 "example": "Show Q2 EAD by sector."},
                {"type": "Analysis", "path": "SQL + Python",
                 "example": "Decompose the ECL movement."}],
            "produces": ["what changed", "what explains the change",
                         "what should happen next",
                         "evidence linked to executed queries"],
            "worked_example": {
                "question": "Decompose the ECL movement for me.",
                "scope": "Corporate portfolio Q1 → Q2 2026, SAR million",
                "what_changed": (
                    "ECL rose from SAR 240m to SAR 300m — up SAR 60m, or 25%. "
                    "Exposure grew 4.2% while ECL coverage moved from 2.0% to "
                    "2.4%."),
                "what_explains_it": (
                    "A sequential revaluation bridge — not an independently "
                    "validated attribution method. Construction contributes "
                    "60% of the increase."),
                "what_next": ["prioritise the construction borrowers "
                              "contributing most to the movement",
                              "review stage, rating and collateral evidence "
                              "for those borrowers",
                              "run the targeted construction downside "
                              "scenario and prepare committee review"],
                "label": "deck example, not live data"},
            "owns": ("historical and recorded corporate credit analysis, "
                     "product help, and stable credit and accounting "
                     "concepts"),
            "does_not_own": (
                "emerging deterioration workflow (Early Warning), "
                "prospective shocks (What-If), model validation (Scorecard "
                "Validation), committee packs (Playbook), dashboards "
                "(Lenses), delivery plans (AI Project Planner)"),
            "boundary": (
                "Cockpit explains the evidence it retrieved. It does not "
                "invent the underlying financial calculation."),
            "example_questions": [
                "What is total exposure at default by sector in the latest quarter?",
                "Which sectors saw the largest increase in Stage 2 exposure over the latest year?",
                "Decompose the ECL movement between the last two quarters.",
                "Which borrowers contribute most to the construction ECL increase?"],
        },
        {
            "id": "early_warning", "name": "Early Warning Analysis",
            "slide": 5,
            "also_slides": [6, 7, 8],
            "one_line": "Identify emerging deterioration early, size how serious it is, and escalate it to an owner.",
            "three_beats": ["Identify upcoming deterioration",
                            "Measure severity", "Trigger escalation"],
            "four_d": ["Emerging signals (new triggers)",
                       "Severity and context (score + band)",
                       "Proposed response (recommended action)",
                       "Escalation (routed to owner)"],
            "why_it_matters": (
                "Deterioration usually shows up in behaviour before it shows "
                "up in a rating. The difficulty is telling one weak signal "
                "apart from several worsening together, and knowing which "
                "borrower can least afford it."),
            "core_principle": (
                "Fresh signals drive the warning. Structural credit quality "
                "determines how seriously they are interpreted."),
            "tac": {
                "slide": 5,
                "summary": (
                    "TAC turns live behaviour into a context-aware risk "
                    "signal. It is applied across all four intelligence "
                    "layers."),
                "T": {"name": "Trigger", "question": "What changed?",
                      "detail": ("A change-based deviation from the "
                                 "customer's own normal, drawn from "
                                 "high-frequency data — not a static "
                                 "threshold."),
                      "examples": ["deposit or turnover decline",
                                   "utilisation spike or excess",
                                   "repayment deterioration",
                                   "abnormal transaction behaviour",
                                   "a newly observed covenant event"]},
                "A": {"name": "Accelerator",
                      "question": "How fast, how convincingly?",
                      "detail": ("Weighs the shape of the deterioration, not "
                                 "merely its presence. One weak signal is not "
                                 "several worsening together."),
                      "dimensions": ["magnitude", "velocity", "persistence",
                                     "recency", "repetition",
                                     "corroboration"]},
                "C": {"name": "Classifier",
                      "question": "Who is it happening to?",
                      "detail": ("Slow-moving structural quality, reviewed "
                                 "periodically rather than re-scored daily. "
                                 "The same behaviour is read differently in a "
                                 "vulnerable borrower."),
                      "inputs": ["rating", "DSCR", "financial ratios",
                                 "exposure and limit"]},
                "scoring": ("Conceptual and configurable: the TAC-adjusted "
                            "signal is approximately trigger × acceleration × "
                            "classifier."),
                "same_behaviour_different_risk": (
                    "A 30% decline in operating or deposit balance is a WATCH "
                    "for a borrower with a strong rating and comfortable "
                    "DSCR, and a HIGH CONCERN for one with a weak rating and "
                    "thin DSCR. Classifiers change the interpretation, not "
                    "the underlying observation."),
            },
            "layers": {
                "slide": 6,
                "summary": ("Four intelligence layers feed one TAC-based "
                            "engine. Every alert names its layer, its driver "
                            "and its evidence."),
                "items": [
                    {"layer": 1, "name": "Internal behavioural intelligence",
                     "role": "triggers",
                     "question": "What is changing inside the banking relationship?",
                     "examples": ["deposit balance decline",
                                  "account credits down",
                                  "turnover or volume fall",
                                  "utilisation spike", "sustained high usage",
                                  "excess over limit", "repayment delays",
                                  "returned payments"],
                     "note": "Detects live deterioration — the usual source of fresh triggers."},
                    {"layer": 2, "name": "Credit and financial fundamentals",
                     "role": "classifiers",
                     "question": "Who is the customer, and how vulnerable are they?",
                     "examples": ["rating and migration", "PD level and movement",
                                  "IFRS 9 stage and ECL", "exposure and limit",
                                  "collateral and LTV", "covenant compliance",
                                  "DSCR and leverage",
                                  "sector and group exposure"],
                     "note": "Structural credit quality calibrates severity — periodic, not daily."},
                    {"layer": 3, "name": "External intelligence",
                     "role": "triggers and evidence",
                     "question": "What is happening outside the bank's own data?",
                     "examples": ["exchange disclosures",
                                  "bankruptcy and litigation",
                                  "regulatory and sanctions",
                                  "rating downgrades",
                                  "market and spread moves",
                                  "contract loss or delay",
                                  "management and audit events",
                                  "macro and commodity shocks"],
                     "note": "Converts external events into traceable borrower-level signals.",
                     "pipeline": ["entity resolution — is this actually my borrower?",
                                  "event extraction — structured, not a keyword hit",
                                  "relevance and materiality — does it move cash flow or repayment?",
                                  "confidence and corroboration — tier 1, 2 or 3",
                                  "relationship mapping — supplier, customer, guarantor"],
                     "evidence_retained": ["source", "date", "event type",
                                           "confidence", "why it matters",
                                           "link to evidence"]},
                    {"layer": 4, "name": "Graph and relationship intelligence",
                     "role": "contagion",
                     "question": "Where can risk travel from?",
                     "examples": ["parent and group risk",
                                  "guarantor deterioration",
                                  "key supplier distress",
                                  "key customer distress",
                                  "project dependency",
                                  "ownership and directors",
                                  "related-party exposure",
                                  "cross-default links"],
                     "note": "Sees risk around the borrower, not only inside it."},
                ]},
            "produces": ["EWS score (0–100, configurable)",
                         "risk band (Low, Watch, High, Critical)",
                         "top drivers, ranked",
                         "direction of travel (improving or worsening)",
                         "reason codes", "evidence trail"],
            "worked_example": {
                "slide": 8,
                "summary": (
                    "Cedar Infrastructure moved from 68 to 88 — CRITICAL — as "
                    "four independent layers deteriorated together: a fresh "
                    "Layer 1 cash-flow signal, a Layer 2 context that was "
                    "already weak, a Layer 3 disclosed contract delay "
                    "corroborating the internal signal, and a Layer 4 "
                    "dependency on a supplier now in confirmed distress. The "
                    "combination, not any single signal, drives the severity."),
                "label": "deck example, not live data"},
            "boundary": (
                "Escalation and limit changes are proposals routed through the "
                "configured escalation matrix and approved by the bank — not "
                "decided autonomously. Early detection is not guaranteed."),
            "owns": "emerging deterioration and the warning and escalation workflow",
            "example_questions": [
                "What is TAC?",
                "What are the four Early Warning intelligence layers?",
                "Why is a borrower flagged High rather than Watch?",
                "Which customers moved into Critical since the last run?"],
        },
        {
            "id": "what_if", "name": "What-If Analysis", "slide": 9,
            "one_line": "Test a prospective shock and see what it would do to the portfolio.",
            "three_beats": ["Design scenarios", "Analyze portfolio impact",
                            "Assess likelihood"],
            "four_d": ["Locate vulnerability (which book)",
                       "Trace transmission (rating + collateral)",
                       "Compare impacts (baseline vs stress)",
                       "Prepare response (committee input)"],
            "why_it_matters": (
                "Knowing a sector is deteriorating is not the same as knowing "
                "what a further downgrade or a fall in collateral values "
                "would cost, or whether the scenario is plausible enough to "
                "act on."),
            "how_it_works": (
                "A scenario is defined explicitly — scope, baseline, shocks "
                "and horizon — and the impact is computed from configured "
                "rating mappings and collateral assumptions."),
            "produces": ["the scenario as defined",
                         "portfolio impact against baseline",
                         "a likelihood assessment, qualified"],
            "worked_example": {
                "request": ("Simulate a one-notch downgrade for construction "
                            "borrowers and a 20% fall in their property "
                            "collateral values. What is the portfolio impact, "
                            "and how likely is this over the next 12 months?"),
                "scenario": ("Scope construction, baseline Q2 2026, one-notch "
                             "rating shock, −20% collateral, 12-month horizon"),
                "impact": ("Portfolio ECL +SAR 80m (+26.7%); ECL/EAD 2.40% → "
                           "3.04%. Rating/PD +40, collateral/LGD +25, "
                           "interaction +15, not double-counted. EAD "
                           "unchanged; other sectors held flat as a "
                           "simplifying assumption."),
                "likelihood": ("Plausible as an adverse stress if migration "
                               "and collateral-market evidence support it. A "
                               "probability is not quantified from these "
                               "inputs alone."),
                "label": "deck example, not live data"},
            "boundary": (
                "The scenario horizon is distinct from the applicable ECL "
                "measurement horizon. Outputs follow configured mappings and "
                "assumptions — they are not automatic consequences of the "
                "shocks alone, and not every downgrade moves a borrower into "
                "Stage 2. A probability is not asserted without evidence."),
            "owns": "prospective and hypothetical shocks and scenarios",
            "example_questions": [
                "What happens if construction borrowers are downgraded one notch?",
                "What is the ECL impact of a 20% fall in property collateral?",
                "How does the stressed case compare with baseline?"],
        },
        {
            "id": "scorecard_validation", "name": "Scorecard Validation",
            "slide": 10,
            "one_line": "Assess whether a scorecard is still working, and diagnose what is wrong when it is not.",
            "three_beats": ["Check model health", "Diagnose weaknesses",
                            "Prepare validation outputs"],
            "four_d": ["Performance concern (weak KS)",
                       "Variable diagnosis (counterintuitive variable)",
                       "Test remediation (retrain, revalidate)",
                       "Validation report (findings pack)"],
            "why_it_matters": (
                "A model-risk leader needs to know not only that "
                "discrimination has weakened but which variable is behaving "
                "against expectation, and to produce validation evidence a "
                "reviewer will accept."),
            "produces": ["validation findings", "discrimination evidence",
                         "variable review", "remediation plan"],
            "worked_example": {
                "question": "What is the issue with my scorecard?",
                "finding": ("Discrimination is weak: KS = 22, below the "
                            "internal benchmark of 30. KS is a "
                            "22-percentage-point maximum separation between "
                            "cumulative default and non-default "
                            "distributions — not an accuracy of 22%. The "
                            "benchmark is a demo assumption, not a universal "
                            "regulatory threshold."),
                "diagnosis": ("Debt-to-income has a counterintuitive fitted "
                              "effect: higher DTI is reducing predicted "
                              "default risk, the opposite of the stated "
                              "business expectation."),
                "label": "deck example, not live data"},
            "boundary": (
                "A low aggregate metric does not prove which variable caused "
                "it. Coding, binning, missing-value treatment and sign "
                "conventions are checked first, and removing a variable is "
                "not guaranteed to improve the metric. Reviewers approve any "
                "model change."),
            "owns": "model-validation diagnostics",
            "example_questions": [
                "What is the issue with my scorecard?",
                "Is the model still discriminating well?",
                "Which variables behave against expectation?"],
        },
        {
            "id": "playbook", "name": "Playbook", "slide": 11,
            "one_line": "Turn analyses and evidence into a committee-ready narrative with the decisions named.",
            "three_beats": ["Frame committee stories", "Link evidence",
                            "Extract decision questions"],
            "four_d": ["Material issues (ECL, EWS, What-If)",
                       "Evidence (numbered references)",
                       "Decision questions (for committee)",
                       "Shared pack (draft for review)"],
            "why_it_matters": (
                "Committee packs are assembled by hand, and the claims in "
                "them drift away from the analysis that produced them."),
            "brings_together": ["uploaded committee material",
                                "saved Cockpit analyses",
                                "saved Early Warning analyses",
                                "saved What-If analyses",
                                "other authorized saved evidence"],
            "produces": ["a draft narrative following the prior pack's structure",
                         "material claims linked to numbered evidence",
                         "extracted decision questions",
                         "a draft pack for review"],
            "worked_example": {
                "decision_questions": [
                    "Which deteriorating borrowers require committee escalation?",
                    "Should a targeted limit and collateral review be approved?",
                    "What further evidence is needed before agreeing the response?"],
                "label": "deck example, not live data"},
            "boundary": ("Playbook organises supplied material, drafts the "
                         "narrative, links evidence and extracts questions. "
                         "It does not invent committee approval."),
            "owns": "committee narrative, evidence linkage and decision questions",
            "example_questions": [
                "Develop a committee report for me.",
                "What decision questions come out of this quarter's analysis?",
                "Link the ECL movement to the evidence behind it."],
        },
        {
            "id": "lenses", "name": "Lenses", "slide": 12,
            "one_line": "Bring the indicators an executive actually watches onto one screen.",
            "three_beats": ["Unified dashboards", "Live tracking",
                            "Simplified reporting"],
            "four_d": ["Track indicators (KPI cards)",
                       "Understand trends (charts)",
                       "Focus attention (AI read)",
                       "Report / share (export)"],
            "why_it_matters": (
                "A CRO wants the movement and the reason on the same screen, "
                "not a dashboard that shows a number and leaves the "
                "explanation somewhere else."),
            "typical_measures": ["total EAD", "ECL", "ECL / EAD",
                                 "Stage 2 EAD", "Stage 3 EAD",
                                 "High/Critical EWS customers",
                                 "selected trends"],
            "produces": ["KPI cards", "trend charts",
                         "a short written read of what moved",
                         "export and share"],
            "boundary": (
                "Metrics are brought together from relevant CreditProbe "
                "datasets, not unrestricted access to every possible source. "
                "'Last refreshed' reflects when the screen re-rendered, not "
                "that source observations changed. Stage 3 exposure is shown "
                "as defined, not relabelled as NPL."),
            "owns": "dashboard and reporting workflow",
            "example_questions": ["Create a CRO dashboard.",
                                  "How has ECL coverage moved this quarter?"],
        },
        {
            "id": "planner", "name": "AI Project Planner", "slide": 13,
            "one_line": "Keep risk and model-risk workstreams moving by tracking dependencies, blockers and overdue work.",
            "three_beats": ["Plan workstreams",
                            "Agentic AI tracks dependencies", "Drive delivery"],
            "four_d": ["Deadline risk (overdue review)",
                       "Dependency (sign-off blocked)",
                       "Agree response (notify / escalate)",
                       "Notify and deliver (sponsor informed)"],
            "why_it_matters": (
                "Risk and model-risk initiatives fail quietly: a review goes "
                "overdue, a sign-off blocks everything downstream, owners are "
                "fragmented and the delivery risk is invisible until the "
                "deadline."),
            "tracks": ["milestones", "dates", "dependencies", "progress",
                       "blockers", "deliverables", "overdue activity",
                       "escalation"],
            "monitoring_flow": [
                "upcoming deliverable → notify owner",
                "due now → request update or confirm evidence",
                "missed or blocked → notify and escalate per policy",
                "record update → reassess dependent milestones"],
            "worked_example": {
                "summary": ("In an eight-week scorecard validation and "
                            "remediation plan, independent review is overdue, "
                            "which directly blocks methodology sign-off and "
                            "puts remediation at risk."),
                "label": "deck example, not live data"},
            "boundary": ("Users approve material changes to ownership, dates "
                         "and the plan. A product demonstration is not "
                         "evidence that a real notification was sent."),
            "owns": "project, workstream and dependency management",
            "example_questions": ["What is blocking the validation plan?",
                                  "Which milestones are overdue?",
                                  "Who needs to be notified about the delay?"],
        },
    ]


def _supporting() -> list[dict]:
    return [
        {"name": "Data Builder", "slide": 2, "stage": "Diagnose",
         "one_line": "Cross-domain evidence: assemble the data an investigation needs from across the domains that hold it."},
        {"name": "Graph Data", "slide": 2, "stage": "Diagnose",
         "one_line": "Relationship intelligence: parents, guarantors, suppliers, customers and the paths risk can travel along."},
        {"name": "Borrower 360", "slide": 2, "stage": "Diagnose",
         "one_line": "Complete credit context for one borrower in one place."},
        {"name": "Root-Cause Investigation", "slide": 2, "stage": "Diagnose",
         "one_line": "From signal to why: work a movement back to what caused it."},
        {"name": "Action Matrix", "slide": 2, "stage": "Decide",
         "one_line": "Define the response: what should happen, to whom, by when."},
        {"name": "Escalation Matrix", "slide": 2, "stage": "Decide",
         "one_line": "Escalate with purpose, through the configured policy rather than ad hoc."},
        {"name": "Transportable Investigation", "slide": 2,
         "stage": "Drive Alignment",
         "one_line": "Take the evidence with you into the meeting it is needed in."},
        {"name": "Workflow", "slide": 2, "stage": "Drive Alignment",
         "one_line": "Notify, assign and align the people who have to act."},
        {"name": "Agentic AI Portfolio Watchdog", "slide": 2,
         "stage": "Detect",
         "one_line": "Watches the portfolio for movement worth a person's attention."},
    ]


def _relationships() -> list[dict]:
    return [
        {"between": ["cockpit", "early_warning"],
         "difference": (
             "Cockpit explains what the recorded book already shows — what "
             "moved and why. Early Warning looks forward from live behaviour "
             "to borrowers that are starting to deteriorate."),
         "together": (
             "Cockpit identifies that construction ECL moved and which "
             "borrowers sit behind it; Early Warning says which of those "
             "borrowers are deteriorating now and on what evidence.")},
        {"between": ["cockpit", "what_if"],
         "difference": (
             "Cockpit is historical and recorded: it analyses what the book "
             "actually shows. What-If is prospective: it applies a shock that "
             "has not happened and computes the consequence."),
         "together": (
             "Cockpit sizes the movement that already happened; What-If tests "
             "what a further downgrade or collateral fall would add.")},
        {"between": ["early_warning", "what_if"],
         "difference": (
             "Early Warning is about deterioration that is already visible in "
             "behaviour and evidence. What-If is about a hypothetical shock "
             "and its modelled impact."),
         "together": (
             "Early Warning names the borrowers under pressure; What-If tests "
             "how much worse a plausible adverse scenario would make them.")},
        {"between": ["cockpit", "what_if", "playbook"],
         "together": (
             "Cockpit establishes the movement and its drivers, What-If tests "
             "the adverse case, and Playbook turns both into a committee "
             "narrative with the decisions named and the evidence linked.")},
        {"between": ["early_warning", "playbook"],
         "together": (
             "Early Warning produces ranked deteriorating borrowers with "
             "layer-tagged drivers and an evidence trail; Playbook carries "
             "that evidence into the committee story and extracts the "
             "decisions it implies.")},
        {"between": ["scorecard_validation", "planner"],
         "together": (
             "Scorecard Validation produces findings and a remediation plan; "
             "Planner tracks the workstream, its dependencies and what is "
             "blocking sign-off.")},
        {"between": ["cockpit", "lenses"],
         "difference": (
             "Lenses is a standing view of the indicators an executive "
             "watches. Cockpit is where a question about one of them gets "
             "investigated.")},
    ]


# ---- Markdown rendering -------------------------------------------------

def render_markdown(pack: dict) -> str:
    out: list[str] = []
    w = out.append
    source = pack["source"]
    w("# CreditProbe Product Knowledge Pack")
    w("")
    w("> Generated by `scripts/cockpit_v4/ingest_product_deck.py` from the "
      "functionality deck. **Review this file; correct "
      "`backend/cockpit_v4/product_knowledge.json`** — the Markdown is "
      "rendered from the JSON and edits here are overwritten.")
    w("")
    w(f"- **Pack version:** `{pack['pack_version']}`")
    w(f"- **Ingested:** {pack['ingested_on']}")
    w(f"- **Source document:** {source['document']} ({source['pages']} pages)")
    w(f"- **SHA-256:** `{source['sha256']}`")
    w(f"- {source['note']}")
    w("")
    w("Every section records the deck slide it came from, so a claim in a "
      "Product Help answer can be traced back to the page that supports it.")
    w("")

    p = pack["positioning"]
    w(f"## Positioning (slide {p['slide']})")
    w("")
    w(f"**{p['headline']}** {p['tagline']}")
    w("")
    w(f"> {p['distinction']}")
    w("")
    w("The product arc:")
    w("")
    for stage in p["arc"]:
        w(f"- **{stage}** — {p['arc_questions'][stage]}")
    w("")
    w(f"In plain terms: {p['plain_arc']}.")
    w("")
    w(f"_{p['arc_note']}_")
    w("")

    a = pack["audience"]
    w(f"## Who this is for, and the problem they have (slide {a['slide']})")
    w("")
    w(f"Written for: {', '.join(a['roles'])}.")
    w("")
    w(a["the_real_problem"])
    w("")
    w("Evidence is fragmented across: "
      + ", ".join(a["fragmented_across"]) + ".")
    w("")
    w("Which shows up as:")
    w("")
    for item in a["consequences"]:
        w(f"- {item}")
    w("")
    w("The questions that actually get asked:")
    w("")
    for item in a["questions_a_cro_asks"]:
        w(f"- {item}")
    w("")

    w("## The seven functionalities")
    w("")
    for module in pack["modules"]:
        also = module.get("also_slides")
        slides = f"slide {module['slide']}"
        if also:
            slides += f", with detail on slides {', '.join(str(s) for s in also)}"
        w(f"### {module['name']} ({slides})")
        w("")
        w(module["one_line"])
        w("")
        w("**In three beats:** " + " · ".join(module["three_beats"]))
        w("")
        w("**Four-D chain:**")
        w("")
        for i, beat in enumerate(module["four_d"], 1):
            w(f"{i}. {beat}")
        w("")
        w(f"**Why it matters.** {module['why_it_matters']}")
        w("")
        if module.get("how_it_works"):
            w(f"**How it works.** {module['how_it_works']}")
            w("")
        if module.get("core_principle"):
            w(f"**Core principle.** {module['core_principle']}")
            w("")
        if module.get("question_types"):
            w("**How Cockpit classifies a question:**")
            w("")
            w("| Question type | Execution path | Example |")
            w("|---|---|---|")
            for q in module["question_types"]:
                w(f"| {q['type']} | {q['path']} | {q['example']} |")
            w("")
        if module.get("tac"):
            tac = module["tac"]
            w(f"**TAC (slide {tac['slide']}).** {tac['summary']}")
            w("")
            for key in ("T", "A", "C"):
                part = tac[key]
                w(f"- **{key} — {part['name']}: {part['question']}** "
                  f"{part['detail']}")
            w("")
            w(f"_{tac['scoring']}_")
            w("")
            w(f"**Same behaviour, different risk.** "
              f"{tac['same_behaviour_different_risk']}")
            w("")
        if module.get("layers"):
            layers = module["layers"]
            w(f"**The four intelligence layers (slide {layers['slide']}).** "
              f"{layers['summary']}")
            w("")
            for layer in layers["items"]:
                w(f"- **Layer {layer['layer']} — {layer['name']}** "
                  f"({layer['role']}): {layer['question']} "
                  f"_{layer['note']}_")
                w(f"  - Examples: {', '.join(layer['examples'])}")
                if layer.get("pipeline"):
                    w("  - Pipeline: " + "; ".join(layer["pipeline"]))
                if layer.get("evidence_retained"):
                    w("  - Evidence retained: "
                      + ", ".join(layer["evidence_retained"]))
            w("")
        for label, key in (("Brings together", "brings_together"),
                           ("Tracks", "tracks"),
                           ("Typical measures", "typical_measures"),
                           ("Monitoring flow", "monitoring_flow"),
                           ("Produces", "produces")):
            if module.get(key):
                w(f"**{label}:** " + "; ".join(module[key]) + ".")
                w("")
        if module.get("worked_example"):
            example = module["worked_example"]
            w("**Worked example** — _" + example.get("label", "deck example")
              + "_")
            w("")
            for k, v in example.items():
                if k == "label":
                    continue
                pretty = k.replace("_", " ")
                if isinstance(v, list):
                    w(f"- *{pretty}:*")
                    for item in v:
                        w(f"  - {item}")
                else:
                    w(f"- *{pretty}:* {v}")
            w("")
        w(f"**Owns:** {module['owns']}")
        w("")
        if module.get("does_not_own"):
            w(f"**Does not own:** {module['does_not_own']}")
            w("")
        w(f"**Boundary.** {module['boundary']}")
        w("")
        w("**Questions it answers:**")
        w("")
        for question in module["example_questions"]:
            w(f"- {question}")
        w("")

    w("## Supporting capabilities")
    w("")
    w("Platform and cross-cutting capabilities from the deck. These are not "
      "counted among the seven main functionalities.")
    w("")
    w("| Capability | Stage | What it does |")
    w("|---|---|---|")
    for item in pack["supporting_capabilities"]:
        w(f"| {item['name']} | {item['stage']} | {item['one_line']} |")
    w("")

    w("## How the modules relate")
    w("")
    names = {m["id"]: m["name"] for m in pack["modules"]}
    for rel in pack["relationships"]:
        pair = " ↔ ".join(names.get(x, x) for x in rel["between"])
        w(f"**{pair}**")
        w("")
        if rel.get("difference"):
            w(f"- *Difference:* {rel['difference']}")
        if rel.get("together"):
            w(f"- *Together:* {rel['together']}")
        w("")

    b = pack["boundaries"]
    w("## Boundaries and governance")
    w("")
    w("CreditProbe must never claim:")
    w("")
    for item in b["never_claim"]:
        w(f"- {item}")
    w("")
    w("What the deck states explicitly:")
    w("")
    for item in b["governance"]:
        w(f"- {item}")
    w("")
    w(f"**Demo data.** {b['demo_data']}")
    w("")

    h = pack["historical_architecture"]
    w(f"## {h['status']} — slide {h['slide']} ({h['label']})")
    w("")
    w("> **This section does NOT describe the current runtime.** It is "
      "retained for provenance only.")
    w("")
    w(h["summary"])
    w("")
    w(f"**Why it is excluded.** {h['why_excluded']}")
    w("")
    w("Must never be reintroduced as current behaviour:")
    w("")
    for item in h["must_not_reintroduce"]:
        w(f"- {item}")
    w("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", required=True)
    args = parser.parse_args()

    deck = Path(args.deck)
    if not deck.exists():
        print(f"deck not found: {deck}", file=sys.stderr)
        return 1
    sha = hashlib.sha256(deck.read_bytes()).hexdigest()
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(str(deck)).pages)
    except Exception:  # noqa: BLE001
        pages = 0

    pack = build(deck.name, sha, pages)
    JSON_OUT.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(render_markdown(pack), encoding="utf-8")
    print(f"pack version {pack['pack_version']}")
    print(f"  source   {deck.name} ({pages} pages, sha256 {sha[:16]}…)")
    print(f"  modules  {len(pack['modules'])}")
    print(f"  support  {len(pack['supporting_capabilities'])}")
    print(f"  written  {JSON_OUT.relative_to(ROOT)}")
    print(f"           {MD_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
