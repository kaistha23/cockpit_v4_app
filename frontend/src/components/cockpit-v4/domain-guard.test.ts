/**
 * §17, §50. A payload that arrived is about the book that was asked for.
 *
 * The case this exists for is the one no component can see on its own: two
 * fetches in flight, the reader switches back, and the slower one lands
 * last. The effect's cleanup catches a response for a component that has
 * moved on; it cannot catch a response for the RIGHT component and the
 * wrong book, and that is the failure where everything on screen looks
 * correct.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { belongsTo, forDomain } from "./domain-guard.ts";

test("a payload about this book is rendered", () => {
  assert.equal(belongsTo({ domain_id: "corporate" }, "corporate"), true);
  assert.equal(belongsTo({ domain_id: "retail" }, "retail"), true);
});

test("a payload about the other book is dropped", () => {
  assert.equal(belongsTo({ domain_id: "retail" }, "corporate"), false);
  assert.equal(belongsTo({ domain_id: "corporate" }, "retail"), false);
  assert.equal(forDomain({ domain_id: "retail" }, "corporate"), null);
});

test("a panel that was told no book takes what the server resolved", () => {
  // The server picked the default; there is nothing to disagree with.
  assert.equal(belongsTo({ domain_id: "corporate" }, undefined), true);
  assert.equal(belongsTo({ domain_id: "retail" }, undefined), true);
});

test("a payload from an older server is not treated as a mismatch", () => {
  // Refusing it would blank a working panel against a server that simply
  // does not stamp the field yet.
  assert.equal(belongsTo({}, "corporate"), true);
  assert.equal(belongsTo({ domain_id: "" }, "corporate"), true);
  assert.equal(belongsTo({ domain_id: "  " }, "corporate"), true);
});

test("nothing at all is not a payload", () => {
  assert.equal(belongsTo(null, "corporate"), false);
  assert.equal(belongsTo(undefined, "corporate"), false);
  assert.equal(forDomain(null, "corporate"), null);
});

test("the late response from the previous book cannot land", () => {
  // The sequence: ask corporate, switch to retail, switch back to
  // corporate, and the FIRST corporate fetch resolves after the retail one.
  const corporate = { domain_id: "corporate", note: "first" };
  const retail = { domain_id: "retail", note: "second" };
  let shown: { note: string } | null = null;
  for (const arriving of [retail, corporate]) {
    const kept = forDomain(arriving, "corporate");
    if (kept) shown = kept;
  }
  assert.equal(shown?.note, "first");
});
