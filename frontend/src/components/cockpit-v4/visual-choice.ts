/**
 * What is worth showing, and how to read a value safely.
 *
 * Separate from the drawing because it is the part with rules in it. A chart
 * earns its place or it does not appear, and deciding that is logic a test
 * can hold; turning numbers into rectangles is not.
 */

import type { RenderedChart, RenderedTable } from "./client.ts";

/** How many rows a table shows before a reader asks for the rest. §14, §41. */
export const TOP_N = 10;

/**
 * Every form the analyst may ask for, and the browser must be able to draw.
 *
 * Kept here, in a module with no JSX in it, so the unit suite can read it
 * and `visuals.tsx` can key its dispatch by it: a `Record<ChartKind, …>`
 * with a kind missing is a TYPE error, not a chart that quietly renders as
 * bars. That silence is not hypothetical -- `waterfall` and `scatter` were
 * legal in the contract for months and fell through to `_bar_svg`, and an
 * analyst who asked for a bridge got bars and was told nothing.
 *
 * The same list is `finalization.CHART_KINDS` on the server and the `kind`
 * enum in `shared_defs.schema.json`. The test pins all three together.
 */
export const CHART_KINDS = [
  "area", "bar", "box", "bubble", "combo", "donut", "grouped_bar", "heatmap",
  "histogram", "line", "pie", "scatter", "stacked_bar", "stacked_bar_100",
  "step_line", "waterfall",
] as const;

export type ChartKind = (typeof CHART_KINDS)[number];

/**
 * A number, or nothing.
 *
 * A null is NOT zero, and `Number(null)` is 0 -- which would plot a cell
 * nobody computed as a bar of height zero, indistinguishable from a real
 * one. An empty string and a boolean convert just as cheerfully, and none of
 * the three is a measurement.
 */
export function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * Whether a chart is worth drawing at all.
 *
 * §13 and §42: one point is a number and a chart of it is decoration; no
 * points is an empty frame, which reads as missing data rather than as no
 * data. §40 is the same rule from the other side -- an analysis that
 * returned nothing says so in words.
 */
export function chartIsUseful(chart: RenderedChart | undefined): boolean {
  if (!chart) return false;
  // A MATRIX AND A BOX PLOT ARE NOT POINT SERIES. Their bodies are built
  // beside the points, and asking "does it have two points with a number
  // in the first y column" of a grid answers a question about a different
  // chart. A grid is worth drawing when it has two axes and a filled cell;
  // a box plot when it has a box.
  if (chart.kind === "heatmap") {
    const matrix = chart.matrix;
    return Boolean(
      matrix?.rows?.length &&
        matrix.columns?.length &&
        Object.values(matrix.cells ?? {}).some((v) => typeof v === "number"),
    );
  }
  if (chart.kind === "box") return (chart.boxes ?? []).length > 0;

  const columns = chart.y_columns ?? [];
  if (!columns.length) return false;
  const points = chart.points ?? [];
  if (points.length < 2) return false;
  return points.some((p) => numeric(p.values?.[columns[0]]) !== null);
}

/** Whether a table has anything in it. An empty one is said in words. */
export function tableIsUseful(table: RenderedTable | undefined): boolean {
  return Boolean(table && (table.rows ?? []).length > 0);
}

/**
 * What to show, and whether a toggle is worth offering.
 *
 * §21: a disabled Chart button beside a table that could never be charted is
 * a control that exists to be refused.
 */
export function choose(
  tables: RenderedTable[],
  charts: RenderedChart[],
): {
  chart?: RenderedChart;
  table?: RenderedTable;
  both: boolean;
  /** EVERY useful chart, in the order the analyst sent them. */
  usefulCharts: RenderedChart[];
  usefulTables: RenderedTable[];
  /**
   * Where each useful chart sat in the ANSWER.
   *
   * The export route addresses a chart by its index in `answer.charts`,
   * and this list is filtered -- so the second chart on screen can be the
   * fourth in the payload. Downloading by the on-screen position would
   * hand the reader a different chart from the one they pressed the
   * button under.
   */
  chartIndices: number[];
} {
  // `.find()` returned the FIRST useful chart and discarded the rest, so a
  // three-chart answer rendered one. A question like "show the delinquency
  // trend for each product" is answered by one chart per product, and the
  // reader saw one product. The singular fields are kept for callers that
  // still want a headline pair; the arrays are what gets rendered.
  const kept = charts
    .map((chart, index) => ({ chart, index }))
    .filter(({ chart }) => chartIsUseful(chart));
  const usefulCharts = kept.map(({ chart }) => chart);
  const usefulTables = tables.filter((t) => tableIsUseful(t));
  return {
    chart: usefulCharts[0],
    table: usefulTables[0],
    both: Boolean(usefulCharts.length && usefulTables.length),
    usefulCharts,
    usefulTables,
    chartIndices: kept.map(({ index }) => index),
  };
}
