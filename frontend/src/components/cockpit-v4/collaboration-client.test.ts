/**
 * The save / investigate / comment / share client, and the one rule that
 * matters most on this screen: nothing may render as "sent" unless the
 * server said it was delivered.
 *
 * No network and no backend. These pin the paths the UI addresses and the
 * exact sentence it is allowed to show about a notification.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import {
  addComment,
  createInvestigation,
  deliveryWording,
  listComments,
  readOutbox,
  saveAnalysis,
  shareItem,
  type Notification,
} from "./client.ts";

const ORIGIN = "http://127.0.0.1:8414";
const PREFIX = "/api/v1/cockpit-v4";

let recorded: { url: string; method: string; body: unknown }[] = [];
let reply: unknown = {};

beforeEach(() => {
  process.env.NEXT_PUBLIC_COCKPIT_V4_API = ORIGIN;
  recorded = [];
  reply = {};
  (globalThis as Record<string, unknown>).fetch = async (
    url: string,
    init?: { method?: string; body?: string },
  ) => {
    recorded.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(init.body) : null,
    });
    return {
      ok: true,
      status: 200,
      json: async () => reply,
      text: async () => JSON.stringify(reply),
    };
  };
});

afterEach(() => {
  delete (globalThis as Record<string, unknown>).fetch;
});

test("every collaboration call addresses the V4 prefix and no other", async () => {
  reply = { saved_id: "save-1", title: "t" };
  await saveAnalysis({ run_id: "run-1", title: "t", note: "n" });
  reply = { investigation_id: "inv-1", title: "i" };
  await createInvestigation({ title: "i", saved_ids: ["save-1"] });
  reply = { comment_id: "cmt-1" };
  await addComment({
    subject_kind: "saved_analysis",
    subject_id: "save-1",
    body: "check collateral",
  });
  reply = { comments: [] };
  await listComments("saved_analysis", "save-1");

  const paths = recorded.map((r) => r.url.replace(ORIGIN, ""));
  assert.deepEqual(paths, [
    `${PREFIX}/saved-analyses`,
    `${PREFIX}/investigations`,
    `${PREFIX}/comments`,
    `${PREFIX}/comments?subject_kind=saved_analysis&subject_id=save-1`,
  ]);
  assert.deepEqual(
    recorded.map((r) => r.method),
    ["POST", "POST", "POST", "GET"],
  );
});

test("a share carries the audience and the optional address, nothing else", async () => {
  reply = {
    share: { share_id: "shr-1", audience_id: "credit.committee" },
    notification: null,
    delivery: { can_deliver: false },
  };
  await shareItem({
    subject_kind: "saved_analysis",
    subject_id: "save-1",
    audience_id: "credit.committee",
    message: "For Thursday.",
  });
  assert.deepEqual(recorded[0].body, {
    subject_kind: "saved_analysis",
    subject_id: "save-1",
    audience_id: "credit.committee",
    message: "For Thursday.",
  });
});

function notification(over: Partial<Notification>): Notification {
  return {
    notification_id: "ntf-1",
    recipient: "colleague@bank.test",
    subject: "s",
    state: "RECORDED",
    transport: "",
    reason: "No delivery transport is configured, so this message was recorded and NOT sent.",
    delivered: false,
    created_at: "2026-09-11T00:00:00Z",
    ...over,
  };
}

test("only a delivered notification may be described as sent", () => {
  const sent = deliveryWording(
    notification({ state: "SENT", delivered: true, transport: "smtp-uat" }),
  );
  assert.match(sent, /^Sent to colleague@bank\.test via smtp-uat\./);

  for (const row of [
    notification({}),
    notification({ state: "REFUSED" }),
    notification({ state: "FAILED", reason: "relay closed" }),
  ]) {
    const wording = deliveryWording(row);
    assert.ok(
      !/^Sent\b/.test(wording),
      `a ${row.state} notification must not read as sent: ${wording}`,
    );
    assert.match(wording, /[Nn]ot sent/);
  }
});

test("a state of SENT without delivered is still not a delivery claim", () => {
  // Belt and braces: the wording switches on `delivered`, which only the
  // server sets, so a hand-edited or replayed row cannot talk its way into
  // the sent sentence.
  const wording = deliveryWording(
    notification({ state: "SENT", delivered: false }),
  );
  assert.ok(!/^Sent\b/.test(wording), wording);
});

test("no notification at all says so plainly", () => {
  assert.equal(deliveryWording(null), "No notification was requested.");
});

test("the outbox is read from the V4 prefix", async () => {
  reply = { delivery: { can_deliver: false }, notifications: [] };
  await readOutbox();
  assert.equal(
    recorded[0].url.replace(ORIGIN, ""),
    `${PREFIX}/notifications`,
  );
});
