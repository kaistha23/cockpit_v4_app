"use client";

/**
 * Conversations this reader can pick back up.
 *
 * Real V4 threads with at least one completed turn, from the V4 thread store.
 * A thread with nothing in it is not something to continue, and a landing
 * page with no history says so quietly rather than showing invented rows.
 */

import * as React from "react";

import { readSession, type RecentThread } from "./client";

function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const minutes = Math.round((Date.now() - at.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return at.toLocaleDateString();
}

export function ContinueWhereYouLeftOff({
  threads,
  onOpen,
}: {
  threads: RecentThread[];
  onOpen: (thread: RecentThread) => void;
}) {
  return (
    <section className="space-y-3" data-testid="continue-where-you-left-off">
      <h2 className="text-[11px] font-medium uppercase tracking-[0.12em] text-text-muted">
        Continue where you left off
      </h2>
      {threads.length ? (
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          {threads.map((thread) => (
            <button
              key={thread.thread_id}
              type="button"
              data-testid="continue-thread"
              data-thread-id={thread.thread_id}
              // Which book this conversation is in, so the browser suite
              // and a reader can both see it before clicking.
              data-domain={thread.domain_id ?? ""}
              onClick={() => onOpen(thread)}
              className="flex w-full items-baseline gap-3 border-b border-border px-5 py-3 text-left transition last:border-b-0 hover:bg-surface-sunken"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-text-primary" dir="auto">
                  {thread.attention_item || thread.last_question}
                </span>
                <span className="mt-0.5 block text-xs text-text-muted">
                  {thread.origin === "attention_item"
                    ? `From Requires attention${
                        thread.segment ? ` · ${thread.segment}` : ""
                      }`
                    : "Cockpit conversation"}
                  {" · "}
                  {thread.turns} {thread.turns === 1 ? "turn" : "turns"}
                </span>
              </span>
              <span className="shrink-0 text-xs text-text-muted">
                {when(thread.last_activity_at)}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p
          className="rounded-xl border border-dashed border-border px-5 py-6 text-sm text-text-muted"
          data-testid="continue-empty"
        >
          Nothing to continue yet. Investigations you start here will appear in
          this list.
        </p>
      )}
    </section>
  );
}

/** Reads the session once. Exported so the page owns the state, not this. */
export function useSession() {
  const [name, setName] = React.useState("");
  const [threads, setThreads] = React.useState<RecentThread[]>([]);
  const [ready, setReady] = React.useState(false);

  const refresh = React.useCallback(async () => {
    try {
      const session = await readSession();
      setName(session.display_name ?? "");
      setThreads(session.recent_threads ?? []);
    } catch {
      // A greeting without a name is correct; an invented one is not.
      setName("");
      setThreads([]);
    } finally {
      setReady(true);
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  return { name, threads, ready, refresh };
}
