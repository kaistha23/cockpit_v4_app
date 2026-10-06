/**
 * How a reporting period is written for a reader.
 *
 * The Cockpit serves two analytical books on two different calendars: the
 * Corporate book reports QUARTERS (`2026Q2`) and the Retail book reports
 * MONTHS (`2026-08`). Nothing in the UI may assume one of them. A component
 * that hard-codes `quarterLabel(feed.reporting_quarter)` renders an empty
 * string the moment it is pointed at the other book, and an empty string in
 * a cover line is worse than a wrong one because nobody notices it.
 *
 * So there is exactly one function here, it takes whatever the server sent,
 * and it recognises the shape rather than being told it.
 */

const QUARTER = /^(\d{4})Q([1-4])$/;
const MONTH = /^(\d{4})-(\d{2})$/;

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/**
 * `2026Q2` reads as `Q2 2026`; `2026-08` reads as `Aug 2026`. Anything else
 * is returned untouched -- a label is not the place to discover that the
 * server sent something unexpected, and blanking it would hide the fact.
 */
export function periodLabel(period: string | null | undefined): string {
  const text = (period ?? "").trim();
  if (!text) return "";

  const quarter = QUARTER.exec(text);
  if (quarter) return `Q${quarter[2]} ${quarter[1]}`;

  const month = MONTH.exec(text);
  if (month) {
    const index = Number(month[2]) - 1;
    if (index >= 0 && index < 12) return `${MONTH_NAMES[index]} ${month[1]}`;
  }
  return text;
}

/**
 * The period a payload reports, preferring the calendar-neutral field.
 *
 * The older `reporting_quarter` / `reporting_month` pair is still served,
 * but only the key belonging to the payload's own book is ever filled, so
 * the fallback takes whichever one is non-empty instead of picking a side.
 */
export function reportingPeriod(body: {
  reporting_period?: string | null;
  reporting_quarter?: string | null;
  reporting_month?: string | null;
} | null | undefined): string {
  if (!body) return "";
  return (body.reporting_period || body.reporting_quarter
          || body.reporting_month || "").trim();
}

/** The period a payload compares against, under the same rule. */
export function comparisonPeriod(body: {
  comparison_period?: string | null;
  comparison_quarter?: string | null;
  comparison_month?: string | null;
} | null | undefined): string {
  if (!body) return "";
  return (body.comparison_period || body.comparison_quarter
          || body.comparison_month || "").trim();
}

/** "quarter" or "month", inferred from the period itself when not given. */
export function periodNoun(body: {
  period_noun?: string | null;
  reporting_period?: string | null;
  reporting_quarter?: string | null;
  reporting_month?: string | null;
} | null | undefined): string {
  const stated = (body?.period_noun ?? "").trim();
  if (stated) return stated;
  const period = reportingPeriod(body);
  if (QUARTER.test(period)) return "quarter";
  if (MONTH.test(period)) return "month";
  return "period";
}
