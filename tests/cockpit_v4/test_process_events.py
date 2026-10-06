"""UNIT + REAL WORKER. The process panel shows what happened, in order.

§33-§37. A step appears when it starts; the previous foreground step closes
when the next exclusive one begins; the sequence, the stage instance, the
start, the end and the state are all PERSISTED rather than inferred; and a
replay or a reconnect can neither duplicate a step nor reorder the trace.

The live evidence
-----------------
The Mac browser run showed "Request accepted — not started" and
"Understanding the request — not started" at 0s, side by side, as the first
thing on screen. Both rows were laid out by the client before anything had
happened: a guess about the future dressed as progress.

The second half is subtler and is what this file mostly exists for. The
panel closed a stage by INFERENCE -- "any earlier stage still marked running
has been left behind" -- which holds only while stages run in array order and
never re-enter. A run that re-enters `preparing` after a failed submission
breaks it in both directions: the second pass reopens the first pass's row,
and a stage that ran out of order stays spinning forever.

So the server runs the state machine and says what it did. The client applies
it. Nothing on the panel is derived from the absence of an event.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import events as ev


class Recorder:
    """The store, reduced to the one thing the emitter needs from it."""

    def __init__(self) -> None:
        self.rows: list[ev.Event] = []

    def append_event(self, event: ev.Event) -> ev.Event:
        event.seq = len(self.rows) + 1
        self.rows.append(event)
        return event


@pytest.fixture
def emitter():
    store = Recorder()
    return ev.Emitter(store, "run-1", started_monotonic=0.0), store


def rows(store: Recorder) -> list[dict]:
    return [row.to_dict() for row in store.rows]


# ---- §35, §36: one foreground stage, and it closes ----------------------

def test_the_previous_stage_closes_when_the_next_one_begins(emitter):
    emit, store = emitter
    emit.append(ev.RUN_STARTED, stage="accepted", operation="start",
                status=ev.STATUS_OK, public_message="a")
    emit.append(ev.CONTEXT_READY, stage="understanding", operation="intent",
                status=ev.STATUS_OK, public_message="b")

    first, second = rows(store)
    assert first["closed_stages"] == [], (
        "the first event of a run displaces nothing")
    assert first["stage_instance_id"] == "accepted#1"
    assert first["stage_state"] == ev.STAGE_RUNNING

    closed = second["closed_stages"]
    assert len(closed) == 1
    assert closed[0]["stage_instance_id"] == "accepted#1"
    assert closed[0]["state"] == ev.STAGE_DONE
    assert closed[0]["ended_ms"] >= closed[0]["started_ms"]
    assert second["stage_instance_id"] == "understanding#1"


def test_two_events_in_one_stage_close_nothing(emitter):
    emit, store = emitter
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="a")
    emit.append(ev.TOOL_COMPLETED, stage="preparing", operation="bind",
                status=ev.STATUS_OK, public_message="b")
    first, second = rows(store)
    assert first["stage_instance_id"] == second["stage_instance_id"]
    assert second["closed_stages"] == []


def test_a_re_entered_stage_is_a_second_instance(emitter):
    """§34. Two passes through `preparing` are two things that happened.

    One row whose state flickers hides the first pass entirely, and the
    first pass is the one that failed.
    """
    emit, store = emitter
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="first")
    emit.append(ev.TOOL_FAILED, stage="preparing", operation="bind",
                status=ev.STATUS_REJECTED, public_message="no")
    emit.append(ev.CONTEXT_READY, stage="understanding", operation="repair",
                status=ev.STATUS_OK, public_message="rethink")
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="second")

    instances = [r["stage_instance_id"] for r in rows(store)]
    assert instances == ["preparing#1", "preparing#1", "understanding#1",
                         "preparing#2"]
    closed = rows(store)[2]["closed_stages"]
    assert closed[0]["stage_instance_id"] == "preparing#1"
    assert closed[0]["state"] == ev.STAGE_FAILED
    assert closed[0]["failures"] == 1


def test_a_stage_that_recovered_closes_done_with_its_failure_counted(emitter):
    """A success after a failure is a success -- and the failure stays."""
    emit, store = emitter
    emit.append(ev.TOOL_STARTED, stage="executing", operation="run",
                status=ev.STATUS_STARTED, public_message="go")
    emit.append(ev.TOOL_FAILED, stage="executing", operation="run",
                status=ev.STATUS_FAILED, public_message="no")
    emit.append(ev.TOOL_COMPLETED, stage="executing", operation="run",
                status=ev.STATUS_OK, public_message="yes")
    emit.append(ev.ANSWER_READY, stage="publishing", operation="publish",
                status=ev.STATUS_OK, public_message="done")

    closed = rows(store)[3]["closed_stages"]
    executing = next(c for c in closed
                     if c["stage_instance_id"] == "executing#1")
    assert executing["state"] == ev.STAGE_DONE
    assert executing["failures"] == 1


def test_a_terminal_event_leaves_no_stage_running(emitter):
    """§36. A browser that reconnects after the last frame must not be left
    with a stage spinning, and the last frame is the only place that can
    say so."""
    emit, store = emitter
    emit.append(ev.RUN_STARTED, stage="accepted", operation="start",
                status=ev.STATUS_OK, public_message="a")
    emit.append(ev.ANSWER_READY, stage="publishing", operation="publish",
                status=ev.STATUS_OK, public_message="b")

    last = rows(store)[-1]
    ids = [c["stage_instance_id"] for c in last["closed_stages"]]
    assert ids == ["accepted#1", "publishing#1"]
    assert all(c["state"] == ev.STAGE_DONE for c in last["closed_stages"])
    assert emit.stage_instance_id == "", "nothing is left open"


@pytest.mark.parametrize("terminal", sorted(ev.TERMINAL_EVENTS))
def test_every_terminal_event_closes_its_own_stage(terminal):
    store = Recorder()
    emit = ev.Emitter(store, "run-1", started_monotonic=0.0)
    emit.append(ev.RUN_STARTED, stage="accepted", operation="start",
                status=ev.STATUS_OK, public_message="a")
    emit.append(terminal, stage="publishing", operation="end",
                status=ev.STATUS_OK, public_message="b")
    assert emit.stage_instance_id == "", terminal
    assert any(c["stage_instance_id"] == "publishing#1"
               for c in rows(store)[-1]["closed_stages"]), terminal


# ---- §35: every field the panel reads is persisted ---------------------

#: What the panel needs that cannot be inferred. Checked as a set: losing
#: one of them is not a missing label, it is a stage that never closes.
PERSISTED = ("seq", "stage", "stage_instance_id", "stage_started_ms",
             "stage_state", "stage_failures", "closed_stages", "elapsed_ms")


def test_every_event_persists_the_state_machine(emitter):
    emit, store = emitter
    for stage in ("accepted", "understanding", "preparing", "executing"):
        emit.append(ev.TOOL_STARTED, stage=stage, operation="x",
                    status=ev.STATUS_STARTED, public_message=stage)
    for body in rows(store):
        for key in PERSISTED:
            assert key in body, key
        assert body["stage_state"] in ev.STAGE_STATES
        assert body["stage_started_ms"] <= body["elapsed_ms"]
        # And it survives serialization, which is what the SSE frame is.
        assert json.loads(json.dumps(body)) == body


def test_the_sse_frame_carries_the_state_machine(emitter):
    emit, store = emitter
    emit.append(ev.RUN_STARTED, stage="accepted", operation="start",
                status=ev.STATUS_OK, public_message="a")
    frame = store.rows[0].to_sse()
    body = json.loads(frame.split("data: ", 1)[1].strip())
    assert body["stage_instance_id"] == "accepted#1"
    assert body["stage_state"] == ev.STAGE_RUNNING
    assert body["closed_stages"] == []


# ---- §37: replay and reconnect ------------------------------------------

def test_the_sequence_is_dense_and_increasing(emitter):
    """A client resumes from `Last-Event-ID`, so a gap or a repeat in the
    sequence is a frame it will either miss or apply twice."""
    emit, store = emitter
    for i in range(12):
        emit.append(ev.TOOL_STARTED, stage=f"stage{i % 3}", operation="x",
                    status=ev.STATUS_STARTED, public_message=str(i))
    seqs = [r["seq"] for r in rows(store)]
    assert seqs == list(range(1, 13))


def test_replaying_the_whole_stream_reaches_the_same_state(emitter):
    """§37. The panel is a fold over the sequence, so replaying it from the
    beginning must land where the live stream did -- otherwise a reconnect
    shows a different run from the one that happened."""
    emit, store = emitter
    emit.append(ev.RUN_STARTED, stage="accepted", operation="start",
                status=ev.STATUS_OK, public_message="a")
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="b")
    emit.append(ev.TOOL_FAILED, stage="preparing", operation="bind",
                status=ev.STATUS_REJECTED, public_message="c")
    emit.append(ev.TOOL_STARTED, stage="executing", operation="run",
                status=ev.STATUS_STARTED, public_message="d")
    emit.append(ev.ANSWER_READY, stage="publishing", operation="publish",
                status=ev.STATUS_OK, public_message="e")

    once = _fold(rows(store))
    twice = _fold(rows(store) + rows(store))
    assert once == twice, "a replayed frame changed the panel"

    # And an out-of-order delivery lands in the same place, because the
    # fold is keyed on the sequence rather than on arrival.
    shuffled = list(reversed(rows(store)))
    assert _fold(sorted(shuffled, key=lambda r: r["seq"])) == once


def _fold(stream: list[dict]) -> list[tuple]:
    """The same fold the client reducer performs, in Python.

    Deliberately a SECOND implementation. The point is that the persisted
    stream determines the panel: if this and `reducer.ts` can disagree, the
    stream is not enough and something on screen is coming from somewhere
    else.
    """
    steps: dict[str, dict] = {}
    seen = 0
    for event in stream:
        if event["seq"] <= seen:
            continue
        seen = event["seq"]

        # OPEN first, then close. A terminal event closes its OWN instance
        # as well as the one it displaced, and closing before opening would
        # apply that close to a step that does not exist yet -- leaving the
        # last stage of every run spinning forever on a reconnect.
        instance = event["stage_instance_id"]
        step = steps.setdefault(instance, {
            "stage": event["stage"], "opened": event["seq"],
            "state": ev.STAGE_RUNNING, "elapsed": 0, "failures": 0})
        if event["stage_state"] == ev.STAGE_FAILED:
            step["state"] = ev.STAGE_FAILED
            step["failures"] = max(step["failures"], event["stage_failures"])
        elif step["state"] != ev.STAGE_DONE:
            step["state"] = ev.STAGE_RUNNING

        for closed in event["closed_stages"]:
            done = steps.get(closed["stage_instance_id"])
            if done is not None:
                done["state"] = closed["state"]
                done["elapsed"] = closed["ended_ms"] - closed["started_ms"]
                done["failures"] = max(done["failures"], closed["failures"])
    return [(s["stage"], k, s["state"], s["failures"])
            for k, s in sorted(steps.items(), key=lambda kv: kv[1]["opened"])]


def test_the_fold_shows_only_stages_that_started(emitter):
    """§33. Four events, four stage instances, and nothing else."""
    emit, store = emitter
    for stage in ("accepted", "understanding", "preparing", "publishing"):
        emit.append(ev.TOOL_STARTED, stage=stage, operation="x",
                    status=ev.STATUS_STARTED, public_message=stage)
    folded = _fold(rows(store))
    assert [s for s, _i, _st, _f in folded] == [
        "accepted", "understanding", "preparing", "publishing"]
    assert "executing" not in {s for s, _i, _st, _f in folded}, (
        "a stage that never ran must not appear")


def test_a_failed_pass_and_a_later_success_are_both_visible(emitter):
    """The audit trace is the point: a clean tick over a hidden failure is
    a trace that lies."""
    emit, store = emitter
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="1")
    emit.append(ev.TOOL_FAILED, stage="preparing", operation="bind",
                status=ev.STATUS_REJECTED, public_message="no")
    emit.append(ev.CONTEXT_READY, stage="understanding", operation="repair",
                status=ev.STATUS_OK, public_message="rethink")
    emit.append(ev.TOOL_STARTED, stage="preparing", operation="submit",
                status=ev.STATUS_STARTED, public_message="2")
    emit.append(ev.ANSWER_READY, stage="publishing", operation="publish",
                status=ev.STATUS_OK, public_message="done")

    folded = dict((instance, (state, failures))
                  for _stage, instance, state, failures in _fold(rows(store)))
    assert folded["preparing#1"] == (ev.STAGE_FAILED, 1)
    assert folded["preparing#2"][0] == ev.STAGE_DONE
    assert folded["publishing#1"][0] == ev.STAGE_DONE
