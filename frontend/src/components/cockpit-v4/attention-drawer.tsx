"use client";

/**
 * The right-side detail card for one attention item.
 *
 * It renders the eight things a senior credit officer needs to decide whether
 * to look further: what the issue is, why it appeared, what changed, the
 * numbers, what moved alongside it, what to review next, the trace, and a way
 * to start investigating.
 *
 * On "possible drivers": every line says "coincides with" or "associated
 * with", because that is what the data supports. Two aggregates moving in the
 * same quarter is an association. The drawer does not upgrade one into a
 * cause, and neither does the server that produced it.
 */

import * as React from "react";

import { investigate, type AttentionItem } from "./client";
import {
  comparisonPeriod, periodLabel, periodNoun, reportingPeriod,
} from "./period";

export function AttentionDrawer({
  item,
  onClose,
  onInvestigate,
  operatorView = false,
}: {
  item: AttentionItem | null;
  onClose: () => void;
  onInvestigate: (thread: {
    threadId: string;
    item: AttentionItem;
    suggested: string[];
  }) => void;
  operatorView?: boolean;
}) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [showTrace, setShowTrace] = React.useState(false);

  React.useEffect(() => {
    setError("");
    setBusy(false);
    setShowTrace(false);
  }, [item?.item_id]);

  React.useEffect(() => {
    if (!item) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  if (!item) return null;

  const drill = item.drilldown;

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const opened = await investigate(item.item_id);
      onInvestigate({
        threadId: opened.thread_id,
        item,
        suggested: opened.suggested_questions,
      });
    } catch {
      setBusy(false);
      setError("The investigation could not be opened. Nothing was started.");
    }
  };

  return (
    <aside
      data-testid="attention-drawer"
      data-item-id={item.item_id}
      aria-label={item.headline}
      className="fixed right-0 top-0 z-40 flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-border bg-surface shadow-xl"
    >
      <header className="sticky top-0 flex items-start justify-between gap-3 border-b border-border bg-surface px-5 py-4">
        <div>
          <h2
            className="text-sm font-semibold text-text-primary"
            data-testid="attention-drawer-title"
          >
            {item.headline}
          </h2>
          <p className="mt-0.5 text-xs text-text-muted">
            {item.segment_label || item.segment} ·{" "}
            {periodLabel(reportingPeriod(item))}
            {comparisonPeriod(item)
              ? ` vs ${periodLabel(comparisonPeriod(item))}`
              : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          data-testid="attention-drawer-close"
          className="rounded px-2 py-1 text-sm text-text-muted hover:bg-surface-sunken"
          aria-label="Close"
        >
          ✕
        </button>
      </header>

      <div className="space-y-5 px-5 py-4 text-sm text-text-secondary">
        <section data-testid="attention-drawer-why">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Why it appeared
          </h3>
          <p className="mt-1">{item.why_it_appeared}</p>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            What changed
          </h3>
          <p className="mt-1">{item.what_changed}</p>
        </section>

        <section data-testid="attention-drawer-numbers">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Key numbers
          </h3>
          <dl className="mt-1 divide-y divide-border">
            {item.key_numbers.map((entry) => (
              <div
                key={entry.label}
                className="flex items-baseline justify-between gap-4 py-1.5"
              >
                <dt className="text-xs text-text-muted">{entry.label}</dt>
                <dd className="font-mono text-sm text-text-primary">
                  {entry.value}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        {item.possible_drivers.length ? (
          <section data-testid="attention-drawer-drivers">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Possible drivers
            </h3>
            <ul className="mt-1 space-y-1.5">
              {item.possible_drivers.map((driver, index) => (
                <li key={`${driver.metric}-${index}`} className="text-sm">
                  <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-text-secondary">
                    {driver.relationship}
                  </span>{" "}
                  {driver.statement}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[11px] text-text-muted">
              Recorded alongside this movement. Association, not established
              cause.
            </p>
          </section>
        ) : null}

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            What to review next
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {item.what_to_review_next.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {drill ? (
            <p
              className="mt-2 text-xs text-text-muted"
              data-testid="attention-drawer-drilldown"
            >
              {drill.note}
              {drill.entity_count
                ? ` This segment holds ${drill.entity_count} ${drill.entity_label} in ${periodLabel(reportingPeriod(item))}.`
                : ""}
            </p>
          ) : null}
        </section>

        <section>
          <button
            type="button"
            onClick={() => setShowTrace((open) => !open)}
            data-testid="attention-drawer-trace-toggle"
            className="text-xs text-text-muted underline"
          >
            {showTrace ? "Hide trace" : "Trace and evidence"}
          </button>
          {showTrace ? (
            <pre
              data-testid="attention-drawer-trace"
              className="mt-2 max-h-64 overflow-auto rounded border border-border bg-surface-sunken p-3 text-[11px] leading-relaxed text-text-primary"
            >
              {JSON.stringify(item.evidence, null, 2)}
            </pre>
          ) : null}
          {operatorView ? (
            <p className="mt-1 text-[11px] text-text-muted">
              Full technical record: {item.evidence_url}
            </p>
          ) : null}
        </section>
      </div>

      <footer className="sticky bottom-0 border-t border-border bg-surface px-5 py-4">
        {error ? (
          <p
            className="mb-2 text-xs text-negative"
            data-testid="attention-drawer-error"
          >
            {error}
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => void start()}
          disabled={busy}
          data-testid="attention-investigate"
          className="w-full rounded bg-accent px-4 py-2 text-sm text-accent-contrast disabled:opacity-40"
        >
          {busy ? "Opening…" : "Investigate further"}
        </button>
        <p className="mt-1.5 text-[11px] text-text-muted"
           data-testid="attention-investigate-note">
          {/* The card's own period noun. This said "quarter" to a Retail
              reader looking at 2026-08, under a button that seeds a thread
              pinned to a month. */}
          Opens a Cockpit conversation already holding this segment,{" "}
          {periodNoun(item)} and movement. You will not need to restate them.
        </p>
      </footer>
    </aside>
  );
}
