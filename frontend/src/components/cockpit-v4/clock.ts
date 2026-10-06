/**
 * Seconds that move while something is running.
 *
 * The defect this exists for
 * --------------------------
 * The panel showed each stage's elapsed time from the last EVENT that
 * mentioned it. Events arrive when the backend has something to say, which
 * during a thirty-second model call is: not at all. So a stage that had been
 * running for half a minute sat on screen reading
 *
 *     Understanding the request                 0.0s
 *
 * and the application looked frozen at exactly the moment it was working
 * hardest.
 *
 * The fix is not to invent progress. There is no percentage here and no
 * estimate of what is left, because nothing knows either. There is only
 * elapsed time, which is a fact, and the only reason it was not on screen is
 * that nobody was counting between events.
 *
 * How the time base works
 * -----------------------
 * The BACKEND's `elapsed_ms` is authoritative and is measured from the run's
 * own start. When an event is applied the view records it along with the
 * local clock reading at that instant -- the ANCHOR. Live elapsed is then
 *
 *     authoritative_at_anchor + (now - anchor)
 *
 * which is the server's number carried forward by the reader's own clock,
 * rather than a second clock started independently and allowed to drift.
 *
 * This is what makes a refresh correct for free. A reconnect replays the
 * event stream from the cursor, every replayed event carries its original
 * `elapsed_ms`, and the anchor is set when the last of them is applied. A run
 * that is eleven seconds old displays eleven seconds, not zero -- and not
 * twenty-two, which is what a second independent clock would produce if it
 * were started again on mount.
 *
 * When the run settles the authoritative total replaces the live figure
 * entirely. The estimate never outlives the fact.
 */

import type { RunView, Step } from "./reducer";

/** Reads the wall clock. Injected so a test can control time. */
export type Now = () => number;

export const systemNow: Now = () =>
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();

/**
 * How long the RUN has been going, live.
 *
 * Once the run is terminal this is the authoritative total and nothing is
 * added to it: a finished run does not keep ageing on screen.
 */
export function liveRunElapsedMs(view: RunView, now: number): number {
  if (view.terminal || view.anchorLocalMs <= 0) return view.elapsedMs;
  return view.elapsedMs + Math.max(0, now - view.anchorLocalMs);
}

/**
 * How long THIS step has been going, live.
 *
 * Only a running step counts forward. A step that is done carries the
 * duration the backend measured for it, and a step that has not started
 * carries nothing.
 */
export function liveStepElapsedMs(step: Step, view: RunView,
                                  now: number): number {
  if (step.state !== "running") return step.elapsedMs;
  if (view.terminal || view.anchorLocalMs <= 0) return step.elapsedMs;
  return step.elapsedMs + Math.max(0, now - view.anchorLocalMs);
}

/** True while anything is still counting. Nothing ticks once it is false. */
export function isCounting(view: RunView): boolean {
  return Boolean(view.runId) && !view.terminal;
}

/**
 * `12s`, `1m 04s`. Whole seconds, because a display that shows hundredths
 * flickers and a reader cannot read it anyway.
 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** The line at the top of the panel: what is happening, and for how long. */
export function runHeadline(view: RunView, now: number): string {
  const elapsed = formatElapsed(liveRunElapsedMs(view, now));
  if (!view.runId) return "";
  if (!view.terminal) {
    const running = view.steps.find((s) => s.state === "running");
    const what = running ? running.label : "Working";
    return `${what} · ${elapsed} elapsed`;
  }
  if (view.state === "COMPLETED") return `Answered in ${elapsed}`;
  if (view.state === "CANCELLED") return `Cancelled after ${elapsed}`;
  return `Stopped after ${elapsed}`;
}
