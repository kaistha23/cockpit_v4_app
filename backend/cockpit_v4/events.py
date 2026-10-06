"""
The persisted event sequence, and the SSE frames built from it.

Why events are persisted rather than pushed
-------------------------------------------
A pushed event that nobody received did not happen, and a UI that renders a
stage it inferred is guessing. So every event here is committed to the run
store BEFORE it is streamed, carries a monotonic `seq` within its run, and is
replayable from a cursor. A browser that reconnects reads the same rows the
worker wrote; it never reconstructs a missing step, and it never invents one.

Heartbeats are deliberately NOT durable. They are a transport liveness signal,
not a state change, and writing a row every five seconds would bury the actual
timeline in noise.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.cockpit_v4 import EVENT_SCHEMA_VERSION

# ---- event types -------------------------------------------------------

RUN_ACCEPTED = "run.accepted"
RUN_STARTED = "run.started"
CONTEXT_READY = "context.ready"
MODEL_REQUESTED = "model.requested"
MODEL_RESPONSE_RECEIVED = "model.response_received"
MODEL_PARSED = "model.parsed"
INTENT_VALIDATED = "intent.validated"
TOOL_REQUESTED = "tool.requested"
TOOL_VALIDATED = "tool.validated"
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
TOOL_FAILED = "tool.failed"
RETRY_REQUESTED = "retry.requested"
ANSWER_VALIDATED = "answer.validated"
ANSWER_READY = "answer.ready"
#: The analysis completed and its result was kept, but the written answer
#: could not be published. Emitted so a reader is not left to conclude that
#: the query failed: in the live case that prompted it, the SQL had already
#: returned twelve correct rows.
ANALYSIS_PRESERVED = "analysis.preserved"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"
RUN_EXPIRED = "run.expired"
RUN_INTERRUPTED = "run.interrupted"
MEMORY_STARTED = "memory.started"
MEMORY_COMPLETED = "memory.completed"
MEMORY_FAILED = "memory.failed"

EVENT_TYPES: tuple[str, ...] = (
    RUN_ACCEPTED, RUN_STARTED, CONTEXT_READY, MODEL_REQUESTED,
    MODEL_RESPONSE_RECEIVED, MODEL_PARSED, INTENT_VALIDATED, TOOL_REQUESTED,
    TOOL_VALIDATED, TOOL_STARTED, TOOL_COMPLETED, TOOL_FAILED,
    RETRY_REQUESTED, ANSWER_VALIDATED, ANSWER_READY, ANALYSIS_PRESERVED,
    RUN_FAILED,
    RUN_CANCELLED, RUN_EXPIRED, RUN_INTERRUPTED, MEMORY_STARTED,
    MEMORY_COMPLETED, MEMORY_FAILED)

#: Events after which no further analytical event may be appended to the run.
TERMINAL_EVENTS: frozenset[str] = frozenset({
    ANSWER_READY, RUN_FAILED, RUN_CANCELLED, RUN_EXPIRED, RUN_INTERRUPTED})

#: Memory runs on its own linked sequence and cannot reopen a settled run.
MEMORY_EVENTS: frozenset[str] = frozenset({
    MEMORY_STARTED, MEMORY_COMPLETED, MEMORY_FAILED})

STATUS_STARTED = "started"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

#: What a STAGE INSTANCE is, as opposed to what one event says. A stage is
#: running from the event that opened it until the event that closes it, and
#: the server says which -- a panel that inferred "running" from the absence
#: of a later event would show a stage running forever whenever one event
#: was dropped.
STAGE_RUNNING = "running"
STAGE_DONE = "done"
STAGE_FAILED = "failed"
STAGE_STATES: tuple[str, ...] = (STAGE_RUNNING, STAGE_DONE, STAGE_FAILED)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Event:
    """One committed fact about a run. Every field is something that happened.

    `public_message` is business language for the process panel. `detail_ref`
    points at an operator-only record: model ids, request sizes, provider
    request ids, the exact code. Nothing secret, and no private reasoning,
    goes into either -- see `detail.py` redaction on the way in.
    """

    run_id: str
    seq: int
    event_type: str
    stage: str
    operation: str
    status: str
    public_message: str
    occurred_at: str = field(default_factory=now_iso)
    elapsed_ms: int = 0
    attempt: int = 0
    submission: int = 0
    round: int = 0
    detail_ref: str = ""
    error_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    #: §33-§36. Which RUN of this stage the event belongs to.
    #:
    #: A stage is not a row on a list: a run can re-enter `preparing` after a
    #: failed submission, and two passes through it are two things that
    #: happened. The instance id distinguishes them, so the panel shows two
    #: rows in the order they ran rather than one row whose state flickers.
    stage_instance_id: str = ""
    #: When this instance began, and what state it is in as of this event.
    #: Both come from the server: a panel that inferred "running" from the
    #: absence of a later event would show a stage running forever whenever
    #: one event was dropped.
    stage_started_ms: int = 0
    stage_state: str = ""
    #: The stage instances this event CLOSED, in the order they closed.
    #:
    #: §35: the previous foreground step must close when the next exclusive
    #: one begins, and saying so explicitly is what stops a reconnect
    #: leaving a stage open. Usually zero or one -- the instance this event
    #: displaced. A terminal event closes two: whatever it displaced, and
    #: then its own, because a run that ended left nothing running and the
    #: last event is the only place that can say so.
    closed_stages: list[dict[str, Any]] = field(default_factory=list)
    #: How many events in this instance reported a failure. A stage that
    #: succeeded on a retry is neither a clean tick nor a failure, and the
    #: count is what lets the panel say which.
    stage_failures: int = 0
    event_id: str = field(default_factory=lambda: f"ev-{uuid.uuid4().hex}")
    schema_version: str = EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "event_id": self.event_id,
            "run_id": self.run_id, "seq": self.seq,
            "event_type": self.event_type, "stage": self.stage,
            "operation": self.operation, "status": self.status,
            "occurred_at": self.occurred_at, "elapsed_ms": self.elapsed_ms,
            "attempt": self.attempt, "submission": self.submission,
            "round": self.round, "public_message": self.public_message,
            "detail_ref": self.detail_ref, "error_id": self.error_id,
            "trace_id": self.trace_id, "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "stage_instance_id": self.stage_instance_id,
            "stage_started_ms": self.stage_started_ms,
            "stage_state": self.stage_state,
            "stage_failures": self.stage_failures,
            "closed_stages": [dict(c) for c in self.closed_stages]}

    def to_sse(self) -> str:
        """One `text/event-stream` frame, with the id a reconnect resumes from."""
        body = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return (f"id: {self.seq}\n"
                f"event: {self.event_type}\n"
                f"data: {body}\n\n")


def heartbeat_frame() -> str:
    """A comment frame. Keeps the connection warm; commits nothing."""
    return f": heartbeat {int(time.time())}\n\n"


class Emitter:
    """Appends events for one run. The only writer of the run's sequence.

    Deliberately not a queue: `append` returns after the row is committed, so
    a caller that has an event id knows the fact is durable. The publish path
    reads committed rows.
    """

    def __init__(self, store: Any, run_id: str, *, started_monotonic: float,
                 trace_id: str = "") -> None:
        self.store = store
        self.run_id = run_id
        self._t0 = started_monotonic
        self.trace_id = trace_id or f"tr-{uuid.uuid4().hex}"
        self._span: str = ""
        # The stage state machine. One foreground stage at a time, each pass
        # through it a distinct instance. §33-§36.
        self._stage: str = ""
        self._instance: str = ""
        self._instance_started_ms: int = 0
        self._instance_state: str = ""
        self._instance_failures: int = 0
        self._passes: dict[str, int] = {}

    def span(self, span_id: str) -> None:
        self._span = span_id

    @property
    def stage_instance_id(self) -> str:
        """The foreground stage instance, or "" before anything started."""
        return self._instance

    def append(self, event_type: str, *, stage: str, operation: str,
               status: str, public_message: str, attempt: int = 0,
               submission: int = 0, round: int = 0, detail_ref: str = "",
               error_id: str = "", parent_span_id: str = "") -> Event:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        elapsed = int((time.monotonic() - self._t0) * 1000)
        closed = self._enter(stage, elapsed)
        # The instance's state is the state of its LAST event. A success
        # after a failure is a success -- the failure survives in the count
        # and in the substep list, which is what an audit trace is for.
        self._instance_state = self._state_word(status)
        if self._instance_state == STAGE_FAILED:
            self._instance_failures += 1
        event = Event(
            run_id=self.run_id, seq=0, event_type=event_type, stage=stage,
            operation=operation, status=status, public_message=public_message,
            elapsed_ms=elapsed,
            attempt=attempt, submission=submission, round=round,
            detail_ref=detail_ref, error_id=error_id, trace_id=self.trace_id,
            span_id=self._span or f"sp-{uuid.uuid4().hex[:16]}",
            parent_span_id=parent_span_id,
            stage_instance_id=self._instance,
            stage_started_ms=self._instance_started_ms,
            stage_state=self._instance_state,
            stage_failures=self._instance_failures,
            closed_stages=closed)
        if event_type in TERMINAL_EVENTS:
            # A run that ended left no stage running, and the last event is
            # the only place that can say so: a browser that reconnects
            # after it must not be left with a stage spinning forever.
            own = self._close(elapsed)
            if own:
                event.closed_stages = list(event.closed_stages) + [own]
        return self.store.append_event(event)

    def _enter(self, stage: str, elapsed: int) -> list[dict[str, Any]]:
        """Open `stage` as the foreground instance, closing any other.

        Returns the closing records this event carries. An event that stays
        in the stage already open closes nothing and returns `[]`.
        """
        if stage == self._stage:
            return []
        closed = self._close(elapsed)
        self._stage = stage
        self._passes[stage] = self._passes.get(stage, 0) + 1
        self._instance = f"{stage}#{self._passes[stage]}"
        self._instance_started_ms = elapsed
        self._instance_state = STAGE_RUNNING
        self._instance_failures = 0
        return [closed] if closed else []

    def _close(self, elapsed: int) -> dict[str, Any]:
        if not self._instance:
            return {}
        closed = {
            "stage": self._stage,
            "stage_instance_id": self._instance,
            "started_ms": self._instance_started_ms,
            "ended_ms": elapsed,
            "failures": self._instance_failures,
            # A stage whose last event failed closed failed. One that failed
            # and then succeeded closed DONE, with its failures counted.
            "state": (STAGE_FAILED if self._instance_state == STAGE_FAILED
                      else STAGE_DONE),
        }
        self._instance = ""
        self._stage = ""
        self._instance_state = ""
        self._instance_failures = 0
        return closed

    @staticmethod
    def _state_word(status: str) -> str:
        """What this event says about its stage.

        Only a FAILURE changes the state: every other status means the stage
        is still the foreground one, and a stage is done when something else
        starts or the run ends -- not because one of its steps reported ok.
        """
        return (STAGE_FAILED if status in (STATUS_FAILED, STATUS_REJECTED)
                else STAGE_RUNNING)


#: The business-language stage labels the process panel shows. A stage that
#: has not started is shown as prospective, never as done: `PROSPECTIVE` is
#: what the UI renders with an empty circle.
STAGE_LABELS: dict[str, str] = {
    "accepted": "Request accepted",
    "understanding": "Understanding the request",
    "catalog": "Reading relevant data definitions",
    "product_knowledge": "Reading product knowledge",
    "preparing": "Preparing query",
    "validating": "Validating query",
    "executing": "Executing query",
    "reviewing": "Reviewing results",
    "publishing": "Validating and publishing answer",
    "memory": "Memory maintenance",
}

#: The order the panel lays stages out in when it has no events yet. The
#: ACTUAL path is dynamic -- product help never reaches `catalog` -- so this
#: is a layout hint, not a promise that every stage runs.
STAGE_ORDER: tuple[str, ...] = (
    "accepted", "understanding", "product_knowledge", "catalog", "preparing",
    "validating", "executing", "reviewing", "publishing")


__all__ = ["ANALYSIS_PRESERVED", "ANSWER_READY", "ANSWER_VALIDATED",
           "CONTEXT_READY", "Emitter",
           "EVENT_TYPES", "Event", "INTENT_VALIDATED", "MEMORY_COMPLETED",
           "MEMORY_EVENTS", "MEMORY_FAILED", "MEMORY_STARTED",
           "MODEL_PARSED", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED",
           "RETRY_REQUESTED", "RUN_ACCEPTED", "RUN_CANCELLED", "RUN_EXPIRED",
           "RUN_FAILED", "RUN_INTERRUPTED", "RUN_STARTED", "STAGE_LABELS",
           "STAGE_DONE", "STAGE_FAILED", "STAGE_ORDER", "STAGE_RUNNING",
           "STAGE_STATES", "STATUS_FAILED", "STATUS_OK", "STATUS_REJECTED",
           "STATUS_STARTED", "TERMINAL_EVENTS", "TOOL_COMPLETED",
           "TOOL_FAILED", "TOOL_REQUESTED", "TOOL_STARTED", "TOOL_VALIDATED",
           "heartbeat_frame", "now_iso"]
