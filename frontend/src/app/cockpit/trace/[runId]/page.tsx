"use client";

/**
 * The governance record for one Cockpit run.
 *
 * A SEPARATE ROUTE FROM `/trace/[runId]`, deliberately. That page is the
 * legacy Analytical Reasoning Map: it calls `api.investigation(id)` with
 * `Number(runId)`, and a V4 id is `run-<32 hex>`, so the number is `NaN`
 * and the page resolves nothing. The Trace button in the thread pointed
 * at it, which is why pressing it did nothing at all.
 *
 * Nothing about the legacy page changes. It answers for the runs it knows
 * about; this one answers for Cockpit runs.
 */

import { use } from "react";

import { GovernancePanel } from "@/components/cockpit-v4/governance-panel";

export default function CockpitTracePage({ params }: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = use(params);
  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-8">
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-text-primary">
          Governance record
        </h1>
        <p className="mono mt-0.5 text-xs text-text-muted">{runId}</p>
      </header>
      <GovernancePanel runId={runId} />
    </main>
  );
}
