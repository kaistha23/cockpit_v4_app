/**
 * What to offer the reader next, and where it comes from.
 *
 * §31/§32. Three sources, in order of authority, and never two at once:
 *
 *   1. the newest ANSWER's own suggestions, when it offered any;
 *   2. the book's deterministic fallbacks, when it did not -- a validated
 *      answer may legitimately carry none (the analyst is not required to
 *      invent them, and validation drops any naming a field the release does
 *      not hold), and the reader then reached the end of a good answer with
 *      nowhere to go, which is the state the strip exists to prevent;
 *   3. the thread's OPENING questions, while it is still empty. A thread
 *      opened from an attention card arrived as a headline and a blank box:
 *      the reader had just clicked a finding and was being asked to compose
 *      a question about it from scratch.
 *
 * None of this costs a model call. A suggestion that arrives a generation
 * later arrives after the reader has stopped looking for it.
 */

export const FALLBACK_FOLLOW_UPS: Record<string, string[]> = {
  corporate: [
    "Break this down by sector.",
    "Which borrowers drove this?",
    // In the book's own periods. A corporate reader asked for twelve months
    // of a book that publishes quarters, and got four points under a chip
    // that promised twelve.
    "How has this moved over the last eight quarters?",
    "Show the same figure by facility type.",
  ],
  retail: [
    "Break this down by product.",
    "Which customer segments drove this?",
    "How has this moved over the last twelve months?",
    "Show the same figure by region.",
  ],
};

/** How many chips the strip shows. Five before the first question, four
 * after one: an opening set is the whole menu, a follow-up set is a nudge. */
export const MAX_OPENING = 5;
export const MAX_FOLLOW_UPS = 4;

export type Suggestion = { question: string };

export function fallbackFollowUps(domainId: string): string[] {
  return FALLBACK_FOLLOW_UPS[domainId] ?? [];
}

function texts(suggestions: readonly Suggestion[] | undefined): string[] {
  return (suggestions ?? [])
    .map((s) => (s?.question ?? "").trim())
    .filter(Boolean);
}

export function chipsToShow({
  busy,
  answered,
  domainId,
  offered,
  opening,
}: {
  /** A run is in flight: the suggestions on screen belong to the question
   * being answered, so there are none. */
  busy: boolean;
  /** This thread has at least one answer in it. */
  answered: boolean;
  domainId: string;
  offered?: readonly Suggestion[];
  opening?: readonly Suggestion[];
}): string[] {
  if (busy) return [];
  if (!answered) return texts(opening).slice(0, MAX_OPENING);
  const own = texts(offered);
  return (own.length ? own : fallbackFollowUps(domainId)).slice(
    0,
    MAX_FOLLOW_UPS,
  );
}
