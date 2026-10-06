/**
 * The last mile of §17: what arrived is about the book that was asked for.
 *
 * Every panel here fetches per book and refetches when the book changes. The
 * hazard is the one no component can see on its own: two fetches in flight,
 * the reader switches back, and the SLOWER one lands last. The effect's own
 * cleanup catches the common case -- a component that has moved on ignores
 * its stale response -- but it cannot catch a response that arrives for the
 * right component and the wrong book.
 *
 * So the body says which book it is about, and the panel checks. A payload
 * that does not match is dropped rather than rendered: a Retail dashboard
 * under a Corporate heading is the one failure mode where everything on
 * screen looks correct.
 */

import type { DomainId } from "./client";

/** Whether a server payload is about `domain`. */
export function belongsTo(
  body: { domain_id?: string } | null | undefined,
  domain: DomainId | undefined,
): boolean {
  if (!body) return false;
  // A panel that was not told which book it is in takes what it is given:
  // the server resolved the default, and there is nothing to disagree with.
  if (!domain) return true;
  const named = String(body.domain_id ?? "").trim();
  // A payload that names no book at all is from an older server. It is not
  // evidence of a mismatch, and refusing it would blank a working panel.
  if (!named) return true;
  return named === domain;
}

/**
 * The payload to render, or null when it is about another book.
 *
 * Written as a function rather than an inline comparison so that the rule
 * has one definition and one test, and so a new panel picks it up by
 * calling it rather than by remembering to write the comparison again.
 */
export function forDomain<T extends { domain_id?: string }>(
  body: T | null | undefined,
  domain: DomainId | undefined,
): T | null {
  return belongsTo(body, domain) ? (body ?? null) : null;
}
