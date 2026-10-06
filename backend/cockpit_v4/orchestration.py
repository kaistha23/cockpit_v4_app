"""
The one analyst loop. A messenger with a ledger, not a second brain.

What this module does: assemble authorized context, dispatch one generation,
validate the action it asked for, run it, persist what happened, put the
result back, and repeat until the analyst finalizes or a bound stops it.

What this module must never do, and has no code for:
  * repair a query, a plan, a field reference or an answer;
  * compute a business number when execution failed;
  * fall back to V3, to a canned decomposition, or to another model;
  * claim an answer was delivered because it was generated.

Every loop back to `CONTEXT_READY` passes through a ledger call that consumes
something finite. There is no `continue` in this file that does not.
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from datetime import datetime, timedelta, timezone

from backend.cockpit_v4 import DEEP, events as ev
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.artifacts import ArtifactService
from backend.cockpit_v4.budgets import BudgetExceeded, Ledger
from backend.cockpit_v4.catalog_tool import CatalogService
from backend.cockpit_v4.contracts import (BATCHABLE, MAX_BATCHED_READS,
                                          Rejection, TOOL_EXECUTE,
                                          TOOL_FINALIZE, TOOL_INSPECT,
                                          TOOL_NAMES, TOOL_READ,
                                          parse_artifact, parse_catalog,
                                          parse_execution, parse_final,
                                          parse_intent,
                                          parse_product_knowledge,
                                          TOOL_PRODUCT, units_display)
from backend.cockpit_v4 import execute_tool as xt
from backend.cockpit_v4.run_store import redact as _store_redact
from backend.cockpit_v4.execute_tool import (ExecutionService,
                                             no_progress_key)
from backend.cockpit_v4.finalization import RESULT_ONLY_REASON, Finalizer
from backend.cockpit_v4.provider import (Analyst, InputTooLarge,
                                         OutputTruncated, ProviderFailure)
from backend.cockpit_v4.run_store import (LeaseLost, StorageUnavailable,
                                          TerminalAlready)


class Cancelled(RuntimeError):
    """The user cancelled. Wins the race with a terminal answer atomically."""


@dataclass
class Outcome:
    state: str
    error_code: str = ""
    error_id: str = ""
    response: dict[str, Any] | None = None
    message: str = ""
    #: True when the orchestrator has already emitted the run's terminal
    #: failure event, with the stage it actually failed at. The worker then
    #: does not emit a second one. Two RUN_FAILED events for one run read as
    #: two failures, and the second always says "publishing" -- which is the
    #: exact misreading that sent an operator hunting for a delivery problem
    #: while the provider had refused the request before any inference.
    terminal_event_emitted: bool = False
    #: Every generation attempt this run made: purpose, phase, what the
    #: request measured, what the response was allowed, how it stopped and
    #: how long the provider took. Attached on every terminal path, because
    #: the runs worth explaining are the ones that did not finish.
    call_report: dict[str, Any] = field(default_factory=dict)


#: What each model-call purpose is called in front of a reader.
#:
#: The taxonomy exists so that "why was model call #3 made?" has an answer in
#: the trace. A reader watching the panel should be able to tell an analysis
#: round from a re-ask for a cut-off response, and a re-ask for the ACTION
#: from a re-ask for the ANSWER -- those are different failures with different
#: allowances, and a live run stopped because they shared one.
_PURPOSE_MESSAGE: dict[str, str] = {
    "ANALYSIS_ACTION": "Preparing the next action",
    # The retry notice has already said WHAT went wrong. These say what is
    # being asked for now, so the panel does not print the same sentence
    # twice in a row with nothing between them.
    "ACTION_FORMAT_RECOVERY": "Asking again for a complete action",
    "FINAL_ANSWER": "Writing the answer from the result",
    "ANSWER_FORMAT_RECOVERY": "Asking again for a complete answer",
    "ANSWER_CORRECTION": "Correcting the written answer against the result",
}


#: WHY the written answer never arrived. Lives with `result_only_response`,
#: because the SUPERVISOR publishes the same way for the same reasons and
#: two tables of the same sentences are two tables that can disagree.
_RESULT_ONLY_REASON = RESULT_ONLY_REASON


#: Which disposition settles into which terminal state.
_DISPOSITION_STATE = {
    "answer": st.COMPLETED, "partial_answer": st.PARTIAL,
    "clarification": st.WAITING_FOR_USER, "referral": st.REFERRED,
    "unsupported": st.UNSUPPORTED, "safe_failure": st.FAILED,
}


@dataclass
class Orchestrator:
    """One run, start to terminal state."""

    run: Any
    store: Any
    ledger: Ledger
    analyst: Analyst
    catalog_service: CatalogService
    execution_service: ExecutionService
    artifact_service: ArtifactService
    finalizer: Finalizer
    emitter: ev.Emitter
    catalog: Any
    cancel_check: Any = None
    #: The full tool set, held back from the FIRST generation only. Set by
    #: the worker when `product_knowledge.coverage` says the synopsis
    #: already covers the question; restored before the second action so a
    #: misjudged question is never left without the tool it needs.
    deferred_tools: list[dict[str, Any]] | None = None
    #: The FULL tool contract, for the turn that writes the answer. Action
    #: turns run on the stop-early subset of `finalize_response`; this is
    #: what comes back once a result exists and there is an answer to write.
    answer_tools: list[dict[str, Any]] | None = None
    #: `semantics.readiness` for this question, computed by the server
    #: before any provider call. The action state machine reads it.
    readiness: dict[str, Any] = field(default_factory=dict)
    #: True when this request was classified as a data analysis at intake.
    analytical: bool = False
    #: What the last turn was allowed to be. Carried so a recovery can be
    #: NARROWER than the attempt it recovers, and never broader.
    decision: Any = None
    #: Set once the catalogue has answered, so NEEDS_METADATA is a state a
    #: run passes THROUGH rather than one it can sit in.
    catalog_answered: bool = False
    #: True once a validated answer has actually been published. Guards the
    #: result-only channel: a terminal stop AFTER a successful publication
    #: must not overwrite the answer with a bare table.
    answer_published: bool = False
    #: step_id -> the purpose the analyst gave it. Titles for a result
    #: published without a written answer. See `_result_only_response`.
    step_purposes: dict[str, str] = field(default_factory=dict)
    #: True once an ACTION turn has been cut off at its allowance. The
    #: re-ask then gets the larger one: a truncation is direct evidence
    #: that the allowance was the binding constraint on that turn, and it
    #: is the only lever left once the schemas, the context, the tool
    #: ambiguity and the effort have all been taken away.
    action_truncated: bool = False
    #: True once the FINAL ANSWER turn has been cut off at its allowance.
    #: Its twin, for the phase the live failure was in. Sticky, like the
    #: action side: an allowance proven too small once is too small for the
    #: rest of the run.
    answer_truncated: bool = False
    #: The attention item this conversation was opened from, if any.
    investigation: dict[str, Any] | None = None
    #: What the QUESTION'S OWN WORDS were resolved to against the values this
    #: release holds, computed by `context.build` before any provider call.
    #: Merged into the declared intent so the resolution is in the run's
    #: semantics whether or not the analyst thought to repeat it.
    value_resolution: dict[str, Any] = field(default_factory=dict)
    #: Set once the analyst has been told to correct only its answer.
    answer_only: bool = False
    #: Set once the finalization reserve has been handed over. Once per run:
    #: see `_demand_an_answer_if_time_is_short`.
    _answer_demanded: bool = False
    executed: bool = False
    #: The latest batch failed a step after this run already had a result,
    #: and submissions remain: the next turn may correct it.
    repair_open: bool = False
    #: claim_id -> `answer_draft.Drafted`, for every planned result this run
    #: produced. Attached to the answer only where its narrative uses them.
    drafted: dict[str, Any] = field(default_factory=dict)
    #: step_id -> `plan_compiler.StepShape` of the submission being run.
    _plan_shapes: dict[str, Any] = field(default_factory=dict)
    _analysis_preserved: bool = False
    #: What the NEXT provider generation is for. Recorded on the ledger and
    #: on the trace so "why was model call #3 made?" has an answer, and used
    #: to charge a structure recovery to the right phase.
    purpose: str = "ANALYSIS_ACTION"
    #: Filled on every terminal path. See `_build_call_report`.
    call_report: dict[str, Any] = field(default_factory=dict)
    #: Product-knowledge reads are cheap and bounded separately from the
    #: catalog: they touch no borrower data and open no analysis round.
    product_calls: int = 0
    #: §24-§27. What this run IS, settled by the server before the first
    #: provider call: the book, the release, the kind of turn, the calendar
    #: and the category values the question named. The analyst is never
    #: asked to restate any of it -- `intent` is not a field on any tool
    #: schema -- so the run must OWN one from the start or the first tool
    #: call has no carried intent to merge into.
    envelope: Any = None
    intent: Any = None
    #: Set once the analytical allowance has been adopted, so it happens
    #: exactly once however many times the intent is re-declared.
    _analytical: bool = False
    #: The first analytical failure in this run, kept so a later deadline or
    #: cost stop cannot bury it.
    first_failure: dict[str, Any] | None = None
    #: Consecutive inspect_catalog calls that added nothing.
    barren_catalog_calls: int = 0
    #: Set when a submission is refused for a blocking ambiguity the ANALYST
    #: declared. The run then stops being offered `execute_analysis` and can
    #: only publish -- which is the one state a clarification is reachable
    #: from. Without it the gate required execution, the refusal advised
    #: moving the doubt into `resolved_assumptions`, and the only move left
    #: was to guess. See `action_state.NEEDS_CLARIFICATION`.
    must_clarify: bool = False
    #: Set once the clarification gate has refused an unearned question. One
    #: refusal per run: a second clarification always goes through, so the
    #: gate can cost a run one generation and never the reader their answer.
    _clarify_gate_used: bool = False
    _version: int = 0

    # -- helpers ---------------------------------------------------------

    def _cancelled(self) -> bool:
        if callable(self.cancel_check) and self.cancel_check():
            return True
        record = self.store.get_run(self.run.run_id)
        return bool(record and record.cancel_requested)

    def _guard(self) -> None:
        if self._cancelled():
            raise Cancelled("cancelled by the user")
        self.ledger.check_deadline()

    def _advance(self, state: str, *, operation: str = "") -> None:
        record = self.store.update_state(
            self.run.run_id, expect_version=self._version, state=state,
            operation=operation, budget=self.ledger.snapshot())
        self._version = record.version

    def _detail(self, body: dict[str, Any]) -> str:
        """Operator-only record. Redacted before it is written."""
        return self.store.put_detail(self.run.run_id, _redact(body))

    # -- the run ---------------------------------------------------------

    def run_to_completion(self) -> Outcome:
        """One run, start to terminal state, with its call report attached.

        The report is attached on EVERY path. A run that completed rarely
        needs explaining; a run that stopped at a limit always does, and the
        question is almost never "which limit" -- it is which calls were
        made, what each one was for, how big each request had grown and where
        the wall-clock actually went.
        """
        try:
            return self._with_report(self._run_to_completion())
        except BaseException:
            # A path that escapes entirely still leaves the report on the
            # orchestrator for the worker and the trace to read.
            self.call_report = self._build_call_report()
            raise

    def _with_report(self, outcome: Outcome) -> Outcome:
        self.call_report = self._build_call_report()
        outcome.call_report = self.call_report
        try:
            self._detail({"call_report": self.call_report})
        except Exception:  # noqa: BLE001 - observability is never the failure
            pass
        return outcome

    def _build_call_report(self) -> dict[str, Any]:
        """Provider time and CreditProbe time, separated.

        A run that overran is a different defect depending on which of the
        two grew. Before this the only figure available was the total, so
        "the model was slow" and "we spent the deadline assembling requests"
        were indistinguishable from the outside.
        """
        try:
            report = self.analyst.call_report()
        except Exception:  # noqa: BLE001
            return {}
        elapsed_ms = max(0, int(self.ledger.elapsed_seconds * 1000))
        provider_ms = int(report.get("provider_ms") or 0)
        report["elapsed_ms"] = elapsed_ms
        report["local_ms"] = max(0, elapsed_ms - provider_ms)
        report["deadline_seconds"] = self.ledger.limits.deadline_seconds
        report["executed"] = self.executed
        report["analysis_preserved"] = self._analysis_preserved
        return report

    def _run_to_completion(self) -> Outcome:
        try:
            return self._loop()
        except Cancelled:
            self.ledger.cancel()
            self.emitter.append(
                ev.RUN_CANCELLED, stage="publishing", operation="cancel",
                status=ev.STATUS_OK,
                public_message="Cancelled at your request.")
            return Outcome(st.CANCELLED, error_code=st.CANCELLED_BY_USER)
        except BudgetExceeded as exc:
            return self._stop(exc.code, str(exc))
        except InputTooLarge as exc:
            return self._stop(exc.code, str(exc))
        except OutputTruncated as exc:
            return self._stop(exc.code, str(exc))
        except ProviderFailure as exc:
            if exc.code in (st.PROVIDER_REQUEST_INVALID,
                            st.TOOL_SCHEMA_INVALID):
                return self._provider_rejected(exc)
            return self._stop(exc.code, str(exc))
        except (StorageUnavailable, LeaseLost, TerminalAlready) as exc:
            # No further paid or executable operation is launched when the
            # store cannot commit or this worker has been fenced.
            #
            # The reference is minted in memory, because the store is exactly
            # what may be unavailable. It reaches the reader through the
            # outcome whether or not the event can be written, and the event
            # is attempted rather than assumed: a store that is merely
            # refusing one table can still record why the run stopped.
            fenced = isinstance(exc, (LeaseLost, TerminalAlready))
            error_id = f"err-{uuid.uuid4().hex[:12]}"
            message = f"{exc} Reference {error_id}."
            if not fenced:
                try:
                    self.emitter.append(
                        ev.RUN_FAILED, stage="publishing",
                        operation=st.STORAGE_UNAVAILABLE.lower(),
                        status=ev.STATUS_FAILED, error_id=error_id,
                        public_message=message)
                except Exception:                             # noqa: BLE001
                    pass
            return Outcome(
                st.INTERRUPTED if fenced else st.FAILED,
                error_code=st.WORKER_LOST if fenced else st.STORAGE_UNAVAILABLE,
                error_id=error_id, message=message,
                terminal_event_emitted=not fenced)
        except Exception as exc:  # noqa: BLE001
            # An application defect. Recorded under an error id that is
            # PERSISTED (V3's was only ever written to a log line), with the
            # operation it failed in, and never blamed on the provider.
            error_id = f"err-{uuid.uuid4().hex[:12]}"
            detail_ref = ""
            try:
                detail_ref = self._detail({
                    "exception_type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "operation": getattr(self.run, "operation", ""),
                })
            except Exception:  # noqa: BLE001
                pass
            self.emitter.append(
                ev.RUN_FAILED, stage="publishing", operation="internal",
                status=ev.STATUS_FAILED, error_id=error_id,
                detail_ref=detail_ref,
                public_message=("Something inside CreditProbe failed while "
                                "handling this request. Nothing was answered "
                                "from a substitute."))
            return Outcome(st.FAILED, error_code=st.INTERNAL_ERROR,
                           error_id=error_id, message=str(exc)[:300])

    def _provider_rejected(self, exc) -> Outcome:
        """A malformed request is not a failed answer.

        The panel used to show this at the publishing stage, which reads as
        "we produced an answer and could not deliver it". Nothing had been
        produced: the provider refused the request before the model saw it.
        """
        detail = dict(getattr(exc, "detail", None) or {})
        error_id = f"err-{uuid.uuid4().hex[:12]}"
        self.ledger.cancel()
        self.emitter.append(
            ev.RUN_FAILED, stage="understanding",
            operation="provider_request", status=ev.STATUS_FAILED,
            error_id=error_id,
            detail_ref=self._detail({"error_code": exc.code, **detail}),
            public_message=(
                f"{str(exc)} Reference {error_id}."))
        return Outcome(st.FAILED, error_code=exc.code, error_id=error_id,
                       message=str(exc), terminal_event_emitted=True)

    def _preserve_analysis(self, reason: str) -> None:
        """Say that the mathematics succeeded, whatever stopped afterwards.

        Emitted on ANY terminal stop that follows a successful execution --
        a deadline, a cost ceiling, an exhausted correction. A run reported
        only as "failed" reads as if the query failed, and in the live case
        the query had already returned its rows.
        """
        if not (self.executed and self.finalizer.run_artifacts):
            return
        if self._analysis_preserved:
            return
        self._analysis_preserved = True
        try:
            self.emitter.append(
                ev.ANALYSIS_PRESERVED, stage="publishing",
                operation="preserve_result", status=ev.STATUS_OK,
                public_message=(
                    "The analysis completed and its result was kept. "
                    "Publication of the written answer is what stopped."),
                detail_ref=self._detail({
                    "artifact_ids": sorted(self.finalizer.run_artifacts),
                    "analysis_status": "completed",
                    "publication_status": "failed", "reason": reason}))
        except Exception:                                     # noqa: BLE001
            pass

    def _stop(self, code: str, message: str) -> Outcome:
        """A mechanical stop, generated from the error record.

        Never a fabricated analytical answer: when no model call remains
        affordable, CreditProbe states the reason and stops.

        The terminal code is the bound that ran out, but it is rarely the
        interesting fact. A run that hit a binder failure, retried, and then
        ran out of time stops as DEADLINE_EXPIRED -- and "what went wrong
        first" is the binder failure. Both are reported.

        An OPERATOR-class stop also mints a reference. Rephrasing the question
        will not fix a rejected credential, an unprepared state database or a
        missing model configuration, so the reader needs something to quote to
        somebody who can fix it. Those codes reached this path without one,
        which left the person in front of the screen with nothing to carry to
        an operator.
        """
        self.ledger.cancel()
        self._preserve_analysis(code)
        if self.first_failure:
            message = (f"{message} The first analytical failure in this run "
                       f"was: {self.first_failure['summary']}")
        state = st.EXPIRED if code == st.DEADLINE_EXPIRED else st.FAILED
        # THE RESULT, PUBLISHED WITHOUT ITS WRITE-UP.
        #
        # A run that executed its query and then could not write the answer
        # about it has a result and no way to show it: the only channel a
        # result reaches a reader through is the written answer object, and
        # failing to produce that object is exactly what happened. So the
        # object is built here from the stored artifacts instead, with a
        # server-written caveat and no numeric claims.
        #
        # `_RESULT_ONLY_REASON` is the list of codes this applies to, and
        # it is also the promotion list below -- ONE set, so a run can never
        # settle as FAILED while carrying a published result, or settle as
        # PARTIAL with nothing to show for it. It now includes the two
        # format exhaustions and the output limit: the codes that mean "the
        # analysis worked and the writing did not", which is the case where
        # a reader most needs the rows and used to get a red box.
        response = self._result_only_response(code)
        if self.executed and code in _RESULT_ONLY_REASON:
            # Verified partial evidence exists; it is preserved and labelled.
            state = st.PARTIAL
        error_id = ""
        if code in st.OPERATOR_CODES and state != st.PARTIAL:
            error_id = f"err-{uuid.uuid4().hex[:12]}"
            message = f"{message} Reference {error_id}."
        self.emitter.append(
            ev.RUN_EXPIRED if state == st.EXPIRED else ev.RUN_FAILED,
            stage="publishing", operation=code.lower(),
            status=ev.STATUS_FAILED, public_message=message,
            error_id=error_id,
            detail_ref=(self._detail({"terminal_code": code,
                                      "first_analytical_failure":
                                      self.first_failure})
                        if self.first_failure else ""))
        return Outcome(state, error_code=code, error_id=error_id,
                       message=message, response=response,
                       terminal_event_emitted=True)

    def _result_only_response(self, code: str,
                              rejected: Any = None) -> dict[str, Any] | None:
        """The stored result, shaped as a response, or nothing.

        Nothing is the right answer for a run that executed nothing, and
        for one that already published an answer -- a terminal stop after a
        successful publication is not this case and must not overwrite it.

        `rejected` is the answer that failed its evidence check, when there
        is one. Its CHARTS are re-checked and republished; its narrative and
        claims are not. A stop with no answer object -- a deadline, a cost
        ceiling -- passes nothing and publishes tables alone, as before.
        """
        if self.answer_published or not self.executed:
            return None
        if not self.finalizer.run_artifacts:
            return None
        reason = _RESULT_ONLY_REASON.get(code, "")
        if not reason:
            # The codes that mean "the analysis worked and the writing did
            # not", which now includes an answer REJECTED twice by its own
            # evidence check. Everything published here is read back out of
            # the artifact store; the narrative the analyst wrote is
            # discarded whether or not the rows go out with it.
            return None
        try:
            body = self.finalizer.result_only_response(
                reason=reason, purposes=self.step_purposes,
                catalog=self.catalog, intent=self.intent,
                rejected=rejected)
        except Exception:                                     # noqa: BLE001
            # A result that cannot be rendered must not turn a stop into a
            # crash. The run still fails; it fails the way it did before.
            return None
        if not body:
            return None
        # WHAT WENT WRONG, WHERE THE READER IS. The first analytical failure
        # is already kept so a later stop cannot bury it, and `_stop` puts it
        # in the run's message -- which a reader does not see next to the
        # rows. A live run whose first submission failed to parse published
        # its result with no hint that anything had; the reason sat in the
        # process panel, one click away, for whoever thought to look.
        if self.first_failure:
            summary = str(self.first_failure.get("summary") or "").strip()
            if summary:
                body.setdefault("limitations", []).append(
                    f"The first thing that went wrong in this run: {summary}")
        count = len(body.get("tables") or [])
        self.emitter.append(
            ev.ANSWER_VALIDATED, stage="publishing",
            operation="publish_result_only", status=ev.STATUS_OK,
            public_message=(
                f"The written answer could not be completed, so the result "
                f"of the analysis is published on its own: "
                f"{count} {'table' if count == 1 else 'tables'} from the "
                f"query this run executed."))
        return body

    def _loop(self) -> Outcome:
        self.emitter.append(
            ev.RUN_STARTED, stage="accepted", operation="start",
            status=ev.STATUS_OK,
            public_message="Working on your question.")
        self.emitter.append(
            ev.CONTEXT_READY, stage="accepted", operation="context",
            status=ev.STATUS_OK,
            public_message="Authorized context assembled.")
        # §24. The run owns its intent before it spends anything.
        self._seed_intent()
        if self.investigation:
            # A real step, so it appears in the trace. Seeding is a context
            # load, not a hidden model action, and the panel says which.
            self.emitter.append(
                ev.CONTEXT_READY, stage="accepted",
                operation="investigation_context", status=ev.STATUS_OK,
                public_message=(
                    "Investigation context loaded · "
                    f"{self.investigation.get('segment_label') or self.investigation.get('segment', '')} · "
                    f"{self.investigation.get('reporting_period') or self.investigation.get('reporting_quarter') or self.investigation.get('reporting_month', '')}"
                    ).strip(" ·"))

        while True:
            self._guard()
            self._demand_an_answer_if_time_is_short()
            self._advance(st.MODEL_RUNNING, operation="generation")
            turn = self._generate()
            if turn is None:
                continue  # a bounded recovery consumed its allowance

            self._advance(st.ACTION_VALIDATING, operation="action")
            outcome = self._handle_turn(turn)
            if outcome is not None:
                return outcome

    def _demand_an_answer_if_time_is_short(self) -> None:
        """Spend the last seconds WRITING, not discovering there is nothing.

        The reserve was always there and always held back. What was missing
        is the turn it was held back FOR: when the action window closed, the
        run raised DEADLINE_EXPIRED on its next call and published nothing --
        so twenty seconds were reserved to write an answer and then no answer
        was written.

        This is not a fabricated answer and it does not invent a number. It
        is one bounded turn, with only `finalize_response` on it, telling the
        analyst what it has and what time it has left. A concise answer from
        verified evidence, or an honest statement of what was established and
        what was not, both beat a bare deadline notice -- and if the analyst
        has nothing worth publishing it says so, in its own words, which is
        still more than the reader was getting.

        Once only. A run that has already been asked for its answer and came
        back with another action is out of moves, and the deadline takes it.
        """
        if self._answer_demanded or self.answer_only:
            return
        if not self.ledger.action_window_closed():
            return
        if not self.ledger.answer_window_open():
            return
        self._answer_demanded = True
        self.answer_only = True
        self.purpose = "FINAL_ANSWER"
        remaining = self.ledger.remaining_seconds
        self.emitter.append(
            ev.CONTEXT_READY, stage="publishing", operation="answer_reserve",
            status=ev.STATUS_OK,
            public_message=(
                f"{remaining:.0f}s remain — writing the answer from what has "
                f"been established."),
            detail_ref=self._detail({
                "reserve_seconds":
                    self.ledger.limits.finalization_reserve_seconds,
                "remaining_seconds": round(remaining, 2),
                "executed": self.executed}))
        self.analyst.user(
            f"There is no time left for another action: about "
            f"{remaining:.0f} seconds remain of this run's "
            f"{self.ledger.limits.deadline_seconds:.0f}-second allowance, "
            f"and they are reserved for writing the answer. Send "
            f"finalize_response now and send nothing else.\n\n"
            + ("Answer the question from the evidence you have already "
               "executed. Keep it short. Every number must still be bound to "
               "a real row of a stored result — state fewer figures rather "
               "than any unbound one. If what you executed only partly "
               "answers the question, say so in `limitations` and use the "
               "`partial_answer` disposition."
               if self.executed else
               "No analysis was executed in this run, so there is no figure "
               "to state. Do not invent one and do not describe a query you "
               "did not run. Say what the question was understood to mean, "
               "what would answer it, and that this run stopped before it "
               "could be answered. Use the `safe_failure` disposition.\n\n"
               # A RUN THAT GOT NOWHERE OFTEN KNOWS WHY. Where the reason
               # is that the question admits two readings, "this stopped
               # before it could be answered" wastes what the run learned:
               # the reader is told nothing and must guess what to change.
               # One question with concrete options is a better use of the
               # same sentence, and the next run starts from an answer.
               "If the reason you got no further is that the question has "
               "two defensible readings, ask instead of reporting a dead "
               "end: use the `clarification` disposition, put the one "
               "question in `clarification_question`, and give the concrete "
               "choices in `clarification_options`."))

    # -- generation ------------------------------------------------------

    def _action_surface(self, *, recovering: bool = False):
        """Which tools this turn may use, and which one it must call.

        The state machine decides; this turns its decision into the tool
        definitions and hands them to the analyst for one call.

        `recovering` is the whole of the live defect: a re-ask after a
        malformed action may never be a broader question than the one that
        failed. The live run restored a withheld tool between its two
        attempts and told the reader "Product knowledge lookup available" --
        so the turn that had just run out of output while deciding was
        handed one more thing to decide about.
        """
        from backend.cockpit_v4 import action_state as acts
        from backend.cockpit_v4.contracts import provider_tools

        # The product pack is withheld from the FIRST action of a broad
        # product question and restored for every action after it -- that
        # is a first-action economy, not a capability the run can lose.
        #
        # A RECOVERY is the exception, and it is the whole of the live
        # defect: the re-ask after a malformed action must never be a
        # broader question than the one that failed.
        first_action = self.ledger.counters.generation_attempts < 1
        withheld = (self.deferred_tools is not None
                    and (first_action or recovering))
        decision = acts.decide(
            executed=self.executed, answer_only=self.answer_only,
            analytical=self.analytical, readiness=self.readiness,
            product_tool_withheld=withheld,
            must_clarify=self.must_clarify,
            repair_open=self.repair_open)
        if self.catalog_answered:
            decision = acts.after_catalog(decision, self.readiness)
        if recovering:
            decision = decision.narrow()
        self.decision = decision

        names = acts.offered(decision)
        answering = decision.state == acts.RESULT_READY
        plan, note = self._plan_offer() if TOOL_EXECUTE in names else (None, "")
        tools = provider_tools(
            catalog=self.catalog, only=names,
            stage="" if answering else "analytical_action",
            max_steps=self.ledger.limits.steps_per_batch,
            plan=plan, plan_note=note)
        return decision, tools

    def _generate(self):
        # WHICH KIND OF TURN THIS IS, what it may call, and what it may
        # cost.
        #
        # An action is a decision: which tool, with which arguments. An
        # answer is a document. They were given the same allowance AND the
        # same five tools, so an action turn could spend an answer's worth
        # of output deciding among tools that could not legally do anything
        # in the state the run was in.
        from backend.cockpit_v4 import action_state as acts

        recovering = self.purpose in ("ACTION_FORMAT_RECOVERY",
                                      "ANSWER_FORMAT_RECOVERY")
        decision, tools = self._action_surface(recovering=recovering)
        answering = decision.state == acts.RESULT_READY
        limits = self.ledger.limits
        if answering and self.answer_truncated:
            # THE RE-ASK MUST DIFFER FROM THE REQUEST THAT FAILED.
            #
            # It used to be the identical call with "keep it compact"
            # appended, which is the one instruction that cannot help: a
            # correct answer to a four-step analysis measures ~5,700 tokens
            # before the model thinks, and the allowance was 4,096. Asking
            # again at the same size asks for the impossible twice.
            #
            # So the re-ask gets more room AND spends less of it thinking.
            # By this point the analysis has run and the numbers are chosen;
            # what is left is serialising a decision already taken, which is
            # the cheapest kind of work there is.
            reserved = max(limits.reserved_output_tokens,
                           limits.answer_output_ceiling)
            effort = limits.answer_recovery_effort or limits.answer_effort
        elif answering:
            reserved = limits.reserved_output_tokens
            effort = limits.answer_effort
        elif self.action_truncated:
            reserved = max(limits.action_output_tokens,
                           limits.action_output_ceiling)
            effort = limits.action_effort
        else:
            reserved = limits.action_output_tokens
            effort = limits.action_effort

        if answering and self.purpose == "ANALYSIS_ACTION":
            self.purpose = "FINAL_ANSWER"
        restore_tools = self.analyst.tools
        restore_system = None
        self.analyst.tools = tools
        domain_id = str(getattr(self.envelope, "domain_id", "") or "")
        if answering:
            from backend.cockpit_v4 import context as ctx

            compact = ctx.finalization_system(
                self.analyst.system, domain_id=domain_id,
                question=self._asked(), undecided=self._undecided_terms())
            if compact is not self.analyst.system:
                restore_system = self.analyst.system
                self.analyst.system = compact
        elif acts.TOOL_FINALIZE in decision.tools:
            # THE CREDIT POLICY REACHES EVERY TURN THAT CAN WRITE THE ANSWER.
            #
            # H-LIVE-05. The policy pack was attached by
            # `finalization_system`, which runs only when the action state
            # is RESULT_READY. A PRODUCT_HELP turn publishes a user-facing
            # answer too, and it got no policy at all -- so the live answer
            # to "Which sectors are above the single-name limit?" was
            # written with an empty policy context and stated that no
            # single-name limit is recorded in the Corporate book. CP-1.1
            # is recorded, its keyword map holds "single name", and
            # `cp.retrieve` returns it for that exact question.
            #
            # An ACTION turn still gets none: it is choosing what to run
            # and holds no number, so a threshold in front of it is a
            # threshold nothing can be measured against. The condition is
            # therefore the surface -- can this turn call
            # `finalize_response` -- and not whether a query happened to
            # run.
            from backend.cockpit_v4 import context as ctx

            extra = ctx.policy_blocks(domain_id=domain_id,
                                      question=self._asked(),
                                      undecided=self._undecided_terms())
            # AND THE SHAPE A PRODUCT ANSWER WANTS REACHES THE TURN THAT
            # WRITES ONE. The four outlines used to sit in `analyst.md`,
            # which every action attempt carries and pays for; an action
            # turn is choosing what to run and can write no product answer
            # at all. They belong here, on the one turn that can act on
            # them, exactly like the chart policy and the policy pack.
            if decision.state == acts.PRODUCT_HELP:
                extra = extra + ctx.product_blocks()
            if extra:
                restore_system = self.analyst.system
                self.analyst.system = list(self.analyst.system) + extra
        self.emitter.append(
            ev.MODEL_REQUESTED, stage="understanding", operation="generate",
            status=ev.STATUS_STARTED,
            attempt=self.ledger.counters.generation_attempts + 1,
            detail_ref=self._detail({
                "purpose": self.purpose,
                "phase": "answer" if answering else "action",
                "action_state": decision.to_dict(),
                "tools_offered": [t.get("name") for t in tools],
                "required_tool": decision.require,
                "max_output_tokens": reserved,
                "effort": effort,
                "context_bytes": self.analyst.payload_bytes()}),
            public_message=_PURPOSE_MESSAGE.get(self.purpose,
                                                decision.stage))
        try:
            try:
                turn = self.analyst.ask(
                    purpose=self.purpose, max_output_tokens=reserved,
                    phase="answer" if answering else "action",
                    effort=effort, require=decision.require)
            finally:
                # The surface is per CALL and is rebuilt from the state on
                # the next one, so nothing here can leave a run holding a
                # narrower set than its own state allows.
                self.analyst.tools = restore_tools
                if restore_system is not None:
                    self.analyst.system = restore_system
        except OutputTruncated as exc:
            # One structure regeneration PER PHASE, and the incomplete turn
            # is not in history. Nothing from a truncated response executes.
            #
            # The phase matters. A truncated ACTION happens before any work
            # exists; a truncated ANSWER happens after the SQL has run and
            # been paid for. Charging both to one counter is what refused a
            # live run whose query had already executed correctly.
            phase = "answer" if self.executed else "action"
            # WHICH LATCH, AND WHY IT IS NOT `phase`.
            #
            # `phase` above answers "whose recovery budget pays for this",
            # and it asks `self.executed` because the cost of a truncation
            # is the work already done behind it. The LATCH answers a
            # different question -- "which allowance was too small" -- and
            # the allowance was chosen by `answering`, a few lines up. The
            # two predicates agree on every ordinary turn and disagree on an
            # action issued after a successful query, where the counter
            # should charge the answer phase and the allowance raised must
            # be the action's. Each follows the thing it is about.
            if answering:
                self.answer_truncated = True
            else:
                self.action_truncated = True
            self.ledger.spend_format_recovery(phase=phase)
            self.analyst.rollback_last_turn()
            self.purpose = ("ANSWER_FORMAT_RECOVERY" if phase == "answer"
                            else "ACTION_FORMAT_RECOVERY")
            # The number the re-ask is entitled to name, taken from the
            # same predicate the allowance ladder used. Naming the answer
            # ceiling on an ACTION re-ask would be a promise the next call
            # does not keep -- which is the divergent case the latch
            # comment above is about, said out loud.
            raised = (max(limits.reserved_output_tokens,
                          limits.answer_output_ceiling) if answering else
                      max(limits.action_output_tokens,
                          limits.action_output_ceiling))
            self.emitter.append(
                ev.RETRY_REQUESTED,
                stage="publishing" if phase == "answer" else "understanding",
                operation="output_truncated", status=ev.STATUS_REJECTED,
                detail_ref=self._detail({
                    "phase": phase, "answering": answering,
                    "overran": exc.limit, "next_allowance": raised}),
                public_message=(
                    f"The written answer was cut off before it was "
                    f"complete; asking again with a larger allowance "
                    f"({raised:,} tokens). The analysis is unaffected and "
                    f"its result is preserved."
                    if answering else
                    "The response was cut off before it was complete; "
                    "asking again once."))
            reason = (f"it reached its {exc.limit:,}-token output "
                      f"allowance before a tool call was complete, so "
                      f"nothing from it ran")
            # WHAT THE RE-ASK IS TOLD.
            #
            # It used to be told to "keep it compact", which asks the model
            # to solve a problem it cannot: the object that overran was the
            # right size for the analysis, and the allowance was the thing
            # that was wrong. Telling it to shrink invites a shorter answer
            # about the same result -- fewer claims, fewer rows -- which is
            # a worse answer, not a shorter one. So the re-ask says what
            # actually changed, and what NOT to do with it.
            self.analyst.user(
                self._action_recovery_prompt(reason)
                if not answering else
                f"Your previous response was cut off at its "
                f"{exc.limit:,}-token output allowance and nothing from it "
                f"ran. The allowance for this attempt is larger: "
                f"{raised:,} tokens. Send the complete finalize_response "
                f"call. Do NOT drop claims, rows or coverage to make it "
                f"fit -- the analysis is already done and paid for, and a "
                f"shorter answer about it is a worse one. Spend the room on "
                f"the object rather than on reasoning about it: what to say "
                f"was settled by the result you already have.")
            return None
        except ProviderFailure as exc:
            if exc.retry_class == "transport":
                self.ledger.spend_transport_retry()
                stalled = bool((exc.detail or {}).get("timed_out"))
                self.emitter.append(
                    ev.RETRY_REQUESTED,
                    stage="publishing" if self.executed else "understanding",
                    operation="timeout" if stalled else "transport",
                    status=ev.STATUS_REJECTED,
                    detail_ref=self._detail({"code": exc.code,
                                             "retry_class": exc.retry_class,
                                             "timed_out": stalled}),
                    public_message=(
                        # §18. What the reader is told is what happened: a
                        # turn that ran past the time one action is allowed
                        # is not the same event as a network fault, and
                        # neither of them is a call limit.
                        "That attempt ran past the time one action is "
                        "allowed; asking again once."
                        if stalled else
                        "The request did not complete; retrying once."))
                return None
            raise

        self.emitter.append(
            ev.MODEL_RESPONSE_RECEIVED, stage="understanding",
            operation="generate", status=ev.STATUS_OK,
            attempt=self.ledger.counters.generation_attempts,
            detail_ref=self._detail({
                "purpose": self.purpose,
                "model": turn.model, "request_id": turn.request_id,
                "stop_reason": turn.stop_reason,
                "counted_input_tokens": turn.counted_input_tokens,
                "count_method": turn.count_method,
                "reported_input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "cache_read_tokens": turn.cache_read_tokens,
                "cache_write_tokens": turn.cache_write_tokens,
                # WHAT THE CALL WAS ACTUALLY GRANTED.
                #
                # `MODEL_REQUESTED` above carries what was WANTED, because
                # it is emitted before the call. `affordable_output_tokens`
                # can then cut that down to fit the cost ceiling, and until
                # now the only record of it was one field on a call-report
                # row nothing reads. So the trace could say 12,288 while the
                # request sent 6,000 -- and a truncation at 6,000 looked
                # like the model overrunning a generous allowance rather
                # than the ledger handing it a smaller one.
                "output_allowance": dict(self.analyst.response_allowance),
                "duration_ms": turn.duration_ms}),
            public_message=self._allowance_message(answering))
        # The recovery label belongs to the call that recovers, and to no
        # call after it. Leaving it set was how a completed run reported its
        # final answer as a structure re-ask -- on the ledger reservation as
        # well as in the trace, so the cost of writing the answer was booked
        # against a failure that had already been repaired.
        self.purpose = "ANALYSIS_ACTION"
        self.store.save_messages(self.run.run_id, self.analyst.messages)
        return turn

    def _allowance_message(self, answering: bool) -> str:
        """"Response received", unless the ledger shrank the allowance.

        A reader whose answer came back cut off is owed the reason it was
        cut off, and "the cost ceiling decided how long this could be" is a
        different fact from "the model wrote too much". Only said when it is
        true, and only on the answer turn, where the length is what the
        reader experiences.
        """
        granted = self.analyst.response_allowance or {}
        if not (answering and granted.get("reduced")):
            return "Response received."
        return (f"Response received. The written answer was allowed "
                f"{int(granted.get('granted', 0)):,} tokens rather than the "
                f"{int(granted.get('wanted', 0)):,} this run reserves for "
                f"it, because that is what remained under the cost ceiling.")

    def _action_recovery_prompt(self, reason: str) -> str:
        """What to send after an ACTION turn failed to produce one.

        Five things, and deliberately nothing else: the exact reason the
        last attempt was unusable, the question in the user's own words, the
        governed semantics already resolved for it, the grain and period a
        query here reports on, and what a valid response is.

        What is NOT sent: the malformed output itself (the run paid for
        those tokens once; sending them back buys another chance to continue
        them), the product synopsis, the catalogue, any tool the state does
        not allow, and any part of the transcript this turn does not need.
        """
        question = self._asked()

        ready = dict(self.readiness or {})
        resolved = [str(m.get("field_id") or m.get("term") or "")
                    for m in
                    (ready.get("governed_measures_already_resolved") or [])]
        periods = dict(ready.get("periods_resolved") or {})
        required = str(getattr(self.decision, "require", "") or "")

        lines = [f"That attempt could not be used: {reason}", ""]
        if question:
            lines += [f"The request, unchanged: {question}", ""]
        if resolved:
            lines += ["Already resolved for you, in the opening context — do "
                      "not look any of it up and do not restate it: "
                      + ", ".join(resolved[:10]), ""]
        column = str(periods.get("period_column") or "")
        latest = str(periods.get("reporting") or "")
        if column or latest:
            said = f"This book reports on {column}." if column else ""
            if latest:
                said += f" Its latest populated period is {latest}."
            lines += [said.strip(), ""]
        if required:
            lines += [f"Reply with ONE {required} call and nothing else. No "
                      f"explanation, no plan, no summary — those belong in "
                      f"the final answer, after a result exists."]
        else:
            lines += ["Reply with ONE tool call and nothing else."]
        return "\n".join(lines).strip()

    # -- action handling -------------------------------------------------

    def _handle_turn(self, turn) -> Outcome | None:
        calls = turn.tool_calls
        if not calls:
            # Free prose is not silently promoted into a validated answer.
            phase = "answer" if self.executed else "action"
            self.analyst.annotate(parse_status="no_tool_call",
                                  usable=False,
                                  rejected_because="no tool call in the "
                                                   "response")
            self.ledger.spend_format_recovery(phase=phase)
            self.purpose = ("ANSWER_FORMAT_RECOVERY" if phase == "answer"
                            else "ACTION_FORMAT_RECOVERY")
            self.emitter.append(
                ev.RETRY_REQUESTED,
                stage="publishing" if phase == "answer" else "understanding",
                operation="no_tool_call", status=ev.STATUS_REJECTED,
                public_message=("That response did not contain an action; "
                                "asking again once."))
            if phase == "answer":
                self.analyst.user(
                    "That response contained no tool call. Every action, "
                    "including the final answer, is taken through one of: "
                    + ", ".join(TOOL_NAMES) + ".")
            else:
                self.analyst.user(self._action_recovery_prompt(
                    "it contained no tool call at all"))
            return None
        self.analyst.annotate(parse_status="tool_call", usable=True,
                              tool_names=[c.name for c in calls])

        unknown = [c.name for c in calls if c.name not in TOOL_NAMES]
        if unknown:
            # Name what THIS turn offered, not every tool there is: listing
            # tools the turn cannot call invites a second wrong call. And say
            # how to do what an invented tool usually wants -- ask the reader.
            offered = [str(t.get("name") or "")
                       for t in getattr(self.analyst, "tools", None) or []] or list(TOOL_NAMES)
            hint = (" To ask the reader a question, call finalize_response "
                    "with disposition 'clarification'."
                    if TOOL_FINALIZE in offered else "")
            return self._reject_batch(
                calls, f"unknown tool(s) {unknown}; the tools available on "
                       f"this turn are {offered}.{hint}")

        names = {c.name for c in calls}
        if len(calls) > 1:
            if not names <= BATCHABLE:
                # Mixed execution/finalization batches are refused WITHOUT
                # side effects: there is no defined order and no safe partial
                # outcome for "run this query and also publish the answer".
                return self._reject_batch(
                    calls,
                    "only metadata and artifact reads may be batched. An "
                    "execution or a final response is one action per "
                    "response, and this batch had no side effects.")
            if len(calls) > MAX_BATCHED_READS:
                return self._reject_batch(
                    calls, f"at most {MAX_BATCHED_READS} reads may be "
                           f"batched; {len(calls)} were requested.")

        self.emitter.append(
            ev.MODEL_PARSED, stage="understanding", operation="parse",
            status=ev.STATUS_OK,
            public_message=f"Next action: {', '.join(sorted(names))}.")
        if TOOL_INSPECT in names:
            # NEEDS_METADATA is a state a run passes THROUGH. It asked for
            # the field facts it said were missing; once they are in hand
            # the next legal transition is to use them. A run that could
            # ask again and again is the run that read the catalogue twice
            # and answered nothing.
            self.catalog_answered = True

        for call in calls:
            outcome = self._handle_call(call)
            if outcome is not None:
                return outcome
        return None

    def _reject_batch(self, calls, message: str) -> None:
        """Answer every call with a matching error. No side effects."""
        self.ledger.spend_format_recovery(
            phase="answer" if self.executed else "action")
        for call in calls:
            self.analyst.tool_result(
                call.id, {"status": "rejected",
                          "error_code": st.INVALID_MODEL_OUTPUT,
                          "message": message}, is_error=True)
        self.emitter.append(
            ev.TOOL_FAILED, stage="understanding", operation="batch",
            status=ev.STATUS_REJECTED, public_message=message)
        return None

    def _handle_call(self, call) -> Outcome | None:
        self.emitter.append(
            ev.TOOL_REQUESTED, stage=_stage_for(call.name),
            operation=call.name, status=ev.STATUS_STARTED,
            public_message=_public_for(call.name))

        if self.answer_only and call.name != TOOL_FINALIZE:
            self._tool_error(
                call, st.ANSWER_VALIDATION,
                "This run is in answer-correction mode: only "
                "finalize_response is available and no new analysis can be "
                "started.")
            return None

        try:
            if call.name == TOOL_INSPECT:
                return self._do_catalog(call)
            if call.name == TOOL_PRODUCT:
                return self._do_product_knowledge(call)
            if call.name == TOOL_READ:
                return self._do_artifact(call)
            if call.name == TOOL_EXECUTE:
                return self._do_execute(call)
            return self._do_finalize(call)
        except Rejection as exc:
            self._tool_error(call, exc.code, exc.message,
                             field_path=exc.field_path, detail=exc.detail)
            return None

    def _tool_error(self, call, code: str, message: str, *,
                    field_path: str = "",
                    detail: dict[str, Any] | None = None) -> None:
        body = {"status": "rejected", "error_code": code, "message": message}
        if field_path:
            body["field"] = field_path
        if detail:
            body["detail"] = detail
        body["budgets_remaining"] = self.ledger.snapshot()
        self.analyst.tool_result(call.id, body, is_error=True)
        self.emitter.append(
            ev.TOOL_FAILED, stage=_stage_for(call.name), operation=call.name,
            status=ev.STATUS_REJECTED, public_message=message[:300],
            detail_ref=self._detail({"tool": call.name, "code": code,
                                     "field": field_path,
                                     "message": message}))

    # -- inspect_catalog -------------------------------------------------

    def _do_catalog(self, call) -> None:
        request = parse_catalog(call.arguments, carried=self.intent)
        self._record_intent(request.intent)
        self.ledger.spend_catalog_call()
        self._advance(st.TOOL_RUNNING, operation=TOOL_INSPECT)
        result = self.catalog_service.inspect(request)

        # The no-progress guard. A catalogue call that adds nothing is not a
        # refusal -- the analyst may have had a reason -- but a RUN that keeps
        # making them is going nowhere, and the live defect spent its whole
        # deadline doing exactly that. Two in a row is a typed warning; a
        # third ends the run rather than burning the remaining budget on a
        # loop that has already proven itself.
        if result.get("added_new_information") is False:
            self.barren_catalog_calls += 1
        else:
            self.barren_catalog_calls = 0

        if self.barren_catalog_calls >= BARREN_CATALOG_TERMINAL:
            raise BudgetExceeded(
                st.NO_PROGRESS,
                f"{self.barren_catalog_calls} consecutive inspect_catalog "
                f"calls added no new information. The metadata already in "
                f"this conversation is what there is; the run stopped rather "
                f"than spend the remaining deadline on it.")
        if self.barren_catalog_calls >= BARREN_CATALOG_WARN:
            result["no_progress"] = {
                "consecutive_calls_without_new_information":
                    self.barren_catalog_calls,
                "message": (
                    "No new catalogue information was added. The requested "
                    "fields are already available in this conversation. "
                    "Continue with the analysis, or request a different, "
                    "specific metadata item. One further request that adds "
                    "nothing ends this run."),
            }

        result["budgets_remaining"] = self.ledger.snapshot()
        self.analyst.tool_result(call.id, result)
        self.emitter.append(
            ev.TOOL_COMPLETED, stage="catalog", operation=TOOL_INSPECT,
            status=(ev.STATUS_REJECTED
                    if result.get("status") == "needs_scope"
                    else ev.STATUS_OK),
            public_message=_catalog_message(result, request),
            detail_ref=self._detail({
                "requested": result.get("requested"),
                "returned": result.get("returned"),
                "already_known": result.get("already_known"),
                "still_missing": result.get("still_missing"),
                "coverage_complete_for_request":
                    result.get("coverage_complete_for_request"),
                "added_new_information":
                    result.get("added_new_information"),
                "requested_fields": list(request.field_ids),
                "requested_relations": list(request.relation_ids),
                "detail": list(request.detail),
                "receipt": result.get("metadata_receipt_id")}))
        return None

    # -- inspect_product_knowledge ---------------------------------------

    def _do_product_knowledge(self, call) -> None:
        """Return product facts. Available in every mode.

        Reading what the product is is not analysis, so this does not require
        a declared data analysis and does not consume the catalog budget: a
        product-help question must be able to reach it without pretending to
        be something it is not.
        """
        from backend.cockpit_v4 import product_knowledge as pk

        request = parse_product_knowledge(call.arguments,
                                          carried=self.intent)
        self._record_intent(request.intent)
        self.ledger.check_deadline()
        self.product_calls += 1
        if self.product_calls > MAX_PRODUCT_CALLS:
            self._tool_error(
                call, st.CALL_LIMIT,
                f"All {MAX_PRODUCT_CALLS} product-knowledge reads for this "
                f"run were used. Answer from what you already have, or say "
                f"what you could not establish.")
            return None
        self._advance(st.TOOL_RUNNING, operation=TOOL_PRODUCT)
        result = pk.retrieve(query=request.query, topics=request.topics,
                             detail=request.detail)
        result["budgets_remaining"] = self.ledger.snapshot()
        self.analyst.tool_result(call.id, result)
        self.emitter.append(
            ev.TOOL_COMPLETED, stage="product_knowledge",
            operation=TOOL_PRODUCT, status=ev.STATUS_OK,
            public_message=(
                f"Read product knowledge: "
                f"{', '.join(result['topics_returned'])}."),
            detail_ref=self._detail({
                "query": request.query, "topics": list(request.topics),
                "detail": request.detail,
                "pack_version": result["pack_version"],
                "sections": [s.get("title") for s in result["sections"]]}))
        return None

    def _undecided_terms(self) -> list[str]:
        """The governed terms the SERVER could not decide for this question.

        `semantics.readiness` computes them deterministically before any
        model call, and an undecided term is exactly "an alternative that
        can change the answer". A policy clause the question happens to
        reach must not close one of these quietly: see
        `context.POLICY_DOES_NOT_CLOSE_AN_OPEN_READING`.
        """
        terms = (dict(self.readiness or {})
                 .get("terms_still_needing_a_decision") or ())
        return [str(t.get("term") or "") for t in terms if t.get("term")]

    def _asked(self) -> str:
        """The reader's own words, as the first user message carries them.

        The run does not hold the question as a field -- it holds the
        conversation -- so this reads it back out of the first message, the
        same way the repair note does.
        """
        first = self.analyst.messages[0] if self.analyst.messages else None
        if not first or not isinstance(first.get("content"), str):
            return ""
        return str(first["content"]).split("\n\n")[0].replace(
            "USER REQUEST (original wording, unmodified):\n", "")

    # -- read_artifact ---------------------------------------------------

    def _do_artifact(self, call) -> None:
        request = parse_artifact(call.arguments, carried=self.intent)
        self._record_intent(request.intent)
        self.ledger.spend_artifact_read()
        self._advance(st.TOOL_RUNNING, operation=TOOL_READ)
        result = self.artifact_service.read(request)
        result["budgets_remaining"] = self.ledger.snapshot()
        self.analyst.tool_result(call.id, result)
        self.emitter.append(
            ev.TOOL_COMPLETED, stage="reviewing", operation=TOOL_READ,
            status=ev.STATUS_OK,
            public_message=(
                f"Read {result.get('returned_rows', 0)} row(s) from stored "
                f"evidence." if result.get("status") == "ok"
                else "That reference is not available."))
        return None

    # -- execute_analysis ------------------------------------------------

    # -- plan_compiler -------------------------------------------------

    def _plan_book(self):
        """The governed vocabulary for plans, built once per run."""
        book = getattr(self, "_plan_book_cache", None)
        if book is None:
            from backend.cockpit_v4 import plan_compiler

            book = plan_compiler.build_book(
                domain_id=str(getattr(self.envelope, "domain_id", "") or "corporate"),
                catalog=self.catalog,
                session=getattr(self.execution_service, "session", None))
            self._plan_book_cache = book
        return book

    def _plan_offer(self) -> tuple[dict[str, Any] | None, str]:
        capability = getattr(self.analyst, "capability", None)
        if not getattr(capability, "supports_plan_compiler", False):
            return None, ""
        try:
            from backend.cockpit_v4 import plan_compiler

            book = self._plan_book()
            return plan_compiler.plan_schema(book), plan_compiler.metric_glossary(book)
        except Exception:  # noqa: BLE001 - the plan is an aid; SQL still works
            import logging
            logging.getLogger(__name__).warning(
                "plan_compiler unavailable for this run", exc_info=True)
            return None, ""

    def _check_intent(self, plan: Any) -> None:
        """Run the plan-vs-question check; raise its Rejection under enforce.

        Warnings (and, under shadow, would-be refusals) are recorded on
        `self._intent_findings` and as a context event, never refused.
        """
        from backend.cockpit_v4 import intent_check

        if intent_check.mode() == "off" or not isinstance(plan, dict):
            return
        recorded, rejection = intent_check.gate(
            plan, question=self._asked(), envelope=self.envelope,
            book=self._plan_book())
        if recorded:
            self._intent_findings = [*getattr(self, "_intent_findings", []), *recorded]
            try:
                self.emitter.append(
                    ev.CONTEXT_READY, stage="understanding", operation="intent_check",
                    status=ev.STATUS_OK,
                    public_message=f"Plan intent check noted {len(recorded)} finding(s).",
                    detail_ref=self._detail(
                        {"intent_check": intent_check.mode(),
                         "findings": [f.as_dict() for f in recorded]}))
            except Exception:  # noqa: BLE001 - a record of a finding is not a reason to fail
                pass
        if rejection is not None:
            raise rejection

    def _can_afford_a_retry(self) -> bool:
        """Could this run still act on a refusal? Otherwise let the question
        through: the gate must cost a run one generation, never its answer.

        The refusal costs a generation, a provider attempt or two, and the
        analysis it asks for needs a submission and an answer turn after it.
        Cost headroom is judged against what this run has averaged per
        generation so far, for two more turns.
        """
        counters, limits = self.ledger.counters, self.ledger.limits
        if limits.generation_attempts - counters.generation_attempts < 2:
            return False
        if limits.provider_attempts - counters.provider_attempts < 6:
            return False
        if limits.execution_submissions - counters.execution_submissions < 1:
            return False
        try:
            spend = self.ledger.spend()
            used = spend["committed_usd"] + spend["pending_usd"]
            average = used / max(1, counters.generation_attempts)
            return (limits.spend_ceiling_usd - used) >= 2 * average
        except Exception:  # noqa: BLE001 - unknown headroom is not headroom
            return False

    def _check_clarification(self, final) -> None:
        """Refuse, once, a clarification the server can show was not earned.

        A plain Rejection, NOT the answer-correction path: that one sets
        `answer_only`, after which only finalize is offered, which is the
        opposite of what is wanted here. `_handle_call` turns this into a
        tool error that spends no budget; the next generation counts against
        `generation_attempts`, so the retry is visible and bounded.

        Runs only where it can do no harm: before anything executed, not on
        the time-short answer path, not on a run that could not fit another
        action, and never twice. Under `shadow` it records and returns.
        """
        from backend.cockpit_v4 import clarify_gate

        gate_mode = clarify_gate.mode()
        if (gate_mode == "off" or final.disposition != "clarification"
                or self.executed or self.answer_only or self._answer_demanded
                or self._clarify_gate_used):
            return
        if not (self.analytical or getattr(self.envelope, "query_mode", "")
                == contracts_mod.DATA_ANALYSIS):
            return
        try:
            self.ledger.check_call_window(phase="action")
        except BudgetExceeded:
            return  # no time to act on a refusal, so let the question through
        if not self._can_afford_a_retry():
            return
        finding = clarify_gate.check(
            question=self._asked(), readiness=self.readiness,
            value_resolution=self.value_resolution, envelope=self.envelope,
            final=final)
        if finding is None:
            return
        self._intent_findings = [*getattr(self, "_intent_findings", []), finding]
        if gate_mode == "shadow":
            try:
                self.emitter.append(
                    ev.CONTEXT_READY, stage="understanding",
                    operation="clarify_gate", status=ev.STATUS_OK,
                    public_message="Clarification gate noted an unearned question.",
                    detail_ref=self._detail(
                        {"clarify_gate": gate_mode,
                         "findings": [finding.as_dict()]}))
            except Exception:  # noqa: BLE001 - a record is not a reason to fail
                pass
            return
        self._clarify_gate_used = True
        # UNDO THE STATE THE ANALYST'S OWN ASK LEFT BEHIND. A blocking
        # ambiguity declared on an earlier submission was recorded on the
        # envelope and is copied into any later payload that omits the key,
        # and `must_clarify` withdrew `execute_analysis`. Leaving either
        # makes the correction below impossible to follow.
        self.must_clarify = False
        if self.envelope is not None:
            self.envelope = dataclasses.replace(
                self.envelope, blocking_ambiguities=())
            self.intent = self.envelope.as_intent()
        elif self.intent is not None:
            self.intent = dataclasses.replace(
                self.intent, blocking_ambiguities=())
        self.emitter.append(
            ev.INTENT_VALIDATED, stage="understanding",
            operation="clarify_gate", status=ev.STATUS_REJECTED,
            public_message=("Every term in the question maps -- running with "
                            "stated assumptions instead of asking."),
            detail_ref=self._detail({"clarify_gate": gate_mode,
                                     "findings": [finding.as_dict()]}))
        # `_tool_error` publishes the message to the reader, so it is written
        # for them; what the analyst must DO travels in `detail.note`, which
        # reaches it in the tool result.
        raise Rejection(
            "CLARIFICATION_UNGROUNDED",
            "A question back was not needed; running with stated assumptions.",
            field_path="disposition",
            detail={"note": (
                "Every term maps: choose the reading, record it in "
                "resolved_assumptions, send blocking_ambiguities: [] and "
                "call execute_analysis.")})

    def _compiled(self, arguments: Any) -> Any:
        """A submission carrying `plan` and no `steps`, with the SQL written.

        Raises the compiler's Rejection, which reaches the analyst exactly as
        any other refused submission does, naming the governed choices.
        """
        self._plan_shapes = {}
        if not (isinstance(arguments, dict) and not arguments.get("steps")
                and (arguments.get("plan") or arguments.get("sensitivity"))):
            return arguments
        from backend.cockpit_v4 import plan_compiler

        try:
            if arguments.get("sensitivity"):
                steps, objective = plan_compiler.compile_sensitivity(
                    arguments["sensitivity"], self._plan_book())
            else:
                # Valid is not the same as right: refuse a plan that answers a
                # different period or scope than the question named. Raised
                # here so the handler below charges the plan retry once.
                self._check_intent(arguments.get("plan"))
                steps, objective = plan_compiler.compile_plan(arguments["plan"], self._plan_book())
        except Rejection as exc:
            # A plan compiler refusal happens before an ExecutionSubmission
            # exists: there is no SQL, no bind pass and no execution to
            # charge.  Give the model three narrowly-scoped corrections,
            # then stop with a truthful plan-format failure.
            retry = self.ledger.spend_plan_retry()
            detail = dict(exc.detail or {})
            detail.update({"plan_retry": retry,
                           "plan_retries_remaining": (
                               self.ledger.limits.plan_retries - retry),
                           "note": (
                               "Correct the plan only and call execute_analysis again. "
                               "Use 1 to 4 governed measures. A stage filter can only "
                               "be combined with measures and dimensions on the relation "
                               "that holds stage; for Corporate, use facility-grain metrics "
                               "(for example EAD, ECL or facility_count), not borrower_count. "
                               "No SQL was executed and no execution submission was used.")})
            exc.detail = detail
            raise
        if arguments.get("plan"):
            self._plan_shapes = plan_compiler.output_columns(arguments["plan"], self._plan_book())
        compiled = {k: v for k, v in arguments.items() if k not in ("plan", "sensitivity")}
        compiled["steps"] = steps
        compiled.setdefault("objective", objective)
        if not compiled.get("objective"):
            compiled["objective"] = objective
        return compiled

    def _do_execute(self, call) -> None:
        # The allowance widens HERE, before anything expensive and before any
        # field of this request has been parsed.
        #
        # It used to widen inside `_record_intent`, which needs a well-formed
        # `intent` to have been parsed first. So a run whose analyst declared
        # DATA_ANALYSIS by submitting SQL still executed that SQL on the
        # Product Help clock, and a malformed `intent` meant the clock never
        # widened at all -- sixty seconds, spent on a query that needed more,
        # and a public failure naming the parser.
        #
        # Asking to execute analysis IS the declaration. Nothing about the
        # budget needs the model to also say it in a field.
        self._adopt_analytical_limits_for_tool(TOOL_EXECUTE)
        # The same declaration, in the run's settled intent. The budget
        # classifier reads the question's words before the first call and
        # can read an analytical question as product help; submitting SQL
        # says otherwise, and the run widens rather than refusing over a
        # classification the reader was never shown.
        if self.envelope is not None:
            self.envelope = self.envelope.escalate_to_analysis()
            self.intent = self.envelope.as_intent()
        arguments = self._compiled(call.arguments)
        # Compilation above has either produced governed SQL or spent one of
        # the separate, bounded plan-correction retries.  From this point a
        # received submission is executable SQL and counts against the five
        # execution submissions even if later validation rejects it.
        ordinal = self.ledger.spend_submission()
        submission = parse_execution(
            arguments, max_steps=self.ledger.limits.steps_per_batch,
            carried=self.intent)
        self._record_intent(submission.intent)

        # Computed before the check, because a submission REFUSED has to be
        # recorded under the same key as one that failed while running. A
        # rejection used to be filed with an empty key, so an analyst that
        # resubmitted a byte-identical query which the checks refuse was not
        # caught by NO_PROGRESS -- only one that got as far as the engine
        # was. Moving the join-grain check up into validation is what makes
        # that hole reachable, so it is closed here.
        key = no_progress_key(submission, release_id=self.run.release_id)
        # Raw-SQL intent checks run in SHADOW inside validate_batch: they need
        # the question and the envelope, and they never refuse. Their findings
        # join the plan check's on `_intent_findings` for the trace and for
        # measuring precision before any rule is promoted.
        try:
            self.execution_service.intent_question = self._asked()
            self.execution_service.intent_envelope = self.envelope
            self.execution_service.shadow_findings = []
        except AttributeError:  # a service without the shadow hook
            pass
        try:
            bind_report = self.execution_service.validate_batch(submission)
            shadow = getattr(self.execution_service, "shadow_findings", None)
            if shadow:
                self._intent_findings = [
                    *getattr(self, "_intent_findings", []), *shadow]
        except Rejection as rejection:
            # A REFUSAL IS THE MOST REPAIRABLE THING THAT CAN HAPPEN, AND IT
            # WAS THE ONE THAT SAID SO LEAST.
            #
            # A batch that fails while RUNNING is handed a note -- "author
            # the corrected code yourself, or finalize a supported partial
            # answer" -- and a submission refused before it ran was handed
            # nothing. That is backwards. Nothing executed, so there is no
            # partial result to reconcile and no step budget to recover: the
            # analyst can simply write the query again. Mutating the
            # rejection's own detail is deliberate -- `_handle_call` passes
            # this same dict to `_tool_error`, so the note reaches the model
            # without a second user turn and without a new mechanism.
            #
            # What is NOT repeated here: how to fix it. That is the check's
            # own business and the check already said it -- a grain refusal
            # names the join, both grains, the measures at risk and the
            # three de-duplications that would be valid.
            # A BLOCKING AMBIGUITY IS NOT A CODE DEFECT AND HAS NO REPAIR.
            #
            # The analyst said it cannot choose between two readings. The
            # standing note tells it to author corrected code and submit
            # again, the gate then required `execute_analysis`, and the
            # refusal itself advised moving the doubt into
            # `resolved_assumptions`, "which do not stop execution". Every
            # road led back to guessing, and a live run took it: "Assumed:
            # attribution is sequential (PD first, then LGD, then EAD)" --
            # a choice that changes every number in the answer, made
            # silently because the run had no way to ask.
            if getattr(submission.intent, "blocking_ambiguities", ()):
                self.must_clarify = True
                rejection.detail.setdefault("note", (
                    "Nothing was executed. This is not a defect in the code "
                    "and there is nothing to repair: you declared a reading "
                    "you cannot choose between, and choosing one silently is "
                    "the outcome this refusal exists to prevent. Put the "
                    "question to the reader instead -- call finalize_response "
                    "with disposition \"clarification\", the one question you "
                    "need answered, and the concrete choices in "
                    "clarification_options so it can be clicked rather than "
                    "typed. If on reflection the reading does NOT change the "
                    "figures, it was never blocking: say so in "
                    "resolved_assumptions and submit again."))
            else:
                rejection.detail.setdefault("note", (
                    "Nothing was executed and nothing was repaired: this "
                    "submission was refused before any step ran, so no step "
                    "budget was spent and no result exists to reconcile. "
                    "Author the corrected code yourself and submit again, or "
                    "finalize a supported partial answer. CreditProbe will "
                    "not rewrite a query, drop a step or compute a "
                    "substitute."))
            # A submission refused at the binder is still a numbered
            # submission, and its exact SQL is still on file. "Which query, on
            # which attempt" has to be answerable for the one that never ran,
            # not only for the ones that did.
            submission_id = self.store.record_submission(
                run_id=self.run.run_id, ordinal=ordinal, round=0,
                payload=submission.to_dict(), status="rejected",
                no_progress_key=key)
            detail = dict(rejection.detail or {})
            self._remember_failure(
                stage="validating", code=rejection.code,
                summary=(f"submission {ordinal} did not bind — "
                         f"{rejection.message}"
                         if detail.get("phase") == "bind"
                         else f"submission {ordinal} was refused — "
                              f"{rejection.message}"),
                detail={"submission": ordinal, **detail})
            self.emitter.append(
                ev.TOOL_FAILED, stage="validating", operation=TOOL_EXECUTE,
                status=ev.STATUS_REJECTED, submission=ordinal,
                public_message=(
                    "The query did not bind and was not run."
                    if detail.get("phase") == "bind"
                    else "The submission was refused before anything ran."),
                detail_ref=self._detail({
                    "submission_id": submission_id, "submission": ordinal,
                    "stage": "validating",
                    "error_code": rejection.code,
                    "field": rejection.field_path,
                    "message": rejection.message,
                    "steps": [{"step_id": s.step_id, "language": s.language,
                               "code": s.code, "parameters": s.parameters}
                              for s in submission.steps],
                    **detail}))
            raise
        self.ledger.spend_steps(len(submission.steps))
        try:
            self.ledger.no_progress_check(key)
        except BudgetExceeded as exc:
            # NO_PROGRESS is a refusal of THIS submission, not the end of the
            # run: the slot is already spent, and the analyst may still write
            # different code or finalize a supported partial answer. Ending
            # the run here would throw away work that already succeeded.
            raise Rejection(exc.code, str(exc), field_path="steps",
                            detail={"budgets_remaining":
                                    self.ledger.snapshot()}) from exc
        round_no = self.ledger.open_round()

        submission_id = self.store.record_submission(
            run_id=self.run.run_id, ordinal=ordinal, round=round_no,
            payload=submission.to_dict(), status="running",
            no_progress_key=key)

        proven = len(bind_report.get("bound_now", ()))
        deferred = list(bind_report.get("bound_at_run_time", ()))
        self.emitter.append(
            ev.TOOL_VALIDATED, stage="validating", operation=TOOL_EXECUTE,
            status=ev.STATUS_OK, submission=ordinal, round=round_no,
            # WHAT WAS PROVEN, AND WHAT WAS ONLY STATED.
            #
            # This used to read "Query validated and bound: N step(s), X
            # grain, Y units", which claimed three things it had not earned.
            # "Bound" is true only of the steps that reached EXPLAIN, and the
            # grain and the units are the ANALYST'S OWN declared strings --
            # echoed here, never checked against anything. A reader who is
            # told the units were validated has been told the server agreed
            # with a number it has not seen.
            public_message=(
                f"Query checked: {len(submission.steps)} step(s) passed the "
                f"structure, table-access and join-grain checks; {proven} "
                f"also bound against the live tables, executing nothing."
                + (f" {len(deferred)} step(s) read an earlier result and are "
                   f"bound as they run." if deferred else "")
                + f" Grain and units — {submission.expected_output_grain}, "
                  f"{units_display(submission.expected_units)} — are as the "
                  f"analysis declared them and are not proven here."),
            detail_ref=self._detail({
                "objective": submission.objective,
                "submission_id": submission_id,
                "bind_proof": {"method": "DuckDB EXPLAIN, nothing executed",
                               "covers": ("every relation, column, alias, "
                                          "function, GROUP BY position and "
                                          "ORDER BY term, with the step's "
                                          "own parameter values"),
                               "proven_before_execution": proven,
                               "proven_at_run_time": deferred},
                # SHORT NAMES, NOT THE CHECK CONSTANTS. Two scanners read
                # this body as text and both key on the substring
                # "authorization": the store's redaction, which would
                # replace the description with "[redacted]", and the
                # evidence-trace generator, which refuses to publish a file
                # containing it. `CHECK_AUTHORIZATION` is spelled
                # "relation_authorization", so naming it here trips both --
                # and loosening a credential scanner to fit a label is the
                # wrong way round. The canonical name still travels on a
                # FAILURE, as `failed_check`; this is the passing side, and
                # what it owes the reader is English.
                "checks_passed": {
                    "structure": ("one SELECT, parsed; no forbidden keyword "
                                  "or function"),
                    "relations": ("every FROM/JOIN name is a relation this "
                                  "domain is allowed to read"),
                    "join_grain": ("no additive measure is aggregated across "
                                   "a join the catalogue says repeats the "
                                   "row carrying it")},
                "not_proven": [
                    "that any row exists, that the query completes within "
                    "its deadline, or how it behaves against resource "
                    "limits",
                    f"the declared output grain and units "
                    f"({submission.expected_output_grain!r}, "
                    f"{units_display(submission.expected_units)}): the "
                    f"analysis's own statement, echoed and never checked "
                    f"against the result",
                    *([f"steps {deferred} were not bound: they read an "
                       f"earlier result and are bound as they run."]
                      if deferred else [])],
                "steps": [{"step_id": s.step_id, "language": s.language,
                           "purpose": s.purpose, "code": s.code,
                           "parameters": s.parameters}
                          for s in submission.steps],
                "fields_required": list(submission.fields_required),
                "metadata_receipts": list(submission.metadata_receipt_ids)}))

        self._advance(st.TOOL_RUNNING, operation=TOOL_EXECUTE)

        def on_step(phase: str, step, result) -> None:
            if phase == "started":
                self.emitter.append(
                    ev.TOOL_STARTED, stage="executing", operation=step.step_id,
                    status=ev.STATUS_STARTED, submission=ordinal,
                    round=round_no,
                    public_message=f"Executing {step.purpose}")
            elif phase == "completed":
                self.emitter.append(
                    ev.TOOL_COMPLETED, stage="executing",
                    operation=step.step_id, status=ev.STATUS_OK,
                    submission=ordinal, round=round_no,
                    public_message=(f"{step.purpose}: {result.row_count} "
                                    f"row(s)."),
                    detail_ref=self._detail({
                        "step_id": step.step_id,
                        "executed_code_digest": result.code_digest,
                        "submitted_code": step.code,
                        "artifact_id": result.artifact_id,
                        "warnings": result.warnings}))
            elif phase == "failed":
                # THREE OUTCOMES, BECAUSE THERE ARE THREE. The engine ran it
                # and it failed; the binder refused it; a check refused it
                # before either was asked. This used to be a two-way test on
                # `phase != "bind"`, which called every refusal that was not
                # the binder's a query that had run.
                reached, ran = result.reached_phase, result.ran
                said = {
                    xt.PHASE_RUNTIME: f"{step.purpose} failed while running.",
                    xt.PHASE_BIND: (f"{step.purpose} did not bind and was "
                                    f"not run."),
                    xt.PHASE_CHECK: (f"{step.purpose} was refused before it "
                                     f"ran. Nothing was executed."),
                }.get(reached, f"{step.purpose} was not run.")
                prefix = {
                    xt.PHASE_RUNTIME: "failed while running — ",
                    xt.PHASE_BIND: "did not bind — ",
                    xt.PHASE_CHECK: "was refused before running — ",
                }.get(reached, "was not run — ")
                self._remember_failure(
                    stage="executing", code=result.error_code,
                    summary=(f"step {step.step_id} of submission {ordinal} "
                             + prefix + result.message),
                    # The engine's own diagnostic goes FIRST, so the derived
                    # facts always win. `BindFailure.detail()` carries its
                    # own "phase" key and used to overwrite this one -- they
                    # agree today, and nothing required them to.
                    detail={**dict(result.engine_detail or {}),
                            "submission": ordinal, "step_id": step.step_id,
                            "phase": reached, "executed": ran})
                self.emitter.append(
                    ev.TOOL_FAILED, stage="executing",
                    operation=step.step_id, status=ev.STATUS_FAILED,
                    submission=ordinal, round=round_no,
                    public_message=said,
                    detail_ref=self._detail({
                        **dict(result.engine_detail or {}),
                        "step_id": step.step_id,
                        "phase": reached,
                        "executed": ran,
                        "failed_check": result.failed_check,
                        "error_code": result.error_code,
                        "message": result.message,
                        "parameters": step.parameters,
                        "submitted_code": step.code,
                        "failed_code_digest": result.code_digest}))

        batch = self.execution_service.run_batch(
            submission, submission_id=submission_id,
            deadline_seconds=min(self.ledger.remaining_seconds,
                                 self.ledger.limits.step_seconds
                                 * len(submission.steps)),
            on_step=on_step)
        self.store.set_submission_status(submission_id, batch.status)

        for result in batch.steps:
            if result.artifact_id:
                self.finalizer.run_artifacts.add(result.artifact_id)
                self.executed = True
                # What this result WAS, kept so a table published without a
                # written answer still has a name a reader recognises.
                if result.purpose:
                    self.step_purposes[result.step_id] = result.purpose

        self.repair_open = (
            batch.status != "ok" and self.executed
            and self.ledger.counters.execution_submissions
            < self.ledger.limits.execution_submissions)
        if batch.status == "ok":
            # A completed batch closes the round; the NEXT batch opens a new
            # one. A repair stays inside the round it failed in.
            self.ledger.close_round()
        else:
            # Record the failure class so a genuine environment recovery can
            # retry while an identical deterministic failure cannot.
            failed = next((s for s in batch.steps if s.status == "failed"),
                          None)
            if failed is not None:
                self.store.record_submission(
                    run_id=self.run.run_id, ordinal=ordinal, round=round_no,
                    payload={"failed": failed.step_id}, status="failed",
                    no_progress_key=no_progress_key(
                        submission, release_id=self.run.release_id,
                        error_class=failed.error_code))

        body = batch.to_dict()
        self._attach_answer_kits(batch, body)
        body["budgets_remaining"] = self.ledger.snapshot()
        if batch.status != "ok":
            body["note"] = (
                "CreditProbe did not modify or repair anything. Author the "
                "corrected code yourself, or finalize a supported partial "
                "answer.")
        self.analyst.tool_result(call.id, body,
                                 is_error=batch.status != "ok")
        return None

    def _attach_answer_kits(self, batch, body: dict[str, Any]) -> None:
        """For each planned step that ran: the figures, already claimed.

        Replaces that step's generic citation guide -- the kit says the same
        things about the same artifact, already done -- so the answer turn
        reads less, not more.
        """
        if not self._plan_shapes:
            return
        from backend.cockpit_v4 import answer_draft

        for result, shown in zip(batch.steps, body.get("steps") or []):
            shape = self._plan_shapes.get(result.step_id)
            if shape is None:
                continue
            drafted = answer_draft.draft(result, shape,
                                         money_unit=result.money_unit)
            if not drafted:
                continue
            for item in drafted:
                self.drafted[item.claim.claim_id] = item
            shown.pop("how_to_cite_these_numbers", None)
            shown["answer_kit"] = answer_draft.kit(drafted, result.purpose)

    def _with_drafted(self, final):
        """Attach the drafted claim behind each placeholder the model used.

        Only placeholders the narrative contains and the analyst did not
        declare itself. A claim the analyst sent under the same id wins, and
        is checked as its own.
        """
        if not self.drafted:
            return final
        from backend.cockpit_v4.finalization import PLACEHOLDER

        declared = {c.claim_id for c in final.numeric_claims}
        added = tuple(self.drafted[claim_id].claim for claim_id in
                      dict.fromkeys(PLACEHOLDER.findall(final.narrative))
                      if claim_id in self.drafted and claim_id not in declared)
        if not added:
            return final
        return dataclasses.replace(
            final, numeric_claims=tuple(final.numeric_claims) + added)

    # -- finalize_response -----------------------------------------------

    def _do_finalize(self, call) -> Outcome | None:
        final = self._with_drafted(parse_final(call.arguments,
                                               carried=self.intent))
        self._record_intent(final.intent)
        self._check_clarification(final)
        self._advance(st.FINAL_VALIDATING, operation=TOOL_FINALIZE)

        # What the entity and averaging checks need, set per validation
        # because the Finalizer is built before the envelope is settled.
        self.finalizer.resolved_entities = tuple(
            getattr(self.envelope, "resolved_entities", ()) or ())
        self.finalizer.question = str(getattr(self.run, "question", "") or "")
        self.finalizer.unweighted_measures = frozenset(
            c.name for shape in self._plan_shapes.values()
            for c in shape.columns
            if str(getattr(c, "label", "")).startswith("Simple average"))
        report = self.finalizer.validate(final, executed=self.executed)
        if not report.ok:
            self.emitter.append(
                ev.ANSWER_VALIDATED, stage="publishing",
                operation="answer_check", status=ev.STATUS_REJECTED,
                public_message="The response did not pass its evidence check.",
                detail_ref=self._detail({"problems": report.problems}))
            try:
                self.ledger.spend_answer_correction()
            except BudgetExceeded:
                # A second invalid answer is an explicit failure, not another
                # loop and never a fabricated narrative.
                #
                # But the ANALYSIS is not thrown away with the answer. The
                # query ran, it was validated and bound, its result is
                # stored, and an operator reading "this run failed" should
                # not conclude the SQL failed -- in the live case it had
                # already succeeded and returned twelve correct rows. The
                # event says which half stopped, and names the artifact that
                # survived so the work can be inspected.
                self._preserve_analysis("answer_correction_exhausted")
                # AND THE RESULT IS PUBLISHED, not merely preserved. Naming
                # the surviving artifact in an event tells an OPERATOR the
                # SQL was fine; it does not put a single row in front of the
                # reader, who asked a question and is looking at a red box
                # over a query that ran correctly. Same channel, same
                # server-written caveat, no narrative -- the rejected one is
                # discarded here exactly as it was before.
                published = self._result_only_response(
                    st.ANSWER_VALIDATION, rejected=final)
                return Outcome(
                    st.PARTIAL if published else st.FAILED,
                    error_code=st.ANSWER_VALIDATION, response=published,
                    message=(
                        ("The analysis completed and its result is stored, "
                         "but the written answer could not be validated "
                         "against that evidence and the one correction for "
                         "this run was already used.")
                        if self.executed else
                        ("The response could not be validated against the "
                         "executed evidence and the one correction for this "
                         "run was already used.")))
            self.answer_only = True
            self.purpose = "ANSWER_CORRECTION"
            from backend.cockpit_v4.finalization import (correction_packet,
                                                         rejection)
            # The analysis SUCCEEDED. What failed is the binding between the
            # answer's numbers and the evidence, so the repair is a rewrite
            # of the answer and nothing else: no new SQL, no catalogue read,
            # no question back to the user. The packet carries the artifact,
            # its columns and its real row ids, because the live failure was
            # an analyst guessing row names it had never been shown.
            packet = correction_packet(
                final, report, store=self.store,
                tenant_id=self.run.tenant_id,
                run_artifacts=self.finalizer.run_artifacts)
            if self.drafted:
                # The figures this run already bound, again: the usual
                # failure is a placeholder spelled differently from its id.
                from backend.cockpit_v4.answer_draft import MAX_CLAIMS
                packet["ready_figures"] = [
                    d.to_dict() for d in list(self.drafted.values())[-MAX_CLAIMS:]]
            self._tool_error(call, st.ANSWER_VALIDATION,
                             rejection(report).message, detail=packet)
            return None

        charts = self.finalizer.surviving_charts(final)
        suggestions = self.finalizer.validate_suggestions(final, self.catalog)

        published = final.to_dict()
        published["narrative"] = report.rendered_narrative
        # The figure a reader sees is CreditProbe's rounding of CreditProbe's
        # arithmetic -- the same string that was substituted into the
        # narrative. `decimal_value` is the analyst's cross-check and may
        # carry machine precision; it is never what gets shown.
        for claim in published["numeric_claims"]:
            shown = report.claim_values.get(claim["claim_id"], "")
            if shown:
                claim["display_value"] = shown
        # Every value in a published table or chart comes from the stored
        # artifact, formatted by the one display policy. The analyst chose
        # what to show; no number in either has passed through the model.
        published["tables"] = self.finalizer.render_tables(final,
                                                           self.catalog)
        published["charts"] = self.finalizer.render_charts(charts,
                                                           self.catalog)
        published["suggested_questions"] = suggestions
        published["validation"] = report.to_dict()
        published["evidence_bound"] = bool(final.numeric_claims)
        published["executed"] = self.executed
        # WHERE A POLICY NUMBER IN THIS ANSWER CAME FROM.
        #
        # H-LIVE-06. A live turn wrote "well inside the CP-1.1 single
        # obligor limit of SAR 25,000 million". The figure is right and the
        # answer showed no retrieval, because there was none to show: the
        # threshold reached that turn inside the policy synopsis the server
        # attaches. Correct is not the same as governed -- nothing in the
        # published answer said where the number came from, and a reader
        # could not tell it from a figure the model remembered.
        #
        # Built from the same deterministic retrieval that fed the turn, so
        # it cannot claim the answer was given something it was not, and
        # paired with the clause ids the narrative actually cites.
        from backend.cockpit_v4 import context as ctx_mod
        from backend.cockpit_v4 import credit_policy as cp_mod

        receipt = ctx_mod.policy_receipt(
            domain_id=str(getattr(self.envelope, "domain_id", "") or ""),
            question=self._asked())
        if receipt:
            published["policy_context"] = receipt
            published["policy_citations"] = cp_mod.citations(
                report.rendered_narrative, receipt["book"])
        # From here a real answer exists. A later terminal stop must not
        # replace it with the bare-result channel.
        self.answer_published = True
        # What these numbers mean, travelling with them. A saved analysis, a
        # shared link or a reopened thread carries the release, the bytes,
        # the country, the currency and the scale, so a reader months later
        # is not left inferring a denomination from the figures.
        if self.finalizer.header is not None:
            published["release"] = self.finalizer.header.to_dict()

        self.emitter.append(
            ev.ANSWER_VALIDATED, stage="publishing",
            operation="answer_check", status=ev.STATUS_OK,
            public_message=(f"Answer checked against "
                            f"{len(final.numeric_claims)} evidence-bound "
                            f"value(s)."))

        # The accepted tool result goes into canonical history BEFORE the
        # answer is published, so a reused history has no dangling tool_use.
        self.analyst.tool_result(call.id, {
            "status": "accepted", "published": True,
            "warnings": report.warnings})
        self.store.save_messages(self.run.run_id, self.analyst.messages)

        state = _DISPOSITION_STATE.get(final.disposition, st.COMPLETED)
        return Outcome(state, response=published)

    # -- intent ----------------------------------------------------------

    def _remember_failure(self, *, stage: str, code: str, summary: str,
                          detail: dict[str, Any] | None = None) -> None:
        """The FIRST one only. Later failures are in the event log anyway."""
        if self.first_failure is None:
            self.first_failure = {"stage": stage, "error_code": code,
                                  "summary": summary,
                                  "detail": dict(detail or {})}

    #: Tools whose use IS a declaration of analysis. Reading the catalogue is
    #: not one of them: a Product Help answer may legitimately look a field
    #: up, and widening the clock for that would be widening it for
    #: everything.
    _ANALYTICAL_TOOLS = (TOOL_EXECUTE,)

    def _adopt_analytical_limits_for_tool(self, tool: str) -> None:
        """Asking to run an analysis is asking for the analysis allowance."""
        if tool in self._ANALYTICAL_TOOLS:
            self._widen_to_analytical()

    def _adopt_analytical_limits(self, intent) -> None:
        """A declared analysis gets the analytical time and cost allowance.

        The mode is not knowable at intake -- the question arrives as text --
        so the run starts on the product-help allowance and widens here or at
        the first analytical tool, once and only upward. A Product Help run
        therefore keeps the tight bound it should have.
        """
        if intent.query_mode != contracts_mod.DATA_ANALYSIS:
            return
        self._widen_to_analytical()

    def _widen_to_analytical(self) -> None:
        if self._analytical:
            return
        self._analytical = True
        report = self.ledger.adopt(
            config_mod.analytical_limits_for(self.ledger.limits.mode))
        if not report["changed"]:
            return
        try:
            self.store.extend_deadline(
                self.run.run_id,
                (datetime.now(timezone.utc) + timedelta(
                    seconds=self.ledger.limits.deadline_seconds)
                 ).isoformat(timespec="milliseconds"))
        except Exception:  # noqa: BLE001 - a stale watchdog is not fatal here
            pass
        after = report["after"]
        self.emitter.append(
            ev.CONTEXT_READY, stage="understanding", operation="budget",
            status=ev.STATUS_OK,
            public_message=(
                f"Analysis allowance: {after['deadline_seconds']:.0f}s, "
                f"${after['spend_ceiling_usd']:.2f}."),
            detail_ref=self._detail({"budget_adopted": report}))

    def _seed_intent(self) -> None:
        """Adopt the server's envelope as this run's intent, before the
        first provider call.

        Without this the first tool call has nothing to merge into: the tool
        schemas no longer offer `intent`, so every payload arrives without
        one and `parse_intent` has only the carried value to fall back on.
        The envelope IS that carried value, and it is authored by the server
        rather than asked for, which is the whole point of §24.
        """
        if self.envelope is None or self.intent is not None:
            return
        self.intent = self.envelope.as_intent()
        self._adopt_analytical_limits(self.intent)
        self.emitter.append(
            ev.INTENT_VALIDATED, stage="understanding", operation="intent",
            status=ev.STATUS_OK,
            public_message=(
                f"{_mode_label(self.intent.query_mode)} · "
                f"{self.envelope.domain_label} · "
                f"latest {self.envelope.period.noun} "
                f"{self.envelope.period.latest}"),
            detail_ref=self._detail(self.envelope.to_dict()))

    def _record_intent(self, intent) -> None:
        intent = self._with_value_resolution(intent)
        if self.envelope is not None:
            # The answer refines the reader-facing half. It does not get to
            # restate which book this is or which release: those are facts
            # about a run that already happened.
            #
            # The mode is the one server decision that still moves, and only
            # UPWARD. The budget classifier reads the question's words
            # before anything is spent and can read an analytical turn as
            # product help; an analyst that then declares an analysis is
            # telling us something we did not know, and the run widens on
            # the declaration wherever it arrives. It can never narrow: a
            # run that has already read the book does not get moved onto the
            # cheaper clock by saying so at the end.
            if (intent.query_mode == contracts_mod.DATA_ANALYSIS
                    and self.envelope.query_mode
                    != contracts_mod.DATA_ANALYSIS):
                self.envelope = self.envelope.escalate_to_analysis()
                self._adopt_analytical_limits_for_tool(TOOL_EXECUTE)
            self.envelope = self.envelope.with_intent(intent)
            intent = self.envelope.as_intent()
        if self.intent is not None and intent.to_dict() == self.intent.to_dict():
            return
        self.intent = intent
        self._adopt_analytical_limits(intent)
        self.emitter.append(
            ev.INTENT_VALIDATED, stage="understanding", operation="intent",
            status=ev.STATUS_OK,
            public_message=(f"{_mode_label(intent.query_mode)} · owned by "
                            f"{intent.owner.replace('_', ' ').title()}"),
            detail_ref=self._detail(intent.to_dict()))
        # Resolutions are shown, not hidden and not treated as refusals. A
        # reader should be able to see that "exposure at default" was read as
        # EAD and that the period was resolved to the latest populated
        # quarter, without either one having stopped the analysis.
        for label, lines in (("Resolved", intent.canonical_mappings),
                             ("Assumed", intent.resolved_assumptions)):
            for line in lines:
                self.emitter.append(
                    ev.INTENT_VALIDATED, stage="understanding",
                    operation="semantics", status=ev.STATUS_OK,
                    public_message=f"{label}: {line}")
        for line in intent.blocking_ambiguities:
            self.emitter.append(
                ev.INTENT_VALIDATED, stage="understanding",
                operation="ambiguity", status=ev.STATUS_REJECTED,
                public_message=f"Needs a decision: {line}")


    def _with_value_resolution(self, intent):
        """The declared intent, plus what the reader's own words were taken
        to mean.

        The resolver ran before the first generation and is not an opinion:
        "prject finance" IS `facility_type = 'Project Finance'` in this
        release, or it is nothing. Recording it here means the trace and the
        answer carry the reading whether or not the analyst repeated it --
        and a reader who disagrees can see exactly what to disagree with.
        """
        recognised = list((self.value_resolution or {}).get("recognised") or [])
        if not recognised:
            return intent
        exact = tuple(dict.fromkeys(
            [*intent.canonical_mappings]
            + [e["say"] for e in recognised if e.get("exact")]))
        assumed = tuple(dict.fromkeys(
            [*intent.resolved_assumptions]
            + [e["say"] for e in recognised if not e.get("exact")]))
        return dataclasses.replace(intent, canonical_mappings=exact,
                                   resolved_assumptions=assumed)


#: Product-knowledge reads a single run may make. Generous, because each is
#: a dictionary lookup, and bounded, because nothing is unbounded.
MAX_PRODUCT_CALLS = 4

#: Consecutive catalogue calls that add nothing before the analyst is told
#: so, and before the run stops. The bound is on REPEATING, not on asking:
#: a complex question may legitimately need four distinct metadata reads, and
#: `catalog_calls` still allows them. What it may not do is ask the same
#: thing until the deadline expires.
BARREN_CATALOG_WARN = 2
BARREN_CATALOG_TERMINAL = 3


def _catalog_message(result: dict[str, Any], request: Any) -> str:
    """What the panel says a catalogue read actually did."""
    if result.get("status") == "needs_scope":
        return "Catalogue request needs a scope; nothing was read."
    if result.get("added_new_information") is False:
        return "No new catalogue information added; it was already in context."
    returned = (result.get("returned") or {}).get("field_ids") or []
    parts = []
    asked = (result.get("requested") or {}).get("field_ids") or []
    if asked:
        parts.append("Requested " + ", ".join(
            f.rpartition(".")[2] for f in asked[:6])
            + (f" and {len(asked) - 6} more" if len(asked) > 6 else ""))
    parts.append(f"returned {len(returned)} definition(s)")
    if result.get("coverage_complete_for_request"):
        parts.append("coverage complete")
    elif result.get("still_missing"):
        parts.append(f"{len(result['still_missing'])} still missing")
    return " · ".join(parts) + "."


def _stage_for(tool: str) -> str:
    return {TOOL_INSPECT: "catalog", TOOL_PRODUCT: "product_knowledge",
            TOOL_EXECUTE: "preparing", TOOL_READ: "reviewing",
            TOOL_FINALIZE: "publishing"}.get(tool, "understanding")


def _public_for(tool: str) -> str:
    return {TOOL_INSPECT: "Reading relevant data definitions",
            TOOL_PRODUCT: "Reading product knowledge",
            TOOL_EXECUTE: "Preparing query",
            TOOL_READ: "Reading stored evidence",
            TOOL_FINALIZE: "Validating and publishing answer"}.get(
                tool, "Working")


def _mode_label(mode: str) -> str:
    return {"PRODUCT_HELP": "About CreditProbe",
            "THEORY_CONCEPT": "Credit concept",
            "DATA_ANALYSIS": "Portfolio analysis",
            "OTHER_FUNCTIONALITY": "Another part of CreditProbe",
            "CLARIFICATION_REQUIRED": "Needs clarification",
            "UNSUPPORTED": "Not supported here"}.get(mode, mode)


#: Re-exported from `run_store`, where the redaction now runs: it belongs at
#: the write, not at one of the writers. Two callers reached `put_detail`
#: without passing through this, which is how 8 KB of a reader's own question
#: text got into the details table unfiltered. Kept importable under this
#: name because it is a named guarantee with its own test.
_redact = _store_redact


__all__ = ["Cancelled", "Orchestrator", "Outcome"]
