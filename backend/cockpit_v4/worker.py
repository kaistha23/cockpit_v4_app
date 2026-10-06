"""
The worker: claims a run, builds its services, drives it, settles it.

The lease is the point. A worker holds a lease with a fence number and
heartbeats every two seconds; the supervisor settles a run whose lease went
stale. A worker that comes back after being fenced cannot overwrite the
settled outcome, because every state write is a compare-and-swap on the run's
version and the store refuses a stale writer.

On worker loss the run becomes INTERRUPTED with its current operation and its
preserved artifacts. It is NOT replayed automatically: an uncertain paid
provider call or an executable batch may have already happened, and replaying
it spends money twice and can double-count an execution submission.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import context as context_mod
from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.artifacts import ArtifactService
from backend.cockpit_v4.budgets import Ledger
from backend.cockpit_v4.catalog_tool import CatalogService
from backend.cockpit_v4 import envelope
from backend.cockpit_v4.contracts import TOOL_PRODUCT, provider_tools
from backend.cockpit_v4.execute_tool import ExecutionService
from backend.cockpit_v4.finalization import Finalizer
from backend.cockpit_v4.orchestration import Orchestrator, Outcome
from backend.cockpit_v4.provider import Analyst
from backend.cockpit_v4.run_store import LeaseLost, RunStore
from backend.cockpit_v4.service import PreflightFailed, Runtime

logger = logging.getLogger(__name__)


class _NoBook(RuntimeError):
    """This run's book cannot be opened. Said, never substituted."""


@dataclass
class _LegacyBook:
    """The pre-domain release, presented with the same surface as a book.

    It exists so `_drive` has ONE shape to consume. It carries no domain id,
    because the release it wraps does not have one, and every message built
    from it therefore names no book rather than naming the wrong one.
    """

    runtime: Any
    record: Any
    session: Any = None

    def __post_init__(self) -> None:
        self.session = self._open()

    @property
    def catalog(self) -> Any:
        return self.runtime.catalog

    @property
    def coverage(self) -> Any:
        return self.runtime.coverage

    @property
    def release_id(self) -> str:
        return str(self.record.release_id)

    def _open(self) -> Any:
        try:
            from backend.cockpit_agentic import sql as v3_sql

            return v3_sql.open_session(
                scope=self.runtime.scope_for(
                    {"id": self.record.principal_id,
                     "tenant": self.record.tenant_id}),
                catalog=self.runtime.catalog)
        except Exception as exc:  # noqa: BLE001
            # An unopenable session does not stop help or theory: those never
            # execute. It stops ANALYSIS, and the analyst is told so through
            # the tool rather than by a pre-emptive refusal of the question.
            logger.info("V4 SQL session unavailable for run %s: %s",
                        self.record.run_id, exc)
            return None

    def release_summary(self) -> dict[str, Any]:
        return dict(self.runtime.release_summary)

    def read_scope(self, principal: dict[str, Any]) -> Any:
        return self.runtime.scope_for(principal)


@dataclass
class Worker:
    """Executes runs from the durable outbox."""

    store: RunStore
    runtime: Runtime
    worker_id: str = field(default_factory=lambda: f"w-{uuid.uuid4().hex[:8]}")
    stop_event: threading.Event = field(default_factory=threading.Event)
    poll_seconds: float = 0.05

    def serve_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                record = self.store.claim_next(self.worker_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("V4 worker could not claim a run: %s", exc)
                record = None
            if record is None:
                self.stop_event.wait(self.poll_seconds)
                continue
            try:
                self.execute(record)
            except Exception:  # noqa: BLE001
                logger.exception("V4 worker failed on run %s", record.run_id)

    def stop(self) -> None:
        self.stop_event.set()

    # -- one run ---------------------------------------------------------

    def execute(self, record: Any) -> Outcome:
        started = time.monotonic()
        provider = self.runtime.provider
        capability = self.runtime.capability
        # A run owns its selection. Never let a later preference change alter
        # an accepted operation; an unpriced/unqualified pair fails before a
        # provider call rather than silently using the runtime default.
        runtime_pair = (str(getattr(self.runtime.cfg, "provider", "")).lower(),
                        str(getattr(self.runtime.capability, "model_id", "")))
        pinned_pair = (str(getattr(record, "provider", "")).lower(),
                       str(getattr(record, "model", "")))
        # A run pinned to the runtime's own pair already has its verified
        # provider and capability; only a different pair is rebuilt.
        if all(pinned_pair) and pinned_pair != runtime_pair:
            from backend.llm import get_provider
            from backend.cockpit_v4 import capability as capability_mod
            provider = get_provider(provider=record.provider, model=record.model)
            local_card = str(Path(__file__).resolve().parents[2] / "config" /
                             "cockpit_v4" / "price_card.omlx.local.json")
            card = (os.environ.get("OMLX_PRICE_CARD", local_card) if record.provider == "omlx"
                    else self.runtime.cfg.price_card_path)
            capability = capability_mod.load_price_card(
                card or self.runtime.cfg.price_card_path, model_id=record.model,
                provider=record.provider)
        emitter = ev.Emitter(self.store, record.run_id,
                             started_monotonic=started)
        # The SAME verdict the route stamped the deadline with, recomputed
        # from the same durable inputs. It is not persisted: a value in two
        # places is a value that can disagree with itself, and the question,
        # the mode and the thread are all already on the record.
        verdict = envelope.for_request(
            store=self.store, thread_id=record.thread_id,
            question=record.question, mode=record.mode)
        limits = verdict.limits
        ledger = Ledger(limits=limits, capability=capability,
                        store=self.store, run_id=record.run_id,
                        started_monotonic=started)

        beat = _Heartbeat(self.store, record.run_id, self.worker_id)
        beat.start()
        try:
            # The watchdog reads `deadline_at`, and it must agree with the
            # allowance this run is actually on from the first second. The
            # route stamps it at accept; this is the belt to that brace, and
            # it is what makes the guarantee unconditional for a run created
            # by any other path. `extend_deadline` only ever pushes out.
            try:
                self.store.extend_deadline(
                    record.run_id,
                    (datetime.now(timezone.utc) + timedelta(
                        seconds=limits.deadline_seconds)
                     ).isoformat(timespec="milliseconds"))
            except Exception:  # noqa: BLE001 - a stale watchdog is not fatal
                pass
            emitter.append(
                ev.CONTEXT_READY, stage="accepted", operation="allowance",
                status=ev.STATUS_OK,
                public_message=(
                    f"Allowance: {limits.deadline_seconds:.0f}s, "
                    f"${limits.spend_ceiling_usd:.2f}."),
                detail_ref=self.store.put_detail(
                    record.run_id, {"envelope": verdict.to_dict()}))
            outcome = self._drive(record, emitter, ledger, limits, verdict,
                                  provider=provider, capability=capability)
        except PreflightFailed as exc:
            outcome = Outcome(st.FAILED, error_code=exc.code,
                              message=str(exc))
            emitter.append(ev.RUN_FAILED, stage="accepted",
                           operation="preflight", status=ev.STATUS_FAILED,
                           public_message=str(exc))
        finally:
            beat.stop()

        self._settle(record, outcome, emitter, ledger)
        return outcome

    def _drive(self, record: Any, emitter: ev.Emitter, ledger: Ledger,
               limits: Any, verdict: Any = None, *, provider: Any = None,
               capability: Any = None) -> Outcome:
        provider = provider or self.runtime.provider
        capability = capability or self.runtime.capability
        principal = {"id": record.principal_id, "tenant": record.tenant_id}

        # WHICH BOOK. Resolved from the persisted run and its thread, and
        # from nothing else -- not the frontend, not the Home switch as it
        # stands now, not a startup default, and not whichever catalogue
        # happened to be cached first. A run settled three minutes after the
        # reader switched the page still reads the book it was asked in.
        try:
            book = self._book_for(record)
        except _NoBook as exc:
            # There is no substitute book. Refusing is the whole point: the
            # alternative is an answer computed from data the reader did not
            # ask about, and nothing in it would say so.
            emitter.append(ev.RUN_FAILED, stage="accepted",
                           operation="open_domain_runtime",
                           status=ev.STATUS_FAILED, public_message=str(exc))
            return Outcome(st.FAILED, error_code=st.DATA_UNAVAILABLE,
                           message=str(exc))

        scope = book.read_scope(principal)
        session = book.session
        release_summary = book.release_summary()

        seeded = self.store.thread_context(record.thread_id,
                                           tenant_id=record.tenant_id)
        packet = context_mod.build(
            question=record.question, principal=principal, scope=scope,
            catalog=book.catalog, limits=limits, mode=record.mode,
            release_summary=release_summary,
            ui_filters=record.ui_filters,
            recent_turns=self.store.recent_turns(
                record.thread_id, context_mod.DEFAULT_RECENT_TURNS),
            summary=self.store.get_summary(record.thread_id),
            capability=capability,
            investigation=(seeded or {}).get("body") if seeded else None,
            session=session,
            # An analytical turn does not carry the product pack. See
            # `context.build`.
            analytical=bool(verdict is not None and verdict.analytical))

        # One-generation broad Product Help. When the question names no
        # product detail beyond the synopsis the packet already carries,
        # `inspect_product_knowledge` is not offered on the FIRST action —
        # the live run that cost two generations and 28s spent the first one
        # retrieving what was already in front of it. The full set is
        # restored for every action after the first, so a misjudged question
        # costs nothing that it does not cost today.
        from backend.cockpit_v4 import product_knowledge as pk

        coverage = pk.coverage(record.question)
        # §13. A data-analysis turn does not open with a product lookup.
        #
        # The live turn that died at sixty seconds was a follow-up in an
        # analytical thread, and `inspect_product_knowledge` was on the
        # table for its first action. Every tool on a call is a thing the
        # model must consider, and the one that cannot contribute to this
        # answer is the one worth taking off. It comes back for the second
        # action like any withheld tool, so nothing is lost if the reader
        # really did want to know how ECL is defined.
        analytical = bool(verdict is not None and verdict.analytical)
        withhold = ((TOOL_PRODUCT,)
                    if (coverage["level"] == pk.COVERAGE_SYNOPSIS
                        or analytical)
                    else ())
        # The tool contract speaks THIS book's period language. Handing a
        # monthly run a schema whose worked example is "the latest populated
        # quarter 2026Q2 against 2026Q1" is how a live Corporate thread came
        # back saying the book was quarterly: the analyst believed the
        # contract over the catalogue, because the contract is the thing it
        # has to fill in.
        # TWO tool sets, for two different jobs.
        #
        # `full_tools` is what a turn that WRITES THE ANSWER needs: the whole
        # finalize contract, narrative and claims and presentation included.
        # An ACTION turn gets the stop-early subset of it instead -- about
        # seven kilobytes less on every analytical action, and, more to the
        # point, no longer a detailed description of an answer handed to a
        # turn whose only job is to choose what to run.
        full_tools = provider_tools(catalog=book.catalog,
                                    max_steps=limits.steps_per_batch)
        analyst = Analyst(
            provider=provider,
            capability=capability, ledger=ledger,
            system=packet.system_blocks,
            tools=provider_tools(
                withhold=withhold, catalog=book.catalog,
                stage="analytical_action" if analytical else "",
                max_steps=limits.steps_per_batch))
        analyst.user(packet.first_user_message)

        from backend.cockpit_v4 import intent_envelope as intent_env
        from backend.cockpit_v4 import pyrunner
        from backend.cockpit_v4 import release as release_mod

        # The execution header: which release, which bytes, which currency.
        # Pinned once per run and stamped on everything it produces.
        header = release_mod.header(
            release_id=book.release_id, catalog=book.catalog,
            release_summary=release_summary,
            tenant_id=record.tenant_id)

        orchestrator = Orchestrator(
            run=record, store=self.store, ledger=ledger, analyst=analyst,
            catalog_service=CatalogService(
                catalog=book.catalog, scope=scope,
                coverage=getattr(book, "coverage", None), session=session),
            execution_service=ExecutionService(
                session=session, scope=scope, catalog=book.catalog,
                store=self.store, run_id=record.run_id,
                tenant_id=record.tenant_id, release_id=book.release_id,
                limits=limits, python_runner=pyrunner.PythonRunner(),
                header=header),
            artifact_service=ArtifactService(
                store=self.store, tenant_id=record.tenant_id,
                release_id=book.release_id, limits=limits),
            finalizer=Finalizer(
                store=self.store, tenant_id=record.tenant_id,
                release_id=book.release_id, limits=limits,
                header=header,
                # `getattr`, not `book.domain_id`: `_LegacyBook` carries no
                # domain id on purpose, because the pre-domain release it
                # wraps does not have one. A run with no book has no credit
                # policy to cite and the citation check stands down.
                domain_id=str(getattr(book, "domain_id", "") or "")),
            emitter=emitter, catalog=book.catalog,
            cancel_check=lambda: bool(
                (self.store.get_run(record.run_id) or record).cancel_requested),
            deferred_tools=(
                provider_tools(
                    catalog=book.catalog,
                    stage="analytical_action" if analytical else "",
                    max_steps=limits.steps_per_batch)
                if withhold else None),
            # The full contract, restored for the turn that writes the
            # answer. See `Orchestrator._action_surface`.
            answer_tools=full_tools,
            # What the ACTION STATE MACHINE reads: whether this question's
            # governed metadata is already resolved, and whether the request
            # is analytical at all. Both are settled by the server before
            # the first provider call.
            readiness=dict(packet.payload.get("analysis_readiness") or {}),
            analytical=analytical,
            investigation=(seeded or {}).get("body") if seeded else None,
            value_resolution=packet.payload.get("value_resolution") or {},
            # §24-§27. The run's intent is SETTLED before the first provider
            # call, and no tool asks the analyst to restate it. Which book,
            # which release, what kind of turn this is and what a period
            # means here are facts the server already holds -- and a nested
            # `intent` object retyped on every call is how a live Mac run
            # got `intent must be an object` twice in two rounds.
            envelope=intent_env.for_run(
                scope=scope, catalog=book.catalog, verdict=verdict,
                value_resolution=packet.payload.get("value_resolution") or {},
                seeded=(seeded or {}).get("body") if seeded else None))
        orchestrator._version = record.version
        return orchestrator.run_to_completion()

    def _book_for(self, record: Any) -> Any:
        """The analytical book this run was accepted against.

        Two shapes, and no third. A run accepted against a DOMAIN release
        gets that domain's book, opened per run and cached by tenant, domain,
        release AND fingerprint. A run accepted against the pre-domain
        release gets THAT release, through the runtime this process was
        configured with -- and only when the two release ids are the same
        string, because a runtime configured for another release is not this
        run's book either.

        What is deliberately absent is the path that used to exist: "use
        whatever catalogue the process has". That is how a Retail thread
        could read corporate relations with nothing in the answer saying so.
        """
        try:
            return arun.for_run(record, store=self.store)
        except arun.LegacyRelease as legacy:
            configured = str(getattr(self.runtime.cfg, "release_id", "") or "")
            if legacy.release_id != configured:
                raise _NoBook(
                    f"This run was accepted against release "
                    f"{legacy.release_id!r}, which is neither a domain "
                    f"release nor the release this runtime is configured "
                    f"for ({configured!r}). Nothing was substituted."
                ) from legacy
            return _LegacyBook(runtime=self.runtime, record=record)
        except arun.AnalyticalRuntimeUnavailable as exc:
            raise _NoBook(str(exc)) from exc

    def _settle(self, record: Any, outcome: Outcome, emitter: ev.Emitter,
                ledger: Ledger) -> None:
        """Persist the answer, THEN publish `answer.ready`.

        Order matters: an answer that was emitted but not stored cannot be
        retrieved after a UI failure, and the user is told to ask again --
        paying for the same analysis twice.
        """
        try:
            current = self.store.get_run(record.run_id)
            version = current.version if current else record.version
            # The transcript row goes in BEFORE the run is marked terminal.
            #
            # It used to go in after, and the ordering was observable: a
            # reader who clicked back the instant the answer appeared reached
            # the landing page before the turn existed, and "Continue where
            # you left off" -- which lists threads that HAVE a turn -- did
            # not list the conversation they had just held. The run was
            # terminal and its thread was still empty.
            #
            # `append_turn` is idempotent per run, so a settle that then
            # loses its lease cannot produce a second copy of the exchange.
            if outcome.response is not None:
                try:
                    self.store.append_turn(
                        thread_id=record.thread_id, run_id=record.run_id,
                        question=record.question, answer=outcome.response)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "V4 could not append the thread turn for %s",
                        record.run_id)
            self.store.update_state(
                record.run_id, expect_version=version, state=outcome.state,
                operation="", budget=ledger.snapshot(),
                error_code=outcome.error_code, error_id=outcome.error_id,
                final_response=outcome.response, terminal=True)
        except LeaseLost:
            emitter.append(
                ev.RUN_INTERRUPTED, stage="publishing", operation="settle",
                status=ev.STATUS_FAILED,
                public_message=("This run was settled elsewhere; its result "
                                "was not overwritten."))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("V4 run %s could not be settled", record.run_id)
            emitter.append(
                ev.RUN_FAILED, stage="publishing", operation="settle",
                status=ev.STATUS_FAILED,
                public_message=f"The result could not be stored: {exc}")
            return

        if outcome.response is not None:
            emitter.append(
                ev.ANSWER_READY, stage="publishing", operation="publish",
                status=ev.STATUS_OK,
                public_message="Answer ready.")
        elif (outcome.state not in (st.CANCELLED,)
              and not outcome.terminal_event_emitted):
            # Only when the orchestrator has not already said so, and said it
            # at the stage the run actually stopped at.
            emitter.append(
                ev.RUN_EXPIRED if outcome.state == st.EXPIRED
                else ev.RUN_FAILED,
                stage="publishing", operation="publish",
                status=ev.STATUS_FAILED, error_id=outcome.error_id,
                public_message=outcome.message
                or f"The request stopped: {outcome.error_code}.")

        # Memory maintenance runs AFTER the answer is published, on its own
        # quota, and cannot reopen this run.
        try:
            from backend.cockpit_v4 import memory

            memory.maybe_schedule(self.store, record.thread_id,
                                  cfg=self.runtime.cfg)
        except Exception:  # noqa: BLE001
            logger.info("V4 memory maintenance skipped for thread %s",
                        record.thread_id)


class _Heartbeat(threading.Thread):
    """Keeps the lease alive while the run works."""

    def __init__(self, store: RunStore, run_id: str, worker_id: str) -> None:
        super().__init__(daemon=True, name=f"v4-heartbeat-{run_id[:8]}")
        self.store = store
        self.run_id = run_id
        self.worker_id = worker_id
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self.store.heartbeat(self.run_id, self.worker_id)
            except LeaseLost:
                return
            except Exception:  # noqa: BLE001
                return

    def stop(self) -> None:
        self._stop.set()


__all__ = ["Worker"]
