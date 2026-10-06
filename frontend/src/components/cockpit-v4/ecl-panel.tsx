"use client";

/**
 * Where this book's expected credit loss is, and what moved it.
 *
 * Two statements the server computed and this component renders. It formats
 * nothing: every figure arrives as a string the server already wrote at the
 * governed precision, so a monetary amount reads the same here as it does in
 * an answer, a table and an export.
 *
 * The decomposition publishes its residual. A panel that showed five
 * components adding to "about" the movement would be asking the reader to
 * trust it; this one shows that they add to exactly the movement, and says
 * so in a line they can check against the numbers above it.
 */

import * as React from "react";

import { readEcl } from "./client";
import type { DomainId, EclPanel as EclPanelBody } from "./client";
import { belongsTo } from "./domain-guard";
import { comparisonPeriod, periodLabel, reportingPeriod } from "./period";

export function EclPanel({ domain }: { domain: DomainId }) {
  const [body, setBody] = React.useState<EclPanelBody | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let live = true;
    setBody(null);
    setError("");
    readEcl(domain)
      .then((next) => {
        // §17, last mile. See `domain-guard`: the effect's cleanup catches
        // a response for a component that moved on, and cannot catch one
        // for the right component and the wrong book.
        if (live && belongsTo(next, domain)) setBody(next);
      })
      .catch((exc: unknown) => {
        // A panel that cannot be computed says so and leaves the rest of the
        // page working. It never renders zeros, which read as "no loss".
        if (live) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      live = false;
    };
  }, [domain]);

  if (error) {
    return (
      <section data-testid="v4-ecl-panel" data-state="unavailable">
        <h2 className="text-lg font-semibold text-text-primary">
          Expected credit loss
        </h2>
        <p
          data-testid="v4-ecl-unavailable"
          className="mt-2 text-sm text-text-secondary"
        >
          This panel could not be computed: {error} Nothing was substituted
          for it.
        </p>
      </section>
    );
  }
  if (!body) {
    return (
      <section data-testid="v4-ecl-panel" data-state="loading">
        <h2 className="text-lg font-semibold text-text-primary">
          Expected credit loss
        </h2>
        <p className="mt-2 text-sm text-text-muted">Computing…</p>
      </section>
    );
  }

  const { profile, decomposition } = body;
  return (
    <section
      data-testid="v4-ecl-panel"
      data-state="ready"
      data-domain={profile.domain_id}
      data-release={profile.release_id}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-text-primary">
          {profile.domain_label}: expected credit loss
        </h2>
        <p className="text-xs text-text-muted">
          {periodLabel(reportingPeriod(profile))} against{" "}
          {periodLabel(comparisonPeriod(profile))} · per{" "}
          {profile.exposure_grain}
        </p>
      </div>

      <dl
        data-testid="v4-ecl-headline"
        className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3"
      >
        {[
          { key: "ead", term: "Exposure at default", value: profile.display_total_ead },
          { key: "ecl", term: "Recognised ECL", value: profile.display_total_ecl },
          { key: "coverage", term: "Coverage", value: profile.display_coverage },
        ].map((entry) => (
          <div
            key={entry.key}
            data-testid={`v4-ecl-${entry.key}`}
            className="rounded-lg border border-border bg-surface p-4"
          >
            <dt className="text-xs uppercase tracking-wide text-text-muted">
              {entry.term}
            </dt>
            <dd className="mt-1 text-xl font-semibold text-text-primary">
              {entry.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="mt-6 overflow-x-auto">
        <table
          data-testid="v4-ecl-stages"
          className="w-full min-w-[38rem] text-left text-sm"
        >
          <thead className="text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Stage</th>
              <th className="py-2 pr-4 text-right font-medium">Exposure</th>
              <th className="py-2 pr-4 text-right font-medium">Share</th>
              <th className="py-2 pr-4 text-right font-medium">ECL</th>
              <th className="py-2 pr-4 text-right font-medium">Coverage</th>
              <th className="py-2 text-right font-medium">ECL movement</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {profile.stages.map((stage) => (
              <tr key={stage.stage} data-testid={`v4-ecl-stage-${stage.stage}`}>
                <td className="py-2 pr-4 text-text-primary">{stage.label}</td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {stage.display_ead}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {stage.display_share_of_ead}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {stage.display_ecl}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {stage.display_coverage}
                </td>
                <td className="py-2 text-right tabular-nums">
                  {stage.display_ecl_movement}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-text-muted">{profile.note}</p>

      <h3 className="mt-8 text-base font-semibold text-text-primary">
        What moved it: {decomposition.display_opening} →{" "}
        {decomposition.display_closing} ({decomposition.display_movement})
      </h3>
      <ul data-testid="v4-ecl-decomposition" className="mt-3 space-y-2">
        {decomposition.components.map((component) => (
          <li
            key={component.component_id}
            data-testid={`v4-ecl-component-${component.component_id}`}
            className="rounded-lg border border-border bg-surface p-3"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium text-text-primary">
                {component.label}
              </span>
              <span className="text-sm tabular-nums text-text-primary">
                {component.display_amount}
              </span>
            </div>
            <p className="mt-1 text-xs text-text-secondary">
              {component.explanation}
            </p>
          </li>
        ))}
      </ul>
      <p
        data-testid="v4-ecl-reconciliation"
        className="mt-3 text-xs text-text-muted"
      >
        {decomposition.reconciles
          ? "These components sum to the movement exactly."
          : "These components do NOT sum to the movement; the difference is " +
            "shown as a residual rather than absorbed."}{" "}
        {decomposition.method}
      </p>
    </section>
  );
}
