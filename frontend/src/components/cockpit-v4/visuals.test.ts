import assert from "node:assert/strict";
import test from "node:test";

import type { RenderedChart, RenderedTable } from "./client.ts";
import { readFileSync } from "node:fs";
import path from "node:path";

import { CHART_KINDS, TOP_N, chartIsUseful, choose } from "./visual-choice.ts";

/**
 * What earns a chart, and what a chart is drawn from.
 *
 * The rule that matters most here is not tested by looking at pixels: bars
 * and sort order come from the CANONICAL values, never the displayed
 * strings. "SAR 9,000 million" sorts above "SAR 40,599 million" as text, so
 * a chart drawn from formatted labels is a chart of string lengths.
 */

function chart(over: Partial<RenderedChart> = {}): RenderedChart {
  return {
    kind: "bar",
    title: "EAD by sector",
    artifact_id: "art-1",
    x_column: "sector_name",
    y_columns: ["ead"],
    unit: "SAR million",
    points: [
      { row_id: "r0", label: "Information Technology",
        values: { ead: 7013.1167 }, display: { ead: "SAR 7,013 million" } },
      { row_id: "r1", label: "Manufacturing",
        values: { ead: 6936.1557 }, display: { ead: "SAR 6,936 million" } },
    ],
    series_units: { ead: "SAR million" },
    rendered_by: "creditprobe",
    ...over,
  };
}

test("a ranked comparison of several categories is worth charting", () => {
  assert.equal(chartIsUseful(chart()), true);
});

test("a single value is a number, and a chart of it is decoration", () => {
  assert.equal(
    chartIsUseful(chart({ points: [chart().points![0]] })),
    false,
  );
});

test("no rows is not an empty chart", () => {
  // §40: an empty frame reads as missing data rather than as no data.
  assert.equal(chartIsUseful(chart({ points: [] })), false);
});

test("a chart with no series to plot is not drawn", () => {
  assert.equal(chartIsUseful(chart({ y_columns: [] })), false);
});

test("a chart whose points carry no numbers is not drawn", () => {
  assert.equal(
    chartIsUseful(
      chart({
        points: [
          { row_id: "r0", label: "A", values: { ead: null },
            display: { ead: "" } },
          { row_id: "r1", label: "B", values: { ead: "n/a" },
            display: { ead: "" } },
        ],
      }),
    ),
    false,
  );
});

test("an absent chart is not useful", () => {
  assert.equal(chartIsUseful(undefined), false);
});

test("a table shows a readable top slice before the whole result", () => {
  // §41: five hundred rows are not dumped into a conversation.
  assert.equal(TOP_N, 10);
});

test("the displayed value is what the server wrote, not a local rounding",
  () => {
    const table: RenderedTable = {
      title: "EAD by sector",
      artifact_id: "art-1",
      columns: ["sector_name", "ead"],
      column_units: { ead: "SAR million" },
      rows: [
        {
          row_id: "r0",
          canonical: { sector_name: "Information Technology",
                       ead: 7013.1167117986615 },
          display: { sector_name: "Information Technology",
                     ead: "SAR 7,013 million" },
        },
      ],
      row_count: 1,
      rendered_by: "creditprobe",
    };
    // The contract this file depends on: both forms present, the canonical
    // one at full precision, and the display one already formatted.
    assert.equal(table.rows![0].display.ead, "SAR 7,013 million");
    assert.equal(table.rows![0].canonical.ead, 7013.1167117986615);
    assert.notEqual(
      String(table.rows![0].display.ead),
      String(table.rows![0].canonical.ead),
      "if these were ever the same string the frontend would be rounding",
    );
  });

/**
 * A matrix and a box plot are not point series.
 *
 * `chartIsUseful` asked "does it have two points with a number in the first
 * y column". Of a GRID that answers a question about a different chart:
 * the live rating migration arrived with 48 rows and no `points` shaped
 * like a series, and would have been judged not worth drawing by a test
 * written for bars.
 */
function matrix(over: Partial<RenderedChart> = {}): RenderedChart {
  return {
    kind: "heatmap",
    title: "Rating migration",
    artifact_id: "art-1",
    x_column: "rating_to",
    series_column: "rating_from",
    y_columns: ["borrowers"],
    unit: "borrowers",
    matrix: {
      row_axis: "rating_from",
      column_axis: "rating_to",
      measure: "borrowers",
      unit: "borrowers",
      rows: ["AA", "A+", "A"],
      columns: ["AA", "A+", "A"],
      square: true,
      // AA -> A is absent on purpose: nobody reported that move.
      cells: { "AA|AA": 127, "AA|A+": 3, "A+|A+": 78, "A|A": 168 },
      display: {
        "AA|AA": "127 borrowers", "AA|A+": "3 borrowers",
        "A+|A+": "78 borrowers", "A|A": "168 borrowers",
      },
    },
    rendered_by: "creditprobe",
    ...over,
  };
}

test("a grid with filled cells is worth drawing", () => {
  assert.equal(chartIsUseful(matrix()), true);
});

test("a grid with no filled cell is not", () => {
  const empty = matrix();
  assert.equal(
    chartIsUseful({ ...empty, matrix: { ...empty.matrix!, cells: {} } }),
    false,
  );
});

test("a grid is not judged by whether it has point series", () => {
  // No `points` at all: the old rule would have refused it.
  const withoutPoints = { ...matrix(), points: [] };
  assert.equal(chartIsUseful(withoutPoints), true);
});

test("the axis keeps the order the result was returned in", () => {
  // Alphabetically this is A, A+, AA -- and a diagonal drawn through that
  // pairs grades that have nothing to do with each other.
  const axis = matrix().matrix!.rows;
  assert.deepEqual(axis, ["AA", "A+", "A"]);
  assert.notDeepEqual(axis, [...axis].sort());
});

test("an absent cell is absent, not zero", () => {
  const cells = matrix().matrix!.cells;
  assert.equal("AA|A" in cells, false);
  assert.equal(cells["AA|AA"], 127);
});

test("a box plot is worth drawing when it has a box", () => {
  const box: RenderedChart = {
    kind: "box",
    title: "EAD spread by sector",
    artifact_id: "art-1",
    x_column: "sector",
    y_columns: ["ead"],
    unit: "SAR million",
    boxes: [
      { label: "Energy", count: 5, unit: "SAR million",
        minimum: "10", q1: "20.00", median: "30.0", q3: "40.00",
        maximum: "50",
        display: { minimum: "SAR 10 million", q1: "SAR 20 million",
                   median: "SAR 30 million", q3: "SAR 40 million",
                   maximum: "SAR 50 million" } },
    ],
    rendered_by: "creditprobe",
  };
  assert.equal(chartIsUseful(box), true);
  assert.equal(chartIsUseful({ ...box, boxes: [] }), false);
});

/**
 * Every chart, not the first one.
 *
 * `choose` used `.find()`. A three-chart answer rendered one, and the
 * reader had no way to know the other two existed -- "the delinquency
 * trend for each product" came back as one product. The singular fields
 * stay for the headline pair; the arrays are what gets drawn.
 */
test("every useful chart survives the choice, in the analyst's order", () => {
  const charts = [
    chart({ title: "Credit Card" }),
    chart({ title: "Personal Finance" }),
    chart({ title: "Auto" }),
    chart({ title: "Mortgage" }),
  ];
  const chosen = choose([], charts);
  assert.equal(chosen.usefulCharts.length, 4);
  assert.deepEqual(
    chosen.usefulCharts.map((c) => c.title),
    ["Credit Card", "Personal Finance", "Auto", "Mortgage"],
  );
  // The headline pair still points at the first one.
  assert.equal(chosen.chart!.title, "Credit Card");
});

test("a chart that is not worth drawing is dropped, not the ones after it",
  () => {
    const chosen = choose([], [
      chart({ title: "Empty", points: [] }),
      chart({ title: "Real" }),
    ]);
    assert.equal(chosen.usefulCharts.length, 1);
    assert.equal(chosen.usefulCharts[0].title, "Real");
  });

test("charts and tables are peers, not two sides of a toggle", () => {
  const table: RenderedTable = {
    title: "EAD by sector", artifact_id: "art-1",
    columns: ["sector_name", "ead"], column_units: {},
    rows: [{ row_id: "r0", canonical: { ead: 1 }, display: { ead: "1" } }],
    row_count: 1, rendered_by: "creditprobe",
  };
  const chosen = choose([table], [chart(), chart({ title: "Second" })]);
  assert.equal(chosen.both, true);
  assert.equal(chosen.usefulCharts.length, 2);
  assert.equal(chosen.usefulTables.length, 1);
});

test("the browser's vocabulary is the contract's vocabulary", () => {
  // `CHART_BODIES` in `visuals.tsx` is a `Record<ChartKind, ...>`, so a kind
  // with no component is a TYPE error rather than a chart that quietly
  // renders as bars -- which is how `waterfall` and `scatter` were legal in
  // the contract for months and drawn as something else. What a type cannot
  // check is whether that list is still the server's, so this reads the
  // schema both sides are generated against.
  const schema = JSON.parse(readFileSync(
    path.join(import.meta.dirname, "../../../../backend/cockpit_v4",
              "contracts/shared_defs.schema.json"),
    "utf8",
  ));
  const enumerated: string[] = schema.$defs.Chart.properties.kind.enum;
  assert.deepEqual([...CHART_KINDS].sort(), [...enumerated].sort());
});

// ---- downloading the figure you are looking at --------------------------

/** A chart with `n` plottable points and nothing else distinctive. */
function withPoints(n: number, kind = "bar"): RenderedChart {
  return chart({
    kind,
    points: Array.from({ length: n }, (_unused, i) => ({
      row_id: `r${i}`,
      label: `Sector ${i}`,
      values: { ead: 100 + i },
      display: { ead: `SAR ${100 + i} million` },
    })),
  });
}

test("a download button names the chart's place in the ANSWER", () => {
  // The rendered list is FILTERED: a chart with one point is dropped as
  // useless, so the second chart a reader sees can be the third in the
  // payload. Addressing the export by the on-screen position would
  // download a different chart from the one the button sits under.
  const picked = choose([], [withPoints(4), withPoints(1), withPoints(6, "line")]);
  assert.equal(picked.usefulCharts.length, 2);
  assert.deepEqual(picked.chartIndices, [0, 2]);
});

test("every index is kept when nothing is dropped", () => {
  const picked = choose([], [withPoints(3), withPoints(3, "line")]);
  assert.deepEqual(picked.chartIndices, [0, 1]);
});

test("there is an index for every chart that is rendered, always", () => {
  const picked = choose([], [withPoints(1), withPoints(5)]);
  assert.equal(picked.chartIndices.length, picked.usefulCharts.length);
});
