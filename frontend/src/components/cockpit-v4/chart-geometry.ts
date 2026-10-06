/**
 * Where a mark goes, and which mark the cursor is over.
 *
 * Arithmetic ABOUT positions, never about values. This file turns a
 * canonical number into a pixel and a pixel back into a point index; it
 * never rounds, re-scales or formats a figure, because the published
 * number is the server's and a second opinion about it is the one defect
 * this product cannot have.
 *
 * It lives in `.ts` rather than `.tsx` deliberately: the test runner is
 * `node --test --experimental-strip-types "src/**\/*.test.ts"` and does not
 * load `.tsx`, so geometry that sits in a component is geometry nothing
 * tests. The charts had no axes, no hover and no zoom, and all three are
 * mostly this arithmetic -- so this is where it goes.
 */

/** One number on an axis, chosen and WRITTEN by the server. */
export interface AxisTick {
  value: number;
  display: string;
}

/** A published axis. `ticks` is empty on a category axis: its categories
 *  are the points, and a second copy of those strings could drift. */
export interface Axis {
  kind: "measure" | "category" | string;
  label: string;
  column: string;
  unit: string;
  ticks: AxisTick[];
}

/** The plot area inside the frame, in the SVG's own units. */
export interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** A half-open window over the points, which is what zoom actually is. */
export interface Window {
  start: number;
  end: number;
}

export const FULL: Window = { start: 0, end: Number.POSITIVE_INFINITY };

/**
 * The low and high a chart is drawn between.
 *
 * The PUBLISHED axis wins. Falling back to the data is for a saved answer
 * rendered before axes existed -- a reader's own history must not stop
 * drawing because the contract grew.
 */
export function extentOf(
  axis: Axis | undefined,
  values: number[],
  options: { zeroBased?: boolean } = {},
): [number, number] {
  const ticks = (axis?.ticks ?? [])
    .map((tick) => tick.value)
    .filter((value): value is number => Number.isFinite(value));
  if (ticks.length >= 2) return [Math.min(...ticks), Math.max(...ticks)];
  const usable = values.filter((value) => Number.isFinite(value));
  if (!usable.length) return [0, 1];
  let low = Math.min(...usable);
  let high = Math.max(...usable);
  if (options.zeroBased) {
    low = Math.min(low, 0);
    high = Math.max(high, 0);
  }
  if (low === high) return [low, low + (Math.abs(low) || 1)];
  return [low, high];
}

/**
 * A coordinate as SVG path text.
 *
 * THE ONLY PLACE IN THE CHART LAYER THAT TURNS A NUMBER INTO A STRING,
 * and it is a POSITION rather than a published figure -- an SVG `d`
 * attribute needs digits and `x="12.3456789"` is bytes nobody reads.
 * `tests/frontend/test_chart_formats_nothing.py` forbids the rest, so
 * routing every coordinate through here is what keeps that guard honest
 * instead of speckling the components with exemptions.
 */
export function px(value: number): string {
  return value.toFixed(2); // not-a-published-figure: an SVG coordinate
}

/** A value's height in the box, given the scale it is drawn against. */
export function yOf(value: number, low: number, high: number, box: Box): number {
  const span = high - low || 1;
  return box.bottom - ((value - low) / span) * (box.bottom - box.top);
}

/** A point's horizontal position, by index, across `count` positions. */
export function xOf(index: number, count: number, box: Box): number {
  if (count <= 1) return (box.left + box.right) / 2;
  return box.left + (index / (count - 1)) * (box.right - box.left);
}

/** The centre of the `index`th of `count` equal bands. Bars, not lines. */
export function bandOf(index: number, count: number, box: Box): {
  centre: number;
  width: number;
} {
  const width = (box.right - box.left) / Math.max(1, count);
  return { centre: box.left + index * width + width / 2, width };
}

/**
 * Which point the cursor is nearest, from its position in the box.
 *
 * Returns -1 when there is nothing to be near. The caller decides whether
 * being outside the box counts -- a tooltip that vanishes between two
 * marks is a tooltip that is hard to use, so the plot keeps it.
 */
export function nearestIndex(x: number, count: number, box: Box): number {
  if (count <= 0) return -1;
  if (count === 1) return 0;
  const span = box.right - box.left || 1;
  const ratio = (x - box.left) / span;
  return Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))));
}

/** Which band the cursor is over. Bands tile the box; positions do not. */
export function bandIndex(x: number, count: number, box: Box): number {
  if (count <= 0) return -1;
  const width = (box.right - box.left) / count || 1;
  return Math.min(count - 1, Math.max(0, Math.floor((x - box.left) / width)));
}

/**
 * The slice of points a window shows.
 *
 * A window is clamped rather than rejected: a saved zoom over a result
 * that has since been re-run with fewer rows must show something, and the
 * nearest legal window is a better answer than an empty chart.
 */
export function slice<T>(items: T[], window: Window): T[] {
  if (!items.length) return items;
  // Clamped to `length - 1`, not to `length`: a start ON the end leaves an
  // empty slice, which is the empty chart this is here to prevent.
  const start = Math.max(0, Math.min(Math.floor(window.start), items.length - 1));
  const end = Math.max(start + 1, Math.min(Math.ceil(window.end), items.length));
  return items.slice(start, end);
}

/** Whether a window actually hides anything. A "Reset" that resets nothing
 *  is a control that exists to be pressed for no effect. */
export function isZoomed(window: Window, count: number): boolean {
  return slice(new Array(count).fill(0), window).length < count;
}

//: How few points a window may hold. Below three a line is not a trend and
//: the zoom control has nothing left to grip.
export const MIN_WINDOW = 3;

/**
 * Zoom in or out about the middle of what is currently shown.
 *
 * Anchored on the CENTRE rather than on the start, because a reader
 * zooming is looking at something and expects it to stay in view.
 */
export function zoom(window: Window, count: number, factor: number): Window {
  const current = slice(new Array(count).fill(0), window);
  const start = Math.max(0, Math.min(Math.floor(window.start), count - 1));
  const width = current.length;
  const next = Math.max(MIN_WINDOW, Math.min(count, Math.round(width * factor)));
  if (next >= count) return { start: 0, end: count };
  const centre = start + width / 2;
  const from = Math.max(0, Math.min(count - next, Math.round(centre - next / 2)));
  return { start: from, end: from + next };
}

/** Move the window without changing how much it shows. */
export function pan(window: Window, count: number, by: number): Window {
  const width = slice(new Array(count).fill(0), window).length;
  if (width >= count) return { start: 0, end: count };
  const from = Math.max(0, Math.min(count - width, Math.floor(window.start) + by));
  return { start: from, end: from + width };
}

/**
 * How many x labels fit, as a stride over the points.
 *
 * Twenty periods in the width a chart gets inside a thread is twenty
 * overlapping labels, which is the same as none. Every nth is legible and
 * still says what the axis runs over.
 */
export function labelStride(count: number, room: number): number {
  if (count <= room || room <= 0) return 1;
  return Math.ceil(count / room);
}
