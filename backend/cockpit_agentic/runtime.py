"""
One request, end to end. Specification sections 8, 9 and 11.

This is the server-owned state machine. The model requests transitions; this
approves them, updates the counters atomically, and stops.

The four properties this module is responsible for
--------------------------------------------------
**The gate is first and it is real.** Nothing reaches the validator before
`decision.may_execute` is true. A referral or a clarification returns from
`FUNCTIONALITY_ASSESSMENT` without ever entering `VALIDATING`, so "a referral
performs zero analytical executions" is structural rather than a promise.

**CreditProbe never authors a repair.** On failure it builds an
`ExecutionFailurePacket` and returns it to Opus. There is no branch in this
file that writes or edits SQL. `_execute_submission` runs what it was given.

**The counters are global and they do not reset.** Five submissions and three
rounds for the whole request, enforced by the ledger, which raises rather than
returns. A new plan does not buy a sixth attempt because `note_submission` does
not know what plan asked.

**A stop is honest.** Whatever ends the request -- the gate, the budget, the
deadline, an unrepairable failure, insufficient evidence -- the user gets an
envelope that says what was understood, what was tried, why it stopped and what
would help. There is no fallback to a deterministic narrative engine and no
canned decomposition standing in for an unavailable model (sections 17 and 18).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import DOMAIN, STANDARD
from backend.cockpit_agentic import answer_check
from backend.cockpit_agentic import catalog as catalog_mod
from backend.cockpit_agentic import context as context_mod
from backend.cockpit_agentic import contracts as K
from backend.cockpit_agentic import credential as credential_mod
from backend.cockpit_agentic import failure as failure_mod
from backend.cockpit_agentic import models as models_mod
from backend.cockpit_agentic import opus as opus_mod
from backend.cockpit_agentic import pysandbox as py_mod
from backend.cockpit_agentic import scope as scope_mod
from backend.cockpit_agentic import sonnet as sonnet_mod
from backend.cockpit_agentic import sql as sql_mod
from backend.cockpit_agentic import states as st
from backend.cockpit_agentic import tokens as tokens_mod
from backend.cockpit_agentic.ledger import (STORE, BudgetExceeded,
                                            DuplicateCandidate, Ledger,
                                            Prices, STOP_CALLS,
                                            STOP_CANCELLED, STOP_DEADLINE,
                                            STOP_INPUT_TOO_LARGE,
                                            STOP_METADATA, STOP_NO_PROGRESS,
                                            STOP_ROUNDS, STOP_SPEND,
                                            STOP_STEPS, STOP_SUBMISSIONS,
                                            STOP_TOKENS)

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    """What the user gets, and everything needed to audit how."""

    request_id: str
    status: str
    envelope: K.AnswerEnvelope
    decision: K.FunctionalityDecision | None = None
    plan: K.AnalysisPlan | None = None
    results: list[K.ExecutionResultPacket] = field(default_factory=list)
    failures: list[K.ExecutionFailurePacket] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    machine: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, Any] = field(default_factory=dict)
    python_audit: list[dict[str, Any]] = field(default_factory=list)
    repair_audits: list[dict[str, Any]] = field(default_factory=list)
    answer_checks: list[dict[str, Any]] = field(default_factory=list)
    models: dict[str, Any] = field(default_factory=dict)
    #: How the analysis plan was arrived at: generations, whether the compact
    #: regeneration ran, and what the bounds trimmed.
    planning: dict[str, Any] = field(default_factory=dict)
    exchange: K.Exchange | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "domain_id": DOMAIN,
            "status": self.status,
            "answer": self.envelope.to_dict(),
            "functionality_decision": (self.decision.to_dict()
                                       if self.decision else None),
            "plan": self.plan.to_dict() if self.plan else None,
            "results": [r.to_dict() for r in self.results],
            "failures": [f.to_dict() for f in self.failures],
            "budget": dict(self.budget),
            "states": dict(self.machine),
            "context": dict(self.context),
            "tokens": dict(self.tokens),
            "python_execution": list(self.python_audit),
            "repair_context_audits": list(self.repair_audits),
            "answer_validation": list(self.answer_checks),
            "planning": dict(self.planning),
            "models": dict(self.models),
        }


def _stop_envelope(*, reason: str, narrative: str, understood: str = "",
                   tried: list[str] | None = None, help_text: str = "",
                   limitations: list[str] | None = None) -> K.AnswerEnvelope:
    """A factual, server-generated stop. Never a fabricated analyst answer."""
    return K.AnswerEnvelope(
        kind="stop", narrative=narrative, complete=False, stop_reason=reason,
        what_was_understood=understood, what_was_tried=list(tried or []),
        what_would_help=help_text, limitations=list(limitations or []))


class Runtime:
    """One request. Construct, `run`, read the outcome."""

    def __init__(self, *, provider: Any, principal: Any,
                 dataset_release_id: str, coverage: Any, calendar: Any,
                 mode: str = STANDARD, request_id: str = "",
                 prices: Prices | None = None, store: Any = STORE,
                 provider_error: Exception | None = None) -> None:
        self.request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        self.provider = provider
        self.mode = mode
        self.scope = scope_mod.for_principal(
            principal, dataset_release_id=dataset_release_id)
        self.catalog = catalog_mod.build(
            dataset_release_id=dataset_release_id, calendar=calendar,
            tenant_id=self.scope.tenant_id)
        self.coverage = coverage
        # Idempotent: the same request id continues one budget rather than
        # opening a second (section 9.3).
        self.ledger, self.resumed = store.open(
            request_id=self.request_id, mode=mode, prices=prices)
        self.machine = st.Machine()
        self.session: sql_mod.Session | None = None
        self.progress: list[str] = []

        self.decision: K.FunctionalityDecision | None = None
        self.plan: K.AnalysisPlan | None = None
        self.results: list[K.ExecutionResultPacket] = []
        self.failures: list[K.ExecutionFailurePacket] = []
        self.attempts: list[dict[str, Any]] = []
        self.artifacts = K.ArtifactManifest(request_id=self.request_id)
        self.completed_steps: list[str] = []
        #: Stage A. The light packet the gate decided from.
        self.gate_packet: context_mod.CockpitContextPacket | None = None
        #: Stage B. The full analytical packet, built ONLY for a Cockpit data
        #: analysis. It stays None for every other outcome, which is what makes
        #: "product help never assembles the dictionary" checkable rather than
        #: asserted.
        self.context_packet: context_mod.CockpitContextPacket | None = None
        self.conversation: Any = None
        #: Every Opus conversation this request opened, in order: the gate's,
        #: then the analysis conversation if it reached stage B. Reported
        #: together, because a token report that showed only the second would
        #: hide the cost of the first.
        self.conversations: list[Any] = []
        #: One record per sandboxed Python step: what was run, under which
        #: limits, with which guarantees, and how it ended. Never the data.
        self.python_audit: list[dict[str, Any]] = []
        #: One record per repair dispatch: which of the sixteen required parts
        #: of the effective context were found in the ACTUAL outbound request.
        self.repair_audits: list[dict[str, Any]] = []
        #: One report per answer validation. Section 29.
        self.answer_checks: list[dict[str, Any]] = []
        #: The bound that makes the rewrite ONE. Nothing decrements it.
        self.answer_rewrites = 0
        #: How the plan was arrived at: how many generations it took, whether
        #: the compact regeneration was used, and what the bounds trimmed.
        #: Reported, because a plan that was cut to fit is a fact about the
        #: analysis and not something to absorb quietly.
        self.planning: dict[str, Any] = {}
        # Resolved at construction, so a misconfigured deployment is caught
        # before any work is done -- but REPORTED through `run`, because a
        # constructor that raises gives the caller an exception where the rest
        # of this class gives it an honest outcome, and the API surface should
        # not have two shapes for the same kind of stop.
        # Raised by `service._resolve_provider` when the Cockpit's own
        # credential is not configured. Carried rather than raised at
        # construction, for the same reason the model error is: the caller
        # gets an honest outcome, not an exception, and there is one shape for
        # a stop rather than two.
        self.provider_error = provider_error
        self.models: models_mod.CockpitModels | None = None
        self.model_error: models_mod.CockpitModelError | None = None
        try:
            self.models = models_mod.resolve()
        except models_mod.CockpitModelError as e:
            self.model_error = e
        # A missing credential is NOT caught here. `service._resolve_provider`
        # raises it, the caller passes it in as `provider_error`, and `_run`
        # raises it before anything is assembled -- one shape of stop, reported
        # through `run`. A second handler here would have had to finish a
        # request from inside a constructor, which is why it is not one.

    # -- progress -------------------------------------------------------

    def _advance(self, state: str, why: str = "") -> None:
        self.machine.advance(state, why)
        self.progress.append(st.progress(
            state, submission=self.ledger.submissions + 1,
            submission_max=self.ledger.limits.execution_submissions,
            round=max(1, self.ledger.analysis_rounds),
            round_max=self.ledger.limits.analysis_rounds))

    def _planning_truncated(self, truncation: Any) -> None:
        """One compact regeneration is about to be asked for.

        The user sees work being refined. They do not see `max_tokens`, a stop
        reason, a schema or anything else about how the provider ended a turn:
        section 11 wants progress that names the actual work, and "the model
        hit its output limit" is not work, it is plumbing. The internals are
        recorded in `planning` and in `tokens.turns` for an operator.
        """
        self.progress.append("Refining the analysis plan")
        self.machine.history.append(
            {"state": self.machine.state,
             "why": "the planning response reached the output allowance; one "
                    "compact regeneration was requested"})
        logger.info("Cockpit planning truncated at %s tokens (%s); one "
                    "compact regeneration follows.",
                    getattr(truncation, "limit", "?"),
                    getattr(truncation, "purpose", "?"))

    def cancel(self) -> None:
        self.ledger.cancel()

    # -- the run --------------------------------------------------------

    def run(self, question: str, *, ui_filters: dict[str, Any] | None = None,
            rolling_summary: dict[str, Any] | None = None,
            recent_exchanges: list[dict[str, Any]] | None = None) -> Outcome:
        try:
            return self._run(question, ui_filters=ui_filters,
                             rolling_summary=rolling_summary,
                             recent_exchanges=recent_exchanges)
        except BudgetExceeded as e:
            return self._budget_stop(e, question)
        except opus_mod.PlanTruncated as e:
            # Section 42: one terminal state per guardrail. The output
            # allowance is a guardrail, and it is not the input cap -- an
            # operator sent to CONTEXT_TOO_LARGE would look at the packet,
            # which was never the problem.
            #
            # Nothing was executed and no execution submission was consumed:
            # none had been submitted when the plan failed to arrive.
            return self._finish(
                st.STOPPED_OUTPUT_LIMIT,
                _stop_envelope(
                    reason=K.PLAN_OUTPUT_TRUNCATED, narrative=str(e),
                    understood=question,
                    tried=[f"the analysis plan was requested {e.attempts} "
                           f"times, and each response reached the "
                           f"{e.limit:,}-token response allowance"],
                    help_text=(
                        "Nothing is wrong with the question and rewording it "
                        "is unlikely to help: what did not fit was the "
                        "model's own plan, not anything you asked for. Deep "
                        "mode allows a longer response where an analysis "
                        "genuinely needs one, and an operator can see the two "
                        "attempts in this request's record.")))
        except opus_mod.OpusUnavailable as e:
            # Section 17 and 18: no hidden fallback to the old deterministic
            # engine, and no canned decomposition standing in for a missing
            # model. The user is told the truth.
            return self._finish(
                st.PROVIDER_ERROR,
                _stop_envelope(
                    reason="provider_unavailable",
                    narrative=str(e),
                    understood=question,
                    help_text=("This is an operator or configuration matter, "
                               "not something rephrasing the question can "
                               "fix.")))
        except credential_mod.ProviderCredentialMissing as e:
            # Fails closed before the first provider request. No deterministic
            # answer, and the message names the variable and never a value.
            return self._finish(
                st.PROVIDER_CREDENTIAL_MISSING,
                _stop_envelope(
                    reason=e.status, narrative=str(e), understood=question,
                    help_text=(f"Set {' and '.join(e.variables)} in the "
                               f"runtime environment and restart the "
                               f"service.")))
        except models_mod.CockpitModelError as e:
            # Requirement, stated plainly: no deterministic answering fallback.
            # An unconfigured or unserveable model role ends the request with a
            # status an operator can act on.
            return self._finish(
                e.status,
                _stop_envelope(
                    reason=e.status, narrative=str(e), understood=question,
                    help_text=(
                        "Set " + " and ".join(e.variables)
                        + " and restart the service."
                        if e.variables else
                        "This is an operator or configuration matter.")))
        except tokens_mod.TooLargeToSend as e:
            return self._finish(
                st.CONTEXT_TOO_LARGE,
                _stop_envelope(
                    reason="context_too_large", narrative=str(e),
                    understood=question,
                    help_text=(
                        f"The request was measured at {e.counted.tokens:,} "
                        f"tokens "
                        f"({'against ' + e.counted.model if e.counted.measured else 'by local estimate'}) "
                        f"against a {e.cap:,}-token limit and was not sent. "
                        f"An administrator can raise the per-call input cap "
                        f"for this deployment.")))
        except context_mod.ContextTooLarge as e:
            return self._finish(
                st.CONTEXT_TOO_LARGE,
                _stop_envelope(
                    reason="context_too_large", narrative=str(e),
                    understood=question,
                    help_text=("An administrator can raise the per-call input "
                               "cap for this deployment. Rephrasing the "
                               "question cannot make the data dictionary "
                               "smaller.")))
        except Exception as e:                              # noqa: BLE001
            from backend.llm.base import LLMError

            if isinstance(e, LLMError):
                # The provider's own reason, not a generic internal error. A
                # deployment with no credential must be told that, because it
                # is the actionable fact and no analysis was substituted for it.
                return self._finish(
                    st.PROVIDER_ERROR,
                    _stop_envelope(
                        reason="provider_unavailable", narrative=str(e),
                        understood=question,
                        help_text=("This is an operator or configuration "
                                   "matter. No answer was produced from a "
                                   "deterministic substitute.")))
            # Section 41: an unexpected application failure is INTERNAL_ERROR,
            # with the stage and an error id recorded and no secret and no
            # stack trace shown. Not PROVIDER_ERROR: blaming the provider for
            # this application's defect sends an operator to look in the wrong
            # place.
            error_id = f"err-{uuid.uuid4().hex[:12]}"
            logger.exception("The Cockpit request failed [%s] at stage %s",
                             error_id, self.machine.state)
            if not self.machine.finished:
                return self._finish(
                    st.INTERNAL_ERROR,
                    _stop_envelope(
                        reason="internal_error",
                        narrative=(
                            f"Something in this application failed while "
                            f"{st.progress(self.machine.state).lower()}. The "
                            f"failure is recorded under {error_id}. No answer "
                            f"was produced from a substitute."),
                        understood=question,
                        help_text=(f"Quote {error_id} to an operator. The "
                                   f"question itself is fine; rephrasing it "
                                   f"is unlikely to help.")))
            # The machine already settled -- the failure happened on the way
            # out. The outcome stands; the error is recorded and not shown.
            return self._outcome(
                self.machine.state,
                _stop_envelope(
                    reason="internal_error",
                    narrative=(f"The Cockpit could not complete this request. "
                               f"The failure is recorded under {error_id}."),
                    understood=question,
                    help_text=f"Quote {error_id} to an operator."))

    def _run(self, question: str, *, ui_filters, rolling_summary,
             recent_exchanges) -> Outcome:
        # Before anything, including the first preprocessing call: if nobody
        # has said which account pays for this Cockpit, nothing runs. This is
        # the "before the first provider request" gate -- no request has been
        # assembled at this point, let alone sent.
        if self.provider_error is not None:
            raise self.provider_error
        # And if nobody has said which models serve it.
        if self.model_error is not None:
            raise self.model_error
        # And that the provider will actually serve what they name. Checked
        # once per process against the token counter, so a configured-but-wrong
        # id is reported as MODEL_UNAVAILABLE rather than surfacing later as a
        # generic provider error with a different remedy.
        assert self.models is not None
        self.models = models_mod.ensure_available(self.provider, self.models)

        # ---- Sonnet pass 1 -------------------------------------------
        self._advance(st.NORMALIZING_1, "cleaning and translating")
        cleaned = sonnet_mod.clean(question, self.provider, self.ledger)

        # ---- Sonnet pass 2 -------------------------------------------
        self._advance(st.NORMALIZING_2, "normalizing into a business request")
        normalized = sonnet_mod.normalize(
            cleaned, self.provider, self.ledger, ui_filters=ui_filters,
            rolling_summary=rolling_summary, recent_exchanges=recent_exchanges)

        # ---- the context packet --------------------------------------
        self._advance(st.BUILDING_CONTEXT, "assembling the Cockpit catalogue")
        try:
            self.session = sql_mod.open_session(scope=self.scope,
                                                catalog=self.catalog)
        except sql_mod.SqlRejected as e:
            return self._finish(
                st.EXECUTION_FAILED,
                _stop_envelope(
                    reason=e.category, narrative=str(e), understood=question,
                    help_text=("This is an access or configuration matter for "
                               "an administrator.")))

        # ---- STAGE A: the gate packet --------------------------------
        # The light packet. The whole question, the whole registry, the domain
        # in outline -- and no field dictionary, because nothing is planned
        # from this turn. See `context.build_gate`.
        packet_args = dict(
            request_id=self.request_id, cleaned=cleaned, normalized=normalized,
            scope=self.scope, catalog=self.catalog, coverage=self.coverage,
            ledger=self.ledger, session=self.session,
            ui_filters=ui_filters, rolling_summary=rolling_summary,
            recent_exchanges=recent_exchanges)
        self.gate_packet = context_mod.build_gate(
            **packet_args,
            reserve_tokens=self.ledger.limits.max_opus_output_tokens,
            measure=opus_mod.request_sizer(
                stage=context_mod.GATE_STAGE, contract="opus_gate",
                schema=opus_mod.GATE_SCHEMA))

        gate_conversation = opus_mod.Conversation(
            provider=self.provider, ledger=self.ledger,
            packet=self.gate_packet,
            # The exact id that will serve the request, so tokens are counted
            # against the tokenizer that will actually be used.
            model=self.models.reasoning if self.models else "")
        self.conversation = gate_conversation
        self.conversations.append(gate_conversation)

        # ---- the gate ------------------------------------------------
        self._advance(st.FUNCTIONALITY_ASSESSMENT,
                      "checking this is a Cockpit question")
        self.decision = opus_mod.gate(gate_conversation, self.gate_packet)

        # Sections 10 and 11: a question about the product or about what a
        # term means is answered here, from knowledge, in the turn that
        # classified it. Zero SQL, zero Python, zero execution submissions,
        # zero analysis rounds -- by construction, because this branch never
        # reaches the loop that consumes them.
        if self.decision.answers_without_executing:
            return self._explain(self.decision, question)

        if not self.decision.may_execute:
            return self._not_ours(question, cleaned, normalized)

        # Section 9: the server reads the scores, not the model's confidence
        # about them. Below the floor, not highest, or too close to call is a
        # question for the user.
        owned, why = self.decision.ownership_test()
        if not owned:
            self.decision.decision = K.CLARIFY_FUNCTIONALITY
            self.decision.query_mode = K.CLARIFICATION_REQUIRED
            if not self.decision.clarification_question:
                self.decision.clarification_question = (
                    "This request could be handled by more than one part of "
                    "CreditProbe. Which would you like?")
            self.decision.decision_reason = why
            return self._not_ours(question, cleaned, normalized)

        # ---- STAGE B: the full analytical packet ---------------------
        # Reached ONLY here: query_mode is DATA_ANALYSIS, owner is COCKPIT, and
        # the server's own score test passed. Every other route out of the gate
        # has already returned, so no product-help, theory, referral,
        # clarification or unsupported request can reach this line.
        self._advance(st.PLANNING, "planning the analysis")
        self.ledger.note_analysis_round()
        self.context_packet = context_mod.build(
            **packet_args,
            # The reply, plus room for the turns that come after this one: the
            # analysis packet is built once and every review and repair turn is
            # appended to the same conversation.
            reserve_tokens=(self.ledger.limits.max_opus_output_tokens
                            + opus_mod.CONVERSATION_RESERVE),
            measure=opus_mod.request_sizer(
                stage=context_mod.ANALYSIS_STAGE, contract="opus_plan",
                schema=opus_mod.PLAN_SCHEMA))

        # A NEW conversation. The two stages pin different domain blocks, and a
        # prefix that changed under itself mid-request would invalidate the
        # cache for every turn after it -- and, worse, would leave the gate's
        # outline and the analysis dictionary both in one history, saying
        # different things about the same domain.
        conversation = opus_mod.Conversation(
            provider=self.provider, ledger=self.ledger,
            packet=self.context_packet,
            model=self.models.reasoning if self.models else "")
        self.conversation = conversation
        self.conversations.append(conversation)

        # The planning turn is bounded: a plan is executable instructions, not
        # a credit memo, and the step cap comes from the ledger rather than
        # being repeated here.
        planned = opus_mod.plan_first_submission(
            conversation, self.context_packet, self.decision,
            bounds=K.bounds_for(self.ledger.limits),
            on_retry=self._planning_truncated)
        self.plan = planned["plan"]
        self.planning = {
            "attempts": planned["attempts"],
            "regenerated": planned["regenerated"],
            "bounds": K.bounds_for(self.ledger.limits).to_dict(),
            "bounds_applied": list(planned["bounds_applied"]),
        }
        if planned["action"] == "ask_a_targeted_clarification":
            return self._clarify(planned["clarification_question"],
                                 planned["clarification_options"])
        if planned["action"] == "explain_and_stop":
            return self._insufficient(
                question,
                planned["explanation"]
                or "The Cockpit's data cannot answer this question, and no "
                   "approximation was substituted for it.")

        return self._loop(conversation, planned["submission"], question,
                          cleaned, normalized)

    # -- the loop --------------------------------------------------------

    def _loop(self, conversation, submission, question, cleaned,
              normalized) -> Outcome:
        while True:
            stopped = self.ledger.may_continue()
            if stopped:
                raise BudgetExceeded(stopped,
                                     self.ledger._stop_message(stopped))

            if submission is None:
                return self._insufficient(
                    question,
                    "The analysis produced no executable step, so nothing "
                    "could be run.")

            outcome = self._attempt(conversation, submission)
            if isinstance(outcome, Outcome):
                return outcome
            submission = outcome

    def _attempt(self, conversation, submission: K.ExecutionSubmission):
        """One validate-execute-review cycle. Returns the next submission, or
        an Outcome when the request is finished."""
        # ---- validate ------------------------------------------------
        if self.machine.state != st.VALIDATING:
            self._advance(st.VALIDATING, "checking the analysis against the "
                                         "data")
        duplicate: DuplicateCandidate | None = None
        try:
            number = self.ledger.note_submission(submission.fingerprint)
        except DuplicateCandidate as e:
            # Section 22: the attempt is spent and the code is NOT run again.
            # Opus is told, and may write something different if attempts
            # remain. Only exhaustion ends the request.
            duplicate, number = e, e.submission_number
        submission = K.ExecutionSubmission(
            submission_id=submission.submission_id, plan_id=submission.plan_id,
            analysis_round=self.ledger.analysis_rounds,
            submission_number=number, steps=submission.steps,
            authored_by="opus")

        try:
            self.ledger.note_steps(len(submission.steps))
        except BudgetExceeded:
            raise

        if duplicate is not None:
            step = (submission.steps[0] if submission.steps
                    else K.ExecutionStep(step_id="duplicate", language=K.SQL,
                                         code="", purpose=""))
            return self._failed_step(
                conversation, submission, step,
                sql_mod.SqlRejected(K.NO_PROGRESS_DUPLICATE, str(duplicate),
                                    detail=submission.fingerprint))

        step_results: list[K.StepResult] = []
        for step in submission.steps:
            rejection = self._validate(step)
            if rejection is None:
                self._advance(st.EXECUTING, f"running {step.step_id}") \
                    if self.machine.state == st.VALIDATING else None
                try:
                    if step.language == K.PYTHON:
                        result = self._run_python(step, step_results)
                    else:
                        result = sql_mod.execute(
                            step.code, self.session,
                            deadline_seconds=min(
                                self.ledger.limits.step_wall_seconds,
                                max(0.5, self.ledger.remaining_seconds)))
                except sql_mod.SqlRejected as e:
                    rejection = e
                else:
                    artifact = self.artifacts.add(K.Artifact(
                        artifact_id=f"art-{uuid.uuid4().hex[:8]}",
                        kind="table", domain_id=DOMAIN,
                        tenant_id=self.scope.tenant_id,
                        dataset_release_id=self.scope.dataset_release_id,
                        step_id=step.step_id, row_count=result.row_count,
                        byte_size=len(str(result.rows)),
                        columns=result.columns))
                    self.completed_steps.append(step.step_id)
                    warnings = list(result.warnings)
                    stdout = getattr(result, "stdout", "")
                    if stdout:
                        warnings.append("The step printed: " + stdout[:2_000])
                    step_results.append(K.StepResult(
                        step_id=step.step_id,
                        status=("empty" if result.row_count == 0
                                else "truncated" if result.truncated
                                else "success"),
                        executed_code=step.code, parameters=step.parameters,
                        columns=result.columns, row_count=result.row_count,
                        artifact_id=artifact.artifact_id, rows=result.rows,
                        truncated=result.truncated, warnings=warnings,
                        elapsed_seconds=result.elapsed_seconds))
                    continue

            # ---- failure: hand the facts back, author nothing ---------
            return self._failed_step(conversation, submission, step, rejection)

        # ---- results -> review ---------------------------------------
        packet = K.ExecutionResultPacket(
            request_id=self.request_id, plan_id=submission.plan_id,
            submission_id=submission.submission_id,
            submission_number=submission.submission_number,
            analysis_round=submission.analysis_round,
            dataset_release_id=self.scope.dataset_release_id,
            status=("empty" if all(s.status == "empty" for s in step_results)
                    else "partial" if any(s.status == "truncated"
                                          for s in step_results)
                    else "complete"),
            steps=step_results, budget=self.ledger.budget_view(),
            note=("A zero-row step means nothing matched the filters. It is "
                  "not a failure and it is not proof that the quantity is "
                  "zero."))
        self.results.append(packet)

        self._advance(st.REVIEWING, "reviewing the results")
        decision = opus_mod.review(conversation, packet, plan=self.plan,
                                   analysis_round=self.ledger.analysis_rounds)

        if decision.decision == K.ANSWER and decision.answer is not None:
            self._advance(st.ANSWERING, "preparing the answer")
            return self._answer(decision.answer, partial=False)

        if decision.decision == K.NEEDS_CLARIFICATION:
            return self._clarify(decision.clarification_question,
                                 decision.clarification_options)

        if decision.decision == K.REVISE_ANALYSIS:
            if self.ledger.rounds_remaining <= 0:
                return self._round_limited(decision)
            self._advance(st.PLANNING, "revising the analysis")
            self.ledger.note_analysis_round()
            self.plan = decision.revised_plan or self.plan
            return decision.revised_submission

        # INSUFFICIENT_DATA, SYSTEM_ERROR, BUDGET_LIMITED
        if decision.answer is not None:
            self._advance(st.ANSWERING, "preparing a partial answer")
            return self._answer(decision.answer, partial=True)
        return self._insufficient(
            "", decision.gap_addressed
            or "The results do not support an answer to the question as asked.")

    def _run_python(self, step: K.ExecutionStep,
                    done: list[K.StepResult]) -> py_mod.SandboxResult:
        """Run an Opus-authored Python step over the rows this submission has
        already fetched. The sandbox reads nothing else: there is no database
        handle inside it, so Python cannot widen the scope SQL was held to."""
        inputs = {
            earlier.step_id: {"columns": [dict(c) for c in earlier.columns],
                              "rows": [dict(r) for r in earlier.rows],
                              "row_count": earlier.row_count,
                              "truncated": earlier.truncated}
            for earlier in done}
        result = py_mod.execute(
            step.code, inputs=inputs,
            limits=py_mod.SandboxLimits.from_ledger(self.ledger),
            cancel=lambda: self.ledger.cancelled,
            step_id=step.step_id, request_id=self.request_id)
        self.python_audit.append(result.audit)
        return result

    def _validate(self, step: K.ExecutionStep) -> sql_mod.SqlRejected | None:
        if step.language == K.PYTHON:
            # The only validation a Python step gets. There is no lint, no AST
            # allowlist and no rewrite: the boundary is the kernel's, and
            # inspecting the code for intent here would be the first step
            # towards editing it.
            if not py_mod.probe().available:
                return py_mod.PythonRejected(
                    K.SANDBOX_UNAVAILABLE, py_mod.unavailable_reason())
            return None
        if step.language != K.SQL:
            return sql_mod.SqlRejected(
                K.UNSAFE_OPERATION,
                f"`{step.language}` is not an executable language here. The "
                f"runtime executes SQL and, where the isolated sandbox is "
                f"available, Python.")
        try:
            sql_mod.check_structure(step.code)
            sql_mod.bind(step.code, self.session)
        except sql_mod.SqlRejected as e:
            return e
        return None

    def _failed_step(self, conversation, submission, step, rejection):
        """Build the factual packet, return it to Opus, and take what Opus
        writes. CreditProbe writes nothing."""
        packet = failure_mod.build(
            error=rejection, step=step, submission=submission,
            session=self.session, ledger=self.ledger,
            plan_id=submission.plan_id, request_id=self.request_id,
            completed_steps=list(self.completed_steps),
            reusable_artifacts=list(self.artifacts.artifacts),
            previous_attempts=list(self.attempts),
            phase=("validation" if self.machine.state == st.VALIDATING
                   else "execution"))
        self.failures.append(packet)
        self.attempts.append(failure_mod.summarize_attempt(step, rejection))

        if not packet.repairable:
            return self._failed(
                f"That request cannot be answered from the Cockpit: "
                f"{rejection}",
                reason=rejection.category)

        if self.ledger.submissions_remaining <= 0:
            return self._submissions_exhausted(conversation)

        stopped = self.ledger.may_continue()
        if stopped:
            raise BudgetExceeded(stopped, self.ledger._stop_message(stopped))

        # Back to PLANNING: only Opus may author the next candidate.
        self._advance(st.PLANNING, "returning the failure to the analyst")
        expectations = failure_mod.outbound_expectations(
            packet=packet, step=step,
            context_payload=(self.context_packet.payload
                             if self.context_packet else {}))
        self.repair_audits.append({"submission": submission.submission_number,
                                   "step_id": step.step_id,
                                   "verified": [], "missing": []})

        def inspect(system_blocks, messages, tools):
            """Read the assembled request. Refuse it if the effective context
            is not in it. This does not edit the request and cannot: it is
            handed the assembled blocks and returns nothing."""
            serialized = json.dumps(
                {"system": system_blocks, "messages": messages,
                 "tools": tools}, default=str)
            missing = failure_mod.audit_outbound(serialized, expectations)
            record = self.repair_audits[-1]
            record["missing"] = missing
            record["verified"] = [k for k in failure_mod.OUTBOUND_ITEMS
                                  if k not in missing]
            record["serialized_bytes"] = len(serialized)
            if missing:
                raise failure_mod.IncompleteRepairContext(missing)

        conversation.before_dispatch = inspect
        try:
            response = opus_mod.repair(
                conversation, packet, plan=self.plan,
                analysis_round=self.ledger.analysis_rounds)
        except failure_mod.IncompleteRepairContext as e:
            # An application defect, and it stops the request. Continuing
            # would send Opus a repair task without the material to do it,
            # and take the answer that came back as if it were informed.
            logger.error("Cockpit repair context incomplete: %s", e)
            return self._failed(
                "The analysis stopped because this application could not "
                "assemble the complete context the failure needed. That is a "
                "defect here, not a limit of the data or of the question.",
                reason=K.INFRASTRUCTURE_ERROR)
        finally:
            conversation.before_dispatch = None
        self.plan = response["plan"] or self.plan

        if response["action"] == "ask_a_targeted_clarification":
            return self._clarify(response["clarification_question"],
                                 response["clarification_options"])
        if response["action"] == "explain_and_stop" or \
                response["submission"] is None:
            return self._failed(
                response["explanation"]
                or "The analysis could not be completed against this data.",
                reason=rejection.category)
        if response["action"] == "revise_the_analysis_plan":
            if self.ledger.rounds_remaining > 0:
                self.ledger.note_analysis_round()
        return response["submission"]

    # -- terminal outcomes ----------------------------------------------

    def _not_ours(self, question, cleaned, normalized) -> Outcome:
        """A referral or a functionality clarification. Zero execution."""
        decision = self.decision
        assert decision is not None
        if decision.decision == K.REDIRECT:
            envelope = K.AnswerEnvelope(
                kind="referral",
                narrative=decision.public_explanation or decision.referral_reason,
                complete=True,
                referral={
                    "destination": decision.referral_destination,
                    "reason": decision.referral_reason,
                    "route": decision.referral_route or None,
                    "enabled": decision.referral_enabled,
                    "navigation_available": bool(decision.referral_route),
                },
                alternatives=decision.alternatives,
                limitations=([decision.mixed_scope_explanation]
                             if decision.mixed_scope else []))
            return self._finish(st.REDIRECTED, envelope)

        if decision.decision == K.UNSUPPORTED_REQUEST:
            return self._finish(st.UNSUPPORTED, K.AnswerEnvelope(
                kind="stop",
                narrative=decision.public_explanation
                or "No part of CreditProbe owns this request.",
                complete=False, stop_reason="unsupported",
                alternatives=decision.alternatives))

        return self._finish(st.WAITING_FOR_USER, K.AnswerEnvelope(
            kind="clarification",
            narrative=decision.public_explanation
            or decision.clarification_question,
            complete=False,
            clarification_question=decision.clarification_question,
            clarification_options=list(decision.clarification_options),
            alternatives=decision.alternatives))

    def _explain(self, decision: K.FunctionalityDecision,
                 question: str) -> Outcome:
        """Sections 10 and 11: product help and theory, answered without
        touching the book.

        This path never reaches `_loop`, so there is no code path by which it
        could consume an execution submission or an analysis round. That is
        the proof, and it is structural rather than a promise.
        """
        envelope = decision.answer
        assert envelope is not None
        self._advance(st.ANSWER_VALIDATION, "checking what the answer claims")
        envelope, report = self._validate_answer(envelope, executed=False)
        if not report.valid:
            envelope, report = self._rewrite_once(envelope, report)
        return self._finish(
            st.COMPLETED if report.valid else st.PARTIAL, envelope)

    def _answer(self, envelope: K.AnswerEnvelope, *, partial: bool) -> Outcome:
        if self.machine.state != st.ANSWER_VALIDATION:
            self._advance(st.ANSWER_VALIDATION,
                          "checking every figure against the results")
        envelope, report = self._validate_answer(envelope, executed=True)
        if not report.valid:
            envelope, report = self._rewrite_once(envelope, report)
            partial = partial or not report.valid

        charts = self.ledger.limits.max_charts
        if len(envelope.charts) > charts:
            envelope.charts = envelope.charts[:charts]
            envelope.limitations.append(
                f"{charts} charts is the limit in {self.ledger.mode} mode; the "
                f"most relevant were kept.")
        if partial:
            envelope.complete = False
        return self._finish(st.PARTIAL if partial else st.COMPLETED, envelope)

    def _validate_answer(self, envelope: K.AnswerEnvelope, *,
                         executed: bool) -> tuple[K.AnswerEnvelope, Any]:
        """Section 29: hold the answer against the evidence before anyone
        sees it. Optional parts that fail are dropped here; blocking failures
        go back to Opus."""
        from backend.cockpit_agentic import registry as registry_mod

        envelope, report = answer_check.check(
            envelope, results=self.results, catalog=self.catalog,
            registry_facts=registry_mod.grounding_facts(), executed=executed)
        self.answer_checks.append(report.to_dict())
        for dropped in report.dropped_charts:
            logger.info("Cockpit chart dropped: %s", dropped)
        if report.dropped_references:
            envelope.limitations.append(
                f"{len(report.dropped_references)} evidence reference(s) in "
                f"this answer did not match a result produced for this "
                f"request and were removed.")
        return envelope, report

    def _rewrite_once(self, envelope: K.AnswerEnvelope,
                      report: Any) -> tuple[K.AnswerEnvelope, Any]:
        """The one rewrite, section 29. Never a second.

        `answer_rewrites` is the counter that makes it one, and nothing
        decrements it. If the rewrite fails validation too, the answer is
        rendered with only what checks out and a limitation saying so -- which
        is what a stop looks like when there is something worth showing.
        """
        if self.answer_rewrites >= 1 or self.conversation is None:
            envelope.limitations.append(
                "Parts of this answer could not be traced to the results of "
                "this request and were removed. What remains is supported by "
                "the evidence shown.")
            envelope.complete = False
            return envelope, report

        self.answer_rewrites += 1
        self._advance(st.ANSWERING, "correcting the answer against the results")
        self._advance(st.ANSWER_VALIDATION, "re-checking the corrected answer")
        try:
            rewritten = opus_mod.rewrite_answer(
                self.conversation, envelope,
                answer_check.rewrite_request(envelope, report))
        except Exception as e:                              # noqa: BLE001
            logger.info("The Cockpit answer rewrite did not complete: %s", e)
            rewritten = None
        if rewritten is None:
            envelope.limitations.append(
                "Parts of this answer could not be traced to the results of "
                "this request and were removed.")
            envelope.complete = False
            return envelope, report

        checked, second = self._validate_answer(
            rewritten, executed=bool(self.results))
        if not second.valid:
            checked.limitations.append(
                "Some figures in this answer could not be traced to the "
                "results of this request. Only what the evidence supports is "
                "shown.")
            checked.complete = False
        return checked, second

    # -- which packet is in hand ----------------------------------------

    def _packet(self) -> context_mod.CockpitContextPacket | None:
        """The most specific packet built so far.

        Stage B if this became a Cockpit data analysis, stage A otherwise.
        Everything that only needs the question, the scope or the thread finds
        them in either, and nothing here promises a dictionary that a
        product-help request never assembled.
        """
        return self.context_packet or self.gate_packet

    def _context_report(self) -> dict[str, Any]:
        """Both stages, so an audit can see which packets were built and how
        large each was. A request that never reached stage B says so by the
        absence of the analysis entry, not by a flag someone had to set."""
        report: dict[str, Any] = {}
        if self.gate_packet is not None:
            report["gate"] = self.gate_packet.to_dict()
        if self.context_packet is not None:
            report["analysis"] = self.context_packet.to_dict()
        report["stages_built"] = list(report)
        report["full_catalogue_sent"] = self.context_packet is not None
        return report

    def _token_report(self) -> dict[str, Any]:
        """Every counted turn of every stage, and the total that was actually
        assembled. Two stages means two conversations; reporting one of them
        would understate what the request cost."""
        if not self.conversations:
            return {}
        counts = [c for conv in self.conversations for c in conv.counts]
        turns = [t for conv in self.conversations for t in conv.turns]
        return {
            "counter": self.conversations[0].counter.report(),
            "counts": counts,
            "turns": turns,
            "stages": [
                {"stage": conv.packet.stage,
                 "packet_tokens": conv.packet.estimated_tokens,
                 "reserved_tokens": conv.packet.reserve_tokens,
                 "turns": len(conv.turns),
                 "largest_request_tokens": max(
                     [c["tokens"] for c in conv.counts] or [0])}
                for conv in self.conversations],
            "largest_request_tokens": max(
                [c["tokens"] for c in counts] or [0]),
        }

    def _clarify(self, question: str, options: list[str]) -> Outcome:
        return self._finish(st.WAITING_FOR_USER, K.AnswerEnvelope(
            kind="clarification",
            narrative=question or "A clarification is needed to continue.",
            complete=False, clarification_question=question,
            clarification_options=list(options or [])))

    def _insufficient(self, question: str, why: str) -> Outcome:
        return self._finish(st.INSUFFICIENT_DATA, _stop_envelope(
            reason="insufficient_data", narrative=why,
            understood=question,
            tried=[a["what_failed"] for a in self.attempts],
            help_text=("Naming the exact measure, period or cohort you want "
                       "would let the analysis be narrowed to what this data "
                       "supports.")))

    def _failed(self, why: str, *, reason: str) -> Outcome:
        return self._finish(st.EXECUTION_FAILED, _stop_envelope(
            reason=reason, narrative=why,
            tried=[a["what_failed"] for a in self.attempts]))

    def _round_limited(self, decision) -> Outcome:
        """Three rounds used and the evidence still insufficient. Section 14."""
        gaps = [s.gap for s in decision.per_subquestion if s.gap]
        if decision.answer is not None:
            return self._answer(decision.answer, partial=True)
        return self._finish(st.PARTIAL, _stop_envelope(
            reason=STOP_ROUNDS,
            narrative=(
                f"After {self.ledger.limits.analysis_rounds} analysis rounds "
                f"the evidence still does not settle the question as asked, so "
                f"the analysis stopped rather than inventing the remainder."),
            tried=[a["what_failed"] for a in self.attempts] or
                  [s.subquestion for s in decision.per_subquestion],
            limitations=gaps,
            help_text=(decision.clarification_question
                       or "Narrowing the question to one measure and one "
                          "period would let the remaining gap be closed.")))

    def _submissions_exhausted(self, conversation) -> Outcome:
        """Five submissions used. Section 8.3: reserve a bounded explanation if
        it is affordable, and a factual server envelope if it is not."""
        tried = [a["what_failed"] for a in self.attempts]
        narrative = (
            f"The analysis could not be executed within the "
            f"{self.ledger.limits.execution_submissions} attempts allowed for "
            f"one question. Each attempt failed for a reason recorded below, "
            f"and no further attempt is permitted.")
        return self._finish(st.EXECUTION_FAILED, _stop_envelope(
            reason=STOP_SUBMISSIONS, narrative=narrative, tried=tried,
            help_text=("Naming the exact fields, period or cohort you need "
                       "would let the query be written against data that is "
                       "definitely present. You are not being asked to fix "
                       "SQL: the failures above are the application's to "
                       "resolve.")))

    def _budget_stop(self, error: BudgetExceeded, question: str) -> Outcome:
        # Section 42: one terminal state per guardrail. "The request stopped"
        # is not actionable; "it used its five execution submissions" is, and
        # it is a different thing to do about it than "it reached the spend
        # ceiling".
        status = {
            STOP_DEADLINE: st.STOPPED_TIME_LIMIT,
            STOP_CANCELLED: st.CANCELLED,
            STOP_SUBMISSIONS: st.STOPPED_EXECUTION_LIMIT,
            STOP_STEPS: st.STOPPED_EXECUTION_LIMIT,
            STOP_CALLS: st.STOPPED_EXECUTION_LIMIT,
            STOP_METADATA: st.STOPPED_EXECUTION_LIMIT,
            STOP_ROUNDS: st.STOPPED_ANALYSIS_LIMIT,
            STOP_TOKENS: st.STOPPED_TOKEN_LIMIT,
            STOP_INPUT_TOO_LARGE: st.CONTEXT_TOO_LARGE,
            STOP_SPEND: st.STOPPED_COST_LIMIT,
            STOP_NO_PROGRESS: st.INSUFFICIENT_DATA,
        }.get(error.reason, st.STOPPED_EXECUTION_LIMIT)
        return self._finish(status, _stop_envelope(
            reason=error.reason, narrative=str(error), understood=question,
            tried=[a["what_failed"] for a in self.attempts],
            help_text=(
                "A new question starts a new budget. This one stopped where "
                "it did and its attempts are preserved so the next does not "
                "repeat them.")))

    def _outcome(self, status: str, envelope: K.AnswerEnvelope) -> Outcome:
        """Build the outcome for a machine that has ALREADY settled.

        Split from `_finish` so the failure path out of a finished request
        cannot try to advance a terminal state and raise a second time inside
        the handler for the first.
        """
        envelope.status = status
        return Outcome(
            request_id=self.request_id, status=status, envelope=envelope,
            decision=self.decision, plan=self.plan, results=self.results,
            failures=self.failures, budget=self.ledger.to_dict(),
            machine={**self.machine.to_dict(), "progress": self.progress},
            context=self._context_report(),
            planning=dict(self.planning),
            models=self.models.to_dict() if self.models else {},
            python_audit=list(self.python_audit),
            repair_audits=list(self.repair_audits),
            answer_checks=list(self.answer_checks))

    def _finish(self, status: str, envelope: K.AnswerEnvelope) -> Outcome:
        if not self.machine.finished:
            if self.machine.may(status):
                self.machine.advance(status, "finished")
            elif self.machine.may(st.SUMMARIZING) and \
                    status in st.TRANSITIONS[st.SUMMARIZING]:
                self.machine.advance(st.SUMMARIZING, "finishing")
                self.machine.advance(status, "finished")
            else:
                # Section 42: no request may remain RUNNING. A status the
                # machine cannot reach from here is an application defect, and
                # leaving the request in a working state to hide it would be
                # the exact failure this rule exists to prevent. Raising sends
                # it to INTERNAL_ERROR, which is a terminal state.
                raise st.IllegalTransition(
                    f"the request cannot settle as {status} from "
                    f"{self.machine.state}")
        envelope.status = status
        exchange = K.Exchange(
            exchange_id=self.request_id,
            question=(self._packet().payload["A_request"]["original_question"]
                      if self._packet() is not None else ""),
            answer=envelope.narrative,
            kind=("referral" if status == st.REDIRECTED
                  else "clarification" if status == st.WAITING_FOR_USER
                  else "answer" if status == st.COMPLETED
                  else "stop"),
            fact_ids=list(envelope.fact_ids),
            dataset_release_id=self.scope.dataset_release_id)
        return Outcome(
            request_id=self.request_id, status=status, envelope=envelope,
            decision=self.decision, plan=self.plan, results=self.results,
            failures=self.failures, budget=self.ledger.to_dict(),
            machine={**self.machine.to_dict(), "progress": self.progress},
            context=self._context_report(),
            tokens=self._token_report(),
            planning=dict(self.planning),
            python_audit=list(self.python_audit),
            repair_audits=list(self.repair_audits),
            answer_checks=list(self.answer_checks),
            models=self.models.to_dict() if self.models else {},
            exchange=exchange)


__all__ = ["Outcome", "Runtime"]
