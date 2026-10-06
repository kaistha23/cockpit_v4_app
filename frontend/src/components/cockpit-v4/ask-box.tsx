"use client";

/**
 * The Ask surface as the landing page wants it: wide, primary, and first.
 *
 * The earlier Cockpit put one large question box across the workspace with a
 * few business prompts under it, and that is the shape restored here. What is
 * NOT restored is any of the backend behind it — this posts to the V4 run API
 * and nothing else.
 *
 * The process panel appears when a run exists and not before. An idle landing
 * page reserves no space for it, because a blank frame where the work will go
 * is not information.
 */

import * as React from "react";

import type { DomainAvailability, DomainId, RunMode } from "./client";
import { promptsFor } from "./domain-meta";

/**
 * The suggested questions, on the calendar of the book they are offered in.
 *
 * The wording lives in `domain-meta`, which fills the period noun from the
 * `/domains` payload. It used to live here as two literal lists, and the
 * corporate one said "this month" about a book that reports quarters --
 * which is a chip that sends a question the book cannot answer.
 */
export { PROMPT_TEMPLATES } from "./domain-meta";

const MODES: { id: RunMode; label: string; hint: string }[] = [
  {
    id: "standard",
    label: "Standard",
    hint: "Enough for most questions.",
  },
  {
    id: "deep",
    label: "Deep",
    hint: "A longer allowance, for work that needs it.",
  },
];

export function AskBox({
  question,
  onQuestionChange,
  mode,
  onModeChange,
  onAsk,
  busy,
  showPrompts,
  onDismissPrompts,
  domain,
  domains,
  models = [],
  modelsStatus = "ready",
  selectedModel = "",
  onModelChange,
}: {
  domain?: DomainId;
  /** The books this runtime published, with each one's own calendar. The
   *  chips are written from it; without it they name no period at all. */
  domains?: DomainAvailability | null;
  models?: { provider: string; model: string; label: string; available: boolean; qualified: boolean }[];
  modelsStatus?: "loading" | "ready" | "error";
  selectedModel?: string;
  onModelChange?: (value: string) => void;
  question: string;
  onQuestionChange: (value: string) => void;
  mode: RunMode;
  onModeChange: (mode: RunMode) => void;
  onAsk: (text: string) => void;
  busy: boolean;
  showPrompts: boolean;
  onDismissPrompts: () => void;
}) {
  return (
    <form
      data-testid="cockpit-v4-ask-box"
      onSubmit={(event) => {
        event.preventDefault();
        onAsk(question);
      }}
      className="w-full rounded-xl border border-border bg-surface shadow-sm"
    >
      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center">
        <label className="sr-only" htmlFor="cockpit-v4-question">
          Ask the Cockpit
        </label>
        <input
          id="cockpit-v4-question"
          data-testid="cockpit-v4-question"
          dir="auto"
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          placeholder="What deteriorated this period?"
          className="w-full flex-1 bg-transparent text-base text-text-primary outline-none placeholder:text-text-muted"
        />
        <div className="flex shrink-0 items-center gap-3 self-end sm:self-auto">
          <span className="hidden text-xs text-text-muted sm:inline">
            Enter to ask
          </span>
          <button
            type="submit"
            data-testid="cockpit-v4-ask"
            disabled={busy || !question.trim()}
            className="rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-contrast transition hover:bg-accent-hover disabled:opacity-40"
          >
            ↵ Ask
          </button>
        </div>
      </div>

      {showPrompts ? (
        <div
          data-testid="cockpit-v4-prompts"
          className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3"
        >
          {promptsFor(domains, domain).map((prompt) => (
            <button
              key={prompt}
              type="button"
              data-testid="cockpit-v4-prompt"
              disabled={busy}
              onClick={() => onAsk(prompt)}
              className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition hover:border-border-strong hover:bg-surface-sunken disabled:opacity-40"
            >
              {prompt}
            </button>
          ))}
          <button
            type="button"
            aria-label="Hide suggested questions"
            data-testid="cockpit-v4-prompts-dismiss"
            onClick={onDismissPrompts}
            className="ml-auto rounded px-2 py-1 text-text-muted hover:bg-surface-sunken"
          >
            ✕
          </button>
        </div>
      ) : null}

      <fieldset
        className="flex flex-wrap items-center gap-3 border-t border-border px-5 py-2.5"
        disabled={busy}
      >
        <legend className="sr-only">Analysis depth</legend>
        {MODES.map((option) => (
          <label
            key={option.id}
            className="inline-flex items-center gap-1.5 text-xs text-text-secondary"
          >
            <input
              type="radio"
              name="cockpit-v4-mode"
              data-testid={`cockpit-v4-mode-${option.id}`}
              value={option.id}
              checked={mode === option.id}
              onChange={() => onModeChange(option.id)}
            />
            {option.label}
          </label>
        ))}
        <span className="text-xs text-text-muted">
          {MODES.find((m) => m.id === mode)?.hint}
        </span>
        {onModelChange ? (
          <select aria-label="AI model" data-testid="cockpit-v4-model" value={selectedModel}
            disabled={modelsStatus !== "ready" || models.length === 0}
            onChange={(event) => onModelChange(event.target.value)}
            className="ml-auto rounded border border-border bg-surface px-2 py-1 text-xs">
            <option value="">{modelsStatus === "loading" ? "Loading models…"
              : modelsStatus === "error" ? "Models unavailable"
              : models.length === 0 ? "No models available" : "Select model"}</option>
            {Array.from(new Set(models.map((item) => item.label))).map((group) => {
              // Grouped by the label the server sends, so no provider name
              // is written into this source file (§12).
              const items = models.filter((item) => item.label === group);
              return (
                <optgroup key={group} label={group}>
                  {items.map((item) => <option key={`${item.provider}:${item.model}`}
                    value={`${item.provider}:${item.model}`}
                    disabled={!item.available || !item.qualified}>
                    {item.model}{!item.qualified ? " (not qualified)" : !item.available ? " (unavailable)" : ""}
                  </option>)}
                </optgroup>
              );
            })}
          </select>
        ) : null}
      </fieldset>
    </form>
  );
}
