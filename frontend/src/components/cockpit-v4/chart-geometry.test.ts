import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  FULL,
  MIN_WINDOW,
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
} from "./chart-geometry.ts";

const BOX: Box = { left: 40, right: 240, top: 10, bottom: 110 };

const AXIS: Axis = {
  kind: "measure",
  label: "Exposure at default",
  column: "ead_sar_mn",
  unit: "SAR_MN",
  ticks: [
    { value: 0, display: "SAR 0 million" },
    { value: 50, display: "SAR 50 million" },
    { value: 100, display: "SAR 100 million" },
  ],
};

describe("the scale a chart is drawn against", () => {
  it("takes the extent the server published", () => {
    assert.deepEqual(extentOf(AXIS, [40, 52, 60]), [0, 100]);
  });

  it("falls back to the data for an answer saved before axes existed", () => {
    // A reader's own history must not stop drawing because the contract
    // grew a field.
    assert.deepEqual(extentOf(undefined, [40, 60]), [40, 60]);
  });

  it("falls back for an axis whose ticks the server could not build", () => {
    const empty: Axis = { ...AXIS, ticks: [] };
    assert.deepEqual(extentOf(empty, [40, 60]), [40, 60]);
  });

  it("reaches zero for a length-encoded form when it has to guess", () => {
    // A bar from 92 to 95 on a 92-95 scale is a 3% difference drawn as a
    // doubled bar.
    assert.deepEqual(extentOf(undefined, [92, 95], { zeroBased: true }), [0, 95]);
  });

  it("gives a usable scale when every value is the same", () => {
    const [low, high] = extentOf(undefined, [7, 7, 7]);
    assert.ok(high > low);
  });

  it("gives a usable scale when there are no values at all", () => {
    const [low, high] = extentOf(undefined, []);
    assert.ok(high > low);
  });

  it("ignores a tick the payload left unusable", () => {
    const broken = {
      ...AXIS,
      ticks: [{ value: Number.NaN, display: "" }, { value: 10, display: "10" }],
    } as Axis;
    // One usable tick is not a scale, so the data decides.
    assert.deepEqual(extentOf(broken, [3, 9]), [3, 9]);
  });
});

describe("where a mark goes", () => {
  it("puts the low at the floor and the high at the ceiling", () => {
    assert.equal(yOf(0, 0, 100, BOX), BOX.bottom);
    assert.equal(yOf(100, 0, 100, BOX), BOX.top);
  });

  it("puts the midpoint halfway up", () => {
    assert.equal(yOf(50, 0, 100, BOX), 60);
  });

  it("spreads positions from edge to edge", () => {
    assert.equal(xOf(0, 5, BOX), BOX.left);
    assert.equal(xOf(4, 5, BOX), BOX.right);
  });

  it("centres a lone point rather than pinning it to the left edge", () => {
    assert.equal(xOf(0, 1, BOX), 140);
  });

  it("tiles bands across the box without a gap", () => {
    const first = bandOf(0, 4, BOX);
    const last = bandOf(3, 4, BOX);
    assert.equal(first.width, 50);
    assert.equal(first.centre - first.width / 2, BOX.left);
    assert.equal(last.centre + last.width / 2, BOX.right);
  });
});

describe("which mark the cursor is over", () => {
  it("finds the nearest position", () => {
    assert.equal(nearestIndex(BOX.left, 5, BOX), 0);
    assert.equal(nearestIndex(BOX.right, 5, BOX), 4);
    assert.equal(nearestIndex(140, 5, BOX), 2);
  });

  it("keeps hold past the edge rather than blinking out", () => {
    // A tooltip that vanishes between two marks is a tooltip nobody can
    // use.
    assert.equal(nearestIndex(-500, 5, BOX), 0);
    assert.equal(nearestIndex(5000, 5, BOX), 4);
  });

  it("reports nothing to be near when there is nothing", () => {
    assert.equal(nearestIndex(100, 0, BOX), -1);
  });

  it("finds the band the cursor is inside, not the nearest edge", () => {
    assert.equal(bandIndex(BOX.left + 1, 4, BOX), 0);
    assert.equal(bandIndex(BOX.left + 49, 4, BOX), 0);
    assert.equal(bandIndex(BOX.left + 51, 4, BOX), 1);
    assert.equal(bandIndex(BOX.right - 1, 4, BOX), 3);
  });
});

describe("zoom", () => {
  const COUNT = 20;

  it("shows everything until it is asked not to", () => {
    assert.equal(slice(new Array(COUNT).fill(0), FULL).length, COUNT);
    assert.equal(isZoomed(FULL, COUNT), false);
  });

  it("narrows about the middle of what is on screen", () => {
    // A reader zooming is looking at something and expects it to stay in
    // view.
    const next = zoom(FULL, COUNT, 0.5);
    assert.equal(next.end - next.start, 10);
    assert.equal(next.start, 5);
  });

  it("widens back out to everything", () => {
    const narrow = zoom(FULL, COUNT, 0.5);
    assert.equal(isZoomed(zoom(narrow, COUNT, 4), COUNT), false);
  });

  it("never narrows below what a trend needs to be a trend", () => {
    let window = FULL;
    for (let i = 0; i < 10; i += 1) window = zoom(window, COUNT, 0.5);
    assert.equal(window.end - window.start, MIN_WINDOW);
  });

  it("pans without changing how much is shown", () => {
    const narrow = zoom(FULL, COUNT, 0.5);
    const moved = pan(narrow, COUNT, 3);
    assert.equal(moved.end - moved.start, narrow.end - narrow.start);
    assert.equal(moved.start, narrow.start + 3);
  });

  it("stops panning at the ends instead of running off them", () => {
    const narrow = zoom(FULL, COUNT, 0.5);
    assert.equal(pan(narrow, COUNT, -500).start, 0);
    assert.equal(pan(narrow, COUNT, 500).end, COUNT);
  });

  it("clamps a window that outlives the result it was made on", () => {
    // A saved zoom over a re-run that returned fewer rows shows the
    // nearest legal window rather than an empty chart.
    assert.deepEqual(slice([1, 2, 3], { start: 10, end: 20 }), [3]);
  });
});

describe("x labels", () => {
  it("shows every one when they fit", () => {
    assert.equal(labelStride(5, 8), 1);
  });

  it("thins them when they do not", () => {
    // Twenty periods in a thread's width is twenty overlapping labels,
    // which is the same as none.
    assert.equal(labelStride(20, 6), 4);
  });

  it("never divides by a stride of nothing", () => {
    assert.equal(labelStride(20, 0), 1);
  });
});
