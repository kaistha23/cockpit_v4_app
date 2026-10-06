"use client";

/**
 * How this question became a number.
 *
 * "Trace" opened the process panel: a list of stages with ticks against
 * them. It said the analysis had run. A reader who wanted to know what
 * had been understood from their question, what query was written, or how
 * the figure in the second paragraph reached the page got none of it --
 * and all of it was on file.
 *
 * So this panel is not a visualisation. It is a record, laid out in the
 * order a person reads it: what was asked, what was understood, what was
 * submitted (including what was refused), what each step read, and where
 * every figure came from. Nothing in it is computed here.
 *
 * TWO THINGS IT SAYS OUT LOUD rather than leaving to inference:
 *
 *   the question was not rewritten -- normalisation is mechanical, and a
 *   reader who assumes their English was silently corrected has been
 *   misled by the absence of a sentence;
 *
 *   provenance stops at the result set -- a published total is traced to
 *   a stored row, and that row's own source rows are not retained.
 */

import * as React from "react";

import { exportLinks, readGovernance, type GovernanceRecord } from "./client";

/**
 * The whole record as a pack: the document, the JSON and the rows.
 *
 * Three formats because three readers. The Markdown is what a person
 * circulates; the JSON is the same record whole; the CSVs are the stored
 * results, so a reviewer can re-run the arithmetic rather than take the
 * answer's word for it. The SQL is gated in the pack exactly as it is on
 * screen -- a download is not a way around a permission.
 *
 * A LINK, not a fetch-and-save. `exportLinks` says why in its own header:
 * the browser downloads the document straight from the API with the
 * session cookie it already has, so nothing about the file passes through
 * this code and nothing here can reformat a figure on its way out. Every
 * other export in this product works that way and this one is not
 * special.
 */
function PackDownload({ runId }: { runId: string }) {
  return (
    <a
      data-testid="v4-governance-download"
      href={exportLinks(runId).governance}
      download
      className="rounded border border-border px-2 py-1 text-xs text-text-secondary transition hover:bg-surface-hover"
    >
      Download the record
    </a>
  );
}

function Section({ title, hint, children }: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border pt-3">
      <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
      {hint ? (
        <p className="mt-0.5 text-xs text-text-muted">{hint}</p>
      ) : null}
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="mt-2">
      <p className="meta text-xs font-semibold uppercase tracking-wide text-text-muted">
        {title}
      </p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-sm text-text-secondary" dir="auto">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Step({ step }: { step: GovernanceRecord["submissions"][0]["steps"][0] }) {
  return (
    <li data-testid="v4-governance-step"
        className="rounded border border-border bg-surface p-3">
      <p className="text-sm font-medium text-text-primary">
        {step.step_id}
        <span className="ml-2 font-normal text-text-secondary">
          {step.purpose}
        </span>
      </p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-text-muted">Read</dt>
        <dd className="text-text-secondary">
          {/* WHAT THE QUERY READ, not what it was allowed to read. The
              authorized set is the whole book and naming it here would
              say a corporate question touched the retail relations. */}
          {step.relations_read.join(", ") || "—"}
        </dd>
        <dt className="text-text-muted">Rows out</dt>
        <dd className="tabular-nums text-text-secondary">
          {step.rows_out ?? "—"}
          {step.complete === false ? (
            <span className="ml-2 text-warning">clipped</span>
          ) : null}
        </dd>
        <dt className="text-text-muted">Result</dt>
        <dd className="mono truncate text-text-secondary">
          {step.artifact_id || "none — this step produced nothing"}
        </dd>
        <dt className="text-text-muted">Code digest</dt>
        <dd className="mono truncate text-text-secondary">
          {step.code_digest || "—"}
        </dd>
      </dl>
      {step.failed ? (
        <p data-testid="v4-governance-step-failed"
           className="mt-2 text-xs text-negative">
          Failed at {step.failed.phase || "runtime"}: {step.failed.message}
        </p>
      ) : null}
      {step.code_shown ? (
        <pre data-testid="v4-governance-sql"
             className="mono mt-2 overflow-x-auto rounded border border-border bg-surface-sunken p-2 text-xs leading-relaxed text-text-primary">
          <code>{step.code}</code>
        </pre>
      ) : (
        // WITHHELD AND SAYING SO. A panel that simply goes quiet reads as
        // a control that did not run.
        <p data-testid="v4-governance-sql-withheld"
           className="mt-2 rounded border border-dashed border-border px-2 py-1.5 text-xs text-text-muted">
          {step.code}
        </p>
      )}
    </li>
  );
}

function Submission({ submission }: {
  submission: GovernanceRecord["submissions"][0];
}) {
  return (
    <li data-testid="v4-governance-submission"
        data-ran={submission.ran ? "true" : "false"}
        className="space-y-2 rounded border border-border bg-surface-sunken p-3">
      <p className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-semibold text-text-primary">
          Submission {submission.ordinal}
        </span>
        <span className={`meta text-xs font-semibold uppercase tracking-wide ${
          submission.ran ? "text-positive" : "text-negative"}`}>
          {submission.ran ? "ran" : "refused — never executed"}
        </span>
      </p>
      {submission.objective ? (
        <p className="text-sm text-text-secondary" dir="auto">
          {submission.objective}
        </p>
      ) : null}
      {submission.refusal ? (
        // THE PART THAT SHOWS THE CONTROLS WORKING. A record that left
        // refused submissions out would read as a system that has never
        // stopped anything.
        <p data-testid="v4-governance-refusal"
           className="rounded border border-negative/40 bg-negative-muted px-2 py-1.5 text-xs text-text-primary">
          <span className="font-semibold">
            Stopped at {submission.refusal.stage || "validation"}
          </span>
          {submission.refusal.error_code
            ? ` (${submission.refusal.error_code})`
            : ""}
          . {submission.refusal.message}
        </p>
      ) : null}
      {submission.expected_grain ? (
        <p className="text-xs text-text-muted">
          Expected grain: {submission.expected_grain}
        </p>
      ) : null}
      <ol className="space-y-2">
        {submission.steps.map((step) => (
          <Step key={step.step_id} step={step} />
        ))}
      </ol>
    </li>
  );
}

/** The whole record, for one run. */
export function GovernancePanel({ runId }: { runId: string }) {
  const [record, setRecord] = React.useState<GovernanceRecord | null>(null);
  const [problem, setProblem] = React.useState("");
  const [busy, setBusy] = React.useState(true);

  React.useEffect(() => {
    let live = true;
    setBusy(true);
    readGovernance(runId)
      .then((body) => { if (live) { setRecord(body); setProblem(""); } })
      .catch((error: unknown) => {
        if (!live) return;
        setProblem(error instanceof Error ? error.message
                                          : "This record could not be opened.");
      })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [runId]);

  if (busy) {
    return (
      <p data-testid="v4-governance-loading" className="text-sm text-text-muted">
        Opening the record…
      </p>
    );
  }
  if (problem || !record) {
    return (
      <p data-testid="v4-governance-problem" className="text-sm text-negative">
        {problem || "This record could not be opened."}
      </p>
    );
  }

  const { question, interpretation, waterfall } = record;

  return (
    <div data-testid="v4-governance" className="space-y-4">
      <div className="flex justify-end">
        <PackDownload runId={runId} />
      </div>
      <Section
        title="The question"
        hint={question.policy}
      >
        <p className="text-sm text-text-primary" dir="auto">{question.asked}</p>
        {question.changed ? (
          <div data-testid="v4-governance-normalised" className="mt-2">
            <p className="text-sm text-text-secondary" dir="auto">
              Analysed as: {question.analysed}
            </p>
            <p className="mt-0.5 text-xs text-text-muted">
              Mechanical normalisation applied:{" "}
              {(question.normalisation.applied ?? []).join(", ") || "none"}.
            </p>
          </div>
        ) : null}
      </Section>

      <Section
        title="What was understood"
        hint={interpretation.domain_label
          ? `Answered in the ${interpretation.domain_label} book.`
          : undefined}
      >
        {interpretation.understood_request ? (
          <p className="text-sm text-text-secondary" dir="auto">
            {interpretation.understood_request}
          </p>
        ) : null}
        {interpretation.value_resolutions.length ? (
          <div data-testid="v4-governance-resolutions" className="mt-3">
            <p className="meta text-xs font-semibold uppercase tracking-wide text-text-muted">
              What your words resolved to
            </p>
            <table className="mt-1 w-full border-collapse text-sm">
              <thead>
                <tr className="text-left">
                  <th className="border-b border-border-strong py-1 pr-3 text-xs font-semibold text-text-muted">
                    You typed
                  </th>
                  <th className="border-b border-border-strong py-1 pr-3 text-xs font-semibold text-text-muted">
                    Governed value
                  </th>
                  <th className="border-b border-border-strong py-1 text-xs font-semibold text-text-muted">
                    Field
                  </th>
                </tr>
              </thead>
              <tbody>
                {interpretation.value_resolutions.map((entry) => (
                  <tr key={`${entry.you_typed}-${entry.on_field}`}>
                    <td className="border-b border-border py-1 pr-3 text-text-secondary"
                        dir="auto">
                      {entry.you_typed}
                      {entry.exact ? null : (
                        <span className="ml-1 text-xs text-warning">
                          nearest match
                        </span>
                      )}
                    </td>
                    <td className="border-b border-border py-1 pr-3 text-text-primary"
                        dir="auto">
                      {entry.resolved_to}
                    </td>
                    <td className="mono border-b border-border py-1 text-xs text-text-muted">
                      {entry.on_field}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <Bullets title="Canonical mappings"
                 items={interpretation.canonical_mappings} />
        <Bullets title="Assumptions resolved"
                 items={interpretation.resolved_assumptions} />
        <Bullets title="Left out" items={interpretation.excluded_parts} />
        <Bullets title="Still open"
                 items={interpretation.blocking_ambiguities} />
      </Section>

      <Section
        title="What was submitted"
        hint={record.sql_visible ? undefined : record.sql_policy}
      >
        {record.submissions.length ? (
          <ol className="space-y-3">
            {record.submissions.map((submission) => (
              <Submission key={submission.submission_id}
                          submission={submission} />
            ))}
          </ol>
        ) : (
          <p className="text-sm text-text-muted">
            No query was submitted for this question.
          </p>
        )}
      </Section>

      <Section title="From the release to the figure">
        <div className="overflow-x-auto">
          <table data-testid="v4-governance-waterfall"
                 className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-left">
                {["Step", "Read", "Result", "Rows", "Shown as"].map((head) => (
                  <th key={head}
                      className="border-b border-border-strong px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {waterfall.artifacts.map((row) => (
                <tr key={row.artifact_id}>
                  <td className="border-b border-border px-2 py-1.5 text-text-secondary">
                    {row.step_id || "—"}
                  </td>
                  <td className="border-b border-border px-2 py-1.5 text-text-secondary">
                    {row.relations_read.join(", ") || "—"}
                  </td>
                  <td className="mono border-b border-border px-2 py-1.5 text-xs text-text-muted">
                    {row.artifact_id}
                  </td>
                  <td className="border-b border-border px-2 py-1.5 tabular-nums text-text-secondary">
                    {row.rows ?? "—"}
                  </td>
                  <td className="border-b border-border px-2 py-1.5 text-text-secondary">
                    {row.published_as.length
                      ? row.published_as.map((item, at) => (
                          <span key={at} className="block">
                            {item.kind}: {item.title}
                            {/* Why the analyst drew it this way. Shown only
                                when they said; the record never writes a
                                justification on their behalf. */}
                            {item.why_this_chart ? (
                              <span data-testid="v4-governance-chart-reason"
                                    className="block text-xs text-text-muted">
                                {item.why_this_chart}
                              </span>
                            ) : null}
                          </span>
                        ))
                      : "not published"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {waterfall.claims.length ? (
          <div data-testid="v4-governance-claims" className="mt-4">
            <p className="meta text-xs font-semibold uppercase tracking-wide text-text-muted">
              Every published figure, and where it came from
            </p>
            <ul className="mt-1 space-y-1">
              {waterfall.claims.map((claim) => (
                <li key={claim.claim_id}
                    className="flex flex-wrap items-baseline gap-2 text-sm">
                  <span className="tabular-nums font-medium text-text-primary">
                    {claim.published}
                  </span>
                  <span className="mono text-xs text-text-muted">
                    {claim.from_cell
                      ? `${claim.from_cell.artifact_id} · row ${
                          claim.from_cell.row_key} · ${claim.from_cell.column_id}`
                      : "recomputed from operands the server re-ran"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* STATED, not left to inference. A waterfall that stops at the
            result set without saying so reads as one that goes all the
            way down to the borrowers. */}
        <p data-testid="v4-governance-limit"
           className="mt-4 rounded border border-border bg-surface-sunken px-3 py-2 text-xs text-text-secondary">
          {waterfall.limit}
        </p>
      </Section>
    </div>
  );
}
