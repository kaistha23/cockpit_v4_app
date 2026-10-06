"use client";

/**
 * Charts and tables, drawn from what the server already validated.
 *
 * Nothing in this file computes anything. Every figure arrives twice from the
 * backend -- `canonical`, which is the full-precision value the arithmetic and
 * the ordering ran on, and `display`, which is that same value written by the
 * one display policy that decides money shows no decimals and a share shows
 * two. This component picks the second and lays it out.
 *
 * That division is the whole point. A frontend that rounded for itself would
 * be a second opinion about a number the server had already published, and
 * the first time the two disagreed nobody would be able to say which was the
 * answer.
 *
 * Bars are scaled from the CANONICAL values, never the displayed strings:
 * "SAR 9,000 million" sorts above "SAR 40,599 million" as text, and a bar
 * chart drawn from formatted labels is a bar chart of string lengths.
 *
 * Drawn as plain SVG and a plain table. A charting library would bring its own
 * number formatting, its own locale rules and its own idea of a sensible axis,
 * and every one of those is a place for the published figure to change on its
 * way to the screen.
 */

import * as React from "react";

import {
  AxisCaption,
  Legend,
  Plot,
  Ruler,
  slotColor,
  useWindow,
} from "./chart-frame";
import { PLOT_BOX, bandOf, extentOf, slice, xOf, yOf } from "./chart-frame";
import { px } from "./chart-geometry";
import type { HoverPoint } from "./chart-frame";
import type { ChartPoint, RenderedChart, RenderedTable } from "./client";
import { ChartDownload, TableDownload } from "./figure-download";
import { TOP_N, choose, numeric, text } from "./visual-choice";
import type { ChartKind } from "./visual-choice";

/**
 * The scale this chart is drawn against.
 *
 * THE SERVER'S, whenever it published one. The data is the fallback, for
 * a thread saved before axes existed -- a reader's own history must not
 * stop drawing because the contract grew a field.
 */
function scaleOf(chart: RenderedChart, values: number[], zeroBased: boolean) {
  const [low, high] = extentOf(chart.y_axis, values, { zeroBased });
  return { low, high, span: high - low || 1 };
}

/** Every named measure of one point, for the hover layer. */
function hoverOf(point: ChartPoint | undefined, columns: string[]): HoverPoint | null {
  if (!point) return null;
  return {
    label: text(point.label),
    rowId: point.row_id,
    values: columns.map((column, slot) => ({
      name: column,
      // The server's string, always. Never derived from `values`.
      display: text(point.display?.[column]),
      slot,
    })),
  };
}

export { TOP_N, chartIsUseful } from "./visual-choice";

// ---- table --------------------------------------------------------------

export function ResultTable({ table, runId }: {
  table: RenderedTable;
  /** Absent in a preview, where there is no stored run to export from. */
  runId?: string;
}) {
  const rows = table.rows ?? [];
  const [showAll, setShowAll] = React.useState(false);
  const [sort, setSort] = React.useState<{ column: string; desc: boolean }
    | null>(null);

  const ordered = React.useMemo(() => {
    if (!sort) return rows;
    // Sorted on CANONICAL values. Sorting the displayed strings would order
    // a money column alphabetically.
    const copy = [...rows];
    copy.sort((a, b) => {
      const left = numeric(a.canonical[sort.column]);
      const right = numeric(b.canonical[sort.column]);
      if (left !== null && right !== null) return left - right;
      return text(a.canonical[sort.column]).localeCompare(
        text(b.canonical[sort.column]),
      );
    });
    return sort.desc ? copy.reverse() : copy;
  }, [rows, sort]);

  const shown = showAll ? ordered : ordered.slice(0, TOP_N);
  const hidden = ordered.length - shown.length;

  if (!rows.length) {
    return (
      <p data-testid="v4-table-empty" className="text-sm text-text-secondary">
        {table.title ? `${table.title}: ` : ""}no rows matched.
      </p>
    );
  }

  return (
    <figure data-testid="v4-result-table" className="min-w-0">
      {table.title ? (
        <figcaption className="mb-2 flex items-center gap-2 text-sm font-medium text-text-primary">
          <span className="min-w-0 truncate">
            {table.title}
            <span className="ml-2 font-normal text-text-muted">
              {table.row_count ?? rows.length} row
              {(table.row_count ?? rows.length) === 1 ? "" : "s"}
            </span>
          </span>
          {runId && table.artifact_id ? (
            <span className="ml-auto shrink-0">
              <TableDownload runId={runId} artifactId={table.artifact_id} />
            </span>
          ) : null}
        </figcaption>
      ) : null}
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-surface-sunken text-left">
            <tr>
              {table.columns.map((column) => (
                <th key={column} scope="col" className="px-3 py-2 font-medium">
                  <button
                    type="button"
                    data-testid={`v4-table-sort-${column}`}
                    className="text-text-secondary hover:underline"
                    onClick={() =>
                      setSort((prev) =>
                        prev && prev.column === column
                          ? { column, desc: !prev.desc }
                          : { column, desc: true },
                      )
                    }
                  >
                    {column}
                    {sort?.column === column ? (sort.desc ? " ▾" : " ▴") : ""}
                  </button>
                  {table.column_units?.[column] ? (
                    <span className="ml-1 font-normal text-xs text-text-muted">
                      {table.column_units[column]}
                    </span>
                  ) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr
                key={row.row_id}
                data-testid="v4-table-row"
                className="border-t border-border"
              >
                {table.columns.map((column) => (
                  <td
                    key={column}
                    className="px-3 py-2 tabular-nums text-text-primary"
                    dir="auto"
                  >
                    {text(row.display[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 ? (
        <button
          type="button"
          data-testid="v4-table-show-all"
          onClick={() => setShowAll(true)}
          className="mt-2 text-xs font-medium text-accent hover:underline"
        >
          View all {ordered.length} rows ({hidden} more)
        </button>
      ) : null}
      {showAll && ordered.length > TOP_N ? (
        <button
          type="button"
          data-testid="v4-table-show-top"
          onClick={() => setShowAll(false)}
          className="mt-2 text-xs font-medium text-accent hover:underline"
        >
          Show top {TOP_N}
        </button>
      ) : null}
    </figure>
  );
}

// ---- chart --------------------------------------------------------------

/** The series a chart actually plots, with its canonical scale. */
function series(chart: RenderedChart): {
  column: string;
  unit: string;
  points: { label: string; value: number; display: string; rowId: string }[];
} | null {
  const column = chart.y_columns?.[0];
  if (!column) return null;
  const points = (chart.points ?? [])
    .map((p: ChartPoint) => ({
      rowId: p.row_id,
      label: text(p.label),
      value: numeric(p.values?.[column]),
      display: text(p.display?.[column]),
    }))
    .filter((p): p is { rowId: string; label: string; value: number;
                        display: string } => p.value !== null);
  if (!points.length) return null;
  return {
    column,
    unit: chart.series_units?.[column] ?? chart.unit ?? "",
    points,
  };
}

/**
 * A horizontal bar per category. §13: the shape for a ranked comparison,
 * because the labels are words and words read horizontally.
 */
//: The label column of every horizontal form. Shared so the ruler under
//: the bars lands exactly on the track rather than approximately on it.
const BAR_GRID = "minmax(0,11rem) 1fr auto";

function BarChart({ chart }: { chart: RenderedChart }) {
  const data = series(chart);
  if (!data) return null;
  // Scaled from canonical values, and from zero: a bar chart whose axis
  // starts elsewhere makes a 3% difference look like a threefold one.
  // The extent is the SERVER'S when it published one, which is what makes
  // the ruler below and the bars above the same scale.
  const { high } = scaleOf(chart, data.points.map((p) => p.value), true);
  const max = Math.max(high, 0);
  return (
    <div>
    <ol data-testid="v4-chart-bars" className="space-y-1.5">
      {data.points.map((point) => {
        const width = max > 0 ? (Math.abs(point.value) / max) * 100 : 0;
        return (
          <li key={point.rowId} className="grid grid-cols-[minmax(0,11rem)_1fr_auto] items-center gap-3">
            <span className="truncate text-xs text-text-secondary" dir="auto"
                  title={point.label}>
              {point.label}
            </span>
            <span className="h-4 rounded-sm bg-surface-sunken">
              <span
                data-testid="v4-chart-bar"
                data-value={String(point.value)}
                className="block h-4 rounded-sm bg-chart-1"
                style={{ width: `${width}%` }}
                title={`${point.label}: ${point.display}`}
              />
            </span>
            <span className="shrink-0 tabular-nums text-xs text-text-secondary">
              {point.display}
            </span>
          </li>
        );
      })}
    </ol>
    <Ruler axis={chart.y_axis} template={BAR_GRID} low={0} high={max} />
    <AxisCaption axis={chart.y_axis} />
    </div>
  );
}

/**
 * A line over an ordered axis. §13: the shape for a time series.
 *
 * FOUR FORMS SHARE THIS GEOMETRY and none of them is the others. A `line`
 * interpolates between readings; a `step_line` holds each value until the
 * next one, which is what a policy rate or a limit actually does; an `area`
 * fills to the baseline, so the eye reads the total rather than the slope;
 * a `scatter` draws the points and NO line, because there is no ordering
 * between them to interpolate along. All four rendered as a plain line,
 * which told the reader three things that were not true.
 */
function LineChart({ chart, variant = "line" }: {
  chart: RenderedChart;
  variant?: "line" | "step_line" | "area" | "scatter";
}) {
  const data = series(chart);
  const all = data?.points ?? [];
  const [view, setView] = useWindow(all.length);
  if (!data || all.length < 2) return null;

  // ZOOM IS A WINDOW OVER THE POINTS, never a re-scale of the values. The
  // marks shown are a subset of the published ones; none of them moves.
  const points = slice(all, view);
  const { low, high, span } = scaleOf(chart, points.map((p) => p.value), false);
  const at = (value: number, index: number): [number, number] => [
    xOf(index, points.length, PLOT_BOX),
    yOf(value, low, high, PLOT_BOX),
  ];
  const path = points
    .map((p, i) => {
      const [x, y] = at(p.value, i);
      if (i === 0) return `M ${px(x)} ${px(y)}`;
      if (variant === "step_line") {
        const [, previous] = at(points[i - 1].value, i - 1);
        return `L ${px(x)} ${px(previous)} L ${px(x)} ${px(y)}`;
      }
      return `L ${px(x)} ${px(y)}`;
    })
    .join(" ");
  const floor = yOf(Math.max(low, Math.min(0, high)), low, high, PLOT_BOX);

  return (
    <Plot
      testId={`v4-chart-${variant}`}
      title={chart.title}
      xAxis={chart.x_axis}
      yAxis={chart.y_axis}
      labels={points.map((p) => p.label)}
      low={low}
      high={high}
      zoom={{ count: all.length, window: view, onChange: setView }}
      hover={(index) => {
        const point = points[index];
        if (!point) return null;
        return {
          label: point.label,
          rowId: point.rowId,
          // The server's string for this exact point. Not read off the
          // chart, not recomputed from the geometry.
          values: [{ name: data.column, display: point.display, slot: 0 }],
        };
      }}
    >
      {variant === "area" ? (
        <path
          d={`${path} L ${PLOT_BOX.right} ${floor} L ${PLOT_BOX.left} ${floor} Z`}
          fill={slotColor(0)}
          fillOpacity="0.15"
          stroke="none"
        />
      ) : null}
      {variant === "scatter" ? null : (
        <path d={path} fill="none" stroke={slotColor(0)} strokeWidth="1.6"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      )}
      {/* A mark on every point, on every variant. A line with no marks
          leaves a reader guessing where the observations actually are,
          and gives the hover layer nothing to sit under. */}
      {points.map((p, i) => {
        const [x, y] = at(p.value, i);
        return (
          <circle key={p.rowId} cx={x} cy={y}
                  r={variant === "scatter" ? 2.6 : 1.9}
                  fill={slotColor(0)}
                  data-testid={variant === "scatter"
                    ? "v4-chart-point" : "v4-chart-line-point"}
                  data-value={String(p.value)}>
            <title>{`${p.label}: ${p.display}`}</title>
          </circle>
        );
      })}
    </Plot>
  );
}

/** One bar per category, segmented by series. §13: the shape for a mix. */
/**
 * Segments piled to a total, per category.
 *
 * `normalise` is the difference between the two forms a credit pack uses.
 * A 100% stack answers "what SHARE sits in each bucket" — the delinquency
 * band mix — and every row fills the width. A plain stack keeps the totals
 * comparable, so a book that grew shows a longer bar.
 */
function StackedBarChart({ chart, normalise = true }: {
  chart: RenderedChart;
  normalise?: boolean;
}) {
  const columns = chart.y_columns ?? [];
  const points = chart.points ?? [];
  if (columns.length < 2 || !points.length) return null;
  // A STACK IS READ OFF ITS TOTAL, so the scale reaches the tallest BAR
  // rather than the tallest segment.
  const totals = points.map((p) =>
    columns.reduce((sum, c) => sum + Math.abs(numeric(p.values?.[c]) ?? 0), 0));
  const widest = scaleOf(chart, totals, true).high || 1;
  return (
    <div data-testid={normalise ? "v4-chart-stacked" : "v4-chart-stacked-abs"}
         className="space-y-2">
      <ol className="space-y-1.5">
        {points.map((point) => {
          const parts = columns.map((c) => numeric(point.values?.[c]) ?? 0);
          const sum = parts.reduce((a, b) => a + Math.abs(b), 0) || 1;
          const total = normalise ? sum : widest;
          return (
            <li key={point.row_id}
                className="grid grid-cols-[minmax(0,11rem)_1fr] items-center gap-3">
              <span className="truncate text-xs text-text-secondary" dir="auto">
                {text(point.label)}
              </span>
              <span className="flex h-4 overflow-hidden rounded-sm bg-surface-sunken">
                {parts.map((value, index) => (
                  <span
                    key={columns[index]}
                    className="h-4"
                    // BY SLOT, NEVER BY RANK: a series keeps its colour
                    // whatever else is on screen beside it.
                    style={{ width: `${(Math.abs(value) / total) * 100}%`,
                             backgroundColor: slotColor(index) }}
                    title={`${columns[index]}: ${
                      text(point.display?.[columns[index]])}`}
                  />
                ))}
              </span>
            </li>
          );
        })}
      </ol>
      {/* A 100% stack is read as a share of the row, so the absolute
          scale underneath it would be a second, wrong story. */}
      {normalise ? null : (
        <Ruler axis={chart.y_axis} template="minmax(0,11rem) 1fr"
               low={0} high={widest} />
      )}
      {normalise ? null : <AxisCaption axis={chart.y_axis} />}
      <Legend names={columns} />
    </div>
  );
}

/** Bars side by side per category: the comparison is WITHIN a group. */
function GroupedBarChart({ chart }: { chart: RenderedChart }) {
  const columns = chart.y_columns ?? [];
  const points = chart.points ?? [];
  if (columns.length < 2 || !points.length) return null;
  const max = scaleOf(
    chart,
    points.flatMap((p) => columns.map((c) => numeric(p.values?.[c]) ?? 0)),
    true).high || 1;
  return (
    <div data-testid="v4-chart-grouped" className="space-y-2">
      <ol className="space-y-2">
        {points.map((point) => (
          <li key={point.row_id} className="space-y-0.5">
            <span className="truncate text-xs text-text-secondary" dir="auto">
              {text(point.label)}
            </span>
            {columns.map((column, index) => {
              const value = numeric(point.values?.[column]) ?? 0;
              return (
                <span key={column} className="flex items-center gap-2">
                  <span className="h-2.5 w-full rounded-sm bg-surface-sunken">
                    <span className="block h-2.5 rounded-sm"
                          style={{ width: `${(Math.abs(value) / max) * 100}%`,
                                   backgroundColor: slotColor(index) }}
                          title={`${column}: ${text(point.display?.[column])}`} />
                  </span>
                  <span className="shrink-0 tabular-nums text-[10px] text-text-muted">
                    {text(point.display?.[column])}
                  </span>
                </span>
              );
            })}
          </li>
        ))}
      </ol>
      <Ruler axis={chart.y_axis} template="1fr auto" low={0} high={max} />
      <AxisCaption axis={chart.y_axis} />
      <Legend names={columns} />
    </div>
  );
}

/**
 * Volumes as bars, a RATE as a line on its own scale.
 *
 * Exposure in SAR millions and a delinquency rate in percent do not share
 * an axis. Plotted on one, the rate is a flat line along the floor and the
 * chart says nothing — which is why this form exists separately.
 */
function ComboChart({ chart }: { chart: RenderedChart }) {
  const columns = chart.y_columns ?? [];
  const points = chart.points ?? [];
  if (columns.length < 2 || !points.length) return null;
  const [bars, line] = columns;
  const barMax = extentOf(chart.y_axis,
                          points.map((p) => numeric(p.values?.[bars]) ?? 0),
                          { zeroBased: true })[1] || 1;
  const rates = points.map((p) => numeric(p.values?.[line]) ?? 0);
  // THE SECOND SCALE, published separately. A combo is the one form this
  // product draws on two, because a rate over the volumes it is a rate of
  // is what it is for -- and both have to be named or the reader cannot
  // tell which mark belongs to which.
  const [bottom, top] = extentOf(chart.y_axis_secondary, rates);
  const span = top - bottom || 1;
  return (
    <div data-testid="v4-chart-combo" className="space-y-2">
      <ol className="space-y-1.5">
        {points.map((point, index) => {
          const value = numeric(point.values?.[bars]) ?? 0;
          const rate = rates[index];
          return (
            <li key={point.row_id}
                className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3">
              <span className="truncate text-xs text-text-secondary" dir="auto">
                {text(point.label)}
              </span>
              <span className="relative block h-4 rounded-sm bg-surface-sunken">
                <span className="block h-4 rounded-sm bg-chart-1"
                      style={{ width: `${(Math.abs(value) / barMax) * 100}%` }}
                      title={`${bars}: ${text(point.display?.[bars])}`} />
                <span data-testid="v4-combo-rate"
                      className="absolute top-0 h-4 w-0.5 bg-warning"
                      style={{ left: `${((rate - bottom) / span) * 100}%` }}
                      title={`${line}: ${text(point.display?.[line])}`} />
              </span>
              <span className="shrink-0 tabular-nums text-xs text-warning">
                {text(point.display?.[line])}
              </span>
            </li>
          );
        })}
      </ol>
      <Ruler axis={chart.y_axis} template="minmax(0,9rem) 1fr auto"
             low={0} high={barMax} />
      <AxisCaption axis={chart.y_axis} />
      {chart.y_axis_secondary?.label ? (
        <p className="mt-0.5 text-center text-xs text-warning">
          {chart.y_axis_secondary.label}
          {chart.y_axis_secondary.ticks.length ? (
            <span className="ml-2 tabular-nums text-text-muted">
              {chart.y_axis_secondary.ticks[0].display}
              {" – "}
              {chart.y_axis_secondary.ticks[
                chart.y_axis_secondary.ticks.length - 1].display}
            </span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

/** Shares of one whole. `hole` makes it a donut. */
function SliceChart({ chart, hole }: { chart: RenderedChart; hole: number }) {
  const data = series(chart);
  if (!data) return null;
  const total = data.points.reduce((a, p) => a + Math.abs(p.value), 0);
  if (total <= 0) return null;
  const tones = ["var(--ipm-chart-1)", "var(--ipm-chart-2)", "var(--ipm-chart-3)",
                 "var(--ipm-chart-4)", "var(--ipm-chart-5)", "var(--ipm-chart-6)",
                 "var(--ipm-chart-7)", "var(--ipm-chart-8)"];
  // The running angle is carried in the array rather than in a variable
  // this closure reassigns: a render body that mutates its own scope is a
  // render that can disagree with itself on a re-run.
  const starts = data.points.reduce<number[]>(
    (acc, point) => [...acc,
                     acc[acc.length - 1] + (Math.abs(point.value) / total) * 360],
    [-90]);
  const arcs = data.points.map((point, index) => {
    const sweep = (Math.abs(point.value) / total) * 360;
    const start = starts[index];
    const angle = starts[index + 1];
    const rad = (deg: number) => (deg * Math.PI) / 180;
    const x1 = 60 + 52 * Math.cos(rad(start));
    const y1 = 60 + 52 * Math.sin(rad(start));
    const x2 = 60 + 52 * Math.cos(rad(angle));
    const y2 = 60 + 52 * Math.sin(rad(angle));
    return (
      <path key={point.rowId}
            d={`M 60 60 L ${px(x1)} ${px(y1)} A 52 52 0 ${
              sweep > 180 ? 1 : 0} 1 ${px(x2)} ${px(y2)} Z`}
            fill={tones[index % tones.length]}>
        <title>{`${point.label}: ${point.display}`}</title>
      </path>
    );
  });
  return (
    <div data-testid={hole > 0 ? "v4-chart-donut" : "v4-chart-pie"}
         className="flex flex-wrap items-center gap-4">
      <svg viewBox="0 0 120 120" className="h-32 w-32" role="img">
        {arcs}
        {hole > 0 ? (
          <circle cx="60" cy="60" r={52 * hole} fill="var(--ipm-surface)" />
        ) : null}
      </svg>
      <ul className="space-y-1 text-xs text-text-secondary">
        {data.points.map((point, index) => (
          <li key={point.rowId} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-sm"
                  style={{ backgroundColor: tones[index % tones.length] }} />
            <span className="truncate" dir="auto">{point.label}</span>
            <span className="tabular-nums text-text-muted">{point.display}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Counts per band, drawn touching.
 *
 * The gap between bars is what says "these categories are separate". A DPD
 * distribution has no gaps — 10-19 abuts 20-29 — and drawing one invites a
 * reader to see groups that are not there.
 */
function HistogramChart({ chart }: { chart: RenderedChart }) {
  const data = series(chart);
  if (!data) return null;
  const points = data.points;
  const { low, high } = scaleOf(chart, points.map((p) => p.value), true);
  const base = yOf(Math.max(low, 0), low, high, PLOT_BOX);
  return (
    <Plot
      testId="v4-chart-histogram"
      title={chart.title}
      xAxis={chart.x_axis}
      yAxis={chart.y_axis}
      labels={points.map((p) => p.label)}
      banded
      low={low}
      high={high}
      hover={(index) => {
        const point = points[index];
        if (!point) return null;
        return {
          label: point.label,
          rowId: point.rowId,
          values: [{ name: data.column, display: point.display, slot: 0 }],
        };
      }}
    >
      {points.map((point, index) => {
        const { centre, width } = bandOf(index, points.length, PLOT_BOX);
        const y = yOf(point.value, low, high, PLOT_BOX);
        return (
          <rect
            key={point.rowId}
            data-testid="v4-histogram-bin"
            data-value={String(point.value)}
            // Drawn TOUCHING: the gap between bars is what says "these
            // categories are separate", and a DPD distribution has none --
            // 10-19 abuts 20-29.
            x={centre - width / 2}
            y={Math.min(y, base)}
            width={Math.max(0.5, width - 0.4)}
            height={Math.max(0.5, Math.abs(base - y))}
            fill={slotColor(0)}
          >
            <title>{`${point.label}: ${point.display}`}</title>
          </rect>
        );
      })}
    </Plot>
  );
}

/**
 * A bridge: each step starts where the last one finished.
 *
 * A waterfall is how a book explains a MOVEMENT — opening balance, new
 * lending, repayments, write-offs, closing balance. Drawn as ordinary bars
 * it becomes five unrelated quantities and the arithmetic that connects
 * them is invisible, which is exactly what `waterfall` did for the months
 * it was legal in the contract and fell through to `_bar_svg`.
 *
 * The last point is treated as a total and drawn from the baseline when it
 * equals the running sum; every other step floats.
 */
function WaterfallChart({ chart }: { chart: RenderedChart }) {
  const data = series(chart);
  if (!data) return null;
  // Running totals in an array, not a reassigned variable: the lint rule
  // that forbids the second one is right that a render body which mutates
  // its own scope can disagree with itself on a re-run.
  const cumulative = data.points.reduce<number[]>(
    (acc, point) => [...acc, acc[acc.length - 1] + point.value], [0]);
  const last = data.points[data.points.length - 1];
  // A LAST STEP THAT EQUALS EVERYTHING BEFORE IT IS THE TOTAL, not another
  // movement: "closing balance" is drawn from the baseline, or the bridge
  // ends with a bar that says the book doubled.
  const before = cumulative[cumulative.length - 2];
  const isTotal = Math.abs(before - last.value)
    <= Math.abs(last.value) * 1e-9;
  const steps = data.points.map((point, index) => (
    isTotal && index === data.points.length - 1
      ? { ...point, from: 0, to: point.value }
      : { ...point, from: cumulative[index], to: cumulative[index + 1] }));
  // A bridge is measured against the RUNNING TOTALS it passes through,
  // not against its individual movements.
  const reach = steps.flatMap((s) => [s.from, s.to]);
  const [floor, ceiling] = extentOf(chart.y_axis, reach, { zeroBased: true });
  const span = ceiling - floor || 1;
  return (
    <div>
    <ol data-testid="v4-chart-waterfall" className="space-y-1.5">
      {steps.map((step, index) => {
        const low = Math.min(step.from, step.to);
        const high = Math.max(step.from, step.to);
        const total = isTotal && index === steps.length - 1;
        return (
          <li key={step.rowId}
              className="grid grid-cols-[minmax(0,11rem)_1fr_auto] items-center gap-3">
            <span className="truncate text-xs text-text-secondary" dir="auto"
                  title={step.label}>{step.label}</span>
            <span className="relative block h-4 rounded-sm bg-surface-sunken">
              <span
                data-testid="v4-chart-waterfall-step"
                data-value={String(step.value)}
                className={`absolute h-4 rounded-sm ${
                  total ? "bg-text-secondary"
                        : step.value >= 0 ? "bg-positive" : "bg-negative"}`}
                style={{ left: `${((low - floor) / span) * 100}%`,
                         width: `${Math.max((high - low) / span, 0.004) * 100}%` }}
                title={`${step.label}: ${step.display}`}
              />
            </span>
            <span className="shrink-0 tabular-nums text-xs text-text-secondary">
              {step.display}
            </span>
          </li>
        );
      })}
    </ol>
    <Ruler axis={chart.y_axis} template={BAR_GRID} low={floor} high={ceiling} />
    <AxisCaption axis={chart.y_axis} />
    </div>
  );
}

/**
 * Two measures and a third in the size of the mark.
 *
 * The form a concentration slide uses: exposure against delinquency rate,
 * with the bubble sized by account count, so a terrible rate over eleven
 * accounts does not read like a terrible rate over eleven thousand. Three
 * numbers per point is the whole reason to draw it, and it was rendering
 * as bars of the first one.
 */
function BubbleChart({ chart }: { chart: RenderedChart }) {
  const columns = chart.y_columns ?? [];
  const points = chart.points ?? [];
  if (columns.length < 2 || !points.length) return null;
  const [xColumn, yColumn, sizeColumn] = columns;
  const read = (column: string | undefined) => (column
    ? points.map((p) => numeric(p.values?.[column]) ?? 0) : []);
  const xs = read(xColumn);
  const ys = read(yColumn);
  const sizes = sizeColumn ? read(sizeColumn) : ys.map(() => 1);
  // Both scales from the server, so the bubble sitting against a tick on
  // screen sits against the same tick in the exported copy.
  const [xLow, xHigh] = extentOf(chart.x_axis, xs);
  const [yLow, yHigh] = extentOf(chart.y_axis, ys);
  const across = (value: number) =>
    PLOT_BOX.left
    + ((value - xLow) / ((xHigh - xLow) || 1)) * (PLOT_BOX.right - PLOT_BOX.left);
  const widest = Math.max(...sizes.map(Math.abs), 0) || 1;
  return (
    <Plot
      testId="v4-chart-bubble"
      title={chart.title}
      xAxis={chart.x_axis}
      yAxis={chart.y_axis}
      labels={points.map((p) => text(p.label))}
      low={yLow}
      high={yHigh}
      hover={(index) => hoverOf(points[index], columns)}
      footer={
        <p className="text-xs text-text-muted">
          {sizeColumn ? `Sized by ${sizeColumn}` : ""}
        </p>
      }
    >
      {points.map((point, index) => (
        <circle
          key={point.row_id}
          data-testid="v4-chart-bubble-mark"
          cx={across(xs[index])}
          cy={yOf(ys[index], yLow, yHigh, PLOT_BOX)}
          // AREA, not radius: a radius proportional to the value
          // exaggerates a big bubble by its square.
          r={2 + Math.sqrt(Math.abs(sizes[index]) / widest) * 9}
          fill={slotColor(0)}
          fillOpacity="0.45"
          stroke={slotColor(0)}
          strokeWidth="0.6"
        >
          <title>{`${text(point.label)}: ${
            columns.map((c) => text(point.display?.[c])).join(" / ")}`}</title>
        </circle>
      ))}
    </Plot>
  );
}

/**
 * A from/to result as a grid.
 *
 * NOT `analytics/charts.tsx#MatrixHeatmap`, which looks like the same
 * component and is not: it is hard-coded for row PERCENTAGES
 * (a hard-coded one-decimal round and a `%` tooltip), so a migration
 * counted in borrowers
 * renders as "100.0" and claims to be a percentage. It also formats in the
 * browser, which is the one thing the numeric contract forbids; takes a
 * single `categories` axis, so a sector-by-stage grid cannot be drawn; and
 * reads a missing cell as `?? 0`, asserting that nobody made a move the
 * query simply never reported.
 *
 * Every string here is the server's. The only arithmetic is the shade.
 */
function MatrixChart({ chart }: { chart: RenderedChart }) {
  const matrix = chart.matrix;
  if (!matrix?.rows?.length || !matrix.columns?.length) return null;
  const magnitudes = Object.values(matrix.cells ?? {})
    .filter((v): v is number => typeof v === "number")
    .map(Math.abs);
  const max = magnitudes.length ? Math.max(...magnitudes) : 0;

  return (
    <div data-testid="v4-chart-matrix" className="overflow-x-auto">
      <table className="border-separate border-spacing-0.5 text-xs">
        <thead>
          <tr>
            <th className="px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-text-muted">
              {matrix.row_axis} \ {matrix.column_axis}
            </th>
            {matrix.columns.map((column) => (
              <th key={column}
                  className="px-1.5 py-1 text-center text-[10px] font-semibold text-text-muted">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((row) => (
            <tr key={row}>
              <th className="px-2 py-1 text-left text-[10px] font-semibold text-text-muted">
                {row}
              </th>
              {matrix.columns.map((column) => {
                const key = `${row}|${column}`;
                const value = matrix.cells?.[key];
                const shown = matrix.display?.[key];
                const has = typeof value === "number";
                const weight = has && max > 0 ? Math.abs(value) / max : 0;
                const mix = ((0.08 + 0.84 * weight) * 100).toFixed(1); // not-a-published-figure: a colour-mix ratio
                const diagonal = matrix.square && row === column;
                return (
                  <td
                    key={column}
                    data-testid="v4-matrix-cell"
                    data-cell={key}
                    data-empty={has ? undefined : "true"}
                    title={has ? `${row} → ${column}: ${shown}` : undefined}
                    className={[
                      "min-w-12 rounded-[3px] px-1.5 py-1.5 text-center",
                      "tabular-nums",
                      weight > 0.55 ? "text-accent-contrast" : "text-text-secondary",
                      diagonal ? "ring-1 ring-inset ring-accent" : "",
                    ].join(" ")}
                    style={{
                      // The ramp is mixed from the theme's own accent and
                      // its own surface, so a hot cell is hot in every theme
                      // rather than near-black on near-black.
                      backgroundColor: has
                        ? `color-mix(in srgb, var(--ipm-accent) ${mix}%, var(--ipm-surface))`
                        : "var(--ipm-surface-sunken)",
                    }}
                  >
                    {/* An empty cell is not a zero. */}
                    {has ? shown : "·"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Min, Q1, median, Q3, max — the five numbers, as computed by the server. */
function BoxChart({ chart }: { chart: RenderedChart }) {
  const boxes = chart.boxes ?? [];
  if (!boxes.length) return null;
  const numbers = boxes
    .flatMap((b) => [Number(b.minimum), Number(b.maximum)])
    .filter((n) => Number.isFinite(n));
  if (!numbers.length) return null;
  const top = Math.max(...numbers);
  const bottom = Math.min(...numbers);
  const span = top - bottom || 1;
  const place = (raw: string) => ((Number(raw) - bottom) / span) * 100;

  return (
    <ol data-testid="v4-chart-boxes" className="space-y-2">
      {boxes.map((box) => {
        const q1 = place(box.q1);
        const q3 = place(box.q3);
        return (
          <li key={box.label || "all"}
              className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3">
            <span className="truncate text-xs text-text-secondary" dir="auto"
                  title={box.label}>
              {box.label || "All"}
            </span>
            <span data-testid="v4-box" data-label={box.label}
                  className="relative block h-5">
              <span className="absolute top-1/2 h-px bg-border-strong"
                    style={{
                      left: `${place(box.minimum)}%`,
                      width: `${place(box.maximum) - place(box.minimum)}%`,
                    }} />
              <span className="absolute top-0 h-5 rounded-sm border border-chart-1 bg-chart-1/15"
                    style={{
                      left: `${Math.min(q1, q3)}%`,
                      width: `${Math.max(Math.abs(q3 - q1), 0.5)}%`,
                    }} />
              <span data-testid="v4-box-median"
                    className="absolute top-0 h-5 w-0.5 bg-chart-1"
                    style={{ left: `${place(box.median)}%` }}
                    title={`median ${box.display?.median ?? ""}`} />
            </span>
            <span className="shrink-0 tabular-nums text-xs text-text-secondary">
              {box.display?.median}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The form the analyst asked for, drawn as itself.
 *
 * THE KIND DECIDES, not the shape of the payload. This read
 * `y_columns.length > 1` FIRST, so any chart with two measures became a
 * stack whatever it said it was — a combo, a grouped comparison and a
 * bubble all rendered as stacked bars. The count of series is a fallback
 * for a kind nobody recognises, not a rule that outranks the contract.
 */
export const CHART_BODIES: Record<
  ChartKind,
  (chart: RenderedChart) => React.ReactElement | null
> = {
  heatmap: (chart) => <MatrixChart chart={chart} />,
  box: (chart) => <BoxChart chart={chart} />,
  line: (chart) => <LineChart chart={chart} />,
  step_line: (chart) => <LineChart chart={chart} variant="step_line" />,
  area: (chart) => <LineChart chart={chart} variant="area" />,
  scatter: (chart) => <LineChart chart={chart} variant="scatter" />,
  stacked_bar: (chart) => <StackedBarChart chart={chart} normalise={false} />,
  stacked_bar_100: (chart) => <StackedBarChart chart={chart} />,
  grouped_bar: (chart) => <GroupedBarChart chart={chart} />,
  combo: (chart) => <ComboChart chart={chart} />,
  pie: (chart) => <SliceChart chart={chart} hole={0} />,
  donut: (chart) => <SliceChart chart={chart} hole={0.55} />,
  histogram: (chart) => <HistogramChart chart={chart} />,
  bubble: (chart) => <BubbleChart chart={chart} />,
  waterfall: (chart) => <WaterfallChart chart={chart} />,
  bar: (chart) => <BarChart chart={chart} />,
};

export function ResultChart({ chart, runId, index }: {
  chart: RenderedChart;
  /** Absent in a preview, where there is no stored run to export from. */
  runId?: string;
  /** This chart's position in the ANSWER, which is what the API addresses. */
  index?: number;
}) {
  const draw = CHART_BODIES[chart.kind as ChartKind];
  const body = draw
    ? draw(chart)
    : (chart.y_columns ?? []).length > 1
      ? <StackedBarChart chart={chart} />
      : <BarChart chart={chart} />;
  if (!body) return null;
  const unit =
    chart.matrix?.unit ||
    chart.boxes?.[0]?.unit ||
    chart.unit ||
    Object.values(chart.series_units ?? {})[0] ||
    "";
  return (
    <figure data-testid="v4-result-chart" data-kind={chart.kind}
            className="min-w-0">
      <figcaption className="mb-2 flex items-center gap-2 text-sm font-medium text-text-primary">
        <span className="min-w-0 truncate">
          {chart.title}
          {unit ? (
            <span className="ml-2 font-normal text-text-muted">{unit}</span>
          ) : null}
        </span>
        {runId && index !== undefined ? (
          <span className="ml-auto shrink-0">
            <ChartDownload runId={runId} index={index} />
          </span>
        ) : null}
      </figcaption>
      {/* The analyst's one line about why this form rather than the table
          beside it. Shown only when they wrote one. */}
      {chart.why_this_chart ? (
        <p data-testid="v4-chart-reason"
           className="mb-2 text-xs text-text-muted" dir="auto">
          {chart.why_this_chart}
        </p>
      ) : null}
      {body}
    </figure>
  );
}

// ---- what to show -------------------------------------------------------

/**
 * Chart and table together, with a toggle only when both say something.
 *
 * §21: a disabled Chart button beside a table that could never be charted is
 * a control that exists to be refused. When only the table is useful, only
 * the table appears.
 */
export function Visuals({
  tables,
  charts,
  runId,
}: {
  tables: RenderedTable[];
  charts: RenderedChart[];
  /**
   * The run these figures belong to.
   *
   * Absent in a preview or a seeded card, where nothing has been stored
   * to export. A download button that 404s is worse than no button.
   */
  runId?: string;
}) {
  const { usefulCharts, usefulTables, chartIndices } = choose(tables, charts);
  if (!usefulCharts.length && !usefulTables.length) return null;

  // EVERY CHART, ONE AFTER ANOTHER, and the tables below them.
  //
  // This was one chart and one table, mutually exclusive behind a toggle.
  // Two things were wrong with it. An answer that draws a trend per product
  // showed one product, silently — the rest were computed, validated,
  // rendered and dropped a line before the screen. And a reader who wanted
  // the numbers under the picture had to give up the picture to get them,
  // which is not a choice anybody wants to make about their own result.
  return (
    <div data-testid="v4-visuals" className="mt-4 space-y-5">
      {usefulCharts.map((chart, index) => (
        <ResultChart key={`${chart.artifact_id}-${chart.kind}-${index}`}
                     chart={chart} runId={runId}
                     index={chartIndices[index]} />
      ))}
      {usefulTables.map((table, index) => (
        <ResultTable key={`${table.artifact_id}-${index}`} table={table}
                     runId={runId} />
      ))}
    </div>
  );
}
