"use client";

/**
 * The Cockpit thread: a conversation, not a page that answers once.
 *
 * The product problem this exists for
 * -----------------------------------
 * V4 could execute an analytical question and publish an evidence-bound
 * answer, and the experience was still a dashboard. You asked from a landing
 * page, one process panel appeared, one answer appeared, and to ask the
 * obvious next question you scrolled back up to the box you started in. There
 * was no transcript, so the second question felt like starting over, and the
 * answer read as the end of the interaction when the follow-up is the point.
 *
 * So: a thread. The question sits at the top, the process runs underneath it,
 * the answer appends below that, and the composer is waiting where your eye
 * already is. Ask again and the exchange appends -- it does not replace what
 * came before. Five questions render as five exchanges.
 *
 * What did NOT come back with the shape
 * --------------------------------------
 * None of the previous Cockpit's backend. No `/investigations`, no
 * `/agentic/officer`, no old Ask flow, no shim translating either. The run
 * lifecycle, the evidence model, the release binding and the numeric
 * rendering are V4's, unchanged by this file. This is the interaction the old
 * product got right, over the architecture the new one got right.
 *
 * Reloading is free
 * -----------------
 * The transcript is the server's. A refresh reads it back and re-follows only
 * a run that is genuinely still working -- it never re-asks, because asking
 * again spends the analysis twice and tells the reader nothing about the
 * first one.
 */

import * as React from "react";

import { comparisonPeriod, periodLabel, reportingPeriod } from "./period";

import {
  acknowledge,
  cancelRun,
  forgetRun,
  readStatus,
  readTrace,
  recallRun,
  rememberRun,
  readThread,
  renameThread,
  saveAnalysis,
  shareItem,
  createInvestigation,
  deliveryWording,
  type Notification,
  startRun,
  watch,
  type FinalResponse,
  type RunMode,
  type ThreadTranscript,
  type ThreadTurn,
} from "./client";
import { chipsToShow } from "./follow-ups";
import { AnswerActions } from "./answer-actions";
import { ProcessPanel } from "./process-panel";
import { initial, reduce, type RunView } from "./reducer";
import { ResponsePanel } from "./response-panel";
import { Visuals } from "./visuals";
import { RecipientPicker } from "./recipient-picker";

/** A run in one of these states is finished; there is nothing to follow. */
const TERMINAL_RUN_STATES = new Set([
  "COMPLETED", "PARTIAL", "FAILED", "EXPIRED", "CANCELLED", "UNSUPPORTED",
  "REFERRED", "WAITING_FOR_USER", "INTERRUPTED",
]);

/** One exchange already on the server. */
type Settled = {
  key: string;
  runId: string;
  question: string;
  answer: FinalResponse;
};

function settled(turn: ThreadTurn): Settled {
  return {
    key: turn.turn_id,
    runId: turn.run_id,
    question: turn.question,
    answer: turn.answer as FinalResponse,
  };
}

// ---- the pieces ---------------------------------------------------------

function UserTurn({ question }: { question: string }) {
  return (
    <div data-testid="v4-turn-user" className="flex justify-end">
      <p
        dir="auto"
        className="max-w-[48rem] rounded-2xl rounded-br-sm bg-accent px-4 py-2.5 text-sm text-accent-contrast"
      >
        {question}
      </p>
    </div>
  );
}

/**
 * The trace of a turn that has already finished. §28.
 *
 * The live panel shows a run while it happens. A reader coming back to a
 * past turn -- their own, an hour later, or after a reload -- had no way to
 * see the same thing, and "trust the answer" is not the claim being made.
 *
 * Loaded on demand, because a transcript of twenty turns should not fetch
 * twenty event streams to render. Nothing here costs a model call.
 */
function TurnTrace({ runId }: { runId: string }) {
  const [view, setView] = React.useState<RunView | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [failed, setFailed] = React.useState("");

  const open = React.useCallback(async () => {
    if (view || busy) {
      setView(null);
      return;
    }
    setBusy(true);
    setFailed("");
    try {
      const trace = await readTrace(runId);
      let next = reduce(initial(runId), { type: "start", runId });
      for (const event of trace.events) {
        next = reduce(next, { type: "event", event });
      }
      // Settled from the RECORD, not from whichever event happened to be
      // last: a stream that dropped its final frame must not leave a
      // finished run looking as though it is still working.
      next = {
        ...next,
        terminal: true,
        state: trace.state,
        errorCode: trace.error_code,
        elapsedIsAuthoritative: true,
        steps: next.steps.map((step) =>
          step.state === "running" ? { ...step, state: "done" as const } : step,
        ),
      };
      setView(next);
    } catch (cause) {
      setFailed(
        cause instanceof Error ? cause.message : "The trace could not be read.",
      );
    } finally {
      setBusy(false);
    }
  }, [busy, runId, view]);

  return (
    <div data-testid="v4-turn-trace">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="v4-view-trace"
          onClick={() => void open()}
          aria-expanded={Boolean(view)}
          className="text-xs font-medium text-accent hover:underline"
        >
          {busy ? "Reading trace…" : view ? "Hide trace" : "View trace"}
        </button>
        {/* WHAT THE STAGE LIST DOES NOT SAY. The trace above shows that
            the analysis ran; this shows what was understood, what query
            was written, what was refused and where each figure came
            from. Two different questions, so two different controls. */}
        <a
          data-testid="v4-view-governance"
          href={`/cockpit/trace/${encodeURIComponent(runId)}`}
          className="text-xs font-medium text-accent hover:underline"
        >
          How this was worked out
        </a>
      </div>
      {failed ? (
        <p className="mt-1 text-xs text-negative">{failed}</p>
      ) : null}
      {view ? (
        <div className="mt-2">
          <ProcessPanel view={view} />
        </div>
      ) : null}
    </div>
  );
}

function AssistantTurn({
  turn,
  onAsk,
}: {
  turn: Settled;
  onAsk: (question: string) => void;
}) {
  // The answer body is the SAME component the live run renders, driven by a
  // view reconstructed from the stored turn. A second renderer for answers
  // read back from the server would be a second set of rules about
  // dispositions, evidence links and suggested questions, and the two would
  // drift the first time either changed.
  const view = React.useMemo(
    () => ({
      ...initial(turn.runId),
      terminal: true,
      state: "COMPLETED",
      response: turn.answer,
    }),
    [turn.runId, turn.answer],
  );

  return (
    <div data-testid="v4-turn-assistant" className="space-y-4">
      <ResponsePanel
        view={view}
        question={turn.question}
        onAsk={onAsk}
        showSuggestions={false}
      />
      <Visuals tables={turn.answer.tables ?? []}
               charts={turn.answer.charts ?? []} runId={turn.runId} />
      <TurnTrace runId={turn.runId} />
    </div>
  );
}


/** What a seeded investigation already knows, before anything is asked. */
function SeedCard({
  context,
  threadId,
}: {
  context: ThreadTranscript["context"];
  threadId: string;
}) {
  const body = (context?.body ?? {}) as Record<string, unknown>;
  if (context?.kind !== "attention_item") return null;
  const rows: [string, string][] = [
    ["Segment", String(body.segment ?? "")],
    // A seed carries whichever calendar its book keeps, so read the
    // neutral field and fall back to whichever legacy key is filled.
    ["Period", [periodLabel(reportingPeriod(body as never)),
                periodLabel(comparisonPeriod(body as never))]
      .filter(Boolean).join(" vs ")],
    ["Measure", String(body.metric_label ?? body.metric ?? "")],
  ].filter(([, value]) => Boolean(value)) as [string, string][];

  return (
    <section
      // The same name the concept has always had. One testid for one thing:
      // this card IS the investigation context, wherever it is rendered.
      data-testid="investigation-context"
      data-thread-id={threadId}
      data-segment={String(body.segment ?? "")}
      className="rounded-lg border border-accent bg-accent-muted/60 px-4 py-3"
    >
      <p className="text-xs font-medium uppercase tracking-wide text-accent">
        Investigating
      </p>
      <p dir="auto" className="mt-1 text-sm font-medium text-text-primary">
        {String(body.headline ?? "")}
      </p>
      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-text-secondary">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-1.5">
            <dt className="text-text-muted">{label}</dt>
            <dd className="font-medium text-text-primary" dir="auto">{value}</dd>
          </div>
        ))}
      </dl>
      {body.issue ? (
        <p dir="auto" className="mt-2 max-w-[68ch] text-xs text-text-secondary">
          {String(body.issue)}
        </p>
      ) : null}
    </section>
  );
}

/**
 * What to ask next, where the next question gets typed.
 *
 * §24: the chips belong between the answer and the composer, not buried in
 * the answer panel above a chart and a table. A suggestion the reader has to
 * scroll back up to find is a suggestion that does not get used.
 *
 * These come from the LATEST answer. An older turn's follow-ups are answered
 * by the turns after it and offering them again is offering to go backwards.
 */
function FollowUps({
  questions,
  busy,
  onAsk,
}: {
  questions: string[];
  busy: boolean;
  onAsk: (question: string) => void;
}) {
  if (!questions.length) return null;
  // §34. An OPAQUE band, with the label on its own line.
  //
  // The strip used to sit in the transparent top of the composer's gradient
  // with `items-center`, so a chip that wrapped to two lines overlapped the
  // transcript showing through behind it, and the "Ask next" label collided
  // with the first chip at narrow widths. Three fixes, all of them the same
  // fix: give the strip its own background so nothing shows through it,
  // break the label onto its own row so it can never share a line with a
  // chip, and align the chips to the TOP of the row so a chip that wraps
  // grows downward into the band's own padding rather than over its
  // neighbour.
  return (
    <div
      data-testid="v4-followups"
      className="mb-3 rounded-xl border border-border bg-surface/95 px-3 py-2.5 shadow-[0_-4px_12px_-8px_rgba(15,23,42,0.25)] backdrop-blur-sm"
    >
      <span className="mb-1.5 block text-[11px] uppercase tracking-wide text-text-muted">
        Ask next
      </span>
      <div className="flex flex-wrap items-start gap-x-2 gap-y-2">
        {questions.map((question) => (
          <button
            key={question}
            type="button"
            data-testid="v4-followup-chip"
            disabled={busy}
            dir="auto"
            onClick={() => onAsk(question)}
            className="max-w-full rounded-full border border-border-strong bg-surface px-3 py-1.5 text-left text-xs leading-5 text-text-secondary hover:border-border-strong hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-50"
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer({
  onAsk,
  busy,
  mode,
  onMode,
  followUps,
}: {
  onAsk: (question: string) => void;
  busy: boolean;
  mode: RunMode;
  onMode: (mode: RunMode) => void;
  followUps: string[];
}) {
  const [text, setText] = React.useState("");
  const submit = () => {
    const question = text.trim();
    if (!question || busy) return;
    setText("");
    onAsk(question);
  };

  return (
    <div
      data-testid="v4-composer"
      className="sticky bottom-0 z-10 -mx-1 bg-gradient-to-t from-surface via-surface to-transparent px-1 pb-4 pt-6"
    >
      {/*
        Inside the sticky block, so the suggestions travel with the box they
        feed. A strip in normal flow above a sticky composer is a strip that
        scrolls away from it -- which is the same problem as burying it in
        the answer, arrived at differently.
      */}
      <FollowUps questions={followUps} busy={busy} onAsk={onAsk} />
      <div className="rounded-xl border border-border-strong bg-surface shadow-sm focus-within:border-border-strong">
        <textarea
          data-testid="v4-composer-input"
          dir="auto"
          rows={2}
          value={text}
          disabled={busy}
          placeholder="Ask a follow-up…"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter asks; Shift+Enter is a new line. The same contract every
            // chat surface has trained every reader to expect.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className="w-full resize-none rounded-xl px-4 py-3 text-sm text-text-primary outline-none disabled:bg-surface-sunken"
        />
        <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2">
          <label className="flex items-center gap-2 text-xs text-text-muted">
            <span>Depth</span>
            <select
              data-testid="v4-composer-mode"
              value={mode}
              onChange={(event) => onMode(event.target.value as RunMode)}
              className="rounded border border-border px-2 py-1 text-xs"
            >
              <option value="standard">Standard</option>
              <option value="deep">Deep</option>
            </select>
          </label>
          <button
            type="button"
            data-testid="v4-composer-send"
            onClick={submit}
            disabled={busy || !text.trim()}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:bg-border-strong"
          >
            {busy ? "Working…" : "Ask"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * What you can do with the conversation, not with one answer in it.
 *
 * These act on the LATEST answered turn, because that is what a colleague
 * opening a shared link needs to land on and what an investigation entry
 * should point at. The per-answer actions further down the transcript still
 * act on their own turn; this is the shortcut for the one you just read.
 *
 * "Add to Project" is deliberately absent. Projects are a main-CreditProbe
 * surface and this isolated V4 runtime does not serve them, so there is no
 * endpoint behind a button here. A button that looks like it works and does
 * not is worse than the sentence saying so. Investigations ARE V4-native and
 * are offered here instead.
 */
function ThreadActions({
  runId,
  threadId,
  title,
}: {
  runId: string;
  threadId: string;
  title: string;
}) {
  const [panel, setPanel] = React.useState<"" | "share" | "investigate">("");
  const [audience, setAudience] = React.useState("");
  const [recipient, setRecipient] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [status, setStatus] = React.useState("");
  const [notification, setNotification] =
    React.useState<Notification | null>(null);
  const [notified, setNotified] = React.useState(false);

  const run = React.useCallback(async (work: () => Promise<string>) => {
    setBusy(true);
    setStatus("");
    try {
      setStatus(await work());
    } catch (cause) {
      setStatus(
        cause instanceof Error
          ? `That did not go through: ${cause.message}`
          : "That did not go through.",
      );
    } finally {
      setBusy(false);
    }
  }, []);

  if (!runId) return null;
  const label = title || "Cockpit conversation";

  return (
    <div data-testid="v4-thread-actions" className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="v4-thread-share"
          aria-pressed={panel === "share"}
          onClick={() => setPanel(panel === "share" ? "" : "share")}
          className="rounded border border-border-strong px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-sunken"
        >
          Share
        </button>
        <button
          type="button"
          data-testid="v4-thread-investigate"
          aria-pressed={panel === "investigate"}
          onClick={() => setPanel(panel === "investigate" ? "" : "investigate")}
          className="rounded border border-border-strong px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-sunken"
        >
          Add to investigation
        </button>
        <a
          data-testid="v4-thread-trace"
          // `/cockpit/trace`, not `/trace`. The latter is the legacy
          // reasoning map, which does `Number(runId)` on a `run-<hex>` id
          // and gets NaN -- so this button had been dead since V4 shipped.
          href={`/cockpit/trace/${encodeURIComponent(runId)}`}
          className="rounded border border-border-strong px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-sunken"
        >
          Trace
        </a>
      </div>

      {panel === "share" ? (
        <div className="mt-2 max-w-md space-y-2" data-testid="v4-thread-share-panel">
          <p className="text-xs text-text-muted">
            Shares the latest answer in this conversation.
          </p>
          <label className="block text-xs text-text-secondary">
            Audience (who this is shared with)
            <input
              data-testid="v4-thread-share-audience"
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          {/* THE PART THAT ACTUALLY REACHES SOMEBODY. The audience above
              records who the share is for; this sends them the message. */}
          <RecipientPicker value={recipient} onChange={setRecipient} />
          <label className="block text-xs text-text-secondary">
            Notify by email (optional)
            <input
              data-testid="v4-thread-share-email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-1 w-full rounded border border-border-strong px-2 py-1 text-sm"
            />
          </label>
          <button
            type="button"
            data-testid="v4-thread-share-submit"
            disabled={busy || !audience.trim()}
            onClick={() =>
              void run(async () => {
                const saved = await saveAnalysis({
                  run_id: runId, title: label.slice(0, 120),
                });
                const result = await shareItem({
                  subject_kind: "saved_analysis",
                  subject_id: saved.saved_id,
                  audience_id: audience.trim(),
                  notify_in_app: recipient.trim(),
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
              data-testid="v4-thread-share-delivery"
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

      {panel === "investigate" ? (
        <div
          className="mt-2 max-w-md space-y-2"
          data-testid="v4-thread-investigate-panel"
        >
          <p className="text-xs text-text-muted">
            Opens an investigation for this conversation.
          </p>
          <button
            type="button"
            data-testid="v4-thread-investigate-submit"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const record = await createInvestigation({
                  title: label.slice(0, 120),
                  origin: "cockpit_thread",
                  thread_id: threadId,
                });
                return `Investigation opened: ${record.title}.`;
              })
            }
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            Open investigation
          </button>
          <p className="text-xs text-text-muted" data-testid="v4-thread-no-projects">
            Add to Project is not available in this isolated Cockpit V4
            runtime. Projects are served by the main CreditProbe backend,
            which this instance does not start, so nothing here would reach
            one. Investigations are the V4-native equivalent.
          </p>
        </div>
      ) : null}

      {status ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="v4-thread-action-status">
          {status}
        </p>
      ) : null}
    </div>
  );
}

function ThreadHeader({
  title,
  named,
  turnCount,
  latestRunId,
  threadId,
  domainId,
  domainLabel,
  onRename,
  onHome,
}: {
  title: string;
  /**
   * Whether the server has said what this conversation is called yet.
   *
   * Separate from `title` being empty, because those are different facts
   * and were being rendered as the same one. See the header below.
   */
  named: boolean;
  turnCount: number;
  latestRunId: string;
  threadId: string;
  domainId: string;
  domainLabel: string;
  onRename: (title: string) => void;
  onHome: () => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(title);
  React.useEffect(() => setDraft(title), [title]);

  return (
    <header
      data-testid="v4-thread-header"
      className="border-b border-border pb-4"
    >
      {/*
        §22: the way back is at the TOP LEFT, above the title, where every
        reader of every application already looks for it. It used to be a
        small button on the far right of the header row, level with Rename,
        which is where a reader looks for actions ON this conversation and
        not for the exit from it.
      */}
      <button
        type="button"
        data-testid="v4-back-to-cockpit"
        onClick={onHome}
        className="-ml-1 mb-2 inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-sm text-text-muted hover:bg-surface-sunken hover:text-text-primary"
      >
        <span aria-hidden="true">←</span> Cockpit
      </button>

      <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        {editing ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const next = draft.trim();
              if (next) onRename(next);
              setEditing(false);
            }}
          >
            <input
              data-testid="v4-thread-title-input"
              autoFocus
              dir="auto"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="w-[28rem] max-w-full rounded border border-border-strong px-2 py-1 text-lg"
            />
            <button
              type="submit"
              data-testid="v4-thread-rename-save"
              className="rounded bg-accent px-3 py-1 text-xs text-accent-contrast"
            >
              Save
            </button>
          </form>
        ) : named ? (
          <h1
            data-testid="v4-thread-title"
            dir="auto"
            className="truncate text-xl font-semibold text-text-primary"
          >
            {title || "New conversation"}
          </h1>
        ) : (
          /*
            "New conversation" means this conversation has no name. It was
            also what a reader saw for the moment between a refresh and the
            transcript arriving, because `transcript?.title ?? ""` collapses
            "not loaded yet" and "loaded, unnamed" into the same empty
            string. So a thread the reader had named appeared, briefly, to
            have been renamed back on every reload.

            A heading is a claim about what something is called. While the
            server has not said, the honest heading is that we are still
            asking -- and it carries a DIFFERENT test id, so a test reading
            the title waits for a title rather than racing the fetch and
            quoting the placeholder.
          */
          <h1
            data-testid="v4-thread-title-loading"
            className="truncate text-xl font-semibold text-text-muted"
          >
            Opening this conversation…
          </h1>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-muted">
          {/*
            §35: which book this conversation is held in, stated rather than
            inferred. A reader with a corporate and a retail thread open
            should not have to read the answers to tell them apart.
          */}
          {domainLabel ? (
            <span
              data-testid="v4-thread-domain"
              data-domain={domainId}
              className="rounded-full border border-border-strong px-2 py-0.5 text-[11px] font-medium text-text-secondary"
            >
              {domainLabel}
            </span>
          ) : null}
          <span data-testid="v4-thread-turns">
            {turnCount} message{turnCount === 1 ? "" : "s"}
          </span>
        </p>
        <ThreadActions
          runId={latestRunId}
          threadId={threadId}
          title={title}
        />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {/* Offered once there is a name to change. Before the transcript
            lands the draft would open empty, and saving it would replace a
            name the reader had not seen with nothing. */}
        <button
          type="button"
          data-testid="v4-thread-rename"
          disabled={!named}
          onClick={() => setEditing((prev) => !prev)}
          className="rounded border border-border-strong px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-sunken disabled:opacity-50"
        >
          Rename
        </button>
      </div>
      </div>
    </header>
  );
}

// ---- the thread ---------------------------------------------------------

export function CockpitV4Thread({
  threadId,
  onHome,
}: {
  threadId: string;
  onHome: () => void;
}) {
  const [transcript, setTranscript] = React.useState<ThreadTranscript | null>(
    null,
  );
  const [loadError, setLoadError] = React.useState("");
  const [live, setLive] = React.useState<
    { question: string; runId: string } | null
  >(null);
  const [view, dispatch] = React.useReducer(reduce, initial());
  const [mode, setMode] = React.useState<RunMode>("standard");
  const [error, setError] = React.useState("");
  const resumed = React.useRef(false);
  const bottom = React.useRef<HTMLDivElement | null>(null);

  const load = React.useCallback(async () => {
    try {
      setTranscript(await readThread(threadId));
    } catch (cause) {
      setLoadError(
        cause instanceof Error ? cause.message : "This conversation could not be opened.",
      );
    }
  }, [threadId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  /**
   * Follow a run that is already going: attach the stream, settle, reload.
   *
   * Shared by asking and by RESUMING after a refresh, because the two are the
   * same thing from the moment the run id exists -- and a resumed run that
   * followed a different code path would be a second set of rules about
   * cursors, settlement and acknowledgement.
   */
  const follow = React.useCallback(
    (runId: string) =>
      watch(runId, {
        onEvent: (event) => dispatch({ type: "event", event }),
        onConnectionState: (state) =>
          dispatch({ type: "connection", state }),
        onSettled: async () => {
          try {
            const status = await readStatus(runId);
            dispatch({ type: "settled", status });
            await acknowledge(runId).catch(() => undefined);
          } catch {
            /* the transcript reload below is the authority anyway */
          }
          forgetRun();
          await load();
        },
      }),
    [load],
  );

  const ask = React.useCallback(
    async (question: string) => {
      if (!question.trim()) return;
      setError("");
      // The previous run's panel gives way to this one. Its exchange is
      // already in the transcript by now, so the conversation keeps it.
      await load();
      setLive({ question, runId: "" });
      dispatch({ type: "start", runId: "" });
      try {
        const started = await startRun({ question, mode, threadId });
        setLive({ question, runId: started.run_id });
        dispatch({ type: "start", runId: started.run_id });
        // The pointer a refresh picks the run back up from. The transcript
        // itself is the server's; this is only "which run is still working".
        rememberRun({
          runId: started.run_id, threadId, cursor: 0, question,
        });
        // The live turn STAYS once the answer arrives. Its process panel is
        // where the stages, their timings and any failed attempt live, and
        // clearing it the moment the answer landed took the trace off the
        // screen at exactly the point a reader wants to check it (§56). The
        // turn it belongs to is filtered out of the reloaded transcript, so
        // the exchange is drawn once.
        return follow(started.run_id);
      } catch (cause) {
        setLive(null);
        setError(
          cause instanceof Error ? cause.message : "The request was not accepted.",
        );
      }
      return undefined;
    },
    [load, mode, threadId],
  );

  /**
   * Pick a run back up after a refresh.
   *
   * A run outlives this page. Reloading while one is working must reconnect
   * to THAT run rather than ask again -- asking again spends the analysis
   * twice and tells the reader nothing about the first one. The status is
   * read first, so a run that settled while the browser was away is settled
   * here rather than followed into a stream that has already ended.
   */
  React.useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    const active = recallRun();
    if (!active || active.threadId !== threadId) return;
    let stop: (() => void) | undefined;
    void (async () => {
      try {
        const status = await readStatus(active.runId);
        if (TERMINAL_RUN_STATES.has(status.state)) {
          forgetRun();
          return;
        }
        // The reader's own words, from the pointer that was written when
        // the run started. A turn that renders blank above a visibly
        // working panel reads as a bug in the conversation.
        setLive({ question: active.question ?? "", runId: active.runId });
        dispatch({ type: "start", runId: active.runId });
        stop = follow(active.runId);
      } catch {
        forgetRun();
      }
    })();
    return () => stop?.();
  }, [follow, threadId]);

  React.useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [transcript?.turn_count, live?.question, view.terminal]);

  const rename = React.useCallback(
    async (title: string) => {
      try {
        const renamed = await renameThread(threadId, title);
        setTranscript((prev) =>
          prev ? { ...prev, title: renamed.title } : prev,
        );
      } catch {
        /* a rename that did not take is not shown as if it had */
      }
    },
    [threadId],
  );

  // The exchange that is still on screen as a live run is not drawn a second
  // time from the transcript. Both are the same question and the same answer;
  // the live one additionally carries its process panel.
  const turns = (transcript?.turns ?? [])
    .filter((turn) => !live || turn.run_id !== live.runId)
    .map(settled);
  // "Busy" is about whether a NEW question can be asked, which a finished run
  // does not prevent -- the panel simply stays on screen.
  const busy = Boolean(live) && !view.terminal;

  // The follow-ups belong to the NEWEST answer: the live one once it has
  // settled, otherwise the last stored turn. While a run is working there
  // are none, because the suggestions that were on screen belong to a
  // question already being followed up.
  const latest = live && view.terminal
    ? view.response
    : turns.length
      ? turns[turns.length - 1].answer
      : null;
  // §31/§32. See `chipsToShow`: the answer's own suggestions, else the
  // book's deterministic fallbacks, else -- while the thread is still empty
  // -- the questions it was opened on.
  const followUps = chipsToShow({
    busy,
    answered: Boolean(latest),
    domainId: transcript?.domain_id ?? "",
    offered: latest?.suggested_questions,
    opening: transcript?.opening_questions,
  });

  if (loadError) {
    return (
      <div data-testid="v4-thread-error" className="mx-auto max-w-2xl py-16">
        <p className="text-sm text-negative">{loadError}</p>
        <button
          type="button"
          onClick={onHome}
          className="mt-3 text-sm text-accent hover:underline"
        >
          Back to Cockpit home
        </button>
      </div>
    );
  }

  return (
    <div
      data-testid="cockpit-v4-thread"
      data-thread-id={threadId}
      className="mx-auto flex w-full max-w-[90rem] flex-col px-4 py-8 sm:px-6 lg:px-10"
    >
      <ThreadHeader
        title={transcript?.title ?? ""}
        named={transcript !== null}
        turnCount={turns.length + (live ? 1 : 0)}
        domainId={transcript?.domain_id ?? ""}
        domainLabel={transcript?.domain_short_label ?? ""}
        latestRunId={
          // The newest ANSWERED run: the live one once it has settled,
          // otherwise the last turn already in the transcript. A run still
          // working has nothing to share yet.
          live && view.terminal && view.runId
            ? view.runId
            : turns.length
              ? turns[turns.length - 1].runId
              : ""
        }
        threadId={threadId}
        onRename={(title) => void rename(title)}
        onHome={onHome}
      />

      <div className="mt-6 space-y-10">
        <SeedCard context={transcript?.context ?? {}}
                  threadId={threadId} />

        {turns.map((turn) => (
          <article key={turn.key} className="space-y-4">
            <UserTurn question={turn.question} />
            <AssistantTurn turn={turn} onAsk={(q) => void ask(q)} />
          </article>
        ))}

        {live ? (
          <article className="space-y-4" data-testid="v4-turn-live">
            <UserTurn question={live.question} />
            <ProcessPanel
              view={view}
              onCancel={
                view.runId && !view.terminal
                  ? () => void cancelRun(view.runId).catch(() => undefined)
                  : undefined
              }
              onRetryStatus={
                view.runId
                  ? () =>
                      void readStatus(view.runId)
                        .then((status) =>
                          dispatch({ type: "settled", status }),
                        )
                        .catch(() => undefined)
                  : undefined
              }
            />
            {/*
              The answer lands HERE, under the process panel that produced
              it, and a run that stopped shows the stop in the same place.
              §55: one failed exchange does not wipe the conversation it
              happened in -- the turns before it are untouched, and the
              composer below is still available.
            */}
            {view.terminal ? (
              // The same turn marker a settled exchange carries. This IS
              // the assistant's turn -- it simply still has the process
              // panel that produced it attached above.
              <div data-testid="v4-turn-assistant" className="space-y-4">
                <ResponsePanel
                  view={view}
                  question={live.question}
                  threadId={threadId}
                  onAsk={(q) => void ask(q)}
                  showSuggestions={false}
                />
                {view.response ? (
                  <Visuals
                    tables={view.response.tables ?? []}
                    charts={view.response.charts ?? []}
                    runId={view.runId}
                  />
                ) : null}
              </div>
            ) : null}
          </article>
        ) : null}

        {error ? (
          <p data-testid="v4-thread-ask-error" className="text-sm text-negative">
            {error}
          </p>
        ) : null}
      </div>

      <div ref={bottom} />
      <Composer
        onAsk={(question) => void ask(question)}
        busy={busy}
        mode={mode}
        onMode={setMode}
        followUps={followUps}
      />
    </div>
  );
}
