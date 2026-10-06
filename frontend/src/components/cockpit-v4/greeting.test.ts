/**
 * Unit tests for the landing-page greeting. No network, no clock surprises.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { greeting, timeOfDay } from "./greeting.ts";

test("the greeting follows the reader's own clock", () => {
  assert.equal(timeOfDay(0), "Good morning");
  assert.equal(timeOfDay(8), "Good morning");
  assert.equal(timeOfDay(11), "Good morning");
  assert.equal(timeOfDay(12), "Good afternoon");
  assert.equal(timeOfDay(16), "Good afternoon");
  assert.equal(timeOfDay(17), "Good evening");
  assert.equal(timeOfDay(23), "Good evening");
});

test("a display name is used when there is one", () => {
  const at = new Date(2026, 8, 11, 14, 0, 0);
  assert.deepEqual(greeting("Mr. Sajid", at), {
    salutation: "Good afternoon",
    name: "Mr. Sajid",
    full: "Good afternoon, Mr. Sajid",
  });
});

test("no name means no name, never a placeholder person", () => {
  const at = new Date(2026, 8, 11, 9, 0, 0);
  for (const absent of ["", "   ", undefined as unknown as string]) {
    const hello = greeting(absent, at);
    assert.equal(hello.name, "");
    assert.equal(hello.full, "Good morning");
    assert.ok(
      !/sajid|user|there|admin/i.test(hello.full),
      `a missing name must not become one: ${hello.full}`,
    );
  }
});

test("the name is trimmed, not reformatted", () => {
  const at = new Date(2026, 8, 11, 19, 0, 0);
  assert.equal(greeting("  Aisha Rahman  ", at).full,
    "Good evening, Aisha Rahman");
});
