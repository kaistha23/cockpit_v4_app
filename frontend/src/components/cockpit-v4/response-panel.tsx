"use client";

/**
 * The response panel: answer, partial answer, referral, clarification,
 * unsupported, or an explicit stop.
 *
 * A stop is rendered as a stop. There is no branch here that dresses a
 * failure as a short answer, and no "try rephrasing" on a failure that
 * rephrasing cannot fix -- the terminal reason is shown with the support
 * reference, because that is what an operator needs and what the user can
 * act on.
 */

import { AnswerActions } from "./answer-actions";
import { claimDisplayLine } from "./claim-display";
import { API_PREFIX, type FinalResponse } from "./client";
import { Markdown } from "./markdown.tsx";
import type { RunView } from "./reducer";

/**
 * A link to the exact rows a figure came from.
 *
 * The artifact endpoint is authenticated and tenant-checked, so this is a
 * link to evidence the reader is already entitled to see — not a public
 * export. It exists because "trust the number" is not the claim being made:
 * "here is the query output it was read from" is.
 */
function ArtifactLinks({
  runId,
  response,
}: {
  runId: string;
  response: FinalResponse;
}) {
  const ids = new Set<string>();
  for (const table of response.tables) {
    if (table.artifact_id) ids.add(table.artifact_id);
  }
  for (const claim of response.numeric_claims) {
    const id = (claim as { evidence?: { artifact_id?: string } }).evidence
      ?.artifact_id;
    if (id) ids.add(id);
    // A DERIVED claim -- a total, a share, a movement -- carries no single
    // cell reference; its evidence is the rows its derivation consumed.
    // Collecting only `evidence` would leave the reader of a calculated
    // figure with nothing to open.
    const derivation = (
      claim as { derivation?: { operands?: { artifact_id?: string }[] } }
    ).derivation;
    for (const operand of derivation?.operands ?? []) {
      if (operand.artifact_id) ids.add(operand.artifact_id);
    }
  }
  if (ids.size === 0) return null;

  return (
    <div className="mt-3" data-testid="v4-artifacts">
      <h4 className="text-xs font-medium uppercase tracking-wide text-text-muted">
        Evidence
      </h4>
      <ul className="mt-1 space-y-1">
        {[...ids].map((id) => (
          <li key={id}>
            <a
              href={`${API_PREFIX}/runs/${runId}/artifacts/${id}`}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs text-accent underline underline-offset-2"
            >
              {id}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

const DISPOSITION_LABEL: Record<string, string> = {
  answer: "Answer",
  partial_answer: "Partial answer",
  referral: "This belongs to another part of CreditProbe",
  clarification: "One question first",
  unsupported: "Not supported here",
  safe_failure: "Could not complete",
};

const COVERAGE_LABEL: Record<string, string> = {
  answered: "answered",
  partial: "partly answered",
  referred: "referred",
  needs_clarification: "needs clarification",
  unsupported: "not supported",
};

function Coverage({ response }: { response: FinalResponse }) {
  if (!response.coverage.length) return null;
  return (
    <div className="mt-3">
      <h4 className="text-xs font-medium uppercase tracking-wide text-text-muted">
        What was covered
      </h4>
      <ul className="mt-1 space-y-1 text-sm">
        {response.coverage.map((item, i) => (
          <li key={i} className="flex gap-2">
            <span
              className={
                item.status === "answered"
                  ? "text-positive"
                  : item.status === "unsupported"
                    ? "text-negative"
                    : "text-warning"
              }
              aria-hidden
            >
              •
            </span>
            <span className="text-text-secondary">
              {item.subquestion}{" "}
              <span className="text-text-muted">
                — {COVERAGE_LABEL[item.status] ?? item.status}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ResponsePanel({
  view,
  onAsk,
  question,
  threadId,
  showSuggestions = true,
}: {
  view: RunView;
  onAsk?: (question: string) => void;
  /** The question this answer belongs to, used to title a saved copy. */
  question?: string;
  threadId?: string;
  /** False in a thread, which renders the follow-ups above its composer. */
  showSuggestions?: boolean;
}) {
  if (!view.terminal) return null;

  const response = view.response;
  if (!response) {
    return (
      <section
        data-testid="v4-terminal-failure"
        className="rounded-lg border border-negative bg-negative-muted p-3"
      >
        <h3 className="text-sm font-medium text-negative">
          {view.state === "CANCELLED"
            ? "Cancelled"
            : view.state === "EXPIRED"
              ? "This request ran out of time"
              : view.state === "INTERRUPTED"
                ? "This request was interrupted"
                : "This request stopped"}
        </h3>
        <p className="mt-1 text-sm text-negative">
          {view.errorCode
            ? `Reason: ${view.errorCode}.`
            : "No answer was produced, and nothing was substituted for one."}
        </p>
        {view.errorId ? (
          <p
            data-testid="v4-support-reference"
            className="mt-1 text-xs text-negative"
          >
            Quote <span className="font-mono">{view.errorId}</span> to an
            operator. Rephrasing the question is unlikely to help.
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section
      data-testid="v4-response"
      data-disposition={response.disposition}
      className="rounded-lg border border-border p-3"
    >
      <h3 className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {DISPOSITION_LABEL[response.disposition] ?? response.disposition}
      </h3>

      {response.result_only ? (
        <div
          data-testid="v4-result-only-caveat"
          className="mt-2 rounded border border-warning bg-warning-muted p-2"
        >
          <p className="text-sm font-medium text-warning">
            The analysis ran. The written explanation did not.
          </p>
          <p className="mt-1 text-sm text-warning">
            {response.result_only_reason
              ? `${response.result_only_reason} The result below is the query's own output, computed and stored by CreditProbe, with no commentary on it.`
              : "The result below is the query's own output, computed and stored by CreditProbe, with no commentary on it."}
          </p>
        </div>
      ) : null}

      <div className="mt-2">
        <Markdown source={response.narrative} />
      </div>

      {response.disposition === "clarification" &&
      response.clarification_question ? (
        <div className="mt-3 rounded border border-accent bg-accent-muted p-2">
          <p className="text-sm text-accent">{response.clarification_question}</p>
          {response.clarification_options.length ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {response.clarification_options.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => onAsk?.(option)}
                  className="rounded border border-accent bg-surface px-2 py-1 text-xs text-accent"
                >
                  {option}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {response.disposition === "referral" && response.referral_owner ? (
        <p className="mt-2 text-sm text-text-secondary">
          {response.referral_reason}
        </p>
      ) : null}

      <Coverage response={response} />

      {response.numeric_claims.length ? (
        <div className="mt-3">
          <h4 className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Figures, and where each came from
          </h4>
          <ul className="mt-1 space-y-1 text-xs text-text-secondary">
            {response.numeric_claims.map((claim) => (
              <li key={claim.claim_id} className="font-mono">
                {claim.claim_id}: {claimDisplayLine(claim)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ArtifactLinks runId={view.runId} response={response} />

      {view.errorId ? (
        <p
          data-testid="v4-support-reference"
          className="mt-3 text-xs text-text-secondary"
        >
          Support reference{" "}
          <span className="font-mono">{view.errorId}</span>
        </p>
      ) : null}

      {response.limitations.length ? (
        <div className="mt-3">
          <h4 className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Limitations
          </h4>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-text-secondary">
            {response.limitations.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {/*
        Follow-ups are NOT rendered here in a conversation. They belong
        immediately above the box you would type the next question into --
        that is where a reader's eye and cursor already are, and a chip four
        screens up beside the answer is a chip nobody clicks. The thread
        passes `showSuggestions={false}` and renders them itself; a panel
        used outside a thread keeps them.
      */}
      {showSuggestions && response.suggested_questions.length && onAsk ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {response.suggested_questions.map((suggestion, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onAsk(suggestion.question)}
              className="rounded-full border border-border-strong px-3 py-1 text-xs text-text-secondary hover:bg-surface-sunken"
            >
              {suggestion.question}
            </button>
          ))}
        </div>
      ) : null}

      {/* Actions belong on an answer that exists, not on a failure and not
          on a clarification that is still a question. */}
      {view.runId && response.disposition !== "clarification" ? (
        <AnswerActions
          runId={view.runId}
          question={question ?? ""}
          threadId={threadId}
          tables={response.tables ?? []}
          charts={response.charts ?? []}
        />
      ) : null}
    </section>
  );
}
