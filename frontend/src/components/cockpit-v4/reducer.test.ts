/**
 * Unit tests for the UI reducer. No network, no backend.
 *
 * These cover the fencing rules, which are the ones that go wrong in
 * practice: a replayed event after a reconnect, and a late event from a run
 * the user has already moved on from.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { RunEvent, RunStatus } from "./client.ts";
import { collapsedSummary, failedStage, initial, reduce } from "./reducer.ts";

function event(over: Partial<RunEvent> = {}): RunEvent {
  return {
    schema_version: "v4.1",
    event_id: "ev-1",
    run_id: "run-a",
    seq: 1,
    event_type: "model.requested",
    stage: "understanding",
    operation: "generate",
    status: "started",
    occurred_at: "2026-09-10T00:00:00.000Z",
    elapsed_ms: 100,
    attempt: 1,
    submission: 0,
    round: 0,
    public_message: "Understanding the request",
    detail_ref: "",
    error_id: "",
    trace_id: "tr-1",
    span_id: "sp-1",
    parent_span_id: "",
    stage_instance_id: `${over.stage ?? "understanding"}#1`,
    stage_started_ms: over.elapsed_ms ?? 100,
    stage_state: "running",
    stage_failures: 0,
    closed_stages: [],
    ...over,
  };
}

test("a run that has done nothing shows no steps at all", () => {
  // §33, §34. The panel used to lay out "Request accepted" and
  // "Understanding the request" as empty circles, so a reader watching a
  // live run at 0s was shown two rows reading "not started" -- a guess
  // about the future, dressed as progress, as the first thing on screen.
  const view = initial("run-a");
  assert.deepEqual(view.steps, []);
});

test("a step appears when it starts, and only then", () => {
  let view = initial("run-a");
  assert.equal(view.steps.length, 0);
  view = reduce(view, { type: "event", event: event() });
  assert.equal(view.steps.length, 1);
  assert.equal(view.steps[0].stage, "understanding");
  assert.equal(view.steps[0].state, "running");
  assert.ok(!view.steps.some((s) => s.stage === "executing"),
    "a stage that may never run is not drawn before it happens");
});

test("an event from another run is ignored", () => {
  const view = reduce(initial("run-a"), {
    type: "event",
    event: event({ run_id: "run-b", seq: 5 }),
  });
  assert.equal(view.lastSeq, 0);
});

test("a replayed event does not duplicate a step", () => {
  let view = reduce(initial("run-a"), { type: "event", event: event() });
  const before = view.steps.find((s) => s.stage === "understanding");
  view = reduce(view, { type: "event", event: event() });
  const after = view.steps.find((s) => s.stage === "understanding");
  assert.equal(after?.substeps.length, before?.substeps.length);
  assert.equal(view.lastSeq, 1);
});

test("a failed event marks the step and exposes its error id", () => {
  let view = initial("run-a");
  view = reduce(view, { type: "event", event: event() });
  view = reduce(view, {
    type: "event",
    event: event({
      seq: 2,
      stage: "executing",
      status: "failed",
      event_type: "tool.failed",
      error_id: "err-abc123456789",
      public_message: "Executing query failed at the bind check.",
    }),
  });
  assert.equal(failedStage(view), "executing");
  assert.equal(view.errorId, "err-abc123456789");
});

test("a settled status closes any step still marked running", () => {
  let view = reduce(initial("run-a"), { type: "event", event: event() });
  const status = {
    run_id: "run-a",
    state: "COMPLETED",
    error_code: "",
    error_id: "",
    final_response: null,
  } as unknown as RunStatus;
  view = reduce(view, { type: "settled", status });
  assert.ok(!view.steps.some((s) => s.state === "running"));
  assert.equal(view.terminal, true);
});

test("a stop is summarised as a stop, not as an answer", () => {
  let view = initial("run-a");
  view = reduce(view, {
    type: "settled",
    status: {
      run_id: "run-a",
      state: "FAILED",
      error_code: "COST_LIMIT",
      error_id: "",
      final_response: null,
    } as unknown as RunStatus,
  });
  assert.match(collapsedSummary(view), /Stopped: COST_LIMIT/);
});

test("V4-AT-082: connection loss is a client state, not a server failure", () => {
  let view = reduce(initial("run-a"), { type: "event", event: event() });
  view = reduce(view, { type: "connection", state: "lost" });
  assert.equal(view.connection, "lost");
  assert.equal(view.terminal, false,
    "losing the stream must not mark the run finished");
  assert.equal(view.state, "ACCEPTED",
    "the client does not know the server failed, and must not say it did");
});

test("V4-AT-068: the failed step is identifiable for auto-expansion", () => {
  let view = reduce(initial("run-a"), { type: "event", event: event() });
  assert.equal(failedStage(view), "", "nothing has failed yet");
  view = reduce(view, {
    type: "event",
    event: event({
      seq: 2,
      stage: "executing",
      status: "failed",
      event_type: "tool.failed",
      public_message: "Executing query failed at the bind check.",
    }),
  });
  assert.equal(failedStage(view), "executing");
  const step = view.steps.find((s) => s.stage === "executing");
  assert.equal(step?.state, "failed");
  assert.match(step?.detail ?? "", /bind check/,
    "the panel can show WHICH substep failed, not just that one did");
});

test("V4-AT-069: substeps keep their operation and detail reference", () => {
  const view = reduce(initial("run-a"), {
    type: "event",
    event: event({
      status: "failed",
      event_type: "model.response_received",
      operation: "generate",
      detail_ref: "dt-abc123",
      error_id: "err-abc123456789",
    }),
  });
  const sub = view.steps.find((s) => s.stage === "understanding")?.substeps[0];
  assert.equal(sub?.operation, "generate");
  assert.equal(sub?.detailRef, "dt-abc123");
  assert.equal(sub?.errorId, "err-abc123456789");
});


// ---- §33-§37: the stage state machine comes from the server -------------

function closed(instance: string, over: Record<string, unknown> = {}) {
  return {
    stage: instance.split("#")[0],
    stage_instance_id: instance,
    started_ms: 0,
    ended_ms: 100,
    failures: 0,
    state: "done" as const,
    ...over,
  };
}

test("a step closes when the server says it closed, not by array order", () => {
  // The old rule was "any EARLIER stage still marked running has been left
  // behind", which holds only while stages run in array order. A run that
  // re-enters `preparing` after a failed submission breaks it in both
  // directions: the second pass reopens the first pass's row, and a stage
  // that ran out of order stays spinning.
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "preparing",
                   stage_instance_id: "preparing#1" }),
  });
  assert.equal(view.steps[0].state, "running");

  view = reduce(view, {
    type: "event",
    event: event({
      seq: 2, stage: "understanding", stage_instance_id: "understanding#1",
      closed_stages: [closed("preparing#1")],
    }),
  });
  const first = view.steps.find((s) => s.instanceId === "preparing#1");
  assert.equal(first?.state, "done");
  assert.equal(first?.elapsedMs, 100);
});

test("two passes through one stage are two rows, in the order they ran", () => {
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "preparing",
                   stage_instance_id: "preparing#1" }),
  });
  view = reduce(view, {
    type: "event",
    event: event({
      seq: 2, stage: "preparing", stage_instance_id: "preparing#1",
      status: "rejected", event_type: "tool.failed", stage_state: "failed",
      stage_failures: 1,
    }),
  });
  view = reduce(view, {
    type: "event",
    event: event({
      seq: 3, stage: "preparing", stage_instance_id: "preparing#2",
      closed_stages: [closed("preparing#1", { state: "failed", failures: 1 })],
    }),
  });

  assert.deepEqual(view.steps.map((s) => s.instanceId),
    ["preparing#1", "preparing#2"]);
  assert.equal(view.steps[0].state, "failed");
  assert.equal(view.steps[0].failures, 1);
  assert.equal(view.steps[1].state, "running");
});

test("a terminal event leaves no step running", () => {
  // The last frame is the only place that can say so, and a browser that
  // reconnects after it must not be shown a stage spinning forever.
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "accepted",
                   stage_instance_id: "accepted#1" }),
  });
  view = reduce(view, {
    type: "event",
    event: event({
      seq: 2, stage: "publishing", stage_instance_id: "publishing#1",
      event_type: "answer.ready", status: "ok",
      closed_stages: [closed("accepted#1"), closed("publishing#1")],
    }),
  });
  assert.ok(view.steps.every((s) => s.state !== "running"),
    JSON.stringify(view.steps.map((s) => [s.instanceId, s.state])));
  assert.ok(view.terminal);
});

test("replaying the whole stream lands where the live stream did", () => {
  const stream = [
    event({ seq: 1, stage: "accepted", stage_instance_id: "accepted#1" }),
    event({ seq: 2, stage: "preparing", stage_instance_id: "preparing#1",
            closed_stages: [closed("accepted#1")] }),
    event({ seq: 3, stage: "publishing", stage_instance_id: "publishing#1",
            event_type: "answer.ready", status: "ok",
            closed_stages: [closed("preparing#1"), closed("publishing#1")] }),
  ];
  const fold = (frames: RunEvent[]) =>
    frames.reduce(
      (view, frame) => reduce(view, { type: "event", event: frame }),
      initial("run-a"),
    );

  const once = fold(stream);
  const twice = fold([...stream, ...stream]);
  assert.deepEqual(
    twice.steps.map((s) => [s.instanceId, s.state, s.substeps.length]),
    once.steps.map((s) => [s.instanceId, s.state, s.substeps.length]),
    "a replayed frame changed the panel",
  );
});

test("an out-of-order frame cannot reorder the trace", () => {
  // Fencing drops the stale frame outright; the ordering rule is what keeps
  // the rows in the order the server committed them either way.
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 5, stage: "publishing",
                   stage_instance_id: "publishing#1" }),
  });
  view = reduce(view, {
    type: "event",
    event: event({ seq: 2, stage: "preparing",
                   stage_instance_id: "preparing#1" }),
  });
  assert.deepEqual(view.steps.map((s) => s.instanceId), ["publishing#1"]);
  assert.equal(view.lastSeq, 5);
});

test("a stream from an older server still closes its stages", () => {
  // Such a stream carries no `closed_stages`, so nothing in it can close
  // anything. The reducer falls back to the inference the old client used
  // -- which is wrong in the ways §35 describes, and is still better than a
  // panel where every stage of an old stream spins forever.
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "accepted", status: "ok",
                   stage_instance_id: undefined, stage_state: undefined,
                   closed_stages: undefined }),
  });
  view = reduce(view, {
    type: "event",
    event: event({ seq: 2, stage: "publishing", status: "ok",
                   event_type: "answer.ready",
                   stage_instance_id: undefined, stage_state: undefined,
                   closed_stages: undefined }),
  });
  assert.deepEqual(view.steps.map((s) => s.stage),
    ["accepted", "publishing"]);
  assert.ok(view.steps.every((s) => s.state === "done"),
    JSON.stringify(view.steps.map((s) => [s.stage, s.state])));
});

test("a stated stage is not closed by one of its own operations", () => {
  // The other half of the same rule: where the server DOES run the state
  // machine, an "ok" substep means one operation finished, not that the
  // stage did. Treating it as the stage's completion is how a run showed
  // "Executing query ✓" while it was still executing.
  let view = initial("run-a");
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "executing", status: "started",
                   stage_instance_id: "executing#1" }),
  });
  view = reduce(view, {
    type: "event",
    event: event({ seq: 2, stage: "executing", status: "ok",
                   stage_instance_id: "executing#1" }),
  });
  assert.equal(view.steps[0].state, "running");
});

test("a run that computed rows carries them into the view, narrative or not", () => {
  // Fault 4. The answer turn truncated twice and the run stopped as
  // ANSWER_FORMAT_EXHAUSTED -- but four steps had been validated and three
  // had stored their results. The panel branched on `!view.response` alone,
  // so the reader got a red box and none of the rows they had paid for.
  //
  // The server now publishes the stored result on its own channel. The
  // reducer's job is to carry it: a settled status with a final_response
  // must produce a view with that response, whatever the error code says.
  let view = initial("run-a");
  view = reduce(view, {
    type: "settled",
    status: {
      run_id: "run-a",
      state: "PARTIAL",
      error_code: "ANSWER_FORMAT_EXHAUSTED",
      error_id: "",
      final_response: {
        disposition: "partial_answer",
        narrative: "The analysis ran and its result is below.",
        numeric_claims: [],
        tables: [{ artifact_id: "art-1", title: "ECL by sector", rows: [] }],
        executed: true,
        result_only: true,
        result_only_reason: "The written answer was cut off.",
      },
    } as unknown as RunStatus,
  });
  assert.equal(view.terminal, true);
  assert.notEqual(view.response, null);
  assert.equal(view.response?.result_only, true);
  assert.equal(view.response?.executed, true);
  // No number reaches the reader that the model supplied.
  assert.deepEqual(view.response?.numeric_claims, []);
  // And the run is summarised as what it was, not as a bare stop.
  assert.match(collapsedSummary(view), /Partly answered/);
});
