"""
The context packet Opus receives. Specification section 7.4.

Two stages, because one packet cannot serve two questions
---------------------------------------------------------
"Who are you?" and "Show the borrowers whose PIT 12-month PD rose most over the
last four quarters" are not the same size of question, and until Opus has read
the first one nobody knows which it is. Sending the complete field dictionary
to find that out cost 60,530 input tokens in live UAT -- which, with the 4,096
tokens of output the same call needs, is 64,626 against a 64,000 ceiling, so
the request was refused before it was sent and the user got nothing.

So there are two packets:

* `build_gate` -- STAGE A. The question in all three forms, the confirmed
  scope, the thread, the functionality registry, an OUTLINE of the domain
  (what each relation is about, how many columns, which quarters) and the
  remaining budget. No column names, no field definitions, no ratio formulas,
  no join graph, no sample rows, no per-field missingness table and no SQL
  execution contract. This is what the gate reads.

* `build` -- STAGE B. Everything above plus the COMPLETE compact dictionary,
  the measured coverage, the sample rows and the execution contract. Built
  only after the gate has returned `query_mode = DATA_ANALYSIS` and
  `owner = COCKPIT`, and never for a product-help, theory, referral,
  clarification or unsupported request.

The gate stays a full semantic decision made by Opus. Stage A is smaller, not
weaker: it is not a keyword router, it carries the whole question and the whole
registry, and it is the same model making the same judgement.

Sections A through J, assembled from authenticated application state and this
domain's own catalogue. Sonnet does not invent a dataset, a field, a coverage
figure or a missing rate; every number here is measured or configured.

The rule about what shrinks
---------------------------
Section 7.4's guardrail is precise about priority. When the packet is too
large, the COMPLETE compact schema and the current scope are preserved, and the
optional preview rows and non-essential history are reduced FIRST. If the
required core still does not fit, the answer is `CONTEXT_TOO_LARGE` with an
honest explanation -- never a silently halved dictionary and never a silently
raised budget.

`build` implements exactly that, as an ordered ladder of reductions, and it
reports which rungs it used. What it will not do at any rung is drop a business
field.

A measured finding, recorded rather than hidden
-----------------------------------------------
The complete compact dictionary for this domain measures about 18,700 tokens.
The specification's Standard per-call input cap is 12,000 and Deep's is 20,000.
So the mandatory catalogue does not fit Standard at all, and fits Deep only
with nothing else beside it. Section 7.4 anticipates this case in terms and
says what to do about it: "More specific user wording cannot fix an application
whose mandatory catalogue never fits. Fix that serialization/configuration as
an implementation issue."

Both halves of that were done. The serialization was fixed as far as it
honestly goes -- structural families brought it from 29,600 tokens to 18,700
with no field lost, and the remainder is 751 genuine field definitions. What
remains is a configuration question, so `required_core_tokens` is measured and
reported, the packet returns CONTEXT_TOO_LARGE when it does not fit, and
`docs/cockpit_agentic_v3/CONTEXT_SIZING.md` records the measurement and the
configuration change an administrator would need to approve. The limits are NOT
raised here to make a test pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import (CONTEXT_VERSION, DOMAIN, UNTRUSTED_NOTE,
                                     registry)
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic import profile as profile_mod
from backend.cockpit_agentic import pysandbox as py_mod
from backend.cockpit_agentic import sql as sql_mod
from backend.cockpit_agentic import tokens as tokens_mod
from backend.cockpit_agentic.catalog import Catalog
from backend.cockpit_agentic.contracts import (CleanedQuestion,
                                               DataCoverageProfile,
                                               NormalizedQuestion)
from backend.cockpit_agentic.ledger import Ledger
from backend.cockpit_agentic.scope import Scope

#: The calibrated local estimate, owned by `tokens` and re-exported here so
#: there is exactly ONE characters-per-token number in this package. It shipped
#: at 3.4 and live UAT measured 2.32 for this content -- see `tokens` for the
#: measurement and why a single under-counting constant was the reason a packet
#: this module believed was 33,000 tokens became a 60,530-token request.
CHARS_PER_TOKEN = tokens_mod.CHARS_PER_TOKEN


def estimate_tokens(payload: Any) -> int:
    """A conservative local token estimate.

    Not a substitute for the provider's counter, and not presented as exact.
    `build` records which method produced the number so a UAT run can compare
    the estimate with the provider's reported usage.
    """
    return tokens_mod.estimate(payload)


#: The two packets, named so that nothing downstream has to infer which one it
#: is holding. `CockpitContextPacket.stage` carries one of these and the
#: conversation renders the system prompt from it.
GATE_STAGE = "gate"
ANALYSIS_STAGE = "analysis"


class ContextTooLarge(RuntimeError):
    """The required core does not fit the mode's cap. Reported, never trimmed
    into silence."""

    def __init__(self, message: str, *, required: int, cap: int,
                 breakdown: dict[str, int]) -> None:
        super().__init__(message)
        self.required = required
        self.cap = cap
        self.breakdown = breakdown


#: The reduction ladder, in the order section 7.4 mandates. Everything here is
#: OPTIONAL detail; no rung touches the dictionary or the current scope.
LADDER: tuple[tuple[str, str], ...] = (
    ("samples_10_to_5", "halve the preview rows"),
    ("history_to_3_pairs", "keep only the three default recent exchanges"),
    ("profile_to_120", "list the 120 most misleading coverage gaps"),
    ("samples_5_to_2", "reduce the preview to two rows per relation"),
    ("profile_to_60", "list the 60 most misleading coverage gaps"),
    ("drop_samples", "drop the preview rows entirely"),
    ("history_to_1_pair", "keep only the most recent exchange"),
    ("drop_history", "drop the recent exchanges, keeping the rolling summary"),
)


@dataclass
class CockpitContextPacket:
    """Sections A-J of section 7.4, and how it was assembled.

    `stage` says which of the two packets this is. It is not decoration: the
    conversation asks the packet what belongs in the cached system prefix and
    what belongs in the opening user message, and those are different for the
    two stages. A packet that did not know its own stage would have to be
    guessed at by the caller, and the guess is exactly the bug this defect was.
    """

    version: str
    request_id: str
    payload: dict[str, Any]
    estimated_tokens: int
    token_method: str
    reductions_applied: list[str] = field(default_factory=list)
    required_core_tokens: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)
    stage: str = ANALYSIS_STAGE
    reserve_tokens: int = 0
    #: The FULLY ASSEMBLED request this packet produces -- the packet plus the
    #: instructions, the tool schema, the turn's message and the escaping the
    #: message envelope adds. This is the number the per-call cap applies to;
    #: `estimated_tokens` is the packet on its own. Zero when the caller could
    #: not render the request (the floor measurement, and the tests that build
    #: a packet without a conversation).
    request_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "request_id": self.request_id,
                "stage": self.stage,
                "estimated_tokens": self.estimated_tokens,
                "token_method": self.token_method,
                "reductions_applied": list(self.reductions_applied),
                "required_core_tokens": self.required_core_tokens,
                "reserve_tokens": self.reserve_tokens,
                "request_tokens": self.request_tokens,
                "breakdown": dict(self.breakdown),
                "payload": self.payload}

    def serialize(self) -> str:
        return json.dumps(self.payload, separators=(",", ":"), default=str)

    # -- what the conversation renders ---------------------------------

    def pinned(self) -> dict[str, Any]:
        """The half that is byte-identical for every turn of this request, and
        therefore sits above the one cache breakpoint.

        Stage A pins the domain OUTLINE and the functionality registry. Stage B
        pins the complete dictionary and the execution contract. Neither pins
        anything that moves between turns.
        """
        payload = self.payload
        scope = payload["B_scope"]
        common = {
            "domain_id": scope["domain_id"],
            "dataset_release_id": scope["dataset_release_id"],
            "reporting_currency": scope["reporting_currency"],
            "amount_scale": scope["amount_scale"],
        }
        if self.stage == GATE_STAGE:
            return {**common,
                    "domain_outline": payload["D_domain_outline"],
                    "functionalities": payload["H_functionalities"],
                    "execution_capabilities": payload["I_capabilities"]}
        return {**common,
                "catalogue": payload["D_E_catalogue"],
                "functionalities": payload["H_functionalities"],
                "execution_capabilities": payload["I_execution"]}

    def pinned_heading(self) -> str:
        """The sentence above the pinned block. It must not overstate what is
        in it: a gate packet that announced "the complete field dictionary"
        would be lying to the model about its own contents."""
        if self.stage == GATE_STAGE:
            return ("THE COCKPIT DOMAIN, IN OUTLINE, AND THE FUNCTIONALITY "
                    "REGISTRY.\n"
                    "What each part of this module is about, how large it is, "
                    "which quarters it covers, and what every other "
                    "functionality of CreditProbe does. THIS IS NOT THE FIELD "
                    "DICTIONARY: it names no column and defines no field, and "
                    "nothing may be planned or asserted about the data from "
                    "it. If this request turns out to be a Cockpit data "
                    "analysis, the complete dictionary is assembled and sent "
                    "before you plan anything.")
        return ("THE AUTHORIZED COCKPIT DOMAIN FOR THIS REQUEST.\n"
                "The complete field dictionary, the grains, the joins, the "
                "functionality registry and the execution contract. This is "
                "pinned for the lifetime of this request and is sent in full "
                "on every turn -- it is never abridged, hashed or replaced by "
                "a reference.")

    def dynamic(self) -> dict[str, Any]:
        """The half that changes, and therefore sits below the breakpoint."""
        payload = self.payload
        moving = {
            "request": payload["A_request"],
            "scope_and_filters": {
                k: v for k, v in payload["B_scope"].items()
                if k not in ("domain_id", "dataset_release_id",
                             "reporting_currency", "amount_scale")},
            "thread": payload["C_thread"],
            "remaining_budget": payload["J_budget"],
        }
        if self.stage == GATE_STAGE:
            moving["coverage_outline"] = payload["F_coverage_outline"]
            return moving
        moving["measured_coverage"] = payload["F_coverage"]
        moving["sample_rows"] = payload["G_samples"]
        return moving

    def dynamic_heading(self) -> str:
        if self.stage == GATE_STAGE:
            return ("THE REQUEST, ITS SCOPE, THE THREAD AND THE REMAINING "
                    "BUDGET.")
        return ("THE REQUEST, ITS SCOPE, THE MEASURED COVERAGE AND THE "
                "REMAINING BUDGET.")


def floor(**kwargs: Any) -> dict[str, Any]:
    """The smallest packet this domain can produce, measured.

    Runs every rung of the reduction ladder and reports what is left. Used by
    the sizing document and its test, so the recorded floor is a measurement
    rather than a number someone typed. Deliberately not implemented by passing
    an impossibly small cap: an override that only raises would ignore it, and a
    measurement that depends on a configuration quirk is not a measurement.
    """
    reduced = build(**{**kwargs, "_force_full_reduction": True})
    return {"tokens": reduced.estimated_tokens,
            "reductions_applied": list(reduced.reductions_applied),
            "breakdown": dict(reduced.breakdown)}


def _python_section(limits) -> dict[str, Any]:
    """What Python can and cannot do here, established by running the probe
    rather than by asserting a policy."""
    detected = py_mod.probe()
    if not detected.available:
        return {"available": False,
                "reason": py_mod.unavailable_reason(),
                "consequence": ("Express the analysis in SQL. There is no "
                                "in-process substitute, so a Python step will "
                                "be refused rather than approximated.")}
    return {
        "available": True,
        "runtime": "Python 3.13, in a separate process with no network, no "
                   "shell, no filesystem beyond its own workspace and no "
                   "access to this application's data stores.",
        "packages": ["numpy", "pandas", "the standard library"],
        "package_rule": ("Nothing else is importable. The unapproved packages "
                         "are not blocked by policy, they are not present."),
        "input_rule": ("A Python step reads `inputs`: a dict keyed by the "
                       "step_id of each step ALREADY EXECUTED IN THE SAME "
                       "SUBMISSION, each of them "
                       "`{'columns': [{'name','type'}], 'rows': [{column: "
                       "value}], 'row_count': int, 'truncated': bool}` -- the "
                       "same shape a SQL step returns, so "
                       "`pandas.DataFrame(inputs['s1']['rows'])` is the whole "
                       "of the setup. There is no database connection inside "
                       "the sandbox, so a Python step cannot read anything a "
                       "SQL step did not fetch first, and `truncated` is worth "
                       "reading before you aggregate."),
        "output_rule": ("Assign `result`. A pandas DataFrame or Series comes "
                        "back as named columns and records; a list of dicts, a "
                        "list, a dict or a scalar is shaped into the same "
                        "thing. `print` output is returned as a warning on the "
                        "step, not as the result."),
        "wall_seconds_per_step": limits.step_wall_seconds,
        "memory_mib": limits.python_memory_mib,
        "failure_rule": ("If the step raises, the interpreter's traceback is "
                         "returned to you unedited and the repair is yours. "
                         "Nothing here rewrites your code."),
    }


def _section_a(cleaned: CleanedQuestion,
               normalized: NormalizedQuestion) -> dict[str, Any]:
    """Section A: the question in all three forms. Identical in both stages --
    the gate is a semantic decision and it gets the whole question."""
    return {
        "original_question": cleaned.original_text,
        "detected_language": cleaned.detected_language,
        "english_translation": cleaned.english_text,
        "translation_uncertainties": list(cleaned.uncertainties),
        "translation_failed": cleaned.failed,
        "business_request": normalized.business_question,
        "subquestions": list(normalized.subquestions),
        "requested_measures": list(normalized.requested_measures),
        "requested_actions": list(normalized.requested_actions),
        "entity_references": list(normalized.entity_references),
        "periods": list(normalized.periods),
        "unresolved_ambiguity": list(normalized.unresolved_ambiguity),
        "normalization_failed": normalized.failed,
        "note": ("Every subquestion and every stated ambiguity is here. "
                 "An ambiguity is genuine: do not resolve it by guessing, "
                 "and do not drop a subquestion to make the rest "
                 "answerable."),
    }


def _section_b(normalized: NormalizedQuestion, *, scope: Scope,
               catalog: Catalog, ledger: Ledger,
               ui_filters: dict[str, Any] | None) -> dict[str, Any]:
    """Section B: the scope the SERVER confirmed. Identical in both stages."""
    return {
        "current_module": "Cockpit",
        "domain_id": DOMAIN,
        "tenant_scope": scope.to_dict(),
        "dataset_release_id": scope.dataset_release_id,
        "reporting_currency": catalog.reporting_currency,
        "amount_scale": catalog.amount_scale,
        "ui_filters": dict(ui_filters or {}),
        "explicit_scope_this_message": dict(normalized.explicit_scope),
        "inherited_scope": dict(normalized.inherited_scope),
        "effective_scope": normalized.effective_scope,
        "response_language": normalized.response_language,
        "mode": ledger.mode,
        "precedence_note": (
            "explicit_scope_this_message overrides inherited_scope. A new "
            "instruction from the user replaces an inherited filter; it "
            "does not merge with it."),
        "transmission_note": (
            "Only this domain's data may be sent to the model provider. "
            "There is no other module's data in this packet, and the "
            "functionality descriptions in section H are metadata, not a "
            "route to one."),
    }


def _section_c(limits, *, exchanges: list[dict[str, Any]],
               rolling_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Section C: the thread. Identical in both stages -- a gate decision that
    could not see the thread would classify a follow-up wrongly."""
    return {
        "rolling_summary": dict(rolling_summary or {}),
        "recent_exchanges": list(exchanges),
        "default_pairs": limits.recent_pairs_default,
        "hard_cap_pairs": limits.recent_pairs_hard_cap,
        "note": ("A recent exchange is one complete question and its final "
                 "answer, referral or clarification -- not one message. "
                 "Only authorized Cockpit evidence appears here; a result "
                 "produced by another module or an earlier, broader "
                 "Cockpit does not become authorized by appearing in an "
                 "old answer."),
    }


def _capabilities(limits, *, session_open: bool) -> dict[str, Any]:
    """Stage A's section I: WHAT this module can do, not HOW to instruct it.

    The gate has to fill in `requires_sql` and `requires_python`, so it must
    know that querying and computing are possible at all. It does not need the
    dialect, the statement rule, the relation list, the row cap or the sandbox
    contract -- it is not writing a step, and stage B carries all of that.
    """
    return {
        "can_query_this_domain": session_open,
        "can_run_python": py_mod.probe().available,
        "can_draw_charts": limits.max_charts > 0,
        "note": ("Capability only. The SQL dialect, the statement rules, the "
                 "readable relations, the row limits and the Python sandbox "
                 "contract are not in this packet. Do not author a query, a "
                 "step or a plan here."),
    }


def build_gate(*, request_id: str, cleaned: CleanedQuestion,
               normalized: NormalizedQuestion, scope: Scope, catalog: Catalog,
               coverage: DataCoverageProfile, ledger: Ledger,
               session: sql_mod.Session | None = None,
               ui_filters: dict[str, Any] | None = None,
               rolling_summary: dict[str, Any] | None = None,
               recent_exchanges: list[dict[str, Any]] | None = None,
               reserve_tokens: int = 0,
               measure: Any = None) -> CockpitContextPacket:
    """STAGE A. The packet the gate decides from.

    What is here, and why each part earns its place:

    * A -- the question in all three forms, with its subquestions, measures,
      actions, entities, periods and ambiguities. The gate is a semantic
      decision; it reads the whole question.
    * B -- the confirmed scope: module, domain, tenant, release, currency,
      filters. Which book, at a high level.
    * C -- the rolling summary and the bounded recent exchanges. A follow-up
      cannot be classified without the thread it follows.
    * D -- the domain OUTLINE: what each subject area is about, how many
      columns it has, which quarters exist. Not the dictionary.
    * F -- coverage counts. Not the field-by-field missingness table.
    * H -- the complete functionality registry. Scoring every functionality is
      the gate's actual job, so this is not reduced.
    * I -- capability flags. Whether querying and computing are possible.
    * J -- the remaining budget.

    What is deliberately NOT here: every relation.column, the field
    definitions, the ratio definitions, the qualitative detail, the macro pivot
    columns, the sample rows, the per-field missingness table, the join graph,
    the execution relation schemas, the SQL instructions and the grain
    warnings. Those exist to write a correct analysis, and no analysis is
    written in this turn.

    There is no reduction ladder here. Every part of this packet is required
    for the decision and none of it is optional detail, so if it does not fit
    the answer is the honest refusal, not a quietly halved registry.
    """
    limits = ledger.limits
    exchanges = list(recent_exchanges or [])[-limits.recent_pairs_hard_cap:] \
        if limits.recent_pairs_hard_cap else []
    payload = {
        "context_version": CONTEXT_VERSION,
        "request_id": request_id,
        "stage": GATE_STAGE,
        "A_request": _section_a(cleaned, normalized),
        "B_scope": _section_b(normalized, scope=scope, catalog=catalog,
                              ledger=ledger, ui_filters=ui_filters),
        "C_thread": _section_c(limits, exchanges=exchanges,
                               rolling_summary=rolling_summary),
        "D_domain_outline": catalog.outline(),
        "F_coverage_outline": profile_mod.outline(coverage),
        "H_functionalities": registry.compact(),
        "I_capabilities": _capabilities(limits,
                                        session_open=session is not None),
        "J_budget": ledger.budget_view(),
        "stage_note": (
            "THIS IS THE GATE PACKET. Decide the query_mode and the owner "
            "from it, and -- for PRODUCT_HELP and THEORY_CONCEPT -- answer "
            "from it. Do not plan an analysis, do not name a column, and do "
            "not state what the data does or does not contain at field level. "
            "If this is a Cockpit data analysis, the complete field "
            "dictionary, the measured coverage, the sample rows and the "
            "execution contract are assembled and sent to you before you plan "
            "anything."),
        "untrusted_data_note": UNTRUSTED_NOTE,
    }
    breakdown = {key: estimate_tokens(value) for key, value in payload.items()}
    total = estimate_tokens(payload)
    # What the provider will actually be sent, when the caller can render it.
    request = measure(payload) if measure is not None else total
    cap = max(0, limits.max_input_tokens_per_call - max(0, reserve_tokens))
    if request > cap:
        raise ContextTooLarge(
            f"The Cockpit's gate request is about {request} tokens against "
            f"{cap} available in {ledger.mode} mode "
            f"({limits.max_input_tokens_per_call} per call, less "
            f"{reserve_tokens} reserved for the reply). Its context packet is "
            f"about {total} tokens and carries no field dictionary and no "
            f"sample rows; this is already the smallest question this module "
            f"can ask. That makes it a configuration limit -- see "
            f"docs/cockpit_agentic_v3/CONTEXT_SIZING.md -- and not something "
            f"a differently worded question can fix.",
            required=request, cap=cap, breakdown=breakdown)
    return CockpitContextPacket(
        version=CONTEXT_VERSION, request_id=request_id, payload=payload,
        estimated_tokens=total,
        token_method=f"local conservative estimate at {CHARS_PER_TOKEN} "
                     f"characters per token",
        required_core_tokens=total, breakdown=breakdown, stage=GATE_STAGE,
        reserve_tokens=max(0, reserve_tokens), request_tokens=request)


def build(*, request_id: str, cleaned: CleanedQuestion,
          normalized: NormalizedQuestion, scope: Scope, catalog: Catalog,
          coverage: DataCoverageProfile, ledger: Ledger,
          session: sql_mod.Session | None = None,
          ui_filters: dict[str, Any] | None = None,
          rolling_summary: dict[str, Any] | None = None,
          recent_exchanges: list[dict[str, Any]] | None = None,
          sample_relations: tuple[str, ...] = (),
          include_pivot_columns: bool = False,
          reserve_tokens: int = 0,
          measure: Any = None,
          _force_full_reduction: bool = False,
          _cap_override: int = 0) -> CockpitContextPacket:
    """STAGE B. Assemble sections A-J, reduce optional detail, or refuse.

    Built only for a request the gate has already ruled a Cockpit data
    analysis. It carries the COMPLETE compact dictionary; nothing here abridges
    it, and the ladder never touches it.

    `measure` renders a candidate payload through the same functions that will
    render the real request and returns its estimated size, so the ladder runs
    against what is actually sent rather than against the packet alone -- the
    packet is not the request, and comparing the packet to the per-call cap is
    how a packet that "fitted" became a request the provider refused.
    `reserve_tokens` is what the same call needs for its reply. Without
    `measure` the packet measures itself, which is what the floor measurement
    and the sizing tests want.
    """
    limits = ledger.limits
    exchanges = list(recent_exchanges or [])
    sample_rows = limits.sample_rows_per_dataset
    profile_limit = 0
    keep_samples = True
    keep_history = True
    history_pairs = min(len(exchanges), limits.recent_pairs_hard_cap)

    def assemble() -> dict[str, Any]:
        # ---- A. the question, all three forms ------------------------
        section_a = _section_a(cleaned, normalized)

        # ---- B. server-confirmed scope -------------------------------
        section_b = _section_b(normalized, scope=scope, catalog=catalog,
                               ledger=ledger, ui_filters=ui_filters)

        # ---- C. thread context ---------------------------------------
        section_c = _section_c(
            limits,
            exchanges=(exchanges[-history_pairs:]
                       if keep_history and history_pairs else []),
            rolling_summary=rolling_summary)

        # ---- D and E. the complete dictionary, grains and calendar ----
        section_de = catalog.compact(
            include_pivot_columns=include_pivot_columns)

        # ---- F. measured coverage ------------------------------------
        section_f = profile_mod.compact(coverage, limit=profile_limit)

        # ---- G. bounded, reproducible samples ------------------------
        section_g: dict[str, Any] = {"samples": [], "note": (
            "Samples illustrate the SHAPE of a relation. They do not establish "
            "a total, a distribution or a missing rate -- section F does that, "
            "measured over every row.")}
        if keep_samples and session is not None and sample_rows > 0:
            preview = ["facility_id", "borrower_id", "reporting_quarter",
                       "ifrs9_stage", "pd_pit_12m", "pd_ttc_12m",
                       "ead_reported", "ecl_reported"]
            for relation in (sample_relations or (F.FACILITY_QUARTER,)):
                try:
                    columns = (preview if relation == F.FACILITY_QUARTER
                               else None)
                    section_g["samples"].append(sql_mod.sample_rows(
                        relation, session, limit=sample_rows,
                        columns=columns))
                except Exception:                          # noqa: BLE001
                    continue
            if any(s["relation"] == F.FACILITY_QUARTER
                   for s in section_g["samples"]):
                section_g["preview_note"] = (
                    "The facility preview shows a named subset of key columns "
                    "for readability. The COMPLETE field dictionary is in "
                    "section D; do not treat these eight columns as the "
                    "available fields.")

        # ---- H. the functionality registry ---------------------------
        section_h = registry.compact()

        # ---- I. what can actually be executed ------------------------
        section_i = {
            "sql": {
                "dialect": "DuckDB 1.5 SQL",
                "available": session is not None,
                "readable_relations": sorted(scope.relations),
                "statement_rule": (
                    "Exactly one SELECT per step. No writes, no schema "
                    "changes, no configuration changes, no COPY, no ATTACH, "
                    "no INSTALL, and no file or network functions -- there is "
                    "no filesystem or network from this session."),
                "tenant_rule": (
                    "tenant_id, dataset_release_id and domain_id are already "
                    "filtered in every relation. You do not need to write "
                    "those predicates and writing them cannot widen what you "
                    "see."),
                "permitted": (
                    "Aggregates, window functions, CTEs, date arithmetic, "
                    "joins and the supported statistical functions."),
                "max_model_rows": sql_mod.MAX_MODEL_ROWS,
                "wall_seconds_per_step": limits.step_wall_seconds,
            },
            "python": _python_section(limits),
            "charts": {
                "max": limits.max_charts,
                "kinds": ["bar", "line", "waterfall", "scatter"],
                "note": ("A chart is a declarative specification bound to "
                         "result fact ids. HTML and JavaScript are never "
                         "executed."),
            },
        }

        # ---- J. the budget ledger ------------------------------------
        section_j = ledger.budget_view()

        return {
            "context_version": CONTEXT_VERSION,
            "request_id": request_id,
            "A_request": section_a,
            "B_scope": section_b,
            "C_thread": section_c,
            "D_E_catalogue": section_de,
            "F_coverage": section_f,
            "G_samples": section_g,
            "H_functionalities": section_h,
            "I_execution": section_i,
            "J_budget": section_j,
            "untrusted_data_note": UNTRUSTED_NOTE,
        }

    def per_section(payload: dict[str, Any]) -> dict[str, int]:
        return {key: estimate_tokens(value) for key, value in payload.items()}

    # `_cap_override` exists so a test can simulate a deployment whose cap is
    # below this domain's floor. It is a test seam and nothing reads it in
    # production: the real cap comes from the ledger's limits.
    cap = (0 if _force_full_reduction
           else max(0, (_cap_override or limits.max_input_tokens_per_call)
                    - max(0, reserve_tokens)))
    def size(candidate: dict[str, Any]) -> int:
        return (measure(candidate) if measure is not None
                else estimate_tokens(candidate))

    payload = assemble()
    applied: list[str] = []

    # The required core: everything the guardrail forbids reducing.
    core_keys = ("A_request", "B_scope", "D_E_catalogue", "H_functionalities",
                 "I_execution", "J_budget")
    core = estimate_tokens({k: payload[k] for k in core_keys})
    breakdown = per_section(payload)

    total = size(payload)
    for name, _why in LADDER:
        if total <= cap:
            break
        if name == "samples_10_to_5":
            sample_rows = 5
        elif name == "history_to_3_pairs":
            history_pairs = min(history_pairs, limits.recent_pairs_default)
        elif name == "profile_to_120":
            profile_limit = 120
        elif name == "samples_5_to_2":
            sample_rows = 2
        elif name == "profile_to_60":
            profile_limit = 60
        elif name == "drop_samples":
            keep_samples = False
        elif name == "history_to_1_pair":
            history_pairs = 1
        elif name == "drop_history":
            keep_history = False
        applied.append(name)
        payload = assemble()
        total = size(payload)

    breakdown = per_section(payload)
    if _force_full_reduction:
        # A floor measurement, not a request. Every rung has been spent; there
        # is nothing to refuse.
        return CockpitContextPacket(
            version=CONTEXT_VERSION, request_id=request_id, payload=payload,
            estimated_tokens=estimate_tokens(payload),
            token_method="local floor measurement",
            reductions_applied=applied, required_core_tokens=core,
            breakdown=breakdown, request_tokens=total)
    if total > cap:
        raise ContextTooLarge(
            f"The Cockpit's analysis request is about {total} tokens against "
            f"{cap} available in {ledger.mode} mode "
            f"({limits.max_input_tokens_per_call} per call, less "
            f"{reserve_tokens} reserved for the reply). The complete "
            f"field dictionary alone is about "
            f"{breakdown.get('D_E_catalogue', 0)} tokens and it is not "
            f"abridged: sending half a schema would produce a confident answer "
            f"over fields that were silently hidden. Every optional reduction "
            f"has already been applied "
            f"({', '.join(applied) or 'none were available'}). This is a "
            f"configuration limit, not something a differently worded question "
            f"can fix -- see docs/cockpit_agentic_v3/CONTEXT_SIZING.md.",
            required=total, cap=cap, breakdown=breakdown)

    return CockpitContextPacket(
        version=CONTEXT_VERSION, request_id=request_id, payload=payload,
        estimated_tokens=estimate_tokens(payload),
        token_method=f"local conservative estimate at {CHARS_PER_TOKEN} "
                     f"characters per token",
        reductions_applied=applied, required_core_tokens=core,
        breakdown=breakdown, stage=ANALYSIS_STAGE,
        reserve_tokens=max(0, reserve_tokens), request_tokens=total)


__all__ = ["ANALYSIS_STAGE", "CHARS_PER_TOKEN", "CockpitContextPacket",
           "ContextTooLarge", "GATE_STAGE", "LADDER", "build", "build_gate",
           "estimate_tokens"]
