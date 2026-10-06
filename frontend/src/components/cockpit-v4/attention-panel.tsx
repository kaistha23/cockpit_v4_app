"use client";

/**
 * The two analytical sections on the Cockpit home page.
 *
 * Everything here is already computed. The component fetches one endpoint,
 * renders what came back, and holds no analytical logic of its own: no
 * ranking, no thresholds, no derived percentage. If a number is on the
 * screen, the server put it there with its evidence attached, which is what
 * makes "click through to the evidence" a real promise rather than a button.
 *
 * A failure here is a COMPONENT failure. The section says so, carries its
 * reference, and leaves the rest of the Cockpit alone — a dashboard that
 * cannot compute is not a backend that is down.
 */

import * as React from "react";

import {
  readAttention,
  type DomainId,
  type AttentionFeed,
  type AttentionItem,
} from "./client";
import { belongsTo } from "./domain-guard";
import {
  comparisonPeriod,
  periodLabel,
  periodNoun,
  reportingPeriod,
} from "./period";

/** Kept for callers that already have a quarter in hand. New code should
 *  use `periodLabel`, which also knows what a month looks like. */
export function quarterLabel(quarter: string): string {
  return periodLabel(quarter);
}

const DOT: Record<string, string> = {
  high: "bg-negative",
  moderate: "bg-warning",
  low: "bg-border-strong",
  informational: "bg-accent",
};

const SEVERITY: Record<string, string> = {
  high: "bg-negative-muted text-negative border-negative",
  moderate: "bg-warning-muted text-warning border-warning",
  low: "bg-surface-sunken text-text-secondary border-border",
  informational: "bg-accent-muted text-accent border-accent",
};

function Card({
  item,
  onOpen,
}: {
  item: AttentionItem;
  onOpen: (item: AttentionItem) => void;
}) {
  return (
    <button
      type="button"
      data-testid="attention-card"
      data-item-id={item.item_id}
      data-segment={item.segment}
      data-scope={item.scope}
      data-severity={item.severity}
      onClick={() => onOpen(item)}
      className="flex w-full items-start gap-3 border-b border-border px-5 py-4 text-left transition last:border-b-0 hover:bg-surface-sunken"
    >
      <span
        aria-hidden="true"
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
          DOT[item.severity] ?? DOT.low
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium text-text-primary">
          {item.headline}
        </span>
        <span className="mt-1 block text-sm text-text-muted">
          <span className="text-text-secondary">{item.metric_label}</span>
          {" · "}
          {item.one_line}
        </span>
      </span>
      <span
        className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
          SEVERITY[item.severity] ?? SEVERITY.low
        }`}
      >
        {item.severity}
      </span>
    </button>
  );
}

export function AttentionPanel({
  onOpen,
  domain,
}: {
  onOpen: (item: AttentionItem) => void;
  /** Which book. Refetched when it changes, because the server computes a
   *  different dashboard rather than filtering a shared one. */
  domain?: DomainId;
}) {
  const [feed, setFeed] = React.useState<AttentionFeed | null>(null);
  const [failure, setFailure] = React.useState<{
    message: string;
    reference: string;
  } | null>(null);

  React.useEffect(() => {
    let live = true;
    setFeed(null);
    setFailure(null);
    void (async () => {
      try {
        const loaded = await readAttention(domain);
        // §17, last mile. Two fetches in flight and the slower one landing
        // last would put one book's dashboard under the other's heading,
        // and every number on the screen would look correct.
        if (live && belongsTo(loaded, domain)) setFeed(loaded);
      } catch (caught) {
        if (!live) return;
        const detail = (caught as { detail?: { detail?: Record<string, string> } })
          ?.detail;
        const body = (detail as { detail?: Record<string, string> })?.detail ??
          (detail as Record<string, string> | undefined);
        setFailure({
          message:
            body?.message ??
            "The segment attention feed could not be computed.",
          reference: body?.error_reference ?? "",
        });
      }
    })();
    return () => {
      live = false;
    };
  }, [domain]);

  if (failure) {
    return (
      <section
        className="rounded border border-warning bg-warning-muted px-4 py-3"
        data-testid="attention-unavailable"
      >
        <h2 className="text-sm font-semibold text-warning">
          Segment attention feed unavailable
        </h2>
        <p className="mt-1 text-sm text-warning">{failure.message}</p>
        {failure.reference ? (
          <p className="mt-1 text-xs text-warning">
            Reference {failure.reference}
          </p>
        ) : null}
        <p className="mt-1 text-xs text-warning">
          Ask is unaffected — the question box above still works.
        </p>
      </section>
    );
  }

  if (!feed) {
    return (
      <p className="text-xs text-text-muted" data-testid="attention-loading">
        Reading the latest period…
      </p>
    );
  }

  // The book supplies its own calendar. Corporate reports quarters and
  // Retail reports months, and this line is the only place that has to know
  // the difference -- everything below writes `period`.
  const period = periodLabel(reportingPeriod(feed));
  const against = periodLabel(comparisonPeriod(feed));
  const noun = periodNoun(feed);
  const segments = feed.segments_requiring_attention;
  const highlights = feed.ecl_highlights;

  return (
    <div className="space-y-10" data-testid="attention-panel">
      <section data-testid="segments-requiring-attention" className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          {/* The server names its own section: a corporate book watches
              segments and a retail book watches its portfolio, and a
              hard-coded heading over retail data would be the label lying
              about the numbers under it. */}
          <h2 className="text-lg font-semibold text-text-primary"
              data-testid="attention-heading">
            {feed.attention_label || "Segments requiring attention"}
          </h2>
          <span
            data-testid="attention-reporting-period"
            className="text-[11px] font-medium uppercase tracking-[0.12em] text-text-muted"
          >
            Reporting {noun} {period}
          </span>
        </div>
        <p className="text-sm text-text-secondary" data-testid="attention-summary">
          CreditProbe reviewed {period} against {against} and identified{" "}
          {segments.length}{" "}
          {segments.length === 1 ? "segment issue" : "segment issues"}.
        </p>

        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          {segments.length ? (
            segments.map((item) => (
              <Card key={item.item_id} item={item} onOpen={onOpen} />
            ))
          ) : (
            <p className="px-5 py-6 text-sm text-text-muted">
              No segment movement cleared the materiality floor for{" "}
              {period}.
            </p>
          )}
        </div>
        {feed.segment_note ? (
          <p
            className="text-xs text-text-muted"
            data-testid="segments-requiring-attention-note"
          >
            {feed.segment_note}
          </p>
        ) : null}
      </section>

      <section data-testid="ecl-highlights" className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-lg font-semibold text-text-primary"
              data-testid="ecl-heading">
            {feed.highlights_label || `Latest-${noun} ECL highlights`}
          </h2>
          <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-text-muted">
            {period} vs {against}
          </span>
        </div>
        <p className="text-sm text-text-secondary">
          {highlights.length} distinct ECL{" "}
          {highlights.length === 1 ? "development" : "developments"} — by
          sector, by borrower and across the book.
        </p>
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          {highlights.map((item) => (
            <Card key={item.item_id} item={item} onOpen={onOpen} />
          ))}
        </div>
      </section>

      <p className="text-[11px] text-text-muted" data-testid="attention-footnote">
        {feed.ownership?.note ??
          "Movements in the recorded book between two reporting periods."}{" "}
        Computed from release {feed.release_id}
        {typeof feed.computed_ms === "number"
          ? ` in ${feed.computed_ms} ms`
          : ""}{" "}
        with no model call.
      </p>
    </div>
  );
}
