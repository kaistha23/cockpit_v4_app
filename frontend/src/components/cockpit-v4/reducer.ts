/**
 * Run-id and sequence fenced UI state.
 *
 * The fencing is the point. A late event from a run the user has moved on
 * from must not overwrite the active one, and a replayed event must not
 * duplicate a step already shown. Both are ordinary in practice: a reconnect
 * replays, and a user who asks a second question while the first is still
 * working has two streams alive for a moment.
 */

import type { FinalResponse, RunEvent, RunStatus } from "./client";

/**
 * What a stage instance is doing.
 *
 * `prospective` is gone. §33-§34: a step appears when it STARTS. The panel
 * used to lay out "Request accepted" and "Understanding the request" as
 * empty circles before either had happened, and a reader watching a run at
 * 0s saw two rows reading "not started" -- a guess about the future dressed
 * as progress, and the first thing on screen.
 */
export type StepState = "running" | "done" | "failed";

export type Substep = {
  seq: number;
  operation: string;
  message: string;
  status: string;
  elapsedMs: number;
  attempt: number;
  errorId: string;
  detailRef: string;
  eventType: string;
};

export type Step = {
  stage: string;
  /**
   * WHICH RUN of this stage. A run can re-enter `preparing` after a failed
   * submission, and two passes through it are two things that happened; the
   * server names each pass so the panel shows two rows in the order they ran
   * rather than one row whose state flickers.
   */
  instanceId: string;
  /** The sequence number that opened this instance. The panel orders by it,
   *  so a replayed or out-of-order frame cannot reorder the trace. */
  openedAtSeq: number;
  label: string;
  state: StepState;
  detail: string;
  startedAtMs: number;
  elapsedMs: number;
  errorId: string;
  /**
   * Failed attempts at this stage, which a later success does NOT erase.
   *
   * A run that finalized on its second attempt genuinely failed once, and a
   * panel that shows a clean tick over a hidden failure is an audit trace
   * that lies. `state` is the LATEST outcome; this is the count that keeps
   * the earlier one on screen.
   */
  failures: number;
  attempts: number;
  substeps: Substep[];
};

export type RunView = {
  runId: string;
  lastSeq: number;
  connection: "open" | "retrying" | "lost";
  steps: Step[];
  currentStage: string;
  /**
   * How long the run took, in milliseconds.
   *
   * While the run is live this is the highest `elapsed_ms` any event
   * reported. Once it settles, the authoritative run state replaces it:
   * the server's own clock is the fact, and an event stream that dropped
   * its last frame must not shorten the reported duration.
   */
  elapsedMs: number;
  elapsedIsAuthoritative: boolean;
  /**
   * The reader's own clock when `elapsedMs` was last set from an event.
   *
   * The anchor for live timing. Elapsed is the server's number carried
   * forward by this clock -- never a second clock started independently,
   * which would drift, and would restart at zero on a remount while the run
   * was already half a minute old.
   *
   * Zero until the first event: there is nothing to carry forward yet.
   */
  anchorLocalMs: number;
  terminal: boolean;
  state: string;
  errorCode: string;
  errorId: string;
  /** The provider/model this run was pinned to at acceptance. Empty while
   *  the run is live and for runs recorded before selection existed; those
   *  render as "unknown" rather than as a guessed model. */
  pinnedModel: string;
  response: FinalResponse | null;
};

export const STAGE_LABELS: Record<string, string> = {
  accepted: "Request accepted",
  understanding: "Understanding the request",
  product_knowledge: "Reading product knowledge",
  catalog: "Reading relevant data definitions",
  preparing: "Preparing query",
  validating: "Validating query",
  executing: "Executing query",
  reviewing: "Reviewing results",
  publishing: "Validating and publishing answer",
};

/**
 * The stages the panel lays out before anything has happened: NONE.
 *
 * §33, §34. The actual path is dynamic -- product help never reads the
 * catalogue and never executes -- and drawing an empty row for a stage that
 * may never run is a guess about the future dressed as progress. This used
 * to pre-render the two stages "every run reaches", which is how a reader
 * watching a live run at 0s was shown two rows saying "not started".
 *
 * A step appears when the server says it started, and not before.
 */
export const INITIAL_STAGES: readonly string[] = [];

export type Action =
  | { type: "start"; runId: string }
  | { type: "event"; event: RunEvent }
  | { type: "connection"; state: RunView["connection"] }
  | { type: "settled"; status: RunStatus };

/**
 * Reads the local clock. Replaceable so a test can drive time by hand
 * rather than by sleeping, which is how a one-second tick gets asserted in
 * milliseconds.
 */
let nowMs: () => number = () =>
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();

export function setClock(reader: () => number): () => void {
  const previous = nowMs;
  nowMs = reader;
  return () => {
    nowMs = previous;
  };
}

export function initial(runId = ""): RunView {
  return {
    runId,
    lastSeq: 0,
    connection: "open",
    steps: [],
    currentStage: "",
    elapsedMs: 0,
    elapsedIsAuthoritative: false,
    anchorLocalMs: 0,
    terminal: false,
    state: "ACCEPTED",
    errorCode: "",
    errorId: "",
    pinnedModel: "",
    response: null,
  };
}

const TERMINAL_EVENTS = new Set([
  "answer.ready",
  "run.failed",
  "run.cancelled",
  "run.expired",
  "run.interrupted",
]);

export function reduce(view: RunView, action: Action): RunView {
  switch (action.type) {
    case "start":
      return initial(action.runId);

    case "connection":
      return { ...view, connection: action.state };

    case "event": {
      const event = action.event;
      // Fencing: a different run, or an event already applied.
      if (event.run_id !== view.runId) return view;
      if (event.seq <= view.lastSeq) return view;

      let steps = view.steps.slice();

      // The instance this event belongs to. Keyed by the server's instance
      // id, so a second pass through a stage is a second row rather than a
      // row whose state flickers.
      //
      // `stated` is whether the SERVER ran the state machine for this
      // event. A stream that predates it closes nothing, so the branches
      // below fall back to the inference the old client used -- which is
      // wrong in the ways §35 describes and is still better than a panel
      // where every stage of an old stream spins forever.
      const stated = Boolean(event.stage_instance_id);
      const instanceId =
        event.stage_instance_id || `${event.stage}#legacy`;
      let index = steps.findIndex((s) => s.instanceId === instanceId);
      if (index < 0) {
        steps.push({
          stage: event.stage,
          instanceId,
          openedAtSeq: event.seq,
          label: STAGE_LABELS[event.stage] ?? event.stage,
          state: "running",
          detail: "",
          startedAtMs: event.stage_started_ms ?? event.elapsed_ms,
          elapsedMs: 0,
          errorId: "",
          failures: 0,
          attempts: 0,
          substeps: [],
        });
        index = steps.length - 1;
      }

      const step = { ...steps[index] };
      step.substeps = [
        ...step.substeps,
        {
          seq: event.seq,
          operation: event.operation,
          message: event.public_message,
          status: event.status,
          elapsedMs: event.elapsed_ms,
          attempt: event.attempt,
          errorId: event.error_id,
          detailRef: event.detail_ref,
          eventType: event.event_type,
        },
      ];
      step.detail = event.public_message;
      if (!step.startedAtMs) step.startedAtMs = event.elapsed_ms;
      step.elapsedMs = Math.max(0, event.elapsed_ms - step.startedAtMs);
      if (event.attempt > step.attempts) step.attempts = event.attempt;

      // A step is RUNNING until something closes it. Only a failure is
      // read off the event itself -- an "ok" substep means one operation
      // finished, not that the stage did, and treating it as the stage's
      // completion is how a run showed "Executing query ✓" while it was
      // still executing.
      if (event.status === "failed" || event.status === "rejected") {
        step.state = "failed";
        // Counted, not just displayed: a later close can set `state` back
        // to "done", and this is what keeps the failure on screen.
        step.failures = Math.max(step.failures + 1, event.stage_failures ?? 0);
        step.errorId = event.error_id || step.errorId;
      } else if (stated) {
        // A server that runs the state machine will close this stage when
        // the next one begins. Until then it is running, whatever its
        // individual operations reported.
        step.state = "running";
      } else {
        // A stream from a server that does not send the state machine. It
        // closes nothing, so the OLD inference is the only thing that can
        // close anything -- and a panel that left every stage of such a
        // stream spinning would be worse than the inference it replaced.
        step.state = "done";
      }
      steps[index] = step;

      if (!stated) {
        // The same legacy rule for the stages this one displaced: array
        // order, which is what the old client had and all an old stream
        // supports.
        for (let i = 0; i < index; i += 1) {
          if (steps[i].state === "running") {
            steps[i] = { ...steps[i], state: "done" };
          }
        }
      }

      // §35. Close whatever this event closed, exactly as the SERVER says.
      // The panel used to infer it -- "any earlier stage still marked
      // running has been left behind" -- which is a guess that holds only
      // while stages run in array order and never re-enter. Two passes
      // through `preparing` broke it in both directions: the second pass
      // reopened the first pass's row, and a stage that ran out of order
      // stayed spinning.
      //
      // AFTER the step above, not before: a terminal event closes its own
      // instance as well as the one it displaced, and closing first would
      // apply that to a row that does not exist yet -- leaving the last
      // stage of every run spinning forever.
      for (const closed of event.closed_stages ?? []) {
        const at = steps.findIndex(
          (s) => s.instanceId === closed.stage_instance_id,
        );
        if (at < 0) continue;
        steps[at] = {
          ...steps[at],
          state: closed.state === "failed" ? "failed" : "done",
          elapsedMs: Math.max(0, closed.ended_ms - closed.started_ms),
          failures: Math.max(steps[at].failures, closed.failures ?? 0),
        };
      }

      // The panel shows the order things HAPPENED, which is the order the
      // server committed them in. Sorting by the opening sequence means a
      // replayed or late frame cannot reorder the trace.
      steps = steps.slice().sort((a, b) => a.openedAtSeq - b.openedAtSeq);

      return {
        ...view,
        lastSeq: event.seq,
        steps,
        currentStage: event.stage,
        elapsedMs: Math.max(view.elapsedMs, event.elapsed_ms),
        anchorLocalMs: nowMs(),
        errorId: event.error_id || view.errorId,
        terminal: view.terminal || TERMINAL_EVENTS.has(event.event_type),
      };
    }

    case "settled": {
      if (action.status.run_id !== view.runId) return view;
      // A stage still marked running when the run settled did finish; the
      // event that said so may simply not have reached this browser.
      const steps = view.steps.map((step) =>
        step.state === "running" ? { ...step, state: "done" as StepState } : step,
      );
      const authoritative = authoritativeElapsedMs(action.status);
      return {
        ...view,
        steps,
        terminal: true,
        state: action.status.state,
        errorCode: action.status.error_code,
        errorId: action.status.error_id || view.errorId,
        response: action.status.final_response,
        pinnedModel: action.status.model
          ? `${action.status.provider ?? ""}:${action.status.model}` : "",
        elapsedMs: authoritative ?? view.elapsedMs,
        elapsedIsAuthoritative: authoritative !== null,
      };
    }

    default:
      return view;
  }
}

/**
 * The run's own elapsed time, from the server, in milliseconds.
 *
 * `budget.elapsed_seconds` is what the run ledger recorded. Preferring it
 * over the last event the browser happened to receive is the difference
 * between reporting 29.4s and reporting 0s.
 */
export function authoritativeElapsedMs(status: RunStatus): number | null {
  const budget = status.budget as { elapsed_seconds?: unknown } | undefined;
  const seconds = budget?.elapsed_seconds;
  if (typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0) {
    return Math.round(seconds * 1000);
  }
  return null;
}

/** The one-line collapsed summary. Says what is happening, or what stopped. */
export function collapsedSummary(view: RunView): string {
  const seconds = formatSeconds(view.elapsedMs);
  if (view.terminal) {
    if (view.state === "COMPLETED") return `Answered in ${seconds}`;
    if (view.state === "PARTIAL") return `Partly answered in ${seconds}`;
    if (view.state === "WAITING_FOR_USER") return "Waiting for your answer";
    if (view.state === "REFERRED") return "Referred to another area";
    if (view.state === "CANCELLED") return "Cancelled";
    return `Stopped: ${view.errorCode || view.state}`;
  }
  const running = view.steps.find((s) => s.state === "running");
  const label = running?.label ?? STAGE_LABELS[view.currentStage] ?? "Working";
  return `${label} · ${seconds} elapsed`;
}

/**
 * A duration a reader can act on.
 *
 * Sub-second runs keep one decimal, because "0s" for a run that took 400ms
 * reads as "nothing happened" — which is how a false trace hides.
 */
export function formatSeconds(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

/** The step a failure should auto-expand to. */
export function failedStage(view: RunView): string {
  return (
    view.steps.find((s) => s.state === "failed")?.stage ??
    // A stage that failed and then succeeded still deserves the reader's
    // attention when the panel opens.
    view.steps.find((s) => s.failures > 0)?.stage ??
    ""
  );
}

/** Did any stage fail at any point, even if a later attempt succeeded? */
export function hadAnyFailure(view: RunView): boolean {
  return view.steps.some((step) => step.failures > 0);
}
