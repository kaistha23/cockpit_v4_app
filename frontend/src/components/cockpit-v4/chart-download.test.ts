import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  RASTER_SCALE,
  pngName,
  rasterSize,
  svgDataUrl,
} from "./chart-download.ts";

describe("what a downloaded chart is called", () => {
  it("puts the PNG beside the SVG of the same chart", () => {
    assert.equal(
      pngName("creditprobe-chart-run-abc123-2.svg"),
      "creditprobe-chart-run-abc123-2.png",
    );
  });

  it("does not double an extension the API left off", () => {
    assert.equal(pngName("chart"), "chart.png");
  });

  it("replaces the extension rather than the word", () => {
    // A run called "…-svg-review-…" must keep its name.
    assert.equal(pngName("chart-svg-review.svg"), "chart-svg-review.png");
  });
});

describe("the size of a raster", () => {
  const SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="360" ' +
    'viewBox="0 0 720 360"><rect/></svg>';

  it("keeps the drawing's own shape, not the window's", () => {
    const { width, height } = rasterSize(SVG);
    assert.equal(width / height, 2);
  });

  it("is legible in a slide", () => {
    assert.deepEqual(rasterSize(SVG), {
      width: 720 * RASTER_SCALE,
      height: 360 * RASTER_SCALE,
    });
  });

  it("gives a drawing with no stated size a page rather than refusing", () => {
    const { width, height } = rasterSize("<svg><rect/></svg>");
    assert.ok(width > 0 && height > 0);
  });

  it("ignores a size that is not a size", () => {
    const { width } = rasterSize('<svg width="0" height="0"></svg>');
    assert.ok(width > 0);
  });
});

describe("handing the drawing to the browser", () => {
  it("survives a label that is not ASCII", () => {
    // A Saudi book's chart carries Arabic sector names. `btoa`, the
    // obvious way to do this, throws on every one of them.
    const svg = '<svg><text>قطاع الطاقة</text></svg>';
    const url = svgDataUrl(svg);
    assert.ok(url.startsWith("data:image/svg+xml;charset=utf-8,"));
    assert.equal(
      decodeURIComponent(url.slice("data:image/svg+xml;charset=utf-8,".length)),
      svg,
    );
  });

  it("escapes the characters that would end the URL early", () => {
    const url = svgDataUrl('<svg><text>a#b&c</text></svg>');
    assert.ok(!url.includes("#"));
    assert.ok(!url.includes("&c"));
  });
});
