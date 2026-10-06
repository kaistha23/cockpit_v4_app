/**
 * FIXTURE (recorded live run) · UNIT.
 *
 * The process panel must reconstruct the same truthful trace whether the
 * events arrive live, are replayed from a cursor, or arrive all at once after
 * the run already finished.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { RunEvent, RunStatus } from "./client.ts";
import {
  LIVE_RUN_EVENTS,
  LIVE_RUN_ID,
  LIVE_RUN_STATUS,
} from "./live-run-fixture.ts";
import {
  collapsedSummary,
  failedStage,
  formatSeconds,
  hadAnyFailure,
  initial,
  reduce,
  type RunView,
} from "./reducer.ts";

function apply(events: RunEvent[], status?: RunStatus): RunView {
  let view = initial(LIVE_RUN_ID);
  for (const event of events) view = reduce(view, { type: "event", event });
  if (status) view = reduce(view, { type: "settled", status });
  return view;
}

function step(view: RunView, stage: string) {
  const found = view.steps.find((s) => s.stage === stage);
  assert.ok(found, `stage ${stage} is missing from the panel`);
  return found;
}

test("the recorded run reconstructs a truthful trace, not '0s / not started'", () => {
  const view = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);

  // The exact defect: both stages showed "not started" and the summary said
  // "Answered in 0s" while the backend had recorded 29.391 seconds.
  assert.notEqual(step(view, "accepted").state, "prospective");
  assert.notEqual(step(view, "understanding").state, "prospective");
  assert.equal(step(view, "accepted").state, "done",
    "run.accepted must mark Request accepted complete");
  assert.match(collapsedSummary(view), /Answered in 29/);
  assert.equal(view.elapsedIsAuthoritative, true);
});

test("every stage the backend emitted is present, and none that it did not", () => {
  const view = apply(LIVE_RUN_EVENTS);
  const emitted = new Set(LIVE_RUN_EVENTS.map((e) => e.stage));
  const rendered = new Set(view.steps.map((s) => s.stage));
  for (const stage of emitted) {
    assert.ok(rendered.has(stage), `${stage} was emitted and is not rendered`);
  }
  // The two laid out before any event are allowed; nothing else may be
  // invented.
  for (const stage of rendered) {
    assert.ok(
      emitted.has(stage) || ["accepted", "understanding"].includes(stage),
      `${stage} was rendered and the backend never emitted it`,
    );
  }
});

test("a failed attempt is not erased by the later success", () => {
  const view = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);
  const publishing = step(view, "publishing");

  assert.equal(publishing.failures, 1,
    "the run genuinely failed once at publishing");
  assert.equal(publishing.state, "done", "and then succeeded");
  assert.equal(hadAnyFailure(view), true);

  const failed = publishing.substeps.filter(
    (s) => s.status === "failed" || s.status === "rejected",
  );
  assert.equal(failed.length, 1);
  assert.equal(failed[0].seq, 8, "the failure keeps its place in the sequence");
  assert.match(failed[0].message, /did not pass its contract check/);
});

test("the second model attempt is visible as attempt 2", () => {
  const view = apply(LIVE_RUN_EVENTS);
  const understanding = step(view, "understanding");
  assert.equal(understanding.attempts, 2);
  const attempts = understanding.substeps
    .filter((s) => s.operation === "generate")
    .map((s) => s.attempt);
  assert.deepEqual(attempts, [1, 1, 2, 2]);
});

test("a model response closes its attempt with a duration", () => {
  const view = apply(LIVE_RUN_EVENTS);
  const understanding = step(view, "understanding");
  const received = understanding.substeps.find(
    (s) => s.eventType === "model.response_received" && s.attempt === 1,
  );
  assert.ok(received);
  // The first generation took ~16.2s from the request at 417ms.
  assert.ok(received.elapsedMs > 16_000 && received.elapsedMs < 16_700,
    `first attempt duration looks wrong: ${received.elapsedMs}`);
});

test("the real tool is named, not a generic label", () => {
  const view = apply(LIVE_RUN_EVENTS);
  const operations = step(view, "publishing").substeps.map((s) => s.operation);
  assert.ok(operations.includes("finalize_response"));
});

test("intent, answer validation and answer.ready are all reflected", () => {
  const view = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);
  const types = view.steps.flatMap((s) => s.substeps.map((x) => x.eventType));
  for (const required of [
    "intent.validated",
    "answer.validated",
    "answer.ready",
  ]) {
    assert.ok(types.includes(required), `${required} is missing from the trace`);
  }
  assert.equal(view.terminal, true);
  assert.equal(view.state, "COMPLETED");
});

test("replay from cursor 0 reconstructs exactly the live state", () => {
  const live = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);
  const replayed = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);
  assert.deepEqual(replayed.steps, live.steps);
  assert.equal(replayed.elapsedMs, live.elapsedMs);
  assert.equal(collapsedSummary(replayed), collapsedSummary(live));
});

test("a run that completed before the browser subscribed still reconstructs", () => {
  // The settled status arrives FIRST, then the replayed backlog.
  let view = initial(LIVE_RUN_ID);
  view = reduce(view, { type: "settled", status: LIVE_RUN_STATUS });
  for (const event of LIVE_RUN_EVENTS) {
    view = reduce(view, { type: "event", event });
  }
  assert.equal(step(view, "accepted").state, "done");
  assert.equal(step(view, "publishing").failures, 1);
  assert.match(collapsedSummary(view), /Answered in 29/);
});

test("a duplicate or replayed event does not double-count a substep", () => {
  const once = apply(LIVE_RUN_EVENTS);
  const twice = apply([...LIVE_RUN_EVENTS, ...LIVE_RUN_EVENTS]);
  assert.deepEqual(
    twice.steps.map((s) => s.substeps.length),
    once.steps.map((s) => s.substeps.length),
    "replaying the stream must not duplicate the trace",
  );
  assert.equal(twice.lastSeq, 15);
});

test("an out-of-order event older than the cursor is ignored", () => {
  let view = apply(LIVE_RUN_EVENTS);
  const stale = LIVE_RUN_EVENTS[3];
  const before = view.steps.map((s) => s.substeps.length);
  view = reduce(view, { type: "event", event: stale });
  assert.deepEqual(view.steps.map((s) => s.substeps.length), before);
});

test("a missing event does not fabricate the stage it would have created", () => {
  const withoutPublishing = LIVE_RUN_EVENTS.filter(
    (e) => e.stage !== "publishing",
  );
  const view = apply(withoutPublishing);
  assert.equal(
    view.steps.find((s) => s.stage === "publishing"),
    undefined,
    "a stage the backend did not emit must not appear",
  );
});

test("a very fast run still renders its stages and a real duration", () => {
  const fast: RunEvent[] = LIVE_RUN_EVENTS.slice(0, 3).map((e, i) => ({
    ...e,
    elapsed_ms: i * 40,
  }));
  const status: RunStatus = {
    ...LIVE_RUN_STATUS,
    budget: { elapsed_seconds: 0.42 },
  };
  const view = apply(fast, status);
  assert.equal(step(view, "accepted").state, "done");
  assert.match(collapsedSummary(view), /Answered in 0\.4s/,
    "a sub-second run must not read as 0s, which looks like nothing ran");
});

test("the panel opens on the stage that failed, even after it recovered", () => {
  const view = apply(LIVE_RUN_EVENTS, LIVE_RUN_STATUS);
  assert.equal(failedStage(view), "publishing");
});

test("durations are formatted so a short run is still legible", () => {
  assert.equal(formatSeconds(0), "0.0s");
  assert.equal(formatSeconds(416), "0.4s");
  assert.equal(formatSeconds(29_391), "29s");
});
