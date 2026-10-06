/**
 * Unit tests for the V4 API origin. No network.
 *
 * These call the module, so they prove the RUNTIME behaviour: that an unset
 * variable raises rather than resolving to a default, and that the legacy
 * variable is not consulted even when it is set to something plausible.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  CockpitV4NotConfigured,
  apiOrigin,
  cockpitV4Enabled,
  exportLinks,
  forgetRun,
  recallRun,
  rememberRun,
} from "./client.ts";

const LEGACY = "http://127.0.0.1:8000";

function setEnv(v4: string | undefined, generic: string | undefined): void {
  if (v4 === undefined) delete process.env.NEXT_PUBLIC_COCKPIT_V4_API;
  else process.env.NEXT_PUBLIC_COCKPIT_V4_API = v4;
  if (generic === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = generic;
}

afterEach(() => setEnv(undefined, undefined));

test("the configured V4 API origin is used", () => {
  setEnv("http://127.0.0.1:8414", undefined);
  assert.deepEqual(apiOrigin(), { ok: true, origin: "http://127.0.0.1:8414" });
});

test("an alternate selected port propagates", () => {
  for (const port of [8414, 8415, 8422, 9001]) {
    setEnv(`http://127.0.0.1:${port}`, undefined);
    const resolved = apiOrigin();
    assert.equal(resolved.ok, true);
    assert.equal(
      resolved.ok && resolved.origin,
      `http://127.0.0.1:${port}`,
      "the client must address the port the launcher actually selected",
    );
  }
});

test("a trailing slash is normalised away", () => {
  setEnv("http://127.0.0.1:8414/", undefined);
  assert.deepEqual(apiOrigin(), { ok: true, origin: "http://127.0.0.1:8414" });
});

test("an unset V4 variable fails loudly instead of defaulting", () => {
  setEnv(undefined, undefined);
  const resolved = apiOrigin();
  assert.equal(resolved.ok, false);
  assert.match(
    resolved.ok ? "" : resolved.reason,
    /NEXT_PUBLIC_COCKPIT_V4_API is not set/,
  );
});

test("the generic API variable is NEVER used as a fall back", () => {
  // The exact shape of the defect: the shell variable is set (to the legacy
  // backend, or even to something that looks like a V4 address) and the V4
  // variable is not. The client must refuse, not borrow it.
  setEnv(undefined, LEGACY);
  const resolved = apiOrigin();
  assert.equal(resolved.ok, false, "the legacy address must not be borrowed");

  setEnv(undefined, "http://127.0.0.1:8414");
  assert.equal(
    apiOrigin().ok,
    false,
    "even a plausible address in the wrong variable is not configuration",
  );
});

test("no V4 request can be addressed to the legacy backend port", () => {
  setEnv(undefined, LEGACY);
  const resolved = apiOrigin();
  assert.equal(resolved.ok, false);
  // There is no origin at all, so there is nothing to send to 8000.
  assert.equal("origin" in resolved, false);
});

test("an empty or whitespace value is not configuration", () => {
  for (const value of ["", "   "]) {
    setEnv(value, LEGACY);
    assert.equal(apiOrigin().ok, false);
  }
});

test("same-origin is an explicit opt-in, not an accident", () => {
  setEnv("same-origin", LEGACY);
  assert.deepEqual(apiOrigin(), { ok: true, origin: "" });
});

test("the thrown error is typed so callers can render it", () => {
  setEnv(undefined, undefined);
  assert.throws(
    () => {
      const resolved = apiOrigin();
      if (!resolved.ok) throw new CockpitV4NotConfigured();
    },
    CockpitV4NotConfigured,
  );
});

test("V4 mode is decided by the Cockpit variable alone", () => {
  setEnv(undefined, LEGACY);
  assert.equal(cockpitV4Enabled(), false,
    "the shell variable must not put the build into V4 mode");

  setEnv("http://127.0.0.1:8414", LEGACY);
  assert.equal(cockpitV4Enabled(), true);

  setEnv("   ", LEGACY);
  assert.equal(cockpitV4Enabled(), false,
    "whitespace is not configuration");
});

test("a remembered run survives and is forgotten deliberately", () => {
  // A minimal sessionStorage, because this runs outside a browser.
  const store = new Map<string, string>();
  (globalThis as { sessionStorage?: unknown }).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };

  assert.equal(recallRun(), null);
  rememberRun({ runId: "run-abc", threadId: "th-1", cursor: 7 });
  assert.deepEqual(recallRun(), {
    runId: "run-abc",
    threadId: "th-1",
    cursor: 7,
    // Absent when it was never written down; never undefined, so a caller
    // rendering it does not have to guard.
    question: "",
  });
  forgetRun();
  assert.equal(recallRun(), null,
    "a settled run is forgotten, so a later refresh does not resurrect it");

  // A corrupt entry is ignored rather than crashing the page on load.
  store.set("cockpit-v4:active-run", "{not json");
  assert.equal(recallRun(), null);
  store.set("cockpit-v4:active-run", JSON.stringify({ cursor: 3 }));
  assert.equal(recallRun(), null, "an entry with no run id is not a run");
});

test("the remembered run keeps the question it is answering", () => {
  // It is rebuilt field by field on the way out, and a field that is not
  // named there is silently dropped -- which rendered the reader's own turn
  // blank above a visibly working process panel after every refresh.
  const store = new Map<string, string>();
  (globalThis as { sessionStorage?: unknown }).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };

  rememberRun({
    runId: "run-1", threadId: "th-1", cursor: 0,
    question: "What is total EAD by sector?",
  });
  assert.equal(recallRun()?.question, "What is total EAD by sector?");
});

test("export links name the run, the artifact and the row scope", () => {
  setEnv("http://127.0.0.1:8414", undefined);
  const links = exportLinks("run-abc123");
  assert.ok(links.analysis.endsWith("/runs/run-abc123/export"));
  assert.ok(
    links
      .table("art-9")
      .endsWith("/runs/run-abc123/artifacts/art-9/export?rows=all"),
  );
  assert.ok(
    links
      .table("art-9", "displayed")
      .endsWith("/runs/run-abc123/artifacts/art-9/export?rows=displayed"),
  );
  assert.ok(links.chart(2).endsWith("/runs/run-abc123/charts/2/export"));
});

test("an export link escapes a run id rather than pasting it into a path", () => {
  setEnv("http://127.0.0.1:8414", undefined);
  const links = exportLinks("run/../../etc");
  assert.ok(!links.analysis.includes("/../"));
  assert.ok(links.analysis.includes("run%2F..%2F..%2Fetc"));
});
