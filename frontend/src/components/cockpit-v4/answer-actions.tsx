/**
 * What you can do with an answer once you have read it.
 *
 * Four actions, all of them on a finished run: save it, put it in an
 * investigation, comment on it, share it with a colleague.
 *
 * The delivery line is the rule this component exists to hold. It renders
 * whatever `deliveryWording` returns and nothing else, so the only sentence
 * on this screen that says "Sent" is one the server reached by handing the
 * message to a named transport that accepted it. A build with no transport
 * -- which is every build nobody has configured one for -- shows "Recorded,
 * not sent" and says why.
 */

"use client";

import { useCallback, useState } from "react";

import {
  addComment,
  createInvestigation,
  deliveryWording,
  exportLinks,
  saveAnalysis,
  shareItem,
  type Notification,
  type SavedAnalysis,
} from "./client";

type Panel = "" | "save" | "investigate" | "comment" | "share" | "export";

function Button({
  id,
  label,
  active,
  onClick,
  disabled,
}: {
  id: string;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      data-testid={`v4-action-${id}`}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={
        "rounded border px-2.5 py-1 text-xs font-medium transition " +
        (active
          ? "border-accent bg-accent text-accent-contrast"
          : "border-border-strong text-text-secondary hover:border-border-strong") +
        (disabled ? " cursor-not-allowed opacity-50" : "")
      }
    >
      {label}
    </button>
  );
}

export function AnswerActions({
  runId,
  question,
  threadId,
  tables = [],
  charts = [],
}: {
  runId: string;
  question: string;
  threadId?: string;
  /** Published tables, for the per-table CSV links. */
  tables?: { artifact_id?: string; title?: string; row_count?: number }[];
  /** Published charts, for the per-chart SVG links. */
  charts?: { title?: string }[];
}) {
  const [panel, setPanel] = useState<Panel>("");
  const [saved, setSaved] = useState<SavedAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [notification, setNotification] = useState<Notification | null>(null);
  const [notified, setNotified] = useState(false);

  const [title, setTitle] = useState(question.slice(0, 120));
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [audience, setAudience] = useState("");
  const [email, setEmail] = useState("");

  const ensureSaved = useCallback(async (): Promise<SavedAnalysis> => {
    if (saved) return saved;
    const record = await saveAnalysis({ run_id: runId, title, note });
    setSaved(record);
    return record;
  }, [saved, runId, title, note]);

  const guard = useCallback(
    async (work: () => Promise<string>) => {
      setBusy(true);
      setStatus("");
      try {
        setStatus(await work());
      } catch (error) {
        setStatus(
          error instanceof Error
            ? `That did not go through: ${error.message}`
            : "That did not go through.",
        );
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <div
      className="mt-4 border-t border-border pt-3"
      data-testid="v4-answer-actions"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-text-muted">
          Do something with this
        </span>
        <Button
          id="save"
          label={saved ? "Saved" : "Save"}
          active={panel === "save"}
          onClick={() => setPanel(panel === "save" ? "" : "save")}
        />
        <Button
          id="investigate"
          label="Add to investigation"
          active={panel === "investigate"}
          onClick={() => setPanel(panel === "investigate" ? "" : "investigate")}
        />
        <Button
          id="comment"
          label="Comment"
          active={panel === "comment"}
          onClick={() => setPanel(panel === "comment" ? "" : "comment")}
        />
        <Button
          id="share"
          label="Share"
          active={panel === "share"}
          onClick={() => setPanel(panel === "share" ? "" : "share")}
        />
        <Button
          id="export"
          label="Export"
          active={panel === "export"}
          onClick={() => setPanel(panel === "export" ? "" : "export")}
        />
      </div>

      {panel === "export" ? (
        <div className="mt-3 space-y-2" data-testid="v4-panel-export">
          <p className="text-xs text-text-secondary">
            Every document below carries the book, the release and the
            fingerprint of the bytes it was computed from, so a figure pasted
            into a memo can still be traced back to the query that produced
            it.
          </p>
          <ul className="space-y-1.5 text-sm">
            <li>
              <a
                data-testid="v4-export-analysis"
                href={exportLinks(runId).analysis}
                className="text-text-primary underline underline-offset-2"
              >
                This analysis (Markdown)
              </a>
            </li>
            {tables.map((table, index) =>
              table.artifact_id ? (
                <li key={table.artifact_id}>
                  <a
                    data-testid={`v4-export-table-${index}`}
                    href={exportLinks(runId).table(table.artifact_id)}
                    className="text-text-primary underline underline-offset-2"
                  >
                    {table.title || "Result"} — every row as CSV
                  </a>
                  {typeof table.row_count === "number" ? (
                    <span className="ml-1.5 text-xs text-text-muted">
                      ({table.row_count} rows)
                    </span>
                  ) : null}
                </li>
              ) : null,
            )}
            {charts.map((chart, index) => (
              <li key={`chart-${index}`}>
                <a
                  data-testid={`v4-export-chart-${index}`}
                  href={exportLinks(runId).chart(index)}
                  className="text-text-primary underline underline-offset-2"
                >
                  {chart.title || "Chart"} — as SVG
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {panel === "save" ? (
        <div className="mt-3 space-y-2" data-testid="v4-panel-save">
          <label className="block text-xs text-text-secondary">
            Title
            <input
              data-testid="v4-save-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <label className="block text-xs text-text-secondary">
            Why you are keeping it
            <input
              data-testid="v4-save-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <button
            type="button"
            data-testid="v4-save-submit"
            disabled={busy}
            onClick={() =>
              void guard(async () => {
                const record = await ensureSaved();
                return `Saved as “${record.title}”.`;
              })
            }
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            Save this analysis
          </button>
        </div>
      ) : null}

      {panel === "investigate" ? (
        <div className="mt-3 space-y-2" data-testid="v4-panel-investigate">
          <label className="block text-xs text-text-secondary">
            Investigation title
            <input
              data-testid="v4-investigation-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <button
            type="button"
            data-testid="v4-investigation-submit"
            disabled={busy}
            onClick={() =>
              void guard(async () => {
                const record = await ensureSaved();
                const investigation = await createInvestigation({
                  title,
                  summary: note,
                  origin: "answer",
                  thread_id: threadId ?? "",
                  saved_ids: [record.saved_id],
                });
                return `Opened “${investigation.title}” with this analysis in it.`;
              })
            }
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            Open an investigation
          </button>
        </div>
      ) : null}

      {panel === "comment" ? (
        <div className="mt-3 space-y-2" data-testid="v4-panel-comment">
          <textarea
            data-testid="v4-comment-body"
            value={comment}
            rows={3}
            onChange={(event) => setComment(event.target.value)}
            className="w-full rounded border border-border-strong px-2 py-1 text-sm"
            placeholder="What should a reviewer check?"
          />
          <button
            type="button"
            data-testid="v4-comment-submit"
            disabled={busy || !comment.trim()}
            onClick={() =>
              void guard(async () => {
                const record = await ensureSaved();
                await addComment({
                  subject_kind: "saved_analysis",
                  subject_id: record.saved_id,
                  body: comment.trim(),
                });
                setComment("");
                return "Comment added.";
              })
            }
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            Add comment
          </button>
        </div>
      ) : null}

      {panel === "share" ? (
        <div className="mt-3 space-y-2" data-testid="v4-panel-share">
          <label className="block text-xs text-text-secondary">
            Colleague or group inside the bank
            <input
              data-testid="v4-share-audience"
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <label className="block text-xs text-text-secondary">
            Notify by email (optional)
            <input
              data-testid="v4-share-email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <button
            type="button"
            data-testid="v4-share-submit"
            disabled={busy || !audience.trim()}
            onClick={() =>
              void guard(async () => {
                const record = await ensureSaved();
                const result = await shareItem({
                  subject_kind: "saved_analysis",
                  subject_id: record.saved_id,
                  audience_id: audience.trim(),
                  message: note,
                  notify_email: email.trim(),
                });
                setNotification(result.notification);
                setNotified(true);
                return `Shared with ${result.share.audience_id}.`;
              })
            }
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            Share
          </button>
          {notified ? (
            <p
              data-testid="v4-share-delivery"
              className={
                "text-xs " +
                (notification?.delivered ? "text-positive" : "text-warning")
              }
            >
              {deliveryWording(notification)}
            </p>
          ) : null}
        </div>
      ) : null}

      {status ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="v4-action-status">
          {status}
        </p>
      ) : null}
    </div>
  );
}
