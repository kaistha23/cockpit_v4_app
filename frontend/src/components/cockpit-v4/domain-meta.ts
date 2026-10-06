/**
 * What a book IS, read from the book rather than assumed.
 *
 * The Cockpit serves two analytical books on two calendars: the Corporate
 * book reports QUARTERS and the Retail book reports MONTHS. Everything a
 * reader sees above the numbers -- the cover line under the domain switch,
 * the suggested questions beside the Ask box -- names that calendar, and a
 * component that spells it out in its own JSX names whichever one was true
 * on the day it was written.
 *
 * That is not hypothetical. Home carried the literal
 * `Saudi Arabia · SAR million · monthly` while, eight inches below it, the
 * attention section read `Reporting quarter Q2 2026` off the server -- the
 * same page describing the same book on two calendars, and the wrong one
 * first.
 *
 * So the frequency, the currency, the scale and the country come from ONE
 * place: the `/domains` payload the server already sends, which carries them
 * per book from the published release. This module is the only reader of
 * those fields, it is plain TypeScript so a unit test can run it, and no V4
 * component states a calendar of its own.
 */

import type { DomainAvailability, DomainId, DomainStatus } from "./client";
import { periodNoun } from "./period.ts";

/** The metadata the server published for one book, or nothing.
 *
 * Nothing is a real answer here: it is the state between the page mounting
 * and `/domains` returning, and a caller that invents a calendar to fill it
 * is the defect this module exists to remove.
 */
export function domainEntry(
  availability: DomainAvailability | null | undefined,
  domain: DomainId | null | undefined,
): DomainStatus | null {
  if (!availability || !domain) return null;
  return (availability.domains ?? []).find(
    (entry) => entry?.domain_id === domain,
  ) ?? null;
}

/**
 * "quarter" or "month" for this book, or "period" when the book has not
 * said yet.
 *
 * Three sources in order of authority: what the release states outright,
 * what its declared frequency implies, and -- last -- the SHAPE of the
 * period it publishes. The third is inference, but inference from that
 * book's own data rather than from which button is currently pressed.
 */
export function domainNoun(entry: DomainStatus | null | undefined): string {
  const stated = (entry?.period_noun ?? "").trim();
  if (stated) return stated;
  const frequency = (entry?.reporting_frequency ?? "").trim().toLowerCase();
  if (frequency === "quarterly") return "quarter";
  if (frequency === "monthly") return "month";
  return periodNoun({ reporting_period: entry?.latest_period ?? "" });
}

/** "quarterly" or "monthly" for this book, or "" when it has not said. */
export function domainFrequency(
  entry: DomainStatus | null | undefined,
): string {
  const stated = (entry?.reporting_frequency ?? "").trim();
  if (stated) return stated;
  const noun = domainNoun(entry);
  if (noun === "quarter") return "quarterly";
  if (noun === "month") return "monthly";
  return "";
}

/**
 * The cover line under the domain switch: where the book is, what it counts
 * in, and how often it reports.
 *
 * Assembled from what the release published, and a part the release did not
 * publish is left out rather than guessed. An empty string is the correct
 * output before `/domains` answers -- a blank line for a moment is not a
 * claim, and `monthly` under a quarterly book is.
 */
export function domainHeadline(
  availability: DomainAvailability | null | undefined,
  domain: DomainId | null | undefined,
): string {
  const entry = domainEntry(availability, domain);
  if (!entry) return "";
  const money = [entry.reporting_currency, entry.amount_scale]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" ");
  return [(entry.country ?? "").trim(), money, domainFrequency(entry)]
    .filter(Boolean)
    .join(" · ");
}

/**
 * What each book is worth asking, written once per book.
 *
 * Per domain, because a retail book has no sectors and a corporate one has
 * no behavioural score bands -- a chip offering either to the wrong book is
 * a question that cannot be answered, and a reader who clicks one learns
 * that the suggestions are decoration.
 *
 * `{period}` is the book's own period noun. It is a placeholder rather than
 * a word because these chips are the question the reader actually sends:
 * "Show EAD by sector for the latest month." asked of a book that reports
 * quarters is a question with no answer in it, and the reader is the one who
 * finds that out.
 *
 * Deterministic. Rendering these costs no model call.
 */
export const PROMPT_TEMPLATES: Record<DomainId, readonly string[]> = {
  corporate: [
    "What is driving Stage 2 and ECL growth?",
    "Which sectors deteriorated most this {period}?",
    "Show EAD by sector for the latest {period}.",
    "Which borrowers were downgraded this {period}?",
    "Why is risk building across the corporate book?",
  ],
  retail: [
    "Which products saw the largest Stage 2 increase this {period}?",
    "Where is delinquency building?",
    "Which behavioural score bands deteriorated most?",
    "Show retail EAD by product for the latest {period}.",
    "Why is risk building across the retail book?",
  ],
};

/** The chips for one book, on that book's calendar. */
export function promptsFor(
  availability: DomainAvailability | null | undefined,
  domain: DomainId | null | undefined,
): string[] {
  const chosen: DomainId = domain ?? "corporate";
  const noun = domainNoun(domainEntry(availability, chosen));
  return (PROMPT_TEMPLATES[chosen] ?? PROMPT_TEMPLATES.corporate).map(
    (template) => template.replaceAll("{period}", noun),
  );
}
