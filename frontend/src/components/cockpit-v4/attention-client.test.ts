/**
 * Unit tests for the home-feed client. No network, no backend.
 *
 * They pin two things the browser suite cannot see directly: that the feed
 * and the investigate action address the V4 prefix and nothing else, and that
 * a remembered investigation survives a reload without trusting whatever
 * happens to be in session storage.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import {
  forgetInvestigation,
  investigate,
  readAttention,
  readAttentionItem,
  recallInvestigation,
  rememberInvestigation,
} from "./client.ts";

const ORIGIN = "http://127.0.0.1:8414";

type Recorded = { url: string; method: string };

let recorded: Recorded[] = [];

beforeEach(() => {
  process.env.NEXT_PUBLIC_COCKPIT_V4_API = ORIGIN;
  recorded = [];
  const store = new Map<string, string>();
  (globalThis as Record<string, unknown>).sessionStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  (globalThis as Record<string, unknown>).fetch = async (
    url: string,
    init?: { method?: string },
  ) => {
    recorded.push({ url: String(url), method: init?.method ?? "GET" });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true }),
    };
  };
});

afterEach(() => {
  delete process.env.NEXT_PUBLIC_COCKPIT_V4_API;
});

test("the feed is read from the V4 attention endpoint", async () => {
  await readAttention();
  assert.deepEqual(recorded, [
    { url: `${ORIGIN}/api/v1/cockpit-v4/attention`, method: "GET" },
  ]);
});

test("one item is read from its own V4 path", async () => {
  await readAttentionItem("att-abc123");
  assert.equal(
    recorded[0].url,
    `${ORIGIN}/api/v1/cockpit-v4/attention/att-abc123`,
  );
});

test("an item id is encoded, never interpolated raw", async () => {
  await readAttentionItem("att-../../diagnostics");
  assert.ok(
    !recorded[0].url.includes("../"),
    `a path segment must not escape its endpoint: ${recorded[0].url}`,
  );
});

test("Investigate Further posts to the V4 investigate action", async () => {
  await investigate("att-abc123");
  assert.deepEqual(recorded, [
    {
      url: `${ORIGIN}/api/v1/cockpit-v4/attention/att-abc123/investigate`,
      method: "POST",
    },
  ]);
});

test("no home-feed call addresses the legacy backend", async () => {
  await readAttention();
  await investigate("att-abc123");
  for (const call of recorded) {
    assert.ok(
      !/:8000(\/|$)/.test(call.url),
      `the home feed must never address the legacy backend: ${call.url}`,
    );
    assert.ok(call.url.startsWith(`${ORIGIN}/api/v1/cockpit-v4/`));
  }
});

test("an open investigation survives a reload", () => {
  rememberInvestigation({
    threadId: "th-1",
    itemId: "att-1",
    suggested: ["Which borrowers?"],
  });
  assert.deepEqual(recallInvestigation(), {
    threadId: "th-1",
    itemId: "att-1",
    suggested: ["Which borrowers?"],
  });
  forgetInvestigation();
  assert.equal(recallInvestigation(), null);
});

test("a malformed remembered investigation is discarded, not rendered", () => {
  sessionStorage.setItem("cockpit-v4:active-investigation", "{not json");
  assert.equal(recallInvestigation(), null);
  sessionStorage.setItem(
    "cockpit-v4:active-investigation",
    JSON.stringify({ threadId: "th-1" }),
  );
  assert.equal(recallInvestigation(), null, "an item id is required");
  sessionStorage.setItem(
    "cockpit-v4:active-investigation",
    JSON.stringify({ threadId: "th-1", itemId: "att-1", suggested: "nope" }),
  );
  assert.deepEqual(recallInvestigation(), {
    threadId: "th-1",
    itemId: "att-1",
    suggested: [],
  });
});
