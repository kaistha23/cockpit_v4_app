"use client";

/**
 * What a colleague sent you.
 *
 * There was no inbox. `readOutbox` existed and nothing called it, and what
 * it returns is the tenant's whole outbox without the message bodies --
 * what an operator wants, and the opposite of what a person reading their
 * own post wants. So an analysis shared with a credit officer reached a
 * database row and stopped.
 *
 * Every message here is one the server says was DELIVERED, or one it
 * recorded and said it could not deliver -- and the difference is shown in
 * the same words the server used, never softened. A panel that rendered
 * "sent" over a message that sat in an outbox is the specific failure the
 * whole collaboration module is written to avoid.
 */

import * as React from "react";

import { deliveryWording, readInbox, type InboxMessage } from "./client";

function Message({ message }: { message: InboxMessage }) {
  return (
    <li data-testid="v4-inbox-message"
        data-delivered={message.delivered ? "true" : "false"}
        className="rounded border border-border bg-surface p-3">
      <p className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-medium text-text-primary">
          {message.subject}
        </span>
        <span className="text-xs text-text-muted">
          from {message.actor_id || "a colleague"}
        </span>
      </p>
      <p className="mt-1 text-sm text-text-secondary" dir="auto">
        {message.body}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {message.subject_kind === "saved_analysis" && message.subject_id ? (
          <a
            data-testid="v4-inbox-open"
            href={`/cockpit/saved/${encodeURIComponent(message.subject_id)}`}
            className="text-xs font-medium text-accent hover:underline"
          >
            Open the analysis
          </a>
        ) : null}
        {/* The server's own sentence about what happened to this message.
            `deliveryWording` is the only thing allowed to claim delivery
            and it reaches that wording only when the server said so. */}
        <span className={`text-xs ${
          message.delivered ? "text-text-muted" : "text-warning"}`}>
          {deliveryWording(message)}
        </span>
      </div>
    </li>
  );
}

export function InboxPanel() {
  const [messages, setMessages] = React.useState<InboxMessage[] | null>(null);
  const [problem, setProblem] = React.useState("");

  React.useEffect(() => {
    let live = true;
    readInbox()
      .then((body) => { if (live) setMessages(body.messages); })
      .catch(() => {
        if (live) setProblem("Your messages could not be read.");
      });
    return () => { live = false; };
  }, []);

  if (problem) {
    return (
      <p data-testid="v4-inbox-problem" className="text-sm text-negative">
        {problem}
      </p>
    );
  }
  if (messages === null) {
    return (
      <p className="text-sm text-text-muted">Reading your messages…</p>
    );
  }
  if (!messages.length) {
    return (
      <p data-testid="v4-inbox-empty" className="text-sm text-text-muted">
        Nobody has sent you an analysis yet.
      </p>
    );
  }
  return (
    <ul data-testid="v4-inbox" className="space-y-2">
      {messages.map((message) => (
        <Message key={message.notification_id} message={message} />
      ))}
    </ul>
  );
}

/** How many unread-ish messages there are, for the header. */
export function useInboxCount(): number {
  const [count, setCount] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    readInbox()
      .then((body) => { if (live) setCount(body.messages.length); })
      .catch(() => { /* A bell that cannot count says nothing. */ });
    return () => { live = false; };
  }, []);
  return count;
}
