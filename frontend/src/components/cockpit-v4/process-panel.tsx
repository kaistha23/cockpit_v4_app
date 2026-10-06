"use client";

/**
 * The live process panel: Show process / Hide process.
 *
 * What it renders is what the backend PERSISTED. There is no timer that
 * advances a step because time passed, and no stage drawn as complete
 * because a later one started. A future step is drawn as plainly
 * prospective -- an empty circle and muted text -- because a checkmark on
 * something that has not happened is a lie the user cannot detect.
 *
 * Accessibility: the collapsed line is a real button with aria-expanded; the
 * live region announces the STAGE, politely, not every event, so a screen
 * reader is not re-read a queue of substeps as they stream.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  formatElapsed,
  isCounting,
  liveStepElapsedMs,
  runHeadline,
  systemNow,
} from "./clock";
import type { RunView, Step } from "./reducer";
import { collapsedSummary, failedStage, formatSeconds } from "./reducer";

const PREFERENCE_KEY = "cockpit-v4:process-panel-open";

function readPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PREFERENCE_KEY) === "open";
  } catch {
    return false;
  }
}

function writePreference(open: boolean): void {
  try {
    window.localStorage.setItem(PREFERENCE_KEY, open ? "open" : "closed");
  } catch {
    /* a browser that refuses storage still gets a working panel */
  }
}

function Marker({ state, failures }: { state: Step["state"]; failures: number }) {
  // A stage that succeeded on a retry is neither a clean tick nor a failure.
  // Saying so is the whole point of an audit trace.
  const recovered = state === "done" && failures > 0;
  const glyph =
    state === "failed"
      ? "✕"
      : recovered
        ? "⟳"
        : state === "done"
          ? "✓"
          : state === "running"
            ? "●"
            : "○";
  const tone =
    state === "failed"
      ? "text-negative"
      : recovered
        ? "text-warning"
        : state === "done"
          ? "text-positive"
          : state === "running"
            ? "text-accent"
            : "text-text-muted";
  return (
    <span aria-hidden className={`w-4 shrink-0 text-center ${tone}`}>
      {glyph}
    </span>
  );
}

function stateWord(state: Step["state"]): string {
  // Every step on screen has started. §33: there is no "not started" row,
  // because a row is drawn when the server says the stage began.
  return state;
}

export function ProcessPanel({
  view,
  operatorView = false,
  onCancel,
  onRetryStatus,
}: {
  view: RunView;
  operatorView?: boolean;
  onCancel?: () => void;
  onRetryStatus?: () => void;
}) {
  const [open, setOpen] = useState<boolean>(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const autoExpanded = useRef("");

  useEffect(() => {
    setOpen(readPreference());
  }, []);

  // A failure auto-expands its own step, once, without stealing the user's
  // choice to close the panel again afterwards.
  const failed = failedStage(view);
  useEffect(() => {
    if (failed && autoExpanded.current !== failed) {
      autoExpanded.current = failed;
      setOpen(true);
      setExpanded((prev) => ({ ...prev, [failed]: true }));
    }
  }, [failed]);

  // ONE interval for the whole panel, started only while something is
  // running and cleared the moment it is not. A timer per stage would be a
  // dozen intervals on a busy run and a leak on every one that unmounts
  // mid-flight.
  const [now, setNow] = useState(() => systemNow());
  const counting = isCounting(view);
  useEffect(() => {
    if (!counting) return undefined;
    setNow(systemNow());
    const handle = setInterval(() => setNow(systemNow()), 1000);
    return () => clearInterval(handle);
  }, [counting]);

  // While the run is live this is the moving figure; once it settles
  // `runHeadline` reports the server's authoritative total and stops.
  const summary = counting
    ? runHeadline(view, now)
    : collapsedSummary(view);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    writePreference(next);
  };

  return (
    <section
      data-testid="v4-process-panel"
      data-open={open ? "true" : "false"}
      className="rounded-lg border border-border bg-surface-sunken/60 text-sm"
      aria-label="Process"
    >
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <p
          data-testid="v4-process-summary"
          className="min-w-0 truncate text-text-secondary"
          aria-live="polite"
        >
          {summary}
          {operatorView && view.terminal ? (
            <span data-testid="v4-pinned-model" className="ml-2 text-xs text-text-muted">
              {view.pinnedModel || "model: unknown"}
            </span>
          ) : null}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          {!view.terminal && onCancel ? (
            <button
              type="button"
              data-testid="v4-stop"
              onClick={onCancel}
              className="rounded border border-border-strong px-2 py-1 text-xs text-text-secondary hover:bg-surface"
            >
              Stop
            </button>
          ) : null}
          <button
            type="button"
            data-testid="v4-toggle-process"
            onClick={toggle}
            aria-expanded={open}
            aria-controls="cockpit-v4-process-detail"
            className="rounded px-2 py-1 text-xs font-medium text-accent hover:bg-surface"
          >
            {open ? "Hide process ▴" : "Show process ▾"}
          </button>
        </div>
      </div>

      {view.connection === "lost" ? (
        <p className="border-t border-warning bg-warning-muted px-3 py-2 text-xs text-warning">
          CONNECTION_LOST — this browser stopped receiving updates. The last
          stage it saw was{" "}
          <strong>{view.steps.find((s) => s.state === "running")?.label ?? "—"}</strong>
          . This says nothing about whether the request itself failed.{" "}
          {onRetryStatus ? (
            <button
              type="button"
              onClick={onRetryStatus}
              className="underline underline-offset-2"
            >
              Check its status
            </button>
          ) : null}
        </p>
      ) : null}

      {open ? (
        <div
          id="cockpit-v4-process-detail"
          className="border-t border-border px-3 py-2"
        >
          <ol className="space-y-1" data-testid="v4-process-steps">
            {view.steps.map((step) => {
              const isOpen = Boolean(expanded[step.instanceId]);
              const hasDetail = step.substeps.length > 0;
              return (
                <li key={step.instanceId}>
                  <div className="flex items-start gap-2">
                    <Marker state={step.state} failures={step.failures} />
                    <div className="min-w-0 flex-1">
                      <button
                        type="button"
                        disabled={!hasDetail}
                        onClick={() =>
                          setExpanded((prev) => ({
                            ...prev,
                            [step.instanceId]: !prev[step.instanceId],
                          }))
                        }
                        aria-expanded={isOpen}
                        className={`flex w-full items-baseline justify-between gap-3 text-left text-text-primary ${
                          hasDetail ? "hover:underline" : "cursor-default"
                        }`}
                      >
                        <span className="truncate">
                          {step.label}
                          <span className="sr-only"> — {stateWord(step.state)}</span>
                        </span>
                        <span
                          data-testid={`v4-step-elapsed-${step.stage}`}
                          data-running={step.state === "running"
                            ? "true" : "false"}
                          className="shrink-0 tabular-nums text-xs text-text-muted"
                        >
                          {step.state === "running"
                            ? formatElapsed(liveStepElapsedMs(step, view, now))
                            : formatSeconds(step.elapsedMs)}
                        </span>
                      </button>
                      {step.detail ? (
                        <p className="truncate text-xs text-text-muted">
                          {step.detail}
                        </p>
                      ) : null}
                      {step.failures > 0 ? (
                        <p
                          data-testid={`v4-step-failures-${step.stage}`}
                          className="text-xs text-warning"
                        >
                          {step.failures === 1
                            ? "1 attempt failed here"
                            : `${step.failures} attempts failed here`}
                          {step.state === "done"
                            ? " before this stage completed"
                            : ""}
                        </p>
                      ) : null}

                      {isOpen ? (
                        <ul className="mt-1 space-y-1 border-l border-border pl-3">
                          {step.substeps.map((sub) => (
                            <li
                              key={sub.seq}
                              data-testid="v4-substep"
                              data-status={sub.status}
                              className="flex items-baseline gap-2 text-xs"
                            >
                              <span className="shrink-0 tabular-nums text-text-muted">
                                {formatSeconds(sub.elapsedMs)}
                              </span>
                              <span
                                className={
                                  sub.status === "failed" || sub.status === "rejected"
                                    ? "text-negative"
                                    : "text-text-secondary"
                                }
                              >
                                {sub.message}
                                {sub.attempt > 1 ? (
                                  <span className="ml-1 text-text-muted">
                                    (attempt {sub.attempt})
                                  </span>
                                ) : null}
                              </span>
                              {operatorView && sub.operation ? (
                                <span className="ml-auto shrink-0 text-text-muted">
                                  {sub.eventType} · {sub.operation}
                                  {sub.detailRef ? ` · ${sub.detailRef}` : ""}
                                </span>
                              ) : null}
                              {sub.errorId ? (
                                <span className="ml-2 font-mono text-negative">
                                  {sub.errorId}
                                </span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>

          <p className="mt-2 border-t border-border pt-2 text-xs text-text-muted">
            Memory maintenance is separate and never holds this answer open.
          </p>
          {view.errorId ? (
            <p className="mt-1 text-xs text-text-secondary">
              Support reference <span className="font-mono">{view.errorId}</span>
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
