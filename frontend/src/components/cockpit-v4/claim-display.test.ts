import assert from "node:assert/strict";
import test from "node:test";

import { claimDisplayLine, claimDisplayValue } from "./claim-display.ts";

test("the published string is what is shown", () => {
  assert.equal(
    claimDisplayValue({
      display_value: "SAR 7,013 million",
      decimal_value: "7013.1167117986615",
      display_precision: 0,
    }),
    "SAR 7,013 million",
  );
});

test("a full-precision cross-check never reaches the screen", () => {
  // The live defect: the figures list printed `7013.1167117986615 SAR
  // million` under a narrative that had already written `SAR 7,013 million`.
  const shown = claimDisplayValue({
    decimal_value: "7013.1167117986615",
    display_precision: 0,
  });
  assert.equal(shown, "7013");
  assert.ok(!shown.includes("1167117986615"));
});

test("the declared precision is respected when the server sent no string", () => {
  assert.equal(
    claimDisplayValue({ decimal_value: "0.0312481", display_precision: 2 }),
    "0.03",
  );
});

test("nothing is shown when there is nothing to show", () => {
  assert.equal(claimDisplayValue({}), "");
  assert.equal(claimDisplayValue({ decimal_value: "" }), "");
  // No published string and no declared precision: showing the raw digits
  // would be the defect, so the claim renders with no figure at all.
  assert.equal(claimDisplayValue({ decimal_value: "7013.1167117986615" }), "");
  assert.equal(
    claimDisplayValue({ decimal_value: "7013.11", display_precision: -1 }),
    "",
  );
});

test("a non-numeric cross-check is not printed as digits either", () => {
  assert.equal(
    claimDisplayValue({ decimal_value: "n/a", display_precision: 0 }),
    "",
  );
});

test("the unit is not said twice", () => {
  // format_value writes the unit into the string, so appending the unit
  // beside it produced `SAR 7,013 million SAR million` on screen.
  assert.equal(
    claimDisplayLine({
      display_value: "SAR 7,013 million",
      unit: "SAR million",
      display_precision: 0,
    }),
    "SAR 7,013 million",
  );
  assert.equal(
    claimDisplayLine({ display_value: "12.40%", unit: "percent" }),
    "12.40%",
  );
});

test("the fallback still says what the number measures", () => {
  assert.equal(
    claimDisplayLine({
      decimal_value: "7013.1167117986615",
      unit: "SAR million",
      display_precision: 0,
    }),
    "7013 SAR million",
  );
  assert.equal(claimDisplayLine({ unit: "SAR million" }), "");
});
