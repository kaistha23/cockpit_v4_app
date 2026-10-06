import assert from "node:assert/strict";
import test from "node:test";

import {
  formatElapsed,
  isCounting,
  liveRunElapsedMs,
  liveStepElapsedMs,
  runHeadline,
} from "./clock.ts";
import type { RunEvent } from "./client.ts";
import { initial, reduce, setClock } from "./reducer.ts";

/**
 * Seconds that move while something is running.
 *
 * A stage that had been working for half a minute used to sit on screen at
 * "0.0s", because the panel showed the elapsed time from the last EVENT and
 * events do not arrive during a model call. The application looked frozen at
 * exactly the moment it was working hardest.
 *
 * Time is driven by hand here. Asserting a one-second tick by sleeping for a
 * second makes a suite slower and no more certain.
 */

function event(over: Partial<RunEvent> = {}): RunEvent {
  const stage = over.stage ?? "understanding";
  return {
    run_id: "run-1",
    seq: 1,
    event_type: "model.requested",
    stage,
    operation: "generate",
    status: "started",
    public_message: "Understanding the request",
    elapsed_ms: 0,
    attempt: 1,
    error_id: "",
    detail_ref: "",
    // The stage state machine the server sends. A fixture without it makes
    // the panel look as though a stage never closes, which is a property of
    // the fixture rather than of anything under test.
    stage_instance_id: `${stage}#1`,
    stage_started_ms: over.elapsed_ms ?? 0,
    stage_state: "running",
    stage_failures: 0,
    closed_stages: [],
    ...over,
  } as RunEvent;
}

/** What the server sends when one stage displaces another. */
function closed(instance: string, started: number, ended: number) {
  return {
    stage: instance.split("#")[0],
    stage_instance_id: instance,
    started_ms: started,
    ended_ms: ended,
    failures: 0,
    state: "done" as const,
  };
}

function at(ms: number) {
  return setClock(() => ms);
}

test("a running stage counts forward between events", () => {
  const restore = at(1_000);
  let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
  view = reduce(view, { type: "event", event: event({ elapsed_ms: 2_000 }) });
  restore();

  // The backend said two seconds. Ten seconds of reader-clock have passed
  // since, with no further event.
  assert.equal(liveRunElapsedMs(view, 1_000), 2_000);
  assert.equal(liveRunElapsedMs(view, 11_000), 12_000);
  assert.equal(formatElapsed(liveRunElapsedMs(view, 11_000)), "12s");
});

test("the step that is running is the one that counts", () => {
  const restore = at(1_000);
  let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
  view = reduce(view, {
    type: "event",
    event: event({ seq: 1, stage: "accepted", status: "ok",
                   event_type: "run.started", elapsed_ms: 0 }),
  });
  view = reduce(view, {
    type: "event",
    event: event({ seq: 2, elapsed_ms: 500, status: "started",
                   closed_stages: [closed("accepted#1", 0, 500)] }),
  });
  restore();

  const understanding = view.steps.find((s) => s.stage === "understanding")!;
  const accepted = view.steps.find((s) => s.stage === "accepted")!;
  assert.equal(accepted.state, "done");
  assert.equal(understanding.state, "running");

  assert.equal(liveStepElapsedMs(understanding, view, 9_000), 8_000);
  assert.equal(liveStepElapsedMs(accepted, view, 9_000), accepted.elapsedMs,
    "a finished stage carries the duration the backend measured for it");
});

test("a settled run stops counting and reports the authoritative total", () => {
  const restore = at(1_000);
  let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
  view = reduce(view, { type: "event", event: event({ elapsed_ms: 2_000 }) });
  restore();

  assert.equal(isCounting(view), true);
  view = reduce(view, {
    type: "settled",
    status: {
      run_id: "run-1", state: "COMPLETED", error_code: "", error_id: "",
      message: "",
      // The server's own clock, where the run status actually carries it.
      budget: { elapsed_seconds: 31 },
      final_response: null,
    } as never,
  });

  assert.equal(isCounting(view), false);
  assert.equal(liveRunElapsedMs(view, 900_000), 31_000,
    "the estimate must never outlive the fact");
  assert.equal(runHeadline(view, 900_000), "Answered in 31s");
});

test("a refresh at eleven seconds displays eleven, not zero and not twenty-two",
  () => {
    // The reconnect replays the stream; every replayed event carries its
    // original elapsed_ms, and the anchor is set as the last one is applied.
    const restore = at(50_000);
    let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
    view = reduce(view, { type: "event", event: event({ seq: 1, elapsed_ms: 0 }) });
    view = reduce(view, {
      type: "event",
      event: event({ seq: 2, elapsed_ms: 11_000 }),
    });
    restore();

    assert.equal(formatElapsed(liveRunElapsedMs(view, 50_000)), "11s");
    assert.equal(formatElapsed(liveRunElapsedMs(view, 53_000)), "14s");
  });

test("a replayed event does not advance the clock twice", () => {
  const restore = at(1_000);
  let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
  view = reduce(view, { type: "event", event: event({ seq: 2, elapsed_ms: 5_000 }) });
  const before = view.elapsedMs;
  // seq 2 again, which is what a reconnect with a stale cursor delivers.
  view = reduce(view, { type: "event", event: event({ seq: 2, elapsed_ms: 5_000 }) });
  restore();
  assert.equal(view.elapsedMs, before);
});

test("the headline names what is running and how long it has been", () => {
  const restore = at(1_000);
  let view = reduce(initial("run-1"), { type: "start", runId: "run-1" });
  view = reduce(view, { type: "event", event: event({ elapsed_ms: 3_000 }) });
  restore();
  assert.equal(runHeadline(view, 16_000),
    "Understanding the request · 18s elapsed");
});

test("elapsed is whole seconds, and minutes past sixty", () => {
  assert.equal(formatElapsed(0), "0s");
  assert.equal(formatElapsed(999), "0s");
  assert.equal(formatElapsed(1_000), "1s");
  assert.equal(formatElapsed(59_999), "59s");
  assert.equal(formatElapsed(60_000), "1m 00s");
  assert.equal(formatElapsed(125_000), "2m 05s");
  assert.equal(formatElapsed(-5), "0s", "a clock that ran backwards is zero");
});

test("nothing counts before the first event", () => {
  const view = initial("run-1");
  assert.equal(view.anchorLocalMs, 0);
  assert.equal(liveRunElapsedMs(view, 900_000), 0,
    "there is nothing to carry forward yet");
});
