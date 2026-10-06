"use client";

/**
 * The neutral state for a widget whose backend is not part of this runtime.
 *
 * The isolated V4 UAT instance serves the Cockpit API and nothing else. Risk
 * cases, investigations, early warning, the legacy briefing and workspace
 * notifications are served by the main CreditProbe backend, which this
 * instance does not start.
 *
 * So those widgets do not call. A request that is going to 404 is not
 * diagnostics — it is noise in the API log and a red card on the screen, and
 * a page covered in red cards is one nobody can test a Cockpit in. Saying
 * plainly that the surface is not part of this runtime is both honest and
 * quiet.
 */

export function NotInThisRuntime({
  title,
  what,
}: {
  title: string;
  what: string;
}) {
  return (
    <section
      data-testid="v4-not-in-runtime"
      className="rounded-lg border border-dashed border-border-strong bg-surface-sunken/60 px-4 py-3"
    >
      <h3 className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {title}
      </h3>
      <p className="mt-1 text-sm text-text-muted">
        {what} is not available in this isolated Cockpit V4 runtime. It is
        served by the main CreditProbe backend, which this instance does not
        start. Nothing was requested and nothing failed.
      </p>
    </section>
  );
}
