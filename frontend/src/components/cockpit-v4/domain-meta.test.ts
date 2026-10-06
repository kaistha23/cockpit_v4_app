/**
 * The calendar on Home comes from the book, not from the component.
 *
 * The defect these cover shipped to a live screen: Home's cover line read
 * `Saudi Arabia · SAR million · monthly` under a Corporate book that
 * reports quarters, while the attention section eight inches below it read
 * `Reporting quarter Q2 2026` off the same server. The suggested questions
 * under the Ask box said "this month" and "the latest month" to that same
 * book -- chips that send a question the book cannot answer.
 *
 * So: one reader of the `/domains` payload, and two assertions that hold
 * whichever way the calendars are configured -- the Corporate entry decides
 * the Corporate line, the Retail entry decides the Retail one, and neither
 * decides the other.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { DomainAvailability } from "./client.ts";
import {
  PROMPT_TEMPLATES, domainEntry, domainFrequency, domainHeadline, domainNoun,
  promptsFor,
} from "./domain-meta.ts";

/** What `/domains` sends today, trimmed to the fields Home reads. */
const AVAILABLE: DomainAvailability = {
  default_domain: "corporate",
  ready: ["corporate", "retail"],
  domains: [
    {
      domain_id: "corporate",
      domain_label: "Corporate Credit",
      release_id: "v4-saudi-corporate-20q-v3",
      ready: true,
      country: "Saudi Arabia",
      reporting_currency: "SAR",
      amount_scale: "million",
      reporting_frequency: "quarterly",
      period_noun: "quarter",
      latest_period: "2026Q2",
    },
    {
      domain_id: "retail",
      domain_label: "Retail Credit",
      release_id: "v4-saudi-retail-20m-v3",
      ready: true,
      country: "Saudi Arabia",
      reporting_currency: "SAR",
      amount_scale: "million",
      reporting_frequency: "monthly",
      period_noun: "month",
      latest_period: "2026-08",
    },
  ],
};

test("the cover line is the selected book's own, both ways", () => {
  assert.equal(domainHeadline(AVAILABLE, "corporate"),
               "Saudi Arabia · SAR million · quarterly");
  assert.equal(domainHeadline(AVAILABLE, "retail"),
               "Saudi Arabia · SAR million · monthly");
});

test("the corporate cover line never says monthly", () => {
  // The literal defect on the live screen.
  assert.ok(!domainHeadline(AVAILABLE, "corporate").includes("monthly"));
  assert.ok(!domainHeadline(AVAILABLE, "retail").includes("quarterly"));
});

test("switching back restores the first book's line", () => {
  // There is no state to go stale: the line is a function of the payload
  // and the selection, so the third read equals the first.
  const first = domainHeadline(AVAILABLE, "corporate");
  domainHeadline(AVAILABLE, "retail");
  assert.equal(domainHeadline(AVAILABLE, "corporate"), first);
});

test("corporate prompts are quarterly and retail prompts monthly", () => {
  const corporate = promptsFor(AVAILABLE, "corporate");
  const retail = promptsFor(AVAILABLE, "retail");

  assert.deepEqual(corporate, [
    "What is driving Stage 2 and ECL growth?",
    "Which sectors deteriorated most this quarter?",
    "Show EAD by sector for the latest quarter.",
    "Which borrowers were downgraded this quarter?",
    "Why is risk building across the corporate book?",
  ]);
  assert.deepEqual(retail, [
    "Which products saw the largest Stage 2 increase this month?",
    "Where is delinquency building?",
    "Which behavioural score bands deteriorated most?",
    "Show retail EAD by product for the latest month.",
    "Why is risk building across the retail book?",
  ]);
});

test("no corporate chip says month and no retail chip says quarter", () => {
  for (const prompt of promptsFor(AVAILABLE, "corporate")) {
    assert.ok(!/month/i.test(prompt), `corporate chip says month: ${prompt}`);
  }
  for (const prompt of promptsFor(AVAILABLE, "retail")) {
    assert.ok(!/quarter/i.test(prompt), `retail chip says quarter: ${prompt}`);
  }
});

test("the templates themselves name no calendar", () => {
  // The guarantee behind the two tests above: a chip's period word can only
  // arrive from the payload, so there is no list to update the day a book's
  // frequency changes.
  for (const list of Object.values(PROMPT_TEMPLATES)) {
    for (const template of list) {
      assert.ok(!/\b(month|quarter)/i.test(template),
                `a template states a calendar: ${template}`);
    }
  }
});

test("the calendar follows the payload, not the name of the book", () => {
  // The same reading, with the frequencies swapped in the payload. A
  // component holding its own list would keep saying "quarter" here.
  const swapped: DomainAvailability = {
    ...AVAILABLE,
    domains: AVAILABLE.domains.map((entry) => ({
      ...entry,
      reporting_frequency: entry.domain_id === "corporate"
        ? "monthly" : "quarterly",
      period_noun: entry.domain_id === "corporate" ? "month" : "quarter",
      latest_period: entry.domain_id === "corporate" ? "2026-08" : "2026Q2",
    })),
  };
  assert.equal(domainHeadline(swapped, "corporate"),
               "Saudi Arabia · SAR million · monthly");
  assert.ok(promptsFor(swapped, "corporate")
    .includes("Show EAD by sector for the latest month."));
});

test("a release that states only its frequency still gets a noun", () => {
  const terse: DomainAvailability = {
    ...AVAILABLE,
    domains: [{
      domain_id: "corporate", domain_label: "Corporate Credit",
      release_id: "r", ready: true, reporting_frequency: "quarterly",
    }],
  };
  assert.equal(domainNoun(domainEntry(terse, "corporate")), "quarter");
  assert.equal(domainHeadline(terse, "corporate"), "quarterly");
});

test("a release that states only its periods still gets a frequency", () => {
  const shapes: DomainAvailability = {
    ...AVAILABLE,
    domains: [{
      domain_id: "retail", domain_label: "Retail Credit",
      release_id: "r", ready: true, latest_period: "2026-08",
    }],
  };
  assert.equal(domainFrequency(domainEntry(shapes, "retail")), "monthly");
  assert.equal(domainNoun(domainEntry(shapes, "retail")), "month");
});

test("before the payload arrives nothing claims a calendar", () => {
  // A blank line for a moment is not a claim. `monthly` under a quarterly
  // book is, and it is the one that shipped.
  assert.equal(domainHeadline(null, "corporate"), "");
  assert.equal(domainEntry(null, "corporate"), null);
  for (const prompt of promptsFor(null, "corporate")) {
    assert.ok(!/\b(month|quarter)\b/i.test(prompt), prompt);
  }
  assert.ok(promptsFor(null, "corporate")
    .includes("Show EAD by sector for the latest period."));
});

test("a payload missing this book states nothing about it", () => {
  const onlyRetail: DomainAvailability = {
    ...AVAILABLE,
    domains: AVAILABLE.domains.filter((d) => d.domain_id === "retail"),
  };
  assert.equal(domainHeadline(onlyRetail, "corporate"), "");
  // And emphatically not the other book's calendar.
  assert.ok(!promptsFor(onlyRetail, "corporate")
    .some((prompt) => /month/i.test(prompt)));
});
