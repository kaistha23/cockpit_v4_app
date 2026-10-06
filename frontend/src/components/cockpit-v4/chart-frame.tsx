"use client";

/**
 * The page a Cockpit chart is drawn on: axes, gridlines, hover and zoom.
 *
 * What was wrong
 * --------------
 * The charts had no axes. Not "poor axes" -- none. A bar chart with a
 * scale nowhere on it, a line with no vertical reference, and the only
 * way to learn what a mark was worth was to rest on it and wait for the
 * browser's own `<title>` tooltip. Twenty periods of labels overlapped
 * into a grey smear, and a reader who wanted to look at three of them
 * had no way to.
 *
 * The reason was a rule, not an oversight: `visuals.tsx` may not format,
 * round or re-scale a number, so the browser could not invent "0, 5, 10,
 * 15 SAR mn". It still cannot. What changed is that the server now
 * publishes the axis -- tick values by a 1/2/5 rule, each tick's STRING
 * written by the one display policy that governs every published figure
 * -- and this file draws what it is given.
 *
 * So: every number visible here came out of `chart.y_axis.ticks[].display`
 * or `point.display[column]`. There is no `toFixed`, no `Intl`, no
 * rounding and no unit string built by hand anywhere in this file, and
 * `visuals.test.ts` holds it to that.
 *
 * Why not a charting library
 * --------------------------
 * `analytics/` has a Recharts kit and it is the right tool over there.
 * Here the axis, the ticks and every label are already decided by the
 * server; what a library would add is its own number formatting, its own
 * locale rules and its own idea of a sensible scale -- three more places
 * for a published figure to change on its way to the screen, to replace
 * about two hundred lines of positioning arithmetic that is unit-tested
 * in `chart-geometry.ts`. The trade goes the other way here.
 */

import * as React from "react";

import {
  FULL,
  bandIndex,
  bandOf,
  extentOf,
  isZoomed,
  labelStride,
  nearestIndex,
  pan,
  slice,
  xOf,
  yOf,
  zoom,
  type Axis,
  type Box,
  type Window,
} from "./chart-geometry";

/** The frame's own coordinate space. Everything inside is in these units. */
export const VIEW = { width: 320, height: 180 };

/**
 * The plot area.
 *
 * The left gutter is wide because the tick labels live in it and they are
 * the server's strings -- "SAR 1,240 million" is not a width this file
 * gets to choose. It is generous rather than measured: a label that
 * overflows into the plot is worse than a plot that is slightly narrow.
 */
export const PLOT: Box = { left: 62, right: 314, top: 12, bottom: 148 };

/** One thing the cursor can be over. */
export interface HoverPoint {
  /** The category or period, as the server sent it. */
  label: string;
  /** Series name -> the server's written value. Never formatted here. */
  values: { name: string; display: string; slot: number }[];
  /** The stored row this mark came from. The reader's audit handle. */
  rowId: string;
}

/** A series colour, by its fixed slot in the theme's categorical ramp.
 *
 *  BY SLOT, NEVER BY RANK: a legend filter that changed how many series
 *  were on screen would otherwise repaint the survivors, and a reader who
 *  had learnt that blue was Stage 1 would be reading the wrong line. */
export function slotColor(slot: number): string {
  return `var(--ipm-chart-${(slot % 8) + 1})`;
}

// ---- the axes -----------------------------------------------------------

/**
 * The horizontal gridlines and their labels.
 *
 * Recessive on purpose: a gridline competing with the data is a gridline
 * that makes the chart harder to read, which is the opposite of the job.
 */
function Gridlines({ axis, low, high }: {
  axis?: Axis;
  low: number;
  high: number;
}) {
  const ticks = (axis?.ticks ?? []).filter(
    (tick) => Number.isFinite(tick.value) && tick.value >= low && tick.value <= high,
  );
  if (!ticks.length) return null;
  return (
    <g data-testid="v4-chart-gridlines">
      {ticks.map((tick) => {
        const y = yOf(tick.value, low, high, PLOT);
        return (
          <g key={tick.value}>
            <line
              x1={PLOT.left}
              y1={y}
              x2={PLOT.right}
              y2={y}
              className="stroke-border"
              strokeWidth="0.5"
            />
            <text
              data-testid="v4-chart-y-tick"
              x={PLOT.left - 5}
              y={y + 2.5}
              textAnchor="end"
              className="fill-text-muted"
              style={{ fontSize: 6.5 }}
            >
              {/* The server's string. This file never writes a number. */}
              {tick.display}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** The categories or periods along the bottom, thinned to what fits. */
function XLabels({ labels, banded }: { labels: string[]; banded: boolean }) {
  const stride = labelStride(labels.length, 7);
  return (
    <g data-testid="v4-chart-x-labels">
      {labels.map((label, index) => {
        if (index % stride !== 0 && index !== labels.length - 1) return null;
        const x = banded
          ? bandOf(index, labels.length, PLOT).centre
          : xOf(index, labels.length, PLOT);
        return (
          <text
            key={`${label}-${index}`}
            data-testid="v4-chart-x-tick"
            x={x}
            y={PLOT.bottom + 10}
            textAnchor="middle"
            className="fill-text-muted"
            style={{ fontSize: 6.5 }}
          >
            {label.length > 12 ? `${label.slice(0, 11)}…` : label}
          </text>
        );
      })}
    </g>
  );
}

/**
 * What each axis MEASURES, in the catalogue's own words.
 *
 * "Exposure at default", not `ead_sar_mn`. A reader who has to decode a
 * column name to read a chart is a reader the chart failed -- and this is
 * the specific thing that was asked for.
 */
function AxisTitles({ x, y }: { x?: Axis; y?: Axis }) {
  return (
    <>
      {y?.label ? (
        <text
          data-testid="v4-chart-y-label"
          transform={`translate(10 ${(PLOT.top + PLOT.bottom) / 2}) rotate(-90)`}
          textAnchor="middle"
          className="fill-text-secondary"
          style={{ fontSize: 7, fontWeight: 600 }}
        >
          {y.label}
        </text>
      ) : null}
      {x?.label ? (
        <text
          data-testid="v4-chart-x-label"
          x={(PLOT.left + PLOT.right) / 2}
          y={VIEW.height - 4}
          textAnchor="middle"
          className="fill-text-secondary"
          style={{ fontSize: 7, fontWeight: 600 }}
        >
          {x.label}
        </text>
      ) : null}
    </>
  );
}

// ---- the legend ---------------------------------------------------------

/**
 * Which colour is which measure.
 *
 * Present whenever there are two or more series, because identity carried
 * by colour alone is identity a colour-blind reader does not have.
 */
export function Legend({ names }: { names: string[] }) {
  if (names.length < 2) return null;
  return (
    <ul data-testid="v4-chart-legend"
        className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
      {names.map((name, slot) => (
        <li key={name} className="flex items-center gap-1.5 text-xs text-text-secondary">
          <span
            aria-hidden="true"
            className="inline-block h-2 w-2 shrink-0 rounded-sm"
            style={{ backgroundColor: slotColor(slot) }}
          />
          <span className="truncate" dir="auto">{name}</span>
        </li>
      ))}
    </ul>
  );
}

// ---- the ruler under a horizontal form ----------------------------------

/**
 * The scale under a bar list, aligned to the bars themselves.
 *
 * The ranked forms -- bar, stack, group, combo, waterfall -- are laid out
 * horizontally, because their labels are words and words read across.
 * That is the right shape and it is kept. What they never had was a
 * SCALE: a reader could see one bar was longer and had no way to say by
 * how much except to read the number printed beside it, which is not what
 * a chart is for.
 *
 * Rendered as a grid row with the same template as the bars, so the ticks
 * land exactly under the track rather than approximately under it.
 */
export function Ruler({ axis, template, low, high }: {
  axis?: Axis;
  /** The caller's own grid template, so the ruler cannot drift from it. */
  template: string;
  low: number;
  high: number;
}) {
  const ticks = (axis?.ticks ?? []).filter(
    (tick) => Number.isFinite(tick.value) && tick.value >= low && tick.value <= high,
  );
  if (ticks.length < 2) return null;
  const span = high - low || 1;
  return (
    <div data-testid="v4-chart-ruler" className="mt-1 grid items-start gap-3"
         style={{ gridTemplateColumns: template }}>
      <span aria-hidden="true" />
      <span className="relative block h-6">
        {ticks.map((tick) => {
          const at = ((tick.value - low) / span) * 100;
          return (
            <span key={tick.value} className="absolute top-0"
                  style={{ left: `${at}%` }}>
              <span aria-hidden="true"
                    className="block h-1.5 w-px bg-border-strong" />
              <span
                data-testid="v4-chart-x-tick"
                className="mt-0.5 block -translate-x-1/2 whitespace-nowrap text-[10px] tabular-nums text-text-muted"
              >
                {/* The server's string. Nothing here writes a number. */}
                {tick.display}
              </span>
            </span>
          );
        })}
      </span>
    </div>
  );
}

/** What the scale measures, under a horizontal form. */
export function AxisCaption({ axis }: { axis?: Axis }) {
  if (!axis?.label) return null;
  return (
    <p data-testid="v4-chart-x-label"
       className="mt-1 text-center text-xs font-medium text-text-secondary">
      {axis.label}
    </p>
  );
}

// ---- hover --------------------------------------------------------------

/**
 * The exact value under the cursor.
 *
 * Positioned in the document rather than inside the SVG so it can escape
 * the plot's bounds and stay legible at small sizes. Every string in it
 * came from the server; the tooltip's job is to show one, not to make one.
 */
function Tooltip({ point, at }: { point: HoverPoint; at: number }) {
  // Flipped to the other side near the right edge so the tooltip never
  // leaves the card.
  const flip = at > 0.62;
  return (
    <div
      data-testid="v4-chart-tooltip"
      role="status"
      className="pointer-events-none absolute top-2 z-10 max-w-56 rounded border border-border bg-surface-raised px-2 py-1.5 text-xs shadow-sm"
      style={flip ? { right: `${(1 - at) * 100}%` } : { left: `${at * 100}%` }}
    >
      <p className="font-medium text-text-primary" dir="auto">{point.label}</p>
      <ul className="mt-0.5 space-y-0.5">
        {point.values.map((value) => (
          <li key={value.name} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: slotColor(value.slot) }}
            />
            <span className="truncate text-text-secondary">{value.name}</span>
            <span className="ml-auto shrink-0 tabular-nums font-medium text-text-primary">
              {value.display}
            </span>
          </li>
        ))}
      </ul>
      {point.rowId ? (
        <p className="mono mt-1 text-[0.65rem] text-text-muted">{point.rowId}</p>
      ) : null}
    </div>
  );
}

// ---- zoom ---------------------------------------------------------------

/**
 * In, out, along, and back to everything.
 *
 * Buttons rather than a drag-brush. A brush is nicer with a mouse and
 * unusable with a finger or a keyboard, and this chart sits in a thread a
 * credit officer reads on whatever they have open. Four labelled controls
 * work everywhere and are reachable by tab.
 */
function ZoomControls({ count, window: view, onChange }: {
  count: number;
  window: Window;
  onChange: (next: Window) => void;
}) {
  const shown = slice(new Array(count).fill(0), view).length;
  const zoomed = isZoomed(view, count);
  const button =
    "rounded border border-border px-1.5 py-0.5 text-xs text-text-secondary " +
    "transition hover:bg-surface-hover disabled:opacity-40 " +
    "disabled:hover:bg-transparent";
  return (
    <div data-testid="v4-chart-zoom" className="flex items-center gap-1">
      <button type="button" className={button} data-testid="v4-chart-zoom-out"
              aria-label="Show more of this chart" disabled={!zoomed}
              onClick={() => onChange(zoom(view, count, 2))}>
        −
      </button>
      <button type="button" className={button} data-testid="v4-chart-zoom-in"
              aria-label="Zoom into this chart" disabled={shown <= 3}
              onClick={() => onChange(zoom(view, count, 0.5))}>
        +
      </button>
      <button type="button" className={button} data-testid="v4-chart-pan-back"
              aria-label="Move earlier" disabled={!zoomed || view.start <= 0}
              onClick={() => onChange(pan(view, count, -Math.max(1, Math.floor(shown / 2))))}>
        ‹
      </button>
      <button type="button" className={button} data-testid="v4-chart-pan-on"
              aria-label="Move later"
              disabled={!zoomed || view.start + shown >= count}
              onClick={() => onChange(pan(view, count, Math.max(1, Math.floor(shown / 2))))}>
        ›
      </button>
      {zoomed ? (
        <button type="button" className={`${button} ml-0.5`}
                data-testid="v4-chart-zoom-reset"
                onClick={() => onChange(FULL)}>
          {/* Says what it will do, and how much is currently hidden. */}
          All {count}
        </button>
      ) : null}
    </div>
  );
}

/** Zoom state for one chart, with the reset a re-run needs. */
export function useWindow(count: number): [Window, (next: Window) => void] {
  const [view, setView] = React.useState<Window>(FULL);
  // A new result is a new chart. Keeping the old window would show a
  // reader rows 5-10 of something they have not seen rows 1-4 of.
  const seen = React.useRef(count);
  React.useEffect(() => {
    if (seen.current !== count) {
      seen.current = count;
      setView(FULL);
    }
  }, [count]);
  return [view, setView];
}

// ---- the frame ----------------------------------------------------------

export interface PlotProps {
  /** For `data-testid`, so the browser suite keeps its handles. */
  testId: string;
  title: string;
  xAxis?: Axis;
  yAxis?: Axis;
  /** The x labels, already windowed by the caller. */
  labels: string[];
  /** Bars occupy bands; lines and dots occupy positions. */
  banded?: boolean;
  /** The scale, decided by the caller because only it knows about stacks. */
  low: number;
  high: number;
  /** What to show for the mark at each index. */
  hover: (index: number) => HoverPoint | null;
  /** The marks, positioned against `low`/`high` and this module's PLOT. */
  children: React.ReactNode;
  /** Shown beside the zoom controls; the legend when there is one. */
  footer?: React.ReactNode;
  /** The zoom control, when the caller has more points than it is showing. */
  zoom?: { count: number; window: Window; onChange: (next: Window) => void };
}

/**
 * Axes, gridlines, hover and zoom around a set of marks.
 *
 * The caller supplies the marks because only it knows what shape they are.
 * Everything a reader uses to INTERPRET them is here, once, so a line and
 * a bar cannot end up with different ideas of what an axis looks like.
 */
export function Plot({
  testId, title, xAxis, yAxis, labels, banded = false, low, high,
  hover, children, footer, zoom: zoomable,
}: PlotProps) {
  const [at, setAt] = React.useState<number | null>(null);
  const frame = React.useRef<SVGSVGElement | null>(null);

  const index = at === null
    ? -1
    : banded
      ? bandIndex(at, labels.length, PLOT)
      : nearestIndex(at, labels.length, PLOT);
  const point = index >= 0 ? hover(index) : null;

  function track(event: React.PointerEvent<SVGSVGElement>) {
    const node = frame.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (!rect.width) return;
    // Into the SVG's own units, so the geometry module does not have to
    // know anything about the page.
    setAt(((event.clientX - rect.left) / rect.width) * VIEW.width);
  }

  const marker = point && index >= 0
    ? (banded
        ? bandOf(index, labels.length, PLOT).centre
        : xOf(index, labels.length, PLOT))
    : null;

  return (
    <div className="relative">
      <svg
        ref={frame}
        data-testid={testId}
        viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
        className="h-52 w-full touch-pan-y"
        role="img"
        aria-label={`${title}. ${labels.length} point${
          labels.length === 1 ? "" : "s"}.`}
        onPointerMove={track}
        onPointerLeave={() => setAt(null)}
      >
        <Gridlines axis={yAxis} low={low} high={high} />
        {/* The baseline, drawn stronger than the gridlines: zero is where
            a length-encoded mark is measured from, and a reader has to be
            able to find it. */}
        {low <= 0 && high >= 0 ? (
          <line
            data-testid="v4-chart-baseline"
            x1={PLOT.left} y1={yOf(0, low, high, PLOT)}
            x2={PLOT.right} y2={yOf(0, low, high, PLOT)}
            className="stroke-border-strong" strokeWidth="0.6"
          />
        ) : null}
        {marker !== null ? (
          <line
            data-testid="v4-chart-crosshair"
            x1={marker} y1={PLOT.top} x2={marker} y2={PLOT.bottom}
            className="stroke-border-strong" strokeWidth="0.5"
            strokeDasharray="2 2"
          />
        ) : null}
        {children}
        <XLabels labels={labels} banded={banded} />
        <AxisTitles x={xAxis} y={yAxis} />
      </svg>
      {point ? (
        <Tooltip
          point={point}
          at={Math.min(0.92, Math.max(0.02, (marker ?? 0) / VIEW.width))}
        />
      ) : null}
      {footer || zoomable ? (
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">{footer}</div>
          {zoomable && zoomable.count > 4 ? (
            <ZoomControls count={zoomable.count} window={zoomable.window}
                          onChange={zoomable.onChange} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export { extentOf, slice, yOf, xOf, bandOf, PLOT as PLOT_BOX };
export type { Axis, Window };
