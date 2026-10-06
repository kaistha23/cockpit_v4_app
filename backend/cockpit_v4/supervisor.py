"""
The independent supervisor: deadlines and abandoned runs.

Separate from the worker on purpose. A cooperative deadline check inside the
orchestration loop is only reached when the loop is running; a worker blocked
on a socket, or one whose process died, never reaches it. So this polls the
store, settles what the worker cannot, and is the reason a browser stops
spinning when a worker disappears.

It never replays work. An abandoned run becomes INTERRUPTED with its last
operation and its preserved artifacts, and a retry is a new explicit run.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.finalization import (RESULT_ONLY_REASON
                                             as _RESULT_REASON)
from backend.cockpit_v4.run_store import LeaseLost, RunStore, TerminalAlready

logger = logging.getLogger(__name__)

#: `operation` is a machine tag -- "generation", "batch", "context". Dropped
#: into "The standard deadline passed while ___" it produced "…passed while
#: generation.", which is what a reader was actually shown. These are the
#: same facts as sentences.
_DOING = {
    "generation": "CreditProbe was writing",
    "generate": "CreditProbe was writing",
    "provider_request": "CreditProbe was writing",
    "batch": "the query was running",
    "context": "the question was being prepared",
    "semantics": "the question was being prepared",
    "intake": "the question was being prepared",
    "preflight": "the request was being checked",
    "publish": "the answer was being published",
    "publish_result_only": "the result was being published",
}


def _doing(record: Any) -> str:
    """What the run was doing, as a clause a reader can read."""
    operation = str(getattr(record, "operation", "") or "")
    return _DOING.get(operation, "this request was working")


@dataclass
class Supervisor:
    store: RunStore
    poll_seconds: float = 2.0
    lease_stale_seconds: float = 10.0
    #: How far past its deadline a run must be before this settles it.
    #:
    #: The worker's own ledger raises inside the loop and settles the run by
    #: PUBLISHING what it computed. This cannot; it only knows the row. They
    #: used to race on the same instant and this one usually won, so a run
    #: that had executed its query and stored its rows was reported as
    #: nothing but "This request ran out of time".
    #:
    #: They are not equals. Held back by more than a clean settlement takes,
    #: this goes back to being what it is for -- a worker that is blocked on
    #: a socket or gone -- and costs a genuinely lost run only these few
    #: seconds of extra spinning.
    grace_seconds: float = 15.0
    stop_event: threading.Event = field(default_factory=threading.Event)

    def serve_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                logger.exception("V4 supervisor sweep failed")
            self.stop_event.wait(self.poll_seconds)

    def stop(self) -> None:
        self.stop_event.set()

    def sweep(self) -> dict[str, int]:
        expired = self._settle_expired()
        interrupted = self._settle_abandoned()
        return {"expired": expired, "interrupted": interrupted}

    def _settle_expired(self) -> int:
        settled = 0
        for record in self.store.expiring_runs(
                grace_seconds=self.grace_seconds):
            if self._terminate(
                    record, st.EXPIRED, st.DEADLINE_EXPIRED, ev.RUN_EXPIRED,
                    f"The {record.mode} deadline passed while "
                    f"{_doing(record)}.",
                    result_reason=_RESULT_REASON[st.DEADLINE_EXPIRED]):
                settled += 1
        return settled

    def _settle_abandoned(self) -> int:
        settled = 0
        for record in self.store.stale_runs(
                stale_seconds=self.lease_stale_seconds):
            if self._terminate(
                    record, st.INTERRUPTED, st.WORKER_LOST,
                    ev.RUN_INTERRUPTED,
                    ("The process handling this request stopped responding. "
                     "Nothing was replayed: a paid call or an executed query "
                     "may already have happened, and repeating it "
                     "automatically would spend it twice. Any results "
                     "already produced are preserved.")):
                settled += 1
        return settled

    def _rows_already_computed(self, record: Any,
                               reason: str) -> dict[str, Any] | None:
        """The result this run stored, as a publishable answer.

        A RUN THIS SETTLES MAY HAVE ALREADY DONE THE WORK. It executed its
        query, stored its artifacts and was part-way through writing about
        them when its clock ran out. The orchestrator has a path for exactly
        that -- `result_only_response`, which publishes the rows with a
        server-written caveat in place of the narrative -- and
        `DEADLINE_EXPIRED` has been on its eligibility list all along.

        It was unreachable from here. It lives on the Orchestrator, this
        settles runs from outside the worker, and so the one component that
        routinely ended these runs was the one component that threw the rows
        away. A reader saw a bare red box while the tables sat in the
        artifact store, individually serveable.

        Returns `None` when there is nothing to publish -- no artifacts, or
        an answer already written -- which is the correct outcome for a run
        that never got that far.
        """
        from backend.cockpit_v4.finalization import Finalizer

        if getattr(record, "final_response", None):
            return None
        tenant_id = str(getattr(record, "tenant_id", "") or "")
        try:
            artifacts = self.store.artifact_ids_for_run(
                record.run_id, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            return None
        if not artifacts:
            return None

        # The book this run was accepted in, for the units and the header.
        # Best effort: rows with no unit beat no rows at all, so an
        # unopenable book degrades the table rather than withholding it.
        catalog = header = None
        try:
            from backend.cockpit_v4 import analytical_runtime as arun
            from backend.cockpit_v4 import release as release_mod

            runtime = arun.for_run(record, store=self.store)
            catalog = runtime.catalog
            header = release_mod.header(
                release_id=runtime.release_id, catalog=catalog,
                release_summary=runtime.release_summary(),
                tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            pass

        try:
            from backend.cockpit_v4.config import analytical_limits_for

            finalizer = Finalizer(
                store=self.store, tenant_id=tenant_id,
                release_id=str(getattr(record, "release_id", "") or ""),
                limits=analytical_limits_for(record.mode),
                run_artifacts=set(artifacts), header=header,
                domain_id=str(getattr(record, "domain_id", "") or ""))
            body = finalizer.result_only_response(reason=reason,
                                                  catalog=catalog)
        except Exception:  # noqa: BLE001
            logger.exception("V4 supervisor could not publish %s's result",
                             record.run_id)
            return None
        return body or None

    def _terminate(self, record: Any, state: str, code: str,
                   event_type: str, message: str,
                   result_reason: str = "") -> bool:
        published = (self._rows_already_computed(record, result_reason)
                     if result_reason else None)
        try:
            self.store.update_state(
                record.run_id, expect_version=record.version,
                # ROWS THAT EXIST ARE NOT A FAILURE. A run that computed its
                # result and lost only the write-up settles as PARTIAL with
                # that result, exactly as the worker's own deadline path
                # settles it. The error code is kept either way: what
                # stopped the run is still true.
                state=st.PARTIAL if published else state,
                error_code=code, final_response=published, terminal=True)
        except (LeaseLost, TerminalAlready):
            # The worker settled it first. That outcome stands.
            return False
        except Exception:  # noqa: BLE001
            logger.exception("V4 supervisor could not settle %s",
                             record.run_id)
            return False
        emitter = ev.Emitter(self.store, record.run_id,
                             started_monotonic=time.monotonic())
        emitter.append(event_type, stage="publishing", operation="supervisor",
                       status=ev.STATUS_FAILED, public_message=message)
        if published:
            emitter.append(
                ev.ANALYSIS_PRESERVED, stage="publishing",
                operation="publish_result_only", status=ev.STATUS_OK,
                public_message=(
                    "The analysis had already run, so its result is "
                    "published without the written answer."))
        return True


__all__ = ["Supervisor"]
