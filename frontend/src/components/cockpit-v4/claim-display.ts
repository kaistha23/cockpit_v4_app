/**
 * What a figure looks like on screen.
 *
 * The server publishes `display_value`: CreditProbe's rounding of
 * CreditProbe's arithmetic, the same string it substituted into the
 * narrative. That is the figure a credit officer reads.
 *
 * `decimal_value` is a different thing. It is the analyst's optional
 * cross-check, a lossless decimal kept at full precision so a correct
 * analysis is never refused over the fifteenth decimal place. Putting it on
 * screen shows a reader `7013.1167117986615 SAR million`, which is machine
 * precision reaching a person -- a defect whatever produced it.
 *
 * So: show the published string. When the server sent none, round the
 * cross-check to the precision the server declared rather than printing its
 * digits raw, and when there is nothing to show at all, show nothing.
 */

export type DisplayableClaim = {
  display_value?: string;
  decimal_value?: string;
  display_precision?: number;
  unit?: string;
};

/** Round half-away-from-zero at `places`, as the display policy does. */
function round(value: string, places: number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return n.toFixed(Math.max(0, places));
}

export function claimDisplayValue(claim: DisplayableClaim): string {
  const published = (claim.display_value ?? "").trim();
  if (published) return published;
  const raw = (claim.decimal_value ?? "").trim();
  if (!raw) return "";
  const places = claim.display_precision;
  if (typeof places !== "number" || places < 0) return "";
  return round(raw, places);
}


/**
 * The whole line for one figure: the value, and its unit only if the value
 * is not already written in it.
 *
 * `format_value` writes the unit INTO the string -- `SAR 7,013 million`,
 * `12.40%` -- so appending `claim.unit` beside it produces `SAR 7,013
 * million SAR million`. The fallback path has no unit in it, so there the
 * unit still has to be said.
 */
export function claimDisplayLine(claim: DisplayableClaim): string {
  const value = claimDisplayValue(claim);
  if (!value) return "";
  const unit = (claim.unit ?? "").trim();
  if (!unit) return value;
  const published = (claim.display_value ?? "").trim();
  return published ? value : `${value} ${unit}`;
}
